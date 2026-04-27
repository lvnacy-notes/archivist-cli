"""
archivist/utils/note_filter.py

Shared note selection logic for frontmatter commands.

Every frontmatter command that operates on a set of notes uses the same
selection criteria: a specific file, a directory scope, a class value, and/or
a tag. This module is the single source of truth for that. Stop copy-pasting
filter predicates between command modules. I did it once so you don't have to.

Public surface:
    NoteFilter              — selection criteria, frozen dataclass
    build_note_filter       — construct from parsed argparse.Namespace
    validate_note_filter    — enforce mutual-exclusivity and presence rules
    resolve_file_targets    — return the list of paths to operate on
    note_matches_filter     — per-note predicate; returns True if note qualifies
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from archivist.utils.frontmatter import (
    extract_tags_from_entries,
    find_markdown_files,
    matches_class_filter,
    parse_frontmatter_entries,
)


# ---------------------------------------------------------------------------
# The one structure to rule them all
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NoteFilter:
    """
    Immutable selection criteria for a frontmatter command run.

    Exactly one of (file, path) can be set. class_value and tag are
    only meaningful when file is NOT set — if you pass --file alongside
    --class or --tag you will get a very justified error before anything runs.

    Fields:
        file            Operate on exactly this one file. Mutually exclusive
                        with path, class_value, and tag.
        path            Limit the directory walk to this subtree.
        note_class      Required 'class' frontmatter value (case-insensitive).
        class_property  The frontmatter key to match class_value against.
                        Defaults to 'class'. Exists because some vaults use
                        a different key and we're not here to judge.
        tag             Required tag value (case-insensitive).
    """
    file: Path | None = None
    path: Path | None = None
    note_class: str | None = None
    class_property: str = "class"
    tag: str | None = None

    @property
    def is_empty(self) -> bool:
        """True when no selection criteria have been provided at all."""
        return not any([self.file, self.path, self.note_class, self.tag])

    @property
    def is_single_file(self) -> bool:
        return self.file is not None

    @property
    def active_filter_labels(self) -> list[str]:
        """Human-readable list of active filter criteria. For progress output."""
        labels = []
        if self.file:
            labels.append(f"file = {self.file}")
        if self.path:
            labels.append(f"path ⊆ {self.path}")
        if self.note_class:
            labels.append(f"{self.class_property} = {self.note_class}")
        if self.tag:
            labels.append(f"tag = {self.tag}")
        return labels


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def build_note_filter(args: object) -> NoteFilter:
    """
    Build a NoteFilter from a parsed argparse.Namespace.

    Gracefully handles commands that don't define every attribute — missing
    attrs are treated as None / default. This means you can call it from any
    frontmatter command without wiring up args you don't use.
    """
    raw_file: str | None = getattr(args, "file", None)
    raw_path: str | None = getattr(args, "path", None)
    note_class: str | None = getattr(args, "note_class", None) or None
    class_property: str = getattr(args, "class_property", None) or "class"
    tag: str | None = getattr(args, "tag", None) or None

    return NoteFilter(
        file = Path(raw_file) if raw_file else None,
        path = Path(raw_path) if raw_path else None,
        note_class = note_class,
        class_property = class_property,
        tag = tag,
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_note_filter(
    nf: NoteFilter,
    *,
    require_at_least_one: bool = False,
    command_name: str = "this command",
) -> None:
    """
    Enforce the selection criteria contract. Calls sys.exit(1) on violations
    after printing a useful (and appropriately annoyed) error message.

    Args:
        nf:                  The filter to validate.
        require_at_least_one: If True, an empty filter is a hard error.
                              Use this for apply-template; leave it False
                              for add/remove/rename where "all notes" is valid.
        command_name:        For error messages. 'archivist frontmatter add',
                             that sort of thing.
    """
    if nf.file and (nf.path or nf.note_class or nf.tag):
        _die(
            "--file is a single-target selector. Pairing it with --path, --class,\n"
            "    or --tag is incoherent and I won't pretend otherwise.\n"
            "    Pick one approach and commit to it."
        )

    if nf.file and not nf.file.exists():
        _die(f"File not found: '{nf.file}'")

    if nf.file and not nf.file.is_file():
        _die(f"'{nf.file}' is not a file. Directories go in --path.")

    if nf.file and nf.file.suffix.lower() not in (".md", ".markdown"):
        _die(f"'{nf.file}' doesn't look like a markdown file. I only touch .md files.")

    if nf.path and not nf.path.exists():
        _die(f"Path not found: '{nf.path}'")

    if nf.path and not nf.path.is_dir():
        _die(
            f"'{nf.path}' is not a directory. If you're targeting a single file,\n"
            "    use --file instead."
        )

    if require_at_least_one and nf.is_empty:
        _die(
            f"You need to give me something to work with.\n"
            f"    --file, --class, --path, --tag — pick at least one.\n"
            f"    I'm not running {command_name} over your entire vault on a hunch."
        )


def _die(msg: str) -> None:
    print(f"❌  {msg}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# File target resolution
# ---------------------------------------------------------------------------

def resolve_file_targets(nf: NoteFilter, repo_root: Path) -> list[Path]:
    """
    Return the list of markdown files that should be processed for this filter.

    If --file was given, the list is exactly that one file (already validated).
    Otherwise, walk from --path (if set) or repo_root, and return all .md files.
    Class and tag filtering happen per-note in note_matches_filter — don't do
    it here, we'd have to read every file twice.
    """
    if nf.is_single_file:
        return [nf.file]  # type: ignore[list-item]  # validated non-None above

    search_root = (repo_root / nf.path).resolve() if nf.path else repo_root
    return find_markdown_files(search_root)


# ---------------------------------------------------------------------------
# Per-note predicate
# ---------------------------------------------------------------------------

def note_matches_filter(
    nf: NoteFilter,
    raw_fm: str,
) -> bool:
    """
    Return True if a note's raw frontmatter satisfies the filter.

    Path scoping and single-file targeting are resolved upstream in
    resolve_file_targets — this function only handles class and tag checks.
    If neither is set, it always returns True (the note is "in scope" by
    virtue of being in the file list at all).

    Args:
        nf:     The active NoteFilter.
        raw_fm: Raw frontmatter text (already read from disk, not yet parsed).
                Callers that have already parsed entries can use
                note_matches_filter_entries directly instead.
    """
    if not nf.note_class and not nf.tag:
        return True

    entries = parse_frontmatter_entries(raw_fm)
    return note_matches_filter_entries(nf, entries)


def note_matches_filter_entries(
    nf: NoteFilter,
    entries: list[tuple[str, list[str]]],
) -> bool:
    """
    Predicate variant for callers that already have parsed entries.

    Avoids re-parsing raw_fm when the command has already done it.
    apply-template parses entries early for its own merge logic, so
    it calls this variant directly rather than paying to parse twice.
    """
    if nf.note_class:
        if nf.class_property == "class":
            fm_dict: dict[str, str | list[str]] = {}
            for key, lines in entries:
                value = lines[0].split(":", 1)[1].strip() if ":" in lines[0] else ""
                fm_dict[key] = value
            if not matches_class_filter(fm_dict, nf.note_class):
                return False
        else:
            # Non-standard class property — exact string match.
            found = False
            for key, lines in entries:
                if key == nf.class_property:
                    found = lines[0].split(":", 1)[1].strip() == nf.note_class
                    break
            if not found:
                return False

    if nf.tag:
        tags = extract_tags_from_entries(entries)
        if nf.tag.lower() not in tags:
            return False

    return True