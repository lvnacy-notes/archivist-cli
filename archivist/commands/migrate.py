"""
archivist migrate

One-shot migration from the legacy flat `.archivist` config file to the
`.archivist/` directory form.

What it does:
  1. Reads the existing flat `.archivist` file
  2. Creates `.archivist/` directory
  3. Writes the config to `.archivist/config.yaml`
  4. Copies `sample-changelog.py` from the package bundle if module-type is library
  5. Deletes the flat `.archivist` file
  6. Stages both sides of the migration
  7. Offers to sync git hooks locally so they recognise the new config form

What it does NOT do:
  - Touch any other files in the repo
  - Modify the config content in any way — it's a structural migration, not a
    values migration. Whatever was in `.archivist` is what ends up in
    `.archivist/config.yaml`. Exactly. Word for word.
  - Run if `.archivist/config.yaml` already exists — the job is already done.
  - Run if there's no `.archivist` flat file to migrate from.
  - Touch global hook templates — that's `archivist hooks install`'s job.

This command is intentionally narrow. It does one thing, it tells you exactly
what it did, and it gets out of your way. Run it once per project. Then never
again.
"""

import argparse
import importlib.resources
import subprocess
import sys
from pathlib import Path

from archivist.utils import (
    get_repo_root,
    progress,
    read_archivist_config,
    success,
    write_archivist_config,
)


def _get_legacy_path(git_root: Path) -> Path:
    return git_root / ".archivist"


def _get_config_yaml_path(git_root: Path) -> Path:
    return git_root / ".archivist" / "config.yaml"


def _copy_sample_changelog(git_root: Path, dry_run: bool) -> None:
    """
    Copy the bundled sample-changelog.py into .archivist/ if the project
    is a library module and the file isn't already there.

    Non-fatal on read failure — the migration succeeds without it, and the
    user can always grab it manually from the Archivist repo.
    """
    dest = git_root / ".archivist" / "sample-changelog.py"

    if dest.exists():
        progress(f"  sample-changelog.py already present — leaving it alone.")
        return

    try:
        ref = importlib.resources.files("archivist.data").joinpath("sample-changelog.py")
        content = ref.read_text(encoding="utf-8")
    except Exception as e:
        progress(
            f"  ⚠️  Couldn't read bundled sample-changelog.py: {e}\n"
            "     Migration will complete without it. Grab it from the Archivist\n"
            "     repo if you need the plugin reference."
        )
        return

    if dry_run:
        progress(f"  [dry-run] Would write: .archivist/sample-changelog.py")
        return

    dest.write_text(content, encoding="utf-8")
    success(f"  Written: .archivist/sample-changelog.py")


def _sync_hooks_local(git_root: Path, dry_run: bool) -> None:
    """
    Sync hooks into this repo only.

    The old hooks used `[ -f .archivist ]` which is false for a directory,
    so every post-migration commit would run with broken hooks until the user
    remembered to sync manually. Offering this here closes that gap without
    making it mandatory or touching global templates.

    Non-fatal — a hook sync failure is annoying but doesn't invalidate the
    config migration. Warn loudly and let the user finish by hand if needed.
    """
    from archivist.commands.hooks.install import install_hooks_local
    try:
        install_hooks_local(git_root, dry_run=dry_run)
    except Exception as e:
        progress(
            f"  ⚠️  Hook sync failed — migration succeeded but hooks are stale.\n"
            f"     Run `archivist hooks sync` to fix it.\n"
            f"     ({e})"
        )


def run(args: argparse.Namespace) -> None:
    git_root = get_repo_root()
    dry_run: bool = getattr(args, "dry_run", False)

    legacy_path = _get_legacy_path(git_root)
    config_yaml_path = _get_config_yaml_path(git_root)

    print(f"\n  📁 Repo root: {git_root}")

    # --- Guard: already migrated ---
    if config_yaml_path.exists():
        progress(
            "  .archivist/config.yaml already exists. "
            "Nothing to migrate — you're already on the directory form."
        )
        sys.exit(0)

    # --- Guard: nothing to migrate from ---
    if not legacy_path.exists() or legacy_path.is_dir():
        print(
            "❌  No flat .archivist file found. Nothing to migrate.\n"
            "   If you're starting fresh, run `archivist init` instead.",
            file=sys.stderr,
        )
        sys.exit(1)

    # --- Read the existing config ---
    config = read_archivist_config(git_root)
    if config is None:
        # Should be unreachable given the guard above, but be explicit.
        print(
            "❌  Found .archivist but couldn't read it. "
            "Check the file for YAML errors before retrying.",
            file=sys.stderr,
        )
        sys.exit(1)

    module_type = config.get("module-type") if config else None

    # --- Preview ---
    print(f"\n  Migration plan:")
    print(f"    Read   : .archivist  (flat file)")
    print(f"    Create : .archivist/ (directory)")
    print(f"    Write  : .archivist/config.yaml")
    if module_type == "library":
        print(f"    Write  : .archivist/sample-changelog.py  (if not present)")
    print(f"    Delete : .archivist  (flat file)")
    print(f"    Stage  : .archivist/ + .archivist deletion")
    print(f"\n  Config content (unchanged):")
    for k, v in config.items():
        print(f"    {k}: {v}")

    if dry_run:
        progress("\n  [dry-run] No files written, deleted, or staged.")
        return

    # --- Confirm ---
    answer = input(
        "\n  This will delete the flat .archivist file. "
        "It's not recoverable unless you're in git. Proceed? [y/N] "
    ).strip().lower()
    if answer not in ("y", "yes"):
        progress("  Aborted.")
        sys.exit(0)

    # --- Execute ---

    # 1. Create .archivist/ and write config.yaml.
    #    write_archivist_config handles the flat-file eviction internally,
    #    but migrate already read the config so we unlink explicitly here
    #    for the progress message. Order: read → unlink → mkdir → write.
    legacy_path.unlink()
    progress(f"  🗑   Deleted: .archivist (flat file)")

    write_archivist_config(git_root, config)
    success(f"  Written: .archivist/config.yaml")

    # 2. Sample changelog for library projects.
    if module_type == "library":
        _copy_sample_changelog(git_root, dry_run=False)

    # 3. Stage both sides of the migration automatically — same pattern as
    #    `git submodule add`, which stages .gitmodules and the submodule
    #    directory without asking. The deletion and the new directory are one
    #    logical operation; they should land in the index together.
    try:
        subprocess.run(
            ["git", "add", ".archivist/"],
            check = True,
            cwd = git_root,
            capture_output = True,
        )
        # Stage the flat file deletion. git add on a deleted path records the
        # removal in the index — equivalent to git rm --cached but works whether
        # the file is already gone from disk (which it is) or not.
        subprocess.run(
            ["git", "add", ".archivist"],
            check = True,
            cwd = git_root,
            capture_output = True,
        )
        success("  Staged: .archivist/ (new) + .archivist deletion")
    except subprocess.CalledProcessError as e:
        progress(
            "  ⚠️  Auto-staging failed — stage manually before committing:\n"
            "     git add .archivist/\n"
            f"     ({e})"
        )

    # 4. Offer to sync hooks locally. The old flat-file hooks check `[ -f
    #    .archivist ]` which is false for a directory — they're broken the
    #    moment migration completes. Syncing now fixes that immediately.
    #    Global templates are the user's call; `archivist hooks install` handles
    #    that separately.
    print(
        "\n  The existing git hooks check for a flat .archivist file and will"
        "\n  silently skip all archivist work on migrated repos until updated."
    )
    answer = input("  Sync hooks for this repo now? [Y/n] ").strip().lower()
    if answer in ("", "y", "yes"):
        _sync_hooks_local(git_root, dry_run=False)
    else:
        progress(
            "  Skipping hook sync. Run `archivist hooks sync` before your next commit"
            " or changelog sealing will not work."
        )

    # --- Done ---
    print(
        "\n  Migration complete. Commit when ready:\n"
        "\n"
        "      git commit -m 'chore: migrate .archivist to directory form'\n"
        "\n"
        "  If your .gitignore mentions .archivist specifically, update it.\n"
        "  If it ignores dotfiles wholesale, you may need to un-ignore .archivist/."
    )