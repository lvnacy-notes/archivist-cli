"""
archivist frontmatter rename

Rename a property key across all notes in the repo, preserving its value exactly.
Handles scalar values, inline lists, and multi-line block sequences.
"""

import argparse
import re
import sys
from pathlib import Path

from archivist.utils import FRONTMATTER_RE, get_repo_root


def _rename_property(frontmatter: str, old_prop: str, new_prop: str) -> tuple[str, bool]:
    """
    Rename a property key in raw YAML frontmatter, preserving its value exactly.
    Returns (updated_frontmatter, was_found).
    """
    lines = frontmatter.split("\n")
    result = []
    i = 0
    found = False

    while i < len(lines):
        line = lines[i]
        if re.match(rf"^{re.escape(old_prop)}\s*:", line):
            found = True
            result.append(line.replace(old_prop, new_prop, 1))
            i += 1
            while i < len(lines) and lines[i].startswith(" "):
                result.append(lines[i])
                i += 1
        else:
            result.append(line)
            i += 1

    return "\n".join(result), found


def _process_note(
    note_path: Path,
    old_prop: str,
    new_prop: str,
    dry_run: bool,
) -> bool:
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

    updated_frontmatter, found = _rename_property(raw_frontmatter, old_prop, new_prop)
    if not found:
        return False

    new_content = f"---\n{updated_frontmatter}\n---\n{body}"

    if dry_run:
        print(f"  [dry-run] Would rename '{old_prop}' → '{new_prop}' in: {note_path}")
    else:
        note_path.write_text(new_content, encoding="utf-8")
        print(f"  ✅ Renamed '{old_prop}' → '{new_prop}' in: {note_path}")

    return True


def run(args: argparse.Namespace) -> None:
    if args.property == args.new_name:
        print("❌  Old and new property names are the same. Nothing to do.")
        sys.exit(1)

    root = get_repo_root()
    files = sorted(root.rglob("*.md"))

    if not files:
        print(f"⚠️  No .md files found under '{root}'.")
        sys.exit(0)

    if args.dry_run:
        print("=== DRY RUN — nothing will be written ===")

    print(f"Root: {root}")
    print(f"Scanning {len(files)} file(s) to rename '{args.property}' → '{args.new_name}'...\n")

    changed = 0
    for f in files:
        if _process_note(f, args.property, args.new_name, args.dry_run):
            changed += 1

    label = "would be updated" if args.dry_run else "updated"
    print(f"\nDone. {changed}/{len(files)} file(s) {label}.")