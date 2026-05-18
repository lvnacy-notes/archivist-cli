---
class: index
category:
  - feature
  - cli
  - changelog
affiliations:
created:
modified: 2026-05-18
version:
status: in-progress
related:
  - "[[PLUGIN_SYSTEM_FEATURE_CHECKLIST]]"
  - "[[PLUGIN_SYSTEM_SPECIFICATION]]"
tags:
---
```dataviewjs
// PROGRESS BAR
// Update checklistPath to the vault-relative path of this feature's checklist.
// Remove this block entirely if no checklist exists yet.
const checklistPath = "ROADMAP/plugin-system/PLUGIN_SYSTEM_FEATURE_CHECKLIST"; // ← UPDATE THIS
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

A convention-based plugin system allowing per-project overrides of Archivist commands. File existence is registration — drop `.archivist/changelog.py` into a project and it runs instead of the built-in changelog command. No config changes, no flags, instant activation and deactivation.

```toc
```

## Status

**Status:** `= this.status`

Partially shipped. The plugin system works for `archivist changelog` and has been confirmed functional — the discovery logic, contract, and activation/deactivation flow are all in place. Scope is currently limited to that one command while the pattern is observed in practice and the extension path to `archivist manifest` is worked out.

The extension convention is already designed: Archivist looks for `.archivist/<command>.py`, loads it if present, falls back to built-in if not. Wiring this up for `manifest` (and eventually `reclassify`) is the outstanding work. Nothing is blocked — this is an active investigation.

## Dashboard

```dataview
TASK
FROM "ROADMAP/plugin-system/PLUGIN_SYSTEM_FEATURE_CHECKLIST"
WHERE !completed
SORT file.mtime DESC
```

## Description

The plugin system is built on a single deliberate convention: **the file's existence is the registration**. There is no plugin registry, no config key to flip, no command to run. Place `.archivist/changelog.py` in a project and Archivist loads it on the next `archivist changelog` invocation. Remove or rename it and the built-in takes over immediately.

### Discovery and Routing

Archivist checks for `.archivist/changelog.py` on every `archivist changelog` invocation. If found, the plugin's `run(args)` is called and the built-in subcommand is bypassed entirely. Explicit subcommands always bypass the plugin — `archivist changelog library` runs the built-in library subcommand regardless. The plugin is only active for bare `archivist changelog` invocations.

`sample-changelog.py` is a reference file that ships with every initialized project. It is never loaded. Only `changelog.py` is recognized. The distinction is exact and intentional.

### The Contract

A plugin is a Python file exposing one callable:

```python
def run(args: argparse.Namespace) -> None:
    ...
```

That function calls `run_changelog()` from `changelog_base` with builder callables. Everything else is up to the plugin author. The library module exposes four public functions for composition:

```python
from archivist.commands.changelog.library import (
    analyse_catalog,   # post_changes hook — populates ctx.data
    build_frontmatter, # YAML frontmatter block
    build_body,        # full changelog body including sentinel
    print_summary,     # terminal summary after write
)
```

Anything prefixed with `_` in the library module is internal and will change without notice.

### Activation, Deactivation, Testing

Activate by renaming `sample-changelog.py` → `changelog.py` and editing it. Deactivate by deleting or renaming it back. Test with `archivist changelog --dry-run` — the full pipeline runs including the plugin, and the indicator line confirms which path ran:

```
→ changelog plugin found: .archivist/changelog.py
```

### Extension to Other Commands

The convention is designed to extend to `manifest`, `reclassify`, and any other command using identical discovery logic. Not yet implemented beyond `changelog`. See Status above.

## Documents

| Role | Document |
|---|---|
| Spec | [[PLUGIN_SYSTEM]] |
| Checklist | [[PLUGIN_SYSTEM_FEATURE_CHECKLIST]] |

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

The plugin system is cross-cutting by design — it touches any command that adopts the convention. Affiliations will populate as extension work to `manifest` and other commands gets specced and added to the roadmap.