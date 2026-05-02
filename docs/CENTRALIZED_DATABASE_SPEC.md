# Archivist — Centralized Database Specification

**Status:** Draft  
**Version:** 0.1.0  
**Authors:** LVNACY  

---

## 1. Overview

This document specifies the design and behavior of Archivist's centralized database system. The goal is a machine-level infrastructure that aggregates data across all registered modules and vaults — tracking changelog history and maintaining a master works catalog — without requiring per-project databases and without any of the operational overhead that makes developers reach for a server and immediately regret it.

The system introduces two levels of persistent storage: a global registry that knows about every Apparatus, vault, and module on the machine; and a per-Apparatus database that holds that Apparatus's works catalog and changelog records.

---

## 2. Conceptual Hierarchy

```
Machine
├── Global Registry (~/.archivist/registry.db)
│
├── Apparatus A  (e.g. "writing")
│   ├── (~/.archivist/writing.db)
│   ├── Vault 1
│   │   ├── Module: story
│   │   ├── Module: library  ← "cosmic-horror"
│   │   └── Module: publication
│   └── Vault 2
│       ├── Module: library  ← "victorian-mayhem"
│       └── Module: library  ← "panopticon"
│
└── Apparatus B  (e.g. "cyber")
    ├── (~/.archivist/cyber.db)
    └── Vault 1
        ├── Module: library
        └── Module: general
```

**Definitions:**

- **Module** — a single git repository with an `.archivist/config.yaml`. Scoped to a specific job: `story`, `publication`, `library`, `vault`, `general`, or `custom`.
- **Vault** — a collection of modules that are related in scope to one another.
- **Apparatus** — a collection of vaults that are related in some broader sense. The natural boundary for shared databases.
- **Machine** — the host system. The global registry lives here and knows about everything below it.

---

## 3. Storage Locations

All Archivist databases live at a system-wide path, not inside any individual project. Per-project databases are explicitly rejected — they defeat the purpose of centralized aggregation.

```
~/.archivist/
├── registry.db          ← global: apparatuses, vaults, modules
├── writing.db           ← apparatus-level: works catalog + changelogs
├── cyber.db             ← apparatus-level: works catalog + changelogs
└── [apparatus-name].db  ← one per apparatus, named after it
```

The `~/.archivist/` directory is created on first run of `archivist init` if it does not exist. The registry database is created at that time. Apparatus databases are created when the first module is registered to a new Apparatus.

---

## 4. Module Types

The `module-type` field in `.archivist/config.yaml` determines how Archivist treats a module across all commands.

| Type | Description | Works Catalog | Changelog DB |
|---|---|---|---|
| `library` | Catalogues works for research | ✓ | ✓ |
| `story` | Story development and writing | — | ✓ |
| `publication` | Newsletters, periodicals | — | ✓ |
| `vault` | Collection of modules | — | ✓ |
| `general` | General-purpose | — | ✓ |
| `custom` | One-off, domain-specific modules (e.g. PLEROMA) | — | ✓ |

`custom` modules opt out of the works catalog entirely. Their only footprint in the Apparatus database is changelog records. They declare their own changelog generation behavior via the `.archivist/changelog.py` plugin system, which already exists. Nothing else about them is Archivist's problem.

---

## 5. Global Registry Schema (`registry.db`)

```sql
CREATE TABLE apparatuses (
    id          INTEGER PRIMARY KEY,
    name        TEXT UNIQUE NOT NULL,
    db_path     TEXT NOT NULL,        -- absolute path to apparatus .db file
    created_at  TEXT NOT NULL
);

CREATE TABLE vaults (
    id              INTEGER PRIMARY KEY,
    apparatus_id    INTEGER NOT NULL REFERENCES apparatuses(id),
    name            TEXT NOT NULL,
    path            TEXT NOT NULL,    -- absolute path to vault root
    created_at      TEXT NOT NULL
);

CREATE TABLE modules (
    id              INTEGER PRIMARY KEY,
    apparatus_id    INTEGER NOT NULL REFERENCES apparatuses(id),
    vault_id        INTEGER REFERENCES vaults(id),  -- nullable; not all modules are in a vault
    name            TEXT NOT NULL,
    module_type     TEXT NOT NULL,
    path            TEXT NOT NULL,    -- absolute path to module root
    library_tag     TEXT,             -- e.g. "cosmic-horror"; library modules only
    created_at      TEXT NOT NULL
);
```

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

-- Junction table: one row per library that holds a copy of this work.
-- Library-local fields live here, not on works, because the same work
-- can be at different stages in different libraries simultaneously.
CREATE TABLE work_libraries (
    work_id         INTEGER NOT NULL REFERENCES works(id),
    module_id       INTEGER NOT NULL,  -- FK into registry.db modules.id
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
    module_id   INTEGER NOT NULL,      -- FK into registry.db modules.id
    uuid        TEXT UNIQUE,           -- unsealed identifier
    commit_sha  TEXT UNIQUE,           -- sealed identifier; NULL until post-commit hook
    date        TEXT NOT NULL,
    sealed_at   TEXT,
    created_at  TEXT NOT NULL
);
```

---

## 7. Library Module Config

A library module's `.archivist/config.yaml` carries the following fields relevant to the centralized database system. All directory paths are relative to the module root and have sensible defaults; Archivist always checks config before assuming a default.

```yaml
module-type: library
apparatus: writing               # which apparatus this module belongs to
library-tag: cosmic-horror       # applied alongside catalog-works on all cards

directories:
  works: works/                  # default; override if the library uses a different name
  authors: authors/
  publications: publications/
```

The `library-tag` is the library's Obsidian scoping tag (e.g. `cosmic-horror`, `pulp-fiction`). Every card in the library carries this tag alongside `catalog-works`. Archivist applies the library tag automatically when creating cards — it is injected from config, so per-library Obsidian templates are not needed on Archivist's end.

`catalog-works` is the functional tag Archivist keys on for card identification. The library tag is for Obsidian's own query scoping and is not part of Archivist's identification logic.

---

## 8. Registration

### 8.1 `archivist init` — Apparatus and Module Registration

During `archivist init`, after the standard module setup questions, Archivist asks:

```
Is this an Apparatus module? [y/N]
```

If yes:

```
To which Apparatus does this module belong?

  1. writing
  2. cyber
  3. Create new Apparatus

→
```

If the user selects an existing Apparatus, the module is registered to it in `registry.db`. If the user creates a new one, a new `apparatuses` row and a new `[apparatus-name].db` file are created before registration proceeds.

```
Does this module belong to a Vault? [y/N]

  1. vault-name-a
  2. vault-name-b
  3. Create new Vault

→
```

Vault membership is optional. A module may belong to an Apparatus without belonging to any Vault.

### 8.2 Registration Data Written

On successful registration:

- `registry.db` → new row in `apparatuses` (if new), `vaults` (if new or selected), `modules`
- `.archivist/config.yaml` → `apparatus` and `vault` fields written
- `[apparatus].db` → created if it did not exist

---

## 9. `archivist works add`

### 9.1 Command Signature

```
archivist works add --title <title> --author <last-name-fragment>
```

The command must be run from inside a registered library module. `--title` is the work's sort title. `--author` is a partial or full last name used for matching — any author on a matching work satisfies the lookup. Both flags are required.

For works with multiple authors, editors, or translators, the `--author` flag serves only as the lookup key. All contributors on an existing record are pulled automatically. For new works, the remaining contributors are filled in via the card in Obsidian.

### 9.2 Preconditions

Before doing anything else, Archivist:

1. Reads `.archivist/config.yaml` and confirms `module-type: library`.
2. Queries `registry.db` to confirm this module is registered.
3. Resolves the `works/` directory from config (falling back to default).

If any of these fail, the command exits with a clear error.

### 9.3 Lookup and Match Logic

Archivist normalizes both the title and author fragment (lowercase, strip punctuation, collapse whitespace) and queries:

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

### 9.4 MATCH Path

```
pull works, authors, publication records from DB
pre-populate new .md card with all shared core fields
  (work_stage, date_consumed, date_cataloged, date_reviewed are NOT pre-populated — these are library-local)
insert work_libraries row (work_stage and date fields NULL pending commit)
write card to [module-root]/[works-dir]/[sort-title].md
```

### 9.5 NO MATCH Path

```
run apply-template → write blank works card to [module-root]/[works-dir]/[sort-title].md
insert pending work_libraries row (work_id NULL until commit resolves it)
```

No works, authors, or publications rows are inserted at this stage. The card is handed off to the user to fill out in Obsidian. The database is populated at commit time, not at card creation time.

---

## 10. `archivist changelog` — Works Harvesting

When `archivist changelog` runs in a library module, it already reads the frontmatter of staged `.md` files to categorize changes for the changelog body. At the same time, it harvests core works fields from any works card it encounters and holds them in `ctx.data` for the post-commit hook.

No database writes happen during changelog generation. The changelog step is read-only with respect to the database.

### 10.1 Card Identification

A staged `.md` file is identified as a works card by, in order:

1. `tags` frontmatter contains `catalog-works` — primary signal.
2. File path falls under the configured `works/` directory — structural fallback.

Author cards are identified by:

1. `class: author` in frontmatter.
2. File path falls under `authors/`.

Publication cards are identified by:

1. `class: publication` in frontmatter.
2. File path falls under `publications/`.

Files that match none of these are treated as ordinary module files and are not harvested.

### 10.2 Fields Harvested from Works Cards

The following frontmatter fields are harvested into `ctx.data` for each identified works card:

```
sort_title, title_alt, class, category, year, citation, text_source,
word_count, part_of, themes, keywords, content_warnings,
authors, editors, translators, publications,
work_stage, date_consumed, date_cataloged, date_reviewed
```

`authors`, `editors`, `translators`, and `publications` are Obsidian wikilinks. The harvester strips the `[[` and `]]` and stores the display text as the reference string for resolution at commit time.

---

## 11. Post-Commit Hook — Database Pipeline

On commit, the post-commit hook processes every works card in the committed changeset. This is the only point at which rows are inserted or updated in the Apparatus database.

The hook is **idempotent**: running it twice on the same commit produces the same database state. Running it on an old commit after manually deleting a DB row reconstructs correctly from disk.

### 11.1 Reference Resolution Order

For author and publication references on a works card:

1. Check the Apparatus DB first. If a matching row exists, use it.
2. If not in DB, find the card on disk using the configured directory path, read its frontmatter, upsert the row.

The DB is the cache. The filesystem is the fallback. No network calls. No Obsidian process.

### 11.2 Pipeline per Committed Works Card

```
for each committed .md file identified as a works card:

  1. extract harvested data from ctx.data (collected during changelog step)

  2. resolve authors:
       for each name in authors/editors/translators:
         check authors table → if exists, use id
         if not → find author card on disk → upsert authors row → use id

  3. resolve publication:
       check publications table → if exists, use id
       if not → find publication card on disk → upsert publications row → use id

  4. resolve work:
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

### 11.3 Author and Publication Cards Committed Independently

If an author or publication card is committed without an accompanying works card (e.g. pre-populating the authors directory), the hook processes it independently:

```
for each committed .md file identified as an author card:
  upsert authors row from frontmatter

for each committed .md file identified as a publication card:
  upsert publications row from frontmatter
```

---

## 12. Query Capabilities (Initial Scope)

The following queries are supported against the Apparatus database at initial implementation. Domain-specific layer queries are explicitly deferred.

**What libraries share this work?**
```sql
SELECT m.name, m.path, wl.card_path, wl.work_stage
FROM work_libraries wl
JOIN works w ON w.id = wl.work_id
WHERE w.sort_title = ?
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
ORDER BY w.sort_title ASC
```

---

## 13. Open Questions and Deferred Decisions

The following are explicitly out of scope for initial implementation and should be revisited when the use cases are better understood.

**Domain-specific layer storage** — the five library templates (Victorian Mayhem, Cosmic Horror, Panopticon, Marginalia, Pulp Fiction) each carry fields beyond the common core. Whether these are worth storing in the Apparatus database — and if so, whether as extension tables, JSON columns, or something else — is deferred until there is a concrete query use case that cannot be satisfied by the common core alone.

**`archivist works query` command** — a CLI interface for the queries described in §12. The queries themselves are specced; the command interface is not. Defer until the database schema has been exercised against real data and the query patterns stabilize.

**Multi-machine sync** — the roadmap notes that SQLite is the right choice at every stage up to the point where multi-machine access or concurrent writes become a requirement. That point has not arrived. If it does, the schema is already relational and migration to PostgreSQL is straightforward. Do not touch this until the pain is real.

**`archivist works update`** — a command to manually push changes from a works card to the database outside of the commit pipeline. Not currently specced. The commit pipeline is the canonical write path; this command exists only as a convenience for edge cases where someone needs to force a sync without making a commit.

---

*This document is a living spec. It will be revised as implementation surfaces decisions that were not anticipated here. That is not a failure of the spec — it is the spec doing its job.*