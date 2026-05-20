---
class: planning-doc
category:
  - infrastructure
affiliations:
created:
modified: 2026-05-15
version:
status:
related:
tags:
  - archivist
  - custodian
---
# Custodian Implementation Checklist

Tracking document for the Custodian logging augmentation across both phases.
Check boxes as work completes. Notes column is for anything that bites you mid-implementation.

---

## Phase 1 — Single PR

### Core Infrastructure

| # | File | Task | Status | Notes |
|---|------|------|--------|-------|
| 1.1 | `formatter.py` | Add `SUCCESS` level, `ArchivistTerminalFormatter`, `ArchivistFileFormatter`, `ArchivistStreamHandler` | ✅ Done | Completed during logging augmentation prep, in commit ed5ece0 |
| 1.2 | `utils/output.py` | Rewire all five output functions to pure logger facade; remove all direct `print()` calls | ✅ Done | `print_dry_run_header()` becomes `log.info()`; spinner untouched |
| 1.3 | `cli.py` | Add `_configure_logging()`; call it in `main()` before routing; add `--quiet`, `--verbose`/`--debug`, `--log-file` to root parser | ✅ Done | Flags go on root parser only — do not touch subparsers |

### Utility Module print() Audit

| #   | File                   | Task                                                                            | Status | Notes                                                                |
| --- | ---------------------- | ------------------------------------------------------------------------------- | ------ | -------------------------------------------------------------------- |
| 1.4 | `utils/git.py`         | Replace raw `print()` calls with output functions; `logger` → `log` from barrel | ✅ Done | `logger.error()` calls stay as-is — they already propagate correctly |
| 1.5 | `utils/changelog.py`   | Replace raw `print()` calls with output functions                               | ✅ Done |                                                                      |
| 1.6 | `utils/config.py`      | Replace raw `print()` calls with output functions                               | ✅ Done |                                                                      |
| 1.7 | `utils/frontmatter.py` | Replace raw `print()` calls with output functions                               | ✅ Done |                                                                      |
| 1.8 | `utils/note_filter.py` | Replace raw `print()` calls with output functions                               | ✅ Done | `_die()` helper — single call site, straightforward `error()` swap   |

---

## Phase 2 — Follow-up, Piecemeal

### Command Module print() Audit

| #   | File                                   | Task                                                        | Status | Notes                                                                                                   |
| --- | -------------------------------------- | ----------------------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------- |
| 2.1 | `commands/changelog/changelog_base.py` | Replace raw `print()` calls with output functions           | ✅ Done | Dry-run content preview (`print(changelog_content)`) stays as `print()` — it is output, not a log event |
| 2.2 | `commands/changelog/library.py`        | Replace raw `print()` calls with output functions           | ✅ Done | All in `print_summary()`                                                                                |
| 2.3 | `cli.py`                               | Replace three routing `print()` calls with output functions | ✅ Done | Module-type routing and plugin detection lines                                                          |

### Verbosity Demotions

| #   | File                                     | Task                                                              | Status | Notes                                                                           |
| --- | ---------------------------------------- | ----------------------------------------------------------------- | ------ | ------------------------------------------------------------------------------- |
| 2.4 | `commands/frontmatter/apply_template.py` | Demote per-file `progress()` calls to `log.debug()`               | ✅ Done | `[dry-run]` line in `_process_note` demoted; structural run() lines (root, template, filters, scanning, done) stay at INFO                               |
| 2.5 | `commands/changelog/changelog_base.py`   | Demote structural `progress()` calls that are debug-level chatter | ✅ Done | Repo root and output dir lines demoted; existing/new changelog lines, dry-run target, and summary lines stay at INFO                                    |
| 2.6 | Remaining command modules                | Audit for `progress()` calls that belong at debug level           | ✅ Done | Do piecemeal as modules are touched for other reasons; remaining: `manifest.py` |

---

## Watch Out For

- [x] **`git.py` has two logging mechanisms in flight.** It uses `logger = logging.getLogger(__name__)` for subprocess errors alongside raw `print()` for user-facing output. Phase 1 replaces the `print()` calls with output functions. The `logger.error()` calls are fine and stay — they propagate through the `archivist` hierarchy correctly. Do not consolidate them into `log` from output.py; they carry call-site information that the output functions don't.

- [x] **`_configure_logging()` must fire before any command module is imported.** `main()` in `cli.py` does lazy imports inside each routing branch. `_configure_logging(args)` goes immediately after `args = parser.parse_args()`, before the first `if args.command ==` branch. If it fires after a command module has already emitted output, the first few lines of every run will bypass the handler configuration.

- [ ] **The dry-run content preview in `changelog_base.py` is not a log event.** The block that prints `changelog_content` to stdout during `--dry-run` is showing the user what would be written. It stays as `print()`. Everything around it (the header, the "Would write to" line) routes through output functions normally.

- [ ] **Interactive prompts are not log events.** The `input()` calls in `git.py` (`prompt_out_of_scope_changes`) and `changelog_base.py` (`_wait_for_save_confirmation`) write to stdout directly as part of user interaction. The surrounding `print()` calls that frame the prompt (the warning line listing out-of-scope files, the "Aborted." line) should become output function calls. The `input()` itself does not.

- [x] **`SUCCESS` is imported from `formatter.py`, not defined in `output.py`.** When wiring `output.py`, import `SUCCESS` from `archivist.formatter`. Do not redefine it. One definition, one place.

- [x] **`--verbose` and `--debug` are one argument, not two.** In argparse: `parser.add_argument("--verbose", "--debug", dest="verbose", ...)`. Both flags set `args.verbose`. `_configure_logging()` only checks `args.verbose`. There is no `args.debug`.

- [x] **`output.py` must be fully wired before Phase 1 is considered done.** The `--quiet` flag does nothing useful until `output.py` stops printing directly. 1.2 is the load-bearing step — 1.3 is wasted work without it.