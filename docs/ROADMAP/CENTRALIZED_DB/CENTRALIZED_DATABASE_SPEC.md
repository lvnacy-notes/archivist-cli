---
class: spec
category:
  - feature
  - database
  - infrastructure
affiliations:
modified: 2026-05-18
version: 0.2
related:
tags:
---


**Status:** Draft  
**Version:** 0.2.0  
**Authors:** LVNACY

---

## 1. Overview

This document specifies the design and behavior of Archivist's centralized database system. The goal is a machine-level infrastructure that aggregates data across all registered modules — tracking changelog history and maintaining a master works catalog — without requiring per-project databases and without any of the operational overhead that makes developers reach for a server and immediately regret it.

The system introduces two levels of persistent storage: a global registry that knows about every Apparatus and module (including vault modules) on the machine; and a per-Apparatus database that holds that Apparatus's works catalog and changelog records.

---

## 2. Conceptual Hierarchy

```
Machine
├── Global Registry (~/.archivist/registry.db)
│
├── Apparatus A  (e.g. "writing")
│   ├── (~/.archivist/writing.db)
│   ├── Module: vault  ← "fiction-vault"
│   │   ├── Module: story
│   │   ├── Module: library  ← "cosmic-horror"
│   │   └── Module: publication
│   ├── Module: vault  ← "research-vault"
│   │   ├── Module: library  ← "victorian-mayhem"
│   │   └── Module: library  ← "panopticon"
│   └── Module: library  ← "standalone" (no containing vault)
│
└── Apparatus B  (e.g. "cyber")
    ├── (~/.archivist/cyber.db)
    └── Module: vault  ← "cyber-vault"
        ├── Module: library
        └── Module: general
```

**Definitions:**

- **Module** — a single git repository with an `.archivist/config.yaml`. The fundamental unit of the system. Scoped to a specific job: `story`, `publication`, `library`, `vault`, `general`, or `custom`. Vaults are modules.
- **Vault** — a module with `module_type: vault`. Acts as a superproject and container for other modules. Vaults are registered with the Apparatus just like any other module; their containment relationships are tracked in `module_bays`. A module does not need to belong to a vault to belong to an Apparatus.
- **Apparatus** — a collection of modules, some of which may be vaults. The natural boundary for shared databases. The Apparatus is the only entity in the system that is not itself a module.
- **Machine** — the host system. The global registry lives here and knows about everything below it.

---

## 3. Storage Locations

All Archivist databases live at a system-wide path, not inside any individual project. Per-project databases are explicitly rejected — they defeat the purpose of centralized aggregation.

```
~/.archivist/
├── registry.db          ← global: apparatuses, modules (including vault modules)
├── writing.db           ← apparatus-level: works catalog + changelogs
├── cyber.db             ← apparatus-level: works catalog + changelogs
└── [apparatus-name].db  ← one per apparatus, named after it
```

The `~/.archivist/` directory is created on first run of `archivist init` if it does not exist. The registry database is created at that time. Apparatus databases are created when the first module is registered to a new Apparatus.

**`~/.archivist/` is a git repository.** The entire directory — `registry.db`, all apparatus databases, everything — is version controlled. This is the foundation for `archivist restore` (deferred; see `GIT_SPEC.md`). The directory is initialized as a git repo on first run and configured with a user-supplied remote. The pre-commit hook commits and pushes changes to `~/.archivist/` automatically after each registry sync, keeping the remote current.

SQLite files are binary. Git tracks them but cannot diff or merge them meaningfully. **`~/.archivist/` is never merged — it is overwritten from the remote on restore.** No merge conflicts. The remote is the restoration anchor; local is the active state. The pre-commit sync keeps them in agreement.

---

## 4. Module Types

The `module-type` field in `.archivist/config.yaml` determines how Archivist treats a module across all commands. All types are modules — `vault` is not a separate entity class, it is a module type with containment capabilities.

| Type | Description | Works Catalog | Changelog DB | Can Contain Modules |
|---|---|---|---|---|
| `vault` | Superproject; container for other modules | — | ✓ | ✓ |
| `library` | Catalogues works for research | ✓ | ✓ | — |
| `story` | Story development and writing | — | ✓ | — |
| `publication` | Newsletters, periodicals | — | ✓ | — |
| `general` | General-purpose | — | ✓ | — |
| `custom` | One-off, domain-specific modules (e.g. PLEROMA) | — | ✓ | — |

The "Can Contain Modules" column reflects the intended design. The `module_bays` schema does not technically enforce this restriction — containment is a registry relationship, not a schema constraint — but non-vault modules containing other modules is not a supported use case and should not be encouraged.


---

## 5. Global Registry Schema (`registry.db`)

```sql
CREATE TABLE apparatuses (
    id          INTEGER PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    db_path     TEXT NOT NULL,        -- absolute path to apparatus .db file
    created_at  TEXT NOT NULL
);

-- All modules, including vaults, live here. vault is a module_type value,
-- not a separate table. Do not add a vaults table. It is not coming back.
CREATE TABLE modules (
    id              INTEGER PRIMARY KEY,
    uuid            TEXT UNIQUE NOT NULL, -- stable identifier; lives in .archivist/config.yaml
    apparatus_id    INTEGER NOT NULL REFERENCES apparatuses(id),
    name            TEXT NOT NULL,
    module_type     TEXT NOT NULL,        -- vault | library | story | publication | general | custom
    path            TEXT NOT NULL,        -- absolute path to module root on this machine
    git_remote      TEXT,                 -- remote URL; used for cross-module sync
    library_tag     TEXT,                 -- library modules only
    created_at      TEXT NOT NULL,
    decimated_at    TEXT                  -- NULL = active; ISO date = tombstoned
);

-- Containment relationships between modules. A vault module (container_id)
-- contains other modules (contained_id). Both columns reference modules.id.
-- A module with no row in this table as contained_id belongs directly to its
-- Apparatus with no containing vault — this is valid and fully supported.
-- The schema does not restrict which module_types may appear as container_id,
-- but in practice only vault modules act as containers.
CREATE TABLE module_bays (
    container_id  INTEGER NOT NULL REFERENCES modules(id),
    contained_id  INTEGER NOT NULL REFERENCES modules(id),
    PRIMARY KEY (container_id, contained_id)
);
```

### 5.1 UUID as Canonical Module Identifier

Every module has a UUID, generated once at first registration and written to `.archivist/config.yaml`. It is the canonical identifier for a module across its entire lifetime — stable across deinit, re-add, machine migration, and repo moves.

Because `.archivist/` is committed to the repo, the UUID travels with the code. Cloning the repo on a new machine, moving it to a different vault, or re-adding it after a `deinit` — in all cases, `archivist add` reads the config UUID and matches it to the registry row. The module's history is intact.

`modules.path` is a convenience field for filesystem operations on the current machine. It is not the primary lookup key. UUID is.

### 5.2 Tombstoning

Modules are never deleted from the registry. When a module is removed from the Apparatus — via `archivist deinit` removing its last registered path — the `modules` row is stamped with `decimated_at` and left in place.

The decimation trigger is Apparatus inaccessibility, not vault membership. A module belongs to an Apparatus; it may or may not be contained by a vault. Either way, it has exactly one `modules` row. Decimation happens when that module has no remaining presence in the Apparatus: its `module_bays` rows (if any) have been removed and there is no other active path by which the Apparatus can reach it.

A module with no `module_bays` rows is not automatically decimated — it may be a standalone module registered directly to the Apparatus without a containing vault, which is a perfectly valid and active state. Decimation is an explicit act triggered by `archivist deinit`, not a side effect of losing vault containment.

This preserves all historical data. `work_libraries` and `changelogs` rows in the apparatus DB reference `module_id` as soft FKs — those rows remain valid because the `modules` row was never removed. No orphans. No dangling references. No data loss.

**Reactivation:** When a decimated module is re-added via `archivist add` on a repo whose `.archivist/config.yaml` carries a matching UUID, Archivist finds the existing `modules` row, clears `decimated_at`, updates `path` to the current machine's path, and adds any new `module_bays` association. Full history restored.

---

## 6. Apparatus Database Schema (`[apparatus].db`)

### 6.1 Works Catalog

```sql
CREATE TABLE authors (
    id          INTEGER PRIMARY KEY,
    sort_name   TEXT NOT NULL UNIQUE, -- "Last, First" normalized
    first_name  TEXT,
    last_name   TEXT NOT NULL,
    aliases     TEXT,                 -- JSON array
    homepage    TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE publications (
    id          INTEGER PRIMARY KEY,
    sort_title  TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    pub_type    TEXT,                 -- journal | magazine | newspaper | anthology | series
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE works (
    id               INTEGER PRIMARY KEY,
    sort_title       TEXT NOT NULL,
    title_alt        TEXT,
    class            TEXT,            -- article | monograph | novel | essay | etc.
    category         TEXT,
    year             INTEGER,
    publication_id   INTEGER REFERENCES publications(id),
    citation         TEXT,
    text_source      TEXT,
    word_count       INTEGER,
    part_of          TEXT,
    themes           TEXT,            -- JSON array
    keywords         TEXT,            -- JSON array
    content_warnings TEXT,            -- JSON array
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE work_authors (
    work_id     INTEGER NOT NULL REFERENCES works(id),
    author_id   INTEGER NOT NULL REFERENCES authors(id),
    role        TEXT NOT NULL DEFAULT 'author', -- author | editor | translator
    PRIMARY KEY (work_id, author_id, role)
);

-- One row per library that holds a copy of this work. Library-local fields
-- live here, not on works — the same work can be at different stages in
-- different libraries simultaneously.
CREATE TABLE work_libraries (
    work_id         INTEGER NOT NULL REFERENCES works(id),
    module_id       INTEGER NOT NULL,  -- soft FK into registry.db modules.id
    card_path       TEXT NOT NULL,     -- absolute path to the .md file on disk
    work_stage      TEXT,              -- placeholder | raw | active | processed | shelved
    date_consumed   TEXT,
    date_cataloged  TEXT,
    date_reviewed   TEXT,
    PRIMARY KEY (work_id, module_id)
);

-- Typed relationships between works: citations, series membership, etc.
CREATE TABLE work_relations (
    work_id       INTEGER NOT NULL REFERENCES works(id),
    related_id    INTEGER NOT NULL REFERENCES works(id),
    relation_type TEXT NOT NULL,       -- cites | part-of | related
    PRIMARY KEY (work_id, related_id, relation_type)
);
```

### 6.2 Changelog Records

```sql
CREATE TABLE changelogs (
    id          INTEGER PRIMARY KEY,
    module_id   INTEGER NOT NULL,      -- soft FK into registry.db modules.id
    uuid        TEXT UNIQUE,           -- unsealed identifier
    commit_sha  TEXT UNIQUE,           -- sealed identifier; NULL until post-commit hook
    date        TEXT NOT NULL,
    sealed_at   TEXT,
    created_at  TEXT NOT NULL
);
```

---

## 7. Config as Authoritative

**`.archivist/config.yaml` is the source of truth for everything the registry stores about a module.** The registry is a derived index — built from configs, not the primary record. If the registry and the config disagree, the config wins. Always.

This has two consequences:

**Registration changes are made by editing the config.** Whether the user runs `archivist init`, `archivist add`, or opens `.archivist/config.yaml` in an editor — the config is where registration data lives. The registry reflects it.

**The registry stays current passively via the pre-commit hook.** On every commit, the hook reads the current module's config and upserts its registry entry. One config read, one upsert, scoped to the current module. Fast, invisible, automatic.

`.archivist/` is committed to the repo. This is what makes the UUID scheme work and what makes the config authoritative — the config travels with the code across machines, clones, and vaults.

---

## 8. Module Config — Registry Fields

All fields relevant to the registry that `.archivist/config.yaml` may carry:

```yaml
uuid: 550e8400-e29b-41d4-a716-446655440000  # generated once at first registration; never changes
module-type: library                          # required
apparatus: writing                            # which Apparatus this module belongs to
vaults:                                       # vault modules this module is contained by (zero or more)
  - my-vault
  - another-vault
git-remote: git@github.com:user/repo.git      # for cross-module sync
library-tag: cosmic-horror                    # library modules only
works-dir: works/                             # library modules only; default: works/
```

Absent fields mean no association for that concept. A module with no `apparatus` key is not registered to any Apparatus. A module with no `vaults` key belongs to no containing vault — it is either registered directly to its Apparatus or not registered at all. Both are valid states. `archivist init` on a standalone codebase produces a config with no Apparatus context and every Archivist command works correctly.

The `vault` field (singular) is not supported. Use `vaults` (list) exclusively. A module contained by exactly one vault is a list with one entry.

---

## 9. Registration Commands

### 9.1 `archivist add` — Register a Module

```
archivist add <url> <path> [git submodule flags]
```

Run from the superproject (vault module or nested container). Registers the module with the Apparatus and adds the appropriate `module_bays` row. Git submodule wiring is scaffolded but not yet active — the command does not currently execute `git submodule add`. That integration is a planned future phase.

**Flow:**

```
1. [SCAFFOLDED — NOT EXECUTED] Resolve git submodule add command from args
   git submodule add [flags] <url> <path>

2. Enter the target module directory (must exist on disk)

3. Read .archivist/config.yaml if it exists
   → uuid present, found in registry, decimated:
       reactivation path — clear decimated_at, update path,
       add module_bays row for this superproject, done
   → uuid present, found in registry, active:
       module already registered; add module_bays row if not present and exit
   → uuid present, not found in registry:
       fresh registration using existing config as pre-populated defaults
   → no config:
       full interactive registration flow

4. Create ~/.archivist/ and registry.db if they do not exist

5. Run interactive registration (vault context pre-populated from superproject)

6. Generate UUID if not already present in config

7. Write .archivist/config.yaml with all registration data including uuid

8. Upsert registry.db; add module_bays row linking this module to its
   containing vault (if the superproject is a registered vault module)

9. Install git hooks into the module
```

The registration flow is baked directly into `archivist add` — it does not call `archivist init`. Both commands share the same underlying registration helpers so any augmentation lifts both automatically.

**`--dry-run`:** prints what registration would occur and what `module_bays` row would be added; writes nothing.

### 9.2 `archivist init` — Register or Reconfigure a Module

Run from within the module. Covers:

- **Existing module not yet registered** with Archivist
- **Standalone repo** with no vault context
- **Reconfiguration** — module already registered; user wants to update registration data

**Git context gathered before the interactive flow:**

`git_remote` is NOT captured via `git remote get-url origin`. Not every user names their remote "origin"; git imposes no such convention. Instead, Archivist lists all configured remotes and lets the user select:

- **No remotes configured:** inform the user; offer free-text URL input; allow skipping with a warning that `restore` capability will be limited
- **One remote configured:** present it and ask for confirmation; allow overriding with free-text input
- **Multiple remotes configured:** present a numbered list; require selection; allow free-text input for "none of these"

The selected **URL** (not the remote name) is stored as `modules.git_remote`. Remote names are irrelevant for restore purposes.

Superproject context is still gathered silently:

```
git rev-parse --show-superproject-working-tree  → superproject path (if inside a submodule)
git rev-parse --show-prefix                     → path relative to superproject
```

**Forward compatibility with git integration:** The current init flow calls `get_repo_root()` early and exits if no repo is found. This must not be the first call in the function. Structure the init flow so the `.git` check comes first and `get_repo_root()` is called only after that check resolves. When the git integration ships, the "no `.git` found" branch will run `git init` instead of exiting — this restructuring must be in place to make that a clean one-line change rather than a full flow rewrite.

**UUID resolution:**

```
uuid present in config, found in registry, active    → reconfiguration; present current values as defaults
uuid present in config, found in registry, decimated → reactivation; clear decimated_at, reconfigure
uuid present in config, not found in registry        → not yet registered on this machine; register using config as defaults
no uuid anywhere                                     → fresh registration; generate uuid, write to config
```

**Interactive flow:**

```
Is this an Apparatus module? [y/N]

  → If yes:
    To which Apparatus does this module belong?
      1. writing
      2. cyber
      3. Create new Apparatus
    →

  → Vault association:
    [If superproject detected and is a registered vault module]:
      Detected containing vault: my-vault — confirm? [Y/n]
    [Regardless]:
      Is this module contained by any other vault? [y/N]
      → repeat until done

  → For library modules:
    Library tag (e.g. cosmic-horror):
    Works directory [works/]:
```

Vault modules are registered via their own `archivist init`, which establishes them in the registry as `module_type: vault`. Other modules can then be added to them via `archivist add`. Creating unregistered vault references during a module's init is not supported — the vault must already exist in the registry before it can be named as a container.

**On completion:**

- Generate UUID if not already present
- Write all registration data to `.archivist/config.yaml` including `uuid`
- Upsert `registry.db`; add `module_bays` rows for any named vault containers
- Create `[apparatus].db` if new Apparatus
- Install git hooks

### 9.3 `archivist deinit` — Deregister a Module

```
archivist deinit <path> [git submodule flags]
```

Run from the superproject (or directly from the module). Deregisters the module from the Apparatus and manages the registry cascade. Git submodule wiring is scaffolded but not yet active — the command does not currently execute `git submodule deinit`. That integration is a planned future phase.

**Flow:**

```
1. Look up module by path in registry.db to get module record
   → not found: warn clearly; do not proceed

2. Confirm with user (--dry-run bypasses registry writes, not the prompt)

3. [SCAFFOLDED — NOT EXECUTED] Check if module is a git submodule in a superproject:
   git rev-parse --show-superproject-working-tree
   → if superproject found: would run git submodule deinit [flags] <path>
   → if not a submodule: skip git step

4. Remove module_bays row(s) for this module as contained_id where the
   container belongs to this superproject's registered vault module.
   If called outside a superproject context, remove all module_bays rows
   for this module.

5. Check: does this module have any remaining module_bays rows as contained_id?
   → YES: module is still accessible via another vault; leave modules row
          intact; done
   → NO: check whether this was a standalone (no-vault) registration that
         was directly deregistered. In either case — no remaining bay rows
         and explicit deregistration — stamp modules.decimated_at = today

6. Print summary; if decimated, note that history is preserved and the
   module can be reactivated via archivist add with the same repo
```

**On `--dry-run`:** prints what registry changes would occur; writes nothing. Confirmation prompt still fires.

**Note on `.archivist/`:** The config and UUID in `.archivist/` survive deregistration — they live in the git history. Re-adding the repo later restores full registration via UUID matching.

### 9.4 `archivist migrate` — Config Migration + Registry Upsert

`archivist migrate` migrates the flat `.archivist` file to `.archivist/config.yaml`. As part of migration, it reads the resulting config and upserts the module's registry entry immediately — same operation as the pre-commit hook sync, not deferred to the next commit. A migrated module's registry state is current the moment migration completes.

---

## 10. Pre-Commit Hook — Registry Sync

The pre-commit hook is augmented to sync the current module's config to the registry before each commit.

**What the sync does:**

```
1. Read .archivist/config.yaml
2. Look up module in registry.db by UUID (fall back to path if no UUID in config yet)
3. Upsert modules row with current config values; update path to current machine path
4. Reconcile module_bays:
   → Add any vault containment relationships present in config's vaults: list
     but absent in registry as module_bays rows
   → Remove none — removals are explicit via archivist deinit only
5. If decimated_at is set and module now has active registry presence: clear decimated_at
```

**Graceful degradation:** If no `apparatus` is configured, registry writes are skipped entirely. If `registry.db` doesn't exist, it is created. No command fails because the registry is missing or stale.

The sync is scoped to the current module only — one config read, one upsert. Invisible on the commit hot path.

---

## 11. Library Module Config

A library module's `.archivist/config.yaml` carries the following fields relevant to the centralized database system:

```yaml
uuid: 550e8400-e29b-41d4-a716-446655440000
module-type: library
apparatus: writing
library-tag: cosmic-horror
works-dir: works/
```

The `library-tag` is the library's Obsidian scoping tag. Every card in the library carries this tag alongside `catalog-works`. Archivist applies it automatically when creating cards — injected from config, so per-library Obsidian templates are not needed on Archivist's end.

`catalog-works` is the functional tag Archivist keys on for card identification. The library tag is for Obsidian's own query scoping and is not part of Archivist's identification logic.

---

## 12. `archivist works add`

### 12.1 Command Signature

```
archivist works add --title <title> --author <last-name-fragment>
```

The command must be run from inside a registered library module. `--title` is the work's sort title. `--author` is a partial or full last name used for matching — any author on a matching work satisfies the lookup. Both flags are required.

For works with multiple authors, editors, or translators, the `--author` flag serves only as the lookup key. All contributors on an existing record are pulled automatically. For new works, the remaining contributors are filled in via the card in Obsidian.

### 12.2 Preconditions

Before doing anything else, Archivist:

1. Reads `.archivist/config.yaml` and confirms `module-type: library`.
2. Queries `registry.db` by UUID to confirm this module is registered and active.
3. Resolves the `works-dir` directory from config (falling back to default).

If any of these fail, the command exits with a clear error.

### 12.3 Lookup and Match Logic

Archivist normalizes both the title and author fragment and queries:

```sql
SELECT w.*, a.sort_name
FROM works w
JOIN work_authors wa ON wa.work_id = w.id
JOIN authors a ON a.id = wa.author_id
WHERE lower(replace(replace(w.sort_title, '.', ''), ',', '')) LIKE ?
  AND lower(a.last_name) LIKE ?
```

If one or more matches are found, Archivist presents them for user confirmation:

```
Found: The Lodger — Lowndes, Marie Belloc (1913)
Is this the work you're adding? [y/N]
```

A `y` proceeds to the MATCH path. Anything else falls through to NO MATCH.

### 12.4 MATCH Path

```
pull works, authors, publication records from DB
pre-populate new .md card with all shared core fields
  (work_stage, date_consumed, date_cataloged, date_reviewed are NOT pre-populated — these are library-local)
insert work_libraries row (work_stage and date fields NULL pending commit)
write card to [module-root]/[works-dir]/[sort-title].md
```

### 12.5 NO MATCH Path

```
run apply-template → write blank works card to [module-root]/[works-dir]/[sort-title].md
insert pending work_libraries row (work_id NULL until commit resolves it)
```

No works, authors, or publications rows are inserted at this stage. The card is handed off to the user to fill out in Obsidian. The database is populated at commit time, not at card creation time.

---

## 13. `archivist changelog` — Works Harvesting

When `archivist changelog` runs in a library module, it already reads the frontmatter of staged `.md` files to categorize changes for the changelog body. At the same time, it harvests core works fields from any works card it encounters and holds them in `ctx.data` for the post-commit hook.

No database writes happen during changelog generation. The changelog step is read-only with respect to the database.

### 13.1 Card Identification

A staged `.md` file is identified as a works card by, in order:

1. `tags` frontmatter contains `catalog-works` — primary signal.
2. File path falls under the configured `works-dir` directory — structural fallback.

Author cards are identified by:

1. `class: author` in frontmatter.
2. File path falls under `authors/`.

Publication cards are identified by:

1. `class: publication` in frontmatter.
2. File path falls under `publications/`.

Files that match none of these are treated as ordinary module files and are not harvested.

### 13.2 Fields Harvested from Works Cards

The following frontmatter fields are harvested into `ctx.data` for each identified works card:

```
class,
category,
sort_title,
aliases,
work-stage,
date-consumed,
date-catalogued,
date-reviewed,
authors,
publications,
year,
citation,
text_source,
word_count,
part_of,
themes,
keywords,
content_warnings
```

`authors`, `editors`, `translators`, and `publications` are Obsidian wikilinks. The harvester strips the `[[` and `]]` and stores the display text as the reference string for resolution at commit time.

---

## 14. Post-Commit Hook — Database Pipeline

On commit, the post-commit hook processes every works card in the committed changeset. This is the only point at which rows are inserted or updated in the Apparatus database.

The hook is **idempotent**: running it twice on the same commit produces the same database state. Running it on an old commit after manually deleting a DB row reconstructs correctly from disk.

### 14.1 Reference Resolution Order

For author and publication references on a works card:

1. Check the Apparatus DB first. If a matching row exists, use it.
2. If not in DB, find the card on disk using the configured directory path, read its frontmatter, upsert the row.

The DB is the cache. The filesystem is the fallback. No network calls. No Obsidian process.

### 14.2 Pipeline per Committed Works Card

```
for each committed .md file identified as a works card:

  1. Extract harvested data from ctx.data (collected during changelog step)

  2. Resolve authors:
       for each name in authors/editors/translators:
         check authors table → if exists, use id
         if not → find author card on disk → upsert authors row → use id

  3. Resolve publication:
       check publications table → if exists, use id
       if not → find publication card on disk → upsert publications row → use id

  4. Resolve work:
       query works WHERE sort_title matches AND any author matches
       present confirmation if ambiguous (edge case)
       if MATCH:
         UPDATE works row with any changed core fields
         UPDATE work_libraries row (work_stage, dates, card_path)
       if NO MATCH (new work):
         INSERT works row
         INSERT work_authors rows (one per contributor, with role)
         INSERT work_relations rows for cites and related references
         resolve pending work_libraries row:
           UPDATE SET work_id = new id, work_stage, dates, card_path

  5. INSERT or UPDATE changelogs row for this commit
```

### 14.3 Author and Publication Cards Committed Independently

If an author or publication card is committed without an accompanying works card (e.g. pre-populating the authors directory), the hook processes it independently:

```
for each committed .md file identified as an author card with no accompanying works card:
  upsert authors row from frontmatter

for each committed .md file identified as a publication card:
  upsert publications row from frontmatter
```

---

## 15. Query Capabilities (Initial Scope)

The following queries are supported against the Apparatus database at initial implementation. Domain-specific layer queries are explicitly deferred.

Queries over active modules should always filter `m.decimated_at IS NULL` unless historical data from decimated modules is explicitly desired.

**What libraries share this work?**
```sql
SELECT m.name, m.path, wl.card_path, wl.work_stage
FROM work_libraries wl
JOIN works w ON w.id = wl.work_id
JOIN modules m ON m.id = wl.module_id
WHERE w.sort_title = ?
  AND m.decimated_at IS NULL
```

**What works are related to this one, and where can I find them?**
```sql
SELECT w2.sort_title, a.sort_name, w2.year, wl.card_path, wl.work_stage
FROM work_relations wr
JOIN works w2 ON w2.id = wr.related_id
JOIN work_authors wa ON wa.work_id = w2.id
JOIN authors a ON a.id = wa.author_id
JOIN work_libraries wl ON wl.work_id = w2.id
WHERE wr.work_id = ? AND wr.relation_type = 'related'
```

**What works has this author contributed to across the Apparatus?**
```sql
SELECT w.sort_title, w.year, wa.role, wl.card_path
FROM work_authors wa
JOIN works w ON w.id = wa.work_id
JOIN work_libraries wl ON wl.work_id = w.id
JOIN authors a ON a.id = wa.author_id
WHERE lower(a.last_name) LIKE ?
ORDER BY w.year ASC
```

**What works are currently active across all libraries?**
```sql
SELECT w.sort_title, a.sort_name, w.year, m.name AS library, wl.card_path
FROM work_libraries wl
JOIN works w ON w.id = wl.work_id
JOIN work_authors wa ON wa.work_id = w.id AND wa.role = 'author'
JOIN authors a ON a.id = wa.author_id
JOIN modules m ON m.id = wl.module_id
WHERE wl.work_stage = 'active'
  AND m.decimated_at IS NULL
ORDER BY w.sort_title ASC
```

**What modules live in a given vault?**
```sql
SELECT m.name, m.module_type, m.path
FROM modules m
JOIN module_bays mb ON mb.contained_id = m.id
JOIN modules vault ON vault.id = mb.container_id
WHERE vault.name = ?
  AND m.decimated_at IS NULL
ORDER BY m.name ASC
```

---

## 16. `archivist init` — Forward Compatibility with the Git Integration Phase

The Centralized Database feature and the Git Integration feature both modify `archivist init`. The Centralized DB version adds Apparatus registration. The Git Integration version adds `git init` capability, live git operation execution, and `~/.archivist/` initialization as a git repo. These two versions of the same command must not require a destructive rewrite of the Centralized DB work when the git integration ships. The following design decisions are made here, during the Centralized DB phase, to ensure that the git integration layers on cleanly.

### 16.1 — `~/.archivist/` Git Initialization Ships with Centralized DB

`~/.archivist/` git initialization is **not deferred to the git integration phase**. It is implemented as part of the Centralized Database feature. On first run of `archivist init`, after creating `~/.archivist/` if it does not exist, Archivist:

1. Checks whether `~/.archivist/` is already a git repository
2. If not: runs `git init` inside `~/.archivist/`; prompts the user for a registry remote URL; adds the remote if provided; makes an initial commit
3. If yes: proceeds without reinitializing

Doing this now means that by the time the git integration ships, every machine that has run `archivist init` will already have a version-controlled registry. The git integration then activates live module-level git operations without needing to touch the registry setup. Machines that initialized before this version of Archivist can upgrade simply by re-running `archivist init` — the reconfiguration path handles the `~/.archivist/` check naturally.

### 16.2 — `get_repo_root()` Must Not Be the First Call in `archivist init`

The current init flow calls `get_repo_root()` early and exits if no git repo is found. When the git integration ships, the "no repo found" case will run `git init` instead of exiting. That change must be a one-line branch swap, not a flow rewrite.

**Required structure for the Centralized DB implementation:**

```
1. Check working directory for .git (file or folder)
   → found: call get_repo_root(); proceed
   → not found: exit with error (current behavior)
                ↑ this branch becomes `git init; get_repo_root(); proceed`
                  when the git integration ships — one change, not a rewrite
2. get_repo_root() — called here, after the check, not before
3. Remainder of init flow
```

`get_repo_root()` must not be called before this check. Any code that currently calls it at the top of the init function must be moved. This is a non-negotiable forward-compatibility requirement.

### 16.3 — Interactive Remote Selection, Not Hardcoded "origin"

Covered in §9.2. The `git remote get-url origin` call is replaced with interactive remote listing and selection during the Centralized DB implementation. This is not a deferred concern — it is implemented now so that modules registered during the Centralized DB phase have correctly populated `git_remote` values before the git integration ships.

### 16.4 — Pre-Commit Hook: Graceful Degradation on Missing Registry Remote

The pre-commit hook sync (§10) adds a registry commit-and-push step. On machines that have `~/.archivist/` initialized but have not yet configured a registry remote (e.g. users who skipped the remote prompt during init), this step must be a clean no-op rather than an error. The check is simple: if `~/.archivist/` has no configured remote, skip the push silently. Log a warning at most. Do not block the commit.

This graceful degradation also handles the window between the Centralized DB feature shipping and the user re-running `archivist init` on any machine that was initialized on an older version. The registry still stays current locally; it just does not push until a remote is configured.

### 16.5 — Shared Registration Helper Must Not Own Git Operations

The shared registration helper (§9, §1.5.7 of the implementation checklist) handles registration data: UUID generation, config writing, registry upsert, vault association. It must not own any git operations — not `git init`, not `git submodule add`, not hook installation into remote modules. Those belong in the command-specific code (`init.py`, `add.py`, `deinit.py`).

This boundary is what makes the git integration a layering exercise rather than a refactor. The helper stays unchanged; the commands around it gain git execution capability.

---

## 17. Open Questions and Deferred Decisions

The following are explicitly out of scope for initial implementation and should be revisited when the use cases are better understood.

**Domain-specific layer storage** — library templates carry fields beyond the common core. Whether these are worth storing in the Apparatus database — and if so, whether as extension tables, JSON columns, or something else — is deferred until there is a concrete query use case that cannot be satisfied by the common core alone.

**`archivist works query` command** — a CLI interface for the queries described in §15. The queries themselves are specced; the command interface is not. Defer until the database schema has been exercised against real data and the query patterns stabilize.

**`archivist works update`** — a command to manually push changes from a works card to the database outside of the commit pipeline. Not currently specced. The commit pipeline is the canonical write path; this command exists only as a convenience for edge cases where someone needs to force a sync without making a commit.

**`archivist works cleanup`** — handle `work_libraries` rows where `work_id` is NULL and no corresponding card exists on disk. Deferred.

**Multi-machine sync** — previously listed as a deferred concern pending scale requirements. This is superseded: `~/.archivist/` as a git repository with a configured remote is the sync mechanism. Each machine pulls from the remote on restore; the pre-commit hook keeps the remote current. SQLite-in-git is not a merge strategy — it is a backup and restore strategy. Concurrent writes from multiple machines to the same registry are not supported and are not a design goal. If that requirement ever materializes, migration to PostgreSQL is straightforward; the schema is already relational.

---

*This document is a living spec. It will be revised as implementation surfaces decisions that were not anticipated here. That is not a failure of the spec — it is the spec doing its job.*