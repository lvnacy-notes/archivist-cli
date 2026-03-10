"""
archivist changelog vault

Generate a vault-level changelog capturing:
  - Vault-wide file changes (templates, config, scripts)
  - Which submodules were updated in this commit
  - Which submodules have uncommitted changes
  - Which submodules have unpushed commits

Searches for CHANGELOG_TEMPLATE.md recursively under ARCHIVE/ at the vault root.
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
    pathspec = ["--", str(path)] if path is not None else []

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
# Template
# ---------------------------------------------------------------------------

def _find_template(git_root: Path) -> Path:
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
        "log-scope":      "vault",
        "modified":       today,
        "updated":        today,
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
    git_root: Path,
) -> str:
    today = datetime.now().strftime("%Y-%m-%d")

    def file_list(files, fallback):
        if not files:
            return f"- {fallback}\n"
        return "".join(f"- `{_clean_filename(f)}`: [description]\n" for f in files)

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

    return f"""

# Changelog — {today} (Vault)

## Overview

| Field | Value |
|-------|-------|
| Date | {today} |
| Commit SHA | {commit_sha or "[fill in after commit]"} |
| Files Added | {len(changes["A"])} |
| Files Modified | {len(changes["M"])} |
| Files Archived | {len(changes["D"])} |
| Submodules Updated | {len(updated_subs)} |

## Submodules

### Updated in This Commit
{submodule_list(updated_subs, "No submodules updated in this commit")}
### Status Overview

{sub_table}

## Vault Changes

### Templates & Scaffolding
{file_list([f for f in changes["M"] + changes["A"] if "template" in f.lower() or "scaffold" in f.lower()], "No template changes")}
### Config & Scripts
{file_list([f for f in changes["M"] + changes["A"] if any(x in f.lower() for x in (".archivist", "script", "hook", ".gitmodules"))], "No config or script changes")}
### Other Files Modified
{file_list([f for f in changes["M"] if "template" not in f.lower() and "scaffold" not in f.lower()], "No other modifications")}
### New Files
{file_list([f for f in changes["A"] if "template" not in f.lower() and "scaffold" not in f.lower()], "No new files")}
### Removed / Archived
{file_list(changes["D"], "No files archived")}

## Summary

### Key Changes
[Summary of what changed and why]

### Decisions Made
[Important decisions and rationale]

### Next Steps
- [ ] [Next task]

---

*Changelog auto-generated by archivist changelog vault — fill in bracketed fields before committing.*
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    git_root = get_repo_root()

    template_path = _find_template(git_root)
    template_fm = extract_frontmatter(template_path.read_text())
    output_dir = template_path.parent

    scope_path = Path(args.path).resolve() if getattr(args, "path", None) else None

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
    body = _build_body(changes, args.commit_sha, git_root)
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

    print(f"  Project    : {_get_project_name(git_root)}")
    print(f"  Changes    : {num_added} added, {num_modified} modified, {num_archived} archived")
    updated_subs = _get_submodules_in_commit(args.commit_sha, git_root)
    print(f"  Submodules : {len(updated_subs)} updated in this commit")
    if args.commit_sha:
        print(f"  SHA        : {args.commit_sha}")
    else:
        print("  SHA        : (staged changes — run after your commit to lock it in)")