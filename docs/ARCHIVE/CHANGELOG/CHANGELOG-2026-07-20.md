---
class: archive
category:
  - changelog
log-scope: general
modified: 2026-07-20
UUID: 688b23af-ab46-407e-a41b-d647e6a300fc
commit-sha: 
files-modified: 45
files-created: 1
files-archived: 0
tags:
  - archivist-cli
---

# Changelog — 2026-07-20

## Overview

| Field | Value |
|-------|-------|
| Date | 2026-07-20 |
| Commit SHA | [fill in after commit] |
| Files Added | 1 |
| Files Modified | 45 |
| Files Archived | 0 |

## Changes

### Files Modified
- `archivist/commands/changelog/changelog_base.py`: patched 2 bugs, detailed below 
- `archivist/commands/changelog/seal.py`: patched bug where sealed logs were not accounted for
- `archivist/utils/changelog.py`: patched 2 bugs, detailed below
- `archivist/utils/rename_helpers.py`: patched 2 bugs, detailed below
- `tests/integration/test_seal.py`: updated tests for patched bugs
- `tests/unit/test_changelog_helpers.py`: updated tests for patched bugs
- `tests/unit/test_rename_helpers.py`: updated tests for patched bugs
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-03-09-14d08f2.md` *(moved from `docs/ARCHIVE/CHANGELOG/2026/03/CHANGELOG-2026-03-09-14d08f2.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-03-09-a9340ce.md` *(moved from `docs/ARCHIVE/CHANGELOG/2026/03/CHANGELOG-2026-03-09-a9340ce.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-03-09-dcfcaca.md` *(moved from `docs/ARCHIVE/CHANGELOG/2026/03/CHANGELOG-2026-03-09-dcfcaca.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-03-13-b1f8438.md` *(moved from `docs/ARCHIVE/CHANGELOG/2026/03/CHANGELOG-2026-03-13-b1f8438.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-03-14-48f6da2.md` *(moved from `docs/ARCHIVE/CHANGELOG/2026/03/CHANGELOG-2026-03-14-48f6da2.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-03-15-cce03dc.md` *(moved from `docs/ARCHIVE/CHANGELOG/2026/03/CHANGELOG-2026-03-15-cce03dc.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-03-15-fba982f.md` *(moved from `docs/ARCHIVE/CHANGELOG/2026/03/CHANGELOG-2026-03-15-fba982f.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-04-02-796132a.md` *(moved from `docs/ARCHIVE/CHANGELOG/2026/04/CHANGELOG-2026-04-02-796132a.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-04-04-541a454.md` *(moved from `docs/ARCHIVE/CHANGELOG/2026/04/CHANGELOG-2026-04-04-541a454.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-04-18-89c08fe.md` *(moved from `docs/ARCHIVE/CHANGELOG/2026/04/CHANGELOG-2026-04-18-89c08fe.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-04-18-c1495eb.md` *(moved from `docs/ARCHIVE/CHANGELOG/2026/04/CHANGELOG-2026-04-18-c1495eb.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-04-18-d006c88.md` *(moved from `docs/ARCHIVE/CHANGELOG/2026/04/CHANGELOG-2026-04-18-d006c88.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-04-20-f2586de.md` *(moved from `docs/ARCHIVE/CHANGELOG/2026/04/CHANGELOG-2026-04-20-f2586de.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-04-27-28a843a.md` *(moved from `docs/ARCHIVE/CHANGELOG/2026/04/CHANGELOG-2026-04-27-28a843a.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-04-27-92cd2bc.md` *(moved from `docs/ARCHIVE/CHANGELOG/2026/04/CHANGELOG-2026-04-27-92cd2bc.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-04-27-bb2a47c.md` *(moved from `docs/ARCHIVE/CHANGELOG/2026/04/CHANGELOG-2026-04-27-bb2a47c.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-04-28-13ee17a.md` *(moved from `docs/ARCHIVE/CHANGELOG/2026/04/CHANGELOG-2026-04-28-13ee17a.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-04-28-e7c6567.md` *(moved from `docs/ARCHIVE/CHANGELOG/2026/04/CHANGELOG-2026-04-28-e7c6567.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-04-29-706b2d9.md` *(moved from `docs/ARCHIVE/CHANGELOG/2026/04/CHANGELOG-2026-04-29-706b2d9.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-04-30-09ada7d.md` *(moved from `docs/ARCHIVE/CHANGELOG/2026/04/CHANGELOG-2026-04-30-09ada7d.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-04-30-ee2058a.md` *(moved from `docs/ARCHIVE/CHANGELOG/2026/04/CHANGELOG-2026-04-30-ee2058a.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-05-01-2c4e26e.md` *(moved from `docs/ARCHIVE/CHANGELOG/CHANGELOG-2026-05-01-2c4e26e.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-05-07-233bcb8.md` *(moved from `docs/ARCHIVE/CHANGELOG/CHANGELOG-2026-05-07-233bcb8.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-05-07-c62fb10.md` *(moved from `docs/ARCHIVE/CHANGELOG/CHANGELOG-2026-05-07-c62fb10.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-05-14-1e5c604.md` *(moved from `docs/ARCHIVE/CHANGELOG/CHANGELOG-2026-05-14-1e5c604.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-05-14-baf67ce.md` *(moved from `docs/ARCHIVE/CHANGELOG/CHANGELOG-2026-05-14-baf67ce.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-05-14-cdb08e5.md` *(moved from `docs/ARCHIVE/CHANGELOG/CHANGELOG-2026-05-14-cdb08e5.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-05-14-fbdc2ac.md` *(moved from `docs/ARCHIVE/CHANGELOG/CHANGELOG-2026-05-14-fbdc2ac.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-05-15-22fe0cb.md` *(moved from `docs/ARCHIVE/CHANGELOG/CHANGELOG-2026-05-15-22fe0cb.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-05-15-71d94f8.md` *(moved from `docs/ARCHIVE/CHANGELOG/CHANGELOG-2026-05-15-71d94f8.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-05-15-ed5ece0.md` *(moved from `docs/ARCHIVE/CHANGELOG/CHANGELOG-2026-05-15-ed5ece0.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-05-16-7744695.md` *(moved from `docs/ARCHIVE/CHANGELOG/CHANGELOG-2026-05-16-7744695.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-05-17-0cbe36c.md` *(moved from `docs/ARCHIVE/CHANGELOG/CHANGELOG-2026-05-17-0cbe36c.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-05-17-cee3a20.md` *(moved from `docs/ARCHIVE/CHANGELOG/CHANGELOG-2026-05-17-cee3a20.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-05-18-343ab4e.md` *(moved from `docs/ARCHIVE/CHANGELOG/CHANGELOG-2026-05-18-343ab4e.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-05-19-38f07aa.md` *(moved from `docs/ARCHIVE/CHANGELOG/CHANGELOG-2026-05-19-38f07aa.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-05-20-828bf0c.md` *(moved from `docs/ARCHIVE/CHANGELOG/CHANGELOG-2026-05-20-828bf0c.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]
- `docs/ARCHIVE/CHANGELOG/2026/CHANGELOG-2026-05-24-9d05ee6.md` *(moved from `docs/ARCHIVE/CHANGELOG/CHANGELOG-2026-05-24-9d05ee6.md`)* ⚠️ *rename unverified (cross-directory) — double-check*: [description]

### New Files Created
- `docs/ARCHIVE/CHANGELOG/CHANGELOG-2026-07-20.md`: this changelog

### Files Removed / Archived
- No files archived


<!-- archivist:auto-end -->
## Notes

patch: three bugs squashed

- bug 1: changelog command on iteration was not properly preserving descriptions
- bug 2: changelog command on iteration would cause Archivist to generat false paths based on ill-collated data, when files were renamed
- bug 3: sealed changelogs were unnaccounted for on commit
- general project update: historical changelogs reorganized in flat year directory

---

*This changelog was automatically generated by Archivist CLI.*
*See [Archivist CLI](https://github.com/lvnacy-notes/archivist-cli) for more information.*

