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
from pathlib import Path
from typing import cast

from archivist.commands.changelog.changelog_base import ChangelogContext, run_changelog
from archivist.utils import (
    format_file_list,
    get_project_name,
    get_submodule_status,
    get_today,
    render_field,
    resolve_changelog_title,
)


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
        modules: list[str] = []
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
    """Return submodule paths updated in the given commit or staged changes."""
    cmd = (
        ["git", "diff-tree", "--name-only", "-r", commit_sha]
        if commit_sha
        else ["git", "diff-index", "--cached", "--name-only", "HEAD"]
    )
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.PIPE, text=True, cwd=git_root)
    except subprocess.CalledProcessError:
        return []
    all_submodules = set(_get_submodules(git_root))
    return [f for f in output.strip().splitlines() if f in all_submodules]


# ---------------------------------------------------------------------------
# Post-changes hook
# ---------------------------------------------------------------------------

def _analyse_submodules(ctx: ChangelogContext) -> None:
    """
    Collect submodule state and store it in ctx.data for use by the builders.
    Runs once so neither builder has to make its own subprocess calls.
    """
    ctx.data["updated_subs"] = _get_submodules_in_commit(ctx.args.commit_sha, ctx.git_root)
    ctx.data["sub_status"] = get_submodule_status(ctx.git_root)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _build_frontmatter(ctx: ChangelogContext) -> str:
    today = get_today()
    auto = {
        "class": "archive",
        "category": ["changelog"],
        "log-scope": "vault",
        "modified": today,
        "UUID": ctx.changelog_uuid,
        "commit-sha": ctx.args.commit_sha or "",
        "files-modified": len(ctx.modified),
        "files-created": len(ctx.changes["A"]),
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
    descriptions = ctx.descriptions or {}
    commit_sha = ctx.args.commit_sha
    updated_subs: list[str] = cast(list[str], ctx.data["updated_subs"])
    sub_status: dict[str, dict[str, bool | str]] = cast(dict[str, dict[str, bool | str]], ctx.data["sub_status"])

    def submodule_list(subs: list[str], fallback: str) -> str:
        if not subs:
            return f"- {fallback}\n"
        return "".join(f"- `{s}`\n" for s in subs)

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

    template_modified = [f for f in ctx.modified if "template" in f.lower() or "scaffold" in f.lower()]
    config_modified = [f for f in ctx.modified if any(x in f.lower() for x in (".archivist", "script", "hook", ".gitmodules"))]
    other_modified = [f for f in ctx.modified if "template" not in f.lower() and "scaffold" not in f.lower()]

    template_added = [f for f in ctx.changes["A"] if "template" in f.lower() or "scaffold" in f.lower()]
    config_added = [f for f in ctx.changes["A"] if any(x in f.lower() for x in (".archivist", "script", "hook", ".gitmodules"))]
    other_added = [f for f in ctx.changes["A"] if "template" not in f.lower() and "scaffold" not in f.lower()]

    user_block = ctx.user_content if ctx.user_content is not None else """
## Notes


---

*This changelog was automatically generated by Archivist CLI.*
*See [Archivist CLI](https://github.com/lvnacy-notes/archivist-cli) for more information.*

"""

    return f"""

{ resolve_changelog_title(ctx, today) }

## Overview

| Field | Value |
|-------|-------|
| Date | {today} |
| Commit SHA | {commit_sha or "[fill in after commit]"} |
| Files Added | {len(ctx.changes["A"])} |
| Files Modified | {len(ctx.modified)} |
| Files Archived | {len(ctx.true_deleted)} |
| Submodules Updated | {len(updated_subs)} |

## Submodules

### Updated in This Commit
{submodule_list(updated_subs, "No submodules updated in this commit")}
### Status Overview

{sub_table}

## Vault Changes

### Templates & Scaffolding
{format_file_list(template_modified + template_added, "No template changes", descriptions, ctx.renames)}
### Config & Scripts
{format_file_list(config_modified + config_added, "No config or script changes", descriptions, ctx.renames)}
### Other Files Modified
{format_file_list(other_modified, "No other modifications", descriptions, ctx.renames)}
### New Files
{format_file_list(other_added, "No new files", descriptions)}
### Removed / Archived
{format_file_list(ctx.true_deleted, "No files archived", descriptions)}

<!-- archivist:auto-end -->
{user_block}
"""


def _print_summary(ctx: ChangelogContext) -> None:
    updated_subs: list[str] = cast(list[str], ctx.data["updated_subs"])
    print(f"  Project    : {get_project_name(ctx.git_root)}")
    print(
        f"  Changes    : {len(ctx.changes['A'])} added, "
        f"{len(ctx.modified)} modified, {len(ctx.true_deleted)} archived"
    )
    print(f"  Submodules : {len(updated_subs)} updated in this commit")
    if ctx.args.commit_sha:
        print(f"  SHA        : {ctx.args.commit_sha}")
    else:
        print("  SHA        : (staged — backfilled by post-commit hook)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    run_changelog(
        args,
        module_type = "vault",
        build_frontmatter = _build_frontmatter,
        build_body = _build_body,
        post_changes = _analyse_submodules,
        print_summary = _print_summary,
    )