"""
tests/integration/test_frontmatter_commands.py

Integration tests for all four frontmatter subcommands: add, remove, rename,
and apply-template.

These tests run the actual run() functions against real files in a real git
repo. No mocking of subprocess. No fake git outputs. No bullshit.

They do NOT test CLI argument parsing — argparse has its own damn tests.
We call run(args) directly with a fake namespace from the `args` helper in
conftest.py, and we verify that the files on disk are what they should be.

monkeypatch.chdir(git_repo.path) is the magic that makes get_repo_root()
find the right repo without us having to mock half the stdlib. Keep it.
"""

from pathlib import Path
import argparse
import pytest

from archivist.commands.frontmatter.add import run as run_add
from archivist.commands.frontmatter.remove import run as run_remove
from archivist.commands.frontmatter.rename import run as run_rename
from archivist.commands.frontmatter.apply_template import run as run_apply_template
from archivist.commands.reclassify import run as run_reclassify
from archivist.utils import extract_frontmatter, has_frontmatter

pytestmark = pytest.mark.integration


# ===========================================================================
# Helpers — because typing the same frontmatter block 40 times is for people
# who hate themselves.
# ===========================================================================

def _note(*, fm: str = "", body: str = "Body text.") -> str:
    """Build a markdown file string. Pass raw YAML for fm, no delimiters needed."""
    if fm:
        return f"---\n{fm}\n---\n{body}"
    return body


def _read(path) -> str:
    return path.read_text(encoding="utf-8")


def _rc_args(**kwargs) -> argparse.Namespace:
    defaults = {
        "dry_run": False,
        "from_class": None,
        "to_class": None,
        "file": None,
        "path": None,
        "note_class": None,
        "class_property": "class",
        "tag": None,
    }
    return argparse.Namespace(**{**defaults, **kwargs})


# ===========================================================================
# frontmatter add
# ===========================================================================

class TestFrontmatterAdd:
    """
    run_add() should add a property to every .md file it can find.
    It should do exactly what it says on the tin and nothing more.
    """

    def test_adds_property_to_file_with_existing_frontmatter(
        self, git_repo, monkeypatch, args
    ):
        monkeypatch.chdir(git_repo.path)
        note = git_repo.path / "note.md"
        note.write_text(_note(fm="class: character\ntitle: Asha"), encoding="utf-8")

        run_add(args(property="status", value="draft"))

        fm = extract_frontmatter(_read(note))
        assert fm["status"] == "draft"
        # Pre-existing keys must survive unscathed
        assert fm["class"] == "character"
        assert fm["title"] == "Asha"

    def test_creates_frontmatter_block_when_none_exists(
        self, git_repo, monkeypatch, args
    ):
        monkeypatch.chdir(git_repo.path)
        bare = git_repo.path / "bare.md"
        bare.write_text("Just a bare-ass note with no frontmatter.", encoding="utf-8")

        run_add(args(property="status", value="draft"))

        content = _read(bare)
        assert has_frontmatter(content)
        assert extract_frontmatter(content)["status"] == "draft"

    def test_skips_file_that_already_has_property_without_overwrite(
        self, git_repo, monkeypatch, args
    ):
        monkeypatch.chdir(git_repo.path)
        note = git_repo.path / "note.md"
        note.write_text(_note(fm="status: published"), encoding="utf-8")
        original = _read(note)

        run_add(args(property="status", value="draft"))

        # File must be byte-for-byte identical — no sneaky rewrites
        assert _read(note) == original

    def test_overwrites_existing_property_when_flag_is_set(
        self, git_repo, monkeypatch, args
    ):
        monkeypatch.chdir(git_repo.path)
        note = git_repo.path / "note.md"
        note.write_text(_note(fm="status: published\ntitle: Fine"), encoding="utf-8")

        run_add(args(property="status", value="draft", overwrite=True))

        fm = extract_frontmatter(_read(note))
        assert fm["status"] == "draft"
        # Collateral damage check — title should be untouched
        assert fm["title"] == "Fine"

    def test_adds_bare_key_with_no_value(self, git_repo, monkeypatch, args):
        monkeypatch.chdir(git_repo.path)
        note = git_repo.path / "note.md"
        note.write_text(_note(fm="title: Asha"), encoding="utf-8")

        run_add(args(property="reviewed"))

        content = _read(note)
        assert "reviewed:" in content

    def test_dry_run_touches_absolutely_nothing(self, git_repo, monkeypatch, args):
        monkeypatch.chdir(git_repo.path)
        note = git_repo.path / "note.md"
        note.write_text(_note(fm="class: character"), encoding="utf-8")
        original = _read(note)

        run_add(args(property="status", value="draft", dry_run=True))

        assert _read(note) == original, (
            "dry_run=True and you still wrote to disk. "
            "That's not a dry run, that's just a run with a lying flag."
        )

    def test_processes_multiple_files_in_one_shot(self, git_repo, monkeypatch, args):
        monkeypatch.chdir(git_repo.path)
        notes = [git_repo.path / f"note{i}.md" for i in range(3)]
        for note in notes:
            note.write_text(_note(fm=f"title: Note {note.name}"), encoding="utf-8")

        run_add(args(property="status", value="draft"))

        for note in notes:
            assert extract_frontmatter(_read(note))["status"] == "draft"

    def test_skips_non_markdown_files_without_exploding(
        self, git_repo, monkeypatch, args
    ):
        monkeypatch.chdir(git_repo.path)
        # A .md file must exist or run_add exits before it ever reaches the .txt file —
        # "no .md files found" is a fast-exit, not a per-file skip.
        (git_repo.path / "note.md").write_text(_note(fm="class: thing"), encoding="utf-8")
        txt = git_repo.path / "notes.txt"
        txt.write_text("not a markdown file, leave me alone", encoding="utf-8")
        original = _read(txt)

        run_add(args(property="status", value="draft"))

        assert _read(txt) == original


# ===========================================================================
# frontmatter remove
# ===========================================================================

class TestFrontmatterRemove:
    """
    run_remove() strips a property from every .md file that has it.
    Leave everything else alone. That's the whole job.
    """

    def test_removes_property_that_exists(self, git_repo, monkeypatch, args):
        monkeypatch.chdir(git_repo.path)
        note = git_repo.path / "note.md"
        note.write_text(_note(fm="class: character\nstatus: draft\ntitle: Asha"), encoding="utf-8")

        run_remove(args(property="status"))

        fm = extract_frontmatter(_read(note))
        assert "status" not in fm
        assert fm["class"] == "character"
        assert fm["title"] == "Asha"

    def test_removes_last_property_and_drops_entire_frontmatter_block(
        self, git_repo, monkeypatch, args
    ):
        monkeypatch.chdir(git_repo.path)
        note = git_repo.path / "note.md"
        note.write_text(_note(fm="status: draft", body="Keep the body."), encoding="utf-8")

        run_remove(args(property="status"))

        content = _read(note)
        assert not has_frontmatter(content)
        assert "Keep the body." in content

    def test_skips_file_with_no_frontmatter_gracefully(
        self, git_repo, monkeypatch, args
    ):
        monkeypatch.chdir(git_repo.path)
        bare = git_repo.path / "bare.md"
        bare.write_text("No frontmatter here, officer.", encoding="utf-8")
        original = _read(bare)

        run_remove(args(property="status"))

        assert _read(bare) == original

    def test_skips_file_without_the_target_property(
        self, git_repo, monkeypatch, args
    ):
        monkeypatch.chdir(git_repo.path)
        note = git_repo.path / "note.md"
        note.write_text(_note(fm="class: character\ntitle: Asha"), encoding="utf-8")
        original = _read(note)

        run_remove(args(property="status"))  # status doesn't exist in this file

        assert _read(note) == original

    def test_removes_block_sequence_property_and_its_continuation_lines(
        self, git_repo, monkeypatch, args
    ):
        monkeypatch.chdir(git_repo.path)
        note = git_repo.path / "note.md"
        note.write_text(
            _note(fm="class: character\ntags:\n  - hero\n  - rogue\ntitle: Asha"),
            encoding="utf-8",
        )

        run_remove(args(property="tags"))

        content = _read(note)
        assert "tags" not in content
        assert "  - hero" not in content
        assert "  - rogue" not in content
        # Collateral damage check
        fm = extract_frontmatter(content)
        assert fm["class"] == "character"
        assert fm["title"] == "Asha"

    def test_dry_run_is_actually_dry(self, git_repo, monkeypatch, args):
        monkeypatch.chdir(git_repo.path)
        note = git_repo.path / "note.md"
        note.write_text(_note(fm="class: character\nstatus: draft"), encoding="utf-8")
        original = _read(note)

        run_remove(args(property="status", dry_run=True))

        assert _read(note) == original, (
            "dry_run=True is not a suggestion. Nothing should have changed on disk."
        )


# ===========================================================================
# frontmatter rename
# ===========================================================================

class TestFrontmatterRename:
    """
    run_rename() swaps a property key while preserving the value exactly.
    Scalar values, inline lists, block sequences — all of them, verbatim.
    Don't fuck with the values.
    """

    def test_renames_scalar_property(self, git_repo, monkeypatch, args):
        monkeypatch.chdir(git_repo.path)
        note = git_repo.path / "note.md"
        note.write_text(_note(fm="status: draft\ntitle: Asha"), encoding="utf-8")

        run_rename(args(property="status", new_name="state"))

        fm = extract_frontmatter(_read(note))
        assert "status" not in fm
        assert fm["state"] == "draft"
        assert fm["title"] == "Asha"

    def test_preserves_value_exactly(self, git_repo, monkeypatch, args):
        """
        The docstring is clear: values are preserved EXACTLY. No YAML round-trip,
        no coercion, no 'helpfully' turning strings into other things.
        """
        monkeypatch.chdir(git_repo.path)
        note = git_repo.path / "note.md"
        note.write_text(
            _note(fm="work-stage: active\ntitle: The Thing"),
            encoding="utf-8",
        )

        run_rename(args(property="work-stage", new_name="stage"))

        content = _read(note)
        assert "work-stage" not in content
        assert "stage: active" in content

    def test_preserves_block_sequence_value(self, git_repo, monkeypatch, args):
        monkeypatch.chdir(git_repo.path)
        note = git_repo.path / "note.md"
        note.write_text(
            _note(fm="tags:\n  - hero\n  - rogue\ntitle: Asha"),
            encoding="utf-8",
        )

        run_rename(args(property="tags", new_name="keywords"))

        content = _read(note)
        assert "tags:" not in content
        assert "keywords:" in content
        assert "  - hero" in content
        assert "  - rogue" in content

    def test_leaves_files_without_the_old_key_untouched(
        self, git_repo, monkeypatch, args
    ):
        monkeypatch.chdir(git_repo.path)
        note = git_repo.path / "note.md"
        note.write_text(_note(fm="class: character\ntitle: Asha"), encoding="utf-8")
        original = _read(note)

        run_rename(args(property="status", new_name="state"))  # 'status' not present

        assert _read(note) == original

    def test_does_not_rename_partial_key_matches(self, git_repo, monkeypatch, args):
        """
        Renaming 'class' must not touch 'classification'. The regex anchors on
        the full key. If this breaks, we'll be renaming things we shouldn't.
        """
        monkeypatch.chdir(git_repo.path)
        note = git_repo.path / "note.md"
        note.write_text(
            _note(fm="class: character\nclassification: top-secret"),
            encoding="utf-8",
        )

        run_rename(args(property="class", new_name="kind"))

        content = _read(note)
        assert "kind: character" in content
        assert "classification: top-secret" in content
        assert "class:" not in content

    def test_dry_run_changes_nothing(self, git_repo, monkeypatch, args):
        monkeypatch.chdir(git_repo.path)
        note = git_repo.path / "note.md"
        note.write_text(_note(fm="status: draft"), encoding="utf-8")
        original = _read(note)

        run_rename(args(property="status", new_name="state", dry_run=True))

        assert _read(note) == original

    def test_exits_when_old_and_new_names_are_identical(
        self, git_repo, monkeypatch, args
    ):
        monkeypatch.chdir(git_repo.path)
        note = git_repo.path / "note.md"
        note.write_text(_note(fm="status: draft"), encoding="utf-8")

        with pytest.raises(SystemExit):
            run_rename(args(property="status", new_name="status"))


# ===========================================================================
# frontmatter apply-template
# ===========================================================================

class TestFrontmatterApplyTemplate:
    """
    run_apply_template() is the most opinionated command in the suite.
    The template is the authority. The template is the law.

    These tests verify that the filter logic is correct (AND logic, all
    conditions must pass), that the merge is right (add missing, remove extra,
    reorder to match template), and that dry-run behaves itself.
    """

    def _make_template(self, path, fm: str) -> "Path":
        """Write a template .md file with the given frontmatter."""
        p = path / "template.md"
        p.write_text(f"---\n{fm}\n---\nTemplate body, irrelevant.\n", encoding="utf-8")
        return p

    def _apply_template_args(self, template_path, **kwargs):
        """Stamp out a namespace for apply-template with sane defaults."""
        import argparse
        return argparse.Namespace(
            dry_run=kwargs.get("dry_run", False),
            template=str(template_path),
            note_class=kwargs.get("note_class", None),
            class_property=kwargs.get("class_property", "class"),
            path=kwargs.get("path", None),
            tag=kwargs.get("tag", None),
        )

    def test_applies_template_to_note_matching_class_filter(
        self, git_repo, monkeypatch
    ):
        monkeypatch.chdir(git_repo.path)
        template = self._make_template(
            git_repo.path,
            fm="class: character\nname:\nstatus:\nalignment:",
        )
        note = git_repo.path / "hero.md"
        note.write_text(_note(fm="class: character\nname: Asha"), encoding="utf-8")

        run_apply_template(
            self._apply_template_args(template, note_class="character")
        )

        fm = extract_frontmatter(_read(note))
        # Template adds missing props
        assert "status" in fm
        assert "alignment" in fm
        # Existing value must be preserved, not wiped
        assert fm["name"] == "Asha"

    def test_does_not_touch_note_that_fails_class_filter(
        self, git_repo, monkeypatch
    ):
        monkeypatch.chdir(git_repo.path)
        template = self._make_template(
            git_repo.path,
            fm="class: character\nname:\nstatus:",
        )
        wrong_class = git_repo.path / "location.md"
        wrong_class.write_text(_note(fm="class: location\ntitle: The Keep"), encoding="utf-8")
        original = _read(wrong_class)

        run_apply_template(
            self._apply_template_args(template, note_class="character")
        )

        assert _read(wrong_class) == original

    def test_applies_template_to_note_matching_tag_filter(
        self, git_repo, monkeypatch
    ):
        monkeypatch.chdir(git_repo.path)
        template = self._make_template(
            git_repo.path,
            fm="class: article\ntitle:\nstatus:\npublished:",
        )
        tagged = git_repo.path / "tagged_note.md"
        tagged.write_text(
            _note(fm="class: article\ntitle: My Post\ntags: [draft, blog]"),
            encoding="utf-8",
        )
        untagged = git_repo.path / "untagged_note.md"
        untagged.write_text(_note(fm="class: article\ntitle: Other"), encoding="utf-8")
        untagged_original = _read(untagged)

        run_apply_template(self._apply_template_args(template, tag="draft"))

        tagged_fm = extract_frontmatter(_read(tagged))
        assert "published" in tagged_fm
        # Note that does NOT have the tag must be untouched
        assert _read(untagged) == untagged_original

    def test_applies_template_to_notes_under_path_filter(
        self, git_repo, monkeypatch
    ):
        monkeypatch.chdir(git_repo.path)
        template = self._make_template(
            git_repo.path,
            fm="class: note\ntitle:\nreviewed:",
        )
        in_scope_dir = git_repo.path / "content"
        in_scope_dir.mkdir()
        in_scope = in_scope_dir / "scoped.md"
        in_scope.write_text(_note(fm="class: note\ntitle: Scoped"), encoding="utf-8")

        out_of_scope = git_repo.path / "other.md"
        out_of_scope.write_text(_note(fm="class: note\ntitle: Out"), encoding="utf-8")
        out_original = _read(out_of_scope)

        run_apply_template(
            self._apply_template_args(template, path="content")
        )

        assert "reviewed" in extract_frontmatter(_read(in_scope))
        assert _read(out_of_scope) == out_original

    def test_and_logic_note_matching_all_filters_is_updated(
        self, git_repo, monkeypatch
    ):
        """
        Both class AND tag must match. A note with the right class but wrong tag
        should be left alone. A note with both should get the treatment.
        """
        monkeypatch.chdir(git_repo.path)
        template = self._make_template(
            git_repo.path,
            fm="class: character\nname:\nalignment:",
        )
        both_match = git_repo.path / "both.md"
        both_match.write_text(
            _note(fm="class: character\nname: Asha\ntags: [hero]"),
            encoding="utf-8",
        )
        class_only = git_repo.path / "class_only.md"
        class_only.write_text(
            _note(fm="class: character\nname: Villain\ntags: [villain]"),
            encoding="utf-8",
        )
        class_only_original = _read(class_only)

        run_apply_template(
            self._apply_template_args(template, note_class="character", tag="hero")
        )

        # All-filters match → updated
        assert "alignment" in extract_frontmatter(_read(both_match))
        # Class matches but tag doesn't → untouched
        assert _read(class_only) == class_only_original

    def test_template_removes_properties_not_in_template(
        self, git_repo, monkeypatch
    ):
        """
        The template is the authority. Properties the note has that the template
        doesn't are evicted without a second thought.
        """
        monkeypatch.chdir(git_repo.path)
        template = self._make_template(
            git_repo.path,
            fm="class: character\nname:",
        )
        note = git_repo.path / "note.md"
        note.write_text(
            _note(fm="class: character\nname: Asha\nlegacy_field: cruft\nold_junk: more_cruft"),
            encoding="utf-8",
        )

        run_apply_template(
            self._apply_template_args(template, note_class="character")
        )

        fm = extract_frontmatter(_read(note))
        assert "legacy_field" not in fm
        assert "old_junk" not in fm
        assert fm["class"] == "character"
        assert fm["name"] == "Asha"

    def test_template_reorders_properties_to_match_template_order(
        self, git_repo, monkeypatch
    ):
        """
        Order follows the template. The note's original order is irrelevant.
        """
        monkeypatch.chdir(git_repo.path)
        template = self._make_template(
            git_repo.path,
            fm="class: character\nalignment:\nname:\nstatus:",
        )
        note = git_repo.path / "note.md"
        # Write props in a different order from the template
        note.write_text(
            _note(fm="class: character\nstatus: active\nname: Asha\nalignment: neutral"),
            encoding="utf-8",
        )

        run_apply_template(
            self._apply_template_args(template, note_class="character")
        )

        content = _read(note)
        # Check that 'alignment' appears before 'name' in the output
        alignment_pos = content.index("alignment:")
        name_pos = content.index("name:")
        status_pos = content.index("status:")
        assert alignment_pos < name_pos < status_pos, (
            "Properties are out of template order. "
            "The template is the law and the law got ignored."
        )

    def test_dry_run_is_completely_inert(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        template = self._make_template(
            git_repo.path,
            fm="class: character\nname:\nalignment:",
        )
        note = git_repo.path / "note.md"
        note.write_text(_note(fm="class: character\nname: Asha"), encoding="utf-8")
        original = _read(note)

        run_apply_template(
            self._apply_template_args(template, note_class="character", dry_run=True)
        )

        assert _read(note) == original, (
            "dry_run=True and you still mutated a file. "
            "At this point you're just lying to the user."
        )

    def test_exits_when_no_filters_provided(self, git_repo, monkeypatch):
        """
        Running apply-template with no class, path, or tag specified is a
        vault-wide nuclear option. The command refuses to proceed. Good.
        """
        monkeypatch.chdir(git_repo.path)
        template = self._make_template(git_repo.path, fm="class: character\nname:")

        with pytest.raises(SystemExit):
            run_apply_template(
                self._apply_template_args(template)  # no filters at all
            )

    def test_exits_when_template_file_does_not_exist(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        import argparse
        nonexistent_template = str(git_repo.path / "ghost_template.md")

        with pytest.raises(SystemExit):
            run_apply_template(
                argparse.Namespace(
                    dry_run=False,
                    template=nonexistent_template,
                    note_class="character",
                    class_property="class",
                    path=None,
                    tag=None,
                )
            )

    def test_exits_when_template_has_no_frontmatter(self, git_repo, monkeypatch):
        monkeypatch.chdir(git_repo.path)
        template = git_repo.path / "bad_template.md"
        template.write_text("This template has no frontmatter. Tragic.", encoding="utf-8")

        with pytest.raises(SystemExit):
            run_apply_template(
                self._apply_template_args(template, note_class="character")
            )

    def test_note_not_matching_any_filter_is_never_touched(
        self, git_repo, monkeypatch
    ):
        """
        Belt and suspenders. A note with the wrong class AND no matching tag
        AND outside the path scope should be completely ignored.
        """
        monkeypatch.chdir(git_repo.path)
        template = self._make_template(
            git_repo.path,
            fm="class: character\nname:",
        )
        irrelevant = git_repo.path / "irrelevant.md"
        irrelevant.write_text(
            _note(fm="class: location\ntitle: The Dungeon"),
            encoding="utf-8",
        )
        original = _read(irrelevant)

        run_apply_template(
            self._apply_template_args(template, note_class="character")
        )

        assert _read(irrelevant) == original

    def test_preserves_existing_values_for_properties_in_template(
        self, git_repo, monkeypatch
    ):
        """
        The template provides defaults for missing properties. It does NOT
        clobber values the note already has. That would be monstrous.
        """
        monkeypatch.chdir(git_repo.path)
        template = self._make_template(
            git_repo.path,
            fm="class: character\nname: [default name]\nalignment: neutral",
        )
        note = git_repo.path / "note.md"
        note.write_text(
            _note(fm="class: character\nname: Asha\nalignment: chaotic evil"),
            encoding="utf-8",
        )

        run_apply_template(
            self._apply_template_args(template, note_class="character")
        )

        fm = extract_frontmatter(_read(note))
        assert fm["name"] == "Asha", "Existing value was overwritten. The template should not clobber."
        assert fm["alignment"] == "chaotic evil"


# ===========================================================================
# Ignore patterns (.archivist ignores)
# ===========================================================================

class TestIgnorePatterns:
    """
    Files matching patterns in .archivist `ignores` must be skipped by every
    frontmatter command. This is tested once here rather than repeated in every
    command class — the filtering happens in resolve_file_targets(), which all
    commands go through. If that breaks, it breaks everywhere at once and this
    class catches it.
    """

    def _write_archivist(
        self,
        root: Path,
        patterns: list[str]
    ) -> None:
        from archivist.utils import write_archivist_config
        write_archivist_config(root, {"module-type": "general", "ignores": patterns})

    def test_add_skips_ignored_file(
        self,
        git_repo,
        monkeypatch,
        args
    ):
        monkeypatch.chdir(git_repo.path)
        self._write_archivist(git_repo.path, ["ignored/**"])

        ignored_dir = git_repo.path / "ignored"
        ignored_dir.mkdir()
        ignored = ignored_dir / "note.md"
        ignored.write_text(_note(fm="class: character"), encoding="utf-8")
        original = _read(ignored)

        normal = git_repo.path / "normal.md"
        normal.write_text(_note(fm="class: character"), encoding="utf-8")

        run_add(args(property="status", value="draft"))

        assert _read(ignored) == original, (
            "Ignored file was modified by run_add. "
            "The ignore pattern did absolutely nothing."
        )
        assert extract_frontmatter(_read(normal))["status"] == "draft", (
            "Normal file was untouched. "
            "The ignore spec ate everything, not just the ignored path."
        )

    def test_remove_skips_ignored_file(self, git_repo, monkeypatch, args):
        monkeypatch.chdir(git_repo.path)
        self._write_archivist(git_repo.path, ["ignored/**"])

        ignored_dir = git_repo.path / "ignored"
        ignored_dir.mkdir()
        ignored = ignored_dir / "note.md"
        ignored.write_text(_note(fm="class: character\nstatus: draft"), encoding="utf-8")
        original = _read(ignored)

        normal = git_repo.path / "normal.md"
        normal.write_text(_note(fm="class: character\nstatus: draft"), encoding="utf-8")

        run_remove(args(property="status"))

        assert _read(ignored) == original, "Ignored file had a property removed. Ignore is broken."
        assert "status" not in extract_frontmatter(_read(normal))

    def test_ignored_file_passed_to_file_flag_causes_exit(
        self,
        git_repo,
        monkeypatch,
        args
    ):
        """
        Explicitly targeting an ignored file via --file is a misconfiguration.
        The command should exit rather than silently process or silently skip.
        """
        monkeypatch.chdir(git_repo.path)
        self._write_archivist(git_repo.path, ["ignored/**"])

        ignored_dir = git_repo.path / "ignored"
        ignored_dir.mkdir()
        ignored = ignored_dir / "note.md"
        ignored.write_text(_note(fm="class: character"), encoding="utf-8")

        with pytest.raises(SystemExit):
            run_add(args(property="status", value="draft", file=str(ignored)))

    def test_non_ignored_files_in_same_repo_are_unaffected(
        self,
        git_repo,
        monkeypatch,
        args
    ):
        """
        Paranoia check: the ignore spec should not bleed into files outside
        the ignored path. A misconfigured spec that matches too broadly would
        silently process nothing and look like a passing test everywhere else.
        """
        monkeypatch.chdir(git_repo.path)
        self._write_archivist(git_repo.path, ["scratch/**"])

        note = git_repo.path / "notes" / "real.md"
        note.parent.mkdir()
        note.write_text(_note(fm="class: character"), encoding="utf-8")

        run_add(args(property="status", value="draft"))

        assert extract_frontmatter(_read(note))["status"] == "draft", (
            "File outside the ignored path wasn't processed. "
            "The ignore spec is too broad, or resolve_file_targets broke something."
        )

    def test_reclassify_skips_ignored_file(
        self,
        git_repo,
        monkeypatch
    ):
        monkeypatch.chdir(git_repo.path)
        self._write_archivist(git_repo.path, ["ignored/**"])

        ignored_dir = git_repo.path / "ignored"
        ignored_dir.mkdir()
        ignored = ignored_dir / "note.md"
        ignored.write_text(_note(fm="class: article"), encoding="utf-8")
        original = _read(ignored)

        normal = git_repo.path / "normal.md"
        normal.write_text(_note(fm="class: article"), encoding="utf-8")

        run_reclassify(_rc_args(from_class="article", to_class="column"))

        assert _read(ignored) == original, (
            "Ignored file was reclassified. "
            "It went through resolve_file_targets and it shouldn't have."
        )
        assert extract_frontmatter(_read(normal))["class"] == "column", (
            "Normal file wasn't reclassified. "
            "The ignore spec ate everything, not just the ignored path."
        )