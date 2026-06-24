---
class: index
category:
  - feature
  - infrastructure
affiliations:
created: 2026-05-21
modified: 2026-05-23
version:
status: in-progress
related:
  - "[[APPARATUS_PLATFORM_SPEC]]"
  - "[[AP_PHASE_1_IMPLEMENTATION]]"
  - "[[AP_PHASE_2_IMPLEMENTATION]]"
  - "[[AP_PHASE_3_IMPLEMENTATION]]"
tags:
---

```dataviewjs
// PHASE 1 PROGRESS
const p1Path = "ROADMAP/APPARATUS_PLATFORM/AP_PHASE_1_IMPLEMENTATION";
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
const p2Path = "ROADMAP/APPARATUS_PLATFORM/AP_PHASE_2_IMPLEMENTATION";
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
```dataviewjs
// PHASE 3 PROGRESS
const p3Path = "ROADMAP/APPARATUS_PLATFORM/AP_PHASE_3_IMPLEMENTATION";
const p3Tasks = dv.page(p3Path)?.file?.tasks;
if (!p3Tasks || p3Tasks.length === 0) {
    dv.paragraph("_Phase 3 checklist not linked. Update `p3Path` to render progress._");
} else {
    const completed = p3Tasks.filter(t => t.completed).length;
    const total = p3Tasks.length;
    const pct = Math.round((completed / total) * 100);
    dv.el("p", "**Phase 3 — Registry Maintenance (`archivist remedy`)**", {});
    dv.el("progress", "", {
        attr: { value: completed, max: total, style: "width: 100%; margin-bottom: 0.25em;" }
    });
    dv.el("p", `${completed} / ${total} tasks complete — ${pct}%`, {
        attr: { style: "margin: 0; font-size: 0.85em; color: var(--text-muted);" }
    });
}
```

## Overview

Archivist currently knows only about the repository it's running in. The Apparatus Platform gives it machine-level awareness of every registered module, vault, and apparatus — a persistent registry that enables cross-module status reporting, file distribution, and frontmatter operations across an entire writing project. It ships in three phases: Phase 1 builds the `~/.archivist/` registry infrastructure and the git-integrated module lifecycle commands (`add`, `deinit`, and augmented `init`); Phase 2 adds the multi-vault orchestration tooling (`census`, `distribute`, `broadcast`) that reads from and operates across that registry; Phase 3 adds `archivist remedy`, the maintenance suite for keeping the registry and per-module configs consistent without touching SQL directly.

```toc
```

---

## Status

**Status:** `= this.status`

Phase 1 is functionally complete. Remedial work is in progress to correct a schema assumption — see §Remedial Work below. Phase 2 and Phase 3 have not begun. The gate is strict: Phase 2 does not open until Phase 1 is committed, tested, and the remedial work is complete.

The three specs this work supersedes (`CENTRALIZED_DATABASE_SPEC`, `MULTI_VAULT_ORCHESTRATION_SPEC`, `GIT_INTEGRATION_SPEC`) are archived. `APPARATUS_PLATFORM_SPEC` is the authoritative document.

### Remedial Work

Phase 1 was implemented with `apparatus_uuid TEXT NOT NULL` on the `modules` table — a one-to-many constraint that allowed each module exactly one apparatus. Before Phase 2 begins, this constraint was found to be wrong: a module shared between two writing corpuses legitimately belongs to multiple apparati simultaneously.

The fix mirrors the existing `module_bays` pattern for vault containment: a junction table. `module_apparatus` is to apparatus membership what `module_bays` is to structural containment. The column is removed; the table is added; the public surface gains four new functions; `ConfigSchema` gains a new plural key (`apparati: list[str]`); the existing lean registry is nuked and rebuilt.

See `AP_PHASE_1_IMPLEMENTATION §13` for the complete task list. See `APPARATUS_PLATFORM_SPEC §4` for the corrected schema.

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

**Phase 3 — Open Tasks**

```dataview
TASK
FROM
	#apparatus-platform AND
	#phase-3
WHERE !completed
SORT file.mtime DESC
```

---

## Description

### The Problem

Archivist manages individual git repositories well. One repo, one vault, one library, one story. That scope has always been enough for the core changelog and frontmatter operations. But an Apparatus — the full graph of modules that constitute any given project — spans multiple repositories. Archivist has had no way to reason about them together: no registry of what exists, no understanding of containment, no way to run a frontmatter operation across an entire corpus, no way to restore the whole thing on a new machine.

The Apparatus Platform is the infrastructure layer that fixes this.

### What It Builds

**A machine-level registry at `~/.archivist/`.** A version-controlled git repository containing `registry.db` (global: apparati, modules, containment relationships, apparatus membership) and per-apparatus databases (works catalogs, cross-module changelog aggregation). Every Archivist-managed repository registers itself with this registry. The registry knows where everything is on disk, what type each module is, which vaults contain which modules, which apparati each module belongs to, and when each module last synced.

On top of that registry, three orchestration commands: `census` (status table across registered modules), `distribute` (copy a file into multiple modules at once), `broadcast` (run a frontmatter subcommand across multiple modules at once).

And on top of those, `archivist remedy`: a full maintenance suite for keeping the registry and configs in sync — surgical field updates, apparatus membership management, orphan detection, apparatus renaming, module transfer between vaults.

### Key Design Decisions

**Centralized registry.** `~/.archivist/` is a machine-level singleton. A decentralized alternative — distributing registry data across vault repos — was explored and tabled. The blockers: SQLite is binary and unmerge-able by git; local paths are machine-specific; restoration requires knowing WHERE to put things, which a decentralized structure can't answer without a local path graph. The design does not foreclose future decentralization — `get_registry_dir()` is the single source of the storage path and changing it is a one-function change.

**Any module type can be a superproject.** Vaults are the expected container, not the only permitted one. `module_bays` records containment between any two registered modules regardless of type.

**Apparatus membership is many-to-many.** A module may belong to multiple apparati. This was discovered after Phase 1 was implemented — a shared library feeding two independent writing corpuses is a real and valid arrangement. The `module_apparatus` junction table handles this. The plural form is `apparati`; this is canonical and intentional.

**Two independent dimensions.** A module's apparatus memberships (logical grouping for catalog and changelog aggregation) and its vault containment (structural git superproject relationships) are separate concerns tracked separately. Changing one does not affect the other. `deinit` from a vault removes the bay row; `deinit` in standalone mode removes the apparatus memberships. These are not the same operation.

**Per-project databases are unchanged.** `ARCHIVE/archive.db` and the seal pipeline are unaffected. The centralized layer aggregates what those databases produce; it does not replace them.

### The Three Phases

**Phase 1 — Registry and Git Integration**

The registry infrastructure itself: `~/.archivist/`, `registry.db`, the apparatus DB schema, all the lifecycle functions in `archivist/utils/registry.py`. Two new commands (`archivist add`, `archivist deinit`) that manage module registration and git operations together with the correct operation order (Apparatus first, git second — always). Augmentations to `archivist init` and `archivist migrate` for registering existing and new modules. A pre-commit hook augmentation that syncs module path and timestamp on every commit. The `ConfigSchema` TypedDict that gives the config file a proper type contract for the first time.

Phase 1 is the foundation that everything else depends on. It ships as a unit. Nothing in it is optional.

**Phase 2 — Multi-Vault Orchestration**

Three read and fan-out commands built on the Phase 1 registry: `archivist census` for a cross-module status table scoped by apparatus, vault, type, or specific modules; `archivist distribute` for copying a file into multiple module directories at once; `archivist broadcast` for running a `frontmatter` subcommand across all modules in scope. All three require explicit scope — no implicit "all modules" default exists.

Phase 2 cannot begin until Phase 1 is complete and stable.

**Phase 3 — Registry Maintenance (`archivist remedy`)**

The maintenance layer for keeping the registry and per-module configs in sync without opening a SQLite shell. Covers: config-driven sync (`remedy sync`), surgical single-field updates (`remedy set`), apparatus membership management (`remedy join-apparatus`, `remedy leave-apparatus`), module transfer between vaults (`remedy transfer`), decimated module reactivation (`remedy reactivate`), full registry state inspection (`remedy inspect`), orphan detection (`remedy orphans`), apparatus rename (`remedy rename-apparatus`), and apparatus deletion (`remedy obliterate-apparatus`).

Phase 3 cannot begin until Phase 2 is complete and stable.

---

## Documents

| Role | Document |
|---|---|
| Spec (v3) | [[APPARATUS_PLATFORM_SPEC]] |
| Phase 1 Checklist | [[AP_PHASE_1_IMPLEMENTATION]] |
| Phase 2 Checklist | [[AP_PHASE_2_IMPLEMENTATION]] |
| Phase 3 Checklist | [[AP_PHASE_3_IMPLEMENTATION]] |

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

The three affiliated features are the prior specs this work supersedes. They are archived, not active. `centralized-db` in particular contains the historical brainstorming around the decentralized registry question that informed the decision to go centralized.