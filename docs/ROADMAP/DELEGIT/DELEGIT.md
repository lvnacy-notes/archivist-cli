---
class: index
category:
  - feature
  - git
  - mobile
affiliations:
created: 2026-05-17
modified: 2026-05-17
version:
status: not-started
related:
  - "[[DELEGIT_SPECIFICATION]]"
tags:
---
```dataviewjs
// PROGRESS BAR
// Update checklistPath to the vault-relative path of this feature's checklist.
// Remove this block entirely if no checklist exists yet.
const checklistPath = "ROADMAP/delegit/CHECKLIST"; // ← UPDATE THIS
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

A minimal, focused Swift wrapper over libgit2, purpose-built for Archivist mobile. Covers local git operations only — diff, staging, commit, and blob reading. Remote operations (push, pull, fetch, clone) are explicitly out of scope and belong to Working Copy via x-callback-url.

```toc
```

## Status

**Status:** `= this.status`

No blockers identified. DeleGit is a standalone Swift package with no dependencies on the Python-side Archivist feature set. Implementation can begin independently of any other roadmap item.

## Dashboard

```dataview
TASK
FROM "ROADMAP/delegit/CHECKLIST"
WHERE !completed
SORT file.mtime DESC
```

## Description

DeleGit is designed to grow into a general-purpose Swift git library across future apps. Every function added must be driven by a real use case — completeness for its own sake is explicitly out of scope.

### Guiding Principles

Five principles govern the API surface and every implementation decision:

**Narrow by design.** Only wrap what's needed. Resist the urge to wrap adjacent libgit2 functions because they're nearby in the headers.

**Idiomatic Swift.** No C types leak through the public API. No `UnsafePointer`, no `Int32` error codes, no `OpaquePointer`. Callers see Swift structs, enums, and thrown errors.

**Throwing over optionals.** Functions that can fail throw a typed `GitError`. Never return nil when the reason for failure is knowable and actionable.

**Async where it matters.** Diff and blob operations can be slow on large repos and are marked async. Simple operations like staging a file are synchronous.

**Memory is the library's problem.** Every libgit2 object that requires a free call gets one, inside the wrapper, before the Swift type is returned. Callers never touch a libgit2 pointer.

### v0.1 Scope

Four areas of functionality, all driven by Archivist mobile's specific needs:

**Repository** — entry point for everything else. Opens an existing repo at a given URL, exposes the working directory and `.git` path. Manages the `git_repository *` lifetime internally; callers never see the pointer.

**Diff** — the core of what Archivist needs. Two operations: staged diff (HEAD vs index, the `--cached` equivalent) and committed diff (one commit vs its parent, for post-commit changelog generation). Both support rename detection via libgit2's `git_diff_find_similar`, with a configurable similarity threshold defaulting to git's standard 50. The `renamed` case carries the similarity score so suspicion logic in Archivist can surface near-miss renames in the UI.

**Staging** — `stage(path:)`, `stage(paths:)`, and `stageDirectory(path:)`. The batched `stage(paths:)` exists specifically to avoid N index writes when staging multiple files; prefer it over calling `stage(path:)` in a loop.

**Commit** — `commit(message:)` returns a `CommitResult` with the full SHA, short SHA, message, and timestamp. Returning the SHA directly from the commit call eliminates the follow-up log lookup needed when delegating commits to Working Copy's URL scheme.

**Blob** — `blob(at:ref:)` and `blobFromIndex(at:)` for reading file content at a specific commit or from the index. Used for content-similarity rename detection — fetching the old content of a deleted file to compare against a candidate added file.

### Working Copy Boundary

DeleGit handles all local operations. Working Copy handles all remote operations (push, pull, fetch, clone) via x-callback-url. The boundary is clean: if it requires network access or a remote, it belongs to Working Copy. `WCClient` — a thin async x-callback-url wrapper — mirrors DeleGit's API style so call sites don't feel like they're switching paradigms.

The app can still delegate commits to Working Copy if the user prefers its commit UI. In that case, use Working Copy's `commit` URL scheme command, then call `log(limit: 1)` to retrieve the SHA. Both paths work; DeleGit's `commit()` is for when the app owns the commit flow.

### Post-Commit Backfill Flow

The post-commit seal sequence — generating the changelog, staging it, committing, sealing the SHA into the filename — is driven entirely by `CommitResult`. No hook required, no second commit needed for the seal if the changelog was staged before the commit. The UI must enforce the sequence: generate → stage → commit → seal → stage renamed file.

### v0.2 Candidates

Commit history (`log(limit:path:)`), working tree status (`status()`), branch listing, branch create/switch, and arbitrary two-ref diff (`diff(from:to:)`). None of these belong in v0.1; add them when an app needs them.

## Documents

| Role | Document |
|---|---|
| Spec | [[DELEGIT_SPECIFICATION]] |
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

DeleGit is a platform-isolated package — it has no runtime dependencies on the Python-side Archivist feature set and no coupling to any other item on the roadmap. Affiliations will populate as Archivist Mobile features are added to the roadmap.