# Archivist Testing Spec

> The net exists. Don't fucking tear holes in it.

---

## Status

The initial suite is complete and the Apparatus Platform Phase 1 suite has shipped alongside it. This document covers both. It is the authoritative reference for what the suite covers, what it deliberately skips, and how to evolve it without breaking what's already working.

The registry testing specification lives in a separate document: `REGISTRY_TESTING_SPECIFICATION.md`. Read it before touching `registry.py`, `add.py`, or `deinit.py`. The isolation requirement it defines is absolute.

---

## Philosophy (Unchanged)

**Minimal overhead. Maximum confidence where it actually matters.**

Coverage numbers are vanity. What matters is that the following behaviors are tested hard enough that a regression cannot hide:

- YAML frontmatter mutation — the core of every command
- Dry-run safety — the contract we make with every user who doesn't want to blow up their vault
- Rename detection — where subtle, silent bugs live
- The sentinel boundary — the line between generated content and user content; cross it and you're destroying someone's work
- The UUID / seal lifecycle — the chain that makes publication changelogs idempotent
- Registry integrity — the Apparatus Platform's state is the only source of truth for module identity; a corrupt registry breaks `add`, `deinit`, `muster`, and everything downstream simultaneously
- Operation order in destructive commands — Apparatus first, git second; get it wrong and a git failure leaves the user with no config and no path to recovery

Everything else is supplementary. Useful, but not load-bearing.

### Dependencies

```
pytest          # still the only new dependency
```

No pytest-mock. No factory_boy. No hypothesis. No bullshit. `unittest.mock` is stdlib and covers whatever mocking we could possibly need. For git-dependent tests, we spin up a real repo in `tmp_path` — no mocked subprocess calls, no fake diff output. If git isn't on PATH, the integration tests fail loudly and that is correct behavior.

For commands that call git or modify the filesystem as side effects (`add`, `deinit`), subprocess and filesystem operations are isolated via injection seams — callable parameters with production defaults that tests override directly. No global patching of `subprocess.run` or `shutil.rmtree`. See the injection seam pattern in Patterns and Conventions.

---

## Structure

```
tests/
├── conftest.py                      # shared fixtures and helpers
├── unit/
│   ├── test_changelog_helpers.py    # ✅ complete
│   ├── test_config.py               # ✅ complete
│   ├── test_frontmatter.py          # ✅ complete
│   ├── test_registry.py             # ✅ complete — Apparatus Platform Phase 1
│   ├── test_rename_helpers.py       # ✅ complete
│   └── test_templater.py            # ✅ complete
└── integration/
    ├── test_add.py                  # ✅ complete — Apparatus Platform Phase 1
    ├── test_changelog_commands.py   # ✅ complete
    ├── test_deinit.py               # ✅ complete — Apparatus Platform Phase 1
    ├── test_frontmatter_commands.py # ✅ complete
    └── test_seal.py                 # ✅ complete
```

Run unit tests only (fast, no git required):

```bash
pytest -m "not integration" -v
```

Run everything:

```bash
pytest -v
```

---

## What's Covered

### Unit: `test_changelog_helpers.py`

| Function | Coverage |
|:---------|:---------|
| `extract_descriptions` | Single-line with content, placeholder skipped, sub-bullet returns list, mixed filled/empty, all bullets preserved, bare colon with no sub-bullets skipped, non-entry lines ignored, deep nested path as key, multiple entries, sub-bullet stops at next top-level entry, empty string, no entries, colon in description value not truncated |
| `extract_user_content` | Returns content after sentinel, None when absent, empty string when nothing follows sentinel, splits only on first occurrence, multiline content preserved, slightly-malformed sentinel → None, sentinel constant pinned |
| `format_file_list` | Empty → fallback, no description → placeholder, string description inline, list description → sub-bullets, rename annotation present, no annotation when not renamed, suspicious rename → `⚠️`, clean rename no warning, multiple files, mixed descriptions, no active_renames default, trailing newline always present, list description followed by blank line, same-dir rename shows filename not full path, cross-dir rename shows full path |
| `generate_changelog_uuid` | Returns string, valid UUID4, correct format (5 groups, version nibble), two calls differ, lowercase |

---

### Unit: `test_config.py`

| Function | Coverage |
|:---------|:---------|
| `get_archivist_config_path` | Returns `Path`; returns directory-form path when `.archivist/config.yaml` exists; returns legacy flat path when only flat file exists; returns canonical directory-form path when neither exists; directory form takes priority over flat file |
| `read_archivist_config` | Valid YAML (flat form) returns dict; valid YAML (directory form) returns dict; directory form takes priority over flat file; missing file returns `None`; empty `.archivist/` directory (no `config.yaml`) returns `None`; malformed YAML returns `{}` (not None); malformed prints to stderr; malformed does not raise; non-dict YAML returns `{}`; list YAML returns `{}`; None YAML returns `{}`; multikey config; custom keys with hyphens and slashes |
| `write_archivist_config` | Creates `.archivist/` directory; creates `config.yaml` inside it; expected keys present; starts with comment header; ends with newline; empty config writes only comment; overwrites existing; does NOT write a flat `.archivist` file |
| `write` / `read` round-trip | String values; all known module types; works-dir; changelog-output-dir; multi-key config |
| `get_module_type` | Returns correct value (flat form); returns correct value (directory form); None when file absent; None when key missing; None for malformed config; all known module types |
| `get_today` | Matches ISO 8601 YYYY-MM-DD; four-digit year; returns string; custom format respected; format without separators; two calls same second return same value |
| `find_changelog_plugin` | Returns `None` when no `.archivist/` directory; returns `None` when directory exists but no plugin; returns `Path` when `changelog.py` present; returns `Path` object not string; ignores `sample-changelog.py` explicitly; ignores all other `.py` files; coexists with `config.yaml` |
| `load_changelog_plugin` | Loads valid plugin; loaded module has callable `run`; exits on syntax error; exits when `run` is absent; exits when `run` is not callable; syntax error prints to stderr; missing `run` prints to stderr mentioning "run"; end-to-end happy path (load → call → verify execution) |
| Constants | `APPARATUS_MODULE_TYPES` contains all five; is a list; `MODULE_CHANGELOG_COMMAND` covers all module types; values are valid subcommands; no extra entries (clean bijection) |

**Apparatus Platform Phase 1 additions — `TestConfigSchema`:**

| Scenario | Coverage |
|:---------|:---------|
| `ConfigSchema` is a valid TypedDict | Class importable; `__annotations__` present |
| Partial instantiation | `total=False` — empty dict, single-key, and multi-key dicts all valid |
| `read_archivist_config` returns `ConfigSchema`-compatible value | Hyphenated keys accessible; typed access works |
| `write_archivist_config` accepts a `ConfigSchema`-typed dict | Write succeeds; round-trip readable |
| `apparatus: true` (legacy boolean) not coerced at read time | `read_archivist_config` surfaces raw PyYAML `True`; migration is `migrate`'s responsibility |
| `apparatus: writing` (string name) survives read | String form comes back intact and distinguishable from boolean |
| `uuid` is first non-comment key written | `write_archivist_config` places `uuid` before all other keys when present |

---

### Unit: `test_frontmatter.py`

The most critical unit module. Every command calls these functions.
A bug here propagates everywhere.

| Function | Coverage |
|:---------|:---------|
| `has_frontmatter` | Valid block, no block, mid-doc dashes, trailing whitespace on delimiters, empty block, opening-only delimiter |
| `extract_frontmatter` | Happy path, missing block, malformed YAML (does not raise), non-dict parse results, numeric values, colon-in-value |
| `remove_property_from_frontmatter` | Scalar removal, block sequence removal (multi-line continuation), property absent, removing last property leaves empty string, no partial-name collision, inline list removal |
| `match_property_line` | Exact match, extra spaces before colon, partial prefix rejected, regex special chars in prop name, hyphenated names |
| `parse_frontmatter_entries` | Scalar fields, block sequence grouping, empty input, order preservation, inline list as single entry |
| `extract_tags_from_entries` | Inline list, quoted inline values, scalar, block sequence, no `tags` key, lowercase normalization, empty inline list |
| `render_field` | Scalar string, scalar int, list as block sequence, empty list |
| `matches_class_filter` | Match, case-insensitive match, non-match, missing key, None value |
| `safe_read_markdown` | Existing file, missing file, permission error (Unix only) |
| `safe_write_markdown` | Successful write, write failure |
| `find_markdown_files` | Recursive scan, sorted output, empty dir, path_prefix filter |
| `get_file_frontmatter` | Valid file, no frontmatter, non-markdown, string path |
| `get_file_class` | Returns lowercased value, missing field, missing file |

**Edge cases specifically pinned:**
- Colon in property value doesn't false-match on wrong key
- `tags: []` returns empty list, doesn't explode
- Single-item list renders as block sequence, not scalar
- Property removal doesn't corrupt adjacent properties
- Tab-indented continuation lines handled correctly

---

### Unit: `test_registry.py`

The registry is the foundation of the entire Apparatus Platform. A bug here breaks `add`, `deinit`, `muster`, `distribute`, and `broadcast` simultaneously. Test it hard.

**Critical fixture requirement: `_isolated_registry` must be `autouse=True` on this entire test module.** A test that writes to the real `~/.archivist/` on a developer's machine is a test that deserves to be deleted and its author shamed. No exceptions.

```python
@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    fake_dir = tmp_path / ".archivist"
    monkeypatch.setattr(registry_module, "get_registry_dir", lambda: fake_dir)
    return fake_dir
```

Note: patch against `registry_module` (the imported module object), not the string path — this ensures the patch lands on the correct reference regardless of how callers imported the function. Import `archivist.utils.registry as registry_module` at the top of the test file.

All other path functions derive from `get_registry_dir()` — patching that one function is sufficient to fully isolate every test.

| Function / Behaviour | Coverage |
|:---------------------|:---------|
| `get_registry_dir` | Returns `Path`; returns `~/.archivist` by default (monkeypatched in fixture); all other path helpers derive from it |
| `get_registry_path` | Returns `get_registry_dir() / "registry.db"` |
| `get_apparatus_db_path` | Returns `get_registry_dir() / "{name}.db"` |
| `validate_slug` | Valid slugs accepted; uppercase rejected; spaces rejected; leading hyphen rejected; special characters rejected; empty string rejected; error message includes label parameter |
| `init_registry` | Creates `~/.archivist/` directory; runs `git init`; creates `registry.db`; schema present (all three tables); idempotent — calling twice does not raise or corrupt; `PRAGMA foreign_keys = ON` active on resulting connection |
| `init_apparatus_db` | Creates `[name].db`; schema present (all four tables); validates slug before touching disk; idempotent |
| `register_apparatus` | Returns UUID string; UUID is valid UUID4; upsert — same name twice returns same UUID, not a second row; `git_remote=None` accepted; `git_remote` stored when provided; calls `init_apparatus_db` — apparatus DB exists after registration; invalid slug raises `ValueError` before any DB write |
| `get_apparatus_by_name` | Returns dict for known name; `None` for unknown; dict contains `uuid`, `name`, `db_path`, `created_at` |
| `register_module` | Returns UUID; invalid `module_type` raises `ValueError` before any DB write; unknown apparatus raises `ValueError`; upsert by path — same path twice updates row, does not duplicate; all five module types accepted; `decimated_at` is `NULL` on new row; `last_synced_at` set on new row |
| `get_module_by_uuid` | Returns dict for known UUID; `None` for unknown |
| `get_module_by_path` | Returns dict for known path; `None` for unknown; resolves relative paths to absolute before querying; symlinks resolved |
| `is_module_registered` | `True` for registered UUID; `False` for unknown UUID; `True` for decimated UUID (decimated ≠ deleted) |
| `update_module_sync` | Updates `last_synced_at`; unknown UUID is silent no-op — does not raise; timestamp advances on second call |
| `decimate_module` | Sets `decimated_at` to non-null value; unknown UUID raises `ValueError`; module still exists in DB after decimation (soft delete); `is_module_registered` still returns `True` |
| `reactivate_module` | Clears `decimated_at` to `NULL`; unknown UUID raises `ValueError`; module queryable as active after reactivation |
| `add_module_to_bay` | Inserts bay row; idempotent — same pair twice does not raise (INSERT OR IGNORE); `get_module_bays` reflects new row |
| `remove_module_from_bay` | Deletes bay row; no-op for missing pair — does not raise; `get_module_bays` empty after removal |
| `remove_all_bays_for_contained` | Removes all rows where `contained_id` matches; no-op when no rows match; does not remove rows where module is the container |
| `get_module_bays` | Returns all containers for a module; empty list when no bays; returns list of dicts with module fields; does not return decimated containers |
| `get_apparatus_modules` | Returns all active modules; excludes decimated by default; includes decimated with `include_decimated=True`; sorted by name; empty list for unknown apparatus |
| `get_bay_modules` | Returns all active contained modules; excludes decimated by default; includes decimated with `include_decimated=True`; empty list for unknown container |
| `get_vault_modules` | Delegates correctly to `get_bay_modules`; raises `ValueError` for non-vault container type; raises `ValueError` for unknown UUID |
| `list_apparatus_names` | Returns all apparatus names sorted alphabetically; empty list when registry has no apparatuses; empty list when registry does not exist yet — does not raise |
| `commit_registry` | Callable; does nothing; does not raise |
| `push_registry` | Callable; does nothing; does not raise |

**Cross-database integrity — the tests that matter most for the apparatus DB:**

The apparatus DB columns `changelogs.module_uuid`, `works.module_uuid`, and `authors.apparatus_uuid` are logical references to `registry.db`. SQLite cannot enforce them. Application code is responsible. These tests pin that responsibility explicitly so a future refactor can't quietly remove the guard without a test failing.

| Scenario | Expected behaviour |
|:---------|:-------------------|
| Write to apparatus `changelogs` with valid, active `module_uuid` | Succeeds |
| Attempt to write with `module_uuid` not in `registry.db` | Application code raises or rejects before the write — SQLite will not catch this |
| Attempt to write with decimated `module_uuid` | Application code rejects — `decimated_at IS NOT NULL` check runs before apparatus DB write |
| `decimate_module()` then attempt apparatus write with that UUID | Same as above — decimation check is the gate |
| Registry lookup before apparatus write — order enforced | `get_module_by_uuid()` (or equivalent) is called before any `INSERT` into apparatus DB; assert call order in integration tests |

**These scenarios are integration-level concerns.** The unit tests above cover the registry functions in isolation. The cross-DB integrity tests belong in `test_registry_commands.py` (Phase 1 integration) where a real registry and real apparatus DB are live together under the `isolated_registry` fixture. Unit tests pin the building blocks; integration tests pin the seams.

---

### Unit: `test_rename_helpers.py`

Rename bugs are silent and insidious. These functions are pure — test them hard.

| Function | Coverage |
|:---------|:---------|
| `clean_filename` | Plain filename, trailing space+number (Obsidian conflict), trailing punctuation, multiple garbage chars, genuinely numeric stem, path components stripped, extension preserved, deep path |
| `detect_dir_renames` | Empty input, same-dir rename ignored, single cross-dir rename, multiple files same dir (deduplication), multiple distinct dir renames, nested dir rename, mixed same/cross-dir |
| `infer_undetected_renames` | Empty changes, no unpaired files, simple move, different filenames not paired, ambiguous added locations left alone, already-R-paired deleted side excluded, already-R-added side excluded, multiple independent moves, true deletion untouched when no match |
| `reassign_deletions` | Empty inputs, no dir renames, dir-renamed file reassigned, mixed true deletions and dir-renamed, nested dir path reconstructed correctly, multiple files under same renamed dir, exact prefix match (not startswith) |
| `process_renames_from_changes` | Empty R, single rename inverted, multiple inverted, other change types ignored, cross-dir rename |
| `rename_suspicion` | Clean same-dir rename → empty, exact same path → empty, cross-dir flagged, name mismatch flagged, both flags together, substring suppresses mismatch, reverse substring suppresses mismatch, trailing garbage stripped before comparison, warning contains `⚠️` and "double-check" |

**Deep path chain tests:** All functions exercised against 50-level-deep nested paths to surface any O(n²) or recursion-limit surprises.

**Pipeline integration tests:** `detect_dir_renames → reassign_deletions → process_renames_from_changes` composed end-to-end, including inferred rename composition with confirmed renames, and true deletions surviving the full pipeline.

---

### Unit: `test_templater.py`

The Templater support layer. The mask/restore cycle and the expression evaluator are the two places most likely to silently corrupt frontmatter in production — an unmasked expression reaching YAML parsing, or a sentinel token surviving into the written file. Both failure modes are tested directly.

| Function / Class | Coverage |
|:-----------------|:---------|
| `TemplaterMode.from_config` | `resolve`, `preserve`, `false`, `None` → PRESERVE, unrecognized → PRESERVE, whitespace stripped, case-insensitive |
| `TemplaterMode` (enum values) | Stored `.value` strings match the exact strings written to `.archivist` |
| `get_templater_mode` | Reads from config dict, `None` config → PRESERVE, missing key → PRESERVE |
| `has_templater_expression` | Basic expression, plain string, partial opening tag, expression embedded in value, whitespace-control variant (`<%-`), empty string, multiple expressions |
| `extract_expressions` | Single expression content extracted, multiple expressions, no expressions → empty list, inner whitespace stripped |
| `mask_templater_expressions` | Expression replaced with sentinel, mask map contains full original token, multiple expressions get distinct numbered sentinels, sentinels numbered left-to-right, no expressions → unchanged string and empty map, YAML-hostile characters (`{`, `}`, `:`) safely masked, whitespace-control variants masked |
| `restore_templater_expressions` | Restores original expression verbatim, resolved value substituted when provided, unresolved sentinels fall back to original (never reach output as raw tokens), empty mask map → string unchanged, mask → restore roundtrip is identity, partial resolution leaves unresolved expressions intact |
| `moment_to_strftime` | ISO date (`YYYY-MM-DD`), full datetime with time, long/short month name, long/short weekday, two-digit year, 12-hour clock with AM/PM, complex human-readable format, unknown tokens pass through unchanged |
| `resolve_value` | `tp.date.now()` default format, explicit format, positive offset, negative offset; `tp.date.tomorrow`, `tp.date.yesterday`; `tp.file.title` property access; `tp.file.folder()` name-only; `tp.frontmatter["key"]` subscript; missing frontmatter key → empty string; unresolvable expression left verbatim; `warn_fn` called exactly once per unresolvable expression; `warn_fn=None` does not raise; expression embedded in plain text; multiple expressions in one value; `fully_resolved=False` when any expression fails; static string literal `<% "..." %>` resolves; plain value with no expressions returns unchanged |
| `TemplaterContext` / `_TpFile` | `title` returns stem, `folder()` returns parent name, `folder(absolute=True)` returns full path, `path()` returns absolute path, `last_modified_date` returns correctly formatted string, `creation_date` returns correctly formatted string, missing file stat returns `""` gracefully |
| `_TpFrontmatter` | Subscript access returns string value, missing key returns `""`, `None` frontmatter arg does not raise |
| `_TpDate` | `now()` default format parses correctly, offset arithmetic matches `timedelta`, `tomorrow` / `yesterday` / `today` aliases consistent with `now()`; reference date with zero offset; reference date with positive offset; malformed reference falls back to today; `weekday()` Monday of known week; `weekday()` Sunday of known week |

**Edge cases specifically pinned:**
- Colon inside an expression (e.g. `tp.date.now('HH:mm:ss')`) is fully masked — does not survive into the YAML-parsed string
- Hash character inside an expression masked before YAML can interpret it as a comment
- Curly braces inside an expression masked before YAML can interpret them as a flow mapping
- `resolve_value` on a plain string (no expressions) returns it byte-for-byte unchanged
- Unimplemented namespace (`tp.user`, `tp.system`) degrades to verbatim + warning, never raises
- `mask_map` values are the full `<% expr %>` token including delimiters, not just the inner content
- `TemplaterContext` accepts `dict[str, str | list[str]]` (the actual return type of `extract_frontmatter`) without a `TypeError` — pins the `Mapping` covariance fix
- Longer moment.js tokens take precedence over shorter prefix matches (`YYYY` → `%Y`, not `%y%y`; `MMMM` → `%B`, not downstream token collision)

---

### Integration: `test_add.py`

Tests for `archivist add`. Git and filesystem operations are controlled via injection seams — `runner` and `remover` callables passed directly to `run()`. No global subprocess patching.

**Pure helpers (no registry, no subprocess):**
- `_infer_target_name` — SSH/HTTPS URL parsing; `.git` suffix stripped; trailing slash ignored; empty URL fallback
- `_build_git_command` — clone vs submodule add based on context flag; path appended when provided; passthrough args appended; submodule keyword present in submodule mode

**Dry-run contract:**
- No files written
- Registry untouched — no module row created
- Git runner not invoked
- Plan printed to output

**Clone path (non-git cwd):**
- `git clone` called when cwd has no `.git`
- Module registered after successful clone
- No bay row when cwd is not a registered module

**Submodule path (git cwd):**
- `git submodule add` called when cwd has `.git`
- Bay row created when cwd is a registered, active module
- No bay row when cwd module is decimated
- Vault container updates `vaults` list in target config

**Git failure:**
- Registry untouched when git fails — git is the gate, registry writes happen after
- Exit code propagated from git's return code

**UUID resolution matrix:**
- Case (a): UUID in config + decimated → reactivate; `decimated_at` cleared
- Case (b): UUID in config + active → refresh; no duplicate row; bay row added if absent

**Remote name resolution:**
- `git_remote_name` populated from `git remote -v` output when remote found
- `git_remote_name` stored as `NULL` when no remote configured

---

### Integration: `test_changelog_commands.py`

**The two most important tests in this module exist for every subcommand class.**
If either fails, stop everything and fix it before touching anything else:

1. **`test_dry_run_writes_absolutely_nothing`** — dry-run contract. Compares
   `{p for p in git_repo.path.rglob("*") if p.is_file()}` before and after.
   Note: directories (including empty `ARCHIVE/`) may be created before the
   dry-run gate; the test correctly compares *files only*.

2. **`test_user_content_below_sentinel_survives_rerun`** — sentinel boundary.
   First run generates the file. Test injects user content below the sentinel.
   Second run regenerates. User content must survive untouched.

**`changelog general`:** output dir is `ARCHIVE/` (not `ARCHIVE/CHANGELOG/`).
- Dry-run contract
- Creates `ARCHIVE/CHANGELOG-{today}.md`
- Frontmatter: `class: archive`, `log-scope: general`, `UUID`, `commit-sha`, counters
- Body contains `<!-- archivist:auto-end -->` sentinel
- Rerun updates existing file, does not spawn a second one
- User content below sentinel survives rerun
- Staged rename appears in modified section with annotation
- Empty diff produces changelog with zero counters (does not crash or exit)
- Output dir created if it doesn't exist
- UUID preserved across reruns

**`changelog story`:** output dir is `ARCHIVE/CHANGELOG/`.
- Dry-run contract
- Creates file in `ARCHIVE/CHANGELOG/`, NOT flat `ARCHIVE/`
- `log-scope: story`
- Body contains Story Development, Technical Updates, Publication Preparation, Detailed Change Log sections
- User content below sentinel survives rerun

**`changelog library`:** output dir is `ARCHIVE/`.
- Dry-run contract
- Creates file in `ARCHIVE/`
- Frontmatter contains works/authors/publications/definitions counters
- `work-stage` file routes to Catalog Changes, not Other File Changes
- `class: author` file routes to Author Cards, not Other File Changes
- Plain file without special frontmatter routes to Other File Changes
- Catalog Snapshot section present in body
- User content below sentinel survives rerun

**`changelog vault`:** output dir is `ARCHIVE/`.
- Dry-run contract
- `log-scope: vault`
- Submodules section always present (empty-state placeholder acceptable in no-submodule test repo)
- Template/scaffold files route to Templates & Scaffolding section
- User content below sentinel survives rerun

**`changelog publication`:** output dir is `ARCHIVE/CHANGELOG/`.
- Dry-run contract (files AND DB)
- Dry-run does not claim SHAs in DB
- Frontmatter contains `editions-sha` field
- No archive DB → proceeds without crashing, prints note to stderr, `editions-sha: []`
- Unclaimed SHAs from DB appear in frontmatter and body
- After non-dry run, SHA claimed with UUID (not commit SHA — that's seal's job)
- Rerun: same SHAs still appear (UUID-based re-claim query)
- User content below sentinel survives rerun

**`_wait_for_save_confirmation` (shared prompt, tested via `general`):**
- Non-`'y'` response calls `sys.exit(0)` — does not proceed
- `'yes'` (full word) accepted as valid confirmation
- Prompt does not fire on first run (no existing changelog)

---

### Integration: `test_deinit.py`

Tests for `archivist deinit`. Uses the same injection seam approach as `test_add.py`. `runner` and `remover` are passed directly to `run()`; `_confirm` is patched via monkeypatch since it reads stdin.

**Dry-run contract:**
- No files written
- Registry untouched — `decimated_at` not set
- Runner not invoked (dry-run exits before `_git_cleanup`)
- Confirmation prompt fires even on dry-run
- Plan printed to output

**Confirmation gate:**
- User declines → `sys.exit(0)`; registry and filesystem untouched

**Guard conditions:**
- Path not in registry → warning + `sys.exit(0)`
- Registry does not exist → error + `sys.exit(1)`
- cwd is inside target → error + `sys.exit(1)`; registry untouched

**Happy path — single superproject:**
- Bay row removed
- Module decimated when no bay rows remain
- Operation order pinned: Apparatus cleanup (decimation logged) fires before git step (remover logged); call order asserted explicitly

**Multiple bays:**
- Scoped removal from vault-a removes only vault-a's bay row
- vault-b's relationship survives
- Module not decimated while any bay row remains

**Standalone removal (cwd not a registered superproject):**
- All bay rows removed
- Module decimated

**`--retain` flag:**
- Runner not invoked
- Registry cleanup still runs; module decimated
- Module directory still on disk after deinit

**Idempotency:**
- Already-decimated module handled without raising — recovery path works

**PermissionError:**
- `remover` raises `PermissionError` → `sys.exit(1)`; error message contains path or "permission"
- No raw traceback

---

### Integration: `test_frontmatter_commands.py`

All tests call `run()` directly against real files in a `git_repo` fixture. `monkeypatch.chdir(git_repo.path)` is load-bearing — it makes `get_repo_root()` resolve correctly without mocking subprocess. Do not remove it.

**`frontmatter add`:**
- Adds property to file with existing frontmatter
- Creates frontmatter block when none exists
- Skips file with property already present (no `--overwrite`)
- Overwrites with `--overwrite`, leaves other fields untouched
- Adds bare key with no value
- Dry-run: no files modified
- Processes multiple files in one call
- Skips non-markdown files without exploding

**`frontmatter remove`:**
- Removes property, leaves rest of frontmatter intact
- Removing last property drops the entire frontmatter block
- Skips file with no frontmatter
- Skips file without the target property
- Removes block sequence property and all continuation lines
- Dry-run: no files modified

**`frontmatter rename`:**
- Old key gone, new key present, value identical
- Preserves value exactly (no round-trip coercion)
- Preserves block sequence value with continuation lines
- Skips files without old key
- Does not rename partial key matches (`class` ≠ `classification`)
- Dry-run: no files modified
- Exits when old and new names are identical

**`frontmatter apply-template`:**
- Applies to note matching class filter
- Does not touch note failing class filter
- Applies to note matching tag filter; non-tagged note untouched
- Applies to notes under path filter; out-of-scope untouched
- AND logic: note matching ALL filters updated; note matching only one untouched
- Removes properties absent from template
- Reorders to match template order
- Preserves existing values for properties kept from template
- Dry-run: no files modified
- Exits when no filters provided
- Exits when template file doesn't exist
- Exits when template has no frontmatter

---

### Integration: `test_seal.py`

**Core mechanics:**
- Unsealed changelog renamed to `CHANGELOG-{date}-{short_sha}.md`
- Short SHA backfilled in `commit-sha:` frontmatter field
- Full SHA backfilled in `| Commit SHA |` body table cell
- Sealed filename uses SHORT sha suffix, not 40-char full SHA
- Sealed file NOT picked up by `find_active_changelog()` on subsequent runs
- User content below sentinel survives sealing
- Commit with no unsealed changelogs exits cleanly with progress note

**Already-sealed skip:**
- Partial failure state (unsealed filename + SHA already in frontmatter) detected and skipped
- File not renamed, content not modified
- `skipped_count` reported in output ("already sealed")

**Database interaction:**
- `edition_shas.included_in` transitions from UUID → short_sha after sealing
- Multiple edition SHAs all transition atomically
- `changelogs` table row populated with `commit_sha` and `sealed_at` after sealing
- No archive DB → no crash, no DB created
- Changelog without UUID → sealing completes (backfill + rename), DB not touched

**Multiple changelogs in one commit:**
- All unsealed changelogs in the commit are sealed (loop processes all, not just first)
- `sealed_count` reported correctly in output

**Edge cases:**
- `sys.exit` when no commit SHA provided
- `sys.exit` when `commit_sha` attribute missing from namespace entirely
- Missing file on disk (deleted between commit and seal) → warning to stderr, no crash
- Running seal twice against same commit is idempotent

---

## What's Deliberately Not Tested

**CLI argument parsing.** argparse has its own tests. We verify that `run()` respects `args.dry_run = True`. We do not verify that `--dry-run` populates `args.dry_run`.

**Git internals.** We don't test that `git diff-index` returns the right output. We test that `get_git_changes()` correctly parses what git gives back.

**YAML parsing correctness.** PyYAML's own test suite covers that. We test our wrappers' behavior when YAML is malformed (return `{}`, don't crash) and our rendering output (`render_field`). Not PyYAML itself.

**`manifest` commands.** The manifest pipeline is integration-tested implicitly through the publication changelog's DB interaction (the DB schema is shared). A dedicated `test_manifest.py` is low priority until a regression surfaces.

**`reclassify` command.** Untested. It's simple enough that the risk is low and it doesn't touch any shared state. Add tests when a bug bites.

**`hooks install/sync`.** Writing to `~/.git-templates/` in a test suite is obnoxious. These commands are simple enough to validate manually. Don't bother automating them unless the logic gets substantially more complex.

**`init` command.** Interactive prompts make this awkward to test. The underlying helpers (`read_archivist_config`, `write_archivist_config`, `install_hooks`, `init_registry`, `register_apparatus`) are all tested individually. The glue is trivial.

**Interactive registration in `add`.** When `.archivist/config.yaml` has no apparatus name, `add` prompts the user interactively. The non-interactive paths (config present, UUID resolution matrix) are fully covered. The interactive path is awkward to test for the same reason `init` is.

**`commit_registry` and `push_registry`.** Both are documented stubs that do nothing. They are tested only to confirm they are callable and don't raise. No behaviour to test.

---

## When to Revise or Expand the Suite

### Mandatory: Add a test before shipping a bug fix

If something broke in production, the fix must include a test that would have caught it. No exceptions. "It was a one-liner" is not a reason to skip the test — one-liner bugs are the most embarrassing to repeat.

Pattern:
```python
def test_whatever_the_bug_was(git_repo, monkeypatch):
    # Reproduce the exact conditions that triggered the bug
    ...
    # Assert the correct behavior (which the bug violated)
    ...
```

Name the test after what it's catching, not after the bug number or the fix. `test_removing_last_property_drops_block` is useful six months later. `test_issue_42_fix` is archaeology.

### Mandatory: Add tests when adding a new command or subcommand

New `changelog` subcommand? Write:
- The dry-run contract test
- The sentinel survival test
- At least one test for whatever makes this subcommand different from `general`
- A frontmatter field presence test

New `frontmatter` subcommand? Write:
- Dry-run: no files modified
- Happy path: expected mutation applied
- Skip path: file that shouldn't be touched is untouched
- At minimum one edge case specific to this subcommand's logic

### Mandatory: Add tests when modifying shared helpers

If you touch anything in `archivist/utils/` that already has unit test coverage, run the existing tests first. If they pass, good. If your change makes them fail, fix the test to reflect the new contract AND verify the change is intentional, not a regression.

If the helper you're touching has no unit tests yet, add at least the happy path and one failure case before shipping your change.

### Recommended: Add tests when a behavior has been described incorrectly

If you find a test that pins the wrong behavior (i.e., the test was written against a misunderstanding of what the code should do), fix the test AND the code if needed. Don't just make the test pass.

### Not Worth Testing: Trivial wrappers

If a function is literally `return some_other_thing(arg)` with no conditional logic, no state, and no error handling, it's not worth a dedicated test. Trust the thing it wraps.

---

## Fixture Reference

All fixtures live in `conftest.py`.

### `isolated_registry`

An `autouse = True` fixture required on every test module that touches `registry.py`, `add.py`, or `deinit.py`. Patches `get_registry_dir()` to return a temp path so no test ever touches the real `~/.archivist/`. All other registry path helpers derive from `get_registry_dir()` — patching that one function is sufficient.

```python
import archivist.utils.registry as registry_module

@pytest.fixture(autouse=True)
def isolated_registry(tmp_path, monkeypatch):
    fake_dir = tmp_path / ".archivist"
    monkeypatch.setattr(registry_module, "get_registry_dir", lambda: fake_dir)
    return fake_dir
```

Patch against the imported module object (`registry_module`), not a string path. This guarantees the patch lands correctly regardless of how callers imported the function.

This fixture is **not** in `conftest.py` at the suite root — it must be defined in each affected test module. Putting it in the global `conftest.py` with `autouse=True` would patch `get_registry_dir()` for every test in the suite, including changelog and frontmatter tests that don't touch the registry at all. Scope it correctly.

### `git_repo`

A real, initialized git repo in `tmp_path` with a committed initial state (`.archivist` file seeded, initial commit made). Returns a `_Repo` instance.

```python
git_repo.path           # Path to the repo root
git_repo.commit({...})  # Write files, stage, commit. Returns short SHA.
git_repo.stage({...})   # Write files and stage without committing.
```

The `_Repo.commit()` dict maps relative path strings to file content strings. Parent directories are created automatically.

**`monkeypatch.chdir(git_repo.path)` is required in every integration test** that calls a `run()` function. `get_repo_root()` shells out to `git rev-parse --show-toplevel`, which resolves relative to the process working directory. Without the `chdir`, it finds the actual repo root (wherever archivist itself lives) instead of the test repo.

### `md_file`

A callable fixture. Returns a function `(name: str, content: str) -> Path` that writes a markdown file into `tmp_path`.

```python
note = md_file("note.md", "---\nclass: character\n---\nBody text")
```

### `args` (conftest helper function, not a fixture)

Stamps out a fake `argparse.Namespace` for frontmatter commands.

```python
from tests.conftest import args

run_add(args(property="status", value="draft"))
run_add(args(property="reviewed", dry_run=True))
```

Default values: `dry_run=False`, `property=None`, `value=None`, `overwrite=False`.

Apply-template tests define their own `_apply_template_args()` factory inline because the required kwargs differ completely from the other frontmatter commands. Keep them separate.

---

## Patterns and Conventions

### Injection seam pattern

Used in `test_add.py` and `test_deinit.py` to isolate git and filesystem operations without patching stdlib globals. Commands that call `subprocess.run` or `shutil.rmtree` expose these as callable parameters with production defaults. Tests pass fakes directly.

```python
# Production call — defaults used, real git and filesystem
run(args)

# Test call — controlled behaviour injected
def _noop_runner(*args, **kwargs) -> MagicMock:
    return MagicMock(returncode=0)

def _noop_remover(_path: Path) -> None:
    pass  # accept the path, do nothing

run(args, runner=_noop_runner, remover=_noop_remover)
```

When a test needs to observe call order or verify that a step was skipped, pass a spy:

```python
called = []

def _spy_remover(_path: Path) -> None:
    called.append("remover")

run(args, runner=_noop_runner, remover=_spy_remover)
assert called == ["remover"]
```

Note the `_path` convention: parameters that must exist to satisfy the caller's interface but are not used in the body are prefixed with `_`. This is distinct from parameters that are both declared and meaninglessly ignored — those should not exist.

### Cross-DB integrity test pattern

Used in `test_registry_commands.py` (Phase 1 integration) to verify that application code enforces referential integrity that SQLite cannot. The apparatus DB columns that reference `registry.db` are unenforced at the database layer — these tests are the only thing standing between correct behaviour and silent orphaned rows.

The pattern: bring both databases live under `_isolated_registry`, perform a registry operation that changes a module's state, then assert that the subsequent apparatus DB write either succeeds or is correctly rejected by application code — not by SQLite.

```python
def test_apparatus_write_rejected_for_decimated_module(tmp_path, monkeypatch):
    # Setup: register apparatus and module, then decimate the module
    init_registry()
    apparatus_uuid = register_apparatus("writing", git_remote=None)
    module_uuid = register_module(
        apparatus_name="writing",
        name="cosmic-horror",
        module_type="library",
        path=tmp_path / "cosmic-horror",
        git_remote=None,
    )
    decimate_module(module_uuid)

    # The module is now decimated. Any command that writes to the apparatus DB
    # using this UUID must check registry state first and reject the write.
    module = get_module_by_uuid(module_uuid)
    assert module is not None, "Module row not found. Test setup is broken."
    assert module["decimated_at"] is not None, (
        "Module should be decimated before testing the write guard."
    )

    # Application code is responsible for this check. If you're here because
    # this test failed, something wrote to the apparatus DB without checking
    # decimated_at first. Find it and fix it.
    with pytest.raises((ValueError, RuntimeError)):
        some_apparatus_write_function(module_uuid=module_uuid, ...)
```

The exact exception type depends on how the calling command signals rejection — `ValueError` if it raises, `sys.exit` if it calls that (use `pytest.raises(SystemExit)` accordingly). What matters is that the write does not silently succeed.

**Nullable `TypedDict` fields** (`decimated_at`, `last_synced_at`, `git_remote`, `git_remote_name` on `ModuleRow` / `ApparatusRow`) need a second guard beyond the row-existence check above before they can be passed to anything other than a direct `None` comparison. See "Nullable field extraction pattern" in `REGISTRY_TESTING_SPECIFICATION.md` — it's not repeated here, but it applies to every registry test in this suite, not just the cross-DB ones.

### Dry-run test pattern

Used in every changelog and frontmatter integration test class. Compare **files only** (`.is_file()`), not directory entries. `find_changelog_output_dir` creates `ARCHIVE/` before the dry-run gate.

```python
def test_dry_run_writes_absolutely_nothing(self, git_repo, monkeypatch):
    monkeypatch.chdir(git_repo.path)
    git_repo.stage({"notes/thing.md": "---\ntitle: Thing\n---\nBody."})
    before = {p for p in git_repo.path.rglob("*") if p.is_file()}

    run_whatever(_cl_args(dry_run=True))

    after = {p for p in git_repo.path.rglob("*") if p.is_file()}
    assert before == after, (
        "dry_run=True and files still changed. "
        "A dry run that writes is just called a run."
    )
```

### Sentinel survival test pattern

Used in every changelog integration test class. Two runs, user content injected between them.

```python
def test_user_content_below_sentinel_survives_rerun(self, git_repo, monkeypatch):
    monkeypatch.chdir(git_repo.path)

    # First run
    git_repo.stage({"notes/a.md": "---\ntitle: A\n---\n"})
    run_whatever(_cl_args())

    # Find and verify the sentinel exists
    changelog = _find_changelog(output_dir)
    content = changelog.read_text(encoding="utf-8")
    assert "<!-- archivist:auto-end -->" in content

    # Inject user content below sentinel
    precious = "## My Notes\n\nSome important shit I wrote by hand.\n"
    changelog.write_text(content + "\n" + precious, encoding="utf-8")

    # Stage another file and rerun
    git_repo.stage({"notes/b.md": "---\ntitle: B\n---\n"})
    run_whatever(_cl_args())

    result = changelog.read_text(encoding="utf-8")
    assert "Some important shit I wrote by hand." in result, (
        "Rerun wiped user content below the sentinel. This is catastrophic."
    )
```

### Changelog args helper

All changelog integration tests use `_cl_args()` defined at module level:

```python
def _cl_args(**kwargs) -> argparse.Namespace:
    defaults = {"dry_run": False, "commit_sha": None, "path": None}
    return argparse.Namespace(**{**defaults, **kwargs})
```

### Changelog finder helpers

```python
def _find_changelog(output_dir: Path) -> Path:
    changelogs = list(output_dir.glob("CHANGELOG-*.md"))
    assert changelogs, f"No changelog found in {output_dir}."
    return changelogs[0]

def _read_changelog(output_dir: Path) -> str:
    return _find_changelog(output_dir).read_text(encoding="utf-8")
```

### Error message conventions

Assertion messages in this test suite are written in the voice documented in `AGENTS.md`. They explain what went wrong and why it matters, not just that the assertion failed. They point at the likely culprit. This is not decoration — it's the difference between a useful failure and a debugging session.

Bad:
```python
assert "UUID" in fm
```

Good:
```python
assert fm.get("UUID"), "UUID field is empty or missing entirely"
```

Very good:
```python
assert first_uuid == second_uuid, (
    f"UUID changed on rerun: {first_uuid!r} → {second_uuid!r}. "
    "The UUID must be stable for the lifetime of an unsealed changelog."
)
```

---

## Known Gaps (Accepted)

These are untested and the decision to leave them that way is intentional. If any of them surface a real bug, add a test at that point.

| Gap | Reason |
|:----|:-------|
| `manifest` command | Low regression risk; DB interaction covered via publication tests |
| `reclassify` command | Simple logic, no shared state, no history of bugs |
| `hooks install/sync` | Writes to `~/.git-templates/`; obnoxious in CI |
| `init` command (core) | Interactive prompts; underlying helpers fully tested individually |
| `init` command (AP path) | Calls `init_registry` and `register_apparatus` — both tested in isolation; the glue is trivial until it isn't |
| `migrate` command | One-shot destructive command; interactive confirmation; underlying helpers (`read_archivist_config`, `write_archivist_config`) fully tested. Add tests if the logic grows |
| `archivist muster` | Phase 2; not yet implemented |
| `archivist distribute` | Phase 2; not yet implemented |
| `archivist broadcast` | Phase 2; not yet implemented |
| `test_registry_commands.py` (cross-DB integrity) | Phase 1 integration tests for apparatus DB write guards; pattern documented in spec; not yet built — add when the first apparatus DB write command ships |
| Windows line endings (`\r\n`) in frontmatter | `FRONTMATTER_RE` uses `\n`; behavior pinned but not enforced — see `TestHasFrontmatter.test_windows_line_endings_dont_cause_a_scene` |
| Empty repo (no commits yet) | `get_git_changes` hits non-existent HEAD; would need dedicated fixture |
| `.archivist` with unknown `module-type` | Auto-routing falls back to `general`; testable but low priority |
| `add` with no apparatus in config | Falls through to interactive registration path; interactive prompts make this awkward; the non-interactive paths are fully covered |

---

## Priority Order for Future Expansion

1. **Bug-driven tests** — the only category with a strict deadline (before the fix ships)
2. **New subcommand dry-run and sentinel tests** — mandatory for any new `changelog` subcommand
3. **New helper unit tests** — mandatory when touching existing tested helpers
4. **`test_registry_commands.py` cross-DB integrity tests** — before any command that writes to an apparatus DB ships; the pattern is documented and waiting
5. **`manifest` integration tests** — when the command gets more complex or a bug bites
6. **`reclassify` tests** — if the command acquires conditional logic
7. **Phase 2 AP commands** (`muster`, `distribute`, `broadcast`) — each needs dry-run contract, registry state assertions, and at minimum one end-to-end happy path
8. **Everything else** — when production use surfaces a gap

Tests written against a real bug are worth more than tests written speculatively.
This suite gives you the foundation. Let production use build the rest.