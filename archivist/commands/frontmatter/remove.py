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
    ConfigSchema,
    NoteFilter,
    TemplaterMode,
    build_note_filter,
    get_repo_root,
    get_templater_mode,
    has_frontmatter,
    mask_templater_expressions,
    note_matches_filter,
    print_dry_run_header,
    progress,
    read_archivist_config,
    remove_property_from_frontmatter,
    restore_templater_expressions,
    resolve_file_targets,
    safe_read_markdown,
    safe_write_markdown,
    success,
    validate_note_filter,
    warning,
)


def _process_note(
    note_path: Path,
    prop: str,
    dry_run: bool,
    nf: NoteFilter,
    mode: TemplaterMode
) -> bool:
    """Process a single note. Returns True if a change was made (or would be).
    
    Templater handling:
      DISABLED — no masking, removal operates on raw_fm directly
      PRESERVE — mask expressions before removal, restore after
      RESOLVE  — mask expressions before removal, restore after
    """
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

    # Mask expressions before removal if mode is not DISABLED
    if mode is not TemplaterMode.DISABLED:
        masked_fm, mask_map = mask_templater_expressions(raw_fm)
    else:
        masked_fm, mask_map = raw_fm, {}

    updated_masked_fm, found = remove_property_from_frontmatter(masked_fm, prop)
    if not found:
        return False

    # Restore expressions after removal
    if mode is not TemplaterMode.DISABLED:
        updated_fm = restore_templater_expressions(updated_masked_fm, mask_map)
    else:
        updated_fm = updated_masked_fm

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
    config: ConfigSchema | None = read_archivist_config(root)
    mode = get_templater_mode(config)

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
        return _process_note(f, args.property, args.dry_run, nf, mode)

    changed = sum(1 for f in files if _callback(f))

    label = "would be updated" if args.dry_run else "updated"
    progress(f"\nDone. {changed}/{len(files)} file(s) {label}.")