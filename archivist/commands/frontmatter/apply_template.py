"""
archivist frontmatter apply-template

Apply a frontmatter template to all notes matching the specified criteria.
Filter by any combination of class (-c), directory (--path), and tag (--tag).
At least one filter is required. All provided filters must match (AND logic).

For each matching note the command will:
  - Add properties missing from the note but present in the template
  - Remove properties present in the note but absent from the template
  - Reorder properties to match the template order
  - Preserve existing values for properties that are kept

In RESOLVE mode, Templater expressions in template property *defaults* are
resolved against the *target note's* context — its path, title, dates, etc.
Not the template file's context. Never the template file's context. Don't
make that mistake.

The template is the authority. The template is the law. You built it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from archivist.utils import (
    FRONTMATTER_RE,
    TemplaterContext,
    TemplaterMode,
    error,
    extract_frontmatter,
    extract_tags_from_entries,
    find_markdown_files,
    get_repo_root,
    get_templater_mode,
    has_frontmatter,
    has_templater_expression,
    mask_templater_expressions,
    matches_class_filter,
    parse_frontmatter_entries,
    print_dry_run_header,
    process_markdown_files,
    progress,
    read_archivist_config,
    resolve_value,
    restore_templater_expressions,
    safe_read_markdown,
    safe_write_markdown,
    success,
    warning,
)


# ---------------------------------------------------------------------------
# File reading
# ---------------------------------------------------------------------------

def _load_note(
    path: Path,
) -> tuple[list[tuple[str, list[str]]], str, str] | None:
    """
    Read a markdown file and return (parsed_entries, raw_fm, body_after_frontmatter).

    Returns None if the file can't be read or has no frontmatter block.

    raw_fm is returned alongside parsed_entries so callers can build a
    TemplaterContext from the original unmasked text without re-reading the file.
    Parsed entries are derived from the masked fm, but raw_fm is the original —
    callers that need tp.frontmatter to contain real values (not sentinel tokens)
    should use raw_fm for context construction.
    """
    content = safe_read_markdown(path)
    if content is None:
        return None

    if not has_frontmatter(content):
        return None

    match = FRONTMATTER_RE.match(content)
    if not match:
        return None

    raw_fm = match.group(1)
    body = content[match.end():]
    return parse_frontmatter_entries(raw_fm), raw_fm, body


# ---------------------------------------------------------------------------
# Filter predicates
# ---------------------------------------------------------------------------

def _note_matches_class(
    entries: list[tuple[str, list[str]]],
    class_prop: str,
    class_value: str,
) -> bool:
    """Check whether the note satisfies the class filter."""
    if class_prop == "class":
        # Reconstruct a minimal fm dict and use the canonical helper.
        fm: dict[str, str | list[str]] = {}
        for key, lines in entries:
            value = lines[0].split(":", 1)[1].strip() if ":" in lines[0] else ""
            fm[key] = value
        return matches_class_filter(fm, class_value)
    # Non-standard class property — exact string match.
    for key, lines in entries:
        if key == class_prop:
            return lines[0].split(":", 1)[1].strip() == class_value
    return False


def _note_has_tag(entries: list[tuple[str, list[str]]], tag: str) -> bool:
    """Return True if the note carries the given tag. Case-insensitive."""
    return tag.lower() in extract_tags_from_entries(entries)


def _note_clears_all_filters(
    entries: list[tuple[str, list[str]]],
    class_prop: str | None,
    class_value: str | None,
    tag: str | None,
) -> bool:
    """
    Return True only if the note satisfies every provided filter.
    Unset filters don't count against it. All set filters must pass (AND logic).
    Path scoping is handled upstream — no need to re-check it here.
    """
    if class_prop and class_value:
        if not _note_matches_class(entries, class_prop, class_value):
            return False
    if tag:
        if not _note_has_tag(entries, tag):
            return False
    return True


# ---------------------------------------------------------------------------
# Template application
# ---------------------------------------------------------------------------

def _resolve_template_defaults(
    template_entries: list[tuple[str, list[str]]],
    note_path: Path,
    note_raw_fm: str,
    mode: TemplaterMode,
) -> list[tuple[str, list[str]]]:
    """
    Resolve Templater expressions in template *default* values against the
    target note's context.

    Only runs when mode is RESOLVE. Returns the template entries unchanged for
    PRESERVE and DISABLED — template defaults are used verbatim in those modes,
    and Obsidian handles resolution on the next open.

    The target note's context is used, not the template file's. This is
    intentional and correct — when you apply a template that says
    `created: <% tp.date.now() %>`, you want the creation date of the note
    being processed, not the template. Obviously.

    Expressions that can't be resolved are left verbatim with a warning.
    Only template defaults get resolved here — existing note values are
    never touched by this function.
    """
    if mode is not TemplaterMode.RESOLVE:
        return template_entries

    existing_fm = extract_frontmatter(f"---\n{note_raw_fm}\n---\n")
    ctx = TemplaterContext(note_path, existing_fm)

    resolved_entries: list[tuple[str, list[str]]] = []
    for key, lines in template_entries:
        if not any(has_templater_expression(line) for line in lines):
            resolved_entries.append((key, lines))
            continue

        resolved_lines = []
        for line in lines:
            if not has_templater_expression(line):
                resolved_lines.append(line)
                continue
            resolved_line, _ = resolve_value(line, ctx, warn_fn=warning)
            resolved_lines.append(resolved_line)
        resolved_entries.append((key, resolved_lines))

    return resolved_entries


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

    Template defaults passed in here should already be resolved (if mode is
    RESOLVE) — resolution happens before this function is called so that
    the merge logic stays clean and dumb.
    """
    note_map = {key: lines for key, lines in note_entries}
    template_keys = [key for key, _ in template_entries]
    note_keys = [key for key, _ in note_entries]

    merged: list[tuple[str, list[str]]] = []
    added = 0
    for key, template_lines in template_entries:
        if key in note_map:
            merged.append((key, note_map[key]))
        else:
            merged.append((key, template_lines))
            added += 1

    removed = sum(1 for k in note_keys if k not in template_keys)
    surviving_note_keys = [k for k in note_keys if k in template_keys]
    merged_keys = [k for k, _ in merged]
    reordered = merged_keys != surviving_note_keys

    return merged, added, removed, reordered


def _render_entries(entries: list[tuple[str, list[str]]]) -> str:
    """Flatten parsed entries back into a raw frontmatter string."""
    return "\n".join(line for _, lines in entries for line in lines)


# ---------------------------------------------------------------------------
# Per-note processor
# ---------------------------------------------------------------------------

def _process_note(
    note_path: Path,
    template_entries: list[tuple[str, list[str]]],
    class_prop: str | None,
    class_value: str | None,
    tag: str | None,
    dry_run: bool,
    mode: TemplaterMode,
) -> bool:
    """
    Process a single note against the active filter set.
    Returns True if the note matched and was changed (or would be).

    Operation order (Templater-aware):
      1. Read file → raw_fm, body
      2. Mask expressions in raw_fm → masked_fm, mask_map
      3. Parse masked_fm → note_entries (sentinels survive through parse safely)
      4. Filter check on note_entries (sentinels in values don't affect key matching)
      5. Resolve template defaults against target note context (RESOLVE mode only)
      6. Merge note_entries + resolved template_entries → merged_entries
      7. Render merged_entries → rendered_masked_fm
      8. Restore masked note expressions in rendered_masked_fm → final_fm
         (sentinel tokens from step 2 that survived the merge are restored here;
          resolved template values from step 5 are plain strings and pass through
          restore untouched since they contain no sentinel tokens)
      9. Write final_fm + body to disk

    The mask_map only covers expressions that were in the *note's* existing
    frontmatter. Template expressions are handled separately in step 5.
    """
    result = _load_note(note_path)
    if result is None:
        return False

    note_entries_raw, raw_fm, body = result

    if mode is not TemplaterMode.DISABLED:
        masked_fm, mask_map = mask_templater_expressions(raw_fm)
    else:
        masked_fm, mask_map = raw_fm, {}

    # Step 3: re-parse from masked fm so sentinels survive through the merge
    note_entries = parse_frontmatter_entries(masked_fm)

    # Step 4: filter
    if not _note_clears_all_filters(note_entries, class_prop, class_value, tag):
        return False

    # Step 5: resolve template defaults against target note context
    effective_template_entries = _resolve_template_defaults(
        template_entries, note_path, raw_fm, mode
    )

    # Step 6: merge
    merged, added, removed, reordered = _apply_template(note_entries, effective_template_entries)

    if added == 0 and removed == 0 and not reordered:
        return False

    parts = []
    if added:
        parts.append(f"+{added}")
    if removed:
        parts.append(f"-{removed}")
    if reordered:
        parts.append("reordered")
    summary = ", ".join(parts)

    if dry_run:
        progress(f"  [dry-run] {summary}: {note_path}")
        return True

    # Step 7: render
    rendered_masked_fm = _render_entries(merged)

    # Step 8: restore note's original expressions from mask_map
    if mode is not TemplaterMode.DISABLED:
        final_fm = restore_templater_expressions(rendered_masked_fm, mask_map)
    else:
        final_fm = rendered_masked_fm

    # Step 9: write
    new_content = f"---\n{final_fm}\n---\n{body}"
    if not safe_write_markdown(note_path, new_content):
        return False

    success(f"{summary}: {note_path}")
    return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    has_class = bool(getattr(args, "note_class", None))
    has_path  = bool(getattr(args, "path", None))
    has_tag   = bool(getattr(args, "tag", None))

    if not (has_class or has_path or has_tag):
        error(
            "You need to give me something to work with.\n"
            "    --class, --path, --tag — pick at least one.\n"
            "    I'm not running a template over your entire fucking vault on a hunch."
        )
        sys.exit(1)

    template_path = Path(args.template)
    if not template_path.exists():
        error(f"Template not found: '{template_path}'")
        sys.exit(1)

    # Load template entries from the *unmasked* template file.
    # Template expressions will be resolved per-note in _process_note via
    # _resolve_template_defaults — we want the raw <% %> intact here so
    # they get the correct target-note context on each application.
    template_result = _load_note(template_path)
    if template_result is None:
        error(f"No frontmatter found in template '{template_path}'.")
        sys.exit(1)

    template_entries, _, _ = template_result
    if not template_entries:
        error("Template frontmatter is empty.")
        sys.exit(1)

    root = get_repo_root()
    config = read_archivist_config(root)
    mode = get_templater_mode(config)

    # Path scoping is the bluntest filter — constrain the file list here so
    # _process_note doesn't have to re-check it.
    if has_path:
        search_root = (root / args.path).resolve()
        if not search_root.exists():
            error(f"Path not found: '{args.path}'")
            sys.exit(1)
        if not search_root.is_dir():
            error(f"'{args.path}' is not a directory.")
            sys.exit(1)
    else:
        search_root = root

    files = find_markdown_files(search_root)
    if not files:
        warning(f"No .md files found under '{search_root}'.")
        sys.exit(0)

    if args.dry_run:
        print_dry_run_header()

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

    progress(f"Root:     {root}")
    progress(f"Template: {template_path}")
    progress(f"Filters:  {' AND '.join(active_filters)}")
    progress(f"Scanning {len(files)} file(s)...\n")

    def _callback(f: Path) -> bool:
        return _process_note(
            f,
            template_entries,
            class_prop=args.class_property if has_class else None,
            class_value=args.note_class if has_class else None,
            tag=args.tag if has_tag else None,
            dry_run=args.dry_run,
            mode=mode,
        )

    # Use process_markdown_files with a path_prefix filter when search_root
    # differs from root — avoids rescanning files outside the scoped directory.
    filters = {"path_prefix": search_root} if has_path else None
    changed = process_markdown_files(root, _callback, filters=filters)

    label = "would be updated" if args.dry_run else "updated"
    progress(f"\nDone. {changed}/{len(files)} file(s) {label}.")