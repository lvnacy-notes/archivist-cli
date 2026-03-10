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

# Known Apparatus module types
APPARATUS_MODULE_TYPES = ["story", "publication", "library", "vault", "general"]

# Changelog subcommand for each module type
MODULE_CHANGELOG_COMMAND = {
    "story":       "story",
    "publication": "publication",
    "library":     "library",
    "vault":       "vault",
    "general":     "general",
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


def get_file_frontmatter(filepath: Path) -> dict | None:
    """
    Parse and return the frontmatter dict from a markdown file.
    Returns None if the file is not markdown, has no frontmatter,
    or cannot be read.
    """
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

def ensure_staged(path: Path | None, git_root: Path) -> None:
    """
    Ensure files are staged before generating a document.

    If path is given:
      - Check if any files at that path are staged.
      - If none are staged, run `git add <path>`.

    If path is None:
      - Check if anything is staged in the repo at all.
      - If nothing is staged, run `git add .` from the repo root.

    Prints a note when it stages files automatically.
    """
    if path is not None:
        rel = path.relative_to(git_root) if path.is_absolute() else path
        check_cmd = ["git", "diff", "--cached", "--name-only", "--", str(rel)]
    else:
        check_cmd = ["git", "diff", "--cached", "--name-only"]

    try:
        result = subprocess.run(
            check_cmd, capture_output=True, text=True, check=True, cwd=git_root
        )
        if result.stdout.strip():
            return  # files already staged — nothing to do

        if path is not None:
            subprocess.run(["git", "add", str(path)], check=True, cwd=git_root)
            print(f"  📥 Auto-staged: {path}")
        else:
            subprocess.run(["git", "add", "."], check=True, cwd=git_root)
            print("  📥 Nothing staged — auto-staged all changes (git add .)")

    except subprocess.CalledProcessError as e:
        print(f"❌  Git error while checking/staging files: {e}", file=sys.stderr)
        sys.exit(1)


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