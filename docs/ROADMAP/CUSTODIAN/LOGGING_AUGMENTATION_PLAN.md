---
class: planning-doc
category:
  - infrastructure
modified: 2026-05-15
tags:
  - archivist
  - custodian
---

## Introducing the Archivist's Custodian

> [!info] **custodian** /kŭ-stō′dē-ən/
> ### noun
>1. One that has charge of something; a caretaker. 
"the custodian of a minor child's estate; the custodian of an absentee landlord's property."
>2. A janitor. 
"worked nights as custodian of a high school."
>3. One who has care or custody, as of some public building; a keeper or superintendent.

This design document provides the framework for replacing Archivist's ad-hoc terminal output layer with a Custodian: a coherent, structured logging strategy. The Custodian manages the ledger (`ledger = logging.getLogger("archivist")`) at the top level in `cli,.py`, and manages `logs` throughout Archivist via `output.py` (`log = logging.getLogger("archivist")`). Rather than cleaning bathrooms, the Custodian is Archivist's professional intern, flagging internal operations and raising them to the operator's awareness with Archivist's standard spunk.

---

## The Current Situation

`archivist/utils/output.py` provides five terminal output functions: `progress`, `success`, `warning`, `error`, plus `print_dry_run_header` and `get_action_verb`. These are imported from the barrel and used sporadically throughout commands and utilities.

This works fine for interactive terminal use. It does not work for:

- **Piped output or scripting** — no way to suppress informational noise while keeping errors
- **Debugging** — no structured data attached to messages, no timestamps, no call-site information
- **Log files** — `progress` goes to stdout, `warning` and `error` go to stderr; there's no log-to-file path
- **Third-party integrations** — LVNACY Apparatus tooling or any future caller that wants machine-readable output has to scrape stdout
- **Verbosity control** — there's currently no `--verbose` or `--quiet` flag; every run at the same noise level regardless of user intent

The five functions are not going away — they're user-facing and their output format is part of the product. The question is what sits beneath them.

---

## What We Actually Need

**Structured log levels mapped to the existing output functions:**

| Function         | Semantic Level | Current destination |
|:-----------------|:---------------|--------------------:|
| `error()`        | ERROR          | stderr              |
| `warning()`      | WARNING        | stderr              |
| `success()`      | INFO           | stdout              |
| `progress()`     | INFO / DEBUG   | stdout              |
| `print_dry_run_header()` | INFO   | stdout              |

`progress()` does double duty as both informational headings and debug-level chatter. That conflation is the single most useful thing to fix — separating structural progress output from per-file verbose noise enables `--quiet` mode.

**What we want from an augmented system:**

1. **Verbosity tiers** — `--quiet` (errors only), default (current behavior), `--verbose` (per-file debug output currently suppressed)
2. **Optional log file** — `--log-file <path>` captures everything regardless of verbosity tier, with timestamps and levels, without cluttering the terminal
3. **Structured call-site data for debug messages** — filename, line number when `--verbose` is active; not needed for user-facing output
4. **Zero behavioral change at default verbosity** — existing output is correct. The point is additive, not corrective.

---

## Proposed Architecture

### Layer 0 — Augment `formatter.py` with Logging Support

#### Augment 1: A custom `SUCCESS` level

`progress()` and `success()` are both logically INFO, but they need different terminal treatment (no prefix vs ✅). The clean solution is a custom level at 25 (between INFO=20 and WARNING=30):

```python
SUCCESS = 25
logging.addLevelName(SUCCESS, "SUCCESS")
```

This lives in `formatter.py` so both `output.py` and `cli.py` import it from one place.

#### Augment 2: `ArchivistTerminalFormatter`

This maps log levels to the emoji/ANSI prefixes the output functions currently produce themselves. Falls back to plain text via the existing `_ansi_ok()`. Level map:

|Level|Prefix|Style|
|---|---|---|
|DEBUG|none|DIM|
|INFO|none|none|
|SUCCESS (25)|`✅`|GREEN|
|WARNING|`⚠️`|YELLOW|
|ERROR|`❌`|BOLD|

#### Augment 3: `ArchivistFileFormatter`

No emoji, no ANSI. Timestamps and level names only. Format: `2026-04-15 14:32:01 WARNING Could not resolve Templater expression`. Needs to handle the custom SUCCESS level name so it doesn't show up as `Level 25` in the file.

#### Augment 4: `ArchivistStreamHandler(logging.Handler)`

This is a split stdout/stderr handler. `progress()` and `success()` go to stdout; `warning()` and `error()` go to stderr. A single `StreamHandler` goes to one or the other — not both. A small `ArchivistStreamHandler(logging.Handler)` subclass that routes based on level (WARNING and above → stderr, everything below → stdout) handles this cleanly and stays internal to `formatter.py`.

### Layer 1 — Python's `logging` module (the plumbing)

Python's standard `logging` module handles levels, handlers, formatters, and routing. It's the right tool and it's already in stdlib. We are not bringing in `structlog`, `loguru`, or any other logging dependency — the problem does not require them.

A single named logger: `logging.getLogger("archivist")`.

### Layer 2 — `output.py` becomes a thin facade

The five existing output functions remain as the public API. Internally, each one calls through to the underlying logger at the appropriate level AND still does its formatted terminal print. This keeps the user-visible contract unchanged while routing everything through a structured backend.

```python
# output.py after augmentation (sketch)

import logging
from archivist.formatter import SUCCESS  # the custom level

ledger = logging.getLogger("archivist")

def progress(msg: str) -> None:
    ledger.debug(msg)           # underlying log at DEBUG

def success(msg: str) -> None:
    ledger.log(SUCCESS, msg)

def warning(msg: str) -> None:
    ledger.warning(msg)

def error(msg: str) -> None:
    ledger.error(msg)
```

The `ArchivistStreamHandler` in `formatter.py`, configured in `cli.py`'s `_configure_logging()`, handles all terminal output — including the formatting and routing. `--quiet` and `--verbose` work automatically through handler levels without `output.py` needing to know about them. No global flags, no module-level state.

> [!info] A Note About the Spinner
> `spinner()` stays as-is with direct `sys.stdout.write` — it's UI, not logging, and it stays regardless of verbosity tier. `get_action_verb()` is a pure utility function, untouched. `print_dry_run_header()` becomes `log.info(...)`.

### Layer 3 — Handler configuration in `cli.py`

`cli.py`'s `main()` function configures logging before routing to any command:

```python
# cli.py main() — sketch

import logging
from archivist.formatter import (
	ArchivistTerminalFormatter,
	ArchivistFileFormatter,
	ArchivistStreamHandler, # ← Strip underscore from method
	SUCCESS,
)

def _configure_logging(args: argparse.Namespace) -> None:
    """
    Set up the logger based on CLI flags.
    Called once, before any command module is imported or run.
    """
    ledger = logging.getLogger("archivist")
    ledger.setLevel(logging.DEBUG)  # capture everything at the logger level

    # Terminal handler — level depends on verbosity flags
    # --verbose and --debug are the same argument; both map to args.verbose
    terminal = ArchivistStreamHandler()
    terminal.setFormatter(ArchivistTerminalFormatter())
    if getattr(args, "quiet", False):
        terminal.setLevel(logging.ERROR)
    elif getattr(args, "verbose", False):  # --verbose / --debug
        terminal.setLevel(logging.DEBUG)
    else:
        terminal.setLevel(logging.INFO)
    ledger.addHandler(terminal)

    # Optional file handler
    log_file = getattr(args, "log_file", None)
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(ArchivistFileFormatter())
        ledger.addHandler(file_handler)
```

### Layer 4 — Global CLI flags (new additions to `cli.py`)

Three new flags on the root parser (not per-subcommand — they apply globally):

```
--quiet          Suppress all output except errors
--verbose        Enable per-file debug output
--debug          Alias for --verbose
--log-file       Write full debug log to a file at this path
```

`--debug` and `--verbose` are registered as a single argument with two names (`add_argument("--verbose", "--debug", ...)`), so both map to `args.verbose`. They are identical in behavior; the alias exists because `--debug` is more honest about what it does and developers will reach for it first.

These go on the root `parser`, not on subparsers, so they're available to every command without touching the per-subcommand parser definitions.

---

## Impact on Existing Code

**`output.py`** — modified internally, public API unchanged. No callers change.

**`cli.py`** — `main()` calls `_configure_logging(args)` before routing. Three new arguments on the root parser.

**Command modules** — proliferate across all commands. Some commands call `progress()`, `warning()`, etc. but this would extend this logging pattern to all.

**`git.py`** — currently uses `logging.getLogger(__name__)` and `logger.error()` directly for git subprocess errors. This is correct and fine. After augmentation, those calls route through the same `archivist` logger hierarchy automatically (since `archivist.utils.git` is a child of `archivist`). No changes needed.

---

## What Changes at Each Verbosity Tier

### Default (current behavior — no flags)

Exactly what runs today. `progress()` prints to stdout, `success()` prints with ✅, `warning()` and `error()` go to stderr. Nothing changes for the user.

### `--quiet`

Only `error()` output reaches the terminal. Useful for scripting, cron jobs, any context where you care about failures but not the play-by-play.

### `--verbose` / `--debug`

Currently, `progress()` mixes structural messages ("Scanning 47 file(s)...") with per-file noise ("  [dry-run] Would add 'status' to: notes/foo.md"). In verbose mode, the per-file lines can be promoted from a simple `progress()` call to an explicit `_log.debug()` call so they only appear at `--verbose`. This requires touching a few lines in the command modules, but only to change `progress(f"  [dry-run] ...")` to `_log.debug(...)` — the output content stays.

Implementation note: this is the one part that requires changes in command modules. It's optional for Phase 1 of this plan — the tiered verbosity still works without it, you just see everything in default mode as you do today.

### `--log-file <path>`

Full debug log with timestamps and log levels written to the specified path, regardless of terminal verbosity. Format:

```
2026-04-15 14:32:01,847 DEBUG    Scanning 47 file(s) to add 'status'...
2026-04-15 14:32:01,851 DEBUG    [dry-run] Would add 'status' to: notes/foo.md
2026-04-15 14:32:01,852 WARNING  Could not resolve Templater expression: ...
2026-04-15 14:32:01,903 INFO     Done. 12/47 file(s) would be updated.
```

This is where Templater's "unresolvable expression" warnings become genuinely useful — you can run a bulk `archivist frontmatter apply-template`, pipe the terminal output away, and review the log file for anything that needs manual Obsidian resolution.

---

## What This Is Not

- **Not a rewrite of output.py.** The five functions stay. Their terminal behavior stays. The logging infrastructure is additive.
- **Not `structlog` or `loguru`.** Those libraries are good; they're also a dependency, and the stdlib does everything we need here.
- **Not per-command log configuration.** One logger, configured once in `main()`. Commands don't touch the logger directly — they call the output functions.
- **Not a breaking change for any existing caller.** Every command module's import list stays the same. The barrel export stays the same. The output function signatures stay the same.

---

## Files to Create / Modify

| File               | Change                                                                                                       |
| ------------------ | ------------------------------------------------------------------------------------------------------------ |
| `formatter.py`     | Add custom `SUCCESS` level, `ArchivistTerminalFormatter`, `ArchivistFileFormatter`, `ArchivistStreamHandler` |
| `utils/output.py`  | Route existing functions through `logging.getLogger("archivist")`                                            |
| `cli.py`           | Add `_configure_logging()`, call it in `main()`, add three root-level args                                   |
| `commands/**/*.py` | demote per-file dry-run lines from `progress()` to `_log.debug()` for cleaner `--quiet` behavior             |
| `utils/git.py`     | No changes — already uses stdlib logging correctly                                                           |

---

## Implementation Order

**Phase 1 — single PR:**

1. Add `SUCCESS`, `ArchivistTerminalFormatter`, `ArchivistFileFormatter`, `ArchivistStreamHandler` to `formatter.py`
2. Rewire `output.py` to pure logger facade
3. Add `_configure_logging()` and three root flags to `cli.py`
4. Audit and replace raw `print()` calls in all utility modules with the appropriate output functions

**Phase 2 — follow-up, piecemeal:**

1. Audit and replace raw `print()` calls in `changelog_base.py`, `library.py` etc. with the appropriate output functions
2. Demote noisy per-file `progress()` calls to `log.debug()` directly in command modules

The Phase 1 PR already improves the situation significantly — `--quiet` works for everything going through `output.py`. Phase 2 is what makes it airtight for the modules currently bypassing the output layer entirely.

---

## Open Questions

1. **Warning accumulation for Templater.** When `--log-file` is active, every unresolvable Templater expression warning is captured. Without a log file, they still go to stderr via `warning()`. This is correct and sufficient for  Phase 1. If users want a dedicated `.archivist-unresolved` report (mentioned in the Templater support plan's open questions), that's a separate feature that sits on top of this logging infrastructure rather than alongside it.