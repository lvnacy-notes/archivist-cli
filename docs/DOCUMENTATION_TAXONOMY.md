---
class: spec
category:
  - documentation
  - reference
affiliations:
created: 2026-05-17
modified: 2026-05-18
version:
related:
  - "[[CODE_CONVENTIONS]]"
tags:
---
A living reference defining the classification system for all project documentation. Applied via frontmatter on every doc. Subject to revision as the project matures — update this file when terms are added, collapsed, or redefined.

```toc
```

---

## Frontmatter Fields

```yaml
class:       # singular — the document's shape
category:    # 2–3 values — domain and/or knowledge graph role
created:     # ISO date, set at creation
modified:    # ISO date, auto-managed by plugin — do not edit manually
related:     # wikilinks — parent, child, and sibling docs in the knowledge graph
affiliations: # plain strings — feature-to-feature dependencies and contextual ties
tags:        # freeform
```

`related` and `affiliations` are distinct fields with different semantics. `related` carries wikilinks to structurally connected documents — the parent folder note, sibling specs, child checklists. `affiliations` carries plain-string feature identifiers for cross-feature dependencies and contextual ties; these are not links, just additional context queryable by Dataview.

---

## Class

A document belongs to exactly one class. Class defines the document's shape — what kind of thing it is, not what it's about.

| Class | Shape |
|---|---|
| `index` | Folder note and feature overview. Describes what a feature is, the problem it solves, and links out to specs, checklists, and planning docs. Orientation, not direction. |
| `spec` | Specification. Defines what something is and how it behaves. The authoritative description of intended design. |
| `checklist` | Implementation checklist. Task-oriented, tracks completion state. |
| `plan` | Roadmap or planning document. Directional; captures intended future work not yet specced. |
| `archive` | Changelog. Sealed record of a commit or release. |

If a document doesn't fit cleanly into one of these, that's a signal it warrants its own class — not a junk drawer entry.

---

## Category

A document may belong to 2–3 categories. Category defines the realm of application and, optionally, the document's role in the knowledge graph.

### Domain

What area of the project does this document touch?

| Value | Covers |
|---|---|
| `feature` | A discrete user-facing capability |
| `infrastructure` | Development tooling, CI, environment, internal systems |
| `database` | Database layer, schema, query patterns |
| `cli` | Command-line interface and command design |
| `git` | Git integration, hooks, diff, staging, commit |
| `frontmatter` | Frontmatter parsing, manipulation, templating |
| `changelog` | Changelog generation and management |
| `mobile` | Mobile platform (iOS, Swift, DeleGit) |
| `testing` | Test suite, testing strategy, coverage |
| `documentation` | The docs system itself |

### Role

Optional. Clarifies a document's position in the knowledge graph when domain alone isn't sufficient.

| Value | Meaning |
|---|---|
| `planning` | Roadmap-level; directional, not yet specced |
| `reference` | Stable reference material; consulted, not acted on |
| `decision` | Captures an architectural or design decision and its rationale |

---

## Affiliations

Affiliations capture direct feature-to-feature dependencies and contextual ties. Values are plain strings in slug format, matching feature folder names. Not wikilinks.

```yaml
affiliations:
  - centralized-db
  - frontmatter-apply-template
```

Use affiliations to represent: "this feature depends on," "this feature is blocked by," or "this feature shares significant surface area with." Structural document relationships go in `related`; feature-level relationships go here.

---

## Status

Applies to `index` class documents only. Reflects the current state of the feature, not the document.

| Value | Meaning |
|---|---|
| `not-started` | No implementation work has begun |
| `in-progress` | Actively being implemented |
| `blocked` | Cannot proceed — waiting on another feature, decision, or external dependency |
| `shipped` | Complete and in production; may have open follow-up items |
| `abandoned` | Deliberately set aside; not expected to be revisited |

`blocked` is not a soft status. If a feature is blocked, name what it's blocked on — in the Status section of the index, not just in the frontmatter value. The value alone provides no actionable context.