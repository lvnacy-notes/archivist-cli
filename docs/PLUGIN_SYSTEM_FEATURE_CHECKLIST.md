# Archivist Plugin System — Feature Checklist

Tracking all decisions and implementation tasks from the design session.

---

## Architecture Decisions (locked)

- [x] **Option A: path-based loading** — plugins live in `.archivist/`, discovered by convention, no registry
- [x] **Composition over inheritance** — library builders exposed as importable callables; no base class, no `super()`
- [x] **Convention-over-configuration discovery** — Archivist looks for `.archivist/changelog.py` automatically; file presence = activation, no config key required
- [x] **No `plugins/` subdirectory** — one file per extensible command, flat in `.archivist/`
- [x] **No `init` required to activate** — plugin is live the moment the file exists; delete it to revert
- [x] **`config.yaml` not `config.yml`** — `.archivist` file migrates to `.archivist/config.yaml`
- [x] **Plugin indicator line on load** — `→ changelog plugin found: .archivist/changelog.py` printed before run
- [x] **`sample-changelog.py` written by `init`** — ignored by discovery, permanently available as a working reference
- [x] **Library-first** — only library builders made importable in this pass; extend to other module types if the pattern proves out

---

## Migration: `.archivist` file → `.archivist/` directory

- [x] **`config.py`: `get_archivist_config_path()`** — updated; prefers `.archivist/config.yaml`, falls back to flat `.archivist` file
- [x] **`config.py`: `read_archivist_config()`** — handles both path forms transparently; guards against directory being mistaken for a file
- [x] **`config.py`: `write_archivist_config()`** — always writes to `.archivist/config.yaml`; creates directory if needed
- [x] **`config.py`: `build_ignore_spec()`** — no change needed; unchanged
- [x] **Update `AGENTS.md`** — documents `.archivist/` directory convention, `config.yaml`, plugin discovery rules, library public API, activation/deactivation, and future extensibility

---

## Plugin Discovery

- [x] **`config.py`: `find_changelog_plugin()`** — returns `.archivist/changelog.py` Path if present, None if not; ignores everything else
- [x] **`config.py`: `load_changelog_plugin()`** — loads via `importlib.util`; validates `run` callable; clear error messages on syntax error, missing `run`, or load failure
- [x] **`cli.py`: changelog routing block** — checks for plugin after `cl_command` resolution but only when no explicit subcommand was given; explicit subcommands (`archivist changelog library`) always bypass the plugin

---

## Library Module: expose builders as public API

- [x] **`library.py`: `build_frontmatter()`** — renamed from `_build_frontmatter`; public
- [x] **`library.py`: `build_body()`** — renamed from `_build_body`; public
- [x] **`library.py`: `analyse_catalog()`** — renamed from `_analyse_catalog`; public
- [x] **`library.py`: `print_summary()`** — renamed from `_print_summary`; public (worth exposing for plugin wrapping)
- [x] **`library.py`: module docstring** — documents full public API surface and composition pattern with example

---

## `sample-changelog.py`

- [x] **Write `sample-changelog.py`** — working library reproduction; all four public builders imported; comprehensive commentary at every decision point; accurate `ChangelogContext` and `ctx.data` fields documented with exact types; Archivist voice throughout
- [x] **`init` command** — writes `sample-changelog.py` to `.archivist/` for library projects; skips if already present; non-fatal if bundled file can't be read; dry-run notes what would be written
- [x] **Discovery logic** — `find_changelog_plugin()` loads only `changelog.py`; `sample-changelog.py` is never picked up

---

## Testing & Feedback

- [x] **`--dry-run` covers plugins** — plugin calls `run_changelog()` which already respects `dry_run`; no special handling needed
- [x] **Plugin load error handling** — `load_changelog_plugin()` catches `SyntaxError` and general `Exception` separately; validates `run` callable; all paths exit with clear messages, no raw tracebacks
- [x] **Indicator line format** — `→ changelog plugin found: .archivist/changelog.py` printed before dispatch

---

## Future / Out of Scope for This Pass

- [ ] Extend plugin convention to `manifest` command
- [ ] Extend plugin convention to `reclassify` (when `--migrate` lands)
- [ ] Extend plugin convention to `init` (custom module type registration)
- [ ] Expose other module type builders (story, publication, vault) as public API