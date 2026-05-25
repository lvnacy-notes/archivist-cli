---
class: index
category:
  - feature
  - infrastructure
affiliations:
created: 2026-05-21
modified: 2026-05-23
version:
status: not-started
related:
  - "[[APPARATUS_PLATFORM_SPEC]]"
  - "[[AP_PHASE_1_IMPLEMENTATION]]"
  - "[[AP_PHASE_2_IMPLEMENTATION]]"
tags:
---

```dataviewjs
// PHASE 1 PROGRESS
const p1Path = "ROADMAP/APPARATUS_PLATFORM/AP_PHASE_1_IMPLEMENTATION"; // ← UPDATE
const p1Tasks = dv.page(p1Path)?.file?.tasks;
if (!p1Tasks || p1Tasks.length === 0) {
    dv.paragraph("_Phase 1 checklist not linked. Update `p1Path` to render progress._");
} else {
    const completed = p1Tasks.filter(t => t.completed).length;
    const total = p1Tasks.length;
    const pct = Math.round((completed / total) * 100);
    dv.el("p", "**Phase 1 — Registry and Git Integration**", {});
    dv.el("progress", "", {
        attr: { value: completed, max: total, style: "width: 100%; margin-bottom: 0.25em;" }
    });
    dv.el("p", `${completed} / ${total} tasks complete — ${pct}%`, {
        attr: { style: "margin: 0; font-size: 0.85em; color: var(--text-muted);" }
    });
}
```
```dataviewjs
// PHASE 2 PROGRESS
const p2Path = "ROADMAP/APPARATUS_PLATFORM/AP_PHASE_2_IMPLEMENTATION"; // ← UPDATE
const p2Tasks = dv.page(p2Path)?.file?.tasks;
if (!p2Tasks || p2Tasks.length === 0) {
    dv.paragraph("_Phase 2 checklist not linked. Update `p2Path` to render progress._");
} else {
    const completed = p2Tasks.filter(t => t.completed).length;
    const total = p2Tasks.length;
    const pct = Math.round((completed / total) * 100);
    dv.el("p", "**Phase 2 — Multi-Vault Orchestration**", {});
    dv.el("progress", "", {
        attr: { value: completed, max: total, style: "width: 100%; margin-bottom: 0.25em;" }
    });
    dv.el("p", `${completed} / ${total} tasks complete — ${pct}%`, {
        attr: { style: "margin: 0; font-size: 0.85em; color: var(--text-muted);" }
    });
}
```

## Overview

Archivist currently knows only about the repository it's running in. The Apparatus Platform gives it machine-level awareness of every registered module, vault, and apparatus — a persistent registry that enables cross-module status reporting, file distribution, and frontmatter operations across an entire writing project. It ships in two phases: Phase 1 builds the `~/.archivist/` registry infrastructure and the git-integrated module lifecycle commands (`add`, `deinit`, and augmented `init`); Phase 2 adds the multi-vault orchestration tooling (`muster`, `distribute`, `broadcast`) that reads from and operates across that registry.

```toc
```

---

## Status

**Status:** `= this.status`

Not started. All three specification documents are complete and authoritative. The three prior specs this work supersedes (`CENTRALIZED_DATABASE_SPEC`, `MULTI_VAULT_ORCHESTRATION_SPEC`, `GIT_INTEGRATION_SPEC`) are archived — this spec replaces them.

Phase 1 is the hard prerequisite for Phase 2. The Phase 1 completion gate (all tests passing, manual smoke test clean) must be satisfied before Phase 2 implementation begins.

Open questions that do not block implementation but must not be foreclosed by design choices:

- `archivist restore` — deferred; constraints documented in spec §5.6
- Automated registry commit and push — deferred; manual management for now
- Decentralized registry architecture — tabled; centralized `~/.archivist/` is the current design

---

## Dashboard

**Phase 1 — Open Tasks**

```dataview
TASK
FROM
	#apparatus-platform AND
	#phase-1 
WHERE !completed
SORT file.mtime DESC
```

**Phase 2 — Open Tasks**

```dataview
TASK
FROM
	#apparatus-platform AND
	#phase-2 
WHERE !completed
SORT file.mtime DESC
```

---

## Description

Archivist manages individual git repositories — one repo, one vault, one library, one story. That's been sufficient. But an Apparatus spans multiple repositories, and right now Archivist has no way to reason about them together. There's no registry of what exists, no understanding of containment (which modules live inside which vault), no way to run a frontmatter operation across a set of modules, no way to restore the entire Apparatus on a new machine.

The Apparatus Platform is the infrastructure layer that fixes this.

**What it builds:**

A machine-level registry at `~/.archivist/` — a version-controlled git repository containing `registry.db` (global: apparatuses, modules, containment relationships) and per-apparatus databases (works catalogs, cross-module changelog aggregation). Every Archivist-managed repository registers itself with this registry. The registry knows where everything is on disk, what type each module is, which vaults contain which modules, and when each module last synced.

On top of that registry, three orchestration commands: `muster` (status table across registered modules), `distribute` (copy a file into multiple modules at once), and `broadcast` (run a frontmatter subcommand across multiple modules at once).

**Design constraints worth knowing:**

The registry is intentionally centralized at the machine level. A decentralized alternative — distributing the registry data across vault repos — was explored at length and tabled due to unresolved complications around binary SQLite files in git, machine-specific path storage, and cross-vault synchronization consistency. The design does not foreclose future decentralization: `get_registry_dir()` is the single source of the `~/.archivist/` path, and relocating storage is a change to that one function.

Per-project databases (`ARCHIVE/archive.db`) are unaffected. The centralized layer aggregates data those databases produce; it does not replace them.

Any module type can serve as a git superproject — vaults are the expected container, not the only permitted one. `module_bays` records containment between any two registered modules regardless of type.

**Two implementation phases with a hard gate between them:**

Phase 1 ships the registry and the lifecycle commands. Phase 2 cannot begin until Phase 1 is committed, tested, and stable. The Phase 2 commands depend on a correct, populated registry — implementing them against an unfinished Phase 1 produces untestable code built on shifting ground.

---

## Documents

| Role | Document |
|---|---|
| Spec | [[APPARATUS_PLATFORM_SPEC]] |
| Phase 1 Checklist | [[APPARATUS_PLATFORM_IMPLEMENTATION_PHASE_1]] |
| Phase 2 Checklist | [[APPARATUS_PLATFORM_IMPLEMENTATION_PHASE_2]] |

---

## Affiliated Features

```dataviewjs
const affiliations = dv.current().affiliations ?? [];
if (affiliations.length === 0) {
    dv.paragraph("_No affiliations set._");
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

The three affiliated features are the prior specs this work supersedes. They are archived, not active. The affiliation is contextual — their folders contain historical design notes and the original scoped specs that informed this unified document. `centralized-db` in particular contains the brainstorming around the decentralized registry question.