---
class: plan
category:
  - feature
  - infrastructure
  - planning
affiliations:
created:
modified: 2026-05-18
version:
status:
related:
tags:
---
A living document tracking intended future direction. Feature entries are intentionally minimal — follow the links for full context.

```toc
```

---

## Features

### Centralized Cross-Project Database

A machine-level SQLite database aggregating commit hashes and changelog frontmatter across the entire Apparatus. Foundation for cross-project orchestration; everything below that is blocked is blocked on this.

See [[CENTRALIZED_DB]].

---

### Git Integration

`archivist init`, `add`, and `deinit` extended to wrap select git operations, keeping the Apparatus registry in sync with the filesystem and version control. Blocked on Centralized DB.

See [[GIT_INTEGRATION]]

---

### Graph

Wikilink relationship parsing across registered modules, persisted in `registry.db`, exported as a self-contained interactive HTML visualization — the Cosmoscope. Blocked on Centralized DB.

See [[docs/ROADMAP/GRAPH/GRAPH_SPECIFICATION|GRAPH_SPECIFICATION]] 

---

### DeleGit

A minimal Swift wrapper over libgit2 for Archivist mobile. Local operations only — diff, staging, commit, blob reading. Remote ops belong to Working Copy.

See [[delegit]].

---

### Templater Support ✅

Shipped. All four frontmatter commands handle `<% %>` expressions safely across three modes (`resolve`, `preserve`, `false`). Four deferred follow-up items remain open.

See [[templater-support]].

---

### Multi-Vault / Submodule Orchestration

Coordinating across vaults and submodules: distributing templates, syncing `AGENTS` files, running operations across the full Apparatus without dropping into each module individually. Design should follow, not precede, the Centralized DB work — the machine-level registry is the natural foundation.

See [[multi-vault-orchestration]].

---

### `frontmatter rename` — Type Coercion on Collision

When the target property already exists at a different type, coerce the incoming value to match rather than letting Obsidian silently swallow the mismatch. Larger scope: surface the conflict, give the user a decision point, act on the answer.

---

### User-Defined Templates

Allow users to supply and manage their own templates without touching code. Changelog templates were implemented and removed due to inconsistent behavior from the post-commit hook; manifest scanning is functional but rudimentary. Low priority — revisit when the template machinery is more consistent.

---

### Changelog — Directory Rename Detection

Git's `-M` rename detection has no awareness of directory renames. A partial solution exists (`detect_dir_renames`, `reassign_deletions`, `infer_undetected_renames` in `utils.py`); none fully resolve the issue in practice. Directory renames require manual review of generated changelogs until this is resolved.

---

### `reclassify` — Structural Migration

`archivist reclassify` currently swaps the `class:` value and nothing else. The long-term target: a `--migrate` flag that applies the target class's frontmatter template automatically — adding missing properties, removing extraneous ones, reordering to match. Closely related to `frontmatter apply-template`; shared logic belongs in `utils.py`.

---

## Mobile

### Archivist Mobile

An iOS app providing a subset of Archivist's git operations and changelog generation for on-device use. Not part of the CLI — a companion app operating over the same vaults and modules. DeleGit is the git library purpose-built for it.

See [[delegit]].

---

## Development Infrastructure

See [[DEVELOPMENT_INFRASTRUCTURE]]. Out of date — needs culling before it's useful.