"""
Shared utilities for archivist commands.

Anything used by more than one command lives here:
  - Git root detection
  - Frontmatter parsing
  - File class inspection
  - Archive DB helpers
"""

import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# Rename helpers (shared by all changelog subcommands)
# ---------------------------------------------------------------------------

def clean_filename(filepath: str) -> str:
    """
    Return just the filename from a path, stripping trailing non-alphanumeric
    characters from the stem (handles Obsidian's auto-suffixed conflict copies).
    """
    p = Path(filepath)
    stem = re.sub(r'[^a-zA-Z0-9]+$', '', p.stem)
    return stem + p.suffix

def detect_dir_renames(renames: list[tuple[str, str]]) -> dict[str, str]:
    """
    From file-level rename pairs, infer directory-level renames.
    Returns {old_dir_prefix: new_dir_prefix}.
    """
    dir_renames = {}
    for old, new in renames:
        old_parent = str(Path(old).parent)
        new_parent = str(Path(new).parent)
        if old_parent != new_parent:
            dir_renames[old_parent] = new_parent
    return dir_renames

def infer_undetected_renames(changes: dict) -> list[tuple[str, str]]:
    """
    Find D/A pairs with matching filenames that git's -M didn't detect as renames.
    Returns list of (old, new) tuples to be merged into changes["R"].

    Only pairs files where exactly one candidate exists — ambiguous matches
    (same filename added in multiple locations) are left alone.
    """
    already_paired_old = {old for old, _ in changes["R"]}
    already_paired_new = {new for _, new in changes["R"]}

    unpaired_deleted = [f for f in changes["D"] if f not in already_paired_old]
    unpaired_added   = [f for f in changes["A"] if f not in already_paired_new]

    added_by_name: dict[str, list[str]] = {}
    for f in unpaired_added:
        added_by_name.setdefault(Path(f).name, []).append(f)

    return [
        (old, added_by_name[Path(old).name][0])
        for old in unpaired_deleted
        if len(added_by_name.get(Path(old).name, [])) == 1
    ]

def reassign_deletions(
    deleted: list[str],
    dir_renames: dict[str, str],
) -> tuple[list[str], list[tuple[str, str]]]:
    """
    Separate true deletions from files that appear deleted only because
    their parent directory was renamed.

    Returns (true_deleted, dir_renamed_files) where dir_renamed_files is
    a list of (old, new) path tuples ready to be merged into changes["R"].
    """
    true_deleted = []
    dir_renamed_files = []
    for f in deleted:
        parent = str(Path(f).parent)
        if parent in dir_renames:
            new_path = str(Path(dir_renames[parent]) / Path(f).name)
            dir_renamed_files.append((f, new_path))
        else:
            true_deleted.append(f)
    return true_deleted, dir_renamed_files

def rename_suspicion(old_filepath: str, new_filepath: str) -> str:
    """
    Return a warning string if a rename looks suspicious, else empty string.
    Two independent checks, either or both may fire:

    - Cross-directory: old and new parent directories differ
    - Name mismatch: neither stem is a substring of the other (case-insensitive)

    These are advisory only — the changelog is a draft and the user
    should verify flagged renames before committing.
    """
    old = Path(old_filepath)
    new = Path(new_filepath)

    reasons = []

    if old.parent != new.parent:
        reasons.append("cross-directory")

    old_stem = re.sub(r'[^a-zA-Z0-9]+$', '', old.stem).lower()
    new_stem = re.sub(r'[^a-zA-Z0-9]+$', '', new.stem).lower()
    if old_stem not in new_stem and new_stem not in old_stem:
        reasons.append("name mismatch")

    if not reasons:
        return ""
    return f" ⚠️ *rename unverified ({', '.join(reasons)}) — double-check*"

# Known Apparatus module types
APPARATUS_MODULE_TYPES = ["story", "publication", "library", "vault", "general"]

# Changelog subcommand for each module type
MODULE_CHANGELOG_COMMAND = {
    "general":     "general",
    "library":     "library",
    "publication": "publication",
    "story":       "story",
    "vault":       "vault",
}


# ---------------------------------------------------------------------------
# .archivist config
# ---------------------------------------------------------------------------

def get_archivist_config_path(git_root: Path) -> Path:
    return git_root / ".archivist"


def read_archivist_config(git_root: Path) -> dict | None:
    """
    Read and parse the .archivist config file at the repo root.
    Returns the config dict, or None if the file does not exist.
    """
    path = get_archivist_config_path(git_root)
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError as e:
        print(f"❌  Could not parse .archivist config: {e}", file=sys.stderr)
        return {}


def write_archivist_config(git_root: Path, config: dict) -> None:
    """Write the .archivist config file at the repo root."""
    path = get_archivist_config_path(git_root)
    lines = ["# archivist project configuration"]
    for key, value in config.items():
        lines.append(f"{key}: {value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def get_module_type(git_root: Path) -> str | None:
    """
    Return the module-type from .archivist, or None if not configured.
    """
    config = read_archivist_config(git_root)
    if config is None:
        return None
    return config.get("module-type")


def is_apparatus_project(git_root: Path) -> bool:
    """Return True if .archivist declares this as an Apparatus project."""
    config = read_archivist_config(git_root)
    if config is None:
        return False
    return bool(config.get("apparatus", False))


# Matches a YAML frontmatter block at the top of a file.
# Permissive about trailing whitespace on the delimiter lines.
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------

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
        print("❌  Not inside a git repo. Are you in the right directory?")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

def extract_frontmatter(content: str) -> dict:
    """
    Parse the YAML frontmatter block from a markdown string.
    Returns an empty dict if the block is absent or unparseable.
    """
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}
    try:
        data = yaml.safe_load(match.group(1))
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError:
        return {}


def get_file_frontmatter(filepath: Path | str) -> dict | None:
    """
    Parse and return the frontmatter dict from a markdown file.
    Accepts either a Path or a string filepath.
    Returns None if the file is not markdown, has no frontmatter,
    or cannot be read.
    """
    filepath = Path(filepath)
    if filepath.suffix.lower() not in (".md", ".markdown"):
        return None
    try:
        content = filepath.read_text(encoding="utf-8", errors="ignore")
        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return None
        data = yaml.safe_load(match.group(1))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def get_file_class(filepath: Path) -> str | None:
    """
    Return the 'class' frontmatter field as a lowercase string,
    or None if absent or unreadable.
    """
    fm = get_file_frontmatter(filepath)
    if fm is None:
        return None
    val = fm.get("class")
    return str(val).strip().lower() if val is not None else None


# ---------------------------------------------------------------------------
# Archive DB
# ---------------------------------------------------------------------------


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
        print(f"❌  Git error while staging files: {e}", file=sys.stderr)
        sys.exit(1)


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


def get_db_path(git_root: Path) -> Path:
    return git_root / "ARCHIVE" / "archive.db"


def init_db(db_path: Path) -> sqlite3.Connection:
    """
    Open (or create) the archive DB and ensure the schema exists.
    Returns an open connection.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS edition_shas (
            sha             TEXT PRIMARY KEY,
            commit_message  TEXT,
            manifest_file   TEXT,
            discovered_at   TEXT,
            included_in     TEXT
        )
    """)
    conn.commit()
    return conn

# ---------------------------------------------------------------------------
# Changelog / manifest update helpers
# ---------------------------------------------------------------------------

ARCHIVIST_AUTO_END = "<!-- archivist:auto-end -->"


def find_active_changelog(output_dir: Path) -> Path | None:
    """Return the most recent unsealed (pre-commit) changelog in output_dir, or None.

    Unsealed changelogs match CHANGELOG-YYYY-MM-DD.md exactly — a date and
    nothing else before the extension. Post-commit sealed changelogs carry a
    SHA suffix (CHANGELOG-YYYY-MM-DD-{sha}.md) and are intentionally excluded.

    Returns the lexicographically greatest match, which is always the most
    recent date. No cap on how far back it will look — a working session that
    spans midnight rolls forward naturally rather than abandoning the existing
    file.
    """
    _UNSEALED_RE = re.compile(r"^CHANGELOG-\d{4}-\d{2}-\d{2}\.md$")
    candidates = [
        p for p in output_dir.iterdir()
        if p.is_file() and _UNSEALED_RE.match(p.name)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.name)


def find_todays_manifest(parent_dir: Path, edition_name: str) -> Path | None:
    """Return path to today's manifest for this edition if one exists, else None."""
    candidate = parent_dir / f"{edition_name}-manifest.md"
    return candidate if candidate.exists() else None

def extract_descriptions(existing_content: str) -> dict:
    """
    Parse an existing changelog or manifest and return a dict mapping
    full relative filepath → description for any entry where the user
    has replaced [description] with actual text.

    Supports two formats:

        Single-line:
            - `path/to/file.md`: some description the user wrote

        Sub-bullet list:
            - `path/to/file.md`:
              - did one thing
              - did another thing

    Single-line entries are stored as str; sub-bullet entries as list[str].
    Entries still showing [description] or with no content are skipped.
    """
    descriptions = {}
    lines = existing_content.splitlines()

    for i, line in enumerate(lines):
        m = re.match(r"^- `([^`]+)`:([ \t]*)(.*)$", line)
        if not m:
            continue

        filepath = m.group(1).strip()
        inline   = m.group(3).strip()

        if inline:
            if inline != "[description]":
                descriptions[filepath] = inline
        else:
            bullets = []
            j = i + 1
            while j < len(lines):
                sub = re.match(r"^  - (.+)$", lines[j])
                if sub:
                    bullets.append(sub.group(1).strip())
                    j += 1
                else:
                    break
            if bullets:
                descriptions[filepath] = bullets

    return descriptions


def extract_user_content(existing_content: str) -> str | None:
    """
    Return everything after the archivist:auto-end sentinel, or None if
    the sentinel is not present (e.g. files generated before this feature).
    """
    if ARCHIVIST_AUTO_END not in existing_content:
        return None
    return existing_content.split(ARCHIVIST_AUTO_END, 1)[1]


# ---------------------------------------------------------------------------
# Output helpers (shared by all changelog and manifest subcommands)
# ---------------------------------------------------------------------------

def report_changes(changes: dict, modified: list, true_deleted: list) -> None:
    """
    Print a human-readable summary of the staged diff.
    Called by every changelog subcommand after _get_git_changes() resolves.
    """
    total = len(changes["A"]) + len(modified) + len(true_deleted)
    if total == 0:
        print("  ⚠️  No staged changes found in the diff — changelog will be empty")
    else:
        print(
            f"  📋 Diff resolved: "
            f"{len(changes['A'])} added  |  "
            f"{len(modified)} modified  |  "
            f"{len(true_deleted)} archived"
        )
    if changes["R"]:
        print(f"     ↳ {len(changes['R'])} rename(s) detected")


def write_changelog(output_path: Path, content: str, existing: bool) -> None:
    """
    Write changelog content to disk with before/after messaging and safe
    error handling.

    - Prints the target path before attempting the write so a failure can
      be located immediately.
    - Wraps the write in a try/except so an OSError doesn't produce a raw
      traceback; exits with a clear message instead.
    - Prints a ✓ confirmation with the correct verb (written vs updated)
      only after a successful write.

    Call this instead of output_path.write_text() in every changelog and
    manifest subcommand.
    """
    verb = "Updating" if existing else "Creating"
    print(f"  ✏️  {verb}: {output_path}")
    try:
        output_path.write_text(content, encoding="utf-8")
    except OSError as e:
        print(f"❌  Failed to write changelog: {e}", file=sys.stderr)
        sys.exit(1)
    verb_past = "updated" if existing else "written"
    print(f"✓ Changelog {verb_past}: {output_path}")