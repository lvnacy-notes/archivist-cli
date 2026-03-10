"""
archivist changelog library

Generate a CHANGELOG-{date}.md for a library (catalog) module.
Tracks works added, updated, and removed alongside a status summary
(raw | in-progress | processed) drawn from the catalog-status frontmatter
field — the only analysis performed. Everything else is generic file change
tracking, making this module suitable for any library regardless of what
it catalogs or what downstream workflows it feeds into.

Invoked by:
    archivist changelog library

Searches for CHANGELOG_TEMPLATE.md recursively under ARCHIVE/ at the repo
or submodule root — shallowest match wins.
Output is written to the same directory the template lives in.
"""

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from archivist.utils import (
    ensure_staged,
    extract_frontmatter,
    get_file_frontmatter,
    get_repo_root,
)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _get_git_changes(commit_sha: str | None, path: Path | None = None) -> dict:
    pathspec = []
    if path is not None:
        pathspec = ["--", str(path)]

    if commit_sha:
        cmd = ["git", "-c", "core.quotepath=false", "diff-tree",
               "--name-status", "-r", commit_sha] + pathspec
    else:
        cmd = ["git", "-c", "core.quotepath=false", "diff-index",
               "--cached", "--name-status", "HEAD"] + pathspec

    try:
        output = subprocess.check_output(cmd, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as e:
        print(f"Error running git command: {e}", file=sys.stderr)
        sys.exit(1)

    changes = {"M": [], "A": [], "D": []}
    for line in output.strip().splitlines():
        if not line:
            continue
        parts = line.split("\t")
        status = parts[0].strip()[0]
        filepath = parts[-1].strip()
        if status in changes:
            changes[status].append(filepath)

    return changes


def _get_project_name(git_root: Path) -> str:
    return git_root.name.lower().replace("'", "").replace(" ", "-")


# ---------------------------------------------------------------------------
# Library analysis
# ---------------------------------------------------------------------------

CATALOG_STATUSES = ("raw", "in-progress", "processed")


def _get_class(fm: dict) -> str:
    """Return the class field value, normalised to lowercase stripped string."""
    val = fm.get("class", "")
    if isinstance(val, list):
        return " ".join(str(v).strip().lower() for v in val)
    return str(val).strip().lower()


def _process_file(filepath: str, git_root: Path, git_status: str, stats: dict) -> bool:
    """
    Read frontmatter from a single .md file and route it into the appropriate
    stats bucket. Returns True if the file was claimed by a named class,
    False if it should fall through to the generic sections.
    """
    full = git_root / filepath
    if full.suffix != ".md":
        return False

    fm = get_file_frontmatter(str(full))
    if not fm:
        return False

    cls = _get_class(fm)

    # Works — anything with a catalog-status field
    if "catalog-status" in fm:
        status = fm.get("catalog-status", "")
        title  = fm.get("sort-title") or fm.get("title") or full.stem
        bucket = {"A": "added", "M": "updated"}.get(git_status)
        if bucket:
            stats["works"][bucket].append((filepath, title, status))
            if status in stats["works"]["by_status"]:
                stats["works"]["by_status"][status] += 1
        else:
            stats["works"]["removed"].append(filepath)
        return True

    # Author cards
    if cls == "author":
        name   = full.stem
        bucket = {"A": "added", "M": "updated"}.get(git_status)
        if bucket:
            stats["authors"][bucket].append((filepath, name))
        else:
            stats["authors"]["removed"].append(filepath)
        return True

    # Publication cards (library publication only)
    if cls == "library publication":
        name   = full.stem
        bucket = {"A": "added", "M": "updated"}.get(git_status)
        if bucket:
            stats["publications"][bucket].append((filepath, name))
        else:
            stats["publications"]["removed"].append(filepath)
        return True

    # Definition cards
    if cls == "definition":
        word    = full.stem
        aliases = fm.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        bucket = {"A": "added", "M": "updated"}.get(git_status)
        if bucket:
            stats["definitions"][bucket].append((filepath, word, aliases))
        else:
            stats["definitions"]["removed"].append(filepath)
        return True

    return False


def _analyse_catalog_changes(changes: dict, git_root: Path) -> dict:
    """
    Route changed .md files into named class buckets:
      works        — files with catalog-status field, bucketed by status
      authors      — class: author
      publications — class: library publication
      definitions  — class: definition (word + aliases surfaced)

    Anything not claimed by a named class falls through to generic sections.
    """
    stats = {
        "works": {
            "added":     [],   # (filepath, title, status)
            "updated":   [],
            "removed":   [],
            "by_status": {s: 0 for s in CATALOG_STATUSES},
        },
        "authors": {
            "added":   [],    # (filepath, name)
            "updated": [],
            "removed": [],    # filepath only
        },
        "publications": {
            "added":   [],    # (filepath, name)
            "updated": [],
            "removed": [],
        },
        "definitions": {
            "added":   [],    # (filepath, word, aliases)
            "updated": [],
            "removed": [],    # filepath only
        },
    }

    for filepath in changes["A"]:
        _process_file(filepath, git_root, "A", stats)
    for filepath in changes["M"]:
        _process_file(filepath, git_root, "M", stats)
    for filepath in changes["D"]:
        # Deleted files have no readable frontmatter — route by extension only,
        # leaving them for the generic removed section unless already claimed above
        _process_file(filepath, git_root, "D", stats)

    return stats


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------

def _find_template(git_root: Path) -> Path:
    archive_root = git_root / "ARCHIVE"
    if not archive_root.is_dir():
        print(f"Error: No ARCHIVE/ directory found at repo root ({git_root}).", file=sys.stderr)
        sys.exit(1)

    matches = list(archive_root.rglob("CHANGELOG_TEMPLATE.md"))
    if not matches:
        print(
            f"Error: CHANGELOG_TEMPLATE.md not found anywhere under {archive_root}.",
            file=sys.stderr,
        )
        sys.exit(1)

    if len(matches) > 1:
        matches.sort(key=lambda p: len(p.parts))
        print(f"Warning: Multiple CHANGELOG_TEMPLATE.md found; using {matches[0]}", file=sys.stderr)

    return matches[0]


# ---------------------------------------------------------------------------
# Frontmatter builder
# ---------------------------------------------------------------------------

def _build_frontmatter(
    template_fm: dict,
    commit_sha: str | None,
    num_modified: int,
    num_added: int,
    num_archived: int,
    lib_stats: dict,
    git_root: Path,
) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    project_name = _get_project_name(git_root)

    auto = {
        "class":          "archive",
        "category":       ["changelog"],
        "log-scope":      "library",
        "modified":       today,
        "updated":        today,
        "commit-sha":     commit_sha or "",
        "files-modified": num_modified,
        "files-created":  num_added,
        "files-archived": num_archived,
        "works-added":         len(lib_stats["works"]["added"]),
        "works-updated":       len(lib_stats["works"]["updated"]),
        "works-removed":       len(lib_stats["works"]["removed"]),
        "authors-added":       len(lib_stats["authors"]["added"]),
        "authors-updated":     len(lib_stats["authors"]["updated"]),
        "publications-added":  len(lib_stats["publications"]["added"]),
        "definitions-added":   len(lib_stats["definitions"]["added"]),
        "tags":           [project_name],
    }

    def get_value(key):
        if key in auto:
            return auto[key]
        val = template_fm.get(key)
        return val if val is not None else ""

    def render_field(key, value):
        if isinstance(value, list):
            if not value:
                return [f"{key}: []"]
            return [f"{key}:"] + [f"  - {item}" for item in value]
        return [f"{key}: {value}"]

    all_keys = list(template_fm.keys())
    for key in auto:
        if key not in all_keys:
            all_keys.append(key)

    lines = ["---"]
    for key in all_keys:
        lines.extend(render_field(key, get_value(key)))
    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Body builder
# ---------------------------------------------------------------------------

def _clean_filename(filepath: str) -> str:
    p = Path(filepath)
    stem = re.sub(r'[^a-zA-Z0-9]+$', '', p.stem)
    return stem + p.suffix


def _work_list(works: list, fallback: str) -> str:
    if not works:
        return f"- {fallback}\n"
    return "".join(
        f"- **{title}** — `{status}`\n"
        for _, title, status in works
    )


def _file_list(files: list, fallback: str) -> str:
    if not files:
        return f"- {fallback}\n"
    return "".join(f"- `{_clean_filename(f)}`: [description]\n" for f in files)


def _entity_list(entries: list, fallback: str) -> str:
    """Render (filepath, name) author/publication entries."""
    if not entries:
        return f"- {fallback}\n"
    return "".join(f"- **{name}**\n" for _, name in entries)


def _removed_list(filepaths: list, fallback: str) -> str:
    if not filepaths:
        return f"- {fallback}\n"
    return "".join(f"- `{_clean_filename(f)}`\n" for f in filepaths)


def _definition_list(entries: list, fallback: str) -> str:
    """Render (filepath, word, aliases) definition entries."""
    if not entries:
        return f"- {fallback}\n"
    lines = []
    for _, word, aliases in entries:
        alias_str = f" *(also: {", ".join(aliases)})*" if aliases else ""
        lines.append(f"- **{word}**{alias_str}\n")
    return "".join(lines)


def _build_body(
    changes: dict,
    lib_stats: dict,
    commit_sha: str | None,
) -> str:
    today = datetime.now().strftime("%Y-%m-%d")

    # Collect all claimed filepaths to exclude from generic sections
    claimed = set()
    for group in lib_stats.values():
        for bucket in ("added", "updated", "removed"):
            for entry in group.get(bucket, []):
                claimed.add(entry[0] if isinstance(entry, tuple) else entry)

    other_added   = [f for f in changes["A"] if f not in claimed]
    other_updated = [f for f in changes["M"] if f not in claimed]
    other_removed = [f for f in changes["D"] if f not in claimed]

    works = lib_stats["works"]
    authors = lib_stats["authors"]
    pubs = lib_stats["publications"]
    defs = lib_stats["definitions"]
    by_status = works["by_status"]

    return f"""

# Changelog — {today}

## Overview

| Field | Value |
|-------|-------|
| Date | {today} |
| Commit SHA | {commit_sha or "[fill in after commit]"} |
| Works Added | {len(works["added"])} |
| Works Updated | {len(works["updated"])} |
| Works Removed | {len(works["removed"])} |
| Authors Added | {len(authors["added"])} |
| Authors Updated | {len(authors["updated"])} |
| Publications Added | {len(pubs["added"])} |
| Publications Updated | {len(pubs["updated"])} |
| Definitions Added | {len(defs["added"])} |
| Definitions Updated | {len(defs["updated"])} |
| Other Files Added | {len(other_added)} |
| Other Files Modified | {len(other_updated)} |

## Status Summary

| Status | Count |
|--------|-------|
| Raw | {by_status["raw"]} |
| In Progress | {by_status["in-progress"]} |
| Processed | {by_status["processed"]} |

## Catalog Changes

### Works Added
{_work_list(works["added"], "No works added")}
### Works Updated
{_work_list(works["updated"], "No works updated")}
### Works Removed
{_removed_list(works["removed"], "No works removed")}
## Author Cards

### Added
{_entity_list(authors["added"], "No author cards added")}
### Updated
{_entity_list(authors["updated"], "No author cards updated")}
### Removed
{_removed_list(authors["removed"], "No author cards removed")}
## Publication Cards

### Added
{_entity_list(pubs["added"], "No publication cards added")}
### Updated
{_entity_list(pubs["updated"], "No publication cards updated")}
### Removed
{_removed_list(pubs["removed"], "No publication cards removed")}
## Definitions

### Added
{_definition_list(defs["added"], "No definitions added")}
### Updated
{_definition_list(defs["updated"], "No definitions updated")}
### Removed
{_removed_list(defs["removed"], "No definitions removed")}
## Other File Changes

### Files Added
{_file_list(other_added, "None")}
### Files Modified
{_file_list(other_updated, "None")}
### Files Removed
{_file_list(other_removed, "None")}
## Notes

### Cataloging Notes
[Context, decisions, or research worth recording for this commit]

### Next Steps
- [ ] [Next cataloging task]

---

*Changelog auto-generated by archivist changelog library — fill in bracketed fields before committing.*
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    git_root = get_repo_root()

    template_path = _find_template(git_root)
    template_fm   = extract_frontmatter(template_path.read_text())
    output_dir    = template_path.parent

    scope_path = Path(args.path).resolve() if getattr(args, "path", None) else None

    if not args.dry_run:
        ensure_staged(scope_path, git_root)

    changes      = _get_git_changes(args.commit_sha, scope_path)
    num_modified = len(changes["M"])
    num_added    = len(changes["A"])
    num_archived = len(changes["D"])

    lib_stats = _analyse_catalog_changes(changes, git_root)

    frontmatter = _build_frontmatter(
        template_fm, args.commit_sha,
        num_modified, num_added, num_archived,
        lib_stats, git_root,
    )
    body = _build_body(changes, lib_stats, args.commit_sha)
    changelog_content = frontmatter + body

    today = datetime.now().strftime("%Y-%m-%d")
    output_path = output_dir / f"CHANGELOG-{today}.md"

    if args.dry_run:
        print("=== DRY RUN — no file written ===\n")
        print(changelog_content)
        print(f"\n=== Would write to: {output_path} ===")
    else:
        output_path.write_text(changelog_content)
        print(f"✓ Changelog written to: {output_path}")

    works = lib_stats["works"]
    authors = lib_stats["authors"]
    pubs = lib_stats["publications"]
    defs = lib_stats["definitions"]

    print(f"  Project          : {_get_project_name(git_root)}")
    print(f"  Works            : {len(works['added'])} added, {len(works['updated'])} updated, {len(works['removed'])} removed")
    print(f"  Status counts    : raw={works['by_status']['raw']}, in-progress={works['by_status']['in-progress']}, processed={works['by_status']['processed']}")
    print(f"  Authors          : {len(authors['added'])} added, {len(authors['updated'])} updated, {len(authors['removed'])} removed")
    print(f"  Publications     : {len(pubs['added'])} added, {len(pubs['updated'])} updated, {len(pubs['removed'])} removed")
    print(f"  Definitions      : {len(defs['added'])} added, {len(defs['updated'])} updated, {len(defs['removed'])} removed")
    print(f"  Files total      : {num_added} added, {num_modified} modified, {num_archived} archived")
    if args.commit_sha:
        print(f"  SHA              : {args.commit_sha}")
    else:
        print("  SHA              : (staged — backfilled by post-commit hook)")