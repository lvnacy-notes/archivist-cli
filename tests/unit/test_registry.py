"""
tests/unit/test_registry.py

Unit tests for archivist.utils.registry — the machine-level global registry
and apparatus database helpers.

No git. No git_repo fixture. No subprocess. Filesystem access is limited to
tmp_path. No test in this file touches ~/.archivist/.
"""

import sqlite3
from pathlib import Path

import pytest

from archivist.utils import registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _table_names(conn: sqlite3.Connection) -> set[str]:
    """Return all user table names in the connected database."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ---------------------------------------------------------------------------
# init_registry_db
# ---------------------------------------------------------------------------

class TestInitRegistryDb:

    def test_creates_file_at_given_path(self, tmp_path):
        db_path = tmp_path / "registry.db"
        assert not db_path.exists()
        conn = registry.init_registry_db(db_path)
        conn.close()
        assert db_path.exists(), "registry.db was not created at the given path"

    def test_creates_all_three_tables(self, tmp_path):
        db_path = tmp_path / "registry.db"
        conn = registry.init_registry_db(db_path)
        tables = _table_names(conn)
        conn.close()
        assert "apparatuses" in tables, "apparatuses table missing from schema"
        assert "vaults" in tables, "vaults table missing from schema"
        assert "modules" in tables, "modules table missing from schema"

    def test_safe_to_call_twice(self, tmp_path):
        db_path = tmp_path / "registry.db"
        conn = registry.init_registry_db(db_path)
        conn.close()
        # Should not raise — CREATE TABLE IF NOT EXISTS is the contract
        conn2 = registry.init_registry_db(db_path)
        tables = _table_names(conn2)
        conn2.close()
        assert tables == {"apparatuses", "vaults", "modules"}, (
            "Second init_registry_db call changed the table set — "
            "IF NOT EXISTS is not being used correctly"
        )

    def test_returns_open_connection(self, tmp_path):
        db_path = tmp_path / "registry.db"
        conn = registry.init_registry_db(db_path)
        assert isinstance(conn, sqlite3.Connection), (
            "init_registry_db should return an open sqlite3.Connection"
        )
        # Confirm it's actually open and usable
        conn.execute("SELECT 1")
        conn.close()


# ---------------------------------------------------------------------------
# init_apparatus_db
# ---------------------------------------------------------------------------

class TestInitApparatusDb:

    def test_creates_file_at_given_path(self, tmp_path):
        db_path = tmp_path / "writing.db"
        assert not db_path.exists()
        conn = registry.init_apparatus_db(db_path)
        conn.close()
        assert db_path.exists(), "apparatus DB was not created at the given path"

    def test_creates_all_required_tables(self, tmp_path):
        db_path = tmp_path / "writing.db"
        conn = registry.init_apparatus_db(db_path)
        tables = _table_names(conn)
        conn.close()
        required = {
            "authors", "publications", "works",
            "work_authors", "work_libraries", "work_relations", "changelogs",
        }
        missing = required - tables
        assert not missing, f"apparatus DB missing tables: {missing}"

    def test_safe_to_call_twice(self, tmp_path):
        db_path = tmp_path / "writing.db"
        conn = registry.init_apparatus_db(db_path)
        conn.close()
        conn2 = registry.init_apparatus_db(db_path)
        tables = _table_names(conn2)
        conn2.close()
        assert "works" in tables, (
            "Second init_apparatus_db call lost the works table"
        )

    def test_accepts_path_object(self, tmp_path):
        db_path = tmp_path / "explicit.db"
        conn = registry.init_apparatus_db(db_path)
        conn.close()
        assert db_path.exists()

    def test_accepts_string_apparatus_name(
        self,
        tmp_path,
        monkeypatch
    ):
        # Redirect the default path so we don't write to ~/.archivist/
        monkeypatch.setattr(registry, "get_archivist_home", lambda: tmp_path)
        conn = registry.init_apparatus_db("writing")
        conn.close()
        assert (tmp_path / "writing.db").exists()

    def test_returns_open_connection(self, tmp_path):
        db_path = tmp_path / "writing.db"
        conn = registry.init_apparatus_db(db_path)
        assert isinstance(conn, sqlite3.Connection)
        conn.execute("SELECT 1")
        conn.close()


# ---------------------------------------------------------------------------
# register_apparatus
# ---------------------------------------------------------------------------

class TestRegisterApparatus:

    def test_inserts_one_row(self, registry_db):
        conn = sqlite3.connect(registry_db)
        registry.register_apparatus(name="writing", conn=conn, db_path=registry_db.parent / "writing.db")
        count = _row_count(conn, "apparatuses")
        conn.close()
        assert count == 1, f"expected 1 apparatus row, got {count}"

    def test_returns_new_id(self, registry_db):
        conn = sqlite3.connect(registry_db)
        returned_id = registry.register_apparatus(
            name="writing", conn=conn, db_path=registry_db.parent / "writing.db"
        )
        db_id = conn.execute(
            "SELECT id FROM apparatuses WHERE name = ?", ("writing",)
        ).fetchone()[0]
        conn.close()
        assert returned_id == db_id, (
            f"register_apparatus returned id {returned_id} "
            f"but DB has id {db_id} — they must match"
        )

    def test_name_stored_verbatim(self, registry_db):
        conn = sqlite3.connect(registry_db)
        registry.register_apparatus(name="My Apparatus", conn=conn, db_path=registry_db.parent / "my.db")
        stored = conn.execute(
            "SELECT name FROM apparatuses WHERE name = ?", ("My Apparatus",)
        ).fetchone()[0]
        conn.close()
        assert stored == "My Apparatus", (
            f"apparatus name stored as {stored!r}, expected 'My Apparatus'"
        )

    def test_db_path_stored_verbatim(
        self,
        registry_db,
        tmp_path
    ):
        expected_path = tmp_path / "writing.db"
        conn = sqlite3.connect(registry_db)
        registry.register_apparatus(name="writing", conn=conn, db_path=expected_path)
        stored = conn.execute(
            "SELECT db_path FROM apparatuses WHERE name = ?", ("writing",)
        ).fetchone()[0]
        conn.close()
        assert stored == str(expected_path), (
            f"db_path stored as {stored!r}, expected {str(expected_path)!r}"
        )

    def test_duplicate_name_raises(self, registry_db):
        conn = sqlite3.connect(registry_db)
        registry.register_apparatus(name="writing", conn=conn, db_path=registry_db.parent / "writing.db")
        with pytest.raises(sqlite3.IntegrityError):
            registry.register_apparatus(
                name="writing", conn=conn, db_path=registry_db.parent / "writing2.db"
            )
        conn.close()


# ---------------------------------------------------------------------------
# register_vault
# ---------------------------------------------------------------------------

class TestRegisterVault:

    def _make_apparatus(self, registry_db: Path) -> tuple[sqlite3.Connection, int]:
        conn = sqlite3.connect(registry_db)
        apparatus_id = registry.register_apparatus(
            name="writing", conn=conn, db_path=registry_db.parent / "writing.db"
        )
        return conn, apparatus_id

    def test_inserts_one_row(
        self,
        registry_db,
        tmp_path
    ):
        conn, apparatus_id = self._make_apparatus(registry_db)
        registry.register_vault(apparatus_id=apparatus_id, name="fiction", path=tmp_path, conn=conn)
        count = _row_count(conn, "vaults")
        conn.close()
        assert count == 1, f"expected 1 vault row, got {count}"

    def test_returns_new_id(
        self,
        registry_db,
        tmp_path
    ):
        conn, apparatus_id = self._make_apparatus(registry_db)
        returned_id = registry.register_vault(
            apparatus_id=apparatus_id, name="fiction", path=tmp_path, conn=conn
        )
        db_id = conn.execute(
            "SELECT id FROM vaults WHERE name = ?", ("fiction",)
        ).fetchone()[0]
        conn.close()
        assert returned_id == db_id

    def test_apparatus_id_stored_correctly(
        self,
        registry_db,
        tmp_path
    ):
        conn, apparatus_id = self._make_apparatus(registry_db)
        vault_id = registry.register_vault(
            apparatus_id=apparatus_id, name="fiction", path=tmp_path, conn=conn
        )
        stored_apparatus_id = conn.execute(
            "SELECT apparatus_id FROM vaults WHERE id = ?", (vault_id,)
        ).fetchone()[0]
        conn.close()
        assert stored_apparatus_id == apparatus_id, (
            f"vault.apparatus_id is {stored_apparatus_id}, expected {apparatus_id}"
        )

    def test_vault_id_nullable_on_modules(
        self,
        registry_db,
        tmp_path
    ):
        conn, apparatus_id = self._make_apparatus(registry_db)
        # vault_id=None must not violate schema
        module_id = registry.register_module(
            apparatus_id=apparatus_id,
            vault_id=None,
            name="my-module",
            module_type="general",
            path=tmp_path,
            library_tag=None,
            conn=conn,
        )
        stored = conn.execute(
            "SELECT vault_id FROM modules WHERE id = ?", (module_id,)
        ).fetchone()[0]
        conn.close()
        assert stored is None, (
            f"vault_id should be NULL for vaultless modules, got {stored!r}"
        )


# ---------------------------------------------------------------------------
# register_module
# ---------------------------------------------------------------------------

class TestRegisterModule:

    def _make_apparatus(self, registry_db: Path) -> tuple[sqlite3.Connection, int]:
        conn = sqlite3.connect(registry_db)
        apparatus_id = registry.register_apparatus(
            name="writing", conn=conn, db_path=registry_db.parent / "writing.db"
        )
        return conn, apparatus_id

    def test_inserts_one_row(
        self,
        registry_db,
        tmp_path
    ):
        conn, apparatus_id = self._make_apparatus(registry_db)
        registry.register_module(
            apparatus_id=apparatus_id, vault_id=None, name="cosmic-horror",
            module_type="library", path=tmp_path, library_tag="cosmic-horror", conn=conn,
        )
        count = _row_count(conn, "modules")
        conn.close()
        assert count == 1, f"expected 1 module row, got {count}"

    def test_returns_new_id(
        self,
        registry_db,
        tmp_path
    ):
        conn, apparatus_id = self._make_apparatus(registry_db)
        returned_id = registry.register_module(
            apparatus_id=apparatus_id, vault_id=None, name="cosmic-horror",
            module_type="library", path=tmp_path, library_tag=None, conn=conn,
        )
        db_id = conn.execute(
            "SELECT id FROM modules WHERE name = ?", ("cosmic-horror",)
        ).fetchone()[0]
        conn.close()
        assert returned_id == db_id

    @pytest.mark.parametrize("module_type", [
        "library", "story", "publication", "vault", "general", "custom",
    ])

    def test_all_module_types_accepted(
        self,
        registry_db,
        tmp_path,
        module_type
    ):
        conn, apparatus_id = self._make_apparatus(registry_db)
        # Each parametrize call gets a fresh registry_db, so no collision
        registry.register_module(
            apparatus_id=apparatus_id, vault_id=None, name=f"mod-{module_type}",
            module_type=module_type, path=tmp_path, library_tag=None, conn=conn,
        )
        stored = conn.execute(
            "SELECT module_type FROM modules WHERE name = ?", (f"mod-{module_type}",)
        ).fetchone()[0]
        conn.close()
        assert stored == module_type, (
            f"module_type stored as {stored!r}, expected {module_type!r}"
        )

    def test_library_tag_stored_for_library_modules(
        self,
        registry_db,
        tmp_path
    ):
        conn, apparatus_id = self._make_apparatus(registry_db)
        registry.register_module(
            apparatus_id=apparatus_id, vault_id=None, name="cosmic-horror",
            module_type="library", path=tmp_path, library_tag="cosmic-horror", conn=conn,
        )
        stored = conn.execute(
            "SELECT library_tag FROM modules WHERE name = ?", ("cosmic-horror",)
        ).fetchone()[0]
        conn.close()
        assert stored == "cosmic-horror", (
            f"library_tag stored as {stored!r}, expected 'cosmic-horror'"
        )

    def test_library_tag_nullable_for_non_library_modules(
        self,
        registry_db,
        tmp_path
    ):
        conn, apparatus_id = self._make_apparatus(registry_db)
        registry.register_module(
            apparatus_id=apparatus_id, vault_id=None, name="my-story",
            module_type="story", path=tmp_path, library_tag=None, conn=conn,
        )
        stored = conn.execute(
            "SELECT library_tag FROM modules WHERE name = ?", ("my-story",)
        ).fetchone()[0]
        conn.close()
        assert stored is None, (
            f"library_tag should be NULL for non-library modules, got {stored!r}"
        )

    def test_vault_id_nullable(
        self,
        registry_db,
        tmp_path
    ):
        conn, apparatus_id = self._make_apparatus(registry_db)
        registry.register_module(
            apparatus_id=apparatus_id, vault_id=None, name="lone-module",
            module_type="general", path=tmp_path, library_tag=None, conn=conn,
        )
        stored = conn.execute(
            "SELECT vault_id FROM modules WHERE name = ?", ("lone-module",)
        ).fetchone()[0]
        conn.close()
        assert stored is None, (
            f"vault_id should be NULL for vaultless module, got {stored!r}"
        )

    def test_path_stored_as_absolute(
        self,
        registry_db,
        tmp_path
    ):
        conn, apparatus_id = self._make_apparatus(registry_db)
        registry.register_module(
            apparatus_id=apparatus_id, vault_id=None, name="my-module",
            module_type="general", path=tmp_path, library_tag=None, conn=conn,
        )
        stored = conn.execute(
            "SELECT path FROM modules WHERE name = ?", ("my-module",)
        ).fetchone()[0]
        conn.close()
        assert Path(stored).is_absolute(), (
            f"module path {stored!r} is not absolute — always store absolute paths"
        )


# ---------------------------------------------------------------------------
# get_apparatus_db_path
# ---------------------------------------------------------------------------

class TestGetApparatusDbPath:

    def test_returns_path_under_archivist_home(
        self,
        monkeypatch,
        tmp_path
    ):
        monkeypatch.setattr(registry, "get_archivist_home", lambda: tmp_path)
        result = registry.get_apparatus_db_path("writing")
        assert result.parent == tmp_path
        assert result.name == "writing.db"

    def test_name_lowercased_and_hyphenated(
        self,
        monkeypatch,
        tmp_path
    ):
        monkeypatch.setattr(registry, "get_archivist_home", lambda: tmp_path)
        result = registry.get_apparatus_db_path("My Fancy Apparatus")
        assert result.name == "my-fancy-apparatus.db", (
            f"expected 'my-fancy-apparatus.db', got {result.name!r} — "
            "spaces must become hyphens and the name must be lowercased"
        )

    def test_returns_none_for_unknown_name_does_not_raise(
        self,
        monkeypatch,
        tmp_path
    ):
        # get_apparatus_db_path returns a path regardless — it doesn't
        # check whether the file exists. That's the caller's job.
        monkeypatch.setattr(registry, "get_archivist_home", lambda: tmp_path)
        result = registry.get_apparatus_db_path("nonexistent-apparatus")
        # Just confirms it returns a Path and does not raise
        assert isinstance(result, Path)
        assert not result.exists()


# ---------------------------------------------------------------------------
# get_module_by_path
# ---------------------------------------------------------------------------

class TestGetModuleByPath:

    def _register_one(
        self,
        registry_db: Path,
        path: Path
    ) -> int:
        conn = sqlite3.connect(registry_db)
        apparatus_id = registry.register_apparatus(
            name="writing", conn=conn, db_path=registry_db.parent / "writing.db"
        )
        module_id = registry.register_module(
            apparatus_id=apparatus_id, vault_id=None, name="my-lib",
            module_type="library", path=path, library_tag="weird-fiction", conn=conn,
        )
        conn.close()
        return module_id

    def test_returns_correct_record_for_registered_path(
        self,
        registry_db,
        tmp_path
    ):
        module_path = tmp_path / "my-lib"
        module_path.mkdir()
        expected_id = self._register_one(registry_db, module_path)

        conn = sqlite3.connect(registry_db)
        result = registry.get_module_by_path(module_path, conn)
        conn.close()

        assert result is not None, "get_module_by_path returned None for a registered path"
        assert result["id"] == expected_id
        assert result["module_type"] == "library"
        assert result["library_tag"] == "weird-fiction"

    def test_returns_none_for_unregistered_path(
        self,
        registry_db,
        tmp_path
    ):
        ghost = tmp_path / "i-was-never-here"
        conn = sqlite3.connect(registry_db)
        result = registry.get_module_by_path(ghost, conn)
        conn.close()
        assert result is None, (
            "get_module_by_path returned something for a path that was never registered"
        )

    def test_does_not_raise_for_unregistered_path(
        self,
        registry_db,
        tmp_path
    ):
        conn = sqlite3.connect(registry_db)
        # Must return None, not raise
        try:
            registry.get_module_by_path(tmp_path / "ghost", conn)
        except Exception as e:
            pytest.fail(f"get_module_by_path raised unexpectedly: {e}")
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# get_or_create_apparatus
# ---------------------------------------------------------------------------

class TestGetOrCreateApparatus:

    def test_creates_when_absent(self, registry_db):
        conn = sqlite3.connect(registry_db)
        apparatus_id, created = registry.get_or_create_apparatus(
            "writing", conn, db_path=registry_db.parent / "writing.db"
        )
        conn.close()
        assert created is True
        assert isinstance(apparatus_id, int)

    def test_returns_existing_when_present(self, registry_db):
        conn = sqlite3.connect(registry_db)
        first_id, _ = registry.get_or_create_apparatus(
            "writing", conn, db_path=registry_db.parent / "writing.db"
        )
        second_id, created = registry.get_or_create_apparatus(
            "writing", conn, db_path=registry_db.parent / "writing.db"
        )
        count = _row_count(conn, "apparatuses")
        conn.close()
        assert created is False, "get_or_create_apparatus should return was_created=False for existing"
        assert first_id == second_id, (
            f"IDs differ on second call: {first_id} vs {second_id} — "
            "get_or_create must return the existing id, not insert a duplicate"
        )
        assert count == 1, f"expected 1 row after two calls with same name, got {count}"


# ---------------------------------------------------------------------------
# get_or_create_vault
# ---------------------------------------------------------------------------

class TestGetOrCreateVault:

    def _make_apparatus(self, registry_db: Path) -> tuple[sqlite3.Connection, int]:
        conn = sqlite3.connect(registry_db)
        apparatus_id = registry.register_apparatus(
            name="writing", conn=conn, db_path=registry_db.parent / "writing.db"
        )
        return conn, apparatus_id

    def test_creates_when_absent(
        self,
        registry_db,
        tmp_path
    ):
        conn, apparatus_id = self._make_apparatus(registry_db)
        vault_id, created = registry.get_or_create_vault(apparatus_id, "fiction", tmp_path, conn)
        conn.close()
        assert created is True
        assert isinstance(vault_id, int)

    def test_returns_existing_when_present(
        self,
        registry_db,
        tmp_path
    ):
        conn, apparatus_id = self._make_apparatus(registry_db)
        first_id, _ = registry.get_or_create_vault(apparatus_id, "fiction", tmp_path, conn)
        second_id, created = registry.get_or_create_vault(apparatus_id, "fiction", tmp_path, conn)
        count = _row_count(conn, "vaults")
        conn.close()
        assert created is False
        assert first_id == second_id, (
            f"vault IDs differ on second call: {first_id} vs {second_id}"
        )
        assert count == 1, f"expected 1 vault row, got {count}"


# ---------------------------------------------------------------------------
# is_module_registered
# ---------------------------------------------------------------------------

class TestIsModuleRegistered:

    def test_returns_false_when_registry_absent(
        self,
        tmp_path,
        monkeypatch
    ):
        monkeypatch.setattr(registry, "get_registry_path", lambda: tmp_path / "nope.db")
        assert registry.is_module_registered(tmp_path) is False, (
            "is_module_registered should return False when registry.db doesn't exist"
        )

    def test_returns_true_for_registered_path(
        self,
        registry_db,
        tmp_path,
        monkeypatch
    ):
        monkeypatch.setattr(registry, "get_registry_path", lambda: registry_db)

        module_path = tmp_path / "my-module"
        module_path.mkdir()

        conn = sqlite3.connect(registry_db)
        registry.register_apparatus(
            name="writing", conn=conn, db_path=registry_db.parent / "writing.db"
        )
        apparatus_id = conn.execute(
            "SELECT id FROM apparatuses WHERE name = ?", ("writing",)
        ).fetchone()[0]
        registry.register_module(
            apparatus_id=apparatus_id, vault_id=None, name="my-module",
            module_type="general", path=module_path, library_tag=None, conn=conn,
        )
        conn.close()

        assert registry.is_module_registered(module_path) is True

    def test_returns_false_for_unregistered_path(
        self,
        registry_db,
        tmp_path,
        monkeypatch
    ):
        monkeypatch.setattr(registry, "get_registry_path", lambda: registry_db)
        ghost = tmp_path / "was-never-here"
        assert registry.is_module_registered(ghost) is False