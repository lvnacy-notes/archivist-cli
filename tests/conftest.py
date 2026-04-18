import argparse
import subprocess
from pathlib import Path
import pytest


#------------------------------------------------------------------------------
# Fixtures
#------------------------------------------------------------------------------


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
    seed.write_text("module-type: general\n", encoding="utf-8")
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
        defaults = {"dry_run": False, "property": None, "value": None, "overwrite": False}
        return argparse.Namespace(**{**defaults, **kwargs})
    return _make