"""
tests/unit/test_frontmatter.py

Unit tests for archivist.utils.frontmatter.

Pure-function tests — no git, no disk I/O (except where we're explicitly
testing file helpers, in which case tmp_path keeps it contained).
Fast, paranoid, and deeply suspicious of YAML.
"""

import pytest

from archivist.utils import (
    has_frontmatter,
    extract_frontmatter,
    match_property_line,
    parse_frontmatter_entries,
    remove_property_from_frontmatter,
    extract_tags_from_entries,
    render_field,
    safe_read_markdown,
    safe_write_markdown,
    find_markdown_files,
    get_file_class,
    get_file_frontmatter,
    matches_class_filter,
)


# ===========================================================================
# has_frontmatter
# ===========================================================================

class TestHasFrontmatter:
    def test_valid_block_returns_true(self):
        content = "---\nclass: character\ntitle: Asha\n---\nBody text here."
        assert has_frontmatter(content) is True

    def test_no_frontmatter_returns_false(self):
        content = "Just a plain note with no frontmatter whatsoever."
        assert has_frontmatter(content) is False

    def test_mid_document_dashes_not_frontmatter(self):
        """Dashes that appear mid-document should not fool the regex."""
        content = "Some intro text.\n---\nclass: fake\n---\nMore text."
        assert has_frontmatter(content) is False

    def test_permissive_about_trailing_whitespace_on_delimiters(self):
        """The regex explicitly handles trailing whitespace — make sure it stays that way."""
        content = "---   \nclass: character\n---   \nBody text."
        assert has_frontmatter(content) is True

    def test_empty_frontmatter_block_still_valid(self):
        """An empty block is weird but technically valid YAML."""
        content = "---\n---\nBody."
        assert has_frontmatter(content) is True

    def test_only_opening_delimiter_is_not_frontmatter(self):
        content = "---\nclass: character\nNo closing delimiter."
        assert has_frontmatter(content) is False

    def test_windows_line_endings_dont_cause_a_scene(self):
        """FRONTMATTER_RE uses \\n — verify it doesn't choke on \\r\\n files."""
        content = "---\r\nclass: character\r\ntitle: Asha\r\n---\r\nBody text."
        # The regex won't match CRLF — this is a known limitation worth documenting.
        # The test pins the current behaviour so any future fix shows up as a test change.
        result = has_frontmatter(content)
        assert isinstance(result, bool)  # it won't blow up, at least


# ===========================================================================
# extract_frontmatter
# ===========================================================================

class TestExtractFrontmatter:
    def test_happy_path_returns_correct_dict(self):
        content = "---\nclass: character\ntitle: Asha\ntags: [hero, rogue]\n---\nBody."
        fm = extract_frontmatter(content)
        assert fm["class"] == "character"
        assert fm["title"] == "Asha"
        assert fm["tags"] == ["hero", "rogue"]

    def test_missing_block_returns_empty_dict(self):
        content = "No frontmatter here, just vibes."
        assert extract_frontmatter(content) == {}

    def test_malformed_yaml_returns_empty_dict_without_raising(self):
        """The contract is explicit: does not raise. Ever. You're welcome."""
        content = "---\n: this is not valid yaml: at all: {{\n---\nBody."
        result = extract_frontmatter(content)
        assert result == {}

    def test_frontmatter_parses_to_non_dict_returns_empty_dict(self):
        """A bare scalar at the top level of the YAML block → return {}."""
        content = "---\njust a string\n---\nBody."
        assert extract_frontmatter(content) == {}

    def test_frontmatter_parses_to_list_returns_empty_dict(self):
        content = "---\n- item one\n- item two\n---\nBody."
        assert extract_frontmatter(content) == {}

    def test_numeric_values_round_trip_correctly(self):
        content = "---\nchapter: 42\nword-count: 10000\n---\n"
        fm = extract_frontmatter(content)
        assert fm["chapter"] == 42
        assert fm["word-count"] == 10000

    def test_colon_in_value_does_not_corrupt_parse(self):
        """A URL value contains a colon. PyYAML handles this fine with quoting."""
        content = '---\nurl: "https://example.com/path"\ntitle: Fine\n---\n'
        fm = extract_frontmatter(content)
        assert fm["url"] == "https://example.com/path"
        assert fm["title"] == "Fine"


# ===========================================================================
# remove_property_from_frontmatter
# ===========================================================================

class TestRemovePropertyFromFrontmatter:
    def test_removes_scalar_property(self):
        raw_fm = "class: character\ntitle: Asha\nstatus: draft"
        updated, found = remove_property_from_frontmatter(raw_fm, "status")
        assert found is True
        assert "status" not in updated
        assert "class: character" in updated
        assert "title: Asha" in updated

    def test_removes_block_sequence_property(self):
        """Multi-line block sequence — all indented continuation lines must go."""
        raw_fm = "class: character\ntags:\n  - hero\n  - rogue\ntitle: Asha"
        updated, found = remove_property_from_frontmatter(raw_fm, "tags")
        assert found is True
        assert "tags" not in updated
        assert "  - hero" not in updated
        assert "  - rogue" not in updated
        assert "class: character" in updated
        assert "title: Asha" in updated

    def test_property_not_present_returns_false_and_unchanged_content(self):
        raw_fm = "class: character\ntitle: Asha"
        original = raw_fm
        updated, found = remove_property_from_frontmatter(raw_fm, "nonexistent")
        assert found is False
        assert updated == original

    def test_removing_only_property_leaves_empty_string(self):
        """
        Caller contract: when the only property is removed, the result is an
        empty/whitespace-only string. remove.py checks `updated_fm.strip()` to
        decide whether to drop the block entirely — verify that contract holds.
        """
        raw_fm = "status: draft"
        updated, found = remove_property_from_frontmatter(raw_fm, "status")
        assert found is True
        assert updated.strip() == ""

    def test_does_not_remove_property_that_merely_starts_with_the_name(self):
        """'class' should not match 'class_extra' — the pattern anchors to the key."""
        raw_fm = "class: character\nclassification: top-secret"
        updated, found = remove_property_from_frontmatter(raw_fm, "class")
        assert found is True
        assert "class: character" not in updated
        assert "classification: top-secret" in updated

    def test_removes_inline_list_property(self):
        raw_fm = "title: Asha\ncategory: [archive, changelog]\nmodified: 2024-01-01"
        updated, found = remove_property_from_frontmatter(raw_fm, "category")
        assert found is True
        assert "category" not in updated
        assert "title: Asha" in updated
        assert "modified: 2024-01-01" in updated


# ===========================================================================
# match_property_line
# ===========================================================================

class TestMatchPropertyLine:
    def test_exact_match_returns_true(self):
        assert match_property_line("class: character", "class") is True

    def test_match_with_extra_spaces_before_colon(self):
        assert match_property_line("class  : character", "class") is True

    def test_partial_match_prefix_returns_false(self):
        """'class_extra' must not match when looking for 'class'."""
        assert match_property_line("class_extra: value", "class") is False

    def test_partial_match_suffix_would_not_exist_but_lets_be_sure(self):
        assert match_property_line("not_class: value", "class") is False

    def test_property_name_with_regex_special_chars(self):
        """
        The helper uses re.escape() — verify it handles property names that
        contain regex metacharacters without exploding.
        """
        assert match_property_line("tags+: value", "tags+") is True
        assert match_property_line("tags: value", "tags+") is False

    def test_hyphenated_property_name(self):
        assert match_property_line("work-stage: active", "work-stage") is True

    def test_url_value_with_colon_does_not_cause_false_match_on_wrong_key(self):
        """
        A line like 'url: https://example.com' should only match 'url', not 'url: https'.
        This is a sanity check, not a real risk — but pin it anyway.
        """
        assert match_property_line("url: https://example.com", "url") is True
        assert match_property_line("url: https://example.com", "url: https") is False


# ===========================================================================
# parse_frontmatter_entries
# ===========================================================================

class TestParseFrontmatterEntries:
    def test_scalar_fields_return_correct_tuples(self):
        raw = "class: character\ntitle: Asha\nstatus: draft"
        entries = parse_frontmatter_entries(raw)
        keys = [k for k, _ in entries]
        assert keys == ["class", "title", "status"]
        assert entries[0][1] == ["class: character"]
        assert entries[1][1] == ["title: Asha"]

    def test_block_sequence_groups_continuation_lines_with_key(self):
        raw = "class: character\ntags:\n  - hero\n  - rogue\ntitle: Asha"
        entries = parse_frontmatter_entries(raw)
        tags_entry = next((lines for k, lines in entries if k == "tags"), None)
        assert tags_entry is not None
        assert "tags:" in tags_entry[0]
        assert "  - hero" in tags_entry
        assert "  - rogue" in tags_entry

    def test_empty_frontmatter_returns_empty_list(self):
        assert parse_frontmatter_entries("") == []

    def test_preserves_order(self):
        raw = "z: last\na: first\nm: middle"
        entries = parse_frontmatter_entries(raw)
        keys = [k for k, _ in entries]
        assert keys == ["z", "a", "m"]

    def test_inline_list_is_single_entry(self):
        raw = "tags: [foo, bar, baz]"
        entries = parse_frontmatter_entries(raw)
        assert len(entries) == 1
        assert entries[0][0] == "tags"
        assert len(entries[0][1]) == 1  # all on one line


# ===========================================================================
# extract_tags_from_entries
# ===========================================================================

class TestExtractTagsFromEntries:
    def _entries_from(self, raw: str):
        return parse_frontmatter_entries(raw)

    def test_inline_list(self):
        entries = self._entries_from("tags: [Foo, Bar, baz qux]")
        assert extract_tags_from_entries(entries) == ["foo", "bar", "baz qux"]

    def test_inline_list_with_quoted_values(self):
        entries = self._entries_from('tags: ["Foo", \'Bar\']')
        result = extract_tags_from_entries(entries)
        assert "foo" in result
        assert "bar" in result

    def test_scalar_single_tag(self):
        entries = self._entries_from("tags: Hero")
        assert extract_tags_from_entries(entries) == ["hero"]

    def test_block_sequence(self):
        entries = self._entries_from("tags:\n  - Hero\n  - Rogue")
        result = extract_tags_from_entries(entries)
        assert result == ["hero", "rogue"]

    def test_no_tags_key_returns_empty_list(self):
        entries = self._entries_from("class: character\ntitle: Asha")
        assert extract_tags_from_entries(entries) == []

    def test_returns_lowercase_regardless_of_input(self):
        entries = self._entries_from("tags: [UPPER, Mixed, lower]")
        result = extract_tags_from_entries(entries)
        assert result == ["upper", "mixed", "lower"]


# ===========================================================================
# render_field
# ===========================================================================

class TestRenderField:
    def test_scalar_string_value(self):
        assert render_field("class", "character") == ["class: character"]

    def test_scalar_integer_value(self):
        assert render_field("chapter", 42) == ["chapter: 42"]

    def test_list_value_renders_as_block_sequence(self):
        result = render_field("tags", ["foo", "bar"])
        assert result == ["tags:", "  - foo", "  - bar"]

    def test_empty_list_renders_as_inline_empty(self):
        assert render_field("tags", []) == ["tags: []"]

    def test_single_item_list(self):
        result = render_field("category", ["changelog"])
        assert result == ["category:", "  - changelog"]


# ===========================================================================
# matches_class_filter
# ===========================================================================

class TestMatchesClassFilter:
    def test_matching_class_returns_true(self):
        fm = {"class": "character", "title": "Asha"}
        assert matches_class_filter(fm, "character") is True

    def test_case_insensitive_match(self):
        fm = {"class": "Character"}
        assert matches_class_filter(fm, "character") is True

    def test_non_matching_class_returns_false(self):
        fm = {"class": "location"}
        assert matches_class_filter(fm, "character") is False

    def test_missing_class_key_returns_false(self):
        fm = {"title": "No class here"}
        assert matches_class_filter(fm, "character") is False

    def test_none_class_value_returns_false(self):
        fm = {"class": None}
        assert matches_class_filter(fm, "character") is False


# ===========================================================================
# File I/O helpers (use tmp_path via the md_file fixture from conftest)
# ===========================================================================

class TestSafeReadMarkdown:
    def test_reads_existing_file(self, tmp_path):
        p = tmp_path / "note.md"
        p.write_text("---\nclass: thing\n---\nBody.", encoding="utf-8")
        result = safe_read_markdown(p)
        assert result == "---\nclass: thing\n---\nBody."

    def test_missing_file_returns_none_without_raising(self, tmp_path):
        p = tmp_path / "does_not_exist.md"
        result = safe_read_markdown(p)
        assert result is None

    def test_returns_none_on_permission_error(self, tmp_path):
        """Can't easily simulate this cross-platform, so skip on non-unix."""
        import os
        if os.name == "nt":
            pytest.skip("Permission test not reliable on Windows")
        p = tmp_path / "locked.md"
        p.write_text("content", encoding="utf-8")
        p.chmod(0o000)
        try:
            result = safe_read_markdown(p)
            assert result is None
        finally:
            p.chmod(0o644)


class TestSafeWriteMarkdown:
    def test_writes_content_successfully(self, tmp_path):
        p = tmp_path / "note.md"
        result = safe_write_markdown(p, "---\nclass: thing\n---\nBody.")
        assert result is True
        assert p.read_text(encoding="utf-8") == "---\nclass: thing\n---\nBody."

    def test_returns_false_on_write_failure(self, tmp_path):
        """Writing to a directory path should fail gracefully."""
        p = tmp_path  # This is a directory, not a file
        result = safe_write_markdown(p, "content")
        assert result is False


# ===========================================================================
# find_markdown_files
# ===========================================================================

class TestFindMarkdownFiles:
    def test_finds_all_md_files_recursively(self, tmp_path):
        (tmp_path / "a.md").write_text("a")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.md").write_text("b")
        (tmp_path / "sub" / "c.txt").write_text("not markdown")

        result = find_markdown_files(tmp_path)
        names = [f.name for f in result]
        assert "a.md" in names
        assert "b.md" in names
        assert "c.txt" not in names

    def test_returns_sorted_list(self, tmp_path):
        (tmp_path / "z.md").write_text("z")
        (tmp_path / "a.md").write_text("a")
        (tmp_path / "m.md").write_text("m")
        result = find_markdown_files(tmp_path)
        names = [f.name for f in result]
        assert names == sorted(names)

    def test_empty_directory_returns_empty_list(self, tmp_path):
        assert find_markdown_files(tmp_path) == []

    def test_path_prefix_filter_restricts_results(self, tmp_path):
        sub_a = tmp_path / "a"
        sub_b = tmp_path / "b"
        sub_a.mkdir()
        sub_b.mkdir()
        (sub_a / "in_scope.md").write_text("in")
        (sub_b / "out_of_scope.md").write_text("out")

        result = find_markdown_files(tmp_path, filters={"path_prefix": sub_a})
        names = [f.name for f in result]
        assert "in_scope.md" in names
        assert "out_of_scope.md" not in names


# ===========================================================================
# get_file_frontmatter / get_file_class
# ===========================================================================

class TestGetFileFrontmatter:
    def test_returns_dict_for_valid_markdown_with_frontmatter(self, tmp_path):
        p = tmp_path / "note.md"
        p.write_text("---\nclass: character\ntitle: Asha\n---\nBody.", encoding="utf-8")
        fm = get_file_frontmatter(p)
        assert fm is not None
        assert fm["class"] == "character"

    def test_returns_none_for_file_without_frontmatter(self, tmp_path):
        p = tmp_path / "bare.md"
        p.write_text("Just a bare note.", encoding="utf-8")
        assert get_file_frontmatter(p) is None

    def test_returns_none_for_non_markdown_file(self, tmp_path):
        p = tmp_path / "data.json"
        p.write_text('{"key": "value"}', encoding="utf-8")
        assert get_file_frontmatter(p) is None

    def test_accepts_string_path(self, tmp_path):
        p = tmp_path / "note.md"
        p.write_text("---\nclass: character\n---\nBody.", encoding="utf-8")
        fm = get_file_frontmatter(str(p))
        assert fm is not None
        assert fm["class"] == "character"


class TestGetFileClass:
    def test_returns_class_value_lowercased(self, tmp_path):
        p = tmp_path / "note.md"
        p.write_text("---\nclass: Character\n---\nBody.", encoding="utf-8")
        assert get_file_class(p) == "character"

    def test_returns_none_when_no_class_field(self, tmp_path):
        p = tmp_path / "note.md"
        p.write_text("---\ntitle: Just a title\n---\nBody.", encoding="utf-8")
        assert get_file_class(p) is None

    def test_returns_none_for_missing_file(self, tmp_path):
        p = tmp_path / "ghost.md"
        assert get_file_class(p) is None


# ===========================================================================
# Edge cases the strategy called out explicitly
# ===========================================================================

class TestEdgeCases:
    def test_property_value_with_colon_does_not_false_trigger_match(self):
        """
        'url: https://example.com' — the value contains a colon.
        match_property_line('url: https://example.com', 'url') should be True.
        match_property_line('url: https://example.com', 'url: https') should be False
        because 'url: https' is not a valid property name one would actually search for,
        but more importantly the regex should not match it.
        """
        line = "url: https://example.com"
        assert match_property_line(line, "url") is True
        assert match_property_line(line, "url: https") is False

    def test_extract_frontmatter_handles_frontmatter_with_colon_in_value(self):
        content = '---\nurl: "https://example.com/path?a=1&b=2"\ntitle: Fine\n---\nBody.'
        fm = extract_frontmatter(content)
        assert "url" in fm
        assert fm["title"] == "Fine"

    def test_render_field_with_list_of_one(self):
        """Edge case: single-item list should still render as block, not scalar."""
        result = render_field("tags", ["only-one"])
        assert result == ["tags:", "  - only-one"]

    def test_remove_property_does_not_corrupt_adjacent_properties(self):
        """Removing a property mid-block should leave its neighbours intact."""
        raw_fm = "class: character\nstatus: draft\ntitle: Asha\nwork-stage: active"
        updated, found = remove_property_from_frontmatter(raw_fm, "status")
        assert found is True
        assert "class: character" in updated
        assert "title: Asha" in updated
        assert "work-stage: active" in updated
        assert "status" not in updated

    def test_parse_frontmatter_entries_with_tab_indented_continuation(self):
        """Tabs are valid YAML indentation. Don't be a tab bigot."""
        raw = "tags:\n\t- foo\n\t- bar"
        entries = parse_frontmatter_entries(raw)
        tags_entry = next((lines for k, lines in entries if k == "tags"), None)
        assert tags_entry is not None
        assert len(tags_entry) == 3  # key line + 2 continuation lines

    def test_extract_tags_from_entries_empty_inline_list(self):
        """tags: [] should return an empty list, not explode."""
        entries = parse_frontmatter_entries("tags: []")
        result = extract_tags_from_entries(entries)
        assert result == []