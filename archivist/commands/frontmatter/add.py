"""
archivist frontmatter add

Add a property to the YAML frontmatter of every note in the repo.
Scopes automatically to the current git repo (or submodule) root.
"""

import argparse
import re
import sys
from pathlib import Path

from archivist.utils import FRONTMATTER_RE, get_repo_root


def _property_exists(frontmatter: str, prop: str) -> bool:
    return any(
        re.match(rf"^{re.escape(prop)}\s*:", line)
        for line in frontmatter.split("\n")
    )


def _remove_property(frontmatter: str, prop: str) -> str:
    """Strip a property and its indented continuation lines."""
    lines = frontmatter.split("\n")
    result = []
    i = 0
    while i < len(lines):
        if re.match(rf"^{re.escape(prop)}\s*:", lines[i]):
            i += 1
            while i < len(lines) and lines[i].startswith(" "):
                i += 1
        else:
            result.append(lines[i])
            i += 1
    return "\n".join(result)


def _process_note(
    note_path: Path,
    prop: str,
    value: str | None,
    overwrite: bool,
    dry_run: bool,
) -> bool:
    """Process a single note. Returns True if a change was made (or would be)."""
    try:
        content = note_path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"  ⚠️  Could not read '{note_path}': {e}")
        return False

    match = FRONTMATTER_RE.match(content)
    new_line = f"{prop}: {value}" if value is not None else f"{prop}:"

    if match:
        raw_frontmatter = match.group(1)
        body = content[match.end():]

        if _property_exists(raw_frontmatter, prop):
            if not overwrite:
                return False
            raw_frontmatter = _remove_property(raw_frontmatter, prop)

        updated = raw_frontmatter.rstrip("\n") + f"\n{new_line}"
        new_content = f"---\n{updated}\n---\n{body}"
    else:
        new_content = f"---\n{new_line}\n---\n{content}"

    if dry_run:
        print(f"  [dry-run] Would add '{new_line}' to: {note_path}")
    else:
        note_path.write_text(new_content, encoding="utf-8")
        print(f"  ✅ Added '{new_line}' to: {note_path}")

    return True


def run(args: argparse.Namespace) -> None:
    root = get_repo_root()
    files = sorted(root.rglob("*.md"))

    if not files:
        print(f"⚠️  No .md files found under '{root}'.")
        sys.exit(0)

    if args.dry_run:
        print("=== DRY RUN — nothing will be written ===")

    action = (
        f"'{args.property}: {args.value}'"
        if args.value is not None
        else f"'{args.property}:'"
    )
    print(f"Root: {root}")
    print(f"Scanning {len(files)} file(s) to add {action}...\n")

    changed = 0
    for f in files:
        if _process_note(f, args.property, args.value, args.overwrite, args.dry_run):
            changed += 1

    label = "would be updated" if args.dry_run else "updated"
    print(f"\nDone. {changed}/{len(files)} file(s) {label}.")