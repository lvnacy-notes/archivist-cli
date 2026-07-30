---
class: checklist
category:
  - feature
  - infrastructure
  - database
  - cli
  - git
affiliations:
created: 2026-05-21
modified: 2026-07-28
version:
related:
  - "[[APPARATUS_PLATFORM]]"
  - "[[APPARATUS_PLATFORM_SPEC]]"
  - "[[AP_PHASE_2_IMPLEMENTATION]]"
tags:
  - apparatus-platform
  - phase-1
---

> The net exists. Don't fucking tear holes in it.

Phase 1 ships as a single unit: registry infrastructure, three new commands (`add`, `deinit`, `sync`), an `init` augmentation, and hook updates. Nothing in Phase 1 is optional. Nothing ships with a failing test.

> 📌 **Post-implementation note:** `archivist migrate` — originally planned as a sibling command handling legacy `.archivist` flat-file and `apparatus`-field migration — was cut during implementation. Its responsibilities didn't need a whole command; they got folded into `init`, which already had to handle "existing config, needs updating" as a first-class case. `sync` was added instead, covering a genuinely different problem: non-interactive registry backfill for modules that already have a valid config but never got wired into `~/.archivist/`. See §9 and the new §9a below — this doc has been updated in place rather than left describing a command that doesn't exist.

Phase 2 does not begin until Phase 1 is committed, tested, and stable.

## Contents
```toc
```
---

## Implementation Order

Follow this sequence. Each group depends on the one before it.

```
1. Schema definitions          — no code dependencies; start here
2. registry.py                 — depends on schema
3. ConfigSchema                — independent; do it alongside registry.py
4. archivist init (augmented)  — depends on registry.py + ConfigSchema
5. archivist add               — depends on registry.py
6. archivist deinit            — depends on registry.py
7. archivist sync               — depends on registry.py + init (falls back to it)
8. Hook augmentation           — depends on registry.py
9. CLI parser updates          — alongside or after commands
10. Tests                      — last; cover everything
```

Do not implement `archivist add` or `archivist deinit` before `registry.py` exists. Do not augment `archivist init` before `ConfigSchema` is defined. Do not implement `sync` before `init` exists — every non-interactive-resolution failure in `sync` hands off to `init`. The order is a dependency graph, not a suggestion.

---

## 1. Infrastructure: `~/.archivist/`

- [x] `get_registry_dir() -> Path` returns `Path.home() / ".archivist"`
- [x] `get_registry_path() -> Path` returns `get_registry_dir() / "registry.db"`
- [x] `get_apparatus_db_path(name: str) -> Path` returns `get_registry_dir() / f"{name}.db"`
- [x] `init_registry() -> None` — creates `~/.archivist/`, runs `git init`, creates schema; idempotent

> ⚠️ `get_registry_dir()` is the **single source** of the `~/.archivist/` path. Every other function in `registry.py` derives from it. Nothing outside `registry.py` constructs this path directly. If the storage location ever changes, this is the one function that changes.

> ⚠️ `init_registry()` must be truly idempotent. It will be called during `archivist init` on a machine that may already have a registry from a previous module setup. Running it twice must produce no errors, no duplicate schema creation, no data loss. Test this explicitly.

> 📌 First-run detection: `~/.archivist/` absent → full setup (mkdir, git init, schema). `~/.archivist/` present → check for `registry.db`; if absent, schema only. If both present, no-op. All three states must be handled cleanly.

> 📌 The git init for `~/.archivist/` is separate from any module's git repo. Do not conflate them. `git init ~/.archivist/` runs as a subprocess; it does not affect the calling process's git context.

---

## 2. Schema: `registry.db`

> ⚠️ **The schema below reflects the v3 spec.** The original Phase 1 schema included `apparatus_uuid TEXT NOT NULL REFERENCES apparati(uuid)` on the `modules` table. That column has been removed. If you are reading this checklist while implementing from scratch, use the schema below. If you have an existing `registry.db` built against the original schema, see §13.

- [x] `apparati` table: `uuid TEXT PRIMARY KEY`, `name TEXT NOT NULL UNIQUE`, `db_path TEXT NOT NULL`, `created_at TEXT NOT NULL`, `git_remote TEXT`
- [x] `modules` table: `uuid TEXT PRIMARY KEY`, `name TEXT NOT NULL`, `module_type TEXT NOT NULL`, `path TEXT NOT NULL`, `git_remote TEXT`, `git_remote_name TEXT`, `decimated_at TEXT`, `last_synced_at TEXT`
  - Note: **no `apparatus_uuid` column**. Apparatus membership lives in `module_apparatus`.
- [x] `module_type` CHECK constraint: `module_type IN ('story', 'publication', 'library', 'vault', 'general')`
- [x] `module_bays` table: `container_id TEXT NOT NULL REFERENCES modules(uuid)`, `contained_id TEXT NOT NULL REFERENCES modules(uuid)`, `PRIMARY KEY (container_id, contained_id)`
- [x] `module_apparatus` table: `module_uuid TEXT NOT NULL REFERENCES modules(uuid)`, `apparatus_uuid TEXT NOT NULL REFERENCES apparati(uuid)`, `PRIMARY KEY (module_uuid, apparatus_uuid)`
- [x] FK enforcement: `PRAGMA foreign_keys = ON` executed on **every connection** before use

> 🔴 FK enforcement is OFF by default in SQLite. Every `get_registry_connection()` call must execute `PRAGMA foreign_keys = ON` before returning the connection. Forgetting this means referential integrity is silently unenforced — orphaned rows accumulate with no error until something downstream breaks in a way that's hard to trace back.

> ⚠️ The `module_type` CHECK constraint may or may not be enforced depending on SQLite version and compile flags. Do not rely on it alone. `register_module()` must validate `module_type` in Python before writing. The constraint is a belt-and-suspenders safety net, not the primary gate.

> 📌 `git_remote` and `git_remote_name` are separate columns. `git_remote` is the URL (`git@github.com:user/repo.git`). `git_remote_name` is the local label (`origin`, `upstream`). They are populated via different paths — see §9 of the spec. Do not conflate them in the schema or the query functions.

---

## 3. Schema: Apparatus DB (`~/.archivist/[name].db`)

- [x] `changelogs` table: `uuid TEXT PRIMARY KEY`, `commit_sha TEXT`, `log_scope TEXT`, `module_uuid TEXT NOT NULL`, `created_at TEXT NOT NULL`, `sealed_at TEXT`, `file_path TEXT`
- [x] `works` table: `uuid TEXT PRIMARY KEY`, `title TEXT NOT NULL`, `module_uuid TEXT NOT NULL`, `work_stage TEXT`, `created_at TEXT NOT NULL`, `modified_at TEXT`
- [x] `authors` table: `uuid TEXT PRIMARY KEY`, `name TEXT NOT NULL`, `apparatus_uuid TEXT NOT NULL`, `created_at TEXT NOT NULL`
- [x] `works_authors` junction: `work_uuid TEXT NOT NULL REFERENCES works(uuid)`, `author_uuid TEXT NOT NULL REFERENCES authors(uuid)`, `PRIMARY KEY (work_uuid, author_uuid)`
- [x] `init_apparatus_db(apparatus_name: str) -> None` — idempotent; creates DB file + schema if absent

> ⚠️ The apparatus DB `changelogs` table is **not** the same as the per-project `changelogs` table in `ARCHIVE/archive.db`. They coexist. The per-project table is managed by the seal pipeline. The apparatus table is cross-module aggregation managed by this feature. Do not touch `db.py` or the per-project schema. `registry.py` and `db.py` are peers with separate concerns.

> ⚠️ `init_apparatus_db()` creates a new file at `~/.archivist/[name].db`. If the apparatus name contains characters that are invalid in a filename, this will fail at the OS level. Validate apparatus names at registration time: lowercase, hyphens and alphanumerics only. Reject anything else with a clear error.

---

## 4. Utility Module: `archivist/utils/registry.py`

New module. Barrel-exported. All registry DB access goes through this module — no command or utility opens a connection to `~/.archivist/` directly.

> 🔴 Every function that opens a connection must enable FK enforcement before doing anything else. The pattern is: `conn = sqlite3.connect(path); conn.execute("PRAGMA foreign_keys = ON"); return conn`. This is not optional. See §2 above.

> [!important]
> **bare `sqlite3.connect()`** appears exactly once in the file — inside `_open_connection()` — and the PRAGMA to enforce FK appears exactly once alongside it. A bare `sqlite3.connect()` outside that one function is a bug.

> ⚠️ `registry.py` is a new peer alongside `db.py` in `archivist/utils/`. They serve different layers — per-project vs. machine-level — and must not import from each other. If you find yourself reaching into `db.py` from `registry.py`, stop and rethink.

> 📌 Connection lifecycle: callers are responsible for closing connections. Document this clearly. Consider whether a context manager pattern (`with get_registry_connection() as conn`) is preferable to raw connection returns. Either is acceptable; pick one and be consistent.

**Path resolution:**
- [x] `get_registry_dir() -> Path`
- [x] `get_registry_path() -> Path`
- [x] `get_apparatus_db_path(name: str) -> Path`

**Connection management:**
- [x] `get_registry_connection() -> sqlite3.Connection` — opens `registry.db`; FK enforcement ON
- [x] `get_apparatus_connection(apparatus_name: str) -> sqlite3.Connection` — opens apparatus DB; FK enforcement ON

**Initialization:**
- [x] `init_registry() -> None` — idempotent; mkdir, git init, schema
- [x] `init_apparatus_db(apparatus_name: str) -> None` — idempotent; creates apparatus DB + schema

**Apparatus lifecycle:**
- [x] `register_apparatus(name: str, git_remote: str | None) -> str` — upserts `apparati` row; calls `init_apparatus_db()`; returns UUID
- [x] `get_apparatus_by_name(name: str) -> dict | None`

**Module lifecycle:**
- [x] `register_module(apparatus_name: str | None, name, module_type, path, git_remote, git_remote_name) -> str`
  - `apparatus_name` is `str | None` — pass `None` for standalone modules; apparatus membership is written to `module_apparatus`, not `modules`
  - On new module: inserts `modules` row; if `apparatus_name` provided, inserts `module_apparatus` row
  - On existing module (matched by path): updates `modules` fields; leaves `module_apparatus` untouched — membership changes are a separate operation
  - Validates `module_type` in Python before writing
  - Returns UUID
- [x] `get_module_by_uuid(uuid: str) -> dict | None`
- [x] `get_module_by_path(path: Path) -> dict | None` — matches on absolute resolved path
- [x] `is_module_registered(uuid: str) -> bool`
- [x] `update_module_sync(uuid: str) -> None` — updates `last_synced_at = datetime.now().isoformat()`
- [x] `decimate_module(uuid: str) -> None` — stamps `decimated_at`; raises `ValueError` if UUID not found
- [x] `reactivate_module(uuid: str) -> None` — clears `decimated_at`; raises `ValueError` if UUID not found

**Bay management:**
- [x] `add_module_to_bay(container_uuid: str, contained_uuid: str) -> None` — `INSERT OR IGNORE`; no-op if row exists
- [x] `remove_module_from_bay(container_uuid: str, contained_uuid: str) -> None` — no-op if row absent
- [x] `remove_all_bays_for_contained(contained_uuid: str) -> None`
- [x] `get_module_bays(contained_uuid: str) -> list[dict]` — all containers for this module, any type

**Apparatus membership:**
- [x] `add_module_to_apparatus(module_uuid: str, apparatus_uuid: str) -> None` — `INSERT OR IGNORE`; no-op if row exists
- [x] `remove_module_from_apparatus(module_uuid: str, apparatus_uuid: str) -> None` — no-op if row absent
- [x] `remove_all_apparatus_memberships(module_uuid: str) -> None`
- [x] `get_module_apparati(module_uuid: str) -> list[dict]` — all apparati for this module, sorted by name

**Queries:**
- [x] `get_apparatus_modules(apparatus_name, include_decimated=False) -> list[dict]`
  - JOINs through `module_apparatus`; sorted by name; excludes decimated by default
  - Previously queried `modules.apparatus_uuid` directly — that column no longer exists
- [x] `get_bay_modules(container_uuid, include_decimated=False) -> list[dict]` — any superproject type
- [x] `get_vault_modules(vault_uuid, include_decimated=False) -> list[dict]` — validates container is type `vault`; delegates to `get_bay_modules()`

**Deferred (do not implement in Phase 1):**
- [x] `commit_registry(message: str) -> None` — future automated backup
- [x] `push_registry() -> None` — future automated backup

**Barrel export:**
- [x] `from archivist.utils.registry import *  # noqa: F401, F403` in `archivist/utils/__init__.py`

> ⚠️ The barrel uses wildcard imports by design (see `__init__.py` header). `registry.py` must define `__all__` listing only its public functions, or the wildcard will export private helpers and internal names. Check the existing modules for the convention — if they don't define `__all__`, follow whatever pattern is already established. Do not introduce a new inconsistency.

---

## 5. `ConfigSchema` TypedDict

- [x] Define `ConfigSchema` in `archivist/utils/config.py` using the functional TypedDict form
- [x] All fields `NotRequired` (`total=False`) — config can be partial at any stage
- [x] Fields: `uuid`, `module-type`, `apparati`, `vaults`, `git-remote`, `git-remote-name`, `library-tag`, `works-dir`, `changelog-output-dir`, `templater`, `ignores`
  - Note: field is `apparati: list[str]`, not `apparatus: str` — see §13.2 for the change history
- [x] Update `read_archivist_config` return type: `dict[str, str | list[str]] | None` → `ConfigSchema | None`
- [x] Update `write_archivist_config` parameter type: `dict` → `ConfigSchema`
- [x] `write_archivist_config` renders any list-typed field as a YAML block sequence — not just `ignores`. The generic handling covers `apparati`, `vaults`, and any future list fields without additional special-casing
- [x] Audit every call site of `read_archivist_config` and `write_archivist_config` for type compatibility

> 🔴 The functional TypedDict form is required because hyphenated keys (`module-type`, `git-remote`, etc.) are not valid Python identifiers. The class-based form cannot express them. Do not attempt to use the class-based form — it will not work.
>
> ```python
> # Correct
> ConfigSchema = TypedDict('ConfigSchema', {
>     'module-type': str,
>     'git-remote': str,
>     ...
> }, total=False)
>
> # Wrong — SyntaxError waiting to happen
> class ConfigSchema(TypedDict, total=False):
>     module-type: str  # invalid identifier
> ```

> ⚠️ `total=False` means every field is `NotRequired` at the type-checker level. This reflects reality: config can be written incrementally. The type system won't enforce that `uuid` or `module-type` are always present — that's the write path's responsibility, not the TypedDict's.

> ⚠️ The `apparatus` field has been through two semantic revisions: `"true"/"false"` → apparatus name string → `apparati: list[str]`. Every call site that reads `config.get("apparatus")` is wrong and must be updated to `config.get("apparati", [])`. Search the entire codebase before shipping.

> 📌 `uuid` must be the first field written to `config.yaml`. The `write_archivist_config` function builds lines from the config dict — Python dicts preserve insertion order (3.7+), but the caller must pass `uuid` first. The init flow must ensure this. Verify the write output, not just the dict construction.

---

## 6. `archivist init` Augmentation

> 🚫 **Blocked on:** `registry.py` (§4) and `ConfigSchema` (§5) complete.

> 📌 **Scope grew mid-implementation.** `archivist migrate` was cut (see intro note above), and its two jobs — resurrecting a legacy flat `.archivist` file, and backfilling a `uuid` onto a config that never got one — landed here instead, folded into the "existing config" branch `init` already needed. `init` was always going to be the command that inspects `.archivist/config.yaml` and decides what's missing; teaching it two more "missing" cases was less surface area than a whole extra command that only ever delegated back to it anyway. `sync` (§9a) is the thing that actually calls `init` when it hits one of these cases non-interactively.

- [x] Git context check: look for `.git` in the working directory **before** calling `get_repo_root()`
  - Found: proceed normally
  - Not found: prompt to run `git init`; decline → exit cleanly; accept → run it via subprocess, then proceed
- [x] Legacy flat-file detection: capture `was_flat_config` (existing config found at the flat `.archivist` path, not the directory form) **before** any write touches it — `write_archivist_config()` silently evicts the flat file the instant it writes the directory form, so this is the only point in the run where the question is still answerable
- [x] After existing config flow: check for `~/.archivist/`
  - Absent: call `init_registry()`; prompt for registry remote URL (see below)
  - Present: proceed directly to apparatus registration
- [x] Apparatus registration, now multi-select (per the §13 remedial multi-apparati work — this shipped as multi-select from the start, not single-then-upgraded):
  - Prompt: is this module part of an Apparatus?
  - Yes → `prompt_apparatus_names()`: numbered menu of existing apparati plus "Create new"; accepts multiple selections per round; loops until the user is done adding
    - New apparatus → `register_apparatus()`; create apparatus DB
    - Existing → confirm; proceed to `register_module_with_apparati()`; insert `module_apparatus` row(s)
  - No → skip registry writes; UUID still generated
- [x] Module-type-specific prompts: `works-dir` for `library` modules; `changelog-output-dir` (optional, any module type); Templater mode (`resolve` / `preserve` / `false`, default `preserve`)
- [x] `git_remote` + `git_remote_name` resolution: follow spec §10 workflow — apparatus modules only, not standalone
- [x] UUID: generate if absent (or reuse the existing one for a standalone module being updated); registry upsert runs UUID → config for apparatus modules; write to config as **first field** either way
- [x] `apparati` config field: write the full list from `prompt_apparatus_names()`; render as YAML block sequence
- [x] Upsert `modules` row via `register_module_with_apparati()` (the same shared helper `add` uses — see `CODE_CONVENTIONS.md`); `module_apparatus` row(s) inserted as part of that call, not separately
- [x] For `library` modules: write `.archivist/sample-changelog.py` from the bundled package resource, unless one already exists (never clobber a live plugin or an existing sample)
- [x] Stage `.archivist/` after writing; if `was_flat_config`, stage the flat file's deletion in the same call so both halves land as one logical change — non-fatal on failure, warn with manual `git add` instructions
- [x] Containment check (module being init'd is itself nested in a git submodule): resolve via `get_superproject_root()` + `resolve_container_module()`, same UUID-based resolution `add` and `sync` use — link into the container's bay if found, apparatus modules only
- [x] Dry-run: print git init command and all registry operations; execute neither

> 🔴 The git check must happen **before** `get_repo_root()`. The existing code calls `get_repo_root()` at the top of `run()`. That call shells out to `git rev-parse --show-toplevel`, which exits non-zero if there's no git repo — which means it calls `sys.exit(1)` before init gets a chance to run `git init`. Fix the order. Check for `.git` first, optionally run `git init`, then call `get_repo_root()`.

> ⚠️ When prompting for apparatus name(s), `prompt_apparatus_names()` presents any existing apparatus names from `registry.db` so the user can confirm they're adding to an existing one rather than accidentally creating a duplicate with a typo. The prompt matters here — `"writing"` and `"Writing"` should not both exist.

> ⚠️ **Legacy flat-file and missing-uuid handling is now init's job, not a separate command's — and it doesn't special-case old field formats to do it.** There's no `apparatus: "true"` / `apparatus: "<name>"` detection logic anywhere in `init.py`. On a confirmed "Update configuration?", `final_config` is rebuilt entirely from fresh prompts — only `existing_uuid` is carried forward from the old config, nothing else. Any legacy `apparatus` (singular) key just isn't in the fields `init` writes back, so it's gone on the next write, no translation step required. The flat-file eviction is the same story: `write_archivist_config()` always writes the directory form, so a flat `.archivist` file simply stops being read once `config.yaml` exists — `was_flat_config` exists purely so `_stage_archivist_config()` can also stage that deletion, not to drive any content migration. `sync` detects both conditions (no config at all, or a config with no `uuid`) and calls `init.run()` directly rather than reimplementing any of this.

> 📌 The `vaults` field in config is populated by `archivist add` (or `archivist init`'s own containment check, for a module that's a submodule but wasn't added through `archivist add`) when this module is linked into a superproject. Do not prompt for it directly.

---

## 7. `archivist add` (New Command)

> 🚫 **Blocked on:** `registry.py` (§4) complete.

- [x] Context detection: check for `.git` in the current working directory
  - No `.git` → `git clone <url> [path] [passthrough]`
  - `.git` found → `git submodule add <url> [path] [passthrough]`
- [x] Execute git operation with all passthrough arguments
  - On failure: propagate exit code and stderr verbatim; abort; **no registry changes**
- [x] Enter target module directory (resolved from path arg or inferred from URL stem)
- [x] UUID resolution — four cases, in order:
  - UUID in config + `decimated_at` set in registry → reactivation: call `reactivate_module()`; restore `module_apparatus` and `module_bays` rows
  - UUID in config + active in registry → `add_module_to_bay()` if applicable; `add_module_to_apparatus()` if not already a member; done
  - UUID in config + not in registry → register using config values as defaults
  - No config → full interactive registration (same flow as `archivist init` apparatus registration)
- [x] Generate UUID if absent; write to `config.yaml` as first field
- [x] `git_remote`: store the URL from the `add` argument directly
- [x] `git_remote_name`: query `git remote -v` in the target module after git operation completes; find the name associated with this URL; store it; NULL if not found
- [x] Upsert `modules` row via `register_module()`
- [x] Insert `module_apparatus` row for the apparatus
- [x] Bay management: **shipped differently than originally drafted.** Rather than `get_module_by_path(cwd)`, the container is resolved the same UUID-based way `init` and `sync` do it — `resolve_container_module(get_repo_root())`, gated on `is_submodule_context and is_module_registered(module_uuid)` — then `link_module_into_container(container_row, module_uuid, target_path)`. Path-keyed lookup was a trap here for the same reason it's a trap everywhere else in this codebase: it goes stale the moment a directory is renamed or moved, where the config file's own `uuid` doesn't. See `resolve_container_module()`'s docstring.
- [x] Update `vaults` list in target module's `config.yaml` if superproject is a vault-type module
- [x] Install git hooks into target module
- [x] Dry-run: print git command and registration plan; execute neither

> 🔴 If the git operation fails, **stop**. Write nothing to the registry. The git step is the gate.

> ⚠️ Bay management: only add a `module_bays` row if the current working directory is a **registered** module. Being inside a git repo is not sufficient — the repo must be in the registry.

> ⚠️ The reactivation path (decimated module re-added) must clear `decimated_at` before adding to bays or apparatus memberships. A module with `decimated_at` set must not appear in active queries even if junction table rows exist. Clear first, re-associate second.

> 📌 The `vaults` field in the target module's `config.yaml` records which vault(s) contain it — human-readable vault names, not UUIDs. Both the config (`vaults`) and the DB (`module_bays`) must be updated. If the superproject is not type `vault`, do not add it to the `vaults` list. The `module_bays` row still gets created — containment is containment regardless of type.

---

## 8. `archivist deinit` (New Command)

> 🚫 **Blocked on:** `registry.py` (§4) complete.

> 🔴 **Operation order is not negotiable: Apparatus first. Git second. Always.** If git runs first and succeeds, `config.yaml` is gone — the registry has lost its recovery information. If Apparatus cleanup runs first and fails, the module is still on disk and the user can retry. The inverse failure mode is unrecoverable. Do not reverse this order under any circumstances, not even in edge cases, not even "just this once."

- [x] Look up module by path (`get_module_by_path(Path(args.path).resolve())`)
  - Not found: warn clearly; exit; do nothing
- [x] Confirmation prompt — fires even with `--dry-run`
- [x] Apparatus cleanup (first):
  - Determine context: is the current working directory a registered superproject of the target?
    - Yes (vault-context removal) → remove only the `module_bays` row for (cwd superproject, target module); leave module active
    - No (standalone removal) → `remove_all_bays_for_contained(target_uuid)`; `remove_all_apparatus_memberships(target_uuid)`; `decimate_module(target_uuid)`
  - Update `vaults` list in target module's `config.yaml` to remove this superproject (if applicable)
- [x] Git cleanup (second):
  - Resolve whether target is a git submodule: check parent repo's `.gitmodules`
  - Submodule → `git submodule deinit [passthrough] <path>` then `git rm <path>`
  - Not a submodule → `shutil.rmtree(path)`
    - `PermissionError` → print path; instruct manual removal; exit 1; do not sudo
  - Any git failure → warn; print recovery instructions; exit 1
- [x] `--retain`: runs Apparatus cleanup only; skips git operation entirely
- [x] Idempotency: detect already-cleaned state before each step
  - No bay rows to remove → skip bay cleanup silently
  - No apparatus membership rows to remove → skip apparatus cleanup silently
  - `decimated_at` already set → skip decimation silently
  - Module path already gone from disk → skip git step; warn
- [x] Dry-run: print Apparatus changes and git command; execute neither; confirmation prompt still fires

> ⚠️ **Decimation in vault-context removal:** Vault-context deinit removes the bay row and leaves the module active. It does NOT check remaining bay or apparatus membership counts and does NOT decimate based on those counts. Only standalone-removal mode decimates. Getting this logic inverted orphans active modules or incorrectly tombstones them.

> ⚠️ **Idempotency is load-bearing.** A deinit that partially succeeds (Apparatus cleaned, git failed) must be re-runnable. On re-run: no bay rows exist (already removed), `decimated_at` may already be set — skip those steps, detect that the module path is still on disk (or not), proceed to git. This must work without user intervention or manual registry edits.

> ⚠️ Confirmation prompt fires even with `--dry-run`. A dry run that skips confirmation gives no useful information about what would actually happen, because the prompt is part of what happens. Don't skip it.

> 📌 `--retain` is the manual recovery tool for the scenario where the git operation already ran (manually or in a previous failed attempt) and only the registry needs cleaning. Document this in the help text.

> 📌 When printing recovery instructions after a git failure, be specific: "Registry has been updated. The module directory at `<path>` still exists. Remove it manually with `git submodule deinit <path> && git rm <path>`, then run `archivist deinit --retain <path>` to complete cleanup." Vague "something went wrong" messages are useless here.

---

## 9. `archivist migrate` — Cut

> ❌ **Not implemented. This command does not exist.** The original plan called for `archivist migrate` to handle `apparatus` field format migration (`"true"`/`"false"`/name-string → `apparati: list[str]`) and legacy flat-`.archivist`-file eviction as a standalone command. During implementation, both jobs turned out to be strict subsets of what `init`'s "existing config" branch already had to do — see §6's note above for the mechanism (full config rebuild on confirmed update, `was_flat_config` capture before the write). Adding a second command that only ever delegated to the first added a parser, a help entry, and a decision point ("do I run init or migrate?") for zero net capability. It was cut before shipping.
>
> If you're implementing against an earlier draft of this checklist and see references to `archivist migrate` elsewhere in this document outside this section, they're being corrected in place rather than left to rot — see §9a, §13.5, §13.6, and the Completion Gates below.

---

## 9a. `archivist sync` (New Command)

> 🚫 **Blocked on:** `registry.py` (§4) and `archivist init` (§6) complete.

Non-interactive registry backfill. `sync` is what `migrate` might have grown into if it had stayed a separate command — but scoped to a different problem than migrate ever solved: modules that already have a *valid, current-format* config (a `uuid`, no legacy flat file) but were never linked into `~/.archivist/`, or whose registry entry has gone stale. Typical triggers: a submodule added by hand with `git submodule add`, bypassing `archivist add` entirely; a vault with nesting older than the containment logic; a directory that got renamed or moved since it was last registered.

- [x] Reads `.archivist` config at the repo root via `read_archivist_config()` + `get_archivist_config_path()`
- [x] Three non-interactive-resolution failures, each handed off to `init.run(args)` rather than solved here — `sync` never prompts and never invents an apparatus assignment nobody chose:
  - No config at all → hand off to `init`
  - Config found at the legacy flat `.archivist` path → hand off to `init`
  - Config found, directory form, but missing `uuid` → hand off to `init`
- [x] Otherwise, walks the submodule tree from the repo root **recursively** via `list_direct_submodules()` — any module type, not just vaults, since nesting one module inside another isn't a vault-exclusive privilege
- [x] Per node (`_sync_node`): if the node has its own config with a `uuid`, calls `_register_or_refresh()`:
  - Module found in registry, `decimated_at` set → `reactivate_module()`
  - Module found in registry, active → if the config declares `apparati`, re-runs `register_known_module_with_apparati()` to refresh path/type/remote and pick up any newly-declared apparatus membership; otherwise nothing to do
  - Module not found in registry, config declares `apparati` → `register_known_module_with_apparati()` (the "known UUID" twin of `register_module_with_apparati()` — writes the UUID the config already declares instead of minting a new one; see `registry.py`'s docstring on why path-based lookup alone is a trap)
  - Module not found in registry, config declares no `apparati` → reported as skipped; tells the user to run `archivist init` there to decide
- [x] If registered (or would be, under `--dry-run`), resolves the node's own container via `get_superproject_root()` + `resolve_container_module()` and links into the bay if one is found — every node resolves its own container independently rather than threading one down through the recursion, which is what makes this correct at arbitrary nesting depth
- [x] Accumulates and reports `(linked_count, skipped_count)` across the whole subtree
- [x] Registry schema initialized (`init_registry()`) if `~/.archivist/` doesn't exist yet and this isn't a dry run
- [x] Dry-run: every registry-mutating branch is mirrored with a `[dry-run]` preview line; nothing is written

> 🔴 **`sync` never guesses at an apparatus assignment.** A module with a `uuid` but no `apparati` declared in its own config is reported as skipped, not silently registered standalone or prompted for. Guessing here is exactly the kind of thing that quietly corrupts a registry with associations nobody actually chose — that decision belongs to a human running `init`, not to a recursive tree walk.

> ⚠️ Lookup by UUID, not by path, is load-bearing here — same reasoning as `resolve_container_module()`'s docstring. `register_known_module_with_apparati()` exists specifically because the ordinary `register_module_with_apparati()` has no way to say "trust me, this exact UUID, I got it from the file" — it always resolves existing rows by path, which is a cache, not the module's actual identity.

> 📌 `sync` is deliberately **not** `init`. It never asks a single question. Anything it can't resolve from what's already committed to disk gets reported and skipped, full stop — the fix is to go run `archivist init` there yourself.

---

## 10. Hook Augmentation

> 🚫 **Blocked on:** `registry.py` (§4) complete.

- [x] Add registry sync step to `PRE_COMMIT_HOOK` constant in `archivist/commands/hooks/install.py`, after the existing changelog/manifest check
- [x] Sync logic: if `archivist` on PATH and `~/.archivist/` exists, call `archivist _registry-sync`
- [x] Sync is non-blocking: failure prints a warning; commit proceeds regardless
- [x] Implement `_registry-sync` as an internal subcommand (not user-facing; prefix with `_` in help suppression)
  - Read UUID from `.archivist/config.yaml`
  - Look up module in registry; if not found, exit 0 silently (unregistered modules are not an error)
  - Call `update_module_sync(uuid)` — updates `last_synced_at`
  - Exit 0 always (non-blocking contract)
- [x] Add `_registry-sync` parser to `cli.py` with `help=argparse.SUPPRESS`
- [x] Existing hooks are not auto-updated — users must re-run `archivist hooks sync` or `archivist hooks install` to get the new hook content. Document this prominently in release notes.

> ⚠️ The updated `PRE_COMMIT_HOOK` constant in `install.py` only affects **new** hook installations. Every repo with an existing hook has the old version. There is no auto-update mechanism. Users must be told to run `archivist hooks sync` after upgrading. This is not a bug; it is the expected behavior of installed git hooks. Be explicit about it.

> ⚠️ `_registry-sync` must exit 0 in every scenario — not found in registry, registry DB missing, connection error, anything. This command runs on every commit. A non-zero exit from the pre-commit hook aborts the commit. A registry sync failure must never abort a commit.

> 📌 `_registry-sync` is intentionally not user-facing. Suppress it from help output with `help=argparse.SUPPRESS`. It is called by the hook, not by the user.

---

## 11. CLI Parser Updates (`cli.py`)

> 🚫 **Blocked on:** commands (§6, §7, §8, §9a) complete enough to wire up.

> 📌 **`nargs=argparse.REMAINDER` did not survive contact with reality.** It's gone from the shipped implementation. `add` and `deinit` both accept arbitrary git flags forwarded verbatim to the underlying git command, interleaved with archivist's own flags (`--dry-run`, `--retain`) and — for `add` — a positional `path` that can itself look like a flag's value. `REMAINDER` can't disambiguate any of that; it just grabs everything after the first thing it doesn't recognize, which breaks the moment a git flag appears before the url/path instead of after. The actual solution lives entirely in `cli_helpers.py`, not in per-command parser tweaks — see below.
- [x] All reusable argparse wiring — the subparser wrapper, `--dry-run`, the commit-sha positional, the note-selection argument group, and the git-passthrough resolution for `add`/`deinit` — now lives in `archivist/utils/cli_helpers.py`, not inline in `cli.py`. `cli.py` composes these helpers; it does not redefine them per-subcommand. See `cli_helpers.py`'s own module docstring: nothing outside `cli.py` should ever import from it.
- [x] `subparser(subparsers_obj, name, **kwargs)` — thin wrapper around `add_parser()` that defaults `formatter_class` to `ArchivistHelpFormatter` every time, so that's not a kwarg every one of the ~20 subcommands has to remember
- [x] `add_dry_run(parser, help=...)` — attaches the standard `--dry-run` flag with a sensible default help string, overridable per-subcommand
- [x] `add_commit_sha_arg(parser)` — attaches the optional `commit_sha` positional shared by `manifest` and every `changelog` subcommand except `seal`
- [x] `add_note_selection_args(parser, *, require_one=False)` — attaches `--file`, `--path`, `--class`/`-c`, `--class-property`, `--tag` as a group, shared across all `frontmatter` subcommands and `reclassify`
- [x] `archivist add` parser — real shape, not the originally-drafted one:
  ```python
  add_module_p = subparser(subparsers, "add", help = "...", description = "...")
  add_module_p.add_argument("url", help = "Remote URL to clone or add as a submodule.")
  add_module_p.add_argument("path", nargs = "?", default = None, metavar = "PATH", help = "...")
  add_dry_run(add_module_p, help = "...")
  ```
  No `passthrough` argument on the parser at all — `main()` builds `args.passthrough` itself after parsing, via `split_git_passthrough()` (see below).
- [x] `archivist deinit` parser — same story:
  ```python
  deinit_p = subparser(subparsers, "deinit", help = "...", description = "...", epilog = fmt_warning(...))
  deinit_p.add_argument("path", help = "Path to the module to remove.")
  deinit_p.add_argument("--retain", action = "store_true", help = "...")
  add_dry_run(deinit_p, help = "...")
  ```
- [x] Git-flag passthrough resolution — the actual replacement for `REMAINDER`, all in `cli_helpers.py`:
  - `locate_git_target(tokens)` — finds the token that structurally IS the git target (the url for `add`, the path for `deinit`) by matching git's own disambiguation shape (`scheme://`, `user@host:`, leading `./`, `../`, `~`, or `/`); falls back to the first non-flag token if nothing matches the shape
  - `split_git_passthrough(tokens)` — splits the tokens following `add`/`deinit` into `(git_passthrough, remainder)`; an explicit `--` short-circuits the shape detection entirely, same as git's own convention
  - `find_subcommand(argv)` — scans raw `argv` for the subcommand name, skipping past archivist's own known global flags (`--quiet`, `--verbose`, `--log-file`, etc.) along the way, so `--quiet add ...` and plain `add ...` resolve identically regardless of flag placement; bails (returns `None`) rather than guessing wrong on `-h`/`--version`/an unrecognized flag
  - `main()` calls `find_subcommand()` first; if it identifies `add` or `deinit`, it calls `split_git_passthrough()` on the remainder and sets `args.passthrough = git_passthrough + unrecognized` after `parser.parse_known_args()` runs on the archivist-only remainder
  - `split_passthrough(argv)` — a much dumber sibling used for every *other* command: splits raw argv on the first literal `--`, before argparse ever sees it, because `REMAINDER` plus a `--` separator plus an optional positional is a combination argparse gets wrong in its own special way
- [x] `archivist _registry-sync` parser (internal):
  ```python
  subparser(subparsers, "_registry-sync", help = argparse.SUPPRESS, description = argparse.SUPPRESS)
  ```
- [x] `archivist sync` parser — new, not in the original plan:
  ```python
  sync_p = subparser(subparsers, "sync", help = "...", description = "...")
  add_dry_run(sync_p, help = "...")
  ```
- [x] Dispatch: `elif args.command == "add"`, `"deinit"`, `"sync"`, `"_registry-sync"` branches, each lazily importing its command module (consistent with every other branch — nothing imports a command module at parser-build time)
- [x] `init_p` parser unchanged from the original plan — no new arguments; registration is fully interactive

> ⚠️ Test the passthrough resolution with flags like `--depth 1` positioned *before* the url/path, not just after — that's the exact case `REMAINDER` couldn't handle and the entire reason `locate_git_target`/`split_git_passthrough` exist. `archivist add --depth 1 git@github.com:user/repo.git modules/repo` must forward `--depth 1` to git, not swallow it into archivist's own `path` positional.

> 📌 Per `CLAUDE.md`: `cli.py` parser definitions are in the "What Not to Touch" category unless adding or removing a subcommand. These additions qualify. Be surgical — add the new parsers without touching the existing ones. Any *reusable* wiring, though, belongs in `cli_helpers.py`, not copy-pasted inline — see `CODE_CONVENTIONS.md`'s "shared helpers belong in utils" and the barrel-export rule: command modules that need these helpers import them from `archivist.utils.cli_helpers` directly (this one file is the documented exception to the barrel rule, per its own module docstring — it's CLI-parser-only wiring, never imported by anything except `cli.py`).

---

## 12. Testing: Phase 1

Run existing tests before writing a single new one. If anything is red before you start, stop and fix it.

**Unit tests: `tests/unit/test_registry.py` (new file)**

- [x] `get_registry_dir()` returns `Path.home() / ".archivist"`
- [x] `get_registry_path()` returns correct path relative to registry dir
- [x] `get_apparatus_db_path("writing")` returns `~/.archivist/writing.db`
- [x] `init_registry()` creates directory, creates `registry.db`, creates schema
- [x] `init_registry()` is idempotent — calling twice raises no error and corrupts no data
- [x] `get_registry_connection()` returns connection with FK enforcement ON
  - Verify: attempt FK violation; confirm it raises `IntegrityError`
- [x] `register_apparatus()` creates row; returns UUID; creates apparatus DB
- [x] `register_apparatus()` with same name twice → upsert, not duplicate
- [x] `register_module()` with `apparatus_name` — inserts `modules` row AND `module_apparatus` row
- [x] `register_module()` with `apparatus_name=None` — inserts `modules` row; no `module_apparatus` row
- [x] `register_module()` with invalid `module_type` → raises before writing
- [x] `register_module()` on existing path — updates `modules` row; does NOT touch `module_apparatus`
- [x] `get_module_by_uuid()` happy path and not-found (returns `None`)
  - Note: returned dict contains no `apparatus_uuid` key — that column no longer exists
- [x] `get_module_by_path()` happy path and not-found (returns `None`)
- [x] `get_module_by_path()` resolves symlinks and relative paths to absolute before querying
- [x] `is_module_registered()` true and false cases
- [x] `decimate_module()` stamps `decimated_at`
- [x] `decimate_module()` with unknown UUID → raises `ValueError`
- [x] `reactivate_module()` clears `decimated_at`
- [x] `reactivate_module()` with unknown UUID → raises `ValueError`
- [x] `add_module_to_bay()` creates row; no-op on duplicate (no error)
- [x] `remove_module_from_bay()` removes row; no-op if absent (no error)
- [x] `remove_all_bays_for_contained()` removes all rows for target; leaves other rows intact
- [x] `get_module_bays()` returns all containers; empty list if none
- [x] `add_module_to_apparatus()` creates row; no-op on duplicate (no error)
- [x] `remove_module_from_apparatus()` removes row; no-op if absent (no error)
- [x] `remove_all_apparatus_memberships()` removes all rows for target; leaves other module_apparatus rows intact
- [x] `get_module_apparati()` returns all apparati for module; sorted by name; empty list if none
- [x] `get_apparatus_modules()` — returns modules via module_apparatus JOIN; excludes decimated by default; includes with flag; sorted by name
- [x] `get_apparatus_modules()` — module in two apparati: appears in results for both; counted once in each
- [x] `get_bay_modules()` scoped to container; excludes decimated by default
- [x] `get_vault_modules()` with non-vault container UUID → raises `ValueError`
- [x] Registry isolation: all tests use `tmp_path`; none touch real `~/.archivist/`

> 🔴 **Tests must never touch `~/.archivist/`.** Use `monkeypatch` to override `get_registry_dir()` to return a path inside `tmp_path` for every test in this module. This is not optional. A test that writes to the real registry contaminates the developer's machine and produces results that depend on machine state.

> 📌 Pattern:
> ```python
> @pytest.fixture(autouse=True)
> def isolated_registry(tmp_path, monkeypatch):
>     monkeypatch.setattr("archivist.utils.registry.get_registry_dir", lambda: tmp_path / ".archivist")
> ```
> Apply this as an `autouse` fixture to the entire test module so no test can accidentally escape it.

**Unit tests: `tests/unit/test_config.py` additions**

- [x] `ConfigSchema` is a valid TypedDict; can be instantiated with a subset of fields
- [x] `read_archivist_config()` returns a value compatible with `ConfigSchema`
- [x] `write_archivist_config()` accepts a `ConfigSchema`-typed dict
- [x] `write_archivist_config()` renders `apparati` as YAML block sequence (not inline list)
- [x] `write_archivist_config()` renders `vaults` as YAML block sequence
- [x] `write_archivist_config()` renders `ignores` as YAML block sequence with quoted values
- [x] `uuid` is first key written by `write_archivist_config()` when present
- [x] `config.get("apparatus")` returns `None` — the key no longer exists; callers use `apparati`

**Integration tests: `tests/integration/{test_add, test_deinit}.py` (new files)**

All integration tests use `git_repo` fixture with `monkeypatch.chdir()`. All tests isolate the registry via `monkeypatch` on `get_registry_dir()`.

`archivist add`:
- [x] In non-git directory: `git clone` runs; module registered; no bay row (no superproject)
- [x] In git repo: `git submodule add` runs; module registered; bay row created if cwd is registered
- [x] In git repo with unregistered cwd: module registered; **no** bay row created
- [x] Git failure: no registry changes; exits with propagated exit code
- [x] Decimated module re-added: `decimated_at` cleared; bay row and `module_apparatus` row restored
- [x] Active module re-added (already registered): bay row added if absent; `module_apparatus` row added if absent; no duplicate module row
- [x] `git_remote_name` populated from `git remote -v` after operation
- [x] `git_remote_name` is NULL if remote not yet registered in git
- [x] Dry-run: no git operation; no registry changes; plan printed

`archivist deinit`:
- [x] Happy path: bay row removed; `module_apparatus` rows removed; `decimated_at` set; git submodule deinit + rm runs
- [x] Vault-context removal: bay row removed; `module_apparatus` rows intact; `decimated_at` NOT set
- [x] Module in multiple bays: target bay removed; other bay intact; `decimated_at` NOT set
- [x] Standalone removal (no superproject context): all bays removed; all apparatus memberships removed; `decimated_at` set
- [x] `--retain`: registry cleaned; git untouched; module still on disk
- [x] Idempotency: re-run after Apparatus-cleaned-but-git-failed state → registry steps no-op; git step fires
- [x] Not found in registry: exits with warning; no changes
- [x] Confirmation prompt fires on dry-run
- [x] Dry-run: no registry changes; no git operation; plan printed
- [x] `PermissionError` on `shutil.rmtree`: prints path; instructs manual removal; does not crash

**Dry-run contract (both commands):**
- [x] `test_dry_run_writes_absolutely_nothing` — compare file sets before and after; compare registry state before and after

---

## Phase 1 Completion Gate

Before marking Phase 1 done and before opening Phase 2:

- [x] All tests pass: `pytest -v`
- [x] Unit tests pass without registry fixture: `pytest -m "not integration" -v`
- [x] No regressions in existing test suite
- [x] `archivist init` runs cleanly on a fresh directory (no git, no `.archivist/`)
- [x] `archivist add`, `archivist deinit`, and `archivist sync` are wired in `cli.py` and dispatch correctly
- [x] ~~`archivist migrate` handles all three `apparatus` field migration cases without error~~ — command cut; see §9
- [x] `archivist sync` correctly hands off to `init` for all three non-interactive-resolution failures (no config, legacy flat file, missing uuid) and never invents an apparatus assignment on its own
- [x] `archivist hooks sync` installs the updated pre-commit hook with registry sync step
- [x] Manual smoke test: init a new module; add a submodule; deinit the submodule; registry reflects decimation

---

## 13. Remedial Work: Multi-Apparati Support

> **Context:** Phase 1 was implemented with a one-to-many module-to-apparatus relationship — `apparatus_uuid TEXT NOT NULL` on the `modules` table. Before Phase 2 begins, a use case surfaced where a module legitimately belongs to multiple apparati: a shared library feeding two independent writing corpuses, for example. The constraint is wrong. This section covers the code changes required, and the steps to nuke the existing lean registry and rebuild it cleanly.
>
> The registry currently contains one vault and one module. A rebuild is the right call. Schema migrations for a lean registry in active development cost more to maintain than they save.

### 13.1 Schema: `registry.py` — `_create_registry_schema`

The `modules` DDL loses `apparatus_uuid`. The `module_apparatus` junction table is added.

- [x] Remove `apparatus_uuid TEXT NOT NULL REFERENCES apparati(uuid)` from the `modules` CREATE TABLE
- [x] Add `module_apparatus` table to the executescript:
  ```sql
  CREATE TABLE IF NOT EXISTS module_apparatus (
      module_uuid    TEXT NOT NULL REFERENCES modules(uuid),
      apparatus_uuid TEXT NOT NULL REFERENCES apparati(uuid),
      PRIMARY KEY (module_uuid, apparatus_uuid)
  );
  ```
- [x] Confirm the executescript order: `apparati` → `modules` → `module_bays` → `module_apparatus` (FK references must resolve top-down)

### 13.2 `registry.py` — Updated and New Functions

**`register_module` signature change:**

- [x] `apparatus_name: str` → `apparatus_name: str | None`
- [x] Remove the hard `apparatus = get_apparatus_by_name(apparatus_name)` / `if apparatus is None: raise` block from the function preamble; gate it on `apparatus_name is not None`
- [x] Remove `apparatus_uuid` from the `modules` INSERT statement and the VALUES tuple
- [x] After the new-module INSERT, if `apparatus` is not None: `INSERT OR IGNORE INTO module_apparatus (module_uuid, apparatus_uuid) VALUES (?, ?)`
- [x] On the update path (existing module matched by path): do not touch `module_apparatus` — membership changes are a separate explicit operation; remove the old `apparatus_uuid` from the UPDATE SET clause if it was there

**`get_apparatus_modules` query change:**

- [x] Replace `WHERE apparatus_uuid = ? AND decimated_at IS NULL` with a JOIN:
  ```sql
  SELECT m.*
  FROM modules m
  JOIN module_apparatus ma ON ma.module_uuid = m.uuid
  WHERE ma.apparatus_uuid = ? AND m.decimated_at IS NULL
  ORDER BY m.name
  ```
- [x] Same JOIN pattern for the `include_decimated=True` branch (drop the `AND m.decimated_at IS NULL` predicate)

**New apparatus membership functions (add after the Bay management section):**

- [x] `add_module_to_apparatus(module_uuid: str, apparatus_uuid: str) -> None`
  - `INSERT OR IGNORE INTO module_apparatus (...) VALUES (?, ?)`; idempotent
- [x] `remove_module_from_apparatus(module_uuid: str, apparatus_uuid: str) -> None`
  - `DELETE FROM module_apparatus WHERE module_uuid = ? AND apparatus_uuid = ?`; no-op if absent; do not raise
- [x] `remove_all_apparatus_memberships(module_uuid: str) -> None`
  - `DELETE FROM module_apparatus WHERE module_uuid = ?`
  - Used by standalone-removal `deinit`; does not touch the `modules` row
- [x] `get_module_apparati(module_uuid: str) -> list[dict]`
  - `SELECT a.* FROM apparati a JOIN module_apparatus ma ON ma.apparatus_uuid = a.uuid WHERE ma.module_uuid = ? ORDER BY a.name`
  - Returns apparatus row dicts; empty list if no memberships

**Header comment update:**

- [x] Add `Apparatus membership` to the public surface comment block:
  ```
  #   Apparatus membership  — add_module_to_apparatus, remove_module_from_apparatus,
  #                           remove_all_apparatus_memberships, get_module_apparati
  ```

### 13.3 `config.py` — ConfigSchema and `write_archivist_config`

**ConfigSchema field rename:**

- [x] Replace `'apparatus': str` with `'apparati': list[str]`
- [x] Update the inline comment: `# apparatus names; absent for standalone modules`

**`write_archivist_config` list handling generalization:**

The function currently special-cases only `ignores` as a block sequence. With `apparati` and `vaults` also being lists, generalize the logic:

- [x] Replace the current `if key == "ignores":` branch with a general `if isinstance(value, list):` branch
- [x] Inside that branch, keep the `ignores`-specific quoting: glob patterns get quoted; apparatus names and vault names do not
- [x] The empty-list sentinel (`[]`) behaviour stays the same for all list fields

```python
# Rough shape of the updated loop:
for key, value in config.items():
    if isinstance(value, list):
        lines.append(f"{key}:")
        for item in value:
            if key == "ignores":
                lines.append(f'  - "{ item }"')
            else:
                lines.append(f"  - { item }")
        if not value:
            lines.append("  []")
    else:
        lines.append(f"{key}: {value}")
```

### 13.4 `deinit` Cleanup Logic

The current implementation (if written before this section) may be checking `get_module_bays()` return count to decide whether to decimate. This logic is now wrong.

- [x] Audit `archivist deinit` — specifically the part that decides whether to call `decimate_module()`
- [x] Replace bay-count-based decimation trigger with context-based trigger:
  - **Vault-context removal** (cwd is a registered superproject of the target): remove the bay row; do NOT decimate; do NOT remove apparatus memberships
  - **Standalone removal** (no superproject context): `remove_all_bays_for_contained()`; `remove_all_apparatus_memberships()`; `decimate_module()`
  - The distinction is about HOW deinit was invoked, not about what's left in the tables after removal

### 13.5 v2 `apparatus` String Format — Superseded

> ❌ `archivist migrate` doesn't exist (§9). This subsection originally specified dedicated detection for `apparatus: "<name>"` (string — v2 format), on top of the `apparatus: "true"`/`apparatus: "false"` cases. None of that shipped as its own logic. By the time `registry.py` had the `apparati: list[str]` schema (§13.1–13.3) and the registry rebuild (§13.6) had happened, every project's config got rewritten from scratch through `init`'s "existing config → confirm update → rebuild from fresh prompts" path (see §6's note). A project still sitting on a v2 `apparatus: "<name>"` string just gets that field silently dropped and replaced with a correct `apparati` list the next time someone runs `archivist init` and confirms the update — no special-cased string/bool detection required, because `init` was never reading old fields back into the new config in the first place.

### 13.6 Registry Rebuild

> 📌 The existing registry has one vault and one module. Rebuilding from scratch is faster and cleaner than writing a migration. Do this once. The remedial code changes above ensure that the next `init_registry()` call produces the correct schema, and that re-running `archivist init` (or, for a module that already has a valid config, `archivist sync`) re-registers existing modules correctly.

- [x] Verify all code changes in §13.1–13.5 are complete and the existing test suite is green
- [x] Stop any running processes that may hold a connection to `~/.archivist/registry.db`
- [/] Back up the current registry if desired: `cp -r ~/.archivist ~/.archivist.bak`
- [x] Delete the existing registry: `rm -rf ~/.archivist`
  - This removes `registry.db` and any apparatus DBs
  - `~/.archivist/` itself is deleted; `init_registry()` will recreate it
- [x] From the vault module directory: run `archivist init`
  - This triggers first-run registry setup (mkdir, git init, schema)
  - Walks through apparatus registration with the new `apparati` config field
  - Registers the vault module; inserts `module_apparatus` row
- [x] From the vault module directory: run `archivist add <library-url>` (or `archivist sync` if the library is already on disk as a submodule with a valid config — `archivist init` there directly if it isn't)
  - Re-registers the library module; inserts `module_apparatus` and `module_bays` rows
- [ ] Verify directly against the rebuilt registry (`archivist remedy inspect` doesn't exist yet — that's Phase 3 §3, and it would just duplicate this check anyway):
  ```python
  from archivist.utils import get_module_by_uuid, get_module_apparati, get_module_bays
  # uuid values come from .archivist/config.yaml in each module
  print(get_module_apparati(vault_uuid))     # should list the apparatus
  print(get_module_apparati(library_uuid))   # should list the apparatus
  print(get_module_bays(library_uuid))       # should list the vault as container
  print(get_module_by_uuid(vault_uuid))      # eyeball it — no apparatus_uuid key
  ```
  - `module_apparatus` rows exist for both modules
  - `module_bays` row exists for (vault, library)
  - `modules` rows have no `apparatus_uuid` column

### 13.7 Tests: Multi-Apparati Additions

These tests build on the `test_registry.py` suite from §12. Add them to that file.

- [x] `register_module()` with `apparatus_name` — inserts `module_apparatus` row
- [x] `register_module()` with `apparatus_name=None` — no `module_apparatus` row
- [x] `register_module()` on existing path — `module_apparatus` rows unchanged
- [x] `get_apparatus_modules()` — module in two apparati: appears in both apparatus result sets
- [x] `get_apparatus_modules()` — returns correct modules after `remove_module_from_apparatus()`
- [x] `add_module_to_apparatus()` — idempotent (no error on duplicate)
- [x] `remove_module_from_apparatus()` — no-op if row absent
- [x] `remove_all_apparatus_memberships()` — removes all rows for module; other modules unaffected
- [x] `get_module_apparati()` — returns list sorted by apparatus name; empty list if no memberships
- [x] `get_module_by_uuid()` — returned dict does not contain `apparatus_uuid` key
- [x] Integration: `deinit` vault-context removal → `module_apparatus` rows intact
- [x] Integration: `deinit` standalone removal → `module_apparatus` rows removed; `decimated_at` set
- [x] `write_archivist_config()` with `apparati: ["writing", "cyber"]` → renders as block sequence
- [x] `write_archivist_config()` with `apparati: []` → renders as `apparati:\n  []`
- [x] ~~`migrate` with `apparatus: "writing"` (v2 string) → rewrites to `apparati:\n  - writing`~~ — superseded; no `migrate` command exists. Covered instead by an `init` integration test: existing config with a legacy `apparatus: "writing"` field, user confirms update → written config contains `apparati:\n  - writing` and no `apparatus` key

---

## Revised Completion Gate

The original gate had two items still open. They remain open, plus the remedial work:

- [x] ~~`archivist migrate` handles all `apparatus` field migration cases — including the new v2→v3 string→list transition~~ — superseded; see §13.5. `init`'s config-rebuild-on-update path handles this without dedicated migration logic
- [x] `archivist hooks sync` installs the updated pre-commit hook with registry sync step
- [x] Manual smoke test: init a fresh module; add a submodule to a second apparatus; verify `get_module_apparati()` returns both; deinit the submodule; verify decimation and apparatus membership removal
- [x] All tests in §12 and §13.7 pass
- [x] No regressions in the broader test suite (`pytest -v`)
- [x] `registry.db` schema verified correct: no `apparatus_uuid` column on `modules`; `module_apparatus` table exists