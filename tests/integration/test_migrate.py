"""
tests/integration/test_migrate.py

Integration tests for `archivist migrate` — the one-shot legacy flat
`.archivist` -> `.archivist/config.yaml` directory-form migration.

This is the home of the test the multi-apparati checklist has been missing
since §13.7: the v2-string `apparatus` migration case. See
TestApparatusV2StringCase below.

Every test calls run() directly against a real git_repo with a real legacy
config file on disk, and a real (isolated) registry where the test needs one.
No mocked subprocess, no mocked filesystem — see TESTING_SPECIFICATION.md for
why. input() is the one thing we do fake, because migrate is interactive by
design and a test suite that hangs on stdin is not a test suite.
"""

import argparse
import re
import subprocess
from pathlib import Path
from typing import cast

import pytest

import archivist.commands.migrate as migrate
import archivist.utils.registry as registry_module
from archivist.utils import (
    ConfigSchema,
    get_apparatus_by_name,
    get_module_apparati,
    get_module_by_path,
    init_registry,
    read_archivist_config,
    register_apparatus,
)

UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    """
    migrate.py calls register_apparatus()/register_module() when a registry
    is present. Per REGISTRY_TESTING_SPECIFICATION.md the isolation
    requirement is absolute and does not get a per-test opt-out — every test
    in this module gets it whether it touches the registry or not.

    Deliberately namespaced away from anything named ".archivist" — the
    module under migration has its own ".archivist" inside git_repo.path,
    and this fixture's fake registry home must never collide with it.

    One level deep, not two. init_registry() does
    `registry_dir.mkdir(exist_ok=True)` with no `parents=True` — correct for
    the real `~/.archivist`, since `~` always exists, but it means the fake
    dir's parent must already exist too. tmp_path always does; a synthetic
    "fake-registry-home" subdirectory wouldn't.
    """
    fake_dir = tmp_path / ".archivist-registry-home"
    monkeypatch.setattr(registry_module, "get_registry_dir", lambda: fake_dir)
    return fake_dir


@pytest.fixture(autouse = True)
def _stub_hook_sync(monkeypatch):
    """
    _sync_hooks_local() writes real files into .git/hooks. None of the
    apparatus-migration behaviour this module is testing cares whether hook
    sync ran — stub it to a no-op by default. The three tests in
    TestHookSyncPrompt that actually care override this with a spy.
    """
    monkeypatch.setattr(migrate, "_sync_hooks_local", lambda *a, **k: None)


def _migrate_args(**kwargs) -> argparse.Namespace:
    defaults = {"dry_run": False}
    return argparse.Namespace(**{**defaults, **kwargs})


def _queued_input(monkeypatch, responses: list[str]) -> None:
    """
    Feed canned answers to sequential input() calls in the order migrate.py
    asks for them. Raises loudly — not a hang, not a cryptic StopIteration —
    if migrate asks more questions than the test bothered to answer. That's
    a signal the test doesn't reflect the real prompt flow, not a fixture bug.
    """
    queue = iter(responses)

    def _fake_input(prompt: str = "") -> str:
        try:
            return next(queue)
        except StopIteration:
            raise AssertionError(
                f"migrate asked for input beyond the queued responses: {prompt!r}. "
                "Add another canned answer, or the test isn't exercising the "
                "real prompt sequence it thinks it is."
            )

    monkeypatch.setattr("builtins.input", _fake_input)


def _write_legacy_config(git_root: Path, content: str) -> None:
    """
    Overwrite the flat .archivist that the git_repo fixture seeds by default.
    Direct file write, not git_repo.stage() — migrate reads off disk and
    doesn't care whether the legacy file is staged before it runs.
    """
    (git_root / ".archivist").write_text(content, encoding="utf-8")


def _read_config(git_root: Path) -> ConfigSchema:
    """read_archivist_config() with the None-guard already done — see
    REGISTRY_TESTING_SPECIFICATION.md's nullable-field pattern. Every test
    below that needs the post-migration config goes through this."""
    result = read_archivist_config(git_root)
    assert result is not None, (
        f"No readable config found at {git_root} after migration. "
        "Either migration didn't write anything, or it wrote to the wrong place."
    )
    return result


def _all_files(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file()}


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

class TestGuards:

    def test_exits_cleanly_when_already_migrated(self, git_repo, monkeypatch):
        """
        .archivist/config.yaml already existing means the job's done. Exit 0,
        touch nothing — not even the flat file, which migrate would otherwise
        consider fair game to delete.
        """
        monkeypatch.chdir(git_repo.path)
        legacy = git_repo.path / ".archivist"
        legacy.unlink()
        archivist_dir = git_repo.path / ".archivist"
        archivist_dir.mkdir()
        original = "uuid: already-here\nmodule-type: general\n"
        (archivist_dir / "config.yaml").write_text(original, encoding="utf-8")

        with pytest.raises(SystemExit) as exc_info:
            migrate.run(_migrate_args())
        assert exc_info.value.code == 0

        assert (archivist_dir / "config.yaml").read_text(encoding="utf-8") == original, (
            "Already-migrated guard fired, but the existing config.yaml was "
            "touched anyway. The guard is supposed to be a no-op."
        )

    def test_exits_with_error_when_no_legacy_file(self, git_repo, monkeypatch):
        """No flat .archivist, no .archivist/ directory — nothing to migrate from."""
        monkeypatch.chdir(git_repo.path)
        (git_repo.path / ".archivist").unlink()

        with pytest.raises(SystemExit) as exc_info:
            migrate.run(_migrate_args())
        assert exc_info.value.code == 1

    def test_exits_with_error_when_legacy_path_is_a_bare_directory(self, git_repo, monkeypatch):
        """
        A .archivist/ directory with no config.yaml inside is neither a valid
        legacy file nor a completed migration. It's nothing migrate recognizes.
        """
        monkeypatch.chdir(git_repo.path)
        (git_repo.path / ".archivist").unlink()
        (git_repo.path / ".archivist").mkdir()

        with pytest.raises(SystemExit) as exc_info:
            migrate.run(_migrate_args())
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# apparatus: true / "true"  (bool and quoted-string placeholder)
# ---------------------------------------------------------------------------

class TestApparatusBooleanTrueCase:

    def test_bool_true_prompts_for_name_and_rewrites_to_apparati(self, git_repo, monkeypatch):
        """
        apparatus: true (unquoted — Python bool True) is a placeholder. It
        needs a real name, which only a live human (or our queued input) can
        provide. No registry exists in this test, so nothing gets registered —
        only the config rewrite is under test here.
        """
        monkeypatch.chdir(git_repo.path)
        _write_legacy_config(git_repo.path, "module-type: general\napparatus: true\n")
        _queued_input(monkeypatch, ["writing", "n", "y", "n"])

        migrate.run(_migrate_args())

        result = _read_config(git_repo.path)
        assert result.get("apparati") == ["writing"], (
            f"Expected apparati: ['writing'], got {result.get('apparati')!r}."
        )
        assert result.get("apparatus") is None, (
            "The old 'apparatus' key survived the bool-true migration. "
            "It must be fully replaced by 'apparati', never coexist with it."
        )
        assert result.get("module-type") == "general"

    def test_quoted_string_true_is_treated_identically_to_bool_true(self, git_repo, monkeypatch):
        """
        apparatus: "true" (quoted — Python str "true") means the same thing
        as the unquoted bool. YAML's quoting rules shouldn't change the
        migration outcome.
        """
        monkeypatch.chdir(git_repo.path)
        _write_legacy_config(git_repo.path, 'module-type: general\napparatus: "true"\n')
        _queued_input(monkeypatch, ["writing", "n", "y", "n"])

        migrate.run(_migrate_args())

        result = _read_config(git_repo.path)
        assert result.get("apparati") == ["writing"], (
            f"Quoted 'apparatus: \"true\"' didn't migrate the same way as the "
            f"bool form. Got apparati={result.get('apparati')!r}."
        )
        assert result.get("apparatus") is None

    def test_bool_true_registers_module_in_existing_registry(self, git_repo, monkeypatch):
        """
        When a registry already exists, the prompted name isn't just written
        to config — it gets registered for real: apparatus row, module row,
        module_apparatus row, the whole chain.
        """
        monkeypatch.chdir(git_repo.path)
        init_registry()
        _write_legacy_config(git_repo.path, "module-type: general\napparatus: true\n")
        _queued_input(monkeypatch, ["writing", "n", "y", "n"])

        migrate.run(_migrate_args())

        result = _read_config(git_repo.path)
        module_uuid = result.get("uuid")
        assert module_uuid, "No uuid written to config after registry-backed migration."

        assert get_apparatus_by_name("writing") is not None, (
            "Apparatus 'writing' wasn't registered even though a registry existed."
        )
        registered = get_module_by_path(git_repo.path)
        assert registered is not None, "Module wasn't registered in the registry at all."
        assert registered["uuid"] == module_uuid, (
            "registry.db's module UUID doesn't match what was written to config.yaml. "
            "register_module() is supposed to be the single source of truth here."
        )
        memberships = get_module_apparati(module_uuid)
        assert any(a["name"] == "writing" for a in memberships), (
            "Module registered, but it's not a member of 'writing' in module_apparatus."
        )

    def test_bool_true_multi_select_registers_in_all_chosen_apparati(self, git_repo, monkeypatch):
        """
        The whole point of the multi-select prompt: picking more than one
        apparatus for the 'true' placeholder must register the module in
        ALL of them, not just the first — and the config must end up with
        the full list, not a truncated one.
        """
        monkeypatch.chdir(git_repo.path)
        init_registry()
        register_apparatus("cyber", git_remote=None)
        _write_legacy_config(git_repo.path, "module-type: general\napparatus: true\n")
        # Prompt options: 1. cyber  2. Create new. "1, 2" picks cyber AND
        # create-new in the same line; the slug sub-prompt then asks for the
        # new name ("writing"); "n" declines a third round.
        _queued_input(monkeypatch, ["1, 2", "writing", "n", "y", "n"])

        migrate.run(_migrate_args())

        result = _read_config(git_repo.path)
        apparati = result.get("apparati")
        assert apparati is not None and set(apparati) == {"cyber", "writing"}, (
            f"Expected apparati containing both 'cyber' and 'writing'. Got {apparati!r}."
        )

        module_uuid = result.get("uuid")
        assert module_uuid, "No uuid written to config after multi-select migration."

        memberships = get_module_apparati(module_uuid)
        membership_names = {a["name"] for a in memberships}
        assert membership_names == {"cyber", "writing"}, (
            f"Expected module_apparatus rows for both 'cyber' and 'writing'. Got "
            f"{membership_names!r}. register_module() only wires up the FIRST "
            "apparatus on creation — extras need their own add_module_to_apparatus() "
            "call, and this is the test that catches a regression there."
        )


# ---------------------------------------------------------------------------
# apparatus: false / "false"
# ---------------------------------------------------------------------------

class TestApparatusFalseCase:

    def test_bool_false_drops_the_key_entirely(self, git_repo, monkeypatch):
        """
        apparatus: false means "no apparatus, never had one." The key must
        be dropped — not converted to apparati: [], just gone.
        """
        monkeypatch.chdir(git_repo.path)
        _write_legacy_config(git_repo.path, "module-type: general\napparatus: false\n")
        _queued_input(monkeypatch, ["y", "n"])

        migrate.run(_migrate_args())

        result = _read_config(git_repo.path)
        assert result.get("apparatus") is None
        assert result.get("apparati") is None, (
            f"apparatus: false should drop the key entirely, not write an "
            f"empty/placeholder apparati list. Got {result.get('apparati')!r}."
        )

    def test_quoted_string_false_drops_the_key_entirely(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        _write_legacy_config(git_repo.path, 'module-type: general\napparatus: "false"\n')
        _queued_input(monkeypatch, ["y", "n"])

        migrate.run(_migrate_args())

        result = _read_config(git_repo.path)
        assert result.get("apparatus") is None
        assert result.get("apparati") is None


# ---------------------------------------------------------------------------
# apparatus: "<name>"  — the v2 single-apparatus string form.
# This is the test §13.7 has been missing since the multi-apparati remediation.
# ---------------------------------------------------------------------------

class TestApparatusV2StringCase:

    def test_v2_string_name_rewrites_to_apparati_list(self, git_repo, monkeypatch):
        """
        The exact checklist case: apparatus: "writing" -> apparati: [writing].
        No prompt fires here — the real name is already known, so there's
        nothing to ask the user. Only "y"/"n" for the two non-apparatus
        prompts are queued.
        """
        monkeypatch.chdir(git_repo.path)
        _write_legacy_config(git_repo.path, "module-type: general\napparatus: writing\n")
        _queued_input(monkeypatch, ["y", "n"])

        migrate.run(_migrate_args())

        result = _read_config(git_repo.path)
        assert result.get("apparati") == ["writing"], (
            f"v2 string 'apparatus: writing' did not rewrite to apparati: "
            f"['writing']. Got {result.get('apparati')!r}."
        )
        assert result.get("apparatus") is None, (
            "v2 string case left the old 'apparatus' key behind alongside "
            "the new 'apparati' key. Only one of these may exist at a time."
        )

    def test_v2_string_name_registers_in_existing_registry(self, git_repo, monkeypatch):
        """
        Same as the bool-true registry test, but for the v2 string path —
        confirming the "if registry exists" registration isn't accidentally
        gated on the interactive-prompt code path only.
        """
        monkeypatch.chdir(git_repo.path)
        init_registry()
        _write_legacy_config(git_repo.path, "module-type: general\napparatus: writing\n")
        _queued_input(monkeypatch, ["y", "n"])

        migrate.run(_migrate_args())

        result = _read_config(git_repo.path)
        module_uuid = cast(str, result.get("uuid"))
        registered = get_module_by_path(git_repo.path)
        assert registered is not None, "v2-string migration didn't register the module."
        assert registered["uuid"] == module_uuid

        memberships = get_module_apparati(module_uuid)
        assert any(a["name"] == "writing" for a in memberships), (
            "Module wasn't added to 'writing' in module_apparatus during "
            "v2-string migration with a live registry present."
        )

    def test_v2_string_name_with_no_registry_does_not_crash(self, git_repo, monkeypatch):
        """
        The common real-world case: a solo module with apparatus: "writing"
        but no registry on this machine at all. Migration must still succeed —
        registration is opportunistic, not required.
        """
        monkeypatch.chdir(git_repo.path)
        _write_legacy_config(git_repo.path, "module-type: general\napparatus: writing\n")
        _queued_input(monkeypatch, ["y", "n"])

        migrate.run(_migrate_args())  # must not raise

        result = _read_config(git_repo.path)
        assert result.get("apparati") == ["writing"]


@pytest.mark.parametrize(
    "legacy_apparatus_line,queued_inputs",
    [
        ("apparatus: true", ["writing", "n", "y", "n"]),
        ("apparatus: false", ["y", "n"]),
        ("apparatus: writing", ["y", "n"]),
    ],
    ids=["bool-true", "bool-false", "v2-string"],
)
def test_apparatus_and_apparati_never_coexist(
    git_repo, monkeypatch, legacy_apparatus_line, queued_inputs
):
    """
    Direct pin for the §13.5 requirement: all three migration cases must
    write 'apparati' (plural) and never leave 'apparatus' (singular) behind,
    regardless of which of the three branches handled it.
    """
    monkeypatch.chdir(git_repo.path)
    _write_legacy_config(git_repo.path, f"module-type: general\n{legacy_apparatus_line}\n")
    _queued_input(monkeypatch, queued_inputs)

    migrate.run(_migrate_args())

    result = _read_config(git_repo.path)
    assert result.get("apparatus") is None, (
        f"Case {legacy_apparatus_line!r} left a singular 'apparatus' key in "
        f"the output config: {result.get('apparatus')!r}."
    )


# ---------------------------------------------------------------------------
# UUID handling
# ---------------------------------------------------------------------------

class TestUuidHandling:

    def test_generates_uuid_when_absent(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        _write_legacy_config(git_repo.path, "module-type: general\napparatus: false\n")
        _queued_input(monkeypatch, ["y", "n"])

        migrate.run(_migrate_args())

        result = _read_config(git_repo.path)
        uuid_value = result.get("uuid")
        assert uuid_value and UUID4_RE.match(str(uuid_value)), (
            f"No valid UUID4 was generated. Got {uuid_value!r}."
        )

    def test_preserves_existing_uuid_when_no_registry_involved(self, git_repo, monkeypatch):
        """
        Without a live registry forcing register_module() to mint a fresh
        UUID, an existing uuid in the legacy config must survive untouched.
        """
        monkeypatch.chdir(git_repo.path)
        existing = "deadbeef-0000-4000-8000-000000000000"
        _write_legacy_config(
            git_repo.path,
            f"uuid: {existing}\nmodule-type: general\napparatus: false\n",
        )
        _queued_input(monkeypatch, ["y", "n"])

        migrate.run(_migrate_args())

        result = _read_config(git_repo.path)
        assert result.get("uuid") == existing, (
            f"Existing uuid {existing!r} was not preserved; got "
            f"{result.get('uuid')!r} instead."
        )

    def test_uuid_is_first_non_comment_key_written(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        _write_legacy_config(
            git_repo.path,
            "module-type: general\napparatus: writing\ngit-remote: git@example.com:x.git\n",
        )
        _queued_input(monkeypatch, ["y", "n"])

        migrate.run(_migrate_args())

        content = (git_repo.path / ".archivist" / "config.yaml").read_text(encoding="utf-8")
        non_comment_lines = [
            line for line in content.splitlines() if line.strip() and not line.startswith("#")
        ]
        assert non_comment_lines, "Migrated config.yaml has no substantive content."
        assert non_comment_lines[0].startswith("uuid:"), (
            f"uuid was not the first key written. First line: {non_comment_lines[0]!r}."
        )

    def test_registry_backed_migration_uuid_overrides_legacy_uuid(self, git_repo, monkeypatch):
        """
        Documents real, intentional behaviour from the migrate.py docstring:
        when a registry exists and the module gets registered, register_module()
        is the source of truth for the UUID — even if the legacy config already
        had one. This is not a bug; pin it so nobody "fixes" it by accident.
        """
        monkeypatch.chdir(git_repo.path)
        init_registry()
        stale_uuid = "11111111-0000-4000-8000-000000000000"
        _write_legacy_config(
            git_repo.path,
            f"uuid: {stale_uuid}\nmodule-type: general\napparatus: writing\n",
        )
        _queued_input(monkeypatch, ["y", "n"])

        migrate.run(_migrate_args())

        result = _read_config(git_repo.path)
        registered = get_module_by_path(git_repo.path)
        assert registered is not None
        assert result.get("uuid") == registered["uuid"], (
            "Config uuid and registry uuid disagree after a registry-backed migration."
        )
        assert result.get("uuid") != stale_uuid, (
            "Stale legacy uuid survived a registry-backed migration. "
            "register_module() should have minted a fresh one for this "
            "never-before-registered path — if this assertion now fails "
            "because register_module() started accepting a UUID argument, "
            "update this test deliberately; don't just delete it."
        )


# ---------------------------------------------------------------------------
# File and git mechanics
# ---------------------------------------------------------------------------

class TestFileAndGitMechanics:

    def test_flat_file_deleted_after_migration(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        _write_legacy_config(git_repo.path, "module-type: general\napparatus: false\n")
        _queued_input(monkeypatch, ["y", "n"])

        migrate.run(_migrate_args())

        legacy = git_repo.path / ".archivist"
        assert legacy.is_dir(), (
            ".archivist should now be the new directory form, not the old flat file."
        )
        assert not (legacy.is_file()), "The flat .archivist file should be gone."

    def test_directory_form_config_created(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        _write_legacy_config(git_repo.path, "module-type: general\napparatus: false\n")
        _queued_input(monkeypatch, ["y", "n"])

        migrate.run(_migrate_args())

        assert (git_repo.path / ".archivist" / "config.yaml").exists()

    def test_changes_staged_in_git_index(self, git_repo, monkeypatch):
        """
        Both halves of the migration — the new directory and the flat-file
        deletion — must land in the index automatically, same as
        `git submodule add` stages .gitmodules without being asked.
        """
        monkeypatch.chdir(git_repo.path)
        _write_legacy_config(git_repo.path, "module-type: general\napparatus: false\n")
        _queued_input(monkeypatch, ["y", "n"])

        migrate.run(_migrate_args())

        staged = subprocess.run(
            ["git", "diff", "--cached", "--no-renames", "--name-status"],
            cwd=git_repo.path, capture_output=True, text=True, check=True,
        ).stdout
        assert "A\t.archivist/config.yaml" in staged, (
            f"New config.yaml wasn't staged as an addition. Index shows:\n{staged}"
        )
        assert "D\t.archivist" in staged, (
            f"Flat .archivist deletion wasn't staged. Index shows:\n{staged}"
        )


# ---------------------------------------------------------------------------
# Confirmation prompt
# ---------------------------------------------------------------------------

class TestConfirmationPrompt:

    def test_declining_confirmation_aborts_with_no_changes(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        _write_legacy_config(git_repo.path, "module-type: general\napparatus: false\n")
        before = _all_files(git_repo.path)
        _queued_input(monkeypatch, ["n"])

        with pytest.raises(SystemExit) as exc_info:
            migrate.run(_migrate_args())
        assert exc_info.value.code == 0

        after = _all_files(git_repo.path)
        assert before == after, (
            "Declining the confirmation prompt still resulted in file changes."
        )

    def test_full_word_yes_accepted(self, git_repo, monkeypatch):
        """'yes' (full word), not just 'y', must be accepted — same convention
        used by _wait_for_save_confirmation elsewhere in the suite."""
        monkeypatch.chdir(git_repo.path)
        _write_legacy_config(git_repo.path, "module-type: general\napparatus: false\n")
        _queued_input(monkeypatch, ["yes", "n"])

        migrate.run(_migrate_args())  # must not raise or abort

        assert (git_repo.path / ".archivist" / "config.yaml").exists()


# ---------------------------------------------------------------------------
# Dry-run contract
# ---------------------------------------------------------------------------

class TestDryRunContract:

    def test_dry_run_writes_absolutely_nothing(self, git_repo, monkeypatch):
        """
        Standard dry-run contract pattern. Deliberately uses an
        apparatus: false config — see the next test for why apparatus: true
        is a different, separately-pinned story.
        """
        monkeypatch.chdir(git_repo.path)
        _write_legacy_config(git_repo.path, "module-type: general\napparatus: false\n")
        before = _all_files(git_repo.path)

        migrate.run(_migrate_args(dry_run=True))  # no input() needed — returns before any prompt

        after = _all_files(git_repo.path)
        assert before == after, (
            "dry_run=True and files still changed. "
            "A dry run that writes is just called a run."
        )

    def test_dry_run_with_existing_registry_writes_nothing_there_either(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        init_registry()
        _write_legacy_config(git_repo.path, "module-type: general\napparatus: writing\n")

        migrate.run(_migrate_args(dry_run=True))

        assert get_apparatus_by_name("writing") is None, (
            "Dry-run registered an apparatus in the registry. It must execute neither."
        )
        assert get_module_by_path(git_repo.path) is None, (
            "Dry-run registered a module in the registry. It must execute neither."
        )

    def test_dry_run_with_apparatus_true_still_prompts_interactively(self, git_repo, monkeypatch):
        """
        Known wrinkle, pinned deliberately rather than left to surface as a
        surprise: the apparatus-name prompt for the bool/string-true case
        fires BEFORE the dry-run early-return in migrate.py. A --dry-run run
        against this config shape is NOT silent — it still asks a question.

        It does NOT write anything afterward; the dry-run contract on disk
        still holds. Only the "no interaction" expectation breaks for this
        one config shape. If this is fixed (prompt moved after the dry-run
        gate), this test should start failing on the input-queue assertion
        below — that's the signal to update it, not a regression.
        """
        monkeypatch.chdir(git_repo.path)
        _write_legacy_config(git_repo.path, "module-type: general\napparatus: true\n")
        before = _all_files(git_repo.path)
        _queued_input(monkeypatch, ["writing", "n"])  # name prompt + add-another; dry-run skips the rest

        migrate.run(_migrate_args(dry_run=True))

        after = _all_files(git_repo.path)
        assert before == after, "Even with the premature prompt, dry-run must still write nothing."


# ---------------------------------------------------------------------------
# Hook sync prompt
# ---------------------------------------------------------------------------

class TestHookSyncPrompt:

    def test_runs_hook_sync_when_user_accepts(self, git_repo, monkeypatch):
        calls = []
        monkeypatch.setattr(migrate, "_sync_hooks_local", lambda *a, **k: calls.append(1))
        monkeypatch.chdir(git_repo.path)
        _write_legacy_config(git_repo.path, "module-type: general\napparatus: false\n")
        _queued_input(monkeypatch, ["y", "y"])

        migrate.run(_migrate_args())

        assert calls, "User accepted the hook-sync prompt but _sync_hooks_local never ran."

    def test_skips_hook_sync_when_user_declines(self, git_repo, monkeypatch):
        calls = []
        monkeypatch.setattr(migrate, "_sync_hooks_local", lambda *a, **k: calls.append(1))
        monkeypatch.chdir(git_repo.path)
        _write_legacy_config(git_repo.path, "module-type: general\napparatus: false\n")
        _queued_input(monkeypatch, ["y", "n"])

        migrate.run(_migrate_args())

        assert not calls, "User declined the hook-sync prompt but _sync_hooks_local ran anyway."

    def test_empty_answer_defaults_to_yes(self, git_repo, monkeypatch):
        """The prompt is phrased [Y/n] — empty input must mean yes."""
        calls = []
        monkeypatch.setattr(migrate, "_sync_hooks_local", lambda *a, **k: calls.append(1))
        monkeypatch.chdir(git_repo.path)
        _write_legacy_config(git_repo.path, "module-type: general\napparatus: false\n")
        _queued_input(monkeypatch, ["y", ""])

        migrate.run(_migrate_args())

        assert calls, "Empty answer to the [Y/n] hook-sync prompt should default to yes."


# ---------------------------------------------------------------------------
# Sample changelog copy (library module-type only)
# ---------------------------------------------------------------------------

class TestSampleChangelogCopy:

    def test_copies_sample_changelog_for_library_module_type(self, git_repo, monkeypatch):
        calls = []
        monkeypatch.setattr(migrate, "_copy_sample_changelog", lambda *a, **k: calls.append(1))
        monkeypatch.chdir(git_repo.path)
        _write_legacy_config(git_repo.path, "module-type: library\napparatus: false\n")
        _queued_input(monkeypatch, ["y", "n"])

        migrate.run(_migrate_args())

        assert calls, "module-type: library should trigger _copy_sample_changelog(); it didn't."

    def test_does_not_copy_sample_changelog_for_non_library_module_type(self, git_repo, monkeypatch):
        calls = []
        monkeypatch.setattr(migrate, "_copy_sample_changelog", lambda *a, **k: calls.append(1))
        monkeypatch.chdir(git_repo.path)
        _write_legacy_config(git_repo.path, "module-type: general\napparatus: false\n")
        _queued_input(monkeypatch, ["y", "n"])

        migrate.run(_migrate_args())

        assert not calls, "module-type: general should never trigger _copy_sample_changelog()."