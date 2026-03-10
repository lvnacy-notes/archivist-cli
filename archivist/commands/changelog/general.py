"""
archivist changelog general

Generate a general-purpose CHANGELOG-{date}.md for any project type.
No project-specific sections — just clean git diff output and blank fields
for you to fill in.

Invoked by either:
    archivist changelog
    archivist changelog general

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
    git_root: Path,
) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    project_name = _get_project_name(git_root)

    auto = {
        "class":          "archive",
        "category":       ["changelog"],
        "log-scope":      "general",
        "modified":       today,
        "commit-sha":     commit_sha or "",
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


def _build_body(
    changes: dict,
    commit_sha: str | None,
) -> str:
    today = datetime.now().strftime("%Y-%m-%d")

    def file_list(files, fallback):
        if not files:
            return f"- {fallback}\n"
        return "".join(f"- `{_clean_filename(f)}`: [description]\n" for f in files)

    return f"""

# Changelog — {today}

## Overview

| Field | Value |
|-------|-------|
| Date | {today} |
| Commit SHA | {commit_sha or "[fill in after commit]"} |
| Files Added | {len(changes["A"])} |
| Files Modified | {len(changes["M"])} |
| Files Archived | {len(changes["D"])} |

## Changes

### Files Modified
{file_list(changes["M"], "No files modified")}
### New Files Created
{file_list(changes["A"], "No new files")}
### Files Removed / Archived
{file_list(changes["D"], "No files archived")}

## Summary

### Key Changes
[Summary of what changed and why]

### Decisions Made
[Important decisions and rationale]

### Next Steps
- [ ] [Next task]

---

*Changelog auto-generated by archivist changelog — fill in bracketed fields before committing.*
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    git_root = get_repo_root()

    template_path = _find_template(git_root)
    template_fm = extract_frontmatter(template_path.read_text())
    output_dir = template_path.parent

    # Resolve --path if provided
    scope_path = Path(args.path).resolve() if getattr(args, "path", None) else None

    # Ensure files are staged
    if not args.dry_run:
        ensure_staged(scope_path, git_root)

    changes = _get_git_changes(args.commit_sha, scope_path)
    num_modified = len(changes["M"])
    num_added = len(changes["A"])
    num_archived = len(changes["D"])

    frontmatter = _build_frontmatter(
        template_fm, args.commit_sha,
        num_modified, num_added, num_archived,
        git_root,
    )
    body = _build_body(changes, args.commit_sha)
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

    print(f"  Project  : {_get_project_name(git_root)}")
    print(f"  Changes  : {num_added} added, {num_modified} modified, {num_archived} archived")
    if args.commit_sha:
        print(f"  SHA      : {args.commit_sha}")
    else:
        print("  SHA      : (staged changes — run after your commit to lock it in)")