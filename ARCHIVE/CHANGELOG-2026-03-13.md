---
class: archive
category:
  - changelog
log-scope: general
modified: 2026-03-14
commit-sha: 
files-modified: 12
files-created: 5
files-archived: 0
tags:
  - archivist-cli
---

# Changelog — 2026-03-14

## Overview

| Field | Value |
|-------|-------|
| Date | 2026-03-14 |
| Commit SHA | [fill in after commit] |
| Files Added | 5 |
| Files Modified | 12 |
| Files Archived | 0 |

## Changes

### Files Modified
- `.github/README.md`: [description]
- `archivist/cli.py`: [description]
- `archivist/commands/changelog/general.py`: [description]
- `archivist/commands/changelog/library.py`: [description]
- `archivist/commands/changelog/publication.py`: [description]
- `archivist/commands/changelog/story.py`: [description]
- `archivist/commands/changelog/vault.py`: [description]
- `archivist/commands/hooks/install.py`: [description]
- `archivist/commands/init.py`: [description]
- `archivist/commands/manifest.py`: [description]
- `archivist/utils.py`: [description]
- `pyproject.toml`: bumped version to 1.2.0

### New Files Created
- `AGENTS.md`: [description]
- `ARCHIVE/CHANGELOG-2026-03-13.md`: [description]
- `ROADMAP.md`: [description]
- `archivist/commands/reclassify.py`: [description]
- `archivist/formatter.py`: [description]

### Files Removed / Archived
- No files archived


<!-- archivist:auto-end -->

## Notes

### New feature: ROADMAP.md

An a rough outline of the future of Archivist. Right now, this bitch is a changelog-generating boss. But one day, it will bring cohesion across all projects associated with it with a centralized database.

### New feature: `archivist reclassify`

- finds all `.md` files whose frontmatter `class` value matches a given string and rewrites it to a new value. Surgical: only the `class:` line is touched. Supports `--path` scoping and `--dry-run`. Matching is case-insensitive; the new value is written verbatim.
- added a `reclassify` feature entry scoping the future `--migrate` flag, which will apply the target class's frontmatter template as part of the reclassification pass.

### New feature: terminal formatting

**What changed and why:**
`formatter.py` is a self-contained module with three things: the `ArchivistHelpFormatter` class, and two helper functions — `fmt_examples()` and `fmt_warning()` — that you call when building the parser strings. The formatter itself overrides three methods from argparse's base: `start_section` (headings), `_format_action_invocation` (the left column of flag/command names), and `_format_usage` (the usage line). That's it — everything else argparse handles normally, which keeps it from being fragile.

The ANSI check happens at formatter instantiation time, which is exactly when --help fires, so the TTY detection is accurate. Pipe the output somewhere and it falls back to plain text automatically.
One thing to be aware of: from yaml import parser at the top of your original cli.py was a stray import that shadowed the local parser variable name. I dropped it — it wasn't doing anything.

Also, we gave this bitch some personality. She's annoyed if you ask for help. Ask for help anyway, that's what the flag is for.

### Feature adjustment: remove template discovery from `archivist manifest`

**Summary of what changed:**

- `_find_manifest_template()` — deleted entirely
extract_frontmatter — removed from imports
- `_build_manifest_frontmatter()` — `template_fm: dict` parameter dropped; now iterates directly over `auto.items()` for both field order and values, same pattern as the changelog builders
- `run()` — template discovery lines removed, call site updated to match new signature

One thing worth knowing: MANIFEST_TEMPLATE.md can stay in ARCHIVE/ as a reference document if you want — Archivist just won't touch it anymore. Or pull it out and document the field list in the README. Either way, nothing breaks.

### Feature adjustment: `library.py` enhancements

**Overhauled `archivist changelog library` with several significant additions and fixes.**
- *`catalog-status` → `work-stage` migration* — detection signal updated throughout to match the template field. `CATALOG_STATUSES` replaced with `WORK_STAGES` (`placeholder`, `raw`, `active`, `processed`, `shelved`), all status counts and summary output updated to match.
- *Catalog Snapshot dashboard* — new `## Catalog Snapshot` section generated at run time by scanning the full works directory, producing a static, commit-frozen snapshot of: stage distribution (Mermaid pie), stage throughput for transitions this commit (table), author landscape (Mermaid pie, capped at 8 + Others bucket), reading velocity over a rolling window (Mermaid xychart-beta bar), and placeholder debt count.
- *Works directory scoping* — catalog scan scopes to `works-dir` as declared in `.archivist`, defaulting to `works/`. archivist init now prompts for this value when library is selected as module type.
- *Descriptions on all buckets* — [description] placeholder and sub-bullet note format extended to works, authors, publications, and definitions across added, updated, and removed sections. Notes persist across iterative re-runs via the existing description extraction mechanism.
- *`find_todays_changelog` fix* — post-commit SHA-renamed changelogs (`CHANGELOG-{date}-{sha}.md`) are now correctly excluded from iterative run detection, preventing new runs from overwriting sealed committed changelogs.

### Feature adjustment: `git` file operations enhancement

- *Rename detection* — added `-M` flag to git diff commands to surface renames as first-class events rather than silent add/remove splits. Renamed files route into `updated` buckets with an inline (renamed from `old-name.md`) indicator. Suspicious renames — cross-directory or stem mismatch — additionally render a ⚠️ advisory flag prompting verification before commit.

### Feature adjustment: fixed multi-day changelog continuity

— replaced `find_todays_changelog` with `find_active_changelog` in `utils.py`. The new function scans `ARCHIVE/` for all unsealed changelogs and returns the most recent, regardless of date. Working sessions that span midnight no longer orphan an existing changelog. Updated import and call site in `general.py`; same swap required in `publication`, `story`, `vault`, and `library` subcommands.

---

*This changelog was automatically generated by Archivist CLI.*
*See [Archivist CLI](https://github.com/lvnacy-notes/archivist-cli) for more information.*
