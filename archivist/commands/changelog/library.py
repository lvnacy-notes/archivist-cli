"""
archivist changelog library

Generate a CHANGELOG-{date}.md for a library (catalog) module.
Tracks works added, updated, and removed alongside a Catalog Snapshot
dashboard — stage distribution, stage throughput, author landscape,
reading velocity, and placeholder debt — all computed at generation time
from the full works directory and written as static Mermaid charts.
Work-stage is the detection signal for catalogued works. Everything else
is generic file change tracking.

Invoked by:
    archivist changelog library

Scopes automatically to the current git repo (or submodule) root. The
works directory defaults to 'works/' and is configurable via 'works-dir'
in .archivist. Output is written to ARCHIVE/. Iterative command runs
will preserve user content and descriptions in the existing changelog
for that day, if present.
"""

import argparse
from collections import defaultdict
from datetime import datetime, date as date_type
from pathlib import Path
from typing import TypedDict, cast

import yaml

from archivist.commands.changelog.changelog_base import ChangelogContext, run_changelog
from archivist.utils import (
    GitChanges,
    clean_filename,
    get_file_frontmatter,
    get_file_from_git,
    get_project_name,
    get_today,
    is_cross_dir_move,
    matches_class_filter,
    read_archivist_config,
    rename_display_path,
    rename_suspicion,
    render_field,
)


# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

WORK_STAGES = ("placeholder", "raw", "active", "processed", "shelved")

# Cap the author landscape pie at this many slices before bucketing the
# rest into "Others". Beyond 8, Mermaid's palette starts cycling and
# looking like a unicorn had a seizure.
_AUTHOR_PIE_CAP = 8


# -----------------------------------------------------------------------------
# Library-specific types and interfaces
# -----------------------------------------------------------------------------

# Entries are tuples with varying lengths depending on operation
DefinitionEntry = tuple[str, str, list[str], str | None]  # filepath, word, aliases, old_filepath
EntityEntry = tuple[str, str, str | None]  # filepath, name, old_filepath
WorkEntry = tuple[str, str, str, str | None]  # filepath, title, status, old_filepath

# Catalog scan result structure for the full works directory snapshot. Used to
# build the Catalog Snapshot section with static Mermaid charts. This is a
# point-in-time snapshot of the catalog at generation time, not a live dashboard.
class CatalogScanResult(TypedDict):
    """
    Result of scanning the works directory for a catalog snapshot.
    """
    stage_counts: dict[str, int]
    author_counts: dict[str, int]
    velocity: dict[str, int]
    placeholder_debt: int
    total_works: int

class WorksBucket(TypedDict):
    """
    Stats bucket for works (files with work-stage).
    Entries are routed based on git status (A/M/D/R).
    Added entries: (filepath, title, status)
    Updated entries: (filepath, title, status, old_filepath|None)
    Removed entries: just filepaths (strings)
    by_status: count of each work-stage found during scan
    """
    added: list[WorkEntry]
    updated: list[WorkEntry]
    removed: list[str]
    by_status: dict[str, int]

class EntityBucket(TypedDict):
    """
    Stats bucket for authors (class: author) and publications (class: collection).
    Added entries: (filepath, name)
    Updated entries: (filepath, name, old_filepath|None)
    Removed entries: just filepaths (strings)
    """
    added: list[EntityEntry]
    updated: list[EntityEntry]
    removed: list[str]

class DefinitionsBucket(TypedDict):
    """
    Stats bucket for definitions (class: entry).
    Surfaces word and aliases from definition frontmatter.
    Added entries: (filepath, word, aliases)
    Updated entries: (filepath, word, aliases, old_filepath|None)
    Removed entries: just filepaths (strings)
    """
    added: list[DefinitionEntry]
    updated: list[DefinitionEntry]
    removed: list[str]

class LibraryStats(TypedDict):
    """
    Complete stats dict structure for library changelog generation.
    Routes changed .md files into named class buckets by git status.
    Each bucket tracks added, updated, removed operations separately.
    """
    works: WorksBucket
    authors: EntityBucket
    publications: EntityBucket
    definitions: DefinitionsBucket 


# Type alias for any stats bucket
AnyStatsBucket = WorksBucket | EntityBucket | DefinitionsBucket

# Type alias for entries across all bucket types
AnyEntry = WorkEntry | EntityEntry | DefinitionEntry | str

# -----------------------------------------------------------------------------
# Frontmatter Helpers
# -----------------------------------------------------------------------------

def _get_string_from_fm(value: str | list[str] | None) -> str:
    """
    Extract a string from frontmatter value that might be a string, list, or None.
    If it's a list, return the first element. If it's None, return empty string.
    Useful for fields like title, work-stage that should be strings but might be lists.
    """
    if isinstance(value, list):
        return value[0] if value else ""
    return value if value else ""


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _get_committed_frontmatter(
    filepath: str,
    git_root: Path,
    ref: str = "HEAD"
) -> dict[str, str | list[str]]:
    """
    Recover frontmatter from a file at the given git ref by reading its
    committed content via get_file_from_git().

    Returns an empty dict if the file can't be retrieved, has no frontmatter,
    or the YAML is unparseable. Used for deleted files (ref=HEAD) and
    stage-transition detection where we need the *previous* state.
    """
    content = get_file_from_git(filepath, git_root, ref)
    if content is None:
        return {}

    if not content.startswith("---"):
        return {}
    end = content.find("\n---", 3)
    if end == -1:
        return {}
    try:
        return yaml.safe_load(content[3:end].strip()) or {}
    except Exception:
        return {}


def _get_previous_stage(filepath: str, git_root: Path) -> str | None:
    """
    Read work-stage from the last-committed version of a file.
    Returns None if the file is new or carried no work-stage at HEAD.
    """
    fm = _get_committed_frontmatter(filepath, git_root)
    val = fm.get("work-stage")
    return str(val).strip() if val else None


# ---------------------------------------------------------------------------
# Library analysis
# ---------------------------------------------------------------------------

def _route_entity(
    filepath: str,
    name: str,
    git_status: str,
    old_filepath: str | None,
    bucket: EntityBucket,
) -> None:
    """
    Drop an author or publication entity into its stats bucket.
    Extracted because authors and publications follow identical routing logic.
    """
    if git_status == "R":
        bucket["updated"].append((filepath, name, old_filepath))
    elif git_status == "A":
        bucket["added"].append((filepath, name, None))
    elif git_status == "M":
        bucket["updated"].append((filepath, name, None))
    else:  # D
        bucket["removed"].append(filepath)


def _route_file_into_stats(
    filepath: str,
    git_root: Path,
    git_status: str,
    stats: LibraryStats,
    old_filepath: str | None = None,
) -> bool:
    """
    Read frontmatter from a single .md file and drop it into the right
    stats bucket. Returns True if claimed by a named class (works, authors,
    publications, definitions), False if it should fall through to the
    generic other-files sections.

    `git_status` is one of "A", "M", "D", "R".
    `old_filepath` is only provided for renames.
    """
    full = git_root / filepath
    if full.suffix != ".md":
        return False

    fm = (
        _get_committed_frontmatter(filepath, git_root)
        if git_status == "D"
        else get_file_frontmatter(str(full))
    )
    if not fm:
        return False

    # Works — anything carrying a work-stage field, regardless of class value
    if "work-stage" in fm:
        status = _get_string_from_fm(fm.get("work-stage"))
        title = _get_string_from_fm(fm.get("sort-title")) or _get_string_from_fm(fm.get("title")) or full.stem
        if git_status == "R":
            stats["works"]["updated"].append((filepath, title, status, old_filepath))
        elif git_status == "A":
            stats["works"]["added"].append((filepath, title, status, None))
            if status in stats["works"]["by_status"]:
                stats["works"]["by_status"][status] += 1
        elif git_status == "M":
            stats["works"]["updated"].append((filepath, title, status, None))
            if status in stats["works"]["by_status"]:
                stats["works"]["by_status"][status] += 1
        else:  # D
            stats["works"]["removed"].append(filepath)
        return True

    if matches_class_filter(fm, "author"):
        _route_entity(filepath, full.stem, git_status, old_filepath, stats["authors"])
        return True

    if matches_class_filter(fm, "collection"):
        _route_entity(filepath, full.stem, git_status, old_filepath, stats["publications"])
        return True

    if matches_class_filter(fm, "entry"):
        raw_aliases = fm.get("aliases") or []
        aliases = [raw_aliases] if isinstance(raw_aliases, str) else list(raw_aliases)
        if git_status == "R":
            stats["definitions"]["updated"].append((filepath, full.stem, aliases, old_filepath))
        elif git_status == "A":
            stats["definitions"]["added"].append((filepath, full.stem, aliases, None))
        elif git_status == "M":
            stats["definitions"]["updated"].append((filepath, full.stem, aliases, None))
        else:  # D
            stats["definitions"]["removed"].append(filepath)
        return True

    return False


def _analyse_catalog_changes(changes: GitChanges, git_root: Path) -> LibraryStats:
    """
    Route all changed .md files into named class buckets:
      works        — files carrying work-stage, bucketed by status
      authors      — class: author
      publications — class: collection
      definitions  — class: entry (word + aliases surfaced)

    Files not claimed by any named class fall through to the generic
    sections in _build_body(). Pass processed_changes, not raw git changes.
    """
    stats = LibraryStats(
        works = {
            "added": [],   # (filepath, title, status)
            "updated": [],   # (filepath, title, status, old_filepath|None)
            "removed": [],
            "by_status": {s: 0 for s in WORK_STAGES},
        },
        authors = {
            "added": [],    # (filepath, name)
            "updated": [],    # (filepath, name, old_filepath|None)
            "removed": [],
        },
        publications = {
            "added": [],    # (filepath, name)
            "updated": [],    # (filepath, name, old_filepath|None)
            "removed": [],
        },
        definitions = {
            "added": [],    # (filepath, word, aliases)
            "updated": [],    # (filepath, word, aliases, old_filepath|None)
            "removed": [],
        },
    )

    for fp in changes["A"]:
        _route_file_into_stats(fp, git_root, "A", stats)
    for fp in changes["M"]:
        _route_file_into_stats(fp, git_root, "M", stats)
    for fp in changes["D"]:
        _route_file_into_stats(fp, git_root, "D", stats)
    for old_path, new_path in changes["R"]:
        _route_file_into_stats(new_path, git_root, "R", stats, old_filepath=old_path)

    return stats


# ---------------------------------------------------------------------------
# Catalog snapshot
# ---------------------------------------------------------------------------

def _get_works_dir(git_root: Path) -> Path:
    """Return the works directory from .archivist config, defaulting to 'works'."""
    config = read_archivist_config(git_root) or {}
    return git_root / config.get("works-dir", "works")


def _unwrap_wikilink(val: str) -> str:
    """Strip Obsidian wikilink brackets from a string value."""
    val = val.strip()
    return val[2:-2].strip() if val.startswith("[[") and val.endswith("]]") else val


def _scan_catalog(works_dir: Path) -> CatalogScanResult:
    """
    Walk works_dir and build a full-catalog snapshot:
      stage_counts     — work count per work-stage across the entire catalog
      author_counts    — work count per author (wikilinks unwrapped)
      velocity         — {YYYY-MM: count} for date-consumed in rolling 12 months
      placeholder_debt — placeholder works with no date-consumed
      total_works      — total files with work-stage

    Returns a zero-value snapshot dict if works_dir doesn't exist rather
    than exploding. Don't be precious about missing directories.
    """
    now = datetime.now()
    cutoff = datetime(now.year - 1, now.month, 1)

    stage_counts = {s: 0 for s in WORK_STAGES}
    author_counts: dict[str, int] = defaultdict(int)
    velocity: dict[str, int] = defaultdict(int)
    placeholder_debt: int = 0
    total_works: int = 0

    if not works_dir.exists():
        return CatalogScanResult(
            stage_counts = stage_counts,
            author_counts = {},
            velocity = {},
            placeholder_debt = 0,
            total_works = 0,
        )

    for md_file in works_dir.rglob("*.md"):
        fm = get_file_frontmatter(str(md_file))
        if not fm or "work-stage" not in fm:
            continue

        total_works += 1
        stage = str(fm.get("work-stage", "")).strip()
        if stage in stage_counts:
            stage_counts[stage] += 1

        # Author counts
        raw_authors = fm.get("authors") or []
        if isinstance(raw_authors, str):
            raw_authors = [raw_authors]
        for a in raw_authors:
            name = _unwrap_wikilink(str(a))
            if name:
                author_counts[name] += 1

        # Reading velocity — date-consumed, rolling 12 months
        raw_dates = fm.get("date-consumed") or []
        if not isinstance(raw_dates, list):
            raw_dates = [raw_dates]
        for d in raw_dates:
            if not d:
                continue
            try:
                consumed = (
                    datetime(d.year, d.month, d.day)
                    if isinstance(d, date_type)
                    else datetime.strptime(str(d)[:10], "%Y-%m-%d")
                )
                if consumed >= cutoff:
                    velocity[consumed.strftime("%Y-%m")] += 1
            except (ValueError, AttributeError):
                continue

        # Placeholder debt
        if stage == "placeholder":
            consumed_val = fm.get("date-consumed")
            has_consumed = bool(
                any(consumed_val) if isinstance(consumed_val, list) else consumed_val
            )
            if not has_consumed:
                placeholder_debt += 1

    return CatalogScanResult(
        stage_counts = stage_counts,
        author_counts = dict(
            sorted(
                author_counts.items(),
                key = lambda kv: kv[1],
                reverse = True
            )
        ),
        velocity = dict(sorted(velocity.items())),
        placeholder_debt = placeholder_debt,
        total_works = total_works,
    )


def _detect_throughput(changes: GitChanges, git_root: Path) -> list[tuple[str, str, str]]:
    """
    Detect work-stage transitions among modified and renamed files this commit.
    Returns list of (title, old_stage, new_stage) for files where stage changed.
    """
    transitions: list[tuple[str, str, str]] = []
    candidates = (
        [(fp, fp) for fp in changes["M"]]
        + [(old, new) for old, new in changes["R"]]
    )
    for old_path, new_path in candidates:
        full = git_root / new_path
        if full.suffix != ".md":
            continue
        fm = get_file_frontmatter(str(full))
        if not fm or "work-stage" not in fm:
            continue
        new_stage = str(fm.get("work-stage", "")).strip()
        old_stage = _get_previous_stage(old_path, git_root)
        if old_stage and old_stage != new_stage:
            title = fm.get("sort-title") or fm.get("title") or full.stem
            transitions.append((str(title), old_stage, new_stage))
    return transitions


def _build_catalog_snapshot(snapshot: CatalogScanResult, throughput: list[tuple[str, str, str]]) -> str:
    """
    Build the ## Catalog Snapshot section as a static Mermaid-rendered string.
    All data is computed at generation time and frozen in the changelog — this
    is a point-in-time record, not a live dashboard.
    """
    stage_counts = snapshot["stage_counts"]
    author_counts = snapshot["author_counts"]
    velocity = snapshot["velocity"]
    debt = snapshot["placeholder_debt"]
    total: int = snapshot["total_works"]

    # --- Stage Distribution pie ---
    if total > 0:
        stage_entries = "\n".join(
            f'    "{s.capitalize()}" : {c}'
            for s, c in stage_counts.items() if c > 0
        )
        stage_chart = (
            f"```mermaid\npie title Stage Distribution — {total} works\n"
            f"{stage_entries}\n```"
        )
    else:
        stage_chart = "*No works found in catalog.*"

    # --- Throughput table ---
    if throughput:
        rows = "\n".join(
            f"| **{title}** | `{old}` | → | `{new}` |"
            for title, old, new in throughput
        )
        throughput_block = (
            "| Work | From | | To |\n"
            "|------|------|-|----|\n"
            f"{rows}"
        )
    else:
        throughput_block = "*No stage transitions this commit.*"

    if author_counts:
        top = list(author_counts.items())[:_AUTHOR_PIE_CAP]
        overflow_count = len(author_counts) - _AUTHOR_PIE_CAP
        others_total = sum(c for _, c in list(author_counts.items())[_AUTHOR_PIE_CAP:])
        if others_total:
            top.append((f"Others ({overflow_count} authors)", others_total))
        author_entries = "\n".join(
            f'    "{name}" : {count}' for name, count in top
        )
        author_chart = f"```mermaid\npie title Author Landscape\n{author_entries}\n```"
    else:
        author_chart = "*No author data found.*"

    # --- Reading Velocity bar chart ---
    if velocity:
        now = datetime.now()
        oldest = datetime.strptime(min(velocity.keys()), "%Y-%m")

        months: list[str] = []
        cur = oldest
        while cur <= now:
            months.append(cur.strftime("%Y-%m"))
            cur = (
                datetime(cur.year + 1, 1, 1)
                if cur.month == 12
                else datetime(cur.year, cur.month + 1, 1)
            )

        labels = [datetime.strptime(m, "%Y-%m").strftime("%b %y") for m in months]
        values = [velocity.get(m, 0) for m in months]
        max_val = max(values) if values else 1

        label_str = "[" + ", ".join(f'"{l}"' for l in labels) + "]"
        value_str = "[" + ", ".join(str(v) for v in values) + "]"

        velocity_chart = (
            "```mermaid\n"
            "xychart-beta\n"
            '    title "Reading Velocity — rolling 12 months"\n'
            f"    x-axis {label_str}\n"
            f'    y-axis "Works" 0 --> {max_val}\n'
            f"    bar {value_str}\n"
            "```"
        )
    else:
        velocity_chart = "*No reads recorded in this period.*"

    return f"""
## Catalog Snapshot

### Stage Distribution

{stage_chart}

### Throughput

{throughput_block}

### Author Landscape

{author_chart}

### Reading Velocity

{velocity_chart}

### Placeholder Debt

{debt} work{"s" if debt != 1 else ""} at `placeholder` stage with no `date-consumed`.
"""


# ---------------------------------------------------------------------------
# Post-changes hook
# ---------------------------------------------------------------------------

def _analyse_catalog(ctx: ChangelogContext) -> None:
    """
    Analyse the diff for catalog-specific content and build the snapshot.
    Runs after the base runner has processed renames. Stores results in
    ctx.data for use by _build_frontmatter() and _build_body().
    """
    ctx.data["lib_stats"] = _analyse_catalog_changes(ctx.processed_changes, ctx.git_root)
    works_dir = _get_works_dir(ctx.git_root)
    snapshot: CatalogScanResult = _scan_catalog(works_dir)
    throughput = _detect_throughput(ctx.processed_changes, ctx.git_root)
    ctx.data["snapshot_block"] = _build_catalog_snapshot(snapshot, throughput)


# ---------------------------------------------------------------------------
# Shared annotation helper
# ---------------------------------------------------------------------------

def _build_rename_annotation(old_filepath: str | None, filepath: str) -> str:
    """
    Return the move/rename annotation string for a library entry.

    Mirrors the logic in format_file_list() — same two-verb contract:
      "renamed from" — same-directory name change, shows old filename only.
      "moved from"   — file crossed directories, shows full old path.

    Called by _work_list, _entity_list, and _definition_list so we're not
    copy-pasting the same fucking branch three times.
    """
    if old_filepath is None:
        return ""
    suspicion = rename_suspicion(old_filepath, filepath)
    if is_cross_dir_move(old_filepath, filepath):
        return f" *(moved from `{rename_display_path(old_filepath, filepath)}`)*{suspicion}"
    return f" *(renamed from `{rename_display_path(old_filepath, filepath)}`)*{suspicion}"


# ---------------------------------------------------------------------------
# Body renderers
# ---------------------------------------------------------------------------

def _work_list(
    works: list[WorkEntry],
    fallback: str,
    descriptions: dict[str, str | list[str]]
) -> str:
    """Render (filepath, title, status[, old_filepath]) work entries."""
    if not works:
        return f"- {fallback}\n"
    lines: list[str] = []
    for entry in works:
        filepath, title, status = entry[0], entry[1], entry[2]
        old_filepath = entry[3] if len(entry) > 3 else None
        rename_str = _build_rename_annotation(old_filepath, filepath)
        desc = descriptions.get(filepath)
        if desc is None:
            lines.append(f"- **{title}** — `{status}`{rename_str} — [description]\n")
        elif isinstance(desc, list):
            lines.append(f"- **{title}** — `{status}`{rename_str}:\n")
            for item in desc:
                lines.append(f"  - {item}\n")
            lines.append("\n")
        else:
            lines.append(f"- **{title}** — `{status}`{rename_str} — {desc}\n")
    return "".join(lines)


def _entity_list(
    entries: list[EntityEntry],
    fallback: str,
    descriptions: dict[str, str | list[str]]
) -> str:
    """Render (filepath, name[, old_filepath]) author or publication entries."""
    if not entries:
        return f"- {fallback}\n"
    lines: list[str] = []
    for entry in entries:
        filepath, name = entry[0], entry[1]
        old_filepath = entry[2] if len(entry) > 2 else None
        rename_str = _build_rename_annotation(old_filepath, filepath)
        desc = descriptions.get(filepath)
        if desc is None:
            lines.append(f"- **{name}**{rename_str} — [description]\n")
        elif isinstance(desc, list):
            lines.append(f"- **{name}**{rename_str}:\n")
            for item in desc:
                lines.append(f"  - {item}\n")
            lines.append("\n")
        else:
            lines.append(f"- **{name}**{rename_str} — {desc}\n")
    return "".join(lines)


def _definition_list(
    entries: list[DefinitionEntry],
    fallback: str,
    descriptions: dict[str, str | list[str]]
) -> str:
    """Render (filepath, word, aliases[, old_filepath]) definition entries."""
    if not entries:
        return f"- {fallback}\n"
    lines: list[str] = []
    for entry in entries:
        filepath, word, aliases = entry[0], entry[1], entry[2]
        old_filepath = entry[3] if len(entry) > 3 else None
        alias_str = f" *(also: {', '.join(aliases)})*" if aliases else ""
        rename_str = _build_rename_annotation(old_filepath, filepath)
        desc = descriptions.get(filepath)
        if desc is None:
            lines.append(f"- **{word}**{alias_str}{rename_str} — [description]\n")
        elif isinstance(desc, list):
            lines.append(f"- **{word}**{alias_str}{rename_str}:\n")
            for item in desc:
                lines.append(f"  - {item}\n")
            lines.append("\n")
        else:
            lines.append(f"- **{word}**{alias_str}{rename_str} — {desc}\n")
    return "".join(lines)


def _removed_list(
    filepaths: list[str],
    fallback: str,
    descriptions: dict[str, str | list[str]]
) -> str:
    if not filepaths:
        return f"- {fallback}\n"
    lines: list[str] = []
    for f in filepaths:
        name = clean_filename(f)
        desc = descriptions.get(f)
        if desc is None:
            lines.append(f"- `{name}` — [description]\n")
        elif isinstance(desc, list):
            lines.append(f"- `{name}`:\n")
            for item in desc:
                lines.append(f"  - {item}\n")
            lines.append("\n")
        else:
            lines.append(f"- `{name}` — {desc}\n")
    return "".join(lines)


def _other_file_list(
    files: list[str],
    fallback: str,
    descriptions: dict[str, str | list[str]]
) -> str:
    if not files:
        return f"- {fallback}\n"
    lines: list[str] = []
    for f in files:
        desc = descriptions.get(f, "[description]")
        if isinstance(desc, list):
            lines.append(f"- `{f}`:")
            for item in desc:
                lines.append(f"  - {item}")
            lines.append("")
        else:
            lines.append(f"- `{f}`: {desc}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _build_frontmatter(ctx: ChangelogContext) -> str:
    lib_stats: LibraryStats = cast(LibraryStats, ctx.data["lib_stats"])
    today = get_today()
    auto = {
        "class": "archive",
        "category": ["changelog"],
        "log-scope": "library",
        "modified": today,
        "UUID": ctx.changelog_uuid,
        "commit-sha": ctx.args.commit_sha or "",
        "files-modified": len(ctx.modified),
        "files-created": len(ctx.changes["A"]),
        "files-archived": len(ctx.true_deleted),
        "works-added": len(lib_stats["works"]["added"]),
        "works-updated": len(lib_stats["works"]["updated"]),
        "works-removed": len(lib_stats["works"]["removed"]),
        "authors-added": len(lib_stats["authors"]["added"]),
        "authors-updated": len(lib_stats["authors"]["updated"]),
        "publications-added": len(lib_stats["publications"]["added"]),
        "definitions-added": len(lib_stats["definitions"]["added"]),
        "tags": [get_project_name(ctx.git_root)],
    }
    lines = ["---"]
    for key, value in auto.items():
        lines.extend(render_field(key, value))
    lines.append("---")
    return "\n".join(lines)


def _build_body(ctx: ChangelogContext) -> str:
    lib_stats: LibraryStats = cast(LibraryStats, ctx.data["lib_stats"])
    snapshot_block: CatalogScanResult = cast(CatalogScanResult, ctx.data["snapshot_block"])
    descriptions = ctx.descriptions or {}
    commit_sha = ctx.args.commit_sha
    today = get_today()

    # Collect claimed filepaths to exclude from the generic sections
    claimed: set[str] = set()
    for bucket in lib_stats.values():
        group: AnyStatsBucket = cast(AnyStatsBucket, bucket)
        for bucket_key in ("added", "updated", "removed"):
            entries: list[AnyEntry] = cast(list[AnyEntry], group.get(bucket_key, []))
            for entry in entries:
                claimed.add(entry[0] if isinstance(entry, tuple) else entry)
                if isinstance(entry, tuple) and len(entry) >= 4 and entry[-1]:
                    claimed.add(entry[-1])

    other_added = [f for f in ctx.processed_changes["A"] if f not in claimed]
    other_updated = [f for f in ctx.processed_changes["M"] if f not in claimed]
    other_removed = [f for f in ctx.processed_changes["D"] if f not in claimed]

    works = lib_stats["works"]
    authors = lib_stats["authors"]
    pubs = lib_stats["publications"]
    defs = lib_stats["definitions"]
    by_status = works["by_status"]

    user_block = ctx.user_content if ctx.user_content is not None else """
## Notes


---

*This changelog was automatically generated by Archivist CLI.*
*See [Archivist CLI](https://github.com/lvnacy-notes/archivist-cli) for more information.*

"""

    return f"""

# Changelog — {today}

## Overview

| Field | Value |
|-------|-------|
| Date | {today} |
| Commit SHA | {commit_sha or "[fill in after commit]"} |
| Works Added | {len(works["added"])} |
| Works Updated | {len(works["updated"])} |
| Works Removed | {len(works["removed"])} |
| Authors Added | {len(authors["added"])} |
| Authors Updated | {len(authors["updated"])} |
| Publications Added | {len(pubs["added"])} |
| Publications Updated | {len(pubs["updated"])} |
| Definitions Added | {len(defs["added"])} |
| Definitions Updated | {len(defs["updated"])} |
| Other Files Added | {len(other_added)} |
| Other Files Modified | {len(other_updated)} |
{snapshot_block}
## Status Summary

| Status | Count |
|--------|-------|
| Placeholder | {by_status["placeholder"]} |
| Raw | {by_status["raw"]} |
| Active | {by_status["active"]} |
| Processed | {by_status["processed"]} |
| Shelved | {by_status["shelved"]} |

## Catalog Changes

### Works Added
{_work_list(works["added"], "No works added", descriptions)}
### Works Updated
{_work_list(works["updated"], "No works updated", descriptions)}
### Works Removed
{_removed_list(works["removed"], "No works removed", descriptions)}
## Author Cards

### Added
{_entity_list(authors["added"], "No author cards added", descriptions)}
### Updated
{_entity_list(authors["updated"], "No author cards updated", descriptions)}
### Removed
{_removed_list(authors["removed"], "No author cards removed", descriptions)}
## Publication Cards

### Added
{_entity_list(pubs["added"], "No publication cards added", descriptions)}
### Updated
{_entity_list(pubs["updated"], "No publication cards updated", descriptions)}
### Removed
{_removed_list(pubs["removed"], "No publication cards removed", descriptions)}
## Definitions

### Added
{_definition_list(defs["added"], "No definitions added", descriptions)}
### Updated
{_definition_list(defs["updated"], "No definitions updated", descriptions)}
### Removed
{_removed_list(defs["removed"], "No definitions removed", descriptions)}
## Other File Changes

### Files Added
{_other_file_list(other_added, "None", descriptions)}
### Files Modified
{_other_file_list(other_updated, "None", descriptions)}
### Files Removed
{_other_file_list(other_removed, "None", descriptions)}

<!-- archivist:auto-end -->
{user_block}
"""


def _print_summary(ctx: ChangelogContext) -> None:
    lib_stats: LibraryStats = cast(LibraryStats, ctx.data["lib_stats"])
    works = lib_stats["works"]
    authors = lib_stats["authors"]
    pubs = lib_stats["publications"]
    defs = lib_stats["definitions"]
    by_status = works["by_status"]

    print(f"  Project       : {get_project_name(ctx.git_root)}")
    print(
        f"  Works         : {len(works['added'])} added, "
        f"{len(works['updated'])} updated, {len(works['removed'])} removed"
    )
    print(
        f"  Status counts : "
        + ", ".join(f"{s}={by_status[s]}" for s in WORK_STAGES)
    )
    print(
        f"  Authors       : {len(authors['added'])} added, "
        f"{len(authors['updated'])} updated, {len(authors['removed'])} removed"
    )
    print(
        f"  Publications  : {len(pubs['added'])} added, "
        f"{len(pubs['updated'])} updated, {len(pubs['removed'])} removed"
    )
    print(
        f"  Definitions   : {len(defs['added'])} added, "
        f"{len(defs['updated'])} updated, {len(defs['removed'])} removed"
    )
    print(
        f"  Files total   : {len(ctx.changes['A'])} added, "
        f"{len(ctx.modified)} modified, {len(ctx.true_deleted)} archived"
    )
    if ctx.args.commit_sha:
        print(f"  SHA           : {ctx.args.commit_sha}")
    else:
        print("  SHA           : (staged — backfilled by post-commit hook)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    run_changelog(
        args,
        module_type = "library",
        build_frontmatter = _build_frontmatter,
        build_body = _build_body,
        post_changes = _analyse_catalog,
        print_summary = _print_summary,
    )