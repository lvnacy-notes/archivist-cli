---
class: spec
category:
  - feature
  - infrastructure
  - database
  - cli
  - git
affiliations:
created: 2026-05-21
modified: 2026-06-09
version: 3
related:
  - "[[APPARATUS_PLATFORM]]"
  - "[[AP_PHASE_1_IMPLEMENTATION]]"
  - "[[AP_PHASE_2_IMPLEMENTATION]]"
  - "[[AP_PHASE_3_IMPLEMENTATION]]"
  - "[[TESTING_SPECIFICATION]]"
tags:
  - apparatus-platform
---

**Supersedes:** `CENTRALIZED_DATABASE_SPEC`, `MULTI_VAULT_ORCHESTRATION_SPEC`, `GIT_INTEGRATION_SPEC`

Those three documents are archived. This is the authoritative specification.

```toc
```

---

## Version History

| Version | Date | Summary |
|---|---|---|
| 1 | 2026-05-21 | Initial specification |
| 2 | 2026-05-21 | Schema, CLI, and Phase 3 detail pass |
| 3 | 2026-06-09 | Multi-apparati support: `apparatus_uuid` removed from `modules`; `module_apparatus` junction table introduced; `ConfigSchema` `apparatus` field renamed `apparati` and widened to `list[str]`; `remedy move` semantics revised; deinit decimation logic clarified; "apparati" established as canonical plural |

> [!note] **What changed in v3 and why**
>
> The original design assumed a module belongs to exactly one apparatus — a reasonable assumption for a single writing project, where a library lives in one corpus and publication routes through one workflow. The assumption broke when modules were found to legitimately overlap: a shared reference library used by two independent apparati, a publication module that feeds both a writing corpus and a research apparatus.
>
> The fix follows the pattern already present in the codebase for vault containment: a junction table. `module_apparatus` is to apparatus membership what `module_bays` is to structural containment. The two dimensions are now parallel and independent — a module's apparatus memberships and its vault containment are separate relationships with separate query surfaces.
>
> Sections changed: §4.1 (schema), §4.3 (ConfigSchema), §5.3 (deinit), §5.7 (public surface), §6.2 (census), §7.3 (remedy move), §12 (open questions).

---

## Terminology

**Apparatus** (singular): A named collection of modules sharing a works catalog and cross-module changelog registry in a dedicated apparatus database.

**Apparati** (plural): The established plural for this project. "Apparatuses" is the dictionary plural. We are not a dictionary. A module may belong to multiple apparati. The junction table is `module_apparatus`. The config key is `apparati`. The function is `get_module_apparati`. Consistency over pedantry.

---

## 1. Overview

This document specifies the Apparatus Platform: the infrastructure layer that gives Archivist machine-level awareness of every registered module, vault, and apparatus. It covers the registry architecture, the git-integrated module lifecycle commands, and the multi-vault orchestration tooling built on top of that registry.

Three implementation phases:

- **Phase 1 — Registry and Git Integration:** the `~/.archivist/` registry, the apparatus DB schema, `archivist init` augmentation, and two new commands (`add`, `deinit`) that manage module membership and git operations together.
- **Phase 2 — Multi-Vault Orchestration:** `archivist census`, `archivist distribute`, and `archivist broadcast` — the read and fan-out commands that operate across registered modules.
- **Phase 3 — Registry Maintenance (`archivist remedy`):** the suite of tools for keeping the registry and per-module configs consistent without touching SQL directly. Covers config-driven sync, surgical field updates, module reassignment, orphan detection, and apparatus renaming.

Each phase depends on the previous being complete and stable. Implement in order.

---

## 2. Conceptual Hierarchy

The diagram below shows the expected arrangement. Any module type can serve as a git superproject — containment is not restricted to vaults. A module may belong to multiple apparati simultaneously.

```
Machine
└── ~/.archivist/
    ├── registry.db                  ← global: all apparati, modules, containment, apparatus membership
    ├── writing.db                   ← apparatus: works catalog, cross-module changelogs
    └── cyber.db                     ← apparatus: works catalog, cross-module changelogs

    Apparatus "writing"
    ├── Module: fiction-vault        (type: vault)
    │   ├── Module: cosmic-horror    (type: library)   ← also in apparatus "cyber"
    │   ├── Module: panopticon       (type: library)
    │   └── Module: silver-age      (type: story)
    ├── Module: research-vault       (type: vault)
    │   ├── Module: victorian-mayhem (type: library)
    │   └── Module: quarterly        (type: publication)
    └── Module: standalone-lib       (type: library, no containing vault)

    Apparatus "cyber"
    └── Module: cosmic-horror        (type: library)   ← same module, second apparatus
```

**Module:** Any Archivist-managed git repository. All modules are equal in kind. A module may belong to any number of apparati.

**Apparatus:** A named collection of modules that share a works catalog and a cross-module changelog registry in the apparatus DB. Apparatus membership is many-to-many: one module may belong to multiple apparati, and one apparatus contains many modules.

**Vault:** A module of type `vault`. By convention and expected practice, vaults serve as superprojects — git repositories that contain other modules as git submodules. However, any module type can serve as a superproject. `module_bays` records containment relationships between any two registered modules regardless of type. Vault is the expected container; it is not the only permitted one.

**Per-project database:** `ARCHIVE/archive.db`, inside each module. Serves the existing seal and publication pipeline. Unchanged by this work. The two layers — per-project and registry — coexist without overlap.

---

## 3. Storage Architecture

### 3.1 The Global Registry: `~/.archivist/`

One directory per machine. Contains `registry.db` and all apparatus databases.

```
~/.archivist/
├── .git/
├── registry.db
├── writing.db
└── cyber.db
```

**Version control strategy:** `~/.archivist/` is a git repository and is committed and pushed manually. Automated backup is a future augmentation. SQLite files are binary; git tracks changes to them correctly but cannot auto-merge two diverging versions. If a conflict occurs (two machines pushing concurrently), the resolution is explicit: overwrite local from the remote. The remote is treated as authoritative on conflict. For single-developer use this situation essentially never arises.

The registry remote is set during first-run `archivist init`. The user selects from available git remotes or enters a URL manually. Without a remote, `archivist restore` cannot function. Archivist warns clearly when this is the case but does not require it to proceed.

### 3.2 Per-project Database: `ARCHIVE/archive.db`

Inside each module's `ARCHIVE/` directory. Serves the seal and publication pipeline (`edition_shas`, `changelogs` tables). Not deprecated. Not replaced. The centralized layer aggregates data this layer produces; it does not absorb it.

### 3.3 Coexistence

| Layer | Location | Purpose |
|---|---|---|
| Per-project | `ARCHIVE/archive.db` | Seal and publication mechanics |
| Registry | `~/.archivist/registry.db` | Apparatus membership, module paths, containment |
| Apparatus | `~/.archivist/[name].db` | Cross-module works catalog, changelog aggregation |

These are separate concerns. Nothing in the per-project layer changes. Nothing in the registry layer replaces it.

---

## 4. Schema

### 4.1 registry.db

> [!note] **v3 change — `modules` table and `module_apparatus` junction table**
>
> The original `modules` table included `apparatus_uuid TEXT NOT NULL REFERENCES apparati(uuid)` — a hard one-to-many constraint that allowed each module exactly one apparatus. This column has been removed. Apparatus membership is now tracked via the `module_apparatus` junction table, mirroring the existing `module_bays` pattern for vault containment. The two dimensions — structural containment (bays) and logical grouping (apparatus membership) — are now parallel and independent.
>
> Any existing `registry.db` built against the v2 schema is incompatible. See the remedial migration section in `AP_PHASE_1_IMPLEMENTATION`.

```sql
CREATE TABLE IF NOT EXISTS apparati (
    uuid        TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    db_path     TEXT NOT NULL,      -- absolute path to apparatus DB file in ~/.archivist/
    created_at  TEXT NOT NULL,
    git_remote  TEXT                -- remote URL for the registry repo itself
);

CREATE TABLE IF NOT EXISTS modules (
    uuid            TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    -- apparatus_uuid REMOVED in v3. Apparatus membership is tracked in module_apparatus.
    module_type     TEXT NOT NULL,   -- story | publication | library | vault | general
    path            TEXT NOT NULL,   -- absolute local path; machine-specific
    git_remote      TEXT,            -- remote URL; required for archivist restore
    git_remote_name TEXT,            -- human-readable remote label e.g. "origin", "upstream"
    decimated_at    TEXT,            -- NULL = active; set = deregistered but history preserved
    last_synced_at  TEXT             -- ISO datetime; set by pre-commit hook on every commit
);

CREATE TABLE IF NOT EXISTS module_bays (
    container_id  TEXT NOT NULL REFERENCES modules(uuid),  -- the superproject module (any type)
    contained_id  TEXT NOT NULL REFERENCES modules(uuid),  -- the module inside it
    PRIMARY KEY (container_id, contained_id)
);

-- Many-to-many: a module may belong to multiple apparati.
-- Mirrors module_bays — same pattern, different dimension.
CREATE TABLE IF NOT EXISTS module_apparatus (
    module_uuid    TEXT NOT NULL REFERENCES modules(uuid),
    apparatus_uuid TEXT NOT NULL REFERENCES apparati(uuid),
    PRIMARY KEY (module_uuid, apparatus_uuid)
);
```

**`decimated_at`:** Modules are never hard-deleted. Deregistration sets `decimated_at`. History is preserved. `archivist add` on a decimated module reactivates it by clearing `decimated_at` and restoring the appropriate `module_bays` and `module_apparatus` rows.

**`last_synced_at`:** Set by the pre-commit hook on every upsert. Read by `archivist census` to show how fresh path data is. Stale timestamps signal a module that hasn't committed recently or hasn't been syncing.

**`git_remote`:** The stable, machine-agnostic URL for each module. `archivist restore`'s source for clone URLs. Must be populated at registration time — see §9 for how.

**`git_remote_name`:** The human-readable label git associates with that URL on this machine (e.g., `origin`, `upstream`). Populated automatically alongside `git_remote` — the user is never asked to type it. NULL if no matching remote name can be resolved.

### 4.2 Apparatus Database (`~/.archivist/[name].db`)

Each apparatus has its own database. Schema is identical across all apparatus DBs.

```sql
CREATE TABLE IF NOT EXISTS changelogs (
    uuid        TEXT PRIMARY KEY,
    commit_sha  TEXT,
    log_scope   TEXT,
    module_uuid TEXT NOT NULL,  -- logical FK to registry.db modules.uuid; cross-DB, unenforced by SQLite
    created_at  TEXT NOT NULL,
    sealed_at   TEXT,
    file_path   TEXT
);

CREATE TABLE IF NOT EXISTS works (
    uuid        TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    module_uuid TEXT NOT NULL,  -- logical FK to registry.db modules.uuid; cross-DB, unenforced by SQLite
    work_stage  TEXT,
    created_at  TEXT NOT NULL,
    modified_at TEXT
);

CREATE TABLE IF NOT EXISTS authors (
    uuid           TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    apparatus_uuid TEXT NOT NULL,  -- logical FK to registry.db apparati.uuid; cross-DB, unenforced by SQLite
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS works_authors (
    work_uuid   TEXT NOT NULL REFERENCES works(uuid),
    author_uuid TEXT NOT NULL REFERENCES authors(uuid),
    PRIMARY KEY (work_uuid, author_uuid)
);
```

The `changelogs` table here is cross-module aggregation. It is separate from and coexists with the per-project `changelogs` table in `ARCHIVE/archive.db`. They serve different queries.

> [!important]
> **Cross-database foreign keys are not enforced by SQLite.** `module_uuid` in `changelogs` and `works`, and `apparatus_uuid` in `authors`, are logical references to rows in `registry.db` — a separate database file. `PRAGMA foreign_keys = ON` applies only within a single connection to a single file. No constraint fires across file boundaries regardless of FK enforcement mode.
>
> **Application code is responsible for cross-DB integrity.** Any write to an apparatus DB that references a `module_uuid` or `apparatus_uuid` must first query `registry.db` to confirm the UUID exists and is active (i.e. `decimated_at IS NULL`). Registry lookup first. Apparatus write second. Never the reverse. A decimated module's UUID arriving in an apparatus DB write is a bug in the calling code, not something SQLite will catch.
>
> The `-- logical FK` comments in the DDL document intent. They do not imply enforcement. Do not add `REFERENCES` clauses to these columns — they would silently fail or raise at runtime depending on SQLite version, neither of which is acceptable.

> [!important]
> **bare `sqlite3.connect()`** appears exactly once in the file — inside `_open_connection()` — and the PRAGMA to enforce FK appears exactly once alongside it. A bare `sqlite3.connect()` outside that one function is a bug.

### 4.3 ConfigSchema

> [!note] **v3 change — `apparatus` → `apparati`**
>
> The original field was `apparatus: str`, storing a single apparatus name. Given that a module can now belong to multiple apparati, this field has been renamed `apparati` and widened to `list[str]`. The plural form is intentional and canonical — see Terminology section.
>
> `write_archivist_config` now renders any list-typed field as a YAML block sequence, not just `ignores`. The `apparati` and `vaults` fields benefit from this generalization.
>
> Existing configs with `apparatus: <name>` are handled by `archivist migrate` — see §5.5.

`ConfigSchema` is a TypedDict defined in `archivist/utils/config.py`. The functional form is required because hyphenated keys are not valid Python identifiers.

```python
ConfigSchema = TypedDict('ConfigSchema', {
    'uuid':                 str,         # always present after init; first field written
    'module-type':          str,         # always present; one of APPARATUS_MODULE_TYPES
    'apparati':             list[str],   # apparatus names; absent for standalone modules
    'vaults':               list[str],   # vault module names containing this module
    'git-remote':           str,         # remote URL; absent if not configured
    'git-remote-name':      str,         # human-readable remote label e.g. "origin"
    'library-tag':          str,         # library modules only
    'works-dir':            str,         # library modules only; default: "works"
    'changelog-output-dir': str,         # optional custom output dir
    'templater':            str,         # "resolve" | "preserve" | "false"
    'ignores':              list[str],   # gitignore patterns
}, total=False)
```

`read_archivist_config` return type: `ConfigSchema | None`.
`write_archivist_config` parameter type: `ConfigSchema`.

**`apparati` field config output:** Written as a YAML block sequence, same as `ignores` and `vaults`. An empty list writes as `apparati:\n  []` rather than a null scalar.

**Field migration history:**
- v1: `apparatus: "true"` or `"false"` — boolean flag indicating Apparatus membership
- v2: `apparatus: str` — stores the apparatus name directly
- v3: `apparati: list[str]` — stores all apparatus names; field renamed plural

`archivist migrate` handles both the v1→v2 and v2→v3 transitions for existing projects. New projects written by `archivist init` always write `apparati` directly.

---

## 5. Phase 1 — Registry and Git Integration

### 5.1 `archivist init` (augmented)

**What changes:**
1. Git context check runs before `get_repo_root()`. If no `.git` is found: run `git init`, then proceed. Working in a non-git directory is now valid.
2. After the existing config flow: check for `~/.archivist/`. If absent: first-run setup (see below). If present: proceed directly to apparatus registration.
3. The `apparati` config field is now a list of apparatus names. The interactive prompt asks for an apparatus name. The field is written as a block sequence. Non-apparatus modules either omit the field or receive an empty list.

**First-run registry setup:**
```
~/.archivist/ not found →
  Create ~/.archivist/
  git init ~/.archivist/
  Prompt: registry remote URL
    → list git remotes from current repo as examples
    → manual entry accepted
    → skip accepted (warns: restore capability will be limited)
  git remote add origin <url> (or whatever name the user provides)
  init_registry() — creates schema
```

This runs exactly once per machine. Subsequent `archivist init` calls in other modules find `~/.archivist/` and skip straight to apparatus registration.

**Apparatus registration (added after existing config write):**
```
Is this module part of an Apparatus? [Y/n]
  → yes:
      Apparatus name: [prompt; existing apparati listed if any]
      → new apparatus: create apparati row, create apparatus DB
      → existing apparatus: reuse; add module to it via module_apparatus
      git_remote selection (see §9)
      generate UUID if absent; write to config as first field
      upsert modules row
      insert module_apparatus row for this apparatus
  → no:
      skip registry writes; UUID still generated for future use
```

**Dry-run:** prints `git init` command and all registry writes; executes neither.

### 5.2 `archivist add` (new)

Registers a module with an Apparatus. The git operation is determined by working directory context.

**Context detection:**
- No `.git` in working directory → `git clone <url> [path] [passthrough]`
- `.git` found → `git submodule add <url> [path] [passthrough]`

**Flow:**
```
1. Detect context; build git command with all passthrough args
2. Execute git operation
   → failure: propagate exit code and stderr verbatim; abort; no registry changes
3. Enter target module directory
4. UUID resolution:
   a. UUID in config + decimated in registry → reactivation:
        clear decimated_at; restore module_apparatus and module_bays rows as applicable
   b. UUID in config + active in registry → add module_apparatus row if absent; done
   c. UUID in config + not in registry → register using config as defaults
   d. No config → full interactive registration (same flow as archivist init §5.1)
5. Generate UUID if absent; write to .archivist/config.yaml as first field
6. Upsert modules row
7. Insert module_apparatus row for the apparatus
8. If the working directory is a registered module (any type): add module_bays row for (superproject module, new module)
9. Install git hooks into target module
10. Print summary
```

**`git_remote`:** The URL passed to `archivist add` is stored directly as `git_remote`. Do not query git for a remote name after the fact. The URL is what the user gave; the URL is what gets stored.

**Dry-run:** prints git command and all registration changes; executes neither.

> [!important]
> **`_prompt`/`_confirm`/`_prompt_apparatus_name` are duplicated from `init.py`.** They're UI primitives — interactive prompt helpers are a command concern, not a business logic concern. Putting them in utils would mean barrel-exporting functions that call `input()`, which is wrong. The duplication is intentional and acceptable.
>
> **`_execute_git_operation` propagates the exact exit code.** If `git clone` exits 128, we exit 128. The caller gets the git error verbatim, not a wrapped one.
>
> **`get_registry_path().exists()` guards all registry reads.** Prevents `sqlite3.connect` from silently creating an empty DB file when we're just trying to check if a module is registered. Mirrors what `list_apparatus_names` does.
>
> **Case (b) still calls `_do_register_module`.** An active module getting re-added may have moved on disk or changed its remote. The upsert refreshes path and `git_remote` without creating a duplicate row.

### 5.3 `archivist deinit` (new)

Deregisters a module from the Apparatus and removes it from the superproject or machine. **Run from outside the module being removed.**

**Operation order is not negotiable: Apparatus first, git second.**

Rationale: if git runs first and succeeds, `.archivist/config.yaml` is gone. A subsequent registry failure has nothing to recover from. If Apparatus cleanup runs first and fails, the module is still on disk with its config intact and the user can retry. If Apparatus cleanup succeeds and git fails, the registry says the module is gone while the filesystem still has it — recoverable manually, not catastrophic. The inverse is catastrophic.

> [!note] **v3 change — decimation logic**
>
> The original decimation trigger was: stamp `decimated_at` when no `module_bays` rows remain. This was appropriate when vault containment was the only membership signal but breaks with two independent dimensions (bay containment and apparatus membership). A standalone library — no vault, but actively belonging to an apparatus — would have been incorrectly decimated under the old logic.
>
> The corrected rule: stamp `decimated_at` **only in standalone-removal mode** (deinit called with no superproject context). Vault-context deinit removes the bay row and leaves the module active regardless of remaining bay count. The user explicitly decides when a module is fully gone; `--retain` + standalone removal is the explicit gate.

**Flow:**
```
1. Look up module by path in registry.db
   → not found: warn clearly; exit; do nothing

2. Confirmation prompt
   (fires even with --dry-run; a dry run that skips confirmation is not a dry run)

3. Apparatus cleanup (runs first):
   a. Determine context:
      → inside a superproject with a registered vault UUID:
           remove module_bays row for (superproject vault, this module)
           leave module active — vault-context deinit does not decimate
      → outside superproject context (standalone removal):
           remove_all_bays_for_contained(module_uuid)
           remove_all_apparatus_memberships(module_uuid)
           decimate_module(module_uuid)

4. Git cleanup (runs second):
   → module is a git submodule:
        git submodule deinit [passthrough] <path>
        git rm <path>
   → module is not a git submodule:
        shutil.rmtree(<path>)
        on PermissionError: print path; instruct manual removal; do not sudo; exit 1
   → any git failure: warn; print recovery instructions; registry is already clean

5. Print summary:
   → if decimated: note "history preserved; module reactivatable via archivist add"
   → if still in bays or apparati: note which containers/apparati still hold it
```

**`--retain`:** Runs Apparatus cleanup only. Skips the git operation entirely. Use when git state is already clean and only the registry needed updating, or as a manual recovery path after git failure in a previous run.

**Idempotency:** Re-running after a partial failure must detect that the registry is already updated:
- No `module_bays` rows to remove → skip bay cleanup silently
- No `module_apparatus` rows to remove → skip apparatus membership cleanup silently
- `decimated_at` already set → skip decimation silently
- Proceed directly to git step

This is not optional. Partial failure is a real operational scenario. `archivist deinit` must survive it without complaint.

### 5.4 Hook Augmentation

The pre-commit hook gains a registry sync step. It runs after the existing changelog/manifest check and is non-blocking — the commit is never held hostage to registry availability.

```bash
# Registry sync — non-blocking
if command -v archivist &>/dev/null && [ -d "$HOME/.archivist" ]; then
    archivist _registry-sync 2>/dev/null || \
        echo "  ⚠️  archivist: registry sync failed — commit proceeding anyway"
fi
```

`archivist _registry-sync` (internal subcommand, not user-facing):
1. Read UUID from `.archivist/config.yaml`
2. Look up module in `registry.db`; if not found, exit 0 silently
3. Upsert `modules` row: update `path` (in case the repo moved), set `last_synced_at = now()`

### 5.5 `archivist migrate` Augmentation

`archivist migrate` already exists. It gains two migrations for the apparatus field:

**Migration A — `apparatus` field v1→v2 (boolean → name):** The field was stored as `"true"` or `"false"`. It became the apparatus name.

**Migration B — `apparatus` field v2→v3 (name → `apparati` list):** The field was a single string. It is now a list under a new key.

```
Detecting apparatus: "true" or true in .archivist/config.yaml →
  "This project was configured as an Apparatus module.
   What is the Apparatus name? (e.g. 'writing'): "
  → normalize to lowercase slug
  → write apparati:\n  - <name>
  → if registry exists: create apparatus record if absent; register module; insert module_apparatus row

apparatus: "false" or false →
  → rewrite config: remove apparatus key entirely
  → no registry changes

apparatus: "<name>" (string, v2 format) →
  → rewrite config: rename key to apparati; convert string to single-item list
  → if registry exists: confirm module_apparatus row exists; insert if absent
```

All three cases print a clear summary of what was changed.

> [!important]
> YAML parses `apparatus: true` (no quotes) as Python `bool` `True`, and `apparatus: "true"` (with quotes) as Python `str` `"true"`. Both mean the same thing in the old config format. Handle both. If you only handle the string case, you'll miss repos where the user edited the config without quotes.

### 5.6 `archivist restore` (deferred — design constraints only)

`archivist restore` is not implemented in Phase 1. The following constraints must be satisfied by this phase so that restore can be implemented without architectural changes:

- **`git_remote` must be populated** on every `modules` row.
- **`module_bays` must be current.** Containment relationships must be reconstructable from the registry alone.
- **`module_apparatus` must be current.** Apparatus memberships must be reconstructable.
- **`decimated_at` must be reliable.** Restore skips decimated modules.
- **`~/.archivist/` must be overwritable from remote on conflict.**
- **Restore must know where to put things.** `path` stores absolute local paths. Restore will prompt for a root directory and derive all other paths from containment relationships in `module_bays`. The schema must support this derivation.

### 5.7 Utility Module: `archivist/utils/registry.py`

New module. Barrel-exported via `archivist/utils/__init__.py`. All registry access goes through this module. No command or other utility imports sqlite3 and opens `~/.archivist/` directly.

> [!note] **v3 additions to public surface**
>
> Four new functions for apparatus membership management, mirroring the existing bay management surface exactly. The pattern is established; this is a second application of it.

**Public surface:**

```python
# Path resolution
def get_registry_dir() -> Path: ...              # ~/.archivist/
def get_registry_path() -> Path: ...             # ~/.archivist/registry.db
def get_apparatus_db_path(name: str) -> Path: ...# ~/.archivist/[name].db

# Connection management — callers do not open connections directly
def get_registry_connection() -> sqlite3.Connection: ...
def get_apparatus_connection(apparatus_name: str) -> sqlite3.Connection: ...

# Initialization
def init_registry() -> None: ...                 # idempotent; safe to call on existing registry
def init_apparatus_db(apparatus_name: str) -> None: ...

# Apparatus lifecycle
def register_apparatus(name: str, git_remote: str | None) -> str: ...  # returns uuid
def get_apparatus_by_name(name: str) -> dict | None: ...

# Module lifecycle
def register_module(
    apparatus_name: str | None,   # None for standalone modules; apparatus membership
    name: str,                    # is a separate concern from module registration
    module_type: str,
    path: Path,
    git_remote: str | None,
    git_remote_name: str | None = None,
) -> str: ...                                    # returns uuid

def get_module_by_uuid(uuid: str) -> dict | None: ...
def get_module_by_path(path: Path) -> dict | None: ...
def is_module_registered(uuid: str) -> bool: ...
def update_module_sync(uuid: str) -> None: ...   # updates path + last_synced_at
def decimate_module(uuid: str) -> None: ...      # stamps decimated_at
def reactivate_module(uuid: str) -> None: ...    # clears decimated_at

# Bay management
def add_module_to_bay(container_uuid: str, contained_uuid: str) -> None: ...
def remove_module_from_bay(container_uuid: str, contained_uuid: str) -> None: ...
def remove_all_bays_for_contained(contained_uuid: str) -> None: ...
def get_module_bays(contained_uuid: str) -> list[dict]: ...

# Apparatus membership  ← NEW IN v3
def add_module_to_apparatus(module_uuid: str, apparatus_uuid: str) -> None: ...
def remove_module_from_apparatus(module_uuid: str, apparatus_uuid: str) -> None: ...
def remove_all_apparatus_memberships(module_uuid: str) -> None: ...
def get_module_apparati(module_uuid: str) -> list[dict]: ...

# Queries
def get_apparatus_modules(
    apparatus_name: str,
    include_decimated: bool = False,
) -> list[dict]: ...
# v3: now JOINs through module_apparatus rather than querying modules.apparatus_uuid

def get_bay_modules(
    container_uuid: str,
    include_decimated: bool = False,
) -> list[dict]: ...

def get_vault_modules(
    vault_uuid: str,
    include_decimated: bool = False,
) -> list[dict]: ...

# Registry version control — future automation; not invoked automatically
def commit_registry(message: str) -> None: ...  # deferred
def push_registry() -> None: ...               # deferred
```

`get_registry_dir()` is the single source of the `~/.archivist/` path. Every other path in this module derives from it. If the storage location changes in a future decentralized design, this is the one function that changes.

**`register_module` signature note:** `apparatus_name` is now `str | None`. A module registered as standalone (no apparatus) passes `None`; no `module_apparatus` row is written. Additional apparatus memberships are added separately via `add_module_to_apparatus()`. On update (existing module by path), apparatus associations are not touched — that's a separate operation the caller controls explicitly.

### 5.8 CLI Augmentation (Phase 1)

New parsers added to `build_parser()` in `cli.py`:

```python
# archivist add
add_p = subparsers.add_parser("add", help="Register a module with the Apparatus.")
add_p.add_argument("url", help="Remote URL to clone or add as submodule.")
add_p.add_argument("path", nargs="?", help="Local path (optional).")
add_p.add_argument("passthrough", nargs=argparse.REMAINDER)
add_p.add_argument("--dry-run", action="store_true")

# archivist deinit
deinit_p = subparsers.add_parser("deinit", help="Deregister a module from the Apparatus.")
deinit_p.add_argument("path", help="Path to the module to remove.")
deinit_p.add_argument("passthrough", nargs=argparse.REMAINDER)
deinit_p.add_argument("--retain", action="store_true",
                       help="Apparatus cleanup only; skip git operation.")
deinit_p.add_argument("--dry-run", action="store_true")
```

`init_p` gains no new arguments. All apparatus registration is interactive.

---

## 6. Phase 2 — Multi-Vault Orchestration

Phase 2 requires Phase 1 to be complete, committed, tested, and stable. Do not begin Phase 2 until Phase 1 has shipped.

### 6.1 Scope Selectors

Every multi-module command requires an explicit scope. A command without a scope selector exits immediately with an error — there is no implicit "all modules" default.

```
--apparatus <name>     All active modules in this apparatus (via module_apparatus JOIN)
--vault <name>         All active modules under this vault (via module_bays),
                       including the vault module itself. Targets vault-type
                       containers only. For non-vault superprojects, use
                       --module <name|uuid> to address the container directly.
--type <type>          Filter by module type; combinable with --apparatus or --vault;
                       not a scope on its own
--module <name|uuid>   One or more specific modules; repeatable;
                       mutually exclusive with --apparatus and --vault
```

`--type` narrows an established scope. `--apparatus` and `--vault` establish scope. `--module` is its own scope. Combining `--module` with `--apparatus` or `--vault` is an error.

All operations run in series, sorted alphabetically by module name. No parallelism.

### 6.2 `archivist census`

```
archivist census [scope selector] [--include-decimated]
```

Prints a status table across all matching modules. Read-only. No `--dry-run` accepted or needed.

> [!note] **v3 change — multi-apparatus display**
>
> A module may now belong to multiple apparati. The census output shows all apparatus memberships for each module. Where previously a single apparatus name appeared in the output, a comma-separated list now appears. The column label is "apparati".

**Output format:**
```
cosmic-horror    (library)  ~/writing/cosmic-horror      ✓  last seal: 2026-05-12  synced: 2026-05-14  apparati: writing, cyber
victorian-mayhem (library)  ~/writing/victorian-mayhem   ✓  last seal: 2026-05-19  synced: 2026-05-19  apparati: writing
panopticon       (library)  ~/writing/panopticon         ✗  PATH NOT FOUND         synced: 2026-03-01  apparati: writing
fiction-vault    (vault)    ~/writing/fiction-vault      ✓  last seal: —           synced: 2026-05-18  apparati: writing
```

- **Path validity:** `path.exists()` at display time. `✗` and `PATH NOT FOUND` if the path is stale.
- **Last seal:** most recent `sealed_at` from the apparatus DB `changelogs` table for this module. `—` if no records.
- **Synced:** `modules.last_synced_at` — when the pre-commit hook last updated this module's registry entry.
- **Apparati:** all apparatus names from `get_module_apparati(module_uuid)`, comma-separated.
- Decimated modules excluded by default. `--include-decimated` includes them, marked distinctly.

### 6.3 `archivist distribute`

```
archivist distribute <source> [--dest <relative-path>] [scope selector]
                     [--overwrite] [--dry-run]
```

Copies `<source>` into every module in scope at the target relative path.

`--dest` is the relative path within each module where the file lands. If omitted, the file is placed at the same relative path as `<source>` — which requires `<source>` to be inside the current repo. If `<source>` is absolute or outside the current repo, `--dest` is required.

**Per-module flow:**
```
1. Validate module path exists → skip with warning if not
2. Resolve destination: <module-path>/<dest>
3. Destination exists and --overwrite not set → skip with warning
4. --dry-run → print what would happen; write nothing
5. Write file
6. Report result
```

Distribute writes the file. It does not stage it. Staging is the user's job.

Failures skip and continue. The run does not abort. Summary at completion: N written, M skipped, K failed.

### 6.4 `archivist broadcast`

```
archivist broadcast frontmatter <subcommand> [subcommand-args] [scope selector] [--dry-run]
```

Runs a `frontmatter` subcommand in each module's working directory, in series.

`frontmatter` is a required literal. Broadcast does not accept other command families. This is not a general execution engine and will not become one.

`--dry-run` on broadcast propagates as `--dry-run` to the inner command. Pass it once. Do not pass it twice.

**Per-module flow:**
```
1. Validate module path exists → skip with warning if not
2. chdir into module root
3. Invoke frontmatter subcommand's run() with parsed inner args
4. chdir back
5. Continue to next module
```

**Implementation constraint:** `get_repo_root()` resolves relative to the process working directory. Broadcast depends on `chdir`-ing into the module before invoking `run()`. No frontmatter subcommand may call `get_repo_root()` at import time. Verify this before implementing broadcast.

Failures skip and continue. Summary at completion: N succeeded, M skipped, K failed.

### 6.5 CLI Augmentation (Phase 2)

```python
# archivist census
census_p = subparsers.add_parser("census", help="Status report across registered modules.")
_add_scope_selectors(census_p)
census_p.add_argument("--include-decimated", action="store_true")

# archivist distribute
distribute_p = subparsers.add_parser("distribute", help="Copy a file to multiple modules.")
distribute_p.add_argument("source")
distribute_p.add_argument("--dest")
_add_scope_selectors(distribute_p)
distribute_p.add_argument("--overwrite", action="store_true")
distribute_p.add_argument("--dry-run", action="store_true")

# archivist broadcast
broadcast_p = subparsers.add_parser("broadcast", help="Run a frontmatter command across modules.")
broadcast_p.add_argument("command", choices=["frontmatter"])
broadcast_p.add_argument("passthrough", nargs=argparse.REMAINDER)
_add_scope_selectors(broadcast_p)
broadcast_p.add_argument("--dry-run", action="store_true")
```

`_add_scope_selectors(p)` is a helper that attaches `--apparatus`, `--vault`, `--type`, and `--module` to any parser that needs them. Define it once; call it four times.

---

## 7. Phase 3 — Registry Maintenance (`archivist remedy`)

Phase 3 requires Phase 2 to be complete, committed, tested, and stable. Do not begin Phase 3 until Phase 2 has shipped.

`archivist remedy` is the maintenance layer for the registry. It answers the question Phase 1 and Phase 2 deliberately leave open: what do you do when the registry and the on-disk state of your modules drift apart? The answer is not "open a SQLite shell and remember the schema." The answer is a suite of purpose-built subcommands that handle the coordination that makes manual SQL painful — two-table updates, config file writes, apparatus DB renames, orphan detection across the full registry graph.

There are two complementary approaches and both are first-class:

- **Config-driven sync (`remedy sync`):** the user edits `.archivist/config.yaml` manually, then runs `remedy sync` to push those changes into the registry. Low friction for users who prefer YAML.
- **Imperative subcommands:** the user runs a `remedy` subcommand and Archivist updates both the registry and the config. No manual YAML editing required.

Both directions must write both sides of the config↔registry boundary. A `remedy set` that updates the registry but leaves config stale will be undone by the next `remedy sync`. A `remedy sync` that updates the registry but leaves config stale is a lie about what the config says. **Write both, always.**

---

### 7.1 `remedy sync`

```
archivist remedy sync [--dry-run]
```

Reads `.archivist/config.yaml` from the current module directory and reconciles every reconcilable field against the module's registry row. Idempotent. Safe to run repeatedly. The canonical tool for users who prefer editing YAML over running commands.

**Fields reconciled:**

| Config key | Registry / table | Notes |
|---|---|---|
| `git-remote` | `modules.git_remote` | Also re-derives `git_remote_name` via `git remote -v` |
| `module-type` | `modules.module_type` | Validates against `APPARATUS_MODULE_TYPES` before writing |
| `apparati` | `module_apparatus` rows | Validates each apparatus exists; creates absent ones with confirmation; removes orphaned memberships |
| `uuid` | `modules.uuid` (lookup only) | UUID is the key; not updated; mismatch exits with error |

**`apparati` reconciliation detail:** The config list is treated as the desired set. `remedy sync` computes the diff: apparatus names in config but not in `module_apparatus` → insert. Names in `module_apparatus` but not in config → remove. Names present in both → no-op. New apparatus names not found in the registry prompt for confirmation before creating.

**Flow:**
```
1. Read .archivist/config.yaml → exit with error if absent or unreadable
2. Extract UUID → look up module in registry → exit with error if not found
3. For each reconcilable field: compare config value against registry value
4. --dry-run → print all detected diffs; write nothing
5. For each detected diff:
   a. Validate new value (type check, slug check, apparatus existence)
   b. Update registry (column or junction table rows as appropriate)
   c. Update config field (write_archivist_config)
6. Print summary: N fields updated, M already in sync
```

**What `remedy sync` does NOT do:**
- Does not update `modules.path` directly — handled by pre-commit hook
- Does not rename apparati — use `remedy rename-apparatus`
- Does not transfer module between vaults — use `remedy transfer`
- Does not decimate or reactivate modules — use `remedy reactivate` or `archivist deinit`
- Does not touch any apparatus DB (`~/.archivist/[name].db`) — registry only

---

### 7.2 `remedy set`

```
archivist remedy set <module> <field> <value> [--dry-run]
```

Update a single field on a module's registry row. The imperative alternative to editing config and running `remedy sync`. Always writes both the registry row and the corresponding `.archivist/config.yaml` field.

`<module>` accepts module name or UUID.

**Supported fields and their config equivalents:**

| Field | Registry / table | Config key | Validation |
|---|---|---|---|
| `git-remote` | `modules.git_remote` | `git-remote` | Any non-empty string; also re-derives `git_remote_name` |
| `module-type` | `modules.module_type` | `module-type` | Must be one of `APPARATUS_MODULE_TYPES` |
| `name` | `modules.name` | — | Slug validation; no config equivalent |

Unsupported fields (`uuid`, `decimated_at`, `path`, `last_synced_at`) are rejected with a clear error directing the user to the appropriate command.

**Apparatus membership changes** are not handled by `remedy set`. They are handled by `remedy join-apparatus` and `remedy leave-apparatus` — see §7.3.

---

### 7.3 `remedy join-apparatus` and `remedy leave-apparatus`

> [!note] **v3 change — replaces `remedy move`**
>
> The original spec defined `remedy move <module> --apparatus <new-apparatus>` which reassigned a module from one apparatus to another — a replacement operation that implicitly assumed one-to-one membership. With many-to-many, "moving" is incoherent. Apparatus membership is additive and subtractive, not a slot to overwrite.
>
> `remedy move` is removed. Its semantic replacement is two purpose-built subcommands that mirror how `add_module_to_bay` / `remove_module_from_bay` work for containment.

```
archivist remedy join-apparatus <module> --apparatus <name> [--dry-run]
archivist remedy leave-apparatus <module> --apparatus <name> [--dry-run]
```

**`remedy join-apparatus` flow:**
```
1. Look up <module> by name or UUID → exit if not found or decimated
2. Resolve --apparatus:
   → exists in registry: use it
   → absent: prompt "Apparatus 'X' not found. Create it? [Y/n]"
     → yes: register_apparatus(name, git_remote=None)
     → no: exit
3. Check: is module already a member? → warn and exit ("already a member; nothing to do")
4. --dry-run → print plan; exit
5. add_module_to_apparatus(module_uuid, apparatus_uuid)
6. Update .archivist/config.yaml: append name to apparati list
7. Print summary
```

**`remedy leave-apparatus` flow:**
```
1. Look up <module> by name or UUID → exit if not found or decimated
2. Resolve --apparatus: look up by name → exit if not found
3. Check: is module a member? → warn and exit if not ("not a member; nothing to do")
4. Check remaining memberships: if this is the last apparatus and no bays remain,
   warn: "Removing this membership will leave the module with no apparatus affiliations.
   It will remain active but unaffiliated. Continue? [Y/n]"
5. --dry-run → print plan; exit
6. remove_module_from_apparatus(module_uuid, apparatus_uuid)
7. Update .archivist/config.yaml: remove name from apparati list
8. If apparati list is now empty: set apparati: [] (not remove key)
9. Print summary
```

Neither command touches `module_bays`. Bay membership is a separate structural relationship.

---

### 7.4 `remedy transfer`

```
archivist remedy transfer <module> --to-vault <vault> [--from-vault <vault>] [--dry-run]
```

Move a module's vault membership — update `module_bays` to record that it now lives inside a different vault (or no vault). Does not touch apparatus membership.

**`--from-vault`:** Optional when the module is in exactly one vault (inferred automatically). Required when the module is in multiple vaults — ambiguous without it.

**Flow:**
```
1. Look up <module> by name or UUID → exit if not found or decimated
2. Resolve current bay memberships via get_module_bays()
3. Resolve --from-vault:
   → single current vault and --from-vault absent: use the single vault
   → multiple current vaults and --from-vault absent: exit with error listing current vaults
   → --from-vault provided: validate it exists and contains this module
4. Resolve --to-vault: look up vault module by name or UUID → exit if not found
5. --dry-run → print plan; exit
6. remove_module_from_bay(from_vault_uuid, module_uuid)
7. add_module_to_bay(to_vault_uuid, module_uuid)
8. Update .archivist/config.yaml: vaults field → updated list
9. Print summary
```

**Transferring to "no vault":** `--to-vault none` (literal string "none") removes the module from its current vault without adding it to another. The module becomes a standalone module within its apparati. The `vaults` config field is cleared.

---

### 7.5 `remedy reactivate`

```
archivist remedy reactivate <module> [--apparatus <name>] [--vault <name>] [--dry-run]
```

Bring a decimated module back from the grave. Thin wrapper around `reactivate_module()` with enough interactive guard-rails that a user can do this without knowing the registry internals.

**Flow:**
```
1. Look up <module> by name or UUID
   → not found: exit with error
   → found, not decimated: "This module isn't decimated. Nothing to reactivate."
   → found, decimated: proceed
2. Print module details: name, type, path, apparati (from module_apparatus), last known vaults, decimated_at
3. Confirm: "Reactivate this module? [Y/n]"
4. --dry-run → print what would change; do not prompt
5. reactivate_module(uuid)  — clears decimated_at
6. If --apparatus provided: add_module_to_apparatus(uuid, apparatus_uuid); update config
7. If --vault provided: add_module_to_bay(vault_uuid, module_uuid); update config
8. Print: "Module reactivated. Run archivist census to confirm state."
```

---

### 7.6 `remedy inspect`

```
archivist remedy inspect <module>
```

Print the complete registry state for a single module. Read-only. No `--dry-run` needed.

**Output:**
```
name:           cosmic-horror
uuid:           a1b2c3d4-...
type:           library
apparati:       writing, cyber
path:           ~/writing/cosmic-horror        ✓ exists
git-remote:     git@github.com:user/cosmic-horror.git
git-remote-name: origin
last-synced:    2026-05-14
decimated:      no

Bay memberships (contained in):
  fiction-vault  (vault)  ~/writing/fiction-vault  ✓ exists

Config path:    ~/writing/cosmic-horror/.archivist/config.yaml  ✓ readable
Config snapshot:
  uuid:         a1b2c3d4-...
  module-type:  library
  apparati:
    - writing
    - cyber
  git-remote:   git@github.com:user/cosmic-horror.git
```

`<module>` accepts name or UUID.

---

### 7.7 `remedy orphans`

```
archivist remedy orphans [--apparatus <name>]
```

Read-only audit. Finds registry entries that have drifted from reality in ways the normal toolchain won't catch. No `--dry-run` needed.

**What it looks for:**

| Condition | Label | Suggested action |
|---|---|---|
| `modules.path` does not exist on disk | `STALE PATH` | Run `remedy sync` from correct location, or `archivist deinit --retain` |
| Module active but config at `modules.path` unreadable or missing | `MISSING CONFIG` | Module may have been deleted manually; investigate |
| Module active with no `module_bays` rows AND no `module_apparatus` rows | `UNAFFILIATED` | May be intentional standalone; listed as advisory |
| Module active with `module_apparatus` rows but no `module_bays` rows AND type is not `vault` | `UNCONTAINED` | May be intentional (standalone within apparatus); advisory |
| Apparatus has no active `module_apparatus` rows | `EMPTY APPARATUS` | Run `remedy obliterate-apparatus` if truly abandoned |
| Config `apparati` list does not match `module_apparatus` rows | `CONFIG DRIFT` | Run `remedy sync` to reconcile |
| Config `git-remote` does not match `modules.git_remote` | `CONFIG DRIFT` | Run `remedy sync` to reconcile |

Output groups by condition type. Ends with a summary count per condition. Exits 0 regardless of findings.

---

### 7.8 `remedy rename-apparatus`

```
archivist remedy rename-apparatus <old-name> <new-name> [--dry-run]
```

Rename an apparatus. Touches the `apparati` table and renames the DB file on disk.

**Operation order is not negotiable: file first, row second.**

Rationale: if the row is updated first and the file rename fails, the `db_path` column now points to a file that doesn't exist. If the file rename happens first and the row update fails, the old name still resolves via the still-correct `db_path` — recoverable. Run the operation again.

**Flow:**
```
1. Validate old-name exists in apparati → exit if not
2. Validate new-name is a valid slug → exit if not
3. Validate new-name does not already exist in apparati → exit if it does
4. --dry-run → print plan; exit
5. Rename ~/.archivist/[old-name].db → ~/.archivist/[new-name].db
   → PermissionError or OSError: print error; do not proceed; exit 1
6. UPDATE apparati SET name = ?, db_path = ? WHERE name = ?
   → on failure: print error + manual recovery instructions
7. For every active module in this apparatus (via module_apparatus JOIN):
   a. Read .archivist/config.yaml (skip if path doesn't exist or unreadable)
   b. Update apparati list: replace old name with new name
   c. write_archivist_config()
8. Print summary: apparatus renamed; N module configs updated; M configs unreachable
```

Config updates in step 7 are best-effort. An unreachable config is not a fatal error — `remedy orphans` will flag it as `CONFIG DRIFT`.

---

### 7.9 `remedy obliterate-apparatus`

```
archivist remedy obliterate-apparatus <name> [--dry-run]
```

Hard-delete an apparatus from the registry and remove its database file. Irreversible.

**Preconditions enforced (all must be true before proceeding):**
- Apparatus has zero active modules (no active `module_apparatus` rows pointing to it).
- Apparatus has zero decimated modules, OR user explicitly passes `--including-decimated`.

**Flow:**
```
1. Look up apparatus → exit if not found
2. Count active module_apparatus rows → exit with error if any exist (list the modules)
3. Count decimated module_apparatus rows → if any exist and --including-decimated not passed:
   exit with error naming the count and the flag required
4. Confirmation prompt:
   "This will permanently delete apparatus '[name]' and all its data.
    This cannot be undone. Type the apparatus name to confirm: "
   → string must match exactly; anything else aborts
5. --dry-run: print plan; exit (confirmation still fires)
6. DELETE FROM module_apparatus WHERE apparatus_uuid = ? (if --including-decimated)
7. DELETE FROM apparati WHERE name = ?
8. Delete ~/.archivist/[name].db
   → failure: print warning; registry row already gone; manual cleanup instructions
9. Print: "Apparatus '[name]' and its database have been removed."
```

---

### 7.10 `remedy` CLI Structure

```python
# archivist remedy (parent parser)
remedy_p = subparsers.add_parser("remedy", help="Registry maintenance tools.")
remedy_sub = remedy_p.add_subparsers(dest="remedy_command")

remedy_p_sync               = remedy_sub.add_parser("sync")
remedy_p_set                = remedy_sub.add_parser("set")
remedy_p_join_apparatus     = remedy_sub.add_parser("join-apparatus")   # replaces move
remedy_p_leave_apparatus    = remedy_sub.add_parser("leave-apparatus")  # replaces move
remedy_p_transfer           = remedy_sub.add_parser("transfer")
remedy_p_reactivate         = remedy_sub.add_parser("reactivate")
remedy_p_inspect            = remedy_sub.add_parser("inspect")
remedy_p_orphans            = remedy_sub.add_parser("orphans")
remedy_p_rename_apparatus   = remedy_sub.add_parser("rename-apparatus")
remedy_p_obliterate         = remedy_sub.add_parser("obliterate-apparatus")
```

`archivist remedy` with no subcommand prints help and exits.

**Argument specs (additions and changes from v2):**

```python
# join-apparatus (new)
remedy_p_join_apparatus.add_argument("module", help="Module name or UUID.")
remedy_p_join_apparatus.add_argument("--apparatus", required=True, metavar="NAME")
remedy_p_join_apparatus.add_argument("--dry-run", action="store_true")

# leave-apparatus (new)
remedy_p_leave_apparatus.add_argument("module", help="Module name or UUID.")
remedy_p_leave_apparatus.add_argument("--apparatus", required=True, metavar="NAME")
remedy_p_leave_apparatus.add_argument("--dry-run", action="store_true")

# reactivate (updated: --apparatus now affects module_apparatus, not modules.apparatus_uuid)
remedy_p_reactivate.add_argument("module", help="Module name or UUID.")
remedy_p_reactivate.add_argument("--apparatus", metavar="NAME")
remedy_p_reactivate.add_argument("--vault", metavar="NAME|UUID")
remedy_p_reactivate.add_argument("--dry-run", action="store_true")

# orphans (updated condition labels)
remedy_p_orphans.add_argument("--apparatus", metavar="NAME")
```

---

## 8. Failure Semantics

### 8.1 `deinit` Operation Order

Apparatus first. Git second. This is not a preference — it is the only order that is recoverable from in every failure scenario. See §5.3 for the full rationale.

### 8.2 `deinit` Idempotency

Re-running `archivist deinit` after a partial failure must detect that registry cleanup already ran and skip to the git step. No duplicate warnings, no false errors about rows that don't exist. The command must be safe to run twice.

### 8.3 Phase 2 Failure Semantics

- **Path not found:** skip with warning; continue. Never abort a multi-module run for a stale path.
- **Module command failure (`broadcast`):** capture stderr; report in the per-module output block; continue.
- **Registry not accessible:** hard abort. No registry, no scope resolution, no safe operation.
- **Partial run:** no rollback. Every operation is safe to re-run. Document this in user-facing output.

---

## 9. Argument Passthrough

`archivist add` and `archivist deinit` pass all unrecognized arguments through to the underlying git command without inspection or validation.

Implementation: `nargs=argparse.REMAINDER` captures everything after Archivist's known arguments. The git subprocess receives them verbatim. Exit code and stderr are propagated verbatim.

```
archivist add git@github.com:user/lib.git modules/lib --depth 1 -b main
```

`--depth 1 -b main` are unknown to Archivist and pass directly to `git clone` or `git submodule add`.

---

## 10. `git_remote` and `git_remote_name` Population

`git_remote` is the stable, machine-agnostic URL identifier for each module — the address `archivist restore` clones from. `git_remote_name` is the human-readable label git associates with that URL on this machine (e.g., `origin`, `upstream`, `writing-remote`). Both are populated automatically. The user is never asked to type a URL they haven't already provided and is never asked to name a remote git already knows about.

**URL provided — `archivist add <url>` or `archivist init` receiving a URL:**

The URL goes directly into `git_remote`. Archivist then queries `git remote -v` to find the remote name that corresponds to that URL and writes it into `git_remote_name`. If no matching name is found, `git_remote_name` is left NULL and can be backfilled on the next sync.

**No URL available — migration, existing repo, `archivist init` on a repo without a URL argument:**

Archivist reads `git remote -v`:

- **One remote found:** use it without prompting. URL → `git_remote`. Name → `git_remote_name`. Done.
- **Multiple remotes found:** present the list by name and URL. Ask the user which one matters for the Apparatus. Store the selected URL and name.
- **No remotes found:** allow manual URL entry, or allow skip with a clear warning that `archivist restore` cannot function for this module without it.

---

## 11. Decentralization — Future Direction

The centralized `~/.archivist/` registry is the pragmatic architecture for this implementation. The long-term design aspiration is a decentralized model in which registry data is distributed across vault repos, eliminating dependency on a machine-level singleton.

This aspiration has not resolved to a concrete viable design. The blockers:

- SQLite is binary. Git cannot diff or merge it. Cross-vault propagation via git commits introduces conflict risk without a merge strategy.
- Local filesystem paths are machine-specific. A shared DB cannot store them without machine-scoped records, which reintroduces the centralization problem.
- Restoration requires knowing WHERE to place modules, not only which URLs to clone. Without a local path graph, restore cannot reconstruct the filesystem layout.
- Pull-based cross-vault sync is eventually consistent. The acceptable lag for apparatus data is not yet defined.

The architecture must not foreclose future decentralization:

- `get_registry_dir()` is the single source of the `~/.archivist/` path. Changing storage location is a change to this function and nothing else.
- Nothing outside `registry.py` references `~/.archivist/` directly.
- Schema design avoids assumptions about storage topology.

---

## 12. Open Questions

| Question | Status |
|---|---|
| First-run `~/.archivist/` remote setup: interactive flow design | Needs design |
| `archivist restore` interactive path assignment flow | Deferred to implementation |
| Schema migrations for `registry.db` as the spec evolves | Deferred — current approach is nuke-and-rebuild while registry is lean |
| Automated registry commit and push after registry operations | Future augmentation |
| Decentralized registry architecture | Tabled |
| `archivist restore` implementation | Deferred — constraints in §5.6 |
| Phase 3: `remedy obliterate-apparatus` UX — confirmation string vs y/n | Needs design |
| Phase 3: `remedy sync` path update — should it also update `modules.path` on invocation? | Needs design |
| ~~Module-to-apparatus is one-to-many~~ | **Resolved in v3: many-to-many via `module_apparatus`** |