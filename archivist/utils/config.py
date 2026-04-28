# ---------------------------------------------------------------------------
# .archivist config
# ---------------------------------------------------------------------------

import pathspec
import sys
from datetime import datetime
from pathlib import Path

import yaml


# Known Apparatus module types
APPARATUS_MODULE_TYPES = ["story", "publication", "library", "vault", "general"]

CHANGELOG_DATE_FORMAT = "%Y-%m-%d"

# Changelog subcommand for each module type
MODULE_CHANGELOG_COMMAND = {
    "general":     "general",
    "library":     "library",
    "publication": "publication",
    "story":       "story",
    "vault":       "vault",
}

def build_ignore_spec(git_root: Path) -> pathspec.PathSpec:
    """
    Build a PathSpec from the `ignores` list in .archivist.

    Patterns follow full .gitignore semantics via the gitwildmatch engine —
    leading slashes, double-star globs, negation with `!`, all of it. If
    `ignores` is absent or empty, returns a spec that matches nothing, so
    callers don't have to care whether the user bothered to configure anything.

    Paths passed to spec.match_file() must be repo-relative. That's your
    problem, not this function's. Don't pass absolute paths and then file
    a bug when nothing matches.
    """
    config = read_archivist_config(git_root)
    patterns: list[str] = []

    if config:
        raw = config.get("ignores", [])
        # Tolerate a single string in case someone wrote `ignores: "*.tmp"`
        # instead of a proper list. We've all done dumber things.
        if isinstance(raw, str):
            patterns = [raw]
        elif isinstance(raw, list):
            patterns = [p for p in raw if isinstance(p, str) and p.strip()]

    return pathspec.PathSpec.from_lines("gitignore", patterns)


def get_archivist_config_path(git_root: Path) -> Path:
    return git_root / ".archivist"


def get_module_type(git_root: Path) -> str | None:
    """
    Return the module-type from .archivist, or None if not configured.
    """
    config = read_archivist_config(git_root)
    if config is None:
        return None
    value = config.get("module-type")
    return value if isinstance(value, str) else None


# date formatter for changelog filenames, e.g. "CHANGELOG-2024-06-01.md"
def get_today(format: str = CHANGELOG_DATE_FORMAT) -> str:
    """Return today's date formatted as ISO 8601 (YYYY-MM-DD) by default."""
    return datetime.now().strftime(format)


def read_archivist_config(git_root: Path) -> dict[str, str | list[str]] | None:
    """
    Read and parse the .archivist config file at the repo root.
    Returns the config dict, or None if the file does not exist.
    """
    path = get_archivist_config_path(git_root)
    if not path.exists():
        return None
    try:
        data: dict[str, str | list[str]] | None = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError as e:
        print(f"❌  Could not parse .archivist config: {e}", file=sys.stderr)
        return {}


def write_archivist_config(git_root: Path, config: dict) -> None:
    """
    Write the .archivist config file at the repo root.

    Scalar values are written as plain `key: value` pairs. The `ignores` key
    is always written as a YAML block sequence, even when empty — so users
    know it's there and don't have to guess the expected format when they go
    to fill it in.
    """
    path = get_archivist_config_path(git_root)
    lines = ["# archivist project configuration"]

    for key, value in config.items():
        if key == "ignores":
            lines.append("ignores:")
            entries = value if isinstance(value, list) else []
            for pattern in entries:
                lines.append(f'  - "{ pattern }"')
            if not entries:
                # Write an empty block sequence so the key is visible and the
                # format is unambiguous. A bare `ignores:` with no entries
                # parses as null in YAML — not what we want.
                lines.append("  []")
        else:
            lines.append(f"{key}: {value}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")