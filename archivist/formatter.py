"""
archivist.formatter — ANSI-styled help formatter for argparse.

Provides ArchivistHelpFormatter (drop-in formatter_class replacement) and
fmt_description() / fmt_examples() helpers for building rich help strings.

Falls back to plain text automatically when:
  - stdout is not a TTY (piped output, CI, etc.)
  - the NO_COLOR environment variable is set
"""

import argparse
import os
import shutil
import sys

# ── ANSI escape codes ────────────────────────────────────────────────────────

RESET       = "\033[0m"
BOLD        = "\033[1m"
DIM         = "\033[2m"
YELLOW      = "\033[33m"
CYAN        = "\033[96m"   # bright cyan
GREEN       = "\033[92m"   # bright green
WHITE       = "\033[97m"


# ── TTY / color detection ─────────────────────────────────────────────────────

def _ansi_ok() -> bool:
    """True when ANSI output is safe: stdout is a TTY and NO_COLOR is not set."""
    if os.environ.get("NO_COLOR"):
        return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _esc(code: str, text: str, ansi: bool = True) -> str:
    return f"{code}{text}{RESET}" if ansi else text


# ── Description / example helpers ─────────────────────────────────────────────

def fmt_description(text: str) -> str:
    """
    Wrap a plain-text description for use in a parser's description= argument.
    Preserves the text as-is; pair with RawDescriptionHelpFormatter (the base
    of ArchivistHelpFormatter) so whitespace is not re-wrapped.
    """
    return text.strip()


def fmt_examples(*commands: str) -> str:
    """
    Build a formatted EXAMPLES block for use in description= or epilog=.

    Usage:
        gen_p = cl_sub.add_parser(
            "general",
            description=(
                "Generate a general-purpose changelog.\\n\\n"
                + fmt_examples(
                    "archivist changelog general",
                    "archivist changelog general a1b2c3d",
                )
            ),
        )
    """
    ansi = _ansi_ok()
    lines = ["\n" + _esc(BOLD + YELLOW, "EXAMPLES", ansi)]
    for cmd in commands:
        lines.append("  " + _esc(GREEN, cmd, ansi))
    return "\n".join(lines)


def fmt_warning(text: str) -> str:
    """
    Build a formatted WARNING block for use in epilog=.
    """
    ansi = _ansi_ok()
    label = _esc(BOLD + YELLOW, "WARNING", ansi)
    return f"\n{label}\n  {text}"


# ── Formatter class ───────────────────────────────────────────────────────────

class ArchivistHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """
    ANSI-styled help formatter.

    - Section headers (USAGE, OPTIONS, COMMANDS, etc.) → bold yellow, uppercased
    - Flag and command names in the left column         → bold cyan
    - Descriptions and help strings                     → normal weight
    - Falls back to plain text when not in a TTY
    """

    def __init__(
        self,
        prog: str,
        indent_increment: int = 2,
        max_help_position: int = 30,
        width: int | None = None,
    ) -> None:
        if width is None:
            width = min(shutil.get_terminal_size((100, 24)).columns, 100)
        super().__init__(prog, indent_increment, max_help_position, width)
        self._use_ansi = _ansi_ok()

    # ── internal helper ───────────────────────────────────────────────────────

    def _c(self, code: str, text: str) -> str:
        return _esc(code, text, self._use_ansi)

    # ── section headings ──────────────────────────────────────────────────────

    def start_section(self, heading: str) -> None:  # type: ignore[override]
        styled = self._c(BOLD + YELLOW, heading.upper()) if heading else heading
        super().start_section(styled)

    # ── left-column: flag/command names ───────────────────────────────────────

    def _format_action_invocation(self, action: argparse.Action) -> str:
        text = super()._format_action_invocation(action)
        return self._c(BOLD + CYAN, text)

    # ── usage line ────────────────────────────────────────────────────────────

    def _format_usage(
        self,
        usage: str,
        actions: list,
        groups: list,
        prefix: str | None,
    ) -> str:
        if prefix is None:
            prefix = self._c(BOLD + YELLOW, "USAGE") + "\n  "
        return super()._format_usage(usage, actions, groups, prefix)