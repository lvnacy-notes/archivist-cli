"""
tests/integration/test_seal.py

Integration tests for `archivist changelog seal`.

What seal does, since apparently we need to spell it out:
  1. Finds unsealed changelogs (CHANGELOG-YYYY-MM-DD.md) in a given commit
  2. Checks they haven't already been sealed (non-empty commit-sha in frontmatter)
  3. Backfills the short SHA into `commit-sha:` in frontmatter
  4. Backfills the full SHA into the `| Commit SHA |` table cell in the body
  5. Renames the file from CHANGELOG-YYYY-MM-DD.md → CHANGELOG-YYYY-MM-DD-{short_sha}.md
  6. If a UUID is present in frontmatter, updates the archive DB:
       - changelogs table: upserts the entry with commit SHA and seal timestamp
       - edition_shas table: transitions included_in from UUID → short_sha

Called automatically by the post-commit hook. Manual invocation is for when
the hook misfired, a seal got missed, or you are that kind of person.

Tests run against a real git repo with real commits. No mocking. No fake
output parsing. If it doesn't work with actual git, it doesn't fucking work.

Edge cases pinned here:
  - Already-sealed changelogs are skipped without error or drama
  - Pre-UUID changelogs (no UUID in frontmatter) skip the DB step cleanly
  - A commit with no unsealed changelogs exits cleanly with a progress note
  - The unsealed path must NOT exist after rename (old gone, new present)
  - Sealed file is NOT picked up by find_active_changelog() on subsequent runs
  - DB transition: edition_shas.included_in goes from UUID → short_sha
  - Sealed filename suffix is the SHORT sha from `git rev-parse --short`
  - Running seal twice against the same commit is idempotent
"""

import argparse
import sqlite3
import subprocess
from pathlib import Path

import pytest

from archivist.commands.changelog.seal import run as run_seal
from archivist.utils import (
    extract_frontmatter,
    find_active_changelog,
    get_today,
    init_db
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers — private to this module, not fixtures.
# Named with leading underscore so nobody confuses them for pytest fixtures
# and spends twenty minutes wondering why injection is failing.
# ---------------------------------------------------------------------------

def _get_full_sha(repo_path: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo_path, text=True
    ).strip()


def _get_short_sha(repo_path: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], cwd=repo_path, text=True
    ).strip()


def _commit_changelog_and_get_shas(git_repo, changelog: Path) -> tuple[str, str]:
    """
    Stage and commit the given changelog file. Returns (full_sha, short_sha).
    Uses explicit `git add <path>` rather than git_repo.commit() because we
    need to stage one specific file we've already written to disk, not write
    new content via the fixture's dict interface.
    """
    subprocess.run(
        ["git", "add", str(changelog)],
        cwd=git_repo.path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "add changelog for sealing"],
        cwd=git_repo.path, check=True, capture_output=True,
    )
    return _get_full_sha(git_repo.path), _get_short_sha(git_repo.path)


def _find_sealed_changelog(output_dir: Path, short_sha: str) -> Path | None:
    """
    Find a sealed changelog matching CHANGELOG-*-{short_sha}.md in output_dir.
    Returns None when not found — callers decide if that's assertion-worthy.
    """
    matches = list(output_dir.glob(f"CHANGELOG-*-{short_sha}.md"))
    return matches[0] if matches else None


def _seal_args(commit_sha: str) -> argparse.Namespace:
    return argparse.Namespace(commit_sha=commit_sha)


def _make_unsealed_changelog(
    output_dir: Path,
    *,
    uuid: str | None = "dec0ded0-cafe-4bab-8fac-ed0123456789",
    extra_body: str = "",
) -> Path:
    """
    Drop a properly-structured unsealed CHANGELOG-{today}.md into output_dir.

    Frontmatter has an empty commit-sha field. Body has the placeholder cell
    and the sentinel. Pass uuid=None to simulate a pre-DB changelog with no UUID.
    Pass extra_body to inject content below the sentinel (simulates user notes).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    today = get_today()
    uuid_line = f"UUID: {uuid}\n" if uuid else ""
    content = (
        f"---\n"
        f"class: archive\n"
        f"log-scope: general\n"
        f"modified: {today}\n"
        f"{uuid_line}"
        f"commit-sha: \n"
        f"---\n"
        f"\n# Changelog — {today}\n\n"
        f"## Overview\n\n"
        f"| Field | Value |\n"
        f"|-------|-------|\n"
        f"| Date | {today} |\n"
        f"| Commit SHA | [fill in after commit] |\n"
        f"\n<!-- archivist:auto-end -->\n"
        f"\n## Notes\n\n{extra_body}"
    )
    path = output_dir / f"CHANGELOG-{today}.md"
    path.write_text(content, encoding="utf-8")
    return path


def _seed_db_with_claimed_sha(
    git_root: Path,
    edition_sha: str,
    changelog_uuid: str,
    commit_message: str = "test edition",
) -> None:
    """
    Seed the archive DB with an edition SHA already claimed by a changelog UUID.
    Replicates the state after `archivist changelog publication` runs but before
    seal transitions included_in from UUID → short_sha.
    """
    db_path = git_root / "ARCHIVE" / "archive.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = init_db(db_path)
    conn.execute(
        """INSERT OR IGNORE INTO edition_shas
           (sha, commit_message, discovered_at, included_in)
           VALUES (?, ?, '2024-01-01', ?)""",
        (edition_sha, commit_message, changelog_uuid),
    )
    conn.commit()
    conn.close()


def _get_edition_included_in(git_root: Path, edition_sha: str) -> str | None:
    db_path = git_root / "ARCHIVE" / "archive.db"
    if not db_path.exists():
        return None
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT included_in FROM edition_shas WHERE sha = ?", (edition_sha,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


def _get_changelogs_row(git_root: Path, uuid: str) -> dict | None:
    db_path = git_root / "ARCHIVE" / "archive.db"
    if not db_path.exists():
        return None
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT uuid, commit_sha, sealed_at FROM changelogs WHERE uuid = ?", (uuid,)
    ).fetchone()
    conn.close()
    return {"uuid": row[0], "commit_sha": row[1], "sealed_at": row[2]} if row else None


# ---------------------------------------------------------------------------
# TestSealBasicMechanics
# ---------------------------------------------------------------------------

class TestSealBasicMechanics:
    """
    The core seal loop: find the file in the commit, backfill it, rename it.
    Cares about exactly what's on disk after the smoke clears.
    """

    def test_unsealed_changelog_gets_renamed_with_short_sha(
        self, git_repo, monkeypatch
    ):
        """
        The rename is the lock. Sealed filename must be CHANGELOG-{date}-{sha}.md.
        Without the rename, find_active_changelog() picks it up again on the next
        run and your changelog accumulates like a bad metaphor.
        """
        monkeypatch.chdir(git_repo.path)
        archive = git_repo.path / "ARCHIVE"
        changelog = _make_unsealed_changelog(archive)
        original_name = changelog.name

        full_sha, short_sha = _commit_changelog_and_get_shas(git_repo, changelog)
        run_seal(_seal_args(full_sha))

        assert not (archive / original_name).exists(), (
            f"Unsealed changelog still exists at {original_name}. "
            "The rename didn't happen. find_active_changelog() will pick this up "
            "on the next run and everything will be an absolute disaster."
        )
        sealed = _find_sealed_changelog(archive, short_sha)
        assert sealed is not None, (
            f"No sealed file matching CHANGELOG-*-{short_sha}.md found in {archive}. "
            "Either the rename failed or the SHA suffix is wrong."
        )

    def test_short_sha_backfilled_in_frontmatter_commit_sha_field(
        self, git_repo, monkeypatch
    ):
        monkeypatch.chdir(git_repo.path)
        archive = git_repo.path / "ARCHIVE"
        changelog = _make_unsealed_changelog(archive)

        full_sha, short_sha = _commit_changelog_and_get_shas(git_repo, changelog)
        run_seal(_seal_args(full_sha))

        sealed = _find_sealed_changelog(archive, short_sha)
        fm = extract_frontmatter(sealed.read_text(encoding="utf-8"))
        assert fm.get("commit-sha") == short_sha, (
            f"Expected commit-sha: {short_sha!r}, got: {fm.get('commit-sha')!r}. "
            "The frontmatter backfill regex is targeting the wrong line "
            "or the replace isn't writing back."
        )

    def test_full_sha_backfilled_in_body_overview_table(
        self, git_repo, monkeypatch
    ):
        monkeypatch.chdir(git_repo.path)
        archive = git_repo.path / "ARCHIVE"
        changelog = _make_unsealed_changelog(archive)

        full_sha, short_sha = _commit_changelog_and_get_shas(git_repo, changelog)
        run_seal(_seal_args(full_sha))

        sealed = _find_sealed_changelog(archive, short_sha)
        content = sealed.read_text(encoding="utf-8")
        assert full_sha in content, (
            f"Full SHA {full_sha!r} not found anywhere in the sealed changelog. "
            "The body table placeholder replacement is broken."
        )
        assert "[fill in after commit]" not in content, (
            "The placeholder is STILL IN THE BODY after sealing. "
            "The string replace either targeted the wrong thing or didn't run at all."
        )

    def test_sealed_filename_uses_short_sha_suffix_not_full_sha(
        self, git_repo, monkeypatch
    ):
        """
        Full SHAs are 40 characters of misery. The sealed filename format is
        CHANGELOG-{date}-{short_sha}.md. Absolutely not the full 40-char SHA.
        """
        monkeypatch.chdir(git_repo.path)
        archive = git_repo.path / "ARCHIVE"
        changelog = _make_unsealed_changelog(archive)

        full_sha, short_sha = _commit_changelog_and_get_shas(git_repo, changelog)
        run_seal(_seal_args(full_sha))

        short_sha_files = list(archive.glob(f"CHANGELOG-*-{short_sha}.md"))
        full_sha_files = list(archive.glob(f"CHANGELOG-*-{full_sha}.md"))

        assert short_sha_files, (
            f"No file with short SHA suffix {short_sha!r} found. Rename is broken."
        )
        assert not full_sha_files, (
            "A file with the FULL 40-char SHA as suffix exists. "
            "That is hideous. The suffix must be the short SHA."
        )

    def test_sealed_file_not_picked_up_by_find_active_changelog(
        self, git_repo, monkeypatch
    ):
        """
        This is the lock test. find_active_changelog() uses UNSEALED_RE to match
        CHANGELOG-YYYY-MM-DD.md — it explicitly excludes sealed files carrying a
        SHA suffix. After sealing, the file must be invisible to it.

        If this fails, every subsequent run() call will find the sealed changelog
        and treat it as an existing one to update. Iterative reruns will silently
        clobber a sealed, committed record. That's catastrophic.
        """
        monkeypatch.chdir(git_repo.path)
        archive = git_repo.path / "ARCHIVE"
        changelog = _make_unsealed_changelog(archive)

        full_sha, short_sha = _commit_changelog_and_get_shas(git_repo, changelog)
        run_seal(_seal_args(full_sha))

        # Verify the sealed file actually exists before asserting it's excluded
        sealed = _find_sealed_changelog(archive, short_sha)
        assert sealed is not None, "Seal didn't produce a sealed file — test setup broken."

        result = find_active_changelog(archive)
        assert result is None, (
            f"find_active_changelog() returned {result.name!r} after sealing. "
            "The sealed file is being picked up as an active changelog. "
            "UNSEALED_RE must not match filenames with a SHA suffix."
        )

    def test_user_content_below_sentinel_survives_sealing(
        self, git_repo, monkeypatch
    ):
        """
        Seal rewrites the file before renaming it. The rewrite touches exactly
        two things: the commit-sha frontmatter line and the body table cell.
        Everything else — including user content below the sentinel — must
        come through completely untouched.
        """
        monkeypatch.chdir(git_repo.path)
        archive = git_repo.path / "ARCHIVE"
        precious_note = "This is my precious handwritten note. Seal better not touch it.\n"
        changelog = _make_unsealed_changelog(archive, extra_body=precious_note)

        full_sha, short_sha = _commit_changelog_and_get_shas(git_repo, changelog)
        run_seal(_seal_args(full_sha))

        sealed = _find_sealed_changelog(archive, short_sha)
        content = sealed.read_text(encoding="utf-8")
        assert precious_note.strip() in content, (
            "Seal destroyed the user content below the sentinel. "
            "Seal backfills two strings and renames. Nothing else. "
            "If something else changed, there's a runaway replace somewhere."
        )

    def test_commit_with_no_unsealed_changelogs_exits_cleanly_with_note(
        self, git_repo, monkeypatch, capsys
    ):
        """
        No CHANGELOG-YYYY-MM-DD.md in the commit → print a progress note and return.
        No crash. No sys.exit. No drama.
        """
        monkeypatch.chdir(git_repo.path)
        git_repo.commit({"notes/not_a_changelog.md": "---\ntitle: Nope\n---\nBody."})
        full_sha = _get_full_sha(git_repo.path)

        # Must not raise
        run_seal(_seal_args(full_sha))

        out = capsys.readouterr().out
        assert "Nothing to do" in out or "No unsealed" in out, (
            "Expected a 'nothing to do' progress note when there's no changelog to seal. "
            "Complete silence makes debugging miserable."
        )


# ---------------------------------------------------------------------------
# TestSealAlreadySealedSkip
# ---------------------------------------------------------------------------

class TestSealAlreadySealedSkip:
    """
    A file with an unsealed filename but a SHA already in commit-sha represents
    a partial seal failure: seal backfilled the frontmatter, then crashed or was
    interrupted before it could rename the file. _is_already_sealed() is the
    recovery guard — it detects this broken intermediate state and skips
    re-processing rather than double-backfilling and blowing up on the rename.

    These tests verify that recovery path. They are NOT testing a normal
    operational scenario; they are testing seal's resilience against its own
    prior partial failure.

    Note on the "second run" pattern: after a successful seal, the original
    unsealed path is gone from disk (renamed). A second seal run against the
    same commit finds that path in the diff, checks if it exists on disk,
    finds it missing, and hits the warning path (stderr) — that is the
    missing-file path, not this recovery path. To exercise _is_already_sealed(),
    the file must be on disk with an unsealed filename and a SHA in frontmatter.
    """

    def test_partial_failure_state_is_skipped_not_reprocessed(
        self, git_repo, monkeypatch
    ):
        """
        Simulate a prior partial seal failure: the file has an unsealed filename
        but commit-sha in frontmatter is already backfilled. Seal detects this
        via _is_already_sealed() and skips it — no second backfill, no rename
        attempt, no crash.
        """
        monkeypatch.chdir(git_repo.path)
        archive = git_repo.path / "ARCHIVE"
        archive.mkdir(parents=True, exist_ok=True)

        today = get_today()
        pre_sealed_content = (
            f"---\nclass: archive\ncommit-sha: abc1234def\n---\n\n# Changelog — {today}\n"
        )
        pre_sealed_path = archive / f"CHANGELOG-{today}.md"
        pre_sealed_path.write_text(pre_sealed_content, encoding="utf-8")

        subprocess.run(["git", "add", str(pre_sealed_path)], cwd=git_repo.path, check=True)
        subprocess.run(
            ["git", "commit", "-m", "already sealed but wearing an unsealed filename"],
            cwd=git_repo.path, check=True, capture_output=True,
        )
        full_sha = _get_full_sha(git_repo.path)

        run_seal(_seal_args(full_sha))

        assert pre_sealed_path.exists(), (
            "The pre-sealed file was renamed or deleted. "
            "Seal is supposed to DETECT the existing SHA and skip the file entirely."
        )
        assert pre_sealed_path.read_text(encoding="utf-8") == pre_sealed_content, (
            "Pre-sealed file content was modified. "
            "Seal must not touch files that already have a SHA in commit-sha."
        )

    def test_partial_failure_recovery_increments_skipped_count_in_output(
        self, git_repo, monkeypatch, capsys
    ):
        """
        When _is_already_sealed() fires on a partial-failure file, skipped_count
        increments and the "already sealed — left alone" summary line appears in
        stdout, so the operator knows seal detected and recovered from a prior
        interrupted run.
        """
        monkeypatch.chdir(git_repo.path)
        archive = git_repo.path / "ARCHIVE"
        archive.mkdir(parents=True, exist_ok=True)

        today = get_today()
        pre_sealed_content = (
            f"---\nclass: archive\ncommit-sha: deadbeef1\n---\n\n# Changelog — {today}\n"
        )
        pre_sealed_path = archive / f"CHANGELOG-{today}.md"
        pre_sealed_path.write_text(pre_sealed_content, encoding="utf-8")

        subprocess.run(["git", "add", str(pre_sealed_path)], cwd=git_repo.path, check=True)
        subprocess.run(
            ["git", "commit", "-m", "pre-sealed for skip count test"],
            cwd=git_repo.path, check=True, capture_output=True,
        )
        full_sha = _get_full_sha(git_repo.path)

        run_seal(_seal_args(full_sha))

        out = capsys.readouterr().out
        assert "already sealed" in out.lower(), (
            "Expected 'already sealed' in stdout when skipping a file that already "
            "has a SHA in commit-sha. "
            "Either skipped_count isn't incrementing or the summary line isn't printing."
        )


# ---------------------------------------------------------------------------
# TestSealDatabaseInteraction
# ---------------------------------------------------------------------------

class TestSealDatabaseInteraction:
    """
    The DB half of seal: UUID → commit SHA transition.

    After sealing a changelog that has a UUID in frontmatter:
      - changelogs table must have a row with the commit SHA and seal timestamp
      - edition_shas.included_in must transition from UUID → short_sha for all
        SHAs claimed by that changelog

    Pre-UUID changelogs must skip the DB step cleanly without creating phantom rows.
    """

    def test_edition_shas_transition_from_uuid_to_short_sha(
        self, git_repo, monkeypatch
    ):
        """
        This is THE handoff. included_in goes from UUID (written by publication
        changelog) to short_sha (written by seal). If this breaks, edition SHAs
        will match the OR branch on every future rerun and end up in every
        subsequent changelog until the heat death of the universe.
        """
        monkeypatch.chdir(git_repo.path)
        archive = git_repo.path / "ARCHIVE"

        changelog_uuid = "cafebabe-dead-4eef-b00d-0123456789ab"
        edition_sha = "edition1abc"
        _seed_db_with_claimed_sha(git_repo.path, edition_sha, changelog_uuid)

        assert _get_edition_included_in(git_repo.path, edition_sha) == changelog_uuid

        changelog = _make_unsealed_changelog(archive, uuid=changelog_uuid)
        full_sha, short_sha = _commit_changelog_and_get_shas(git_repo, changelog)
        run_seal(_seal_args(full_sha))

        final = _get_edition_included_in(git_repo.path, edition_sha)
        assert final == short_sha, (
            f"edition_shas.included_in is {final!r}, expected short_sha {short_sha!r}. "
            "The UUID → commit SHA handoff in seal_changelog_in_db() is broken."
        )

    def test_multiple_edition_shas_all_transition_atomically(
        self, git_repo, monkeypatch
    ):
        """
        A publication changelog can claim multiple edition SHAs. Seal must
        transition EVERY SINGLE ONE, not just the first.
        """
        monkeypatch.chdir(git_repo.path)
        archive = git_repo.path / "ARCHIVE"

        changelog_uuid = "facefeed-abcd-4f01-a345-6789abcdef01"
        edition_shas = ["ed1aaa1111", "ed2bbb2222", "ed3ccc3333", "ed4ddd4444"]

        for sha in edition_shas:
            _seed_db_with_claimed_sha(git_repo.path, sha, changelog_uuid, f"Edition {sha}")

        changelog = _make_unsealed_changelog(archive, uuid=changelog_uuid)
        full_sha, short_sha = _commit_changelog_and_get_shas(git_repo, changelog)
        run_seal(_seal_args(full_sha))

        for sha in edition_shas:
            final = _get_edition_included_in(git_repo.path, sha)
            assert final == short_sha, (
                f"Edition SHA {sha!r} still has included_in={final!r}, expected {short_sha!r}. "
                "The batch UPDATE in seal_changelog_in_db() isn't catching all rows. "
                "Partial transitions are worse than no transitions."
            )

    def test_changelogs_table_row_populated_after_seal(
        self, git_repo, monkeypatch
    ):
        monkeypatch.chdir(git_repo.path)
        archive = git_repo.path / "ARCHIVE"

        # Seal only writes to the DB if it already exists — create it first
        db_path = git_repo.path / "ARCHIVE" / "archive.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        init_db(db_path)

        changelog_uuid = "deadcafe-1234-4678-9abc-def012345678"
        changelog = _make_unsealed_changelog(archive, uuid=changelog_uuid)
        full_sha, short_sha = _commit_changelog_and_get_shas(git_repo, changelog)
        run_seal(_seal_args(full_sha))

        row = _get_changelogs_row(git_repo.path, changelog_uuid)
        assert row is not None, (
            f"No row in changelogs table for UUID {changelog_uuid!r}. "
            "seal_changelog_in_db() isn't writing to the changelogs table."
        )
        assert row["commit_sha"] == short_sha, (
            f"changelogs.commit_sha is {row['commit_sha']!r}, expected {short_sha!r}. "
            "Wrong SHA stored — or the wrong SHA was passed to seal_changelog_in_db()."
        )
        assert row["sealed_at"] is not None, (
            "changelogs.sealed_at is None. The seal timestamp wasn't written."
        )

    def test_no_archive_db_means_no_crash_and_no_db_created(
        self, git_repo, monkeypatch
    ):
        """
        No archive.db → seal_changelog_in_db() is a documented no-op.
        The DB must not be conjured into existence by seal. That's manifest's job.
        """
        monkeypatch.chdir(git_repo.path)
        archive = git_repo.path / "ARCHIVE"
        db_path = git_repo.path / "ARCHIVE" / "archive.db"
        assert not db_path.exists()

        changelog = _make_unsealed_changelog(archive, uuid="facefade-fade-4ade-8ade-000000000000")
        full_sha, short_sha = _commit_changelog_and_get_shas(git_repo, changelog)

        # Must not raise
        run_seal(_seal_args(full_sha))

        # File should still be sealed correctly despite no DB
        sealed = _find_sealed_changelog(archive, short_sha)
        assert sealed is not None, (
            "Seal failed to rename the file even though the missing DB is supposed "
            "to be a no-op, not a failure mode."
        )

        # DB must still not exist — seal doesn't create it
        assert not db_path.exists(), (
            "Seal created the archive DB when there was none. "
            "DB creation belongs to manifest, not seal. "
            "Seal's job is to UPDATE an existing DB, not birth new ones."
        )

    def test_changelog_without_uuid_skips_db_writes_cleanly(
        self, git_repo, monkeypatch
    ):
        """
        Pre-DB-era changelogs have no UUID field. Seal must:
          - still backfill the SHA
          - still rename the file
          - NOT write any rows to the DB
          - NOT crash
        """
        monkeypatch.chdir(git_repo.path)
        archive = git_repo.path / "ARCHIVE"

        # DB exists — seal would write to it if it found a UUID
        db_path = git_repo.path / "ARCHIVE" / "archive.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = init_db(db_path)
        conn.close()

        # Changelog with NO UUID field (pre-DB era)
        changelog = _make_unsealed_changelog(archive, uuid=None)
        full_sha, short_sha = _commit_changelog_and_get_shas(git_repo, changelog)
        run_seal(_seal_args(full_sha))

        # File must be sealed correctly
        sealed = _find_sealed_changelog(archive, short_sha)
        assert sealed is not None, "Seal failed to rename the no-UUID changelog."
        fm = extract_frontmatter(sealed.read_text(encoding="utf-8"))
        assert fm.get("commit-sha") == short_sha, (
            "Seal failed to backfill the SHA in a no-UUID changelog. "
            "The no-UUID path is missing the backfill or returning early too soon."
        )

        # DB must have no rows in changelogs — no UUID means no row
        conn = sqlite3.connect(db_path)
        row_count = conn.execute("SELECT COUNT(*) FROM changelogs").fetchone()[0]
        conn.close()
        assert row_count == 0, (
            f"Got {row_count} row(s) in changelogs table after sealing a no-UUID changelog. "
            "Seal must not write to changelogs without a UUID."
        )


# ---------------------------------------------------------------------------
# TestSealMultipleChangelogs
# ---------------------------------------------------------------------------

class TestSealMultipleChangelogs:
    """
    A single commit can contain multiple unsealed changelogs — vault commits
    commonly touch a vault-level changelog and submodule changelogs together.
    Seal must process every single one of them.
    """

    def test_seals_all_unsealed_changelogs_in_a_single_commit(
        self, git_repo, monkeypatch
    ):
        monkeypatch.chdir(git_repo.path)

        archive_root = git_repo.path / "ARCHIVE"
        archive_sub = git_repo.path / "ARCHIVE" / "CHANGELOG"

        cl_a = _make_unsealed_changelog(
            archive_root, uuid="aaaaaaaa-0000-4000-8000-000000000001"
        )
        cl_b = _make_unsealed_changelog(
            archive_sub, uuid="bbbbbbbb-0000-4000-8000-000000000002"
        )

        subprocess.run(
            ["git", "add", str(cl_a), str(cl_b)],
            cwd=git_repo.path, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "two changelogs, one commit"],
            cwd=git_repo.path, check=True, capture_output=True,
        )
        full_sha = _get_full_sha(git_repo.path)
        short_sha = _get_short_sha(git_repo.path)

        run_seal(_seal_args(full_sha))

        sealed_a = _find_sealed_changelog(archive_root, short_sha)
        sealed_b = _find_sealed_changelog(archive_sub, short_sha)

        assert sealed_a is not None, (
            "First changelog (ARCHIVE/) was not sealed. "
            "Seal stopped after one file — that's a bug in the loop, not a feature."
        )
        assert sealed_b is not None, (
            "Second changelog (ARCHIVE/CHANGELOG/) was not sealed. "
            "Seal stopped after the first file."
        )

    def test_sealed_count_reported_correctly_in_output(
        self, git_repo, monkeypatch, capsys
    ):
        monkeypatch.chdir(git_repo.path)

        archive_root = git_repo.path / "ARCHIVE"
        archive_sub = git_repo.path / "ARCHIVE" / "CHANGELOG"

        cl_a = _make_unsealed_changelog(archive_root, uuid="cccccccc-cccc-4ccc-8ccc-cccccccc0001")
        cl_b = _make_unsealed_changelog(archive_sub, uuid="dddddddd-dddd-4ddd-8ddd-dddddddd0002")

        subprocess.run(
            ["git", "add", str(cl_a), str(cl_b)],
            cwd=git_repo.path, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "count these"],
            cwd=git_repo.path, check=True, capture_output=True,
        )
        full_sha = _get_full_sha(git_repo.path)

        run_seal(_seal_args(full_sha))

        out = capsys.readouterr().out
        assert "2" in out, (
            "Expected '2' somewhere in the seal summary for 2 sealed changelogs. "
            "The sealed_count summary line is missing or the count is wrong."
        )


# ---------------------------------------------------------------------------
# TestSealEdgeCases
# ---------------------------------------------------------------------------

class TestSealEdgeCases:
    """
    Weird shit: no SHA provided, files disappearing between commit and seal,
    invalid input, idempotency.
    """

    def test_exits_when_no_commit_sha_provided(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        with pytest.raises(SystemExit):
            run_seal(argparse.Namespace(commit_sha=None))

    def test_exits_when_commit_sha_attribute_missing_from_namespace(
        self, git_repo, monkeypatch
    ):
        """
        seal.py uses getattr with a None fallback. Test that a missing attribute
        still triggers the sys.exit path rather than AttributeError chaos.
        """
        monkeypatch.chdir(git_repo.path)
        with pytest.raises(SystemExit):
            run_seal(argparse.Namespace())

    def test_missing_file_on_disk_produces_warning_not_crash(
        self, git_repo, monkeypatch, capsys
    ):
        """
        File was committed, then disappeared from disk before seal ran.
        Happens when a previous seal run got partway through and then crashed,
        or someone manually renamed shit. Expected behavior: warn and skip,
        don't explode. The warning goes to stderr via output.warning().
        """
        monkeypatch.chdir(git_repo.path)
        archive = git_repo.path / "ARCHIVE"
        changelog = _make_unsealed_changelog(archive)

        full_sha, short_sha = _commit_changelog_and_get_shas(git_repo, changelog)

        # Yeet the file off disk between commit and seal
        changelog.unlink(missing_ok=True)

        # Must not raise
        run_seal(_seal_args(full_sha))

        # The warning goes to stderr — check there, not stdout
        err = capsys.readouterr().err
        assert "Skipping" in err or "manually" in err.lower(), (
            "Expected a warning message about the missing file in stderr. "
            "Silent skips for missing files are how debugging becomes archaeology."
        )

    def test_running_seal_twice_is_idempotent(
        self, git_repo, monkeypatch
    ):
        """
        Idempotency. Full stop. Two seal runs against the same commit must
        produce the same final state. The second run finds the unsealed filename
        missing from disk (renamed by the first run), warns, and exits without
        touching the already-sealed file.
        """
        monkeypatch.chdir(git_repo.path)
        archive = git_repo.path / "ARCHIVE"
        changelog = _make_unsealed_changelog(archive, uuid="1de10000-0000-4000-8000-000000009999")

        full_sha, short_sha = _commit_changelog_and_get_shas(git_repo, changelog)
        run_seal(_seal_args(full_sha))

        sealed = _find_sealed_changelog(archive, short_sha)
        assert sealed is not None, "First seal run failed — test setup is broken."
        content_after_first = sealed.read_text(encoding="utf-8")

        run_seal(_seal_args(full_sha))

        assert sealed.exists(), "Sealed file disappeared after second run."
        assert sealed.read_text(encoding="utf-8") == content_after_first, (
            "Sealed file content changed on the second seal run. "
            "Seal is not idempotent. This will cause problems in the real world."
        )
        assert len(list(archive.glob(f"CHANGELOG-*-{short_sha}.md"))) == 1, (
            "More than one sealed file found after running seal twice. "
            "The file was double-renamed somehow."
        )