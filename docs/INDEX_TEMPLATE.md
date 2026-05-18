---
class: index
category:
affiliations:
created:
modified: 2026-05-17
version:
status: not-started
related:
tags:
---
```dataviewjs
// PROGRESS BAR
// Update checklistPath to the vault-relative path of this feature's checklist.
// Remove this block entirely if no checklist exists yet.
const checklistPath = "ROADMAP/FEATURE_FOLDER/CHECKLIST_FILE"; // ← UPDATE THIS
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

2–3 sentences. What this feature is and what problem it solves. Keep it tight — the full description lives below.
```toc
```
## Status

**Status:** `= this.status`

Current state, active blockers, open questions. Short-form prose — a few sentences at most.

## Dashboard

```dataview
TASK
FROM "ROADMAP/FEATURE_FOLDER/CHECKLIST_FILE"
WHERE !completed
SORT file.mtime DESC
```

## Description

Full feature description. Problem statement, scope, design rationale, known constraints, edge cases worth calling out. This section exists so the spec can focus on implementation — put the "what and why" here, leave the "how" for the spec.

## Documents

| Role | Document |
|---|---|
| Spec | |
| Checklist | |
| Testing | |

Add or remove rows as needed. There is no required set of documents for every feature.

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

Optionally, a brief prose note on each affiliation — what the dependency is, which direction it runs, and whether it's a hard blocker or a loose coupling.

---

## How to Use

### Progress Bar

The DataviewJS block at the top of the file queries a checklist for task completion and renders a native HTML progress element. Two things to update per instance:

1. Set `checklistPath` to the vault-relative path of the feature's checklist file, e.g. `"ROADMAP/centralized-db/CHECKLIST"`. No `.md` extension.
2. Remove the block entirely if no checklist exists yet — it fails gracefully, but a dead block is noise.

### Open Tasks

Standard Dataview `TASK` query. Update the `FROM` path to match the checklist file — same path format as above. Filters to incomplete tasks only, sorted by last modified. Swap `!completed` for `completed` to flip to a done list.

### Affiliated Features

The DataviewJS block reads the `affiliations` frontmatter field from this file and queries all `index`-class files under `ROADMAP/` for matches. Requirements:

- Values in `affiliations` must be plain strings in slug format, matching the feature's folder name exactly — lowercase, hyphens, no `.md`.
- The matched file must have `class: index` set in its frontmatter or it will not appear.

If a feature has no index file yet, it won't show up. That's intentional — the query reflects what actually exists.

---

## Taxonomy Quick Reference

```markdown
CLASS
-------------------
`index`     - Folder note and feature overview — orientation, not direction |
`spec`      - Specification — what something is and how it behaves |
`checklist` - Task list — implementation steps, tracks completion state |
`plan`      - Roadmap-level planning — directional, not yet specced |
`archive`   - Changelog — sealed record of a commit or release |

Category — Domain
-------------------
`feature` · `infrastructure` · `database` · `cli` · `git` · `frontmatter` · `changelog` · `mobile` · `testing` · `documentation`

Category — Role
-------------------
`planning` · `reference` · `decision`

Status
-------------------
`not-started` · `in-progress` · `blocked` · `shipped` · `abandoned`

Affiliations Format
-------------------
    ```yaml
    affiliations:
      - feature-folder-name
      - another-feature-folder-name
    ```

Plain strings, slug format, exact match to the feature's folder name under `ROADMAP/`. Not wikilinks.
```