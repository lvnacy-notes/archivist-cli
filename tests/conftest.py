import argparse
import sqlite3
import subprocess
from pathlib import Path
import pytest

from archivist.utils import (
    init_apparatus_db,
    init_registry_db,
    register_apparatus,
    register_module,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def md_file(tmp_path):
    """
    Drop a markdown file into tmp_path. Returns a callable so tests can
    stamp out as many files as they need with one liner each.

        note = md_file("note.md", "---\nclass: character\n---\nBody text")
    """
    def _make(name: str, content: str) -> Path:
        p = tmp_path / name
        p.write_text(content, encoding="utf-8")
        return p
    return _make


@pytest.fixture
def git_repo(tmp_path):
    """
    A real, initialized git repo in tmp_path with a committed initial state.
    Returns the repo root Path. The working tree is clean after setup.

    Includes a helper `.commit(files, message)` so tests can build up
    commit history without boilerplate.
    """
    root = tmp_path / "repo"
    root.mkdir()

    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "archivist@example.com"],
        cwd=root, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Archivist"],
        cwd=root, check=True, capture_output=True,
    )

    # Seed the repo with something so HEAD exists
    seed = root / ".archivist"
    seed.mkdir()
    (seed / "config.yaml").write_text("module-type: general\n", encoding="utf-8")
    subprocess.run(["git", "add", ".archivist"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--no-verify", "-m", "init"],
        cwd=root, check=True, capture_output=True,
    )

    class _Repo:
        path = root

        @staticmethod
        def commit(files: dict[str, str], message: str = "test commit") -> str:
            """
            Write files (name → content), stage, commit. Returns short SHA.
            """
            for name, content in files.items():
                p = root / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
            subprocess.run(
                ["git", "add", "--all"], cwd=root, check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "--no-verify", "-m", message],
                cwd=root, check=True, capture_output=True,
            )
            return subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=root, text=True, capture_output=False,
            ).strip()

        @staticmethod
        def stage(files: dict[str, str]) -> None:
            """Write files and stage them without committing."""
            for name, content in files.items():
                p = root / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
            subprocess.run(
                ["git", "add", "--all"], cwd=root, check=True, capture_output=True,
            )

    return _Repo()


@pytest.fixture
def registry_db(tmp_path) -> Path:
    """
    Create an isolated registry.db in tmp_path and return its path.

    Schema is initialized; no rows are inserted. Pass this path to any
    registry function that accepts an explicit db_path. Never touches
    ~/.archivist/. If a test writes to ~/.archivist/, the fixture is not
    doing its job and the test is broken.
    """
    db_path = tmp_path / "registry.db"
    conn = init_registry_db(db_path)
    conn.close()
    return db_path


@pytest.fixture
def apparatus_db(tmp_path) -> Path:
    """
    Create an isolated apparatus DB (writing.db) in tmp_path and return its path.

    Schema is initialized; no rows are inserted.
    """
    db_path = tmp_path / "writing.db"
    conn = init_apparatus_db(db_path)
    conn.close()
    return db_path


@pytest.fixture
def registered_library(tmp_path, registry_db, apparatus_db, git_repo) -> dict:
    """
    Full realistic environment: a registered library module with both DBs wired up.

    Returns a dict with keys:
        registry_db   — Path to the isolated registry.db
        apparatus_db  — Path to the isolated writing.db
        module_path   — Path to the git repo root (the module under test)
        module_id     — int, the registered module id in registry.db

    Use this fixture for any test that exercises the full registration +
    works pipeline. Don't reach past it into ~/.archivist/.
    """
    conn = sqlite3.connect(registry_db)
    try:
        apparatus_id = register_apparatus(
            name="writing",
            conn=conn,
            db_path=apparatus_db,
        )
        module_id = register_module(
            apparatus_id=apparatus_id,
            vault_id=None,
            name="cosmic-horror",
            module_type="library",
            path=git_repo.path,
            library_tag="cosmic-horror",
            conn=conn,
        )
    finally:
        conn.close()

    config_dir = git_repo.path / ".archivist"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "config.yaml").write_text(
        "module-type: library\n"
        "apparatus: writing\n"
        "library-tag: cosmic-horror\n"
        "directories:\n"
        "  works: works/\n"
        "  authors: authors/\n"
        "  publications: publications/\n",
        encoding="utf-8",
    )

    (git_repo.path / "works").mkdir(exist_ok=True)
    (git_repo.path / "authors").mkdir(exist_ok=True)
    (git_repo.path / "publications").mkdir(exist_ok=True)

    return {
        "registry_db": registry_db,
        "apparatus_db": apparatus_db,
        "module_path": git_repo.path,
        "module_id": module_id,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def args():
    """
    Factory fixture — inject this, then call it with kwargs to stamp out a
    fake argparse namespace. Tests call it as args(property="status", ...) rather
    than receiving a namespace directly, because different commands need
    different kwargs and we're not making a fixture for every fucking combination.
    """
    def _make(**kwargs):
        defaults = {"dry_run": False, "property": None, "value": None, "overwrite": False}
        return argparse.Namespace(**{**defaults, **kwargs})
    return _make