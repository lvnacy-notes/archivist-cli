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

PRE_COMMIT_HOOK = """\
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

# Check for a staged manifest or changelog
STAGED=$(git diff --cached --name-only)
HAS_MANIFEST=$(echo "$STAGED" | grep -E '.*-manifest\\.md$' || true)
HAS_CHANGELOG=$(echo "$STAGED" | grep -E 'CHANGELOG-[0-9]{4}-[0-9]{2}-[0-9]{2}\\.md$' || true)

if [ -n "$HAS_MANIFEST" ] || [ -n "$HAS_CHANGELOG" ]; then
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
        MANIFEST_FILE=$(ls -t "$GIT_ROOT"/*-manifest.md 2>/dev/null | head -1)
        if [ -n "$MANIFEST_FILE" ]; then
            git add "$MANIFEST_FILE"
            echo "  ✅ Manifest staged."
        fi
        ;;
    3)
        archivist changelog
        CHANGELOG_FILE=$(find "$GIT_ROOT" -name "CHANGELOG-*.md" -newer "$GIT_ROOT/.archivist" 2>/dev/null | head -1)
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

POST_COMMIT_HOOK = """\
#!/bin/sh
#
# archivist post-commit hook
# Always displays commit details. If .archivist is present, also backfills
# the commit SHA into any manifest or changelog included in this commit,
# then renames changelogs to include the short SHA in the filename.
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
# archivist SHA backfill (only runs in archivist-managed repos)
# ---------------------------------------------------------------------------
GIT_ROOT=$(git rev-parse --show-toplevel)

if [ ! -f "$GIT_ROOT/.archivist" ]; then
    exit 0
fi

# Find manifest or changelog files included in this commit
FILES=$(git diff-tree --no-commit-id -r --name-only HEAD)

BACKFILLED=0
for FILE in $FILES; do
    FULL_PATH="$GIT_ROOT/$FILE"

    # Only process manifest or changelog markdown files
    case "$FILE" in
        *-manifest.md|*/CHANGELOG-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md|CHANGELOG-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md) ;;
        *) continue ;;
    esac

    [ -f "$FULL_PATH" ] || continue

    # Flag if commit-sha field is empty, whitespace-only, a placeholder,
    # or anything that isn't a valid 7-character hex short SHA
    CURRENT_SHA_VALUE=$(grep -E '^commit-sha:' "$FULL_PATH" | sed 's/^commit-sha:[[:space:]]*//')
    if echo "$CURRENT_SHA_VALUE" | grep -qE '^[0-9a-f]{7,}$'; then
        continue  # already backfilled — skip
    fi

    # Backfill short SHA into frontmatter
    sed -i.bak "s/^commit-sha:[[:space:]]*.*/commit-sha: $COMMIT_SHORT/" "$FULL_PATH"

    # Backfill full SHA into body table
    sed -i.bak "s/| Commit SHA | \\[fill in after commit\\] |/| Commit SHA | $COMMIT_SHA |/" "$FULL_PATH"

    # Clean up sed backup files (macOS sed -i requires an extension)
    rm -f "${FULL_PATH}.bak"

    BACKFILLED=$((BACKFILLED + 1))
    BASENAME=$(basename "$FILE")
    echo "   📋 archivist: SHA backfilled in $BASENAME"

    # Rename changelogs to append short SHA — manifests keep their names
    case "$FILE" in
        */CHANGELOG-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md|CHANGELOG-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md)
            DIR=$(dirname "$FULL_PATH")
            STEM=$(basename "$FILE" .md)
            NEW_PATH="$DIR/${STEM}-${COMMIT_SHORT}.md"
            mv "$FULL_PATH" "$NEW_PATH"
            echo "   📝 Renamed: $BASENAME → $(basename $NEW_PATH)"
            ;;
    esac
done

if [ "$BACKFILLED" -gt 0 ]; then
    echo "   ✏️  Updated file(s) left unstaged — commit when ready."
    echo ""
fi

exit 0
"""


# ---------------------------------------------------------------------------
# Install logic
# ---------------------------------------------------------------------------

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
        local_hooks = git_root / ".git" / "hooks"
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
    if not dry_run:
        print("\n  Hooks synced to current repo.")