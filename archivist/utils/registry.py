# ---------------------------------------------------------------------------
# Apparatus Registry
# ---------------------------------------------------------------------------
#
# Machine-level registry for the Apparatus Platform. One file. One purpose.
# All registry DB access goes through here — if you find yourself reaching
# into ~/.archivist/ directly from a command module, you've already fucked up.
#
# Two databases. Two concerns. Do not conflate them:
#   registry.db             — apparati, modules, containment (module_bays)
#   [name].db               — per-apparatus: changelogs, works, authors
#
# Public surface:
#   Path resolution         — get_registry_dir, get_registry_path, get_apparatus_db_path
#   Connections             — get_registry_connection, get_apparatus_connection
#   Initialization          — init_registry, init_apparatus_db
#   Apparatus lifecycle     — register_apparatus, get_apparatus_by_name
#   Apparatus membership    — add_module_to_apparatus, remove_module_from_apparatus,
#                             remove_all_apparatus_memberships, get_module_apparati
#   Module lifecycle        — register_module, get_module_by_uuid, get_module_by_path,
#                             is_module_registered, update_module_sync,
#                             decimate_module, reactivate_module
#   Bay management          — add_module_to_bay, remove_module_from_bay,
#                             remove_all_bays_for_contained, get_module_bays
#   Containment resolution  — resolve_container_module, link_module_into_container
#   Shared registration     — register_module_with_apparati (the one true copy of
#                             the register-apparatus-then-module-then-extras dance;
#                             `add` and `init` both call this instead of forking it),
#                             register_known_module / register_known_module_with_apparati
#                             (same dance, but for a module whose UUID is already
#                             declared in its own config — `sync` uses these so it
#                             never mints an identity the module didn't ask for)
#   Queries                 — get_apparatus_modules, get_bay_modules, get_vault_modules
#
# Deferred (not implemented in Phase 1):
#   commit_registry, push_registry — future automated backup
# ---------------------------------------------------------------------------

from __future__ import annotations

import re
import sqlite3
import subprocess
import uuid as _uuid_module
from datetime import datetime
from pathlib import Path
from typing import TypedDict, cast

try:
    import curses
except ImportError:  # pragma: no cover — Windows without windows-curses installed
    curses = None

from archivist.utils.config import (
    APPARATUS_MODULE_TYPES,
    ConfigSchema,
    read_archivist_config,
    write_archivist_config,
)
from archivist.utils.git import get_project_name
from archivist.utils.output import warning


# ---------------------------------------------------------------------------
# Row shapes — typed dicts you get back from DB queries
# ---------------------------------------------------------------------------
# These mirror the schema exactly. If you change the schema and don't update
# these, you deserve the AttributeError you're about to receive.

class ApparatusRow(TypedDict):
    uuid:       str
    name:       str
    db_path:    str
    created_at: str
    git_remote: str | None


class ModuleRow(TypedDict):
    uuid:            str
    name:            str
    module_type:     str
    path:            str
    git_remote:      str | None
    git_remote_name: str | None
    decimated_at:    str | None
    last_synced_at:  str | None


class ModuleBayRow(ModuleRow):
    """ModuleRow of the *container* module, plus the container_id FK from the JOIN."""
    container_id: str


# ---------------------------------------------------------------------------
# Name validation
# ---------------------------------------------------------------------------

# Apparatus names and module names share the same character constraints:
# lowercase, hyphens, and alphanumerics only. No spaces. No uppercase.
# No filesystem-hostile garbage. If you can't name your apparatus something
# that works as a filename, you don't get an apparatus.
_VALID_SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9-]*$')


def validate_slug(name: str, label: str = "name") -> None:
    """
    Raise ValueError if name contains characters that would make Archivist
    (and your filesystem) miserable. Lowercase, hyphens, alphanumerics only.
    Leading hyphen is not permitted — that's a flag, not a name.
    """
    if not _VALID_SLUG_RE.match(name):
        raise ValueError(
            f"Invalid { label } { name!r }. "
            "Lowercase, hyphens, and alphanumerics only. "
            "That's it. That's the whole rule."
        )


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def get_apparatus_db_path(name: str) -> Path:
    """Return the absolute path to the apparatus DB for the given name."""
    return get_registry_dir() / f"{ name }.db"


def get_registry_dir() -> Path:
    """
    The single source of truth for ~/.archivist/.

    Every other path in this module derives from this one. If the storage
    location ever changes — centralized, decentralized, whatever future
    hell we're building toward — this is the one function that changes.
    Nothing outside registry.py constructs this path directly. Nothing.
    """
    return Path.home() / ".archivist"


def get_registry_path() -> Path:
    """Return the absolute path to registry.db."""
    return get_registry_dir() / "registry.db"


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

def _open_connection(db_path: Path) -> sqlite3.Connection:
    """
    Open a SQLite connection with FK enforcement ON.

    FK enforcement is OFF by default in SQLite. Every connection must
    execute PRAGMA foreign_keys = ON before doing anything else, or
    referential integrity is silently unenforced and orphaned rows
    accumulate until something breaks in a way that's a nightmare to trace.

    Callers are responsible for closing the returned connection. This is
    not optional and it's not this function's problem. Close your connections.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_apparatus_connection(apparatus_name: str) -> sqlite3.Connection:
    """
    Open and return a connection to the apparatus DB for the given name.

    Calls init_apparatus_db() first to ensure the schema exists.
    FK enforcement is ON. Callers close the connection.
    """
    init_apparatus_db(apparatus_name)
    return _open_connection(get_apparatus_db_path(apparatus_name))


def get_registry_connection() -> sqlite3.Connection:
    """
    Open and return a connection to registry.db.

    Calls init_registry() first to ensure the schema exists — so a cold
    machine, a nuked DB, or (the fun one) a zero-byte file that SQLite
    created before the schema ran don't explode in callers' faces.
    FK enforcement is ON. Callers close the connection.
    """
    init_registry()
    return _open_connection(get_registry_path())


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------

def _create_apparatus_schema(conn: sqlite3.Connection) -> None:
    """Create the apparatus DB schema. Idempotent — IF NOT EXISTS everywhere."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS changelogs (
            uuid        TEXT PRIMARY KEY,
            commit_sha  TEXT,
            log_scope   TEXT,
            module_uuid TEXT NOT NULL,  -- logical FK to registry.db modules.uuid; cross-DB, unenforced by SQLite
            created_at  TEXT NOT NULL,
            sealed_at   TEXT,
            file_path   TEXT
        );

        CREATE TABLE IF NOT EXISTS works (
            uuid        TEXT PRIMARY KEY,
            title       TEXT NOT NULL,
            module_uuid TEXT NOT NULL,  -- logical FK to registry.db modules.uuid; cross-DB, unenforced by SQLite
            work_stage  TEXT,
            created_at  TEXT NOT NULL,
            modified_at TEXT
        );

        CREATE TABLE IF NOT EXISTS authors (
            uuid           TEXT PRIMARY KEY,
            name           TEXT NOT NULL,
            apparatus_uuid TEXT NOT NULL,  -- logical FK to registry.db apparati.uuid; cross-DB, unenforced by SQLite
            created_at     TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS works_authors (
            work_uuid   TEXT NOT NULL REFERENCES works(uuid),
            author_uuid TEXT NOT NULL REFERENCES authors(uuid),
            PRIMARY KEY (work_uuid, author_uuid)
        );
    """)
    conn.commit()


def _create_registry_schema(conn: sqlite3.Connection) -> None:
    """Create the registry.db schema. Idempotent — IF NOT EXISTS everywhere."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS apparati (
            uuid        TEXT PRIMARY KEY,
            name        TEXT NOT NULL UNIQUE,
            db_path     TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            git_remote  TEXT
        );

        CREATE TABLE IF NOT EXISTS modules (
            uuid             TEXT PRIMARY KEY,
            name             TEXT NOT NULL,
            module_type      TEXT NOT NULL CHECK(
                                 module_type IN (
                                     'story', 'publication', 'library', 'vault', 'general'
                                 )
                             ),
            path             TEXT NOT NULL,
            git_remote       TEXT,
            git_remote_name  TEXT,
            decimated_at     TEXT,
            last_synced_at   TEXT
        );

        CREATE TABLE IF NOT EXISTS module_bays (
            container_id  TEXT NOT NULL REFERENCES modules(uuid),
            contained_id  TEXT NOT NULL REFERENCES modules(uuid),
            PRIMARY KEY (container_id, contained_id)
        );

        CREATE TABLE IF NOT EXISTS module_apparatus (
            module_uuid    TEXT NOT NULL REFERENCES modules(uuid),
            apparatus_uuid TEXT NOT NULL REFERENCES apparati(uuid),
            PRIMARY KEY (module_uuid, apparatus_uuid)
        );
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

def init_apparatus_db(apparatus_name: str) -> None:
    """
    Ensure the apparatus DB for the given name exists with the correct schema.

    Idempotent. Safe to call whether or not the DB already exists.
    Validates the name first — apparatus names become filenames and must
    be slugs. If your apparatus is named "My Writing Projects!!!",
    you deserve the error you're about to receive.
    """
    validate_slug(apparatus_name, label = "apparatus name")
    db_path = get_apparatus_db_path(apparatus_name)
    conn = _open_connection(db_path)
    try:
        _create_apparatus_schema(conn)
    finally:
        conn.close()


def init_registry() -> None:
    """
    Ensure ~/.archivist/ exists, is a git repo, and has a schema.

    Three states, all handled:
      - ~/.archivist/ absent → mkdir, git init, create schema
      - ~/.archivist/ present, no registry.db → create schema only
      - Both present → no-op

    Idempotent. Safe to call on a machine that already has a registry.
    Will not destroy existing data. Will not error on re-runs.

    The git init here is for the registry repo itself, completely separate
    from any module's git context. Do not conflate them.
    """
    registry_dir = get_registry_dir()
    registry_dir.mkdir(exist_ok = True)

    # Initialize registry as its own git repo if it isn't already one.
    # Runs as a subprocess — does not affect the calling process's git context.
    git_dir = registry_dir / ".git"
    if not git_dir.exists():
        subprocess.run(
            [
                "git",
                "init",
                str(registry_dir)
            ],
            check = True,
            capture_output = True,
        )

    # Schema creation is separate from directory/git setup so partial
    # failures (e.g. git init works, schema fails) leave us in a state
    # where re-running init_registry() can resume cleanly.
    db_path = get_registry_path()
    conn = _open_connection(db_path)
    try:
        _create_registry_schema(conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Apparatus lifecycle
# ---------------------------------------------------------------------------

def get_apparatus_by_name(name: str) -> ApparatusRow | None:
    """
    Return the apparatus row as a plain dict, or None if it doesn't exist.
    """
    conn = get_registry_connection()
    try:
        row = conn.execute(
            "SELECT * FROM apparati WHERE name = ?", (name,)
        ).fetchone()
        return cast(ApparatusRow, dict(row)) if row else None
    finally:
        conn.close()


def register_apparatus(name: str, git_remote: str | None) -> str:
    """
    Register an apparatus with the given name, or return its UUID if it
    already exists. Upserts — calling this twice with the same name does
    not create a duplicate.

    Creates the apparatus DB if it doesn't exist yet.

    Returns the apparatus UUID (existing or newly generated).
    """
    validate_slug(name, label = "apparatus name")
    init_apparatus_db(name)

    db_path = str(get_apparatus_db_path(name))
    now = datetime.now().strftime("%Y-%m-%d")

    conn = get_registry_connection()
    try:
        # If an apparatus with this name already exists, hand back its UUID
        # without touching anything else. The git_remote is theirs to manage.
        existing = conn.execute(
            "SELECT uuid FROM apparati WHERE name = ?", (name,)
        ).fetchone()
        if existing:
            return existing["uuid"]

        new_uuid = str(_uuid_module.uuid4())
        conn.execute(
            """
            INSERT INTO apparati (uuid, name, db_path, created_at, git_remote)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                new_uuid,
                name,
                db_path,
                now,
                git_remote
            ),
        )
        conn.commit()
        return new_uuid
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Module lifecycle
# ---------------------------------------------------------------------------

def decimate_module(uuid: str) -> None:
    """
    Stamp decimated_at on the module row.

    Modules are never hard-deleted. Deregistration marks them decimated;
    history is preserved; they're reactivatable via reactivate_module().

    Raises ValueError if the UUID isn't found — you can't decimate what
    was never registered.
    """
    module = get_module_by_uuid(uuid)
    if module is None:
        raise ValueError(
            f"Module {uuid!r} not found in registry. "
            "Can't decimate something that doesn't exist. "
            "You're welcome for the philosophical clarity."
        )

    now = datetime.now().strftime("%Y-%m-%d")
    conn = get_registry_connection()
    try:
        conn.execute(
            "UPDATE modules SET decimated_at = ? WHERE uuid = ?", (now, uuid)
        )
        conn.commit()
    finally:
        conn.close()


def get_module_by_path(path: Path) -> ModuleRow | None:
    """
    Return the module row whose registered path matches the given path,
    or None if not found.

    Resolves symlinks and relative paths to absolute before querying —
    the registry stores absolute paths and so should you when calling this.
    """
    absolute_path = str(path.resolve())
    conn = get_registry_connection()
    try:
        row = conn.execute(
            "SELECT * FROM modules WHERE path = ?", (absolute_path,)
        ).fetchone()
        return cast(ModuleRow, dict(row)) if row else None
    finally:
        conn.close()


def get_module_by_uuid(uuid: str) -> ModuleRow | None:
    """Return the module row as a plain dict, or None if not found."""
    conn = get_registry_connection()
    try:
        row = conn.execute(
            "SELECT * FROM modules WHERE uuid = ?", (uuid,)
        ).fetchone()
        return cast(ModuleRow, dict(row)) if row else None
    finally:
        conn.close()


def is_module_registered(uuid: str) -> bool:
    """Return True if a module with this UUID exists in the registry."""
    return get_module_by_uuid(uuid) is not None


def reactivate_module(uuid: str) -> None:
    """
    Clear decimated_at on the module row, bringing it back from the grave.

    This is what archivist add calls when it detects a UUID that's in the
    registry but marked decimated. Clear first, add bays second — a module
    with decimated_at set must not appear in active queries even if bay rows
    exist.

    Raises ValueError if the UUID isn't found.
    """
    module = get_module_by_uuid(uuid)
    if module is None:
        raise ValueError(
            f"Module { uuid!r } not found in registry. "
            "There's nothing here to reactivate."
        )

    conn = get_registry_connection()
    try:
        conn.execute(
            "UPDATE modules SET decimated_at = NULL WHERE uuid = ?", (uuid,)
        )
        conn.commit()
    finally:
        conn.close()


def register_known_module(
    uuid: str,
    apparatus_name: str | None,
    name: str,
    module_type: str,
    path: Path,
    git_remote: str | None,
    git_remote_name: str | None = None,
) -> str:
    """
    Register a module under a UUID the caller already knows about — because
    the module declared it itself, in its own `.archivist/config.yaml`,
    possibly years before `sync` ever laid eyes on it.

    register_module() cannot do this job. It mints its own UUID on first
    sight, or reuses whatever it finds already sitting at `path` — it has
    NO mechanism for "trust me, THIS is the uuid, I got it from the file."
    That gap is exactly the bug that made `sync` untrustworthy: any module
    it discovered that wasn't already registered by path would get handed
    a brand-new UUID that didn't match the one written in its own config —
    the same module answering to two different identities depending on who
    asked. Not on my watch.

    Lookup here is by UUID, not by path — path is a cache, UUID is the
    actual identity. See resolve_container_module()'s docstring for the
    full sermon on why that distinction matters. If a row with this UUID
    already exists, it's updated in place (name, type, path, remote, sync
    timestamp), same as register_module()'s update branch. Apparatus
    associations are never touched on update here either — same rule,
    same reasons.

    If no row exists yet, one is inserted with EXACTLY this UUID. No
    minting, no negotiating — the config file already made the call;
    this function just writes it down.

    Raises ValueError on invalid module_type, same validation as
    register_module(). Raises ValueError if apparatus_name is provided but
    isn't a registered apparatus.
    """
    if module_type not in APPARATUS_MODULE_TYPES:
        raise ValueError(
            f"Invalid module_type { module_type!r }. "
            f"Must be one of: { ', '.join(APPARATUS_MODULE_TYPES) }. "
            "I don't know what you thought you were registering."
        )

    apparatus: ApparatusRow | None = None
    if apparatus_name is not None:
        apparatus = get_apparatus_by_name(apparatus_name)
        if apparatus is None:
            raise ValueError(
                f"Apparatus { apparatus_name!r } not found in registry. "
                "Register the apparatus before adding modules to it."
            )

    absolute_path = str(path.resolve())
    now = datetime.now().strftime("%Y-%m-%d")

    conn = get_registry_connection()
    try:
        existing_by_uuid = conn.execute(
            "SELECT uuid FROM modules WHERE uuid = ?", (uuid,)
        ).fetchone()

        if existing_by_uuid:
            # Update in place — name, type, path, remote, sync timestamp.
            # Path IS refreshed here (unlike register_module()'s path-keyed
            # lookup, where the path is the thing that was just matched on
            # and refreshing it would be a no-op) — a known module can
            # absolutely have moved since the last time anyone looked.
            conn.execute(
                """
                UPDATE modules
                SET name = ?, module_type = ?, path = ?, git_remote = ?,
                    git_remote_name = ?, last_synced_at = ?
                WHERE uuid = ?
                """,
                (
                    name,
                    module_type,
                    absolute_path,
                    git_remote,
                    git_remote_name,
                    now,
                    uuid,
                ),
            )
            conn.commit()
            return uuid

        conn.execute(
            """
            INSERT INTO modules
                (uuid, name, module_type, path, git_remote,
                 git_remote_name, decimated_at, last_synced_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                uuid,
                name,
                module_type,
                absolute_path,
                git_remote,
                git_remote_name,
                now,
            ),
        )

        if apparatus is not None:
            conn.execute(
                "INSERT OR IGNORE INTO module_apparatus (module_uuid, apparatus_uuid) VALUES (?, ?)",
                (uuid, apparatus["uuid"]),
            )

        conn.commit()
        return uuid
    finally:
        conn.close()


def register_known_module_with_apparati(
    module_uuid: str,
    module_dir: Path,
    apparatus_names: list[str],
    module_type: str,
    git_remote: str | None,
    git_remote_name: str | None = None,
) -> str:
    """
    register_module_with_apparati()'s twin for the case where the module's
    UUID is already a known quantity — read straight off its own config
    file by `sync`, not invented by us. Same apparatus-upsert dance, same
    first-name-rides-along-free / extras-get-their-own-row logic (including
    the empty-apparatus_names / standalone-module case — see that
    docstring), just routed through register_known_module() instead of
    register_module() so the UUID that lands in the registry is THE SAME
    UUID sitting in the module's `.archivist/config.yaml`. That agreement
    is the entire point of this function existing.

    Returns module_uuid, unchanged — it was never in question.
    """
    primary_apparatus = apparatus_names[0] if apparatus_names else None
    extra_apparati = apparatus_names[1:] if apparatus_names else []

    if primary_apparatus is not None:
        register_apparatus(primary_apparatus, git_remote = None)  # registry remote ≠ module remote

    register_known_module(
        uuid = module_uuid,
        apparatus_name = primary_apparatus,
        name = get_project_name(module_dir),
        module_type = module_type,
        path = module_dir,
        git_remote = git_remote,
        git_remote_name = git_remote_name,
    )
    for extra_name in extra_apparati:
        extra_uuid = register_apparatus(extra_name, git_remote = None)
        add_module_to_apparatus(module_uuid, extra_uuid)
    return module_uuid


def register_module(
    apparatus_name: str | None,
    name: str,
    module_type: str,
    path: Path,
    git_remote: str | None,
    git_remote_name: str | None = None,
) -> str:
    """
    Register a module with the Apparatus, or update its row if it already
    exists. Returns the module UUID (existing or newly generated).

    Apparatus associations are always optional and managed through the
    module_apparatus junction table. Pass apparatus_name to associate the
    module with an apparatus on first registration. Pass None for standalone
    modules. Additional apparatus associations can be added later via
    add_module_to_apparatus(); on update (existing module matched by path),
    apparatus associations are never modified by this function — that is
    a separate explicit operation.

    The UUID, if absent from the caller's context, is generated here. If a
    UUID is already known (e.g. read from .archivist/config.yaml), pass it
    via the returned value — the caller is responsible for writing it back to
    config. This function does not touch config files.

    Validates module_type in Python before writing. The CHECK constraint in
    the schema is a safety net, not the primary gate — SQLite's constraint
    enforcement is version-dependent and not to be trusted alone.

    Raises ValueError on invalid module_type.
    Raises ValueError if apparatus_name is provided but not registered — call
    register_apparatus() first.
    """
    if module_type not in APPARATUS_MODULE_TYPES:
        raise ValueError(
            f"Invalid module_type { module_type!r }. "
            f"Must be one of: { ', '.join(APPARATUS_MODULE_TYPES) }. "
            "I don't know what you thought you were registering."
        )

    apparatus: ApparatusRow | None = None
    if apparatus_name is not None:
        apparatus = get_apparatus_by_name(apparatus_name)
        if apparatus is None:
            raise ValueError(
                f"Apparatus { apparatus_name!r } not found in registry. "
                "Register the apparatus before adding modules to it."
            )

    absolute_path = str(path.resolve())
    now = datetime.now().strftime("%Y-%m-%d")

    conn = get_registry_connection()
    try:
        # Check whether this module is already registered (by path, since UUID
        # may not yet be known on first registration).
        existing_by_path = conn.execute(
            "SELECT uuid FROM modules WHERE path = ?", (absolute_path,)
        ).fetchone()

        if existing_by_path:
            existing_uuid = existing_by_path["uuid"]
            # Update in place — name, type, remote, sync timestamp.
            # Apparatus associations are not touched on update — membership
            # changes are a separate explicit operation.
            conn.execute(
                """
                UPDATE modules
                SET name = ?, module_type = ?, git_remote = ?, git_remote_name = ?,
                    last_synced_at = ?
                WHERE uuid = ?
                """,
                (
                    name,
                    module_type,
                    git_remote,
                    git_remote_name,
                    now,
                    existing_uuid
                ),
            )
            conn.commit()
            return existing_uuid

        new_uuid = str(_uuid_module.uuid4())
        conn.execute(
            """
            INSERT INTO modules
                (uuid, name, module_type, path, git_remote,
                 git_remote_name, decimated_at, last_synced_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                new_uuid,
                name,
                module_type,
                absolute_path,
                git_remote,
                git_remote_name,
                now,
            ),
        )

        # Apparatus membership is managed through the module_apparatus junction table.
        # If an apparatus_name was provided, write the association on first registration.
        if apparatus is not None:
            conn.execute(
                "INSERT OR IGNORE INTO module_apparatus (module_uuid, apparatus_uuid) VALUES (?, ?)",
                (new_uuid, apparatus["uuid"]),
            )

        conn.commit()
        return new_uuid
    finally:
        conn.close()


def register_module_with_apparati(
    module_dir: Path,
    apparatus_names: list[str],
    module_type: str,
    git_remote: str | None,
    git_remote_name: str | None = None,
) -> str:
    """
    Upsert apparatus row(s) and the module row for a module claiming
    membership in zero or more apparati.

    register_module() only wires up ONE apparatus association at creation
    time, so the first name in apparatus_names rides along with it; any
    additional names get their own explicit membership row via
    add_module_to_apparatus(). register_apparatus() is an idempotent upsert,
    so calling it repeatedly for the same name across runs is harmless.

    apparatus_names may be empty — a module doesn't have to belong to an
    Apparatus to use Archivist's frontmatter and changelog features, and
    prompt_apparatus_names() will happily hand you an empty list when the
    user confirms with nothing checked. In that case this registers a
    standalone module: apparatus_name=None is passed straight through to
    register_module(), no apparatus row is touched, and the for-loop over
    extras is just a no-op over an empty list.

    This is the ONE place this exact dance lives now. `archivist add` and
    `archivist init` both call it instead of each keeping their own
    hand-rolled copy — see CODE_CONVENTIONS.md ("shared helpers belong in
    utils") if you're about to fork it a third time for `sync`. Don't.

    Returns the module UUID (existing on upsert, freshly minted otherwise).
    """
    primary_apparatus = apparatus_names[0] if apparatus_names else None
    extra_apparati = apparatus_names[1:] if apparatus_names else []

    if primary_apparatus is not None:
        register_apparatus(primary_apparatus, git_remote = None)  # registry remote ≠ module remote

    module_uuid = register_module(
        apparatus_name = primary_apparatus,
        name = get_project_name(module_dir),
        module_type = module_type,
        path = module_dir,
        git_remote = git_remote,
        git_remote_name = git_remote_name,
    )
    for extra_name in extra_apparati:
        extra_uuid = register_apparatus(extra_name, git_remote = None)
        add_module_to_apparatus(module_uuid, extra_uuid)
    return module_uuid


def update_module_sync(uuid: str, path: Path) -> None:
    """
    Update a module's path and last_synced_at timestamp.

    `path` is resolved to an absolute path before writing, same as
    register_module() — the registry stores absolute paths and this
    function is not the place to start being precious about that.

    Called by the pre-commit hook on every commit. Non-blocking contract:
    if the module isn't found, this is a silent no-op. Do not raise.
    The hook cannot abort a commit over a missing registry entry.
    """
    absolute_path = str(path.resolve())
    conn = get_registry_connection()
    try:
        conn.execute(
            "UPDATE modules SET path = ?, last_synced_at = ? WHERE uuid = ?",
            (
                absolute_path,
                datetime.now().isoformat(),
                uuid
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Bay management
# ---------------------------------------------------------------------------

def add_module_to_bay(container_uuid: str, contained_uuid: str) -> None:
    """
    Record that contained_uuid lives inside container_uuid.

    INSERT OR IGNORE — if the bay relationship already exists, this is a
    silent no-op. Idempotent by design; archivist add calls this without
    checking first.
    """
    conn = get_registry_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO module_bays (container_id, contained_id) VALUES (?, ?)",
            (container_uuid, contained_uuid),
        )
        conn.commit()
    finally:
        conn.close()


def get_module_bays(contained_uuid: str) -> list[ModuleBayRow]:
    """
    Return all containers for the given module — every module_bays row where
    contained_id = contained_uuid, joined with module details for the container.

    Returns an empty list if the module has no containing modules.
    Used by archivist deinit to decide whether to decimate or merely evict.
    """
    conn = get_registry_connection()
    try:
        rows = conn.execute(
            """
            SELECT m.*, mb.container_id
            FROM module_bays mb
            JOIN modules m ON m.uuid = mb.container_id
            WHERE mb.contained_id = ?
            """,
            (contained_uuid,),
        ).fetchall()
        return [cast(ModuleBayRow, dict(row)) for row in rows]
    finally:
        conn.close()


def remove_all_bays_for_contained(contained_uuid: str) -> None:
    """
    Remove every bay relationship where contained_uuid is the contained module.

    Used by archivist deinit in standalone removal mode — when there's no
    specific superproject context, we evict the module from every container
    it belongs to, then check if decimation is warranted.
    """
    conn = get_registry_connection()
    try:
        conn.execute(
            "DELETE FROM module_bays WHERE contained_id = ?", (contained_uuid,)
        )
        conn.commit()
    finally:
        conn.close()


def remove_module_from_bay(container_uuid: str, contained_uuid: str) -> None:
    """
    Remove the bay relationship between container and contained.

    No-op if the row doesn't exist — archivist deinit idempotency depends
    on this. Do not raise on missing rows.
    """
    conn = get_registry_connection()
    try:
        conn.execute(
            "DELETE FROM module_bays WHERE container_id = ? AND contained_id = ?",
            (container_uuid, contained_uuid),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Containment resolution — used by add, init, and (eventually) sync
# ---------------------------------------------------------------------------

def _add_to_vaults_list(module_dir: Path, vault_name: str) -> None:
    """
    Add vault_name to the `vaults` list in module_dir's own config.yaml.

    No-op if vault_name is already present. This is the config-file half of
    vault containment; the DB half (module_bays) is written unconditionally
    by link_module_into_container() regardless of the container's type.
    """
    config = read_archivist_config(module_dir) or {}
    current: list[str] = list(config.get("vaults") or [])  # type: ignore[arg-type]
    if vault_name in current:
        return
    current.append(vault_name)
    updated: ConfigSchema = {"uuid": str(config.get("uuid", ""))}  # type: ignore[typeddict-item]
    for k, v in config.items():
        if k != "uuid":
            updated[k] = v  # type: ignore[literal-required]
    updated["vaults"] = current  # type: ignore[typeddict-item]
    write_archivist_config(module_dir, updated)


def link_module_into_container(
    container_row: ModuleRow,
    module_uuid: str,
    module_dir: Path,
) -> None:
    """
    Record that container_row owns module_uuid — in the DB (module_bays,
    unconditionally) and, if the container happens to be a vault, in the
    module's own `vaults` config list too (a human-readable convenience
    field that is genuinely vault-specific; module_bays is the real
    containment record and does not care what type either side is).

    Containers are not required to be vaults. A publication, a general
    module, whatever holds whatever makes sense for your project — vault
    only gets special treatment here because `vaults:` in config.yaml was
    written as a vault-flavored field. The bay row itself doesn't play
    favorites.

    Idempotent — add_module_to_bay() is INSERT OR IGNORE underneath. Call
    this once, or from three different commands each thinking they might be
    first; the outcome is the same DB state either way.
    """
    add_module_to_bay(container_row["uuid"], module_uuid)
    if container_row.get("module_type") == "vault":
        _add_to_vaults_list(module_dir, container_row["name"])


def resolve_container_module(repo_root: Path) -> ModuleRow | None:
    """
    Return the registered, active module that owns repo_root — identified
    by the UUID repo_root's OWN .archivist/config.yaml declares, not by
    matching a path string somewhere in the registry.

    Path-based lookups are a trap: rename the vault's folder, move it to a
    new disk, restructure your projects directory because your old layout
    was an embarrassment — and a path-keyed lookup goes stale while the
    module hasn't changed identity at all. UUID is the module's actual
    identity. Path is a cache we keep around for convenience, and this
    function refuses to trust the cache when the source of truth (the
    module's own config file) is sitting right there for the reading.

    Pass the ACTUAL repo root — get_repo_root(), not a raw cwd. This matters
    when archivist was invoked from a subdirectory of the container rather
    than its top: `git rev-parse --show-toplevel` already solves that
    problem, so don't re-solve it badly with path arithmetic here.

    Refreshes the container's cached path in the registry as a side effect,
    since we've just PROVEN — by UUID, not by guesswork — that this is the
    container and that repo_root is where it currently, actually lives.

    Returns None if there's no config, no uuid in it, or the uuid isn't a
    currently-active registered module. A decimated module owns nothing —
    it doesn't get to be anyone's container just because a bay row from
    its former life is still sitting in the DB.

    Also returns None — without touching anything — if the registry hasn't
    been initialized on this machine yet. get_module_by_uuid() would happily
    auto-create ~/.archivist/ just to answer "no, nothing's there," which is
    exactly the kind of silent write a dry-run preview must never trigger.
    Every real (non-dry-run) call path already calls init_registry() itself
    before reaching this function; if the registry still doesn't exist by
    then, there is, by definition, nothing registered in it to find.
    """
    config = read_archivist_config(repo_root)
    if not config:
        return None

    container_uuid = config.get("uuid")
    if not container_uuid:
        return None

    if not get_registry_path().exists():
        return None

    container_row = get_module_by_uuid(container_uuid)
    if container_row is None or container_row.get("decimated_at"):
        return None

    update_module_sync(container_uuid, repo_root)
    return container_row


# ---------------------------------------------------------------------------
# Apparatus membership
# ---------------------------------------------------------------------------

def add_module_to_apparatus(module_uuid: str, apparatus_uuid: str) -> None:
    """
    Record that module_uuid belongs to apparatus_uuid.

    INSERT OR IGNORE — if the membership already exists, this is a silent
    no-op. Idempotent by design; archivist add calls this without checking
    first.
    """
    conn = get_registry_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO module_apparatus (module_uuid, apparatus_uuid) VALUES (?, ?)",
            (module_uuid, apparatus_uuid),
        )
        conn.commit()
    finally:
        conn.close()


def get_module_apparati(module_uuid: str) -> list[dict]:
    """
    Return all apparati the given module belongs to, sorted by name.

    Returns an empty list if the module has no apparatus memberships or
    isn't registered. Callers must not null-check this.
    """
    conn = get_registry_connection()
    try:
        rows = conn.execute(
            """
            SELECT a.*
            FROM apparati a
            JOIN module_apparatus ma ON ma.apparatus_uuid = a.uuid
            WHERE ma.module_uuid = ?
            ORDER BY a.name
            """,
            (module_uuid,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def remove_all_apparatus_memberships(module_uuid: str) -> None:
    """
    Remove every apparatus membership row for the given module.

    Used by archivist deinit in standalone removal mode — before decimation,
    the module is cleanly evicted from every apparatus it belongs to.
    Other modules' memberships are not touched.
    """
    conn = get_registry_connection()
    try:
        conn.execute(
            "DELETE FROM module_apparatus WHERE module_uuid = ?", (module_uuid,)
        )
        conn.commit()
    finally:
        conn.close()


def remove_module_from_apparatus(module_uuid: str, apparatus_uuid: str) -> None:
    """
    Remove the apparatus membership for a specific module/apparatus pair.

    No-op if the row doesn't exist — deinit idempotency depends on this.
    Do not raise on missing rows.
    """
    conn = get_registry_connection()
    try:
        conn.execute(
            "DELETE FROM module_apparatus WHERE module_uuid = ? AND apparatus_uuid = ?",
            (module_uuid, apparatus_uuid),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_apparatus_modules(apparatus_name: str, include_decimated: bool = False) -> list[ModuleRow]:
    """
    Return all modules belonging to the given apparatus, sorted by name.

    Excludes decimated modules by default — they're gone as far as most
    callers are concerned. Pass include_decimated=True to surface them,
    e.g. for muster --include-decimated.

    Returns an empty list if the apparatus has no modules or doesn't exist.
    """
    apparatus = get_apparatus_by_name(apparatus_name)
    if apparatus is None:
        return []

    conn = get_registry_connection()
    try:
        if include_decimated:
            rows = conn.execute(
                """
                SELECT m.*
                FROM modules m
                JOIN module_apparatus ma ON ma.module_uuid = m.uuid
                WHERE ma.apparatus_uuid = ?
                ORDER BY m.name
                """,
                (apparatus["uuid"],),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT m.*
                FROM modules m
                JOIN module_apparatus ma ON ma.module_uuid = m.uuid
                WHERE ma.apparatus_uuid = ? AND m.decimated_at IS NULL
                ORDER BY m.name
                """,
                (apparatus["uuid"],),
            ).fetchall()
        return [cast(ModuleRow, dict(row)) for row in rows]
    finally:
        conn.close()


def get_bay_modules(container_uuid: str, include_decimated: bool = False) -> list[ModuleRow]:
    """
    Return all modules contained by the given container module.

    Container can be any module type — vault is the expected case but
    containment is not restricted to vaults. Use get_vault_modules() when
    you specifically need to assert the container is a vault.

    Excludes decimated modules by default.
    Returns an empty list if the container has no contained modules or isn't registered.
    """
    conn = get_registry_connection()
    try:
        if include_decimated:
            rows = conn.execute(
                """
                SELECT m.*
                FROM module_bays mb
                JOIN modules m ON m.uuid = mb.contained_id
                WHERE mb.container_id = ?
                ORDER BY m.name
                """,
                (container_uuid,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT m.*
                FROM module_bays mb
                JOIN modules m ON m.uuid = mb.contained_id
                WHERE mb.container_id = ? AND m.decimated_at IS NULL
                ORDER BY m.name
                """,
                (container_uuid,),
            ).fetchall()
        return [cast(ModuleRow, dict(row)) for row in rows]
    finally:
        conn.close()


def get_vault_modules(vault_uuid: str, include_decimated: bool = False) -> list[ModuleRow]:
    """
    Return all modules contained by the given vault module.

    Validates that the container is actually of type 'vault' before
    delegating to get_bay_modules(). If you're trying to get the contents
    of a non-vault superproject, use get_bay_modules() directly and stop
    pretending it's a vault.

    Raises ValueError if vault_uuid isn't a vault-type module.
    Raises ValueError if vault_uuid isn't found in the registry at all.
    """
    container = get_module_by_uuid(vault_uuid)
    if container is None:
        raise ValueError(
            f"Module { vault_uuid!r } not found in registry. "
            "Can't list contents of something that doesn't exist."
        )
    if container["module_type"] != "vault":
        raise ValueError(
            f"Module { vault_uuid!r } is type { container['module_type']!r }, not 'vault'. "
            "Use get_bay_modules() if you want the contents of a non-vault superproject. "
            "Know what you're asking for."
        )
    return get_bay_modules(vault_uuid, include_decimated=include_decimated)


def list_apparatus_names() -> list[str]:
    """
    Return all apparatus names in the registry, sorted alphabetically.

    Returns an empty list if the registry doesn't exist yet or has no rows —
    so callers don't have to care whether the machine has ever run archivist init.
    """
    registry_path = get_registry_path()
    if not registry_path.exists():
        return []
    conn = get_registry_connection()
    try:
        rows = conn.execute(
            "SELECT name FROM apparati ORDER BY name"
        ).fetchall()
        return [row["name"] for row in rows]
    finally:
        conn.close()


_CREATE_NEW_LABEL = "+ Conjure a new apparatus"


def prompt_apparatus_names() -> list[str]:
    """
    Prompt the user to select any number of apparati, creating new ones inline.

    A module can belong to more than one Apparatus — the module_apparatus
    junction table has supported that since day one. This renders an
    honest-to-god interactive checkbox menu via the stdlib curses module:
    arrow keys to move, spacebar to toggle, enter to confirm. No npm-inquirer
    envy required, and no third-party TUI dependency dragged in to get it —
    see CODE_CONVENTIONS.md if you've forgotten why that matters here.

    Check as many existing apparati as you want, plus "+ Conjure a new
    apparatus" if you also want to mint a fresh one — both in the same pass.
    Confirming with the "conjure" row checked drops you into the slug prompt
    afterward, with the option to conjure more than one before you're done.

    Confirming with NOTHING checked is a perfectly valid answer. A module
    does not need to belong to an Apparatus to use Archivist's frontmatter
    and changelog features — this just means "standalone," not "error." The
    caller (register_module_with_apparati() and friends) treats an empty
    list as exactly that.

    If curses can't get a working terminal (Windows without the
    windows-curses package, output piped somewhere without a tty, whatever),
    this falls back to the old numbered comma/space-separated prompt instead
    of crashing like an amateur. See _prompt_apparatus_names_fallback().

    Returns a list of apparatus name slugs, in menu order (existing apparati
    alphabetically, per list_apparatus_names(), followed by any newly
    conjured ones in the order they were created), with no duplicates. May
    return an empty list — that's the standalone-module answer, not a bug.

    Shared by archivist add, archivist init, and archivist migrate — anywhere
    a user needs to assign a module to zero or more apparati interactively.
    """
    existing = list_apparatus_names()

    if curses is None:
        warning(
            "curses isn't available on this platform — falling back to the "
            "plain numbered prompt. Windows folks: `pip install windows-curses` "
            "if you want the checkbox menu."
        )
        return _prompt_apparatus_names_fallback()

    try:
        selected, wants_new = curses.wrapper(_run_apparatus_checkbox_menu, existing)
    except (curses.error, OSError) as e:
        warning(f"curses couldn't render the menu ({e}) — falling back to the numbered prompt.")
        return _prompt_apparatus_names_fallback()

    if wants_new:
        selected.append(_prompt_new_apparatus_slug(selected))
        while True:
            more = input("\n  Conjure another new apparatus? [y/N]: ").strip().lower()
            if more not in ("y", "yes"):
                break
            selected.append(_prompt_new_apparatus_slug(selected))

    return selected


def _run_apparatus_checkbox_menu(stdscr, existing: list[str]) -> tuple[list[str], bool]:
    """
    Drive the actual curses checkbox screen.

    Called via curses.wrapper(), which handles terminal setup and teardown —
    cbreak, noecho, keypad mode, and critically, restoring the terminal to
    sanity if this blows up with an exception instead of leaving the user's
    shell haunted for the rest of the session. Do not call this directly;
    prompt_apparatus_names() is the entry point.

    Returns (selected_existing_names, wants_new) where wants_new is True if
    the "+ Conjure a new apparatus" row was checked at confirm time.
    """
    # prompt_apparatus_names() only calls curses.wrapper() (and therefore
    # only ever reaches this function) after already confirming curses is
    # not None — but that guard lives one function up, and type narrowing
    # doesn't cross function boundaries. Without this, every curses.WHATEVER
    # reference below gets flagged as "not a known attribute of None" by
    # anything running static analysis, despite it being unreachable in
    # practice. One assert, narrowing sorted for the rest of this scope.
    assert curses is not None

    curses.curs_set(0)  # hide the cursor — this isn't a text field
    stdscr.keypad(True)

    options = existing + [_CREATE_NEW_LABEL]
    checked = [False] * len(options)
    cursor = 0

    while True:
        stdscr.erase()
        stdscr.addstr(0, 0, "Select apparatus/apparati for this module:")
        stdscr.addstr(1, 0, "up/down move  ·  space toggle  ·  enter confirm")
        stdscr.addstr(2, 0, "(leave everything unchecked for a standalone module — no Apparatus required)")

        for i, label in enumerate(options):
            box = "[x]" if checked[i] else "[ ]"
            line = f"  {box} {label}"
            attr = curses.A_REVERSE if i == cursor else curses.A_NORMAL
            stdscr.addstr(4 + i, 0, line, attr)

        stdscr.refresh()
        key = stdscr.getch()

        if key in (curses.KEY_UP, ord("k")):
            cursor = (cursor - 1) % len(options)
        elif key in (curses.KEY_DOWN, ord("j")):
            cursor = (cursor + 1) % len(options)
        elif key == ord(" "):
            checked[cursor] = not checked[cursor]
        elif key in (curses.KEY_ENTER, 10, 13):
            wants_new = checked[-1]
            selected = [opt for opt, c in zip(options[:-1], checked[:-1]) if c]
            return selected, wants_new
        # any other key: ignored — redraw and wait for something useful


def _prompt_apparatus_names_fallback() -> list[str]:
    """
    The pre-curses numbered prompt. Kept alive as the fallback path for
    platforms where curses won't cooperate — same selection semantics as
    the checkbox menu, just typed instead of ticked.

    Lists every registered apparatus as a numbered option, plus an
    always-present "Create new" entry. Enter one or more numbers separated
    by commas or spaces (e.g. "1, 3") to select several at once; including
    "Create new" in that same line drops into a slug prompt for a brand-new
    apparatus. Leaving the answer blank means "none of the above" — a
    standalone module with no Apparatus is a valid outcome, not something
    to hassle the user into avoiding. After each round of selections, asks
    if there's another apparatus to add — already-selected names are
    excluded from the list on the next round so you can't pick the same one
    twice.

    Not a public entry point. prompt_apparatus_names() decides when this
    gets called — don't call it directly unless you're specifically testing
    the fallback path.
    """
    selected: list[str] = []

    while True:
        remaining = [n for n in list_apparatus_names() if n not in selected]

        if selected:
            print(f"\n  Selected so far: {', '.join(selected)}")

        options = remaining + ["Create new"]
        print("\n  Select apparatus/apparati (comma- or space-separated numbers, or leave blank for none):")
        for i, name in enumerate(options, 1):
            print(f"  {i}. {name}")

        raw = input("\n  Enter number(s) [blank = standalone, no apparatus]: ").strip()

        if not raw:
            # A blank answer is a real answer: no apparatus for this module.
            # Not an error, not a nag, just "standalone" and move on.
            return selected

        tokens = [t for t in re.split(r"[,\s]+", raw) if t]

        if not all(t.isdigit() and 1 <= int(t) <= len(options) for t in tokens):
            print(
                f"  That's not a valid selection. Numbers between 1 and {len(options)}, "
                "comma- or space-separated, or blank if you meant none. Try again."
            )
            continue

        wants_new = False
        for t in tokens:
            choice = options[int(t) - 1]
            if choice == "Create new":
                wants_new = True
            elif choice not in selected:
                selected.append(choice)

        if wants_new:
            selected.append(_prompt_new_apparatus_slug(selected))

        more = input("\n  Add another apparatus? [y/N]: ").strip().lower()
        if more not in ("y", "yes"):
            return selected


def _prompt_new_apparatus_slug(already_selected: list[str]) -> str:
    """
    Prompt for one brand-new apparatus name slug.

    Loops until the input validates as a slug and isn't a duplicate of
    something already picked in this session — re-typing the apparatus you
    just selected two seconds ago is a you problem, but we catch it anyway.
    """
    while True:
        raw = input("\n  New apparatus name (lowercase, hyphens, alphanumerics): ").strip()
        if not raw:
            print("  Name can't be empty. Try again.")
            continue
        if raw in already_selected:
            print(f"  '{raw}' is already selected. Pick something else or move on.")
            continue
        try:
            validate_slug(raw, label="apparatus name")
            return raw
        except ValueError as e:
            print(f"  {e}")


# ---------------------------------------------------------------------------
# Deferred — Phase 2 / future automated backup
# ---------------------------------------------------------------------------

def commit_registry(message: str) -> None:
    """
    Commit all changes to the ~/.archivist/ registry repo.

    Not implemented in Phase 1. Manual workflow for now:
        cd ~/.archivist && git add -A && git commit && git push

    This function exists as a stub so call sites can be wired up without
    branching when it's eventually automated. Calling it does nothing.
    """


def push_registry() -> None:
    """
    Push the ~/.archivist/ registry repo to its remote.

    Not implemented in Phase 1. See commit_registry() for the manual workflow.
    """