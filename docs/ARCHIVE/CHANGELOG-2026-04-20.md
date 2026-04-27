---
class: archive
category:
  - changelog
log-scope: general
modified: 2026-04-21
UUID: e5cadb6f-8205-425a-af62-f24c38273f28
commit-sha: 
files-modified: 7
files-created: 4
files-archived: 0
tags:
  - archivist-cli
---

# Changelog — 2026-04-21

## Overview

| Field | Value |
|-------|-------|
| Date | 2026-04-21 |
| Commit SHA | [fill in after commit] |
| Files Added | 4 |
| Files Modified | 7 |
| Files Archived | 0 |

## Changes

### Files Modified
- `.github/README.md`: added section for Templater support
- `archivist/commands/frontmatter/add.py`: augmented with Templater support
- `archivist/commands/frontmatter/apply_template.py`: augmented with Templater support
- `archivist/utils/__init__.py`: include `templater.py` utility module in barrel export
- `docs/ROADMAP.md`: updated Templater section to show completed/shipped
- `docs/TESTING_SPECIFICATION.md`: added `test_templater.py` detail
- `pyproject.toml`: bump version

### New Files Created
- `archivist/utils/templater.py`: new Templater support utility module
- `docs/ARCHIVE/CHANGELOG-2026-04-20.md`: this changelog
- `docs/LOGGING_AUGMENTATION_PLAN.md`: future implentation doc
- `tests/unit/test_templater.py`: new test module for Templater functions

### Files Removed / Archived
- No files archived


<!-- archivist:auto-end -->
## Notes

Feat: add Templater support for a subset of `tp` methods

- add `templater.py` for core utilities
- add `test_templater.py` to test suite
- update `add.py`, `apply_template.py` with Templater utilities
- update `README` and `ROADMAP` with templater feature sections

### Full detail

Let me read everything carefully before writing a line.Good. I have everything I need. Let me now generate all the deliverables: `templater.py`, the updated `__init__.py`, and the four modified command modules. I'll also produce the Logging Augmentation Plan as a separate document.No relevant skills to load here — pure Python generation. Let's build.Now the four command modules. These need the most care — reading config once in `run()`, threading mode down through `_process_note`, and getting the mask→parse→merge→render→restore→write order right in `apply_template`.`apply_template.py` is the complex one — the mask→parse→merge→render→restore→write order needs surgical precision.Now the logging augmentation plan as a markdown document:Now copy everything to outputs:Here's the full breakdown of what was built and the decisions worth knowing about.

---

#### `templater.py`

The new utility module. Everything Templater-related lives here and nowhere else.
This follow the `TEMPLATER_SUPPORT_PLAN.md` doc up to Phase 3. Phase 4 is being skipped indefinitely*. See below for details.

**`TemplaterMode` enum** — `RESOLVE`, `PRESERVE`, `DISABLED` (stored as `"false"` to match the config value). `from_config()` classmethod handles parsing and defaults to `PRESERVE` on anything unrecognized, because PRESERVE is the safest failure mode. `get_templater_mode(config)` is the one call site in every command's `run()`.

**Mask/restore cycle** — `mask_templater_expressions` replaces every `<% %>` block with `__ARCHIVIST_TMPL_0__`-style sentinels and returns a `{sentinel: original}` map. `restore_templater_expressions` takes an optional `resolved` dict — sentinels with resolved values get substituted, everything else falls back to the original expression verbatim. This means RESOLVE and PRESERVE use the exact same restore call; the only difference is whether you pass a `resolved` dict.

**`resolve_value`** — returns `(result_str, fully_resolved: bool)` so callers can tell whether everything resolved cleanly. Takes an optional `warn_fn` callable — every command wires `warning` from the barrel in here, so unresolvable expressions emit a ⚠️ to stderr via the existing output infrastructure.

**`_try_resolve_expression`** — handles four expression forms: static string literals, `tp.frontmatter["key"]` subscript access, `tp.namespace.method(args)` function calls, and `tp.namespace.property` access. `_parse_args` uses `ast.literal_eval` wrapped in a tuple — safe, no arbitrary eval, handles string/int/bool/None args cleanly.

**`moment_to_strftime`** — token regex sorted longest-first to prevent prefix collisions (e.g. `YYYY` matches before `YY`). Covers the ~95% of real-world tokens. Passes through anything unrecognized rather than crashing.

---

#### Command modules — what changed and why

**All four** now read config in `run()` via `read_archivist_config(root)` + `get_templater_mode(config)`, then thread `mode` down to `_process_note`. One read per run, not per file.

**`remove` and `rename`** — RESOLVE degrades to PRESERVE silently and correctly. There's nothing to resolve when you're deleting a key or renaming one without touching its value. This is documented in the docstrings rather than having a code branch that does nothing.

**`add`** — has the two-branch structure (with/without existing frontmatter). Both branches handle the new-line resolution case. For the "no existing frontmatter" branch, `TemplaterContext` is constructed with an empty dict since there's no existing FM to draw from.

**`apply_template.py`** — the most interesting one. `_load_note` now returns `(entries, raw_fm, body)` instead of `(entries, body)` — raw FM is needed to build a `TemplaterContext` for `tp.frontmatter` resolution without re-reading the file. `_resolve_template_defaults` only runs in RESOLVE mode and always uses the *target note's* context, which is stated explicitly in the docstring because that's the kind of thing that causes subtle bugs when someone reads it six months from now and wonders. The nine-step operation order in `_process_note`'s docstring is the written-down contract for the mask→parse→merge→render→restore→write sequence.

#### Why is Phase 4 being skipped?

Phase 4 exists to handle expressions that Option A's (from `TEMPLATER_SUPPORT_PLAN.md`) regex parser *can't* match — things like ternary expressions, string concatenation, or other JS-isms that would appear in `<% %>` blocks but fall outside the `tp.namespace.method(args)` / `tp.namespace.property` grammar. In practice, those constructs essentially never appear in frontmatter values. They belong in note bodies and user scripts.

The graceful degradation contract already handles this correctly: anything `_try_resolve_expression` can't match returns `None`, which leaves the expression verbatim with a warning. That's the right behavior whether or not `dukpy` is available.

Phase 4 would only become worth revisiting if users are regularly hitting that warning on legitimate frontmatter expressions that the regex parser can't handle. That's a data problem — wait for the complaints, then evaluate. Adding a 40MB optional dependency speculatively, for an edge case that may never materialize in real vaults, is exactly the kind of thing the constraints doc was written to prevent.

Leave it in the plan as a documented option, ship nothing, and forget about it until someone screams.

#### Templater tests

**`archivist/tests/unit/test_templater.py`**

**`TestTemplaterMode`** — pins the enum values against the actual config strings that get written to `.archivist`. If someone renames a member or changes a value, these fail loudly before anything reaches disk.

**`TestHasTemplaterExpression` / `TestExtractExpressions`** — detection is the gate before everything else. A false negative here means expressions skip masking entirely and get chewed up by YAML parsing downstream.

**`TestMaskTemplaterExpressions` / `TestRestoreTemplaterExpressions`** — the mask/restore cycle is the most load-bearing piece of the whole implementation. The roundtrip identity test, the partial-resolution test, and the "sentinel must never reach output" test are the three that will catch real regressions. The YAML-hostile characters test (colons, braces, hashes) is in both the main section and the edge cases section from different angles — intentionally, because that's the failure mode that eats frontmatter silently.

**`TestMomentToStrftime`** — specifically includes the prefix-precedence test because that's the one subtle bug in any token-replacement scheme: `YYYY` becoming `%y%y` instead of `%Y` if the shorter token wins.

**`TestResolveValue`** — covers the full public contract: successful resolution, verbatim fallback, `warn_fn` called exactly when it should be, `warn_fn=None` doesn't raise, mixed plain/expression values, `fully_resolved` return value accurate. The `test_context_constructed_with_dict_subtype_does_not_raise` in edge cases directly pins the Mapping fix you just landed.

**`TestTpDate`** — reference date tests are included specifically because the `tp.date.now(format, offset, reference, reference_format)` signature is the one most likely to have argument-parsing bugs (`_parse_args` via `ast.literal_eval`). The malformed-reference fallback test pins the graceful degradation contract.

---

#### Logging Augmentation Plan

Scoped to additive changes only — the five output functions stay as the public API, `output.py` gets a logger routed through them internally, `cli.py` gets `_configure_logging()` and three root-level flags (`--quiet`, `--verbose`, `--log-file`). No dependencies, no behavioral change at default verbosity. The open questions on `--verbose` vs `--debug` naming and spinner suppression are flagged for you to decide before implementation.

---

*This changelog was automatically generated by Archivist CLI.*
*See [Archivist CLI](https://github.com/lvnacy-notes/archivist-cli) for more information.*
