# Templater Support — Plan of Attack

A design document for integrating Obsidian Templater expression resolution
into Archivist's frontmatter commands. This is a scoped plan, not a spec —
it is meant to be read, argued with, and revised before a line of
implementation code gets written.

---

## Problem Statement

Obsidian's [Templater plugin](https://github.com/SilentVoid13/Templater)
allows users to embed dynamic expressions in note frontmatter:

```yaml
---
created: <% tp.date.now("YYYY-MM-DD") %>
title: <% tp.file.title %>
author: <% tp.frontmatter["author"] %>
---
```

These expressions are resolved by Templater when a note is created or when
the user triggers resolution manually inside Obsidian. Outside Obsidian —
which is where Archivist operates — they are opaque strings.

The current behaviour when Archivist touches a file containing unresolved
Templater expressions is **corruption by indifference**: the command reads
the raw expression string, treats it as a plain value, and writes it back
verbatim, or worse, mangles it during frontmatter reordering. The expression
is neither resolved nor safely preserved.

The goal is to fix this, without requiring Node.js or any tooling outside
the Python ecosystem.

---

## Constraints

- **No Node.js on the host machine.** Templater is a JavaScript plugin, but
  we are not running it. We are reimplementing the relevant subset of its
  API in Python.
- **No new mandatory runtime dependencies** beyond what already exists
  (`pyyaml`, `argcomplete`). Any new dependencies go in
  `[project.optional-dependencies]` until Templater support is stable enough
  to promote.
- **Scope is frontmatter expressions only.** Templater's full feature set
  includes dynamic note body content, user scripts, system prompts, and
  interactive inputs. That is a different animal. We are solving for the
  property-value case: strings of the form `<% expr %>` sitting in YAML
  frontmatter fields.
- **Graceful degradation is non-negotiable.** If an expression can't be
  resolved — because it uses a function we haven't implemented, or because
  it requires interactive input — Archivist must leave it alone, not crash,
  not emit garbage.

---

## Configuration

The `templater` key in `.archivist` controls how Archivist handles `<% %>`
expressions. It is set explicitly during `archivist init` — there is no
automatic detection, because detection is the wrong abstraction. The user
knows whether their project uses Templater. We just ask.

Three values are valid:

```
templater: resolve    # Archivist resolves the static subset at write time
templater: preserve   # Archivist detects and safely round-trips expressions
templater: false      # Treat <% %> as plain strings; no handling at all
```

**`resolve`** — Archivist attempts to evaluate `tp.date.*`, `tp.file.*`,
and `tp.frontmatter.*` expressions using its own Python implementation at
write time. Expressions it cannot resolve (unsupported namespaces, or
anything requiring Obsidian's runtime) are left verbatim with a warning.
No Obsidian required. Works in any module, vault or otherwise.

**`preserve`** — Archivist detects `<% %>` expressions and round-trips them
safely through every frontmatter operation without touching their content.
Resolution is left to Obsidian: alt-tab, run
"Templater: replace templates in the active file". Use this when your
templates contain user scripts, interactive prompts, or other expressions
that require Obsidian's runtime.

**`false`** — Archivist treats `<% %>` as dumb strings. Zero overhead, zero
handling. Use this when the project has no Templater expressions.

`preserve` is the default. It costs nothing, breaks nothing, and is the
correct behavior for anyone who hasn't thought about it yet.

---

## What Templater Expressions Actually Look Like

Templater uses `<% %>` delimiters (configurable, but `<% %>` is the default
and the only one we need to care about for now). Three main forms appear in
frontmatter:

```
<% tp.date.now("YYYY-MM-DD") %>              — function call, returns string
<% tp.file.title %>                          — property access, returns string
<% tp.date.now("YYYY-MM-DD", 7) %>           — function call with multiple args
<% tp.frontmatter["some-property"] %>        — frontmatter self-reference
<% "static string" %>                        — degenerate case, but valid
```

Multi-expression values are possible but uncommon in frontmatter:

```yaml
date-range: <% tp.date.now("YYYY-MM-DD") %> to <% tp.date.now("YYYY-MM-DD", 30) %>
```

Control flow (`<% if (...) { %>`) essentially never appears in property
values — it belongs in note bodies. We can document it as out of scope and
move on.

---

## Proposed Architecture

### Layer 1 — Expression Detection and Extraction

A module `archivist/utils/templater.py` handles the low-level mechanics:

```python
TEMPLATER_RE = re.compile(r"<%[-_]?\s*(.*?)\s*[-_]?%>", re.DOTALL)

def has_templater_expression(value: str) -> bool: ...
def extract_expressions(value: str) -> list[str]: ...
def resolve_value(value: str, context: "TemplaterContext") -> str: ...
```

`resolve_value` finds all `<% %>` blocks in a string, attempts to evaluate
each one using the context, and substitutes the result back in. If an
expression cannot be resolved, it is left verbatim — the surrounding string
is returned with the `<% %>` block intact.

### Layer 2 — The `tp` Object

A `TemplaterContext` class exposes a `tp` attribute that mimics Templater's
`tp` object. Each namespace is its own class:

```
TemplaterContext
└── tp
    ├── date       — date arithmetic and formatting
    ├── file       — file metadata (title, path, folder, etc.)
    ├── frontmatter — access to other properties in the same file
    └── config     — Templater config values (date format, etc.)
```

We do **not** implement:
- `tp.system` (interactive prompts — requires Obsidian)
- `tp.user` (user-defined JS scripts — requires Node.js)
- `tp.hooks` (Templater's own hook system)
- `tp.obsidian` (Obsidian API — not available outside Obsidian)

### Layer 3 — The Evaluator

The trickiest part. Templater expressions are JavaScript, and Python's
`eval()` won't run them directly. The approach:

**Option A — Regex/AST-based expression parser (recommended)**

For the subset of Templater expressions that appear in frontmatter, the
grammar is extremely limited: it's essentially `tp.<namespace>.<method>(<args>)`
or `tp.<namespace>.<property>`. A targeted parser handles this without a
full JS interpreter.

```python
import ast
import re

_CALL_RE = re.compile(r"^tp\.(\w+)\.(\w+)\((.*)\)$", re.DOTALL)
_PROP_RE  = re.compile(r"^tp\.(\w+)\.(\w+)$")
```

For argument parsing, we abuse Python's `ast.literal_eval` — since Templater
argument lists for date/file functions use only string and numeric literals,
this works cleanly and safely (no `eval()` of arbitrary code).

**Option B — `py_mini_racer` or `execjs`**

These are Python wrappers that embed a V8 JS runtime. They would let us
run actual Templater expression strings. However:
- `py_mini_racer` ships its own V8 binary (~40MB wheel)
- `execjs` requires a system JS runtime (Node, Deno, etc.), which violates
  the constraint

Option B is a non-starter. Option A is the correct path.

**Option C — `dukpy`**

`dukpy` embeds the Duktape JS interpreter in a Python extension. Smaller
than V8, pure Python install, no system dependencies. It can evaluate simple
JS expressions. This is worth keeping in mind as a **fallback for expressions
that Option A's parser can't handle** — but it is a new dependency and
should not be mandatory. If we go this route, it lives behind the same
optional-dependency gate as the rest of Templater support.

Recommendation: implement Option A first. Gate Option C behind a
`[templater-extended]` optional dependency group for users who need it.

---

## `tp.date` — Implementation Scope

This is the most-used namespace in frontmatter and the highest-value thing
to implement.

| Method | Signature | Notes |
|--------|-----------|-------|
| `tp.date.now` | `(format?, offset?, reference?, reference_format?)` | `format` is moment.js — see below |
| `tp.date.tomorrow` | `(format?)` | sugar for `now(format, 1)` |
| `tp.date.yesterday` | `(format?)` | sugar for `now(format, -1)` |
| `tp.date.weekday` | `(format, weekday, reference?, reference_format?)` | can defer |

**The moment.js format problem.** Templater uses moment.js format strings
(`YYYY-MM-DD`, `HH:mm`, `dddd`, etc.). Python's `strftime` uses different
tokens (`%Y-%m-%d`, `%H:%M`, `%A`). We need a translation layer.

This is well-trodden ground — a straightforward mapping table handles 95%
of real-world usage. The `arrow` library does this already, but it's a
dependency. A hand-rolled `moment_to_strftime(fmt: str) -> str` function
covering the common tokens is ~30 lines and zero dependencies. Build that.

---

## `tp.file` — Implementation Scope

File metadata is available to us at resolution time because we know the
path of the file being processed. This works in any module — vault or
otherwise — because it requires nothing beyond a file path and `os.stat`.

| Property/Method | Returns | Available? |
|-----------------|---------|------------|
| `tp.file.title` | Filename without extension | ✅ trivial |
| `tp.file.folder(absolute?)` | Parent directory | ✅ trivial |
| `tp.file.path(relative?)` | Full path | ✅ trivial |
| `tp.file.creation_date(format?)` | File creation time | ✅ `os.stat` |
| `tp.file.last_modified_date(format?)` | Last modified time | ✅ `os.stat` |
| `tp.file.content` | Raw file content | ⚠️ available but expensive |
| `tp.file.cursor()` | Cursor position marker | ❌ Obsidian-only |
| `tp.file.selection()` | Current selection | ❌ Obsidian-only |

---

## `tp.frontmatter` — Implementation Scope

Self-referential access to other properties in the same file. Useful for
computed fields like `display-title: <% tp.frontmatter["sort-title"] %>`.

Implementation: parse the existing frontmatter before resolution, pass it
into the context. Properties resolved earlier in the same pass are available
to later ones only if we do a multi-pass resolution — first pass populates
non-self-referential fields, second pass resolves cross-references.

For the first implementation, single-pass is fine. Document the limitation.

---

## Integration Points

The three frontmatter commands that touch file content are the integration
points. Each one needs to:

1. Check `config.get("templater")` — if `"false"`, skip all handling
2. Parse the file's frontmatter, masking expressions before YAML parsing
3. For `"resolve"`: attempt resolution via `resolve_value(value, context)`
   for each property value containing a `<% %>` expression
4. For `"preserve"`: restore all expressions verbatim after the operation
5. Write results; leave unresolvable expressions verbatim with a warning

### `frontmatter add`

When adding a property whose default value contains a Templater expression
(e.g. from a template file), resolve it at write time if mode is `resolve`.
If resolution fails, write the expression verbatim and warn. If mode is
`preserve`, write it verbatim without attempting resolution.

### `frontmatter apply-template`

Template files frequently contain Templater expressions in their property
defaults. These should be resolved against the *target note's* context (its
path, its title, etc.), not the template file's context.

This is the highest-value integration point and the one most likely to catch
real-world bugs without Templater support.

### `frontmatter remove` / `frontmatter rename`

These don't set values, but they do rewrite frontmatter. The risk is
mangling an expression that happens to contain characters that YAML parsing
mishandles. The mitigation applies for both `resolve` and `preserve` modes:
detect expressions before YAML parsing, temporarily replace them with stable
sentinel tokens, then restore them after the operation.

---

## Sentinel Strategy (for safe round-tripping)

When Archivist reads frontmatter that contains unresolved Templater
expressions, raw YAML parsing may fail or silently corrupt the value if
the expression contains characters YAML treats specially (`{`, `}`, `:`,
`#`, etc.).

The safe approach:

```python
# Before YAML parsing, replace expressions with stable placeholders
def mask_templater_expressions(raw_fm: str) -> tuple[str, dict[str, str]]:
    """Replace <% %> blocks with __TMPL_0__, __TMPL_1__, etc.
    Returns the masked string and a restoration map."""
    ...

# After processing, restore
def restore_templater_expressions(raw_fm: str, mask_map: dict[str, str]) -> str:
    ...
```

This approach means frontmatter commands work correctly on files with
unresolved Templater expressions even before full resolution support is
implemented — mask on read, restore on write, no corruption. This is the
**Phase 1 deliverable** and should be shipped before the resolution engine.

In `resolve` mode, the restore step is skipped for expressions that were
successfully resolved — they get the resolved value instead of the original.
In `preserve` mode, all expressions are always restored verbatim.

---

## Phased Implementation Plan

### Phase 0 — Config (Already Done)

`archivist init` prompts the user to select a Templater mode (`resolve`,
`preserve`, or `false`) and writes `templater: <mode>` to `.archivist`.

### Phase 1 — Safe Preservation (Ship First)

Goal: Archivist never corrupts a Templater expression, even without
resolving it. Handles both `preserve` and `resolve` modes — resolution
can't happen without preservation working correctly first.

- Implement `mask_templater_expressions` / `restore_templater_expressions`
  in `archivist/utils/templater.py`
- Thread the mask/restore cycle through `frontmatter add`, `remove`,
  `rename`, and `apply-template` when `templater` is not `"false"`
- No resolution, no new dependencies — just safe round-tripping
- Test: a file with `created: <% tp.date.now("YYYY-MM-DD") %>` passes
  through every frontmatter command unchanged

### Phase 2 — Resolution Engine Core

Goal: resolve the most common expressions at write time when mode is
`resolve`.

- Implement `TemplaterContext` with `tp.date` and `tp.file`
- Implement `moment_to_strftime` format translation
- Implement `resolve_value` with the regex-based expression parser
- Wire into `frontmatter add` and `frontmatter apply-template`
- No `tp.frontmatter`, no `tp.system`, no user scripts
- Test: a template with `<% tp.date.now("YYYY-MM-DD") %>` and
  `<% tp.file.title %>` resolves correctly when applied

### Phase 3 — `tp.frontmatter` and Cross-References

- Implement `tp.frontmatter` namespace with single-pass resolution
- Document multi-pass limitation
- Test: `display-title: <% tp.frontmatter["sort-title"] %>` resolves if
  `sort-title` is a plain value in the same file

### Phase 4 — Extended Coverage (Optional)

- Gate `dukpy` behind `[templater-extended]` optional dependency
- Use it as a fallback evaluator for expressions the regex parser can't handle
- Log a warning when fallback is used, so users know something unusual happened

---

## What We Are Explicitly Not Building

- Full JavaScript evaluation of arbitrary Templater expressions
- `tp.system.prompt()` — requires interactive Obsidian context
- `tp.user.*` — requires user-defined JS scripts, which require Node.js
- `tp.obsidian.*` — requires the Obsidian API
- Templater's dynamic note-body content (cursor, selection, etc.)
- Support for non-default `<% %>` delimiters (configurable in Templater settings)

If a user has expressions using these features in their frontmatter,
Archivist will leave them verbatim and emit a warning. That is the
correct behaviour.

---

## Files to Create / Modify

| File | Change |
|------|--------|
| `archivist/utils/templater.py` | New module — mask/restore + resolution engine |
| `archivist/utils/__init__.py` | Export new utils |
| `archivist/commands/frontmatter/add.py` | Thread mask/restore + resolution |
| `archivist/commands/frontmatter/remove.py` | Thread mask/restore |
| `archivist/commands/frontmatter/rename.py` | Thread mask/restore |
| `archivist/commands/frontmatter/apply_template.py` | Thread mask/restore + resolution |
| `archivist/utils/config.py` | No change needed — `templater` key already lives in `.archivist` |
| `pyproject.toml` | Add `templater = ["dukpy"]` optional dep group (Phase 4 only) |
| `tests/test_templater.py` | New test module |

---

## Open Questions

1. **Delimiter configuration.** Templater allows users to change `<% %>`
   to custom strings in its settings. Should Archivist read
   `.obsidian/plugins/templater-obsidian/data.json` to pick up the configured
   delimiters? Probably yes, eventually. Defer to Phase 2.

2. **Template file resolution context.** When `apply-template` applies a
   template, whose file context should be used — the template file's, or the
   target note's? The answer is obviously the target note's, but this needs
   to be explicit in the implementation to avoid subtle bugs.

3. **Multi-pass resolution.** Some vaults have expressions that reference
   properties that are themselves expressions. Is one pass enough? For the
   first implementation, yes. If users scream, revisit.

4. **Logging vs. silent no-op for unresolvable expressions.** Currently
   proposing a warning. Could instead write a `.archivist-unresolved` report
   file listing every expression that was left verbatim. Probably overkill
   for Phase 1.