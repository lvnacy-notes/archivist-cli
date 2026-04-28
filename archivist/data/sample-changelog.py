"""
sample-changelog.py — Archivist changelog plugin reference

Rename this file to `changelog.py` and it becomes active. That's it.
Archivist finds it, loads it, and runs it instead of the built-in library
changelog. No config changes. No re-running init. No ceremony. Delete it
or rename it back to revert instantly.

This file is a working, unmodified reproduction of the standard library
changelog. It runs correctly before you change a single line. Start from
something that works, break it on purpose, and you'll know exactly what
you did.

─────────────────────────────────────────────────────────────────────────
THE CONTRACT

Your plugin must expose exactly one thing:

    def run(args: argparse.Namespace) -> None

That function calls run_changelog() with your builder callables. Everything
else in this file is yours.

─────────────────────────────────────────────────────────────────────────
THE PUBLIC API

Four functions from the library module are intentionally public and stable.
Don't reach past them into anything prefixed with `_` — those are internal
and will change without notice.

    analyse_catalog(ctx) -> None
        post_changes hook. Populate ctx.data before content is built.
        After this runs, ctx.data contains:

            ctx.data["lib_stats"]       LibraryStats TypedDict
            ctx.data["snapshot_block"]  str — pre-formatted ## Catalog
                                        Snapshot section (Mermaid charts)

        LibraryStats shape:
            {
                "works":        WorksBucket,
                "authors":      EntityBucket,
                "publications": EntityBucket,
                "definitions":  DefinitionsBucket,
            }

        Each bucket has:
            "added":   list of tuples
            "updated": list of tuples
            "removed": list[str] (filepaths only)

        Tuple shapes by bucket:
            works:        (filepath, title, status, old_filepath | None)
            authors:      (filepath, name, old_filepath | None)
            publications: (filepath, name, old_filepath | None)
            definitions:  (filepath, word, aliases, old_filepath | None)

        If you're adding new content categories, do your analysis after
        analyse_catalog() and store results under new ctx.data keys.
        Don't clobber "lib_stats" or "snapshot_block" unless you're
        replacing the sections that read from them.

    build_frontmatter(ctx) -> str
        YAML frontmatter block. Reads ctx.data["lib_stats"].
        Replace entirely if your frontmatter structure diverges;
        otherwise just call it.

    build_body(ctx) -> str
        Full changelog body: Overview table, Catalog Snapshot, Status
        Summary, Catalog Changes, Author Cards, Publication Cards,
        Definitions, Other File Changes, and the archivist:auto-end
        sentinel. Reads both ctx.data["lib_stats"] and
        ctx.data["snapshot_block"].

        To append sections after the standard body:
            return build_body(ctx) + _my_sections(ctx)

        To insert before the sentinel, you'll need to replace build_body
        entirely and emit ARCHIVIST_AUTO_END yourself:
            from archivist.utils import ARCHIVIST_AUTO_END

        Do not omit the sentinel. Everything before it is regenerated on
        every run; everything after is preserved. That boundary is
        load-bearing. Don't fuck with it.

    print_summary(ctx) -> None
        Terminal summary printed after the write. Reads lib_stats.
        Wrap it, replace it, or drop it from run_changelog() entirely
        to fall back to the base runner's minimal default.

─────────────────────────────────────────────────────────────────────────
CHANGELOGCONTEXT — FIELDS YOU ACTUALLY CARE ABOUT

ctx.args               argparse.Namespace — dry_run, commit_sha, path
ctx.git_root           Path — repo root
ctx.output_dir         Path — where the changelog file will be written
ctx.changes            GitChanges — raw git diff (M, A, D, R lists)
ctx.processed_changes  GitChanges — D and R normalised after rename detection
ctx.modified           list[str] — M + new paths of renamed files
ctx.true_deleted       list[str] — D after directory-rename reassignment
ctx.renames            dict[str, str] — {new_path: old_path} all renames
ctx.moved_files        dict[str, str] — {new_path: old_path} cross-dir only
ctx.descriptions       dict[str, str | list[str]] — preserved user descriptions
ctx.user_content       str | None — everything below archivist:auto-end
ctx.changelog_uuid     str — stable UUID for this changelog until sealed
ctx.data               dict[str, object] — populated by post_changes hooks

─────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import argparse
from typing import cast

# Public builders from the library module. Call them, wrap them, or replace
# them. Don't import anything prefixed with `_` — that's not the deal.
from archivist.commands.changelog.library import (
    LibraryStats,
    analyse_catalog,
    build_body,
    build_frontmatter,
    print_summary,
)
from archivist.commands.changelog.changelog_base import (
    ChangelogContext,
    run_changelog,
)


# ---------------------------------------------------------------------------
# post_changes hook
# ---------------------------------------------------------------------------
# Called after rename detection, before any content is built. Populate
# ctx.data with whatever your builders need.
#
# This implementation calls the standard library analysis and nothing else.
# To add new content categories, do your work after analyse_catalog() and
# store results under new ctx.data keys:
#
#     def _post_changes(ctx: ChangelogContext) -> None:
#         analyse_catalog(ctx)
#         ctx.data["domain_files"] = _sort_by_domain(ctx)
#
# Then read ctx.data["domain_files"] in your _build_body().
# ---------------------------------------------------------------------------

def _post_changes(ctx: ChangelogContext) -> None:
    analyse_catalog(ctx)


# ---------------------------------------------------------------------------
# Frontmatter builder
# ---------------------------------------------------------------------------
# Returns the YAML frontmatter block as a string.
#
# The standard library frontmatter is fine as-is unless you need custom
# fields. If you do, the cleanest approach is to add them before the
# closing `---`:
#
#     def _build_frontmatter(ctx: ChangelogContext) -> str:
#         base = build_frontmatter(ctx)
#         return base.rstrip().removesuffix("---") + "domain: pleroma\n---"
#
# Or replace it entirely if your frontmatter diverges enough that surgery
# would be messier than writing fresh. Just make sure you open and close
# with `---`.
# ---------------------------------------------------------------------------

def _build_frontmatter(ctx: ChangelogContext) -> str:
    return build_frontmatter(ctx)


# ---------------------------------------------------------------------------
# Body builder
# ---------------------------------------------------------------------------
# Returns the full changelog body as a string. Custom sections live here.
#
# Append after the standard sections (most common):
#
#     def _build_body(ctx: ChangelogContext) -> str:
#         return build_body(ctx) + _my_extra_sections(ctx)
#
# Note: build_body() emits the archivist:auto-end sentinel and the
# user-editable block below it. If you're appending sections, you're
# appending *before* that preserved user block — the sentinel and user
# content come from build_body()'s return value, not from you.
#
# If you need to insert sections *between* the generated content and the
# sentinel, you'll need to replace build_body() entirely and emit
# ARCHIVIST_AUTO_END yourself at the right point:
#
#     from archivist.utils import ARCHIVIST_AUTO_END
#
# To read data your _post_changes() stored:
#
#     lib_stats = cast(LibraryStats, ctx.data["lib_stats"])
#     my_stuff = ctx.data["domain_files"]  # whatever you put in there
# ---------------------------------------------------------------------------

def _build_body(ctx: ChangelogContext) -> str:
    return build_body(ctx)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
# Printed to the terminal after the write. Optional — remove print_summary
# from the run_changelog() call to fall back to the base runner's minimal
# default, or replace it entirely with your own.
# ---------------------------------------------------------------------------

def _print_summary(ctx: ChangelogContext) -> None:
    print_summary(ctx)


# ---------------------------------------------------------------------------
# run() — the only thing Archivist actually calls
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    run_changelog(
        args,
        module_type="library",
        build_frontmatter=_build_frontmatter,
        build_body=_build_body,
        post_changes=_post_changes,
        print_summary=_print_summary,
    )