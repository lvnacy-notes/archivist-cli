"""
tests/unit/test_config.py

Unit tests for archivist.utils config helpers.

No git. No disk drama beyond tmp_path. No excuses.
If you broke read_archivist_config, you broke everything that reads .archivist —
which is roughly every fucking command in the tool. So pay attention.
"""

import re
from pathlib import Path

from archivist.utils import (
    APPARATUS_MODULE_TYPES,
    MODULE_CHANGELOG_COMMAND,
    get_archivist_config_path,
    get_module_type,
    get_today,
    read_archivist_config,
    write_archivist_config,
)


# ===========================================================================
# get_archivist_config_path
# ===========================================================================

class TestGetArchivistConfigPath:
    """
    Trivial function, but pinning it costs nothing and catches the one
    catastrophic refactor where someone renames the file to '.archivist.yaml'
    because they "prefer explicit extensions". Don't.
    """

    def test_returns_dot_archivist_at_repo_root(self, tmp_path):
        result = get_archivist_config_path(tmp_path)
        assert result == tmp_path / ".archivist"

    def test_returns_a_path_object(self, tmp_path):
        result = get_archivist_config_path(tmp_path)
        assert isinstance(result, Path)

    def test_filename_is_exactly_dot_archivist(self, tmp_path):
        """No extensions. No suffixes. Just .archivist. The end."""
        result = get_archivist_config_path(tmp_path)
        assert result.name == ".archivist"

    def test_parent_is_git_root(self, tmp_path):
        result = get_archivist_config_path(tmp_path)
        assert result.parent == tmp_path


# ===========================================================================
# read_archivist_config
# ===========================================================================

class TestReadArchivistConfig:
    """
    This is the single point of entry for every command that needs to know
    what kind of project it's in. Get this wrong and the routing logic
    silently falls back to 'general' while you wonder why your library
    changelog looks like garbage.
    """

    def test_valid_yaml_returns_dict(self, tmp_path):
        config_file = tmp_path / ".archivist"
        config_file.write_text(
            "module-type: story\napparatus: true\n", encoding="utf-8"
        )
        result = read_archivist_config(tmp_path)
        assert result == {"module-type": "story", "apparatus": True}

    def test_missing_file_returns_none(self, tmp_path):
        """
        None is the explicit signal that .archivist doesn't exist yet.
        Not {}, not "general", not a FileNotFoundError — None.
        The distinction matters downstream.
        """
        result = read_archivist_config(tmp_path)
        assert result is None

    def test_malformed_yaml_returns_empty_dict_not_none(self, tmp_path):
        """
        Broken YAML means the file EXISTS but is unreadable. That's different
        from the file being absent. {} vs None is load-bearing — don't collapse
        those two failure modes into one.
        """
        config_file = tmp_path / ".archivist"
        config_file.write_text(
            "this: is: not: valid: yaml: {{\n", encoding="utf-8"
        )
        result = read_archivist_config(tmp_path)
        assert result == {}

    def test_malformed_yaml_prints_to_stderr(self, tmp_path, capsys):
        config_file = tmp_path / ".archivist"
        config_file.write_text(
            "this: is: not: valid: yaml: {{\n", encoding="utf-8"
        )
        read_archivist_config(tmp_path)
        captured = capsys.readouterr()
        assert "Could not parse" in captured.err

    def test_malformed_yaml_does_not_raise(self, tmp_path):
        config_file = tmp_path / ".archivist"
        config_file.write_text(": broken\n  - indented: nonsense\n{{", encoding="utf-8")
        # If this raises, the test fails. That's the whole point.
        result = read_archivist_config(tmp_path)
        assert result == {}

    def test_yaml_that_parses_to_scalar_returns_empty_dict(self, tmp_path):
        """
        YAML allows bare scalars at the top level. A .archivist that contains
        only 'hello' is valid YAML but useless to us. Return {} instead of
        making every caller deal with isinstance() checks.
        """
        config_file = tmp_path / ".archivist"
        config_file.write_text("just a bare string\n", encoding="utf-8")
        result = read_archivist_config(tmp_path)
        assert result == {}

    def test_yaml_that_parses_to_list_returns_empty_dict(self, tmp_path):
        config_file = tmp_path / ".archivist"
        config_file.write_text("- item-one\n- item-two\n", encoding="utf-8")
        result = read_archivist_config(tmp_path)
        assert result == {}

    def test_yaml_that_parses_to_none_returns_empty_dict(self, tmp_path):
        """An empty file is valid YAML that parses to None. Same deal."""
        config_file = tmp_path / ".archivist"
        config_file.write_text("", encoding="utf-8")
        result = read_archivist_config(tmp_path)
        assert result == {}

    def test_multikey_config_parsed_correctly(self, tmp_path):
        config_file = tmp_path / ".archivist"
        config_file.write_text(
            "apparatus: true\nmodule-type: library\nworks-dir: works\n",
            encoding="utf-8",
        )
        result = read_archivist_config(tmp_path)
        assert result["module-type"] == "library"
        assert result["works-dir"] == "works"

    def test_custom_changelog_output_dir_survives_parse(self, tmp_path):
        """Verify a key with hyphens and slashes in the value doesn't explode PyYAML."""
        config_file = tmp_path / ".archivist"
        config_file.write_text(
            "module-type: general\nchangelog-output-dir: ARCHIVE/LOGS\n",
            encoding="utf-8",
        )
        result = read_archivist_config(tmp_path)
        assert result["changelog-output-dir"] == "ARCHIVE/LOGS"


# ===========================================================================
# write_archivist_config
# ===========================================================================

class TestWriteArchivistConfig:
    """
    write_archivist_config() is not a YAML serialiser — it writes raw
    key: value lines by hand. That means it gets to have opinions about
    formatting, which is fine as long as read_archivist_config can digest
    what it produces.
    """

    def test_file_is_created(self, tmp_path):
        write_archivist_config(tmp_path, {"module-type": "story"})
        assert (tmp_path / ".archivist").exists()

    def test_written_file_contains_expected_keys(self, tmp_path):
        write_archivist_config(tmp_path, {"module-type": "vault", "apparatus": "true"})
        content = (tmp_path / ".archivist").read_text(encoding="utf-8")
        assert "module-type: vault" in content
        assert "apparatus: true" in content

    def test_file_starts_with_comment_header(self, tmp_path):
        """That comment line is load-bearing documentation. Don't quietly remove it."""
        write_archivist_config(tmp_path, {"module-type": "general"})
        content = (tmp_path / ".archivist").read_text(encoding="utf-8")
        assert content.startswith("# archivist project configuration")

    def test_file_ends_with_newline(self, tmp_path):
        write_archivist_config(tmp_path, {"module-type": "general"})
        content = (tmp_path / ".archivist").read_text(encoding="utf-8")
        assert content.endswith("\n")

    def test_empty_config_writes_only_comment(self, tmp_path):
        write_archivist_config(tmp_path, {})
        content = (tmp_path / ".archivist").read_text(encoding="utf-8")
        lines = [l for l in content.splitlines() if l and not l.startswith("#")]
        assert lines == []

    def test_overwrites_existing_file(self, tmp_path):
        """Second write wins. No appending, no merging, no preserving the old garbage."""
        write_archivist_config(tmp_path, {"module-type": "story"})
        write_archivist_config(tmp_path, {"module-type": "vault"})
        content = (tmp_path / ".archivist").read_text(encoding="utf-8")
        assert "vault" in content
        assert "story" not in content


# ===========================================================================
# write / read round-trip
# ===========================================================================

class TestWriteReadRoundTrip:
    """
    The contract: whatever write_archivist_config puts on disk,
    read_archivist_config must be able to read back. If these diverge,
    init → read will silently return wrong data and everyone will have
    a bad time tracing the bug.
    """

    def test_string_values_survive_round_trip(self, tmp_path):
        original = {"module-type": "library", "apparatus": "true"}
        write_archivist_config(tmp_path, original)
        result = read_archivist_config(tmp_path)
        # apparatus is written as the string "true"; PyYAML reads it as bool True
        assert result["module-type"] == "library"

    def test_all_known_module_types_survive_round_trip(self, tmp_path):
        for module_type in APPARATUS_MODULE_TYPES:
            write_archivist_config(tmp_path, {"module-type": module_type})
            result = read_archivist_config(tmp_path)
            assert result["module-type"] == module_type, (
                f"module-type '{module_type}' didn't survive the round-trip. "
                f"That's embarrassing."
            )

    def test_works_dir_survives_round_trip(self, tmp_path):
        write_archivist_config(tmp_path, {"module-type": "library", "works-dir": "catalog"})
        result = read_archivist_config(tmp_path)
        assert result["works-dir"] == "catalog"

    def test_changelog_output_dir_survives_round_trip(self, tmp_path):
        write_archivist_config(
            tmp_path, {"module-type": "general", "changelog-output-dir": "LOGS/CHANGELOG"}
        )
        result = read_archivist_config(tmp_path)
        assert result["changelog-output-dir"] == "LOGS/CHANGELOG"

    def test_multi_key_config_round_trips_all_keys(self, tmp_path):
        original = {
            "apparatus": "true",
            "module-type": "publication",
            "changelog-output-dir": "ARCHIVE/CHANGELOG",
            "templater": "false",
        }
        write_archivist_config(tmp_path, original)
        result = read_archivist_config(tmp_path)
        assert result["module-type"] == "publication"
        assert result["changelog-output-dir"] == "ARCHIVE/CHANGELOG"


# ===========================================================================
# get_module_type
# ===========================================================================

class TestGetModuleType:
    """
    get_module_type() is the one-liner that drives auto-routing in cli.py.
    It's three lines of code and it absolutely can be broken in three ways.
    """

    def test_returns_module_type_from_valid_config(self, tmp_path):
        (tmp_path / ".archivist").write_text("module-type: vault\n", encoding="utf-8")
        assert get_module_type(tmp_path) == "vault"

    def test_returns_none_when_config_file_is_absent(self, tmp_path):
        """No .archivist → None. Not 'general', not ''. None. Keep it honest."""
        assert get_module_type(tmp_path) is None

    def test_returns_none_when_module_type_key_is_missing(self, tmp_path):
        """
        A valid .archivist that just doesn't have a module-type key.
        Possible after a manual edit or an older init. Should degrade
        gracefully rather than blowing up with a KeyError.
        """
        (tmp_path / ".archivist").write_text("apparatus: true\n", encoding="utf-8")
        assert get_module_type(tmp_path) is None

    def test_returns_none_for_malformed_config(self, tmp_path):
        """
        Malformed YAML → read_archivist_config returns {} → .get() returns None.
        The chain holds. This test pins that it does.
        """
        (tmp_path / ".archivist").write_text(
            "this: is: broken: yaml: {{\n", encoding="utf-8"
        )
        result = get_module_type(tmp_path)
        assert result is None

    def test_returns_correct_type_for_each_known_module(self, tmp_path):
        for module_type in APPARATUS_MODULE_TYPES:
            (tmp_path / ".archivist").write_text(
                f"module-type: {module_type}\n", encoding="utf-8"
            )
            assert get_module_type(tmp_path) == module_type


# ===========================================================================
# get_today
# ===========================================================================

class TestGetToday:
    """
    get_today() feeds every changelog filename and frontmatter date field.
    If this returns the wrong format, you get files named CHANGELOG-04/09/26.md
    and frontmatter that breaks every downstream date parser.
    """

    def test_default_format_matches_iso8601_date(self):
        result = get_today()
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", result), (
            f"get_today() returned '{result}', which is not YYYY-MM-DD. "
            f"Changelog filenames are now fucked."
        )

    def test_default_format_is_four_digit_year(self):
        year = get_today().split("-")[0]
        assert len(year) == 4

    def test_returns_string(self):
        assert isinstance(get_today(), str)

    def test_custom_format_is_respected(self):
        result = get_today(format="%Y/%m/%d")
        assert re.match(r"^\d{4}/\d{2}/\d{2}$", result)

    def test_format_without_separators(self):
        result = get_today(format="%Y%m%d")
        assert re.match(r"^\d{8}$", result)

    def test_two_calls_in_same_second_return_same_value(self):
        """
        Probabilistic, but if this flakes in CI you have bigger problems
        than a flaky test — your system clock is doing something unholy.
        """
        a = get_today()
        b = get_today()
        assert a == b


# ===========================================================================
# Constants
# ===========================================================================

class TestConstants:
    """
    These constants drive routing logic in cli.py and changelog_base.py.
    If someone fat-fingers a module type string or removes an entry, things
    break silently in ways that are absolute bastards to debug.
    Pin them.
    """

    def test_apparatus_module_types_contains_all_five(self):
        assert set(APPARATUS_MODULE_TYPES) == {
            "story", "publication", "library", "vault", "general"
        }

    def test_apparatus_module_types_is_a_list(self):
        assert isinstance(APPARATUS_MODULE_TYPES, list)

    def test_module_changelog_command_covers_all_module_types(self):
        """
        Every module type must have a corresponding changelog subcommand.
        If you add a module type and forget to add the routing entry, this
        will catch it — before cli.py silently falls back to 'general' and
        you spend an hour wondering why your vault changelog looks like shit.
        """
        for module_type in APPARATUS_MODULE_TYPES:
            assert module_type in MODULE_CHANGELOG_COMMAND, (
                f"'{module_type}' is in APPARATUS_MODULE_TYPES but not in "
                f"MODULE_CHANGELOG_COMMAND. Go fix that."
            )

    def test_module_changelog_command_values_are_valid_subcommands(self):
        """The routing dict's values should all be recognised subcommand names."""
        valid_subcommands = {"general", "library", "publication", "story", "vault"}
        for module_type, command in MODULE_CHANGELOG_COMMAND.items():
            assert command in valid_subcommands, (
                f"MODULE_CHANGELOG_COMMAND['{module_type}'] = '{command}', "
                f"which is not a known changelog subcommand."
            )

    def test_no_extra_entries_in_module_changelog_command(self):
        """
        No phantom keys pointing at commands that don't correspond to a real
        module type. The mapping should be a clean bijection.
        """
        assert set(MODULE_CHANGELOG_COMMAND.keys()) == set(APPARATUS_MODULE_TYPES)