---
class: checklist
category:
  - feature
  - infrastructure
  - cli
affiliations:
created: 2026-05-21
modified: 2026-05-23
version:
related:
  - "[[APPARATUS_PLATFORM]]"
  - "[[APPARATUS_PLATFORM_SPEC]]"
  - "[[AP_PHASE_2_IMPLEMENTATION]]"
  - "[[AP_PHASE_1_IMPLEMENTATION]]"
tags:
  - apparatus-platform
  - phase-2
---

> Phase 1 ships first. Full stop. If Phase 1 is not committed, tested, and stable, this document does not exist yet.

Phase 2 adds three commands — `muster`, `distribute`, `broadcast` — and the scope resolution machinery they all depend on. These commands are read-and-fan-out operations on the registry Phase 1 built. They require a correct, populated registry to do anything useful. Implement on top of a working Phase 1, not alongside it.

---

## Phase Gate

Before writing a single line of Phase 2 code, confirm:

- [ ] All Phase 1 tests pass: `pytest -v`
- [ ] `archivist init` registers a module in the registry cleanly
- [ ] `archivist add` registers a submodule and creates a `module_bays` row
- [ ] `archivist deinit` decimates correctly and respects operation order
- [ ] Registry isolation fixture from Phase 1 tests is extractable to `conftest.py` for reuse

If any of the above are red, stop and fix Phase 1.

---

## Implementation Order

```
1. Audit: get_repo_root() at import time    — must happen before broadcast exists
2. Scope resolution utility                 — everything depends on this
3. archivist muster                         — read-only; validates scope resolution
4. archivist distribute                     — file operations; simpler than command execution
5. archivist broadcast                      — most complex; depends on audit results
6. CLI parser updates                       — alongside commands
7. Tests                                    — last; cover everything
```

Do not implement `broadcast` until the `get_repo_root()` audit is complete. Do not implement any Phase 2 command before scope resolution exists. The order is a dependency graph.

---

## 1. Pre-Implementation Audit: `get_repo_root()` at Import Time

> 🔴 **This audit must complete before `broadcast` is implemented.** `broadcast` works by `chdir`-ing into each module and invoking the frontmatter subcommand's `run()` directly. If any frontmatter subcommand calls `get_repo_root()` at module import time — rather than inside `run()` — `chdir` will not fix it. The path will have been resolved against the wrong directory before broadcast ever runs, silently operating on the wrong repo.

- [ ] Audit every file in `archivist/commands/frontmatter/`: `add.py`, `remove.py`, `rename.py`, `apply_template.py`
- [ ] Confirm `get_repo_root()` is called inside `run()`, not at module level or in any function called at import time
- [ ] Confirm no module-level variables are assigned the result of `get_repo_root()` or any function that calls it
- [ ] Confirm no `@functools.cache` or similar decorators memoize a path resolved at first import
- [ ] Document findings: if any subcommand fails this audit, refactor it before proceeding

> ⚠️ The audit covers import-time side effects only. Function-level calls to `get_repo_root()` inside `run()` are exactly correct — that's the pattern broadcast depends on. Don't "fix" those.

---

## 2. Scope Resolution Utility

All three Phase 2 commands share identical scope resolution logic. It lives in a shared utility — not duplicated across commands. Define it in `archivist/utils/` (a new module or added to `registry.py` — pick one, be consistent with the existing structure).

> 🚫 **Blocked on:** Phase 1 registry queries (`get_apparatus_modules`, `get_bay_modules`, `get_vault_modules`) working correctly.

- [ ] `resolve_scope(args: argparse.Namespace, include_decimated: bool = False) -> list[dict]`
  - Reads scope from `args`: one of `--apparatus`, `--vault`, `--module`
  - Applies `--type` filter after scope is resolved (not as a replacement for scope)
  - Returns module list sorted alphabetically by `name`
  - Registry not accessible → `sys.exit(1)` with a clear error; this is a **hard abort**, not a skip
- [ ] `--apparatus <name>`: call `get_apparatus_modules(name, include_decimated)`
  - Apparatus not found → exit with error; not a skip
- [ ] `--vault <name>`: look up vault module by name; call `get_bay_modules(vault_uuid, include_decimated)`
  - Vault name not found in registry → exit with error
  - Vault module found but is not type `vault` → warn; proceed (any superproject is valid scope)
- [ ] `--module <name|uuid>`: look up each by name or UUID; collect results
  - Individual module not found → warn per module; skip that one; continue with the rest
  - This is the only scope selector where "not found" is a per-item warning rather than a hard abort
- [ ] `--type <type>` filter: applied after scope resolution; filters the returned list
  - `--type` without `--apparatus`, `--vault`, or `--module` → exit with error; `--type` is not a scope

> 🔴 **Registry not accessible is a hard abort.** "Module path not found on disk" (checked per-module during command execution) is a skip-with-warning. These are different failure modes. Do not conflate them. The distinction matters: no registry means no scope means no safe operation — abort. A stale path on one module means that module is skipped — everyone else proceeds.

> ⚠️ `--type` is a filter on an established scope. It must fail loudly if used alone:
> ```
> archivist muster --type library
> ✗ --type requires --apparatus or --vault to establish scope. Try:
>   archivist muster --apparatus writing --type library
> ```
> This cannot be enforced by argparse alone since `--type` is a valid standalone argument syntactically. Validate in `resolve_scope()` or at the top of each command's `run()`.

> ⚠️ `--module` accepts names OR UUIDs and is repeatable (`nargs` / called multiple times). Look up each value: try UUID first (exact match on `modules.uuid`), then name (exact match on `modules.name`). If both a UUID and a name resolve to different modules, both are included. Document this behavior.

> 📌 Sort order is alphabetical by module `name`, always. This makes output deterministic and diffable across runs. Do not sort by `path`, `uuid`, or insertion order.

---

## 3. `archivist muster`

```
archivist muster [scope selector] [--include-decimated]
```

Read-only status table. No `--dry-run`. No writes. No side effects.

> 🚫 **Blocked on:** scope resolution utility (§2) complete; apparatus DB `changelogs` table populated by at least one sealed module (for meaningful `last seal` output in tests).

- [ ] Resolve module list via `resolve_scope(args, include_decimated=args.include_decimated)`
- [ ] For each module, collect:
  - `path.exists()` — filesystem check only; no git operations
  - Last seal: most recent `sealed_at` from apparatus DB `changelogs` WHERE `module_uuid = this_uuid`; `—` if no records or apparatus DB inaccessible
  - `last_synced_at` from `modules` row; `—` if NULL (newly registered, never committed since registration)
- [ ] Format output as aligned columns: name, type, path, path-valid indicator, last seal, last synced
- [ ] Decimated modules: excluded unless `--include-decimated`; when shown, mark distinctly (e.g., strikethrough or `[decimated]` label)
- [ ] No `--dry-run` argument; do not add one; the command has no side effects to dry-run

> ⚠️ Path validity is `path.exists()`. That is the entire check. Do not call `get_repo_root()`. Do not call any git subprocess. Do not attempt to verify the path is a git repo. A directory that exists but isn't a git repo shows `✓` — the user's concern, not muster's. A path that doesn't exist shows `✗ PATH NOT FOUND`. Simple.

> ⚠️ `last seal` comes from the **apparatus DB** (`~/.archivist/[apparatus].db`), not from the per-project `ARCHIVE/archive.db`. These are different tables. If the apparatus DB is inaccessible (missing, corrupted), fall back to `—` for all modules in that apparatus. Do not crash. Do not try to read `ARCHIVE/archive.db` as a fallback — that is a different data source with a different schema.

> ⚠️ `last_synced_at` is NULL for any module registered but not yet committed since Phase 1 shipped. Handle NULL gracefully — display as `—`, not as a crash, not as `None`, not as `1970-01-01`.

> 📌 Column alignment: fixed-width formatting matters for readability. The longest module name, path, and date in the result set determine column widths. Compute widths before rendering, not per-row. A ragged output table is an embarrassment.

> 📌 `--include-decimated` is a muster-only flag. `distribute` and `broadcast` always operate on active modules only. Do not add this flag to those commands.

---

## 4. `archivist distribute`

```
archivist distribute <source> [--dest <relative-path>] [scope selector] [--overwrite] [--dry-run]
```

Copies one file into every module in scope. Writes files. Does not stage them.

> 🚫 **Blocked on:** scope resolution utility (§2) complete.

- [ ] Validate `<source>` exists before doing anything else; exit with error if not
- [ ] Resolve source as absolute path; determine if it's inside the current repo
- [ ] `--dest` requirement check (in `run()`, not in the parser — argparse cannot enforce this):
  - Source is absolute path → `--dest` required
  - Source is outside current repo → `--dest` required
  - Violation: exit with clear error explaining why `--dest` is needed
- [ ] Resolve module list via `resolve_scope(args)` — active modules only
- [ ] Per-module flow:
  - [ ] `path.exists()` check → if not: `warning(f"{name}: PATH NOT FOUND — skipping")`; increment skipped; continue
  - [ ] Resolve destination: `module_path / args.dest` (or same relative path as source if no `--dest`)
  - [ ] Destination exists and `--overwrite` not set → `warning(f"{name}: {dest} already exists — pass --overwrite to replace it")`; increment skipped; continue
  - [ ] Dry-run gate: print what would happen; increment written (hypothetically); continue
  - [ ] Write file: `shutil.copy2(source, destination)`
  - [ ] On write failure: `warning(f"{name}: failed to write {dest} — {e}")`; increment failed; continue
  - [ ] On success: `success(f"{name}: written")`; increment written
- [ ] Summary at end: `N written, M skipped, K failed`
- [ ] Do NOT stage files after writing; do NOT call any git subprocess

> 🔴 **Do not stage.** Distribute writes the file and stops. The user decides whether and when to stage it. This is explicit and intentional. Do not add a `--stage` flag without a spec change. Do not add staging "as a convenience." If you find yourself typing `subprocess.run(["git", "add", ...])` in distribute, stop.

> ⚠️ `--dest` validation cannot happen in argparse because it depends on the value of `<source>`. argparse will parse `--dest` as optional and say nothing is wrong. The check must happen in `run()` after both are available. Make the error message specific:
> ```
> ✗ --dest is required when source is an absolute path.
>   The relative destination within each module must be explicit.
>   Example: archivist distribute /abs/path/to/file.md --dest docs/file.md --apparatus writing
> ```

> ⚠️ **Failures skip and continue.** A module with a stale path, an unwritable destination, or a permission error does not abort the run. Every other module in scope still receives the file. The summary at the end is the user's signal that something needs attention. Do not abort. Do not raise. Capture, warn, increment, continue.

> ⚠️ `--overwrite` is explicit consent to replace an existing file. Without it, an existing file is a skip, not an error, not an overwrite. The warning must be actionable:
> ```
> ⚠️ fiction-vault: .archivist/AGENTS.md already exists — pass --overwrite to replace it
> ```

> 📌 `shutil.copy2` preserves metadata (timestamps, etc.) in addition to content. This is preferable to `Path.write_bytes(source.read_bytes())` for file distribution. Use it.

---

## 5. `archivist broadcast`

```
archivist broadcast frontmatter <subcommand> [subcommand-args] [scope selector] [--dry-run]
```

Runs a frontmatter subcommand in each module's working directory, in series. Not a general execution engine.

> 🚫 **Blocked on:** scope resolution utility (§2) complete AND `get_repo_root()` audit (§1) passed for all frontmatter subcommands.

> 🔴 **`chdir` must be wrapped in `try/finally`.** If the inner command raises an unhandled exception, the working directory must still be restored before continuing to the next module. A failed `chdir`-back breaks every subsequent module in the run — `get_repo_root()` will resolve to the wrong place, file paths will be wrong, and the failure will be silent and baffling. Use the pattern:
> ```python
> original = Path.cwd()
> try:
>     os.chdir(module_path)
>     run_inner_command(inner_args)
> except Exception as e:
>     warning(f"{name}: {e}")
>     failed += 1
> finally:
>     os.chdir(original)
> ```

- [ ] Parse `frontmatter` literal — required first argument; reject anything else with a clear error
- [ ] Identify inner subcommand: second argument after `frontmatter` (e.g., `add`, `remove`, `rename`, `apply-template`)
- [ ] Validate inner subcommand is a known frontmatter subcommand; exit with error if not
- [ ] Parse remaining passthrough args for the inner subcommand (see §5.1 below)
- [ ] Inject `--dry-run` into inner args if broadcast received `--dry-run`; user passes it once to broadcast
- [ ] Resolve module list via `resolve_scope(args)` — active modules only
- [ ] Per-module flow:
  - [ ] `path.exists()` check → if not: `warning(f"[{name}] PATH NOT FOUND — skipping")`; increment skipped; continue
  - [ ] `os.chdir(module_path)` inside `try/finally` (see above)
  - [ ] Invoke inner subcommand's `run(inner_args)`
  - [ ] Capture result; classify as success or failure
  - [ ] `os.chdir(original)` in `finally`
  - [ ] Print per-module output block (see §5.2 below)
- [ ] Summary at end: `N succeeded, M skipped, K failed`

### 5.1 Inner Args Parsing

> ⚠️ This is the most technically complex part of broadcast and must be resolved as a design decision before implementation begins.

The frontmatter subcommands each expect an `argparse.Namespace` with specific attributes (e.g., `args.property`, `args.value`, `args.dry_run`). Broadcast receives these as a flat list of strings from `nargs=REMAINDER`. It must convert that list into the correct `Namespace` for the inner command.

Two approaches — pick one before writing code:

**Option A: Re-parse using the inner command's parser**
Expose each frontmatter subcommand's parser setup as a callable (e.g., `build_add_parser() -> ArgumentParser`). Broadcast calls the appropriate builder, parses the passthrough args, and gets a correct `Namespace`. Requires minor refactoring of how frontmatter parsers are defined in `cli.py` — extract sub-parser setup functions that both `build_parser()` and broadcast can call.

**Option B: Build `Namespace` from known arg structures**
Hardcode the expected arg structure for each frontmatter subcommand in broadcast. Broadcast parses the passthrough list against a local mini-parser and constructs the `Namespace` manually. Fragile — any change to a frontmatter subcommand's args requires a matching change in broadcast. Not recommended for long-term maintenance.

> 📌 Option A is the correct choice. The refactor is small: extract each frontmatter subparser definition into a `build_X_parser()` function in `cli.py` (or in each command module). `build_parser()` calls them; broadcast calls them. The parsers themselves don't change. The duplication goes away.

- [ ] Design decision made and documented before implementation begins
- [ ] If Option A: extract frontmatter subparser builders before implementing broadcast
- [ ] `--dry-run` injected into parsed inner `Namespace` by broadcast; not re-parsed from passthrough

### 5.2 Per-Module Output

```
[cosmic-horror]
  ✓ Added reviewed: false to 47 files.

[panopticon]
  ✗ PATH NOT FOUND — skipping

[fiction-vault]
  — 0 files matched
```

- [ ] Module name appears in brackets as a header for each block
- [ ] Inner command output is captured and indented under the module block
- [ ] Failures reported per-module with the exception or stderr; do not propagate upward
- [ ] Empty result (0 files matched) is not a failure; report it as informational

> ⚠️ The inner command's output goes to the per-module block, not directly to stdout. If the inner command uses `progress()`, `success()`, and `warning()` from `output.py`, those will fire to stdout/stderr immediately unless captured. Consider whether capturing is necessary for clean output, or whether the per-module header is sufficient context. Decide before implementing — inconsistent output is worse than either choice consistently applied.

---

## 6. CLI Parser Updates (`cli.py`)

> 🚫 **Blocked on:** commands (§3, §4, §5) complete enough to wire up.

- [ ] Define `_add_scope_selectors(parser)` helper — attaches scope arguments to any parser that needs them:
  ```python
  def _add_scope_selectors(parser: argparse.ArgumentParser) -> None:
      scope = parser.add_mutually_exclusive_group(required=True)
      scope.add_argument("--apparatus", metavar="NAME")
      scope.add_argument("--vault", metavar="NAME")
      scope.add_argument("--module", metavar="NAME|UUID", action="append", dest="modules")
      parser.add_argument("--type", metavar="TYPE", dest="module_type")
  ```
- [ ] `archivist muster` parser:
  ```python
  muster_p = subparsers.add_parser("muster", help="Status report across registered modules.")
  _add_scope_selectors(muster_p)
  muster_p.add_argument("--include-decimated", action="store_true")
  ```
- [ ] `archivist distribute` parser:
  ```python
  distribute_p = subparsers.add_parser("distribute", help="Copy a file to multiple modules.")
  distribute_p.add_argument("source")
  distribute_p.add_argument("--dest")
  _add_scope_selectors(distribute_p)
  distribute_p.add_argument("--overwrite", action="store_true")
  distribute_p.add_argument("--dry-run", action="store_true")
  ```
- [ ] `archivist broadcast` parser:
  ```python
  broadcast_p = subparsers.add_parser("broadcast", help="Run a frontmatter command across modules.")
  broadcast_p.add_argument("command", choices=["frontmatter"])
  broadcast_p.add_argument("passthrough", nargs=argparse.REMAINDER)
  _add_scope_selectors(broadcast_p)
  broadcast_p.add_argument("--dry-run", action="store_true")
  ```
- [ ] Dispatch: add `elif args.command == "muster"`, `"distribute"`, `"broadcast"` branches
- [ ] Import new command modules at the top of `cli.py` dispatch section

> ⚠️ `add_mutually_exclusive_group(required=True)` enforces that exactly one of `--apparatus`, `--vault`, or `--module` is provided. `--type` is outside the group and is optional. The `required=True` handles the "no scope selector → exit with error" requirement from the spec without needing manual validation in `run()`.

> ⚠️ `--module` uses `action="append"` and `dest="modules"` to support repeated use (`--module cosmic-horror --module panopticon`). The result in `args.modules` will be a list. Handle the single-module case (list of one) identically to multi-module — no special casing.

> ⚠️ `_add_scope_selectors` is defined in `cli.py`. It is not a utility module function — it's a parser helper that belongs near the parser definitions. Do not barrel-export it.

> 📌 Per `CLAUDE.md`: parser definitions are in the "What Not to Touch" category unless adding or removing a subcommand. These additions qualify — they are new subcommands. Be surgical. Add new parsers and the `_add_scope_selectors` helper without touching existing parser definitions.

---

## 7. Testing: Phase 2

Run the full test suite before writing new tests. Phase 2 tests depend on Phase 1 being stable.

**Registry isolation** applies to all Phase 2 tests identically to Phase 1. The `autouse` fixture from Phase 1 must be available in `conftest.py` for reuse. Extract it if it isn't already.

**Fixture requirement:** Phase 2 integration tests need multiple registered modules. Define a `multi_module_registry` fixture that creates a registry with at least:
- One apparatus (`"writing"`)
- Two vault modules (`"fiction-vault"`, `"research-vault"`)
- Two library modules under `"fiction-vault"` (`"cosmic-horror"`, `"panopticon"`)
- One library module with no vault (`"standalone-lib"`)
- One decimated module (for decimation-exclusion tests)

Building this fixture correctly once is better than rebuilding it in every test.

**Unit tests: `tests/unit/test_scope_resolution.py` (new file)**

- [ ] `--apparatus`: returns all active modules in apparatus, sorted by name
- [ ] `--apparatus`: excludes decimated modules by default
- [ ] `--vault`: returns all modules under vault (via `module_bays`), including the vault itself
- [ ] `--vault` with non-vault superproject: returns contained modules; no error
- [ ] `--module` by name: returns that module
- [ ] `--module` by UUID: returns that module
- [ ] `--module` not found: warns; returns empty list for that entry; does not abort
- [ ] `--module` repeated: returns all named modules
- [ ] `--type` filter after `--apparatus`: only matching types returned
- [ ] `--type` filter after `--vault`: only matching types returned
- [ ] `--type` alone: exits with error (not a scope)
- [ ] No scope selector provided: exits with error
- [ ] Registry not accessible: `sys.exit(1)` — confirmed via `pytest.raises(SystemExit)`
- [ ] Result is always sorted alphabetically by name

**Integration tests: `tests/integration/test_mvo.py` (new file)**

All tests use `multi_module_registry` fixture and `monkeypatch.chdir()`.

`archivist muster`:
- [ ] `--apparatus`: all active modules listed; decimated excluded
- [ ] `--apparatus --include-decimated`: decimated module appears, marked
- [ ] `--vault`: only modules under that vault listed, including vault itself
- [ ] `--type` filter applied: only matching module types in output
- [ ] Stale path: `✗ PATH NOT FOUND` in output; no crash
- [ ] `last_synced_at` NULL: displays as `—`; no crash
- [ ] `last seal` from apparatus DB: correct value shown; `—` when no records
- [ ] No `--dry-run` argument accepted by parser

`archivist distribute`:
- [ ] Happy path: file written to each module in scope
- [ ] `--dest` omitted with relative source inside repo: destination resolved correctly
- [ ] `--dest` omitted with absolute source: exits with error before any writes
- [ ] `--dest` omitted with source outside repo: exits with error before any writes
- [ ] Existing file without `--overwrite`: skipped with warning; other modules still written
- [ ] Existing file with `--overwrite`: replaced
- [ ] Stale module path: skipped with warning; other modules still written
- [ ] Write failure (permission): skipped with warning; counted as failed; other modules continue
- [ ] Dry-run: no files written anywhere; plan printed for each module
- [ ] Files not staged after write: `git diff --cached` shows nothing new
- [ ] Summary line correct: N written, M skipped, K failed counts match actual outcomes

`archivist broadcast`:
- [ ] Happy path: inner frontmatter command runs in each module; results reported per-module
- [ ] `--dry-run` propagated: inner command receives `dry_run=True` in its `Namespace`
- [ ] `--dry-run` not required twice: passing once to broadcast is sufficient
- [ ] Stale module path: skipped with warning; other modules continue
- [ ] Inner command failure: reported per-module; run continues; working directory restored
- [ ] Working directory restored after inner command failure (`os.getcwd()` matches before/after)
- [ ] `--type` filter: only matching module types receive the command
- [ ] `frontmatter` literal missing: exits with error
- [ ] Unknown inner subcommand: exits with error
- [ ] No scope selector: exits with error
- [ ] Output: per-module block with name header and indented inner output

**Dry-run contract (distribute and broadcast):**
- [ ] `test_dry_run_writes_absolutely_nothing` — file set before == file set after; registry state unchanged

---

## Phase 2 Completion Gate

Before marking Phase 2 done:

- [ ] All tests pass: `pytest -v`
- [ ] No regressions in Phase 1 or pre-existing test suite
- [ ] `get_repo_root()` audit findings resolved — no import-time calls remain
- [ ] `archivist muster --apparatus <name>` produces correct aligned output on a real multi-module registry
- [ ] `archivist distribute` does not stage files under any circumstances (verified with `git status`)
- [ ] `archivist broadcast` restores working directory correctly after every module, including failures
- [ ] `--dry-run` on both commands writes nothing and changes nothing
- [ ] All three commands exit cleanly with a clear error when no scope selector is provided