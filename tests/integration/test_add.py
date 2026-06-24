"""
tests/integration/test_add.py

Integration tests for `archivist add`.

The command is heavily interactive and git-dependent, so two isolation layers
are always active:

  1. Registry isolation — `isolated_registry` (autouse) patches get_registry_dir()
     to tmp_path so no test ever writes to ~/.archivist/. Same pattern as
     test_registry.py.

  2. Git isolation — subprocess.run and subprocess.check_output are patched to
     avoid real network calls. The git operation itself is the gate in
     production; here we control whether it succeeds or fails and verify the
     registry behaviour that follows.

Interactive prompts (_confirm, _prompt, prompt_apparatus_names) are patched
to return canned values so tests don't hang waiting for stdin.

Entry point under test: `archivist.commands.add.run(args)`.
Direct testing of internal helpers (_infer_target_name, _build_git_command,
etc.) lives here only where the helper behaviour is not reachable through
run() in a clean way.
"""

import argparse
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

import archivist.utils.registry as registry_module
from archivist.commands.add import (
    _build_git_command,
    _infer_target_name,
    run,
)
from archivist.utils import (
    add_module_to_bay,
    decimate_module,
    get_apparatus_modules,
    get_module_bays,
    get_module_by_path,
    get_module_by_uuid,
    get_registry_path,
    init_registry,
    is_module_registered,
    read_archivist_config,
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
    monkeypatch.setattr(registry_module, "get_registry_dir", lambda: fake_dir)
    return fake_dir


# ===========================================================================
# Helpers
# ===========================================================================

_FAKE_URL = "git@github.com:user/cosmic-horror.git"
_FAKE_MODULE_NAME = "cosmic-horror"


def _add_args(
    url: str = _FAKE_URL,
    path: str | None = None,
    dry_run: bool = False,
    passthrough: list[str] | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        url=url,
        path=path,
        dry_run=dry_run,
        passthrough=passthrough or [],
    )


def _seed_target_config(target_dir: Path, apparatus: str, module_type: str = "library") -> None:
    """
    Write a minimal .archivist/config.yaml into target_dir so that
    _resolve_and_register hits the 'UUID in config' branches rather than
    falling through to interactive registration.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    archivist_dir = target_dir / ".archivist"
    archivist_dir.mkdir(exist_ok=True)
    (archivist_dir / "config.yaml").write_text(
        f"uuid: aaaaaaaa-0000-0000-0000-000000000000\n"
        f"module-type: {module_type}\n"
        f"apparati:\n"
        f"  - {apparatus}\n",
        encoding="utf-8",
    )


def _git_succeeds(monkeypatch) -> None:
    """Patch subprocess so git operations silently succeed."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: MagicMock(returncode=0))
    monkeypatch.setattr(
        subprocess, "check_output", lambda *a, **kw: b""
    )


def _git_fails(monkeypatch, returncode: int = 128) -> None:
    """Patch subprocess so the git operation raises CalledProcessError."""
    def _raise(*args, **kwargs):
        raise subprocess.CalledProcessError(returncode, args[0])
    monkeypatch.setattr(subprocess, "run", _raise)


def _suppress_hooks(monkeypatch) -> None:
    """Hook installation is non-fatal but noisy. Suppress it throughout."""
    monkeypatch.setattr(
        "archivist.commands.add._install_hooks_local", lambda *a, **kw: None
    )


def _suppress_prompts(monkeypatch, is_apparatus: bool = True, apparatus_names: list[str] | None = None) -> None:
    """
    Suppress all interactive prompts. Tests that exercise specific prompt
    behaviour should patch these individually instead of calling this.
    """
    if apparatus_names is None:
        apparatus_names = ["writing"]
    monkeypatch.setattr("archivist.commands.add._confirm", lambda *a, **kw: is_apparatus)
    monkeypatch.setattr(
        "archivist.commands.add.prompt_apparatus_names", lambda: apparatus_names
    )
    monkeypatch.setattr(
        "archivist.commands.add._prompt", lambda *a, **kw: "library"
    )


# ===========================================================================
# Pure helper unit tests (no subprocess, no registry)
# ===========================================================================

class TestInferTargetName:
    """
    _infer_target_name is the canonical URL → directory name translator.
    Pin it explicitly — every path calculation in run() depends on it.
    """

    def test_ssh_url_with_git_suffix(self):
        assert _infer_target_name("git@github.com:user/my-project.git") == "my-project"

    def test_https_url_without_git_suffix(self):
        assert _infer_target_name("https://github.com/user/my-project") == "my-project"

    def test_https_url_with_git_suffix(self):
        assert _infer_target_name("https://github.com/user/my-project.git") == "my-project"

    def test_trailing_slash_ignored(self):
        assert _infer_target_name("https://github.com/user/my-project/") == "my-project"

    def test_bare_name_fallback(self):
        """Degenerate URL with no path component falls back to 'module'."""
        assert _infer_target_name("") == "module"


class TestBuildGitCommand:

    def test_clone_without_path(self):
        cmd = _build_git_command(_FAKE_URL, None, [], is_submodule_context=False)
        assert cmd == ["git", "clone", _FAKE_URL]

    def test_clone_with_path(self):
        cmd = _build_git_command(_FAKE_URL, "my-dir", [], is_submodule_context=False)
        assert cmd == ["git", "clone", _FAKE_URL, "my-dir"]

    def test_submodule_add_without_path(self):
        cmd = _build_git_command(_FAKE_URL, None, [], is_submodule_context=True)
        assert cmd == ["git", "submodule", "add", _FAKE_URL]

    def test_submodule_add_with_path(self):
        cmd = _build_git_command(_FAKE_URL, "my-dir", [], is_submodule_context=True)
        assert cmd == ["git", "submodule", "add", _FAKE_URL, "my-dir"]

    def test_passthrough_args_appended(self):
        cmd = _build_git_command(_FAKE_URL, None, ["--depth=1", "--branch=main"], is_submodule_context=False)
        assert "--depth=1" in cmd
        assert "--branch=main" in cmd
        assert cmd[-2:] == ["--depth=1", "--branch=main"]

    def test_clone_context_determined_by_flag(self):
        clone_cmd = _build_git_command(_FAKE_URL, None, [], is_submodule_context=False)
        sub_cmd   = _build_git_command(_FAKE_URL, None, [], is_submodule_context=True)
        assert "clone" in clone_cmd
        assert "submodule" in sub_cmd


# ===========================================================================
# Dry-run contract
# ===========================================================================

class TestDryRun:
    """
    A dry run must be a perfect no-op. No git operations, no registry changes,
    no files written. The plan must be printed but nothing else must change.
    """

    def test_dry_run_writes_absolutely_nothing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        init_registry()
        _suppress_hooks(monkeypatch)
        _suppress_prompts(monkeypatch)

        before_files = {p for p in tmp_path.rglob("*") if p.is_file()}

        # Patch subprocess so it would succeed if called — but it shouldn't be
        git_called = []
        def _git_spy(*args, **kwargs):
            git_called.append(args)
            return MagicMock(returncode=0)
        monkeypatch.setattr(subprocess, "run", _git_spy)

        run(_add_args(dry_run=True))

        after_files = {p for p in tmp_path.rglob("*") if p.is_file()}
        assert before_files == after_files, (
            "dry_run=True and files still changed. A dry run that writes is just called a run."
        )

    def test_dry_run_does_not_touch_registry(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        init_registry()
        _suppress_hooks(monkeypatch)
        _suppress_prompts(monkeypatch)
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: MagicMock(returncode=0))

        run(_add_args(dry_run=True))

        # No module should have been written to the registry
        target_path = (tmp_path / _FAKE_MODULE_NAME).resolve()
        assert get_module_by_path(target_path) is None, (
            "dry_run=True wrote a module row to the registry. "
            "Dry runs must not touch the registry."
        )

    def test_dry_run_does_not_execute_git(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        init_registry()
        _suppress_hooks(monkeypatch)
        _suppress_prompts(monkeypatch)

        git_called = []
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: git_called.append(a) or MagicMock(returncode=0))

        run(_add_args(dry_run=True))

        assert not git_called, (
            f"dry_run=True and git was called anyway: {git_called}. "
            "The whole point of --dry-run is that git doesn't run."
        )

    def test_dry_run_prints_plan(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        init_registry()
        _suppress_hooks(monkeypatch)
        _suppress_prompts(monkeypatch)
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: MagicMock(returncode=0))

        run(_add_args(dry_run=True))

        out = capsys.readouterr().out + capsys.readouterr().err
        # Something describing the planned operation must have been printed.
        # We don't assert exact wording — the voice may change. We assert intent.
        assert "dry-run" in out.lower() or "would" in out.lower(), (
            "Dry-run produced no output. The user needs to know what would have happened."
        )


# ===========================================================================
# Non-git-repo context (git clone path)
# ===========================================================================

class TestClonePath:
    """
    cwd has no .git → is_submodule_context=False → git clone runs.
    """

    def test_git_clone_is_called_in_non_git_context(self, tmp_path, monkeypatch):
        """No .git in cwd → subprocess.run must be called with 'clone'."""
        monkeypatch.chdir(tmp_path)
        _suppress_hooks(monkeypatch)
        _suppress_prompts(monkeypatch)

        # Registry setup must happen before subprocess is patched — init_registry()
        # calls git init internally and must not hit the capture spy.
        init_registry()
        register_apparatus("writing", git_remote=None)

        target_dir = tmp_path / _FAKE_MODULE_NAME
        _seed_target_config(target_dir, "writing")

        git_cmds = []
        def _capture(cmd, **kwargs):
            git_cmds.append(cmd)
            return MagicMock(returncode=0)
        monkeypatch.setattr(subprocess, "run", _capture)
        monkeypatch.setattr(subprocess, "check_output", lambda *a, **kw: b"")

        run(_add_args())

        clone_calls = [c for c in git_cmds if "clone" in c]
        assert clone_calls, (
            f"Expected a 'git clone' call in a non-git directory. "
            f"Got: {git_cmds}"
        )

    def test_module_registered_after_clone(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _suppress_hooks(monkeypatch)
        _suppress_prompts(monkeypatch)

        target_dir = tmp_path / _FAKE_MODULE_NAME
        _seed_target_config(target_dir, "writing")
        _git_succeeds(monkeypatch)
        init_registry()
        register_apparatus("writing", git_remote=None)

        run(_add_args())

        result = get_module_by_path(target_dir)
        assert result is not None, (
            "Module not registered after successful clone. "
            "Registry write must follow a successful git operation."
        )

    def test_no_bay_row_created_when_cwd_unregistered(self, tmp_path, monkeypatch):
        """
        cwd is not a registered module — no bay row should be created.
        Being in a directory is not the same as being in a registered superproject.
        """
        monkeypatch.chdir(tmp_path)
        _suppress_hooks(monkeypatch)
        _suppress_prompts(monkeypatch)

        target_dir = tmp_path / _FAKE_MODULE_NAME
        _seed_target_config(target_dir, "writing")
        _git_succeeds(monkeypatch)
        init_registry()
        register_apparatus("writing", git_remote=None)

        run(_add_args())

        target_module = get_module_by_path(target_dir)
        assert target_module is not None
        bays = get_module_bays(target_module["uuid"])
        assert bays == [], (
            "Bay row created even though cwd is not a registered module. "
            "Containment requires a registered superproject, not just a directory."
        )


# ===========================================================================
# Git-repo context (git submodule add path)
# ===========================================================================

class TestSubmodulePath:
    """
    cwd has .git → is_submodule_context=True → git submodule add runs.
    """

    def test_git_submodule_add_is_called_in_git_context(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        _suppress_hooks(monkeypatch)
        _suppress_prompts(monkeypatch)

        # Registry setup before subprocess is patched — init_registry() calls
        # git init and must not hit the capture spy.
        init_registry()
        register_apparatus("writing", git_remote=None)

        target_dir = git_repo.path / _FAKE_MODULE_NAME
        _seed_target_config(target_dir, "writing")

        git_cmds = []
        def _capture(cmd, **kwargs):
            git_cmds.append(cmd)
            return MagicMock(returncode=0)
        monkeypatch.setattr(subprocess, "run", _capture)
        monkeypatch.setattr(subprocess, "check_output", lambda *a, **kw: b"")

        run(_add_args())

        submodule_calls = [c for c in git_cmds if "submodule" in c and "add" in c]
        assert submodule_calls, (
            f"Expected 'git submodule add' in a git-repo context. Got: {git_cmds}"
        )

    def test_bay_row_created_when_cwd_is_registered(self, git_repo, monkeypatch):
        """
        cwd is a registered active module → adding a submodule must create a bay row.
        """
        monkeypatch.chdir(git_repo.path)
        _suppress_hooks(monkeypatch)
        _suppress_prompts(monkeypatch)
        _git_succeeds(monkeypatch)

        init_registry()
        register_apparatus("writing", git_remote=None)

        # Register the cwd (vault) so add() sees it as a superproject
        vault_uuid = register_module(
            apparatus_name="writing",
            name="fiction-vault",
            module_type="vault",
            path=git_repo.path,
            git_remote=None,
        )

        target_dir = git_repo.path / _FAKE_MODULE_NAME
        _seed_target_config(target_dir, "writing")

        run(_add_args())

        target_module = get_module_by_path(target_dir)
        assert target_module is not None
        bays = get_module_bays(target_module["uuid"])
        assert len(bays) == 1, (
            f"Expected 1 bay row (cwd is a registered vault). Got {len(bays)}."
        )
        assert bays[0]["uuid"] == vault_uuid

    def test_no_bay_row_when_cwd_registered_but_decimated(self, git_repo, monkeypatch):
        """
        cwd IS in the registry but is decimated — must not create a bay row.
        A decimated module is not an active superproject.
        """
        monkeypatch.chdir(git_repo.path)
        _suppress_hooks(monkeypatch)
        _suppress_prompts(monkeypatch)
        _git_succeeds(monkeypatch)

        init_registry()
        register_apparatus("writing", git_remote=None)
        vault_uuid = register_module(
            apparatus_name="writing",
            name="fiction-vault",
            module_type="vault",
            path=git_repo.path,
            git_remote=None,
        )
        decimate_module(vault_uuid)

        target_dir = git_repo.path / _FAKE_MODULE_NAME
        _seed_target_config(target_dir, "writing")

        run(_add_args())

        target_module = get_module_by_path(target_dir)
        assert target_module is not None
        bays = get_module_bays(target_module["uuid"])
        assert bays == [], (
            "Bay row created with a decimated superproject as the container. "
            "Decimated modules are not active superprojects."
        )

    def test_vault_container_updates_vaults_list_in_target_config(self, git_repo, monkeypatch):
        """
        When cwd is a vault-type module, the target's config.yaml `vaults`
        field must be updated with the vault's name. The DB bay row and the
        config field are both updated — neither is optional.
        """
        monkeypatch.chdir(git_repo.path)
        _suppress_hooks(monkeypatch)
        _suppress_prompts(monkeypatch)
        _git_succeeds(monkeypatch)

        init_registry()
        register_apparatus("writing", git_remote=None)
        register_module(
            apparatus_name="writing",
            name="fiction-vault",
            module_type="vault",
            path=git_repo.path,
            git_remote=None,
        )

        target_dir = git_repo.path / _FAKE_MODULE_NAME
        _seed_target_config(target_dir, "writing")

        run(_add_args())

        config = read_archivist_config(target_dir)
        assert config is not None
        vaults = config.get("vaults") or []
        assert "fiction-vault" in vaults, (
            f"Vault name not written to target's vaults list. Got: {vaults}. "
            "Both the bay row and the config field must be updated."
        )


# ===========================================================================
# Git failure
# ===========================================================================

class TestGitFailure:
    """
    git is the gate. If it fails, the registry must be untouched.
    That's the entire contract of the command's operation order.
    """

    def test_registry_untouched_on_git_failure(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _suppress_hooks(monkeypatch)
        _suppress_prompts(monkeypatch)

        # Registry must be initialised before subprocess is patched to fail —
        # init_registry() calls git init and would blow up on the failure mock.
        init_registry()
        register_apparatus("writing", git_remote=None)

        target_dir = tmp_path / _FAKE_MODULE_NAME
        _seed_target_config(target_dir, "writing")

        _git_fails(monkeypatch)

        with pytest.raises(SystemExit):
            run(_add_args())

        assert get_module_by_path(target_dir) is None, (
            "Registry was written even though git failed. "
            "git is the gate — registry writes happen AFTER git succeeds."
        )

    def test_exits_with_propagated_return_code(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _suppress_hooks(monkeypatch)
        _suppress_prompts(monkeypatch)

        # Registry must be initialised before subprocess is patched to fail —
        # init_registry() calls git init and would blow up on the failure mock.
        init_registry()
        register_apparatus("writing", git_remote=None)

        target_dir = tmp_path / _FAKE_MODULE_NAME
        _seed_target_config(target_dir, "writing")

        def _fail(*args, **kwargs):
            raise subprocess.CalledProcessError(42, args[0])
        monkeypatch.setattr(subprocess, "run", _fail)

        with pytest.raises(SystemExit) as exc_info:
            run(_add_args())

        assert exc_info.value.code == 42, (
            f"Expected exit code 42 (propagated from git). "
            f"Got: {exc_info.value.code}."
        )


# ===========================================================================
# UUID resolution cases
# ===========================================================================

class TestUUIDResolution:
    """
    The four-case matrix from spec §7. Each case must produce the correct
    registry state without prompting the user for information we already have.
    """

    def test_case_b_active_module_readded_no_duplicate_row(self, git_repo, monkeypatch):
        """
        Case b: UUID in config + active in registry → refresh; no duplicate module row.
        """
        monkeypatch.chdir(git_repo.path)
        _suppress_hooks(monkeypatch)
        _suppress_prompts(monkeypatch)
        _git_succeeds(monkeypatch)

        init_registry()
        register_apparatus("writing", git_remote=None)

        target_dir = git_repo.path / _FAKE_MODULE_NAME
        _seed_target_config(target_dir, "writing")

        # First add
        run(_add_args())
        # Second add — must not create a duplicate row
        run(_add_args())

        modules = get_apparatus_modules("writing")
        target_modules = [m for m in modules if "cosmic-horror" in m["name"]]
        assert len(target_modules) == 1, (
            f"Expected 1 module row after two adds. Got {len(target_modules)}. "
            "Re-adding an active module must upsert, not duplicate."
        )

    def test_case_a_decimated_module_reactivated(self, git_repo, monkeypatch):
        """
        Case a: UUID in config + decimated in registry → reactivate; decimated_at cleared.
        """
        monkeypatch.chdir(git_repo.path)
        _suppress_hooks(monkeypatch)
        _suppress_prompts(monkeypatch)
        _git_succeeds(monkeypatch)

        init_registry()
        register_apparatus("writing", git_remote=None)

        target_dir = git_repo.path / _FAKE_MODULE_NAME
        _seed_target_config(target_dir, "writing")

        # First add → register
        run(_add_args())
        first_module = get_module_by_path(target_dir)
        assert first_module is not None

        # Decimate it
        decimate_module(first_module["uuid"])
        decimated = get_module_by_uuid(first_module["uuid"])
        assert decimated is not None
        assert decimated["decimated_at"] is not None

        # Re-add → must reactivate
        run(_add_args())

        refreshed = get_module_by_uuid(first_module["uuid"])
        assert refreshed is not None, (
            "get_module_by_uuid returned None after re-add — module row is gone entirely."
        )
        assert refreshed["decimated_at"] is None, (
            "Re-adding a decimated module didn't clear decimated_at. "
            "Case (a) of the UUID resolution matrix is broken."
        )

    def test_case_b_bay_row_added_if_absent_on_reregister(self, git_repo, monkeypatch):
        """
        Case b: active re-add in a superproject context must create the bay row
        if it doesn't already exist (e.g. re-add after deinit left the module
        active but removed the bay).
        """
        monkeypatch.chdir(git_repo.path)
        _suppress_hooks(monkeypatch)
        _suppress_prompts(monkeypatch)
        _git_succeeds(monkeypatch)

        init_registry()
        register_apparatus("writing", git_remote=None)
        vault_uuid = register_module(
            apparatus_name="writing",
            name="fiction-vault",
            module_type="vault",
            path=git_repo.path,
            git_remote=None,
        )

        target_dir = git_repo.path / _FAKE_MODULE_NAME
        _seed_target_config(target_dir, "writing")

        run(_add_args())

        target_module = get_module_by_path(target_dir)
        assert target_module is not None, (
            "get_module_by_path returned None after re-add — module was not registered."
        )
        bays = get_module_bays(target_module["uuid"])
        assert any(b["uuid"] == vault_uuid for b in bays), (
            "Bay row not created on re-add with a registered vault superproject. "
            "Case (b) must still wire up containment."
        )


# ===========================================================================
# git_remote_name resolution
# ===========================================================================

class TestGitRemoteName:
    """
    After the git operation, add() queries `git remote -v` in the target dir
    to resolve the remote name. Pin both the success and null cases.
    """

    def test_git_remote_name_populated_from_remote_v(self, tmp_path, monkeypatch):
        """
        git remote -v returns a line containing the URL → remote name stored.
        """
        monkeypatch.chdir(tmp_path)
        _suppress_hooks(monkeypatch)
        _suppress_prompts(monkeypatch)
        init_registry()
        register_apparatus("writing", git_remote=None)

        target_dir = tmp_path / _FAKE_MODULE_NAME
        _seed_target_config(target_dir, "writing")

        # First subprocess.run call is the git clone — succeeds silently.
        # Second is git remote -v — returns a fake remote line.
        remote_v_output = f"origin\t{_FAKE_URL} (fetch)\norigin\t{_FAKE_URL} (push)\n"
        call_count = [0]
        def _fake_run(cmd, **kwargs):
            call_count[0] += 1
            m = MagicMock()
            m.returncode = 0
            if "remote" in cmd:
                m.stdout = remote_v_output
            else:
                m.stdout = ""
            return m
        monkeypatch.setattr(subprocess, "run", _fake_run)
        monkeypatch.setattr(subprocess, "check_output", lambda *a, **kw: b"")

        run(_add_args())

        result = get_module_by_path(target_dir)
        assert result is not None
        assert result["git_remote_name"] == "origin", (
            f"Expected git_remote_name='origin'. Got: {result['git_remote_name']!r}. "
            "Remote name must be resolved from git remote -v after the operation."
        )

    def test_git_remote_name_null_when_remote_not_found(self, tmp_path, monkeypatch):
        """
        git remote -v returns nothing useful → git_remote_name stored as NULL.
        Happens when the remote isn't configured yet (e.g. fresh clone with no push remote).
        """
        monkeypatch.chdir(tmp_path)
        _suppress_hooks(monkeypatch)
        _suppress_prompts(monkeypatch)
        init_registry()
        register_apparatus("writing", git_remote=None)

        target_dir = tmp_path / _FAKE_MODULE_NAME
        _seed_target_config(target_dir, "writing")

        def _fake_run(cmd, **kwargs):
            m = MagicMock()
            m.returncode = 0
            m.stdout = ""  # no remote output
            return m
        monkeypatch.setattr(subprocess, "run", _fake_run)
        monkeypatch.setattr(subprocess, "check_output", lambda *a, **kw: b"")

        run(_add_args())

        result = get_module_by_path(target_dir)
        assert result is not None
        assert result["git_remote_name"] is None, (
            f"Expected git_remote_name=None when remote not found. "
            f"Got: {result['git_remote_name']!r}."
        )