---
class: planning-doc
category:
  - implementation
created: 2026-05-15
modified: 2026-05-15
tags:
  - archivist
  - templater
---

**Review of:** [TEMPLATER_SUPPORT_PLAN.md](ROADMAP/TEMPLATER_SUPPORT_PLAN.md)

## Executive Summary

**Current State:** Phases 0, 2, and 3 are **complete and tested**. Phase 1 (Safe Preservation) is 60% complete — the mask/restore infrastructure exists and is integrated into `add.py` and `apply_template.py`, but **two commands are missing this critical integration**: `remove.py` and `rename.py`.

**Blocker:** `remove` and `rename` commands will corrupt or mangle Templater expressions containing YAML-sensitive characters (`{`, `}`, `:`, `#`, etc.) because they operate on raw frontmatter without masking first.

**Effort to Complete:** ~30 minutes. The pattern is already established in `add.py`; it's a straightforward port to the remaining two commands, plus integration tests.

---

## Phase-by-Phase Status

### Phase 0: Config ✅ **COMPLETE**

**What was needed:**
- User prompt for Templater mode during `archivist init`
- Three options: `resolve`, `preserve`, `false`
- Storage in `.archivist/config.yaml`
- Parsing at runtime

**What exists:**
- [archivist/commands/init.py](../archivist/commands/init.py) — `_prompt_templater_mode()` function presents the three options with full explanations
- [archivist/utils/templater.py](../archivist/utils/templater.py) — `TemplaterMode` enum with `from_config()` parser
- [archivist/utils/templater.py](../archivist/utils/templater.py) — `get_templater_mode(config)` helper reads mode from config dict
- [tests/unit/test_templater.py](../tests/unit/test_templater.py) — Unit tests verify enum parsing and default behavior

**Status:** Shipping. This phase is done.

---

### Phase 1: Safe Preservation — **PARTIALLY COMPLETE** ⚠️

**What was needed:**
Safe round-tripping of `<% %>` expressions through frontmatter operations without corruption, for all three modes:
- **DISABLED:** No handling; treat as dumb strings
- **PRESERVE:** Detect and round-trip expressions verbatim without touching them
- **RESOLVE:** Later; masking is the prerequisite

**What exists:**

#### Core Infrastructure ✅
- [archivist/utils/templater.py](../archivist/utils/templater.py) — `TEMPLATER_EXPR_RE` regex matches `<% %>` expressions with whitespace-control variants
- [archivist/utils/templater.py](../archivist/utils/templater.py) — `mask_templater_expressions(raw_fm)` replaces all `<% %>` blocks with stable sentinel tokens (`__ARCHIVIST_TMPL_0__`, etc.)
- [archivist/utils/templater.py](../archivist/utils/templater.py) — `restore_templater_expressions(raw_fm, mask_map, resolved=None)` restores sentinels to either original expressions (PRESERVE) or resolved values (RESOLVE)
- [archivist/utils/templater.py](../archivist/utils/templater.py) — Detection helpers: `has_templater_expression()`, `extract_expressions()`
- [tests/unit/test_templater.py](../tests/unit/test_templater.py) — Extensive test coverage of mask/restore cycle, edge cases, multiple expressions per value, whitespace-control variants

#### Integration Points ✅
- [archivist/commands/frontmatter/add.py](../archivist/commands/frontmatter/add.py) — Full mask/restore cycle in `_process_note()`:
  - Masks raw_fm before string operations
  - Restores after changes
  - Respects all three modes correctly
  - Handles both existing frontmatter and new blocks

- [archivist/commands/frontmatter/apply_template.py](../archivist/commands/frontmatter/apply_template.py) — Full mask/restore cycle in `_process_note()`:
  - Masks note and template before merge
  - Restores note's original expressions after merge (template expressions are resolved separately)
  - Correct ordering: mask → parse → filter → resolve → merge → restore
  - Comprehensive comment block explains the operation order

#### **Missing Integration** ❌

**[archivist/commands/frontmatter/remove.py](../archivist/commands/frontmatter/remove.py)**

Current code:
```python
def _process_note(note_path: Path, prop: str, dry_run: bool, nf: NoteFilter) -> bool:
    content = safe_read_markdown(note_path)
    if content is None:
        return False

    if not has_frontmatter(content):
        return False

    match = FRONTMATTER_RE.match(content)
    if not match:
        return False
    raw_fm = match.group(1)
    body = content[match.end():]

    if not note_matches_filter(nf, raw_fm):
        return False

    updated_fm, found = remove_property_from_frontmatter(raw_fm, prop)
    # ☝️ PROBLEM: raw_fm is operated on directly without masking
```

**Issue:** YAML parsing in `remove_property_from_frontmatter()` may fail or silently corrupt expressions containing `{`, `}`, `:`, `#`, `*`, `[`, `]`, etc.

**Example corruption:**
```yaml
created: <% tp.date.now("YYYY-MM-DD") %>
config: { "name": "test" }
```
If `config` is removed without masking, the YAML parser may treat the `{` in the `created` expression as a flow mapping start, causing parse errors or value mangling.

---

**[archivist/commands/frontmatter/rename.py](../archivist/commands/frontmatter/rename.py)**

Current code:
```python
def _rename_property_in_raw_fm(raw_fm: str, old_prop: str, new_prop: str) -> tuple[str, bool]:
    """Rename a property key in raw YAML frontmatter..."""
    lines = raw_fm.split("\n")
    result = []
    i = 0
    found = False

    while i < len(lines):
        line = lines[i]
        if match_property_line(line, old_prop):
            found = True
            result.append(line.replace(old_prop, new_prop, 1))
            # ☝️ PROBLEM: operating on raw lines without masking first
```

**Issue:** While this function is simpler (regex-based line matching, not YAML parsing), it still has risk. If an expression contains the property name string by coincidence, or if continuation lines cause issues with unmasked content, corruption is possible.

Example:
```yaml
---
title: <% tp.file.title %>
created: <% tp.date.now("title-format") %>  # Oops, "title" is in the expression
---
```
Renaming `title` → `name` without masking could partially replace the string inside the expression.

---

### Phase 2: Resolution Engine Core ✅ **COMPLETE**

**What was needed:**
Evaluator for Templater expressions in the `tp.date.*`, `tp.file.*`, and `tp.frontmatter.*` namespaces using Python, without Node.js or Obsidian.

**What exists:**

#### Expression Evaluator ✅
- [archivist/utils/templater.py](../archivist/utils/templater.py) — `_try_resolve_expression(expr, ctx)` regex-based expression parser:
  - Handles `tp.namespace.method(args)` function calls
  - Handles `tp.namespace.property` property access
  - Handles `tp.frontmatter["key"]` subscript access
  - Handles `"static string"` literal expressions
  - Safe argument parsing via `ast.literal_eval` (no arbitrary code execution)
  - Returns None for unresolvable expressions (graceful degradation)

- [archivist/utils/templater.py](../archivist/utils/templater.py) — `resolve_value(value, ctx, warn_fn=None)` wraps the evaluator:
  - Finds all `<% %>` blocks in a string
  - Resolves each independently
  - Leaves unresolvable expressions verbatim
  - Returns `(result_str, fully_resolved)` tuple
  - Optional callback for warnings on unresolved expressions

#### tp Namespace Implementations ✅

**_TpDate**
- `now(fmt, offset, reference, reference_format)` — current date with offset
- `today(fmt)` — alias for now() with offset=0
- `tomorrow(fmt)` — alias for now(fmt, 1)
- `yesterday(fmt)` — alias for now(fmt, -1)
- `weekday(fmt, weekday, reference, reference_format)` — date of specific weekday

**_TpFile**
- `title` property — filename without extension
- `path(relative=False)` method — absolute or relative file path
- `folder(absolute=False)` method — parent directory name or absolute path
- `creation_date(fmt)` method — file birthtime (macOS) or ctime (Linux)
- `last_modified_date(fmt)` method — file mtime
- `content` property — raw file content (expensive)

**_TpFrontmatter**
- `__getitem__(key)` subscript access — dictionary-style lookup of frontmatter properties

#### TemplaterContext ✅
- [archivist/utils/templater.py](../archivist/utils/templater.py) — Context class holding `tp` object bound to a specific file
- Takes file_path and frontmatter dict at init
- Provides all three namespaces with correct per-file context

#### Format Translation ✅
- [archivist/utils/templater.py](../archivist/utils/templater.py) — `moment_to_strftime(fmt)` converts moment.js format tokens to Python strftime
- Covers ~95% of real-world tokens (YYYY, MM, DD, HH, mm, ss, A, Z, etc.)
- Passes through unrecognized tokens (user's problem if they use exotic locales)
- Handles longest-first matching to avoid prefix ambiguity

#### Integration Points ✅

**[archivist/commands/frontmatter/add.py](../archivist/commands/frontmatter/add.py)**
- `_resolve_new_line()` resolves Templater expressions in new property values
- Only runs in RESOLVE mode
- Builds TemplaterContext from target note's context (not a template context)
- Uses unmasked raw_fm for tp.frontmatter so cross-references have real values

**[archivist/commands/frontmatter/apply_template.py](../archivist/commands/frontmatter/apply_template.py)**
- `_resolve_template_defaults()` resolves expressions in template default values
- **Correctly resolves against target note context, not template file context** (critical detail in docstring)
- Only runs in RESOLVE mode
- Expressions that can't be resolved are left verbatim with warnings

#### Testing ✅
- [tests/unit/test_templater.py](../tests/unit/test_templater.py) — Comprehensive unit tests:
  - TemplaterMode enum parsing and defaults
  - Expression detection and extraction
  - tp.date methods with various format strings and offsets
  - tp.file methods with path edge cases (relative, absolute, nonexistent files)
  - tp.frontmatter subscript access
  - Moment.js format translation
  - Cross-reference resolution (tp.frontmatter["key"])
  - Error cases and graceful degradation

**Status:** Shipping. Phase 2 is complete and tested.

---

### Phase 3: `tp.frontmatter` and Cross-References ✅ **COMPLETE**

**What was needed:**
Self-referential access to other properties in the same file for computed fields like:
```yaml
sort-title: "The Example"
display-title: <% tp.frontmatter["sort-title"] %>
```

**What exists:**
- [archivist/utils/templater.py](../archivist/utils/templater.py) — `_TpFrontmatter` class with subscript access
- [archivist/utils/templater.py](../archivist/utils/templater.py) — `_try_resolve_expression()` handles `tp.frontmatter["key"]` syntax via `_FM_SUBSCRIPT_RE` regex
- [tests/unit/test_templater.py](../tests/unit/test_templater.py) — Tests for cross-reference resolution

**Limitation (documented and accepted):**
- Single-pass resolution only. Properties that are themselves expressions won't be resolved before cross-referencing them.
- Example: if `sort-title: <% tp.date.now() %>` and `display-title: <% tp.frontmatter["sort-title"] %>`, the second will see the unresolved expression string, not the date.
- This is acceptable for Phase 1; multi-pass resolution can be added later if users request it.

**Status:** Shipping. Phase 3 is complete and documented within its scope.

---

### Phase 4: Extended Coverage 🔲 **NOT STARTED**

**What was needed:**
Fallback evaluator for expressions that the Python regex parser can't handle, using the `dukpy` library (Duktape JS interpreter embedded in Python).

**Current Status:**
- No dependencies added to `pyproject.toml`
- No dukpy integration
- Not blocking ship; Phase 2's coverage is sufficient for the vast majority of real-world frontmatter usage

**When to do it:** If users report expressions using namespaces we don't implement (tp.system, tp.user, tp.obsidian, complex control flow, etc.), add this as a fallback. For now, unresolvable expressions are left verbatim with a warning.

**Status:** Deferred. Not required for initial ship.

---

## Critical Gaps Requiring Action

### 1. **remove.py — Missing mask/restore cycle** ❌

**File:** [archivist/commands/frontmatter/remove.py](../archivist/commands/frontmatter/remove.py)

**Current behavior:**
- Reads raw frontmatter
- Calls `remove_property_from_frontmatter(raw_fm, prop)` directly
- Writes result

**Required changes:**
1. Read config and get templater mode: `mode = get_templater_mode(config)`
2. If mode is not DISABLED, mask before removal: `masked_fm, mask_map = mask_templater_expressions(raw_fm)`
3. Perform removal on masked_fm: `updated_masked_fm, found = remove_property_from_frontmatter(masked_fm, prop)`
4. Restore: `updated_fm = restore_templater_expressions(updated_masked_fm, mask_map)`
5. Write `updated_fm` instead of `updated_fm`

**Pattern to follow:** [archivist/commands/frontmatter/add.py](../archivist/commands/frontmatter/add.py) lines 110–140 (existing frontmatter case)

**Effort:** ~10 lines of code, including imports

---

### 2. **rename.py — Missing mask/restore cycle** ❌

**File:** [archivist/commands/frontmatter/rename.py](../archivist/commands/frontmatter/rename.py)

**Current behavior:**
- Reads raw frontmatter
- Calls `_rename_property_in_raw_fm(raw_fm, old_prop, new_prop)` directly
- Updates via transformer closure

**Required changes:**
1. Read config in `run()`: `config = read_archivist_config(root)`
2. Get mode: `mode = get_templater_mode(config)`
3. In `_process_note()`, pass mode as parameter
4. In the transformer closure, mask before rename: `masked_fm, mask_map = mask_templater_expressions(raw_fm)`
5. Perform rename on masked_fm: `updated_masked_fm, found = _rename_property_in_raw_fm(masked_fm, old_prop, new_prop)`
6. Restore and return: `return f"---\n{restore_templater_expressions(updated_masked_fm, mask_map)}\n---\n{body}"`

**Pattern to follow:** [archivist/commands/frontmatter/apply_template.py](../archivist/commands/frontmatter/apply_template.py) lines 195–225 (transformer pattern)

**Effort:** ~15 lines of code, including config read in `run()` and transformer updates

---

### 3. **Testing gaps** ❌

**Missing integration tests for:**
- remove.py with PRESERVE mode (expression should survive unchanged)
- remove.py with RESOLVE mode (shouldn't apply; expressions in removed properties don't matter)
- remove.py with unresolvable expressions (shouldn't corrupt)
- rename.py with PRESERVE mode (expression should survive)
- rename.py with RESOLVE mode (expression in renamed property should survive)
- All three: DISABLED mode should leave expressions as-is

**Where:** `tests/integration/` (new file or add to existing integration test)

**Effort:** ~50 lines of test code covering the happy path + edge cases

---

## Files Modified vs. Touched

### Implemented ✅
- `archivist/utils/templater.py` — New file, fully implemented
- `archivist/commands/init.py` — Templater mode prompt added
- `archivist/commands/frontmatter/add.py` — Mask/restore integrated
- `archivist/commands/frontmatter/apply_template.py` — Mask/restore integrated
- `tests/unit/test_templater.py` — New test file, comprehensive

### Requiring Updates ❌
- `archivist/commands/frontmatter/remove.py` — Add mask/restore
- `archivist/commands/frontmatter/rename.py` — Add mask/restore
- `tests/integration/` — Add integration tests (or update existing)

### Not Touched
- `archivist/commands/frontmatter/__init__.py` — No changes needed; exports exist
- `archivist/utils/config.py` — No changes needed; already handles templater key
- `pyproject.toml` — No changes for Phase 1; Phase 4 would add optional `[templater-extended]`
- `.archivist/sample-changelog.py` — No changes needed for basic Templater support

---

## Shipping Checklist

- [x] Phase 0 (Config) — complete and tested
- [x] Phase 2 (Resolution Engine) — complete and tested
- [x] Phase 3 (Cross-references) — complete and tested
- [x] Phase 1 (Safe Preservation) — 60% complete
  - [x] Mask/restore infrastructure
  - [x] Integration in add.py and apply_template.py
  - [ ] Integration in remove.py ← **BLOCKING**
  - [ ] Integration in rename.py ← **BLOCKING**
  - [ ] Integration tests

### To Ship Phase 1 Fully:
1. Add mask/restore to remove.py (~10 lines)
2. Add mask/restore to rename.py (~15 lines)
3. Add integration tests (~50 lines)
4. Run full test suite
5. Update CHANGELOG

**Estimated time:** 30–45 minutes

---

## Unresolved Expressions: Reporting Strategy

### Current Approach (Implemented) ✅

When a Templater expression cannot be resolved in RESOLVE mode:

1. The expression is **left verbatim** in the output (graceful degradation)
2. A **warning is emitted to stdout/stderr** via the `warn_fn` callback
3. The `resolve_value()` function returns a `fully_resolved` boolean indicating whether all expressions succeeded

**Example warning:**
```
⚠️  Could not resolve Templater expression: <% tp.system.prompt("Name?") %> — leaving verbatim. This may require Obsidian.
```

This approach is sufficient for Phase 1 because:
- For small operations (single note), warnings appear immediately as they occur
- Users see exactly which expressions failed and why
- No additional filesystem I/O or report management overhead

### Alternative: `.archivist-unresolved` Report File (Deferred)

The original plan mentioned collecting unresolved expressions into a `.archivist-unresolved` report file instead of (or in addition to) warnings. This would be useful for:
- **Large batch operations** (e.g., applying a template to 100 notes) where you want to review failures in aggregate
- **Post-mortem analysis** of why certain defaults didn't resolve
- **Audit trails** — "what expressions in this repo are not being resolved?"

**Implementation (if needed in Phase 2+):**

1. Modify `resolve_value()` to optionally collect unresolved expressions:
   ```python
   def resolve_value(..., collect_unresolved: list[str] | None = None) -> tuple[str, bool]:
       # ... existing code ...
       if resolved is None and collect_unresolved is not None:
           collect_unresolved.append(full_token)
   ```

2. In `add.py`, `apply_template.py`, and future commands that use RESOLVE mode:
   - Create a list to collect unresolved expressions per command invocation
   - Pass it to `resolve_value()`
   - After all files are processed, write to `.archivist-unresolved` if the list is non-empty

3. File format (simple, one per line):
   ```
   # Generated by: archivist frontmatter apply-template 2026-05-15 14:32:10
   # Command: apply-template --template=template.md --path=vault/
   # Total unresolved: 3
   
   path/to/note.md
   Line: created: <% tp.system.prompt("Date?") %>
   Context: tp.system not implemented, requires Obsidian interactivity
   
   path/to/another.md
   Line: tags: <% custom.function() %>
   Context: custom namespace not recognized
   ```

4. Update CHANGELOG with guidance: "Run `archivist changelog` and check `.archivist-unresolved` to see what didn't resolve"

**Decision:** Defer to Phase 2. The current warning-based approach is simpler and covers the primary use case (immediate user feedback). If users report needing batch visibility into unresolved expressions, implement this enhancement then.

---

## Known Limitations (Documented)

- **Single-pass resolution:** Cross-references to other expressions won't resolve recursively
- **Delimiter configuration:** Archivist always uses `<% %>`. Templater's configurable delimiters are not detected from `.obsidian/` config
- **Non-standard namespaces:** `tp.system`, `tp.user`, `tp.obsidian`, and complex control flow expressions are left verbatim with warnings
- **Phase 4 not implemented:** No JavaScript fallback evaluator yet
- **No batch unresolved reporting:** Unresolved expressions are warned at resolution time; no `.archivist-unresolved` report file is written. (Deferred to Phase 2 if needed.)

All limitations are acceptable for the initial release and are documented to users.

---

## Next Steps

**Immediate:**
1. Implement remove.py masking
2. Implement rename.py masking
3. Add integration tests

**Follow-up (post-ship):**
1. Monitor for unresolvable expressions users encounter
2. If Phase 4 is needed, add dukpy fallback
3. If multi-pass resolution is requested, implement Phase 3 extension

