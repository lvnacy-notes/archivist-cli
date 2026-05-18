---
class: plan
category:
  - feature
  - database
affiliations:
created:
modified: 2026-05-18
version: 0.1
related:
  - "[[GIT_INTEGRATION]]"
  - "[[GIT_INTEGRATION_SPEC]]"
  - "[[Drawing - git Implementation.png]]"
tags:
---
**Purpose:** Everything that must be in place before development begins on the
git integration feature. Some items are gaps in the Centralized Database spec or
implementation. Some are new requirements that the git integration spec has
surfaced. None of this is git integration implementation work — this is the
table-setting that makes that work possible.

---

## 1. Fix `git_remote` Population Throughout

The current centralized DB implementation uses `git remote get-url origin` to
capture a module's remote URL. This is a footgun (not every user names their
remote "origin") and must be replaced before any further registry work touches
this field.

### 1.1 — Utility function: `list_git_remotes()`

- [ ] Add `list_git_remotes(git_root: Path) -> list[tuple[str, str]]` to
  `archivist/utils/git.py`
  - Runs `git remote -v` and parses the output
  - Returns a list of `(name, url)` pairs — fetch URLs only, deduped
  - Returns an empty list if no remotes are configured; does not raise
- [ ] Export from the `archivist/utils` barrel

### 1.2 — `archivist init`: interactive remote selection

- [ ] Replace the `git remote get-url origin` call in the init flow (§1.5.5 of
  `CENTRALIZED_DATABASE_IMPLEMENTATION.md`) with the following behavior:
  - Call `list_git_remotes()` against the current repo
  - **No remotes configured:** inform the user; offer a free-text URL input;
    allow skipping with a warning that `restore` capability will be degraded
  - **One remote configured:** present it and ask for confirmation;
    allow overriding with free-text input
  - **Multiple remotes configured:** present a numbered list; require selection;
    allow free-text input for "none of these"
  - Store the selected URL (not the remote name) in `modules.git_remote`
- [ ] Update `CENTRALIZED_DATABASE_SPEC.md` §9.2 to replace the
  `git remote get-url origin` reference with the correct selection flow
- [ ] Update `CENTRALIZED_DATABASE_IMPLEMENTATION.md` §1.5.5 to reflect
  the new behavior

### 1.3 — `archivist add`: capture URL from command args

- [ ] In `archivist add`, the remote URL is already known — it is the `<url>`
  positional argument passed to the command
- [ ] Store this URL directly as `modules.git_remote` at registration time;
  do not query git for a remote name after the operation completes
- [ ] Update `CENTRALIZED_DATABASE_IMPLEMENTATION.md` §1.5.3 to document
  this explicitly
- [ ] Confirm this handles the submodule case and the clone case identically —
  the URL is the URL regardless of which git operation ran

### 1.4 — Audit for remaining hardcoded "origin" references

- [ ] Search the codebase for `git remote get-url origin` and any other
  hardcoded assumptions about the remote name "origin"
- [ ] Replace or remove every instance found

---

## 2. `~/.archivist/` as a Git Repository

The entire `~/.archivist/` directory — registry, apparatus databases, all of it —
is version controlled. This is the foundation for `archivist restore`. It must
be established during first-run `archivist init` and kept current by the
pre-commit hook sync.

### 2.1 — First-run initialization

- [ ] During `archivist init`, after creating `~/.archivist/` if it does not
  exist, check whether it is already a git repository
  - **Not a git repo:** run `git init` inside `~/.archivist/`; prompt the user
    for a registry remote URL (free-text input; skippable with warning);
    add the remote if provided; make an initial commit
  - **Already a git repo:** proceed; do not re-initialize
- [ ] The registry remote URL is stored as a normal git remote on the
  `~/.archivist/` repo — no separate config entry needed; `git remote get-url`
  against `~/.archivist/` is the retrieval mechanism
- [ ] Update `CENTRALIZED_DATABASE_IMPLEMENTATION.md` Phase 1 storage checklist
  to include `~/.archivist/` git initialization

### 2.2 — SQLite-in-git strategy (document explicitly)

- [ ] Add a comment block to `archivist/utils/registry.py` (or wherever the
  registry connection lives) documenting the following:
  - SQLite files are binary; git tracks them but cannot diff or merge them
  - `~/.archivist/` is **never merged** — on restore it is overwritten from
    the remote in its entirety
  - This is intentional: the remote is the restoration anchor; local is the
    active state; the pre-commit sync keeps them in agreement
  - Any future tooling that touches `~/.archivist/` must not introduce merge
    assumptions

### 2.3 — Pre-commit hook: registry commit and push

- [ ] After the existing registry sync step in the pre-commit hook (§1.5.8 of
  `CENTRALIZED_DATABASE_IMPLEMENTATION.md`), add:
  - Stage all changes in `~/.archivist/` (`git add -A` scoped to that directory)
  - Commit with an auto-generated message (e.g. `archivist: sync [module-name] [date]`)
  - Push to the configured remote, if one exists
  - On push failure: warn and continue — do not block the commit
- [ ] Update `CENTRALIZED_DATABASE_IMPLEMENTATION.md` §1.5.8 to include this step
- [ ] Update hook installation (`archivist hooks install` / `archivist hooks sync`)
  to write the augmented hook script

### 2.4 — `.gitignore` for `~/.archivist/`

- [ ] Evaluate whether anything in `~/.archivist/` should be excluded from
  version control (e.g. lock files, temporary SQLite WAL/SHM files)
- [ ] Write a `.gitignore` into `~/.archivist/` during initialization if needed
  - At minimum: `*.db-wal`, `*.db-shm` (SQLite write-ahead log files)

---

## 3. Hook Installation on Freshly Cloned / Added Modules

`archivist add` installs git hooks into the target module after registration.
The behavior of `archivist hooks sync` on a freshly cloned repo (no existing
hooks, no prior Archivist setup) has not been explicitly verified or specced.

- [ ] Manually verify `archivist hooks sync` run against a freshly cloned repo:
  - Hooks directory exists but is empty → should install cleanly
  - Hooks directory does not exist → should create and install
  - Hooks already present (non-Archivist) → should warn; document current behavior
- [ ] If any of the above cases fail or behave unexpectedly, fix before
  git integration begins
- [ ] Document the verified behavior in `CENTRALIZED_DATABASE_IMPLEMENTATION.md`
  §1.5.3 (the `archivist add` hook installation step)
- [ ] Confirm `archivist hooks install` (global template path) propagates to
  newly cloned repos via git's `init.templateDir` mechanism; verify this works
  end-to-end on a test clone

---

## 4. `archivist init` Flow Restructure for `git init`

The current `archivist init` calls `get_repo_root()` early and exits if no git
repo is found. The git integration adds `git init` as a first step when no repo
exists, which means `get_repo_root()` must move.

- [ ] Map the exact call site of `get_repo_root()` in the current `archivist init`
  implementation
- [ ] Confirm no other early-exit paths assume a repo exists before the `.git`
  check would run
- [ ] Document the new intended call order:
  1. Check for `.git` (file or folder) in working directory
  2. If absent: run `git init`
  3. `get_repo_root()` — now safe to call
  4. Remainder of init flow unchanged
- [ ] This is design/planning work only; do not implement until the Centralized
  DB feature has shipped and the git integration phase begins

---

## 5. `archivist deinit` Disk Removal Strategy

The current scaffolded `deinit` does not implement disk removal. When real git
execution is wired up, two cases need explicit handling.

- [ ] **Submodule removal sequence:** confirm the correct order is
  `git submodule deinit` → `git rm` → done; document any superproject staging
  implications (the `git rm` stages a deletion in the superproject index and
  requires a subsequent commit by the user)
- [ ] **Non-submodule removal:** use `shutil.rmtree` (Python stdlib; no sudo
  required for user-owned paths); on `PermissionError`, print the path and
  instruct the user to remove it manually; never invoke `sudo` from within
  Archivist
- [ ] Document both behaviors in `CENTRALIZED_DATABASE_IMPLEMENTATION.md` §1.5.4
- [ ] Confirm `--retain` flag skips disk removal entirely in both cases

---

## 6. Centralized DB Spec and Checklist Updates

Collected updates to existing documents required before git integration begins.

### `CENTRALIZED_DATABASE_SPEC.md`

- [ ] §9.2 (`archivist init` flow): replace `git remote get-url origin`
  reference with the remote selection flow described in prerequisite §1.2
- [ ] §9.1 (`archivist add`): note that `git_remote` is captured from the
  `<url>` command argument, not from querying git post-operation
- [ ] §3 (Storage Locations): update to reflect that `~/.archivist/` is a git
  repository with a configured remote; document the SQLite-in-git strategy
- [ ] §10 (Pre-Commit Hook): update to include the registry commit-and-push step

### `CENTRALIZED_DATABASE_IMPLEMENTATION.md`

- [ ] §Phase 1 storage checklist: add `~/.archivist/` git initialization steps
- [ ] §1.5.3 (`archivist add`): update `git_remote` capture to use URL from
  command args; add hook installation verification note
- [ ] §1.5.4 (`archivist deinit`): document submodule vs. non-submodule removal
  sequences; document `shutil.rmtree` strategy
- [ ] §1.5.5 (`archivist init` augmentation): replace `git remote get-url origin`
  with interactive remote selection; add registry git initialization step
- [ ] §1.5.8 (pre-commit hook): add registry commit-and-push step

---

## 7. Test Suite

These test fixtures and cases must exist before git integration implementation
begins. They are not git integration tests — they are the infrastructure that
git integration tests will build on.

- [ ] Fixture: repo with no remotes configured
- [ ] Fixture: repo with exactly one remote (non-"origin" name)
- [ ] Fixture: repo with multiple remotes (mixed names and URLs)
- [ ] Fixture: `~/.archivist/` as a temp-dir git repo with a bare remote
- [ ] Test: `list_git_remotes()` returns correct pairs for all three remote
  configurations above
- [ ] Test: `list_git_remotes()` returns empty list for no-remote case without
  raising
- [ ] Test: remote selection in `archivist init` correctly stores URL (not name)
  for each remote configuration
- [ ] Test: `archivist add` stores the `<url>` argument as `git_remote` without
  querying git

---

## 8. `GIT_SPEC.md` Internal Consistency Pass

Once all of the above prerequisite work is documented and the centralized DB spec
has been updated to match:

- [ ] Re-read `GIT_SPEC.md` end-to-end against the updated centralized DB spec
  to confirm all cross-references are accurate
- [ ] Confirm §7 (Dependencies on Centralized Database Feature) reflects the
  current state of the checklist — not the state it was in when first drafted
- [ ] Bump `GIT_SPEC.md` version to `0.2.0` when this pass is complete