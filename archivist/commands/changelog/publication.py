"""
archivist changelog publication

Generate a project-level CHANGELOG-{date}.md capturing overall publication
project changes: editions committed, workflow updates, infrastructure changes,
etc.

Queries the archive DB for edition SHAs not yet recorded in any changelog,
includes them in the output, then marks them as claimed by this changelog's
UUID in a single transaction. At seal time, the UUID transitions to the
commit SHA in the DB.

Scopes automatically to the current git repo (or submodule) root. Output is
written to ARCHIVE/CHANGELOG/. Iterative command runs will preserve user
content and descriptions in the existing changelog for that day, if present.
"""

import argparse
import sys
from pathlib import Path
from typing import cast

from archivist.commands.changelog.changelog_base import ChangelogContext, run_changelog
from archivist.utils import (
    format_file_list,
    get_db_path,
    get_project_name,
    get_today,
    infer_undetected_renames,
    init_db,
    rename_display_path,
    render_field,
)


# ---------------------------------------------------------------------------
# Archive DB helpers
# ---------------------------------------------------------------------------

def _get_edition_shas(git_root: Path, current_uuid: str | None) -> list[tuple[str, str]]:
    """
    Query the archive DB for SHAs not yet claimed by any changelog, plus any
    already claimed by the current changelog UUID (for iterative re-runs).

    On first run, current_uuid is freshly generated and no SHAs will match
    the OR branch — only unclaimed (NULL) SHAs are returned.

    On re-runs, current_uuid matches the UUID stored in the existing
    changelog's frontmatter. The OR branch re-surfaces SHAs already claimed
    by this specific changelog, ensuring they stay in the output across runs.

    After sealing, seal_changelog_in_db() transitions included_in from UUID
    to commit SHA. Those entries will never be returned here again.
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
            """SELECT sha, commit_message FROM edition_shas
               WHERE included_in IS NULL OR included_in = ?
               ORDER BY discovered_at""",
            (current_uuid,),
        ).fetchall()
        return [(row[0], row[1] or "") for row in rows]
    finally:
        conn.close()


def _mark_shas_included(
    git_root: Path,
    shas: list[tuple[str, str]],
    changelog_uuid: str,
) -> None:
    """
    Claim edition SHAs against this changelog's UUID.

    Stores the UUID rather than a file path — stable across renames and
    survives the post-commit hook renaming the changelog file. At seal time,
    seal_changelog_in_db() transitions these to the commit SHA.
    """
    if not shas:
        return
    conn = init_db(get_db_path(git_root))
    try:
        conn.executemany(
            "UPDATE edition_shas SET included_in = ? WHERE sha = ?",
            [(changelog_uuid, sha) for sha, _ in shas],
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Post-changes hook
# ---------------------------------------------------------------------------

def _analyse_publication(ctx: ChangelogContext) -> None:
    """
    Run publication-specific rename inference on top of the base runner's
    rename processing, then query the archive DB for unclaimed edition SHAs.
    Stores results in ctx.data for use by the builders.
    """
    # Publication does an extra pass to catch D/A pairs that git's -M missed
    inferred = infer_undetected_renames(ctx.changes)
    inferred_old = {old for old, _ in inferred}
    inferred_new = {new for _, new in inferred}

    ctx.data["remaining_added"] = [f for f in ctx.changes["A"] if f not in inferred_new]
    ctx.processed_changes["D"] = [f for f in ctx.processed_changes["D"] if f not in inferred_old]

    ctx.data["edition_shas"] = _get_edition_shas(ctx.git_root, ctx.changelog_uuid)


# ---------------------------------------------------------------------------
# Post-write hook
# ---------------------------------------------------------------------------

def _mark_shas_post_write(ctx: ChangelogContext) -> None:
    edition_shas: list[tuple[str, str]] = cast(list[tuple[str, str]], ctx.data["edition_shas"])
    if ctx.args.dry_run:
        print(
            f"  Would mark {len(edition_shas)} SHA(s) as included in DB "
            f"(UUID: {ctx.changelog_uuid[:8]}...)"
        )
        return
    _mark_shas_included(ctx.git_root, edition_shas, ctx.changelog_uuid)
    if edition_shas:
        print(f"✓ {len(edition_shas)} SHA(s) marked as included in archive DB")


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _build_frontmatter(ctx: ChangelogContext) -> str:
    today = get_today()
    edition_shas: list[tuple[str, str]] = cast(list[tuple[str, str]], ctx.data["edition_shas"])
    remaining_added: list[str] = cast(list[str], ctx.data["remaining_added"])
    auto = {
        "class": "archive",
        "category": ["changelog"],
        "log-scope": "project",
        "modified": today,
        "UUID": ctx.changelog_uuid,
        "commit-sha": ctx.args.commit_sha or "",
        "editions-sha": [sha for sha, _ in edition_shas],
        "files-modified": len(ctx.modified),
        "files-created": len(remaining_added),
        "files-archived": len(ctx.true_deleted),
        "tags": [get_project_name(ctx.git_root)],
    }
    lines = ["---"]
    for key, value in auto.items():
        lines.extend(render_field(key, value))
    lines.append("---")
    return "\n".join(lines)


def _build_body(ctx: ChangelogContext) -> str:
    today = get_today()
    edition_shas: list[tuple[str, str]] = cast(list[tuple[str, str]], ctx.data["edition_shas"])
    descriptions = ctx.descriptions or {}
    commit_sha = ctx.args.commit_sha

    def is_active_edition_dir(filepath: str) -> bool:
        upper = [p.upper() for p in Path(filepath).parts]
        return any(p in ("EDITIONS", "EDITION") for p in upper) and "ARCHIVE" not in upper

    def is_archive_editions_dir(filepath: str) -> bool:
        upper = [p.upper() for p in Path(filepath).parts]
        return "ARCHIVE" in upper and any(p in ("EDITIONS", "EDITION") for p in upper)

    edition_archived = [(old, new) for new, old in ctx.renames.items() if is_archive_editions_dir(new)]
    archived_new_paths = {new for _, new in edition_archived}

    edition_added = [f for f in ctx.changes["A"] if is_active_edition_dir(f)]
    edition_modified = [f for f in ctx.modified if is_active_edition_dir(f) and f not in archived_new_paths]
    other_added = [f for f in ctx.changes["A"] if not is_active_edition_dir(f)]
    other_modified = [f for f in ctx.modified if not is_active_edition_dir(f) and f not in archived_new_paths]
    other_archived = [f for f in ctx.true_deleted if not is_active_edition_dir(f)]

    def archive_list(archived: list[tuple[str, str]]) -> str:
        if not archived:
            return "- No editions archived\n"
        return "".join(
            f"- `{rename_display_path(old, new)}` → `{new}`\n"
            for old, new in archived
        )

    def sha_list(shas: list[tuple[str, str]]) -> str:
        if not shas:
            return "- None\n"
        return "".join(
            f"- `{sha}` — {msg}\n" if msg else f"- `{sha}`\n"
            for sha, msg in shas
        )

    user_block = ctx.user_content if ctx.user_content is not None else """
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
| Files Added | {len(ctx.changes["A"])} |
| Files Modified | {len(ctx.modified)} |
| Files Archived | {len(ctx.true_deleted)} |

## Edition SHAs

> Commit SHAs harvested from edition manifests not yet recorded in a changelog.

{sha_list(edition_shas)}
## Changes

### Editions

#### New
{format_file_list(edition_added, "No new edition files", descriptions)}
#### Modified
{format_file_list(edition_modified, "No edition files modified", descriptions, ctx.renames)}
#### Archived
{archive_list(edition_archived)}

### Project & Workflow

#### New
{format_file_list(other_added, "No new project files", descriptions)}
#### Modified
{format_file_list(other_modified, "No project files modified", descriptions, ctx.renames)}
#### Archived
{format_file_list(other_archived, "No project files archived", descriptions)}

<!-- archivist:auto-end -->
{user_block}"""


def _print_summary(ctx: ChangelogContext) -> None:
    edition_shas: list[tuple[str, str]] = cast(list[tuple[str, str]], ctx.data["edition_shas"])
    remaining_added: list[str] = cast(list[str], ctx.data["remaining_added"])
    print(f"  Project      : {get_project_name(ctx.git_root)}")
    print(f"  Edition SHAs : {len(edition_shas)} (not yet in any changelog)")
    print(
        f"  Changes      : {len(remaining_added)} added, "
        f"{len(ctx.modified)} modified, {len(ctx.true_deleted)} archived"
    )
    if ctx.args.commit_sha:
        print(f"  SHA          : {ctx.args.commit_sha}")
    else:
        print("  SHA          : (staged — backfilled by post-commit hook)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    run_changelog(
        args,
        module_type="publication",
        build_frontmatter=_build_frontmatter,
        build_body=_build_body,
        post_changes=_analyse_publication,
        post_write=_mark_shas_post_write,
        print_summary=_print_summary,
    )