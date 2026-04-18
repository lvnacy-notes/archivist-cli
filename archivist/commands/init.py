"""
archivist init

Interactive project setup. Writes .archivist config and installs git hooks.
Safe to re-run at any time — idempotent, never clobbers existing config without asking.
"""

import argparse
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
        print(f"  Please enter a number between 1 and {len(options)}.")


def _confirm(question: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    raw = input(f"\n{question} {hint}: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _install_hooks(git_root: Path, dry_run: bool = False) -> None:
    """Delegate to the hooks install command."""
    from archivist.commands.hooks.install import install_hooks
    install_hooks(git_root, dry_run=dry_run)


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
    config_path = get_archivist_config_path(git_root)
    existing = read_archivist_config(git_root)

    print(f"\n  📁 Repo root: {git_root}")

    # --- Existing config ---
    if existing is not None:
        success("Found existing .archivist config:")
        for k, v in existing.items():
            print(f"     {k}: {v}")

        if not _confirm("Update configuration?", default=False):
            # Offer hook reinstall even if config unchanged
            if _confirm("Reinstall git hooks?", default=True):
                _install_hooks(git_root, dry_run=getattr(args, "dry_run", False))
            progress("Done.")
            return

    # --- Apparatus project? ---
    is_apparatus = _confirm("Is this an Apparatus project?", default=True)

    if is_apparatus:
        module_type = _prompt(
            "Select module type:",
            APPARATUS_MODULE_TYPES,
        )
        config = {
            "apparatus":   "true",
            "module-type": module_type,
        }
        if module_type == "library":
            print("\n  Works directory (relative to repo root).")
            print("  This is where archivist scans for catalogued works.")
            works_dir = input("  works-dir [works]: ").strip() or "works"
            config["works-dir"] = works_dir
    else:
        config = {
            "apparatus":   "false",
            "module-type": "general",
        }

    # --- Custom changelog output directory (optional) ---
    print("\n  Changelog output directory (relative to repo root).")
    print("  Leave blank to use defaults (ARCHIVE/ or ARCHIVE/CHANGELOG/ by module type).")
    changelog_dir = input("  changelog-output-dir: ").strip()
    if changelog_dir:
        config["changelog-output-dir"] = changelog_dir

    # --- Templater mode ---
    config["templater"] = _prompt_templater_mode()

    # --- Preview / write ---
    print(f"\n  .archivist will be written as:")
    for k, v in config.items():
        print(f"     {k}: {v}")

    if getattr(args, "dry_run", False):
        progress("  [dry-run] No files written.")
        return

    if not _confirm("Write .archivist and install hooks?", default=True):
        progress("Aborted.")
        sys.exit(0)

    write_archivist_config(git_root, config)
    success(f"Written: {config_path}")

    _install_hooks(git_root)

    progress("Done. Run `archivist --help` to see available commands.")