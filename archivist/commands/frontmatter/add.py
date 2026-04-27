"""
archivist frontmatter add

Add a property to the YAML frontmatter of every note that matches the
selection criteria. With no selection flags, operates on the entire repo.
Scopes automatically to the current git repo (or submodule) root.

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
    TemplaterContext,
    TemplaterMode,
    build_note_filter,
    get_repo_root,
    get_templater_mode,
    has_frontmatter,
    has_templater_expression,
    mask_templater_expressions,
    match_property_line,
    note_matches_filter,
    print_dry_run_header,
    progress,
    read_archivist_config,
    remove_property_from_frontmatter,
    resolve_file_targets,
    resolve_value,
    restore_templater_expressions,
    safe_read_markdown,
    safe_write_markdown,
    success,
    validate_note_filter,
    warning,
)


def _property_exists(raw_fm: str, prop: str) -> bool:
    """Return True if prop already has a key line in the raw frontmatter block."""
    return any(match_property_line(line, prop) for line in raw_fm.split("\n"))


def _resolve_new_line(
    new_line: str,
    note_path: Path,
    raw_fm: str,
    mode: TemplaterMode,
) -> str:
    """
    Conditionally resolve Templater expressions in a new property line string.

    Called when the value being added contains a <% %> expression. Only fires
    when mode is RESOLVE; PRESERVE and DISABLED leave the line untouched.

    Args:
        new_line:  the full "prop: <% expr %>" line string
        note_path: path to the target note (used to build the file context)
        raw_fm:    the existing raw frontmatter text, used to populate
                   tp.frontmatter for cross-reference resolution
        mode:      active TemplaterMode
    """
    if mode is not TemplaterMode.RESOLVE:
        return new_line
    if not has_templater_expression(new_line):
        return new_line

    # Parse existing frontmatter into a plain dict for tp.frontmatter context.
    # Import inline to avoid circular dependency — extract_frontmatter lives in
    # frontmatter.py which is already imported transitively via the barrel.
    from archivist.utils import extract_frontmatter
    existing_fm = extract_frontmatter(f"---\n{raw_fm}\n---\n")

    ctx = TemplaterContext(note_path, existing_fm)
    resolved_line, _ = resolve_value(new_line, ctx, warn_fn=warning)
    return resolved_line


def _process_note(
    note_path: Path,
    prop: str,
    value: str | None,
    overwrite: bool,
    dry_run: bool,
    mode: TemplaterMode,
    nf: NoteFilter,
) -> bool:
    """
    Process a single note. Returns True if a change was made (or would be).

    Note: add is the one frontmatter command that can't use update_frontmatter_in_file
    cleanly because it also creates a frontmatter block from scratch when none
    exists. Hence the two-branch structure here. Everything else uses the helpers.

    Templater handling:
      DISABLED — no masking, no resolution, expressions are dumb strings
      PRESERVE — mask expressions in existing frontmatter before read,
                 restore verbatim after write
      RESOLVE  — mask existing expressions, then also attempt to resolve
                 any expression in the new value at write time
    """
    content = safe_read_markdown(note_path)
    if content is None:
        return False

    new_line = f"{prop}: {value}" if value is not None else f"{prop}:"

    if has_frontmatter(content):
        match = FRONTMATTER_RE.match(content)
        if not match:
            return False

        raw_fm = match.group(1)
        body = content[match.end():]

        # Class/tag filter check against this note's actual frontmatter.
        # Path and file scoping are already handled upstream in resolve_file_targets;
        # we only need to evaluate class and tag here.
        if not note_matches_filter(nf, raw_fm):
            return False

        # Mask existing expressions before any string operations on raw_fm.
        # Even in PRESERVE mode we need to mask so that _property_exists and
        # remove_property_from_frontmatter don't choke on exotic expression content.
        if mode is not TemplaterMode.DISABLED:
            masked_fm, mask_map = mask_templater_expressions(raw_fm)
        else:
            masked_fm, mask_map = raw_fm, {}

        if _property_exists(masked_fm, prop):
            if not overwrite:
                return False
            masked_fm, _ = remove_property_from_frontmatter(masked_fm, prop)

        # Resolve the new line against the target note's context if applicable.
        # Pass the original (unmasked) raw_fm so tp.frontmatter has real values.
        final_line = _resolve_new_line(new_line, note_path, raw_fm, mode)

        updated_masked_fm = masked_fm.rstrip("\n") + f"\n{final_line}"

        # Restore all existing expressions verbatim (PRESERVE) or with
        # resolved substitutions where available (RESOLVE — restoration
        # happens implicitly because sentinels in mask_map are the fallback
        # and resolved values are not in mask_map, so they stay as-is).
        if mode is not TemplaterMode.DISABLED:
            updated_fm = restore_templater_expressions(updated_masked_fm, mask_map)
        else:
            updated_fm = updated_masked_fm

        new_content = f"---\n{updated_fm}\n---\n{body}"
    else:
        # No frontmatter block — notes without frontmatter can't satisfy class/tag
        # filters. If any metadata filter is active, skip this note.
        if nf.note_class or nf.tag:
            return False

        final_line = new_line
        if mode is TemplaterMode.RESOLVE and has_templater_expression(new_line):
            ctx = TemplaterContext(note_path, {})
            final_line, _ = resolve_value(new_line, ctx, warn_fn=warning)
        new_content = f"---\n{final_line}\n---\n{content}"

    if dry_run:
        progress(f"  [dry-run] Would add '{new_line}' to: {note_path}")
    else:
        if not safe_write_markdown(note_path, new_content):
            return False
        success(f"Added '{new_line}' to: {note_path}")

    return True


def run(args: argparse.Namespace) -> None:
    nf = build_note_filter(args)
    validate_note_filter(nf, require_at_least_one=False, command_name="frontmatter add")

    root = get_repo_root()
    config = read_archivist_config(root)
    mode = get_templater_mode(config)

    if args.dry_run:
        print_dry_run_header()

    action = (
        f"'{args.property}: {args.value}'"
        if args.value is not None
        else f"'{args.property}:'"
    )
    progress(f"Root: {root}")

    if nf.active_filter_labels:
        progress(f"Filters: {' AND '.join(nf.active_filter_labels)}")

    files = resolve_file_targets(nf, root)
    if not files:
        warning(f"No .md files found matching the given criteria.")
        sys.exit(0)

    progress(f"Scanning {len(files)} file(s) to add {action}...\n")

    def _callback(f: Path) -> bool:
        return _process_note(f, args.property, args.value, args.overwrite, args.dry_run, mode, nf)

    changed = sum(1 for f in files if _callback(f))

    label = "would be updated" if args.dry_run else "updated"
    progress(f"\nDone. {changed}/{len(files)} file(s) {label}.")