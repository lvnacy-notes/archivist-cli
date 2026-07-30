---
class: checklist
category:
  - feature
  - infrastructure
  - database
  - cli
affiliations:
created: 2026-06-09
modified: 2026-06-09
version:
related:
  - "[[APPARATUS_PLATFORM]]"
  - "[[APPARATUS_PLATFORM_SPEC]]"
  - "[[AP_PHASE_1_IMPLEMENTATION]]"
  - "[[AP_PHASE_2_IMPLEMENTATION]]"
tags:
  - apparatus-platform
  - phase-3
---

> The registry and the config are the same truth told twice. Keep them that way or I will find you.

Phase 2 ships first. Full stop. If Phase 2 is not committed, tested, and stable, this document does not exist yet.

Phase 3 adds `archivist remedy`: the maintenance suite for keeping the registry and per-module configs in sync without touching SQL directly. It assumes a working, populated registry. It is not a workaround for a broken Phase 1. Do not implement Phase 3 against an unfinished Phase 2.

## Contents
```toc
```
---

## Implementation Order

Follow this sequence. Each group depends on the one before it.

```
1. remedy_helpers.py           — shared utilities; no dependencies within Phase 3
2. remedy sync                 — validates the full config↔registry contract
3. remedy inspect              — read-only; validates helper layer; no side effects
4. remedy orphans              — read-only; validates query layer
5. remedy set                  — simplest write command; single-field update
6. remedy move                 — apparatus reassignment; depends on set patterns
7. remedy transfer             — bay management; depends on registry queries
8. remedy reactivate           — depends on all query + write patterns
9. remedy rename-apparatus     — touches both files and DB; operation order matters
10. remedy obliterate-apparatus — destructive; implement last; gate heavily
11. CLI parser updates          — alongside or after commands
12. Tests                       — last; cover everything
```

Do not implement any write command before `remedy sync` exists. `remedy sync` defines the config↔registry contract. Everything else that writes must honour the same contract.

---

## Phase Gate

Before writing a single line of Phase 3 code, confirm:

- [ ] All Phase 2 tests pass: `pytest -v`
- [ ] `archivist census` produces correct output on a real multi-module registry
- [ ] `archivist distribute` and `archivist broadcast` are wired and dispatching correctly
- [ ] Registry isolation fixture is available in `conftest.py` (not just in Phase 1 tests)
- [ ] `write_archivist_config()` and `read_archivist_config()` handle the full `ConfigSchema` round-trip cleanly

If any of the above are red, stop and fix Phase 2.

---

## 1. Utility Module: `archivist/commands/remedy/remedy_helpers.py`

Shared helpers for the `remedy` command suite. This is NOT a utils module — it is a command-level helper, living alongside the command files, not barrel-exported. The distinction matters: these helpers call `input()`, format user-facing messages, and do things that have no place in the shared utilities layer.

> ⚠️ Do NOT put these in `archivist/utils/`. Barrel-exported utils that call `input()` are a category error. These are command helpers. They live in `archivist/commands/remedy/`.

> 📌 This mirrors the established pattern from the spec: `_prompt`/`_confirm` helpers in `init.py` and `add.py` are intentionally duplicated rather than extracted to utils for the same reason.

**Module lookup:**

- [ ] `resolve_module_or_die(identifier: str) -> dict` — look up a module by name or UUID; try UUID first (exact match), then name (exact match); `sys.exit(1)` with a clear message if not found
- [ ] `resolve_active_module_or_die(identifier: str) -> dict` — same as above but additionally exits if `decimated_at` is not NULL
- [ ] `resolve_decimated_module_or_die(identifier: str) -> dict` — same as above but exits if `decimated_at` IS NULL ("This module isn't decimated. Nothing to reactivate.")

**Config I/O:**

- [ ] `read_module_config_or_die(module_path: Path) -> ConfigSchema` — reads `.archivist/config.yaml` from the given path; `sys.exit(1)` with a useful message if absent or unreadable; returns the parsed config dict
- [ ] `config_path_for_module(module: dict) -> Path` — returns `Path(module["path"]) / ".archivist" / "config.yaml"`; does not check existence

**Diff display:**

- [ ] `print_field_diff(field: str, old_value: object, new_value: object) -> None` — prints a single-line diff in a consistent format: `  git-remote: git@old.com → git@new.com`; used by all dry-run and update paths

**Apparatus resolution:**

- [ ] `resolve_apparatus_name(apparatus_uuid: str) -> str | None` — looks up apparatus by UUID; returns the name, or None if not found (not a die — callers handle missing apparatus differently)

> ⚠️ Every `resolve_*_or_die` function must print its error via `error()` (from `archivist.utils`) before calling `sys.exit(1)`. The exit code is always 1 for these. Never raise exceptions to the user in command code.

---

## 2. `remedy sync`

File: `archivist/commands/remedy/sync.py`

> 🚫 **Blocked on:** `remedy_helpers.py` complete.

Config-driven reconciliation. Reads `.archivist/config.yaml` from the **current working directory's module** and pushes any detected diffs into the registry row. Always writes both sides of the config↔registry boundary when a diff is found.

**Fields reconciled:**

| Config key | Registry column | Validation before write |
|---|---|---|
| `git-remote` | `modules.git_remote` | Non-empty string |
| `module-type` | `modules.module_type` | Must be in `APPARATUS_MODULE_TYPES` |
| `apparatus` | `modules.apparatus_uuid` | Apparatus must exist (or be created with confirmation) |

> ⚠️ `uuid` is never updated. It is the lookup key. If the config UUID doesn't match any registry row, that's a hard error — not a drift to reconcile.

> 📌 `git_remote_name` is re-derived automatically whenever `git-remote` changes: call `get_git_remotes(git_root)` after writing `git_remote` and find the name whose URL matches the new remote. Update `git_remote_name` in the same transaction. If no match, set NULL. Do not prompt the user.

**Implementation checklist:**

- [ ] Read `get_repo_root()` → build `config_path`
- [ ] Call `read_module_config_or_die(repo_root)` → extract UUID
- [ ] Look up module in registry by UUID → `sys.exit(1)` if not found (not drift, not sync-able)
- [ ] For each reconcilable field: compare config value vs registry value
  - [ ] `git-remote` diff detected → validate non-empty → update registry + re-derive `git_remote_name` + update config
  - [ ] `module-type` diff detected → validate against `APPARATUS_MODULE_TYPES` → update registry + update config
  - [ ] `apparatus` diff detected:
    - [ ] Look up target apparatus in registry
    - [ ] If absent: prompt "Apparatus 'X' not found in registry. Create it? [Y/n]" → `register_apparatus(name, None)` on yes; `sys.exit(0)` on no
    - [ ] Update `modules.apparatus_uuid` → update config `apparatus` field
- [ ] `--dry-run`: print all detected diffs via `print_field_diff()`; write nothing
- [ ] No diffs detected: `progress("Everything's already in sync. Enjoy the rare sensation.")` and exit 0
- [ ] N diffs found and written: `success(f"{n} field(s) updated.")`

---

## 3. `remedy inspect`

File: `archivist/commands/remedy/inspect.py`

Read-only. No `--dry-run` needed. Prints the complete registry state for one module and compares it to what's on disk. The "let me see exactly what the registry thinks about this module" command.

> 🚫 **Blocked on:** `remedy_helpers.py` complete.

- [ ] `resolve_active_module_or_die(identifier)` — accepts name or UUID; works for active and decimated (inspect should show decimated modules too; use `resolve_module_or_die` not the active variant)
- [ ] Print module row fields: name, uuid, type, apparatus name (resolved from UUID), path, path-exists check, git-remote, git-remote-name, last-synced, decimated status
- [ ] Print bay memberships: list all containers via `get_module_bays(uuid)`, showing container name, type, path, and whether the container path exists
- [ ] Print config section:
  - [ ] Resolve config path via `config_path_for_module(module)`
  - [ ] If config readable: print relevant fields (`uuid`, `module-type`, `apparatus`, `git-remote`, `vaults`)
  - [ ] If config unreadable or absent: note this clearly; do NOT exit — this is exactly the state `inspect` is designed to surface
- [ ] Visual drift cue: if any config field disagrees with the registry, prefix that line with `⚠️` — do NOT print a programmatic diff or error, just the marker; the user reads it
- [ ] No writes. No side effects. This function cannot break anything.

---

## 4. `remedy orphans`

File: `archivist/commands/remedy/orphans.py`

Read-only audit across the full registry (or one apparatus). No `--dry-run` needed.

> 🚫 **Blocked on:** `remedy_helpers.py` complete.

**Conditions to detect:**

- [ ] `STALE PATH` — `modules.path` does not exist on disk; active modules only (decimated paths are expected to be stale)
- [ ] `MISSING CONFIG` — path exists but `.archivist/config.yaml` is absent or unreadable
- [ ] `UNCONTAINED` — active, non-vault module with zero `module_bays` rows where it is the contained module; emit as advisory (not an error — standalone is valid)
- [ ] `EMPTY APPARATUS` — apparatus with zero active modules
- [ ] `CONFIG DRIFT` — config readable AND (`apparatus` field doesn't match registry apparatus name OR `git-remote` field doesn't match `modules.git_remote`); only checked when path exists and config is readable

**Implementation checklist:**

- [ ] Query: active modules for scope (all, or `--apparatus` filter)
- [ ] For each module: run each condition check; accumulate results by condition type
- [ ] Query: all apparatuses; check active module counts for `EMPTY APPARATUS`
- [ ] Output: group by condition type; list affected modules under each heading; show suggested action per condition (see spec §7.7 table)
- [ ] Summary line at end: count per condition type; e.g. `3 STALE PATH  1 CONFIG DRIFT  2 UNCONTAINED`
- [ ] Exit 0 regardless of findings — this is a reporting tool

> ⚠️ `CONFIG DRIFT` check is skipped for modules with `STALE PATH` or `MISSING CONFIG` — can't read config from a path that doesn't exist. Skip these cleanly; don't report config drift on a module whose path is already flagged.

---

## 5. `remedy set`

File: `archivist/commands/remedy/set.py`

> 🚫 **Blocked on:** `remedy sync` patterns established.

Single-field update. Writes both registry and config (where a config equivalent exists).

- [ ] `resolve_active_module_or_die(identifier)` — name or UUID
- [ ] Dispatch on `field`:
  - [ ] `git-remote`:
    - [ ] Validate value is a non-empty string
    - [ ] Update `modules.git_remote`
    - [ ] Re-derive `git_remote_name` via `get_git_remotes()` on the module's path; update `modules.git_remote_name`
    - [ ] Update config `git-remote` field via `write_archivist_config()`
  - [ ] `module-type`:
    - [ ] Validate value is in `APPARATUS_MODULE_TYPES`
    - [ ] Update `modules.module_type`
    - [ ] Update config `module-type` field via `write_archivist_config()`
  - [ ] `name`:
    - [ ] Validate slug via `validate_slug()` from `registry.py`
    - [ ] Update `modules.name`
    - [ ] No config equivalent — note this in `--dry-run` output: `name: no config equivalent; registry only`
  - [ ] Anything else: `error(f"'{field}' is not a settable field. ...")` listing valid options; `sys.exit(1)`
- [ ] `--dry-run`: print `print_field_diff(field, old, new)`; write nothing
- [ ] On success: `success(f"Set {field} on '{module['name']}'.")` and print the new value

> ⚠️ The module's config is read from `config_path_for_module(module)`. If the config path doesn't exist or is unreadable, `remedy set` still updates the registry but warns: "Config at '[path]' is unreadable — registry updated but config not synced. Run remedy sync from the module directory when it's accessible." Do not abort the registry update because the config is stale.

---

## 6. `remedy move`

File: `archivist/commands/remedy/move.py`

Reassign a module from one apparatus to another. Touches `modules.apparatus_uuid` and the config `apparatus` field. Does NOT touch `module_bays`.

> 🚫 **Blocked on:** `remedy set` patterns established.

- [ ] `resolve_active_module_or_die(identifier)` — name or UUID
- [ ] Resolve current apparatus name via `resolve_apparatus_name(module["apparatus_uuid"])`
- [ ] Look up `--apparatus` target:
  - [ ] Found: proceed
  - [ ] Not found: prompt "Apparatus 'X' not found. Create it? [Y/n]"
    - [ ] Yes: `register_apparatus(new_name, git_remote=None)` → proceed
    - [ ] No: `sys.exit(0)` with "Aborted."
- [ ] Confirm: print current → new apparatus assignment; prompt "[Y/n]" (unless `--dry-run`)
- [ ] `--dry-run`: print `print_field_diff("apparatus", current_name, new_name)`; exit
- [ ] Update `modules.apparatus_uuid` to target apparatus UUID
- [ ] Update config `apparatus` field via `write_archivist_config()` (best-effort; warn if config unreadable)
- [ ] Check old apparatus active module count:
  - [ ] Zero remaining: warn "Apparatus '[old]' now has no active modules. Remove it with `remedy obliterate-apparatus '[old]'` if you're done with it."
  - [ ] Modules remain: no note needed
- [ ] `success(f"Moved '{module['name']}' from '{old_apparatus}' to '{new_apparatus}'.")`

---

## 7. `remedy transfer`

File: `archivist/commands/remedy/transfer.py`

Move a module's vault membership. Updates `module_bays`. Does NOT touch apparatus membership.

> 🚫 **Blocked on:** `remedy move` patterns established.

- [ ] `resolve_active_module_or_die(identifier)` — name or UUID
- [ ] Resolve current bay memberships via `get_module_bays(module_uuid)`
- [ ] Resolve `--from-vault`:
  - [ ] `--from-vault` absent AND exactly one current vault: use it automatically; note which one
  - [ ] `--from-vault` absent AND multiple current vaults: `sys.exit(1)` listing the vaults and instructing `--from-vault`
  - [ ] `--from-vault` absent AND no current vaults: proceed (transferring from "uncontained" to a vault is valid)
  - [ ] `--from-vault` provided: look up vault module; validate it contains this module (exists in `module_bays`)
- [ ] Resolve `--to-vault`:
  - [ ] `"none"` (literal): target is "no vault" — removal only
  - [ ] Otherwise: look up vault module by name or UUID; `sys.exit(1)` if not found
- [ ] Confirm: print move plan; prompt "[Y/n]" (unless `--dry-run`)
- [ ] `--dry-run`: print plan; exit
- [ ] If `--from-vault` resolved: `remove_module_from_bay(from_vault_uuid, module_uuid)`
- [ ] If `--to-vault` is not `"none"`: `add_module_to_bay(to_vault_uuid, module_uuid)`
- [ ] Update config `vaults` field: rebuild list from current bays after the operation; `write_archivist_config()` (best-effort; warn if config unreadable)
- [ ] `success(...)` with a summary of what moved where

> 📌 The `vaults` config field is a list of vault **names** (slugs), not UUIDs. After updating `module_bays`, re-query `get_module_bays(module_uuid)` to get the current containers, resolve each to a name, and write that list to config. Do not try to maintain the list manually in memory.

---

## 8. `remedy reactivate`

File: `archivist/commands/remedy/reactivate.py`

Bring a decimated module back. Clear `decimated_at`. Optionally reassign apparatus and vault in the same operation.

> 🚫 **Blocked on:** `remedy move` and `remedy transfer` patterns established (their logic may be called internally).

- [ ] `resolve_module_or_die(identifier)` — accepts name or UUID; works for active AND decimated
- [ ] If module is NOT decimated: `error("This module isn't decimated. Nothing to reactivate.")` and `sys.exit(1)` (do not proceed)
- [ ] Print module details: name, type, path, apparatus, last known vaults, `decimated_at` date
- [ ] Confirm: "[Y/n]" prompt (unless `--dry-run`)
- [ ] `--dry-run`: print what would change; print `--apparatus` and `--vault` additions if provided; exit
- [ ] `reactivate_module(uuid)` — clears `decimated_at`
- [ ] If `--apparatus` provided: run `remedy move` logic inline (update `apparatus_uuid` + config)
- [ ] If `--vault` provided: run `remedy transfer` logic inline (add bay row + update config)
- [ ] `success(...)` with summary; note "Run archivist census to confirm state."

> ⚠️ Do not import `remedy move` or `remedy transfer` as modules and call their `run()`. Extract the write logic from those modules into helpers in `remedy_helpers.py` if it needs to be shared, or duplicate it explicitly with a comment. Command `run()` functions are not a shared API.

---

## 9. `remedy rename-apparatus`

File: `archivist/commands/remedy/rename_apparatus.py`

Rename an apparatus. Touches the `apparatuses` table and renames a file on disk. **File rename happens before the row update.** This is not optional. See spec §7.8 for the rationale.

> 🚫 **Blocked on:** all write commands established; this is the most failure-sensitive operation in the suite.

- [ ] Look up `old-name` in apparatuses → `sys.exit(1)` if not found
- [ ] `validate_slug(new_name)` → `sys.exit(1)` with clear message if invalid
- [ ] Check `new-name` does not already exist in apparatuses → `sys.exit(1)` if it does
- [ ] Confirm: "Rename apparatus '[old]' to '[new]'? [Y/n]" (unless `--dry-run`)
- [ ] `--dry-run`:
  - [ ] Print: file rename `~/.archivist/[old].db → ~/.archivist/[new].db`
  - [ ] Print: `UPDATE apparatuses SET name = '[new]', db_path = '...'`
  - [ ] Print: N module configs would be updated
  - [ ] Exit
- [ ] **Step 1 — file rename first:**
  - [ ] `old_db.rename(new_db)` where `old_db = get_apparatus_db_path(old_name)` and `new_db = get_apparatus_db_path(new_name)`
  - [ ] On `OSError`: `error(...)` with full path; `sys.exit(1)`; do NOT proceed to row update
- [ ] **Step 2 — row update second:**
  - [ ] `UPDATE apparatuses SET name = ?, db_path = ? WHERE name = ?` with `(new_name, str(new_db), old_name)`
  - [ ] On failure: print error AND manual recovery instructions (see spec §7.8); `sys.exit(1)`
- [ ] **Step 3 — config updates (best-effort):**
  - [ ] `get_apparatus_modules(old_name)` — query uses OLD name before the rename; this must be called BEFORE step 2 to capture the module list while the old name is still resolvable, OR re-query by `apparatus_uuid` after the rename
  - [ ] For each module: read config; if `apparatus` field matches old name, update to new name; write config; log success or warn on failure
- [ ] Summary: "Apparatus renamed. N module configs updated. M configs unreachable (run remedy sync from those modules)."

> ⚠️ Capture the module list via the apparatus UUID (not name) if querying after the row rename. After step 2, `old_name` no longer resolves. Query `get_apparatus_modules` by UUID or query before renaming the row.

---

## 10. `remedy obliterate-apparatus`

File: `archivist/commands/remedy/obliterate_apparatus.py`

Hard-delete an apparatus and its database file. Irreversible. Implement last. Gate heavily.

> 🚫 **Blocked on:** all other `remedy` commands complete and tested.

- [ ] Look up `<name>` in apparatuses → `sys.exit(1)` if not found
- [ ] Count active (non-decimated) modules → if any: `error(...)` listing them by name; `sys.exit(1)`. There is no override for this. Active modules block obliteration, full stop.
- [ ] Count decimated modules:
  - [ ] Any exist AND `--including-decimated` not provided: `error("Apparatus has N decimated module(s). Pass --including-decimated to confirm you want to lose this history.")` and `sys.exit(1)`
  - [ ] Any exist AND `--including-decimated` provided: proceed
  - [ ] None exist: proceed
- [ ] Confirmation prompt: "This will permanently delete apparatus '[name]' and all its data. This cannot be undone. Type the apparatus name to confirm: "
  - [ ] Input must match `name` exactly (case-sensitive); anything else: "Aborted." and `sys.exit(0)`
  - [ ] This prompt fires even with `--dry-run` — a dry run that skips confirmation is not a dry run
- [ ] `--dry-run`:
  - [ ] After confirmation: print what would be deleted (apparatus row, module rows if `--including-decimated`, DB file)
  - [ ] Exit without writing anything
- [ ] `DELETE FROM modules WHERE apparatus_uuid = ?` (decimated rows only, if `--including-decimated`; active rows were already blocked above)
- [ ] `DELETE FROM apparatuses WHERE name = ?`
- [ ] Delete `~/.archivist/[name].db`:
  - [ ] On `OSError`: warn that registry row is already gone and print path for manual cleanup; do NOT re-add the row
- [ ] `success(f"Apparatus '{name}' and its database have been removed.")`

> 🔴 The confirmation prompt requires typing the apparatus name verbatim. A simple "y" is not sufficient for a destructive, irreversible operation. This is intentional and not subject to debate.

---

## 11. CLI Parser Updates

> 🚫 **Blocked on:** at least `remedy sync` and `remedy inspect` exist and are wired.

The `remedy` parser is a parent parser with a `dest="remedy_command"` subparser group. Each subcommand is registered under it. Per `CLAUDE.md`, parser definitions are in the "What Not to Touch" category unless adding or removing a subcommand — these additions qualify.

- [ ] Parent parser:
  ```python
  remedy_p = subparsers.add_parser("remedy", help="Registry maintenance tools.")
  remedy_sub = remedy_p.add_subparsers(dest="remedy_command")
  ```
- [ ] `remedy sync` parser:
  ```python
  remedy_p_sync = remedy_sub.add_parser("sync", help="Reconcile config with registry.")
  remedy_p_sync.add_argument("--dry-run", action="store_true")
  ```
- [ ] `remedy set` parser:
  ```python
  remedy_p_set = remedy_sub.add_parser("set", help="Update a single field on a module.")
  remedy_p_set.add_argument("module", help="Module name or UUID.")
  remedy_p_set.add_argument("field", choices=["git-remote", "module-type", "name"])
  remedy_p_set.add_argument("value")
  remedy_p_set.add_argument("--dry-run", action="store_true")
  ```
- [ ] `remedy move` parser:
  ```python
  remedy_p_move = remedy_sub.add_parser("move", help="Reassign a module to a different apparatus.")
  remedy_p_move.add_argument("module", help="Module name or UUID.")
  remedy_p_move.add_argument("--apparatus", required=True, metavar="NAME")
  remedy_p_move.add_argument("--dry-run", action="store_true")
  ```
- [ ] `remedy transfer` parser:
  ```python
  remedy_p_transfer = remedy_sub.add_parser("transfer", help="Move a module between vaults.")
  remedy_p_transfer.add_argument("module", help="Module name or UUID.")
  remedy_p_transfer.add_argument("--to-vault", required=True, metavar="NAME|UUID|none",
                                  dest="to_vault")
  remedy_p_transfer.add_argument("--from-vault", metavar="NAME|UUID", dest="from_vault")
  remedy_p_transfer.add_argument("--dry-run", action="store_true")
  ```
- [ ] `remedy reactivate` parser:
  ```python
  remedy_p_reactivate = remedy_sub.add_parser("reactivate", help="Bring a decimated module back.")
  remedy_p_reactivate.add_argument("module", help="Module name or UUID.")
  remedy_p_reactivate.add_argument("--apparatus", metavar="NAME")
  remedy_p_reactivate.add_argument("--vault", metavar="NAME|UUID")
  remedy_p_reactivate.add_argument("--dry-run", action="store_true")
  ```
- [ ] `remedy inspect` parser:
  ```python
  remedy_p_inspect = remedy_sub.add_parser("inspect", help="Print full registry state for a module.")
  remedy_p_inspect.add_argument("module", help="Module name or UUID.")
  ```
- [ ] `remedy orphans` parser:
  ```python
  remedy_p_orphans = remedy_sub.add_parser("orphans", help="Audit registry for drift and stale entries.")
  remedy_p_orphans.add_argument("--apparatus", metavar="NAME")
  ```
- [ ] `remedy rename-apparatus` parser:
  ```python
  remedy_p_rename_ap = remedy_sub.add_parser("rename-apparatus",
                                              help="Rename an apparatus.")
  remedy_p_rename_ap.add_argument("old_name", metavar="old-name")
  remedy_p_rename_ap.add_argument("new_name", metavar="new-name")
  remedy_p_rename_ap.add_argument("--dry-run", action="store_true")
  ```
- [ ] `remedy obliterate-apparatus` parser:
  ```python
  remedy_p_obliterate = remedy_sub.add_parser("obliterate-apparatus",
                                               help="Permanently delete an apparatus and its database.")
  remedy_p_obliterate.add_argument("name")
  remedy_p_obliterate.add_argument("--including-decimated", action="store_true",
                                    dest="including_decimated")
  remedy_p_obliterate.add_argument("--dry-run", action="store_true")
  ```
- [ ] Dispatch in `cli.py`: add `elif args.command == "remedy"` branch; dispatch to `remedy_command` subparser; handle missing subcommand (print remedy help and exit 0)
- [ ] Import: add `from archivist.commands.remedy import sync, inspect, orphans, set as set_, move, transfer, reactivate, rename_apparatus, obliterate_apparatus` (or equivalent)

> ⚠️ `set` is a Python builtin. The module must be named `set.py` to match the subcommand, but the import alias in `cli.py` must avoid shadowing the builtin. `import archivist.commands.remedy.set as remedy_set` or equivalent.

> 📌 `archivist remedy` with no subcommand: `remedy_p.print_help()` and `sys.exit(0)`. Do not default to any subcommand. This is consistent with the `frontmatter` and `changelog` parent parsers.

---

## 12. Testing: Phase 3

Run the full test suite before writing a single new test. Phase 3 tests depend on Phase 2 being stable.

**Registry isolation applies to every test in this suite.** The `autouse` fixture from Phase 1 (`isolated_registry`) must be active for all Phase 3 tests. No Phase 3 test writes to the real `~/.archivist/`.

> 🔴 **Tests must never touch `~/.archivist/`.** If you're writing a test and you didn't confirm the isolation fixture is active, you've already fucked up.

**Fixture requirements:**

Phase 3 tests need the `multi_module_registry` fixture from Phase 2 plus some additions. Extend it or compose from it — do not duplicate it.

The Phase 3 extended fixture should add:

```python
# At minimum, Phase 3 tests need:
# - One apparatus: "writing"
# - Two vault modules: "fiction-vault", "research-vault"  
# - Two library modules under "fiction-vault": "cosmic-horror", "panopticon"
# - One standalone library: "standalone-lib" (no bay membership)
# - One decimated module: "old-project"
# - Actual .archivist/config.yaml files on disk for each module whose path exists
#   (remedy sync and inspect read config from disk)
# - A tmp_path layout with real directories for module paths (so path.exists() checks work)
```

The key addition vs Phase 2: Phase 3 commands read from disk. The registry needs real paths pointing to real temp directories with real config files, not just registry rows with placeholder paths.

---

### Unit Tests: `tests/unit/test_remedy_helpers.py`

- [ ] `resolve_module_or_die()` — happy path (by name); happy path (by UUID); name takes priority over UUID when identical? (no — UUID checked first; document and test)
- [ ] `resolve_module_or_die()` — not found → `sys.exit(1)` confirmed via `pytest.raises(SystemExit)`
- [ ] `resolve_active_module_or_die()` — exits on decimated module; passes on active module
- [ ] `resolve_decimated_module_or_die()` — exits on active module; passes on decimated module
- [ ] `read_module_config_or_die()` — happy path; exits if absent; exits if unreadable
- [ ] `config_path_for_module()` — returns correct path; does not check existence
- [ ] `print_field_diff()` — output format matches spec; handles None values gracefully
- [ ] `resolve_apparatus_name()` — returns name when found; returns None when not found (does NOT exit)

---

### Unit Tests: `tests/unit/test_remedy_sync.py`

- [ ] No diffs detected → no writes; progress message printed
- [ ] `git-remote` diff detected → registry updated; config updated; `git_remote_name` re-derived
- [ ] `module-type` diff detected → registry updated; config updated
- [ ] `apparatus` diff detected → registry updated; config updated
- [ ] `apparatus` diff to non-existent apparatus → user declines creation → exit 0 with no writes
- [ ] `apparatus` diff to non-existent apparatus → user accepts creation → apparatus created; registry updated; config updated
- [ ] UUID in config not found in registry → `sys.exit(1)`; no writes
- [ ] Config absent → `sys.exit(1)`
- [ ] `--dry-run` → diffs printed; ZERO writes (compare file set and registry state before/after)
- [ ] `--dry-run` with no diffs → "Everything's already in sync" printed; no writes

---

### Unit Tests: `tests/unit/test_remedy_inspect.py`

- [ ] Active module → full output including bay memberships
- [ ] Decimated module → output includes decimated marker
- [ ] Module with stale path → path shown with `✗`; no crash
- [ ] Module with unreadable config → config section shows "unreadable"; does not exit
- [ ] Config field disagrees with registry → `⚠️` prefix on drifted line
- [ ] Module in multiple bays → all containers listed
- [ ] Module with no bay memberships → "No container vaults" (or equivalent); no crash

---

### Unit Tests: `tests/unit/test_remedy_orphans.py`

- [ ] `STALE PATH` detected for module with non-existent path
- [ ] `MISSING CONFIG` detected for module whose path exists but config absent
- [ ] `UNCONTAINED` detected for active non-vault module with no bay rows
- [ ] `EMPTY APPARATUS` detected for apparatus with zero active modules
- [ ] `CONFIG DRIFT` detected for `apparatus` field mismatch
- [ ] `CONFIG DRIFT` detected for `git-remote` field mismatch
- [ ] `CONFIG DRIFT` NOT reported for module with `STALE PATH` (skipped correctly)
- [ ] `--apparatus` filter: only modules in that apparatus checked
- [ ] All-clean registry → "No issues found." or equivalent; exit 0
- [ ] Always exits 0 regardless of findings

---

### Unit Tests: `tests/unit/test_remedy_set.py`

- [ ] `git-remote` updated in registry and config; `git_remote_name` re-derived
- [ ] `module-type` updated in registry and config; validates against `APPARATUS_MODULE_TYPES`
- [ ] `name` updated in registry only; no config write; dry-run notes "registry only"
- [ ] Invalid field → exits with error listing valid choices
- [ ] Invalid `module-type` value → exits with error
- [ ] Config unreadable → registry updated; warning printed; no crash
- [ ] `--dry-run` → no writes; diff printed
- [ ] Module not found → exits with error

---

### Unit Tests: `tests/unit/test_remedy_move.py`

- [ ] Happy path: `apparatus_uuid` updated in registry; config `apparatus` updated
- [ ] Target apparatus doesn't exist → user accepts creation → apparatus created; module moved
- [ ] Target apparatus doesn't exist → user declines → exit 0; no writes
- [ ] Old apparatus now empty → warning printed
- [ ] Old apparatus still has modules → no warning
- [ ] Bay memberships unchanged
- [ ] Config unreadable → registry updated; warning printed
- [ ] Module not found → exits
- [ ] `--dry-run` → no writes; plan printed

---

### Unit Tests: `tests/unit/test_remedy_transfer.py`

- [ ] Happy path (single current vault, no `--from-vault`): bay row updated; config `vaults` updated
- [ ] Multiple current vaults, `--from-vault` absent → exits with error listing vaults
- [ ] Multiple current vaults, `--from-vault` provided → correct bay removed; correct bay added
- [ ] `--to-vault none` → bay row removed; module uncontained; config `vaults` cleared
- [ ] `--from-vault` points to vault that does NOT contain this module → exits with error
- [ ] `--to-vault` not found → exits with error
- [ ] Module currently uncontained (no bays) → no `--from-vault` removal attempted; bay added for `--to-vault`
- [ ] Config rebuilt from actual post-operation bay state (not from pre-operation state)
- [ ] `--dry-run` → no writes
- [ ] Module not found → exits

---

### Unit Tests: `tests/unit/test_remedy_reactivate.py`

- [ ] Decimated module → confirmation → `decimated_at` cleared
- [ ] Active module → exits immediately with "nothing to reactivate"
- [ ] `--apparatus` provided → apparatus reassigned after reactivation
- [ ] `--vault` provided → bay row added after reactivation
- [ ] Confirmation declined → exit 0; no writes
- [ ] `--dry-run` → confirmation NOT skipped; after confirming, no writes made
- [ ] Module not found → exits

---

### Unit Tests: `tests/unit/test_remedy_rename_apparatus.py`

- [ ] Happy path: DB file renamed; row updated; module configs updated
- [ ] `old-name` not found → exits
- [ ] `new-name` already exists → exits
- [ ] `new-name` invalid slug → exits
- [ ] File rename fails → row NOT updated; exits with error
- [ ] Row update fails → manual recovery instructions printed; exits
- [ ] Module config unreadable → warning; rename proceeds; summary notes M unreachable
- [ ] `--dry-run` → file NOT renamed; row NOT updated; plan printed
- [ ] Post-rename: `get_apparatus_modules(new_name)` returns same modules as pre-rename

---

### Unit Tests: `tests/unit/test_remedy_obliterate_apparatus.py`

- [ ] Active modules present → exits; no writes; lists active modules
- [ ] Decimated modules without `--including-decimated` → exits with instructive error
- [ ] Decimated modules with `--including-decimated` → proceeds after confirmation
- [ ] Confirmation mismatch → "Aborted."; no writes
- [ ] Confirmation match → apparatus row deleted; DB file deleted; decimated module rows deleted (if `--including-decimated`)
- [ ] DB file deletion fails → warning + manual cleanup instructions; registry row already gone; no crash
- [ ] `--dry-run` → confirmation still fires; after confirming, plan printed; no writes
- [ ] Empty apparatus (no modules at all) → proceeds with confirmation

---

### Integration Tests: `tests/integration/test_remedy.py`

Integration tests call `run()` directly against real `tmp_path` directories with real config files and a real (isolated) registry. `monkeypatch.chdir()` as appropriate.

**`remedy sync` integration:**

- [ ] All three reconcilable fields drift simultaneously → all three updated in one run
- [ ] Dry-run contract: `test_dry_run_writes_absolutely_nothing` — files AND registry unchanged before vs after

**`remedy rename-apparatus` integration:**

- [ ] Actual DB file renamed on disk; old path gone; new path exists
- [ ] Registry row reflects new name and new `db_path`
- [ ] Module configs updated on disk (read back and verify `apparatus` field)

**`remedy obliterate-apparatus` integration:**

- [ ] DB file actually gone from `tmp_path` after obliteration
- [ ] Registry row gone
- [ ] Dry-run: DB file still exists; registry row still exists

**Write-both-sides contract (applies to all write commands):**

For every command that updates the registry, add an assertion that the corresponding config field on disk also reflects the change:

```python
# Pattern for every write command test
run_whatever(args)
updated_registry = get_module_by_uuid(uuid)
updated_config = read_archivist_config(module_path)
assert updated_registry["some_field"] == expected_value
assert updated_config["some-key"] == expected_value, (
    "Registry updated but config wasn't. "
    "Write-both-sides contract violated."
)
```

This assertion is not optional. It is the primary contract of the entire Phase 3 feature. If it fails on any command, that command is not done.

---

### Dry-run Contract

Every write command in Phase 3 gets `test_dry_run_writes_absolutely_nothing`. Same pattern as Phases 1 and 2: compare file sets before and after; compare registry state before and after.

```python
def test_dry_run_writes_absolutely_nothing(self, ...):
    before_files = {p for p in tmp_path.rglob("*") if p.is_file()}
    before_module = get_module_by_uuid(uuid)

    run_whatever(_args(dry_run=True))

    after_files = {p for p in tmp_path.rglob("*") if p.is_file()}
    after_module = get_module_by_uuid(uuid)
    assert before_files == after_files, "dry_run=True and files still changed."
    assert before_module == after_module, "dry_run=True and registry still changed."
```

---

## Phase 3 Completion Gate

Before marking Phase 3 done:

- [ ] All tests pass: `pytest -v`
- [ ] No regressions in Phase 1, Phase 2, or pre-existing test suite
- [ ] `remedy sync` correctly reconciles all three fields in one run
- [ ] `remedy inspect` shows a clean diff-free view for an in-sync module
- [ ] `remedy orphans` correctly identifies all five condition types on a seeded registry
- [ ] `remedy rename-apparatus` leaves the DB file on disk with the new name and the row consistent
- [ ] `remedy obliterate-apparatus` refuses to proceed with active modules; proceeds correctly after confirmation for an empty apparatus
- [ ] Write-both-sides contract verified for every write command: registry change AND config change confirmed in the same test
- [ ] Dry-run contract: every write command tested; no files and no registry changes on `--dry-run`
- [ ] Manual smoke test: init a new module; drift the config manually; `remedy orphans` flags it; `remedy sync` fixes it; `remedy inspect` shows clean state