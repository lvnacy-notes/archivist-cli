# Archivist — Centralized Database Implementation Checklist

**Spec:** `CENTRALIZED_DATABASE_IMPLEMENTATION.md`  
**Status:** Not started

---

## Phase 1 — Global Registry

Foundation. Everything else depends on this being in place and correct.

### Storage

- [ ] Create `~/.archivist/` directory on first `archivist init` if it does not exist
- [ ] Create `registry.db` at `~/.archivist/registry.db` on first run if it does not exist
- [ ] Write `registry.db` schema: `apparatuses`, `vaults`, `modules` tables
- [ ] Add utility function: `get_registry_path() -> Path`
- [ ] Add utility function: `get_registry_connection() -> sqlite3.Connection`
- [ ] Add utility function: `get_apparatus_db_path(apparatus_name: str) -> Path`
- [ ] Add utility function: `get_apparatus_db_connection(apparatus_name: str) -> sqlite3.Connection`

### `archivist init` — Registration Flow

- [ ] After standard init questions, prompt: `Is this an Apparatus module? [y/N]`
- [ ] If yes, query `registry.db` for existing Apparatuses and present list
- [ ] Present option to create new Apparatus alongside existing ones
- [ ] If new Apparatus: insert `apparatuses` row, create `[apparatus-name].db`, write apparatus DB schema
- [ ] Prompt: `Does this module belong to a Vault? [y/N]`
- [ ] If yes, query `registry.db` for existing Vaults in the selected Apparatus and present list
- [ ] Present option to create new Vault alongside existing ones
- [ ] If new Vault: insert `vaults` row
- [ ] Insert `modules` row with `module_type`, `path`, `apparatus_id`, `vault_id`
- [ ] Write `apparatus` and `vault` fields to `.archivist/config.yaml`
- [ ] Write `library-tag` field to `.archivist/config.yaml` for `library` module types
- [ ] Write `directories` block to `.archivist/config.yaml` for `library` module types (with defaults)

### Apparatus Database Schema

- [ ] Write apparatus DB schema on creation: `authors`, `publications`, `works`, `work_authors`, `work_libraries`, `work_relations`, `changelogs` tables
- [ ] Confirm cross-database soft reference behavior is documented in code (no FK enforcement across `registry.db` and `[apparatus].db` — enforced at application layer)

---

## Phase 2 — `archivist works add`

Depends on Phase 1. The registry must exist and the module must be registered before this command does anything useful.

### Precondition Checks

- [ ] Confirm `module-type: library` in config — exit with clear error if not
- [ ] Confirm module is registered in `registry.db` — exit with clear error if not
- [ ] Resolve `works/` directory from config, fall back to default

### Lookup and Match

- [ ] Normalize title and author fragment (lowercase, strip punctuation, collapse whitespace)
- [ ] Query `works` + `work_authors` + `authors` for title/author match
- [ ] Present match(es) to user for confirmation before proceeding
- [ ] Handle rejected match → fall through to NO MATCH path

### MATCH Path

- [ ] Pull `works`, `work_authors`, `authors`, `publications` records from DB
- [ ] Pre-populate new `.md` card with all shared core fields
- [ ] Confirm library-local fields (`work_stage`, `date_consumed`, `date_cataloged`, `date_reviewed`) are NOT pre-populated
- [ ] Insert `work_libraries` row (library-local fields NULL, `card_path` set)
- [ ] Write card to `[module-root]/[works-dir]/[sort-title].md`

### NO MATCH Path

- [ ] Run `apply-template` with the library's works template
- [ ] Write blank card to `[module-root]/[works-dir]/[sort-title].md`
- [ ] Insert pending `work_libraries` row (`work_id` NULL until commit resolves it)

### Author Lookup (both paths)

- [ ] Normalize author last name for lookup
- [ ] On match confirmation, pull all contributors (authors, editors, translators) from `work_authors`
- [ ] On new author encountered during upsert: confirm with user before inserting — `Found: Lowndes, Marie Belloc — is this the right one? [y/N]`

---

## Phase 3 — `archivist changelog` Harvesting

Depends on Phase 1. Phase 2 should be complete and exercised against real data before this is wired up, but is not a strict dependency.

### Card Identification

- [ ] On changelog run in a library module, identify card type for each staged `.md` file
- [ ] Works card: `tags` contains `catalog-works` (primary) OR file path under configured `works/` dir (fallback)
- [ ] Author card: `class: author` (primary) OR file path under `authors/` (fallback)
- [ ] Publication card: `class: publication` (primary) OR file path under `publications/` (fallback)
- [ ] Files matching none of the above: skip silently

### Harvesting into `ctx.data`

- [ ] For each identified works card, extract core frontmatter fields into `ctx.data`
- [ ] Fields: `sort_title`, `title_alt`, `class`, `category`, `year`, `citation`, `text_source`, `word_count`, `part_of`, `themes`, `keywords`, `content_warnings`, `authors`, `editors`, `translators`, `publications`, `work_stage`, `date_consumed`, `date_cataloged`, `date_reviewed`
- [ ] Strip `[[` and `]]` from wikilink values; store display text as reference string for resolution at commit time
- [ ] Confirm no database writes occur during changelog step — read-only

---

## Phase 4 — Post-Commit Hook Pipeline

Depends on Phases 1, 2, and 3. This is the canonical write path for all works data.

### Reference Resolution

- [ ] For each author/editor/translator reference: check `authors` table first; if not found, locate card on disk via configured `authors/` dir, read frontmatter, upsert row
- [ ] For each publication reference: check `publications` table first; if not found, locate card on disk via configured `publications/` dir, read frontmatter, upsert row
- [ ] Confirm resolution is idempotent — running twice on the same commit produces the same DB state

### Works Upsert

- [ ] Query `works` for title + author match on each committed works card
- [ ] MATCH: `UPDATE works` with any changed core fields; `UPDATE work_libraries` with stage, dates, `card_path`
- [ ] NO MATCH (new work): `INSERT works`; `INSERT work_authors` (one per contributor with role); `INSERT work_relations` for `cites` and `related` references; resolve pending `work_libraries` row — `UPDATE SET work_id`, stage, dates, `card_path`

### Independent Author and Publication Cards

- [ ] If a committed file is identified as an author card (no associated works card in the commit): upsert `authors` row from frontmatter
- [ ] If a committed file is identified as a publication card: upsert `publications` row from frontmatter

### Changelog Records

- [ ] On each commit, insert or update `changelogs` row for this module with `commit_sha` and `date`
- [ ] Confirm UUID → `commit_sha` transition behavior is consistent with existing seal mechanics

### Cleanup

- [ ] Add note or future task: periodic cleanup of `work_libraries` rows where `work_id` is NULL and no corresponding card exists on disk (cards created with `works add` but never committed and subsequently deleted)

---

## Phase 5 — Verification and Hardening

Do this after the pipeline is exercised against real data, not before.

- [ ] Confirm idempotency: run post-commit hook twice on same commit, assert DB state is identical
- [ ] Confirm dry-run contract: `archivist changelog --dry-run` in a library module performs no DB writes
- [ ] Confirm `works add` in a non-library module exits with a clear error
- [ ] Confirm `works add` in an unregistered module exits with a clear error
- [ ] Confirm `registry.db` and `[apparatus].db` are created correctly on a fresh machine with no `~/.archivist/` directory
- [ ] Write integration tests covering: registration flow, `works add` MATCH path, `works add` NO MATCH path, post-commit upsert, idempotency

---

*Cross-reference: `SPEC-centralized-db.md` for full schema, pipeline detail, and deferred decisions.*