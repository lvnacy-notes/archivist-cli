"""
tests/unit/test_changelog_helpers.py

Unit tests for the changelog helper functions in archivist.utils.changelog.

Pure-function tests where possible. No git. No disk I/O except where
we're explicitly prodding the filesystem helpers, and even then
tmp_path keeps the mess contained.

If these tests fail, the changelog pipeline is broken and your output
files are garbage. Congratulations.
"""

import uuid as _stdlib_uuid


from archivist.utils import (
    ARCHIVIST_AUTO_END,
    extract_descriptions,
    extract_user_content,
    format_file_list,
    generate_changelog_uuid,
)


# ===========================================================================
# extract_descriptions
# ===========================================================================

class TestExtractDescriptions:
    """
    extract_descriptions() parses an existing changelog and pulls out the
    filepath → description mappings for entries where the user has actually
    filled something in.

    Entries still showing [description] are worthless noise and get skipped.
    Sub-bullet entries come back as list[str]. Single-line entries as str.
    Everything else can go fuck itself.
    """

    def test_single_line_entry_with_description(self):
        content = "- `path/to/note.md`: the user actually wrote something here\n"
        result = extract_descriptions(content)
        assert result == {"path/to/note.md": "the user actually wrote something here"}

    def test_placeholder_entry_is_skipped(self):
        content = "- `path/to/note.md`: [description]\n"
        result = extract_descriptions(content)
        assert result == {}

    def test_sub_bullet_entry_returns_list(self):
        content = (
            "- `path/to/note.md`:\n"
            "  - did one thing\n"
            "  - did another thing\n"
        )
        result = extract_descriptions(content)
        assert result == {"path/to/note.md": ["did one thing", "did another thing"]}

    def test_mixed_content_only_filled_entries_returned(self):
        content = (
            "- `filled.md`: this one has content\n"
            "- `empty.md`: [description]\n"
            "- `also-filled.md`: this one too\n"
        )
        result = extract_descriptions(content)
        assert "filled.md" in result
        assert "also-filled.md" in result
        assert "empty.md" not in result
        assert result["filled.md"] == "this one has content"
        assert result["also-filled.md"] == "this one too"

    def test_sub_bullet_entry_preserves_all_bullets(self):
        content = (
            "- `multi.md`:\n"
            "  - first bullet\n"
            "  - second bullet\n"
            "  - third bullet\n"
        )
        result = extract_descriptions(content)
        assert result["multi.md"] == ["first bullet", "second bullet", "third bullet"]

    def test_entry_with_no_content_after_colon_and_no_sub_bullets_skipped(self):
        """Bare colon, nothing after, no sub-bullets — nothing to extract."""
        content = "- `bare.md`:\n"
        result = extract_descriptions(content)
        assert result == {}

    def test_non_entry_lines_are_ignored(self):
        """Headers, prose, blank lines — none of that shit should end up in the result."""
        content = (
            "## Some Section\n"
            "\n"
            "A paragraph of text that isn't a file entry.\n"
            "\n"
            "- `actual-entry.md`: a real description\n"
        )
        result = extract_descriptions(content)
        assert list(result.keys()) == ["actual-entry.md"]

    def test_deeply_nested_path_preserved_as_key(self):
        content = "- `some/deep/nested/path/to/file.md`: description lives here\n"
        result = extract_descriptions(content)
        assert "some/deep/nested/path/to/file.md" in result

    def test_multiple_entries_all_returned(self):
        content = (
            "- `alpha.md`: first\n"
            "- `beta.md`: second\n"
            "- `gamma.md`: third\n"
        )
        result = extract_descriptions(content)
        assert len(result) == 3
        assert result["alpha.md"] == "first"
        assert result["beta.md"] == "second"
        assert result["gamma.md"] == "third"

    def test_sub_bullet_stops_at_next_top_level_entry(self):
        """
        Sub-bullets only belong to their parent entry. The next `- ` line
        at the top level is not a sub-bullet — make sure parsing doesn't
        bleed into the next entry.
        """
        content = (
            "- `first.md`:\n"
            "  - bullet one\n"
            "  - bullet two\n"
            "- `second.md`: own description\n"
        )
        result = extract_descriptions(content)
        assert result["first.md"] == ["bullet one", "bullet two"]
        assert result["second.md"] == "own description"

    def test_empty_string_returns_empty_dict(self):
        assert extract_descriptions("") == {}

    def test_content_with_no_entries_returns_empty_dict(self):
        content = "# Changelog — 2024-06-01\n\n## Overview\n\nSome prose here.\n"
        assert extract_descriptions(content) == {}

    def test_description_with_colons_in_value_not_truncated(self):
        """
        A description like 'updated URL: https://example.com' contains a colon.
        The regex captures everything after the first ': ' — the value should
        not be cut off at the second colon.
        """
        content = "- `file.md`: updated URL: https://example.com\n"
        result = extract_descriptions(content)
        assert result["file.md"] == "updated URL: https://example.com"


# ===========================================================================
# extract_user_content
# ===========================================================================

class TestExtractUserContent:
    """
    extract_user_content() returns everything after the auto-end sentinel,
    or None if the sentinel is absent.

    None is the signal for "this file predates the sentinel system." The
    changelog runner uses it to decide whether to inject a default Notes block.
    Get this wrong and you either trash user content or inject duplicate Notes
    sections. Both outcomes are annoying as hell.
    """

    def test_returns_content_after_sentinel(self):
        content = f"auto-generated stuff\n\n{ARCHIVIST_AUTO_END}\n\n## Notes\n\nUser wrote this.\n"
        result = extract_user_content(content)
        assert result == "\n\n## Notes\n\nUser wrote this.\n"

    def test_returns_none_when_sentinel_absent(self):
        content = "Just a changelog with no sentinel. Probably old.\n"
        assert extract_user_content(content) is None

    def test_empty_user_section_after_sentinel_returns_empty_string(self):
        """Sentinel present but nothing after it — returns empty string, not None."""
        content = f"some generated content\n{ARCHIVIST_AUTO_END}"
        result = extract_user_content(content)
        assert result == ""
        assert result is not None

    def test_only_splits_on_first_occurrence(self):
        """
        If somehow the sentinel appears twice (cursed, but possible), we only
        split on the first one. Everything after the first sentinel is user territory.
        """
        content = f"generated\n{ARCHIVIST_AUTO_END}\nuser stuff\n{ARCHIVIST_AUTO_END}\nmore user stuff\n"
        result = extract_user_content(content)
        assert ARCHIVIST_AUTO_END in result
        assert result.startswith("\nuser stuff")

    def test_multiline_user_content_fully_preserved(self):
        user_section = "\n\n## Notes\n\nLine one.\nLine two.\nLine three.\n\n---\n\n*footer*\n"
        content = f"generated part\n{ARCHIVIST_AUTO_END}{user_section}"
        result = extract_user_content(content)
        assert result == user_section

    def test_sentinel_is_exact_string_match(self):
        """Slightly malformed sentinel (extra space, different case) → None."""
        not_quite = "<!-- archivist:auto-end  -->"
        content = f"stuff\n{not_quite}\nuser content\n"
        assert extract_user_content(content) is None

    def test_sentinel_constant_has_expected_value(self):
        """Pin the sentinel value. If someone changes it, this test screams."""
        assert ARCHIVIST_AUTO_END == "<!-- archivist:auto-end -->"


# ===========================================================================
# format_file_list
# ===========================================================================

class TestFormatFileList:
    """
    format_file_list() renders a list of files into changelog markdown with
    descriptions, move/rename annotations, and suspicious-rename warnings.

    Two distinct annotation verbs:
      "renamed from" — same-directory name change. Old filename only; the
                       directory is already visible from the new entry path.
      "moved from"   — file crossed directory boundaries (with or without a
                       simultaneous name change). Full old path shown, because
                       "moved from `note.md`" tells you precisely nothing when
                       there are forty files called note.md scattered about.

    This is the workhorse of every changelog body section. If it's broken,
    every single changelog output is wrong. Test it like it owes you money.
    """

    def test_empty_list_returns_fallback_line(self):
        result = format_file_list([], "No files modified", {})
        assert result == "- No files modified\n"

    def test_single_file_no_description_gets_placeholder(self):
        result = format_file_list(["notes/file.md"], "fallback", {})
        assert "- `notes/file.md`" in result
        assert "[description]" in result

    def test_single_file_with_string_description_renders_inline(self):
        descriptions = {"notes/file.md": "the user wrote this"}
        result = format_file_list(["notes/file.md"], "fallback", descriptions)
        assert "- `notes/file.md`" in result
        assert "the user wrote this" in result
        assert "[description]" not in result

    def test_single_file_with_list_description_renders_sub_bullets(self):
        descriptions = {"notes/file.md": ["did this", "also did that"]}
        result = format_file_list(["notes/file.md"], "fallback", descriptions)
        assert "- `notes/file.md`:" in result
        assert "  - did this" in result
        assert "  - also did that" in result

    def test_same_dir_rename_uses_renamed_from_verb(self):
        """
        Same directory, name changed — annotation says "renamed from `old-name.md`".
        The directory is identical to the new path's directory; no need to repeat it.
        """
        renames = {"notes/new-name.md": "notes/old-name.md"}
        result = format_file_list(["notes/new-name.md"], "fallback", {}, active_renames=renames)
        assert "renamed from" in result
        assert "moved from" not in result
        assert "old-name.md" in result

    def test_cross_dir_move_uses_moved_from_verb(self):
        """
        File jumped directories — annotation says "moved from `old/path/file.md`".
        "renamed from `file.md`" would be useless: the reader can't tell which of
        the forty files called file.md we're talking about without the full path.
        """
        renames = {"new/location/file.md": "old/location/file.md"}
        result = format_file_list(["new/location/file.md"], "fallback", {}, active_renames=renames)
        assert "moved from" in result
        assert "renamed from" not in result
        assert "old/location/file.md" in result

    def test_cross_dir_move_with_rename_uses_moved_from_verb(self):
        """
        File crossed directories AND changed name simultaneously — still "moved from",
        still shows the full old path. The verb is about topology, not the name.
        """
        renames = {"published/chapter-final.md": "drafts/chapter.md"}
        result = format_file_list(["published/chapter-final.md"], "fallback", {}, active_renames=renames)
        assert "moved from" in result
        assert "renamed from" not in result
        assert "drafts/chapter.md" in result

    def test_no_annotation_when_file_not_in_active_renames(self):
        result = format_file_list(["notes/file.md"], "fallback", {}, active_renames={})
        assert "renamed from" not in result
        assert "moved from" not in result

    def test_suspicious_rename_triggers_warning_emoji(self):
        """
        A cross-directory rename with unrelated stems should get the ⚠️ treatment.
        The user should know to double-check their shit.
        """
        renames = {"new/zeta.md": "old/alpha.md"}
        result = format_file_list(["new/zeta.md"], "fallback", {}, active_renames=renames)
        assert "⚠️" in result

    def test_clean_rename_does_not_trigger_warning(self):
        """Same directory, related stems — shut up and render cleanly."""
        renames = {"notes/my-note-v2.md": "notes/my-note.md"}
        result = format_file_list(["notes/my-note-v2.md"], "fallback", {}, active_renames=renames)
        assert "⚠️" not in result

    def test_multiple_files_all_rendered(self):
        files = ["a.md", "b.md", "c.md"]
        descriptions = {"a.md": "desc a", "b.md": "desc b", "c.md": "desc c"}
        result = format_file_list(files, "fallback", descriptions)
        assert "- `a.md`" in result
        assert "- `b.md`" in result
        assert "- `c.md`" in result
        assert "desc a" in result
        assert "desc b" in result
        assert "desc c" in result

    def test_mixed_descriptions_some_filled_some_placeholder(self):
        files = ["filled.md", "empty.md"]
        descriptions = {"filled.md": "this one has content"}
        result = format_file_list(files, "fallback", descriptions)
        assert "this one has content" in result
        assert "[description]" in result

    def test_default_active_renames_is_empty_dict(self):
        """Calling without active_renames should not blow up."""
        result = format_file_list(["file.md"], "fallback", {})
        assert "- `file.md`" in result

    def test_result_ends_with_newline(self):
        """Every section this renders into expects a trailing newline. Don't drop it."""
        result = format_file_list(["file.md"], "fallback", {})
        assert result.endswith("\n")

    def test_fallback_result_ends_with_newline(self):
        result = format_file_list([], "No files modified", {})
        assert result.endswith("\n")

    def test_list_description_block_followed_by_blank_line(self):
        """
        Sub-bullet blocks need a trailing blank line so the next entry
        doesn't smash into them in the rendered markdown.
        """
        descriptions = {"file.md": ["bullet one", "bullet two"]}
        result = format_file_list(["file.md"], "fallback", descriptions)
        lines = result.splitlines()
        # Find the last sub-bullet line and check there's a blank line after it
        last_bullet_idx = max(i for i, l in enumerate(lines) if l.strip().startswith("- ") and "file.md" not in l)
        assert lines[last_bullet_idx + 1] == ""

    def test_rename_shows_old_filename_not_old_full_path_when_same_dir(self):
        """
        Same-directory rename: the annotation should show just the filename,
        not the full path. Cross-directory renames show the full path.
        Nobody wants to read 'renamed from notes/old-name.md' when the
        directory hasn't changed.
        """
        renames = {"notes/new-name.md": "notes/old-name.md"}
        result = format_file_list(["notes/new-name.md"], "fallback", {}, active_renames=renames)
        # The old name's parent dir should NOT appear in the rename annotation
        assert "notes/old-name.md" not in result
        assert "old-name.md" in result

    def test_rename_shows_full_path_when_cross_directory(self):
        """
        Cross-directory rename: the full old path (relative to git root)
        should appear so the user knows where the thing came from.
        """
        renames = {"published/chapter.md": "drafts/chapter.md"}
        result = format_file_list(["published/chapter.md"], "fallback", {}, active_renames=renames)
        assert "drafts" in result


# ===========================================================================
# generate_changelog_uuid
# ===========================================================================

class TestGenerateChangelogUuid:
    """
    generate_changelog_uuid() hands out UUID4 strings. It should do exactly
    that and nothing else. This isn't rocket science, but if someone
    accidentally makes it return a constant or breaks the format, we want
    to know about it before it corrupts the archive DB.
    """

    def test_returns_a_string(self):
        result = generate_changelog_uuid()
        assert isinstance(result, str)

    def test_returned_string_is_valid_uuid4(self):
        result = generate_changelog_uuid()
        parsed = _stdlib_uuid.UUID(result, version=4)
        # UUID constructor accepts the string and version check passes iff it's UUID4
        assert parsed.version == 4

    def test_returned_string_has_correct_format(self):
        """
        UUID4 strings look like xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx.
        32 hex digits, 4 hyphens, version nibble is 4.
        """
        result = generate_changelog_uuid()
        parts = result.split("-")
        assert len(parts) == 5
        assert len(parts[0]) == 8
        assert len(parts[1]) == 4
        assert len(parts[2]) == 4
        assert parts[2][0] == "4"   # version nibble
        assert len(parts[3]) == 4
        assert len(parts[4]) == 12

    def test_two_consecutive_calls_return_different_values(self):
        """
        Not a guarantee — UUIDs are probabilistic. But if this fails,
        something is catastrophically wrong and you should panic accordingly.
        """
        first = generate_changelog_uuid()
        second = generate_changelog_uuid()
        assert first != second

    def test_returned_value_is_lowercase(self):
        """
        uuid.uuid4().__str__() returns lowercase hex. Pin this so nobody
        accidentally upcases it and breaks frontmatter comparisons downstream.
        """
        result = generate_changelog_uuid()
        assert result == result.lower()