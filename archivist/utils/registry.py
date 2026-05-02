# ---------------------------------------------------------------------------
# Centralized Registry & Apparatus Database
# ---------------------------------------------------------------------------
#
# Two tiers of machine-level storage:
#
#   registry.db  (~/.archivist/registry.db)
#       Knows about every Apparatus, Vault, and Module on this machine.
#
#   [apparatus].db  (~/.archivist/[apparatus-name].db)
#       Apparatus-level catalog: works, authors, publications, changelogs.
#
# Cross-database foreign keys (module_id in work_libraries and changelogs)
# are soft references. SQLite does not enforce FKs across files. That is an
# application-layer responsibility. Don't screw it up.
#
# Public surface:
#   Path helpers       — get_archivist_home, get_registry_path,
#                        get_apparatus_db_path
#   Connection helpers — get_registry_connection, get_apparatus_db_connection
#   Schema init        — init_registry_db, init_apparatus_db
#   Registration       — register_apparatus, register_vault, register_module,
#                        get_or_create_apparatus, get_or_create_vault
#   Queries            — list_apparatuses, list_vaults, list_modules,
#                        get_module_by_path, get_module_id_by_path,
#                        is_module_registered
# ---------------------------------------------------------------------------

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_archivist_home() -> Path:
    """
    Return ~/.archivist/ — the one true home of all Archivist databases.

    Does NOT create it. That's init_registry_db()'s job.
    """
    return Path.home() / ".archivist"


def get_registry_path() -> Path:
    """
    Return the absolute path to registry.db.

    It does not live in any project. Stop looking in the repo root for it.
    """
    return get_archivist_home() / "registry.db"


def get_apparatus_db_path(apparatus_name: str) -> Path:
    """
    Return the absolute path to an apparatus-level database.

    Lowercased and hyphenated — ~/.archivist/My Fancy Apparatus.db is not
    something we are doing.
    """
    safe_name = apparatus_name.strip().lower().replace(" ", "-")
    return get_archivist_home() / f"{safe_name}.db"


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def get_registry_connection() -> sqlite3.Connection:
    """
    Open (or create) registry.db and return an open connection.

    The caller owns the connection. Close it when you're done.
    """
    return init_registry_db()


def get_apparatus_db_connection(apparatus_name: str) -> sqlite3.Connection:
    """
    Open (or create) an apparatus-level database and return an open connection.

    The caller owns the connection. Close it when you're done.
    """
    return init_apparatus_db(apparatus_name)


# ---------------------------------------------------------------------------
# Schema init
# ---------------------------------------------------------------------------

def init_registry_db(db_path: Path | None = None) -> sqlite3.Connection:
    """
    Create ~/.archivist/ and registry.db if they don't exist, apply schema,
    and return an open connection.

    `db_path` is optional — pass an explicit path to use a custom location
    (e.g. a tmp_path in tests). Omit to use the default ~/.archivist/registry.db.

    Idempotent. Safe to call on every startup.
    """
    if db_path is None:
        home = get_archivist_home()
        home.mkdir(parents=True, exist_ok=True)
        db_path = get_registry_path()

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS apparatuses (
            id          INTEGER PRIMARY KEY,
            name        TEXT UNIQUE NOT NULL,
            db_path     TEXT NOT NULL,
            created_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vaults (
            id              INTEGER PRIMARY KEY,
            apparatus_id    INTEGER NOT NULL REFERENCES apparatuses(id),
            name            TEXT NOT NULL,
            path            TEXT NOT NULL,
            created_at      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS modules (
            id              INTEGER PRIMARY KEY,
            apparatus_id    INTEGER NOT NULL REFERENCES apparatuses(id),
            vault_id        INTEGER REFERENCES vaults(id),
            name            TEXT NOT NULL,
            module_type     TEXT NOT NULL,
            path            TEXT NOT NULL,
            library_tag     TEXT,
            created_at      TEXT NOT NULL
        );
    """)
    conn.commit()
    return conn


def init_apparatus_db(apparatus_name_or_path: str | Path) -> sqlite3.Connection:
    """
    Create an apparatus-level database if it doesn't exist, apply the full
    works catalog + changelog schema, and return an open connection.

    `apparatus_name_or_path` accepts either:
      - a str apparatus name (e.g. "writing") — path derived via get_apparatus_db_path()
      - a Path to an explicit DB file — used by test fixtures so they never
        touch ~/.archivist/

    module_id in work_libraries and changelogs is a soft FK to registry.db.
    SQLite cannot enforce it across files. The application layer must.
    """
    if isinstance(apparatus_name_or_path, Path):
        db_path = apparatus_name_or_path
    else:
        db_path = get_apparatus_db_path(apparatus_name_or_path)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS authors (
            id          INTEGER PRIMARY KEY,
            sort_name   TEXT NOT NULL UNIQUE,
            first_name  TEXT,
            last_name   TEXT NOT NULL,
            aliases     TEXT,
            homepage    TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS publications (
            id          INTEGER PRIMARY KEY,
            sort_title  TEXT NOT NULL UNIQUE,
            title       TEXT NOT NULL,
            pub_type    TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS works (
            id               INTEGER PRIMARY KEY,
            sort_title       TEXT NOT NULL,
            title_alt        TEXT,
            class            TEXT,
            category         TEXT,
            year             INTEGER,
            publication_id   INTEGER REFERENCES publications(id),
            citation         TEXT,
            text_source      TEXT,
            word_count       INTEGER,
            part_of          TEXT,
            themes           TEXT,
            keywords         TEXT,
            content_warnings TEXT,
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS work_authors (
            work_id     INTEGER NOT NULL REFERENCES works(id),
            author_id   INTEGER NOT NULL REFERENCES authors(id),
            role        TEXT NOT NULL DEFAULT 'author',
            PRIMARY KEY (work_id, author_id, role)
        );

        CREATE TABLE IF NOT EXISTS work_libraries (
            work_id         INTEGER NOT NULL REFERENCES works(id),
            module_id       INTEGER NOT NULL,
            card_path       TEXT NOT NULL,
            work_stage      TEXT,
            date_consumed   TEXT,
            date_cataloged  TEXT,
            date_reviewed   TEXT,
            PRIMARY KEY (work_id, module_id)
        );

        CREATE TABLE IF NOT EXISTS work_relations (
            work_id       INTEGER NOT NULL REFERENCES works(id),
            related_id    INTEGER NOT NULL REFERENCES works(id),
            relation_type TEXT NOT NULL,
            PRIMARY KEY (work_id, related_id, relation_type)
        );

        CREATE TABLE IF NOT EXISTS changelogs (
            id          INTEGER PRIMARY KEY,
            module_id   INTEGER NOT NULL,
            uuid        TEXT UNIQUE,
            commit_sha  TEXT UNIQUE,
            date        TEXT NOT NULL,
            sealed_at   TEXT,
            created_at  TEXT NOT NULL
        );
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_apparatus(
    name: str,
    conn: sqlite3.Connection,
    db_path: Path | None = None,
) -> int:
    """
    Insert a new Apparatus row and materialize its database file on disk.

    `db_path` is optional. Omit to derive the path from `name` via
    get_apparatus_db_path() — the standard production path. Pass an explicit
    Path to use a custom location (e.g. a tmp_path in tests so the apparatus
    DB never touches ~/.archivist/).

    Returns the new apparatus id. Raises sqlite3.IntegrityError on duplicate
    name — check with list_apparatuses() first if unsure.
    """
    if db_path is None:
        db_path = get_apparatus_db_path(name)
    conn.execute(
        "INSERT INTO apparatuses (name, db_path, created_at) VALUES (?, ?, ?)",
        (name, str(db_path), _now()),
    )
    conn.commit()
    # Materialize the apparatus DB. Passing the path directly so we land
    # exactly where the caller expects — not a derived default location.
    apparatus_conn = init_apparatus_db(db_path)
    apparatus_conn.close()
    row = conn.execute(
        "SELECT id FROM apparatuses WHERE name = ?", (name,)
    ).fetchone()
    return row[0]


def register_vault(
    apparatus_id: int,
    name: str,
    path: Path,
    conn: sqlite3.Connection,
) -> int:
    """Insert a new Vault row and return its id."""
    conn.execute(
        "INSERT INTO vaults (apparatus_id, name, path, created_at) VALUES (?, ?, ?, ?)",
        (apparatus_id, name, str(path.resolve()), _now()),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM vaults WHERE apparatus_id = ? AND name = ?",
        (apparatus_id, name),
    ).fetchone()
    return row[0]


def register_module(
    apparatus_id: int,
    vault_id: int | None,
    name: str,
    module_type: str,
    path: Path,
    library_tag: str | None,
    conn: sqlite3.Connection,
) -> int:
    """
    Insert a new Module row and return its id.

    `vault_id` is nullable. `library_tag` is only meaningful for library
    modules; pass None for everything else.
    """
    conn.execute(
        """
        INSERT INTO modules
            (apparatus_id, vault_id, name, module_type, path, library_tag, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (apparatus_id, vault_id, name, module_type, str(path.resolve()), library_tag, _now()),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM modules WHERE apparatus_id = ? AND path = ?",
        (apparatus_id, str(path.resolve())),
    ).fetchone()
    return row[0]


def get_or_create_apparatus(
    name: str,
    conn: sqlite3.Connection,
    db_path: Path | None = None,
) -> tuple[int, bool]:
    """
    Return (apparatus_id, was_created).

    `db_path` is forwarded to register_apparatus() when creating a new
    apparatus. Omit to use the default path derived from `name`. Pass an
    explicit Path in tests so the apparatus DB never lands in ~/.archivist/.
    """
    row = conn.execute(
        "SELECT id FROM apparatuses WHERE name = ?", (name,)
    ).fetchone()
    if row:
        return row[0], False
    return register_apparatus(name, conn, db_path=db_path), True


def get_or_create_vault(
    apparatus_id: int,
    name: str,
    path: Path,
    conn: sqlite3.Connection,
) -> tuple[int, bool]:
    """Return (vault_id, was_created)."""
    row = conn.execute(
        "SELECT id FROM vaults WHERE apparatus_id = ? AND name = ?",
        (apparatus_id, name),
    ).fetchone()
    if row:
        return row[0], False
    return register_vault(apparatus_id, name, path, conn), True


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def list_apparatuses(conn: sqlite3.Connection) -> list[dict]:
    """Return all registered Apparatuses as a list of dicts, sorted by name."""
    rows = conn.execute(
        "SELECT id, name, db_path, created_at FROM apparatuses ORDER BY name"
    ).fetchall()
    return [
        {"id": r[0], "name": r[1], "db_path": r[2], "created_at": r[3]}
        for r in rows
    ]


def list_vaults(apparatus_id: int, conn: sqlite3.Connection) -> list[dict]:
    """Return all Vaults for a given Apparatus, sorted by name."""
    rows = conn.execute(
        "SELECT id, name, path, created_at FROM vaults WHERE apparatus_id = ? ORDER BY name",
        (apparatus_id,),
    ).fetchall()
    return [
        {"id": r[0], "name": r[1], "path": r[2], "created_at": r[3]}
        for r in rows
    ]


def list_modules(apparatus_id: int, conn: sqlite3.Connection) -> list[dict]:
    """Return all Modules for a given Apparatus, sorted by name."""
    rows = conn.execute(
        """
        SELECT id, vault_id, name, module_type, path, library_tag, created_at
        FROM modules WHERE apparatus_id = ? ORDER BY name
        """,
        (apparatus_id,),
    ).fetchall()
    return [
        {
            "id": r[0], "vault_id": r[1], "name": r[2], "module_type": r[3],
            "path": r[4], "library_tag": r[5], "created_at": r[6],
        }
        for r in rows
    ]


def get_module_by_path(path: Path, conn: sqlite3.Connection) -> dict | None:
    """
    Look up a module by its absolute path. Returns a dict or None.

    This is how Archivist confirms registration before running commands
    that require it. None means go run `archivist init`.
    """
    row = conn.execute(
        """
        SELECT id, apparatus_id, vault_id, name, module_type, path, library_tag, created_at
        FROM modules WHERE path = ?
        """,
        (str(path.resolve()),),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row[0], "apparatus_id": row[1], "vault_id": row[2],
        "name": row[3], "module_type": row[4], "path": row[5],
        "library_tag": row[6], "created_at": row[7],
    }


def get_module_id_by_path(path: Path, conn: sqlite3.Connection) -> int | None:
    """Return just the module id for a given path, or None if not registered."""
    module = get_module_by_path(path, conn)
    return module["id"] if module else None


def is_module_registered(path: Path) -> bool:
    """
    Return True if the module at `path` is registered in registry.db.

    One-shot: opens and closes its own connection. Returns False cleanly
    if registry.db doesn't exist yet rather than creating it and lying
    about a successful lookup on an empty registry.
    """
    registry_path = get_registry_path()
    if not registry_path.exists():
        return False
    conn = sqlite3.connect(registry_path)
    try:
        return get_module_id_by_path(path, conn) is not None
    finally:
        conn.close()