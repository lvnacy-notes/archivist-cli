# Archivist — Graph Feature Specification

**Status:** Draft  
**Version:** 0.1.0  
**Authors:** LVNACY

-----

## 1. Overview

This document specifies the design and behavior of Archivist’s graph feature. The goal is a system that can parse `[[wikilink]]` relationships across registered modules, persist the resulting graph structure in the centralized registry, and export a self-contained interactive HTML visualization viewable in any browser — including mobile Safari — without requiring any additional runtime, server, or application.

The graph feature is a read layer over the file system. It does not modify notes, frontmatter, or any existing Archivist-managed data. It reads; it builds; it exports. That is the full extent of its ambitions.

-----

## 2. Conceptual Hierarchy

The graph mirrors the module hierarchy already established in `registry.db`. There is no new hierarchy to learn.

```
Machine
├── Global Registry (~/.archivist/registry.db)  ← graph tables live here
│
├── Apparatus A  (e.g. "writing")
│   ├── Vault 1
│   │   ├── Module: story       ← graph_nodes + graph_edges scoped to module_id
│   │   ├── Module: library
│   │   └── Module: publication
│   └── Vault 2
│       └── Module: library
│
└── Apparatus B  (e.g. "cyber")
    └── Vault 1
        └── Module: general
```

**Graph scopes:**

|Scope    |Description                                       |Phase   |
|---------|--------------------------------------------------|--------|
|Module   |All nodes and edges within a single git repo      |1       |
|Vault    |Aggregate of all modules belonging to a vault     |2       |
|Apparatus|Aggregate of all modules belonging to an apparatus|2       |
|Machine  |Every registered module on the machine            |deferred|

Phase 1 implements module-level graphs only. Phase 2 adds vault and apparatus aggregation using the same tables and a wider query.

-----

## 3. Storage

Graph data lives in `registry.db` alongside the module registry that already exists. No new database files are created.

```
~/.archivist/
├── registry.db     ← graph_nodes and graph_edges tables added here
├── writing.db
└── cyber.db
```

**Why `registry.db` and not the apparatus database?**

Graph nodes and edges are structural metadata about files within modules — they describe the shape of the note graph, not the content of works or changelogs. `registry.db` already has the `modules` table that provides the foreign key anchor for `module_id`. Apparatus databases stay focused on works catalog and changelog records. Concerns remain separated.

-----

## 4. Schema

Two tables are added to `registry.db`. Both are keyed to `module_id`, which references `modules.id` in the existing registry schema.

```sql
CREATE TABLE IF NOT EXISTS graph_nodes (
    id          INTEGER PRIMARY KEY,
    module_id   INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    file_path   TEXT NOT NULL,   -- absolute path to the .md file on disk
    title       TEXT,            -- from frontmatter `title` field; falls back to filename stem
    class       TEXT,            -- from frontmatter `class` field; NULL if absent
    tags        TEXT,            -- JSON array from frontmatter `tags`; NULL if absent
    created     TEXT,            -- from frontmatter `created` field; NULL if absent
    modified    TEXT,            -- from frontmatter `modified` field; NULL if absent
    UNIQUE (module_id, file_path)
);

CREATE TABLE IF NOT EXISTS graph_edges (
    id          INTEGER PRIMARY KEY,
    module_id   INTEGER NOT NULL REFERENCES modules(id) ON DELETE CASCADE,
    source_id   INTEGER NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
    target_id   INTEGER REFERENCES graph_nodes(id) ON DELETE SET NULL,
    link_text   TEXT NOT NULL,   -- raw wikilink text, e.g. "My Note" from [[My Note]]
    resolved    INTEGER NOT NULL DEFAULT 1,  -- 1 = target found; 0 = broken link
    UNIQUE (source_id, link_text)
);
```

**Design notes:**

- `target_id` is nullable. Unresolved links (where the target file does not exist in the module) store `target_id = NULL` and `resolved = 0`. Broken links are first-class data — they are reported, not silently dropped.
- `ON DELETE CASCADE` on `module_id` means `archivist graph clear --module <name>` can wipe a module’s graph data with a single delete against `modules` — no manual cleanup needed.
- `UNIQUE (module_id, file_path)` on nodes and `UNIQUE (source_id, link_text)` on edges make upserts safe and idempotent.
- `tags` is stored as a JSON array string. SQLite’s `json_each()` can be used for tag-based filtering in future query commands without a schema change.

-----

## 5. Wikilink Resolution

Obsidian wikilinks use the format `[[target]]` or `[[target|display text]]`. Archivist resolves these by filename, which is standard Obsidian behavior with no settings manipulation.

### 5.1 Link Formats

|Format           |Example               |Behaviour                                                        |
|-----------------|----------------------|-----------------------------------------------------------------|
|Basic            |`[[My Note]]`         |Match file named `My Note.md` anywhere in the module             |
|With display text|`[[My Note|See here]]`|Target is `My Note`; display text is ignored for resolution      |
|With heading     |`[[My Note#Section]]` |Target is `My Note`; heading fragment is stripped and discarded  |
|With path hint   |`[[folder/My Note]]`  |Target resolved by path; falls back to filename if path not found|

### 5.2 Resolution Logic

For each raw wikilink text extracted from a file:

1. Strip display text (`|...`) and heading fragment (`#...`) to get the bare target stem.
1. If the target contains a path separator, attempt an exact path match relative to the module root first.
1. If no path match, search all `.md` files in the module for a filename stem that case-insensitively matches the target stem.
1. If exactly one match is found: `resolved = 1`, `target_id` set to matching node’s `id`.
1. If zero matches are found: `resolved = 0`, `target_id = NULL`. The link is recorded as broken.
1. If multiple matches are found (ambiguous): `resolved = 0`, `target_id = NULL`. The link is flagged as ambiguous. Ambiguous and broken links are reported separately in `archivist graph status` output.

### 5.3 Cross-Module Links

Phase 1 does not resolve cross-module links. A wikilink pointing to a file in a different module is treated as an unresolved link for that module’s graph. Cross-module edge resolution is a Phase 2 concern.

-----

## 6. Commands

```
archivist graph <subcommand> [options]
```

All subcommands respect `--dry-run` where writes are involved. All subcommands that operate on a specific module resolve the module via `git rev-parse --show-toplevel` by default — run from anywhere inside the repo.

-----

### `archivist graph build`

Parses all `.md` files in the current module, extracts wikilinks, resolves them, upserts the results into `graph_nodes` and `graph_edges` in `registry.db`, and exports a Cosmoscope HTML file.

This is the primary command. It does everything.

```bash
# Run from anywhere inside a registered module
archivist graph build

# Export HTML to a specific location
archivist graph build --output ~/Desktop/my-module-graph.html

# Parse and update DB only; skip HTML export
archivist graph build --no-export

# Preview what would be parsed; write nothing
archivist graph build --dry-run
```

**Build pipeline:**

```
1. Resolve module root via git rev-parse
2. Confirm module is registered in registry.db — exit with error if not
3. Walk all .md files in the module root, respecting .archivist ignores
4. For each file:
     a. Parse frontmatter → populate node fields
     b. Regex pass over body content → extract [[wikilinks]]
     c. Upsert graph_nodes row
5. Resolution pass: for each extracted link, resolve to node id or NULL
6. Upsert graph_edges rows
7. Print build summary (node count, edge count, broken links, orphans)
8. Export Cosmoscope HTML (unless --no-export)
```

**Ignored files:**

Files and directories listed in `.archivist/ignores` are excluded from the graph walk, using the same pathspec logic already in place for `archivist frontmatter` commands. `ARCHIVE/` is ignored by default.

**Upsert behaviour:**

Re-running `archivist graph build` on a module is safe and idempotent. Existing rows are updated in place. Edges for files that no longer exist are deleted. The graph always reflects the current state of the file system after a build.

**Default export path:**

If `--output` is not specified, the HTML file is written to `ARCHIVE/graph/[module-name]-graph.html` within the module root. The `ARCHIVE/graph/` directory is created if it does not exist. This path is excluded from the graph walk automatically.

-----

### `archivist graph export`

Re-renders the Cosmoscope HTML from the current state of `registry.db` without re-parsing the file system. Use this when you have corrected DB entries directly and want a fresh export without a full rebuild.

```bash
archivist graph export
archivist graph export --output ~/Desktop/my-module-graph.html
```

Exits with an error if no graph data exists for the current module. Run `archivist graph build` first.

-----

### `archivist graph status`

Reads the current graph data for the module from `registry.db` and prints a summary. Does not modify anything.

```bash
archivist graph status
```

**Output:**

```
Graph status: my-module  (last built: 2025-11-04 14:32)

  Nodes          247
  Edges          891
  Broken links    12   ← target file not found
  Ambiguous        2   ← multiple files match target stem
  Orphans         18   ← nodes with no edges in or out

Broken links:
  notes/chapter-3.md → [[The Missing Scene]]
  characters/vera.md → [[Antagonist Profile]]
  ...

Run `archivist graph build` to rebuild from disk.
```

The Archivist voice applies here. She finds your orphaned notes deeply unsurprising.

-----

### `archivist graph clear`

Deletes all graph data for the current module from `registry.db`. Does not delete the exported HTML file.

```bash
archivist graph clear
archivist graph clear --dry-run
```

Prompts for confirmation before deleting. `--dry-run` prints what would be deleted without touching the database. This is a destructive operation — the next `archivist graph build` will reconstruct from disk, but any manual DB corrections will be lost.

-----

## 7. HTML Export — The Cosmoscope

The Cosmoscope is a single self-contained `.html` file. It has no external dependencies — no CDN calls, no local server, no companion files. It opens in any browser. It works offline. It works on mobile Safari.

All JavaScript, CSS, and graph data are inlined at export time.

### 7.1 Graph Rendering

The force-directed graph is rendered using **D3.js v7**, inlined into the HTML at export. No other JavaScript library is required.

Node and edge data are serialized as a JSON literal embedded in a `<script>` block:

```javascript
const GRAPH_DATA = {
  nodes: [
    { id: 1, title: "My Note", file_path: "/abs/path/to/note.md", class: "character", tags: ["draft"], degree: 4 },
    ...
  ],
  links: [
    { source: 1, target: 2, resolved: true },
    { source: 3, target: null, resolved: false },
    ...
  ]
};
```

`degree` is computed at export time as the total count of incoming and outgoing edges for each node. It drives node sizing.

### 7.2 Visual Design

|Element          |Behaviour                                                                                                                                                |
|-----------------|---------------------------------------------------------------------------------------------------------------------------------------------------------|
|Node size        |Proportional to degree — more connections, larger node                                                                                                   |
|Node color       |Determined by `class` frontmatter field. Nodes with no `class` use a default neutral color. Color mapping is consistent per export.                      |
|Broken link edges|Rendered as dashed lines in a distinct muted color. Unresolved targets have no node — the edge terminates at a ghost node labeled with the raw link text.|
|Orphan nodes     |Rendered at full opacity; distinguished only by their isolation in the layout. Not hidden.                                                               |
|Selected node    |Highlighted with a distinct accent color. Connected nodes and edges are brought forward; unconnected nodes are dimmed.                                   |

### 7.3 Sidebar

Clicking a node opens a sidebar panel showing:

- **Title** — from frontmatter, or filename stem if absent
- **File path** — absolute path, displayed in full, copyable
- **Class** — if present
- **Tags** — if present
- **Backlinks** — list of nodes that link to this node, clickable to navigate to that node in the graph
- **Outgoing links** — list of nodes this node links to, clickable

The sidebar does not attempt to open files. File path is displayed so the user can locate the file manually or on another device. This is a Phase 1 constraint — see §9 for Phase 2 file-opening behaviour.

### 7.4 Controls

|Control          |Behaviour                                                          |
|-----------------|-------------------------------------------------------------------|
|Search bar       |Filters nodes by title in real time; non-matching nodes are dimmed |
|Filter by class  |Checkbox list; unchecking a class hides those nodes and their edges|
|Filter by tag    |Same pattern as class filtering                                    |
|Show broken links|Toggle; hides or shows ghost nodes and dashed edges                |
|Show orphans     |Toggle; hides or shows isolated nodes                              |
|Reset            |Returns graph to default state                                     |

### 7.5 Cosmoscope Metadata Block

The exported HTML includes a metadata comment block at the top for identification:

```html
<!--
  Archivist Cosmoscope
  Module:     my-module
  Apparatus:  writing
  Built:      2025-11-04T14:32:00
  Nodes:      247
  Edges:      891
-->
```

-----

## 8. Integration with Existing Archivist Systems

### 8.1 `.archivist/ignores`

The graph walk respects `ignores` in `.archivist/config.yaml` using the same pathspec logic already used by `archivist frontmatter` commands. `ARCHIVE/**` is effectively ignored by default since the export path lives there and the directory is typically listed in ignores already.

### 8.2 `archivist init`

`archivist init` does not change. Graph tables are created in `registry.db` on first `archivist graph build` run, not at init time. Schema migrations are handled by Archivist’s existing pattern of `CREATE TABLE IF NOT EXISTS`.

### 8.3 Post-Commit Hook

`archivist graph build` is not called automatically by the post-commit hook in Phase 1. Graph rebuilds are explicit, user-initiated operations. The post-commit hook’s existing commit summary output may optionally append a reminder if graph data is stale (last build older than last commit), but this is cosmetic and does not block the commit.

### 8.4 `--dry-run` Contract

All graph subcommands honour `--dry-run`. No files are written and no database rows are modified. Output describes what would happen.

-----

## 9. Phase 2 — Apparatus-Wide Graphs

Phase 2 is not specced in detail here. The following notes record design decisions made during Phase 1 that Phase 2 depends on.

**Wider query, same tables.** An apparatus-wide graph is a query over `graph_nodes WHERE module_id IN (SELECT id FROM modules WHERE apparatus_id = ?)`. No schema changes required.

**Cross-module edge resolution.** A `[[wikilink]]` in one module that resolves to a file in another module within the same apparatus becomes a cross-module edge. These require a second resolution pass over the full apparatus node set. Phase 1 marks these as broken; Phase 2 resolves them properly and renders them as a distinct edge type in the Cosmoscope.

**Apparatus registry.** `archivist graph build --apparatus <name>` walks all modules registered to the named apparatus in sequence, rebuilds each module’s node/edge data, then exports a single apparatus-wide Cosmoscope. The per-module HTML exports are generated as a side effect.

**Zoom behaviour.** The apparatus-wide Cosmoscope groups nodes by module. Clicking a module cluster zooms into a module-scoped view. The per-module HTML exports are linked from the apparatus view as an escape hatch for deep navigation — clicking a module’s label opens its standalone Cosmoscope.

**File opening.** Phase 2 will investigate a lightweight local HTTP server approach (`archivist graph serve`) that enables file-open requests from the browser by routing them through `subprocess.run(["open", filepath])` on the server side. This is a desktop-only feature and does not affect the mobile HTML export.

-----

## 10. Open Questions and Deferred Decisions

**Incremental builds.** Phase 1 always does a full rebuild of the module graph. An incremental mode — parsing only files modified since the last build, using `modified` timestamps or git diff — is a natural optimisation for large modules but is deferred until the full rebuild proves too slow in practice.

**Frontmatter field configurability.** The fields harvested into `graph_nodes` (`title`, `class`, `tags`, `created`, `modified`) are hardcoded in Phase 1. A future config option in `.archivist/config.yaml` could allow users to specify which frontmatter fields map to which node attributes, enabling domain-specific coloring and filtering beyond `class`.

**Graph data staleness indicator.** The Cosmoscope could display the build timestamp prominently and warn when the HTML was generated from a build older than N days. Not specced; trivially implementable at export time.

**`archivist graph diff`.** A command that compares the current file system state to the last build and reports what has changed — new nodes, deleted nodes, new edges, broken links introduced — without doing a full rebuild. Useful for large modules. Deferred.

**Tag-based subgraph export.** `archivist graph export --tag cosmic-horror` would export a Cosmoscope containing only nodes carrying that tag and their immediate neighbors. Not specced; the data model supports it without schema changes.

**Plugin system extension.** The `.archivist/changelog.py` plugin convention could extend to `graph.py` — a per-project plugin that customizes node coloring, filtering defaults, or sidebar content. Deferred until the base implementation is stable.

-----

*This document is a living spec. It will be revised as implementation surfaces decisions that were not anticipated here. That is not a failure of the spec — it is the spec doing its job.*