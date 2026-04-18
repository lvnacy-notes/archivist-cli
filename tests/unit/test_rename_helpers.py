"""
tests/unit/test_renames.py

Unit tests for archivist.utils.renames (and rename.py where rename_display_path lives).

No git, no disk, no bullshit. Pure function calls with crafted inputs.
If one of these fails, the rename logic is broken and you should feel bad.
"""

from archivist.utils import (
    clean_filename,
    detect_dir_renames,
    infer_undetected_renames,
    process_renames_from_changes,
    reassign_deletions,
    rename_suspicion,
)


# ---------------------------------------------------------------------------
# Helpers — build the bare-minimum changes dict so tests aren't typing
# {"M": [], "A": [], "D": [], "R": []} forty-seven times.
# ---------------------------------------------------------------------------

def _changes(*, A=None, M=None, D=None, R=None):
    return {
        "A": list(A or []),
        "M": list(M or []),
        "D": list(D or []),
        "R": list(R or []),
    }


# ===========================================================================
# clean_filename
# ===========================================================================

class TestCleanFilename:
    """
    clean_filename() strips trailing non-alphanumeric garbage from the stem
    and returns only the filename component of a path.

    The canonical use case: Obsidian suffixes conflict copies with shit like
    ' 1', ' 2', or the occasional cryptic punctuation disaster.
    """

    def test_plain_filename_untouched(self):
        assert clean_filename("notes/my-note.md") == "my-note.md"

    def test_strips_trailing_space_number(self):
        # Obsidian conflict copies get suffixed with ' 1', ' 2', etc.
        assert clean_filename("notes/my-note 1.md") == "my-note.md"

    def test_strips_trailing_punctuation(self):
        assert clean_filename("notes/my-note-.md") == "my-note.md"

    def test_strips_multiple_trailing_garbage_chars(self):
        assert clean_filename("notes/some-file 2 .md") == "some-file.md"

    def test_alphanumeric_stem_preserved(self):
        # Trailing digit that is genuinely part of the name (no non-alnum after)
        assert clean_filename("notes/chapter01.md") == "chapter01.md"

    def test_ignores_directory_components(self):
        # Only the filename matters; the path prefix is dropped
        assert clean_filename("deep/nested/path/file.md") == "file.md"

    def test_preserves_extension(self):
        assert clean_filename("docs/README.txt") == "README.txt"

    def test_bare_filename_no_dir(self):
        assert clean_filename("standalone.md") == "standalone.md"

    def test_hyphens_in_middle_of_stem_untouched(self):
        # Hyphens mid-stem are fine; only trailing garbage gets the axe
        assert clean_filename("my-great-note.md") == "my-great-note.md"

    def test_deep_path_with_conflict_suffix(self):
        assert clean_filename("ARCHIVE/EDITIONS/042/draft 3.md") == "draft.md"


# ===========================================================================
# detect_dir_renames
# ===========================================================================

class TestDetectDirRenames:
    """
    detect_dir_renames() takes file-level rename pairs and infers which
    *directories* were renamed.

    Returns {old_dir_prefix: new_dir_prefix}.
    """

    def test_empty_renames_returns_empty_dict(self):
        assert detect_dir_renames([]) == {}

    def test_same_directory_rename_not_included(self):
        # File renamed within the same directory — no dir rename to infer
        renames = [("notes/old-name.md", "notes/new-name.md")]
        assert detect_dir_renames(renames) == {}

    def test_single_cross_directory_rename(self):
        renames = [("old-dir/file.md", "new-dir/file.md")]
        result = detect_dir_renames(renames)
        assert result == {"old-dir": "new-dir"}

    def test_multiple_files_same_dir_rename_deduplicates(self):
        """
        When multiple files all agree on the same old→new directory mapping, the
        result dict ends up with one entry for that pair. Technically this is
        last-write-wins on dict assignment — an accepted behaviour per the testing
        strategy, since disagreeing pairs (conflicting dir mappings for the same
        old prefix) are a git pathology we don't need to defend against here.
        The important thing is that the result is correct and contains exactly
        one entry, not three.
        """
        renames = [
            ("old-dir/a.md", "new-dir/a.md"),
            ("old-dir/b.md", "new-dir/b.md"),
            ("old-dir/c.md", "new-dir/c.md"),
        ]
        result = detect_dir_renames(renames)
        assert result == {"old-dir": "new-dir"}
        assert len(result) == 1

    def test_multiple_distinct_dir_renames(self):
        renames = [
            ("alpha/file.md", "alpha-renamed/file.md"),
            ("beta/other.md", "beta-renamed/other.md"),
        ]
        result = detect_dir_renames(renames)
        assert result == {
            "alpha": "alpha-renamed",
            "beta": "beta-renamed",
        }

    def test_nested_directory_rename(self):
        renames = [("EDITIONS/042/draft.md", "ARCHIVE/EDITIONS/042/draft.md")]
        result = detect_dir_renames(renames)
        assert result == {"EDITIONS/042": "ARCHIVE/EDITIONS/042"}

    def test_mixed_same_dir_and_cross_dir(self):
        renames = [
            ("notes/old-name.md", "notes/new-name.md"),   # same dir — ignored
            ("drafts/work.md",    "published/work.md"),    # cross-dir — captured
        ]
        result = detect_dir_renames(renames)
        assert result == {"drafts": "published"}


# ===========================================================================
# infer_undetected_renames
# ===========================================================================

class TestInferUndetectedRenames:
    """
    infer_undetected_renames() finds D/A pairs with matching filenames that
    git's -M flag missed. Only unambiguous pairs (exactly one deleted, one
    added, same filename) get inferred. Ambiguous cases are left the fuck alone.
    """

    def test_empty_changes_returns_empty_list(self):
        assert infer_undetected_renames(_changes()) == []

    def test_no_unpaired_files_returns_empty(self):
        # Everything already has a rename pair
        changes = _changes(R=[("old/file.md", "new/file.md")])
        assert infer_undetected_renames(changes) == []

    def test_infers_simple_move(self):
        changes = _changes(
            D=["old-dir/note.md"],
            A=["new-dir/note.md"],
        )
        result = infer_undetected_renames(changes)
        assert result == [("old-dir/note.md", "new-dir/note.md")]

    def test_different_filenames_not_paired(self):
        changes = _changes(
            D=["old/note-a.md"],
            A=["new/note-b.md"],
        )
        assert infer_undetected_renames(changes) == []

    def test_ambiguous_added_locations_left_unpaired(self):
        # Same filename added in TWO places — we have no fucking clue which
        # one is the rename, so we leave both alone
        changes = _changes(
            D=["old/note.md"],
            A=["new-a/note.md", "new-b/note.md"],
        )
        assert infer_undetected_renames(changes) == []

    def test_already_r_paired_deleted_side_excluded(self):
        # old/note.md is already in R — should not also show up as inferred
        changes = _changes(
            D=["old/note.md"],
            A=["new/note.md"],
            R=[("old/note.md", "elsewhere/note.md")],
        )
        result = infer_undetected_renames(changes)
        assert result == []

    def test_already_r_added_side_excluded(self):
        # new/note.md is already the destination of a known rename
        changes = _changes(
            D=["old/note.md"],
            A=["new/note.md"],
            R=[("somewhere/note.md", "new/note.md")],
        )
        result = infer_undetected_renames(changes)
        assert result == []

    def test_infers_multiple_independent_moves(self):
        changes = _changes(
            D=["old/alpha.md", "old/beta.md"],
            A=["new/alpha.md", "new/beta.md"],
        )
        result = infer_undetected_renames(changes)
        assert sorted(result) == sorted([
            ("old/alpha.md", "new/alpha.md"),
            ("old/beta.md", "new/beta.md"),
        ])

    def test_true_deletion_untouched_when_no_matching_add(self):
        changes = _changes(
            D=["old/note.md", "permanently-gone.md"],
            A=["new/note.md"],
        )
        result = infer_undetected_renames(changes)
        # permanently-gone.md has no match in A — stays out of result
        assert result == [("old/note.md", "new/note.md")]


# ===========================================================================
# reassign_deletions
# ===========================================================================

class TestReassignDeletions:
    """
    reassign_deletions() separates true deletions from files that only
    *look* deleted because their parent directory got renamed.

    Returns (true_deleted, dir_renamed_files).
    """

    def test_empty_inputs_return_empty_outputs(self):
        true_del, dir_renamed = reassign_deletions([], {})
        assert true_del == []
        assert dir_renamed == []

    def test_no_dir_renames_all_stay_deleted(self):
        deleted = ["notes/a.md", "notes/b.md"]
        true_del, dir_renamed = reassign_deletions(deleted, {})
        assert sorted(true_del) == sorted(deleted)
        assert dir_renamed == []

    def test_dir_renamed_file_is_reassigned(self):
        deleted = ["old-dir/file.md"]
        dir_renames = {"old-dir": "new-dir"}
        true_del, dir_renamed = reassign_deletions(deleted, dir_renames)
        assert true_del == []
        assert dir_renamed == [("old-dir/file.md", "new-dir/file.md")]

    def test_mixed_true_deletions_and_dir_renamed(self):
        deleted = ["old-dir/moved.md", "genuinely-deleted.md"]
        dir_renames = {"old-dir": "new-dir"}
        true_del, dir_renamed = reassign_deletions(deleted, dir_renames)
        assert true_del == ["genuinely-deleted.md"]
        assert dir_renamed == [("old-dir/moved.md", "new-dir/moved.md")]

    def test_nested_dir_rename_path_reconstructed_correctly(self):
        deleted = ["EDITIONS/042/draft.md"]
        dir_renames = {"EDITIONS/042": "ARCHIVE/EDITIONS/042"}
        true_del, dir_renamed = reassign_deletions(deleted, dir_renames)
        assert true_del == []
        assert dir_renamed == [("EDITIONS/042/draft.md", "ARCHIVE/EDITIONS/042/draft.md")]

    def test_multiple_files_under_same_renamed_dir(self):
        deleted = ["old/a.md", "old/b.md", "old/c.md"]
        dir_renames = {"old": "new"}
        true_del, dir_renamed = reassign_deletions(deleted, dir_renames)
        assert true_del == []
        assert sorted(dir_renamed) == sorted([
            ("old/a.md", "new/a.md"),
            ("old/b.md", "new/b.md"),
            ("old/c.md", "new/c.md"),
        ])

    def test_prefix_match_is_exact_on_parent_not_startswith(self):
        # "old" renamed to "new", but "old-stuff/file.md"'s parent is
        # "old-stuff", NOT "old" — should NOT be reassigned
        deleted = ["old-stuff/file.md"]
        dir_renames = {"old": "new"}
        true_del, dir_renamed = reassign_deletions(deleted, dir_renames)
        assert true_del == ["old-stuff/file.md"]
        assert dir_renamed == []


# ===========================================================================
# process_renames_from_changes
# ===========================================================================

class TestProcessRenamesFromChanges:
    """
    process_renames_from_changes() inverts changes["R"] from a list of
    (old, new) tuples into a {new: old} lookup dict.

    It's one line. It should be bulletproof. These tests are here to make
    sure nobody "helpfully" breaks it.
    """

    def test_empty_renames_returns_empty_dict(self):
        assert process_renames_from_changes(_changes()) == {}

    def test_single_rename_inverted(self):
        changes = _changes(R=[("old/file.md", "new/file.md")])
        assert process_renames_from_changes(changes) == {"new/file.md": "old/file.md"}

    def test_multiple_renames_all_inverted(self):
        changes = _changes(R=[
            ("old/a.md", "new/a.md"),
            ("old/b.md", "new/b.md"),
        ])
        result = process_renames_from_changes(changes)
        assert result == {
            "new/a.md": "old/a.md",
            "new/b.md": "old/b.md",
        }

    def test_other_change_types_are_ignored(self):
        changes = _changes(
            M=["modified.md"],
            A=["added.md"],
            D=["deleted.md"],
            R=[("old/note.md", "new/note.md")],
        )
        result = process_renames_from_changes(changes)
        assert result == {"new/note.md": "old/note.md"}

    def test_cross_directory_rename(self):
        changes = _changes(R=[("EDITIONS/042/draft.md", "ARCHIVE/EDITIONS/042/draft.md")])
        result = process_renames_from_changes(changes)
        assert result == {"ARCHIVE/EDITIONS/042/draft.md": "EDITIONS/042/draft.md"}


# ===========================================================================
# rename_suspicion
# ===========================================================================

class TestRenameSuspicion:
    """
    rename_suspicion() returns a non-empty warning string when a rename looks
    sketchy — cross-directory moves or unrelated stem names.

    Returns empty string when everything checks out.
    """

    def test_clean_rename_same_dir_related_name_returns_empty(self):
        result = rename_suspicion("notes/my-note.md", "notes/my-note-v2.md")
        assert result == ""

    def test_exact_same_path_returns_empty(self):
        result = rename_suspicion("notes/file.md", "notes/file.md")
        assert result == ""

    def test_cross_directory_flagged(self):
        # Same filename, different directory — cross-directory fires, name mismatch does not.
        # The negative assertion matters: if both flags fired here, the warning would
        # misrepresent a perfectly valid rename that just crossed a directory boundary.
        result = rename_suspicion("notes/file.md", "archive/file.md")
        assert result != ""
        assert "cross-directory" in result
        assert "name mismatch" not in result

    def test_name_mismatch_flagged(self):
        # Completely unrelated stems, same directory — name mismatch fires, cross-directory does not.
        # Negative assertion: a same-dir rename with mismatched names is suspicious on its own;
        # we don't want the message lying about a directory move that didn't happen.
        result = rename_suspicion("notes/alpha.md", "notes/zeta.md")
        assert result != ""
        assert "name mismatch" in result
        assert "cross-directory" not in result

    def test_both_flags_fire_when_both_conditions_met(self):
        result = rename_suspicion("notes/alpha.md", "archive/zeta.md")
        assert "cross-directory" in result
        assert "name mismatch" in result

    def test_substring_match_suppresses_name_mismatch(self):
        # "note" is a substring of "notebook" — no mismatch
        result = rename_suspicion("notes/note.md", "notes/notebook.md")
        assert "name mismatch" not in result

    def test_reverse_substring_also_suppresses_name_mismatch(self):
        result = rename_suspicion("notes/notebook.md", "notes/note.md")
        assert "name mismatch" not in result

    def test_trailing_obsidian_garbage_stripped_before_comparison(self):
        # Conflict suffix on old path should not cause a false name mismatch
        result = rename_suspicion("notes/my-note 1.md", "notes/my-note.md")
        assert result == ""

    def test_warning_string_contains_expected_formatting(self):
        result = rename_suspicion("a/alpha.md", "b/beta.md")
        assert "⚠️" in result
        assert "double-check" in result

    def test_same_dir_name_mismatch_only_cross_directory_absent(self):
        result = rename_suspicion("notes/apple.md", "notes/orange.md")
        assert "name mismatch" in result
        assert "cross-directory" not in result

    def test_stem_comparison_is_case_insensitive(self):
        # "NOTE" and "note" should match — no mismatch
        result = rename_suspicion("notes/NOTE.md", "notes/note-final.md")
        assert "name mismatch" not in result

    def test_cross_directory_same_name_only_cross_directory_flagged(self):
        # Same name, just moved — cross-directory fires, name mismatch does not
        result = rename_suspicion("drafts/chapter.md", "published/chapter.md")
        assert "cross-directory" in result
        assert "name mismatch" not in result


# ===========================================================================
# Deep path chains — performance and recursion safety
# ===========================================================================

class TestDeepPathChains:
    """
    Verify that no function in this module has a nasty surprise waiting at
    the bottom of an absurdly nested path. None of these should be doing
    anything recursive over path components, but if someone ever "improves"
    the implementation, these tests will catch the fallout before it does.

    50 levels of nesting is pathological enough to expose recursion limits
    or O(n²) behaviour without taking long enough to matter in CI.
    """

    _DEEP_PREFIX = "/".join(f"level{i:02d}" for i in range(50))
    _DEEP_OLD = f"{_DEEP_PREFIX}/old-subdir/note.md"
    _DEEP_NEW = f"{_DEEP_PREFIX}/new-subdir/note.md"

    def test_clean_filename_deep_path(self):
        deep = f"{self._DEEP_PREFIX}/subdir/my-note 1.md"
        assert clean_filename(deep) == "my-note.md"

    def test_detect_dir_renames_deep_path(self):
        renames = [(self._DEEP_OLD, self._DEEP_NEW)]
        result = detect_dir_renames(renames)
        old_parent = f"{self._DEEP_PREFIX}/old-subdir"
        new_parent = f"{self._DEEP_PREFIX}/new-subdir"
        assert result == {old_parent: new_parent}

    def test_reassign_deletions_deep_path(self):
        old_parent = f"{self._DEEP_PREFIX}/old-subdir"
        new_parent = f"{self._DEEP_PREFIX}/new-subdir"
        deleted = [self._DEEP_OLD]
        dir_renames = {old_parent: new_parent}
        true_del, dir_renamed = reassign_deletions(deleted, dir_renames)
        assert true_del == []
        assert dir_renamed == [(self._DEEP_OLD, self._DEEP_NEW)]

    def test_rename_suspicion_deep_path_clean_rename(self):
        # Same deep prefix, same filename — no flags should fire
        old = f"{self._DEEP_PREFIX}/subdir/chapter.md"
        new = f"{self._DEEP_PREFIX}/subdir/chapter-final.md"
        assert rename_suspicion(old, new) == ""

    def test_rename_suspicion_deep_path_cross_dir(self):
        result = rename_suspicion(self._DEEP_OLD, self._DEEP_NEW)
        assert "cross-directory" in result
        assert "name mismatch" not in result

    def test_infer_undetected_renames_deep_path(self):
        changes = _changes(D=[self._DEEP_OLD], A=[self._DEEP_NEW])
        result = infer_undetected_renames(changes)
        assert result == [(self._DEEP_OLD, self._DEEP_NEW)]

class TestRenameProcessingPipeline:
    """
    Run the whole dance: detect_dir_renames → reassign_deletions →
    process_renames_from_changes. Verifies the pieces compose correctly
    without needing git anywhere near the room.
    """

    def test_dir_rename_pipeline_end_to_end(self):
        """
        Scenario: git detected ONE file rename under drafts/ → published/,
        missing the second due to similarity threshold. The pipeline recovers
        the rest from the D/A pairs.
        """
        raw_changes = _changes(
            D=["drafts/chapter-02.md"],
            A=["published/chapter-02.md"],
            R=[("drafts/chapter-01.md", "published/chapter-01.md")],
        )

        dir_renames = detect_dir_renames(raw_changes["R"])
        assert dir_renames == {"drafts": "published"}

        true_deleted, dir_renamed = reassign_deletions(raw_changes["D"], dir_renames)
        assert true_deleted == []
        assert ("drafts/chapter-02.md", "published/chapter-02.md") in dir_renamed

        all_renames = raw_changes["R"] + dir_renamed
        lookup = process_renames_from_changes({"R": all_renames})

        assert lookup["published/chapter-01.md"] == "drafts/chapter-01.md"
        assert lookup["published/chapter-02.md"] == "drafts/chapter-02.md"

    def test_inferred_renames_compose_with_confirmed_renames(self):
        """
        Scenario: git confirmed some renames, we infer additional ones from
        unmatched D/A pairs. Final lookup should contain all of them without
        collision or dropped entries.
        """
        raw_changes = _changes(
            D=["old/lonely.md"],
            A=["new/lonely.md"],
            R=[("confirmed/from.md", "confirmed/to.md")],
        )

        inferred = infer_undetected_renames(raw_changes)
        assert inferred == [("old/lonely.md", "new/lonely.md")]

        all_renames = raw_changes["R"] + inferred
        lookup = process_renames_from_changes({"R": all_renames})

        assert lookup["confirmed/to.md"] == "confirmed/from.md"
        assert lookup["new/lonely.md"] == "old/lonely.md"
        assert len(lookup) == 2

    def test_true_deletions_survive_the_pipeline(self):
        """
        Not everything gets reassigned. Make sure genuine deletions come out
        the other side intact and don't get silently swallowed by the pipeline.
        """
        # moved.md is already in R — only nuclear-option.md is in D for reassignment
        raw_changes = _changes(
            D=["nuclear-option.md"],
            A=["new/moved.md"],
            R=[("old/moved.md", "new/moved.md")],
        )

        dir_renames = detect_dir_renames(raw_changes["R"])
        true_deleted, dir_renamed = reassign_deletions(raw_changes["D"], dir_renames)

        assert "nuclear-option.md" in true_deleted
        assert dir_renamed == []