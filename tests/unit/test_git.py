"""
tests/unit/test_git.py

Deliberately small. TESTING_SPECIFICATION.md carves git internals out of
scope on purpose — we don't test that `git diff-index` returns the right
output, git's own test suite covers that. This file is not that: it pins
one thing — that get_git_changes() builds a command git will actually
accept — because a malformed `-c key = value` (spaces around `=`) shipped
silently for who knows how long and only started failing loudly once a
git version tightened `-c` parsing. Real git, no mocked subprocess calls,
per the project's own testing philosophy for anything git-shaped.
"""

from archivist.utils import get_git_changes


class TestGetGitChangesCommandIsValid:
    """
    Regression coverage for the `core.quotepath = false` -> invalid-key bug.
    Git's `-c key=value` syntax rejects spaces around `=` outright — passing
    the malformed form doesn't degrade gracefully, it makes every single
    call to get_git_changes() exit(1), which took out the entire changelog
    command surface. If this ever regresses, this test fails immediately
    and specifically, instead of forty unrelated changelog tests failing
    with a confusing git stderr buried in the traceback.
    """

    def test_no_staged_changes_does_not_exit(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)

        # Must not sys.exit — a malformed -c value makes git reject the
        # command outright regardless of what's staged.
        changes = get_git_changes(commit_sha=None)
        assert changes == {"M": [], "A": [], "D": [], "R": []}

    def test_staged_addition_is_detected(self, git_repo, monkeypatch):
        git_repo.stage({"new_file.md": "content\n"})
        monkeypatch.chdir(git_repo.path)

        changes = get_git_changes(commit_sha=None)
        assert "new_file.md" in changes["A"]