# Logging Augmentation Plan

A design document for replacing Archivist's ad-hoc terminal output layer with
a coherent, structured logging strategy. This is scoped planning only — nothing
here gets built until you say so.

---

## The Current Situation

`archivist/utils/output.py` provides five terminal output functions:
`progress`, `success`, `warning`, `error`, plus `print_dry_run_header` and
`get_action_verb`. These are imported from the barrel and used throughout every
command and utility module.

This works fine for interactive terminal use. It does not work for:

- **Piped output or scripting** — no way to suppress informational noise while
  keeping errors
- **Debugging** — no structured data attached to messages, no timestamps, no
  call-site information
- **Log files** — `progress` goes to stdout, `warning` and `error` go to stderr;
  there's no log-to-file path
- **Third-party integrations** — LVNACY Apparatus tooling or any future caller
  that wants machine-readable output has to scrape stdout
- **Verbosity control** — there's currently no `--verbose` or `--quiet` flag;
  every run at the same noise level regardless of user intent

The five functions are not going away — they're user-facing and their output
format is part of the product. The question is what sits beneath them.

---

## What We Actually Need

**Structured log levels mapped to the existing output functions:**

| Function         | Semantic Level | Current destination |
|------------------|---------------|---------------------|
| `error()`        | ERROR         | stderr              |
| `warning()`      | WARNING       | stderr              |
| `success()`      | INFO          | stdout              |
| `progress()`     | INFO / DEBUG  | stdout              |
| `print_dry_run_header()` | INFO  | stdout              |

`progress()` does double duty as both informational headings and debug-level
chatter. That conflation is the single most useful thing to fix — separating
structural progress output from per-file verbose noise enables `--quiet` mode.

**What we want from an augmented system:**

1. **Verbosity tiers** — `--quiet` (errors only), default (current behavior),
   `--verbose` (per-file debug output currently suppressed)
2. **Optional log file** — `--log-file <path>` captures everything regardless
   of verbosity tier, with timestamps and levels, without cluttering the terminal
3. **Structured call-site data for debug messages** — filename, line number when
   `--verbose` is active; not needed for user-facing output
4. **Zero behavioral change at default verbosity** — existing output is correct.
   The point is additive, not corrective.

---

## Proposed Architecture

### Layer 1 — Python's `logging` module (the plumbing)

Python's standard `logging` module handles levels, handlers, formatters, and
routing. It's the right tool and it's already in stdlib. We are not bringing in
`structlog`, `loguru`, or any other logging dependency — the problem does not
require them.

A single named logger: `logging.getLogger("archivist")`.

### Layer 2 — `output.py` becomes a thin facade

The five existing output functions remain as the public API. Internally, each
one calls through to the underlying logger at the appropriate level AND still
does its formatted terminal print. This keeps the user-visible contract
unchanged while routing everything through a structured backend.

```python
# output.py after augmentation (sketch)

import logging
_log = logging.getLogger("archivist")

def progress(msg: str) -> None:
    _log.debug(msg)           # underlying log at DEBUG
    print(msg)                # terminal output unchanged (at default verbosity)

def success(msg: str) -> None:
    _log.info(msg)
    print(f"✅ {msg}")

def warning(msg: str) -> None:
    _log.warning(msg)
    print(f"⚠️  {msg}", file=sys.stderr)

def error(msg: str) -> None:
    _log.error(msg)
    print(f"❌  {msg}", file=sys.stderr)
```

Terminal output is controlled by the log level set on the handler — at `--quiet`
the StreamHandler is set to ERROR; at `--verbose` it's DEBUG; default is INFO.

### Layer 3 — Handler configuration in `cli.py`

`cli.py`'s `main()` function configures logging before routing to any command:

```python
# cli.py main() — sketch

import logging

def _configure_logging(args: argparse.Namespace) -> None:
    """
    Set up the archivist logger based on CLI flags.
    Called once, before any command module is imported or run.
    """
    logger = logging.getLogger("archivist")
    logger.setLevel(logging.DEBUG)  # capture everything at the logger level

    # Terminal handler — level depends on verbosity flags
    terminal_handler = logging.StreamHandler()
    if getattr(args, "quiet", False):
        terminal_handler.setLevel(logging.ERROR)
    elif getattr(args, "verbose", False):
        terminal_handler.setLevel(logging.DEBUG)
    else:
        terminal_handler.setLevel(logging.INFO)
    logger.addHandler(terminal_handler)

    # Optional file handler
    log_file = getattr(args, "log_file", None)
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(message)s")
        )
        logger.addHandler(file_handler)
```

### Layer 4 — Global CLI flags (new additions to `cli.py`)

Three new flags on the root parser (not per-subcommand — they apply globally):

```
--quiet       Suppress all output except errors
--verbose     Enable per-file debug output
--log-file    Write full debug log to a file at this path
```

These go on the root `parser`, not on subparsers, so they're available to every
command without touching the per-subcommand parser definitions.

---

## Impact on Existing Code

**`output.py`** — modified internally, public API unchanged. No callers change.

**`cli.py`** — `main()` calls `_configure_logging(args)` before routing.
Three new arguments on the root parser.

**Command modules** — no changes. They call `progress()`, `warning()`, etc.
exactly as they do now.

**`git.py`** — currently uses `logging.getLogger(__name__)` and `logger.error()`
directly for git subprocess errors. This is correct and fine. After augmentation,
those calls route through the same `archivist` logger hierarchy automatically
(since `archivist.utils.git` is a child of `archivist`). No changes needed.

---

## What Changes at Each Verbosity Tier

### Default (current behavior — no flags)

Exactly what runs today. `progress()` prints to stdout, `success()` prints
with ✅, `warning()` and `error()` go to stderr. Nothing changes for the user.

### `--quiet`

Only `error()` output reaches the terminal. Useful for scripting, cron jobs,
any context where you care about failures but not the play-by-play.

### `--verbose`

Currently, `progress()` mixes structural messages ("Scanning 47 file(s)...")
with per-file noise ("  [dry-run] Would add 'status' to: notes/foo.md"). In
verbose mode, the per-file lines can be promoted from a simple `progress()` call
to an explicit `_log.debug()` call so they only appear at `--verbose`. This
requires touching a few lines in the command modules, but only to change
`progress(f"  [dry-run] ...")` to `_log.debug(...)` — the output content stays.

Implementation note: this is the one part that requires changes in command
modules. It's optional for Phase 1 of this plan — the tiered verbosity still
works without it, you just see everything in default mode as you do today.

### `--log-file <path>`

Full debug log with timestamps and log levels written to the specified path,
regardless of terminal verbosity. Format:

```
2026-04-15 14:32:01,847 DEBUG    Scanning 47 file(s) to add 'status'...
2026-04-15 14:32:01,851 DEBUG    [dry-run] Would add 'status' to: notes/foo.md
2026-04-15 14:32:01,852 WARNING  Could not resolve Templater expression: ...
2026-04-15 14:32:01,903 INFO     Done. 12/47 file(s) would be updated.
```

This is where Templater's "unresolvable expression" warnings become genuinely
useful — you can run a bulk apply-template, pipe the terminal output away,
and review the log file for anything that needs manual Obsidian resolution.

---

## What This Is Not

- **Not a rewrite of output.py.** The five functions stay. Their terminal
  behavior stays. The logging infrastructure is additive.
- **Not `structlog` or `loguru`.** Those libraries are good; they're also a
  dependency, and the stdlib does everything we need here.
- **Not per-command log configuration.** One logger, configured once in `main()`.
  Commands don't touch the logger directly — they call the output functions.
- **Not a breaking change for any existing caller.** Every command module's
  import list stays the same. The barrel export stays the same. The output
  function signatures stay the same.

---

## Files to Create / Modify

| File | Change |
|------|--------|
| `archivist/utils/output.py` | Route existing functions through `logging.getLogger("archivist")` |
| `cli.py` | Add `_configure_logging()`, call it in `main()`, add three root-level args |
| `archivist/commands/frontmatter/*.py` | Optional: demote per-file dry-run lines from `progress()` to `_log.debug()` for cleaner `--quiet` behavior |
| `archivist/utils/git.py` | No changes — already uses stdlib logging correctly |

---

## Implementation Order

1. Augment `output.py` — route through logger, no behavioral change yet
2. Add `_configure_logging()` to `cli.py` and wire the three flags
3. Test: default run identical to current; `--quiet` suppresses progress; `--verbose` shows debug; `--log-file` captures everything
4. Optional cleanup: demote noisy per-file `progress()` calls in command modules to explicit debug-level calls

Steps 1-3 are a single PR. Step 4 is a follow-up and can be done piecemeal as
commands are touched for other reasons.

---

## Open Questions

1. **`--verbose` vs `--debug`.** `--verbose` is friendlier for users. `--debug`
   is more honest about what it does. Leaning toward `--verbose` for the flag
   name with `--debug` as an alias. Worth deciding before implementation so the
   help text doesn't need to change later.

2. **Spinner interaction.** `output.py`'s `spinner()` context manager writes
   directly to stdout via `sys.stdout.write`. In `--quiet` mode, should the
   spinner be suppressed? Almost certainly yes. Implementation: check the
   configured log level before starting the spin thread, or add a `quiet` flag
   to the spinner itself. Low priority — the spinner is used in manifest, not
   frontmatter commands.

3. **Warning accumulation for Templater.** When `--log-file` is active, every
   unresolvable Templater expression warning is captured. Without a log file,
   they still go to stderr via `warning()`. This is correct and sufficient for
   Phase 1. If users want a dedicated `.archivist-unresolved` report (mentioned
   in the Templater support plan's open questions), that's a separate feature
   that sits on top of this logging infrastructure rather than alongside it.