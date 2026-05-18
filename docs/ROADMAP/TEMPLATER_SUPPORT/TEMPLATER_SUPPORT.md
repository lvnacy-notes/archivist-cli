---
class: index
category:
  - feature
  - frontmatter
  - cli
affiliations:
created: 2026-05-18
modified: 2026-05-18
version:
status: shipped
related:
  - "[[TEMPLATER_SUPPORT_PLAN]]"
  - "[[TEMPLATER_IMPLEMENTATION_STATUS]]"
tags:
  - templater
---
```dataviewjs
// PROGRESS BAR
// Update checklistPath to the vault-relative path of this feature's checklist.
// Remove this block entirely if no checklist exists yet.
const checklistPath = "ROADMAP/templater-support/CHECKLIST"; // ← UPDATE THIS
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

Archivist handles `<% %>` Templater expressions in frontmatter safely across all four frontmatter commands (`add`, `remove`, `rename`, `apply-template`). Three modes: `resolve` (Python reimplementation of the `tp.date`, `tp.file`, and `tp.frontmatter` API surface), `preserve` (safe round-trip without resolution), and `false` (zero handling). Phases 0–3 are shipped and tested. Phase 4 is deferred.

```toc
```

## Status

**Status:** `= this.status`

Phases 0, 1, 2, and 3 are complete, tested, and shipped. No mandatory dependencies were added. Phase 4 — the optional `dukpy` fallback evaluator for expressions the regex parser can't handle — is explicitly deferred and not blocking.

Four follow-up items are holding the door open on this feature:

1. **Phase 4 — dukpy fallback.** If users encounter unresolvable expressions in practice, add `dukpy` as an optional dependency behind `[templater-extended]`. Not worth adding speculatively. The graceful degradation contract (leave verbatim, emit warning) covers the gap.

2. **Multi-pass resolution.** Currently single-pass. Chained `tp.frontmatter` cross-references — where a property is itself an expression — won't resolve recursively. No users have hit this yet; revisit if they do.

3. **Delimiter configuration.** Archivist always assumes `<% %>`. Templater allows custom delimiters, configurable in Obsidian settings. If this becomes a real pain point, read `.obsidian/plugins/templater-obsidian/data.json` to detect the configured delimiters at runtime.

4. **Batch unresolved reporting.** Unresolved expressions currently warn at resolution time. For large batch operations, a `.archivist-unresolved` report file would be more useful. The implementation path is specced in [[TEMPLATER_IMPLEMENTATION_STATUS]] — deferred until users ask for it.

## Dashboard

```dataview
TASK
FROM "ROADMAP/templater-support/CHECKLIST"
WHERE !completed
SORT file.mtime DESC
```

## Description

Obsidian's Templater plugin allows users to embed dynamic expressions in note frontmatter — `<% tp.date.now("YYYY-MM-DD") %>`, `<% tp.file.title %>`, and so on. Outside Obsidian, where Archivist operates, these expressions are opaque strings. Without handling them, Archivist's frontmatter commands would corrupt or mangle expressions during YAML parsing and reordering.

The solution is a mask/restore cycle: before any frontmatter operation, `<% %>` blocks are replaced with stable sentinel tokens (`__ARCHIVIST_TMPL_0__`, etc.) that survive YAML parsing, reordering, and merging. After the operation, sentinels are restored to either the original expressions (`preserve` mode) or resolved values (`resolve` mode). The sentinel strategy is the foundation everything else is built on.

### Modes

**`preserve`** — The default. Detects and round-trips expressions verbatim through every frontmatter operation. Resolution is left to Obsidian. Zero new dependencies, zero corruption risk.

**`resolve`** — Archivist attempts to evaluate expressions at write time using its own Python implementation. Expressions it cannot resolve are left verbatim with a warning — graceful degradation is non-negotiable. No Node.js, no Obsidian required.

**`false`** — No handling. Treat `<% %>` as plain strings. Use this when the project has no Templater expressions.

Mode is set during `archivist init` and stored in `.archivist/config.yaml`.

### Resolution Engine

Implemented namespaces:

**`tp.date`** — `now()`, `today()`, `tomorrow()`, `yesterday()`, `weekday()`. Moment.js format strings are translated to Python `strftime` tokens via a hand-rolled mapping table covering ~95% of real-world usage. No `arrow` dependency.

**`tp.file`** — `title`, `path()`, `folder()`, `creation_date()`, `last_modified_date()`, `content`. All derived from the file path and `os.stat` at resolution time; no Obsidian API required. Resolves against the target note's context, not the template file's context — a critical detail in `apply-template`.

**`tp.frontmatter`** — Subscript access (`tp.frontmatter["key"]`) for self-referential computed fields. Single-pass resolution; chained cross-references are a known limitation.

Argument parsing uses `ast.literal_eval` — no `eval()` of arbitrary code, no arbitrary execution.

### Explicitly Not Implemented

`tp.system` (interactive prompts), `tp.user` (user-defined JS scripts), `tp.obsidian` (Obsidian API), complex control flow in property values. Expressions using these namespaces are left verbatim with a warning. This is the correct behavior.

## Documents

| Role | Document |
|---|---|
| Plan | [[TEMPLATER_SUPPORT_PLAN]] |
| Implementation Status | [[TEMPLATER_IMPLEMENTATION_STATUS]] |

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

Templater support is cross-cutting — it touches all four frontmatter commands (`add`, `remove`, `rename`, `apply-template`) — but has no hard runtime dependencies on other roadmap features. Affiliations will populate if downstream features emerge that depend on expression resolution behavior.