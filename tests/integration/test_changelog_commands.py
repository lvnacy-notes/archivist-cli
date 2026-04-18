"""
tests/integration/test_changelog_commands.py

Integration tests for all five changelog subcommands: general, story,
library, vault, and publication.

These tests call run() directly against a real git repo with real staged
changes. No mocking of subprocess. No fake git diffs. No bullshit.

The two most important tests in this entire file:

  1. test_dry_run_writes_absolutely_nothing — exists in every subcommand
     class. If ANY of these fail, the dry-run contract is broken and we
     are lying to users. Fix immediately, ship nothing until fixed.

  2. test_user_content_below_sentinel_survives_rerun — exists in every
     subcommand class. The sentinel boundary is the whole reason iterative
     reruns exist. If user content gets wiped on rerun, we're destroying
     people's work. This is catastrophic, not a minor regression.

Note on "nothing staged → exits with error": the strategy doc mentions
this case, but the current implementation of run_changelog() always
passes output_dir to ensure_staged(), making path non-None. The
nothing-is-staged exit lives in the else-branch of ensure_staged(),
which is unreachable from run_changelog(). We test the actual observed
behavior instead: empty diff produces an empty changelog without crashing.

Note on dry-run directory creation: find_changelog_output_dir() calls
output_dir.mkdir(parents=True, exist_ok=True) unconditionally in step 1
of run_changelog(), before the dry-run gate at step 10. This means
ARCHIVE/ and ARCHIVE/CHANGELOG/ get created as empty directories even on
dry runs. No files are written — the dry-run contract (no content written)
holds — but the directories do appear. The dry-run tests therefore compare
file sets only (p.is_file()), not directory entries.

Note on dual-prompt scenarios (--path scope):
When --path is active, ensure_staged() stages only the scope directory,
leaving ARCHIVE/ untouched. A committed-then-modified changelog will
appear in BOTH _get_out_of_scope_unstaged() (because it's outside the
scope). Tests that probe the save-before-overwrite prompt use an
iter-based mock that feeds 'n' first (out-of-scope prompt) then 'y'
(save prompt), matching the actual call order in run_changelog():
Step 3 → prompt_out_of_scope_changes, Step 6 → _prompt_save_before_overwrite.
"""

import argparse
import sqlite3
import subprocess
from pathlib import Path

import pytest

from archivist.commands.changelog.general import run as run_general
from archivist.commands.changelog.library import run as run_library
from archivist.commands.changelog.publication import run as run_publication
from archivist.commands.changelog.story import run as run_story
from archivist.commands.changelog.vault import run as run_vault
from archivist.utils import (
    extract_frontmatter,
    get_today,
    init_db,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers — because typing argparse.Namespace(...) forty times is miserable
# ---------------------------------------------------------------------------

def _cl_args(**kwargs) -> argparse.Namespace:
    """
    Stamp out a changelog args namespace with sane defaults.
    All three kwargs a changelog run() function could want: dry_run,
    commit_sha, path. Override any of them with keyword args.
    """
    defaults = {"dry_run": False, "commit_sha": None, "path": None}
    return argparse.Namespace(**{**defaults, **kwargs})


def _find_changelog(output_dir: Path) -> Path:
    """
    Locate and return the first changelog file in output_dir.
    Blows up with a clear assertion error if nothing's there — if your
    test setup is broken, you should know about it immediately.
    """
    changelogs = list(output_dir.glob("CHANGELOG-*.md"))
    assert changelogs, (
        f"No changelog found in {output_dir}. "
        f"Did the run() call actually do anything, or did it silently bail?"
    )
    return changelogs[0]


def _read_changelog(output_dir: Path) -> str:
    return _find_changelog(output_dir).read_text(encoding="utf-8")


def _stage_only(git_repo, rel_path: str, content: str) -> None:
    """
    Write a file and stage ONLY that file via a targeted `git add <path>`.

    Do NOT replace this with git_repo.stage() — that fixture method runs
    `git add --all`, which stages every file in the repo. Tests that rely
    on specific files remaining unstaged (to trigger
    _get_out_of_scope_unstaged) will silently pass for the wrong reasons if
    you use the nuclear option here.
    """
    p = git_repo.path / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    subprocess.run(
        ["git", "add", rel_path], cwd=git_repo.path, check=True, capture_output=True
    )


# ---------------------------------------------------------------------------
# TestChangelogGeneral
# ---------------------------------------------------------------------------

class TestChangelogGeneral:
    """
    Tests for `archivist changelog general`.
    Output goes to ARCHIVE/ at the repo root — NOT ARCHIVE/CHANGELOG/.
    """

    def test_dry_run_writes_absolutely_nothing(self, git_repo, monkeypatch):
        """
        THE most important test in this class. dry_run=True must not
        create or modify a single file under the repo root. If this
        fails, the dry-run contract is broken and we are liars.
        """
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({"notes/shiny_new_note.md": "---\ntitle: Shiny\n---\nBody."})
        before = {p for p in git_repo.path.rglob("*") if p.is_file()}

        run_general(_cl_args(dry_run=True))

        after = {p for p in git_repo.path.rglob("*") if p.is_file()}
        assert before == after, (
            "dry_run=True and files still changed on disk. "
            "A dry run that writes is just called a run."
        )

    def test_creates_changelog_in_archive_dir(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({"notes/thing.md": "---\ntitle: Thing\n---\nBody."})

        run_general(_cl_args())

        archive = git_repo.path / "ARCHIVE"
        assert archive.exists(), "ARCHIVE/ directory was not created — find_changelog_output_dir is broken"
        changelogs = list(archive.glob(f"CHANGELOG-{get_today()}.md"))
        assert changelogs, f"No CHANGELOG-{get_today()}.md found in ARCHIVE/"

    def test_changelog_frontmatter_has_required_fields(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({"notes/thing.md": "---\ntitle: Thing\n---\nBody."})

        run_general(_cl_args())

        content = _read_changelog(git_repo.path / "ARCHIVE")
        fm = extract_frontmatter(content)

        assert fm.get("class") == "archive", (
            f"Expected class: archive, got: {fm.get('class')!r}"
        )
        assert fm.get("log-scope") == "general", (
            f"Expected log-scope: general, got: {fm.get('log-scope')!r}"
        )
        assert fm.get("UUID"), "UUID field is empty or missing entirely"
        assert "commit-sha" in fm, "commit-sha field is missing from frontmatter"
        assert "files-created" in fm, "files-created counter missing from frontmatter"
        assert "files-modified" in fm, "files-modified counter missing from frontmatter"
        assert "files-archived" in fm, "files-archived counter missing from frontmatter"

    def test_changelog_body_contains_auto_end_sentinel(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({"notes/thing.md": "---\ntitle: Thing\n---\nBody."})

        run_general(_cl_args())

        content = _read_changelog(git_repo.path / "ARCHIVE")
        assert "<!-- archivist:auto-end -->" in content, (
            "The sentinel is missing from the generated changelog. "
            "Without it, reruns cannot find the boundary and will gleefully "
            "obliterate user content. Fix this before shipping anything."
        )

    def test_reruns_update_existing_file_not_spawn_a_second_one(
        self, git_repo, monkeypatch
    ):
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({"notes/first.md": "---\ntitle: First\n---\nBody."})
        run_general(_cl_args())

        git_repo.stage({"notes/second.md": "---\ntitle: Second\n---\nBody."})
        # Mock input() for the save confirmation prompt that fires on rerun
        monkeypatch.setattr("builtins.input", lambda _: "y")
        run_general(_cl_args())

        changelogs = list((git_repo.path / "ARCHIVE").glob(f"CHANGELOG-{get_today()}.md"))
        assert len(changelogs) == 1, (
            f"Expected exactly 1 changelog, found {len(changelogs)}. "
            "Reruns should update in place, not breed."
        )

    def test_user_content_below_sentinel_survives_rerun(self, git_repo, monkeypatch):
        """
        THE second most important test. The sentinel boundary is the
        entire reason iterative reruns exist. User content below
        <!-- archivist:auto-end --> must survive every subsequent rerun
        without a single character touched.
        """
        monkeypatch.chdir(git_repo.path)

        # First run
        git_repo.stage({"notes/alpha.md": "---\ntitle: Alpha\n---\nBody."})
        run_general(_cl_args())

        archive_dir = git_repo.path / "ARCHIVE"
        changelog = _find_changelog(archive_dir)
        content = changelog.read_text(encoding="utf-8")
        assert "<!-- archivist:auto-end -->" in content

        # Simulate the user adding precious notes below the sentinel
        precious_content = "## My Notes\n\nSome important shit I wrote by hand.\n"
        changelog.write_text(content + "\n" + precious_content, encoding="utf-8")

        # Stage another file and rerun
        git_repo.stage({"notes/beta.md": "---\ntitle: Beta\n---\nBody."})
        # Mock input() for the save confirmation prompt that fires on rerun
        monkeypatch.setattr("builtins.input", lambda _: "y")
        run_general(_cl_args())

        result = changelog.read_text(encoding="utf-8")
        assert "Some important shit I wrote by hand." in result, (
            "Rerun wiped the user content below the sentinel. "
            "This is catastrophic. Go fix extract_user_content() and weep."
        )

    def test_staged_rename_appears_in_modified_section_with_annotation(
        self, git_repo, monkeypatch
    ):
        monkeypatch.chdir(git_repo.path)

        # Commit a file so it exists in history
        git_repo.commit({"notes/original_name.md": "---\ntitle: Original\n---\nBody."})

        # Perform a rename: delete old, write new with identical content so
        # git's similarity threshold detects it as a rename, not a D + A
        (git_repo.path / "notes" / "original_name.md").unlink()
        git_repo.stage({"notes/renamed_and_shiny.md": "---\ntitle: Original\n---\nBody."})

        run_general(_cl_args())

        content = _read_changelog(git_repo.path / "ARCHIVE")
        assert "renamed" in content.lower(), (
            "A staged rename produced no rename annotation in the changelog. "
            "Either rename detection is broken or the formatter dropped it."
        )

    def test_empty_diff_produces_changelog_with_zero_counters(
        self, git_repo, monkeypatch
    ):
        """
        When nothing is staged, run_changelog() stages output_dir (which is
        empty/new), gets an empty diff, warns, and writes a zero-count
        changelog anyway. It should NOT crash or sys.exit. We test the
        actual observed behavior here — see module docstring for context.
        """
        monkeypatch.chdir(git_repo.path)
        # Stage nothing — run_general will git-add ARCHIVE/ (empty) and carry on

        run_general(_cl_args())

        changelogs = list((git_repo.path / "ARCHIVE").glob("CHANGELOG-*.md"))
        assert changelogs, "Even an empty diff should produce a changelog file"

        fm = extract_frontmatter(changelogs[0].read_text(encoding="utf-8"))
        assert fm.get("files-created") == 0
        assert fm.get("files-modified") == 0
        assert fm.get("files-archived") == 0

    def test_output_dir_is_created_if_it_does_not_exist(
        self, git_repo, monkeypatch
    ):
        monkeypatch.chdir(git_repo.path)
        archive = git_repo.path / "ARCHIVE"
        assert not archive.exists(), "ARCHIVE/ already exists before the test started — fixture is dirty"

        git_repo.stage({"notes/thing.md": "---\ntitle: Thing\n---\n"})
        run_general(_cl_args())

        assert archive.exists(), "ARCHIVE/ was not created by run_general"

    def test_uuid_is_preserved_across_reruns(self, git_repo, monkeypatch):
        """
        The UUID written on the first run must persist on rerun. It's the
        key that lets publication changelogs re-surface their claimed SHAs.
        Generating a fresh UUID on every rerun breaks that contract.
        """
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({"notes/a.md": "---\ntitle: A\n---\n"})
        run_general(_cl_args())

        archive_dir = git_repo.path / "ARCHIVE"
        first_uuid = extract_frontmatter(_read_changelog(archive_dir)).get("UUID")
        assert first_uuid, "UUID was empty after first run"

        git_repo.stage({"notes/b.md": "---\ntitle: B\n---\n"})
        # Mock input() for the save confirmation prompt that fires on rerun
        monkeypatch.setattr("builtins.input", lambda _: "y")
        run_general(_cl_args())

        second_uuid = extract_frontmatter(_read_changelog(archive_dir)).get("UUID")
        assert first_uuid == second_uuid, (
            f"UUID changed on rerun: {first_uuid!r} → {second_uuid!r}. "
            "The UUID must be stable for the lifetime of an unsealed changelog."
        )


# ---------------------------------------------------------------------------
# TestChangelogStory
# ---------------------------------------------------------------------------

class TestChangelogStory:
    """
    Tests for `archivist changelog story`.
    Output goes to ARCHIVE/CHANGELOG/ — NOT the flat ARCHIVE/.
    Contains writing-session-specific sections that general doesn't have.
    """

    def test_dry_run_writes_absolutely_nothing(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({"scenes/act_one.md": "---\ntitle: Act One\n---\nScene stuff."})
        before = {p for p in git_repo.path.rglob("*") if p.is_file()}

        run_story(_cl_args(dry_run=True))

        after = {p for p in git_repo.path.rglob("*") if p.is_file()}
        assert before == after, (
            "Story dry-run wrote files to disk. "
            "A dry run that writes is just called a run with a lying flag."
        )

    def test_creates_changelog_in_archive_changelog_subdir(
        self, git_repo, monkeypatch
    ):
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({"scenes/act_one.md": "---\ntitle: Act One\n---\nScene stuff."})

        run_story(_cl_args())

        output_dir = git_repo.path / "ARCHIVE" / "CHANGELOG"
        assert output_dir.exists(), "ARCHIVE/CHANGELOG/ subdir was not created for story output"
        assert _find_changelog(output_dir), "No changelog found in ARCHIVE/CHANGELOG/"

        # It must NOT land in the flat ARCHIVE/ dir
        flat_changelogs = list((git_repo.path / "ARCHIVE").glob("CHANGELOG-*.md"))
        assert not flat_changelogs, (
            "Story changelog landed in flat ARCHIVE/ instead of ARCHIVE/CHANGELOG/. "
            "Module-type routing is broken."
        )

    def test_frontmatter_log_scope_is_story(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({"scenes/act_one.md": "---\ntitle: Act One\n---\nScene stuff."})

        run_story(_cl_args())

        fm = extract_frontmatter(_read_changelog(git_repo.path / "ARCHIVE" / "CHANGELOG"))
        assert fm.get("log-scope") == "story", (
            f"Expected log-scope: story, got: {fm.get('log-scope')!r}. "
            "Either the frontmatter builder is broken or you ran the wrong subcommand."
        )

    def test_body_contains_story_specific_sections(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({"scenes/act_one.md": "---\ntitle: Act One\n---\nScene stuff."})

        run_story(_cl_args())

        content = _read_changelog(git_repo.path / "ARCHIVE" / "CHANGELOG")
        assert "Story Development" in content, (
            "Story Development section missing — this is a story changelog"
        )
        assert "Technical Updates" in content, "Technical Updates section missing"
        assert "Publication Preparation" in content, "Publication Preparation section missing"
        assert "Detailed Change Log" in content, "Detailed Change Log section missing"

    def test_user_content_below_sentinel_survives_rerun(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({"scenes/act_one.md": "---\ntitle: Act One\n---\nScene stuff."})
        run_story(_cl_args())

        output_dir = git_repo.path / "ARCHIVE" / "CHANGELOG"
        changelog = _find_changelog(output_dir)
        content = changelog.read_text(encoding="utf-8")

        session_notes = "## Session Notes\n\nKilled a darling today. Worth it.\n"
        changelog.write_text(content + "\n" + session_notes, encoding="utf-8")

        git_repo.stage({"scenes/act_two.md": "---\ntitle: Act Two\n---\nMore scene stuff."})
        # Mock input() for the save confirmation prompt that fires on rerun
        monkeypatch.setattr("builtins.input", lambda _: "y")
        run_story(_cl_args())

        result = changelog.read_text(encoding="utf-8")
        assert "Killed a darling today. Worth it." in result, (
            "Story rerun wiped the session notes. "
            "Every writer using this tool just lost something because of you."
        )


# ---------------------------------------------------------------------------
# TestChangelogLibrary
# ---------------------------------------------------------------------------

class TestChangelogLibrary:
    """
    Tests for `archivist changelog library`.

    This one's heavier than the rest because it reads frontmatter from
    changed files to route them into catalog-specific sections:
      - work-stage files → Catalog Changes / Works
      - class: author files → Author Cards
      - class: collection files → Publication Cards
      - class: entry files → Definitions
      - everything else → Other File Changes

    Output goes to ARCHIVE/ (not ARCHIVE/CHANGELOG/).
    """

    def test_dry_run_writes_absolutely_nothing(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({
            "works/the_name_of_the_wind.md": (
                "---\ntitle: The Name of the Wind\nwork-stage: active\n---\nBody."
            )
        })
        before = {p for p in git_repo.path.rglob("*") if p.is_file()}

        run_library(_cl_args(dry_run=True))

        after = {p for p in git_repo.path.rglob("*") if p.is_file()}
        assert before == after, "Library dry-run left fingerprints on disk. Unacceptable."

    def test_creates_changelog_in_archive_dir(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({
            "works/dune.md": "---\ntitle: Dune\nwork-stage: processed\n---\nBody."
        })

        run_library(_cl_args())

        changelogs = list((git_repo.path / "ARCHIVE").glob("CHANGELOG-*.md"))
        assert changelogs, "Library changelog not found in ARCHIVE/"

    def test_frontmatter_contains_works_counters(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({
            "works/dune.md": "---\ntitle: Dune\nwork-stage: processed\n---\nBody.",
            "works/dune_messiah.md": "---\ntitle: Dune Messiah\nwork-stage: raw\n---\nBody.",
        })

        run_library(_cl_args())

        fm = extract_frontmatter(_read_changelog(git_repo.path / "ARCHIVE"))
        assert "works-added" in fm, "works-added counter missing from library frontmatter"
        assert "works-updated" in fm, "works-updated counter missing"
        assert "works-removed" in fm, "works-removed counter missing"
        assert "authors-added" in fm, "authors-added counter missing"
        assert "authors-updated" in fm, "authors-updated counter missing"
        assert "publications-added" in fm, "publications-added counter missing"
        assert "definitions-added" in fm, "definitions-added counter missing"
        assert fm.get("works-added") == 2, (
            f"Expected works-added: 2, got: {fm.get('works-added')}. "
            "work-stage detection is broken or counting wrong."
        )

    def test_work_stage_file_routes_to_catalog_section_not_other_files(
        self, git_repo, monkeypatch
    ):
        """
        A file carrying work-stage in its frontmatter must land in
        ## Catalog Changes, not ## Other File Changes.
        """
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({
            "works/blood_meridian.md": (
                "---\ntitle: Blood Meridian\nwork-stage: shelved\n---\nThe kid."
            )
        })

        run_library(_cl_args())

        content = _read_changelog(git_repo.path / "ARCHIVE")

        catalog_pos = content.find("## Catalog Changes")
        other_pos = content.find("## Other File Changes")
        # _work_list renders the title field ("Blood Meridian"), not the
        # filename stem ("blood_meridian"). Searching for the underscore form
        # finds nothing and the test lies. Search for what actually appears.
        file_pos = content.find("Blood Meridian")

        assert catalog_pos != -1, "## Catalog Changes section is missing from library changelog"
        assert other_pos != -1, "## Other File Changes section is missing from library changelog"
        assert file_pos != -1, (
            "'Blood Meridian' doesn't appear in the changelog at all. "
            "_work_list renders the title field — if it's missing, "
            "either frontmatter parsing failed or the work wasn't routed."
        )
        assert catalog_pos < file_pos < other_pos, (
            "work-stage file ended up in Other File Changes instead of Catalog Changes. "
            "Frontmatter-based routing is broken."
        )

    def test_author_class_file_routes_to_author_cards_section(
        self, git_repo, monkeypatch
    ):
        """
        A file with class: author must land in ## Author Cards,
        not ## Other File Changes or ## Catalog Changes.
        """
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({
            "authors/cormac_mccarthy.md": (
                "---\nclass: author\nname: Cormac McCarthy\n---\nBody."
            )
        })

        run_library(_cl_args())

        content = _read_changelog(git_repo.path / "ARCHIVE")

        author_cards_pos = content.find("## Author Cards")
        other_pos = content.find("## Other File Changes")
        file_pos = content.find("cormac_mccarthy")

        assert author_cards_pos != -1, "## Author Cards section is missing"
        assert file_pos != -1, "cormac_mccarthy.md doesn't appear in the changelog at all"
        assert author_cards_pos < file_pos < other_pos, (
            "class: author file ended up in the wrong section. "
            "Author routing is broken."
        )

    def test_plain_file_without_special_frontmatter_falls_through_to_other(
        self, git_repo, monkeypatch
    ):
        """
        A plain .md file with no work-stage and no special class must land
        in ## Other File Changes. If it somehow claims a catalog section,
        something is routing things that have no business being routed.
        """
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({
            "notes/miscellaneous_garbage.md": "---\ntitle: Misc\n---\nJust a note."
        })

        run_library(_cl_args())

        content = _read_changelog(git_repo.path / "ARCHIVE")

        other_pos = content.find("## Other File Changes")
        file_pos = content.find("miscellaneous_garbage")

        assert other_pos != -1, "## Other File Changes section is missing entirely"
        assert file_pos != -1, "miscellaneous_garbage.md doesn't appear in the changelog at all"
        assert file_pos > other_pos, (
            "Plain file without work-stage or special class routed itself into "
            "a catalog section it has absolutely no business being in."
        )

    def test_catalog_snapshot_section_is_present_in_body(
        self, git_repo, monkeypatch
    ):
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({
            "works/some_book.md": "---\ntitle: Some Book\nwork-stage: raw\n---\nBody."
        })

        run_library(_cl_args())

        content = _read_changelog(git_repo.path / "ARCHIVE")
        assert "## Catalog Snapshot" in content, (
            "Catalog Snapshot section missing from library changelog. "
            "_build_catalog_snapshot() probably failed silently."
        )

    def test_user_content_below_sentinel_survives_rerun(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({
            "works/dune.md": "---\ntitle: Dune\nwork-stage: processed\n---\nBody."
        })
        run_library(_cl_args())

        archive_dir = git_repo.path / "ARCHIVE"
        changelog = _find_changelog(archive_dir)
        content = changelog.read_text(encoding="utf-8")

        reading_notes = "## Reading Notes\n\nSandworms are load-bearing metaphors.\n"
        changelog.write_text(content + "\n" + reading_notes, encoding="utf-8")

        git_repo.stage({
            "works/dune_messiah.md": "---\ntitle: Dune Messiah\nwork-stage: raw\n---\nBody."
        })
        # Mock input() for the save confirmation prompt that fires on rerun
        monkeypatch.setattr("builtins.input", lambda _: "y")
        run_library(_cl_args())

        result = changelog.read_text(encoding="utf-8")
        assert "Sandworms are load-bearing metaphors." in result, (
            "Library rerun torched the reading notes. That's the user's catalogue, not ours."
        )


# ---------------------------------------------------------------------------
# TestChangelogVault
# ---------------------------------------------------------------------------

class TestChangelogVault:
    """
    Tests for `archivist changelog vault`.

    Tracks vault-wide file changes alongside submodule state: current SHAs,
    dirty status, unpushed commits. In test repos with no submodules, the
    section will show the empty-state placeholder — that's fine and expected.

    Output goes to ARCHIVE/ (not ARCHIVE/CHANGELOG/).
    """

    def test_dry_run_writes_absolutely_nothing(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({"templates/daily_note.md": "---\ntitle: Daily\n---\nBody."})
        before = {p for p in git_repo.path.rglob("*") if p.is_file()}

        run_vault(_cl_args(dry_run=True))

        after = {p for p in git_repo.path.rglob("*") if p.is_file()}
        assert before == after, (
            "Vault dry-run touched the filesystem. "
            "Dry runs that write are just runs with extra dishonesty."
        )

    def test_frontmatter_log_scope_is_vault(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({"templates/daily_note.md": "---\ntitle: Daily\n---\nBody."})

        run_vault(_cl_args())

        fm = extract_frontmatter(_read_changelog(git_repo.path / "ARCHIVE"))
        assert fm.get("log-scope") == "vault", (
            f"Expected log-scope: vault, got: {fm.get('log-scope')!r}"
        )

    def test_submodule_section_is_present_in_body(self, git_repo, monkeypatch):
        """
        The Submodules section must always be present. In a test repo with
        no submodules, we expect the empty-state placeholder text.
        """
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({"templates/daily_note.md": "---\ntitle: Daily\n---\nBody."})

        run_vault(_cl_args())

        content = _read_changelog(git_repo.path / "ARCHIVE")
        assert "## Submodules" in content, (
            "## Submodules section is missing from the vault changelog. "
            "This is a vault changelog — submodules are literally the point."
        )

    def test_submodule_empty_state_placeholder_present_when_no_submodules(
        self, git_repo, monkeypatch
    ):
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({"templates/daily_note.md": "---\ntitle: Daily\n---\nBody."})

        run_vault(_cl_args())

        content = _read_changelog(git_repo.path / "ARCHIVE")
        # In a repo with no submodules, both sub-sections should say so
        assert (
            "No submodules registered" in content
            or "No submodules updated" in content
        ), (
            "Expected an empty-state placeholder in the submodules section "
            "for a repo with no registered submodules."
        )

    def test_template_files_route_to_templates_and_scaffolding_section(
        self, git_repo, monkeypatch
    ):
        """
        Files whose paths contain 'template' or 'scaffold' should end up in
        the Templates & Scaffolding section, not Other Files Modified.
        """
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({"templates/weekly_review.md": "---\ntitle: Weekly\n---\nBody."})

        run_vault(_cl_args())

        content = _read_changelog(git_repo.path / "ARCHIVE")

        templates_pos = content.find("### Templates & Scaffolding")
        other_pos = content.find("### Other Files Modified")
        file_pos = content.find("weekly_review")

        assert templates_pos != -1, "Templates & Scaffolding section is missing"
        assert file_pos != -1, "weekly_review.md doesn't appear in changelog at all"
        assert templates_pos < file_pos < other_pos, (
            "Template file didn't land in the Templates & Scaffolding section."
        )

    def test_user_content_below_sentinel_survives_rerun(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({"templates/daily_note.md": "---\ntitle: Daily\n---\nBody."})
        run_vault(_cl_args())

        archive_dir = git_repo.path / "ARCHIVE"
        changelog = _find_changelog(archive_dir)
        content = changelog.read_text(encoding="utf-8")

        health_check = "## Vault Health\n\nEverything is on fire but fine.\n"
        changelog.write_text(content + "\n" + health_check, encoding="utf-8")

        git_repo.stage({"templates/weekly.md": "---\ntitle: Weekly\n---\nBody."})
        # Mock input() for the save confirmation prompt that fires on rerun
        monkeypatch.setattr("builtins.input", lambda _: "y")
        run_vault(_cl_args())

        result = changelog.read_text(encoding="utf-8")
        assert "Everything is on fire but fine." in result, (
            "Vault rerun torched the health check notes. "
            "Poetic, given the context. Still wrong."
        )


# ---------------------------------------------------------------------------
# TestChangelogPublication
# ---------------------------------------------------------------------------

class TestChangelogPublication:
    """
    Tests for `archivist changelog publication`.

    This is the most stateful of the five subcommands — it queries and
    modifies the archive SQLite DB to track edition SHAs through their
    lifecycle: unclaimed → claimed by UUID → transitioned to commit SHA
    at seal time (the seal transition is tested in test_seal.py).

    Output goes to ARCHIVE/CHANGELOG/ (same as story).
    """

    # ── DB helpers ──────────────────────────────────────────────────────────

    def _seed_db_with_unclaimed_sha(
        self, git_root: Path, sha: str, message: str = "test edition"
    ) -> None:
        """
        Create the archive DB and insert a single unclaimed edition SHA.
        This replicates what `archivist manifest --register` does in production.
        """
        db_path = git_root / "ARCHIVE" / "archive.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = init_db(db_path)
        conn.execute(
            """INSERT INTO edition_shas (sha, commit_message, discovered_at)
               VALUES (?, ?, '2024-01-01')""",
            (sha, message),
        )
        conn.commit()
        conn.close()

    def _get_included_in(self, git_root: Path, sha: str) -> str | None:
        """
        Read included_in for a SHA directly from the DB.
        Returns None if the DB doesn't exist or the SHA isn't found.
        """
        db_path = git_root / "ARCHIVE" / "archive.db"
        if not db_path.exists():
            return None
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT included_in FROM edition_shas WHERE sha = ?", (sha,)
        ).fetchone()
        conn.close()
        return row[0] if row else None

    # ── Tests ────────────────────────────────────────────────────────────────

    def test_dry_run_writes_absolutely_nothing(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({"editions/042/note.md": "---\ntitle: Edition 042\n---\nBody."})
        before = {p for p in git_repo.path.rglob("*") if p.is_file()}

        run_publication(_cl_args(dry_run=True))

        after = {p for p in git_repo.path.rglob("*") if p.is_file()}
        assert before == after, (
            "Publication dry-run created or modified files. "
            "The DB was probably written too, which is equally fucked."
        )

    def test_dry_run_does_not_claim_shas_in_db(self, git_repo, monkeypatch):
        """
        A dry run must not mark any SHA as included in the DB.
        The post-write hook is responsible for DB writes — verify it checks
        dry_run before touching anything.
        """
        monkeypatch.chdir(git_repo.path)
        test_sha = "deadbeef1234"
        self._seed_db_with_unclaimed_sha(git_repo.path, test_sha, "Test edition")
        git_repo.stage({"editions/042/note.md": "---\ntitle: Edition 042\n---\nBody."})

        run_publication(_cl_args(dry_run=True))

        claimed = self._get_included_in(git_repo.path, test_sha)
        assert claimed is None, (
            f"Dry-run modified the DB — SHA is now claimed as {claimed!r}. "
            "Dry runs must not touch the DB. Ever."
        )

    def test_frontmatter_contains_editions_sha_field(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({"editions/042/note.md": "---\ntitle: Edition 042\n---\nBody."})

        run_publication(_cl_args())

        fm = extract_frontmatter(_read_changelog(git_repo.path / "ARCHIVE" / "CHANGELOG"))
        assert "editions-sha" in fm, (
            "editions-sha field missing from publication frontmatter. "
            "This field is the entire reason publication changelog exists."
        )

    def test_no_archive_db_proceeds_without_crashing(
        self, git_repo, monkeypatch, capsys
    ):
        """
        No archive.db → should proceed cleanly, print a note to stderr,
        and produce a changelog with an empty editions-sha list.
        Should absolutely not crash or sys.exit.
        """
        monkeypatch.chdir(git_repo.path)
        assert not (git_repo.path / "ARCHIVE" / "archive.db").exists()

        git_repo.stage({"editions/042/note.md": "---\ntitle: Edition 042\n---\nBody."})
        # Must not raise
        run_publication(_cl_args())

        captured = capsys.readouterr()
        assert "archive DB" in captured.err or "No archive DB" in captured.err, (
            "Expected a note about the missing archive DB on stderr. "
            "Silent failures are how bugs become disasters weeks later."
        )

        fm = extract_frontmatter(_read_changelog(git_repo.path / "ARCHIVE" / "CHANGELOG"))
        assert fm.get("editions-sha") == [], (
            f"editions-sha should be an empty list when there's no DB, got: {fm.get('editions-sha')!r}"
        )

    def test_unclaimed_shas_in_db_appear_in_frontmatter_and_body(
        self, git_repo, monkeypatch
    ):
        monkeypatch.chdir(git_repo.path)
        test_sha = "abc123def456"
        self._seed_db_with_unclaimed_sha(git_repo.path, test_sha, "A real edition commit")
        git_repo.stage({"editions/042/note.md": "---\ntitle: Edition 042\n---\nBody."})

        run_publication(_cl_args())

        output_dir = git_repo.path / "ARCHIVE" / "CHANGELOG"
        content = _read_changelog(output_dir)
        fm = extract_frontmatter(content)

        assert test_sha in fm.get("editions-sha", []), (
            f"SHA {test_sha!r} should appear in editions-sha frontmatter but doesn't. "
            "DB query or frontmatter builder is broken."
        )
        assert test_sha in content, (
            f"SHA {test_sha!r} should appear in the changelog body too, not just frontmatter."
        )

    def test_unclaimed_shas_get_claimed_with_uuid_after_run(
        self, git_repo, monkeypatch
    ):
        """
        After a successful non-dry-run, the SHA must be marked as claimed
        in the DB. At this stage it should be claimed with the changelog's
        UUID — the transition to commit SHA happens at seal time.
        """
        import re
        uuid_pattern = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        )

        monkeypatch.chdir(git_repo.path)
        test_sha = "facade00coffee"
        self._seed_db_with_unclaimed_sha(git_repo.path, test_sha)
        git_repo.stage({"editions/001/note.md": "---\ntitle: Edition 001\n---\nBody."})

        run_publication(_cl_args())

        claimed = self._get_included_in(git_repo.path, test_sha)
        assert claimed is not None, (
            f"SHA {test_sha!r} is still unclaimed after a successful run. "
            "The post-write hook failed to mark it in the DB."
        )
        assert uuid_pattern.match(claimed), (
            f"included_in is {claimed!r}, which doesn't look like a UUID. "
            "Should be a UUID at this stage — SHA transition only happens at seal time."
        )

    def test_rerun_same_shas_still_appear_in_output(self, git_repo, monkeypatch):
        """
        SHAs claimed by the current changelog's UUID must reappear on rerun.
        They must NOT be silently dropped just because included_in is no
        longer NULL. This is the OR branch in _get_edition_shas:
          WHERE included_in IS NULL OR included_in = <current_uuid>
        If that branch breaks, iterative reruns lose their edition SHAs.
        """
        monkeypatch.chdir(git_repo.path)
        test_sha = "reaaalsha9999"
        self._seed_db_with_unclaimed_sha(git_repo.path, test_sha, "Persistent edition")
        git_repo.stage({"editions/001/note.md": "---\ntitle: Edition 001\n---\nBody."})

        # First run — SHA gets claimed with the changelog UUID
        run_publication(_cl_args())
        claimed_uuid = self._get_included_in(git_repo.path, test_sha)
        assert claimed_uuid is not None, "SHA should be claimed after first run"

        # Second run — SHA must still appear because its UUID matches the existing changelog
        git_repo.stage({"editions/002/note.md": "---\ntitle: Edition 002\n---\nBody."})
        # Mock input() for the save confirmation prompt that fires on rerun
        monkeypatch.setattr("builtins.input", lambda _: "y")
        run_publication(_cl_args())

        output_dir = git_repo.path / "ARCHIVE" / "CHANGELOG"
        fm = extract_frontmatter(_read_changelog(output_dir))
        assert test_sha in fm.get("editions-sha", []), (
            f"SHA {test_sha!r} disappeared from the changelog on rerun. "
            "The UUID-based re-claim query is broken. "
            "SHAs claimed by the current changelog's UUID must persist across reruns."
        )

    def test_user_content_below_sentinel_survives_rerun(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({"editions/001/note.md": "---\ntitle: Edition 001\n---\nBody."})
        run_publication(_cl_args())

        output_dir = git_repo.path / "ARCHIVE" / "CHANGELOG"
        changelog = _find_changelog(output_dir)
        content = changelog.read_text(encoding="utf-8")

        editorial_note = "## Editorial Notes\n\nIssue 42 ships Friday. God help us.\n"
        changelog.write_text(content + "\n" + editorial_note, encoding="utf-8")

        git_repo.stage({"editions/002/note.md": "---\ntitle: Edition 002\n---\nBody."})
        # Mock input() for the save confirmation prompt that fires on rerun
        monkeypatch.setattr("builtins.input", lambda _: "y")
        run_publication(_cl_args())

        result = changelog.read_text(encoding="utf-8")
        assert "Issue 42 ships Friday. God help us." in result, (
            "Publication rerun destroyed the editorial notes. "
            "If we're nuking content in the publication changelog of all places, "
            "we have deeply failed the use case this tool was built for."
        )


# ---------------------------------------------------------------------------
# TestSaveBeforeOverwritePrompt
# ---------------------------------------------------------------------------


class TestSaveBeforeOverwritePrompt:
    """
    Tests for _wait_for_save_confirmation() — the guard that blocks
    Archivist from overwriting a changelog until the user confirms
    they've saved their edits.

    Fires at Step 6 of run_changelog() when:
      - not dry_run
      - find_active_changelog() returns an existing file

    Does NOT fire on a fresh run (no existing changelog found).
    """

    def test_abort_response_calls_sys_exit(self, git_repo, monkeypatch):
        """
        Anything that isn't 'y' or 'yes' must abort. Full stop.
        Mash enter, type 'n', type 'nope' — all of them should exit cleanly
        rather than blowing past the prompt and overwriting a dirty buffer.
        """
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({"notes/a.md": "---\ntitle: A\n---\n"})
        run_general(_cl_args())  # first run — no prompt, no existing changelog

        git_repo.stage({"notes/b.md": "---\ntitle: B\n---\n"})
        monkeypatch.setattr("builtins.input", lambda _: "n")

        with pytest.raises(SystemExit) as exc_info:
            run_general(_cl_args())

        assert exc_info.value.code == 0, (
            f"Expected sys.exit(0) on abort, got exit code {exc_info.value.code!r}. "
            "_wait_for_save_confirmation() should exit cleanly, not crash."
        )

    def test_yes_full_word_is_also_accepted(self, git_repo, monkeypatch):
        """
        'yes' (full word) must be treated as confirmation, same as 'y'.
        The code explicitly allows both — pin that contract.
        """
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({"notes/a.md": "---\ntitle: A\n---\n"})
        run_general(_cl_args())

        git_repo.stage({"notes/b.md": "---\ntitle: B\n---\n"})
        monkeypatch.setattr("builtins.input", lambda _: "yes")

        # Must not raise or sys.exit
        run_general(_cl_args())

        changelogs = list((git_repo.path / "ARCHIVE").glob("CHANGELOG-*.md"))
        assert len(changelogs) == 1, (
            "'yes' should have been accepted as confirmation, but the run "
            f"either aborted or spawned extra changelogs. Found: {len(changelogs)}."
        )

    def test_prompt_does_not_fire_on_first_run(self, git_repo, monkeypatch):
        """
        No existing changelog → no prompt. input() should never be called
        on a fresh run. This is the complement to the abort test above —
        if the prompt fires unconditionally, you're nagging users who've
        done nothing wrong.
        """
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({"notes/thing.md": "---\ntitle: Thing\n---\n"})

        input_was_called = False

        def catch_unexpected_input(_):
            nonlocal input_was_called
            input_was_called = True
            return "y"

        monkeypatch.setattr("builtins.input", catch_unexpected_input)
        run_general(_cl_args())

        assert not input_was_called, (
            "Save prompt fired on a fresh run with no existing changelog. "
            "find_active_changelog() must have returned something it shouldn't, "
            "or the `if existing:` guard in run_changelog() Step 6 is missing."
        )


# ---------------------------------------------------------------------------
# TestChangelogOutOfScopePrompt
# ---------------------------------------------------------------------------

class TestChangelogOutOfScopePrompt:
    """
    Tests for prompt_out_of_scope_changes() — the function that notices
    unstaged changes outside your --path scope and offers to include them.

    Fires at Step 3 of run_changelog() when:
      - not dry_run
      - scope_path is not None (--path was given)
      - _get_out_of_scope_unstaged() finds something

    With no --path: scope_path is None → function not called → no prompt ever.
    """

    def test_no_prompt_when_all_changes_are_inside_scope(
        self, git_repo, monkeypatch
    ):
        """
        All staged changes are in notes/. Nothing outside the scope.
        Nobody should be asked anything.
        """
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({"notes/in_scope.md": "---\ntitle: In Scope\n---\nBody."})

        input_was_called = False

        def catch_unexpected_input(_):
            nonlocal input_was_called
            input_was_called = True
            return "n"

        monkeypatch.setattr("builtins.input", catch_unexpected_input)
        run_general(_cl_args(path="notes"))

        assert not input_was_called, (
            "Out-of-scope prompt fired despite no changes existing outside the scope. "
            "_get_out_of_scope_unstaged returned something it shouldn't have. "
            "Check that git diff and git ls-files are interpreting scope_prefix correctly."
        )

    def test_prompt_fires_when_untracked_file_exists_outside_scope(
        self, git_repo, monkeypatch
    ):
        """
        Untracked file sitting outside the scope. The user might have forgotten
        to stage a related file. Prompt should fire so they can decide.
        """
        monkeypatch.chdir(git_repo.path)
        git_repo.stage({"notes/scoped.md": "---\ntitle: Scoped\n---\nBody."})

        # Create an untracked file outside the scope — not staged, not committed
        scripts_dir = git_repo.path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "new_script.sh").write_text("#!/bin/bash\necho hi", encoding="utf-8")

        prompt_calls = []
        monkeypatch.setattr("builtins.input", lambda _: prompt_calls.append(True) or "n")

        run_general(_cl_args(path="notes"))

        assert prompt_calls, (
            "Out-of-scope prompt never fired despite an untracked file outside the scope. "
            "Either _get_out_of_scope_unstaged is filtering it out or "
            "git ls-files --others isn't seeing it."
        )

    def test_prompt_fires_when_tracked_file_is_modified_outside_scope(
        self, git_repo, monkeypatch
    ):
        """
        Committed file modified in working tree, outside scope.
        git diff --name-only surfaces it. Prompt should fire.
        """
        monkeypatch.chdir(git_repo.path)

        # Commit a file outside the scope so it's tracked
        git_repo.commit({"scripts/deploy.sh": "#!/bin/bash\necho deploy v1"})

        # Modify without staging
        (git_repo.path / "scripts" / "deploy.sh").write_text(
            "#!/bin/bash\necho deploy v2", encoding="utf-8"
        )

        # Stage something inside scope for the diff — targeted so deploy.sh stays
        # as a working-tree modification visible to _get_out_of_scope_unstaged.
        # git_repo.stage() uses git add --all and would sweep up deploy.sh.
        _stage_only(git_repo, "notes/scoped.md", "---\ntitle: Scoped\n---\nBody.")

        prompt_calls = []
        monkeypatch.setattr("builtins.input", lambda _: prompt_calls.append(True) or "n")

        run_general(_cl_args(path="notes"))

        assert prompt_calls, (
            "Out-of-scope prompt never fired despite a tracked modified file outside the scope. "
            "git diff --name-only should surface working-tree modifications vs the index."
        )

    def test_y_response_stages_the_out_of_scope_file(self, git_repo, monkeypatch):
        """
        User says 'y' → the out-of-scope file gets staged. Full stop.
        """
        monkeypatch.chdir(git_repo.path)

        scripts_dir = git_repo.path / "scripts"
        scripts_dir.mkdir()
        forgotten = scripts_dir / "forgotten.sh"
        forgotten.write_text("#!/bin/bash\necho forgotten", encoding="utf-8")

        # Targeted stage — git add --all would sweep up forgotten.sh immediately,
        # meaning the out-of-scope prompt would never fire and the 'y' response
        # would be testing nothing. The assertion would pass for the wrong reason.
        _stage_only(git_repo, "notes/scoped.md", "---\ntitle: Scoped\n---\nBody.")
        monkeypatch.setattr("builtins.input", lambda _: "y")

        run_general(_cl_args(path="notes"))

        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=git_repo.path, capture_output=True, text=True, check=True,
        ).stdout.strip().splitlines()

        assert any("forgotten.sh" in f for f in staged), (
            "User said 'y' to the out-of-scope prompt but scripts/forgotten.sh "
            "isn't in the staged index. "
            "The `git add f` loop in prompt_out_of_scope_changes() isn't running."
        )

    def test_n_response_leaves_out_of_scope_file_unstaged(
        self, git_repo, monkeypatch
    ):
        """
        User says 'n' → unstaged changes stay unstaged. Their call.
        The prompt is advisory, not mandatory.
        """
        monkeypatch.chdir(git_repo.path)

        scripts_dir = git_repo.path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "stay_out.sh").write_text("#!/bin/bash\necho nope", encoding="utf-8")

        # Targeted stage — git add --all would sweep up stay_out.sh, making it
        # staged before run_general runs. The assertion would fail because the
        # file ends up staged regardless of what the user answers.
        _stage_only(git_repo, "notes/scoped.md", "---\ntitle: Scoped\n---\nBody.")
        monkeypatch.setattr("builtins.input", lambda _: "n")

        run_general(_cl_args(path="notes"))

        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=git_repo.path, capture_output=True, text=True, check=True,
        ).stdout.strip().splitlines()

        assert not any("stay_out.sh" in f for f in staged), (
            "User said 'n' to the out-of-scope prompt but scripts/stay_out.sh "
            "got staged anyway. That's the exact opposite of what 'n' means."
        )

    def test_no_prompt_when_path_scope_not_given(self, git_repo, monkeypatch):
        """
        No --path → scope_path is None → prompt_out_of_scope_changes is never
        called. Changes everywhere are just… changes. No nag.
        """
        monkeypatch.chdir(git_repo.path)

        # Untracked file that WOULD be out-of-scope if a scope existed
        (git_repo.path / "random_untracked.sh").write_text("#!/bin/bash", encoding="utf-8")
        git_repo.stage({"notes/thing.md": "---\ntitle: Thing\n---\nBody."})

        input_was_called = False

        def catch_unexpected_input(_):
            nonlocal input_was_called
            input_was_called = True
            return "n"

        monkeypatch.setattr("builtins.input", catch_unexpected_input)

        # No path argument → scope_path is None → the entire out-of-scope block is skipped
        run_general(_cl_args())

        assert not input_was_called, (
            "Out-of-scope prompt fired even though no --path scope was given. "
            "The `if scope_path is not None:` guard in run_changelog() Step 3 "
            "is missing or bypassed."
        )

    def test_no_prompt_during_dry_run_even_with_out_of_scope_changes(
        self, git_repo, monkeypatch
    ):
        """
        Dry run: zero interactivity. Not for the save prompt, not for out-of-scope.
        The entire `if not args.dry_run:` block at Step 3 gates both.
        """
        monkeypatch.chdir(git_repo.path)

        scripts_dir = git_repo.path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "untracked.sh").write_text("#!/bin/bash", encoding="utf-8")

        git_repo.stage({"notes/thing.md": "---\ntitle: Thing\n---\nBody."})

        input_was_called = False

        def catch_any_input(_):
            nonlocal input_was_called
            input_was_called = True
            return "n"

        monkeypatch.setattr("builtins.input", catch_any_input)

        run_general(_cl_args(path="notes", dry_run=True))

        assert not input_was_called, (
            "A prompt fired during dry run. "
            "The `if not args.dry_run:` block at Step 3 of run_changelog() "
            "is not guarding prompt_out_of_scope_changes correctly."
        )

    def test_out_of_scope_files_listed_in_output_before_prompt(
        self, git_repo, monkeypatch, capsys
    ):
        """
        Before firing the prompt, the function must print the list of out-of-scope
        files so the user knows what they'd be staging. Asking a question without
        showing the options is not a feature, it's a bug.
        """
        monkeypatch.chdir(git_repo.path)

        scripts_dir = git_repo.path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "list_me.sh").write_text("#!/bin/bash\necho list me", encoding="utf-8")

        # Targeted stage — git add --all would sweep up list_me.sh. Then
        # _get_out_of_scope_unstaged finds nothing → no prompt → nothing listed.
        _stage_only(git_repo, "notes/scoped.md", "---\ntitle: Scoped\n---\nBody.")
        monkeypatch.setattr("builtins.input", lambda _: "n")

        run_general(_cl_args(path="notes"))

        out = capsys.readouterr().out
        assert "list_me.sh" in out, (
            "Out-of-scope file was not listed in the output before the prompt. "
            "The user is being asked to stage something they can't see. "
            "Check the print loop in prompt_out_of_scope_changes()."
        )