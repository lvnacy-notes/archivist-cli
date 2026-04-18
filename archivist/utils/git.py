# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------

import logging
import re
import subprocess
import sys

from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared type
# ---------------------------------------------------------------------------

class GitChanges(TypedDict):
    """
    The canonical shape of a git changes dict.

    M, A, D are flat lists of file paths. R is a list of (old, new) pairs
    because that's what a rename actually is — two paths, not one. Typing
    R as list[str] and then pretending the strings are secretly tuples is
    the kind of shit that causes type errors at 11pm.
    """
    M: list[str]
    A: list[str]
    D: list[str]
    R: list[tuple[str, str]]

class SubmoduleInfo(TypedDict):
    has_uncommitted: bool
    has_unpushed: bool
    current_sha: str


# -----------------------------------------------------------------------------
# Git change utilities
# -----------------------------------------------------------------------------


def ensure_staged(
    path: Path | None,
    git_root: Path,
    extra_paths: list[Path] | None = None,
) -> None:
    """
    Ensure files are staged before generating a document.

    If path is given:
      - Always run `git add <path>` (idempotent — picks up the changelog
        itself plus any other in-scope changes on re-runs).
      - Also stage any extra_paths that exist (e.g. README, .github/).

    If path is None:
      - Check if anything is staged in the repo at all.
      - If nothing is staged, exit with a clear error — the user is
        responsible for staging when no scope is provided.
      - extra_paths are not auto-staged in this case; the user owns staging.

    Prints a note when it stages files automatically.
    """
    try:
        if path is not None:
            subprocess.run(["git", "add", str(path)], check=True, cwd=git_root)
            print(f"  📥 Staged: {path}")
            for ep in (extra_paths or []):
                if ep.exists():
                    subprocess.run(["git", "add", str(ep)], check=True, cwd=git_root)
                    rel = ep.relative_to(git_root) if ep.is_absolute() else ep
                    print(f"  📥 Staged: {rel}")
        else:
            result = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                capture_output=True, text=True, check=True, cwd=git_root,
            )
            if not result.stdout.strip():
                print(
                    "❌  Nothing is staged. Stage your changes before running archivist changelog.",
                    file=sys.stderr,
                )
                sys.exit(1)
            staged_files = result.stdout.strip().splitlines()
            print(f"  ✔  Staging check passed — {len(staged_files)} file(s) staged")

    except subprocess.CalledProcessError as e:
        logger.error(f"❌  Git error while staging files: {e}")
        sys.exit(1)


def get_file_from_git(filepath: str, git_root: Path, ref: str = "HEAD") -> str | None:
    """
    Retrieve the content of a file from a specific git ref using `git show <ref>:<path>`.
    
    Args:
        filepath: Path to the file as it appears in the repo (relative or absolute)
        git_root: Root of the git repository
        ref: Git reference (default: "HEAD")
    
    Returns:
        File content as a string, or None if the file can't be retrieved or 
        git command fails (e.g., file doesn't exist at that ref).
    """
    try:
        raw = subprocess.check_output(
            ["git", "show", f"{ref}:{filepath}"],
            stderr = subprocess.PIPE,
            cwd = git_root,
        )
        return raw.decode("utf-8", errors = "replace")
    except subprocess.CalledProcessError:
        return None


def get_git_changes(
    commit_sha: str | None,
    path: Path | None = None,
    extra_paths: list[Path] | None = None,
    git_root: Path | None = None,
) -> GitChanges:
    """
    Get staged or committed file changes from git.
    
    Args:
        commit_sha: Commit SHA for historical diff, or None for staged changes
        path: Scope the diff to a specific path (file or directory)
        extra_paths: Additional paths to always include (only when path is given)
        git_root: Git repo root. If provided with path, validates path is inside repo
                 and converts to relative (manifest.py use case)
    
    Returns:
        Dict with keys "M", "A", "D", "R" mapping to lists of changed files
        (paths relative to repo root when git_root is provided)
    """
    # Validate and convert path to relative if git_root is provided
    scope_path = path
    if git_root is not None and path is not None:
        try:
            scope_path = path.relative_to(git_root)
        except ValueError:
            logger.error(
                f"Error: Path '{path}' is not inside the git repo at '{git_root}'."
            )
            sys.exit(1)
    
    # Build a multi-pathspec: primary scope + any always-include extras.
    # extra_paths are only appended when a scope_path is active — with no
    # scope, the diff must be unconstrained (full staged index).
    all_paths: list[str] = []
    if scope_path is not None:
        all_paths.append(str(scope_path))
        for ep in (extra_paths or []):
            if ep.exists():
                all_paths.append(str(ep))
    pathspec = (["--"] + all_paths) if all_paths else []

    if commit_sha:
        cmd = ["git", "-c", "core.quotepath=false", "diff-tree",
               "--name-status", "-M", "-r", commit_sha] + pathspec
    else:
        cmd = ["git", "-c", "core.quotepath=false", "diff-index",
               "--cached", "--name-status", "-M", "HEAD"] + pathspec

    try:
        output = subprocess.check_output(
            cmd,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace"
        )
    except subprocess.CalledProcessError as e:
        logger.error(f"Error running git command: {e}")
        sys.exit(1)

    modified: list[str] = []
    added: list[str] = []
    deleted: list[str] = []
    renamed: list[tuple[str, str]] = []
    
    for line in output.strip().splitlines():
        if not line:
            continue
        parts = line.split("\t")
        
        match parts[0].strip()[0]:
            case "R" if len(parts) == 3:
                renamed.append((parts[1].strip(), parts[2].strip()))
            case "M":
                modified.append(parts[-1].strip())
            case "A":
                added.append(parts[-1].strip())
            case "D":
                deleted.append(parts[-1].strip())
            case _:
                logger.warning(f"Unrecognized git status code in line: {line}")

    return GitChanges(
        M = modified,
        A = added,
        D = deleted,
        R = renamed
    )


def get_project_name(git_root: Path) -> str:
    return git_root.name.lower().replace("'", "").replace(" ", "-")


def get_repo_root() -> Path:
    """
    Return the root of the current git repo or submodule.
    Exits with a clear message if not inside a git repo.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        )
        return Path(result.stdout.strip())
    except subprocess.CalledProcessError:
        logger.error("❌  Not inside a git repo. Are you in the right directory?")
        sys.exit(1)


def get_submodule_status(git_root: Path) -> dict[str, SubmoduleInfo]:
    """
    Query the status of all registered submodules.
    
    For each submodule, returns a dict with:
      - has_uncommitted: bool — whether the submodule has uncommitted changes
      - has_unpushed: bool — whether the submodule has unpushed commits
      - current_sha: str — the short SHA of the submodule's current HEAD
    
    Submodules that are not initialized or cause git errors are still included
    in the result with default values (all False/"").
    
    Args:
        git_root: Root of the git repository
    
    Returns:
        Dict[submodule_path: str, status: SubmoduleInfo]
    """
    status: dict[str, SubmoduleInfo] = {}
    
    # Get all registered submodules
    submodules: list[str] = []
    try:
        output = subprocess.check_output(
            ["git", "submodule", "status"],
            stderr=subprocess.PIPE, text=True, cwd=git_root,
        )
        for line in output.strip().splitlines():
            if not line:
                continue
            # Format: [+- U]?<sha> <path> [(<description>)]
            # Match: optional status char, sha, one+ spaces, then everything up to
            # either end-of-line or the description paren. Fuck around with spaces.
            match = re.match(
                r"^[ +\-U]?([a-f0-9]+)\s+(.+?)(?:\s+\(.+\))?$",
                line.strip()
            )
            if match:
                path = match.group(2).strip()
                submodules.append(path)
    except subprocess.CalledProcessError:
        return {}
    
    # Query status for each submodule
    for sub in submodules:
        sub_path = git_root / sub
        info = SubmoduleInfo(
            has_uncommitted = False,
            has_unpushed = False,
            current_sha = ""
        )
        try:
            # Get current short SHA
            info["current_sha"] = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                stderr=subprocess.PIPE, text=True, cwd=sub_path,
            ).strip()
            # Check for uncommitted changes
            info["has_uncommitted"] = bool(subprocess.check_output(
                ["git", "status", "--porcelain"],
                stderr=subprocess.PIPE, text=True, cwd=sub_path,
            ).strip())
            # Check for unpushed commits
            info["has_unpushed"] = bool(subprocess.check_output(
                ["git", "log", "@{u}..", "--oneline"],
                stderr=subprocess.PIPE, text=True, cwd=sub_path,
            ).strip())
        except subprocess.CalledProcessError:
            pass  # submodule may not be initialized — leave defaults
        status[sub] = info
    
    return status


def _get_out_of_scope_unstaged(scope_path: Path, git_root: Path) -> list[str]:
    """
    Return unstaged (modified or untracked) files that fall outside scope_path.
    """
    try:
        modified = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True, text=True, check=True, cwd=git_root,
        ).stdout.strip().splitlines()

        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True, text=True, check=True, cwd=git_root,
        ).stdout.strip().splitlines()

        rel = scope_path.relative_to(git_root) if scope_path.is_absolute() else scope_path
        scope_prefix = str(rel)

        return [f for f in modified + untracked if not f.startswith(scope_prefix)]

    except subprocess.CalledProcessError:
        return []


def prompt_out_of_scope_changes(scope_path: Path, git_root: Path) -> None:
    """
    Check for unstaged changes outside scope_path and prompt the user to stage
    them alongside the scoped changes. A 'y' stages them; anything else skips
    and continues.

    Only relevant for changelog subcommands where a --path scope is active.
    Do NOT call this from manifest — it is intentionally scope-locked.
    """
    out_of_scope = _get_out_of_scope_unstaged(scope_path, git_root)
    if not out_of_scope:
        return

    print(f"\n  ⚠️  There are unstaged changes outside the scope ({scope_path}):")
    for f in out_of_scope:
        print(f"       {f}")
    answer = input("\n  Stage these too? [y/N] ").strip().lower()
    if answer == "y":
        for f in out_of_scope:
            subprocess.run(["git", "add", f], check=True, cwd=git_root)
        print("  📥 Staged out-of-scope changes.")
    else:
        print("  Skipping out-of-scope changes.")