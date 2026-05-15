# Archivist — Centralized Database Implementation Checklist

**Spec:** `CENTRALIZED_DATABASE_SPEC.md`  
**Status:** Phase 1 In Progress

---

## Phase 1 — Global Registry

Foundation. Everything else depends on this being in place and correct.

### Storage

- [ ] Create `~/.archivist/` directory on first `archivist init` if it does not exist
- [ ] Initialize `~/.archivist/` as a git repository on first run if it is not already one:
  - [ ] Run `git init` inside `~/.archivist/`
  - [ ] Prompt user for a registry remote URL; allow skipping with a warning
  - [ ] Add the remote if provided (`git remote add origin <url>` or equivalent)
  - [ ] Make an initial commit of the empty registry state
  - [ ] If `~/.archivist/` is already a git repo: skip without reinitializing
- [ ] Create `registry.db` at `~/.archivist/registry.db` on first run if it does not exist
- [ ] Write `registry.db` schema: `apparatuses`, `modules`, `module_bays` tables
  - `modules` includes `uuid`, `decimated_at`, and `git_remote` from the initial schema — no migration needed for these
  - `module_bays(container_id, contained_id)` — both columns are FKs into `modules`
  - No `vaults` table. Not now, not ever.
- [ ] Add `.gitignore` to `~/.archivist/` covering SQLite transient files: `*.db-wal`, `*.db-shm`
- [ ] Add utility function: `get_registry_path() -> Path`
- [ ] Add utility function: `get_registry_connection() -> sqlite3.Connection`
- [ ] Add utility function: `get_apparatus_db_path(apparatus_name: str) -> Path`
- [ ] Add utility function: `get_apparatus_db_connection(apparatus_name: str) -> sqlite3.Connection`
- [ ] Add utility function: `is_registry_git_repo() -> bool` — checks whether `~/.archivist/` is a git repository; used by the pre-commit hook to determine whether to attempt commit/push

### `archivist init` — Registration Flow

- [ ] After standard init questions, prompt: `Is this an Apparatus module? [y/N]`
- [ ] If yes, query `registry.db` for existing Apparatuses and present list
- [ ] Present option to create new Apparatus alongside existing ones
- [ ] If new Apparatus: insert `apparatuses` row, create `[apparatus-name].db`, write apparatus DB schema
- [ ] Prompt: `Is this module contained by a vault? [y/N]`
- [ ] If yes, query `registry.db` for modules with `module_type="vault"` in the selected Apparatus and present list
  - [ ] If no vault modules exist yet: inform the user that vault modules must be registered via their own `archivist init` before they can be named as containers; skip vault association
  - [ ] Vault creation is NOT offered here — vault modules register themselves
- [ ] Insert `modules` row with `uuid`, `module_type`, `path`, `apparatus_id`, `git_remote`
- [ ] Insert `module_bays` row(s) for any confirmed vault containment associations
- [ ] Write `apparatus` and `vaults` (list) fields to `.archivist/config.yaml`
- [ ] Write `library-tag` field to `.archivist/config.yaml` for `library` module types
- [ ] Write `works-dir` field to `.archivist/config.yaml` for `library` module types (default: `works/`)

### Apparatus Database Schema

- [ ] Write apparatus DB schema on creation: `authors`, `publications`, `works`, `work_authors`, `work_libraries`, `work_relations`, `changelogs` tables
- [ ] Confirm cross-database soft reference behavior is documented in code (no FK enforcement across `registry.db` and `[apparatus].db` — enforced at application layer)

### Schema Migrations

`migrate_registry_schema()` exists for forward compatibility — adding new columns and tables to deployments that were initialized with an older schema. Starting from scratch, the initial schema is already correct. This function's job is to keep existing installations current as the schema evolves, not to correct a wrong initial setup.

- [ ] `migrate_registry_schema()` is idempotent and safe to call on every open
- [ ] Any future schema additions go through `migrate_registry_schema()` as `ALTER TABLE ADD COLUMN` or `CREATE TABLE IF NOT EXISTS` operations
- [ ] Document in code that the function must never contain destructive operations

---

## Phase 1.5 — Config as Authoritative + Submodule Lifecycle

**Depends on:** Phase 1 complete.  
**Must be complete before:** Phase 2 begins.

This phase establishes the architectural principle that `.archivist/config.yaml` is the source of truth for all registry data, and implements the commands and hook augmentations that enforce it. No further registry work proceeds until this is in place.

---

### 1.5.1 — Schema Additions and Restructuring

**`modules` table additions:**
- [ ] Add `uuid TEXT UNIQUE NOT NULL` to `modules` table
- [ ] Add `decimated_at TEXT` to `modules` table (NULL = active; ISO date = tombstoned)
- [ ] Add both columns to `migrate_registry_schema()` as idempotent `ALTER TABLE ADD COLUMN` ops

**`module_bays` table (replaces `module_vaults`):**
- [ ] Add `module_bays` table to `init_registry_db()` schema: `(container_id INTEGER NOT NULL REFERENCES modules(id), contained_id INTEGER NOT NULL REFERENCES modules(id), PRIMARY KEY (container_id, contained_id))`
- [ ] Add `module_bays` creation to `migrate_registry_schema()` as idempotent `CREATE TABLE IF NOT EXISTS`
- [ ] Backfill `module_bays` from legacy `module_vaults` rows on old schemas (map `vault_id` as container if the vault is registered as a module; otherwise drop — the vault table is gone)
- [ ] Remove `module_vaults` table creation from `init_registry_db()` — new schemas do not create it

**`vaults` table removal:**
- [ ] Remove `vaults` table creation from `init_registry_db()` — new schemas do not create it
- [ ] Add note in `migrate_registry_schema()` that `vaults` rows cannot be migrated automatically; print a warning if the table exists, directing the user to re-register vault modules via `archivist init`

**TypedDict updates in `registry.py`:**
- [ ] Update `ModuleRecord` to include `uuid` and `decimated_at`; remove any vault-specific fields
- [ ] Delete `VaultRecord`, `VaultMembershipRecord`, `ModuleInVaultRecord` — these are gone; vault modules use `ModuleRecord` like everything else
- [ ] Update all `ModuleRecord` construction sites to include `uuid` and `decimated_at`

**Function updates in `registry.py`:**
- [ ] Update `register_module()` to accept and write `uuid`
- [ ] Update `get_module_by_path()` to return `uuid` and `decimated_at`
- [ ] Add `get_module_by_uuid(uuid: str, conn) -> ModuleRecord | None`
- [ ] Update `is_module_registered()` to check by UUID first, fall back to path
- [ ] Delete `register_vault()` — vault modules are registered via `register_module()` with `module_type="vault"`
- [ ] Delete `get_or_create_vault()` — same reason
- [ ] Delete `list_vaults()` — replaced by `list_modules()` filtered on `module_type="vault"`
- [ ] Delete `get_module_vaults()` — replaced by `get_module_containers()` (see below)
- [ ] Delete `get_modules_in_vault()` — replaced by `get_contained_modules()` (see below)
- [ ] Add `add_module_to_bay(container_id: int, contained_id: int, conn) -> None`
- [ ] Add `get_module_containers(module_id: int, conn) -> list[ModuleRecord]` — vault modules that contain this module
- [ ] Add `get_contained_modules(container_id: int, conn) -> list[ModuleRecord]` — modules contained by this vault module
- [ ] Add `get_sibling_modules()` update: rewrite to use `module_bays` instead of `module_vaults`

---

### 1.5.2 — UUID in Config

- [ ] Add `uuid` field to `ConfigSchema` TypedDict in `config.py`
- [ ] Remove `vault` field (singular) from `ConfigSchema` TypedDict — `vaults` (list) is the only supported form; the singular was never valid
- [ ] Update `write_archivist_config()` to write `uuid` as the first field (before `module-type`)
- [ ] Add `generate_module_uuid() -> str` to `config.py` (thin wrapper over `uuid.uuid4()`)
- [ ] Update `read_archivist_config()` — no changes needed; `uuid` is just another field

---

### 1.5.3 — `archivist add` Command

New top-level command. Registers a module with the Apparatus. Git submodule execution is scaffolded but does not run in this phase.

- [ ] Add `add` subparser to `cli.py`
  - positional: `url`
  - positional: `path`
  - `--dry-run`
  - all remaining args passed through via `nargs=argparse.REMAINDER` (scaffolded for future git passthrough)
- [ ] Create `archivist/commands/add.py`
- [ ] Scaffold git passthrough (build the `git submodule add [remainder args] <url> <path>` command string; print it; do not execute):
  - [ ] Add a clearly marked `# SCAFFOLDED — NOT EXECUTED` block
  - [ ] Print what git command would run if the integration were active
- [ ] Require that the target path exists on disk; exit with clear error if not
- [ ] UUID resolution (enter target module directory and read config):
  - [ ] Read `.archivist/config.yaml` if it exists
  - [ ] If `uuid` present: call `get_module_by_uuid()` against `registry.db`
    - [ ] Found, `decimated_at` set: reactivation path — clear `decimated_at`, update `path`, add `module_bays` row, done
    - [ ] Found, active: module already registered — add `module_bays` row if not present; exit
    - [ ] Not found: proceed to fresh registration using config as pre-populated defaults
  - [ ] If no config: proceed to full interactive registration
- [ ] Pre-populate vault context from superproject path (look up superproject in `registry.db` by path to find its vault module record)
- [ ] Call shared registration helper (see §1.5.7)
- [ ] Generate UUID if not already present in config
- [ ] Write `.archivist/config.yaml` with all registration data including `uuid`
- [ ] Upsert `registry.db`; add `module_bays` row if superproject is a registered vault module
- [ ] Install git hooks into target module
- [ ] `--dry-run`: print scaffolded git command, print what registration would occur, print what `module_bays` row would be added; write nothing

---

### 1.5.4 — `archivist deinit` Command

New top-level command. Deregisters a module from the Apparatus. Git submodule execution is scaffolded but does not run in this phase.

- [ ] Add `deinit` subparser to `cli.py`
  - positional: `path`
  - `--dry-run`
  - all remaining args passed through via `nargs=argparse.REMAINDER` (scaffolded for future git passthrough)
- [ ] Create `archivist/commands/deinit.py`
- [ ] Look up module in `registry.db` by path
  - [ ] Not found: warn clearly; do not proceed
- [ ] Require explicit user confirmation before any registry changes (prompt is not skipped by `--dry-run`)
- [ ] Scaffold git passthrough (build the `git submodule deinit [remainder args] <path>` command string; print it; do not execute):
  - [ ] Check whether the module is a git submodule: `git rev-parse --show-superproject-working-tree`
  - [ ] If superproject found: print that `git submodule deinit` would be run (scaffolded, not executed)
  - [ ] If not a submodule: note that no git operation is needed
  - [ ] Add clearly marked `# SCAFFOLDED — NOT EXECUTED` block around the git subprocess call
- [ ] Registry cascade:
  - [ ] Identify which vault module the superproject is (path lookup in `registry.db` for a `module_type="vault"` record)
  - [ ] Remove the `module_bays` row where `container_id` = superproject vault module and `contained_id` = this module
  - [ ] If called outside a superproject context (no vault module found for superproject path): remove all `module_bays` rows where `contained_id` = this module
  - [ ] Check remaining `module_bays` rows where `contained_id` = this module
    - [ ] Rows remain: module is still accessible via another container; leave `modules` row intact
    - [ ] No rows remain: stamp `modules.decimated_at` = today's date
- [ ] Print summary
  - [ ] If decimated: note that history is preserved and module can be reactivated via `archivist add`
- [ ] `--dry-run`: print scaffolded git command, print what registry changes would occur; write nothing; confirmation prompt still fires

---

### 1.5.5 — `archivist init` Augmentation

**Forward compatibility requirement:** `get_repo_root()` must not be the first
call in the init function. Structure the flow as follows:

```
1. Check working directory for .git (file or folder)
   → found: call get_repo_root(); proceed
   → not found: exit with clear error
                ↑ this branch becomes `git init; get_repo_root(); proceed`
                  when the git integration ships — one change, not a rewrite
2. get_repo_root() called here, after the .git check resolves
3. Check ~/.archivist/ git status (see Phase 1 Storage)
4. Remainder of init flow
```

Any existing code that calls `get_repo_root()` before this check must be moved.

- [ ] Restructure init entry point so `.git` check precedes `get_repo_root()`
- [ ] Gather git context before the interactive flow:
  - [ ] List configured remotes via `git remote` — do NOT hardcode "origin"
  - [ ] **No remotes:** inform user; offer free-text URL input; allow skipping with warning
  - [ ] **One remote:** present for confirmation; allow free-text override
  - [ ] **Multiple remotes:** present numbered list; require selection; allow free-text override
  - [ ] Store the selected **URL** (not remote name) as `git_remote` — the name is irrelevant
  - [ ] Add utility function `list_git_remotes(git_root: Path) -> list[tuple[str, str]]` to `git.py`; export from barrel
  - [ ] `git rev-parse --show-superproject-working-tree` → superproject path (empty string if not a submodule)
  - [ ] `git rev-parse --show-prefix` → relative path within superproject
- [ ] UUID resolution at start of init flow:
  - [ ] Read existing config UUID if present
  - [ ] Call `get_module_by_uuid()` — handle all four cases (active, decimated, not found, no UUID)
  - [ ] For reconfiguration: present existing values as defaults throughout the interactive flow
  - [ ] For reactivation: clear `decimated_at` on completion
- [ ] Vault containment association:
  - [ ] If superproject detected and is a registered vault module: present it for confirmation
  - [ ] Ask: `Is this module contained by any other vault? [y/N]`; loop until done
  - [ ] Loop: present registered vault modules in the selected Apparatus; allow selection or "done"
  - [ ] Write all confirmed containment associations to `vaults:` list in config
  - [ ] Vault modules are NOT created here — they must be registered via their own `archivist init`
- [ ] Generate UUID if not already present; write to config as first field
- [ ] Write `git-remote` to config if provided and not already present
- [ ] Upsert `registry.db` on completion; add `module_bays` rows for all confirmed containment associations
- [ ] Existing behavior for non-Apparatus modules (no apparatus selected): unchanged

---

### 1.5.6 — `archivist migrate` Augmentation

- [ ] After migration completes and `.archivist/config.yaml` is written:
  - [ ] Run the same registry upsert as the pre-commit hook sync (§1.5.8)
  - [ ] Print confirmation that registry has been updated
- [ ] If module has no `apparatus` in config: skip registry upsert; print note

---

### 1.5.7 — Shared Registration Helpers

Both `archivist add` and `archivist init` share the same interactive registration flow. That flow must not be duplicated.

- [ ] Extract interactive registration flow into a shared helper in `archivist/utils/` (or a dedicated `archivist/utils/registration.py`)
- [ ] Helper accepts: `git_root: Path`, `superproject_path: Path | None`, `existing_config: ConfigSchema | None`
- [ ] Helper returns: completed `ConfigSchema` ready to write
- [ ] Both `add.py` and `init` command call this helper; neither reimplements the flow
- [ ] Confirm that augmenting the helper lifts both commands automatically

**Boundary requirement:** The shared helper handles registration data only — UUID generation, config construction, registry upsert, vault association. It must not own any git operations: not `git init`, not `git submodule add`, not hook installation into remote modules. Git operations belong in the command-specific code (`init.py`, `add.py`, `deinit.py`). This boundary is what allows the git integration to layer on top of the Centralized DB implementation without requiring changes to the shared helper.

---

### 1.5.8 — Pre-Commit Hook Augmentation

- [ ] Add registry sync step to the pre-commit hook, before changelog generation
- [ ] Sync logic:
  - [ ] Read `.archivist/config.yaml`
  - [ ] If no `apparatus` in config: skip registry writes; exit sync step cleanly
  - [ ] Look up module by UUID (fall back to path if no UUID in config)
  - [ ] Upsert `modules` row with all current config values; update `path` to current absolute path
  - [ ] Reconcile `module_bays`:
    - [ ] For each vault named in config `vaults:` list: look up the vault module by name in the Apparatus; add `module_bays` row if absent
    - [ ] Do not remove any rows — removals are explicit via `archivist deinit`
  - [ ] If `decimated_at` is set and module now has active registry presence: clear `decimated_at`
  - [ ] If `registry.db` does not exist: create it (call `init_registry_db()`)
  - [ ] On any error: warn and continue; do not block the commit
- [ ] After registry upsert, commit and push `~/.archivist/`:
  - [ ] Call `is_registry_git_repo()` — if False, skip silently; do not error
  - [ ] Check whether `~/.archivist/` has a configured remote — if not, skip push silently; log a warning at most
  - [ ] Stage all changes in `~/.archivist/` (`git -C ~/.archivist add -A`)
  - [ ] If nothing to stage: skip commit; do not error
  - [ ] Commit with auto-generated message: `archivist: sync [module-name] [ISO date]`
  - [ ] Push to configured remote
  - [ ] On push failure: warn and continue; do not block the commit
- [ ] Update hook installation (`archivist hooks install` / `archivist hooks sync`) to write the augmented hook script

---

### 1.5.9 — `cli.py` Updates

- [ ] Add `add` command to `build_parser()` and route in `main()`
- [ ] Add `deinit` command to `build_parser()` and route in `main()`
- [ ] Ensure help text, descriptions, and epilogs match the Archivist voice (see `AGENTS.md`)
- [ ] Add examples to both parsers consistent with existing command examples

---

### 1.5.10 — Test Suite

See `CENTRALIZED_DATABASE_TESTING_SPECIFICATION.md` §Phase 1.5 for full coverage detail.

- [ ] Add `conftest.py` fixtures: `registry_db`, `apparatus_db`, `superproject_repo`, `submodule_repo`
- [ ] New file: `tests/unit/test_registry_phase15.py`
  - [ ] `get_module_by_uuid()` — found active, found decimated, not found
  - [ ] `is_module_registered()` — UUID path, path fallback
  - [ ] Tombstone: `decimated_at` set when last `module_bays` row as `contained_id` is removed
  - [ ] Tombstone: `decimated_at` NOT set when other `module_bays` rows remain
  - [ ] Reactivation: `decimated_at` cleared, `path` updated, `module_bays` row added
  - [ ] UUID uniqueness constraint enforced
  - [ ] `add_module_to_bay()` — idempotent; duplicate call does not error or duplicate row
  - [ ] `get_module_containers()` — returns correct vault module records
  - [ ] `get_contained_modules()` — returns correct module records
- [ ] New file: `tests/integration/test_add_command.py`
  - [ ] Fresh repo with no config: full registration flow, config written with uuid
  - [ ] Repo with existing config and uuid not in registry: registration using config defaults
  - [ ] Repo with decimated module: reactivation path — `decimated_at` cleared, `module_bays` row added
  - [ ] Repo with active module: `module_bays` row added if absent; no duplicate registration
  - [ ] Scaffolded git command printed but not executed
  - [ ] `--dry-run`: nothing written
- [ ] New file: `tests/integration/test_deinit_command.py`
  - [ ] Module in multiple vaults: only the relevant `module_bays` row removed; `modules` row intact
  - [ ] Module in one vault (last containment): `module_bays` row removed; `decimated_at` stamped
  - [ ] Module not in registry: warning printed; no registry error
  - [ ] Scaffolded git command printed but not executed
  - [ ] `--dry-run`: nothing written; confirmation prompt still fires
- [ ] Update `tests/integration/test_seal.py`
  - [ ] Seal via UUID: confirm `changelogs` row in apparatus DB uses `module_id` from registry
- [ ] Update `tests/unit/test_config.py`
  - [ ] `uuid` field round-trips through `write_archivist_config()` / `read_archivist_config()`
  - [ ] `uuid` is written as first field
  - [ ] `generate_module_uuid()` returns a valid UUID4 string
  - [ ] `vault` singular field absent from `ConfigSchema` — not written, not read
- [ ] Pre-commit hook sync:
  - [ ] Sync updates `modules` row from config
  - [ ] Sync adds missing `module_bays` rows for vaults named in config
  - [ ] Sync does not remove existing `module_bays` rows
  - [ ] Sync clears `decimated_at` when module has active registry presence
  - [ ] Sync is a no-op when no `apparatus` in config
  - [ ] Sync creates `registry.db` if absent
  - [ ] Sync failure does not block commit (error caught, warning printed)

---

## Phase 2 — `archivist works add`

**Depends on:** Phase 1.5 complete and exercised. The registry must be stable and the config-as-authoritative principle must be in place before any works commands are wired up.

### Precondition Checks

- [ ] Confirm `module-type: library` in config — exit with clear error if not
- [ ] Confirm module is registered in `registry.db` by UUID — exit with clear error if not
- [ ] Resolve `works/` directory from config, fall back to default

### Lookup and Match

- [ ] Normalize title and author fragment (lowercase, strip punctuation, collapse whitespace)
- [ ] Query `works` + `work_authors` + `authors` for title/author match
- [ ] Present match(es) to user for confirmation before proceeding
- [ ] Handle rejected match → fall through to NO MATCH path

### MATCH Path

- [ ] Pull `works`, `work_authors`, `authors`, `publications` records from DB
- [ ] Pre-populate new `.md` card with all shared core fields
- [ ] Confirm library-local fields (`work_stage`, `date_consumed`, `date_cataloged`, `date_reviewed`) are NOT pre-populated
- [ ] Insert `work_libraries` row (library-local fields NULL, `card_path` set)
- [ ] Write card to `[module-root]/[works-dir]/[sort-title].md`

### NO MATCH Path

- [ ] Run `apply-template` with the library's works template
- [ ] Write blank card to `[module-root]/[works-dir]/[sort-title].md`
- [ ] Insert pending `work_libraries` row (`work_id` NULL until commit resolves it)

### Author Lookup (both paths)

- [ ] Normalize author last name for lookup
- [ ] On match confirmation, pull all contributors (authors, editors, translators) from `work_authors`
- [ ] On new author encountered during upsert: confirm with user before inserting — `Found: Lowndes, Marie Belloc — is this the right one? [y/N]`

---

## Phase 3 — `archivist changelog` Harvesting

Depends on Phase 1. Phase 2 should be complete and exercised against real data before this is wired up, but is not a strict dependency.

### Card Identification

- [ ] On changelog run in a library module, identify card type for each staged `.md` file
- [ ] Works card: `tags` contains `catalog-works` (primary) OR file path under configured `works/` dir (fallback)
- [ ] Author card: `class: author` (primary) OR file path under `authors/` (fallback)
- [ ] Publication card: `class: publication` (primary) OR file path under `publications/` (fallback)
- [ ] Files matching none of the above: skip silently

### Harvesting into `ctx.data`

- [ ] For each identified works card, extract core frontmatter fields into `ctx.data`
- [ ] Fields: `sort_title`, `title_alt`, `class`, `category`, `year`, `citation`, `text_source`, `word_count`, `part_of`, `themes`, `keywords`, `content_warnings`, `authors`, `editors`, `translators`, `publications`, `work_stage`, `date_consumed`, `date_cataloged`, `date_reviewed`
- [ ] Strip `[[` and `]]` from wikilink values; store display text as reference string for resolution at commit time
- [ ] Confirm no database writes occur during changelog step — read-only

---

## Phase 4 — Post-Commit Hook Pipeline

Depends on Phases 1, 1.5, 2, and 3. This is the canonical write path for all works data.

### Reference Resolution

- [ ] For each author/editor/translator reference: check `authors` table first; if not found, locate card on disk, read frontmatter, upsert row
- [ ] For each publication reference: check `publications` table first; if not found, locate card on disk, read frontmatter, upsert row
- [ ] Confirm resolution is idempotent — running twice on the same commit produces the same DB state

### Works Upsert

- [ ] Query `works` for title + author match on each committed works card
- [ ] MATCH: `UPDATE works` with any changed core fields; `UPDATE work_libraries` with stage, dates, `card_path`
- [ ] NO MATCH (new work): `INSERT works`; `INSERT work_authors` (one per contributor with role); `INSERT work_relations` for `cites` and `related` references; resolve pending `work_libraries` row — `UPDATE SET work_id`, stage, dates, `card_path`

### Independent Author and Publication Cards

- [ ] If a committed file is identified as an author card (no associated works card in the commit): upsert `authors` row from frontmatter
- [ ] If a committed file is identified as a publication card: upsert `publications` row from frontmatter

### Changelog Records

- [ ] On each commit, insert or update `changelogs` row for this module with `commit_sha` and `date`
- [ ] Use `module_id` from registry (looked up by UUID from config)
- [ ] Confirm UUID → `commit_sha` transition behavior is consistent with existing seal mechanics

### Cleanup

- [ ] Add note or future task: periodic cleanup of `work_libraries` rows where `work_id` is NULL and no corresponding card exists on disk

---

## Phase 5 — Verification and Hardening

Do this after the pipeline is exercised against real data, not before.

- [ ] Confirm idempotency: run post-commit hook twice on same commit, assert DB state is identical
- [ ] Confirm dry-run contract: `archivist changelog --dry-run` in a library module performs no DB writes
- [ ] Confirm `works add` in a non-library module exits with a clear error
- [ ] Confirm `works add` in an unregistered module exits with a clear error
- [ ] Confirm `registry.db` and `[apparatus].db` are created correctly on a fresh machine with no `~/.archivist/` directory
- [ ] Confirm decimated module reactivation via `archivist add` with matching UUID restores full history
- [ ] Confirm pre-commit hook sync failure does not block commits
- [ ] Write integration tests covering: registration flow, `works add` MATCH path, `works add` NO MATCH path, post-commit upsert, idempotency, tombstone/reactivation lifecycle

---

*Cross-reference: `CENTRALIZED_DATABASE_SPEC.md` for full schema, pipeline detail, and deferred decisions.*
*Cross-reference: `CENTRALIZED_DATABASE_TESTING_SPECIFICATION.md` for full test coverage requirements.*