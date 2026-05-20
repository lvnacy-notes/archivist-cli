---
class: spec
category:
  - feature
  - infrastructure
affiliations:
  - centralized-db
  - git-integration
created: 2026-05-19
modified: 2026-05-19
version: 0.2
related:
  - "[[MULTI_VAULT_ORCHESTRATION]]"
  - "[[CENTRALIZED_DB]]"
  - "[[GIT_INTEGRATION]]"
tags:
---

**Depends on:** Centralized Database feature (must ship first)

---

## 1. Overview

Two pain points, one feature. If you update a guidance doc in one module and it's relevant across the Apparatus, you should be able to sync it without dropping into six repos. If you've standardized a new frontmatter property and want it everywhere, you should be able to run that once.

That's the whole thing. File distribution and frontmatter consistency across registered modules. Nothing more.

---

## 2. Commands

| Command | Does |
|---|---|
| `archivist muster` | Status report across registered modules |
| `archivist distribute` | Copy a file to multiple modules |
| `archivist broadcast` | Run a frontmatter subcommand across multiple modules |

---

## 3. Scope Selectors

Every multi-module operation requires an explicit scope. No implicit scope exists. A command without a scope selector exits immediately with an error.

```
--apparatus <name>    All active (non-decimated) modules in this apparatus
--vault <name>        All active modules under this vault, including the vault itself
--type <type>         Filter by module type; combinable with --apparatus or --vault
--module <name|uuid>  One or more specific modules; repeatable; mutually exclusive
                      with --apparatus and --vault
```

`--type` is a filter, not a scope. `--apparatus` or `--vault` establishes scope; `--type` narrows it. `--module` is its own scope for when you already know exactly what you're targeting.

`--vault` includes the vault module itself. A vault is a module. If you need to exclude it, `--type library,story,publication` handles it.

Operations run in series, in alphabetical order by module name. No parallelism.

---

## 4. `archivist muster`

```
archivist muster [--apparatus <name>] [--vault <name>] [--type <type>] [--include-decimated]
```

Prints a status table across all matching modules. Read-only; no `--dry-run` needed or accepted.

### 4.1 Output

```
cosmic-horror    (library)  ~/writing/cosmic-horror       ✓  last seal: 2026-05-12  synced: 2026-05-14
victorian-mayhem (library)  ~/writing/victorian-mayhem    ✓  last seal: 2026-05-19  synced: 2026-05-19
panopticon       (library)  ~/writing/panopticon          ✗  PATH NOT FOUND         synced: 2026-03-01
fiction-vault    (vault)    ~/writing/fiction-vault       ✓  last seal: —           synced: 2026-05-18
```

**Path validity** is checked by `path.exists()` at muster time. No guessing.

**Last seal** is pulled from the `changelogs` table in the apparatus DB. Falls back to `—` if no changelogs are recorded.

**Synced** is `modules.last_synced_at` — when the pre-commit hook last confirmed this module's path. Requires the schema addition in §7.

Decimated modules are excluded by default. `--include-decimated` to show them.

---

## 5. `archivist distribute`

```
archivist distribute <source> [--dest <relative-path>]
                     --apparatus|--vault|--module [--type <type>]
                     [--overwrite] [--dry-run]
```

Copies `<source>` into every module in scope. `--dest` is the relative path within each module where the file lands. If `--dest` is omitted, the file lands at the same relative path as `<source>`. If `<source>` is an absolute path or outside the current repo, `--dest` is required.

### 5.1 Flow (per module)

```
1. Validate module path exists → skip with warning if not
2. Resolve destination: <module-path> / <dest>
3. If destination exists and --overwrite not set → skip with warning
4. If --dry-run → print what would happen, write nothing
5. Write file
6. Report result
```

### 5.2 Output

```
→ cosmic-horror: writing .archivist/AGENTS.md
✓ cosmic-horror: done
→ victorian-mayhem: writing .archivist/AGENTS.md
✓ victorian-mayhem: done
→ panopticon: PATH NOT FOUND — skipping
⚠ fiction-vault: .archivist/AGENTS.md already exists — pass --overwrite to replace it
```

Summary at the end: N written, M skipped, K failed. Failures skip and continue; the run does not abort.

No silent overwrites. If the file exists, you get a warning and a flag to pass. The flag is your explicit consent.

Distribute writes the file. It does not stage it. Staging is the user's job.

---

## 6. `archivist broadcast`

```
archivist broadcast frontmatter <subcommand> [subcommand-args]
                    --apparatus|--vault|--module [--type <type>]
                    [--dry-run]
```

Runs a `frontmatter` subcommand in each module's working directory, in series. Scoped strictly to frontmatter commands — this is not a general execution engine.

```
archivist broadcast frontmatter add reviewed false --apparatus writing --type library
archivist broadcast frontmatter remove draft --vault fiction-vault --dry-run
```

### 6.1 Argument Parsing

`frontmatter` is a required literal — broadcast does not accept other command families. Everything after `frontmatter` is the subcommand and its arguments. Scope selectors and `--dry-run` belong to broadcast; everything else passes through to the inner command.

`--dry-run` on broadcast propagates as `--dry-run` to the inner command. You should not have to pass it twice.

### 6.2 Flow (per module)

```
1. Validate module path exists → skip with warning if not
2. chdir into module root
3. Invoke the frontmatter subcommand's run() with the parsed inner args
4. Capture result
5. chdir back
6. Continue to next module
```

### 6.3 Output

```
[cosmic-horror]
  ✓ Added reviewed: false to 47 files.

[victorian-mayhem]
  ✓ Added reviewed: false to 31 files.

[panopticon]
  ✗ PATH NOT FOUND — skipping

[fiction-vault]
  — 0 files matched
```

Summary at the end: N succeeded, M skipped, K failed. Failures don't abort the run.

### 6.4 Implementation Note: `git_root` Resolution

Frontmatter subcommands call `get_repo_root()` inside `run()`. If broadcast has already `chdir`'d into the module, this resolves correctly. Confirm no frontmatter subcommand resolves git root at import time before implementing broadcast.

---

## 7. Failure Semantics

**Path not found:** skip with warning; continue. Never abort the whole run for a stale path.

**Module command failure:** capture stderr, report with the per-module block, continue. Surfaced in the end summary.

**Registry not accessible:** hard abort. No registry, no scope, no module list.

**Partial run:** no rollback. What ran, ran. Every operation is safe to re-run.

---

## 8. Required CDB Schema Addition

Add to `modules` in `registry.db` before the CDB implementation checklist is finalized:

```sql
last_synced_at  TEXT   -- ISO datetime; set by pre-commit hook on each registry upsert
```

The pre-commit hook already upserts the `modules` row on every commit. Adding `last_synced_at = datetime.now()` to that upsert is one line. Without it, `muster` cannot show how fresh the registered path data is.

---

## 9. Deferred: Git Integration Phase

- Automatic staging after `distribute`
- `archivist muster --fetch` — remote tracking state per module