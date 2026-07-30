"""
archivist init

Interactive project setup. Writes .archivist/config.yaml and optionally
installs git hooks locally. Safe to re-run at any time — idempotent, never
clobbers existing config without asking.
"""

import argparse
import importlib.resources
import subprocess
import sys
import uuid as _uuid_module
from pathlib import Path

from archivist.commands.hooks.install import install_hooks_local

from archivist.utils import (
    APPARATUS_MODULE_TYPES,
    ConfigSchema,
    error,
    get_archivist_config_path,
    get_project_name,
    get_registry_dir,
    get_repo_root,
    get_superproject_root,
    init_registry,
    link_module_into_container,
    progress,
    prompt_apparatus_names,
    read_archivist_config,
    register_module_with_apparati,
    resolve_container_module,
    success,
    warning,
    write_archivist_config,
)


def _check_or_init_git(dry_run: bool) -> Path:
    """
    Ensure we're inside a git repo before proceeding — or create one.

    Must run before get_repo_root(). That function shells out to
    git rev-parse --show-toplevel and exits non-zero if there's no repo,
    which means sys.exit(1) before init gets a chance to offer git init.
    Check first. Init if needed. Then proceed.
    """
    cwd = Path.cwd()
    if (cwd / ".git").exists():
        return get_repo_root()

    print(
        "\n  No git repo here. archivist lives inside git repos — "
        "we can create one right now, or you can go find a directory "
        "that already has its life together."
    )
    if not _confirm("Run git init in the current directory?", default = True):
        progress("Fair enough. Come back when you have a repo.")
        sys.exit(0)

    if dry_run:
        progress(f"  [dry-run] Would run: git init { cwd }")
        return cwd  # best we can do without an actual repo

    try:
        subprocess.run(
            ["git", "init"],
            check = True,
            cwd = cwd
        )
        success("  git init complete.")
    except subprocess.CalledProcessError as e:
        error(f"git init failed and now we're both stuck: {e}")
        sys.exit(1)

    return get_repo_root()


def _confirm(question: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    raw = input(f"\n{ question } { hint }: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _first_run_registry_setup(git_root: Path, dry_run: bool) -> None:
    """
    Bootstrap ~/.archivist/ on the first archivist init run on this machine.

    Runs exactly once — every subsequent call finds the directory and returns
    immediately. init_registry() is idempotent, so partial failures on a
    previous run clean up correctly on retry.
    """
    registry_dir = get_registry_dir()
    if registry_dir.exists():
        return

    progress(
        "\n  First time running archivist on this machine. "
        "~/.archivist/ doesn't exist yet — that's the registry where Archivist "
        "tracks every module you've registered. Setting it up now."
    )

    if dry_run:
        progress(
            f"  [dry-run] Would create: {registry_dir}\n"
            f"  [dry-run] Would run: git init {registry_dir}\n"
            f"  [dry-run] Would create: registry schema (apparati, modules, module_bays, module_apparatus)"
        )
        return

    init_registry()
    success(f"  Registry initialized: {registry_dir}")
    _prompt_registry_remote(git_root, registry_dir)


def _install_hooks_local(git_root: Path, dry_run: bool = False) -> None:
    """Install hooks into this repo only. Global templates are the user's call."""
    install_hooks_local(git_root, dry_run = dry_run)


def _prompt(
    question: str,
    options: list[str],
    default: str | None = None
) -> str:
    """
    Present a numbered list of options and return the user's choice.
    Loops until valid input is received.
    """
    print(f"\n{question}")
    for i, opt in enumerate(options, 1):
        marker = " (default)" if opt == default else ""
        print(f"  { i }. { opt }{ marker }")

    while True:
        raw = input("\nEnter number: ").strip()
        if not raw and default:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        print(f"  That's not a number between 1 and { len(options) }. Try again.")


def _prompt_registry_remote(git_root: Path, registry_dir: Path) -> None:
    """
    Prompt for a git remote for the ~/.archivist/ registry repo.

    The registry remote is separate from any module's remote — it's where
    the registry itself gets backed up so archivist restore can function on
    a new machine. Optional in Phase 1 (automated push isn't implemented yet),
    but set it now or you'll be doing it manually later when you care more.

    Lists the current module's remotes as URL examples since the user is
    probably on the same hosting account.
    """
    progress(
        "\n  ~/.archivist/ is its own git repo. Give it a remote and Archivist "
        "can back up your registry so you can restore it on a new machine.\n"
        "  Optional — set it later with:\n"
        "    git -C ~/.archivist remote add origin <url>"
    )

    try:
        result = subprocess.run(
            [
                "git",
                "remote",
                "-v",
            ],
            capture_output = True,
            text = True,
            check = True,
            cwd = git_root,
        )
        fetch_lines = [l for l in result.stdout.strip().splitlines() if "(fetch)" in l]
        if fetch_lines:
            progress("\n  Your current repo's remotes (for reference):")
            for line in fetch_lines:
                progress(f"    {line}")
    except subprocess.CalledProcessError:
        pass  # no remotes configured — say nothing

    raw_url = input("\n  Registry remote URL (or Enter to skip): ").strip()
    if not raw_url:
        warning(
            "  No registry remote set. archivist restore won't work without one.\n"
            "  Set it later: git -C ~/.archivist remote add origin <url>"
        )
        return

    try:
        subprocess.run(
            [
                "git",
                "remote",
                "add",
                "origin",
                raw_url,
            ],
            check = True,
            cwd = registry_dir,
        )
        success(f"  Registry remote set: {raw_url}")
    except subprocess.CalledProcessError:
        warning(
            f"  Couldn't add remote '{raw_url}'. Do it yourself:\n"
            f"    git -C ~/.archivist remote add origin {raw_url}"
        )


def _prompt_templater_mode() -> str:
    """
    Ask the user how Archivist should handle Templater expressions in frontmatter.

    Three modes:

      resolve  — Archivist resolves the static subset of Templater expressions
                 at write time (tp.date.*, tp.file.*, tp.frontmatter.*). Anything
                 it can't handle is left verbatim with a warning. Obsidian not
                 required. Works in any module.

      preserve — Archivist detects <% %> expressions and round-trips them safely
                 without touching them. Alt-tab to Obsidian, run
                 "Templater: replace templates in the active file" yourself.

      false    — Archivist treats <% %> as dumb strings. Use this if your project
                 has no Templater expressions and you want zero overhead.

    Returns one of: "resolve", "preserve", "false".
    """
    print("\n  Templater expression handling.")
    print("  Does this project use Templater expressions in frontmatter?")
    print()
    print("    resolve   — Archivist resolves tp.date.*, tp.file.*, tp.frontmatter.*")
    print("                at write time. Unresolvable expressions are preserved")
    print("                verbatim with a warning. No Obsidian required.")
    print("    preserve  — Archivist detects and safely round-trips <% %> expressions")
    print("                without resolving them. You handle resolution in Obsidian.")
    print("    false     — Treat <% %> as plain strings. No Templater handling at all.")

    return _prompt(
        "Select Templater mode:",
        [
            "resolve",
            "preserve",
            "false"
        ],
        default = "preserve",
    )


def _resolve_git_remote(git_root: Path) -> tuple[str | None, str | None]:
    """
    Resolve git_remote (URL) and git_remote_name for this module. Spec §9.

    Reads git remote -v from git_root:
    - One remote: use it automatically, no prompt.
    - Multiple remotes: present the list and ask which one is the Apparatus remote.
    - No remotes: offer manual URL entry or skip.

    Returns (url, name). Either or both may be None if the user skips.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "remote",
                "-v",
            ],
            capture_output = True,
            text = True,
            check = True,
            cwd = git_root,
        )
        fetch_lines = [
            l for l in result.stdout.strip().splitlines()
            if "(fetch)" in l
        ]
    except subprocess.CalledProcessError:
        fetch_lines = []

    if not fetch_lines:
        print(
            "\n  No git remotes found. archivist restore needs a remote URL "
            "to clone this module from."
        )
        raw = input("  Remote URL (or Enter to skip): ").strip()
        if not raw:
            warning("  No git_remote set. archivist restore won't work for this module.")
            return None, None
        return raw, None

    remotes: list[tuple[str, str]] = []
    for line in fetch_lines:
        parts = line.split("\t", 1)
        if len(parts) == 2:
            name = parts[0].strip()
            url = parts[1].replace(" (fetch)", "").strip()
            remotes.append((name, url))

    if len(remotes) == 1:
        name, url = remotes[0]
        progress(f"  Using git remote '{name}': {url}")
        return url, name

    # Multiple remotes — make them pick
    print("\n  Multiple remotes found. Which one is this module's Apparatus remote?")
    options = [f"{name}  ({url})" for name, url in remotes] + ["Enter URL manually", "Skip"]
    choice = _prompt("Select:", options)

    if choice == "Skip":
        warning("  No git_remote set. archivist restore won't work for this module.")
        return None, None

    if choice == "Enter URL manually":
        raw = input("  Remote URL: ").strip()
        return (raw, None) if raw else (None, None)

    for name, url in remotes:
        if choice.startswith(name):
            return url, name

    return None, None


def _stage_archivist_config(git_root: Path, was_flat_config: bool) -> None:
    """
    Stage .archivist/ after writing it — and, if this run just migrated off
    a flat .archivist file, stage that deletion too so both halves of the
    change land in the index as one logical commit. Same pattern `git
    submodule add` uses for .gitmodules + the submodule directory: nobody
    should have to remember to `git add` the aftermath of a tool they just
    ran on their own behalf.

    Non-fatal on failure — a staging miss is annoying, not catastrophic.
    Warn loudly and tell the user how to finish it by hand.
    """
    try:
        subprocess.run(
            [
                "git",
                "add",
                ".archivist/"
            ],
            check = True,
            cwd = git_root,
            capture_output = True,
        )
        if was_flat_config:
            # git add on a deleted path records the removal in the index —
            # equivalent to `git rm --cached`, but works whether the file is
            # already gone from disk (it is) or not.
            subprocess.run(
                [
                    "git",
                    "add",
                    ".archivist"
                ],
                check = True,
                cwd = git_root,
                capture_output = True,
            )
            success("  Staged: .archivist/ (new) + .archivist deletion (flat file)")
        else:
            success("  Staged: .archivist/")
    except subprocess.CalledProcessError as e:
        warning(
            "Auto-staging failed — stage manually before committing:\n"
            "     git add .archivist/\n"
            f"     ({ e })"
        )


def _write_sample_changelog(git_root: Path) -> None:
    """
    Write sample-changelog.py into .archivist/ if it isn't already there.

    The source file lives in the archivist package under
    archivist/data/sample-changelog.py and is read via importlib.resources
    so this works correctly whether the package is installed as a wheel,
    editable install, or run directly from source.

    Skips the write if the file already exists — re-running init on a library
    project that already has a sample (or a live plugin) should not clobber it.
    Prints a note either way so the user knows what happened.
    """
    dest = git_root / ".archivist" / "sample-changelog.py"

    if dest.exists():
        progress(f"  sample-changelog.py already exists — leaving it alone.")
        return

    try:
        ref = importlib.resources.files("archivist.data").joinpath("sample-changelog.py")
        content = ref.read_text(encoding = "utf-8")
    except Exception as e:
        # Non-fatal — the plugin system works fine without the sample file.
        # The user just won't have the reference. Tell them why.
        progress(
            f"  ⚠️  Couldn't read bundled sample-changelog.py: {e}\n"
            "     You can grab it from the Archivist repo if you need it."
        )
        return

    dest.write_text(content, encoding = "utf-8")
    success(f"  Written: .archivist/sample-changelog.py")
    progress(
        "     Rename it to changelog.py when you're ready to customise.\n"
        "     It runs as-is — start there."
    )


def run(args: argparse.Namespace) -> None:
    dry_run = getattr(
        args,
        "dry_run",
        False
    )

    # --- Git context check: must run before get_repo_root() ---
    # get_repo_root() exits on no-repo. We want to offer git init first.
    git_root = _check_or_init_git(dry_run)

    existing: ConfigSchema | None = read_archivist_config(git_root)
    progress(f"\n  📁 Repo root: {git_root}")

    # Captured now, before write_archivist_config() silently evicts the flat
    # file the moment it writes the directory form. This is the ONLY point
    # in the function where "was this a flat-config migration?" is still an
    # answerable question — the flat path is a FILE, the directory form's
    # config.yaml is a path INSIDE a directory, so this doesn't collide with
    # a normal directory-form existing config.
    was_flat_config = (
        existing is not None
        and get_archivist_config_path(git_root) == git_root / ".archivist"
    )

    # --- Existing config: show it and offer update ---
    if existing is not None:
        existing_path = get_archivist_config_path(git_root)
        success(f"Found existing config: {existing_path.relative_to(git_root)}")
        for k, v in existing.items():
            progress(f"     { k }: { v }")

        if not _confirm("Update configuration?", default = False):
            # Offer hook reinstall even if config unchanged
            if _confirm("Reinstall git hooks?", default = True):
                _install_hooks_local(git_root, dry_run = dry_run)
            progress("Done.")
            return

    # --- First-run registry bootstrap (idempotent; skips if already present) ---
    _first_run_registry_setup(git_root, dry_run)

    # --- Apparatus membership and module type ---
    is_apparatus = _confirm("Is this module part of an Apparatus?", default = True)

    if is_apparatus:
        apparatus_names: list[str] = prompt_apparatus_names()
        module_type = _prompt("Select module type:", APPARATUS_MODULE_TYPES)

        if not apparatus_names:
            # Said "yes" to the Apparatus question, then confirmed the
            # checkbox menu with nothing checked. That's a legitimate
            # change of heart, not an error — treat it exactly like they'd
            # answered "no" to begin with. is_apparatus drives every
            # downstream decision (git remote resolution, the registration
            # call below, dry-run preview, bay linking at the bottom of
            # this function), so flipping it here is the one place that
            # needs to happen for all of those to stay correct. Module
            # type stays whatever they picked — that's about changelog
            # structure, not Apparatus membership, and there's no reason
            # to throw away a perfectly good answer.
            progress(
                "  No apparatus selected — registering as a standalone module instead."
            )
            is_apparatus = False
    else:
        apparatus_names = []
        module_type = "general"

    # --- Config fields (UUID prepended below after registry writes) ---
    config: ConfigSchema = {"module-type": module_type}
    if apparatus_names:
        config["apparati"] = apparatus_names

    if module_type == "library":
        print("\n  Works directory (relative to repo root).")
        print("  This is where archivist scans for catalogued works.")
        config["works-dir"] = input("  works-dir [works]: ").strip() or "works"

    print("\n  Changelog output directory (relative to repo root).")
    print("  Leave blank to use defaults (ARCHIVE/ or ARCHIVE/CHANGELOG/ by module type).")
    changelog_dir = input("  changelog-output-dir: ").strip()
    if changelog_dir:
        config["changelog-output-dir"] = changelog_dir

    config["templater"] = _prompt_templater_mode()
    config["ignores"] = []

    # --- git_remote resolution (apparatus modules only; spec §9) ---
    git_remote: str | None = None
    git_remote_name: str | None = None
    if is_apparatus:
        progress("\n  Resolving this module's git remote...")
        git_remote, git_remote_name = _resolve_git_remote(git_root)
        if git_remote:
            config["git-remote"] = git_remote
        if git_remote_name:
            config["git-remote-name"] = git_remote_name

    # --- UUID: registry is source of truth for apparatus modules ---
    #
    # For apparatus modules: register_apparatus() + register_module() run now,
    # before the config write, so the UUID flows registry → config (per handoff
    # pattern). Both calls are idempotent upserts — safe to run before the user
    # confirms. Worst case they say "no" and we've done a registry upsert that's
    # self-consistent with the next run.
    #
    # For standalone modules: preserve existing UUID or generate a fresh one.
    existing_uuid: str | None = (existing or {}).get("uuid")  # type: ignore[assignment]
    module_uuid: str

    if is_apparatus and not dry_run:
        assert apparatus_names  # guaranteed non-empty — see the is_apparatus downgrade above
        # Register-apparatus-then-module-then-extras is the exact same dance
        # `archivist add` needs — one shared helper in registry.py now, not
        # two copies quietly drifting apart. See CODE_CONVENTIONS.md.
        module_uuid = register_module_with_apparati(
            git_root,
            apparatus_names,
            module_type,
            git_remote,
            git_remote_name
        )
    else:
        module_uuid = existing_uuid or str(_uuid_module.uuid4())

    # uuid is always the first field written to config — spec requirement
    final_config: ConfigSchema = { "uuid": module_uuid }
    final_config.update(config)

    # --- Preview ---
    progress("\n  .archivist/config.yaml will be written as:")
    for k, v in final_config.items():
        progress(f"     { k }: { v }")

    if dry_run:
        progress("\n  [dry-run] No files written.")
        if is_apparatus:
            apparati_str = ", ".join(f"'{n}'" for n in apparatus_names)
            progress(
                f"  [dry-run] Would register apparatus/apparati {apparati_str} "
                f"and upsert module '{get_project_name(git_root)}'."
            )
            superproject_root = get_superproject_root(git_root)
            if superproject_root:
                progress(
                    f"  [dry-run] This is a submodule of { superproject_root } — "
                    "would check for a registered container and link if found."
                )
        if module_type == "library":
            progress("  [dry-run] Would write: .archivist/sample-changelog.py")
        progress("  [dry-run] Would stage: .archivist/")
        if was_flat_config:
            progress("  [dry-run] Would stage: .archivist deletion (flat file)")
        return

    # --- Confirm config write ---
    if not _confirm("Write .archivist/config.yaml?", default = True):
        progress("Aborted.")
        sys.exit(0)

    write_archivist_config(git_root, final_config)
    success("Written: .archivist/config.yaml")

    if module_type == "library":
        _write_sample_changelog(git_root)

    _stage_archivist_config(git_root, was_flat_config)

    # --- Confirm hook install (separate decision from config) ---
    if was_flat_config:
        warning(
            "  Heads up: this project just moved off the flat .archivist file. "
            "\n  Any existing hooks test for it with `[ -f .archivist ]`, which is "
            "\n  now false — they'll silently do nothing on every commit until synced."
        )
    print(
        "\n  Git hooks handle changelog sealing, manifest backfill, and pre-commit"
        "\n  prompts. To seed future clones automatically, run `archivist hooks install`."
    )
    if _confirm("Install git hooks for this repo?", default = True):
        _install_hooks_local(git_root)
    else:
        progress(
            "  Skipping hooks. Run `archivist hooks sync` any time to install them."
        )

    progress(
        "\n  Open .archivist/config.yaml and fill out `ignores` to exclude files and"
        "\n  directories from frontmatter and reclassify operations."
        "\n  Standard .gitignore patterns — same syntax, same rules."
    )

    # --- Containment: is this module itself nested inside another? ---
    #
    # Only meaningful for apparatus modules — standalone ones never get a
    # modules-table row for a bay to reference. This is exactly the case
    # `add` doesn't cover on its own: a module that was `git submodule add`ed
    # by hand (or predates Archivist entirely) and is only now being wired
    # up via `init`. Detected through git's own submodule metadata
    # (get_superproject_root), not by crawling parent directories and
    # hoping — so it's correct no matter how deep git_root sits.
    if is_apparatus:
        superproject_root = get_superproject_root(git_root)
        if superproject_root:
            container_row = resolve_container_module(superproject_root)
            if container_row:
                link_module_into_container(container_row, module_uuid, git_root)
                success(
                    f"Bay registered: { container_row['name'] } ← { get_project_name(git_root) }"
                )
                if container_row.get("module_type") == "vault":
                    progress(f"  Added '{container_row['name']}' to this module's vaults list.")
            else:
                progress(
                    "  This is a git submodule, but its superproject isn't a registered "
                    "Apparatus container — no bay row created. Run `archivist init` up "
                    "there too if it should be one."
                )

    progress("Done. Run `archivist --help` to see available commands.")