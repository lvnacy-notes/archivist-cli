"""
archivist frontmatter remove

Remove a property from the YAML frontmatter of every note in the repo.
If removing the property leaves the frontmatter block empty, the block
is dropped entirely. Don't worry — the note survives. Probably.
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
    print_dry_run_header,
    process_markdown_files,
    progress,
    remove_property_from_frontmatter,
    safe_read_markdown,
    safe_write_markdown,
    success,
    warning,
)


def _process_note(note_path: Path, prop: str, dry_run: bool) -> bool:
    """Process a single note. Returns True if a change was made (or would be)."""
    content = safe_read_markdown(note_path)
    if content is None:
        return False

    if not has_frontmatter(content):
        return False

    match = FRONTMATTER_RE.match(content)
    if not match:
        return False
    raw_fm = match.group(1)
    body = content[match.end():]

    updated_fm, found = remove_property_from_frontmatter(raw_fm, prop)
    if not found:
        return False

    new_content = f"---\n{updated_fm}\n---\n{body}" if updated_fm.strip() else body

    if dry_run:
        progress(f"  [dry-run] Would remove '{prop}' from: {note_path}")
        return True

    if not safe_write_markdown(note_path, new_content):
        return False

    success(f"Removed '{prop}' from: {note_path}")
    return True


def run(args: argparse.Namespace) -> None:
    root = get_repo_root()

    files = find_markdown_files(root)
    if not files:
        warning(f"No .md files found under '{root}'.")
        sys.exit(0)

    if args.dry_run:
        print_dry_run_header()

    progress(f"Root: {root}")
    progress(f"Scanning {len(files)} file(s) for property '{args.property}'...\n")

    def _callback(f: Path) -> bool:
        return _process_note(f, args.property, args.dry_run)

    changed = process_markdown_files(root, _callback)

    label = "would be updated" if args.dry_run else "updated"
    progress(f"\nDone. {changed}/{len(files)} file(s) {label}.")