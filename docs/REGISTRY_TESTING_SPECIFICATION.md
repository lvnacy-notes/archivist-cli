# Registry Testing Specification

> The registry is the only source of truth for module identity. Break it and you break everything downstream simultaneously. There is no partial failure mode here — either the registry is correct or it isn't.

---

## Purpose

This document is the authoritative reference for testing `archivist/utils/registry.py` and any command that reads from or writes to it. It covers isolation requirements, the test coverage contract, patterns specific to registry testing, and the rules for evolving the suite as the Apparatus Platform grows.

Read this before touching `registry.py`, `add.py`, `deinit.py`, or any Phase 2 command that interacts with the registry. The isolation requirement below is absolute. It is not negotiable and it does not have exceptions.

---

## The Isolation Requirement

**No test in this suite may ever write to the real `~/.archivist/`.**

Every test module that imports from `registry.py` — directly or via a command under test — must define and activate an `isolated_registry` fixture:

```python
import archivist.utils.registry as registry_module

@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    fake_dir = tmp_path / ".archivist"
    monkeypatch.setattr(registry_module, "get_registry_dir", lambda: fake_dir)
    return fake_dir
```

**Why `registry_module` and not a string path?** Patching via the module object guarantees the patch lands on the correct reference in memory, regardless of how other modules imported the function. String-based patching (`monkeypatch.setattr("archivist.utils.registry.get_registry_dir", ...)`) is fragile when the function has already been imported by name into another module's namespace.

**Why `autouse=True`?** Because a test that forgets to use the fixture is silently writing to the developer's machine. `autouse=True` removes the possibility of forgetting.

**Why not in the global `conftest.py`?** Because patching `get_registry_dir()` globally would affect changelog, frontmatter, and seal tests that don't touch the registry at all. Keep it scoped to the modules that need it.

**The consequence of getting this wrong:** A test run that writes to `~/.archivist/` can corrupt a developer's real registry, destroy real apparatus state, or interfere with real module registrations. This has already happened once. Don't let it happen again.

---

## Two Databases, Two Concerns

The Apparatus Platform uses two database types. Keep them straight.

**`registry.db`** — lives in `~/.archivist/`. Machine-level. Contains `apparatuses`, `modules`, and `module_bays`. This is what `registry.py` manages. It is the subject of `test_registry.py`.

**`[name].db`** (apparatus DB) — also lives in `~/.archivist/`. One per apparatus. Contains `changelogs`, `works`, `authors`, `works_authors`. Created by `init_apparatus_db()` as a side effect of `register_apparatus()`. The columns that reference `registry.db` (e.g. `changelogs.module_uuid`) are **logical foreign keys only** — SQLite cannot enforce cross-file constraints. Application code is responsible for checking registry state before writing.

Unit tests cover each database's functions in isolation. Integration tests (`test_registry_commands.py`, when built) cover the seams between them.

---

## What `test_registry.py` Covers

### Path resolution

All path functions derive from `get_registry_dir()`. Testing them confirms the derivation is correct and that no path helper has accidentally hardcoded a path.

| Function | What's pinned |
|:---------|:--------------|
| `get_registry_dir` | Returns a `Path`; last component is `.archivist` |
| `get_registry_path` | Equals `get_registry_dir() / "registry.db"` |
| `get_apparatus_db_path` | Equals `get_registry_dir() / "{name}.db"` for any name |

### Slug validation

`validate_slug` is the gatekeeper for every apparatus name and module name. Names become filenames and must be filesystem-safe.

| Scenario | Expected |
|:---------|:---------|
| Lowercase alphanumeric | Accepted |
| Hyphen-separated | Accepted |
| Uppercase | `ValueError` |
| Spaces | `ValueError` |
| Leading hyphen | `ValueError` (looks like a flag) |
| Special characters | `ValueError` |
| Empty string | `ValueError` |
| `label` parameter | Appears in error message |

### `init_registry`

Three entry states, all must work without corrupting data:

| State | Expected behaviour |
|:------|:------------------|
| `~/.archivist/` absent | Directory created; `git init` run; schema created |
| Directory present, no `registry.db` | Schema created; existing directory untouched |
| Both present | No-op; existing data preserved |

Additional pins: all three schema tables present (`apparatuses`, `modules`, `module_bays`); `.git` directory created; calling twice is safe; existing apparatus rows survive a second `init_registry()` call.

### `init_apparatus_db`

| Scenario | Expected |
|:---------|:---------|
| Fresh DB | Created with all four tables (`changelogs`, `works`, `authors`, `works_authors`) |
| Existing DB | No-op; data preserved |
| Invalid name | `ValueError` before disk is touched |

### Connection management

Foreign key enforcement is OFF by default in SQLite. Every connection opened by `_open_connection` must execute `PRAGMA foreign_keys = ON`. This is tested by attempting a FK violation and confirming `sqlite3.IntegrityError` is raised. If it isn't, the pragma isn't firing.

Both `get_registry_connection()` and `get_apparatus_connection()` are tested.

### Apparatus lifecycle

| Function | What's pinned |
|:---------|:--------------|
| `register_apparatus` | Returns UUID string; apparatus row exists after call; apparatus DB created as side effect; `git_remote` stored correctly; `git_remote=None` accepted; invalid slug raises `ValueError` before any DB write |
| Upsert behaviour | Same name twice returns same UUID; only one row exists |
| Multiple apparatuses | Different names produce different UUIDs; both queryable |
| `get_apparatus_by_name` | Returns dict with `uuid`, `name`, `db_path`, `created_at`; returns `None` for unknown name |

### Module lifecycle

| Function | What's pinned |
|:---------|:--------------|
| `register_module` | Returns UUID; row exists after call; stored path is absolute; `decimated_at` is `NULL` on fresh registration |
| Invalid `module_type` | `ValueError` raised before any DB write; no orphaned row |
| Unknown apparatus | `ValueError` before any DB write |
| All five module types | Every value in `APPARATUS_MODULE_TYPES` accepted |
| Upsert by path | Same path twice returns same UUID; no duplicate row; `git_remote` updated on re-registration |
| `get_module_by_uuid` | Returns dict for known UUID; `None` for unknown |
| `get_module_by_path` | Returns dict for known path; `None` for unknown; resolves relative paths to absolute before querying |
| `is_module_registered` | `True` for active UUID; `False` for unknown UUID |

### Decimation and reactivation

Modules are never hard-deleted. Understand this or you will write bugs.

| Scenario | Expected |
|:---------|:---------|
| `decimate_module` | `decimated_at` set to non-null date string |
| `decimate_module` unknown UUID | `ValueError` |
| Row still exists after decimation | `get_module_by_uuid` still returns the row |
| `is_module_registered` after decimation | Still `True` — decimated ≠ deleted |
| `reactivate_module` | `decimated_at` cleared to `NULL` |
| `reactivate_module` unknown UUID | `ValueError` |
| Decimated module excluded from `get_apparatus_modules` | Not returned without `include_decimated=True` |
| Decimated module returned with flag | Appears with `include_decimated=True` |
| Decimate → reactivate → decimate | Does not raise at any step |

### `update_module_sync`

This is called by the pre-commit hook on every commit. Its contract is strict: unknown UUIDs must be a silent no-op. The hook cannot abort a commit because a registry entry is missing.

| Scenario | Expected |
|:---------|:---------|
| Known UUID | `last_synced_at` updated |
| Unknown UUID | Silent no-op; no exception |

### Bay management

| Function | What's pinned |
|:---------|:--------------|
| `add_module_to_bay` | Row created; idempotent (INSERT OR IGNORE; second call does not duplicate or raise) |
| `remove_module_from_bay` | Row deleted; no-op for absent pair |
| `remove_all_bays_for_contained` | All rows where `contained_id` matches are removed; rows where the module is the *container* are not touched; other modules' bays undisturbed |
| `get_module_bays` | Returns all containers; empty list when none; does not return decimated containers |

### Queries

| Function | What's pinned |
|:---------|:--------------|
| `get_apparatus_modules` | Returns active modules; excludes decimated by default; includes with `include_decimated=True`; sorted by name; empty list for unknown apparatus; empty list for apparatus with no modules |
| `get_bay_modules` | Returns active contained modules; excludes decimated by default; includes with flag; sorted by name; empty list for unknown container |
| `get_vault_modules` | Delegates to `get_bay_modules`; raises `ValueError` for non-vault container type; raises `ValueError` for unknown UUID |
| `list_apparatus_names` | Returns all names sorted alphabetically; empty list when no apparatuses; empty list when registry does not exist — does not raise |

### `prompt_apparatus_names`

Unlike the git+confirm-chain interactive flows in `add` and `migrate` (see Accepted Gaps below), this function is pure enough to unit test directly — `list_apparatus_names()` reads plus `input()`/`print()`, no DB writes of its own. Faking `input()` is enough; no `git_repo`, no subprocess mocking.

| Scenario | Expected |
|:---------|:---------|
| No apparati registered yet | Numbered menu skipped entirely; goes straight to the new-slug prompt |
| One or more apparati exist | Numbered menu shown, options sorted alphabetically (`list_apparatus_names()`'s contract), "Create new" always last |
| Comma- or space-separated numbers | Selects multiple in one line (e.g. `"1, 2"` or `"1 2"`) |
| "Create new" combined with existing picks on one line | Both take effect — slug prompt fires, result includes everything picked that round |
| Invalid token, out-of-range number, or empty input | Reprompts the same round; does not consume the next queued answer |
| Selecting the same number twice in one line | No duplicate in the result |
| Apparatus already selected in an earlier round | Excluded from later rounds' menus — not just deduplicated after the fact |
| Invalid slug at the "Create new" sub-prompt | Reprompts the slug question only; does not bounce back to the main menu |
| Duplicate slug against an already-selected name | Rejected and reprompted |
| "Add another apparatus?" — `y` / `yes` / anything else | `y`/`yes` loops for another round; anything else returns |
| Empty result | Never happens — the function always returns at least one name |

### Stubs

`commit_registry` and `push_registry` are Phase 2 placeholders. They are tested only to confirm they are callable and do not raise. There is no other behaviour to pin.

---

## What `test_add.py` and `test_deinit.py` Cover (Registry Perspective)

These are command-level tests, but they are the integration layer between the registry and the git/filesystem operations. The registry assertions in these tests are the ones that matter most for catching operation-order bugs.

### `test_add.py` — registry assertions

- Module row exists after successful git operation
- Module row does NOT exist when git fails — git is the gate
- Bay row created when cwd is a registered, active superproject
- Bay row NOT created when cwd module is decimated
- Re-add of active module: upsert, not duplicate row
- Re-add of decimated module: `decimated_at` cleared
- `vaults` list in target config updated when container is vault-type

### `test_deinit.py` — registry assertions

- Bay row removed after deinit from superproject context
- Only the scoped bay row removed — other containers' rows survive
- Module decimated when no bay rows remain
- Module NOT decimated when bay rows remain
- All bay rows removed in standalone mode
- Module still alive in registry after user declines confirmation
- Module still alive in registry after dry-run
- Registry clean before `sys.exit` on any guard condition (cwd inside target, not registered, no registry)
- Already-decimated module: re-run does not raise; `decimated_at` remains set

---

## Cross-Database Integrity — `test_registry_commands.py`

**Not yet built. Build it before any command that writes to an apparatus DB ships.**

The apparatus DB columns that reference `registry.db` are unenforced by SQLite. Application code is the only guard. These tests verify that guard exists and cannot be bypassed.

The pattern: bring both databases live under `isolated_registry`, change module state in the registry, then assert that the subsequent apparatus DB write is correctly handled by application code.

```python
def test_apparatus_write_rejected_for_decimated_module(tmp_path, monkeypatch):
    init_registry()
    register_apparatus("writing", git_remote=None)
    module_uuid = register_module(
        apparatus_name="writing",
        name="cosmic-horror",
        module_type="library",
        path=tmp_path / "cosmic-horror",
        git_remote=None,
    )
    decimate_module(module_uuid)

    # module_uuid is now decimated. Any command that writes to the apparatus DB
    # using this UUID must check registry state first and reject the write.
    # Application code raises or calls sys.exit — SQLite will not catch this.
    with pytest.raises((ValueError, SystemExit)):
        some_apparatus_write_command(module_uuid=module_uuid)
```

The exact exception type depends on how the command signals rejection. What matters is that the write does not silently succeed.

**Required tests for every apparatus DB write command:**

| Scenario | Expected |
|:---------|:---------|
| Write with valid, active `module_uuid` | Succeeds |
| Write with `module_uuid` not in `registry.db` | Application code rejects |
| Write with decimated `module_uuid` | Application code rejects |
| Registry lookup before apparatus write | Verified via call order (instrument or assert state) |

---

## Patterns and Conventions

### Nullable field extraction pattern

`ModuleRow` and `ApparatusRow` both carry fields typed `str | None` — `decimated_at`, `last_synced_at`, `git_remote`, `git_remote_name`. A test that subscripts one of these out of a result dict and hands it straight to anything with a real signature (`re.match`, `Path(...)`, `.is_absolute()`, string methods, anything beyond a direct `is None` / `is not None` check) will fail static type-checking, because asserting the dict itself is not `None` does not narrow the type of its values for the checker. `result is not None` tells Pylance `result` is safe to subscript. It tells Pylance nothing about what `result["decimated_at"]` is — that's still `str | None` as far as the checker is concerned, dict or no dict.

This is a second, separate guard from the one in the previous section. You need both, in order: first confirm the row exists, then — only if you're about to *use* a nullable field as something other than a `None` comparison — confirm the field itself isn't `None`.

Bad (fails type-checking even with the row guard in place):
```python
result = get_module_by_uuid(uuid)
assert result is not None
assert re.match(r"^\d{4}-\d{2}-\d{2}$", result["decimated_at"])
```

Good — extract to a local, assert on the local, pass the local:
```python
result = get_module_by_uuid(uuid)
assert result is not None, "Module row not found. Nothing to check."
decimated_at = result["decimated_at"]
assert decimated_at is not None, "decimate_module() left decimated_at unset."
assert re.match(r"^\d{4}-\d{2}-\d{2}$", decimated_at)
```

**You do not need the second guard for a direct `None` comparison.** `assert result["decimated_at"] is None` and `assert result["decimated_at"] is not None` are both fine as-is — equality and identity checks against `None` don't require narrowing, only function calls and method access do. Don't extract a local variable you're not going to use for anything but a `None` check; that's noise, not safety.

Apply this every time a test is written, generated, or modified that touches `decimated_at`, `last_synced_at`, `git_remote`, or `git_remote_name` (or any future nullable `TypedDict` field added to either row type) for anything beyond a bare `None` comparison.

---

## Rules for Evolving This Suite

### When adding a Phase 2 command that touches the registry

Write, at minimum:
- A unit test for every new registry function it calls
- The dry-run contract test
- Registry state assertions: what exists before, what exists after, what must NOT exist on failure
- One cross-DB integrity test if the command writes to an apparatus DB

### When modifying `registry.py`

Run `test_registry.py` first. If tests fail, determine whether the failure is a regression or an intentional contract change. Fix the code or the test accordingly — do not do both simultaneously without understanding which is wrong.

If you add a new public function, add tests covering: happy path, at least one failure case, and idempotency if the function mutates state.

If you rename a public function, update every call site in the test suite. The test suite is a contract document — stale references are lies.

### When the isolation requirement is violated

If a test writes to the real `~/.archivist/`, delete the test, fix the fixture, and rewrite. A test that contaminates developer machines is not a test — it's a liability.

---

## Accepted Gaps

| Gap | Reason |
|:----|:-------|
| `commit_registry` / `push_registry` behaviour | Phase 2 stubs; no behaviour to test beyond "callable, does not raise" |
| `archivist muster` | Phase 2; not yet implemented |
| `archivist distribute` | Phase 2; not yet implemented |
| `archivist broadcast` | Phase 2; not yet implemented |
| `archivist init` AP path | Calls `init_registry` and `register_apparatus`, both fully tested in isolation; the glue is trivial |
| Interactive registration in `add` | Awkward to test; non-interactive paths fully covered |
| `test_registry_commands.py` | Pattern documented; build when first apparatus DB write command ships |