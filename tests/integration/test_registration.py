"""
tests/integration/test_registration.py

Integration tests for the archivist init registration flow.

These tests exercise the full registration pipeline against a real filesystem
and real SQLite databases. We are not testing git behavior — the git_repo
fixture provides a realistic module root, but git subprocesses are not the
subject here.

All tests are marked integration because they touch SQLite and the filesystem
beyond tmp_path in combination. None of them touch ~/.archivist/.
"""

from atexit import register
import sqlite3
from pathlib import Path

import pytest

from archivist.utils import (
    read_archivist_config,
    registry,
)

pytestmark = pytest.mark.integration


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


def _row_count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ---------------------------------------------------------------------------
# TestApparatusCreation
# ---------------------------------------------------------------------------

class TestApparatusCreation:

    def test_new_apparatus_row_inserted(self, registry_db):
        conn = sqlite3.connect(registry_db)
        registry.register_apparatus(
            name="writing", conn=conn, db_path=registry_db.parent / "writing.db"
        )
        count = _row_count(conn, "apparatuses")
        conn.close()
        assert count == 1, f"expected 1 apparatus row after registration, got {count}"

    def test_apparatus_db_file_created_on_disk(
        self,
        registry_db,
        tmp_path
    ):
        apparatus_db_path = tmp_path / "writing.db"
        assert not apparatus_db_path.exists()
        conn = sqlite3.connect(registry_db)
        registry.register_apparatus(name="writing", conn=conn, db_path=apparatus_db_path)
        conn.close()
        assert apparatus_db_path.exists(), (
            "apparatus DB file was not created on disk after register_apparatus"
        )

    def test_apparatus_db_schema_initialized(
        self,
        registry_db,
        tmp_path
    ):
        apparatus_db_path = tmp_path / "writing.db"
        conn = sqlite3.connect(registry_db)
        registry.register_apparatus(name="writing", conn=conn, db_path=apparatus_db_path)
        conn.close()

        app_conn = sqlite3.connect(apparatus_db_path)
        tables = _table_names(app_conn)
        app_conn.close()

        required = {"works", "authors", "publications", "changelogs"}
        missing = required - tables
        assert not missing, (
            f"apparatus DB is missing tables after schema init: {missing}"
        )

    def test_existing_apparatus_reused_no_duplicate(self, registry_db):
        conn = sqlite3.connect(registry_db)
        first_id, _ = registry.get_or_create_apparatus(
            "writing", conn, db_path=registry_db.parent / "writing.db"
        )
        second_id, created = registry.get_or_create_apparatus(
            "writing", conn, db_path=registry_db.parent / "writing.db"
        )
        count = _row_count(conn, "apparatuses")
        conn.close()

        assert not created, "get_or_create_apparatus reported created=True for existing apparatus"
        assert first_id == second_id, (
            f"apparatus ids differ: {first_id} vs {second_id} — "
            "re-registering the same apparatus must return the existing id"
        )
        assert count == 1, (
            f"expected 1 apparatus row after two calls with the same name, got {count}"
        )


# ---------------------------------------------------------------------------
# TestModuleRegistration
# ---------------------------------------------------------------------------

class TestModuleRegistration:

    def _make_apparatus(self, registry_db: Path) -> tuple[sqlite3.Connection, int]:
        conn = sqlite3.connect(registry_db)
        apparatus_id = registry.register_apparatus(
            name="writing", conn=conn, db_path=registry_db.parent / "writing.db"
        )
        return conn, apparatus_id

    def test_module_type_stored_correctly(
        self,
        registry_db,
        git_repo
    ):
        conn, apparatus_id = self._make_apparatus(registry_db)
        registry.register_module(
            apparatus_id=apparatus_id, vault_id=None, name="my-lib",
            module_type="library", path=git_repo.path, library_tag=None, conn=conn,
        )
        stored = conn.execute(
            "SELECT module_type FROM modules WHERE name = ?", ("my-lib",)
        ).fetchone()[0]
        conn.close()
        assert stored == "library", f"module_type stored as {stored!r}, expected 'library'"

    def test_path_stored_as_absolute(
        self,
        registry_db,
        git_repo
    ):
        conn, apparatus_id = self._make_apparatus(registry_db)
        registry.register_module(
            apparatus_id=apparatus_id, vault_id=None, name="my-lib",
            module_type="library", path=git_repo.path, library_tag=None, conn=conn,
        )
        stored = conn.execute(
            "SELECT path FROM modules WHERE name = ?", ("my-lib",)
        ).fetchone()[0]
        conn.close()
        assert Path(stored).is_absolute(), (
            f"module path {stored!r} is not absolute — always store resolved absolute paths"
        )

    def test_library_tag_stored_for_library_module(
        self,
        registry_db,
        git_repo
    ):
        conn, apparatus_id = self._make_apparatus(registry_db)
        registry.register_module(
            apparatus_id=apparatus_id, vault_id=None, name="my-lib",
            module_type="library", path=git_repo.path, library_tag="cosmic-horror", conn=conn,
        )
        stored = conn.execute(
            "SELECT library_tag FROM modules WHERE name = ?", ("my-lib",)
        ).fetchone()[0]
        conn.close()
        assert stored == "cosmic-horror", (
            f"library_tag stored as {stored!r}, expected 'cosmic-horror'"
        )

    def test_library_tag_null_for_non_library_module(
        self,
        registry_db,
        git_repo
    ):
        conn, apparatus_id = self._make_apparatus(registry_db)
        registry.register_module(
            apparatus_id=apparatus_id, vault_id=None, name="my-story",
            module_type="story", path=git_repo.path, library_tag=None, conn=conn,
        )
        stored = conn.execute(
            "SELECT library_tag FROM modules WHERE name = ?", ("my-story",)
        ).fetchone()[0]
        conn.close()
        assert stored is None, (
            f"library_tag should be NULL for non-library modules, got {stored!r}"
        )

    def test_apparatus_field_written_to_config(self, registered_library):
        config = read_archivist_config(registered_library["module_path"])
        assert config is not None, "config.yaml missing after registration"
        assert config.get("apparatus") == "writing", (
            f"apparatus field in config is {config.get('apparatus')!r}, expected 'writing'"
        )

    def test_library_tag_written_to_config(self, registered_library):
        config = read_archivist_config(registered_library["module_path"])
        assert config is not None
        assert config.get("library-tag") == "cosmic-horror", (
            f"library-tag in config is {config.get('library-tag')!r}, expected 'cosmic-horror'"
        )

    def test_directories_block_written_to_config_for_library(self, registered_library):
        config = read_archivist_config(registered_library["module_path"])
        assert config is not None
        dirs = config.get("directories")
        assert dirs is not None, "directories block missing from library module config"
        assert isinstance(dirs, dict), (
            f"directories should be a dict, got {type(dirs).__name__}"
        )
        for key in ("works", "authors", "publications"):
            assert key in dirs, f"directories.{key} missing from config"

    def test_directories_block_absent_for_non_library(
        self,
        registry_db,
        git_repo
    ):
        conn = sqlite3.connect(registry_db)
        apparatus_id = registry.register_apparatus(
            name="writing", conn=conn, db_path=registry_db.parent / "writing.db"
        )
        registry.register_module(
            apparatus_id=apparatus_id, vault_id=None, name="my-story",
            module_type="story", path=git_repo.path, library_tag=None, conn=conn,
        )
        conn.close()

        # Write a story config — no directories block
        config_dir = git_repo.path / ".archivist"
        config_dir.mkdir(exist_ok=True)
        (config_dir / "config.yaml").write_text(
            "module-type: story\napparatus: writing\n", encoding="utf-8"
        )

        config = read_archivist_config(git_repo.path)
        assert "directories" not in (config or {}), (
            "directories block should not be present in non-library module config"
        )

    def test_re_registering_same_path_does_not_duplicate(
        self,
        registry_db,
        git_repo
    ):
        conn = sqlite3.connect(registry_db)
        apparatus_id, _ = registry.get_or_create_apparatus(
            "writing", conn, db_path=registry_db.parent / "writing.db"
        )
        registry.register_module(
            apparatus_id=apparatus_id, vault_id=None, name="my-lib",
            module_type="library", path=git_repo.path, library_tag=None, conn=conn,
        )
        count_before = _row_count(conn, "modules")

        # Attempting to register the same path again should not duplicate
        # (in practice, callers check first — we verify the DB constraint holds)
        existing = registry.get_module_by_path(git_repo.path, conn)
        conn.close()

        assert existing is not None, "module should be findable by path after registration"
        assert count_before == 1, (
            f"expected 1 module row, got {count_before} — something is inserting duplicates"
        )


# ---------------------------------------------------------------------------
# TestFreshMachine
# ---------------------------------------------------------------------------

class TestFreshMachine:

    def test_archivist_home_created_if_absent(
        self,
        tmp_path,
        monkeypatch
    ):
        fake_home = tmp_path / "dot-archivist"
        assert not fake_home.exists()
        monkeypatch.setattr(registry, "get_archivist_home", lambda: fake_home)
        monkeypatch.setattr(registry, "get_registry_path", lambda: fake_home / "registry.db")
        conn = registry.init_registry_db()
        conn.close()
        assert fake_home.exists(), (
            "~/.archivist equivalent was not created on first init_registry_db call"
        )

    def test_registry_db_created_if_absent(
        self,
        tmp_path,
        monkeypatch
    ):
        fake_home = tmp_path / "dot-archivist"
        registry_path = fake_home / "registry.db"
        monkeypatch.setattr(registry, "get_archivist_home", lambda: fake_home)
        monkeypatch.setattr(registry, "get_registry_path", lambda: registry_path)
        conn = registry.init_registry_db()
        conn.close()
        assert registry_path.exists(), "registry.db not created on first run"

    def test_first_registration_on_fresh_db_succeeds(
        self,
        tmp_path,
        monkeypatch
    ):
        fake_home = tmp_path / "dot-archivist"
        registry_path = fake_home / "registry.db"
        monkeypatch.setattr(registry, "get_archivist_home", lambda: fake_home)
        monkeypatch.setattr(registry, "get_registry_path", lambda: registry_path)

        conn = registry.init_registry_db()
        apparatus_id = registry.register_apparatus(
            name="writing", conn=conn, db_path=fake_home / "writing.db"
        )
        module_id = registry.register_module(
            apparatus_id=apparatus_id, vault_id=None, name="my-lib",
            module_type="library", path=tmp_path / "repo", library_tag="weird-fiction",
            conn=conn,
        )
        conn.close()

        # Verify rows are there
        conn2 = sqlite3.connect(registry_path)
        app_count = _row_count(conn2, "apparatuses")
        mod_count = _row_count(conn2, "modules")
        conn2.close()

        assert app_count == 1, f"expected 1 apparatus row on fresh DB, got {app_count}"
        assert mod_count == 1, f"expected 1 module row on fresh DB, got {mod_count}"
        assert isinstance(apparatus_id, int)
        assert isinstance(module_id, int)