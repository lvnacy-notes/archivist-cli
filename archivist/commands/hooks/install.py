"""
archivist hooks install / sync

Writes archivist-aware pre-commit and post-commit hook scripts into
~/.git-templates/hooks/ so they are automatically copied into any
new git clone or `git init`.

For existing repos, run `archivist hooks sync` to copy the hooks into
the current repo's .git/hooks/ directly.
"""

import argparse
import os
import stat
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Hook script templates
# ---------------------------------------------------------------------------

PRE_COMMIT_HOOK = r"""\
#!/usr/bin/env bash
# archivist pre-commit hook
# Checks for a staged manifest or changelog; prompts to generate one if absent.

# Exit immediately if archivist is not installed
if ! command -v archivist &>/dev/null; then
    exit 0
fi

# Exit if no .archivist config — this repo is not managed by archivist
if [ ! -f "$(git rev-parse --show-toplevel)/.archivist" ]; then
    exit 0
fi

GIT_ROOT=$(git rev-parse --show-toplevel)

# Check for a staged manifest or changelog.
# Query git directly rather than storing output in a variable and echoing it
# back through grep — that approach is a fragile piece of shit that breaks
# depending on shell, locale, and which way the wind is blowing.
# A staged manifest is sufficient — no changelog needed for an edition commit.
if git diff --cached --name-only | grep -qE '.*-manifest\.md$'; then
    exit 0
fi

# Only an UNSEALED changelog satisfies this check. Sealed changelogs carry a
# short SHA suffix (CHANGELOG-YYYY-MM-DD-{ sha }.md) because they are closed
# records that document a past commit. They have nothing to do with the
# changes currently staged. If only a sealed changelog is in the diff, the
# hook correctly falls through to the prompt — that is intentional behaviour,
# not a bug. Do not broaden this pattern to match sealed filenames.
if git diff --cached --name-only | grep -qE 'CHANGELOG-[0-9]{4}-[0-9]{2}-[0-9]{2}\.md$'; then
    exit 0
fi

# Nothing found — prompt
echo ""
echo "  📋 archivist: No manifest or changelog found in staged files."
echo ""
echo "  Generate one now?"
echo "    1. no — proceed with commit as-is"
echo "    2. manifest — generate an edition manifest"
echo "    3. changelog — generate a changelog"
echo "    4. stage existing — add an existing file to staging"
echo "    5. cancel — abort the commit"
echo ""
printf "  Enter number [1]: "
read -r CHOICE </dev/tty

case "$CHOICE" in
    2)
        printf "  Edition directory path: "
        read -r EDITION_DIR </dev/tty
        archivist manifest "$EDITION_DIR"
        MANIFEST_FILE=$(find "$GIT_ROOT" -name '*-manifest.md' -not -path '*/.git/*' -newer "$GIT_ROOT/.archivist" 2>/dev/null | head -1)
        if [ -n "$MANIFEST_FILE" ]; then
            git add "$MANIFEST_FILE"
            echo "  ✅ Manifest staged."
        fi
        ;;
    3)
        archivist changelog
        CHANGELOG_FILE=$(find "$GIT_ROOT/ARCHIVE" -name "CHANGELOG-*.md" -newer "$GIT_ROOT/.archivist" 2>/dev/null | head -1)
        if [ -n "$CHANGELOG_FILE" ]; then
            git add "$CHANGELOG_FILE"
            echo "  ✅ Changelog staged."
        fi
        ;;
    4)
        printf "  Path to file: "
        read -r EXISTING_FILE </dev/tty
        if [ -f "$EXISTING_FILE" ]; then
            git add "$EXISTING_FILE"
            echo "  ✅ Staged: $EXISTING_FILE"
        else
            echo "  ❌ File not found: $EXISTING_FILE"
            echo "  Proceeding without staging."
        fi
        ;;
    5)
        echo "  Commit cancelled."
        exit 1
        ;;
    *)
        echo "  Proceeding without manifest or changelog."
        ;;
esac

exit 0
"""

POST_COMMIT_HOOK = r"""\
#!/bin/sh
#
# archivist post-commit hook
# Always displays commit details. If .archivist is present, backfills
# manifests and delegates changelog sealing to archivist changelog seal.
#

# ---------------------------------------------------------------------------
# Commit details (runs for every repo, always)
# ---------------------------------------------------------------------------
COMMIT_SHA=$(git rev-parse HEAD)
COMMIT_SHORT=$(git rev-parse --short HEAD)
COMMIT_MESSAGE=$(git log -1 --pretty=%B | head -1)
CURRENT_BRANCH=$(git branch --show-current)

echo ""
echo "🚀 Commit Details:"
echo "   ✅ SHA:     $COMMIT_SHA"
echo "   📝 Short:   $COMMIT_SHORT"
echo "   💬 Message: $COMMIT_MESSAGE"
echo "   🌿 Branch:  $CURRENT_BRANCH"
echo ""
echo "📋 For PR creation, use:"
echo "   \\"Create PR for commit $COMMIT_SHORT from $CURRENT_BRANCH to main\\""
echo "   or"
echo "   \\"Create PR from $CURRENT_BRANCH to main\\""
echo ""

# ---------------------------------------------------------------------------
# archivist post-commit work (only runs in archivist-managed repos)
# ---------------------------------------------------------------------------
GIT_ROOT=$(git rev-parse --show-toplevel)

if [ ! -f "$GIT_ROOT/.archivist" ]; then
    exit 0
fi

# ---------------------------------------------------------------------------
# Manifest backfill — stays in bash, manifests don't rename or touch the DB
# ---------------------------------------------------------------------------
FILES=$(git diff-tree --no-commit-id -r --name-only HEAD)

for FILE in $FILES; do
    case "$FILE" in
        *-manifest.md) ;;
        *) continue ;;
    esac

    FULL_PATH="$GIT_ROOT/$FILE"
    [ -f "$FULL_PATH" ] || continue

    CURRENT_SHA_VALUE=$(grep -E '^commit-sha:' "$FULL_PATH" | sed 's/^commit-sha:[[:space:]]*//')
    if echo "$CURRENT_SHA_VALUE" | grep -qE '^[0-9a-f]{7,}$'; then
        continue  # already backfilled
    fi

    sed -i.bak "s/^commit-sha:[[:space:]]*.*/commit-sha: $COMMIT_SHORT/" "$FULL_PATH"
    sed -i.bak "s/| Commit SHA | \\[fill in after commit\\] |/| Commit SHA | $COMMIT_SHA |/" "$FULL_PATH"
    rm -f "${FULL_PATH}.bak"

    echo "   📋 archivist: SHA backfilled in $(basename $FILE)"
done

# ---------------------------------------------------------------------------
# Changelog seal — delegates to Python for backfill, rename, and DB update
# ---------------------------------------------------------------------------
if command -v archivist &>/dev/null; then
    archivist changelog seal "$COMMIT_SHA"
else
    echo "   ⚠️  archivist not found on PATH — changelog seal skipped"
    echo "        Run: archivist changelog seal $COMMIT_SHA"
fi

exit 0
"""


# ---------------------------------------------------------------------------
# Install logic
# ---------------------------------------------------------------------------

def _resolve_hooks_dir(repo_path: Path) -> Path:
    """
    Return the hooks directory for any repo or submodule worktree.

    Regular repos:    repo_path/.git/hooks/
    Submodules:       repo_path/.git is a file containing the real gitdir path,
                      e.g. `gitdir: ../.git/modules/submodule-name`
    """
    git_entry = repo_path / ".git"
    if git_entry.is_dir():
        return git_entry / "hooks"

    if git_entry.is_file():
        # Read the gitdir pointer and resolve relative to the worktree
        gitdir_line = git_entry.read_text(encoding="utf-8").strip()
        if gitdir_line.startswith("gitdir:"):
            gitdir = gitdir_line[len("gitdir:"):].strip()
            resolved = (repo_path / gitdir).resolve()
            return resolved / "hooks"

    raise RuntimeError(f"Cannot resolve git hooks directory for: {repo_path}")


def _get_submodule_paths(git_root: Path) -> list[Path]:
    """
    Return resolved paths for all initialized submodules in git_root.
    """
    import subprocess
    try:
        output = subprocess.check_output(
            ["git", "submodule", "status"],
            stderr=subprocess.PIPE, text=True, cwd=git_root,
        )
        paths = []
        for line in output.strip().splitlines():
            if not line:
                continue
            # Format: [+- ]<sha> <path> [(<description>)]
            parts = line.strip().split()
            if len(parts) >= 2:
                sub_path = (git_root / parts[1]).resolve()
                if sub_path.exists():
                    paths.append(sub_path)
        return paths
    except subprocess.CalledProcessError:
        return []


def _write_hook(hooks_dir: Path, name: str, content: str, dry_run: bool) -> None:
    hook_path = hooks_dir / name

    if dry_run:
        print(f"  [dry-run] Would write: {hook_path}")
        return

    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_path.write_text(content, encoding="utf-8")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"  ✅ Written: {hook_path}")


def install_hooks(git_root: Path | None = None, dry_run: bool = False) -> None:
    """
    Write hook scripts to ~/.git-templates/hooks/ (global install).
    If git_root is provided, also writes directly into that repo's .git/hooks/.
    """
    # --- Global templates ---
    global_hooks = Path.home() / ".git-templates" / "hooks"
    print(f"\n  Installing global hooks → {global_hooks}")
    _write_hook(global_hooks, "pre-commit", PRE_COMMIT_HOOK, dry_run)
    _write_hook(global_hooks, "post-commit", POST_COMMIT_HOOK, dry_run)

    if not dry_run:
        # Ensure git knows about the templates dir
        result = os.popen("git config --global init.templateDir").read().strip()
        if result != str(Path.home() / ".git-templates"):
            os.system(f'git config --global init.templateDir "{Path.home()}/.git-templates"')
            print(f"  ✅ Set git init.templateDir → ~/.git-templates")

    # --- Local repo (sync) ---
    if git_root is not None:
        local_hooks = _resolve_hooks_dir(git_root)
        print(f"\n  Syncing to local repo → {local_hooks}")
        _write_hook(local_hooks, "pre-commit", PRE_COMMIT_HOOK, dry_run)
        _write_hook(local_hooks, "post-commit", POST_COMMIT_HOOK, dry_run)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def run_install(args: argparse.Namespace) -> None:
    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        print("=== DRY RUN — no files written ===")
    install_hooks(dry_run=dry_run)
    if not dry_run:
        print("\n  Hooks installed globally. New clones will pick them up automatically.")
        print("  Run `archivist hooks sync` inside an existing repo to apply them there.")


def run_sync(args: argparse.Namespace) -> None:
    from archivist.utils import get_repo_root
    git_root = get_repo_root()
    dry_run = getattr(args, "dry_run", False)
    if dry_run:
        print("=== DRY RUN — no files written ===")

    install_hooks(git_root=git_root, dry_run=dry_run)

    # Detect submodules and offer to sync into each one
    submodules = _get_submodule_paths(git_root)
    if submodules:
        print(f"\n  ⚠️  {len(submodules)} submodule(s) detected:")
        for sub in submodules:
            print(f"       {sub.relative_to(git_root)}")
        answer = input("\n  Sync hooks into all submodules too? [y/N] ").strip().lower()
        if answer == "y":
            for sub in submodules:
                try:
                    hooks_dir = _resolve_hooks_dir(sub)
                    rel = sub.relative_to(git_root)
                    print(f"\n  Syncing → {rel}/.git/hooks/")
                    _write_hook(hooks_dir, "pre-commit", PRE_COMMIT_HOOK, dry_run)
                    _write_hook(hooks_dir, "post-commit", POST_COMMIT_HOOK, dry_run)
                except RuntimeError as e:
                    print(f"  ⚠️  Skipping {sub.name}: {e}", file=sys.stderr)
            if not dry_run:
                print("\n  Hooks synced to repo and all submodules.")
        else:
            print("  Skipping submodules.")
            if not dry_run:
                print("\n  Hooks synced to current repo only.")
    else:
        if not dry_run:
            print("\n  Hooks synced to current repo.")