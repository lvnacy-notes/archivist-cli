---
class: index
category:
  - feature
  - database
affiliations:
  - centralized-db
created:
modified: 2026-05-18
version:
status: blocked
related:
  - "[[GRAPH_SPECIFICATION]]"
tags:
---
```dataviewjs
// PROGRESS BAR
// Update checklistPath to the vault-relative path of this feature's checklist.
// Remove this block entirely if no checklist exists yet.
const checklistPath = "ROADMAP/graph/CHECKLIST"; // ← UPDATE THIS
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

A read layer over the file system that parses `[[wikilink]]` relationships across registered modules, persists the resulting graph structure in `registry.db`, and exports a self-contained interactive HTML visualization — the Cosmoscope — viewable in any browser, including mobile Safari, without requiring any additional runtime, server, or companion files.

```toc
```

## Status

**Status:** `= this.status`

Blocked on Centralized DB — `graph_nodes` and `graph_edges` live in `registry.db`, and the module registry infrastructure must exist before graph work can begin. Phase 1 covers module-level graphs only. Vault and apparatus aggregation are Phase 2; machine-wide scope is deferred indefinitely.

## Dashboard

```dataview
TASK
FROM "ROADMAP/graph/CHECKLIST"
WHERE !completed
SORT file.mtime DESC
```

## Description

The graph feature is a read layer. It does not modify notes, frontmatter, or any existing Archivist-managed data. It reads; it builds; it exports. That is the full extent of its ambitions.

The graph mirrors the module hierarchy already established in `registry.db` — there is no new hierarchy to learn. Nodes are `.md` files; edges are resolved wikilinks. Broken links are first-class data: unresolved targets are recorded with `resolved = 0` and `target_id = NULL`, reported in `archivist graph status` output, and rendered as dashed edges in the Cosmoscope. They are not silently dropped.

### Storage

Two tables are added to `registry.db`: `graph_nodes` and `graph_edges`, both keyed to `module_id`. Graph data lives alongside the module registry that already exists — no new database files. Apparatus databases remain focused on works catalog and changelog records; structural metadata about the note graph belongs in the registry.

Upserts are idempotent. Re-running `archivist graph build` on a module is always safe — existing rows are updated in place, edges for files that no longer exist are deleted, and the graph always reflects the current state of the file system after a build. `ON DELETE CASCADE` on `module_id` means clearing a module's graph data is a single delete.

### Commands

**`archivist graph build`** is the primary command — it does everything. Walks all `.md` files in the current module, extracts and resolves wikilinks, upserts into `registry.db`, and exports a Cosmoscope HTML file. Respects `.archivist/ignores` using the same pathspec logic already in place for frontmatter commands. `ARCHIVE/` is excluded by default; the default export path lives there at `ARCHIVE/graph/[module-name]-graph.html`.

**`archivist graph export`** re-renders the Cosmoscope from the current DB state without re-parsing the file system. Use this when DB entries have been corrected manually and a fresh export is needed without a full rebuild.

**`archivist graph status`** reads current graph data and prints a summary: node count, edge count, broken links, ambiguous links, and orphans — nodes with no edges in or out. Broken and ambiguous links are listed individually. Does not modify anything.

**`archivist graph clear`** deletes all graph data for the current module from `registry.db`. Destructive, prompts for confirmation, supports `--dry-run`. Does not delete the exported HTML.

All subcommands honour `--dry-run`. All subcommands that operate on a module resolve it via `git rev-parse --show-toplevel` — run from anywhere inside the repo.

### The Cosmoscope

A single self-contained `.html` file with no external dependencies. All JavaScript (D3.js v7), CSS, and graph data are inlined at export time. No CDN calls, no local server, no companion files. Works offline. Works on mobile Safari.

Node size is proportional to degree — more connections, larger node. Node color is determined by the `class` frontmatter field; nodes with no class use a neutral default. Broken link edges render as dashed lines terminating at a ghost node labeled with the raw link text. Clicking a node opens a sidebar with title, file path, class, tags, backlinks, and outgoing links.

Controls: real-time title search, filter by class, filter by tag, toggles for broken links and orphans, reset.

### Phase 2

Vault and apparatus aggregation use the same tables with a wider query — no schema changes required. Cross-module edges get a second resolution pass over the full apparatus node set and render as a distinct edge type. `archivist graph build --apparatus <name>` walks all modules in sequence and exports a single apparatus-wide Cosmoscope, with per-module HTML as a side effect. Machine-wide scope is deferred.

### Not in Phase 1

Incremental builds (full rebuild always), post-commit hook integration (graph rebuilds are explicit user-initiated operations), file opening from the browser (`archivist graph serve` is a Phase 2 investigation), and frontmatter field configurability (harvested fields are hardcoded in Phase 1).

## Documents

| Role | Document |
|---|---|
| Spec | [[GRAPH_SPECIFICATION]] |
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

Hard dependency on Centralized DB — `graph_nodes` and `graph_edges` are added to `registry.db`, and the module registry (`modules` table, `module_id` foreign key) must exist and be stable before graph implementation begins. Phase 2 apparatus-wide graphs additionally depend on the `module_bays` containment relationships being populated correctly.