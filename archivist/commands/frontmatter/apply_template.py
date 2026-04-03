"""
archivist frontmatter apply-template

Apply a frontmatter template to all notes matching the specified criteria.
Filter by any combination of class (-c), directory (--path), and tag (--tag).
At least one filter is required. All provided filters must match (AND logic).

For each matching note the script will:
  - Add properties missing from the note but present in the template
  - Remove properties present in the note but absent from the template
  - Reorder properties to match the template order
  - Preserve existing values for properties that are kept
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

from archivist.utils import FRONTMATTER_RE, get_repo_root


def _parse_frontmatter(raw: str) -> list[tuple[str, list[str]]]:
    """
    Parse raw frontmatter text into an ordered list of (key, lines) tuples.
    Each entry's 'lines' includes the key line plus any indented continuation
    lines, preserving raw text for round-trip safety.
    """
    entries = []
    lines = raw.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^(\S[^:]*)\s*:", line)
        if m:
            key = m.group(1).strip()
            key_lines = [line]
            i += 1
            while i < len(lines) and (lines[i].startswith(" ") or lines[i].startswith("\t")):
                key_lines.append(lines[i])
                i += 1
            entries.append((key, key_lines))
        else:
            i += 1
    return entries


def _get_frontmatter_from_file(
    path: Path,
) -> Optional[tuple[str, list[tuple[str, list[str]]], str]]:
    """
    Read a file and return (raw_frontmatter, parsed_entries, body_after_frontmatter).
    Returns None if no frontmatter block is found.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except Exception as e:
        print(f"  ⚠️  Could not read '{path}': {e}")
        return None

    match = FRONTMATTER_RE.match(content)
    if not match:
        return None

    raw = match.group(1)
    return raw, _parse_frontmatter(raw), content[match.end():]


def _rip_tags_out_of_entries(entries: list[tuple[str, list[str]]]) -> list[str]:
    """
    Pull all tag values out of parsed frontmatter entries. Handles the three
    ways YAML decides to store a list on any given Tuesday:

      inline:   tags: [foo, bar, "baz qux"]
      scalar:   tags: foo
      block:    tags:
                  - foo
                  - bar

    Returns lowercase stripped strings so callers don't have to think about it.
    """
    for key, lines in entries:
        if key != "tags":
            continue
        value_part = lines[0].split(":", 1)[1].strip()
        if value_part.startswith("[") and value_part.endswith("]"):
            return [
                t.strip().strip("\"'").lower()
                for t in value_part[1:-1].split(",")
                if t.strip()
            ]
        elif value_part:
            return [value_part.strip("\"'").lower()]
        else:
            return [
                line.strip().lstrip("- ").strip("\"'").lower()
                for line in lines[1:]
                if line.strip()
            ]
    return []


def _note_matches_class(
    entries: list[tuple[str, list[str]]],
    class_prop: str,
    class_value: str,
) -> bool:
    for key, lines in entries:
        if key == class_prop:
            value = lines[0].split(":", 1)[1].strip()
            return value == class_value
    return False


def _note_has_tag(
    entries: list[tuple[str, list[str]]],
    tag: str,
) -> bool:
    """Return True if the note carries the given tag. Case-insensitive."""
    return tag.lower() in _rip_tags_out_of_entries(entries)


def _note_clears_all_filters(
    entries: list[tuple[str, list[str]]],
    class_prop: str | None,
    class_value: str | None,
    tag: str | None,
) -> bool:
    """
    Return True only if the note satisfies every provided filter.
    Unset filters don't count against it. All set filters must pass (AND logic).
    Path scoping is handled upstream by constraining the file search — no need
    to re-check it here.
    """
    if class_prop and class_value:
        if not _note_matches_class(entries, class_prop, class_value):
            return False
    if tag:
        if not _note_has_tag(entries, tag):
            return False
    return True


def _apply_template(
    note_entries: list[tuple[str, list[str]]],
    template_entries: list[tuple[str, list[str]]],
) -> tuple[list[tuple[str, list[str]]], int, int, bool]:
    """
    Merge note entries against the template:
      - Order follows the template
      - Existing note values are preserved
      - Properties absent from the template are dropped
      - Properties missing from the note are added with template defaults

    Returns (merged_entries, added_count, removed_count, was_reordered).
    """
    note_map = {key: lines for key, lines in note_entries}
    template_keys = [key for key, _ in template_entries]
    note_keys = [key for key, _ in note_entries]

    merged = []
    added = 0
    for key, template_lines in template_entries:
        if key in note_map:
            merged.append((key, note_map[key]))
        else:
            merged.append((key, template_lines))
            added += 1

    removed = sum(1 for k in note_keys if k not in template_keys)
    reordered = [k for k, _ in merged] != [k for k in note_keys if k in template_keys]

    return merged, added, removed, reordered


def _render_frontmatter(entries: list[tuple[str, list[str]]]) -> str:
    return "\n".join(line for _, lines in entries for line in lines)


def _process_note(
    note_path: Path,
    template_entries: list[tuple[str, list[str]]],
    class_prop: str | None,
    class_value: str | None,
    tag: str | None,
    dry_run: bool,
) -> Optional[str]:
    """
    Process a single note against the active filter set.
    Returns a short change summary string if the note matched and changed,
    None if it didn't match or was already in order.
    """
    result = _get_frontmatter_from_file(note_path)
    if result is None:
        return None

    _, note_entries, body = result

    if not _note_clears_all_filters(note_entries, class_prop, class_value, tag):
        return None

    merged, added, removed, reordered = _apply_template(note_entries, template_entries)

    if added == 0 and removed == 0 and not reordered:
        return None

    new_frontmatter = _render_frontmatter(merged)
    new_content = f"---\n{new_frontmatter}\n---\n{body}"

    parts = []
    if added:
        parts.append(f"+{added}")
    if removed:
        parts.append(f"-{removed}")
    if reordered:
        parts.append("reordered")
    summary = ", ".join(parts)

    if dry_run:
        print(f"  [dry-run] {summary}: {note_path}")
    else:
        note_path.write_text(new_content, encoding="utf-8")
        print(f"  ✅ {summary}: {note_path}")

    return summary


def run(args: argparse.Namespace) -> None:
    has_class = bool(getattr(args, "note_class", None))
    has_path  = bool(getattr(args, "path", None))
    has_tag   = bool(getattr(args, "tag", None))

    if not (has_class or has_path or has_tag):
        print(
            "❌  You need to give me something to work with.\n"
            "    --class, --path, --tag — pick at least one.\n"
            "    I'm not running a template over your entire fucking vault on a hunch."
        )
        sys.exit(1)

    template_path = Path(args.template)

    if not template_path.exists():
        print(f"❌  Template not found: '{template_path}'")
        sys.exit(1)

    template_result = _get_frontmatter_from_file(template_path)
    if template_result is None:
        print(f"❌  No frontmatter found in template '{template_path}'.")
        sys.exit(1)

    _, template_entries, _ = template_result

    if not template_entries:
        print("❌  Template frontmatter is empty.")
        sys.exit(1)

    root = get_repo_root()

    # Resolve search root. Path scoping is the bluntest filter — we constrain
    # the file list here so _process_note doesn't have to re-check it.
    if has_path:
        search_root = (root / args.path).resolve()
        if not search_root.exists():
            print(f"❌  Path not found: '{args.path}'")
            sys.exit(1)
        if not search_root.is_dir():
            print(f"❌  '{args.path}' is not a directory.")
            sys.exit(1)
    else:
        search_root = root

    files = sorted(search_root.rglob("*.md"))

    if not files:
        print(f"⚠️  No .md files found under '{search_root}'.")
        sys.exit(0)

    if args.dry_run:
        print("=== DRY RUN — nothing will be written ===")

    # Build a human-readable summary of what the hell we're actually filtering on
    active_filters = []
    if has_class:
        active_filters.append(f"{args.class_property} = {args.note_class}")
    if has_path:
        try:
            rel = search_root.relative_to(root)
        except ValueError:
            rel = search_root
        active_filters.append(f"path ⊆ {rel}")
    if has_tag:
        active_filters.append(f"tag = {args.tag}")

    print(f"Root:     {root}")
    print(f"Template: {template_path}")
    print(f"Filters:  {' AND '.join(active_filters)}")
    print(f"Scanning {len(files)} file(s)...\n")

    changed = 0
    for f in files:
        if _process_note(
            f,
            template_entries,
            class_prop=args.class_property if has_class else None,
            class_value=args.note_class if has_class else None,
            tag=args.tag if has_tag else None,
            dry_run=args.dry_run,
        ):
            changed += 1

    label = "would be updated" if args.dry_run else "updated"
    print(f"\nDone. {changed}/{len(files)} file(s) {label}.")