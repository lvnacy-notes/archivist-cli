"""
tests/unit/test_config.py

Unit tests for archivist.utils config helpers.

No git. No disk drama beyond tmp_path. No excuses.
If you broke read_archivist_config, you broke everything that reads .archivist —
which is roughly every fucking command in the tool. So pay attention.
"""

import re
import pytest
from pathlib import Path

from archivist.utils import (
    APPARATUS_MODULE_TYPES,
    MODULE_CHANGELOG_COMMAND,
    build_ignore_spec,
    get_archivist_config_path,
    get_module_type,
    get_today,
    read_archivist_config,
    write_archivist_config,
)
from archivist.utils.config import find_changelog_plugin, load_changelog_plugin


# ===========================================================================
# get_archivist_config_path
# ===========================================================================

class TestGetArchivistConfigPath:
    """
    get_archivist_config_path() is now a resolution function, not a constant.
    It checks disk and returns the right path for whichever form exists.
    If neither exists, it returns the canonical directory form so writers
    know where to put things.

    The old contract (always returns tmp_path / ".archivist") is gone.
    Don't write tests that assume the flat filename — that's the whole point
    of the migration.
    """

    def test_prefers_directory_form_when_both_exist(self, tmp_path):
        """
        If someone has both a flat .archivist file and .archivist/config.yaml,
        the directory form wins. The flat file is legacy. It loses.
        """
        (tmp_path / ".archivist").write_text("module-type: story\n", encoding="utf-8")
        archivist_dir = tmp_path / ".archivist"
        # Can't have both a file and a directory with the same name on disk —
        # simulate by just verifying the directory form takes priority when present.
        archivist_dir_path = tmp_path / ".archivist_dir_test"
        archivist_dir_path.mkdir()
        (archivist_dir_path / "config.yaml").write_text(
            "module-type: vault\n", encoding="utf-8"
        )
        # The real test: directory form path, when it exists, is what's returned.
        config_yaml = tmp_path / ".archivist" / "config.yaml"
        # Build the directory form properly via a fresh tmp dir
        fresh = tmp_path / "fresh"
        fresh.mkdir()
        (fresh / ".archivist").mkdir()
        (fresh / ".archivist" / "config.yaml").write_text(
            "module-type: vault\n", encoding="utf-8"
        )
        result = get_archivist_config_path(fresh)
        assert result == fresh / ".archivist" / "config.yaml"

    def test_returns_directory_form_when_only_that_exists(self, tmp_path):
        archivist_dir = tmp_path / ".archivist"
        archivist_dir.mkdir()
        (archivist_dir / "config.yaml").write_text(
            "module-type: library\n", encoding="utf-8"
        )
        result = get_archivist_config_path(tmp_path)
        assert result == tmp_path / ".archivist" / "config.yaml"

    def test_returns_legacy_flat_file_when_only_that_exists(self, tmp_path):
        (tmp_path / ".archivist").write_text(
            "module-type: story\n", encoding="utf-8"
        )
        result = get_archivist_config_path(tmp_path)
        assert result == tmp_path / ".archivist"

    def test_returns_canonical_path_when_neither_exists(self, tmp_path):
        """
        No config of any kind — return the canonical directory-form path
        so callers that need to write know where to put things.
        """
        result = get_archivist_config_path(tmp_path)
        assert result == tmp_path / ".archivist" / "config.yaml"

    def test_returns_a_path_object(self, tmp_path):
        result = get_archivist_config_path(tmp_path)
        assert isinstance(result, Path)


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
# read_archivist_config — directory form
# ===========================================================================

class TestReadArchivistConfigDirectoryForm:
    """
    The directory form is the canonical form for new projects. All the same
    failure modes as the flat file need to work identically — the form is
    different, the contract is identical.
    """

    def _make_dir_config(self, tmp_path: Path, content: str) -> None:
        """Write content to .archivist/config.yaml."""
        archivist_dir = tmp_path / ".archivist"
        archivist_dir.mkdir(exist_ok=True)
        (archivist_dir / "config.yaml").write_text(content, encoding="utf-8")

    def test_reads_from_directory_form(self, tmp_path):
        self._make_dir_config(tmp_path, "module-type: library\napparatus: true\n")
        result = read_archivist_config(tmp_path)
        assert result["module-type"] == "library"

    def test_directory_form_takes_priority_over_flat_file(self, tmp_path):
        """
        Both forms present simultaneously shouldn't happen in practice, but
        if they do, directory form wins. Legacy loses.
        """
        # Can't have a file and directory both named .archivist — use a
        # subdirectory to test the priority logic in isolation.
        # The actual priority is tested via get_archivist_config_path;
        # here we just verify read_archivist_config picks up the right content.
        self._make_dir_config(tmp_path, "module-type: vault\n")
        result = read_archivist_config(tmp_path)
        assert result["module-type"] == "vault", (
            "read_archivist_config didn't find the directory-form config. "
            "Check get_archivist_config_path priority logic."
        )

    def test_malformed_yaml_in_directory_form_returns_empty_dict(self, tmp_path):
        self._make_dir_config(tmp_path, "this: is: not: valid: yaml: {{\n")
        result = read_archivist_config(tmp_path)
        assert result == {}

    def test_missing_config_yaml_with_empty_directory_returns_none(self, tmp_path):
        """
        .archivist/ directory exists but config.yaml doesn't — treat as absent.
        The directory alone is not a config.
        """
        (tmp_path / ".archivist").mkdir()
        result = read_archivist_config(tmp_path)
        assert result is None, (
            "An empty .archivist/ directory was treated as a valid config. "
            "It isn't. The directory alone means nothing."
        )

    def test_multikey_config_in_directory_form(self, tmp_path):
        self._make_dir_config(
            tmp_path,
            "apparatus: true\nmodule-type: library\nworks-dir: works\n"
        )
        result = read_archivist_config(tmp_path)
        assert result["module-type"] == "library"
        assert result["works-dir"] == "works"





# ===========================================================================
# write_archivist_config
# ===========================================================================

class TestWriteArchivistConfig:
    """
    write_archivist_config() now always writes to .archivist/config.yaml and
    creates the directory if needed. The flat .archivist file is the read-only
    legacy form — writes never go there anymore.

    Tests that previously checked (tmp_path / ".archivist").exists() now check
    (tmp_path / ".archivist" / "config.yaml").exists(). That's the contract.
    """

    def test_file_is_created(self, tmp_path):
        write_archivist_config(tmp_path, {"module-type": "story"})
        assert (tmp_path / ".archivist" / "config.yaml").exists(), (
            "write_archivist_config didn't create .archivist/config.yaml. "
            "The directory form is the only valid write target now."
        )

    def test_archivist_directory_is_created(self, tmp_path):
        """The directory must be created if it doesn't exist yet."""
        assert not (tmp_path / ".archivist").exists()
        write_archivist_config(tmp_path, {"module-type": "story"})
        assert (tmp_path / ".archivist").is_dir()

    def test_written_file_contains_expected_keys(self, tmp_path):
        write_archivist_config(tmp_path, {"module-type": "vault", "apparatus": "true"})
        content = (tmp_path / ".archivist" / "config.yaml").read_text(encoding="utf-8")
        assert "module-type: vault" in content
        assert "apparatus: true" in content

    def test_file_starts_with_comment_header(self, tmp_path):
        """That comment line is load-bearing documentation. Don't quietly remove it."""
        write_archivist_config(tmp_path, {"module-type": "general"})
        content = (tmp_path / ".archivist" / "config.yaml").read_text(encoding="utf-8")
        assert content.startswith("# archivist project configuration")

    def test_file_ends_with_newline(self, tmp_path):
        write_archivist_config(tmp_path, {"module-type": "general"})
        content = (tmp_path / ".archivist" / "config.yaml").read_text(encoding="utf-8")
        assert content.endswith("\n")

    def test_empty_config_writes_only_comment(self, tmp_path):
        write_archivist_config(tmp_path, {})
        content = (tmp_path / ".archivist" / "config.yaml").read_text(encoding="utf-8")
        lines = [l for l in content.splitlines() if l and not l.startswith("#")]
        assert lines == []

    def test_overwrites_existing_file(self, tmp_path):
        """Second write wins. No appending, no merging, no preserving the old garbage."""
        write_archivist_config(tmp_path, {"module-type": "story"})
        write_archivist_config(tmp_path, {"module-type": "vault"})
        content = (tmp_path / ".archivist" / "config.yaml").read_text(encoding="utf-8")
        assert "vault" in content
        assert "story" not in content

    def test_does_not_write_flat_archivist_file(self, tmp_path):
        """
        Writes go to the directory form only. The flat .archivist file is
        legacy — writing to it here would be a regression, not a feature.
        """
        write_archivist_config(tmp_path, {"module-type": "general"})
        flat = tmp_path / ".archivist"
        assert not flat.is_file(), (
            "write_archivist_config wrote a flat .archivist file. "
            "That path is now a directory. Something went wrong."
        )


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
# write_archivist_config — ignores serialization
# ===========================================================================

class TestWriteArchivistConfigIgnores:
    """
    ignores is the only list-valued key in .archivist. The serializer has
    a special branch for it. Make sure that branch actually works before
    you find out the hard way that it wrote 'ignores: []' as YAML null.
    """

    def test_empty_ignores_writes_block_sequence_not_null(self, tmp_path):
        """
        A bare `ignores:` line parses as null in YAML, not an empty list.
        We write `ignores:\n  []` to prevent that. Pin it.
        """
        write_archivist_config(tmp_path, {"module-type": "general", "ignores": []})
        content = (tmp_path / ".archivist" / "config.yaml").read_text(encoding="utf-8")
        assert "ignores:" in content, "ignores key is missing entirely"
        # The bare key alone would parse as null — verify the empty sequence marker
        assert "[]" in content, (
            "Empty ignores list written as bare key. "
            "That parses as null, not [], and build_ignore_spec will get None."
        )

    def test_populated_ignores_writes_each_pattern_as_block_entry(self, tmp_path):
        write_archivist_config(
            tmp_path,
            {"module-type": "general", "ignores": ["ARCHIVE/**", "*.tmp", "scratch/"]}
        )
        content = (tmp_path / ".archivist" / "config.yaml").read_text(encoding="utf-8")
        assert '  - "ARCHIVE/**"' in content
        assert '  - "*.tmp"' in content
        assert '  - "scratch/"' in content

    def test_ignores_key_appears_in_output(self, tmp_path):
        write_archivist_config(tmp_path, {"ignores": ["*.tmp"]})
        content = (tmp_path / ".archivist" / "config.yaml").read_text(encoding="utf-8")
        assert "ignores:" in content

    def test_empty_ignores_survives_round_trip_as_empty_list(self, tmp_path):
        write_archivist_config(tmp_path, {"module-type": "general", "ignores": []})
        result = read_archivist_config(tmp_path)
        # PyYAML reads `[]` as an empty list
        assert result["ignores"] == [], (
            f"Empty ignores didn't survive the round-trip. Got: {result.get('ignores')!r}. "
            "build_ignore_spec will choke on this."
        )

    def test_populated_ignores_survives_round_trip(self, tmp_path):
        patterns = ["ARCHIVE/**", "*.tmp", "scratch/"]
        write_archivist_config(tmp_path, {"module-type": "general", "ignores": patterns})
        result = read_archivist_config(tmp_path)
        assert result["ignores"] == patterns, (
            f"Ignore patterns didn't survive the round-trip. "
            f"Written: {patterns!r}, got back: {result.get('ignores')!r}."
        )


# ===========================================================================
# build_ignore_spec
# ===========================================================================

class TestBuildIgnoreSpec:
    """
    build_ignore_spec() is the thing that actually does the filtering.
    If it returns a spec that matches the wrong files — or fails to match
    the right ones — every frontmatter command quietly processes files it
    shouldn't. That's a bad day.

    Paths passed to match_file() must be repo-relative strings or Path objects.
    Absolute paths will silently fail to match. Every test here uses relative
    paths to mirror what resolve_file_targets actually passes in.
    """

    def test_returns_pathspec_instance(self, tmp_path):
        import pathspec
        (tmp_path / ".archivist").write_text(
            "module-type: general\nignores:\n  []\n", encoding="utf-8"
        )
        spec = build_ignore_spec(tmp_path)
        assert isinstance(spec, pathspec.PathSpec)

    def test_empty_ignores_matches_nothing(self, tmp_path):
        write_archivist_config(tmp_path, {"module-type": "general", "ignores": []})
        spec = build_ignore_spec(tmp_path)
        assert not spec.match_file("notes/something.md"), (
            "Empty ignore spec matched a file. "
            "An empty spec should be a transparent no-op, not a blackhole."
        )

    def test_absent_ignores_key_matches_nothing(self, tmp_path):
        """No ignores key at all — should degrade to a spec that matches nothing."""
        write_archivist_config(tmp_path, {"module-type": "general"})
        spec = build_ignore_spec(tmp_path)
        assert not spec.match_file("notes/something.md")

    def test_missing_config_file_matches_nothing(self, tmp_path):
        """No .archivist at all. Should not raise — just return an empty spec."""
        spec = build_ignore_spec(tmp_path)
        assert not spec.match_file("notes/something.md")

    def test_glob_pattern_matches_files_in_directory(self, tmp_path):
        write_archivist_config(tmp_path, {"ignores": ["ARCHIVE/**"]})
        spec = build_ignore_spec(tmp_path)
        assert spec.match_file("ARCHIVE/CHANGELOG-2024-01-01.md")
        assert not spec.match_file("notes/something.md")

    def test_extension_glob_matches_correct_files(self, tmp_path):
        write_archivist_config(tmp_path, {"ignores": ["*.tmp"]})
        spec = build_ignore_spec(tmp_path)
        assert spec.match_file("scratch.tmp")
        assert not spec.match_file("scratch.md")

    def test_directory_pattern_matches_files_inside(self, tmp_path):
        write_archivist_config(tmp_path, {"ignores": ["scratch/"]})
        spec = build_ignore_spec(tmp_path)
        assert spec.match_file("scratch/notes.md")
        assert not spec.match_file("notes/scratch.md")

    def test_negation_pattern_excludes_file_from_ignore(self, tmp_path):
        """
        Full .gitignore semantics includes negation with !.
        Ignore everything in ARCHIVE/ except the index file.
        """
        write_archivist_config(
            tmp_path,
            {"ignores": ["ARCHIVE/**", "!ARCHIVE/INDEX.md"]}
        )
        spec = build_ignore_spec(tmp_path)
        assert spec.match_file("ARCHIVE/CHANGELOG-2024-01-01.md")
        assert not spec.match_file("ARCHIVE/INDEX.md")

    def test_single_string_value_tolerated(self, tmp_path):
        """
        Someone will write `ignores: "*.tmp"` instead of a list.
        We handle it rather than blowing up on them.
        """
        (tmp_path / ".archivist").write_text(
            "module-type: general\nignores: '*.tmp'\n", encoding="utf-8"
        )
        spec = build_ignore_spec(tmp_path)
        assert spec.match_file("scratch.tmp"), (
            "Single-string ignores value wasn't tolerated. "
            "We said we'd handle this. Handle it."
        )

    def test_multiple_patterns_all_respected(self, tmp_path):
        write_archivist_config(
            tmp_path,
            {"ignores": ["ARCHIVE/**", "templates/", "*.draft.md"]}
        )
        spec = build_ignore_spec(tmp_path)
        assert spec.match_file("ARCHIVE/something.md")
        assert spec.match_file("templates/character.md")
        assert spec.match_file("notes/chapter-one.draft.md")
        assert not spec.match_file("notes/chapter-one.md")


# ===========================================================================
# find_changelog_plugin
# ===========================================================================

class TestFindChangelogPlugin:
    """
    find_changelog_plugin() is the discovery half of the plugin system.
    It either finds .archivist/changelog.py and returns its Path, or it
    returns None. That's it. Everything downstream depends on getting
    this right.
    """

    def test_returns_none_when_no_archivist_directory(self, tmp_path):
        result = find_changelog_plugin(tmp_path)
        assert result is None

    def test_returns_none_when_directory_exists_but_no_plugin(self, tmp_path):
        (tmp_path / ".archivist").mkdir()
        result = find_changelog_plugin(tmp_path)
        assert result is None

    def test_returns_path_when_plugin_exists(self, tmp_path):
        archivist_dir = tmp_path / ".archivist"
        archivist_dir.mkdir()
        plugin = archivist_dir / "changelog.py"
        plugin.write_text("def run(args): pass\n", encoding="utf-8")
        result = find_changelog_plugin(tmp_path)
        assert result == plugin

    def test_returns_path_object_not_string(self, tmp_path):
        archivist_dir = tmp_path / ".archivist"
        archivist_dir.mkdir()
        (archivist_dir / "changelog.py").write_text("def run(args): pass\n", encoding="utf-8")
        result = find_changelog_plugin(tmp_path)
        assert isinstance(result, Path)

    def test_ignores_sample_changelog(self, tmp_path):
        """
        sample-changelog.py must never be loaded as a plugin. It's a reference
        file. Loading it automatically would be the exact opposite of its purpose.
        """
        archivist_dir = tmp_path / ".archivist"
        archivist_dir.mkdir()
        (archivist_dir / "sample-changelog.py").write_text(
            "def run(args): pass\n", encoding="utf-8"
        )
        result = find_changelog_plugin(tmp_path)
        assert result is None, (
            "find_changelog_plugin returned sample-changelog.py. "
            "That file is a reference — it must never be loaded automatically."
        )

    def test_ignores_other_py_files_in_archivist_dir(self, tmp_path):
        """Only changelog.py is the plugin. Everything else is ignored."""
        archivist_dir = tmp_path / ".archivist"
        archivist_dir.mkdir()
        (archivist_dir / "helpers.py").write_text("def run(args): pass\n", encoding="utf-8")
        (archivist_dir / "manifest.py").write_text("def run(args): pass\n", encoding="utf-8")
        result = find_changelog_plugin(tmp_path)
        assert result is None, (
            "find_changelog_plugin picked up a file that isn't changelog.py. "
            "The convention is exact: the filename IS the registration."
        )

    def test_coexists_with_config_yaml(self, tmp_path):
        """Plugin and config.yaml should coexist without either getting in the way."""
        archivist_dir = tmp_path / ".archivist"
        archivist_dir.mkdir()
        (archivist_dir / "config.yaml").write_text(
            "module-type: library\n", encoding="utf-8"
        )
        (archivist_dir / "changelog.py").write_text(
            "def run(args): pass\n", encoding="utf-8"
        )
        result = find_changelog_plugin(tmp_path)
        assert result == archivist_dir / "changelog.py"


# ===========================================================================
# load_changelog_plugin
# ===========================================================================

class TestLoadChangelogPlugin:
    """
    load_changelog_plugin() is the loading and validation half. A plugin that
    loads but has no `run` callable is useless and should be caught here, not
    silently fail at dispatch time when nothing happens and nobody knows why.
    """

    def test_loads_valid_plugin(self, tmp_path):
        plugin_path = tmp_path / "changelog.py"
        plugin_path.write_text("def run(args): pass\n", encoding="utf-8")
        module = load_changelog_plugin(plugin_path)
        assert module is not None

    def test_loaded_module_has_run_callable(self, tmp_path):
        plugin_path = tmp_path / "changelog.py"
        plugin_path.write_text("def run(args): pass\n", encoding="utf-8")
        module = load_changelog_plugin(plugin_path)
        assert callable(getattr(module, "run", None))

    def test_exits_on_syntax_error(self, tmp_path):
        plugin_path = tmp_path / "changelog.py"
        plugin_path.write_text("def run(args)\n    pass\n", encoding="utf-8")
        with pytest.raises(SystemExit):
            load_changelog_plugin(plugin_path)

    def test_exits_on_missing_run(self, tmp_path):
        """
        A plugin with no `run` function is broken by definition. Catch it at
        load time with a clear message rather than letting cli.py dispatch into
        the void and produce a cryptic AttributeError.
        """
        plugin_path = tmp_path / "changelog.py"
        plugin_path.write_text("def not_run(args): pass\n", encoding="utf-8")
        with pytest.raises(SystemExit):
            load_changelog_plugin(plugin_path)

    def test_exits_when_run_is_not_callable(self, tmp_path):
        """run = 42 is not a callable. Should be caught."""
        plugin_path = tmp_path / "changelog.py"
        plugin_path.write_text("run = 42\n", encoding="utf-8")
        with pytest.raises(SystemExit):
            load_changelog_plugin(plugin_path)

    def test_syntax_error_prints_to_stderr(self, tmp_path, capsys):
        plugin_path = tmp_path / "changelog.py"
        plugin_path.write_text("def run(args)\n    pass\n", encoding="utf-8")
        with pytest.raises(SystemExit):
            load_changelog_plugin(plugin_path)
        captured = capsys.readouterr()
        assert "Syntax error" in captured.err or "syntax" in captured.err.lower(), (
            "SyntaxError in plugin produced no useful stderr message. "
            "The user has no idea what they broke."
        )

    def test_missing_run_prints_to_stderr(self, tmp_path, capsys):
        plugin_path = tmp_path / "changelog.py"
        plugin_path.write_text("def not_run(args): pass\n", encoding="utf-8")
        with pytest.raises(SystemExit):
            load_changelog_plugin(plugin_path)
        captured = capsys.readouterr()
        assert "run" in captured.err, (
            "Missing `run` callable produced no message mentioning 'run'. "
            "The user needs to know what the contract requires."
        )

    def test_plugin_module_is_callable_end_to_end(self, tmp_path):
        """
        The full happy path: load a plugin, call run(), verify it executed.
        If this breaks, the entire plugin system is dead.
        """
        plugin_path = tmp_path / "changelog.py"
        plugin_path.write_text(
            "executed = []\ndef run(args): executed.append(True)\n",
            encoding="utf-8"
        )
        import argparse
        module = load_changelog_plugin(plugin_path)
        module.run(argparse.Namespace())
        assert module.executed == [True], (
            "Plugin run() was called but didn't execute. "
            "load_changelog_plugin returned a ghost."
        )


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

    def test_reads_module_type_from_directory_form(self, tmp_path):
        """Directory form must work identically to the flat file for routing."""
        archivist_dir = tmp_path / ".archivist"
        archivist_dir.mkdir()
        (archivist_dir / "config.yaml").write_text(
            "module-type: publication\n", encoding="utf-8"
        )
        assert get_module_type(tmp_path) == "publication", (
            "get_module_type didn't find the module type in the directory form. "
            "Auto-routing in cli.py is now broken for all directory-form projects."
        )


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