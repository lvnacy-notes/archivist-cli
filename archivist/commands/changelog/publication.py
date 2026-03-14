"""
archivist changelog publication

Generate a project-level CHANGELOG-{date}.md capturing overall publication 
project changes: editions committed, workflow updates, infrastructure changes, 
etc.

Queries the archive DB for edition SHAs not yet recorded in any changelog,
includes them in the output, then marks them as claimed in a single 
transaction.

Scopes automatically to the current git repo (or submodule) root. Output is
written to ARCHIVE/CHANGELOG/. Iterative command runs will preserve user
content and descriptions in the existing changelog for that day, if present.
"""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from archivist.utils import (
    clean_filename,
    detect_dir_renames,
    ensure_staged,
    extract_descriptions,
    extract_user_content,
    find_active_changelog,
    get_db_path,
    get_repo_root,
    infer_undetected_renames,
    init_db,
    reassign_deletions,
    rename_suspicion,
)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _get_git_changes(commit_sha: str | None, path: Path | None = None) -> dict:
    pathspec = ["--", str(path)] if path is not None else []

    if commit_sha:
        cmd = ["git", "-c", "core.quotepath=false", "diff-tree",
               "--name-status", "-M", "-r", commit_sha] + pathspec
    else:
        cmd = ["git", "-c", "core.quotepath=false", "diff-index",
               "--cached", "--name-status", "-M", "HEAD"] + pathspec

    try:
        output = subprocess.check_output(cmd, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as e:
        print(f"Error running git command: {e}", file=sys.stderr)
        sys.exit(1)

    changes = {"M": [], "A": [], "D": [], "R": []}
    for line in output.strip().splitlines():
        if not line:
            continue
        parts = line.split("\t")
        status = parts[0].strip()[0]
        if status == "R" and len(parts) == 3:
            changes["R"].append((parts[1].strip(), parts[2].strip()))
        elif status in changes:
            changes[status].append(parts[-1].strip())

    return changes


def _get_project_name(git_root: Path) -> str:
    return git_root.name.lower().replace("'", "").replace(" ", "-")


def _find_output_dir(git_root: Path) -> Path:
    """
    Locate and return the changelog output directory (ARCHIVE/CHANGELOG/).
    Creates it if it does not yet exist.
    """
    output_dir = git_root / "ARCHIVE/CHANGELOG"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


# ---------------------------------------------------------------------------
# Archive DB helpers
# ---------------------------------------------------------------------------

def _get_edition_shas(git_root: Path, current_changelog: Path | None) -> list[tuple[str, str]]:
    """
    Query the archive DB for SHAs not yet claimed by any changelog, plus any
    already claimed by the current changelog (for iterative runs).
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
        if current_changelog:
            rows = conn.execute(
                """SELECT sha, commit_message FROM edition_shas
                   WHERE included_in IS NULL OR included_in = ?
                   ORDER BY discovered_at""",
                (str(current_changelog),),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT sha, commit_message FROM edition_shas
                   WHERE included_in IS NULL
                   ORDER BY discovered_at""",
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
    commit_sha: str | None,
    edition_shas: list[tuple[str, str]],
    num_modified: int,
    num_added: int,
    num_archived: int,
    git_root: Path,
) -> str:
    today = datetime.now().strftime("%Y-%m-%d")

    auto = {
        "class": "archive",
        "category": ["changelog"],
        "log-scope": "project",
        "modified": today,
        "commit-sha": commit_sha or "",
        "editions-sha": [sha for sha, _ in edition_shas],
        "files-modified": num_modified,
        "files-created": num_added,
        "files-archived": num_archived,
        "tags": [_get_project_name(git_root)],
    }

    def render_field(key, value):
        if isinstance(value, list):
            if not value:
                return [f"{key}: []"]
            return [f"{key}:"] + [f"  - {item}" for item in value]
        return [f"{key}: {value}"]

    lines = ["---"]
    for key, value in auto.items():
        lines.extend(render_field(key, value))
    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Body builder
# ---------------------------------------------------------------------------

def _build_changelog_body(
    changes: dict,
    true_deleted: list[str],
    renames: dict[str, str],
    modified: list[str],
    commit_sha: str | None,
    edition_shas: list[tuple[str, str]],
    descriptions: dict,
    user_content: str | None,
) -> str:
    today = datetime.now().strftime("%Y-%m-%d")

    def is_active_edition_dir(filepath: str) -> bool:
        upper = [p.upper() for p in Path(filepath).parts]
        return any(p in ("EDITIONS", "EDITION") for p in upper) and "ARCHIVE" not in upper

    def is_archive_editions_dir(filepath: str) -> bool:
        upper = [p.upper() for p in Path(filepath).parts]
        return "ARCHIVE" in upper and any(p in ("EDITIONS", "EDITION") for p in upper)

    # Editions moved to ARCHIVE/EDITIONS — stored as (old, new) for display
    edition_archived   = [(old, new) for new, old in renames.items() if is_archive_editions_dir(new)]
    archived_new_paths = {new for _, new in edition_archived}

    edition_added = [f for f in changes["A"] if is_active_edition_dir(f)]
    edition_modified = [f for f in modified if is_active_edition_dir(f) and f not in archived_new_paths]
    other_added = [f for f in changes["A"] if not is_active_edition_dir(f)]
    other_modified = [f for f in modified if not is_active_edition_dir(f) and f not in archived_new_paths]
    other_archived = [f for f in true_deleted if not is_active_edition_dir(f)]

    def file_list(files, fallback, active_renames=None):
        if active_renames is None:
            active_renames = {}
        if not files:
            return f"- {fallback}\n"
        lines = []
        for f in files:
            desc = descriptions.get(f, "[description]")
            old = active_renames.get(f)
            rename_str = (
                f" *(renamed from `{clean_filename(old)}`)*" + rename_suspicion(old, f)
                if old else ""
            )
            if isinstance(desc, list):
                lines.append(f"- `{f}`{rename_str}:")
                for item in desc:
                    lines.append(f"  - {item}")
                lines.append("")  # blank line after sub-bullets for readability
            else:
                lines.append(f"- `{f}`{rename_str}: {desc}")
        return "\n".join(lines) + "\n"

    def archive_list(archived):
        if not archived:
            return "- No editions archived\n"
        return "".join(
            f"- `{clean_filename(old)}` → `{clean_filename(new)}`\n"
            for old, new in archived
        )

    def sha_list(shas):
        if not shas:
            return "- None\n"
        return "".join(
            f"- `{sha}` — {msg}\n" if msg else f"- `{sha}`\n"
            for sha, msg in shas
        )

    user_block = user_content if user_content is not None else """

## Notes


---

*This changelog was automatically generated by Archivist CLI.*
*See [Archivist CLI](https://github.com/lvnacy-notes/archivist-cli) for more information.*

"""

    return f"""

# Changelog — {today}

## Overview

| Field | Value |
|-------|-------|
| Date | {today} |
| Commit SHA | {commit_sha or "[fill in after commit]"} |
| Edition SHAs | {len(edition_shas)} new |
| Files Added | {len(changes["A"])} |
| Files Modified | {len(modified)} |
| Files Archived | {len(true_deleted)} |

## Edition SHAs

> Commit SHAs harvested from edition manifests not yet recorded in a changelog.

{sha_list(edition_shas)}
## Changes

### Editions

#### New
{file_list(edition_added, "No new edition files")}
#### Modified
{file_list(edition_modified, "No edition files modified", renames)}
#### Archived
{archive_list(edition_archived)}

### Project & Workflow

#### New
{file_list(other_added, "No new project files")}
#### Modified
{file_list(other_modified, "No project files modified", renames)}
#### Archived
{file_list(other_archived, "No project files archived")}

<!-- archivist:auto-end -->
{user_block}"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    git_root = get_repo_root()
    output_dir = _find_output_dir(git_root)
    today = datetime.now().strftime("%Y-%m-%d")

    # Resolve existing changelog first — needed for iterative SHA query
    existing = find_active_changelog(output_dir)
    output_path = existing if existing else output_dir / f"CHANGELOG-{today}.md"

    edition_shas = _get_edition_shas(git_root, existing)

    if not args.dry_run:
        ensure_staged(None, git_root)

    changes = _get_git_changes(args.commit_sha)

    inferred_renames     = infer_undetected_renames(changes)
    inferred_old_paths   = {old for old, _ in inferred_renames}
    inferred_new_paths   = {new for _, new in inferred_renames}

    all_renames          = changes["R"] + inferred_renames
    remaining_deleted    = [f for f in changes["D"] if f not in inferred_old_paths]
    remaining_added      = [f for f in changes["A"] if f not in inferred_new_paths]

    dir_renames = detect_dir_renames(changes["R"])
    true_deleted, dir_renamed_files = reassign_deletions(remaining_deleted, dir_renames)
    all_renames = changes["R"] + dir_renamed_files
    renames = {new: old for old, new in all_renames}
    modified = changes["M"] + list(renames.keys())

    num_modified = len(modified)
    num_added = len(remaining_added)
    num_archived = len(true_deleted)

    descriptions = {}
    user_content = None
    if existing:
        existing_text = existing.read_text()
        descriptions = extract_descriptions(existing_text)
        user_content = extract_user_content(existing_text)

    frontmatter = _build_changelog_frontmatter(
        args.commit_sha, edition_shas,
        num_modified, num_added, num_archived,
        git_root,
    )
    body = _build_changelog_body(
        changes, true_deleted, renames, modified,
        args.commit_sha, edition_shas,
        descriptions, user_content,
    )
    changelog_content = frontmatter + body

    if args.dry_run:
        print("=== DRY RUN — no file written ===\n")
        print(changelog_content)
        print(f"\n=== Would write to: {output_path} ===")
        print(f"  Would mark {len(edition_shas)} SHA(s) as included in DB")
    else:
        output_path.write_text(changelog_content)
        _mark_shas_included(git_root, edition_shas, str(output_path))
        verb = "updated" if existing else "written"
        print(f"✓ Changelog {verb}: {output_path}")
        if edition_shas:
            print(f"✓ {len(edition_shas)} SHA(s) marked as included in archive DB")

    print(f"  Project : {_get_project_name(git_root)}")
    print(f"  Edition SHAs : {len(edition_shas)} (not yet in any changelog)")
    print(f"  Changes : {num_added} added, {num_modified} modified, {num_archived} archived")
    if args.commit_sha:
        print(f"  SHA : {args.commit_sha}")
    else:
        print("  SHA : (staged changes — run after your commit to lock it in)")