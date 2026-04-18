# ---------------------------------------------------------------------------
# Frontmatter parsing & rendering
# ---------------------------------------------------------------------------
#
# This module is the single source of truth for everything frontmatter-shaped.
# If you're about to implement yet another regex that pokes at YAML property
# lines in a command module, stop. The answer lives here. Put it here.
#
# Public surface:
#   Parsing & detection   — FRONTMATTER_RE, has_frontmatter, extract_frontmatter,
#                           parse_frontmatter_entries
#   Property matching     — property_line_pattern, match_property_line
#   Property mutation     — remove_property_from_frontmatter
#   Tag extraction        — extract_tags_from_entries
#   File I/O              — safe_read_markdown, safe_write_markdown
#   File scanning         — find_markdown_files
#   File transforms       — update_frontmatter_in_file, process_markdown_files
#   Class filtering       — get_file_class, get_file_frontmatter, matches_class_filter
#   Rendering             — render_field
# ---------------------------------------------------------------------------

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# Core regex
# ---------------------------------------------------------------------------

# Matches a YAML frontmatter block at the top of a file.
# Permissive about trailing whitespace on the delimiter lines.
# The \n? allows empty frontmatter blocks (---\n---) with no content between delimiters.
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n?---\s*\n", re.DOTALL)


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def has_frontmatter(content: str) -> bool:
    """Return True if content opens with a valid YAML frontmatter block."""
    return bool(FRONTMATTER_RE.match(content))


def extract_frontmatter(content: str) -> dict[str, str | list[str]]:
    """
    Parse the YAML frontmatter block from a markdown string.
    Returns an empty dict if the block is absent or unparseable.
    Does not raise. Ever. You're welcome.
    """
    match = FRONTMATTER_RE.search(content)
    if not match:
        return {}
    try:
        data: dict[str, str | list[str]] | None = yaml.safe_load(match.group(1))
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError:
        return {}


# ---------------------------------------------------------------------------
# Property line matching
# ---------------------------------------------------------------------------

def property_line_pattern(prop: str) -> re.Pattern[str]:
    """
    Return a compiled regex matching a YAML key line for the given property name.
    Handles any special regex characters lurking in your property names — yes,
    including whatever cursed thing you named it.
    """
    if ':' in prop:
        # YAML property names cannot contain colons. Reject suspicious input.
        return re.compile(r'(?!)')  # Negative lookahead that always fails
    return re.compile(rf"^{re.escape(prop)}\s*:")


def match_property_line(line: str, prop: str) -> bool:
    """
    Return True if line is the YAML key line for prop.
    One regex, compiled once, used everywhere. Stop copy-pasting it.
    """
    return bool(property_line_pattern(prop).match(line))


# ---------------------------------------------------------------------------
# Property removal
# ---------------------------------------------------------------------------

def remove_property_from_frontmatter(raw_fm: str, prop: str) -> tuple[str, bool]:
    """
    Remove a property and all its continuation lines from raw YAML frontmatter text.

    Handles scalar values, inline lists, and multi-line block sequences — i.e.,
    all the ways YAML decides to be a special snowflake about list formatting.

    Returns:
        (updated_frontmatter: str, was_found: bool)

    The caller decides what to do when was_found is False. Don't shoot the messenger.
    """
    lines = raw_fm.split("\n")
    result: list[str] = []
    i = 0
    found = False

    while i < len(lines):
        if match_property_line(lines[i], prop):
            found = True
            i += 1
            # Consume indented continuation lines (block sequences, wrapped scalars)
            while i < len(lines) and lines[i].startswith((" ", "\t")):
                i += 1
        else:
            result.append(lines[i])
            i += 1

    return "\n".join(result), found


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def safe_read_markdown(path: Path) -> str | None:
    """
    Read a file and return its content as a string.
    Returns None on any OSError and prints the failure to stderr.
    Does not raise — the caller gets None and deals with it.
    """
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        print(f"  ⚠️  Could not read '{path}': {e}", file=sys.stderr)
        return None


def safe_write_markdown(path: Path, content: str) -> bool:
    """
    Write content to a file.
    Returns True on success, False on any OSError (printed to stderr).
    Does not raise — the caller gets False and deals with it.
    """
    try:
        path.write_text(content, encoding="utf-8")
        return True
    except OSError as e:
        print(f"  ⚠️  Could not write '{path}': {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Structured frontmatter parsing
# ---------------------------------------------------------------------------

def parse_frontmatter_entries(raw: str) -> list[tuple[str, list[str]]]:
    """
    Parse raw frontmatter text into an ordered list of (key, raw_lines) tuples.

    Each entry holds the key name and ALL the raw text lines that belong to
    it — the key line itself plus any indented continuation lines. This
    preserves the original text verbatim for round-trip safety: no YAML
    round-trip surprises, no value mangling, no lost comments.

    This is the structured representation apply-template needs. For a simple
    parsed dict, use extract_frontmatter() instead.
    """
    entries: list[tuple[str, list[str]]] = []
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


def extract_tags_from_entries(entries: list[tuple[str, list[str]]]) -> list[str]:
    """
    Extract tag values from parsed frontmatter entries.

    Handles all three of YAML's charming list personalities:
      inline:   tags: [foo, bar, "baz qux"]
      scalar:   tags: foo
      block:    tags:
                  - foo
                  - bar

    Returns lowercase stripped strings so callers don't have to think about it.
    Returns an empty list if no tags key is found.
    """
    for key, lines in entries:
        if key != "tags":
            continue
        value_part = lines[0].split(":", 1)[1].strip()

        if value_part.startswith("[") and value_part.endswith("]"):
            # Inline list: tags: [foo, bar]
            return [
                t.strip().strip("\"'").lower()
                for t in value_part[1:-1].split(",")
                if t.strip()
            ]
        elif value_part:
            # Scalar: tags: foo
            return [value_part.strip("\"'").lower()]
        else:
            # Block sequence:
            # tags:
            #   - foo
            #   - bar
            return [
                line.strip().lstrip("- ").strip("\"'").lower()
                for line in lines[1:]
                if line.strip()
            ]

    return []


# ---------------------------------------------------------------------------
# File scanning
# ---------------------------------------------------------------------------

def find_markdown_files(root: Path, filters: dict[str, Path | str] | None = None) -> list[Path]:
    """
    Walk root recursively and return a sorted list of every .md file found.

    Optional filters dict:
        "path_prefix": Path | str
            Restrict results to paths under this directory. Pass a narrower
            root instead when you can — this filter exists for cases where
            you're already holding a repo-root reference and need to scope
            without re-rooting the walk.

    Class and tag filtering intentionally live in callbacks. Doing it here
    would require reading every file twice. Don't be that person.
    """
    files = sorted(root.rglob("*.md"))

    if filters:
        if prefix := filters.get("path_prefix"):
            prefix_path = Path(prefix).resolve()
            files = [f for f in files if f.is_relative_to(prefix_path)]

    return files


# ---------------------------------------------------------------------------
# File transform helpers
# ---------------------------------------------------------------------------

def update_frontmatter_in_file(
    path: Path,
    transformer_fn: Callable[[str, str], str | None],
) -> str | None:
    """
    Read a markdown file, extract frontmatter, and hand (raw_frontmatter, body)
    to transformer_fn. If the transformer returns a new content string, write it
    back to disk. Returns the new content string on success, None otherwise.

    transformer_fn(raw_frontmatter: str, body: str) -> str | None
        Return None to signal "nothing to do here". The file is left untouched.

    Limitation: only operates on files that ALREADY have a frontmatter block.
    For creating frontmatter from scratch (i.e. `frontmatter add` on a bare
    note), handle that case separately before calling this. See add.py.

    Returns None if:
      - the file can't be read
      - no frontmatter block is found
      - transformer_fn returns None (no change needed)
      - the file can't be written
    """
    content = safe_read_markdown(path)
    if content is None:
        return None

    if not has_frontmatter(content):
        return None

    match: object = FRONTMATTER_RE.match(content)
    if match is None:
        return None
    raw_fm = match.group(1)
    body = content[match.end():]
    new_content = transformer_fn(raw_fm, body)

    if new_content is None:
        return None

    if not safe_write_markdown(path, new_content):
        return None

    return new_content


def process_markdown_files(
    root: Path,
    callback: Callable[[Path], bool],
    filters: dict[str, Path | str] | None = None,
) -> int:
    """
    Walk root for .md files via find_markdown_files(), invoke callback on each,
    and return the count of files for which callback returned True.

    callback(path: Path) -> bool
        Return True if the file was changed (or would be, in dry-run mode).
        The callback owns all messaging, dry-run checks, and error handling.

    filters (optional dict): passed directly to find_markdown_files().
        See that function for the supported schema.

    Class and tag filtering is intentionally NOT done here — doing so would
    require reading every file twice (once to filter, once to transform). Put
    that logic in the callback where the file is already being read.
    """
    files = find_markdown_files(root, filters)
    return sum(1 for f in files if callback(f))


# ---------------------------------------------------------------------------
# Class-based file helpers
# ---------------------------------------------------------------------------

def get_file_class(filepath: Path) -> str | None:
    """
    Return the 'class' frontmatter field as a lowercase string,
    or None if absent or unreadable.
    """
    fm = get_file_frontmatter(filepath)
    if fm is None:
        return None
    val = fm.get("class")
    return str(val).strip().lower() if val is not None else None


def get_file_frontmatter(filepath: Path | str) -> dict[str, str | list[str]] | None:
    """
    Parse and return the frontmatter dict from a markdown file.
    Accepts either a Path or a string filepath.
    Returns None if the file is not markdown, has no frontmatter,
    or cannot be read.
    """
    filepath = Path(filepath)
    if filepath.suffix.lower() not in (".md", ".markdown"):
        return None
    content = safe_read_markdown(filepath)
    if content is None:
        return None
    if not has_frontmatter(content):
        return None
    match: object = FRONTMATTER_RE.match(content)
    if match is None:
        return None
    try:
        data: dict[str, str | list[str]] | None = yaml.safe_load(match.group(1))
        return data if isinstance(data, dict) else None
    except yaml.YAMLError:
        return None


def matches_class_filter(fm: dict[str, str | list[str]], target_class: str) -> bool:
    """
    Return True if the frontmatter 'class' field matches target_class.
    Case-insensitive. Use this when you already have a parsed fm dict
    and don't want to re-read the file just to check class.
    """
    val = fm.get("class")
    return (
        str(val).strip().lower() == target_class.strip().lower()
        if val is not None
        else False
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render_field(key: str, value: object) -> list[str]:
    """Render a single frontmatter key-value pair as YAML lines."""
    if isinstance(value, list):
        if not value:
            return [f"{key}: []"]
        # value is legitmately unknown; it could be a list, it could be a
        # string, it could be a number, it could be date. ANYTHING.
        return [f"{key}:"] + [f"  - {item}" for item in value] # type: ignore
    return [f"{key}: {value}"]