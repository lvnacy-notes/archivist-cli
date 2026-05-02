# ---------------------------------------------------------------------------
# .archivist config
# ---------------------------------------------------------------------------

import importlib.util
import pathspec
import sys
import types
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

# ---------------------------------------------------------------------------
# Internal path resolution
# ---------------------------------------------------------------------------

def _get_archivist_dir(git_root: Path) -> Path:
    """Return the .archivist/ directory path (does not assert existence)."""
    return git_root / ".archivist"


def _get_config_yaml_path(git_root: Path) -> Path:
    """Return the canonical config path inside the .archivist/ directory."""
    return _get_archivist_dir(git_root) / "config.yaml"


def _get_legacy_config_path(git_root: Path) -> Path:
    """Return the legacy flat-file .archivist path."""
    return git_root / ".archivist"


# ---------------------------------------------------------------------------
# Public config path — handles both forms transparently
# ---------------------------------------------------------------------------

def get_archivist_config_path(git_root: Path) -> Path:
    """
    Resolve the active config path, preferring the directory form.

    Priority:
      1. `.archivist/config.yaml` — the directory form; canonical for all new
         projects and any project that has already migrated.
      2. `.archivist` flat file — legacy form; supported transparently so
         existing projects don't break when they pull a new version of Archivist
         and haven't migrated yet.

    Returns the Path of whichever form exists. If neither exists, returns the
    canonical directory-form path so callers that need to *write* a config know
    where to put it without a separate code path.
    """
    canonical = _get_config_yaml_path(git_root)
    if canonical.exists():
        return canonical

    legacy = _get_legacy_config_path(git_root)
    if legacy.exists() and legacy.is_file():
        return legacy

    # Neither exists yet — return canonical so writers know where to go.
    return canonical


# ---------------------------------------------------------------------------
# ignore spec
# ---------------------------------------------------------------------------

def build_ignore_spec(git_root: Path) -> pathspec.PathSpec:
    """
    Build a PathSpec from the `ignores` list in .archivist config.

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


# ---------------------------------------------------------------------------
# Module type
# ---------------------------------------------------------------------------

def get_module_type(git_root: Path) -> str | None:
    """
    Return the module-type from .archivist config, or None if not configured.
    """
    config = read_archivist_config(git_root)
    if config is None:
        return None
    value = config.get("module-type")
    return value if isinstance(value, str) else None


# ---------------------------------------------------------------------------
# Date
# ---------------------------------------------------------------------------

def get_today(format: str = CHANGELOG_DATE_FORMAT) -> str:
    """Return today's date formatted as ISO 8601 (YYYY-MM-DD) by default."""
    return datetime.now().strftime(format)


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------

def read_archivist_config(git_root: Path) -> dict[str, str | list[str]] | None:
    """
    Read and parse the .archivist config — directory form or legacy flat file.

    Returns the config dict, or None if neither form exists. All call sites
    are agnostic to which form is present; this function handles it.
    """
    path = get_archivist_config_path(git_root)
    if not path.exists():
        return None
    # The directory itself existing is not a config — only the file inside is.
    if path.is_dir():
        return None
    try:
        data: dict[str, str | list[str]] | None = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError as e:
        print(f"❌  Could not parse .archivist config: {e}", file=sys.stderr)
        return {}


def write_archivist_config(git_root: Path, config: dict) -> None:
    """
    Write the .archivist config to `.archivist/config.yaml`.

    Always writes to the directory form. If the directory doesn't exist yet,
    it's created.

    If a legacy flat `.archivist` file occupies the path where the directory
    needs to go, it is removed automatically before the directory is created.
    This handles the case where write_archivist_config is called on a repo
    that hasn't been explicitly migrated yet — init, tests, and any other
    call site shouldn't have to care about the pre-existing form.

    The explicit `archivist migrate` command exists for intentional, supervised
    migration with a confirmation gate and git instructions. This is the silent
    path for everything else that just needs the config written.

    Scalar values are written as plain `key: value` pairs. The `ignores` key
    is always written as a YAML block sequence, even when empty — so users
    know it's there and don't have to guess the expected format when they go
    to fill it in.
    """
    archivist_dir = _get_archivist_dir(git_root)

    # If the legacy flat file is squatting on the directory path, evict it.
    # Can't mkdir over an existing file — and we're not in the business of
    # leaving the config in an indeterminate state because of a path collision.
    if archivist_dir.exists() and archivist_dir.is_file():
        archivist_dir.unlink()

    archivist_dir.mkdir(exist_ok=True)

    path = _get_config_yaml_path(git_root)
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
        elif isinstance(value, dict):
            # Nested mapping — write as a YAML block mapping.
            # Currently used for the `directories` block on library modules.
            # f"{key}: {value}" would write Python repr, which is not YAML.
            lines.append(f"{key}:")
            for sub_key, sub_val in value.items():
                lines.append(f"  {sub_key}: {sub_val}")
        else:
            lines.append(f"{key}: {value}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Plugin discovery and loading
# ---------------------------------------------------------------------------

def find_changelog_plugin(git_root: Path) -> Path | None:
    """
    Check whether a changelog plugin exists at `.archivist/changelog.py`.

    Returns the Path if found, None if not. Does not load or validate it —
    that's `load_changelog_plugin()`'s problem. This function just answers
    the yes/no question of whether the file is there.

    Only `changelog.py` is recognized. `sample-changelog.py` and anything
    else in `.archivist/` are ignored. The convention is exact: the filename
    is the registration.
    """
    plugin_path = _get_archivist_dir(git_root) / "changelog.py"
    return plugin_path if plugin_path.exists() else None


def load_changelog_plugin(plugin_path: Path) -> types.ModuleType:
    """
    Load a changelog plugin from the given path and return the module.

    Validates that the loaded module exposes a callable `run`. If it doesn't,
    or if the file is syntactically broken, exits with a clear error message
    rather than letting Python vomit a raw traceback at the user.

    The returned module is ready to use — call `module.run(args)` and go.

    This does not catch runtime errors inside the plugin's `run()` itself.
    If your plugin blows up mid-execution, you get the traceback. That's your
    fault, not Archivist's.
    """
    try:
        spec = importlib.util.spec_from_file_location("archivist_changelog_plugin", plugin_path)
        if spec is None or spec.loader is None:
            print(
                f"❌  Couldn't load changelog plugin at {plugin_path} — "
                "importlib returned no spec. Is it actually a Python file?",
                file=sys.stderr,
            )
            sys.exit(1)

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]

    except SyntaxError as e:
        print(
            f"❌  Syntax error in changelog plugin {plugin_path}:\n"
            f"    {e}",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as e:
        print(
            f"❌  Failed to load changelog plugin {plugin_path}:\n"
            f"    {e}",
            file=sys.stderr,
        )
        sys.exit(1)

    if not callable(getattr(module, "run", None)):
        print(
            f"❌  Changelog plugin at {plugin_path} has no callable `run` function.\n"
            "    That's the entire contract. One function. Go fix it.",
            file=sys.stderr,
        )
        sys.exit(1)

    return module