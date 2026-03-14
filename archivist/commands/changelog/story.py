"""
archivist changelog story

Generate a story project CHANGELOG-{date}.md capturing writing session changes:
scene additions, character development, plot advancement, workflow updates, 
etc.

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
    get_repo_root,
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
# Template
# ---------------------------------------------------------------------------

def _build_frontmatter(
    commit_sha: str | None,
    num_modified: int,
    num_added: int,
    num_archived: int,
    git_root: Path,
) -> str:
    today = datetime.now().strftime("%Y-%m-%d")

    auto = {
        "class":          "archive",
        "category":       ["changelog"],
        "log-scope":      "story",
        "modified":       today,
        "commit-sha":     commit_sha or "",
        "files-modified": num_modified,
        "files-created":  num_added,
        "files-archived": num_archived,
        "tags":           [_get_project_name(git_root)],
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

def _build_body(
    changes: dict,
    true_deleted: list[str],
    renames: dict[str, str],
    modified: list[str],
    commit_sha: str | None,
    descriptions: dict,
    user_content: str | None,
) -> str:
    today = datetime.now().strftime("%Y-%m-%d")

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
                f" *(renamed from `{clean_filename(old)}`)* {rename_suspicion(old, f)}"
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
| Files Added | {len(changes["A"])} |
| Files Modified | {len(modified)} |
| Files Archived | {len(true_deleted)} |

## Changes

### Story Development
- [ ] Scene additions / modifications
- [ ] Character development progress
- [ ] Plot advancement
- [ ] Dialogue refinements

### Technical Updates
- [ ] File organization improvements
- [ ] Metadata updates
- [ ] Template modifications
- [ ] Workflow enhancements

### Publication Preparation
- [ ] Social media integration updates
- [ ] Archive management
- [ ] Output generation improvements

## Detailed Change Log

### Files Modified
{file_list(modified, "No files modified", renames)}
### New Files Created
{file_list(changes["A"], "No new files")}
### Files Removed / Archived
{file_list(true_deleted, "No files archived")}

<!-- archivist:auto-end -->
{user_block}
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    git_root = get_repo_root()
    output_dir = _find_output_dir(git_root)

    # Ensure files are staged
    if not args.dry_run:
        ensure_staged(None, git_root)

    changes = _get_git_changes(args.commit_sha)

    dir_renames = detect_dir_renames(changes["R"])
    true_deleted, dir_renamed_files = reassign_deletions(changes["D"], dir_renames)
    all_renames = changes["R"] + dir_renamed_files
    renames = {new: old for old, new in all_renames}
    modified = changes["M"] + list(renames.keys())

    num_modified = len(modified)
    num_added = len(changes["A"])
    num_archived = len(true_deleted)

    today = datetime.now().strftime("%Y-%m-%d")
    output_path = output_dir / f"CHANGELOG-{today}.md"

    existing = find_active_changelog(output_dir)
    descriptions = {}
    user_content = None
    if existing:
        existing_text = existing.read_text()
        descriptions = extract_descriptions(existing_text)
        user_content = extract_user_content(existing_text)
        output_path = existing

    frontmatter = _build_frontmatter(
        args.commit_sha,
        num_modified, num_added, num_archived,
        git_root,
    )
    body = _build_body(
        changes, true_deleted, renames, modified,
        args.commit_sha, descriptions, user_content,
    )
    changelog_content = frontmatter + body

    if args.dry_run:
        print("=== DRY RUN — no file written ===\n")
        print(changelog_content)
        print(f"\n=== Would write to: {output_path} ===")
    else:
        output_path.write_text(changelog_content)
        verb = "updated" if existing else "written"
        print(f"✓ Changelog {verb}: {output_path}")

    print(f"  Project  : {_get_project_name(git_root)}")
    print(f"  Changes  : {num_added} added, {num_modified} modified, {num_archived} archived")
    if args.commit_sha:
        print(f"  SHA      : {args.commit_sha}")
    else:
        print("  SHA      : (staged changes — run after your commit to lock it in)")