"""
tests/integration/test_deinit.py

Integration tests for `archivist deinit`.

Operation order is not negotiable — Apparatus first, git second — and the
tests are structured to verify that invariant explicitly. A failure in the
git step after a successful registry cleanup must leave the user with a
recoverable state, not a silent mess.

The same two isolation layers as test_add.py:

  1. Registry isolation — `isolated_registry` (autouse) patches get_registry_dir().
  2. Git/filesystem isolation — runner and remover callables are passed
     directly to run() via its injection seams. No subprocess or shutil
     globals are patched — the seams make that unnecessary.

Interactive confirmation (_confirm) is patched to return True by default in
most tests. Tests that specifically verify the confirmation flow patch it
individually.

Entry point under test: `archivist.commands.deinit.run(args)`.
"""

import argparse
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import archivist.utils.registry as registry_module
from archivist.commands.deinit import run
from archivist.utils import (
    add_module_to_bay,
    decimate_module,
    get_module_apparati,
    get_module_bays,
    get_module_by_path,
    get_module_by_uuid,
    init_registry,
    register_apparatus,
    register_module,
)


# ===========================================================================
# Isolation
# ===========================================================================

@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    """Redirect get_registry_dir() to tmp_path. Never touches ~/.archivist/."""
    fake_dir = tmp_path / ".archivist"
    # Patch in the registry module where the function is defined
    monkeypatch.setattr(
        registry_module,
        "get_registry_dir",
        lambda: fake_dir
    )
    return fake_dir


# ===========================================================================
# Helpers
# ===========================================================================

def _deinit_args(
    path: str,
    dry_run: bool = False,
    retain: bool = False,
    passthrough: list[str] | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        path = path,
        dry_run = dry_run,
        retain = retain,
        passthrough = passthrough or [],
    )


def _setup_apparatus(tmp_path: Path, apparatus_name: str = "writing") -> None:
    """Init registry and register an apparatus. Call before registering modules."""
    init_registry()
    register_apparatus(apparatus_name, git_remote = None)


def _register(
    tmp_path: Path,
    name: str,
    module_type: str = "library",
    apparatus: str = "writing",
    create_dir: bool = True,
) -> tuple[str, Path]:
    """Register a module and return (uuid, path)."""
    p = tmp_path / name
    if create_dir:
        p.mkdir(parents = True, exist_ok = True)
    uuid = register_module(
        apparatus,
        name,
        module_type,
        p,
        git_remote = None
    )
    return uuid, p


def _suppress_confirm(monkeypatch, answer: bool = True) -> None:
    """Patch _confirm so tests don't hang on stdin."""
    monkeypatch.setattr(
        "archivist.commands.deinit._confirm",
        lambda *a,
        **kw: answer
    )


def _noop_runner(*args, **kwargs) -> MagicMock:
    """Stand-in runner for _git_cleanup: succeeds silently, touches nothing."""
    return MagicMock(returncode = 0)


def _noop_remover(_path: Path) -> None:
    """Stand-in remover for _git_cleanup: accepts the path, does nothing."""


# ===========================================================================
# Dry-run contract
# ===========================================================================

class TestDryRun:
    """
    Confirmation fires on dry-run. Beyond that, absolutely nothing changes.
    No registry writes. No git operations. No filesystem mutations.
    """

    def test_dry_run_writes_absolutely_nothing(
        self,
        tmp_path,
        monkeypatch
    ):
        monkeypatch.chdir(tmp_path.parent)
        _suppress_confirm(monkeypatch)

        _setup_apparatus(tmp_path)
        _, target_path = _register(tmp_path, "cosmic-horror")

        before_files = {p for p in tmp_path.rglob("*") if p.is_file()}

        run(_deinit_args(str(target_path), dry_run = True))

        after_files = {p for p in tmp_path.rglob("*") if p.is_file()}
        assert before_files == after_files, (
            "dry_run=True and files still changed. A dry run that writes is just called a run."
        )

    def test_dry_run_does_not_touch_registry(
        self,
        tmp_path,
        monkeypatch
    ):
        monkeypatch.chdir(tmp_path.parent)
        _suppress_confirm(monkeypatch)

        _setup_apparatus(tmp_path)
        target_uuid, target_path = _register(tmp_path, "cosmic-horror")

        run(_deinit_args(str(target_path), dry_run = True))

        # Module must still be alive in the registry
        result = get_module_by_uuid(target_uuid)
        assert result is not None
        assert result["decimated_at"] is None, (
            "dry_run=True set decimated_at. "
            "Dry runs must not touch the registry."
        )

    def test_dry_run_does_not_execute_git(
        self,
        tmp_path,
        monkeypatch
    ):
        """
        dry_run=True exits before _git_cleanup is ever called, so runner is
        never invoked. The assertion here is structural: confirm that run()
        returns without touching the filesystem or spawning a process.
        We verify this by passing a runner that records calls — if it fires,
        the dry-run gate is broken.
        """
        monkeypatch.chdir(tmp_path.parent)
        _suppress_confirm(monkeypatch)

        _setup_apparatus(tmp_path)
        _, target_path = _register(tmp_path, "cosmic-horror")

        runner_called = []
        def _spy_runner(*args):
            runner_called.append(args)
            return MagicMock(returncode=0)

        run(
            _deinit_args(str(target_path), dry_run = True),
            runner = _spy_runner,
            remover = _noop_remover
        )

        assert not runner_called, (
            f"dry_run=True and the git runner was invoked: { runner_called }. "
            "The dry-run gate must prevent _git_cleanup from running at all."
        )

    def test_confirmation_prompt_fires_on_dry_run(
        self,
        tmp_path,
        monkeypatch
    ):
        """
        The spec explicitly calls this out: confirmation fires even on dry-run.
        A dry run that skips confirmation gives no useful information about
        what would actually happen.
        """
        monkeypatch.chdir(tmp_path.parent)

        _setup_apparatus(tmp_path)
        _, target_path = _register(tmp_path, "cosmic-horror")

        confirm_called = []
        monkeypatch.setattr(
            "archivist.commands.deinit._confirm",
            lambda *a, **kw: confirm_called.append(a) or True,
        )

        run(_deinit_args(str(target_path), dry_run = True))

        assert confirm_called, (
            "Confirmation prompt did not fire during dry-run. "
            "The spec requires it: 'a dry run that skips confirmation gives no "
            "useful information about what would actually happen.'"
        )

    def test_dry_run_prints_plan(
        self,
        tmp_path,
        monkeypatch,
        capsys
    ):
        monkeypatch.chdir(tmp_path.parent)
        _suppress_confirm(monkeypatch)

        _setup_apparatus(tmp_path)
        _, target_path = _register(tmp_path, "cosmic-horror")

        run(_deinit_args(str(target_path), dry_run=True))

        out = capsys.readouterr().out + capsys.readouterr().err
        assert "dry-run" in out.lower() or "would" in out.lower(), (
            "Dry-run produced no plan output. The user needs to know what would happen."
        )


# ===========================================================================
# Confirmation gate
# ===========================================================================

class TestConfirmation:

    def test_abort_on_no_confirmation(
        self,
        tmp_path,
        monkeypatch
    ):
        """
        User answers 'n' at the confirmation prompt → sys.exit(0); nothing changes.
        """
        monkeypatch.chdir(tmp_path.parent)
        _suppress_confirm(monkeypatch, answer = False)

        _setup_apparatus(tmp_path)
        target_uuid, target_path = _register(tmp_path, "cosmic-horror")

        with pytest.raises(SystemExit) as exc_info:
            run(_deinit_args(str(target_path)))

        assert exc_info.value.code == 0
        # Registry must be untouched
        result = get_module_by_uuid(target_uuid)
        assert result is not None
        assert result["decimated_at"] is None, (
            "Registry was modified after user declined confirmation. "
            "An aborted deinit must leave everything intact."
        )


# ===========================================================================
# Not registered
# ===========================================================================

class TestNotRegistered:

    def test_exits_with_warning_when_not_in_registry(
        self,
        tmp_path,
        monkeypatch
    ):
        """
        Path exists on disk but is not registered → warning + sys.exit(0).
        Not an error. The user may have already cleaned up manually.
        """
        monkeypatch.chdir(tmp_path.parent)
        _suppress_confirm(monkeypatch)
        init_registry()

        unregistered = tmp_path / "stranger-danger"
        unregistered.mkdir()

        with pytest.raises(SystemExit) as exc_info:
            run(_deinit_args(str(unregistered)))

        assert exc_info.value.code == 0

    def test_exits_when_registry_does_not_exist(
        self,
        tmp_path,
        monkeypatch
    ):
        """No registry at all → error + sys.exit(1)."""
        monkeypatch.chdir(tmp_path.parent)
        _suppress_confirm(monkeypatch)

        with pytest.raises(SystemExit) as exc_info:
            run(_deinit_args(str(tmp_path / "anything")))

        assert exc_info.value.code == 1


# ===========================================================================
# CWD guard
# ===========================================================================

class TestCwdGuard:

    def test_refuses_to_run_from_inside_target(
        self,
        tmp_path,
        monkeypatch
    ):
        """
        Running deinit from inside the module being removed would destroy the
        working directory mid-execution. Must be caught and rejected immediately.
        """
        _setup_apparatus(tmp_path)
        target_uuid, target_path = _register(tmp_path, "cosmic-horror")

        # chdir INTO the target
        monkeypatch.chdir(target_path)
        _suppress_confirm(monkeypatch)

        with pytest.raises(SystemExit) as exc_info:
            run(_deinit_args(str(target_path)))

        assert exc_info.value.code == 1
        # Registry must be untouched
        decimated = get_module_by_uuid(target_uuid)
        assert decimated is not None
        assert decimated["decimated_at"] is None


# ===========================================================================
# Happy path — single superproject
# ===========================================================================

class TestHappyPath:
    """
    Standard case: module is in exactly one superproject. deinit removes the
    bay row, decimates the module, and removes it from disk (or runs git
    submodule deinit+rm for submodules).
    """

    def test_bay_row_removed(
        self,
        tmp_path,
        monkeypatch
    ):
        monkeypatch.chdir(tmp_path.parent)
        _suppress_confirm(monkeypatch)

        _setup_apparatus(tmp_path)
        vault_uuid, _ = _register(tmp_path, "fiction-vault", module_type = "vault")
        target_uuid, target_path = _register(tmp_path, "cosmic-horror")
        add_module_to_bay(vault_uuid, target_uuid)

        # Run from the vault's parent (outside target)
        run(
            _deinit_args(str(target_path)),
            runner = _noop_runner,
            remover = _noop_remover
        )

        bays = get_module_bays(target_uuid)
        assert bays == [], (
            f"Bay row still exists after deinit. Expected 0 bays, got { len(bays) }."
        )

    def test_module_decimated(
        self,
        tmp_path,
        monkeypatch
    ):
        monkeypatch.chdir(tmp_path.parent)
        _suppress_confirm(monkeypatch)

        _setup_apparatus(tmp_path)
        _, target_path = _register(tmp_path, "cosmic-horror")

        run(
            _deinit_args(str(target_path)),
            runner = _noop_runner,
            remover = _noop_remover
        )

        result = get_module_by_path(target_path)
        assert result is not None
        assert result["decimated_at"] is not None, (
            "Module not decimated after deinit. "
            "A fully removed module with no remaining bays must be decimated."
        )

    def test_apparatus_first_git_second(
        self,
        tmp_path,
        monkeypatch
    ):
        """
        Operation order is the entire contract. Verify it by checking that the
        registry is already updated before git runs — instrument both and confirm
        the sequence.

        decimate_module is patched at the command module level to log the
        apparatus step. The remover seam logs the git step. No subprocess or
        shutil globals are touched.
        """
        monkeypatch.chdir(tmp_path.parent)
        _suppress_confirm(monkeypatch)

        _setup_apparatus(tmp_path)
        _, target_path = _register(tmp_path, "cosmic-horror")

        call_log: list[str] = []

        def _fake_decimate(uuid):
            call_log.append("apparatus")
            decimate_module(uuid)

        def _spy_remover(_path: Path) -> None:
            call_log.append("git")

        monkeypatch.setattr("archivist.commands.deinit.decimate_module", _fake_decimate)

        run(
            _deinit_args(str(target_path)),
            runner = _noop_runner,
            remover = _spy_remover
        )

        assert "apparatus" in call_log, "decimate_module never called"
        assert "git" in call_log, "remover never called"
        assert call_log.index("apparatus") < call_log.index("git"), (
            f"Git ran before Apparatus cleanup. Call order: {call_log}. "
            "Operation order is non-negotiable: Apparatus first, git second."
        )


# ===========================================================================
# Multiple bays
# ===========================================================================

class TestMultipleBays:
    """
    Module contained by more than one superproject. deinit from one superproject
    removes only that bay row. The module must NOT be decimated — it still has
    an active container.
    """

    def _setup_two_vaults(self, tmp_path) -> tuple[str, str, str, Path]:
        """
        Returns (vault_a_uuid, vault_b_uuid, target_uuid, target_path).
        """
        _setup_apparatus(tmp_path)
        vault_a_uuid, _ = _register(
            tmp_path,
            "vault-a",
            module_type = "vault"
        )
        vault_b_uuid, _ = _register(
            tmp_path,
            "vault-b",
            module_type = "vault"
        )
        target_uuid, target_path = _register(tmp_path, "cosmic-horror")
        add_module_to_bay(vault_a_uuid, target_uuid)
        add_module_to_bay(vault_b_uuid, target_uuid)
        return vault_a_uuid, vault_b_uuid, target_uuid, target_path

    def test_only_scoped_bay_removed(
        self,
        tmp_path,
        monkeypatch
    ):
        """
        Running deinit from vault-a's context must remove vault-a's bay row only.
        vault-b's relationship must survive untouched.
        """
        vault_a_uuid, vault_b_uuid, target_uuid, target_path = self._setup_two_vaults(tmp_path)

        # chdir to vault-a so deinit sees it as the scoped superproject
        vault_a_path = tmp_path / "vault-a"
        monkeypatch.chdir(vault_a_path)
        _suppress_confirm(monkeypatch)

        run(
            _deinit_args(str(target_path)),
            runner = _noop_runner,
            remover = _noop_remover
        )

        remaining_bays = get_module_bays(target_uuid)
        remaining_uuids = { b["uuid"] for b in remaining_bays }

        assert vault_a_uuid not in remaining_uuids, (
            "vault-a's bay row survived after scoped deinit from vault-a's context."
        )
        assert vault_b_uuid in remaining_uuids, (
            "vault-b's bay row was removed even though deinit was scoped to vault-a. "
            "Only the current superproject's bay must be removed."
        )

    def test_module_not_decimated_when_other_bays_remain(
        self,
        tmp_path,
        monkeypatch
    ):
        """
        After scoped removal, the module still has vault-b as a container.
        It must NOT be decimated — it's still an active, contained module.
        """
        vault_a_uuid, _, target_uuid, target_path = self._setup_two_vaults(tmp_path)

        vault_a_path = tmp_path / "vault-a"
        monkeypatch.chdir(vault_a_path)
        _suppress_confirm(monkeypatch)

        run(
            _deinit_args(str(target_path)),
            runner = _noop_runner,
            remover = _noop_remover
        )

        result = get_module_by_uuid(target_uuid)
        assert result is not None
        assert result["decimated_at"] is None, (
            "Module was decimated even though it still has an active container. "
            "Decimation must only happen when no bay rows remain."
        )

    def test_apparatus_memberships_intact_after_scoped_removal(
        self,
        tmp_path,
        monkeypatch
    ):
        """
        Vault-context deinit removes one bay row and does nothing else to the
        registry. Specifically: the module's apparatus membership rows must
        survive untouched — those are separate from bay containment and are
        only cleaned up by a standalone removal.

        This is the inverse of TestStandaloneRemoval.test_apparatus_memberships_
        removed_in_standalone_mode. Both paths must be pinned, both directions.
        """
        vault_a_uuid, _, target_uuid, target_path = self._setup_two_vaults(tmp_path)

        # Confirm at least one membership row exists before we do anything
        memberships_before = get_module_apparati(target_uuid)
        assert len(memberships_before) > 0, (
            "Test setup failed — module has no apparatus memberships before deinit."
        )

        vault_a_path = tmp_path / "vault-a"
        monkeypatch.chdir(vault_a_path)
        _suppress_confirm(monkeypatch)

        run(
            _deinit_args(str(target_path)),
            runner = _noop_runner,
            remover = _noop_remover
        )

        memberships_after = get_module_apparati(target_uuid)
        assert memberships_after == memberships_before, (
            f"Scoped deinit modified apparatus memberships. Before: "
            f"{[a['name'] for a in memberships_before]}, after: "
            f"{[a['name'] for a in memberships_after]}. "
            "Vault-context removal must only touch bay rows — apparatus "
            "membership cleanup is strictly a standalone-removal operation."
        )


# ===========================================================================
# Standalone removal
# ===========================================================================

class TestStandaloneRemoval:
    """
    deinit from a context that is NOT a registered superproject of the target.
    All bay relationships must be removed; module decimated.
    """

    def test_all_bays_removed_in_standalone_mode(
        self,
        tmp_path,
        monkeypatch
    ):
        monkeypatch.chdir(tmp_path.parent)
        _suppress_confirm(monkeypatch)

        _setup_apparatus(tmp_path)
        vault_a_uuid, _ = _register(
            tmp_path,
            "vault-a",
            module_type = "vault"
        )
        vault_b_uuid, _ = _register(
            tmp_path,
            "vault-b",
            module_type = "vault"
        )
        target_uuid, target_path = _register(tmp_path, "cosmic-horror")
        add_module_to_bay(vault_a_uuid, target_uuid)
        add_module_to_bay(vault_b_uuid, target_uuid)

        # cwd is tmp_path.parent — not registered as any module
        run(
            _deinit_args(str(target_path)),
            runner = _noop_runner,
            remover = _noop_remover
        )

        bays = get_module_bays(target_uuid)
        assert bays == [], (
            f"Bay rows remain after standalone removal. Expected 0, got { len(bays) }. "
            "Standalone deinit must clear all containment relationships."
        )

    def test_module_decimated_in_standalone_mode(
        self,
        tmp_path,
        monkeypatch
    ):
        monkeypatch.chdir(tmp_path.parent)
        _suppress_confirm(monkeypatch)

        _setup_apparatus(tmp_path)
        _, target_path = _register(tmp_path, "cosmic-horror")

        run(
            _deinit_args(str(target_path)),
            runner = _noop_runner,
            remover = _noop_remover
        )

        result = get_module_by_path(target_path)
        assert result is not None
        assert result["decimated_at"] is not None

    def test_apparatus_memberships_removed_in_standalone_mode(
        self,
        tmp_path,
        monkeypatch
    ):
        """
        Standalone deinit must wipe the module's apparatus memberships before
        decimating it. A decimated module with live membership rows is
        corrupted state — get_apparatus_modules() will hide it by default,
        but the junction rows are still there rotting, waiting to cause
        a problem when someone queries them directly.
        """
        monkeypatch.chdir(tmp_path.parent)
        _suppress_confirm(monkeypatch)

        _setup_apparatus(tmp_path)
        _, target_path = _register(tmp_path, "cosmic-horror")
        module = get_module_by_path(target_path)
        assert module is not None
        target_uuid = module["uuid"]

        # Confirm a membership row exists before we do anything
        assert len(get_module_apparati(target_uuid)) > 0, (
            "Test setup failed — module has no apparatus memberships to remove."
        )

        run(
            _deinit_args(str(target_path)),
            runner = _noop_runner,
            remover = _noop_remover
        )

        apparati = get_module_apparati(target_uuid)
        assert apparati == [], (
            f"Standalone deinit left {len(apparati)} apparatus membership row(s) behind. "
            "remove_all_apparatus_memberships() must be called before decimate_module()."
        )


# ===========================================================================
# --retain flag
# ===========================================================================


class TestRetain:
    """
    --retain: run the Apparatus cleanup but skip the git step entirely.
    The module must still be on disk after deinit. Useful when the git tree
    has already been cleaned up manually.
    """

    def test_retain_skips_git_operation(
        self,
        tmp_path,
        monkeypatch
    ):
        monkeypatch.chdir(tmp_path.parent)
        _suppress_confirm(monkeypatch)

        _setup_apparatus(tmp_path)
        _, target_path = _register(tmp_path, "cosmic-horror")

        runner_called = []
        def _spy_runner(*args):
            runner_called.append(args)
            return MagicMock(returncode=0)

        run(
            _deinit_args(str(target_path), retain = True),
            runner = _spy_runner,
            remover = _noop_remover
        )

        assert not runner_called, (
            f"--retain was set but the git runner was invoked: { runner_called }. "
            "The --retain flag means skip git entirely."
        )

    def test_retain_still_cleans_registry(
        self,
        tmp_path,
        monkeypatch
    ):
        monkeypatch.chdir(tmp_path.parent)
        _suppress_confirm(monkeypatch)

        _setup_apparatus(tmp_path)
        target_uuid, target_path = _register(tmp_path, "cosmic-horror")

        run(_deinit_args(str(target_path), retain = True))

        result = get_module_by_uuid(target_uuid)
        assert result is not None
        assert result["decimated_at"] is not None, (
            "--retain skipped the registry cleanup too. "
            "--retain means skip git, not skip everything."
        )

    def test_retain_leaves_module_on_disk(
        self,
        tmp_path,
        monkeypatch
    ):
        monkeypatch.chdir(tmp_path.parent)
        _suppress_confirm(monkeypatch)

        _setup_apparatus(tmp_path)
        _, target_path = _register(tmp_path, "cosmic-horror")

        run(_deinit_args(str(target_path), retain = True))

        assert target_path.exists(), (
            "--retain was set but the module directory was removed from disk. "
            "That's the opposite of what --retain means."
        )


# ===========================================================================
# Idempotency — partially-cleaned state
# ===========================================================================

class TestIdempotency:
    """
    The idempotency case: Apparatus cleanup succeeded but git failed on a
    previous run. Re-running deinit should:
      - Detect that the module is already decimated → skip Apparatus step
      - Attempt the git step again (the disk still has the module)

    This is the recovery path. It must not raise or produce a confusing error.
    """

    def test_already_decimated_module_skips_apparatus_step(
        self,
        tmp_path,
        monkeypatch
    ):
        """
        Module is already decimated (Apparatus cleanup ran previously).
        deinit must not raise on re-run — it should skip the Apparatus step
        and proceed to (or be told to skip via --retain) the git step.
        """
        monkeypatch.chdir(tmp_path.parent)
        _suppress_confirm(monkeypatch)

        _setup_apparatus(tmp_path)
        target_uuid, target_path = _register(tmp_path, "cosmic-horror")
        # Simulate: Apparatus step ran, git failed; module is decimated but still on disk
        decimate_module(target_uuid)

        # Re-run must not raise
        run(
            _deinit_args(str(target_path)),
            runner = _noop_runner,
            remover = _noop_remover
        )

        # Still decimated — no change needed
        result = get_module_by_uuid(target_uuid)
        assert result is not None
        assert result["decimated_at"] is not None


# ===========================================================================
# PermissionError on rmtree
# ===========================================================================

class TestPermissionError:

    def test_permission_error_on_rmtree_exits_cleanly(
        self,
        tmp_path,
        monkeypatch,
        capsys
    ):
        """
        remover raises PermissionError → must print a useful message with the
        path, tell the user not to sudo, and exit with code 1.
        Must NOT produce a raw traceback.

        _is_git_submodule returns (False, None) naturally for tmp_path (no
        .gitmodules), so the plain-directory remover branch is taken without
        any additional patching.
        """
        monkeypatch.chdir(tmp_path.parent)
        _suppress_confirm(monkeypatch)

        _setup_apparatus(tmp_path)
        _, target_path = _register(tmp_path, "cosmic-horror")

        def _raise_permission(_path: Path) -> None:
            raise PermissionError("Operation not permitted")

        with pytest.raises(SystemExit) as exc_info:
            run(
                _deinit_args(str(target_path)),
                runner = _noop_runner,
                remover = _raise_permission
            )

        assert exc_info.value.code == 1

        out = capsys.readouterr()
        stderr = out.err
        assert str(target_path) in stderr or "permission" in stderr.lower(), (
            "PermissionError output didn't mention the path or 'permission'. "
            "The user needs to know which directory to remove manually."
        )