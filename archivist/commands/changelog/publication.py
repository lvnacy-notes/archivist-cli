"""
archivist changelog publication

Generate a project-level CHANGELOG-{date}.md capturing overall publication project
changes: editions committed, workflow updates, infrastructure changes, etc.

Queries the archive DB for edition SHAs not yet recorded in any changelog,
includes them in the output, then marks them as claimed in a single transaction.
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
    get_db_path,
    get_repo_root,
    init_db,
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
    name = git_root.name.lower().replace("'", "").replace(" ", "-")
    return name


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------

def _find_changelog_template(git_root: Path) -> Path:
    archive_root = git_root / "ARCHIVE"
    if not archive_root.is_dir():
        print(f"Error: No ARCHIVE/ directory found at repo root ({git_root}).", file=sys.stderr)
        sys.exit(1)

    matches = list(archive_root.rglob("CHANGELOG_TEMPLATE.md"))
    if not matches:
        print(f"Error: CHANGELOG_TEMPLATE.md not found anywhere under {archive_root}.", file=sys.stderr)
        sys.exit(1)

    if len(matches) > 1:
        matches.sort(key=lambda p: len(p.parts))
        print(f"Warning: Multiple CHANGELOG_TEMPLATE.md found; using {matches[0]}", file=sys.stderr)

    return matches[0]


# ---------------------------------------------------------------------------
# Archive DB helpers
# ---------------------------------------------------------------------------

def _get_new_edition_shas(git_root: Path) -> list[tuple[str, str]]:
    """
    Query the archive DB for SHAs not yet claimed by a changelog.
    Returns list of (sha, commit_message) tuples.
    """
    db_path = get_db_path(git_root)
    if not db_path.exists():
        print(
            "  Note: No archive DB found. Run 'archivist manifest --register' to populate it.",
            file=sys.stderr,
        )
        return []

    conn = init_db(db_path)
    try:
        rows = conn.execute(
            "SELECT sha, commit_message FROM edition_shas WHERE included_in IS NULL ORDER BY discovered_at"
        ).fetchall()
        return [(row[0], row[1] or "") for row in rows]
    finally:
        conn.close()


def _mark_shas_included(
    git_root: Path,
    shas: list[tuple[str, str]],
    changelog_file: str,
) -> None:
    if not shas:
        return
    db_path = get_db_path(git_root)
    conn = init_db(db_path)
    try:
        conn.executemany(
            "UPDATE edition_shas SET included_in = ? WHERE sha = ?",
            [(changelog_file, sha) for sha, _ in shas],
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Frontmatter builder
# ---------------------------------------------------------------------------

def _build_changelog_frontmatter(
    template_fm: dict,
    commit_sha: str | None,
    new_edition_shas: list[tuple[str, str]],
    num_modified: int,
    num_added: int,
    num_archived: int,
    git_root: Path,
) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    project_name = _get_project_name(git_root)

    auto = {
        "class":          "archive",
        "category":       ["changelog"],
        "log-scope":      "project",
        "modified":       today,
        "updated":        today,
        "commit-sha":     commit_sha or "",
        "editions-sha":   [sha for sha, _ in new_edition_shas],
        "files-modified": num_modified,
        "files-created":  num_added,
        "files-archived": num_archived,
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

    lines = ["---"]
    for key in template_fm.keys():
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


def _build_changelog_body(
    changes: dict,
    commit_sha: str | None,
    new_edition_shas: list[tuple[str, str]],
    git_root: Path,
) -> str:
    today = datetime.now().strftime("%Y-%m-%d")

    def file_list(files, fallback):
        if not files:
            return f"- {fallback}\n"
        return "".join(f"- `{_clean_filename(f)}`: [description]\n" for f in files)

    def sha_list(shas):
        if not shas:
            return "- None\n"
        return "".join(
            f"- `{sha}` — {msg}\n" if msg else f"- `{sha}`\n"
            for sha, msg in shas
        )

    def is_edition_dir(filepath):
        parts = Path(filepath).parts
        return any(p.upper() in ("EDITIONS", "EDITION") for p in parts)

    edition_added    = [f for f in changes["A"] if is_edition_dir(f)]
    edition_modified = [f for f in changes["M"] if is_edition_dir(f)]
    edition_archived = [f for f in changes["D"] if is_edition_dir(f)]
    other_added      = [f for f in changes["A"] if not is_edition_dir(f)]
    other_modified   = [f for f in changes["M"] if not is_edition_dir(f)]
    other_archived   = [f for f in changes["D"] if not is_edition_dir(f)]

    return f"""

# Changelog — {today}

## Overview

| Field | Value |
|-------|-------|
| Date | {today} |
| Commit SHA | {commit_sha or "[fill in after commit]"} |
| Edition SHAs | {len(new_edition_shas)} new |
| Files Added | {len(changes["A"])} |
| Files Modified | {len(changes["M"])} |
| Files Archived | {len(changes["D"])} |

## Edition SHAs

> Commit SHAs harvested from edition manifests not yet recorded in a changelog.

{sha_list(new_edition_shas)}
## Changes

### Editions

#### New
{file_list(edition_added, "No new edition files")}
#### Modified
{file_list(edition_modified, "No edition files modified")}
#### Archived
{file_list(edition_archived, "No edition files archived")}

### Project & Workflow

#### New
{file_list(other_added, "No new project files")}
#### Modified
{file_list(other_modified, "No project files modified")}
#### Archived
{file_list(other_archived, "No project files archived")}

## Summary

### Key Changes
[Summary of what changed and why]

### Decisions Made
[Important decisions and rationale]

### Next Steps
- [ ] [Next task]

---

*Changelog auto-generated by archivist changelog publication — fill in bracketed fields before committing.*
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    git_root = get_repo_root()

    template_path = _find_changelog_template(git_root)
    template_fm = extract_frontmatter(template_path.read_text())
    output_dir = template_path.parent

    # Resolve --path if provided
    scope_path = Path(args.path).resolve() if getattr(args, "path", None) else None

    new_edition_shas = _get_new_edition_shas(git_root)

    # Ensure files are staged
    if not args.dry_run:
        ensure_staged(scope_path, git_root)

    changes = _get_git_changes(args.commit_sha, scope_path)
    num_modified = len(changes["M"])
    num_added = len(changes["A"])
    num_archived = len(changes["D"])

    frontmatter = _build_changelog_frontmatter(
        template_fm, args.commit_sha,
        new_edition_shas,
        num_modified, num_added, num_archived,
        git_root,
    )
    body = _build_changelog_body(changes, args.commit_sha, new_edition_shas, git_root)
    changelog_content = frontmatter + body

    today = datetime.now().strftime("%Y-%m-%d")
    output_path = output_dir / f"CHANGELOG-{today}.md"

    if args.dry_run:
        print("=== DRY RUN — no file written ===\n")
        print(changelog_content)
        print(f"\n=== Would write to: {output_path} ===")
        print(f"  Would mark {len(new_edition_shas)} SHA(s) as included in DB")
    else:
        output_path.write_text(changelog_content)
        _mark_shas_included(git_root, new_edition_shas, str(output_path))
        print(f"✓ Changelog written to: {output_path}")
        if new_edition_shas:
            print(f"✓ {len(new_edition_shas)} SHA(s) marked as included in archive DB")

    print(f"  Project       : {_get_project_name(git_root)}")
    print(f"  Edition SHAs  : {len(new_edition_shas)} new (not yet in any changelog)")
    print(f"  Changes       : {num_added} added, {num_modified} modified, {num_archived} archived")
    if args.commit_sha:
        print(f"  SHA           : {args.commit_sha}")
    else:
        print("  SHA           : (staged changes — run after your commit to lock it in)")