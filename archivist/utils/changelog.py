# ---------------------------------------------------------------------------
# Changelog / manifest update helpers
# ---------------------------------------------------------------------------

import re
import sys
import uuid as _uuid_module
from pathlib import Path
from archivist.utils.config import (
    get_module_type,
    read_archivist_config,
)
from archivist.utils.rename_helpers import (
    GitChanges,
    is_cross_dir_move,
    rename_display_path,
    rename_suspicion,
)


ARCHIVIST_AUTO_END = "<!-- archivist:auto-end -->"

# Intentionally strict. Matches ONLY the unsealed filename format
# (CHANGELOG-YYYY-MM-DD.md). Sealed changelogs carry a short SHA suffix
# (CHANGELOG-YYYY-MM-DD-{sha}.md) — they are closed records that document
# a past commit and must never be treated as active. find_active_changelog()
# uses this to exclude them, and the pre-commit hook uses the equivalent
# pattern for the same reason. Do not relax this to match the SHA suffix.
UNSEALED_RE = re.compile(r"^CHANGELOG-\d{4}-\d{2}-\d{2}\.md$")


def extract_descriptions(existing_content: str) -> dict[str, str | list[str]]:
    """
    Parse an existing changelog or manifest and return a dict mapping
    full relative filepath → description for any entry where the user
    has replaced [description] with actual text.

    Supports two formats:

        Single-line:
            - `path/to/file.md`: some description the user wrote

        Sub-bullet list:
            - `path/to/file.md`:
              - did one thing
              - did another thing

    Single-line entries are stored as str; sub-bullet entries as list[str].
    Entries still showing [description] or with no content are skipped.
    """
    descriptions: dict[str, str | list[str]] = {}
    lines = existing_content.splitlines()

    for i, line in enumerate(lines):
        m = re.match(r"^- `([^`]+)`:([ \t]*)(.*)$", line)
        if not m:
            continue

        filepath = m.group(1).strip()
        inline   = m.group(3).strip()

        if inline:
            if inline != "[description]":
                descriptions[filepath] = inline
        else:
            bullets: list[str] = []
            j = i + 1
            while j < len(lines):
                sub = re.match(r"^  - (.+)$", lines[j])
                if sub:
                    bullets.append(sub.group(1).strip())
                    j += 1
                else:
                    break
            if bullets:
                descriptions[filepath] = bullets

    return descriptions


def extract_user_content(existing_content: str) -> str | None:
    """
    Return everything after the archivist:auto-end sentinel, or None if
    the sentinel is not present (e.g. files generated before this feature).
    """
    if ARCHIVIST_AUTO_END not in existing_content:
        return None
    return existing_content.split(ARCHIVIST_AUTO_END, 1)[1]


def extract_changelog_title(existing_content: str) -> str | None:
    """
    Pull the custom title out of an existing changelog, if the user bothered
    to set one.

    Scans for the first `# ...` heading in the auto-generated block (before
    the archivist:auto-end sentinel, where we own the content). If the heading
    is the boring default — "Changelog" — we return None and let the builder
    regenerate it as usual. If the user renamed it to something actually
    descriptive, we return just the title text so it can survive the next
    rewrite.

    The heading is expected to look like:
        # Some Title — YYYY-MM-DD

    We strip the date suffix (everything from " — " onward) so the caller gets
    clean title text it can re-attach a fresh date to. If the heading has no
    date suffix, the whole thing after "# " is returned as-is.

    Returns None if no custom title is found or if the title is the default.
    """
    # Only look in the auto-generated section — don't go spelunking past the sentinel
    auto_block = (
        existing_content.split(ARCHIVIST_AUTO_END, 1)[0]
        if ARCHIVIST_AUTO_END in existing_content
        else existing_content
    )

    for line in auto_block.splitlines():
        m = re.match(r"^#\s+(.+)$", line)
        if not m:
            continue
        heading = m.group(1).strip()
        # Strip the date suffix if present (e.g. " — 2025-04-30")
        title = re.split(r"\s+[—–-]\s+\d{4}-\d{2}-\d{2}", heading)[0].strip()
        # The default is boring. Don't preserve it — let the builder regenerate it.
        if title.lower() == "changelog":
            return None
        return title

    return None


def resolve_changelog_title(ctx: "ChangelogContext", date: str) -> str:  # type: ignore[name-defined]
    """
    Return the heading line for a changelog body.

    Prefers ctx.custom_title (preserved from the existing file on re-runs)
    over the default "Changelog". Either way, the current date gets appended.

        # My Descriptive Title — 2025-04-30
        # Changelog — 2025-04-30   ← what you get if you can't be bothered

    Every build_body implementation should call this instead of hardcoding
    the heading string, or any title the user sets will get nuked on the next
    run. Don't be that asshole.
    """
    title = ctx.custom_title or "Changelog"
    return f"# {title} — {date}"


def find_active_changelog(output_dir: Path) -> Path | None:
    """Return the most recent unsealed (pre-commit) changelog in output_dir, or None.

    Unsealed changelogs match CHANGELOG-YYYY-MM-DD.md exactly — a date and
    nothing else before the extension. Post-commit sealed changelogs carry a
    SHA suffix (CHANGELOG-YYYY-MM-DD-{sha}.md) and are intentionally excluded.

    Returns the lexicographically greatest match, which is always the most
    recent date. No cap on how far back it will look — a working session that
    spans midnight rolls forward naturally rather than abandoning the existing
    file.
    """
    candidates = [
        p for p in output_dir.iterdir()
        if p.is_file() and UNSEALED_RE.match(p.name)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.name)


def find_changelog_output_dir(git_root: Path, module_type: str | None = None) -> Path:
    """
    Locate and return the changelog output directory.
    
    Priority:
      1. Check .archivist config for 'changelog-output-dir' (custom user path)
      2. Fall back to module-type-specific defaults:
         - story, publication → ARCHIVE/CHANGELOG/
         - general, library, vault → ARCHIVE/
    
    Creates the directory if it does not exist.
    """
    config = read_archivist_config(git_root)
    
    # Check config for custom output dir
    if config and "changelog-output-dir" in config:
        raw = config["changelog-output-dir"]
        if not isinstance(raw, str):
            raise TypeError(f"changelog-output-dir in .archivist must be a string, got {type(raw).__name__}")
        output_dir = git_root / raw
    
    else:
        # Fall back to module-type default — only when no custom path was provided.
        if module_type is None:
            module_type = get_module_type(git_root)
        
        if module_type in ("story", "publication"):
            output_dir = git_root / "ARCHIVE" / "CHANGELOG"
        else:
            output_dir = git_root / "ARCHIVE"
    
    output_dir.mkdir(parents = True, exist_ok = True)
    return output_dir


def find_todays_manifest(parent_dir: Path, edition_name: str) -> Path | None:
    """Return path to today's manifest for this edition if one exists, else None."""
    candidate = parent_dir / f"{edition_name}-manifest.md"
    return candidate if candidate.exists() else None


def format_file_list(
    files: list[str],
    fallback: str,
    descriptions: dict[str, str | list[str]],
    active_renames: dict[str, str] | None = None,
) -> str:
    """
    Format a list of files for changelog output with descriptions and rename tracking.

    For same-directory renames (filename changed, directory unchanged), the
    annotation shows just the old filename — the directory is already visible
    from the new path, no need to repeat it.

    For cross-directory moves (file jumped to a different directory, with or
    without a simultaneous name change), the annotation uses "moved from" and
    shows the full old path. "renamed from `file.md`" is a useless hint when
    the file came from three directories away and the reader has no way to know
    which `file.md` you're talking about without the full context.

    Args:
        files: List of file paths to format (new paths for renames)
        fallback: Text to show if files list is empty
        descriptions: Dict mapping filepath → description (str or list[str])
        active_renames: Dict mapping new_path → old_path for all renames/moves
    
    Returns:
        Formatted markdown string ready for inclusion in changelog body
    """
    if active_renames is None:
        active_renames = {}
    if not files:
        return f"- {fallback}\n"

    lines: list[str] = []
    for f in files:
        desc = descriptions.get(f, "[description]")
        old = active_renames.get(f)
        if old:
            suspicion = rename_suspicion(old, f)
            if is_cross_dir_move(old, f):
                # File moved directories — show both full paths so it's unambiguous
                # what came from where. "renamed from `note.md`" tells you nothing
                # when there are forty files called note.md scattered across the vault.
                rename_str = f" *(moved from `{rename_display_path(old, f)}`)*{suspicion}"
            else:
                # Same directory, name changed — old filename is enough context
                # since the new entry already shows the directory.
                rename_str = f" *(renamed from `{rename_display_path(old, f)}`)*{suspicion}"
        else:
            rename_str = ""

        if isinstance(desc, list):
            lines.append(f"- `{f}`{rename_str}:")
            for item in desc:
                lines.append(f"  - {item}")
            lines.append("")
        else:
            lines.append(f"- `{f}`{rename_str}: {desc}")

    return "\n".join(lines) + "\n"


def generate_changelog_uuid() -> str:
    """
    Generate a UUID for a new changelog.

    Written into frontmatter at creation time and used as the stable
    identifier until the changelog is sealed by the post-commit hook,
    at which point the commit SHA takes over as the permanent identifier.

    For publication changelogs, edition_shas.included_in holds this UUID
    until seal time, when seal_changelog_in_db() transitions it to the
    commit SHA. This is the mechanism that makes iterative re-runs on an
    unsealed changelog safe and correct.
    """
    return str(_uuid_module.uuid4())


def report_changes(
    changes: GitChanges,
    modified: list[str],
    true_deleted: list[str]
) -> None:
    """
    Print a human-readable summary of the staged diff.
    Called by every changelog subcommand after _get_git_changes() resolves.
    """
    total = len(changes["A"]) + len(modified) + len(true_deleted)
    if total == 0:
        print("  ⚠️  No staged changes found in the diff — changelog will be empty")
    else:
        print(
            f"  📋 Diff resolved: "
            f"{len(changes['A'])} added  |  "
            f"{len(modified)} modified  |  "
            f"{len(true_deleted)} archived"
        )
    if changes["R"]:
        print(f"     ↳ {len(changes['R'])} rename(s) detected")


def write_changelog(output_path: Path, content: str, existing: bool) -> None:
    """
    Write changelog content to disk with before/after messaging and safe
    error handling.

    - Prints the target path before attempting the write so a failure can
      be located immediately.
    - Wraps the write in a try/except so an OSError doesn't produce a raw
      traceback; exits with a clear message instead.
    - Prints a ✓ confirmation with the correct verb (written vs updated)
      only after a successful write.

    Call this instead of output_path.write_text() in every changelog and
    manifest subcommand.
    """
    verb = "Updating" if existing else "Creating"
    print(f"  ✏️  {verb}: {output_path}")
    try:
        output_path.write_text(content, encoding="utf-8")
    except OSError as e:
        print(f"❌  Failed to write changelog: {e}", file=sys.stderr)
        sys.exit(1)
    verb_past = "updated" if existing else "written"
    print(f"✓ Changelog {verb_past}: {output_path}")