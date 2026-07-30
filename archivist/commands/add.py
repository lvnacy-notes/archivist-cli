"""
archivist add

Register a module with the Apparatus. Git operation is determined by context:
no .git in cwd → git clone; .git present → git submodule add.

Registry writes happen after the git operation succeeds. A failed git step
leaves the registry untouched. That's the contract. If git fails, nothing
in ~/.archivist/ will be different from before you ran this command.
"""

import argparse
import subprocess
import sys
import uuid as _uuid_module
from pathlib import Path

from archivist.utils import (
    APPARATUS_MODULE_TYPES,
    ConfigSchema,
    ModuleRow,
    error,
    get_module_by_uuid,
    get_registry_dir,
    get_repo_root,
    init_registry,
    is_module_registered,
    link_module_into_container,
    progress,
    prompt_apparatus_names,
    read_archivist_config,
    reactivate_module,
    register_module_with_apparati,
    resolve_container_module,
    success,
    warning,
    write_archivist_config,
)


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------
#
# Interactive prompt primitives local to this command. Apparatus name
# prompting lives in utils/registry.py (prompt_apparatus_names) — it lists
# existing registered apparati, supports picking several at once, and handles
# "Create new". Shared with init and migrate and anything else that assigns
# modules to apparati.

def _prompt(question: str, options: list[str], default: str | None = None) -> str:
    """Present a numbered list of options and return the user's choice."""
    print(f"\n{question}")
    for i, opt in enumerate(options, 1):
        marker = " (default)" if opt == default else ""
        print(f"  {i}. {opt}{marker}")
    while True:
        raw = input("\nEnter number: ").strip()
        if not raw and default:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        print(f"  That's not a number between 1 and {len(options)}. Try again.")


def _confirm(question: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    raw = input(f"\n{question} {hint}: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _install_hooks_local(target_dir: Path) -> None:
    """Install hooks into target module. Non-fatal if it fails."""
    try:
        from archivist.commands.hooks.install import install_hooks_local
        install_hooks_local(target_dir)
    except Exception as e:
        warning(f"  Hook install failed: {e}. Run `archivist hooks sync` manually.")


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _is_inside_git_worktree(cwd: Path) -> bool:
    """
    Return True if cwd is anywhere inside a git working tree — not just
    exactly at its root.

    The old check here was `(cwd / ".git").exists()`, which only catches
    the literal top of a repo. Run `archivist add` from a subdirectory of
    an existing vault — `vault/modules/`, say, which is a completely normal
    place to keep your submodules — and that check says "nope, no repo
    here" and cheerfully tries to `git clone` into a location that's
    already under version control. Ask git, which actually knows the
    answer, instead of guessing from directory structure.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, cwd=cwd,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except (OSError, subprocess.CalledProcessError):
        return False


def _infer_target_name(url: str) -> str:
    """
    Derive a local directory name from a git URL.

    git@github.com:user/my-project.git  →  my-project
    https://github.com/user/my-project  →  my-project
    """
    stem = url.rstrip("/").split("/")[-1]
    if stem.endswith(".git"):
        stem = stem[:-4]
    return stem or "module"


def _build_git_command(
    url: str,
    path_arg: str | None,
    passthrough: list[str],
    is_submodule_context: bool,
) -> list[str]:
    """
    Build the git clone or git submodule add command.

    `git submodule add` is documented as `[<options>] [--] <repository> [<path>]`,
    and `git clone` follows the same shape. Options come FIRST. This used to
    tack passthrough onto the very end of the command — after the repo, after
    the path — which meant a perfectly reasonable `--name foo` just sat there
    uselessly like a fart in an elevator, doing nothing, matching nothing.
    Git doesn't parse its own flags out of your positional soup after the
    fact. Options first. Always. This is not a suggestion.

    A literal `--` is inserted between the passthrough options and the URL
    to stop a cursed repo name or path from being misread as a flag. Skipped
    if the caller's passthrough already contains its own `--` — one separator
    is plenty, we're not a buffet.

    When no path is given, git infers the directory name from the URL — same
    as what _infer_target_name does — so target_path stays accurate without
    us spelling it out to git.
    """
    if is_submodule_context:
        cmd = ["git", "submodule", "add"]
    else:
        cmd = ["git", "clone"]
    cmd.extend(passthrough)
    if "--" not in passthrough:
        cmd.append("--")
    cmd.append(url)
    if path_arg:
        cmd.append(path_arg)
    return cmd


def _execute_git_operation(cmd: list[str], cwd: Path) -> None:
    """
    Run the git operation. On failure: propagate exit code; write nothing to
    the registry. The registry is clean if you get here and this raises.
    """
    try:
        subprocess.run(cmd, check=True, cwd=cwd)
    except subprocess.CalledProcessError as e:
        error(
            f"Git operation failed (exit {e.returncode}). "
            "Nothing written to the registry."
        )
        sys.exit(e.returncode)


def _resolve_git_remote_name(target_dir: Path, url: str) -> str | None:
    """
    Query git remote -v in target_dir and return the remote name for url.

    Per spec §9: URL → git_remote (direct from command arg); name → git_remote_name
    (queried from git after the operation). NULL if no matching name is found yet —
    it'll be backfilled on the next sync.
    """
    try:
        result = subprocess.run(
            ["git", "remote", "-v"],
            capture_output=True, text=True, check=True, cwd=target_dir,
        )
        for line in result.stdout.strip().splitlines():
            if "(fetch)" in line and url in line:
                parts = line.split("\t", 1)
                if len(parts) == 2:
                    return parts[0].strip()
    except subprocess.CalledProcessError:
        pass
    return None


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _write_module_config(
    target_dir: Path,
    existing_config: ConfigSchema | None,
    module_uuid: str,
    url: str,
    git_remote_name: str | None,
) -> None:
    """
    Write (or rewrite) .archivist/config.yaml with uuid as the first field.

    Preserves all fields from existing_config; adds/updates uuid, git-remote,
    and git-remote-name. Creates .archivist/ if it doesn't exist.
    """
    config: ConfigSchema = {"uuid": module_uuid}  # type: ignore[typeddict-item]
    if existing_config:
        for k, v in existing_config.items():
            if k != "uuid":
                config[k] = v  # type: ignore[literal-required]
    config["git-remote"] = url  # type: ignore[typeddict-item]
    if git_remote_name:
        config["git-remote-name"] = git_remote_name  # type: ignore[typeddict-item]
    write_archivist_config(target_dir, config)


# ---------------------------------------------------------------------------
# Registration helpers
# ---------------------------------------------------------------------------

def _interactive_register(
    target_dir: Path,
    url: str,
    git_remote_name: str | None,
) -> str:
    """
    Full interactive registration for a module with no .archivist/config.yaml.
    Mirrors the apparatus registration flow from archivist init.
    Returns the module UUID.

    This is case (d) in the UUID resolution matrix — target was a non-Archivist
    project that we just cloned or submodule-added. Set it up now.
    """
    is_apparatus = _confirm(
        "  No Archivist config found. Is this module part of an Apparatus?",
        default=True,
    )

    if not is_apparatus:
        module_uuid = str(_uuid_module.uuid4())
        config: ConfigSchema = {
            "uuid": module_uuid,
            "module-type": "general",
            "git-remote": url,
        }
        if git_remote_name:
            config["git-remote-name"] = git_remote_name
        config["ignores"] = []
        write_archivist_config(target_dir, config)
        progress(
            "  Standalone module — UUID written to config. "
            "Not registered in the Apparatus registry."
        )
        return module_uuid

    apparatus_names = prompt_apparatus_names()
    module_type = _prompt("  Select module type:", APPARATUS_MODULE_TYPES)
    module_uuid = register_module_with_apparati(
        target_dir,
        apparatus_names,
        module_type,
        url,
        git_remote_name
    )

    config = {  # type: ignore[assignment]
        "uuid": module_uuid,
        "module-type": module_type,
        "apparati": apparatus_names,
        "git-remote": url,
    }
    if git_remote_name:
        config["git-remote-name"] = git_remote_name  # type: ignore[literal-required]
    config["ignores"] = []  # type: ignore[literal-required]
    write_archivist_config(target_dir, config)

    return module_uuid


def _resolve_and_register(
    target_dir: Path,
    target_config: ConfigSchema | None,
    url: str,
    git_remote_name: str | None,
) -> str:
    """
    UUID resolution and registration. Four cases per spec §7:

      a. UUID in config + decimated in registry → reactivate; refresh module row
      b. UUID in config + active in registry    → refresh module row; done
      c. UUID in config + not in registry       → register from config values
      d. No config                              → full interactive registration

    Returns the module UUID in all cases. Standalone modules (not Apparatus-
    registered) also return a UUID — it's written to config for hooks and sync.
    """

    # Case d: no config at all
    if target_config is None:
        return _interactive_register(target_dir, url, git_remote_name)

    existing_uuid: str | None = target_config.get("uuid")  # type: ignore[assignment]

    # Config exists but somehow has no UUID — treat as case d
    if not existing_uuid:
        progress("  Config found but no UUID. Running interactive setup.")
        return _interactive_register(target_dir, url, git_remote_name)

    # Cases a, b, c: UUID present — check registry
    registry_row = get_module_by_uuid(existing_uuid)

    if registry_row is not None:
        if registry_row.get("decimated_at"):
            # Case a: decimated → reactivate, then refresh
            progress(f"  Module was decimated — reactivating.")
            reactivate_module(existing_uuid)
        else:
            # Case b: active → refresh only
            progress(f"  Module already registered and active.")

        # Refresh path + git_remote in registry regardless
        apparati: list[str] = list(target_config.get("apparati") or [])  # type: ignore[arg-type]
        if apparati:
            register_module_with_apparati(
                target_dir,
                apparati,
                str(target_config.get("module-type") or "general"),
                url,
                git_remote_name,
            )
        _write_module_config(target_dir, target_config, existing_uuid, url, git_remote_name)
        return existing_uuid

    # Case c: UUID in config but not in registry
    progress(f"  Config found — module not yet in registry. Registering now.")
    apparati = list(target_config.get("apparati") or [])  # type: ignore[arg-type]
    module_type = str(target_config.get("module-type") or "general")

    if apparati:
        module_uuid = register_module_with_apparati(
            target_dir, apparati, module_type, url, git_remote_name
        )
    else:
        # Config exists with UUID but no apparatus assignment — ask
        progress("  Config has no apparatus assignment.")
        is_apparatus = _confirm(
            "  Is this module part of an Apparatus?", default=True
        )
        if is_apparatus:
            apparati = prompt_apparatus_names()
            target_config["apparati"] = apparati  # type: ignore[typeddict-item]
            module_uuid = register_module_with_apparati(
                target_dir,
                apparati,
                module_type,
                url,
                git_remote_name
            )
        else:
            # Standalone: keep existing UUID, skip registry write
            module_uuid = existing_uuid
            progress("  Standalone module — not registered in the Apparatus.")

    _write_module_config(target_dir, target_config, module_uuid, url, git_remote_name)
    return module_uuid


# ---------------------------------------------------------------------------
# Dry-run plan
# ---------------------------------------------------------------------------

def _print_dry_run_plan(
    url: str,
    target_path: Path,
    target_name: str,
    git_cmd: list[str],
    is_submodule_context: bool,
    cwd: Path,
) -> None:
    op = "submodule add" if is_submodule_context else "clone"
    progress(f"  [dry-run] Would run: {' '.join(git_cmd)}")
    progress(f"  [dry-run] Would register module at: {target_path}")

    # Container lookup requires actually being inside a repo — get_repo_root()
    # exits hard otherwise, which is the last thing a dry-run preview should
    # do. is_submodule_context already told us whether cwd is inside one.
    container_row: ModuleRow | None = None
    if is_submodule_context:
        container_row = resolve_container_module(get_repo_root())

    if container_row:
        progress(f"  [dry-run] Would add bay: {container_row['name']} ← {target_name}")
        if container_row.get("module_type") == "vault":
            progress(
                f"  [dry-run] Would add '{container_row['name']}' to target's vaults list."
            )
    else:
        progress(
            "  [dry-run] No registered container found here — no bay row would be created."
        )

    progress("  [dry-run] Would install git hooks into target module.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    dry_run = getattr(args, "dry_run", False)
    url: str = args.url
    path_arg: str | None = getattr(args, "path", None)
    passthrough: list[str] = list(getattr(args, "passthrough", None) or [])

    cwd = Path.cwd()
    if dry_run:
        # Dry run must not invoke so much as a read-only subprocess — see
        # test_dry_run_does_not_execute_git, which is correct to demand
        # this. Approximate with a raw filesystem check instead; it can't
        # see "invoked from a subdirectory of a repo" the way the real
        # check can, but a preview being slightly optimistic in a rare edge
        # case beats breaking "dry-run touches nothing, full stop."
        is_submodule_context = (cwd / ".git").exists()
    else:
        is_submodule_context = _is_inside_git_worktree(cwd)

    target_name = _infer_target_name(url)
    target_path = (cwd / (path_arg or target_name)).resolve()

    git_cmd = _build_git_command(url, path_arg, passthrough, is_submodule_context)

    if dry_run:
        _print_dry_run_plan(url, target_path, target_name, git_cmd, is_submodule_context, cwd)
        return

    # Git is the gate — nothing touches the registry until this succeeds
    progress(f"\n  ▶ {' '.join(git_cmd)}")
    _execute_git_operation(git_cmd, cwd)
    success(f"  Git operation complete.")

    # Resolve git_remote_name by querying the target after the git op (spec §9)
    git_remote_name = _resolve_git_remote_name(target_path, url)

    # Read the target's config (may or may not exist)
    target_config = read_archivist_config(target_path)

    # Ensure the registry is initialized before we start writing to it.
    # add doesn't run the full first-run setup (that's init's job) — it just
    # creates the schema quietly so registration can proceed.
    if not get_registry_dir().exists():
        progress("  Registry not found — initializing schema.")
        init_registry()

    # UUID resolution and registration (spec §7 four-case matrix)
    module_uuid = _resolve_and_register(
        target_dir=target_path,
        target_config=target_config,
        url=url,
        git_remote_name=git_remote_name,
    )

    # Containment: being in a git repo is not sufficient on its own — the
    # repo has to be a registered, active module, and the new module has to
    # have actually made it into the registry (standalone modules don't).
    #
    # Container is resolved by the UUID its OWN config.yaml declares, not by
    # matching cwd against a path string in the registry — see
    # resolve_container_module()'s docstring if you're wondering why. Short
    # version: paths go stale the moment a directory gets renamed or moved;
    # the config file's uuid doesn't.
    if is_submodule_context and is_module_registered(module_uuid):
        container_row = resolve_container_module(get_repo_root())
        if container_row:
            link_module_into_container(container_row, module_uuid, target_path)
            progress(f"  Bay registered: {container_row['name']} ← {target_name}")
            if container_row.get("module_type") == "vault":
                progress(f"  Added '{container_row['name']}' to target's vaults list.")
        else:
            progress(
                "  No registered container found here — no bay row created. "
                "Run `archivist init` (or `archivist sync`, once it exists) "
                "at this level if it should be Apparatus-registered itself."
            )

    # Hook install — non-fatal; user can re-run `archivist hooks sync`
    _install_hooks_local(target_path)

    success(f"Module registered: {target_name}")