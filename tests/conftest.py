import argparse
import logging
import subprocess
from pathlib import Path
import pytest

import archivist.utils.registry as registry_module
from archivist.formatter import ArchivistTerminalFormatter, ArchivistStreamHandler


#------------------------------------------------------------------------------
# Fixtures
#------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _configure_test_logging():
    """
    Automatically configure logging for every test.
    
    Sets up ArchivistStreamHandler with ArchivistTerminalFormatter on the
    archivist logger so that output.py's logging calls reach capsys for
    assertion. Without this, log records are captured by pytest but don't
    reach stdout/stderr.
    
    This fixture runs for every test automatically (autouse=True) and cleans
    up after itself.
    """
    logger = logging.getLogger("archivist")
    
    # Only configure if handlers aren't already set up (e.g. by a previous test)
    if not logger.handlers:
        handler = ArchivistStreamHandler()
        handler.setFormatter(ArchivistTerminalFormatter())
        handler.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
    
    yield
    
    # Clean up after the test
    logger.handlers.clear()


@pytest.fixture
def md_file(tmp_path):
    """
    Drop a markdown file into tmp_path. Returns a callable so tests can
    stamp out as many files as they need with one liner each.

        note = md_file("note.md", "---\nclass: character\n---\nBody text")
    """
    def _make(name: str, content: str) -> Path:
        p = tmp_path / name
        p.write_text(content, encoding = "utf-8")
        return p
    return _make


@pytest.fixture(autouse = True)
def isolated_registry(tmp_path, monkeypatch):
    """
    Redirect get_registry_dir() to tmp_path for every test, automatically.
    No test in this suite should ever be able to touch a developer's real
    ~/.archivist/ — this was previously duplicated locally in test_add.py
    and reinvented (differently — via Path.home(), which only catches call
    sites that re-derive the path themselves rather than the one place that
    actually matters) in a draft of test_sync.py. One canonical version,
    autouse, here.

    Patches registry_module.get_registry_dir directly rather than
    Path.home(): every registry function funnels through that one call, so
    patching it at the source is airtight regardless of how a given
    function gets there. Patching Path.home() is a leaky proxy for the same
    thing and only worth doing if something outside registry.py's control
    needs a fake home directory too.
    """
    fake_dir = tmp_path / ".archivist"
    monkeypatch.setattr(
        registry_module,
        "get_registry_dir",
        lambda: fake_dir
    )
    return fake_dir


@pytest.fixture
def make_git_repo():
    """
    Factory fixture — initializes a real git repo at any given path, with a
    committed initial state. Use this instead of the singular `git_repo`
    fixture whenever a test needs more than one independent repo — testing
    submodule relationships, for instance, where the container and the
    submodule source need two separate histories before they're ever wired
    together.

        vault = make_git_repo(tmp_path / "vault")
        child_source = make_git_repo(tmp_path / "child-source")

    `git_repo` itself is just this factory called once at a fixed path —
    don't fork the init sequence a second time if you need a third repo
    shape; extend this instead.
    """
    def _make(path: Path, seed_content: str = "module-type: general\n") -> Path:
        path.mkdir(parents = True, exist_ok = True)
        subprocess.run(
            ["git", "init"],
            cwd = path,
            check = True,
            capture_output = True
        )
        subprocess.run(
            [
                "git",
                "config",
                "user.email",
                "archivist@example.com"
            ],
            cwd = path,
            check = True,
            capture_output = True,
        )
        subprocess.run(
            [
                "git",
                "config",
                "user.name",
                "Archivist"
            ],
            cwd = path,
            check = True,
            capture_output = True,
        )
        seed = path / ".archivist"
        seed.write_text(seed_content, encoding = "utf-8")
        subprocess.run(
            [
                "git",
                "add",
                ".archivist"
            ],
            cwd = path,
            check = True,
            capture_output = True
        )
        subprocess.run(
            [
                "git",
                "commit",
                "--no-verify",
                "-m",
                "init"
            ],
            cwd = path,
            check = True,
            capture_output = True,
        )
        return path
    return _make


@pytest.fixture
def add_submodule():
    """
    Factory fixture — `git submodule add`s a local repo into a parent repo
    and commits the result.

        child_dir = add_submodule(parent=vault, source=child_source, rel_name="modules/child")

    Local-path submodules need protocol.file.allow=always on modern git
    (CVE-2022-39253 mitigation) or the add is silently refused — that's
    baked in here so no test has to remember it.

    Also commits any pending changes in `source` first. `git submodule add`
    clones from source's COMMITTED history, not its raw working tree — write
    a config into source via write_archivist_config() and forget to commit
    it, and the submodule checkout inside parent just won't have it, with no
    error anywhere to point at why. Tests shouldn't have to remember to
    commit before wiring two repos together; this fixture does it for them.
    """
    def _add(
        parent: Path,
        source: Path,
        rel_name: str
    ) -> Path:
        status = subprocess.run(
            [
                "git",
                "status",
                "--porcelain"
            ],
            cwd = source,
            check = True,
            capture_output = True,
            text = True,
        )
        if status.stdout.strip():
            subprocess.run(
                [
                    "git",
                    "add",
                    "-A"
                ],
                cwd = source,
                check = True,
                capture_output = True
            )
            subprocess.run(
                [
                    "git",
                    "commit",
                    "--no-verify",
                    "-m",
                    "pending changes before submodule add"
                ],
                cwd = source,
                check = True,
                capture_output = True,
            )

        subprocess.run(
            [
                "git",
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                str(source),
                rel_name
            ],
            cwd = parent,
            check = True,
            capture_output = True,
            text = True,
        )
        subprocess.run(
            [
                "git",
                "commit",
                "--no-verify",
                "-m",
                f"add submodule { rel_name }"
            ],
            cwd = parent,
            check = True,
            capture_output = True,
        )
        return parent / rel_name
    return _add


@pytest.fixture
def write_archivist_config():
    """
    Factory fixture — writes a minimal .archivist/config.yaml into a
    directory. Covers the two shapes commands actually branch on: a config
    with a declared apparatus (the "already decided" path) and one without
    (the "needs a human, refuse to guess" path).

        write_archivist_config(target_dir, uuid="...", apparati=["writing"])
        write_archivist_config(target_dir, uuid="...", apparati=None)  # undecided
    """
    def _write(
        target_dir: Path,
        uuid: str,
        apparati: list[str] | str | None = None,
        module_type: str = "general",
    ) -> None:
        target_dir.mkdir(parents = True, exist_ok = True)
        archivist_dir = target_dir / ".archivist"
        # git_repo and make_git_repo both seed a FLAT .archivist file (for
        # HEAD-seeding purposes) at this exact path. mkdir() over an
        # existing file raises FileExistsError regardless of exist_ok — that
        # flag only tolerates an existing DIRECTORY. Evict the flat file
        # first, same as config.py's real write_archivist_config() already
        # does for this exact legacy-form-vs-directory-form collision.
        if archivist_dir.exists() and archivist_dir.is_file():
            archivist_dir.unlink()
        archivist_dir.mkdir(exist_ok = True)
        if isinstance(apparati, str):
            apparati = [apparati]
        lines = [
            f"uuid: { uuid }",
            f"module-type: { module_type }"
        ]
        if apparati:
            lines.append("apparati:")
            lines.extend(f"  - {a}" for a in apparati)
        (archivist_dir / "config.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return _write


@pytest.fixture
def git_repo(make_git_repo, tmp_path):
    """
    A real, initialized git repo in tmp_path with a committed initial state.
    Returns the repo root Path. The working tree is clean after setup.

    Includes a helper `.commit(files, message)` so tests can build up
    commit history without boilerplate.
    """
    root = make_git_repo(tmp_path / "repo")

    class _Repo:
        path = root

        @staticmethod
        def commit(files: dict[str, str], message: str = "test commit") -> str:
            """
            Write files (name → content), stage, commit. Returns short SHA.
            """
            for name, content in files.items():
                p = root / name
                p.parent.mkdir(parents = True, exist_ok = True)
                p.write_text(content, encoding="utf-8")
            subprocess.run(
                [
                    "git",
                    "add",
                    "--all"
                ],
                cwd = root,
                check = True,
                capture_output = True,
            )
            subprocess.run(
                [
                    "git",
                    "commit",
                    "--no-verify",
                    "-m",
                    message
                ],
                cwd = root,
                check = True,
                capture_output = True,
            )
            return subprocess.check_output(
                [
                    "git",
                    "rev-parse",
                    "--short",
                    "HEAD"
                ],
                cwd = root,
                text = True,
            ).strip()

        @staticmethod
        def stage(files: dict[str, str]) -> None:
            """Write files and stage them without committing."""
            for name, content in files.items():
                p = root / name
                p.parent.mkdir(parents = True, exist_ok = True)
                p.write_text(content, encoding = "utf-8")
            subprocess.run(
                [
                    "git",
                    "add",
                    "--all"
                ],
                cwd = root,
                check = True,
                capture_output = True,
            )

    return _Repo()


#------------------------------------------------------------------------------
# Helpers
#------------------------------------------------------------------------------


@pytest.fixture
def args():
    """
    Factory fixture — inject this, then call it with kwargs to stamp out a
    fake argparse namespace. Tests call it as args(property="status", ...) rather
    than receiving a namespace directly, because different commands need
    different kwargs and we're not making a fixture for every fucking combination.
    """
    def _make(**kwargs):
        defaults = {
            "dry_run": False,
            "property": None,
            "value": None,
            "overwrite": False
        }
        return argparse.Namespace(**{ **defaults, **kwargs })
    return _make