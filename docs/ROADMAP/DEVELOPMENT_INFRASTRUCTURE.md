---
class:
category:
affiliations:
created: 2026-05-17
modified: 2026-05-17
version:
status:
related:
tags:
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
