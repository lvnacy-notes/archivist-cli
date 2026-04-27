"""
archivist frontmatter remove

Remove a property from the YAML frontmatter of every note that matches the
selection criteria. With no selection flags, operates on the entire repo.
If removing the property leaves the frontmatter block empty, the block is
dropped entirely. The note survives. Probably.

Selection flags (all optional, combinable except --file):
  --file   Exactly one note. Mutually exclusive with everything else.
  --path   Limit the walk to this directory subtree.
  --class  Only notes whose 'class' frontmatter value matches.
  --tag    Only notes carrying this tag.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from archivist.utils import (
    FRONTMATTER_RE,
    NoteFilter,
    build_note_filter,
    get_repo_root,
    has_frontmatter,
    note_matches_filter,
    print_dry_run_header,
    progress,
    remove_property_from_frontmatter,
    resolve_file_targets,
    safe_read_markdown,
    safe_write_markdown,
    success,
    validate_note_filter,
    warning,
)


def _process_note(note_path: Path, prop: str, dry_run: bool, nf: NoteFilter) -> bool:
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

    if not note_matches_filter(nf, raw_fm):
        return False

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
    nf = build_note_filter(args)
    validate_note_filter(nf, require_at_least_one=False, command_name="frontmatter remove")

    root = get_repo_root()

    if args.dry_run:
        print_dry_run_header()

    progress(f"Root: {root}")

    if nf.active_filter_labels:
        progress(f"Filters: {' AND '.join(nf.active_filter_labels)}")

    files = resolve_file_targets(nf, root)
    if not files:
        warning("No .md files found matching the given criteria.")
        sys.exit(0)

    progress(f"Scanning {len(files)} file(s) for property '{args.property}'...\n")

    def _callback(f: Path) -> bool:
        return _process_note(f, args.property, args.dry_run, nf)

    changed = sum(1 for f in files if _callback(f))

    label = "would be updated" if args.dry_run else "updated"
    progress(f"\nDone. {changed}/{len(files)} file(s) {label}.")