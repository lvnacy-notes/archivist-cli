"""
tests/unit/test_registry.py

Unit tests for archivist.utils.registry — the machine-level Apparatus registry.

The cardinal rule here, above all others:

    THOU SHALT NOT TOUCH ~/.archivist/

Every test in this module runs against a fake registry dir inside tmp_path.
The autouse `isolated_registry` fixture patches get_registry_dir() before
anything else runs. If you break that fixture, every test here becomes a
machine-contaminating disaster. Don't.

No mocked subprocess calls for git operations — init_registry() runs a real
`git init` in the tmp_path registry dir. If git isn't on PATH, those tests
fail loudly, which is correct behavior.
"""

import re
import sqlite3
import sys
from pathlib import Path

import pytest

import archivist.utils.registry as registry_module
from archivist.utils import (
    APPARATUS_MODULE_TYPES,
    add_module_to_apparatus,
    add_module_to_bay,
    decimate_module,
    get_apparatus_by_name,
    get_apparatus_connection,
    get_apparatus_db_path,
    get_apparatus_modules,
    get_bay_modules,
    get_module_apparati,
    get_module_bays,
    get_module_by_path,
    get_module_by_uuid,
    get_registry_connection,
    get_registry_dir,
    get_registry_path,
    get_vault_modules,
    init_apparatus_db,
    init_registry,
    is_module_registered,
    list_apparatus_names,
    prompt_apparatus_names,
    reactivate_module,
    register_apparatus,
    register_module,
    remove_all_apparatus_memberships,
    remove_all_bays_for_contained,
    remove_module_from_apparatus,
    remove_module_from_bay,
    update_module_sync,
    validate_slug,
)


# ===========================================================================
# Isolation — this fixture is the entire reason this suite doesn't destroy
# your machine. autouse=True means it fires for every single test here.
# ===========================================================================

@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    """
    Redirect get_registry_dir() to a throwaway path inside tmp_path.

    Every function in registry.py derives its paths from get_registry_dir().
    Patching this one function guarantees no test ever touches ~/.archivist/.
    This is autouse. It is not optional. Do not remove it.
    """
    fake_registry_dir = tmp_path / ".archivist"
    # Patch in the registry module (for internal calls within registry.py functions)
    monkeypatch.setattr(registry_module, "get_registry_dir", lambda: fake_registry_dir)
    # Also patch in the test module's namespace (for direct calls in test assertions)

    test_module = sys.modules[__name__]
    monkeypatch.setattr(test_module, "get_registry_dir", lambda: fake_registry_dir)
    return fake_registry_dir


# ===========================================================================
# Helpers shared across test classes
# ===========================================================================

def _bootstrap(tmp_path: Path, apparatus_name: str = "writing") -> tuple[str, Path]:
    """
    Initialise the registry and register an apparatus. Returns (apparatus_uuid, module_path).
    Call this at the top of any test that needs an apparatus to exist before
    doing module work. Saves eight lines of boilerplate per test.
    """
    init_registry()
    apparatus_uuid = register_apparatus(apparatus_name, git_remote = None)
    module_path = tmp_path / "modules" / "some-module"
    module_path.mkdir(parents = True)
    return apparatus_uuid, module_path


def _register_a_module(
    apparatus_name: str,
    module_path: Path,
    name: str = "some-module",
    module_type: str = "library",
) -> str:
    """Register a module and return its UUID. Thin wrapper for DRY test setup."""
    return register_module(
        apparatus_name = apparatus_name,
        name = name,
        module_type = module_type,
        path = module_path,
        git_remote = None,
    )


# ===========================================================================
# validate_slug
# ===========================================================================

class TestValidateSlug:
    """
    Slug validation gates every apparatus name and is the first line of defense
    against filesystem-hostile garbage in ~/.archivist/. Get this wrong and
    someone ends up with a database named `My Writing!!!.db`.
    """

    def test_valid_simple_name_passes(self):
        validate_slug("writing")  # should not raise

    def test_valid_name_with_hyphens_passes(self):
        validate_slug("cosmic-horror")

    def test_valid_name_with_numbers_passes(self):
        validate_slug("module42")

    def test_uppercase_raises(self):
        with pytest.raises(ValueError, match="Lowercase"):
            validate_slug("Writing")

    def test_spaces_raise(self):
        with pytest.raises(ValueError):
            validate_slug("my writing")

    def test_leading_hyphen_raises(self):
        """Leading hyphen looks like a flag. Reject it."""
        with pytest.raises(ValueError):
            validate_slug("-bad-name")

    def test_special_characters_raise(self):
        with pytest.raises(ValueError):
            validate_slug("my_apparatus!")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            validate_slug("")

    def test_label_appears_in_error_message(self):
        """The label parameter makes error messages actually useful."""
        with pytest.raises(ValueError, match="apparatus name"):
            validate_slug("Bad Name", label="apparatus name")


# ===========================================================================
# Path resolution
# ===========================================================================

class TestPathResolution:
    """
    These three functions are the foundation everything else stands on.
    If get_registry_dir() returns the wrong thing, every single path in the
    system is wrong. Pin them hard.
    """

    def test_get_registry_dir_returns_path_object(self):
        result = get_registry_dir()
        assert isinstance(result, Path)

    def test_get_registry_dir_ends_with_archivist(self):
        """
        The monkeypatched version returns tmp_path / ".archivist".
        The real one returns Path.home() / ".archivist".
        Either way, the last component is ".archivist".
        """
        result = get_registry_dir()
        assert result.name == ".archivist"

    def test_get_registry_path_is_registry_db_inside_dir(self):
        result = get_registry_path()
        assert result == get_registry_dir() / "registry.db"

    def test_get_registry_path_returns_path_object(self):
        assert isinstance(get_registry_path(), Path)

    def test_get_apparatus_db_path_uses_name_as_filename(self):
        result = get_apparatus_db_path("writing")
        assert result == get_registry_dir() / "writing.db"

    def test_get_apparatus_db_path_for_different_names(self):
        assert get_apparatus_db_path("cyber") == get_registry_dir() / "cyber.db"
        assert get_apparatus_db_path("fiction") == get_registry_dir() / "fiction.db"


# ===========================================================================
# init_registry
# ===========================================================================

class TestInitRegistry:
    """
    Three states. All must work. None of them should destroy data or raise
    on a clean re-run. The spec is clear: idempotent, idempotent, idempotent.
    """

    def test_creates_registry_directory(self):
        init_registry()
        assert get_registry_dir().is_dir(), (
            "init_registry() didn't create the registry directory. "
            "Everything downstream is fucked."
        )

    def test_creates_registry_db(self):
        init_registry()
        assert get_registry_path().exists(), (
            "init_registry() ran but registry.db doesn't exist. "
            "There's no registry without the DB."
        )

    def test_creates_apparati_table(self):
        init_registry()
        conn = get_registry_connection()
        try:
            result = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='apparati'"
            ).fetchone()
            assert result is not None, "apparati table wasn't created — where the fuck is it"
        finally:
            conn.close()

    def test_creates_module_apparatus_table(self):
        init_registry()
        conn = get_registry_connection()
        try:
            result = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='module_apparatus'"
            ).fetchone()
            assert result is not None, (
                "module_apparatus junction table wasn't created. "
                "Multi-apparatus membership is entirely dead without it."
            )
        finally:
            conn.close()

    def test_creates_modules_table(self):
        init_registry()
        conn = get_registry_connection()
        try:
            result = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='modules'"
            ).fetchone()
            assert result is not None, "modules table wasn't created"
        finally:
            conn.close()

    def test_creates_module_bays_table(self):
        init_registry()
        conn = get_registry_connection()
        try:
            result = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='module_bays'"
            ).fetchone()
            assert result is not None, "module_bays table wasn't created"
        finally:
            conn.close()

    def test_creates_git_repo_in_registry_dir(self):
        init_registry()
        assert (get_registry_dir() / ".git").exists(), (
            "init_registry() didn't run git init. "
            "The registry won't be version-controlled."
        )

    def test_is_idempotent_called_twice(self):
        """The whole point. Must not raise, must not corrupt."""
        init_registry()
        init_registry()  # second call — if this raises, we have a problem
        assert get_registry_path().exists()

    def test_idempotent_preserves_existing_data(self, tmp_path):
        """
        Calling init_registry() on an existing registry must not wipe its data.
        This is not academic — archivist init calls this on every new module setup,
        and a machine that already has a registry should not have its data nuked.
        """
        init_registry()
        apparatus_uuid = register_apparatus("writing", git_remote=None)

        init_registry()  # should be a no-op for the existing schema + data

        result = get_apparatus_by_name("writing")
        assert result is not None, (
            "init_registry() on an existing registry wiped the data. "
            "That is catastrophic. Fix it now."
        )
        assert result["uuid"] == apparatus_uuid

    def test_handles_existing_dir_without_git(self, tmp_path):
        """
        Registry dir exists but isn't a git repo yet — e.g. someone manually
        created ~/.archivist/ but never ran init. Should handle it cleanly.
        """
        get_registry_dir().mkdir(parents=True, exist_ok=True)
        init_registry()  # should not raise
        assert (get_registry_dir() / ".git").exists()

    def test_handles_existing_dir_with_existing_db(self, tmp_path):
        """
        Registry dir and DB both exist — the pure no-op case.
        Should not touch the DB or raise.
        """
        init_registry()
        register_apparatus("writing", git_remote=None)
        init_registry()
        # Data still there
        assert get_apparatus_by_name("writing") is not None


# ===========================================================================
# init_apparatus_db
# ===========================================================================

class TestInitApparatusDb:
    """
    One apparatus DB per apparatus name. Must create, must be idempotent,
    must reject garbage names before they become garbage filenames.
    """

    def test_creates_apparatus_db_file(self):
        init_registry()
        init_apparatus_db("writing")
        assert get_apparatus_db_path("writing").exists()

    def test_creates_all_apparatus_tables(self):
        init_registry()
        init_apparatus_db("writing")
        conn = get_apparatus_connection("writing")
        try:
            tables = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "changelogs" in tables, "changelogs table missing from apparatus DB"
            assert "works" in tables, "works table missing from apparatus DB"
            assert "authors" in tables, "authors table missing from apparatus DB"
            assert "works_authors" in tables, "works_authors table missing from apparatus DB"
        finally:
            conn.close()

    def test_is_idempotent(self):
        init_registry()
        init_apparatus_db("writing")
        init_apparatus_db("writing")  # must not raise or corrupt

    def test_rejects_invalid_name(self):
        init_registry()
        with pytest.raises(ValueError):
            init_apparatus_db("My Writing!!!")

    def test_rejects_uppercase_name(self):
        init_registry()
        with pytest.raises(ValueError):
            init_apparatus_db("Writing")


# ===========================================================================
# Connection management — FK enforcement
# ===========================================================================

class TestConnectionFKEnforcement:
    """
    FK enforcement is OFF by default in SQLite. Our _open_connection() turns it ON.
    If this is broken, orphaned rows accumulate silently and produce bugs that
    are an absolute nightmare to trace back to their source.

    The test: attempt a FK violation and confirm it raises IntegrityError.
    If it doesn't, the PRAGMA isn't firing.
    """

    def test_registry_connection_enforces_foreign_keys(self):
        """
        module_apparatus.module_uuid is a FK to modules.uuid. Inserting a
        membership row with a nonexistent module_uuid must raise IntegrityError.
        If it doesn't, the PRAGMA isn't firing and orphaned rows will accumulate
        until something breaks in a way that's a complete fucking nightmare to trace.
        """
        init_registry()
        # Seed a real apparatus so the apparatus FK side is valid — we want
        # to test the module FK, not both at once.
        apparatus_uuid = register_apparatus("writing", git_remote=None)
        conn = get_registry_connection()
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO module_apparatus (module_uuid, apparatus_uuid)
                    VALUES ('nonexistent-module-uuid', ?)
                    """,
                    (apparatus_uuid,),
                )
                conn.commit()
        finally:
            conn.close()

    def test_registry_connection_returns_sqlite_connection(self):
        init_registry()
        conn = get_registry_connection()
        try:
            assert isinstance(conn, sqlite3.Connection)
        finally:
            conn.close()

    def test_apparatus_connection_returns_sqlite_connection(self):
        init_registry()
        init_apparatus_db("writing")
        conn = get_apparatus_connection("writing")
        try:
            assert isinstance(conn, sqlite3.Connection)
        finally:
            conn.close()

    def test_apparatus_connection_enforces_foreign_keys(self):
        """
        works_authors has FKs to both works and authors. Inserting a row with
        a nonexistent work_uuid must raise IntegrityError.
        """
        init_registry()
        init_apparatus_db("writing")
        conn = get_apparatus_connection("writing")
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO works_authors (work_uuid, author_uuid)
                    VALUES ('ghost-work-uuid', 'ghost-author-uuid')
                    """
                )
                conn.commit()
        finally:
            conn.close()


# ===========================================================================
# register_apparatus / get_apparatus_by_name
# ===========================================================================

class TestRegisterApparatus:
    """
    Apparatus registration is an upsert. First call creates. Second call
    with the same name returns the existing UUID without creating a duplicate.
    The apparatus DB must also be created as a side effect.
    """

    def test_creates_apparatus_row(self, tmp_path):
        _bootstrap(tmp_path, "writing")
        result = get_apparatus_by_name("writing")
        assert result is not None, "register_apparatus() didn't create a row"

    def test_returns_uuid_string(self, tmp_path):
        apparatus_uuid, _ = _bootstrap(tmp_path, "writing")
        assert isinstance(apparatus_uuid, str)
        assert len(apparatus_uuid) > 0

    def test_creates_apparatus_db_as_side_effect(self, tmp_path):
        _bootstrap(tmp_path, "writing")
        assert get_apparatus_db_path("writing").exists(), (
            "register_apparatus() didn't create the apparatus DB. "
            "Every apparatus needs its own DB."
        )

    def test_upsert_same_name_returns_same_uuid(self, tmp_path):
        init_registry()
        uuid_first = register_apparatus("writing", git_remote=None)
        uuid_second = register_apparatus("writing", git_remote=None)
        assert uuid_first == uuid_second, (
            f"register_apparatus() created a duplicate row on second call. "
            f"First UUID: {uuid_first!r}, second: {uuid_second!r}."
        )

    def test_upsert_does_not_create_duplicate_rows(self, tmp_path):
        init_registry()
        register_apparatus("writing", git_remote=None)
        register_apparatus("writing", git_remote=None)
        conn = get_registry_connection()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM apparati WHERE name = 'writing'"
            ).fetchone()[0]
            assert count == 1, f"Expected 1 row, got {count}. Duplicate apparatus rows are a bug."
        finally:
            conn.close()

    def test_stores_git_remote_when_provided(self, tmp_path):
        init_registry()
        register_apparatus("writing", git_remote="git@github.com:user/writing.git")
        result = get_apparatus_by_name("writing")
        assert result is not None, "register_apparatus() didn't write a row. Nothing to check."
        assert result["git_remote"] == "git@github.com:user/writing.git"

    def test_git_remote_is_null_when_not_provided(self, tmp_path):
        init_registry()
        register_apparatus("writing", git_remote=None)
        result = get_apparatus_by_name("writing")
        assert result is not None, "register_apparatus() didn't write a row. Nothing to check."
        assert result["git_remote"] is None

    def test_rejects_invalid_apparatus_name(self, tmp_path):
        init_registry()
        with pytest.raises(ValueError):
            register_apparatus("My Writing!!!!", git_remote=None)

    def test_multiple_apparatuses_coexist(self, tmp_path):
        init_registry()
        uuid_a = register_apparatus("writing", git_remote=None)
        uuid_b = register_apparatus("cyber", git_remote=None)
        assert uuid_a != uuid_b
        assert get_apparatus_by_name("writing") is not None
        assert get_apparatus_by_name("cyber") is not None


class TestGetApparatusByName:
    def test_returns_dict_for_existing_apparatus(self, tmp_path):
        _bootstrap(tmp_path, "writing")
        result = get_apparatus_by_name("writing")
        assert isinstance(result, dict)

    def test_returns_none_for_missing_apparatus(self, tmp_path):
        init_registry()
        result = get_apparatus_by_name("does-not-exist")
        assert result is None

    def test_returned_dict_has_expected_keys(self, tmp_path):
        _bootstrap(tmp_path, "writing")
        result = get_apparatus_by_name("writing")
        assert result is not None, "register_apparatus() didn't create a row. Nothing to inspect."
        assert "uuid" in result
        assert "name" in result
        assert "db_path" in result
        assert "created_at" in result


# ===========================================================================
# register_module
# ===========================================================================

class TestRegisterModule:
    """
    register_module() is the core of Phase 1. Get this wrong and the entire
    module lifecycle — add, deinit, muster, sync — breaks in ways that
    produce silent corrupted state instead of clear errors.
    """

    def test_creates_module_row(self, tmp_path):
        _, module_path = _bootstrap(tmp_path)
        uuid = _register_a_module("writing", module_path)
        result = get_module_by_uuid(uuid)
        assert result is not None, "register_module() didn't create a row"

    def test_returns_uuid_string(self, tmp_path):
        _, module_path = _bootstrap(tmp_path)
        uuid = _register_a_module("writing", module_path)
        assert isinstance(uuid, str)
        assert len(uuid) > 0

    def test_stored_path_is_absolute(self, tmp_path):
        """
        Paths must be stored absolute. A relative path in the registry is
        machine-state-dependent and will produce wrong results from any
        working directory other than the one it was written from.
        """
        _, module_path = _bootstrap(tmp_path)
        uuid = _register_a_module("writing", module_path)
        result = get_module_by_uuid(uuid)
        assert result is not None, "register_module() didn't create a row. Nothing to inspect."
        assert Path(result["path"]).is_absolute(), (
            f"Registered path is not absolute: {result['path']!r}. "
            "Relative paths in the registry are a ticking time bomb."
        )

    def test_raises_on_invalid_module_type(self, tmp_path):
        _, module_path = _bootstrap(tmp_path)
        with pytest.raises(ValueError, match="module_type"):
            register_module(
                apparatus_name="writing",
                name="bad-module",
                module_type="nonsense",
                path=module_path,
                git_remote=None,
            )

    def test_raises_before_writing_on_invalid_module_type(self, tmp_path):
        """
        The ValueError must fire before any DB write. If it fires after,
        we might have a half-written row and no clean error to show for it.
        """
        _, module_path = _bootstrap(tmp_path)
        with pytest.raises(ValueError):
            register_module(
                apparatus_name="writing",
                name="bad-module",
                module_type="garbage-type",
                path=module_path,
                git_remote=None,
            )
        # Confirm nothing was written
        result = get_module_by_path(module_path)
        assert result is None, (
            "register_module() wrote a row before raising ValueError. "
            "Validation must happen first."
        )

    def test_raises_if_apparatus_not_registered(self, tmp_path):
        init_registry()
        module_path = tmp_path / "modules" / "orphan"
        module_path.mkdir(parents=True)
        with pytest.raises(ValueError, match="Apparatus"):
            register_module(
                apparatus_name="nonexistent-apparatus",
                name="orphan",
                module_type="library",
                path=module_path,
                git_remote=None,
            )

    def test_all_known_module_types_are_accepted(self, tmp_path):
        """Every value in APPARATUS_MODULE_TYPES must be registerable."""
        init_registry()
        register_apparatus("writing", git_remote=None)
        for i, module_type in enumerate(APPARATUS_MODULE_TYPES):
            p = tmp_path / "modules" / f"module-{i}"
            p.mkdir(parents=True)
            uuid = register_module(
                apparatus_name="writing",
                name=f"module-{i}",
                module_type=module_type,
                path=p,
                git_remote=None,
            )
            result = get_module_by_uuid(uuid)
            assert result is not None, (
                f"register_module() returned a UUID for '{module_type}' but the row doesn't exist. "
                "Something is catastrophically wrong with the write path."
            )
            assert result["module_type"] == module_type, (
                f"module_type '{module_type}' not stored correctly. "
                "APPARATUS_MODULE_TYPES and the schema are out of sync."
            )

    def test_upsert_same_path_returns_same_uuid(self, tmp_path):
        """
        Re-registering a module at the same path must return the existing UUID,
        not create a new row. This is the add-after-clone flow.
        """
        _, module_path = _bootstrap(tmp_path)
        uuid_first = _register_a_module("writing", module_path)
        uuid_second = _register_a_module("writing", module_path)
        assert uuid_first == uuid_second, (
            f"register_module() created a duplicate row for the same path. "
            f"First UUID: {uuid_first!r}, second: {uuid_second!r}."
        )

    def test_upsert_updates_git_remote(self, tmp_path):
        """On re-registration, git_remote and git_remote_name should be updated."""
        _, module_path = _bootstrap(tmp_path)
        _register_a_module("writing", module_path)
        register_module(
            apparatus_name="writing",
            name="some-module",
            module_type="library",
            path=module_path,
            git_remote="git@github.com:user/some-module.git",
            git_remote_name="origin",
        )
        result = get_module_by_path(module_path)
        assert result is not None, "Module row not found after re-registration. The upsert path is broken."
        assert result["git_remote"] == "git@github.com:user/some-module.git"
        assert result["git_remote_name"] == "origin"

    def test_decimated_at_is_null_on_fresh_registration(self, tmp_path):
        """A newly registered module is not decimated. Obviously."""
        _, module_path = _bootstrap(tmp_path)
        uuid = _register_a_module("writing", module_path)
        result = get_module_by_uuid(uuid)
        assert result is not None, "Module row not found immediately after registration. Nothing to check."
        assert result["decimated_at"] is None, (
            "Freshly registered module has decimated_at set. "
            "That module is dead on arrival."
        )

    def test_apparatus_membership_row_created_on_fresh_registration(self, tmp_path):
        """
        A module registered WITH an apparatus_name gets a module_apparatus row.
        Without it, get_apparatus_modules() is blind and the entire containment
        graph is lying to you.
        """
        apparatus_uuid, module_path = _bootstrap(tmp_path)
        uuid = _register_a_module("writing", module_path)
        apparati = get_module_apparati(uuid)
        assert len(apparati) == 1, (
            f"Expected 1 apparatus membership row, got {len(apparati)}. "
            "register_module() must write the junction row on fresh registration."
        )
        assert apparati[0]["uuid"] == apparatus_uuid

    def test_standalone_module_has_no_apparatus_membership(self, tmp_path):
        """
        Passing apparatus_name=None registers a standalone module — no junction
        row, no apparatus association. This is not an error condition.
        """
        init_registry()
        module_path = tmp_path / "modules" / "lone-wolf"
        module_path.mkdir(parents=True)
        uuid = register_module(
            apparatus_name=None,
            name="lone-wolf",
            module_type="general",
            path=module_path,
            git_remote=None,
        )
        assert get_module_by_uuid(uuid) is not None, "standalone module wasn't written at all"
        apparati = get_module_apparati(uuid)
        assert apparati == [], (
            f"Standalone module has apparatus memberships: {apparati}. "
            "apparatus_name=None means no membership, full stop."
        )

    def test_apparatus_membership_not_touched_on_upsert(self, tmp_path):
        """
        Re-registering an existing module (upsert path) must leave the
        module_apparatus junction table completely alone. Adding or removing
        apparatus memberships is a separate explicit operation.
        """
        apparatus_uuid, module_path = _bootstrap(tmp_path)
        uuid = _register_a_module("writing", module_path)

        # Confirm the membership exists after first registration
        assert len(get_module_apparati(uuid)) == 1

        # Re-register (upsert path)
        _register_a_module("writing", module_path)

        # Should still be exactly one membership row — not zero, not two
        apparati = get_module_apparati(uuid)
        assert len(apparati) == 1, (
            f"Expected 1 membership row after upsert, got {len(apparati)}. "
            "Upsert must not touch the junction table."
        )


# ===========================================================================
# get_module_by_uuid / get_module_by_path / is_module_registered
# ===========================================================================

class TestModuleLookups:

    def test_get_module_by_uuid_returns_dict(self, tmp_path):
        _, module_path = _bootstrap(tmp_path)
        uuid = _register_a_module("writing", module_path)
        result = get_module_by_uuid(uuid)
        assert isinstance(result, dict)

    def test_get_module_by_uuid_returns_none_for_missing(self, tmp_path):
        init_registry()
        result = get_module_by_uuid("00000000-dead-beef-0000-000000000000")
        assert result is None

    def test_get_module_by_path_returns_dict(self, tmp_path):
        _, module_path = _bootstrap(tmp_path)
        _register_a_module("writing", module_path)
        result = get_module_by_path(module_path)
        assert isinstance(result, dict)

    def test_get_module_by_path_returns_none_for_missing(self, tmp_path):
        init_registry()
        result = get_module_by_path(tmp_path / "nowhere")
        assert result is None

    def test_get_module_by_path_resolves_relative_paths(self, tmp_path, monkeypatch):
        """
        get_module_by_path() calls path.resolve() before querying.
        A relative path that resolves to a registered absolute path must
        still find the row — otherwise the pre-commit hook breaks any time
        it runs from a non-root working directory.
        """
        _, module_path = _bootstrap(tmp_path)
        _register_a_module("writing", module_path)

        # Construct a relative path that resolves to module_path
        monkeypatch.chdir(module_path.parent)
        relative = Path(module_path.name)
        result = get_module_by_path(relative)
        assert result is not None, (
            "get_module_by_path() failed to find a module via a relative path. "
            "Relative path resolution is supposed to happen inside the function."
        )

    def test_get_module_by_uuid_does_not_contain_apparatus_uuid(self, tmp_path):
        """
        The `apparatus_uuid` column was removed from the `modules` table as
        part of the multi-apparatus remediation. get_module_by_uuid() must
        NOT have that key in the returned dict — if it does, the old schema
        is back, `module_apparatus` is being ignored, and the whole junction-
        table model is silently broken.

        This is the regression pin for the exact bug. Do not delete it.
        """
        _, module_path = _bootstrap(tmp_path)
        uuid = _register_a_module("writing", module_path)
        result = get_module_by_uuid(uuid)
        assert result is not None
        assert "apparatus_uuid" not in result, (
            f"get_module_by_uuid() returned a dict containing 'apparatus_uuid': "
            f"{result.get('apparatus_uuid')!r}. "
            "That column was removed from the modules table. "
            "If it's back, the schema migration didn't land."
        )

    def test_is_module_registered_true(self, tmp_path):
        _, module_path = _bootstrap(tmp_path)
        uuid = _register_a_module("writing", module_path)
        assert is_module_registered(uuid) is True

    def test_is_module_registered_false(self, tmp_path):
        init_registry()
        assert is_module_registered("completely-fake-uuid") is False


# ===========================================================================
# decimate_module / reactivate_module
# ===========================================================================

class TestDecimateReactivate:
    """
    Modules are never hard-deleted. Deregistration stamps decimated_at.
    Reactivation clears it. Both operations must validate the UUID exists
    before touching anything — raising on a ghost UUID is the correct behavior.
    """

    def test_decimate_stamps_decimated_at(self, tmp_path):
        _, module_path = _bootstrap(tmp_path)
        uuid = _register_a_module("writing", module_path)
        decimate_module(uuid)
        result = get_module_by_uuid(uuid)
        assert result is not None, "Module row vanished after decimate_module(). That's not how tombstoning works."
        assert result["decimated_at"] is not None, (
            "decimate_module() didn't stamp decimated_at. "
            "The module is supposed to be dead."
        )

    def test_decimate_decimated_at_is_a_date_string(self, tmp_path):
        """decimated_at should be a YYYY-MM-DD date string, not garbage."""
        _, module_path = _bootstrap(tmp_path)
        uuid = _register_a_module("writing", module_path)
        decimate_module(uuid)
        result = get_module_by_uuid(uuid)
        assert result is not None, "Module row vanished after decimate_module(). That's not how tombstoning works."
        decimated_at = result["decimated_at"]
        assert decimated_at is not None, "decimate_module() left decimated_at unset. Nothing to pattern-match."
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", decimated_at), (
            f"decimated_at is not a YYYY-MM-DD date: { decimated_at!r }"
        )

    def test_decimate_raises_on_unknown_uuid(self, tmp_path):
        init_registry()
        with pytest.raises(ValueError):
            decimate_module("nonexistent-uuid")

    def test_reactivate_clears_decimated_at(self, tmp_path):
        _, module_path = _bootstrap(tmp_path)
        uuid = _register_a_module("writing", module_path)
        decimate_module(uuid)
        reactivate_module(uuid)
        result = get_module_by_uuid(uuid)
        assert result is not None, "Module row not found after reactivate_module(). Nothing to check."
        assert result["decimated_at"] is None, (
            "reactivate_module() didn't clear decimated_at. "
            "The module should be alive again."
        )

    def test_reactivate_raises_on_unknown_uuid(self, tmp_path):
        init_registry()
        with pytest.raises(ValueError):
            reactivate_module("nonexistent-uuid")

    def test_decimated_module_not_returned_by_get_apparatus_modules(self, tmp_path):
        """
        The whole point of decimation is that the module doesn't show up in
        normal queries. If get_apparatus_modules() returns decimated modules
        by default, that's a broken invariant.
        """
        _, module_path = _bootstrap(tmp_path)
        uuid = _register_a_module("writing", module_path)
        decimate_module(uuid)
        active_modules = get_apparatus_modules("writing")
        uuids = [m["uuid"] for m in active_modules]
        assert uuid not in uuids, (
            "Decimated module appeared in get_apparatus_modules() without "
            "include_decimated=True. Decimated modules must be invisible by default."
        )

    def test_decimated_module_visible_with_include_decimated(self, tmp_path):
        _, module_path = _bootstrap(tmp_path)
        uuid = _register_a_module("writing", module_path)
        decimate_module(uuid)
        all_modules = get_apparatus_modules("writing", include_decimated=True)
        uuids = [m["uuid"] for m in all_modules]
        assert uuid in uuids, (
            "Decimated module didn't appear with include_decimated=True. "
            "The flag is supposed to surface them."
        )

    def test_decimation_is_reversible(self, tmp_path):
        """decimate → reactivate → decimate again. Should not raise."""
        _, module_path = _bootstrap(tmp_path)
        uuid = _register_a_module("writing", module_path)
        decimate_module(uuid)
        reactivate_module(uuid)
        decimate_module(uuid)  # should not raise
        result = get_module_by_uuid(uuid)
        assert result is not None, "Module row not found after second decimate. Nothing to check."
        assert result["decimated_at"] is not None


# ===========================================================================
# update_module_sync
# ===========================================================================

class TestUpdateModuleSync:
    """
    update_module_sync() is called by the pre-commit hook on every commit.
    It must be a silent no-op for unknown UUIDs — the hook cannot abort a
    commit over a missing registry entry.
    """

    def test_updates_last_synced_at(self, tmp_path):
        _, module_path = _bootstrap(tmp_path)
        uuid = _register_a_module("writing", module_path)
        update_module_sync(uuid, module_path)
        result = get_module_by_uuid(uuid)
        assert result is not None, "Module row not found after update_module_sync(). Nothing to check."
        assert result["last_synced_at"] is not None, (
            "update_module_sync() didn't set last_synced_at. "
            "muster will show stale sync data forever."
        )

    def test_silent_noop_for_unknown_uuid(self, tmp_path):
        """Must not raise. The pre-commit hook's contract depends on this."""
        init_registry()
        update_module_sync("i-am-a-ghost-uuid", tmp_path)  # should not raise or blow up


# ===========================================================================
# Bay management
# ===========================================================================

class TestBayManagement:
    """
    module_bays is the containment graph. add is idempotent. remove is
    idempotent. remove_all clears every relationship for a contained module
    without disturbing other modules' relationships.
    """

    def _setup_two_modules(self, tmp_path) -> tuple[str, str]:
        """Register a vault and a library. Return (vault_uuid, lib_uuid)."""
        init_registry()
        register_apparatus("writing", git_remote=None)
        vault_path = tmp_path / "modules" / "fiction-vault"
        lib_path = tmp_path / "modules" / "cosmic-horror"
        vault_path.mkdir(parents=True)
        lib_path.mkdir(parents=True)
        vault_uuid = register_module(
            apparatus_name="writing",
            name="fiction-vault",
            module_type="vault",
            path=vault_path,
            git_remote=None,
        )
        lib_uuid = register_module(
            apparatus_name="writing",
            name="cosmic-horror",
            module_type="library",
            path=lib_path,
            git_remote=None,
        )
        return vault_uuid, lib_uuid

    def test_add_module_to_bay_creates_row(self, tmp_path):
        vault_uuid, lib_uuid = self._setup_two_modules(tmp_path)
        add_module_to_bay(vault_uuid, lib_uuid)
        bays = get_module_bays(lib_uuid)
        assert len(bays) == 1
        assert bays[0]["uuid"] == vault_uuid

    def test_add_module_to_bay_is_idempotent(self, tmp_path):
        """INSERT OR IGNORE — second call must be a silent no-op."""
        vault_uuid, lib_uuid = self._setup_two_modules(tmp_path)
        add_module_to_bay(vault_uuid, lib_uuid)
        add_module_to_bay(vault_uuid, lib_uuid)  # should not raise or duplicate
        bays = get_module_bays(lib_uuid)
        assert len(bays) == 1, (
            f"add_module_to_bay() created duplicate rows. Expected 1, got {len(bays)}."
        )

    def test_remove_module_from_bay_removes_row(self, tmp_path):
        vault_uuid, lib_uuid = self._setup_two_modules(tmp_path)
        add_module_to_bay(vault_uuid, lib_uuid)
        remove_module_from_bay(vault_uuid, lib_uuid)
        bays = get_module_bays(lib_uuid)
        assert len(bays) == 0

    def test_remove_module_from_bay_is_noop_if_absent(self, tmp_path):
        """No row to delete — must not raise."""
        vault_uuid, lib_uuid = self._setup_two_modules(tmp_path)
        remove_module_from_bay(vault_uuid, lib_uuid)  # should not raise

    def test_get_module_bays_returns_empty_list_when_none(self, tmp_path):
        _, module_path = _bootstrap(tmp_path)
        uuid = _register_a_module("writing", module_path)
        result = get_module_bays(uuid)
        assert result == []

    def test_remove_all_bays_for_contained_clears_all_relationships(self, tmp_path):
        """
        A module contained in multiple vaults — remove_all should clear
        every relationship for that module, not just one.
        """
        init_registry()
        register_apparatus("writing", git_remote=None)

        vault_a_path = tmp_path / "vault-a"
        vault_b_path = tmp_path / "vault-b"
        lib_path = tmp_path / "lib"
        vault_a_path.mkdir()
        vault_b_path.mkdir()
        lib_path.mkdir()

        vault_a_uuid = register_module("writing", "vault-a", "vault", vault_a_path, None)
        vault_b_uuid = register_module("writing", "vault-b", "vault", vault_b_path, None)
        lib_uuid = register_module("writing", "lib", "library", lib_path, None)

        add_module_to_bay(vault_a_uuid, lib_uuid)
        add_module_to_bay(vault_b_uuid, lib_uuid)

        remove_all_bays_for_contained(lib_uuid)

        assert get_module_bays(lib_uuid) == [], (
            "remove_all_bays_for_contained() left bay rows behind. "
            "The module should have zero containers after a full removal."
        )

    def test_remove_all_bays_does_not_disturb_other_modules(self, tmp_path):
        """
        Removing all bays for module A must not touch the bays for module B,
        even if they share a container.
        """
        init_registry()
        register_apparatus("writing", git_remote=None)

        vault_path = tmp_path / "vault"
        lib_a_path = tmp_path / "lib-a"
        lib_b_path = tmp_path / "lib-b"
        vault_path.mkdir()
        lib_a_path.mkdir()
        lib_b_path.mkdir()

        vault_uuid = register_module("writing", "vault", "vault", vault_path, None)
        lib_a_uuid = register_module("writing", "lib-a", "library", lib_a_path, None)
        lib_b_uuid = register_module("writing", "lib-b", "library", lib_b_path, None)

        add_module_to_bay(vault_uuid, lib_a_uuid)
        add_module_to_bay(vault_uuid, lib_b_uuid)

        remove_all_bays_for_contained(lib_a_uuid)

        # lib-b should still be in the vault
        bays_b = get_module_bays(lib_b_uuid)
        assert len(bays_b) == 1, (
            "remove_all_bays_for_contained() removed bay rows for a module "
            "it was not asked to touch. Collateral damage."
        )


# ===========================================================================
# get_apparatus_modules
# ===========================================================================

class TestGetApparatusModules:
    """
    The primary query for everything that needs to know "what's in this apparatus."
    Excludes decimated by default. Sorted by name. Returns empty list, not None,
    for missing apparatus — callers should not have to null-check this.
    """

    def test_returns_modules_for_apparatus(self, tmp_path):
        _, module_path = _bootstrap(tmp_path)
        _register_a_module("writing", module_path)
        results = get_apparatus_modules("writing")
        assert len(results) == 1

    def test_excludes_decimated_by_default(self, tmp_path):
        _, module_path = _bootstrap(tmp_path)
        uuid = _register_a_module("writing", module_path)
        decimate_module(uuid)
        results = get_apparatus_modules("writing")
        assert results == [], (
            "get_apparatus_modules() returned decimated modules without the flag. "
            "Decimated modules must be invisible by default."
        )

    def test_includes_decimated_with_flag(self, tmp_path):
        _, module_path = _bootstrap(tmp_path)
        uuid = _register_a_module("writing", module_path)
        decimate_module(uuid)
        results = get_apparatus_modules("writing", include_decimated=True)
        assert len(results) == 1

    def test_sorted_by_name(self, tmp_path):
        init_registry()
        register_apparatus("writing", git_remote=None)
        for name in ("zebra-module", "alpha-module", "middle-module"):
            p = tmp_path / name
            p.mkdir()
            register_module("writing", name, "library", p, None)
        results = get_apparatus_modules("writing")
        names = [r["name"] for r in results]
        assert names == sorted(names), (
            f"get_apparatus_modules() returned modules out of order: {names}. "
            "Results must be sorted by name."
        )

    def test_returns_empty_list_for_unknown_apparatus(self, tmp_path):
        init_registry()
        result = get_apparatus_modules("does-not-exist")
        assert result == [], (
            "get_apparatus_modules() returned something other than [] "
            "for an apparatus that doesn't exist. Callers depend on an empty list, not None."
        )

    def test_returns_empty_list_for_apparatus_with_no_modules(self, tmp_path):
        init_registry()
        register_apparatus("writing", git_remote=None)
        result = get_apparatus_modules("writing")
        assert result == []

    def test_module_in_two_apparati_appears_in_both_result_sets(self, tmp_path):
        """
        A module registered under "writing" and then added to "cyber" must
        appear when querying EITHER apparatus. If the JOIN is wrong and only
        the first registration's apparatus is returned, the multi-apparatus
        model is broken and get_apparatus_modules() is lying to you.
        """
        init_registry()
        register_apparatus("writing", git_remote=None)
        cyber_uuid = register_apparatus("cyber", git_remote=None)

        module_path = tmp_path / "modules" / "cosmic-horror"
        module_path.mkdir(parents=True)
        module_uuid = register_module("writing", "cosmic-horror", "library", module_path, None)

        add_module_to_apparatus(module_uuid, cyber_uuid)

        writing_results = get_apparatus_modules("writing")
        cyber_results   = get_apparatus_modules("cyber")

        writing_uuids = {m["uuid"] for m in writing_results}
        cyber_uuids   = {m["uuid"] for m in cyber_results}

        assert module_uuid in writing_uuids, (
            "Module not found in 'writing' — its registration apparatus. "
            "get_apparatus_modules() is dropping modules."
        )
        assert module_uuid in cyber_uuids, (
            "Module not found in 'cyber' after add_module_to_apparatus(). "
            "The junction table JOIN is not returning secondary memberships."
        )

    def test_returns_correct_modules_after_remove_module_from_apparatus(self, tmp_path):
        """
        After remove_module_from_apparatus(), the module must vanish from that
        apparatus's result set. If get_apparatus_modules() still returns it,
        the DELETE didn't land or the query is ignoring the junction table.
        """
        apparatus_uuid, module_path = _bootstrap(tmp_path)
        module_uuid = _register_a_module("writing", module_path)

        # Sanity — module is in the set before removal
        before = get_apparatus_modules("writing")
        assert any(m["uuid"] == module_uuid for m in before), (
            "Test setup failed — module not in apparatus before removal."
        )

        remove_module_from_apparatus(module_uuid, apparatus_uuid)

        after = get_apparatus_modules("writing")
        assert all(m["uuid"] != module_uuid for m in after), (
            f"Module still appears in get_apparatus_modules('writing') after "
            f"remove_module_from_apparatus(). "
            "The junction row is still there and the query is not respecting it."
        )


# ===========================================================================
# get_bay_modules
# ===========================================================================

class TestGetBayModules:

    def test_returns_modules_in_container(self, tmp_path):
        init_registry()
        register_apparatus("writing", git_remote=None)
        vault_path = tmp_path / "vault"
        lib_path = tmp_path / "lib"
        vault_path.mkdir()
        lib_path.mkdir()
        vault_uuid = register_module("writing", "vault", "vault", vault_path, None)
        lib_uuid = register_module("writing", "lib", "library", lib_path, None)
        add_module_to_bay(vault_uuid, lib_uuid)

        results = get_bay_modules(vault_uuid)
        assert len(results) == 1
        assert results[0]["uuid"] == lib_uuid

    def test_excludes_decimated_by_default(self, tmp_path):
        init_registry()
        register_apparatus("writing", git_remote=None)
        vault_path = tmp_path / "vault"
        lib_path = tmp_path / "lib"
        vault_path.mkdir()
        lib_path.mkdir()
        vault_uuid = register_module("writing", "vault", "vault", vault_path, None)
        lib_uuid = register_module("writing", "lib", "library", lib_path, None)
        add_module_to_bay(vault_uuid, lib_uuid)
        decimate_module(lib_uuid)

        results = get_bay_modules(vault_uuid)
        assert results == []

    def test_includes_decimated_with_flag(self, tmp_path):
        init_registry()
        register_apparatus("writing", git_remote=None)
        vault_path = tmp_path / "vault"
        lib_path = tmp_path / "lib"
        vault_path.mkdir()
        lib_path.mkdir()
        vault_uuid = register_module("writing", "vault", "vault", vault_path, None)
        lib_uuid = register_module("writing", "lib", "library", lib_path, None)
        add_module_to_bay(vault_uuid, lib_uuid)
        decimate_module(lib_uuid)

        results = get_bay_modules(vault_uuid, include_decimated=True)
        assert len(results) == 1

    def test_returns_empty_list_for_unknown_container(self, tmp_path):
        init_registry()
        result = get_bay_modules("i-am-not-a-real-uuid")
        assert result == []

    def test_sorted_by_name(self, tmp_path):
        init_registry()
        register_apparatus("writing", git_remote=None)
        vault_path = tmp_path / "vault"
        vault_path.mkdir()
        vault_uuid = register_module("writing", "vault", "vault", vault_path, None)

        for name in ("zebra-lib", "alpha-lib", "middle-lib"):
            p = tmp_path / name
            p.mkdir()
            lib_uuid = register_module("writing", name, "library", p, None)
            add_module_to_bay(vault_uuid, lib_uuid)

        results = get_bay_modules(vault_uuid)
        names = [r["name"] for r in results]
        assert names == sorted(names)


# ===========================================================================
# get_vault_modules
# ===========================================================================

class TestGetVaultModules:
    """
    get_vault_modules() wraps get_bay_modules() with a type assertion.
    It must raise ValueError if the container is not a vault — that's the
    entire point of the function existing separately from get_bay_modules().
    """

    def test_returns_modules_for_vault(self, tmp_path):
        init_registry()
        register_apparatus("writing", git_remote=None)
        vault_path = tmp_path / "vault"
        lib_path = tmp_path / "lib"
        vault_path.mkdir()
        lib_path.mkdir()
        vault_uuid = register_module("writing", "vault", "vault", vault_path, None)
        lib_uuid = register_module("writing", "lib", "library", lib_path, None)
        add_module_to_bay(vault_uuid, lib_uuid)

        results = get_vault_modules(vault_uuid)
        assert len(results) == 1
        assert results[0]["uuid"] == lib_uuid

    def test_raises_for_non_vault_container(self, tmp_path):
        """
        A library is not a vault. Calling get_vault_modules() with a library UUID
        must raise ValueError — use get_bay_modules() if you want containment
        without the type assertion.
        """
        _, module_path = _bootstrap(tmp_path)
        lib_uuid = _register_a_module("writing", module_path, module_type="library")
        with pytest.raises(ValueError, match="vault"):
            get_vault_modules(lib_uuid)

    def test_raises_for_unknown_uuid(self, tmp_path):
        init_registry()
        with pytest.raises(ValueError):
            get_vault_modules("ghost-uuid-that-doesnt-exist")


# ===========================================================================
# list_apparatus_names
# ===========================================================================

class TestListApparatusNames:
    """
    list_apparatus_names() is the registry overview query. It must handle the
    case where the registry doesn't exist yet — returning [] rather than raising,
    because Phase 2 commands like muster need to call this on any machine
    without knowing whether init has run.
    """

    def test_returns_all_apparatus_names(self, tmp_path):
        init_registry()
        register_apparatus("writing", git_remote=None)
        register_apparatus("cyber", git_remote=None)
        names = list_apparatus_names()
        assert "writing" in names
        assert "cyber" in names

    def test_returns_names_sorted_alphabetically(self, tmp_path):
        init_registry()
        for name in ("zebra", "alpha", "middle"):
            register_apparatus(name, git_remote=None)
        names = list_apparatus_names()
        assert names == sorted(names), (
            f"list_apparatus_names() returned names out of order: {names}. "
            "Alphabetical sort is the contract."
        )

    def test_returns_empty_list_when_registry_has_no_rows(self, tmp_path):
        init_registry()
        result = list_apparatus_names()
        assert result == []

    def test_returns_empty_list_when_registry_does_not_exist(self, tmp_path):
        """
        The registry dir doesn't exist at all. Must return [] without raising.
        A machine that hasn't run archivist init should degrade gracefully.
        """
        result = list_apparatus_names()
        assert result == [], (
            "list_apparatus_names() raised or returned non-[] when the registry "
            "doesn't exist yet. Callers must not have to guard against this."
        )

# ===========================================================================
# Apparatus membership
# ===========================================================================

class TestApparatusMembership:
    """
    module_apparatus is the junction table that lets a single module belong to
    more than one apparatus. Every function here is idempotent or a clean no-op
    on missing rows. Break any of these and the multi-apparatus model falls apart
    in ways that are silent and fucking infuriating to debug.
    """

    def _setup_two_apparati(self, tmp_path) -> tuple[str, str, str, Path]:
        """
        Register two apparati and a module. Returns
        (apparatus_a_uuid, apparatus_b_uuid, module_uuid, module_path).
        Module is registered under "writing" only — "cyber" is free for tests
        to add membership to.
        """
        init_registry()
        app_a_uuid = register_apparatus("writing", git_remote=None)
        app_b_uuid = register_apparatus("cyber", git_remote=None)
        module_path = tmp_path / "modules" / "cosmic-horror"
        module_path.mkdir(parents=True)
        module_uuid = register_module(
            apparatus_name="writing",
            name="cosmic-horror",
            module_type="library",
            path=module_path,
            git_remote=None,
        )
        return app_a_uuid, app_b_uuid, module_uuid, module_path

    def test_add_module_to_apparatus_creates_row(self, tmp_path):
        app_a_uuid, app_b_uuid, module_uuid, _ = self._setup_two_apparati(tmp_path)
        # Module was registered under "writing" — add it to "cyber" too
        add_module_to_apparatus(module_uuid, app_b_uuid)
        apparati = get_module_apparati(module_uuid)
        apparatus_uuids = {a["uuid"] for a in apparati}
        assert app_b_uuid in apparatus_uuids, (
            "add_module_to_apparatus() didn't create the junction row. "
            "The module should now be in both apparati."
        )

    def test_add_module_to_apparatus_is_idempotent(self, tmp_path):
        """INSERT OR IGNORE — second call must be a silent no-op, not a duplicate."""
        app_a_uuid, _, module_uuid, _ = self._setup_two_apparati(tmp_path)
        add_module_to_apparatus(module_uuid, app_a_uuid)  # already exists
        add_module_to_apparatus(module_uuid, app_a_uuid)  # should not raise or duplicate
        apparati = get_module_apparati(module_uuid)
        writing_rows = [a for a in apparati if a["uuid"] == app_a_uuid]
        assert len(writing_rows) == 1, (
            f"add_module_to_apparatus() created {len(writing_rows)} rows for the same pair. "
            "INSERT OR IGNORE means one row, you absolute maniac."
        )

    def test_remove_module_from_apparatus_removes_row(self, tmp_path):
        app_a_uuid, _, module_uuid, _ = self._setup_two_apparati(tmp_path)
        remove_module_from_apparatus(module_uuid, app_a_uuid)
        apparati = get_module_apparati(module_uuid)
        assert all(a["uuid"] != app_a_uuid for a in apparati), (
            "remove_module_from_apparatus() left the row in place. "
            "The module should no longer be in 'writing'."
        )

    def test_remove_module_from_apparatus_is_noop_if_absent(self, tmp_path):
        """No row to delete — must not raise. Idempotency is the contract."""
        _, app_b_uuid, module_uuid, _ = self._setup_two_apparati(tmp_path)
        # Module is NOT in "cyber" — removing a nonexistent relationship must be silent
        remove_module_from_apparatus(module_uuid, app_b_uuid)  # should not raise

    def test_remove_all_apparatus_memberships_clears_all_rows(self, tmp_path):
        """
        A module in two apparati — remove_all should clear both junction rows,
        leaving the module effectively standalone without touching the module row itself.
        """
        app_a_uuid, app_b_uuid, module_uuid, _ = self._setup_two_apparati(tmp_path)
        add_module_to_apparatus(module_uuid, app_b_uuid)

        assert len(get_module_apparati(module_uuid)) == 2, "test setup failed — expected 2 memberships"

        remove_all_apparatus_memberships(module_uuid)

        apparati = get_module_apparati(module_uuid)
        assert apparati == [], (
            f"remove_all_apparatus_memberships() left {len(apparati)} row(s) behind. "
            "The module should have zero apparatus associations after a full wipe."
        )

    def test_remove_all_apparatus_memberships_does_not_touch_other_modules(self, tmp_path):
        """
        Removing all memberships for module A must leave module B's memberships
        completely intact. Collateral damage here is catastrophic.
        """
        app_a_uuid, _, module_a_uuid, _ = self._setup_two_apparati(tmp_path)

        # Register a second module in the same apparatus
        module_b_path = tmp_path / "modules" / "eldritch-gazette"
        module_b_path.mkdir(parents=True)
        module_b_uuid = register_module(
            apparatus_name="writing",
            name="eldritch-gazette",
            module_type="publication",
            path=module_b_path,
            git_remote=None,
        )

        # Nuke module A's memberships
        remove_all_apparatus_memberships(module_a_uuid)

        # Module B must still have its row
        b_apparati = get_module_apparati(module_b_uuid)
        assert any(a["uuid"] == app_a_uuid for a in b_apparati), (
            "remove_all_apparatus_memberships() wiped memberships for a module "
            "it was not asked to touch. That's a data loss bug."
        )

    def test_get_module_apparati_returns_all_memberships(self, tmp_path):
        app_a_uuid, app_b_uuid, module_uuid, _ = self._setup_two_apparati(tmp_path)
        add_module_to_apparatus(module_uuid, app_b_uuid)

        apparati = get_module_apparati(module_uuid)
        uuids = {a["uuid"] for a in apparati}
        assert uuids == {app_a_uuid, app_b_uuid}, (
            f"get_module_apparati() returned {uuids!r}. "
            "Expected both apparatus UUIDs."
        )

    def test_get_module_apparati_returns_empty_for_standalone(self, tmp_path):
        init_registry()
        module_path = tmp_path / "lone-wolf"
        module_path.mkdir()
        uuid = register_module(
            apparatus_name=None,
            name="lone-wolf",
            module_type="general",
            path=module_path,
            git_remote=None,
        )
        assert get_module_apparati(uuid) == [], (
            "get_module_apparati() returned rows for a standalone module. "
            "apparatus_name=None at registration means zero memberships, always."
        )

    def test_get_module_apparati_sorted_by_name(self, tmp_path):
        """Results must be alphabetically sorted — callers depend on stable ordering."""
        app_a_uuid, app_b_uuid, module_uuid, _ = self._setup_two_apparati(tmp_path)
        add_module_to_apparatus(module_uuid, app_b_uuid)

        apparati = get_module_apparati(module_uuid)
        names = [a["name"] for a in apparati]
        assert names == sorted(names), (
            f"get_module_apparati() returned names out of order: {names}. "
            "Sort alphabetically or callers have to do it themselves every time."
        )


# ===========================================================================
# prompt_apparatus_names — interactive multi-select
# ===========================================================================

class TestPromptApparatusNames:
    """
    Unlike the git+confirm-chain interactive flows in `add` and `migrate`
    (accepted gaps per TESTING_SPECIFICATION.md — too much surrounding
    machinery to isolate cleanly), prompt_apparatus_names() is pure:
    list_apparatus_names() reads plus input()/print(). No DB writes of its
    own. That makes it directly unit-testable by faking input() — no need
    to drag in git_repo, subprocess mocking, or any of that.

    Every test below exercises the numbered-prompt FALLBACK path
    (_prompt_apparatus_names_fallback), not the curses checkbox screen —
    see _force_fallback_path below for why, and TESTING_SPECIFICATION.md's
    Known Gaps for why the checkbox screen itself isn't covered here.

    Numbering is alphabetical because list_apparatus_names() sorts
    alphabetically — every test below registers names where that ordering
    is either irrelevant (single apparatus) or deliberately chosen so the
    expected option numbers are unambiguous (e.g. "alpha" before "zeta").
    """

    @pytest.fixture(autouse = True)
    def _force_fallback_path(self, monkeypatch):
        """
        Force curses to None for every test in this class, so
        prompt_apparatus_names() takes the numbered-prompt fallback
        deliberately rather than by accident.

        Without this, whether these tests exercise the fallback at all
        depends entirely on whether curses.wrapper() happens to fail in
        whatever environment pytest is running in. It reliably does in a
        headless CI runner (no controlling terminal — that's the
        `_curses.error: cbreak() returned ERR` you'll see if you strip this
        fixture and run the suite there) but there's no guarantee of that
        anywhere else. Run this file from an actual terminal window and
        curses.wrapper() might well succeed, opening a real checkbox screen
        that blocks forever on stdscr.getch() waiting for keypresses these
        tests never send. Setting curses to None sidesteps curses.wrapper()
        entirely — prompt_apparatus_names() takes its "curses isn't
        available on this platform" branch straight to the fallback, same
        codepath, zero terminal dependency, zero chance of a hung test run.
        """
        monkeypatch.setattr(registry_module, "curses", None)

    @staticmethod
    def _queued_input(monkeypatch, responses: list[str]) -> None:
        """
        Feed canned answers to sequential input() calls in order. Raises
        loudly — not a hang — if the function asks more questions than
        the test bothered to answer.
        """
        queue = iter(responses)

        def _fake_input(prompt: str = "") -> str:
            try:
                return next(queue)
            except StopIteration:
                raise AssertionError(
                    f"prompt_apparatus_names() asked for input beyond the "
                    f"queued responses: {prompt!r}."
                )

        monkeypatch.setattr("builtins.input", _fake_input)

    def test_no_existing_apparati_still_shows_the_menu_with_only_create_new(self, monkeypatch):
        """
        Nothing registered yet — the numbered menu still renders, it just
        has exactly one option: "Create new". There's no more automatic
        skip straight to the slug prompt; that shortcut denied the "no
        apparatus" answer, which is now valid even on a cold registry (see
        the next two tests). Explicitly selecting "1" gets you the same
        slug prompt the shortcut used to jump to automatically.
        """
        init_registry()
        self._queued_input(monkeypatch, [
            "1",
            "writing",
            "n"
        ])
        assert prompt_apparatus_names() == ["writing"]

    def test_single_existing_apparatus_selected_by_number(self, monkeypatch):
        init_registry()
        register_apparatus("writing", git_remote=None)
        self._queued_input(monkeypatch, ["1", "n"])
        assert prompt_apparatus_names() == ["writing"]

    def test_multiple_apparati_selected_with_comma_separated_numbers(self, monkeypatch):
        init_registry()
        register_apparatus("writing", git_remote=None)
        register_apparatus("cyber", git_remote=None)
        self._queued_input(monkeypatch, ["1, 2", "n"])
        assert set(prompt_apparatus_names()) == {"writing", "cyber"}

    def test_multiple_apparati_selected_with_space_separated_numbers(self, monkeypatch):
        init_registry()
        register_apparatus("writing", git_remote = None)
        register_apparatus("cyber", git_remote = None)
        self._queued_input(monkeypatch, ["1 2", "n"])
        assert set(prompt_apparatus_names()) == {"writing", "cyber"}

    def test_create_new_option_alongside_existing_pick(self, monkeypatch):
        """
        Picking an existing apparatus AND "Create new" on the same line gets
        you both — the slug prompt fires once, and the result includes
        whatever else was picked in that round too.
        """
        init_registry()
        register_apparatus("writing", git_remote = None)
        # options: 1. writing  2. Create new
        self._queued_input(monkeypatch, ["1, 2", "cyber", "n"])
        assert set(prompt_apparatus_names()) == {"writing", "cyber"}

    def test_add_another_apparatus_loops_for_a_second_round(self, monkeypatch):
        init_registry()
        register_apparatus("writing", git_remote = None)
        # Round 1: pick the only existing apparatus by number. Round 2:
        # nothing left to pick from but "Create new" — still a real menu
        # with a real number to select, not an automatic skip.
        self._queued_input(monkeypatch, [
            "1",
            "y",
            "1",
            "cyber-zine",
            "n"
        ])
        result = prompt_apparatus_names()
        assert result == ["writing", "cyber-zine"], (
            f"Expected ['writing', 'cyber-zine'] in selection order. Got {result!r}."
        )

    def test_already_selected_apparatus_excluded_from_next_round_menu(self, monkeypatch, capsys):
        """
        After picking 'alpha' in round 1, it must not reappear as an option
        in round 2 — picking the same one twice should be impossible by
        construction, not just silently deduplicated after the fact.
        """
        init_registry()
        register_apparatus("alpha", git_remote=None)
        register_apparatus("zeta", git_remote=None)
        self._queued_input(monkeypatch, ["1", "y", "1", "n"])

        result = prompt_apparatus_names()
        assert result == ["alpha", "zeta"]

        out = capsys.readouterr().out
        rounds = out.split("Select apparatus/apparati")
        assert len(rounds) >= 3, "Expected two full menu rounds to be printed."
        second_round_menu = rounds[2]
        assert "alpha" not in second_round_menu, (
            "Already-selected apparatus 'alpha' still appeared as an option "
            "in the second round's menu."
        )
        assert "1. zeta" in second_round_menu

    def test_invalid_selection_reprompts(self, monkeypatch):
        """Garbage input must redisplay the menu, not silently misinterpret."""
        init_registry()
        register_apparatus("writing", git_remote=None)
        self._queued_input(monkeypatch, ["nope", "1", "n"])
        assert prompt_apparatus_names() == ["writing"]

    def test_out_of_range_number_reprompts(self, monkeypatch):
        init_registry()
        register_apparatus("writing", git_remote=None)
        # Only 2 options exist (writing, Create new) — "9" is out of range.
        self._queued_input(monkeypatch, ["9", "1", "n"])
        assert prompt_apparatus_names() == ["writing"]

    def test_blank_input_on_first_round_returns_empty_list_immediately(self, monkeypatch):
        """
        Blank used to be invalid input that forced a reprompt. It isn't
        anymore — it's the standalone-module answer, and it applies even
        before anything's been picked. No trailing "n" needed: a blank
        answer returns immediately, it doesn't fall through to "Add another
        apparatus?" first.
        """
        init_registry()
        register_apparatus("writing", git_remote = None)
        self._queued_input(monkeypatch, [""])
        assert prompt_apparatus_names() == []

    def test_blank_input_on_a_later_round_stops_with_whatever_was_already_picked(self, monkeypatch):
        """
        Same "blank means stop" rule applies mid-flow, after a round of
        real selections — it returns what's already been picked instead of
        demanding a number or re-asking.

        Menu order is alphabetical (list_apparatus_names() sorts), not
        registration order — "cyber" sorts before "writing", so option "1"
        is "cyber" regardless of which one got registered first. Pin that
        assumption here instead of leaving it implicit and fragile.
        """
        init_registry()
        register_apparatus("writing", git_remote = None)
        register_apparatus("cyber", git_remote = None)
        self._queued_input(monkeypatch, ["1", "y", ""])
        assert prompt_apparatus_names() == ["cyber"]

    def test_picking_the_same_number_twice_in_one_line_does_not_duplicate(self, monkeypatch):
        init_registry()
        register_apparatus("writing", git_remote = None)
        self._queued_input(monkeypatch, ["1, 1", "n"])
        assert prompt_apparatus_names() == ["writing"]

    def test_can_return_empty_list_on_a_cold_registry(self, monkeypatch):
        """
        The OLD invariant here was "never returns an empty list" — that's
        inverted now on purpose. A module doesn't have to belong to an
        Apparatus, and declining is always available, even when there's
        nothing registered yet to decline.
        """
        init_registry()
        self._queued_input(monkeypatch, [""])
        assert prompt_apparatus_names() == []

    def test_can_return_empty_list_while_declining_real_options(self, monkeypatch):
        """
        Same as above, but with an actual apparatus on offer — the user
        just doesn't want it. Declining a real option is just as valid as
        declining when there was nothing to decline.
        """
        init_registry()
        register_apparatus("solo-apparatus", git_remote = None)
        self._queued_input(monkeypatch, [""])
        result = prompt_apparatus_names()
        assert result == [], (
            f"Expected an empty list — declining is a valid standalone-module "
            f"answer, not something to keep re-asking about. Got {result!r}."
        )

    def test_invalid_new_slug_reprompts_for_slug_only(self, monkeypatch):
        """
        An invalid slug at the "Create new" sub-prompt re-asks just the slug
        question — it doesn't bounce back out to the main numbered menu.
        """
        init_registry()
        self._queued_input(monkeypatch, [
            "1",
            "Bad Name!",
            "good-name",
            "n"
        ])
        assert prompt_apparatus_names() == ["good-name"]

    def test_duplicate_new_slug_against_already_selected_reprompts(self, monkeypatch):
        """
        Typing the same name you just created again at a second "Create new"
        round must be rejected and re-prompted, not silently duplicated.

        Note the just-created "writing" isn't written to the DB by this
        function at all — _prompt_new_apparatus_slug() only checks the
        in-memory `selected` list, and the actual registry write happens
        later, in register_module_with_apparati(). So round two's
        `remaining` is still empty and "1" (Create new) has to be picked
        again explicitly — it doesn't magically become option "2" just
        because a name was typed in round one.
        """
        init_registry()
        self._queued_input(monkeypatch, [
            "1",
            "writing",
            "y", "1",
            "writing",
            "new-one",
            "n"
        ])
        assert prompt_apparatus_names() == ["writing", "new-one"]

    def test_full_word_yes_accepted_for_add_another(self, monkeypatch):
        """'yes' (full word), not just 'y', must be accepted — same
        convention used everywhere else in this codebase for y/N prompts."""
        init_registry()
        register_apparatus("writing", git_remote=None)
        self._queued_input(monkeypatch, [
            "1",
            "yes",
            "1",
            "cyber-zine",
            "n"
        ])
        assert prompt_apparatus_names() == ["writing", "cyber-zine"]


# ===========================================================================
# Stubs — Phase 2 placeholders
# ===========================================================================

class TestStubs:
    """
    commit_registry and push_registry don't do shit in Phase 1.
    Test only that they exist and don't raise — that's the entire contract
    until Phase 2 makes them actually do something.
    """

    def test_commit_registry_is_callable_and_does_not_raise(self):
        from archivist.utils.registry import commit_registry
        commit_registry("some message")  # must not raise

    def test_push_registry_is_callable_and_does_not_raise(self):
        from archivist.utils.registry import push_registry
        push_registry()  # must not raise