"""
archivist frontmatter rename

Rename a property key across all notes that match the selection criteria,
preserving its value exactly. With no selection flags, operates on the entire
repo. Handles scalar values, inline lists, and multi-line block sequences.

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
    TemplaterMode,
    build_note_filter,
    get_repo_root,
    get_templater_mode,
    has_frontmatter,
    mask_templater_expressions,
    match_property_line,
    note_matches_filter,
    print_dry_run_header,
    progress,
    read_archivist_config,
    resolve_file_targets,
    restore_templater_expressions,
    safe_read_markdown,
    success,
    update_frontmatter_in_file,
    validate_note_filter,
    warning,
)


def _rename_property_in_raw_fm(
    raw_fm: str,
    old_prop: str,
    new_prop: str
) -> tuple[str, bool]:
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


def _process_note(
    note_path: Path,
    old_prop: str,
    new_prop: str,
    dry_run: bool,
    nf: NoteFilter,
    mode: TemplaterMode,
) -> bool:
    """
    Rename old_prop → new_prop in a single note's frontmatter.
    Returns True if a change was made (or would be in dry-run mode).
    
    Templater handling:
      DISABLED — no masking, rename operates on raw_fm directly
      PRESERVE — mask expressions before rename, restore after
      RESOLVE  — mask expressions before rename, restore after
    """
    # We need to read the file up-front to run the filter check before
    # handing off to update_frontmatter_in_file. The transformer closure
    # captures found_and_changed so the outer function can report correctly.
    content = safe_read_markdown(note_path)
    if content is None:
        return False

    if not has_frontmatter(content):
        return False

    match = FRONTMATTER_RE.match(content)
    if not match:
        return False

    raw_fm = match.group(1)

    if not note_matches_filter(nf, raw_fm):
        return False

    found_and_changed = False

    def _transformer(raw_fm: str, body: str) -> str | None:
        nonlocal found_and_changed
        
        # Mask expressions before rename if mode is not DISABLED
        if mode is not TemplaterMode.DISABLED:
            masked_fm, mask_map = mask_templater_expressions(raw_fm)
        else:
            masked_fm, mask_map = raw_fm, {}
        
        updated_masked_fm, found = _rename_property_in_raw_fm(masked_fm, old_prop, new_prop)
        if not found:
            return None
        found_and_changed = True
        
        # Restore expressions after rename
        if mode is not TemplaterMode.DISABLED:
            updated_fm = restore_templater_expressions(updated_masked_fm, mask_map)
        else:
            updated_fm = updated_masked_fm
        
        if dry_run:
            progress(f"  [dry-run] Would rename '{ old_prop }' → '{ new_prop }' in: { note_path }")
            return None  # signal no-write; found_and_changed is already set
        success(f"Renamed '{ old_prop }' → '{ new_prop }' in: { note_path }")
        return f"---\n{updated_fm}\n---\n{body}"

    update_frontmatter_in_file(note_path, _transformer)
    return found_and_changed


def run(args: argparse.Namespace) -> None:
    if args.property == args.new_name:
        print(
            "❌  You gave me the same name twice. Renaming a property to itself is just\n"
            "    called 'doing nothing', and you didn't need me for that."
        )
        sys.exit(1)

    nf = build_note_filter(args)
    validate_note_filter(
        nf,
        require_at_least_one = False,
        command_name = "frontmatter rename"
    )

    root = get_repo_root()
    config = read_archivist_config(root)
    mode = get_templater_mode(config)

    if args.dry_run:
        print_dry_run_header()

    progress(f"Root: { root }")

    if nf.active_filter_labels:
        progress(f"Filters: { ' AND '.join(nf.active_filter_labels) }")

    files = resolve_file_targets(nf, root)
    if not files:
        warning("No .md files found matching the given criteria.")
        sys.exit(0)

    progress(f"Scanning { len(files) } file(s) to rename '{ args.property }' → '{ args.new_name }'...\n")

    def _callback(f: Path) -> bool:
        return _process_note(
            f,
            args.property,
            args.new_name,
            args.dry_run,
            nf,
            mode
        )

    changed = sum(1 for f in files if _callback(f))

    label = "would be updated" if args.dry_run else "updated"
    progress(f"\nDone. { changed }/{ len(files) } file(s) { label }.")