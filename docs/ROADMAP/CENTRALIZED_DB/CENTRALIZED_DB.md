---
class:
category:
affiliations:
created:
modified: 2026-05-17
version:
status:
related:
  - "[[CENTRALIZED_DATABASE_SPEC]]"
  - "[[CENTRALIZED_DATABASE_IMPLEMENTATION]]"
  - "[[CENTRALIZED_DATABASE_TESTING_SPECIFICATION]]"
tags:
---
Currently, `archive.db` is scoped per-project, living in each module’s `ARCHIVE/` directory. The long-term vision is a single machine-level database that every Archivist-managed repo feeds into — aggregating commit hashes and changelog frontmatter data across the entire Apparatus.

Primary use case is day-to-day querying: activity across all projects, changelog history, commit timelines. But the architecture should be designed with behavioral use cases in mind from the start, so it can serve as the foundation for cross-project orchestration down the line (see below) without requiring a structural rewrite.

## Contents
```toc
```
## Technology Decision: SQLite

**The answer is SQLite. No server, no daemon, no container — just a file at a well-defined machine-level path (e.g. `~/.archivist/archivist.db`).**

This decision was reached deliberately, after evaluating alternatives including containerized database servers (PostgreSQL in Docker) and Elasticsearch. The reasoning:

**Why not a containerized server?**
The appeal of Docker is keeping tooling off the local machine, but for a developer tool that runs on every commit via the post-commit hook, a containerized database introduces real costs: the daemon must be running constantly, cold starts add latency on the hot path, and volume mounts and networking add operational surface area. That trades one form of environment management for another that is meaningfully heavier. SQLite has no daemon and no server process — it is the file.

**Why not Elasticsearch?**
Elasticsearch is a distributed search engine built for full-text relevance ranking over large document corpora — millions of documents, complex query DSL, tunable scoring. The Archivist use case is structured queries over frontmatter metadata and commit history: activity timelines, changelog lookups, cross-project filtering. That is a SQL `WHERE` clause, not a search problem. Elastic would impose substantial operational overhead before you ever reached a scale that justified it.

**Why SQLite is sufficient at every stage?**
All queryable content is frontmatter data — structured key-value pairs that map cleanly to columns and rows. SQLite’s home turf. If full-text search over note content ever becomes a requirement, SQLite’s built-in FTS5 extension covers it without an external dependency. If multi-machine access or concurrent writes ever become a requirement, migration to PostgreSQL is straightforward because the schema is already relational — nothing about choosing SQLite now forecloses that path later.

**The upgrade path, if it’s ever needed:**

```
SQLite (per-project, current)
  → SQLite (machine-level, centralized)       ← target
    → PostgreSQL (if multi-machine or concurrent writes demand it)
```

Elasticsearch is not on this path. It solves a different class of problem.

## Schema Strategy

Frontmatter schemas are maintained explicitly and kept in sync with the per-class note templates. This is not a schema-less or EAV design — frontmatter keys are known, typed, and stable per class, and that structure is reflected directly in the database schema.

The reason people reach for JSON blobs or entity-attribute-value patterns is usually that their data shape is unknown or shifts too often to manage migrations. Neither applies here: note classes and their template fields are defined and controlled by Archivist itself. Making them explicit in SQLite adds precision and query clarity at negligible cost. SQLite migrations are plain SQL — no ORM ceremony.

Schemas are cheap. Maintain them.

## Open Questions

Registration, storage location, and full schema are not yet specced. Return here to brainstorm when the per-project DB patterns have stabilized and the query use cases are better understood. The note in the original roadmap stands: design should follow stabilization, not precede it.

## Further Reading

This is a general overview. See:
[[CENTRALIZED_DATABASE_SPEC]] for full specification
[[CENTRALIZED_DATABASE_IMPLEMENTATION]] for implementation checklist
[[CENTRALIZED_DATABASE_TESTING_SPECIFICATION]] for augmenting the testing suite
