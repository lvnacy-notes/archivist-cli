"""
tests/integration/test_sync.py

Integration tests for `archivist sync`.

Per TESTING_SPECIFICATION.md philosophy: real git repos in tmp_path, real
`git submodule add`, no mocked subprocess calls. Nesting is exercised for
real because that's the entire point of this command — a mocked
`list_direct_submodules()` would just be re-asserting our own mock.

Fixtures used here — `make_git_repo`, `add_submodule`, `write_archivist_config`
— live in conftest.py, shared with the rest of the suite. `isolated_registry`
is autouse from conftest.py too; no test in this file touches it directly.

A note on uuids: register_module() has no way to register a module under a
caller-chosen uuid — it always mints its own or reuses one found by path.
So wherever a test pre-registers the vault directly (simulating "this was
already `archivist init`'d in a previous session"), it captures whatever
uuid that call actually returns and writes THAT into the vault's config —
never a separate hardcoded constant. Declaring one uuid in config while a
different one sits in the registry is exactly the bug this test suite
caught in production (see registry.py's register_known_module). _CHILD_UUID
and _GRANDCHILD_UUID are different: those modules are never pre-registered,
only their config is written — sync discovers and registers them for the
first time, and register_known_module() is what makes it honor those
declared uuids instead of minting its own.
"""

import argparse

import archivist.commands.sync as sync_module
from archivist.commands.sync import run
from archivist.utils import (
    get_module_bays,
    get_module_by_uuid,
    init_registry,
    register_apparatus,
    register_module,
)


def _args(dry_run: bool = False) -> argparse.Namespace:
    return argparse.Namespace(dry_run = dry_run)


_CHILD_UUID = "22222222-2222-2222-2222-222222222222"
_GRANDCHILD_UUID = "33333333-3333-3333-3333-333333333333"


# ===========================================================================
# No submodules at all
# ===========================================================================

class TestNoSubmodules:
    def test_no_submodules_does_not_crash(
        self,
        tmp_path,
        monkeypatch,
        make_git_repo
    ):
        repo = make_git_repo(tmp_path / "lonely-vault")
        monkeypatch.chdir(repo)
        init_registry()

        monkeypatch.setattr(
            sync_module,
            "_initialize_project",
            lambda args: None
        )

        run(_args())  # must not raise

    def test_no_submodules_reports_legacy_config_migration(
        self,
        tmp_path,
        monkeypatch,
        make_git_repo,
        capsys
    ):
        """
        `migrate` no longer exists — a legacy flat .archivist config now
        routes straight to `init`, which handles the flat-to-directory move
        itself (and actually does something useful with the registry while
        it's at it, unlike the old `migrate` command).
        """
        repo = make_git_repo(tmp_path / "lonely-vault")
        monkeypatch.chdir(repo)
        init_registry()

        init_calls = []

        def fake_init(args):
            init_calls.append(args)

        monkeypatch.setattr(
            sync_module,
            "_initialize_project",
            fake_init
        )

        run(_args())

        out = capsys.readouterr().out
        assert "Found a legacy flat .archivist config" in out, (
            f"Expected sync to detect the legacy config and route to `init`. Got: { out!r }"
        )
        assert init_calls == [argparse.Namespace(dry_run = False)], (
            "sync should hand legacy flat configs off to `init` — `migrate` is gone."
        )


# ===========================================================================
# Single-level linking
# ===========================================================================

class TestSingleLevelLink:
    def test_registered_submodule_gets_linked_to_registered_parent(
        self,
        tmp_path,
        monkeypatch,
        make_git_repo,
        add_submodule,
        write_archivist_config
    ):
        """
        The core case: parent is a registered vault, child is a submodule
        with a declared apparatus. Sync must register the child (if not
        already) and create the module_bays row.
        """
        init_registry()
        register_apparatus("writing", git_remote = None)

        vault = make_git_repo(tmp_path / "vault")
        vault_uuid = register_module(
            apparatus_name = "writing",
            name = "vault",
            module_type = "vault",
            path = vault,
            git_remote = None,
        )
        write_archivist_config(
            vault,
            vault_uuid,
            apparati = ["writing"],
            module_type = "vault"
        )

        child_source = make_git_repo(tmp_path / "child-source")
        write_archivist_config(
            child_source,
            _CHILD_UUID,
            apparati = ["writing"]
        )
        add_submodule(
            vault,
            child_source,
            "modules/child"
        )

        monkeypatch.chdir(vault)
        run(_args())

        child_row = get_module_by_uuid(_CHILD_UUID)
        assert child_row is not None, "sync did not register the submodule"

        vault_row = get_module_by_uuid(vault_uuid)
        assert vault_row is not None, "registered vault row was not found in registry"
        bays = get_module_bays(_CHILD_UUID)
        assert any(b["uuid"] == vault_row["uuid"] for b in bays), (
            "No module_bays row linking the submodule to its registered parent."
        )

    def test_dry_run_creates_no_bay_row_and_registers_nothing(
        self,
        tmp_path,
        monkeypatch,
        make_git_repo,
        add_submodule,
        write_archivist_config
    ):
        init_registry()
        register_apparatus("writing", git_remote = None)

        vault = make_git_repo(tmp_path / "vault")
        vault_uuid = register_module(
            apparatus_name = "writing",
            name = "vault",
            module_type = "vault",
            path = vault,
            git_remote = None,
        )
        write_archivist_config(
            vault,
            vault_uuid,
            apparati = ["writing"],
            module_type = "vault"
        )

        child_source = make_git_repo(tmp_path / "child-source")
        write_archivist_config(
            child_source,
            _CHILD_UUID,
            apparati=["writing"]
        )
        add_submodule(
            vault,
            child_source,
            "modules/child"
        )

        monkeypatch.chdir(vault)
        run(_args(dry_run = True))

        assert get_module_by_uuid(_CHILD_UUID) is None, (
            "dry_run=True registered a module. Dry runs must not touch the registry."
        )
        assert get_module_bays(_CHILD_UUID) == [], (
            "dry_run=True created a bay row. Dry runs must not touch the registry."
        )


# ===========================================================================
# Non-interactive skip paths — sync must never guess
# ===========================================================================

class TestNonInteractiveSkips:
    def test_sync_prompts_to_initialize_when_repo_has_no_config(
        self,
        tmp_path,
        monkeypatch,
        make_git_repo,
    ):
        repo = make_git_repo(tmp_path / "repo")
        (repo / ".archivist").unlink()

        init_calls = []

        def fake_init(args):
            init_calls.append(args)

        monkeypatch.setattr(
            sync_module,
            "_initialize_project",
            fake_init
        )
        monkeypatch.chdir(repo)
        init_registry()

        run(_args())

        assert init_calls == [argparse.Namespace(dry_run = False)], (
            "sync should prompt to initialize when the repo has no Archivist config."
        )

    def test_directory_config_missing_uuid_routes_to_init(
        self,
        tmp_path,
        monkeypatch,
        make_git_repo,
    ):
        """
        A directory-form config (`.archivist/config.yaml`) missing a uuid
        used to get routed to a `migrate` command that could never fix it —
        `migrate` was purely structural ("content is not modified") and
        would just say "already on the directory form" and exit, leaving
        the project permanently un-registrable via `sync`. `migrate` no
        longer exists; `init` handles this directly.
        """
        repo = make_git_repo(tmp_path / "repo-no-uuid")
        (repo / ".archivist").unlink()
        archivist_dir = repo / ".archivist"
        archivist_dir.mkdir()
        (archivist_dir / "config.yaml").write_text(
            "module-type: general\n", encoding = "utf-8"
        )

        init_calls = []

        monkeypatch.setattr(
            sync_module,
            "_initialize_project",
            lambda args: init_calls.append(args)
        )
        monkeypatch.chdir(repo)
        init_registry()

        run(_args())

        assert init_calls == [argparse.Namespace(dry_run = False)], (
            "A directory-form config with no uuid must route to `init` — "
            "it's the only command that can actually assign one."
        )

    def test_sync_prompts_to_initialize_when_repo_has_legacy_config(
        self,
        tmp_path,
        monkeypatch,
        make_git_repo,
    ):
        """
        `migrate` is gone — a legacy flat .archivist config now routes
        straight to `init`, which handles the flat-to-directory move itself.
        """
        repo = make_git_repo(tmp_path / "repo-legacy")
        (repo / ".archivist").write_text("module-type: general\n", encoding = "utf-8")

        init_calls = []

        monkeypatch.setattr(
            sync_module,
            "_initialize_project",
            lambda args: init_calls.append(args)
        )
        monkeypatch.chdir(repo)
        init_registry()

        run(_args())

        assert init_calls == [argparse.Namespace(dry_run = False)], (
            "sync should hand a legacy flat config off to `init` instead of skipping it."
        )

    def test_submodule_with_no_config_is_skipped_not_crashed(
        self,
        tmp_path,
        monkeypatch,
        make_git_repo,
        add_submodule,
        write_archivist_config
    ):
        init_registry()
        register_apparatus("writing", git_remote = None)

        vault = make_git_repo(tmp_path / "vault")
        vault_uuid = register_module(
            apparatus_name = "writing",
            name = "vault",
            module_type = "vault",
            path = vault,
            git_remote = None,
        )
        write_archivist_config(
            vault,
            vault_uuid,
            apparati = ["writing"],
            module_type = "vault"
        )

        # Bare submodule, no .archivist config at all
        bare_source = make_git_repo(tmp_path / "bare-source")
        add_submodule(
            vault,
            bare_source,
            "modules/bare"
        )

        monkeypatch.chdir(vault)
        run(_args())  # must not raise

    def test_uuid_without_apparati_and_unregistered_is_skipped(
        self,
        tmp_path,
        monkeypatch,
        make_git_repo,
        add_submodule,
        write_archivist_config
    ):
        """
        A config with a uuid but no apparati, and no existing registry row,
        is exactly the case sync must refuse to guess at. It should skip,
        not register the module under an apparatus nobody chose.
        """
        init_registry()
        register_apparatus("writing", git_remote = None)

        vault = make_git_repo(tmp_path / "vault")
        vault_uuid = register_module(
            apparatus_name = "writing",
            name = "vault",
            module_type = "vault",
            path = vault,
            git_remote = None,
        )
        write_archivist_config(
            vault,
            vault_uuid,
            apparati = ["writing"],
            module_type = "vault"
        )

        undecided_source = make_git_repo(tmp_path / "undecided-source")
        write_archivist_config(
            undecided_source,
            _CHILD_UUID,
            apparati = None
        )
        add_submodule(
            vault,
            undecided_source,
            "modules/undecided"
        )

        monkeypatch.chdir(vault)
        run(_args())

        assert get_module_by_uuid(_CHILD_UUID) is None, (
            "sync registered a module with no declared apparatus. "
            "It must never invent an apparatus assignment."
        )


# ===========================================================================
# Nesting — module-inside-a-module, not vault-exclusive
# ===========================================================================

class TestNestedDepth:
    def test_grandchild_module_gets_linked_to_its_direct_parent(
        self,
        tmp_path,
        monkeypatch,
        make_git_repo,
        add_submodule,
        write_archivist_config
    ):
        """
        vault -> child (a plain 'general' module, not a vault) -> grandchild.
        The grandchild must link to the CHILD, not to the vault — containment
        is direct-parent-only, and nesting is not restricted to vault
        containers.
        """
        init_registry()
        register_apparatus("writing", git_remote = None)

        vault = make_git_repo(tmp_path / "vault")
        vault_uuid = register_module(
            apparatus_name = "writing",
            name = "vault",
            module_type = "vault",
            path = vault,
            git_remote = None,
        )
        write_archivist_config(
            vault,
            vault_uuid,
            apparati = ["writing"],
            module_type = "vault"
        )

        # Child: a real git repo with a config AND its own nested submodule,
        # built independently before being pulled into the vault.
        child_source = make_git_repo(tmp_path / "child-source")
        write_archivist_config(
            child_source,
            _CHILD_UUID,
            apparati = ["writing"],
            module_type = "general"
        )

        grandchild_source = make_git_repo(tmp_path / "grandchild-source")
        write_archivist_config(
            grandchild_source,
            _GRANDCHILD_UUID,
            apparati = ["writing"]
        )
        add_submodule(
            child_source,
            grandchild_source,
            "nested"
        )

        add_submodule(
            vault,
            child_source,
            "modules/child"
        )

        monkeypatch.chdir(vault)
        run(_args())

        child_row = get_module_by_uuid(_CHILD_UUID)
        grandchild_row = get_module_by_uuid(_GRANDCHILD_UUID)
        assert child_row is not None, "child module was not registered"
        assert grandchild_row is not None, "grandchild module was not registered"

        grandchild_bays = get_module_bays(_GRANDCHILD_UUID)
        assert any(b["uuid"] == child_row["uuid"] for b in grandchild_bays), (
            "Grandchild must be linked to its direct parent (child), not skipped or "
            "attached to the vault instead."
        )

        vault_row = get_module_by_uuid(vault_uuid)
        assert vault_row is not None, "registered vault row was not found in registry"
        assert not any(b["uuid"] == vault_row["uuid"] for b in grandchild_bays), (
            "Grandchild was linked directly to the vault, skipping its actual "
            "direct container. Containment must reflect the real tree shape."
        )


# ===========================================================================
# Stale path refresh
# ===========================================================================

class TestStalePathRefresh:
    def test_renamed_container_directory_still_resolves_and_refreshes_path(
        self,
        tmp_path,
        monkeypatch,
        make_git_repo,
        add_submodule,
        write_archivist_config
    ):
        """
        Container was registered under its original path. The directory then
        got renamed (simulating a user reorganizing their project layout).
        sync must still resolve it by uuid, link correctly, and refresh the
        stale path in the registry as a side effect.
        """
        init_registry()
        register_apparatus("writing", git_remote = None)

        original_vault = make_git_repo(tmp_path / "vault-original-name")
        original_vault_uuid = register_module(
            apparatus_name = "writing",
            name = "vault",
            module_type = "vault",
            path = original_vault,
            git_remote = None,
        )
        write_archivist_config(
            original_vault,
            original_vault_uuid,
            apparati = ["writing"],
            module_type = "vault"
        )

        child_source = make_git_repo(tmp_path / "child-source")
        write_archivist_config(
            child_source,
            _CHILD_UUID,
            apparati = ["writing"]
        )
        add_submodule(
            original_vault,
            child_source,
            "modules/child"
        )

        renamed_vault = tmp_path / "vault-renamed"
        original_vault.rename(renamed_vault)

        monkeypatch.chdir(renamed_vault)
        run(_args())

        vault_row = get_module_by_uuid(original_vault_uuid)
        assert vault_row is not None, "registered vault row was not found in registry"
        assert vault_row["path"] == str(renamed_vault.resolve()), (
            "Registry still holds the pre-rename path — sync should refresh it "
            "the moment it resolves the container by uuid."
        )

        child_row = get_module_by_uuid(_CHILD_UUID)
        bays = get_module_bays(_CHILD_UUID)
        assert any(b["uuid"] == vault_row["uuid"] for b in bays), (
            "Child was not linked after its container was renamed — sync must "
            "resolve containers by uuid, not by a now-stale path string."
        )


# ===========================================================================
# Idempotent reruns
# ===========================================================================

class TestIdempotentRerun:
    def test_running_sync_twice_does_not_duplicate_bay_rows(
        self,
        tmp_path,
        monkeypatch,
        make_git_repo,
        add_submodule,
        write_archivist_config
    ):
        init_registry()
        register_apparatus("writing", git_remote = None)

        vault = make_git_repo(tmp_path / "vault")
        vault_uuid = register_module(
            apparatus_name = "writing",
            name = "vault",
            module_type = "vault",
            path = vault,
            git_remote = None,
        )
        write_archivist_config(
            vault,
            vault_uuid,
            apparati = ["writing"],
            module_type = "vault"
        )

        child_source = make_git_repo(tmp_path / "child-source")
        write_archivist_config(
            child_source,
            _CHILD_UUID,
            apparati = ["writing"]
        )
        add_submodule(
            vault,
            child_source,
            "modules/child"
        )

        monkeypatch.chdir(vault)
        run(_args())
        run(_args())  # rerun — must not raise, must not duplicate

        bays = get_module_bays(_CHILD_UUID)
        assert len(bays) == 1, (
            f"Expected exactly one bay row after two sync runs, got { len(bays) }. "
            "add_module_to_bay is INSERT OR IGNORE — a rerun duplicating rows "
            "means that contract broke somewhere upstream."
        )