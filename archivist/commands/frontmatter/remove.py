"""
archivist frontmatter remove

Remove a property from the YAML frontmatter of every note in the repo.
If removing the property leaves the frontmatter block empty, the block
is dropped entirely.
"""

import argparse
import re
import sys
from pathlib import Path

from archivist.utils import FRONTMATTER_RE, get_repo_root


def _remove_property(frontmatter: str, prop: str) -> tuple[str, bool]:
    """
    Remove a property and its value from raw YAML frontmatter.
    Handles scalar values, inline lists, and multi-line block sequences.
    Returns (updated_frontmatter, was_found).
    """
    lines = frontmatter.split("\n")
    result = []
    i = 0
    found = False

    while i < len(lines):
        line = lines[i]
        if re.match(rf"^{re.escape(prop)}\s*:", line):
            found = True
            i += 1
            while i < len(lines) and lines[i].startswith(" "):
                i += 1
        else:
            result.append(line)
            i += 1

    return "\n".join(result), found


def _process_note(note_path: Path, prop: str, dry_run: bool) -> bool:
    """Process a single note. Returns True if a change was made (or would be)."""
    try:
        content = note_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"  ⚠️  Could not read '{note_path}': {e}")
        return False

    match = FRONTMATTER_RE.match(content)
    if not match:
        return False

    raw_frontmatter = match.group(1)
    body = content[match.end():]

    updated_frontmatter, found = _remove_property(raw_frontmatter, prop)
    if not found:
        return False

    if updated_frontmatter.strip():
        new_content = f"---\n{updated_frontmatter}\n---\n{body}"
    else:
        new_content = body  # drop the block entirely if now empty

    if dry_run:
        print(f"  [dry-run] Would remove '{prop}' from: {note_path}")
    else:
        note_path.write_text(new_content, encoding="utf-8")
        print(f"  ✅ Removed '{prop}' from: {note_path}")

    return True


def run(args: argparse.Namespace) -> None:
    root = get_repo_root()
    files = sorted(root.rglob("*.md"))

    if not files:
        print(f"⚠️  No .md files found under '{root}'.")
        sys.exit(0)

    if args.dry_run:
        print("=== DRY RUN — nothing will be written ===")

    print(f"Root: {root}")
    print(f"Scanning {len(files)} file(s) for property '{args.property}'...\n")

    changed = 0
    for f in files:
        if _process_note(f, args.property, args.dry_run):
            changed += 1

    label = "would be updated" if args.dry_run else "updated"
    print(f"\nDone. {changed}/{len(files)} file(s) {label}.")