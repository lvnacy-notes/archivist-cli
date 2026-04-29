"""
archivist init

Interactive project setup. Writes .archivist/config.yaml and optionally
installs git hooks locally. Safe to re-run at any time — idempotent, never
clobbers existing config without asking.
"""

import argparse
import importlib.resources
import sys
from pathlib import Path

from archivist.utils import (
    APPARATUS_MODULE_TYPES,
    get_archivist_config_path,
    get_repo_root,
    read_archivist_config,
    progress,
    success,
    write_archivist_config,
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
        content = ref.read_text(encoding="utf-8")
    except Exception as e:
        # Non-fatal — the plugin system works fine without the sample file.
        # The user just won't have the reference. Tell them why.
        progress(
            f"  ⚠️  Couldn't read bundled sample-changelog.py: {e}\n"
            "     You can grab it from the Archivist repo if you need it."
        )
        return

    dest.write_text(content, encoding="utf-8")
    success(f"  Written: .archivist/sample-changelog.py")
    progress(
        "     Rename it to changelog.py when you're ready to customise.\n"
        "     It runs as-is — start there."
    )


def _prompt(question: str, options: list[str], default: str | None = None) -> str:
    """
    Present a numbered list of options and return the user's choice.
    Loops until valid input is received.
    """
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


def _install_hooks_local(git_root: Path, dry_run: bool = False) -> None:
    """Install hooks into this repo only. Global templates are the user's call."""
    from archivist.commands.hooks.install import install_hooks_local
    install_hooks_local(git_root, dry_run=dry_run)


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
        ["resolve", "preserve", "false"],
        default="preserve",
    )


def run(args: argparse.Namespace) -> None:
    git_root = get_repo_root()
    existing = read_archivist_config(git_root)
    dry_run = getattr(args, "dry_run", False)

    print(f"\n  📁 Repo root: {git_root}")

    # --- Existing config ---
    if existing is not None:
        existing_path = get_archivist_config_path(git_root)
        success(f"Found existing config: {existing_path.relative_to(git_root)}")
        for k, v in existing.items():
            print(f"     {k}: {v}")

        if not _confirm("Update configuration?", default=False):
            # Offer hook reinstall even if config unchanged
            if _confirm("Reinstall git hooks?", default=True):
                _install_hooks_local(git_root, dry_run=dry_run)
            progress("Done.")
            return

    # --- Apparatus project? ---
    is_apparatus = _confirm("Is this an Apparatus project?", default=True)

    if is_apparatus:
        module_type = _prompt(
            "Select module type:",
            APPARATUS_MODULE_TYPES,
        )
        config: dict[str, str | list[str]] = {
            "apparatus":   "true",
            "module-type": module_type,
        }
        if module_type == "library":
            print("\n  Works directory (relative to repo root).")
            print("  This is where archivist scans for catalogued works.")
            works_dir = input("  works-dir [works]: ").strip() or "works"
            config["works-dir"] = works_dir
    else:
        module_type = "general"
        config: dict[str, str | list[str]] = {
            "apparatus":   "false",
            "module-type": module_type,
        }

    # --- Custom changelog output directory (optional) ---
    print("\n  Changelog output directory (relative to repo root).")
    print("  Leave blank to use defaults (ARCHIVE/ or ARCHIVE/CHANGELOG/ by module type).")
    changelog_dir = input("  changelog-output-dir: ").strip()
    if changelog_dir:
        config["changelog-output-dir"] = changelog_dir

    # --- Templater mode ---
    config["templater"] = _prompt_templater_mode()

    # --- Ignores (always seeded, filled in by the user afterward) ---
    config["ignores"] = []

    # --- Preview ---
    print(f"\n  .archivist/config.yaml will be written as:")
    for k, v in config.items():
        print(f"     {k}: {v}")

    if dry_run:
        progress("  [dry-run] No files written.")
        if is_apparatus and module_type == "library":
            progress("  [dry-run] Would write: .archivist/sample-changelog.py")
        return

    # --- Confirm config write ---
    if not _confirm("Write .archivist/config.yaml?", default=True):
        progress("Aborted.")
        sys.exit(0)

    write_archivist_config(git_root, config)
    success(f"Written: .archivist/config.yaml")

    if is_apparatus and module_type == "library":
        _write_sample_changelog(git_root)

    # --- Confirm hook install (separate decision from config) ---
    print(
        "\n  Git hooks handle changelog sealing, manifest backfill, and pre-commit"
        "\n  prompts. To seed future clones automatically, run `archivist hooks install`."
    )
    if _confirm("Install git hooks for this repo?", default=True):
        _install_hooks_local(git_root)
    else:
        progress(
            "  Skipping hooks. Run `archivist hooks sync` any time to install them."
        )

    print(
        "\n  Open .archivist/config.yaml and fill out `ignores` to exclude files and"
        "\n  directories from frontmatter and reclassify operations."
        "\n  Standard .gitignore patterns — same syntax, same rules."
    )

    progress("Done. Run `archivist --help` to see available commands.")