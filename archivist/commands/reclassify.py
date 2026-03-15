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

import re
import sys
from pathlib import Path

from archivist.utils import get_file_frontmatter, get_repo_root

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


def run(args) -> None:
    git_root = get_repo_root()

    search_root = (
        Path(args.path).resolve() if getattr(args, "path", None) else git_root
    )

    old_val = args.from_class.strip()
    new_val = args.to_class.strip()

    if old_val.lower() == new_val.lower():
        print("❌  --from and --to resolve to the same value. Nothing to do.")
        sys.exit(1)

    # --- Find matching files via frontmatter parse ---
    matched: list[Path] = []
    for filepath in sorted(search_root.rglob("*.md")):
        fm = get_file_frontmatter(filepath)
        if fm is None:
            continue
        val = fm.get("class")
        if val is not None and str(val).strip().lower() == old_val.lower():
            matched.append(filepath)

    if not matched:
        print(f"  No files found with class: {old_val}")
        return

    # --- Dry run ---
    if args.dry_run:
        print("=== DRY RUN — no files written ===\n")
        print(
            f"  Would reclassify {len(matched)} file(s): "
            f"class: {old_val}  →  class: {new_val}\n"
        )
        for f in matched:
            try:
                rel = f.relative_to(git_root)
            except ValueError:
                rel = f
            print(f"  · {rel}")
        return

    # --- Live run ---
    updated = 0
    failed = 0
    for filepath in matched:
        try:
            rel = filepath.relative_to(git_root)
        except ValueError:
            rel = filepath

        try:
            content = filepath.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            print(f"  ⚠️  Could not read {rel}: {e}", file=sys.stderr)
            failed += 1
            continue

        new_content = _rewrite_class(content, old_val, new_val)
        if new_content is None:
            print(
                f"  ⚠️  Could not locate class: line in frontmatter — skipping {rel}",
                file=sys.stderr,
            )
            failed += 1
            continue

        try:
            filepath.write_text(new_content, encoding="utf-8")
        except OSError as e:
            print(f"  ⚠️  Could not write {rel}: {e}", file=sys.stderr)
            failed += 1
            continue

        print(f"  ✓ {rel}")
        updated += 1

    print(
        f"\n  Reclassified {updated} file(s):  class: {old_val}  →  class: {new_val}"
    )
    if failed:
        print(f"  ⚠️  {failed} file(s) skipped — see warnings above")