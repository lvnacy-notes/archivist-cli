---
class: index
category:
  - feature
  - infrastructure
affiliations:
  - centralized-db
created: 2026-05-18
modified: 2026-05-18
version:
status: blocked
related:
  - "[[CENTRALIZED_DB]]"
tags:
---
```dataviewjs
// PROGRESS BAR
// Update checklistPath to the vault-relative path of this feature's checklist.
// Remove this block entirely if no checklist exists yet.
const checklistPath = "ROADMAP/multi-vault-orchestration/CHECKLIST"; // ← UPDATE THIS
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

Cross-vault and cross-submodule coordination: distributing templates, syncing `AGENTS` files, and running operations across the full Apparatus without dropping into each module individually.

```toc
```

## Status

**Status:** `= this.status`

**Blocked on Centralized DB — do not spec or implement until [[CENTRALIZED_DB]] is shipped.**

The machine-level registry is the natural foundation for orchestration. A system that needs to coordinate across every managed repo first needs to know what every managed repo is. Design of this feature should follow stabilization of the centralized DB schema and query patterns, not precede it — the orchestration model will be shaped by what the registry actually exposes.

No spec exists yet. Return here to brainstorm once the centralized DB is in production and the query use cases are understood.

## Dashboard

```dataview
TASK
FROM "ROADMAP/multi-vault-orchestration/CHECKLIST"
WHERE !completed
SORT file.mtime DESC
```

## Description

The pain point is coordination at Apparatus scale. Currently, operations that touch multiple vaults or submodules — distributing updated templates, propagating `AGENTS` file changes, running a frontmatter command across every module — require dropping into each module individually. That is the problem this feature exists to solve.

The full scope is not yet defined. Spec work begins after Centralized DB ships.

## Documents

| Role | Document |
|---|---|
| Spec | |
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

Hard dependency on Centralized DB. The machine-level registry — knowing what every managed repo is, where it lives, and how it relates to the Apparatus hierarchy — is the prerequisite for any cross-vault coordination. This feature cannot be meaningfully specced until that foundation exists.