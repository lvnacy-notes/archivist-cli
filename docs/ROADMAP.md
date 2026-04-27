# Archivist — Roadmap

A living document tracking intended future direction across two areas: feature development and development infrastructure. Nothing here is a spec — these are pins on a board to return to when the time is right.

---

## Features

### Centralized Cross-Project Database

Currently, `archive.db` is scoped per-project, living in each module's `ARCHIVE/` directory. The long-term vision is a single machine-level database that every Archivist-managed repo feeds into — aggregating commit hashes and changelog frontmatter data across the entire Apparatus.

Primary use case is day-to-day querying: activity across all projects, changelog history, commit timelines. But the architecture should be designed with behavioral use cases in mind from the start, so it can serve as the foundation for cross-project orchestration down the line (see below) without requiring a structural rewrite.

Registration, storage location, and schema are not yet specced. Return here to brainstorm when the per-project DB patterns have stabilized and the query use cases are better understood.

### Multi-Vault / Submodule Orchestration

The pain point is coordination across vaults and submodules: distributing templates, syncing `AGENTS` files, running operations across the full Apparatus without having to drop into each module individually.

This feature area is closely tied to the centralized database — a machine-level registry that knows about every managed repo is the natural foundation for orchestration. Design of this feature should follow, not precede, the centralized DB work.

### `frontmatter rename` — Type Coercion on Collision

Nice-to-have. When the target property name already exists on a note and its value is of a different type than the source property, the command should attempt to coerce the incoming value to match the existing type. Currently Obsidian will silently swallow the type mismatch — the warning disappears and the damage is done.

The larger scope is building checks and prompts around this to drive automation: surface the conflict, give the user a decision point, and let Archivist act on the answer rather than leaving Obsidian to paper over it.

### User-Defined Templates

Changelog templates were implemented and subsequently removed due to inconsistent behavior when fired from the post-commit hook. Manifest template scanning is functional but rudimentary. The broader goal — allowing users to supply and manage their own templates without touching code — is worth returning to once the underlying template machinery is more consistent.

Low priority. Pin and revisit.

### Changelog — Directory Rename Detection

Git's `-M` rename detection operates on file content similarity and has no awareness of directory renames. When an edition directory is renamed (e.g. `VOL II NO 28` → `VOL II NO 28 ✓`), git may report the contained files as raw `D` and `A` pairs rather than renames, causing them to be miscategorized in the changelog output.

A partial solution was implemented: `detect_dir_renames` and `reassign_deletions` in `utils.py` attempt to recover these cases from git's `R` pairs, and `infer_undetected_renames` attempts to match unpaired `D`/`A` entries by filename. Neither fully resolves the issue in practice.

Until resolved, directory renames require manual review of the generated changelog.

### Templater Support ✅

Obsidian's [Templater plugin](https://github.com/SilentVoid13/Templater) allows
users to embed dynamic expressions in frontmatter property values:

```yaml
created: <% tp.date.now("YYYY-MM-DD") %>
title: <% tp.file.title %>
```

**Shipped.** Archivist now handles `<% %>` expressions in frontmatter safely across all four frontmatter commands (`add`, `remove`, `rename`, `apply-template`). Behavior is controlled by the `templater` key in `.archivist`, configured during `archivist init`.

Three modes:

- **`preserve`** — mask expressions before any frontmatter manipulation, restore them verbatim afterward. No resolution, no corruption. The default.
- **`resolve`** — Python reimplementation of the `tp.date`, `tp.file`, and `tp.frontmatter` API surface. Resolves at write time with no Obsidian or Node.js dependency. Unresolvable expressions (`tp.system`, `tp.user`, `tp.obsidian`) fall back to verbatim with a warning.
- **`false`** — treat `<% %>` as plain strings. Zero overhead for Templater-free projects.

Implementation lives in `archivist/utils/templater.py`. The mask/restore cycle uses stable sentinel tokens (`__ARCHIVIST_TMPL_N__`) that survive YAML parsing, reordering, and merging without corruption. The expression evaluator uses `ast.literal_eval` for argument parsing — no arbitrary code execution, no external runtime.

`remove` and `rename` intentionally have no Templater machinery — they operate on keys, never values, and cannot corrupt an expression they never touch.

The optional Phase 4 `dukpy` gate (embedded JS fallback evaluator) was deliberately not implemented. The graceful degradation contract — unresolvable expressions left verbatim with a warning — covers the gap without a 40MB optional dependency. Revisit only if production use surfaces a real gap in the resolve-mode coverage.

### `reclassify` — Structural Migration on Reclassification

Currently, `archivist reclassify` is a surgical value swap: it rewrites the `class:` line in frontmatter and nothing else. That's intentional for the first pass, but the long-term vision is a full structural migration — when you reclassify a note, the command applies the target class's frontmatter template to it.

Concretely: reclassifying a note from `article` to `column` should not just change the `class:` value. It should add properties the `column` template requires that the note is missing, remove properties the template doesn't include, and reorder everything to match — the same logic `frontmatter apply-template` already performs, fired automatically as part of the reclassification.

The natural implementation is a `--migrate` flag that pairs with the existing `--from` / `--to` interface and accepts a path to the target class's template file:

```
archivist reclassify --from article --to column --migrate templates/column.md
archivist reclassify --from article --to column --migrate templates/column.md --dry-run
```

Without `--migrate`, the command stays surgical — just the class value. With it, the template is applied after the rewrite in the same pass.

This is closely related to `frontmatter apply-template` and should be designed alongside it, not independently. The shared logic should live in `utils.py` so both commands can call it without duplication — which is exactly the pattern the rename helpers followed.

---

## Development Infrastructure

A staged plan ordered by priority and dependencies. Each phase is completable independently, without blocking active feature work.

---

### Phase 1 — Linting & Formatting (Do Now)

**Goal:** Baseline code quality tooling. Zero disruption to feature work.

### Install `ruff`

```bash
$(pyenv which pip) install ruff
```

Add to `pyproject.toml`:

```toml
[project.optional-dependencies]
dev = ["ruff", "pyright"]

[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I"]  # pycodestyle errors, pyflakes, isort
```

### Usage

```bash
ruff check .         # lint
ruff format .        # format
ruff check --fix .   # lint + auto-fix where possible
```

### Notes

- `ruff` replaces `black`, `flake8`, and `isort` — do not install those separately
- Pylance (VSCode) handles type checking inline via Pyright — no separate `pyright` CLI install needed until CI is set up
- No reinstall of the package required; `ruff` is a dev tool, not a runtime dependency

---

### Phase 2 — Test Scaffolding (After Active Feature Work Settles)

**Goal:** Establish a test structure before the codebase grows further. Catching regressions in frontmatter manipulation and the archive DB is the primary payoff here.

### Install `pytest`

```bash
$(pyenv which pip) install pytest
```

Add to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
```

### Recommended test structure

```
tests/
├── conftest.py           # shared fixtures (tmp git repo, sample vault files)
├── test_frontmatter.py   # frontmatter add / remove / rename / apply-template
├── test_manifest.py      # manifest generation and template field ordering
├── test_changelog.py     # changelog subcommands
└── test_archive_db.py    # SQLite SHA tracking, claim logic
```

### Strategy: integration over unit tests

This codebase is tightly coupled to the filesystem and git subprocess calls. Pure unit tests with heavy mocking will be brittle and won't catch real bugs. The better approach:

- Use `pytest`'s built-in `tmp_path` fixture to get a throwaway directory per test
- `git init` programmatically inside `tmp_path` to create a realistic environment
- Run actual `archivist` commands against it and assert on file contents and git state

### What to test first

1. **Frontmatter manipulation** — most self-contained, highest-stakes. An add/remove/rename that corrupts a note is a bad day.
2. **Archive DB transactions** — the SHA claim logic in `changelog publication` is the kind of thing that fails silently and is hell to debug later.
3. **Git hook behavior** — hardest to test; defer until the others are covered.

---

### Phase 3 — GitHub Actions CI (After Test Suite Exists)

**Goal:** Automated checks on every push and PR. Catches breakage on Python versions and environments you don't develop on.

### Workflow file: `.github/workflows/ci.yml`

```yaml
name: CI

on:
  push:
    branches: ["main"]
  pull_request:

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install ruff
      - run: ruff check .
      - run: ruff format --check .

  typecheck:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install pyright pyyaml
      - run: pyright

  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: pip install -e ".[dev]" pytest
      - run: pytest
```

### Why the Python version matrix matters

Archivist declares `python 3.10+` and is a public repository. Anyone cloning it may be on 3.10, 3.11, or 3.12. Stdlib behavior has subtle differences across versions — the matrix catches this cheaply. No macOS or Windows runners needed unless portability to those platforms becomes a stated goal.

### Notes

- Lint and typecheck run on a single version (latest stable) — no reason to matrix those
- CI with no tests is just a linter, which is better than nothing but not a substitute for Phase 2
- Do not set up CI until at least a minimal test suite exists — an empty `pytest` run is noise

---

### Dependency Hygiene (Ongoing)

### Pin `pyyaml` with a floor version

The current `pyproject.toml` should bound the one runtime dependency explicitly:

```toml
[project]
dependencies = [
    "pyyaml>=6.0",
]
```

This prevents silent breakage on someone's older environment without over-constraining to an exact version.

### Keep dev dependencies declared

```toml
[project.optional-dependencies]
dev = ["ruff", "pyright", "pytest"]
```

Install for development with:

```bash
$(pyenv which pip) install -e ".[dev]"
```

---

### Summary

| Phase | What | When |
|---|---|---|
| 1 | `ruff` linting + formatting | Now |
| 2 | `pytest` scaffolding + integration tests | After active feature work settles |
| 3 | GitHub Actions CI (lint, typecheck, test matrix) | After Phase 2 |
| — | `pyyaml` version floor, dev deps declared | Ongoing / now |