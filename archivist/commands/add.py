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
    add_module_to_apparatus,
    add_module_to_bay,
    error,
    get_module_by_path,
    get_module_by_uuid,
    get_project_name,
    get_registry_dir,
    get_registry_path,
    init_registry,
    is_module_registered,
    progress,
    prompt_apparatus_names,
    read_archivist_config,
    reactivate_module,
    register_apparatus,
    register_module,
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

    When no path is given, git infers the directory name from the URL — same
    as what _infer_target_name does — so target_path stays accurate without
    us spelling it out to git.
    """
    if is_submodule_context:
        cmd = ["git", "submodule", "add", url]
    else:
        cmd = ["git", "clone", url]
    if path_arg:
        cmd.append(path_arg)
    cmd.extend(passthrough)
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


def _update_vaults_list(target_dir: Path, vault_name: str) -> None:
    """
    Add vault_name to the `vaults` list in the target's config.yaml.

    The `vaults` field is the config layer's record of which vault(s) contain
    this module — human-readable names, not UUIDs. The DB layer's record is
    the module_bays row; both are updated. This function handles the config half.

    No-op if vault_name is already in the list.
    """
    config = read_archivist_config(target_dir) or {}
    current: list[str] = list(config.get("vaults") or [])  # type: ignore[arg-type]
    if vault_name in current:
        return
    current.append(vault_name)
    updated: ConfigSchema = {"uuid": str(config.get("uuid", ""))}  # type: ignore[typeddict-item]
    for k, v in config.items():
        if k != "uuid":
            updated[k] = v  # type: ignore[literal-required]
    updated["vaults"] = current  # type: ignore[typeddict-item]
    write_archivist_config(target_dir, updated)


# ---------------------------------------------------------------------------
# Registration helpers
# ---------------------------------------------------------------------------

def _do_register_module(
    target_dir: Path,
    apparatus_names: list[str],
    module_type: str,
    url: str,
    git_remote_name: str | None,
) -> str:
    """
    Upsert apparatus row(s) and the module row. Returns module UUID from
    registry (existing UUID if the path was already registered; new UUID
    if not).

    register_module() only wires up ONE apparatus association at creation
    time, so the first name in apparatus_names rides along with it; any
    additional names get their own explicit membership row via
    add_module_to_apparatus(). register_apparatus() is an idempotent upsert,
    so calling it repeatedly for the same name across runs is harmless.
    """
    primary_apparatus, *extra_apparati = apparatus_names
    register_apparatus(primary_apparatus, git_remote = None)  # registry remote ≠ module remote
    module_uuid = register_module(
        apparatus_name = primary_apparatus,
        name = get_project_name(target_dir),
        module_type = module_type,
        path = target_dir,
        git_remote = url,
        git_remote_name = git_remote_name,
    )
    for extra_name in extra_apparati:
        extra_uuid = register_apparatus(extra_name, git_remote = None)
        add_module_to_apparatus(module_uuid, extra_uuid)
    return module_uuid


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
        config: ConfigSchema = {  # type: ignore[typeddict-item]
            "uuid": module_uuid,
            "module-type": "general",
            "git-remote": url,
        }
        if git_remote_name:
            config["git-remote-name"] = git_remote_name  # type: ignore[typeddict-item]
        config["ignores"] = []  # type: ignore[typeddict-item]
        write_archivist_config(target_dir, config)
        progress(
            "  Standalone module — UUID written to config. "
            "Not registered in the Apparatus registry."
        )
        return module_uuid

    apparatus_names = prompt_apparatus_names()
    module_type = _prompt("  Select module type:", APPARATUS_MODULE_TYPES)
    module_uuid = _do_register_module(target_dir, apparatus_names, module_type, url, git_remote_name)

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
            _do_register_module(
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
        module_uuid = _do_register_module(
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
            module_uuid = _do_register_module(
                target_dir, apparati, module_type, url, git_remote_name
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

    cwd_module: ModuleRow | None = None
    if get_registry_path().exists():
        cwd_module = get_module_by_path(cwd)

    if cwd_module and not cwd_module.get("decimated_at"):
        progress(f"  [dry-run] Would add bay: {cwd_module['name']} ← {target_name}")
        if cwd_module.get("module_type") == "vault":
            progress(
                f"  [dry-run] Would add '{cwd_module['name']}' to target's vaults list."
            )
    else:
        progress(
            "  [dry-run] No registered superproject found — no bay row would be created."
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
    is_submodule_context = (cwd / ".git").exists()

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

    # Bay management: cwd must be a registered, active module to create a bay row.
    # Being in a git repo is not sufficient — the repo must be in the registry.
    cwd_module: ModuleRow | None = None
    if get_registry_path().exists():
        cwd_module = get_module_by_path(cwd)

    if cwd_module and not cwd_module.get("decimated_at") and is_module_registered(module_uuid):
        add_module_to_bay(cwd_module["uuid"], module_uuid)
        progress(f"  Bay registered: {cwd_module['name']} ← {target_name}")

        # Update vaults list in target config if superproject is vault-type.
        # module_bays row is always created regardless of container type;
        # the vaults field in config is only updated for vault containers.
        if cwd_module.get("module_type") == "vault":
            _update_vaults_list(target_path, cwd_module["name"])
            progress(f"  Added '{cwd_module['name']}' to target's vaults list.")

    # Hook install — non-fatal; user can re-run `archivist hooks sync`
    _install_hooks_local(target_path)

    success(f"Module registered: {target_name}")