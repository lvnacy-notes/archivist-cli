---
class: spec
category:
  - feature
  - git
affiliations:
  - git-integration
created: 2026-05-19
modified: 2026-05-19
version: 0.1
related:
  - "[[GIT_INTEGRATION]]"
tags:
---

Patches `archivist manifest` to resolve its input from the staging index, matching the behavioral contract of `archivist changelog`. Adds a `path` argument that triggers auto-staging when nothing is staged under the given directory.

---

## Command Signature (Revised)

```
archivist manifest [path] [--dry-run]
```

`path` is optional. If omitted, manifest runs over everything currently staged — same as `archivist changelog` with no `--path`. If provided, it scopes to that directory.

---

## Execution Paths

```
path provided, files already staged under it
  → proceed; generate manifest over staged files

path provided, nothing staged under it
  → git add <path>
  → proceed; generate manifest over newly staged files

path provided, nothing staged under it, --dry-run set
  → print what would be staged
  → print what manifest would contain
  → write nothing, stage nothing

no path, files staged
  → proceed over full index

no path, nothing staged
  → exit with error
```

---

## The `git add` Step

- Runs `git add <path>` — recursive, respects `.gitignore` naturally
- Stages only what's under the given path; nothing outside it is touched
- On failure (git error, path doesn't exist, nothing to add): propagate the error and abort — do not proceed to manifest generation with an empty or partial index
- After staging, calls `ensure_staged_under(path, git_root)` as usual to confirm something actually landed in the index before continuing

---

## `--dry-run` Contract

`--dry-run` must gate both the staging step and the write step. A dry run that stages files is not a dry run. Print what `git add` would touch, print the manifest that would be generated, write nothing, stage nothing.

---

## What Doesn't Change

- Manifest generation logic — untouched
- `ensure_staged_under` — still the gate before generation
- Output format — untouched
- DB interaction — untouched