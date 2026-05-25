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
modified: 2026-05-23
version: 1
related:
  - "[[APPARATUS_PLATFORM]]"
  - "[[AP_PHASE_1_IMPLEMENTATION]]"
  - "[[AP_PHASE_2_IMPLEMENTATION]]"
  - "[[TESTING_SPECIFICATION]]"
tags:
  - apparatus-platform
---

**Supersedes:** `CENTRALIZED_DATABASE_SPEC`, `MULTI_VAULT_ORCHESTRATION_SPEC`, `GIT_INTEGRATION_SPEC`

Those three documents are archived. This is the authoritative specification.

---

## 1. Overview

This document specifies the Apparatus Platform: the infrastructure layer that gives Archivist machine-level awareness of every registered module, vault, and apparatus. It covers the registry architecture, the git-integrated module lifecycle commands, and the multi-vault orchestration tooling built on top of that registry.

Two implementation phases:

- **Phase 1 — Registry and Git Integration:** the `~/.archivist/` registry, the apparatus DB schema, `archivist init` augmentation, and two new commands (`add`, `deinit`) that manage module membership and git operations together.
- **Phase 2 — Multi-Vault Orchestration:** `archivist muster`, `archivist distribute`, and `archivist broadcast` — the read and fan-out commands that operate across registered modules.

Phase 2 depends on Phase 1 being complete and stable. Implement in order.

---

## 2. Conceptual Hierarchy

The diagram below shows the expected arrangement. Any module type can serve as a git superproject — containment is not restricted to vaults.

```
Machine
└── ~/.archivist/
    ├── registry.db                  ← global: all apparatuses, modules, containment
    ├── writing.db                   ← apparatus: works catalog, cross-module changelogs
    └── cyber.db                     ← apparatus: works catalog, cross-module changelogs

    Apparatus "writing"
    ├── Module: fiction-vault        (type: vault)
    │   ├── Module: cosmic-horror    (type: library)
    │   ├── Module: panopticon       (type: library)
    │   └── Module: silver-age      (type: story)
    ├── Module: research-vault       (type: vault)
    │   ├── Module: victorian-mayhem (type: library)
    │   └── Module: quarterly        (type: publication)
    └── Module: standalone-lib       (type: library, no containing vault)
```

**Module:** Any Archivist-managed git repository. All modules are equal in kind.

**Apparatus:** A named collection of modules that share a works catalog and a cross-module changelog registry in the apparatus DB.

**Vault:** A module of type `vault`. By convention and expected practice, vaults serve as superprojects — git repositories that contain other modules as git submodules. However, any module type can serve as a superproject. A publication with nested libraries, a library with nested story modules — git does not restrict this, and neither does Archivist. `module_bays` records containment relationships between any two registered modules regardless of type. Vault is the expected container; it is not the only permitted one.

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

```sql
CREATE TABLE IF NOT EXISTS apparatuses (
    uuid        TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    db_path     TEXT NOT NULL,      -- absolute path to apparatus DB file in ~/.archivist/
    created_at  TEXT NOT NULL,
    git_remote  TEXT                -- remote URL for the registry repo itself
);

CREATE TABLE IF NOT EXISTS modules (
    uuid            TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    apparatus_uuid  TEXT NOT NULL REFERENCES apparatuses(uuid),
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
```

**`decimated_at`:** Modules are never hard-deleted. Deregistration sets `decimated_at`. History is preserved. `archivist add` on a decimated module reactivates it by clearing `decimated_at` and restoring the appropriate `module_bays` row.

**`last_synced_at`:** Set by the pre-commit hook on every upsert. Read by `archivist muster` to show how fresh path data is. Stale timestamps signal a module that hasn't committed recently or hasn't been syncing.

**`git_remote`:** The stable, machine-agnostic URL for each module. `archivist restore`'s source for clone URLs. Must be populated at registration time — see §9 for how.

**`git_remote_name`:** The human-readable label git associates with that URL on this machine (e.g., `origin`, `upstream`). Populated automatically alongside `git_remote` — the user is never asked to type it. NULL if no matching remote name can be resolved.

### 4.2 Apparatus Database (`~/.archivist/[name].db`)

Each apparatus has its own database. Schema is identical across all apparatus DBs.

```sql
CREATE TABLE IF NOT EXISTS changelogs (
    uuid        TEXT PRIMARY KEY,
    commit_sha  TEXT,
    log_scope   TEXT,
    module_uuid TEXT NOT NULL REFERENCES modules(uuid),
    created_at  TEXT NOT NULL,
    sealed_at   TEXT,
    file_path   TEXT
);

CREATE TABLE IF NOT EXISTS works (
    uuid        TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    module_uuid TEXT NOT NULL,      -- references the library module that owns it
    work_stage  TEXT,
    created_at  TEXT NOT NULL,
    modified_at TEXT
);

CREATE TABLE IF NOT EXISTS authors (
    uuid           TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    apparatus_uuid TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS works_authors (
    work_uuid   TEXT NOT NULL REFERENCES works(uuid),
    author_uuid TEXT NOT NULL REFERENCES authors(uuid),
    PRIMARY KEY (work_uuid, author_uuid)
);
```

The `changelogs` table here is cross-module aggregation. It is separate from and coexists with the per-project `changelogs` table in `ARCHIVE/archive.db`. They serve different queries.

### 4.3 ConfigSchema

`ConfigSchema` is a TypedDict defined in `archivist/utils/config.py`. The functional form is required because hyphenated keys are not valid Python identifiers.

```python
ConfigSchema = TypedDict('ConfigSchema', {
    'uuid':                 str,         # always present after init; first field written
    'module-type':          str,         # always present; one of APPARATUS_MODULE_TYPES
    'apparatus':            str,         # apparatus name e.g. "writing"; absent for standalone
    'vaults':               list[str],   # vault module names containing this module
    'git-remote':           str,         # remote URL; absent if not configured
    'library-tag':          str,         # library modules only
    'works-dir':            str,         # library modules only; default: "works"
    'changelog-output-dir': str,
    'templater':            str,         # "resolve" | "preserve" | "false"
    'ignores':              list[str],
}, total=False)
```

`read_archivist_config` return type: `dict[str, str | list[str]] | None` → `ConfigSchema | None`.
`write_archivist_config` parameter type: `dict` → `ConfigSchema`.

**`apparatus` field migration:** The field was previously stored as the string `"true"` or `"false"` — a boolean flag. It now stores the apparatus name. `archivist migrate` handles the transition for existing projects. New projects written by `archivist init` always write the apparatus name directly.

---

## 5. Phase 1 — Registry and Git Integration

### 5.1 `archivist init` (augmented)

**What changes:**
1. Git context check runs before `get_repo_root()`. If no `.git` is found: run `git init`, then proceed. Working in a non-git directory is now valid.
2. After the existing config flow: check for `~/.archivist/`. If absent: first-run setup (see below). If present: proceed directly to apparatus registration.
3. The `apparatus` config field is now the apparatus name, not `"true"` or `"false"`. The interactive prompt asks for the name. Non-apparatus modules either omit the field or receive no value.

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
      Apparatus name: [prompt; existing apparatuses listed if any]
      → new apparatus: create apparatuses row, create apparatus DB
      → existing apparatus: reuse; add module to it
      git_remote selection (see §9)
      generate UUID if absent; write to config as first field
      upsert modules row
  → no:
      skip registry writes; UUID still generated for future use
```

**Dry-run:** prints `git init` command and all registry writes; executes neither.

### 5.2 `archivist add` (new)

Registers a module with the Apparatus. The git operation is determined by working directory context.

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
        clear decimated_at; add module_bays row if superproject is a registered vault
   b. UUID in config + active in registry → add module_bays row if absent; done
   c. UUID in config + not in registry → register using config as defaults
   d. No config → full interactive registration (same flow as archivist init §5.1)
5. Generate UUID if absent; write to .archivist/config.yaml as first field
6. Upsert modules row
7. If the working directory is a registered module (any type): add module_bays row for (superproject module, new module)
8. Install git hooks into target module
9. Print summary
```

**`git_remote`:** The URL passed to `archivist add` is stored directly as `git_remote`. Do not query git for a remote name after the fact. The URL is what the user gave; the URL is what gets stored.

**Dry-run:** prints git command and all registration changes; executes neither.

### 5.3 `archivist deinit` (new)

Deregisters a module from the Apparatus and removes it from the superproject or machine. **Run from outside the module being removed.**

**Operation order is not negotiable: Apparatus first, git second.**

Rationale: if git runs first and succeeds, `.archivist/config.yaml` is gone. A subsequent registry failure has nothing to recover from. If Apparatus cleanup runs first and fails, the module is still on disk with its config intact and the user can retry. If Apparatus cleanup succeeds and git fails, the registry says the module is gone while the filesystem still has it — recoverable manually, not catastrophic. The inverse is catastrophic.

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
      → outside superproject context (standalone removal):
           remove ALL module_bays rows where contained_id = this module
   b. Check remaining module_bays rows where contained_id = this module:
      → rows remain: module still accessible via another vault; leave modules row active
      → no rows remain: stamp modules.decimated_at = today

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
   → if still in other vaults: note which vaults still contain it
```

**`--retain`:** Runs Apparatus cleanup only. Skips the git operation entirely. Use when git state is already clean and only the registry needed updating, or as a manual recovery path after git failure in a previous run.

**Idempotency:** Re-running after a partial failure must detect that the registry is already updated:
- No `module_bays` rows to remove → skip bay cleanup silently
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

`archivist migrate` already exists. It gains one new migration:

**`apparatus` field format change:** The field was stored as `"true"` or `"false"`. It is now the apparatus name.

```
Detecting apparatus: "true" in .archivist/config.yaml →
  "This project was configured as an Apparatus module.
   What is the Apparatus name? (e.g. 'writing'): "
  → write apparatus name to config
  → if registry exists: create apparatus record if absent; register module

apparatus: "false" →
  → rewrite config: remove apparatus key entirely
  → no registry changes
```

### 5.6 `archivist restore` (deferred — design constraints only)

`archivist restore` is not implemented in Phase 1. The following constraints must be satisfied by this phase so that restore can be implemented without architectural changes:

- **`git_remote` must be populated** on every `modules` row. Both `archivist init` and `archivist add` must write it. A module without `git_remote` cannot be restored.
- **`module_bays` must be current.** Containment relationships must be reconstructable from the registry alone.
- **`decimated_at` must be reliable.** Restore skips decimated modules. Soft-delete semantics must be consistent.
- **`~/.archivist/` must be overwritable from remote on conflict.** If the local registry has diverged from the remote, restore overwrites local from the remote rather than attempting a merge.
- **Restore must know where to put things.** `path` stores absolute local paths, which are machine-specific and meaningless on a new machine. Restore will prompt for a root directory and derive all other paths from containment relationships in `module_bays`. The schema must support this derivation.

### 5.7 Utility Module: `archivist/utils/registry.py`

New module. Barrel-exported via `archivist/utils/__init__.py`. All registry access goes through this module. No command or other utility imports sqlite3 and opens `~/.archivist/` directly.

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
    apparatus_name: str,
    name: str,
    module_type: str,
    path: Path,
    git_remote: str | None,
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
def get_module_bays(contained_uuid: str) -> list[dict]: ...  # all containers for a module

# Queries
def get_apparatus_modules(
    apparatus_name: str,
    include_decimated: bool = False,
) -> list[dict]: ...

def get_bay_modules(
    container_uuid: str,
    include_decimated: bool = False,
) -> list[dict]: ...
# Returns all modules contained by container_uuid (any superproject type).
# get_vault_modules() is an alias targeting the common vault case; use
# get_bay_modules() wherever the container type is not guaranteed to be a vault.

def get_vault_modules(
    vault_uuid: str,
    include_decimated: bool = False,
) -> list[dict]: ...

# Registry version control — future automation; not invoked automatically in this implementation
# Manual: cd ~/.archivist && git add -A && git commit && git push
def commit_registry(message: str) -> None: ...  # deferred
def push_registry() -> None: ...               # deferred
```

`get_registry_dir()` is the single source of the `~/.archivist/` path. Every other path in this module derives from it. If the storage location changes in a future decentralized design, this is the one function that changes.

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
--apparatus <name>     All active modules in this apparatus
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

### 6.2 `archivist muster`

```
archivist muster [scope selector] [--include-decimated]
```

Prints a status table across all matching modules. Read-only. No `--dry-run` accepted or needed.

**Output format:**
```
cosmic-horror    (library)  ~/writing/cosmic-horror      ✓  last seal: 2026-05-12  synced: 2026-05-14
victorian-mayhem (library)  ~/writing/victorian-mayhem   ✓  last seal: 2026-05-19  synced: 2026-05-19
panopticon       (library)  ~/writing/panopticon         ✗  PATH NOT FOUND         synced: 2026-03-01
fiction-vault    (vault)    ~/writing/fiction-vault      ✓  last seal: —           synced: 2026-05-18
```

- **Path validity:** `path.exists()` at muster time. `✗` and `PATH NOT FOUND` if the path is stale.
- **Last seal:** most recent `sealed_at` from the apparatus DB `changelogs` table for this module. `—` if no records.
- **Synced:** `modules.last_synced_at` — when the pre-commit hook last updated this module's registry entry.
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
# archivist muster
muster_p = subparsers.add_parser("muster", help="Status report across registered modules.")
_add_scope_selectors(muster_p)
muster_p.add_argument("--include-decimated", action="store_true")

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

## 7. Failure Semantics

### 7.1 `deinit` Operation Order

Apparatus first. Git second. This is not a preference — it is the only order that is recoverable from in every failure scenario. See §5.3 for the full rationale.

### 7.2 `deinit` Idempotency

Re-running `archivist deinit` after a partial failure must detect that registry cleanup already ran and skip to the git step. No duplicate warnings, no false errors about rows that don't exist. The command must be safe to run twice.

### 7.3 Phase 2 Failure Semantics

- **Path not found:** skip with warning; continue. Never abort a multi-module run for a stale path.
- **Module command failure (`broadcast`):** capture stderr; report in the per-module output block; continue.
- **Registry not accessible:** hard abort. No registry, no scope resolution, no safe operation.
- **Partial run:** no rollback. Every operation is safe to re-run. Document this in user-facing output.

---

## 8. Argument Passthrough

`archivist add` and `archivist deinit` pass all unrecognized arguments through to the underlying git command without inspection or validation.

Implementation: `nargs=argparse.REMAINDER` captures everything after Archivist's known arguments. The git subprocess receives them verbatim. Exit code and stderr are propagated verbatim.

```
archivist add git@github.com:user/lib.git modules/lib --depth 1 -b main
```

`--depth 1 -b main` are unknown to Archivist and pass directly to `git clone` or `git submodule add`.

---

## 9. `git_remote` and `git_remote_name` Population

`git_remote` is the stable, machine-agnostic URL identifier for each module — the address `archivist restore` clones from. `git_remote_name` is the human-readable label git associates with that URL on this machine (e.g., `origin`, `upstream`, `writing-remote`). Both are populated automatically. The user is never asked to type a URL they haven't already provided and is never asked to name a remote git already knows about.

**URL provided — `archivist add <url>` or `archivist init` receiving a URL:**

The URL goes directly into `git_remote`. Archivist then queries `git remote -v` to find the remote name that corresponds to that URL and writes it into `git_remote_name`. If no matching name is found (the remote hasn't been registered with git yet at the point of query), `git_remote_name` is left NULL and can be backfilled on the next sync.

**No URL available — migration, existing repo, `archivist init` on a repo without a URL argument:**

Archivist reads `git remote -v`:

- **One remote found:** use it without prompting. URL → `git_remote`. Name → `git_remote_name`. Done.
- **Multiple remotes found:** present the list by name and URL. Ask the user which one matters for the Apparatus. Store the selected URL and name.
- **No remotes found:** allow manual URL entry, or allow skip with a clear warning that `archivist restore` cannot function for this module without it.

---

## 10. Decentralization — Future Direction

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

## 11. Open Questions

| Question | Status |
|---|---|
| First-run `~/.archivist/` remote setup: interactive flow design | Needs design |
| `archivist restore` interactive path assignment flow | Deferred to implementation |
| Schema migrations for `registry.db` as the spec evolves | Deferred |
| Automated registry commit and push after registry operations | Future augmentation |
| Decentralized registry architecture | Tabled |
| `archivist restore` implementation | Deferred — constraints in §5.6 |