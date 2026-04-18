"""
archivist frontmatter add

Add a property to the YAML frontmatter of every note in the repo.
Scopes automatically to the current git repo (or submodule) root.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from archivist.utils import (
    FRONTMATTER_RE,
    find_markdown_files,
    get_repo_root,
    has_frontmatter,
    match_property_line,
    print_dry_run_header,
    process_markdown_files,
    progress,
    remove_property_from_frontmatter,
    safe_read_markdown,
    safe_write_markdown,
    success,
    warning,
)


def _property_exists(raw_fm: str, prop: str) -> bool:
    """Return True if prop already has a key line in the raw frontmatter block."""
    return any(match_property_line(line, prop) for line in raw_fm.split("\n"))


def _process_note(
    note_path: Path,
    prop: str,
    value: str | None,
    overwrite: bool,
    dry_run: bool,
) -> bool:
    """
    Process a single note. Returns True if a change was made (or would be).

    Note: add is the one frontmatter command that can't use update_frontmatter_in_file
    cleanly because it also creates a frontmatter block from scratch when none
    exists. Hence the two-branch structure here. Everything else uses the helpers.
    """
    content = safe_read_markdown(note_path)
    if content is None:
        return False

    new_line = f"{prop}: {value}" if value is not None else f"{prop}:"

    if has_frontmatter(content):
        match = FRONTMATTER_RE.match(content)
        raw_fm = match.group(1)
        body = content[match.end():]

        if _property_exists(raw_fm, prop):
            if not overwrite:
                return False
            raw_fm, _ = remove_property_from_frontmatter(raw_fm, prop)

        updated_fm = raw_fm.rstrip("\n") + f"\n{new_line}"
        new_content = f"---\n{updated_fm}\n---\n{body}"
    else:
        # No frontmatter block at all — conjure one from thin air.
        new_content = f"---\n{new_line}\n---\n{content}"

    if dry_run:
        progress(f"  [dry-run] Would add '{new_line}' to: {note_path}")
    else:
        if not safe_write_markdown(note_path, new_content):
            return False
        success(f"Added '{new_line}' to: {note_path}")

    return True


def run(args: argparse.Namespace) -> None:
    root = get_repo_root()

    if args.dry_run:
        print_dry_run_header()

    action = (
        f"'{args.property}: {args.value}'"
        if args.value is not None
        else f"'{args.property}:'"
    )
    progress(f"Root: {root}")

    def _callback(f: Path) -> bool:
        return _process_note(f, args.property, args.value, args.overwrite, args.dry_run)

    files = find_markdown_files(root)
    if not files:
        warning(f"No .md files found under '{root}'.")
        sys.exit(0)

    progress(f"Scanning {len(files)} file(s) to add {action}...\n")
    changed = process_markdown_files(root, _callback)

    label = "would be updated" if args.dry_run else "updated"
    progress(f"\nDone. {changed}/{len(files)} file(s) {label}.")