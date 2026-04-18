# ---------------------------------------------------------------------------
# Archive DB
# ---------------------------------------------------------------------------

import sqlite3
from datetime import datetime
from pathlib import Path


def get_db_path(git_root: Path) -> Path:
    return git_root / "ARCHIVE" / "archive.db"


def init_db(db_path: Path) -> sqlite3.Connection:
    """
    Open (or create) the archive DB and ensure the schema exists.
    Returns an open connection.

    Tables:
      edition_shas — tracks edition commit SHAs from registration through
                     inclusion in a changelog. included_in holds a changelog
                     UUID until the changelog is sealed, at which point
                     seal_changelog_in_db() transitions it to the commit SHA.

      changelogs   — registry of all generated changelogs, keyed by UUID.
                     Populated at seal time. Designed as the foundation for
                     the centralized cross-project DB described in ROADMAP.md.
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS changelogs (
            uuid        TEXT PRIMARY KEY,
            commit_sha  TEXT,
            log_scope   TEXT,
            created_at  TEXT,
            sealed_at   TEXT,
            file_path   TEXT
        )
    """)
    conn.commit()
    return conn


def seal_changelog_in_db(git_root: Path, changelog_uuid: str, commit_sha: str) -> None:
    """
    Mark a changelog as sealed in the archive DB.

    Does two things atomically:
      - changelogs table: upserts the entry with commit SHA and seal timestamp.
      - edition_shas table: transitions any SHAs with included_in = UUID to
        included_in = commit_sha, completing the handoff.

    If no archive DB exists (non-publication repos that have never run
    manifest or changelog publication), this is a no-op. The DB is not
    created here — only updated if it already exists.
    """
    db_path = get_db_path(git_root)
    if not db_path.exists():
        return
    conn = init_db(db_path)
    now = datetime.now().strftime("%Y-%m-%d")
    try:
        conn.execute(
            """INSERT OR IGNORE INTO changelogs (uuid, created_at) VALUES (?, ?)""",
            (changelog_uuid, now),
        )
        conn.execute(
            """UPDATE changelogs SET commit_sha = ?, sealed_at = ? WHERE uuid = ?""",
            (commit_sha, now, changelog_uuid),
        )
        conn.execute(
            """UPDATE edition_shas SET included_in = ? WHERE included_in = ?""",
            (commit_sha, changelog_uuid),
        )
        conn.commit()
    finally:
        conn.close()
