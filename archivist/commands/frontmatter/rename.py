"""
archivist frontmatter rename

Rename a property key across all notes in the repo, preserving its value exactly.
Handles scalar values, inline lists, and multi-line block sequences.
"""

import argparse
import sys
from pathlib import Path

from archivist.utils import (
    find_markdown_files,
    get_repo_root,
    match_property_line,
    print_dry_run_header,
    process_markdown_files,
    progress,
    success,
    update_frontmatter_in_file,
    warning,
)


def _rename_property_in_raw_fm(raw_fm: str, old_prop: str, new_prop: str) -> tuple[str, bool]:
    """
    Rename a property key in raw YAML frontmatter, preserving its value exactly.
    Returns (updated_frontmatter, was_found).

    Continuation lines indented with spaces OR tabs are preserved verbatim —
    both are valid YAML indentation, and we're not here to litigate your style
    choices, just to rename your shit correctly.
    """
    lines = raw_fm.split("\n")
    result = []
    i = 0
    found = False

    while i < len(lines):
        line = lines[i]
        if match_property_line(line, old_prop):
            found = True
            result.append(line.replace(old_prop, new_prop, 1))
            i += 1
            while i < len(lines) and lines[i].startswith((" ", "\t")):
                result.append(lines[i])
                i += 1
        else:
            result.append(line)
            i += 1

    return "\n".join(result), found


def _process_note(note_path: Path, old_prop: str, new_prop: str, dry_run: bool) -> bool:
    """
    Rename old_prop → new_prop in a single note's frontmatter.
    Returns True if a change was made (or would be in dry-run mode).
    """
    found_and_changed = False

    def _transformer(raw_fm: str, body: str) -> str | None:
        nonlocal found_and_changed
        updated_fm, found = _rename_property_in_raw_fm(raw_fm, old_prop, new_prop)
        if not found:
            return None
        found_and_changed = True
        if dry_run:
            progress(f"  [dry-run] Would rename '{old_prop}' → '{new_prop}' in: {note_path}")
            return None  # signal no-write; found_and_changed is already set
        success(f"Renamed '{old_prop}' → '{new_prop}' in: {note_path}")
        return f"---\n{updated_fm}\n---\n{body}"

    update_frontmatter_in_file(note_path, _transformer)
    return found_and_changed


def run(args: argparse.Namespace) -> None:
    if args.property == args.new_name:
        print("❌  You gave me the same name twice. Renaming a property to itself is just\n"
              "    called 'doing nothing', and you didn't need me for that.")
        sys.exit(1)

    root = get_repo_root()
    files = find_markdown_files(root)

    if not files:
        warning(f"No .md files found under '{root}'.")
        sys.exit(0)

    if args.dry_run:
        print_dry_run_header()

    progress(f"Root: {root}")
    progress(f"Scanning {len(files)} file(s) to rename '{args.property}' → '{args.new_name}'...\n")

    def _callback(f: Path) -> bool:
        return _process_note(f, args.property, args.new_name, args.dry_run)

    changed = process_markdown_files(root, _callback)

    label = "would be updated" if args.dry_run else "updated"
    progress(f"\nDone. {changed}/{len(files)} file(s) {label}.")