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
    get_or_create_apparatus,
    get_or_create_vault,
    get_registry_connection,
    list_apparatuses,
    list_vaults,
    get_repo_root,
    read_archivist_config,
    progress,
    register_module,
    success,
    write_archivist_config,
)


def _confirm(question: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    raw = input(f"\n{question} {hint}: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


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

def _run_apparatus_registration(
    git_root: Path,
    module_type: str,
) -> tuple[int, int | None, str | None, dict[str, str]]:
    """
    Interactive Apparatus/Vault registration flow.

    Fires after the standard init questions when the user has confirmed this
    is an Apparatus module. Queries registry.db for existing Apparatuses and
    Vaults, presents options, creates new entries as needed.

    Returns a tuple of:
        apparatus_id  — int, passed to _finalize_apparatus_registration()
        vault_id      — int | None
        library_tag   — str | None
        config_fields — dict of public config keys to merge into .archivist/config.yaml
                        (apparatus, vault, library-tag — never any internal IDs)

    No module rows are written here. That happens in _finalize_apparatus_registration(),
    which is only called AFTER the user confirms the config write. Apparatus and Vault
    rows ARE written here if the user creates new ones — those are shared resources
    that exist independently of any single module and are safe to create up front.
    """

    conn = get_registry_connection()
    config_fields: dict[str, str] = {}

    try:
        # --- Apparatus selection ---
        apparatuses = list_apparatuses(conn)
        apparatus_options = [a["name"] for a in apparatuses] + ["Create new Apparatus"]

        print("\n  To which Apparatus does this module belong?")
        selected = _prompt("Select:", apparatus_options)

        if selected == "Create new Apparatus":
            new_name = input("  Apparatus name: ").strip()
            if not new_name:
                print("  You need to give it a name. Try again.")
                new_name = input("  Apparatus name: ").strip()
            apparatus_id, created = get_or_create_apparatus(new_name, conn)
            apparatus_name = new_name
            if created:
                progress(f"  ✓ Created Apparatus '{apparatus_name}'")
        else:
            apparatus_name = selected
            apparatus_id = next(a["id"] for a in apparatuses if a["name"] == apparatus_name)
            progress(f"  ✓ Using existing Apparatus '{apparatus_name}'")

        config_fields["apparatus"] = apparatus_name

        # --- Vault selection (optional) ---
        vault_id: int | None = None
        if _confirm("Does this module belong to a Vault?", default=False):
            vaults = list_vaults(apparatus_id, conn)
            vault_options = [v["name"] for v in vaults] + ["Create new Vault"]

            print("\n  Which Vault?")
            selected_vault = _prompt("Select:", vault_options)

            if selected_vault == "Create new Vault":
                new_vault_name = input("  Vault name: ").strip()
                if not new_vault_name:
                    print("  A vault needs a name. I'm not making one up for you.")
                    new_vault_name = input("  Vault name: ").strip()
                vault_id, vault_created = get_or_create_vault(
                    apparatus_id, new_vault_name, git_root, conn
                )
                vault_name = new_vault_name
                if vault_created:
                    progress(f"  ✓ Created Vault '{vault_name}'")
            else:
                vault_name = selected_vault
                vault_id = next(v["id"] for v in vaults if v["name"] == vault_name)
                progress(f"  ✓ Using existing Vault '{vault_name}'")

            config_fields["vault"] = vault_name
        else:
            progress("  Skipping vault membership. Fine.")

        # --- library-tag (library modules only) ---
        library_tag: str | None = None
        if module_type == "library":
            print("\n  Library tag (e.g. 'cosmic-horror', 'victorian-mayhem').")
            print("  Applied alongside 'catalog-works' on every card in this library.")
            raw_tag = input("  library-tag: ").strip()
            if raw_tag:
                library_tag = raw_tag
                config_fields["library-tag"] = library_tag

    finally:
        conn.close()

    return apparatus_id, vault_id, library_tag, config_fields


def _finalize_apparatus_registration(
    git_root: Path,
    apparatus_id: int,
    vault_id: int | None,
    module_type: str,
    library_tag: str | None,
) -> None:
    """
    Write the module row to registry.db.

    Called after the user has confirmed the config write — the point of no
    return. Receives IDs directly from _run_apparatus_registration() so
    write_archivist_config() never has to see any internal state and the
    config file stays clean.

    Registration failure is non-fatal: config is already on disk, the project
    is usable, and the user can re-run `archivist init` to retry.
    """
    conn = get_registry_connection()
    try:
        module_name = git_root.name
        register_module(
            apparatus_id=apparatus_id,
            vault_id=vault_id,
            name=module_name,
            module_type=module_type,
            path=git_root,
            library_tag=library_tag,
            conn=conn,
        )
        success(f"Registered module '{module_name}' in registry.db")
    except Exception as e:
        progress(f"  ⚠️  Failed to register in registry.db: {e}")
        progress("     Run `archivist init` again to retry registration.")
    finally:
        conn.close()


def _install_hooks_local(git_root: Path, dry_run: bool = False) -> None:
    """Install hooks into this repo only. Global templates are the user's call."""
    from archivist.commands.hooks.install import install_hooks_local
    install_hooks_local(git_root, dry_run=dry_run)


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
        progress("  sample-changelog.py already exists — leaving it alone.")
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
    success("  Written: .archivist/sample-changelog.py")
    progress(
        "     Rename it to changelog.py when you're ready to customise.\n"
        "     It runs as-is — start there."
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
            if _confirm("Reinstall git hooks?", default=True):
                _install_hooks_local(git_root, dry_run=dry_run)
            progress("Done.")
            return

    # --- Apparatus project? ---
    is_apparatus = _confirm("Is this an Apparatus project?", default=True)

    # registration_data holds IDs returned from _run_apparatus_registration()
    # so _finalize_apparatus_registration() can use them without any of this
    # internal state ever touching the config dict or the written config file.
    registration_data: tuple[int, int | None, str | None] | None = None

    if is_apparatus:
        module_type = _prompt("Select module type:", APPARATUS_MODULE_TYPES)
        config: dict[str, str | list[str]] = {
            "apparatus":   "true",
            "module-type": module_type,
        }

        if module_type == "library":
            print("\n  Directory paths for catalog content (relative to repo root).")
            print("  Hit enter to accept the defaults shown in brackets.")
            works_dir        = input("  works-dir [works]: ").strip() or "works"
            authors_dir      = input("  authors-dir [authors]: ").strip() or "authors"
            publications_dir = input("  publications-dir [publications]: ").strip() or "publications"
            # write_archivist_config handles plain scalars; directories is a nested
            # dict which YAML dumps fine — config.py's write logic just needs to
            # not mangle it. The ignores key gets special treatment; this does not.
            config["directories"] = {  # type: ignore[assignment]
                "works":        works_dir,
                "authors":      authors_dir,
                "publications": publications_dir,
            }

        apparatus_id, vault_id, library_tag, registration_config_fields = (
            _run_apparatus_registration(git_root, module_type)
        )
        registration_data = (apparatus_id, vault_id, library_tag)
        config.update(registration_config_fields)

    else:
        module_type = "general"
        config = {
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

    # --- Preview — config dict is clean; no internal IDs, no dunder keys ---
    print("\n  .archivist/config.yaml will be written as:")
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
    success("Written: .archivist/config.yaml")

    # --- Apparatus registration (module row only — after confirmed config write) ---
    if is_apparatus and registration_data is not None:
        apparatus_id, vault_id, library_tag = registration_data
        _finalize_apparatus_registration(
            git_root, apparatus_id, vault_id, module_type, library_tag
        )

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
        progress("  Skipping hooks. Run `archivist hooks sync` any time to install them.")

    print(
        "\n  Open .archivist/config.yaml and fill out `ignores` to exclude files and"
        "\n  directories from frontmatter and reclassify operations."
        "\n  Standard .gitignore patterns — same syntax, same rules."
    )

    progress("Done. Run `archivist --help` to see available commands.")