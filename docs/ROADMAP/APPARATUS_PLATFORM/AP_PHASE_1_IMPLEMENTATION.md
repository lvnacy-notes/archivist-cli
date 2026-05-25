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
modified: 2026-05-24
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

Phase 1 ships as a single unit: registry infrastructure, two new commands, `init` and `migrate` augmentations, and hook updates. Nothing in Phase 1 is optional. Nothing ships with a failing test.

Phase 2 does not begin until Phase 1 is committed, tested, and stable.

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
7. archivist migrate (augment) — depends on ConfigSchema
8. Hook augmentation           — depends on registry.py
9. CLI parser updates          — alongside or after commands
10. Tests                      — last; cover everything
```

Do not implement `archivist add` or `archivist deinit` before `registry.py` exists. Do not augment `archivist init` before `ConfigSchema` is defined. The order is a dependency graph, not a suggestion.

---

## 1. Infrastructure: `~/.archivist/`

- [ ] `get_registry_dir() -> Path` returns `Path.home() / ".archivist"`
- [ ] `get_registry_path() -> Path` returns `get_registry_dir() / "registry.db"`
- [ ] `get_apparatus_db_path(name: str) -> Path` returns `get_registry_dir() / f"{name}.db"`
- [ ] `init_registry() -> None` — creates `~/.archivist/`, runs `git init`, creates schema; idempotent

> ⚠️ `get_registry_dir()` is the **single source** of the `~/.archivist/` path. Every other function in `registry.py` derives from it. Nothing outside `registry.py` constructs this path directly. If the storage location ever changes, this is the one function that changes.

> ⚠️ `init_registry()` must be truly idempotent. It will be called during `archivist init` on a machine that may already have a registry from a previous module setup. Running it twice must produce no errors, no duplicate schema creation, no data loss. Test this explicitly.

> 📌 First-run detection: `~/.archivist/` absent → full setup (mkdir, git init, schema). `~/.archivist/` present → check for `registry.db`; if absent, schema only. If both present, no-op. All three states must be handled cleanly.

> 📌 The git init for `~/.archivist/` is separate from any module's git repo. Do not conflate them. `git init ~/.archivist/` runs as a subprocess; it does not affect the calling process's git context.

---

## 2. Schema: `registry.db`

- [ ] `apparatuses` table: `uuid TEXT PRIMARY KEY`, `name TEXT NOT NULL UNIQUE`, `db_path TEXT NOT NULL`, `created_at TEXT NOT NULL`, `git_remote TEXT`
- [ ] `modules` table: `uuid TEXT PRIMARY KEY`, `name TEXT NOT NULL`, `apparatus_uuid TEXT NOT NULL REFERENCES apparatuses(uuid)`, `module_type TEXT NOT NULL`, `path TEXT NOT NULL`, `git_remote TEXT`, `git_remote_name TEXT`, `decimated_at TEXT`, `last_synced_at TEXT`
- [ ] `module_type` CHECK constraint: `module_type IN ('story', 'publication', 'library', 'vault', 'general')`
- [ ] `module_bays` table: `container_id TEXT NOT NULL REFERENCES modules(uuid)`, `contained_id TEXT NOT NULL REFERENCES modules(uuid)`, `PRIMARY KEY (container_id, contained_id)`
- [ ] FK enforcement: `PRAGMA foreign_keys = ON` executed on **every connection** before use

> 🔴 FK enforcement is OFF by default in SQLite. Every `get_registry_connection()` call must execute `PRAGMA foreign_keys = ON` before returning the connection. Forgetting this means referential integrity is silently unenforced — orphaned rows accumulate with no error until something downstream breaks in a way that's hard to trace back.

> ⚠️ The `module_type` CHECK constraint may or may not be enforced depending on SQLite version and compile flags. Do not rely on it alone. `register_module()` must validate `module_type` in Python before writing. The constraint is a belt-and-suspenders safety net, not the primary gate.

> 📌 `git_remote` and `git_remote_name` are separate columns. `git_remote` is the URL (`git@github.com:user/repo.git`). `git_remote_name` is the local label (`origin`, `upstream`). They are populated via different paths — see §9 of the spec. Do not conflate them in the schema or the query functions.

---

## 3. Schema: Apparatus DB (`~/.archivist/[name].db`)

- [ ] `changelogs` table: `uuid TEXT PRIMARY KEY`, `commit_sha TEXT`, `log_scope TEXT`, `module_uuid TEXT NOT NULL REFERENCES modules(uuid)`, `created_at TEXT NOT NULL`, `sealed_at TEXT`, `file_path TEXT`
- [ ] `works` table: `uuid TEXT PRIMARY KEY`, `title TEXT NOT NULL`, `module_uuid TEXT NOT NULL`, `work_stage TEXT`, `created_at TEXT NOT NULL`, `modified_at TEXT`
- [ ] `authors` table: `uuid TEXT PRIMARY KEY`, `name TEXT NOT NULL`, `apparatus_uuid TEXT NOT NULL`, `created_at TEXT NOT NULL`
- [ ] `works_authors` junction: `work_uuid TEXT NOT NULL REFERENCES works(uuid)`, `author_uuid TEXT NOT NULL REFERENCES authors(uuid)`, `PRIMARY KEY (work_uuid, author_uuid)`
- [ ] `init_apparatus_db(apparatus_name: str) -> None` — idempotent; creates DB file + schema if absent

> ⚠️ The apparatus DB `changelogs` table is **not** the same as the per-project `changelogs` table in `ARCHIVE/archive.db`. They coexist. The per-project table is managed by the seal pipeline. The apparatus table is cross-module aggregation managed by this feature. Do not touch `db.py` or the per-project schema. `registry.py` and `db.py` are peers with separate concerns.

> ⚠️ `init_apparatus_db()` creates a new file at `~/.archivist/[name].db`. If the apparatus name contains characters that are invalid in a filename, this will fail at the OS level. Validate apparatus names at registration time: lowercase, hyphens and alphanumerics only. Reject anything else with a clear error.

---

## 4. Utility Module: `archivist/utils/registry.py`

New module. Barrel-exported. All registry DB access goes through this module — no command or utility opens a connection to `~/.archivist/` directly.

> 🔴 Every function that opens a connection must enable FK enforcement before doing anything else. The pattern is: `conn = sqlite3.connect(path); conn.execute("PRAGMA foreign_keys = ON"); return conn`. This is not optional. See §2 above.

> ⚠️ `registry.py` is a new peer alongside `db.py` in `archivist/utils/`. They serve different layers — per-project vs. machine-level — and must not import from each other. If you find yourself reaching into `db.py` from `registry.py`, stop and rethink.

> 📌 Connection lifecycle: callers are responsible for closing connections. Document this clearly. Consider whether a context manager pattern (`with get_registry_connection() as conn`) is preferable to raw connection returns. Either is acceptable; pick one and be consistent.

**Path resolution:**
- [ ] `get_registry_dir() -> Path`
- [ ] `get_registry_path() -> Path`
- [ ] `get_apparatus_db_path(name: str) -> Path`

**Connection management:**
- [ ] `get_registry_connection() -> sqlite3.Connection` — opens `registry.db`; FK enforcement ON
- [ ] `get_apparatus_connection(apparatus_name: str) -> sqlite3.Connection` — opens apparatus DB; FK enforcement ON

**Initialization:**
- [ ] `init_registry() -> None` — idempotent; mkdir, git init, schema
- [ ] `init_apparatus_db(apparatus_name: str) -> None` — idempotent; creates apparatus DB + schema

**Apparatus lifecycle:**
- [ ] `register_apparatus(name: str, git_remote: str | None) -> str` — upserts `apparatuses` row; calls `init_apparatus_db()`; returns UUID
- [ ] `get_apparatus_by_name(name: str) -> dict | None`

**Module lifecycle:**
- [ ] `register_module(apparatus_name, name, module_type, path, git_remote, git_remote_name) -> str` — validates `module_type`; upserts `modules` row; returns UUID
- [ ] `get_module_by_uuid(uuid: str) -> dict | None`
- [ ] `get_module_by_path(path: Path) -> dict | None` — matches on absolute resolved path
- [ ] `is_module_registered(uuid: str) -> bool`
- [ ] `update_module_sync(uuid: str) -> None` — updates `path` and `last_synced_at = datetime.now().isoformat()`
- [ ] `decimate_module(uuid: str) -> None` — stamps `decimated_at`; raises `ValueError` if UUID not found
- [ ] `reactivate_module(uuid: str) -> None` — clears `decimated_at`; raises `ValueError` if UUID not found

**Bay management:**
- [ ] `add_module_to_bay(container_uuid: str, contained_uuid: str) -> None` — `INSERT OR IGNORE`; no-op if row exists
- [ ] `remove_module_from_bay(container_uuid: str, contained_uuid: str) -> None` — no-op if row absent
- [ ] `remove_all_bays_for_contained(contained_uuid: str) -> None`
- [ ] `get_module_bays(contained_uuid: str) -> list[dict]` — all containers for this module, any type

**Queries:**
- [ ] `get_apparatus_modules(apparatus_name, include_decimated=False) -> list[dict]` — sorted by name; excludes decimated by default
- [ ] `get_bay_modules(container_uuid, include_decimated=False) -> list[dict]` — any superproject type
- [ ] `get_vault_modules(vault_uuid, include_decimated=False) -> list[dict]` — validates container is type `vault`; delegates to `get_bay_modules()`

**Deferred (do not implement in Phase 1):**
- [ ] `commit_registry(message: str) -> None` — future automated backup
- [ ] `push_registry() -> None` — future automated backup

**Barrel export:**
- [ ] Add `from archivist.utils.registry import *  # noqa: F401, F403` to `archivist/utils/__init__.py`

> ⚠️ The barrel uses wildcard imports by design (see `__init__.py` header). `registry.py` must define `__all__` listing only its public functions, or the wildcard will export private helpers and internal names. Check the existing modules for the convention — if they don't define `__all__`, follow whatever pattern is already established. Do not introduce a new inconsistency.

---

## 5. `ConfigSchema` TypedDict

- [x] Define `ConfigSchema` in `archivist/utils/config.py` using the functional TypedDict form
- [x] All fields `NotRequired` (`total=False`) — config can be partial at any stage
- [x] Fields: `uuid`, `module-type`, `apparatus`, `vaults`, `git-remote`, `git-remote-name`, `library-tag`, `works-dir`, `changelog-output-dir`, `templater`, `ignores`
- [x] Update `read_archivist_config` return type: `dict[str, str | list[str]] | None` → `ConfigSchema | None`
- [x] Update `write_archivist_config` parameter type: `dict` → `ConfigSchema`
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

> ⚠️ The `apparatus` field change is a **breaking semantic change**. It was `"true"` or `"false"`. It is now the apparatus name (`"writing"`, `"cyber"`). Every existing call site that does `if config.get("apparatus") == "true"` is wrong and must be updated. Search the entire codebase for these checks before shipping.

> 📌 `uuid` must be the first field written to `config.yaml`. The `write_archivist_config` function builds lines from the config dict — Python dicts preserve insertion order (3.7+), but the caller must pass `uuid` first. The init flow must ensure this. Verify the write output, not just the dict construction.

---

## 6. `archivist init` Augmentation

> 🚫 **Blocked on:** `registry.py` (§4) and `ConfigSchema` (§5) complete.

- [ ] Git context check: look for `.git` in the working directory **before** calling `get_repo_root()`
  - Found: proceed normally
  - Not found: run `git init` via subprocess; then proceed
- [ ] After existing config flow: check for `~/.archivist/`
  - Absent: call `init_registry()`; prompt for registry remote URL (see below)
  - Present: proceed directly to apparatus registration
- [ ] Registry remote prompt (first-run only):
  - List configured git remotes from the current module repo as URL examples
  - Accept manual URL entry
  - Accept skip — warn clearly that `archivist restore` cannot function without a registry remote
- [ ] Apparatus registration (added after config write):
  - Prompt: is this module part of an Apparatus?
  - Yes → prompt apparatus name; show existing apparatus names if any
    - New apparatus → `register_apparatus()`; create apparatus DB
    - Existing → confirm; proceed to `register_module()`
  - No → skip registry writes; UUID still generated
- [ ] `git_remote` + `git_remote_name` resolution: follow spec §9 workflow
- [ ] UUID: generate if absent; write to config as **first field**
- [ ] `apparatus` config field: write apparatus name (e.g., `"writing"`), not `"true"`
- [ ] Upsert `modules` row via `register_module()`
- [ ] Dry-run: print git init command and all registry operations; execute neither

> 🔴 The git check must happen **before** `get_repo_root()`. The existing code calls `get_repo_root()` at the top of `run()`. That call shells out to `git rev-parse --show-toplevel`, which exits non-zero if there's no git repo — which means it calls `sys.exit(1)` before init gets a chance to run `git init`. Fix the order. Check for `.git` first, optionally run `git init`, then call `get_repo_root()`.

> ⚠️ The first-run registry setup is a one-time operation per machine. If it partially succeeds (e.g., `mkdir` works, `git init` fails), the next run must detect the partial state and resume cleanly. `init_registry()` idempotency covers this — trust it, but test it.

> ⚠️ When prompting for apparatus name, present any existing apparatus names from `registry.db` so the user can confirm they're adding to an existing one rather than accidentally creating a duplicate with a typo. The prompt matters here — `"writing"` and `"Writing"` should not both exist.

> 📌 The `vaults` field in config is populated by `archivist add` when this module is added to a superproject, not by `archivist init`. Do not prompt for it here.

---

## 7. `archivist add` (New Command)

> 🚫 **Blocked on:** `registry.py` (§4) complete.

- [ ] Context detection: check for `.git` in the current working directory
  - No `.git` → `git clone <url> [path] [passthrough]`
  - `.git` found → `git submodule add <url> [path] [passthrough]`
- [ ] Execute git operation with all passthrough arguments
  - On failure: propagate exit code and stderr verbatim; abort; **no registry changes**
- [ ] Enter target module directory (resolved from path arg or inferred from URL stem)
- [ ] UUID resolution — four cases, in order:
  - UUID in config + `decimated_at` set in registry → reactivation: call `reactivate_module()`
  - UUID in config + active in registry → `add_module_to_bay()` if applicable; done
  - UUID in config + not in registry → register using config values as defaults
  - No config → full interactive registration (same flow as `archivist init` apparatus registration)
- [ ] Generate UUID if absent; write to `config.yaml` as first field
- [ ] `git_remote`: store the URL from the `add` argument directly — this is the URL, not a remote name
- [ ] `git_remote_name`: query `git remote -v` in the target module after git operation completes; find the name associated with this URL; store it; NULL if not found
- [ ] Upsert `modules` row via `register_module()`
- [ ] Bay management: check if current working directory (before entering target) is a registered module via `get_module_by_path(cwd)` — if yes, call `add_module_to_bay(cwd_module_uuid, new_module_uuid)`
- [ ] Update `vaults` list in target module's `config.yaml` if superproject is a vault-type module
- [ ] Install git hooks into target module
- [ ] Dry-run: print git command and registration plan; execute neither

> 🔴 If the git operation fails, **stop**. Write nothing to the registry. The git step is the gate. A module that failed to clone or submodule-add does not exist in a meaningful state and should not be registered. Exit cleanly with the git error.

> 🔴 Context detection happens **before** the git operation, on the **current working directory**, not the target directory. After `git clone`, the target directory may contain `.git`. That's irrelevant — the question is whether the caller's directory is a git repo, which determines whether this is a clone or a submodule add.

> ⚠️ Bay management: only add a `module_bays` row if the current working directory is a **registered** module. Being inside a git repo is not sufficient — the repo must be in the registry. Call `get_module_by_path(Path.cwd())` and only add the bay if it returns a result. Unregistered superprojects are not tracked.

> ⚠️ `git_remote_name` is queried **after** the git operation, not before. If the URL isn't yet in `git remote -v` at query time (unlikely but possible depending on timing), store NULL and move on. Don't block registration on a missing label.

> ⚠️ The reactivation path (decimated module re-added) must clear `decimated_at` before adding to bays. A module with `decimated_at` set must not appear in active queries even if a bay row exists. Clear first, bay second.

> 📌 The `vaults` field in the target module's `config.yaml` records which vault(s) contain it — human-readable vault names, not UUIDs. This is the config layer's view of containment; the DB layer's view is `module_bays`. Both must be updated. If the superproject is not type `vault`, do not add it to the `vaults` list (the `module_bays` row still gets created — containment is containment regardless of type).

---

## 8. `archivist deinit` (New Command)

> 🚫 **Blocked on:** `registry.py` (§4) complete.

> 🔴 **Operation order is not negotiable: Apparatus first. Git second. Always.** If git runs first and succeeds, `config.yaml` is gone — the registry has lost its recovery information. If Apparatus cleanup runs first and fails, the module is still on disk and the user can retry. The inverse failure mode is unrecoverable. Do not reverse this order under any circumstances, not even in edge cases, not even "just this once."

- [ ] Look up module by path (`get_module_by_path(Path(args.path).resolve())`)
  - Not found: warn clearly; exit; do nothing
- [ ] Confirmation prompt — fires even with `--dry-run`
- [ ] Apparatus cleanup (first):
  - Determine context: is the current working directory a registered superproject of the target?
    - Yes → remove only the `module_bays` row for (cwd superproject, target module)
    - No (standalone removal) → `remove_all_bays_for_contained(target_uuid)`
  - Check remaining `module_bays` rows for `contained_id = target_uuid`
    - Rows remain → module still belongs to another container; leave `modules` row active
    - No rows remain → `decimate_module(target_uuid)`
  - Update `vaults` list in target module's `config.yaml` to remove this superproject (if applicable)
- [ ] Git cleanup (second):
  - Resolve whether target is a git submodule: check parent repo's `.gitmodules`
  - Submodule → `git submodule deinit [passthrough] <path>` then `git rm <path>`
  - Not a submodule → `shutil.rmtree(path)`
    - `PermissionError` → print path; instruct manual removal; exit 1; do not sudo
  - Any git failure → warn; print recovery instructions; exit 1
- [ ] `--retain`: runs Apparatus cleanup only; skips git operation entirely
- [ ] Idempotency: detect already-cleaned state before each step
  - No bay rows to remove → skip bay cleanup silently
  - `decimated_at` already set → skip decimation silently
  - Module path already gone from disk → skip git step; warn
- [ ] Dry-run: print Apparatus changes and git command; execute neither; confirmation prompt still fires

> ⚠️ **Decimation vs. bay removal** — these are distinct operations. A module with remaining `module_bays` rows is still active in the Apparatus; it just no longer belongs to THIS superproject. Only decimate when `get_module_bays(target_uuid)` returns empty after bay removal. Getting this logic wrong orphans modules or incorrectly tombstones active ones.

> ⚠️ **Idempotency is load-bearing.** A deinit that partially succeeds (Apparatus cleaned, git failed) must be re-runnable. On re-run: no bay rows exist (already removed), `decimated_at` may already be set — skip those steps, detect that the module path is still on disk (or not), proceed to git. This must work without user intervention or manual registry edits.

> ⚠️ Confirmation prompt fires even with `--dry-run`. A dry run that skips confirmation gives no useful information about what would actually happen, because the prompt is part of what happens. Don't skip it.

> 📌 `--retain` is the manual recovery tool for the scenario where the git operation already ran (manually or in a previous failed attempt) and only the registry needs cleaning. Document this in the help text.

> 📌 When printing recovery instructions after a git failure, be specific: "Registry has been updated. The module directory at `<path>` still exists. Remove it manually with `git submodule deinit <path> && git rm <path>`, then run `archivist deinit --retain <path>` to complete cleanup." Vague "something went wrong" messages are useless here.

---

## 9. `archivist migrate` Augmentation

> 🚫 **Blocked on:** `ConfigSchema` (§5) complete.

- [ ] Detect `apparatus: "true"` in `.archivist/config.yaml` (string, not boolean)
  - Prompt: "What is the apparatus name for this module? (e.g. 'writing')"
  - Validate: lowercase, hyphens and alphanumerics only; reject others
  - Rewrite config: `apparatus: <name>`
  - If registry exists and module not yet registered: register now
- [ ] Detect `apparatus: true` (parsed as Python `bool` — YAML parses unquoted `true` as `True`)
  - Same handling as above — YAML is inconsistent about this; handle both
- [ ] Detect `apparatus: "false"` or `apparatus: false`
  - Rewrite config: remove `apparatus` key entirely
  - No registry changes
- [ ] Print clear summary of what was changed

> ⚠️ YAML parses `apparatus: true` (no quotes) as Python `bool` `True`, and `apparatus: "true"` (with quotes) as Python `str` `"true"`. Both mean the same thing in the old config format. Both need the same migration treatment. If you only handle the string case, you'll miss repos where the user edited the config without quotes.

> ⚠️ Validate the apparatus name the user provides. If they type `"Writing"` with a capital W, the apparatus DB will be created as `~/.archivist/Writing.db` — which will not match the lowercase `"writing"` that every other command expects. Normalize to lowercase and hyphens at input time, not at query time.

---

## 10. Hook Augmentation

> 🚫 **Blocked on:** `registry.py` (§4) complete.

- [ ] Add registry sync step to `PRE_COMMIT_HOOK` constant in `archivist/commands/hooks/install.py`, after the existing changelog/manifest check
- [ ] Sync logic: if `archivist` on PATH and `~/.archivist/` exists, call `archivist _registry-sync`
- [ ] Sync is non-blocking: failure prints a warning; commit proceeds regardless
- [ ] Implement `_registry-sync` as an internal subcommand (not user-facing; prefix with `_` in help suppression)
  - Read UUID from `.archivist/config.yaml`
  - Look up module in registry; if not found, exit 0 silently (unregistered modules are not an error)
  - Call `update_module_sync(uuid)` — updates `path` and `last_synced_at`
  - Exit 0 always (non-blocking contract)
- [ ] Add `_registry-sync` parser to `cli.py` with `help=argparse.SUPPRESS`
- [ ] Existing hooks are not auto-updated — users must re-run `archivist hooks sync` or `archivist hooks install` to get the new hook content. Document this prominently in release notes.

> ⚠️ The updated `PRE_COMMIT_HOOK` constant in `install.py` only affects **new** hook installations. Every repo with an existing hook has the old version. There is no auto-update mechanism. Users must be told to run `archivist hooks sync` after upgrading. This is not a bug; it is the expected behavior of installed git hooks. Be explicit about it.

> ⚠️ `_registry-sync` must exit 0 in every scenario — not found in registry, registry DB missing, connection error, anything. This command runs on every commit. A non-zero exit from the pre-commit hook aborts the commit. A registry sync failure must never abort a commit.

> 📌 `_registry-sync` is intentionally not user-facing. Suppress it from help output with `help=argparse.SUPPRESS`. It is called by the hook, not by the user.

---

## 11. CLI Parser Updates (`cli.py`)

> 🚫 **Blocked on:** commands (§6, §7, §8) complete enough to wire up.

- [ ] `archivist add` parser:
  ```python
  add_p = subparsers.add_parser("add", help="Register a module with the Apparatus.")
  add_p.add_argument("url", help="Remote URL to clone or add as submodule.")
  add_p.add_argument("path", nargs="?", help="Local destination path.")
  add_p.add_argument("passthrough", nargs=argparse.REMAINDER)
  add_p.add_argument("--dry-run", action="store_true")
  ```
- [ ] `archivist deinit` parser:
  ```python
  deinit_p = subparsers.add_parser("deinit", help="Deregister a module from the Apparatus.")
  deinit_p.add_argument("path", help="Path to the module to remove.")
  deinit_p.add_argument("passthrough", nargs=argparse.REMAINDER)
  deinit_p.add_argument("--retain", action="store_true",
                         help="Registry cleanup only; skip git operation.")
  deinit_p.add_argument("--dry-run", action="store_true")
  ```
- [ ] `archivist _registry-sync` parser (internal):
  ```python
  sync_p = subparsers.add_parser("_registry-sync", help=argparse.SUPPRESS)
  ```
- [ ] Dispatch: add `elif args.command == "add"`, `"deinit"`, `"_registry-sync"` branches
- [ ] Verify `init_p` parser unchanged — no new arguments; registration is fully interactive

> ⚠️ `nargs=argparse.REMAINDER` for `passthrough` captures everything after the last known argument. This means argument ordering matters — `url` and `path` must come before any passthrough args in usage. Document this in the help text. Test with flags like `--depth 1` in the passthrough to confirm they don't get swallowed by argparse's greedy matching.

> 📌 Per `CLAUDE.md`: `cli.py` parser definitions are in the "What Not to Touch" category unless adding or removing a subcommand. These additions qualify. Be surgical — add the new parsers without touching the existing ones.

---

## 12. Testing: Phase 1

Run existing tests before writing a single new one. If anything is red before you start, stop and fix it.

**Unit tests: `tests/unit/test_registry.py` (new file)**

- [ ] `get_registry_dir()` returns `Path.home() / ".archivist"`
- [ ] `get_registry_path()` returns correct path relative to registry dir
- [ ] `get_apparatus_db_path("writing")` returns `~/.archivist/writing.db`
- [ ] `init_registry()` creates directory, creates `registry.db`, creates schema
- [ ] `init_registry()` is idempotent — calling twice raises no error and corrupts no data
- [ ] `get_registry_connection()` returns connection with FK enforcement ON
  - Verify: attempt FK violation; confirm it raises `IntegrityError`
- [ ] `register_apparatus()` creates row; returns UUID; creates apparatus DB
- [ ] `register_apparatus()` with same name twice → upsert, not duplicate
- [ ] `register_module()` creates row; returns UUID
- [ ] `register_module()` with invalid `module_type` → raises before writing
- [ ] `get_module_by_uuid()` happy path and not-found (returns `None`)
- [ ] `get_module_by_path()` happy path and not-found (returns `None`)
- [ ] `get_module_by_path()` resolves symlinks and relative paths to absolute before querying
- [ ] `is_module_registered()` true and false cases
- [ ] `decimate_module()` stamps `decimated_at`
- [ ] `decimate_module()` with unknown UUID → raises `ValueError`
- [ ] `reactivate_module()` clears `decimated_at`
- [ ] `reactivate_module()` with unknown UUID → raises `ValueError`
- [ ] `add_module_to_bay()` creates row; no-op on duplicate (no error)
- [ ] `remove_module_from_bay()` removes row; no-op if absent (no error)
- [ ] `remove_all_bays_for_contained()` removes all rows for target; leaves other rows intact
- [ ] `get_module_bays()` returns all containers; empty list if none
- [ ] `get_apparatus_modules()` excludes decimated by default; includes with flag; sorted by name
- [ ] `get_bay_modules()` scoped to container; excludes decimated by default
- [ ] `get_vault_modules()` with non-vault container UUID → raises or warns
- [ ] Registry isolation: all tests use `tmp_path`; none touch real `~/.archivist/`

> 🔴 **Tests must never touch `~/.archivist/`.** Use `monkeypatch` to override `get_registry_dir()` to return a path inside `tmp_path` for every test in this module. This is not optional. A test that writes to the real registry contaminates the developer's machine and produces results that depend on machine state.

> 📌 Pattern:
> ```python
> @pytest.fixture(autouse=True)
> def isolated_registry(tmp_path, monkeypatch):
>     monkeypatch.setattr("archivist.utils.registry.get_registry_dir", lambda: tmp_path / ".archivist")
> ```
> Apply this as an `autouse` fixture to the entire test module so no test can accidentally escape it.

**Unit tests: `tests/unit/test_config.py` additions**

- [ ] `ConfigSchema` is a valid TypedDict; can be instantiated with a subset of fields
- [ ] `read_archivist_config()` returns a value compatible with `ConfigSchema`
- [ ] `write_archivist_config()` accepts a `ConfigSchema`-typed dict
- [ ] `apparatus: "true"` in existing config is NOT treated as apparatus name (migration concern — `migrate` handles it; `read` should surface the raw value)
- [ ] `uuid` is first key written by `write_archivist_config()` when present

**Integration tests: `tests/integration/test_add_deinit.py` (new file)**

All integration tests use `git_repo` fixture with `monkeypatch.chdir()`. All tests isolate the registry via `monkeypatch` on `get_registry_dir()`.

`archivist add`:
- [ ] In non-git directory: `git clone` runs; module registered; no bay row (no superproject)
- [ ] In git repo: `git submodule add` runs; module registered; bay row created if cwd is registered
- [ ] In git repo with unregistered cwd: module registered; **no** bay row created
- [ ] Git failure: no registry changes; exits with propagated exit code
- [ ] Decimated module re-added: `decimated_at` cleared; bay row restored
- [ ] Active module re-added (already registered): bay row added if absent; no duplicate module row
- [ ] `git_remote_name` populated from `git remote -v` after operation
- [ ] `git_remote_name` is NULL if remote not yet registered in git
- [ ] Dry-run: no git operation; no registry changes; plan printed

`archivist deinit`:
- [ ] Happy path: bay row removed; `decimated_at` set; git submodule deinit + rm runs
- [ ] Module in multiple bays: target bay removed; other bay intact; `decimated_at` NOT set
- [ ] Standalone removal (no superproject context): all bays removed; `decimated_at` set
- [ ] `--retain`: registry cleaned; git untouched; module still on disk
- [ ] Idempotency: re-run after Apparatus-cleaned-but-git-failed state → registry step no-ops; git step fires
- [ ] Not found in registry: exits with warning; no changes
- [ ] Confirmation prompt fires on dry-run
- [ ] Dry-run: no registry changes; no git operation; plan printed
- [ ] `PermissionError` on `shutil.rmtree`: prints path; instructs manual removal; does not crash

**Dry-run contract (both commands):**
- [ ] `test_dry_run_writes_absolutely_nothing` — compare file sets before and after; compare registry state before and after

---

## Phase 1 Completion Gate

Before marking Phase 1 done and before opening Phase 2:

- [ ] All tests pass: `pytest -v`
- [ ] Unit tests pass without registry fixture: `pytest -m "not integration" -v`
- [ ] No regressions in existing test suite
- [ ] `archivist init` runs cleanly on a fresh directory (no git, no `.archivist/`)
- [ ] `archivist add` and `archivist deinit` are wired in `cli.py` and dispatch correctly
- [ ] `archivist migrate` handles both `apparatus: "true"` and `apparatus: true` without error
- [ ] `archivist hooks sync` installs the updated pre-commit hook with registry sync step
- [ ] Manual smoke test: init a new module; add a submodule; muster shows both (Phase 2 preview); deinit the submodule; registry reflects decimation