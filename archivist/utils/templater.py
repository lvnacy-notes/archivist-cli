# ---------------------------------------------------------------------------
# Templater expression handling
# ---------------------------------------------------------------------------
#
# This module is the single source of truth for everything Templater-shaped.
# It handles detection, masking, restoration, and resolution of <% %> expressions
# in YAML frontmatter — without Node.js, without Obsidian, and without any
# runtime dependencies that aren't already in the project.
#
# Public surface:
#   Mode enum          — TemplaterMode, templater_mode_from_config
#   Detection          — has_templater_expression, extract_expressions
#   Masking            — mask_templater_expressions, restore_templater_expressions
#   Resolution         — resolve_value, TemplaterContext
#   Format translation — moment_to_strftime
#   Config integration — get_templater_mode
# ---------------------------------------------------------------------------

from __future__ import annotations

import ast
import os
import re
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping

# ---------------------------------------------------------------------------
# Mode enum
# ---------------------------------------------------------------------------

class TemplaterMode(Enum):
    """
    How Archivist handles <% %> expressions in frontmatter.

    RESOLVE  — attempt to evaluate tp.date.*, tp.file.*, tp.frontmatter.* at
               write time using our own Python implementation. Anything we
               can't handle is left verbatim with a warning.
    PRESERVE — detect expressions and round-trip them safely without touching
               their content. Resolution is the user's problem in Obsidian.
    DISABLED — treat <% %> as dumb strings. Zero overhead, zero handling.
               Use this if your project is Templater-free and you enjoy speed.
    """
    RESOLVE  = "resolve"
    PRESERVE = "preserve"
    DISABLED = "false"

    @classmethod
    def from_config(cls, value: str | None) -> "TemplaterMode":
        """
        Parse a config string into a TemplaterMode.

        Defaults to PRESERVE if the value is None, empty, or unrecognized —
        because PRESERVE is the safest default for anyone who hasn't thought
        about it yet, which is most people.
        """
        if value is None:
            return cls.PRESERVE
        normalized = value.strip().lower()
        for member in cls:
            if member.value == normalized:
                return member
        # Unrecognized value — PRESERVE is safer than DISABLED
        return cls.PRESERVE


def get_templater_mode(config: dict[str, str] | None) -> TemplaterMode:
    """
    Extract and parse the templater mode from a .archivist config dict.
    Returns TemplaterMode.PRESERVE if config is None or key is absent.
    """
    if config is None:
        return TemplaterMode.PRESERVE
    return TemplaterMode.from_config(config.get("templater"))


# ---------------------------------------------------------------------------
# Core regex
# ---------------------------------------------------------------------------

# Matches Templater expressions: <% expr %>, <%- expr %>, <% expr -%>, etc.
# The [-_]? handles whitespace-control variants. The non-greedy (.*?) means
# multiple expressions on one line are each captured separately.
TEMPLATER_EXPR_RE = re.compile(r"<%[-_]?\s*(.*?)\s*[-_]?%>", re.DOTALL)

# Sentinel token pattern: __ARCHIVIST_TMPL_{index}__
# Chosen to be:
#   (a) valid YAML scalar content — no special chars
#   (b) astronomically unlikely to collide with real frontmatter values
#   (c) easily identifiable in debug output when something inevitably goes wrong
_SENTINEL_PATTERN = "__ARCHIVIST_TMPL_{index}__"
_SENTINEL_RE = re.compile(r"__ARCHIVIST_TMPL_(\d+)__")


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def has_templater_expression(value: str) -> bool:
    """Return True if the string contains at least one <% %> expression."""
    return bool(TEMPLATER_EXPR_RE.search(value))


def extract_expressions(value: str) -> list[str]:
    """
    Extract all raw expression strings (the content between <% and %>) from a value.
    Returns the inner expression text, not the full <% expr %> tokens.
    """
    return TEMPLATER_EXPR_RE.findall(value)


# ---------------------------------------------------------------------------
# Mask / restore cycle
# ---------------------------------------------------------------------------

def mask_templater_expressions(raw_fm: str) -> tuple[str, dict[str, str]]:
    """
    Replace every <% %> block in raw frontmatter text with a stable sentinel token.

    This makes the frontmatter safe to parse, diff, reorder, and otherwise
    molest without corrupting expression content — YAML has Opinions about
    characters like {, }, :, and # that Templater expressions freely use.

    Returns:
        (masked_fm, mask_map)

        masked_fm: the raw frontmatter string with all expressions replaced by
                   __ARCHIVIST_TMPL_0__, __ARCHIVIST_TMPL_1__, etc.
        mask_map:  dict mapping sentinel token → original expression (full
                   <% expr %> form, not just the inner content). Restore by
                   replacing sentinels with their original values.

    The mask_map preserves insertion order (Python 3.7+ dicts do this).
    Sentinels are numbered in left-to-right, top-to-bottom order of appearance.
    """
    mask_map: dict[str, str] = {}
    counter = 0

    def _replacer(match: re.Match) -> str:
        nonlocal counter
        sentinel = _SENTINEL_PATTERN.format(index=counter)
        mask_map[sentinel] = match.group(0)  # full <% expr %> token
        counter += 1
        return sentinel

    masked = TEMPLATER_EXPR_RE.sub(_replacer, raw_fm)
    return masked, mask_map


def restore_templater_expressions(
    raw_fm: str,
    mask_map: dict[str, str],
    resolved: dict[str, str] | None = None,
) -> str:
    """
    Restore masked expressions back into frontmatter text.

    In PRESERVE mode: pass resolved=None. Every sentinel is replaced with its
    original <% expr %> token verbatim.

    In RESOLVE mode: pass resolved={sentinel: resolved_value} for any
    expression that was successfully resolved. Those sentinels get their
    resolved value; everything else gets its original expression back.

    Sentinels with no entry in either dict are left in place and will appear
    in the written output — this should not happen in practice, but if it
    does, it's a loud failure mode that's easy to grep for.
    """
    if not mask_map:
        return raw_fm

    effective = dict(mask_map)  # originals as fallback
    if resolved:
        effective.update(resolved)  # resolved values override

    def _restorer(match: re.Match[str]) -> str:
        sentinel = match.group(0)
        return effective.get(sentinel, sentinel)  # loud fallback

    return _SENTINEL_RE.sub(_restorer, raw_fm)


# ---------------------------------------------------------------------------
# moment.js → strftime format translation
# ---------------------------------------------------------------------------

# Mapping of moment.js format tokens to Python strftime equivalents.
# Ordered longest-first within each group to prevent prefix ambiguity
# (e.g. "YYYY" must match before "YY", "MM" before "M", etc.)
# This covers ~95% of real-world frontmatter date formats.
_MOMENT_TO_STRFTIME_MAP: list[tuple[str, str]] = [
    # Year
    ("YYYY", "%Y"),
    ("YY",   "%y"),
    # Month
    ("MMMM", "%B"),
    ("MMM",  "%b"),
    ("MM",   "%m"),
    ("M",    "%-m"),   # no leading zero — Linux/macOS only
    # Day of month
    ("DD",   "%d"),
    ("D",    "%-d"),   # no leading zero — Linux/macOS only
    # Day of week
    ("dddd", "%A"),
    ("ddd",  "%a"),
    # Hour
    ("HH",   "%H"),
    ("H",    "%-H"),
    ("hh",   "%I"),
    ("h",    "%-I"),
    # Minute
    ("mm",   "%M"),
    # Second
    ("ss",   "%S"),
    # AM/PM
    ("A",    "%p"),
    ("a",    "%P"),    # lowercase am/pm — glibc extension
    # Day of year
    ("DDDD", "%j"),
    # Week of year
    ("ww",   "%W"),
    # Timezone
    ("ZZ",   "%z"),
    ("Z",    "%z"),
]

# Pre-escaped moment tokens sorted longest-first to avoid prefix ambiguity
_MOMENT_TOKEN_RE = re.compile(
    r"(" + "|".join(re.escape(tok) for tok, _ in _MOMENT_TO_STRFTIME_MAP) + r")"
)
_MOMENT_TOKEN_LOOKUP = dict(_MOMENT_TO_STRFTIME_MAP)


def moment_to_strftime(fmt: str) -> str:
    """
    Translate a moment.js date format string to a Python strftime format string.

    Handles the ~95% of real-world tokens that appear in Templater frontmatter.
    Passes through anything it doesn't recognize — if your format breaks, that's
    on you and your exotic locale tokens.

    >>> moment_to_strftime("YYYY-MM-DD")
    '%Y-%m-%d'
    >>> moment_to_strftime("dddd, MMMM D, YYYY")
    '%A, %B %-d, %Y'
    """
    return _MOMENT_TOKEN_RE.sub(lambda m: _MOMENT_TOKEN_LOOKUP[m.group(0)], fmt)


# ---------------------------------------------------------------------------
# tp namespace implementations
# ---------------------------------------------------------------------------

class _TpDate:
    """
    Python implementation of Templater's tp.date namespace.

    Supports: now, today (alias), tomorrow, yesterday.
    Offset unit is days only for now — moment.js unit strings are a Phase 3 problem.
    """

    def now(
        self,
        fmt: str = "YYYY-MM-DD",
        offset: int = 0,
        reference: str | None = None,
        reference_format: str | None = None,
    ) -> str:
        """
        Return a formatted date string, optionally offset by N days.

        Args:
            fmt:              moment.js format string (default: "YYYY-MM-DD")
            offset:           integer day offset from today (default: 0)
            reference:        reference date string (default: today)
            reference_format: moment.js format of the reference string (unused
                              if reference is None)

        The reference and reference_format args exist for API completeness —
        they're rarely used in frontmatter templates and the common case is
        just now() or now("format") or now("format", N).
        """
        if reference is not None and reference_format is not None:
            py_ref_fmt = moment_to_strftime(reference_format)
            try:
                base = datetime.strptime(reference, py_ref_fmt)
            except ValueError:
                base = datetime.now()
        else:
            base = datetime.now()

        target = base + timedelta(days=int(offset))
        return target.strftime(moment_to_strftime(fmt))

    def today(self, fmt: str = "YYYY-MM-DD") -> str:
        """Alias for now() with no offset. Because Templater has both."""
        return self.now(fmt, 0)

    def tomorrow(self, fmt: str = "YYYY-MM-DD") -> str:
        """Sugar for now(fmt, 1)."""
        return self.now(fmt, 1)

    def yesterday(self, fmt: str = "YYYY-MM-DD") -> str:
        """Sugar for now(fmt, -1)."""
        return self.now(fmt, -1)

    def weekday(
        self,
        fmt: str,
        weekday: int,
        reference: str | None = None,
        reference_format: str | None = None,
    ) -> str:
        """
        Return the date of the specified weekday (0=Monday, 6=Sunday) in
        the week containing the reference date (default: today).
        """
        if reference is not None and reference_format is not None:
            py_ref_fmt = moment_to_strftime(reference_format)
            try:
                base = datetime.strptime(reference, py_ref_fmt)
            except ValueError:
                base = datetime.now()
        else:
            base = datetime.now()
        # Roll base back to Monday of its week, then forward to target weekday
        monday = base - timedelta(days=base.weekday())
        target = monday + timedelta(days=int(weekday))
        return target.strftime(moment_to_strftime(fmt))


class _TpFile:
    """
    Python implementation of Templater's tp.file namespace.

    Everything here is derived from the target file's path — no Obsidian
    API required, no magic, just `pathlib` and `os.stat`.
    """

    def __init__(self, file_path: Path) -> None:
        self._path = file_path.resolve()

    @property
    def title(self) -> str:
        """Filename without extension. The simplest possible thing."""
        return self._path.stem

    @property
    def content(self) -> str:
        """
        Raw file content. Available but expensive — reading the whole file
        just to populate a frontmatter field is a hell of a thing to do, but
        Templater supports it and so do we.
        """
        try:
            return self._path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    def folder(self, absolute: bool = False) -> str:
        """
        Parent directory of the file.
        If absolute is True, returns the absolute path. Otherwise, returns
        just the folder name — matching Templater's default behaviour.
        """
        parent = self._path.parent
        return str(parent) if absolute else parent.name

    def path(self, relative: bool = False) -> str:
        """
        Full path to the file.
        If relative is True, attempts to return a path relative to cwd.
        """
        if relative:
            try:
                return str(self._path.relative_to(Path.cwd()))
            except ValueError:
                pass
        return str(self._path)

    def creation_date(self, fmt: str = "YYYY-MM-DD") -> str:
        """
        File creation time, formatted with the given moment.js format string.

        Uses st_birthtime (macOS/BSD) when available; falls back to st_ctime
        on Linux where birthtime is not always accessible. Not perfect, but
        neither is st_ctime and we're not here to litigate filesystem semantics.
        """
        try:
            stat = os.stat(self._path)
            ts = getattr(stat, "st_birthtime", stat.st_ctime)
            return datetime.fromtimestamp(ts).strftime(moment_to_strftime(fmt))
        except OSError:
            return ""

    def last_modified_date(self, fmt: str = "YYYY-MM-DD") -> str:
        """File last-modified time, formatted with the given moment.js format string."""
        try:
            mtime = os.stat(self._path).st_mtime
            return datetime.fromtimestamp(mtime).strftime(moment_to_strftime(fmt))
        except OSError:
            return ""


class _TpFrontmatter:
    """
    Python implementation of Templater's tp.frontmatter namespace.

    Provides dict-style access to the other properties in the same file.
    Populated from the file's existing parsed frontmatter before resolution runs.
    Single-pass only — properties that are themselves expressions won't be
    resolved yet when cross-referencing. Document this and move on.
    """

    def __init__(self, frontmatter: Mapping[str, object]) -> None:
        self._fm = frontmatter

    def __getitem__(self, key: str) -> str:
        """
        Return the value for a frontmatter key as a string.
        Returns an empty string if the key doesn't exist — same as Templater.
        """
        val = self._fm.get(key, "")
        return str(val) if val is not None else ""


class _Tp:
    """
    The tp object. One instance per file being processed.
    Mimics the shape of Templater's tp global — same namespace structure,
    different runtime (Python instead of "inside Obsidian somewhere").
    """

    def __init__(self, file_path: Path, frontmatter: Mapping[str, object]) -> None:
        self.date        = _TpDate()
        self.file        = _TpFile(file_path)
        self.frontmatter = _TpFrontmatter(frontmatter)


class TemplaterContext:
    """
    Execution context for resolving Templater expressions against a specific file.

    Create one per file. Pass it to resolve_value. Discard it. Don't reuse
    it across files — the tp.file namespace is path-specific.

    Args:
        file_path:   absolute or relative path to the target note (the file
                     being written to, not the template file)
        frontmatter: pre-parsed frontmatter dict from the target note, used
                     to populate tp.frontmatter. Pass {} if unavailable.
    """

    def __init__(self, file_path: Path, frontmatter: Mapping[str, object] | None = None) -> None:
        self.tp = _Tp(file_path, frontmatter or {})


# ---------------------------------------------------------------------------
# Expression evaluator
# ---------------------------------------------------------------------------

# Matches: tp.namespace.method(args)
_CALL_RE = re.compile(r"^tp\.(\w+)\.(\w+)\((.*)\)$", re.DOTALL)
# Matches: tp.namespace.property  (no call parens)
_PROP_RE = re.compile(r"^tp\.(\w+)\.(\w+)$")
# Matches: tp.frontmatter["key"] or tp.frontmatter['key']
_FM_SUBSCRIPT_RE = re.compile(r"""^tp\.frontmatter\[(['"])(.+?)\1\]$""")


def _parse_args(raw_args: str) -> list[object]:
    """
    Parse a raw argument string from a Templater function call.

    Uses ast.literal_eval for safety — no arbitrary code execution, just
    string and numeric literals. If parsing fails, returns an empty list
    so the caller can decide whether to fall back or abort.

    Handles: "YYYY-MM-DD", 7, True, None, and combinations thereof.
    Does NOT handle: expressions, variable references, function calls as args.
    If your template args are that spicy, you should be using a user script.
    """
    if not raw_args.strip():
        return []
    try:
        # Wrap in a tuple so ast.literal_eval parses a comma-separated list correctly
        parsed = ast.literal_eval(f"({raw_args},)")
        return list(parsed)
    except (ValueError, SyntaxError):
        return []


def _try_resolve_expression(expr: str, ctx: TemplaterContext) -> str | None:
    """
    Attempt to resolve a single Templater expression string (the content
    between <% and %>) using the provided context.

    Returns the resolved string value on success, None on failure.
    None means "I don't know how to handle this — leave it alone."

    Handles:
      tp.namespace.method(args)      — function calls
      tp.namespace.property          — property access
      tp.frontmatter["key"]          — subscript access
      "static string"                — degenerate literal (valid Templater, rare)
    """
    expr = expr.strip()

    # Degenerate case: static string literal
    # <% "some string" %> is valid Templater and we can handle it trivially
    if (expr.startswith('"') and expr.endswith('"')) or \
       (expr.startswith("'") and expr.endswith("'")):
        try:
            return ast.literal_eval(expr)
        except (ValueError, SyntaxError):
            return None

    # tp.frontmatter["key"] subscript access
    fm_match = _FM_SUBSCRIPT_RE.match(expr)
    if fm_match:
        key = fm_match.group(2)
        return ctx.tp.frontmatter[key]

    # tp.namespace.method(args) function call
    call_match = _CALL_RE.match(expr)
    if call_match:
        namespace_name = call_match.group(1)
        method_name    = call_match.group(2)
        raw_args       = call_match.group(3)

        namespace = getattr(ctx.tp, namespace_name, None)
        if namespace is None:
            return None  # unimplemented namespace — tp.system, tp.user, etc.

        method = getattr(namespace, method_name, None)
        if method is None or not callable(method):
            return None

        args = _parse_args(raw_args)
        try:
            result = method(*args)
            return str(result)
        except (TypeError, ValueError, OSError):
            return None

    # tp.namespace.property access
    prop_match = _PROP_RE.match(expr)
    if prop_match:
        namespace_name = prop_match.group(1)
        prop_name      = prop_match.group(2)

        namespace = getattr(ctx.tp, namespace_name, None)
        if namespace is None:
            return None

        # Properties can be plain attributes or zero-arg methods.
        # tp.file.title is a @property; call getattr and let Python sort it out.
        val = getattr(namespace, prop_name, None)
        if val is None:
            return None
        # If it's callable with no args (zero-arg method, not a @property),
        # call it. Otherwise use it directly.
        if callable(val):
            try:
                return str(val())
            except (TypeError, OSError):
                return None
        return str(val)

    # Nothing matched — expression uses syntax we don't implement
    return None


def resolve_value(
    value: str,
    ctx: TemplaterContext,
    warn_fn: "Callable[[str], None] | None" = None,
) -> tuple[str, bool]:
    """
    Resolve all Templater expressions in a frontmatter value string.

    Finds every <% expr %> block, attempts resolution, substitutes the result.
    Expressions that can't be resolved are left verbatim — the surrounding
    string is returned intact with those blocks still present.

    Args:
        value:   the raw frontmatter value string (may contain one or more
                 <% %> blocks, plain text, or a mix of both)
        ctx:     TemplaterContext bound to the target file
        warn_fn: optional callable(msg: str) called for each expression that
                 couldn't be resolved. Use this to wire in your warning output.
                 Pass None to resolve silently.

    Returns:
        (result_str, fully_resolved)

        result_str:      the value with all resolvable expressions substituted
        fully_resolved:  True if every expression in the value was resolved;
                         False if any were left verbatim
    """
    any_unresolved = False

    def _replacer(match: re.Match) -> str:
        nonlocal any_unresolved
        full_token = match.group(0)
        inner_expr = match.group(1).strip()
        resolved = _try_resolve_expression(inner_expr, ctx)
        if resolved is not None:
            return resolved
        any_unresolved = True
        if warn_fn:
            warn_fn(
                f"Could not resolve Templater expression: {full_token!r} — "
                f"leaving verbatim. This may require Obsidian."
            )
        return full_token

    result = TEMPLATER_EXPR_RE.sub(_replacer, value)
    return result, not any_unresolved