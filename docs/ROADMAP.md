# Archivist — Roadmap

A living document tracking intended future direction across two areas: feature development and development infrastructure. Nothing here is a spec — these are pins on a board to return to when the time is right.

---

## Features

### Centralized Cross-Project Database

Currently, `archive.db` is scoped per-project, living in each module’s `ARCHIVE/` directory. The long-term vision is a single machine-level database that every Archivist-managed repo feeds into — aggregating commit hashes and changelog frontmatter data across the entire Apparatus.

Primary use case is day-to-day querying: activity across all projects, changelog history, commit timelines. But the architecture should be designed with behavioral use cases in mind from the start, so it can serve as the foundation for cross-project orchestration down the line (see below) without requiring a structural rewrite.

#### Technology Decision: SQLite

**The answer is SQLite. No server, no daemon, no container — just a file at a well-defined machine-level path (e.g. `~/.archivist/archivist.db`).**

This decision was reached deliberately, after evaluating alternatives including containerized database servers (PostgreSQL in Docker) and Elasticsearch. The reasoning:

**Why not a containerized server?**
The appeal of Docker is keeping tooling off the local machine, but for a developer tool that runs on every commit via the post-commit hook, a containerized database introduces real costs: the daemon must be running constantly, cold starts add latency on the hot path, and volume mounts and networking add operational surface area. That trades one form of environment management for another that is meaningfully heavier. SQLite has no daemon and no server process — it is the file.

**Why not Elasticsearch?**
Elasticsearch is a distributed search engine built for full-text relevance ranking over large document corpora — millions of documents, complex query DSL, tunable scoring. The Archivist use case is structured queries over frontmatter metadata and commit history: activity timelines, changelog lookups, cross-project filtering. That is a SQL `WHERE` clause, not a search problem. Elastic would impose substantial operational overhead before you ever reached a scale that justified it.

**Why SQLite is sufficient at every stage?**
All queryable content is frontmatter data — structured key-value pairs that map cleanly to columns and rows. SQLite’s home turf. If full-text search over note content ever becomes a requirement, SQLite’s built-in FTS5 extension covers it without an external dependency. If multi-machine access or concurrent writes ever become a requirement, migration to PostgreSQL is straightforward because the schema is already relational — nothing about choosing SQLite now forecloses that path later.

**The upgrade path, if it’s ever needed:**

```
SQLite (per-project, current)
  → SQLite (machine-level, centralized)       ← target
    → PostgreSQL (if multi-machine or concurrent writes demand it)
```

Elasticsearch is not on this path. It solves a different class of problem.

#### Schema Strategy

Frontmatter schemas are maintained explicitly and kept in sync with the per-class note templates. This is not a schema-less or EAV design — frontmatter keys are known, typed, and stable per class, and that structure is reflected directly in the database schema.

The reason people reach for JSON blobs or entity-attribute-value patterns is usually that their data shape is unknown or shifts too often to manage migrations. Neither applies here: note classes and their template fields are defined and controlled by Archivist itself. Making them explicit in SQLite adds precision and query clarity at negligible cost. SQLite migrations are plain SQL — no ORM ceremony.

Schemas are cheap. Maintain them.

#### Open Questions

Registration, storage location, and full schema are not yet specced. Return here to brainstorm when the per-project DB patterns have stabilized and the query use cases are better understood. The note in the original roadmap stands: design should follow stabilization, not precede it.

-----

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

### Phase 2 — Test Suite ✅

**Goal:** Catch regressions in frontmatter manipulation and the archive DB before they eat someone's vault alive.

**Shipped.** This is not a scaffold. The suite is complete, covering every load-bearing behavior in the codebase. The authoritative reference for what's covered, what's deliberately skipped, and how to evolve the suite without tearing holes in the net is `TESTING_SPECIFICATION.md`.

### Structure

```
tests/
├── conftest.py                      # shared fixtures: git_repo, md_file, args factory
├── unit/
│   ├── test_changelog_helpers.py    # extract_descriptions, format_file_list, UUID generation
│   ├── test_config.py               # config read/write, module-type resolution, plugin discovery
│   ├── test_frontmatter.py          # every frontmatter helper — the highest-stakes unit module
│   ├── test_rename_helpers.py       # all three rename inference passes, pipeline integration
│   └── test_templater.py            # mask/restore cycle, expression evaluator, TemplaterContext
└── integration/
    ├── test_changelog_commands.py   # all five changelog subcommands, dry-run, sentinel survival
    ├── test_frontmatter_commands.py # add / remove / rename / apply-template against real files
    └── test_seal.py                 # seal mechanics, DB transitions, idempotency
```

Run unit tests only (fast, no git required):

```bash
pytest -m "not integration" -v
```

Run everything:

```bash
pytest -v
```

### Strategy: integration over unit tests

The codebase is tightly coupled to the filesystem and git subprocess calls. Pure unit tests with heavy mocking would be brittle and wouldn't catch real bugs. The suite:

- Uses `pytest`'s `tmp_path` fixture for a throwaway directory per test
- `git init`s programmatically via the `git_repo` fixture to create a realistic environment
- Runs actual `archivist` operations against real files and asserts on file contents and git state
- Tags real-filesystem tests with the `integration` marker so the fast subset is always runnable in isolation

### What got covered

1. **Frontmatter manipulation** — `add`, `remove`, `rename`, `apply-template`, including Templater mask/restore across all four commands. An add/remove/rename that corrupts a note is a bad day and a worse conversation.
2. **Archive DB transactions** — SHA claim logic in `changelog publication`, UUID→SHA transition at seal time. The kind of thing that fails silently and surfaces three weeks later as a mystery.
3. **Rename detection** — all three inference passes (`detect_dir_renames`, `infer_undetected_renames`, `infer_renames_by_content`) exercised individually and composed end-to-end, including 50-level-deep path chains to surface any O(n²) surprises.
4. **The dry-run contract** — every command that touches files has a test that compares the full file set before and after. A dry run that writes is just called a run.
5. **The sentinel boundary** — every changelog subcommand has a two-run test that injects user content below `<!-- archivist:auto-end -->` and verifies it survives regeneration untouched. Cross this line and you're destroying someone's work.
6. **The UUID / seal lifecycle** — UUID stability across reruns, UUID→short SHA transition at seal, `changelogs` table population, idempotent re-sealing.

Git hook behavior remains the one deferred area — hardest to test in CI, lowest marginal value until everything else is green everywhere.

### Dependency

```
pytest    # still the only new dependency
```

No `pytest-mock`. No `factory_boy`. No `hypothesis`. `unittest.mock` is stdlib and covers whatever mocking is needed. If git isn't on PATH, integration tests fail loudly. That is correct behavior.

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
| 2 | `pytest` test suite — unit + integration | ✅ Shipped |
| 3 | GitHub Actions CI (lint, typecheck, test matrix) | After Phase 2 |
| — | `pyyaml` version floor, dev deps declared | Ongoing / now |