"""
archivist reclassify

Find all .md files in the repo (or a scoped path) where frontmatter `class`
matches a given value and replace it with a new one. Surgical rewrite —
only the `class:` line is touched, nothing else in the frontmatter moves.

    archivist reclassify --from article --to column
    archivist reclassify --from article --to column --path content/
    archivist reclassify --from article --to column --dry-run

Matching is case-insensitive. The --to value is written exactly as given.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from archivist.utils import (
    NoteFilter,
    build_note_filter,
    error,
    find_markdown_files,
    get_file_frontmatter,
    get_repo_root,
    matches_class_filter,
    print_dry_run_header,
    progress,
    resolve_file_targets,
    safe_read_markdown,
    safe_write_markdown,
    validate_note_filter,
    warning,
)

# Matches the class: line within a frontmatter block.
# Handles bare, single-quoted, and double-quoted scalar values.
_CLASS_LINE_RE = re.compile(r"^(class:\s*)['\"]?(.+?)['\"]?\s*$")


def _find_frontmatter_end(lines: list[str]) -> int | None:
    """
    Return the line index of the closing `---` of a frontmatter block,
    or None if the file does not open with a valid frontmatter block.
    """
    if not lines or lines[0].rstrip() != "---":
        return None
    for i, line in enumerate(lines[1:], start=1):
        if line.rstrip() == "---":
            return i
    return None


def _rewrite_class(content: str, old_val: str, new_val: str) -> str | None:
    """
    Replace `class: <old_val>` with `class: <new_val>` within the frontmatter
    block only. Matching is case-insensitive; the new value is written verbatim.

    Returns the rewritten content string, or None if the line was not found
    (so the caller can warn without crashing).
    """
    lines = content.split("\n")
    end_idx = _find_frontmatter_end(lines)
    if end_idx is None:
        return None

    new_lines = list(lines)
    for i in range(1, end_idx):
        m = _CLASS_LINE_RE.match(lines[i])
        if m and m.group(2).strip().lower() == old_val.lower():
            new_lines[i] = m.group(1) + new_val
            return "\n".join(new_lines)

    return None


def run(args: argparse.Namespace) -> None:
    git_root = get_repo_root()

    old_val = args.from_class.strip()
    new_val = args.to_class.strip()

    if old_val.lower() == new_val.lower():
        error("--from and --to resolve to the same value. Nothing to do.")
        sys.exit(1)

    # Build a NoteFilter from args so reclassify goes through the same
    # file resolution path as the frontmatter commands — including ignores.
    nf = build_note_filter(args)
    validate_note_filter(nf, require_at_least_one=False, command_name="reclassify")

    all_files = resolve_file_targets(nf, git_root)

    # --- Find matching files via frontmatter parse ---
    matched: list[Path] = []
    for filepath in all_files:
        fm: dict[str, str | list[str]] | None = get_file_frontmatter(filepath)
        if fm is None:
            continue
        if matches_class_filter(fm, old_val):
            matched.append(filepath)

    if not matched:
        progress(f"  No files found with class: {old_val}")
        return

    # --- Dry run ---
    if args.dry_run:
        print_dry_run_header()
        print()
        progress(
            f"  Would reclassify {len(matched)} file(s): "
            f"class: {old_val}  →  class: {new_val}\n"
        )
        for f in matched:
            try:
                rel = f.relative_to(git_root)
            except ValueError:
                rel = f
            progress(f"  · {rel}")
        return

    # --- Live run ---
    updated = 0
    failed = 0
    for filepath in matched:
        try:
            rel = filepath.relative_to(git_root)
        except ValueError:
            rel = filepath

        content = safe_read_markdown(filepath)
        if content is None:
            failed += 1
            continue

        new_content = _rewrite_class(content, old_val, new_val)
        if new_content is None:
            warning(f"Could not locate class: line in frontmatter — skipping {rel}")
            failed += 1
            continue

        if not safe_write_markdown(filepath, new_content):
            failed += 1
            continue

        progress(f"  ✓ {rel}")
        updated += 1

    progress(
        f"\n  Reclassified {updated} file(s):  class: {old_val}  →  class: {new_val}"
    )
    if failed:
        warning(f"{failed} file(s) skipped — see warnings above")