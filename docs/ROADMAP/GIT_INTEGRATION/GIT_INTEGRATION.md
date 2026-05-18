---
class: index
category:
  - feature
  - git
  - cli
affiliations:
  - centralized-db
created: 2026-05-17
modified: 2026-05-17
version:
status: not-started
related:
  - "[[GIT_INTEGRATION_SPEC]]"
  - "[[GIT_INTEGRATION_PREREQUISITES]]"
  - "[[Drawing - git Implementation.png]]"
tags:
---
```dataviewjs
// PROGRESS BAR
// Update checklistPath to the vault-relative path of this feature's checklist.
// Remove this block entirely if no checklist exists yet.
const checklistPath = "ROADMAP/git-integration/CHECKLIST"; // ← UPDATE THIS
const tasks = dv.page(checklistPath)?.file?.tasks;
if (!tasks || tasks.length === 0) {
    dv.paragraph("_No checklist linked. Update `checklistPath` to render progress._");
} else {
    const completed = tasks.filter(t => t.completed).length;
    const total = tasks.length;
    const pct = Math.round((completed / total) * 100);
    dv.el("progress", "", {
        attr: { value: completed, max: total, style: "width: 100%; margin-bottom: 0.25em;" }
    });
    dv.el("p", `${completed} / ${total} tasks complete — ${pct}%`, {
        attr: { style: "margin: 0; font-size: 0.85em; color: var(--text-muted);" }
    });
}
```

## Overview

Archivist wraps select git operations — `init`, `add`, `deinit` — to keep the Apparatus registry in sync with the actual state of the filesystem and version control. Where module lifecycle management and git intersect, Archivist coordinates both in a single command.

```toc
```

## Status

**Status:** `= this.status`

Blocked on Centralized DB — hard dependency, implementation cannot begin until the centralized DB feature ships. Several open questions remain: registry version control strategy, first-run remote configuration flow, and superproject detection behavior in `deinit`. The `git_remote` population logic in the centralized DB implementation checklist (`§1.5.5`) must be corrected before git integration work begins.

## Dashboard

```dataview
TASK
FROM "ROADMAP/git-integration/CHECKLIST"
WHERE !completed
SORT file.mtime DESC
```

## Description

Archivist does not intend to replicate git's interface or own git's responsibilities. It owns the Apparatus. Git owns the repos. Where those two concerns intersect — registration, containment, lifecycle — Archivist coordinates both in a single command.

The guiding frame: **these commands manage Apparatus membership, and git is the mechanism, not the product.** When `archivist add` runs `git submodule add`, it does so because adding a submodule is how containment is expressed in git. The user's goal is registering a module with the Apparatus; the git operation is how that intent is committed to the repository.

### Commands

Three commands, each handling a distinct point in the module lifecycle:

**`archivist init`** gains the ability to run `git init` itself when no repository is found. Currently it exits on a missing `.git`; the new behavior checks first and initializes if needed, then proceeds with the standard configuration and registration flow. Existing behavior is otherwise unchanged.

**`archivist add`** registers a module with the Apparatus. The git operation — `git clone` or `git submodule add` — is determined by context: inside a repo, you are adding a submodule; outside one, you are cloning. No flags needed to disambiguate. All unknown arguments pass through directly to git.

**`archivist deinit`** deregisters a module and removes it from the superproject or machine. Operation order is fixed and non-negotiable: **Apparatus cleanup runs first, git second.** The rationale is failure recovery — if git runs first and removes the module, the config is gone and a subsequent registry failure has nothing to recover from. Apparatus-first ensures the module is still on disk if anything goes wrong with the git step.

### Argument Passthrough

All git flags and options are passed through to the underlying git command without validation or curation. Archivist does not need to understand them; git will reject invalid arguments with its own error output. Archivist propagates the exit code and stderr verbatim. Implementation via `nargs=argparse.REMAINDER`.

### Failure Semantics

`archivist deinit` must be safe to re-run after a partial failure. If Apparatus cleanup completed but the git step failed, re-running must detect that the registry is already updated and skip straight to the git step. The `--retain` flag provides a surgical recovery path: Apparatus-only cleanup, with git handled manually.

### `archivist restore` — Deferred

Out of scope for initial implementation but must not be foreclosed by decisions made here. `restore` depends on `git_remote` being populated reliably on every `modules` row, tombstoning being clean (`decimated_at` set correctly on `deinit`), and the registry being queryable from outside any individual module. The registry version control question — `~/.archivist/` as a git repo with its own remote — is the primary open question and must be resolved before `restore` can be specced.

## Documents

| Role | Document |
|---|---|
| Spec | [[GIT_INTEGRATION_SPEC]] |
| Checklist | |
| Testing | |

## Affiliated Features

```dataviewjs
// Reads the `affiliations` frontmatter field and renders a status table for each
// matched feature index file found under ROADMAP/.
// Values in `affiliations` must exactly match the feature's folder name — slug format.
const affiliations = dv.current().affiliations ?? [];
if (affiliations.length === 0) {
    dv.paragraph("_No affiliations set. Add feature slugs to the `affiliations` frontmatter field._");
} else {
    const pages = dv.pages('"ROADMAP"')
        .where(p => p.class === "index" && affiliations.includes(p.file.name));
    if (pages.length === 0) {
        dv.paragraph("_No matching index files found. Verify that `affiliations` values match feature folder names exactly._");
    } else {
        dv.table(
            ["Feature", "Status", "Category"],
            pages.map(p => [
                p.file.link,
                p.status ?? "—",
                (p.category ?? []).join(", ") || "—"
            ])
        );
    }
}
```

Hard dependency on Centralized DB — the registry infrastructure (`get_registry_path()`, `register_module()`, `add_module_to_bay()`, hook installation) must be in place and stable before implementation begins. This is not a soft coupling; git integration is effectively Phase 2 of the centralized DB work.