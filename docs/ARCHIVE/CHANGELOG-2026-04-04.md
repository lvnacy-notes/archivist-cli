---
class: archive
category:
  - changelog
log-scope: general
modified: 2026-04-17
UUID: 345cc05c-ae60-41c6-acad-c54ccbd0b667
commit-sha: 
files-modified: 32
files-created: 23
files-archived: 1
tags:
  - archivist-cli
---

# Changelog — 2026-04-17

## Overview

| Field | Value |
|-------|-------|
| Date | 2026-04-17 |
| Commit SHA | [fill in after commit] |
| Files Added | 23 |
| Files Modified | 32 |
| Files Archived | 1 |

## Changes

### Files Modified
- `.archivist`: reinitialized `archivist` to include and test new features
- `.github/README.md`: [description]
- `.gitignore`: [description]
- `.obsidian/workspace.json`: [description]
- `AGENTS.md`: [description]
- `archivist-cli.code-workspace`: [description]
- `archivist/cli.py`: [description]
- `archivist/commands/changelog/general.py`: [description]
- `archivist/commands/changelog/library.py`: [description]
- `archivist/commands/changelog/publication.py`: [description]
- `archivist/commands/changelog/story.py`: [description]
- `archivist/commands/changelog/vault.py`: [description]
- `archivist/commands/frontmatter/add.py`: [description]
- `archivist/commands/frontmatter/apply_template.py`: [description]
- `archivist/commands/frontmatter/remove.py`: [description]
- `archivist/commands/frontmatter/rename.py`: [description]
- `archivist/commands/hooks/install.py`:
  - refactored and leaned out
  - `seal.py` now does more of the heavy lifting

- `archivist/commands/init.py`:
  - introduced user-specified ARCHIVE directory prompt for `.archivist` config

- `archivist/commands/manifest.py`:
  - added UUID functionality

- `archivist/commands/reclassify.py`: [description]
- `archivist/formatter.py`: [description]
- `pyproject.toml`: [description]
- `docs/ARCHIVE/CHANGELOG-2026-03-09-14d08f2.md` *(moved from `ARCHIVE/CHANGELOG-2026-03-09-14d08f2.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG-2026-03-09-a9340ce.md` *(moved from `ARCHIVE/CHANGELOG-2026-03-09-a9340ce.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG-2026-03-09-dcfcaca.md` *(moved from `ARCHIVE/CHANGELOG-2026-03-09-dcfcaca.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG-2026-03-13-b1f8438.md` *(moved from `ARCHIVE/CHANGELOG-2026-03-13-b1f8438.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG-2026-03-14-48f6da2.md` *(moved from `ARCHIVE/CHANGELOG-2026-03-14-48f6da2.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG-2026-03-15-cce03dc.md` *(moved from `ARCHIVE/CHANGELOG-2026-03-15-cce03dc.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG-2026-03-15-fba982f.md` *(moved from `ARCHIVE/CHANGELOG-2026-03-15-fba982f.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG-2026-04-02-796132a.md` *(moved from `ARCHIVE/CHANGELOG-2026-04-02.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ROADMAP.md` *(moved from `ROADMAP.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG_TEMPLATE.md` *(moved from `ARCHIVE/CHANGELOG_TEMPLATE.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]

### New Files Created
- `.github/workflows/ci.yaml`: [description]
- `.github/workflows/release.yaml`: [description]
- `archivist/commands/changelog/changelog_base.py`: new dataclass with boilerplate for changelog subcommands
- `archivist/commands/changelog/seal.py`: new post-commit script to manage editions db and seal changelogs
- `archivist/utils/__init__.py`: [description]
- `archivist/utils/changelog.py`: [description]
- `archivist/utils/config.py`: [description]
- `archivist/utils/db.py`: [description]
- `archivist/utils/frontmatter.py`: [description]
- `archivist/utils/git.py`: [description]
- `archivist/utils/output.py`: [description]
- `archivist/utils/rename_helpers.py`: [description]
- `docs/ARCHIVE/CHANGELOG-2026-04-04.md`: this changelog
- `docs/TEMPLATER_SUPPORT_PLAN.md`: [description]
- `docs/TESTING_SPECIFICATION.md`: [description]
- `tests/conftest.py`: [description]
- `tests/integration/test_changelog_commands.py`: [description]
- `tests/integration/test_frontmatter_commands.py`: [description]
- `tests/integration/test_seal.py`: [description]
- `tests/unit/test_changelog_helpers.py`: [description]
- `tests/unit/test_config.py`: [description]
- `tests/unit/test_frontmatter.py`: [description]
- `tests/unit/test_rename_helpers.py`: [description]

### Files Removed / Archived
- `archivist/utils.py`:
  - `utils.py` was growing unwieldy. Migrated functions to modules with 
  separation of concerns



<!-- archivist:auto-end -->

## Notes

Feat: UUID, Test Suite, and Refactor

- added UUID fields and methods for better changelog management and backfill
- added minimal test suite
- setup initial support for Templater; this will be built out in another update
- refactored codebase
- instantiated tighter type system
- patched a number of bugs and implemented a series of augments
  - better support when dealing with non-UTF-8 characters
  - dealing with spaces in directory and file names
  - spinning cursor when running long jobs
  - manifests are now recognized when committing editions
  - prompt for save before running iterative changelog command

### UUID

- `utils.py` — added `generate_changelog_uuid()`, updated `init_db()` to create changelogs table, added `seal_changelog_in_db()`
- `seal.py` — new command, taking on the bulk of effort from the `POST_COMMIT_HOOK`.
- `cli.py` — added seal subcommand under changelog
- `install.py` — replaced bash backfill in `POST_COMMIT_HOOK` with `archivist changelog seal "$COMMIT_SHA"`, kept manifest backfill in bash since it's simple and semantically separate
- All five changelog modules were UUID threading + visibility improvements

### Refactor: changelog_base

The complexity lives exactly where it belongs. Library needs catalog analysis, so library provides a `post_changes` hook, specified below. Vault needs submodule status, so vault provides one too. General needs nothing beyond the shared flow, so it provides nothing.

The config routing in `cli.py` already fits this shape. The `.archivist` file says `module-type: library`, `MODULE_CHANGELOG_COMMAND` maps that to "library", the right `run()` gets called, and that `run()` tells the runner exactly what's special about library modules. Each layer knows only what it needs to know.
The other thing that falls out of this cleanly is that `ctx.data` is honest about being a bag of module-specific state.There's no pretense that it's typed or shared — it's the thing that lives between `post_changes` and the builders for that one module, and it's named accordingly. When vault's `post_changes` puts submodule status in there, it's obvious that only vault's builders will ever read it.

- `changelog_base.py` — a `ChangelogContext` dataclass that carries everything assembled by the runner, and `run_changelog()` which owns the shared flow. The dataclass has a `data: dict` field as an explicit escape hatch for module-specific state — library puts `lib_stats` and `snapshot_block` in there, vault will put submodule status, etc. This also gets a `post_write` parameter — a hook that fires after the write block (whether live or `dry-run`), before the summary. Publication is the only current consumer of `post_write`, but it's a clean general slot for any module that needs side-effects tied to write completion.The runner signature makes the contract visible at the call site: you can see exactly which pieces a module provides and which it inherits for free.

All five subcommands are now on the same runner:

- `general.py` - `datetime` import is gone, replaced by the new `get_today` utility; docstrings on the private functions are gone because the names say what they do; `_get_extra_paths` now takes `git_root` directly as the runner expects rather than pulling it off self; `run()` is four lines.
- `library.py` — All the hooks are now just named functions prefixed with `_` like everything else in the file. `_analyse_catalog` is the post_changes hook — it runs after the base runner has processed renames and writes into ctx.data. `_build_frontmatter` and `_build_body` both receive ctx and pull what they need. `run()` at the bottom is four lines and reads like a declaration of what this module is.
- `publication.py` - Publication is the interesting one — it has two things the runner doesn't currently support. First, `infer_undetected_renames` runs on top of the standard rename processing. Second, `_mark_shas_included` needs to fire after the write but only when not dry-running. As such, the changes: `_analyse_publication` handles the extra rename inference pass, trims `processed_changes["D"]` and stashes `remaining_added` in `ctx.data`, then queries the DB. By the time `post_changes` runs the UUID is already resolved in the runner, so the DB query lands at exactly the right moment  without any ordering gymnastics. `_mark_shas_post_write` checks `dry_run` itself and either prints what would happen or fires the DB write.
- `story.py` - `datetime` gone, using instead the `get_today` utility; docstrings on private functions gone; `run()` is three lines. The only difference is no `get_extra_paths` since story has nothing to add there.
- `vault.py` - the original `run()` called `_get_submodules_in_commit` twice — once inside `_build_body` and again at the end for the summary print. That's two subprocess chains for the same data. `_analyse_submodules` runs once as `post_changes`, stuffs `updated_subs` and `sub_status` into `ctx.data`, and both `_build_body` and `_print_summary` just read from there.

Five modules that used to be five slightly-different copies of the same 80-line `run()` function are now five `run_changelog()` calls that each say exactly what's special about them and nothing else. Library needed catalog analysis — it has a `post_changes`. Vault needed submodule state — same hook. Publication needed a DB write tied to the write event — it gets `post_write`. General and story needed nothing — they get nothing. And the runner is honest about what it owns: the flow. Every module is honest about what it owns: the differences. Nothing is hiding in a class hierarchy you have to trace across two files to understand.

### Refactor: Frontmatter Utility Extraction

**Scope:**
- `utils/frontmatter.py`
- `commands/frontmatter/{add,remove,rename,apply_template}.py`
- `commands/manifest.py`
- `commands/reclassify.py`

#### Deduplicate Property Regex Pattern

**New helpers in `utils/frontmatter.py`:**

- `property_line_pattern(prop: str) -> re.Pattern` — compiles the canonicalYAML key-line regex for a given property name, escaping any special charactersin the property name along the way.
- `match_property_line(line: str, prop: str) -> bool` — convenience wrapper;returns True if `line` is a YAML key line for `prop`.

**Updated:** `add.py`, `remove.py`, `rename.py`

The naked `rf"^{re.escape(prop)}\s*:"` that lived independently in all three files is gone. One definition, everywhere.

---

#### Unify Frontmatter Property Removal Logic

**New helper in `utils/frontmatter.py`:**

- `remove_property_from_frontmatter(raw_fm: str, prop: str) -> tuple[str, bool]` — removes a property and all its continuation lines (block sequences, multi-line scalars) from raw YAML frontmatter text. Returns the updated frontmatter string and a bool indicating whether the property was found.

**Updated:** `add.py` (overwrite path), `remove.py`

**Note — `apply_template.py`:** `apply_template` does not do line-level property removal. It works at the parsed-entries level: `_apply_template()` rebuilds frontmatter from scratch using only the keys present in the template, so non-template properties are excluded structurally rather than surgically excised. Applying `remove_property_from_frontmatter` here would be the wrong tool — it would require an additional raw-text pass over data that's already been parsed and merged. The refactored `apply_template.py` (item #12) handles this correctly.

---

#### Consolidate File I/O Error Handling

**New helpers in `utils/frontmatter.py`:**

- `safe_read_markdown(path: Path) -> str | None` — reads a file with`encoding="utf-8", errors="ignore"`. Returns `None` on any `OSError` and prints a formatted warning to stderr. Does not raise.
- `safe_write_markdown(path: Path, content: str) -> bool` — writes content to a file. Returns `False` on any `OSError` and prints a formatted warning to stderr. Does not raise.

**Updated:** `add.py`, `remove.py`, `rename.py`, `apply_template.py`, `manifest.py`, `reclassify.py`

All per-file try/except blocks for `path.read_text()` and `path.write_text()` have been replaced. Error message format is now consistent across every command.

**Behaviour change in `manifest.py`:** The existing-manifest read path now handles a previously-unhandled failure mode. If `safe_read_markdown` returns `None` (unreadable existing manifest), the command logs the failure and proceeds as if no existing manifest was found, writing a fresh one. Previously, `existing.read_text()` would raise unhandled and crash.

---

#### Extract Frontmatter Parsing from `apply_template.py`

**New helpers in `utils/frontmatter.py`:**

- `parse_frontmatter_entries(raw: str) -> list[tuple[str, list[str]]]` — parses raw frontmatter text into an ordered list of `(key, raw_lines)` tuples. Preserves original text verbatim (including indentation) for round-trip safety. Use this when you need structural access to frontmatter; use `extract_frontmatter()` when you just need a parsed dict.
- `extract_tags_from_entries(entries) -> list[str]` — extracts tag values from parsed entries. Handles all three YAML list formats (inline, scalar, block sequence). Returns lowercase stripped strings.

**Updated:** `apply_template.py`

Private functions `_parse_frontmatter()` and `_rip_tags_out_of_entries()` have been removed from `apply_template.py`. The command now imports the shared versions. Tag parsing behaviour is unchanged.

---

#### Extract Frontmatter File Operations

**New helpers in `utils/frontmatter.py`:**

- `update_frontmatter_in_file(path, transformer_fn) -> str | None` — reads a markdown file, extracts its frontmatter block, and passes `(raw_frontmatter, body)` to the supplied transformer. If the transformer returns a new content string, the file is written back to disk. Returns `None` if the file is unreadable, has no frontmatter, the transformer signals no change (by returning `None`), or the write fails.

**Scope:** files that already have a frontmatter block. `add.py` is the one command that also creates blocks from scratch and therefore cannot use this helper exclusively — see below.

- `process_markdown_files(root, callback, filters=None) -> int` — walks `root` recursively for `.md` files, applies optional `path_prefix` filtering, invokes `callback(path)` on each file, and returns the count of files for which the callback returned `True`. The callback owns all messaging, dry-run logic, and error handling.

**Updated:** `remove.py`, `rename.py`, `apply_template.py`, `add.py`

**Note — `add.py`:** This command is the structural exception. Because it creates a frontmatter block from scratch when none exists, it cannot delegate the full read→transform→write pipeline to `update_frontmatter_in_file`. It retains an explicit two-branch structure (`if match / else`) but uses all other helpers: `safe_read_markdown`, `safe_write_markdown`, `match_property_line`, `remove_property_from_frontmatter`, and `process_markdown_files`.

#### Markdown File Scanning Patter

**New helpers in `utils/frontmatter.py`:**

- `find_markdown_files(root: Path, filters: dict | None = None) -> list[Path]` - owns the `sorted(root.rglob("*.md"))` call that was scattered across five modules. The `path_prefix` filter matches the one already supported by `process_markdown_files`, and `process_markdown_files` now delegates to it rather than duplicating the walk. Class and tag filtering are explicitly documented as callback territory — reading every file twice to pre-filter would be wasteful and the comment says so.
- `get_file_frontmatter()` - quiet upgrade: it now uses `has_frontmatter()` as its guard instead of a raw `FRONTMATTER_RE.match()` call, which makes the internal implementation consistent with everything else.

**Updated:** `add.py`, `remove.py`, `rename.py`, `apply-template.py`, `reclassify.py`

#### Frontmatter Detection Helpers

**New helpers in `utils/frontmatter.py`:**

- `has_frontmatter(content: str) -> bool` - replaces the dual-purpose `match = FRONTMATTER_RE.match()` boolean check in `_process_note`/`_load_note` functions across all frontmatter commands. `FRONTMATTER_RE` stays for raw extraction but is no longer doing double duty as a guard.

**Updated:** `add.py`, `remove.py`, `rename.py`, `apply-template.py`

### New Test Suite

The goal is not 100% coverage for coverage's sake — it's a suite that would catch
a regression in the behaviors that are genuinely dangerous to get wrong: YAML
mutation, dry-run safety, rename detection, and the sentinel boundary. Everything
else is gravy.

## Structure

```
tests/
├── conftest.py             # shared fixtures
├── unit/
│   ├── test_frontmatter.py
│   ├── test_rename_helpers.py
│   ├── test_changelog_helpers.py
│   └── test_config.py
└── integration/
    ├── test_frontmatter_commands.py
    ├── test_changelog_commands.py
    └── test_seal.py
```

Unit tests run fast, need no git, and cover the pure-function core. Integration
tests need a real git repo and are slower — mark them so you can skip them when
you just want to know if you broke a regex.

Seven files, minimal scope, necessary coverage.

---

*This changelog was automatically generated by Archivist CLI.*
*See [Archivist CLI](https://github.com/lvnacy-notes/archivist-cli) for more information.*
