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
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from archivist.utils import (
    clean_filename,
    detect_dir_renames,
    ensure_staged,
    extract_descriptions,
    extract_user_content,
    find_active_changelog,
    get_file_frontmatter,
    get_repo_root,
    read_archivist_config,
    reassign_deletions,
    rename_suspicion,
    report_changes,
    write_changelog,
)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _get_git_changes(commit_sha: str | None, path: Path | None = None) -> dict:
    pathspec = ["--", str(path)] if path is not None else []

    if commit_sha:
        cmd = ["git", "-c", "core.quotepath=false", "diff-tree",
               "--name-status", "-M", "-r", commit_sha] + pathspec
    else:
        cmd = ["git", "-c", "core.quotepath=false", "diff-index",
               "--cached", "--name-status", "-M", "HEAD"] + pathspec

    try:
        output = subprocess.check_output(cmd, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as e:
        print(f"Error running git command: {e}", file=sys.stderr)
        sys.exit(1)

    changes = {"M": [], "A": [], "D": [], "R": []}
    for line in output.strip().splitlines():
        if not line:
            continue
        parts = line.split("\t")
        status = parts[0].strip()[0]
        if status == "R" and len(parts) == 3:
            changes["R"].append((parts[1].strip(), parts[2].strip()))
        elif status in changes:
            changes[status].append(parts[-1].strip())

    return changes


def _get_project_name(git_root: Path) -> str:
    return git_root.name.lower().replace("'", "").replace(" ", "-")


def _find_output_dir(git_root: Path) -> Path:
    output_dir = git_root / "ARCHIVE"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


# ---------------------------------------------------------------------------
# Library analysis
# ---------------------------------------------------------------------------

WORK_STAGES = ("placeholder", "raw", "active", "processed", "shelved")


def _get_class(fm: dict) -> str:
    """Return the class field value, normalised to lowercase stripped string."""
    val = fm.get("class", "")
    if isinstance(val, list):
        return " ".join(str(v).strip().lower() for v in val)
    return str(val).strip().lower()


def _get_deleted_frontmatter(filepath: str, git_root: Path) -> dict:
    """
    Recover frontmatter from a file that has been deleted from the working tree
    by reading its last-committed content via `git show HEAD:<path>`.
    Returns an empty dict if the file can't be retrieved or has no frontmatter.
    """
    try:
        content = subprocess.check_output(
            ["git", "show", f"HEAD:{filepath}"],
            stderr=subprocess.PIPE, text=True, cwd=git_root,
        )
    except subprocess.CalledProcessError:
        return {}

    if not content.startswith("---"):
        return {}
    end = content.find("\n---", 3)
    if end == -1:
        return {}
    fm_text = content[3:end].strip()
    try:
        import yaml
        return yaml.safe_load(fm_text) or {}
    except Exception:
        return {}


def _process_file(
    filepath: str,
    git_root: Path,
    git_status: str,
    stats: dict,
    old_filepath: str | None = None,
) -> bool:
    """
    Read frontmatter from a single .md file and route it into the appropriate
    stats bucket. Returns True if the file was claimed by a named class,
    False if it should fall through to the generic sections.
    """
    full = git_root / filepath
    if full.suffix != ".md":
        return False

    if git_status == "D":
        fm = _get_deleted_frontmatter(filepath, git_root)
    else:
        fm = get_file_frontmatter(str(full))
    if not fm:
        return False

    cls = _get_class(fm)

    # Works — anything with a work-stage field
    if "work-stage" in fm:
        status = fm.get("work-stage", "")
        title = fm.get("sort-title") or fm.get("title") or full.stem
        if git_status == "R":
            stats["works"]["updated"].append((filepath, title, status, old_filepath))
        else:
            bucket = {"A": "added", "M": "updated"}.get(git_status)
            if bucket:
                if bucket == "updated":
                    stats["works"]["updated"].append((filepath, title, status, None))
                else:
                    stats["works"]["added"].append((filepath, title, status))
                if status in stats["works"]["by_status"]:
                    stats["works"]["by_status"][status] += 1
            else:
                stats["works"]["removed"].append(filepath)
        return True

    # Author cards
    if cls == "author":
        name = full.stem
        if git_status == "R":
            stats["authors"]["updated"].append((filepath, name, old_filepath))
        else:
            bucket = {"A": "added", "M": "updated"}.get(git_status)
            if bucket:
                if bucket == "updated":
                    stats["authors"]["updated"].append((filepath, name, None))
                else:
                    stats["authors"]["added"].append((filepath, name))
            else:
                stats["authors"]["removed"].append(filepath)
        return True

    # Publication cards (library publication only)
    if cls == "collection":
        name = full.stem
        if git_status == "R":
            stats["publications"]["updated"].append((filepath, name, old_filepath))
        else:
            bucket = {"A": "added", "M": "updated"}.get(git_status)
            if bucket:
                if bucket == "updated":
                    stats["publications"]["updated"].append((filepath, name, None))
                else:
                    stats["publications"]["added"].append((filepath, name))
            else:
                stats["publications"]["removed"].append(filepath)
        return True

    # Definition cards
    if cls == "entry":
        word = full.stem
        aliases = fm.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        if git_status == "R":
            stats["definitions"]["updated"].append((filepath, word, aliases, old_filepath))
        else:
            bucket = {"A": "added", "M": "updated"}.get(git_status)
            if bucket:
                if bucket == "updated":
                    stats["definitions"]["updated"].append((filepath, word, aliases, None))
                else:
                    stats["definitions"]["added"].append((filepath, word, aliases))
            else:
                stats["definitions"]["removed"].append(filepath)
        return True

    return False


def _analyse_catalog_changes(changes: dict, git_root: Path) -> dict:
    """
    Route changed .md files into named class buckets:
      works        — files with work-stage field, bucketed by status
      authors      — class: author
      publications — class: collection
      definitions  — class: entry (word + aliases surfaced)

    Anything not claimed by a named class falls through to generic sections.
    """
    stats = {
        "works": {
            "added":     [],   # (filepath, title, status)
            "updated":   [],   # (filepath, title, status, old_filepath|None)
            "removed":   [],
            "by_status": {s: 0 for s in WORK_STAGES},
        },
        "authors": {
            "added":   [],    # (filepath, name)
            "updated": [],    # (filepath, name, old_filepath|None)
            "removed": [],
        },
        "publications": {
            "added":   [],    # (filepath, name)
            "updated": [],    # (filepath, name, old_filepath|None)
            "removed": [],
        },
        "definitions": {
            "added":   [],    # (filepath, word, aliases)
            "updated": [],    # (filepath, word, aliases, old_filepath|None)
            "removed": [],
        },
    }

    for filepath in changes["A"]:
        _process_file(filepath, git_root, "A", stats)
    for filepath in changes["M"]:
        _process_file(filepath, git_root, "M", stats)
    for filepath in changes["D"]:
        _process_file(filepath, git_root, "D", stats)
    for old_path, new_path in changes["R"]:
        _process_file(new_path, git_root, "R", stats, old_filepath=old_path)

    return stats


# ---------------------------------------------------------------------------
# Catalog dashboard
# ---------------------------------------------------------------------------

def _get_works_dir(git_root: Path) -> Path:
    """
    Return the works directory from .archivist config, defaulting to 'works'.
    """
    config = read_archivist_config(git_root) or {}
    return git_root / config.get("works-dir", "works")


def _extract_author_name(val: str) -> str:
    """Strip Obsidian wikilink brackets from an author value."""
    val = val.strip()
    if val.startswith("[[") and val.endswith("]]"):
        return val[2:-2].strip()
    return val


def _get_previous_stage(filepath: str, git_root: Path) -> str | None:
    """
    Read work-stage from the last-committed version of a file.
    Returns None if the file is new or has no work-stage in HEAD.
    """
    try:
        content = subprocess.check_output(
            ["git", "show", f"HEAD:{filepath}"],
            stderr=subprocess.PIPE, text=True, cwd=git_root,
        )
    except subprocess.CalledProcessError:
        return None

    if not content.startswith("---"):
        return None
    end = content.find("\n---", 3)
    if end == -1:
        return None
    try:
        import yaml
        fm = yaml.safe_load(content[3:end].strip()) or {}
        val = fm.get("work-stage")
        return str(val).strip() if val else None
    except Exception:
        return None


def _scan_catalog(works_dir: Path, git_root: Path) -> dict:
    """
    Walk works_dir and build a full-catalog snapshot:
      stage_counts     — work count per work-stage across the entire catalog
      author_counts    — work count per author (wikilinks unwrapped)
      velocity         — {YYYY-MM: count} for date-consumed in rolling 12 months
      placeholder_debt — placeholder works with no date-consumed
      total_works      — total files with work-stage
    """
    from collections import defaultdict

    now = datetime.now()
    cutoff = datetime(now.year - 1, now.month, 1)

    stage_counts = {s: 0 for s in WORK_STAGES}
    author_counts = defaultdict(int)
    velocity = defaultdict(int)
    placeholder_debt = 0
    total_works = 0

    if not works_dir.exists():
        return {
            "stage_counts": stage_counts,
            "author_counts": {},
            "velocity": {},
            "placeholder_debt": 0,
            "total_works": 0,
        }

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
            name = _extract_author_name(str(a))
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
                if isinstance(d, str):
                    consumed = datetime.strptime(d[:10], "%Y-%m-%d")
                else:
                    # yaml may parse YYYY-MM-DD as a date object
                    from datetime import date as date_type
                    if isinstance(d, date_type):
                        consumed = datetime(d.year, d.month, d.day)
                    else:
                        continue
                if consumed >= cutoff:
                    key = consumed.strftime("%Y-%m")
                    velocity[key] += 1
            except (ValueError, AttributeError):
                continue

        # Placeholder debt
        if stage == "placeholder":
            has_consumed = any(
                bool(d) for d in (
                    [fm.get("date-consumed")]
                    if not isinstance(fm.get("date-consumed"), list)
                    else fm.get("date-consumed") or []
                )
            )
            if not has_consumed:
                placeholder_debt += 1

    return {
        "stage_counts": stage_counts,
        "author_counts": dict(sorted(author_counts.items(), key=lambda x: x[1], reverse=True)),
        "velocity": dict(sorted(velocity.items())),
        "placeholder_debt": placeholder_debt,
        "total_works": total_works,
    }


def _detect_throughput(changes: dict, git_root: Path) -> list:
    """
    Detect work-stage transitions among modified and renamed files this commit.
    Returns list of (title, old_stage, new_stage) for files where stage changed.
    """
    transitions = []

    candidates = [(fp, fp) for fp in changes["M"]] + \
                 [(old, new) for old, new in changes["R"]]

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
            transitions.append((title, old_stage, new_stage))

    return transitions


def _build_catalog_snapshot(snapshot: dict, throughput: list) -> str:
    """
    Build the ## Catalog Snapshot section as a static Mermaid-rendered string.
    All data is computed at generation time and frozen in the changelog.
    """
    stage_counts = snapshot["stage_counts"]
    author_counts = snapshot["author_counts"]
    velocity = snapshot["velocity"]
    debt = snapshot["placeholder_debt"]
    total = snapshot["total_works"]

    # --- Stage Distribution pie ---
    if total > 0:
        stage_entries = "\n".join(
            f'    "{s.capitalize()}" : {c}'
            for s, c in stage_counts.items() if c > 0
        )
        stage_chart = f"```mermaid\npie title Stage Distribution — {total} works\n{stage_entries}\n```"
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

    # --- Author Landscape pie — cap at 8 to avoid palette cycling ---
    AUTHOR_PIE_CAP = 8
    if author_counts:
        top = list(author_counts.items())[:AUTHOR_PIE_CAP]
        others = sum(c for _, c in list(author_counts.items())[AUTHOR_PIE_CAP:])
        if others:
            top.append((f"Others ({len(author_counts) - AUTHOR_PIE_CAP} authors)", others))
        author_entries = "\n".join(
            f'    "{name}" : {count}'
            for name, count in top
        )
        author_chart = f"```mermaid\npie title Author Landscape\n{author_entries}\n```"
    else:
        author_chart = "*No author data found.*"

    # --- Reading Velocity bar chart ---
    if velocity:
        # Build month range from oldest entry in window to today
        from datetime import date as date_type
        now = datetime.now()
        oldest_key = min(velocity.keys())
        oldest = datetime.strptime(oldest_key, "%Y-%m")

        months = []
        cur = oldest
        while cur <= now:
            months.append(cur.strftime("%Y-%m"))
            # Advance one month
            if cur.month == 12:
                cur = datetime(cur.year + 1, 1, 1)
            else:
                cur = datetime(cur.year, cur.month + 1, 1)

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
# Frontmatter builder
# ---------------------------------------------------------------------------

def _build_frontmatter(
    commit_sha: str | None,
    num_modified: int,
    num_added: int,
    num_archived: int,
    lib_stats: dict,
    git_root: Path,
) -> str:
    today = datetime.now().strftime("%Y-%m-%d")

    auto = {
        "class":               "archive",
        "category":            ["changelog"],
        "log-scope":           "library",
        "modified":            today,
        "commit-sha":          commit_sha or "",
        "files-modified":      num_modified,
        "files-created":       num_added,
        "files-archived":      num_archived,
        "works-added":         len(lib_stats["works"]["added"]),
        "works-updated":       len(lib_stats["works"]["updated"]),
        "works-removed":       len(lib_stats["works"]["removed"]),
        "authors-added":       len(lib_stats["authors"]["added"]),
        "authors-updated":     len(lib_stats["authors"]["updated"]),
        "publications-added":  len(lib_stats["publications"]["added"]),
        "definitions-added":   len(lib_stats["definitions"]["added"]),
        "tags":                [_get_project_name(git_root)],
    }

    def render_field(key, value):
        if isinstance(value, list):
            if not value:
                return [f"{key}: []"]
            return [f"{key}:"] + [f"  - {item}" for item in value]
        return [f"{key}: {value}"]

    lines = ["---"]
    for key, value in auto.items():
        lines.extend(render_field(key, value))
    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Body renderers
# ---------------------------------------------------------------------------

def _work_list(
    works: list,
    fallback: str,
    descriptions: dict = None
) -> str:
    if descriptions is None:
        descriptions = {}
    if not works:
        return f"- {fallback}\n"
    lines = []
    for entry in works:
        filepath, title, status = entry[0], entry[1], entry[2]
        old_filepath = entry[3] if len(entry) > 3 else None
        rename_str = (
            f" *(renamed from `{clean_filename(old_filepath)}`)*"
            f"{rename_suspicion(old_filepath, filepath)}"
            if old_filepath else ""
        )
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
    entries: list,
    fallback: str,
    descriptions: dict = None
) -> str:
    """Render (filepath, name[, old_filepath]) author/publication entries."""
    if descriptions is None:
        descriptions = {}
    if not entries:
        return f"- {fallback}\n"
    lines = []
    for entry in entries:
        filepath = entry[0]
        name = entry[1]
        old_filepath = entry[2] if len(entry) > 2 else None
        rename_str = (
            f" *(renamed from `{clean_filename(old_filepath)}`)*"
            f"{rename_suspicion(old_filepath, filepath)}"
            if old_filepath else ""
        )
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
    entries: list,
    fallback: str,
    descriptions: dict = None
) -> str:
    """Render (filepath, word, aliases[, old_filepath]) definition entries."""
    if descriptions is None:
        descriptions = {}
    if not entries:
        return f"- {fallback}\n"
    lines = []
    for entry in entries:
        filepath = entry[0]
        word, aliases = entry[1], entry[2]
        old_filepath = entry[3] if len(entry) > 3 else None
        alias_str = f" *(also: {', '.join(aliases)})*" if aliases else ""
        rename_str = (
            f" *(renamed from `{clean_filename(old_filepath)}`)*"
            f"{rename_suspicion(old_filepath, filepath)}"
            if old_filepath else ""
        )
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


def _removed_list(filepaths: list, fallback: str, descriptions: dict = None) -> str:
    if descriptions is None:
        descriptions = {}
    if not filepaths:
        return f"- {fallback}\n"
    lines = []
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


def _file_list(files: list, fallback: str, descriptions: dict = None) -> str:
    if descriptions is None:
        descriptions = {}
    if not files:
        return f"- {fallback}\n"
    lines = []
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
# Body builder
# ---------------------------------------------------------------------------

def _build_body(
    changes: dict,
    lib_stats: dict,
    commit_sha: str | None,
    descriptions: dict,
    user_content: str | None,
    snapshot_block: str,
) -> str:
    today = datetime.now().strftime("%Y-%m-%d")

    # Collect all claimed filepaths to exclude from generic sections
    claimed = set()
    for group in lib_stats.values():
        for bucket in ("added", "updated", "removed"):
            for entry in group.get(bucket, []):
                claimed.add(entry[0] if isinstance(entry, tuple) else entry)
                if isinstance(entry, tuple) and len(entry) >= 4 and entry[-1]:
                    claimed.add(entry[-1])  # old_filepath for renames

    other_added   = [f for f in changes["A"] if f not in claimed]
    other_updated = [f for f in changes["M"] if f not in claimed]
    other_removed = [f for f in changes["D"] if f not in claimed]

    works = lib_stats["works"]
    authors = lib_stats["authors"]
    pubs = lib_stats["publications"]
    defs = lib_stats["definitions"]
    by_status = works["by_status"]

    user_block = user_content if user_content is not None else """

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
{_file_list(other_added, "None", descriptions)}
### Files Modified
{_file_list(other_updated, "None", descriptions)}
### Files Removed
{_file_list(other_removed, "None", descriptions)}

<!-- archivist:auto-end -->
{user_block}
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    git_root = get_repo_root()
    print(f"  📁 Repo root : {git_root}")
    output_dir = _find_output_dir(git_root)
    print(f"  📁 Output dir: {output_dir}")

    if not args.dry_run:
        ensure_staged(None, git_root)

    changes = _get_git_changes(args.commit_sha)

    dir_renames = detect_dir_renames(changes["R"])
    true_deleted, dir_renamed_files = reassign_deletions(changes["D"], dir_renames)
    all_renames = changes["R"] + dir_renamed_files
    modified = changes["M"] + [new for _, new in all_renames]
    report_changes(changes, modified, true_deleted)

    num_modified = len(modified)
    num_added = len(changes["A"])
    num_archived = len(true_deleted)

    # Feed processed changes downstream so catalog analysis and throughput
    # detection see the corrected D and R lists
    processed_changes = {
        "M": changes["M"],
        "A": changes["A"],
        "D": true_deleted,
        "R": all_renames,
    }

    lib_stats = _analyse_catalog_changes(processed_changes, git_root)

    works_dir = _get_works_dir(git_root)
    snapshot = _scan_catalog(works_dir, git_root)
    throughput = _detect_throughput(processed_changes, git_root)
    snapshot_block = _build_catalog_snapshot(snapshot, throughput)

    today = datetime.now().strftime("%Y-%m-%d")
    output_path = output_dir / f"CHANGELOG-{today}.md"

    existing = find_active_changelog(output_dir)
    descriptions = {}
    user_content = None
    if existing:
        print(f"  🔍 Found existing changelog: {existing.name} — updating in place")
        existing_text = existing.read_text()
        descriptions = extract_descriptions(existing_text)
        user_content = extract_user_content(existing_text)
        output_path = existing
    else:
        print(f"  🆕 No existing changelog found — creating {output_path.name}")

    frontmatter = _build_frontmatter(
        args.commit_sha,
        num_modified, num_added, num_archived,
        lib_stats, git_root,
    )
    body = _build_body(
        processed_changes, lib_stats,
        args.commit_sha, descriptions, user_content,
        snapshot_block,
    )
    changelog_content = frontmatter + body

    if args.dry_run:
        print("=== DRY RUN — no file written ===\n")
        print(changelog_content)
        print(f"\n=== Would write to: {output_path} ===")
    else:
        write_changelog(output_path, changelog_content, existing=bool(existing))

    works = lib_stats["works"]
    authors = lib_stats["authors"]
    pubs = lib_stats["publications"]
    defs = lib_stats["definitions"]

    print(f"  Project       : {_get_project_name(git_root)}")
    print(f"  Works         : {len(works['added'])} added, {len(works['updated'])} updated, {len(works['removed'])} removed")
    print(f"  Status counts : placeholder={works['by_status']['placeholder']}, raw={works['by_status']['raw']}, active={works['by_status']['active']}, processed={works['by_status']['processed']}, shelved={works['by_status']['shelved']}")
    print(f"  Authors       : {len(authors['added'])} added, {len(authors['updated'])} updated, {len(authors['removed'])} removed")
    print(f"  Publications  : {len(pubs['added'])} added, {len(pubs['updated'])} updated, {len(pubs['removed'])} removed")
    print(f"  Definitions   : {len(defs['added'])} added, {len(defs['updated'])} updated, {len(defs['removed'])} removed")
    print(f"  Files total   : {num_added} added, {num_modified} modified, {num_archived} archived")
    if args.commit_sha:
        print(f"  SHA           : {args.commit_sha}")
    else:
        print("  SHA           : (staged — backfilled by post-commit hook)")
