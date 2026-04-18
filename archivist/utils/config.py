# ---------------------------------------------------------------------------
# .archivist config
# ---------------------------------------------------------------------------

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


def get_archivist_config_path(git_root: Path) -> Path:
    return git_root / ".archivist"


def get_module_type(git_root: Path) -> str | None:
    """
    Return the module-type from .archivist, or None if not configured.
    """
    config = read_archivist_config(git_root)
    if config is None:
        return None
    return config.get("module-type")


# date formatter for changelog filenames, e.g. "CHANGELOG-2024-06-01.md"
def get_today(format: str = CHANGELOG_DATE_FORMAT) -> str:
    """Return today's date formatted as ISO 8601 (YYYY-MM-DD) by default."""
    return datetime.now().strftime(format)


def read_archivist_config(git_root: Path) -> dict[str, str] | None:
    """
    Read and parse the .archivist config file at the repo root.
    Returns the config dict, or None if the file does not exist.
    """
    path = get_archivist_config_path(git_root)
    if not path.exists():
        return None
    try:
        data: dict[str, str] | None = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError as e:
        print(f"❌  Could not parse .archivist config: {e}", file=sys.stderr)
        return {}


def write_archivist_config(git_root: Path, config: dict[str, str]) -> None:
    """Write the .archivist config file at the repo root."""
    path = get_archivist_config_path(git_root)
    lines = ["# archivist project configuration"]
    for key, value in config.items():
        lines.append(f"{key}: {value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")