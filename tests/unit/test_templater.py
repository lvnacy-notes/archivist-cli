"""
tests/unit/test_templater.py

Unit tests for archivist.utils.templater.

Pure-function tests. No git, no disk I/O except where we're explicitly
testing the TemplaterContext's file-based namespace (_TpFile), in which
case tmp_path keeps it contained.

The mask/restore cycle, the expression evaluator, and the moment.js
format translator are the three things most likely to eat someone's
frontmatter in production. Test them like they owe you money.
"""

import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from archivist.utils import (
    TemplaterMode,
    TemplaterContext,
    extract_expressions,
    get_templater_mode,
    has_templater_expression,
    mask_templater_expressions,
    moment_to_strftime,
    resolve_value,
    restore_templater_expressions,
)


# ===========================================================================
# TemplaterMode
# ===========================================================================

class TestTemplaterMode:
    def test_from_config_resolve(self):
        assert TemplaterMode.from_config("resolve") is TemplaterMode.RESOLVE

    def test_from_config_preserve(self):
        assert TemplaterMode.from_config("preserve") is TemplaterMode.PRESERVE

    def test_from_config_false_string(self):
        assert TemplaterMode.from_config("false") is TemplaterMode.DISABLED

    def test_from_config_none_defaults_to_preserve(self):
        assert TemplaterMode.from_config(None) is TemplaterMode.PRESERVE

    def test_from_config_unrecognized_value_defaults_to_preserve(self):
        """
        Unknown config value → PRESERVE, not DISABLED.
        Silently treating unknown as disabled would be a nasty surprise —
        someone typos 'presrve' and suddenly their expressions get mangled.
        """
        assert TemplaterMode.from_config("garbage") is TemplaterMode.PRESERVE

    def test_from_config_strips_whitespace(self):
        assert TemplaterMode.from_config("  resolve  ") is TemplaterMode.RESOLVE

    def test_from_config_case_insensitive(self):
        assert TemplaterMode.from_config("RESOLVE") is TemplaterMode.RESOLVE
        assert TemplaterMode.from_config("Preserve") is TemplaterMode.PRESERVE

    def test_enum_values_match_config_strings(self):
        """The stored .value must match what get written to .archivist."""
        assert TemplaterMode.RESOLVE.value == "resolve"
        assert TemplaterMode.PRESERVE.value == "preserve"
        assert TemplaterMode.DISABLED.value == "false"

    def test_get_templater_mode_reads_from_config_dict(self):
        config = {"templater": "resolve", "module-type": "vault"}
        assert get_templater_mode(config) is TemplaterMode.RESOLVE

    def test_get_templater_mode_none_config_returns_preserve(self):
        assert get_templater_mode(None) is TemplaterMode.PRESERVE

    def test_get_templater_mode_missing_key_returns_preserve(self):
        assert get_templater_mode({"module-type": "general"}) is TemplaterMode.PRESERVE


# ===========================================================================
# Detection helpers
# ===========================================================================

class TestHasTemplaterExpression:
    def test_basic_expression_detected(self):
        assert has_templater_expression("<% tp.date.now() %>") is True

    def test_plain_string_not_detected(self):
        assert has_templater_expression("2024-01-01") is False

    def test_partial_opening_tag_not_detected(self):
        assert has_templater_expression("<% unclosed") is False

    def test_expression_embedded_in_value(self):
        assert has_templater_expression("created: <% tp.date.now('YYYY-MM-DD') %>") is True

    def test_whitespace_control_variant_detected(self):
        assert has_templater_expression("<%- tp.file.title %>") is True

    def test_empty_string_not_detected(self):
        assert has_templater_expression("") is False

    def test_multiple_expressions_detected(self):
        value = "<% tp.date.now() %> and <% tp.file.title %>"
        assert has_templater_expression(value) is True


class TestExtractExpressions:
    def test_extracts_single_expression_content(self):
        result = extract_expressions("<% tp.date.now('YYYY-MM-DD') %>")
        assert result == ["tp.date.now('YYYY-MM-DD')"]

    def test_extracts_multiple_expressions(self):
        value = "<% tp.date.now() %> — <% tp.file.title %>"
        result = extract_expressions(value)
        assert len(result) == 2
        assert "tp.date.now()" in result
        assert "tp.file.title" in result

    def test_returns_empty_list_for_no_expressions(self):
        assert extract_expressions("plain string") == []

    def test_strips_inner_whitespace(self):
        result = extract_expressions("<%  tp.file.title  %>")
        assert result == ["tp.file.title"]


# ===========================================================================
# Mask / restore cycle
# ===========================================================================

class TestMaskTemplaterExpressions:
    def test_replaces_expression_with_sentinel(self):
        raw_fm = "created: <% tp.date.now('YYYY-MM-DD') %>"
        masked, mask_map = mask_templater_expressions(raw_fm)
        assert "<%" not in masked
        assert "%>" not in masked
        assert "__ARCHIVIST_TMPL_0__" in masked

    def test_mask_map_contains_original_expression(self):
        expr = "<% tp.date.now('YYYY-MM-DD') %>"
        raw_fm = f"created: {expr}"
        _, mask_map = mask_templater_expressions(raw_fm)
        assert "__ARCHIVIST_TMPL_0__" in mask_map
        assert mask_map["__ARCHIVIST_TMPL_0__"] == expr

    def test_multiple_expressions_get_distinct_sentinels(self):
        raw_fm = "created: <% tp.date.now() %>\nmodified: <% tp.date.now() %>"
        masked, mask_map = mask_templater_expressions(raw_fm)
        assert "__ARCHIVIST_TMPL_0__" in masked
        assert "__ARCHIVIST_TMPL_1__" in masked
        assert len(mask_map) == 2

    def test_sentinels_are_numbered_in_order(self):
        raw_fm = "a: <% first %>\nb: <% second %>\nc: <% third %>"
        _, mask_map = mask_templater_expressions(raw_fm)
        assert set(mask_map.keys()) == {
            "__ARCHIVIST_TMPL_0__",
            "__ARCHIVIST_TMPL_1__",
            "__ARCHIVIST_TMPL_2__",
        }

    def test_no_expressions_returns_unchanged_string_and_empty_map(self):
        raw_fm = "title: Asha\nclass: character"
        masked, mask_map = mask_templater_expressions(raw_fm)
        assert masked == raw_fm
        assert mask_map == {}

    def test_yaml_hostile_characters_in_expression_are_safely_masked(self):
        """
        Expressions with {, }, :, #, and other YAML special characters must
        be replaced entirely. If they survive into the masked string, downstream
        YAML parsing will have a very bad time.
        """
        raw_fm = "meta: <% { key: 'value', other: true } %>"
        masked, mask_map = mask_templater_expressions(raw_fm)
        assert "{" not in masked
        assert "}" not in masked
        assert "__ARCHIVIST_TMPL_0__" in masked

    def test_whitespace_control_variants_are_masked(self):
        raw_fm = "title: <%- tp.file.title -%>"
        masked, mask_map = mask_templater_expressions(raw_fm)
        assert "<%" not in masked
        assert len(mask_map) == 1


class TestRestoreTemplaterExpressions:
    def test_restores_original_expression_verbatim(self):
        original = "<% tp.date.now('YYYY-MM-DD') %>"
        raw_fm = f"created: {original}"
        masked, mask_map = mask_templater_expressions(raw_fm)
        restored = restore_templater_expressions(masked, mask_map)
        assert restored == raw_fm

    def test_restore_with_resolved_value_substitutes_result(self):
        original = "<% tp.date.now() %>"
        raw_fm = f"created: {original}"
        masked, mask_map = mask_templater_expressions(raw_fm)
        resolved = {"__ARCHIVIST_TMPL_0__": "2024-01-01"}
        restored = restore_templater_expressions(masked, mask_map, resolved=resolved)
        assert restored == "created: 2024-01-01"

    def test_unresolved_sentinels_fall_back_to_original_expression(self):
        """
        If a sentinel has no resolved value, it must come back as the original
        <% expr %>. A sentinel token in the written file is a catastrophic failure.
        """
        original = "<% tp.system.clipboard() %>"
        raw_fm = f"content: {original}"
        masked, mask_map = mask_templater_expressions(raw_fm)
        restored = restore_templater_expressions(masked, mask_map, resolved={})
        assert "__ARCHIVIST_TMPL_" not in restored, (
            "A sentinel token made it into the output. "
            "That's not a frontmatter value, that's archivist graffiti."
        )
        assert original in restored

    def test_empty_mask_map_returns_string_unchanged(self):
        raw_fm = "title: Asha\nclass: character"
        result = restore_templater_expressions(raw_fm, {})
        assert result == raw_fm

    def test_full_roundtrip_mask_then_restore_is_identity(self):
        raw_fm = (
            "title: <% tp.file.title %>\n"
            "created: <% tp.date.now('YYYY-MM-DD') %>\n"
            "class: character"
        )
        masked, mask_map = mask_templater_expressions(raw_fm)
        restored = restore_templater_expressions(masked, mask_map)
        assert restored == raw_fm, (
            "Mask → restore roundtrip changed the frontmatter. "
            "Something in the cycle is eating content it shouldn't touch."
        )

    def test_partial_resolution_leaves_unresolved_expressions_intact(self):
        """
        Two expressions: one resolvable, one not. The resolved one gets its
        value; the unresolved one comes back verbatim, not as a sentinel.
        """
        raw_fm = (
            "created: <% tp.date.now('YYYY-MM-DD') %>\n"
            "mystery: <% tp.system.clipboard() %>"
        )
        masked, mask_map = mask_templater_expressions(raw_fm)
        resolved = {"__ARCHIVIST_TMPL_0__": "2024-01-01"}
        restored = restore_templater_expressions(masked, mask_map, resolved=resolved)

        assert "created: 2024-01-01" in restored
        assert "<% tp.system.clipboard() %>" in restored
        assert "__ARCHIVIST_TMPL_" not in restored, (
            "Sentinel token leaked into output for the unresolved expression."
        )


# ===========================================================================
# moment_to_strftime
# ===========================================================================

class TestMomentToStrftime:
    def test_iso_date_format(self):
        assert moment_to_strftime("YYYY-MM-DD") == "%Y-%m-%d"

    def test_full_datetime_format(self):
        assert moment_to_strftime("YYYY-MM-DD HH:mm:ss") == "%Y-%m-%d %H:%M:%S"

    def test_long_month_name(self):
        assert moment_to_strftime("MMMM") == "%B"

    def test_short_month_name(self):
        assert moment_to_strftime("MMM") == "%b"

    def test_long_weekday_name(self):
        assert moment_to_strftime("dddd") == "%A"

    def test_short_weekday_name(self):
        assert moment_to_strftime("ddd") == "%a"

    def test_two_digit_year(self):
        assert moment_to_strftime("YY") == "%y"

    def test_twelve_hour_clock(self):
        assert moment_to_strftime("hh:mm A") == "%I:%M %p"

    def test_complex_human_readable_format(self):
        assert moment_to_strftime("dddd, MMMM D, YYYY") == "%A, %B %-d, %Y"

    def test_unknown_tokens_pass_through_unchanged(self):
        """
        An unrecognized token should survive rather than blow up.
        Output will be wrong, but that's the caller's fault, not ours.
        """
        result = moment_to_strftime("YYYY [some literal text]")
        assert "%Y" in result

    def test_longer_token_takes_precedence_over_shorter_prefix(self):
        """
        YYYY must match before YY; MMMM before MMM before MM before M.
        If shorter tokens match first, 'YYYY' becomes '%y%y' and everyone
        has a very bad time.
        """
        assert moment_to_strftime("YYYY") == "%Y"   # not "%y%y"
        assert moment_to_strftime("MMMM") == "%B"   # not "%b%m" or similar
        assert moment_to_strftime("MM") == "%m"     # not "%-m%-m" (M matched twice)


# ===========================================================================
# resolve_value
# ===========================================================================

class TestResolveValue:
    """
    resolve_value() is the public entry point for RESOLVE mode.
    These tests verify correct resolution, graceful fallback, and that
    the warn_fn contract is honoured.
    """

    def _ctx(self, tmp_path: Path, fm: dict | None = None) -> TemplaterContext:
        note = tmp_path / "note.md"
        note.write_text("", encoding="utf-8")
        return TemplaterContext(note, fm or {})

    def test_resolves_tp_date_now_default_format(self, tmp_path):
        ctx = self._ctx(tmp_path)
        today = datetime.now().strftime("%Y-%m-%d")
        result, fully_resolved = resolve_value("<% tp.date.now() %>", ctx)
        assert result == today
        assert fully_resolved is True

    def test_resolves_tp_date_now_with_explicit_format(self, tmp_path):
        ctx = self._ctx(tmp_path)
        expected = datetime.now().strftime("%Y/%m/%d")
        result, _ = resolve_value("<% tp.date.now('YYYY/MM/DD') %>", ctx)
        assert result == expected

    def test_resolves_tp_date_now_with_positive_offset(self, tmp_path):
        ctx = self._ctx(tmp_path)
        expected = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")
        result, _ = resolve_value("<% tp.date.now('YYYY-MM-DD', 7) %>", ctx)
        assert result == expected

    def test_resolves_tp_date_now_with_negative_offset(self, tmp_path):
        ctx = self._ctx(tmp_path)
        expected = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        result, _ = resolve_value("<% tp.date.now('YYYY-MM-DD', -1) %>", ctx)
        assert result == expected

    def test_resolves_tp_date_tomorrow(self, tmp_path):
        ctx = self._ctx(tmp_path)
        expected = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        result, _ = resolve_value("<% tp.date.tomorrow() %>", ctx)
        assert result == expected

    def test_resolves_tp_date_yesterday(self, tmp_path):
        ctx = self._ctx(tmp_path)
        expected = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        result, _ = resolve_value("<% tp.date.yesterday() %>", ctx)
        assert result == expected

    def test_resolves_tp_file_title(self, tmp_path):
        note = tmp_path / "my-brilliant-note.md"
        note.write_text("", encoding="utf-8")
        ctx = TemplaterContext(note)
        result, fully_resolved = resolve_value("<% tp.file.title %>", ctx)
        assert result == "my-brilliant-note"
        assert fully_resolved is True

    def test_resolves_tp_file_title_via_property_access(self, tmp_path):
        """tp.file.title is a @property — accessing it via getattr must call it correctly."""
        note = tmp_path / "specific-title.md"
        note.write_text("", encoding="utf-8")
        ctx = TemplaterContext(note)
        result, _ = resolve_value("<% tp.file.title %>", ctx)
        assert result == "specific-title"

    def test_resolves_tp_file_folder_name_only(self, tmp_path):
        subdir = tmp_path / "projects"
        subdir.mkdir()
        note = subdir / "task.md"
        note.write_text("", encoding="utf-8")
        ctx = TemplaterContext(note)
        result, _ = resolve_value("<% tp.file.folder() %>", ctx)
        assert result == "projects"

    def test_resolves_tp_frontmatter_subscript(self, tmp_path):
        ctx = self._ctx(tmp_path, fm={"class": "character", "name": "Asha"})
        result, fully_resolved = resolve_value('<% tp.frontmatter["name"] %>', ctx)
        assert result == "Asha"
        assert fully_resolved is True

    def test_resolves_tp_frontmatter_missing_key_returns_empty_string(self, tmp_path):
        ctx = self._ctx(tmp_path, fm={"class": "character"})
        result, fully_resolved = resolve_value('<% tp.frontmatter["nonexistent"] %>', ctx)
        assert result == ""
        assert fully_resolved is True

    def test_unresolvable_expression_left_verbatim(self, tmp_path):
        """
        tp.system is not implemented. The expression must survive intact —
        not as a sentinel, not as an empty string. Verbatim.
        """
        ctx = self._ctx(tmp_path)
        expr = "<% tp.system.clipboard() %>"
        result, fully_resolved = resolve_value(expr, ctx)
        assert result == expr
        assert fully_resolved is False

    def test_warn_fn_called_for_unresolvable_expression(self, tmp_path):
        ctx = self._ctx(tmp_path)
        warnings = []
        resolve_value("<% tp.system.clipboard() %>", ctx, warn_fn=warnings.append)
        assert len(warnings) == 1
        assert "tp.system.clipboard()" in warnings[0]

    def test_warn_fn_not_called_when_resolution_succeeds(self, tmp_path):
        ctx = self._ctx(tmp_path)
        warnings = []
        resolve_value("<% tp.date.now() %>", ctx, warn_fn=warnings.append)
        assert warnings == []

    def test_warn_fn_none_does_not_raise_on_unresolvable(self, tmp_path):
        """Passing warn_fn=None is the documented way to resolve silently."""
        ctx = self._ctx(tmp_path)
        result, _ = resolve_value("<% tp.system.clipboard() %>", ctx, warn_fn=None)
        assert result == "<% tp.system.clipboard() %>"  # still verbatim

    def test_mixed_value_with_plain_text_and_expression(self, tmp_path):
        """
        Expressions embedded mid-string must resolve in place without corrupting
        the surrounding plain text. This is a real frontmatter pattern.
        """
        ctx = self._ctx(tmp_path)
        today = datetime.now().strftime("%Y-%m-%d")
        result, _ = resolve_value("Created on <% tp.date.now('YYYY-MM-DD') %> by hand", ctx)
        assert result == f"Created on {today} by hand"

    def test_multiple_expressions_in_one_value(self, tmp_path):
        note = tmp_path / "dual.md"
        note.write_text("", encoding="utf-8")
        ctx = TemplaterContext(note)
        today = datetime.now().strftime("%Y-%m-%d")
        result, fully_resolved = resolve_value(
            "<% tp.file.title %> — <% tp.date.now('YYYY-MM-DD') %>", ctx
        )
        assert result == f"dual — {today}"
        assert fully_resolved is True

    def test_fully_resolved_false_when_at_least_one_expression_fails(self, tmp_path):
        note = tmp_path / "mixed.md"
        note.write_text("", encoding="utf-8")
        ctx = TemplaterContext(note)
        _, fully_resolved = resolve_value(
            "<% tp.file.title %> <% tp.system.clipboard() %>", ctx
        )
        assert fully_resolved is False

    def test_static_string_literal_expression_resolves(self, tmp_path):
        """<% "static string" %> is valid Templater syntax and should resolve trivially."""
        ctx = self._ctx(tmp_path)
        result, fully_resolved = resolve_value('<% "hello world" %>', ctx)
        assert result == "hello world"
        assert fully_resolved is True

    def test_plain_string_with_no_expressions_returns_unchanged(self, tmp_path):
        ctx = self._ctx(tmp_path)
        result, fully_resolved = resolve_value("2024-01-01", ctx)
        assert result == "2024-01-01"
        assert fully_resolved is True


# ===========================================================================
# TemplaterContext and _TpFile
# ===========================================================================

class TestTpFile:
    """
    _TpFile is bound to a specific file path. These tests write real temp files
    so that stat() calls return meaningful data.
    """

    def _ctx(self, path: Path) -> TemplaterContext:
        return TemplaterContext(path)

    def test_title_returns_stem(self, tmp_path):
        note = tmp_path / "my-note-title.md"
        note.write_text("", encoding="utf-8")
        ctx = self._ctx(note)
        assert ctx.tp.file.title == "my-note-title"

    def test_folder_returns_parent_name_by_default(self, tmp_path):
        subdir = tmp_path / "stories"
        subdir.mkdir()
        note = subdir / "chapter-one.md"
        note.write_text("", encoding="utf-8")
        ctx = self._ctx(note)
        assert ctx.tp.file.folder() == "stories"

    def test_folder_absolute_returns_full_path(self, tmp_path):
        subdir = tmp_path / "stories"
        subdir.mkdir()
        note = subdir / "chapter-one.md"
        note.write_text("", encoding="utf-8")
        ctx = self._ctx(note)
        result = ctx.tp.file.folder(absolute=True)
        assert result == str(subdir.resolve())

    def test_path_returns_absolute_path_by_default(self, tmp_path):
        note = tmp_path / "note.md"
        note.write_text("", encoding="utf-8")
        ctx = self._ctx(note)
        result = ctx.tp.file.path()
        assert Path(result).is_absolute()
        assert result.endswith("note.md")

    def test_last_modified_date_returns_formatted_string(self, tmp_path):
        note = tmp_path / "note.md"
        note.write_text("content", encoding="utf-8")
        ctx = self._ctx(note)
        result = ctx.tp.file.last_modified_date("YYYY-MM-DD")
        # Should look like a date — we can't assert the exact value without
        # pinning the clock, but we can assert it's not empty and looks right.
        assert result != ""
        assert len(result) == 10  # YYYY-MM-DD is exactly 10 chars
        datetime.strptime(result, "%Y-%m-%d")  # raises ValueError if malformed

    def test_creation_date_returns_formatted_string(self, tmp_path):
        note = tmp_path / "note.md"
        note.write_text("content", encoding="utf-8")
        ctx = self._ctx(note)
        result = ctx.tp.file.creation_date("YYYY-MM-DD")
        assert result != ""
        assert len(result) == 10
        datetime.strptime(result, "%Y-%m-%d")

    def test_missing_file_stat_returns_empty_string_gracefully(self, tmp_path):
        """
        Stat on a nonexistent path should return "" rather than raise.
        In practice this shouldn't happen since we're processing real files,
        but the graceful fallback matters.
        """
        ghost = tmp_path / "ghost.md"
        ctx = self._ctx(ghost)
        assert ctx.tp.file.last_modified_date("YYYY-MM-DD") == ""
        assert ctx.tp.file.creation_date("YYYY-MM-DD") == ""


class TestTpFrontmatter:
    def test_subscript_access_returns_string_value(self, tmp_path):
        note = tmp_path / "note.md"
        note.write_text("", encoding="utf-8")
        ctx = TemplaterContext(note, {"class": "character", "name": "Asha"})
        assert ctx.tp.frontmatter["name"] == "Asha"

    def test_subscript_access_missing_key_returns_empty_string(self, tmp_path):
        note = tmp_path / "note.md"
        note.write_text("", encoding="utf-8")
        ctx = TemplaterContext(note, {"class": "character"})
        assert ctx.tp.frontmatter["nope"] == ""

    def test_none_frontmatter_arg_does_not_raise(self, tmp_path):
        note = tmp_path / "note.md"
        note.write_text("", encoding="utf-8")
        ctx = TemplaterContext(note, None)
        assert ctx.tp.frontmatter["anything"] == ""


class TestTpDate:
    """
    _TpDate tests use the real clock. Offset arithmetic is tested against
    timedelta so we're not just asserting that Python can add numbers.
    """

    def _ctx(self, tmp_path: Path) -> TemplaterContext:
        note = tmp_path / "note.md"
        note.write_text("", encoding="utf-8")
        return TemplaterContext(note)

    def test_now_default_format_returns_iso_date(self, tmp_path):
        ctx = self._ctx(tmp_path)
        result = ctx.tp.date.now()
        datetime.strptime(result, "%Y-%m-%d")  # raises if wrong format

    def test_now_with_offset_is_correct_number_of_days(self, tmp_path):
        ctx = self._ctx(tmp_path)
        base = datetime.now()
        result = ctx.tp.date.now("YYYY-MM-DD", 14)
        expected = (base + timedelta(days=14)).strftime("%Y-%m-%d")
        assert result == expected

    def test_tomorrow_equals_now_plus_one(self, tmp_path):
        ctx = self._ctx(tmp_path)
        assert ctx.tp.date.tomorrow() == ctx.tp.date.now("YYYY-MM-DD", 1)

    def test_yesterday_equals_now_minus_one(self, tmp_path):
        ctx = self._ctx(tmp_path)
        assert ctx.tp.date.yesterday() == ctx.tp.date.now("YYYY-MM-DD", -1)

    def test_today_alias_equals_now_with_zero_offset(self, tmp_path):
        ctx = self._ctx(tmp_path)
        assert ctx.tp.date.today() == ctx.tp.date.now()

    def test_now_with_reference_date_and_format(self, tmp_path):
        ctx = self._ctx(tmp_path)
        result = ctx.tp.date.now(
            "YYYY-MM-DD",
            0,
            reference="2020-06-15",
            reference_format="YYYY-MM-DD",
        )
        assert result == "2020-06-15"

    def test_now_with_reference_and_positive_offset(self, tmp_path):
        ctx = self._ctx(tmp_path)
        result = ctx.tp.date.now(
            "YYYY-MM-DD",
            7,
            reference="2020-06-15",
            reference_format="YYYY-MM-DD",
        )
        assert result == "2020-06-22"

    def test_now_with_malformed_reference_falls_back_to_today(self, tmp_path):
        """
        An unparseable reference date should not raise — it falls back to
        today's date. Better a slightly wrong date than a crashed run.
        """
        ctx = self._ctx(tmp_path)
        result = ctx.tp.date.now(
            "YYYY-MM-DD",
            0,
            reference="not-a-date",
            reference_format="YYYY-MM-DD",
        )
        today = datetime.now().strftime("%Y-%m-%d")
        assert result == today

    def test_weekday_returns_correct_day(self, tmp_path):
        """
        Weekday 0 (Monday) of the week containing 2024-06-19 (Wednesday)
        should be 2024-06-17.
        """
        ctx = self._ctx(tmp_path)
        result = ctx.tp.date.weekday(
            "YYYY-MM-DD",
            0,
            reference="2024-06-19",
            reference_format="YYYY-MM-DD",
        )
        assert result == "2024-06-17"

    def test_weekday_sunday_of_known_week(self, tmp_path):
        ctx = self._ctx(tmp_path)
        result = ctx.tp.date.weekday(
            "YYYY-MM-DD",
            6,
            reference="2024-06-19",
            reference_format="YYYY-MM-DD",
        )
        assert result == "2024-06-23"


# ===========================================================================
# Edge cases the support plan called out explicitly
# ===========================================================================

class TestEdgeCases:
    def test_mask_restore_survives_colon_in_expression(self, tmp_path):
        """
        Expressions containing colons (common in date formats and ternaries)
        must not confuse the mask or restore logic — or downstream YAML parsing.
        """
        raw_fm = "time: <% tp.date.now('HH:mm:ss') %>"
        masked, mask_map = mask_templater_expressions(raw_fm)
        assert ":" not in masked.split(": ", 1)[1], (
            "Colon from the expression content survived masking. "
            "YAML is going to have opinions about that."
        )
        restored = restore_templater_expressions(masked, mask_map)
        assert restored == raw_fm

    def test_mask_restore_survives_hash_in_expression(self, tmp_path):
        """# is a YAML comment character. It must be masked, not interpreted."""
        raw_fm = "comment: <% '# not a comment' %>"
        masked, mask_map = mask_templater_expressions(raw_fm)
        restored = restore_templater_expressions(masked, mask_map)
        assert restored == raw_fm

    def test_mask_restore_survives_curly_braces_in_expression(self, tmp_path):
        """Curly braces in expressions would trigger YAML flow mapping parsing."""
        raw_fm = "meta: <% { key: 'value' } %>"
        masked, mask_map = mask_templater_expressions(raw_fm)
        restored = restore_templater_expressions(masked, mask_map)
        assert restored == raw_fm

    def test_resolve_value_does_not_mutate_plain_frontmatter(self, tmp_path):
        """
        A value with no expressions at all must come back byte-for-byte identical.
        resolve_value runs on every value in RESOLVE mode — it must be a no-op
        when there's nothing to resolve.
        """
        note = tmp_path / "note.md"
        note.write_text("", encoding="utf-8")
        ctx = TemplaterContext(note)
        plain = "2024-01-01"
        result, fully_resolved = resolve_value(plain, ctx)
        assert result == plain
        assert fully_resolved is True

    def test_unimplemented_namespace_returns_verbatim_not_error(self, tmp_path):
        """
        tp.user, tp.system, and other unimplemented namespaces should degrade
        gracefully. Verbatim expression, warn_fn called, no exception.
        """
        note = tmp_path / "note.md"
        note.write_text("", encoding="utf-8")
        ctx = TemplaterContext(note)
        warnings = []
        expr = "<% tp.user.name %>"
        result, fully_resolved = resolve_value(expr, ctx, warn_fn=warnings.append)
        assert result == expr
        assert fully_resolved is False
        assert len(warnings) == 1

    def test_mask_map_stores_full_token_including_delimiters(self, tmp_path):
        """
        mask_map values must be the full <% expr %> token, not just the inner
        expression. restore uses them as direct substitutions.
        """
        expr = "<% tp.date.now('YYYY-MM-DD') %>"
        _, mask_map = mask_templater_expressions(f"created: {expr}")
        assert list(mask_map.values())[0] == expr

    def test_context_constructed_with_dict_subtype_does_not_raise(self, tmp_path):
        """
        The Mapping fix for TemplaterContext should accept dict[str, str | list[str]]
        without complaint — this is what extract_frontmatter actually returns.
        """
        note = tmp_path / "note.md"
        note.write_text("", encoding="utf-8")
        fm: dict[str, str | list[str]] = {"class": "character", "tags": ["hero", "rogue"]}
        ctx = TemplaterContext(note, fm)  # must not raise a TypeError
        assert ctx.tp.frontmatter["class"] == "character"