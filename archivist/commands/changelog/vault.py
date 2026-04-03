"""
archivist changelog vault

Generate a vault-level changelog capturing:
  - Vault-wide file changes (templates, config, scripts)
  - Which submodules were updated in this commit
  - Which submodules have uncommitted changes
  - Which submodules have unpushed commits

Scopes automatically to the current git repo (or submodule) root.
Output is written to ARCHIVE/. Iterative command runs will preserve
user content and descriptions in the existing changelog for that day,
if present.
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
    report_changes,
    write_changelog,
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
    output_dir = git_root / "ARCHIVE"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


# ---------------------------------------------------------------------------
# Submodule helpers
# ---------------------------------------------------------------------------

def _get_submodules(git_root: Path) -> list[str]:
    """Return list of submodule paths registered in this repo."""
    try:
        output = subprocess.check_output(
            ["git", "submodule", "status"],
            stderr=subprocess.PIPE, text=True, cwd=git_root,
        )
        modules = []
        for line in output.strip().splitlines():
            if not line:
                continue
            # Format: [+- ]<sha> <path> (<description>)
            parts = line.strip().split()
            if len(parts) >= 2:
                modules.append(parts[1])
        return modules
    except subprocess.CalledProcessError:
        return []


def _get_submodules_in_commit(commit_sha: str | None, git_root: Path) -> list[str]:
    """Return submodule paths that were updated in the given commit or staged changes."""
    if commit_sha:
        cmd = ["git", "diff-tree", "--name-only", "-r", commit_sha]
    else:
        cmd = ["git", "diff-index", "--cached", "--name-only", "HEAD"]

    try:
        output = subprocess.check_output(cmd, stderr=subprocess.PIPE, text=True, cwd=git_root)
    except subprocess.CalledProcessError:
        return []

    all_submodules = set(_get_submodules(git_root))
    return [f for f in output.strip().splitlines() if f in all_submodules]


def _get_submodule_status(git_root: Path) -> dict[str, dict]:
    """
    For each submodule, report:
      - has_uncommitted: bool
      - has_unpushed: bool
      - current_sha: str
    """
    submodules = _get_submodules(git_root)
    status = {}

    for sub in submodules:
        sub_path = git_root / sub
        info = {"has_uncommitted": False, "has_unpushed": False, "current_sha": ""}

        try:
            # Current SHA
            sha = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                stderr=subprocess.PIPE, text=True, cwd=sub_path,
            ).strip()
            info["current_sha"] = sha

            # Uncommitted changes
            dirty = subprocess.check_output(
                ["git", "status", "--porcelain"],
                stderr=subprocess.PIPE, text=True, cwd=sub_path,
            ).strip()
            info["has_uncommitted"] = bool(dirty)

            # Unpushed commits
            unpushed = subprocess.check_output(
                ["git", "log", "@{u}..", "--oneline"],
                stderr=subprocess.PIPE, text=True, cwd=sub_path,
            ).strip()
            info["has_unpushed"] = bool(unpushed)

        except subprocess.CalledProcessError:
            pass  # submodule may not be initialized — leave defaults

        status[sub] = info

    return status


# ---------------------------------------------------------------------------
# Frontmatter builder
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
        "log-scope":      "vault",
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
    git_root: Path,
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

    def submodule_list(subs, fallback):
        if not subs:
            return f"- {fallback}\n"
        return "".join(f"- `{s}`\n" for s in subs)

    updated_subs = _get_submodules_in_commit(commit_sha, git_root)
    sub_status = _get_submodule_status(git_root)

    # Build submodule status table
    if sub_status:
        sub_rows = "\n".join(
            f"| `{path}` | {info['current_sha']} | "
            f"{'⚠️ yes' if info['has_uncommitted'] else 'clean'} | "
            f"{'⚠️ yes' if info['has_unpushed'] else 'pushed'} |"
            for path, info in sub_status.items()
        )
        sub_table = (
            "| Module | SHA | Uncommitted | Unpushed |\n"
            "|--------|-----|-------------|----------|\n"
            f"{sub_rows}"
        )
    else:
        sub_table = "_No submodules registered._"

    # Categorise modified and added files by pattern
    template_modified = [f for f in modified if "template" in f.lower() or "scaffold" in f.lower()]
    config_modified = [f for f in modified if any(x in f.lower() for x in (".archivist", "script", "hook", ".gitmodules"))]
    other_modified = [f for f in modified if "template" not in f.lower() and "scaffold" not in f.lower()]

    template_added = [f for f in changes["A"] if "template" in f.lower() or "scaffold" in f.lower()]
    config_added = [f for f in changes["A"] if any(x in f.lower() for x in (".archivist", "script", "hook", ".gitmodules"))]
    other_added = [f for f in changes["A"] if "template" not in f.lower() and "scaffold" not in f.lower()]

    user_block = user_content if user_content is not None else """

## Notes


---

*This changelog was automatically generated by Archivist CLI.*
*See [Archivist CLI](https://github.com/lvnacy-notes/archivist-cli) for more information.*

"""

    return f"""

# Changelog — {today} (Vault)

## Overview

| Field | Value |
|-------|-------|
| Date | {today} |
| Commit SHA | {commit_sha or "[fill in after commit]"} |
| Files Added | {len(changes["A"])} |
| Files Modified | {len(modified)} |
| Files Archived | {len(true_deleted)} |
| Submodules Updated | {len(updated_subs)} |

## Submodules

### Updated in This Commit
{submodule_list(updated_subs, "No submodules updated in this commit")}
### Status Overview

{sub_table}

## Vault Changes

### Templates & Scaffolding
{file_list(template_modified + template_added, "No template changes", renames)}
### Config & Scripts
{file_list(config_modified + config_added, "No config or script changes", renames)}
### Other Files Modified
{file_list(other_modified, "No other modifications", renames)}
### New Files
{file_list(other_added, "No new files")}
### Removed / Archived
{file_list(true_deleted, "No files archived")}

<!-- archivist:auto-end -->
{user_block}
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    git_root = get_repo_root()
    print(f"  📁 Repo root : {git_root}")
    output_dir = _find_output_dir(git_root)
    print(f"  📁 Output dir: {output_dir}")

    if not args.dry_run:
        ensure_staged(None, git_root)

    changes = _get_git_changes(args.commit_sha)

    dir_renames = detect_dir_renames(changes["R"])
    true_deleted, dir_renamed_files = reassign_deletions(changes["D"], dir_renames)
    all_renames = changes["R"] + dir_renamed_files
    renames = {new: old for old, new in all_renames}
    modified = changes["M"] + list(renames.keys())
    report_changes(changes, modified, true_deleted)

    num_modified = len(modified)
    num_added = len(changes["A"])
    num_archived = len(true_deleted)

    today = datetime.now().strftime("%Y-%m-%d")
    output_path = output_dir / f"CHANGELOG-{today}.md"

    existing = find_active_changelog(output_dir)
    descriptions = {}
    user_content = None
    if existing:
        print(f"  🔍 Found existing changelog: {existing.name} — updating in place")
        existing_text = existing.read_text()
        descriptions = extract_descriptions(existing_text)
        user_content = extract_user_content(existing_text)
        output_path = existing
    else:
        print(f"  🆕 No existing changelog found — creating {output_path.name}")

    frontmatter = _build_frontmatter(
        args.commit_sha,
        num_modified, num_added, num_archived,
        git_root,
    )
    body = _build_body(
        changes, true_deleted, renames, modified,
        args.commit_sha, git_root, descriptions, user_content,
    )
    changelog_content = frontmatter + body

    if args.dry_run:
        print("=== DRY RUN — no file written ===\n")
        print(changelog_content)
        print(f"\n=== Would write to: {output_path} ===")
    else:
        write_changelog(output_path, changelog_content, existing=bool(existing))

    print(f"  Project    : {_get_project_name(git_root)}")
    print(f"  Changes    : {num_added} added, {num_modified} modified, {num_archived} archived")
    updated_subs = _get_submodules_in_commit(args.commit_sha, git_root)
    print(f"  Submodules : {len(updated_subs)} updated in this commit")
    if args.commit_sha:
        print(f"  SHA        : {args.commit_sha}")
    else:
        print("  SHA        : (staged changes — run after your commit to lock it in)")