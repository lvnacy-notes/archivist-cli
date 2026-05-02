# Archivist — Centralized Database Testing Specification

> Same net. More floor space. Don't fuck it up.

**Companion to:** `TESTING_SPECIFICATION.md` and `SPEC-centralized-db.md`  
**Status:** Pending implementation — tests are added phase by phase as each implementation phase completes

---

## Philosophy (Inherited, with One Addition)

Everything in the existing testing spec applies here without exception. Read it. The additions specific to this feature area:

**The database is shared state.** Unlike frontmatter operations — which are scoped to a single file and leave no side effects — every database write in this feature area persists across test runs if you're not careful. Every test that touches a database must create its own isolated DB in `tmp_path` and must never write to `~/.archivist/`. Fixtures handle this. The tests must not work around the fixtures.

**Two databases, two connections.** The registry (`registry.db`) and the Apparatus DB (`[apparatus].db`) are separate files. Tests that exercise the full pipeline need both. Fixtures that set up a realistic environment must create both and wire them together correctly before any test logic runs.

---

## Structure

Tests are added to the following files as each implementation phase completes. Files that do not yet exist are noted. Do not create a file until the phase it covers is ready to test.

```
tests/
├── conftest.py                          # ← extend with DB fixtures (see §Fixtures)
├── unit/
│   ├── test_changelog_helpers.py        # ✅ complete — no changes needed
│   ├── test_config.py                   # ← extend: registration config helpers (Phase 1)
│   ├── test_frontmatter.py              # ✅ complete — no changes needed
│   ├── test_rename_helpers.py           # ✅ complete — no changes needed
│   ├── test_templater.py                # ✅ complete — no changes needed
│   └── test_registry.py                 # ← new file (Phase 1)
└── integration/
    ├── test_changelog_commands.py       # ✅ complete — no changes needed
    ├── test_frontmatter_commands.py     # ✅ complete — no changes needed
    ├── test_seal.py                     # ✅ complete — no changes needed
    ├── test_registration.py             # ← new file (Phase 1)
    ├── test_works_add.py                # ← new file (Phase 2)
    ├── test_changelog_harvesting.py     # ← new file (Phase 3)
    └── test_works_pipeline.py           # ← new file (Phase 4)
```

---

## Fixtures

Add the following to `conftest.py`. All database fixtures use `tmp_path` and never touch `~/.archivist/`.

```python
# conftest.py additions

import sqlite3
from pathlib import Path
import pytest

from archivist.utils import (
    init_apparatus_db,
    init_registry_db,
    register_apparatus,
    register_module,
)

@pytest.fixture
def registry_db(tmp_path) -> Path:
    """
    Create an isolated registry.db in tmp_path and return its path.
    Schema is initialized; no rows are inserted.
    """
    db_path = tmp_path / "registry.db"
    init_registry_db(db_path)
    return db_path


@pytest.fixture
def apparatus_db(tmp_path) -> Path:
    """
    Create an isolated apparatus DB (writing.db) in tmp_path and return its path.
    Schema is initialized; no rows are inserted.
    """
    db_path = tmp_path / "writing.db"
    init_apparatus_db(db_path)
    return db_path


@pytest.fixture
def registered_library(tmp_path, registry_db, apparatus_db, git_repo) -> dict:
    """
    Full realistic environment: a registered library module with both DBs wired up.
    Returns a dict with keys: registry_db, apparatus_db, module_path, module_id.

    Use this fixture for any test that exercises the full registration + works pipeline.
    """
    apparatus_id = register_apparatus(
        registry_db,
        name="writing",
        db_path=str(apparatus_db),
    )
    module_id = register_module(
        registry_db,
        apparatus_id=apparatus_id,
        name="cosmic-horror",
        module_type="library",
        path=str(git_repo.path),
        library_tag="cosmic-horror",
    )

    # Write config so the module recognizes itself
    config_dir = git_repo.path / ".archivist"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "config.yaml").write_text(
        "module-type: library\n"
        "apparatus: writing\n"
        "library-tag: cosmic-horror\n"
        "directories:\n"
        "  works: works/\n"
        "  authors: authors/\n"
        "  publications: publications/\n",
        encoding="utf-8",
    )

    (git_repo.path / "works").mkdir(exist_ok=True)
    (git_repo.path / "authors").mkdir(exist_ok=True)
    (git_repo.path / "publications").mkdir(exist_ok=True)

    return {
        "registry_db": registry_db,
        "apparatus_db": apparatus_db,
        "module_path": git_repo.path,
        "module_id": module_id,
    }
```

---

## Phase 1 — Global Registry

### Unit: `test_registry.py`

New file. Pure unit tests against the registry DB helpers. No git, no git_repo fixture, no filesystem beyond `tmp_path`.

#### `init_registry_db`

| Case | What to assert |
|:-----|:---------------|
| Creates the file at the given path | File exists after call |
| Creates all three tables | `apparatuses`, `vaults`, `modules` all present in schema |
| Safe to call twice | Second call does not raise, tables not duplicated |
| Returns an open connection | Return value is a `sqlite3.Connection` |

#### `register_apparatus`

| Case | What to assert |
|:-----|:---------------|
| Inserts a row | `SELECT COUNT(*)` returns 1 |
| Returns the new `id` | Return value matches `SELECT id FROM apparatuses WHERE name = ?` |
| `name` is stored verbatim | Round-trips correctly |
| `db_path` is stored verbatim | Round-trips correctly |
| Duplicate `name` raises | `UNIQUE` constraint violation |

#### `register_vault`

| Case | What to assert |
|:-----|:---------------|
| Inserts a row | `SELECT COUNT(*)` returns 1 |
| Returns the new `id` | Matches DB |
| FK to `apparatus_id` stored | Round-trips correctly |
| `vault_id` is nullable on `modules` | A module without a vault does not violate schema |

#### `register_module`

| Case | What to assert |
|:-----|:---------------|
| Inserts a row | `SELECT COUNT(*)` returns 1 |
| Returns the new `id` | Matches DB |
| All module types accepted | `library`, `story`, `publication`, `vault`, `general`, `custom` all insert cleanly |
| `library_tag` stored for library modules | Round-trips correctly |
| `library_tag` nullable for non-library modules | NULL stored without error |
| `vault_id` nullable | Module without vault inserts cleanly |

#### `get_apparatus_db_path`

| Case | What to assert |
|:-----|:---------------|
| Returns correct path for registered apparatus | Matches what was registered |
| Returns `None` for unknown apparatus name | Does not raise |

#### `get_module_by_path`

| Case | What to assert |
|:-----|:---------------|
| Returns module record for registered path | `module_type`, `apparatus_id`, `library_tag` all correct |
| Returns `None` for unregistered path | Does not raise |

---

### Unit: `test_config.py` Extensions

Add to the existing file. These test the new config fields introduced by registration.

| Function | Cases to add |
|:---------|:-------------|
| `write_archivist_config` / `read_archivist_config` round-trip | `apparatus` field; `vault` field; `library-tag` field; `directories` block with all three subkeys; `directories` with overridden `works` dir name; partial `directories` block |
| `get_module_type` | Still returns correct value when new registration fields are present alongside it — no regression |

---

### Integration: `test_registration.py`

New file. Tests the full `archivist init` registration flow against real filesystem and real SQLite. No git subprocess needed — `git_repo` fixture provides the module root, but we're not testing git behavior here.

#### `TestApparatusCreation`

| Test | What to assert |
|:-----|:---------------|
| New apparatus created when name is new | `apparatuses` row exists in registry |
| Apparatus DB file created at configured path | File exists on disk |
| Apparatus DB schema initialized | `works`, `authors`, `publications`, `changelogs` tables present |
| Existing apparatus reused when name matches | No duplicate `apparatuses` row inserted |

#### `TestModuleRegistration`

| Test | What to assert |
|:-----|:---------------|
| Module row inserted with correct `module_type` | Round-trips from DB |
| Module row inserted with correct `path` | Absolute path stored |
| `library_tag` written to DB for library modules | Round-trips from DB |
| `library_tag` absent from DB for non-library modules | NULL in DB |
| `.archivist/config.yaml` updated with `apparatus` field | `read_archivist_config` returns it |
| `.archivist/config.yaml` updated with `library-tag` for library modules | Present in config |
| `directories` block written to config for library modules | All three subkeys present |
| Non-library modules do not get `directories` block | Absent from config |
| Re-registering the same path does not duplicate DB rows | `SELECT COUNT(*)` is still 1 |

#### `TestFreshMachine`

| Test | What to assert |
|:-----|:---------------|
| `~/.archivist/` equivalent (`tmp_path`) created if absent | Directory exists after registration |
| `registry.db` created if absent | File exists after registration |
| First registration on a fresh DB succeeds | No errors, all rows present |

---

## Phase 2 — `archivist works add`

Depends on Phase 1 tests passing cleanly.

### Unit: `test_registry.py` Extensions

Add to the existing file. These cover the works-catalog helpers exercised by `works add`.

#### `normalize_lookup_string`

| Case | What to assert |
|:-----|:---------------|
| Lowercased | `"The Lodger"` → `"the lodger"` |
| Punctuation stripped | `"Smith, John."` → `"smith john"` |
| Whitespace collapsed | `"  Smith  John  "` → `"smith john"` |
| Empty string input | Returns empty string, does not raise |
| Already normalized | Returns unchanged |

#### `find_works_by_title_and_author`

| Case | What to assert |
|:-----|:---------------|
| Exact title + exact last name → match | Returns work record |
| Partial last name fragment → match | `"Lown"` matches `"Lowndes"` |
| Title matches, author does not → no match | Returns empty list |
| Author matches, title does not → no match | Returns empty list |
| Multiple works by same author → returns all | List length correct |
| No works in DB → returns empty list | Does not raise |
| Match via editor role, not author | `work_authors` role = `'editor'` still matches |

#### `upsert_author`

| Case | What to assert |
|:-----|:---------------|
| New author inserted | Row exists, returns `id` |
| Existing author by `sort_name` returns existing `id` | No duplicate row |
| `aliases` stored as JSON | Round-trips as list |
| `homepage` stored | Round-trips correctly |
| `first_name` nullable | NULL stored without error |

#### `upsert_publication`

| Case | What to assert |
|:-----|:---------------|
| New publication inserted | Row exists, returns `id` |
| Existing publication by `sort_title` returns existing `id` | No duplicate row |
| `pub_type` stored | Round-trips correctly |

---

### Integration: `test_works_add.py`

New file. Uses `registered_library` fixture throughout. Monkeypatches `input()` to simulate user confirmation responses.

#### `TestPreconditions`

| Test | What to assert |
|:-----|:---------------|
| Non-library module exits with clear error | `sys.exit` called; message mentions `module-type` |
| Unregistered module exits with clear error | `sys.exit` called; message mentions registration |
| Missing `works/` directory created if absent | Directory exists after command |

#### `TestNoMatchPath`

| Test | What to assert |
|:-----|:---------------|
| Works card written to `works/` dir | File exists at expected path |
| Card written with `apply-template` output | Frontmatter block present |
| `catalog-works` tag present in card | In `tags` frontmatter field |
| Library tag present in card | `library-tag` from config injected |
| Pending `work_libraries` row inserted | `work_id` is NULL, `card_path` is set |
| No `works` row inserted | `works` table still empty |
| No `authors` row inserted | `authors` table still empty |

#### `TestMatchPath`

| Test | What to assert |
|:-----|:---------------|
| User presented with match before proceeding | `input()` called with match details |
| Confirmed match: card written to `works/` dir | File exists |
| Confirmed match: core fields pre-populated in card | `sort_title`, `authors`, `year` present in frontmatter |
| Confirmed match: `work_stage` NOT pre-populated | Field absent or blank in card |
| Confirmed match: `date_consumed` NOT pre-populated | Field absent or blank in card |
| Confirmed match: `work_libraries` row inserted | `work_id` matches existing works row |
| Confirmed match: no new `works` row inserted | `works` table row count unchanged |
| Rejected match falls through to NO MATCH path | Blank card written, pending row inserted |
| Multiple contributors all pre-populated | All `work_authors` roles reflected in card |

#### `TestAuthorLookup`

| Test | What to assert |
|:-----|:---------------|
| Partial last name matches existing author | Match found without full name |
| Any author on the work satisfies the lookup | Second author's name fragment finds the work |
| New author confirmed before insertion | `input()` called for author confirmation |
| Rejected author confirmation → treated as new | New author row inserted |

#### `TestDryRun`

| Test | What to assert |
|:-----|:---------------|
| `--dry-run` writes no files | File set before == file set after |
| `--dry-run` writes no DB rows | All table counts unchanged |

---

## Phase 3 — `archivist changelog` Harvesting

Depends on Phase 2 tests passing cleanly.

### Integration: `test_changelog_harvesting.py`

New file. Uses `registered_library` fixture and the `git_repo` fixture together. Tests that changelog runs in library modules correctly identify card types and populate `ctx.data` — without writing anything to the DB.

#### `TestCardIdentification`

| Test | What to assert |
|:-----|:---------------|
| File with `catalog-works` tag identified as works card | Appears in harvested works data in `ctx.data` |
| File in `works/` dir (no tag) identified as works card | Fallback identification works |
| File with `class: author` identified as author card | Appears in harvested author data |
| File in `authors/` dir (no class field) identified as author card | Fallback identification works |
| File with `class: publication` identified as publication card | Appears in harvested publication data |
| File in `publications/` dir identified as publication card | Fallback identification works |
| Ordinary module file (no signals) skipped silently | Not present in any harvested data |
| File matching both tag and dir signals identified once | Not double-counted |

#### `TestFieldHarvesting`

| Test | What to assert |
|:-----|:---------------|
| All core fields harvested from works card frontmatter | Each field present in `ctx.data` |
| Wikilink brackets stripped from `authors` field | `[[Lovecraft, H.P.]]` → `"Lovecraft, H.P."` |
| Wikilink brackets stripped from `publications` field | `[[Weird Tales]]` → `"Weird Tales"` |
| Multi-value `authors` list harvested as list | All names present |
| Empty `themes` list harvested as empty list | Does not raise, does not produce `None` |
| `work_stage` harvested correctly | Value matches frontmatter |
| `date_consumed` harvested correctly | Value matches frontmatter |

#### `TestNoDatabaseWrites`

| Test | What to assert |
|:-----|:---------------|
| `archivist changelog` in library module writes nothing to apparatus DB | All table counts unchanged after run |
| `archivist changelog --dry-run` writes nothing to apparatus DB | All table counts unchanged after run |
| `ctx.data` populated regardless of dry-run flag | Harvested data present in both modes |

#### `TestNonLibraryModule`

| Test | What to assert |
|:-----|:---------------|
| `archivist changelog` in non-library module does not attempt harvesting | No card identification logic runs; no `ctx.data` keys for works |
| `archivist changelog` in non-library module still writes changelog correctly | Existing behavior unaffected — no regression |

---

## Phase 4 — Post-Commit Hook Pipeline

Depends on Phase 3 tests passing cleanly. These are the heaviest integration tests in the suite. They exercise the full pipeline end-to-end.

### Integration: `test_works_pipeline.py`

New file. Uses `registered_library` fixture. Every test that touches the DB asserts the final DB state explicitly — row counts, field values, FK relationships. Do not assert on intermediate state; assert on what the DB looks like when the smoke clears.

#### `TestNewWorkPipeline`

Full pipeline: new works card committed, no prior DB record.

| Test | What to assert |
|:-----|:---------------|
| `works` row inserted with correct core fields | `sort_title`, `class`, `year` all match frontmatter |
| `authors` row(s) inserted | One row per contributor |
| `work_authors` rows inserted with correct roles | `author`, `editor`, `translator` roles correct |
| `publication` row inserted if `publications` field present | Title matches |
| `work_libraries` row updated: `work_id` no longer NULL | Pending row resolved |
| `work_libraries` row updated: `work_stage` matches card | Matches frontmatter value |
| `work_libraries` row updated: `card_path` is absolute path to file | File exists at that path |
| `changelogs` row inserted for this commit | `commit_sha` matches |

#### `TestExistingWorkNewLibrary`

Works card committed in a second library where the work already exists in DB.

| Test | What to assert |
|:-----|:---------------|
| No duplicate `works` row inserted | `works` table count unchanged |
| No duplicate `authors` rows inserted | `authors` table count unchanged |
| New `work_libraries` row inserted for second library | Second module's row present |
| Existing `work_libraries` row for first library unchanged | First module's row still correct |

#### `TestUpdatedWorkCard`

Existing works card modified and recommitted.

| Test | What to assert |
|:-----|:---------------|
| `works` row updated with changed core field | New value in DB |
| `work_libraries` row updated with changed `work_stage` | New stage in DB |
| No duplicate rows inserted | Table counts unchanged |
| Unchanged fields not corrupted | All other fields still correct |

#### `TestReferenceResolution`

| Test | What to assert |
|:-----|:---------------|
| Author already in DB: no new row inserted | `authors` count unchanged |
| Author not in DB, card on disk: row upserted from disk | Author data from card frontmatter |
| Publication already in DB: no new row inserted | `publications` count unchanged |
| Publication not in DB, card on disk: row upserted from disk | Publication data from card frontmatter |
| Author card committed independently (no works card): `authors` row upserted | Row present after hook |
| Publication card committed independently: `publications` row upserted | Row present after hook |

#### `TestWorkRelations`

| Test | What to assert |
|:-----|:---------------|
| `cites` wikilinks produce `work_relations` rows with `relation_type = 'cites'` | Row count matches `cites` list length |
| `related` wikilinks produce `work_relations` rows with `relation_type = 'related'` | Row count matches `related` list length |
| Both sides of relation must exist in `works` before row is inserted | No orphaned FK references |

#### `TestIdempotency`

The most important tests in this file. Run the hook twice. Assert identical DB state.

| Test | What to assert |
|:-----|:---------------|
| Running post-commit hook twice on same commit: `works` count unchanged | No duplicates |
| Running post-commit hook twice on same commit: `authors` count unchanged | No duplicates |
| Running post-commit hook twice on same commit: `work_libraries` count unchanged | No duplicates |
| Running post-commit hook twice on same commit: all field values identical | No corruption |
| Deleting a `works` row and rerunning hook: row reconstructed from disk | Recovery from manual DB edits works |

#### `TestCustomModuleExclusion`

| Test | What to assert |
|:-----|:---------------|
| Post-commit hook in `custom` module writes `changelogs` row | Changelog record present |
| Post-commit hook in `custom` module does not touch `works` table | Works table count unchanged |
| Post-commit hook in `custom` module does not touch `authors` table | Authors table count unchanged |

---

## Phase 5 — Verification and Hardening

Add these after the full pipeline is exercised against real data. These tests are not speculative — they pin behaviors that production use has confirmed matter.

### Additions to `test_works_pipeline.py`

| Test | What to assert |
|:-----|:---------------|
| `works add` in non-library module exits cleanly | Error message mentions module type; no DB writes |
| `works add` in unregistered module exits cleanly | Error message mentions registration; no DB writes |
| Title + author normalization is consistent between `works add` lookup and pipeline upsert | Same work found by both code paths |
| Works card with no `authors` field does not crash hook | Inserts works row; no work_authors rows |
| Works card with empty `themes: []` does not corrupt DB | NULL or empty JSON stored cleanly |
| `work_libraries` pending rows with NULL `work_id` and no card on disk do not crash hook | Skipped with warning, no exception |

---

## Contracts That Must Never Break

These are the database equivalents of the sentinel boundary and the dry-run contract. Add a test the moment any of these is threatened by a change.

**The DB is never the primary record.** The `.md` card on disk is. The DB is derived from it. If the DB and the card disagree, the card wins. Any command that reads the card must be able to reconstruct the DB state from it.

**Pending `work_libraries` rows are never silently orphaned.** A row with `work_id = NULL` that has no corresponding card on disk is a corruption state. The hook must detect it, warn, and skip — not silently insert garbage.

**Library-local fields never overwrite shared fields.** `work_stage`, `date_consumed`, `date_cataloged`, `date_reviewed` live in `work_libraries`. They must never find their way into the `works` table, regardless of what the frontmatter says.

**The post-commit hook is idempotent.** Always. No exceptions. If a change makes it non-idempotent, that is a bug regardless of how it got there.

**`--dry-run` writes nothing to any database.** Not a partial write. Not a speculative insert that gets rolled back. Nothing. The DB state before and after a dry-run must be byte-for-byte identical.

---

## Known Gaps (Accepted for Now)

| Gap | Reason |
|:----|:-------|
| `archivist works query` CLI | Command not yet specced; test when the interface is defined |
| `archivist works update` | Not yet specced; test when implemented |
| `archivist init` registration interactive prompts | Same accepted gap as the existing spec — interactive prompts; underlying helpers fully tested individually |
| Multi-apparatus queries | Not a current use case; test when the query interface exists |
| Pending `work_libraries` cleanup command | Deferred in spec; test when implemented |
| Domain-specific layer fields | Explicitly out of scope for this phase |

---

## Priority Order for Expansion

Inherits from the existing spec. Additions specific to this feature area:

1. **Bug-driven tests** — before the fix ships, always
2. **Idempotency regressions** — any change to the hook pipeline gets an idempotency test immediately
3. **New library templates** — if a new library's works card introduces a new core field that gets stored, add a harvesting and upsert test for it
4. **Query interface tests** — when `archivist works query` is specced and built
5. **Everything else** — when production use surfaces a gap

The pipeline tests are integration tests by necessity. They touch two SQLite files, the filesystem, and git. They are slower than unit tests. Run them with the full suite, not in the fast subset.

```bash
pytest -m "not integration" -v   # fast; skips all pipeline tests
pytest -v                         # everything
```