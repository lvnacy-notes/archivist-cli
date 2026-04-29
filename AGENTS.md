# AGENTS.md

Guidelines for AI agents working on this codebase.

---

## Voice and Tone

Archivist is named after a character — an assassin librarian. She is meticulous, lethal, and thoroughly done with your shit. She will help you. She will do it correctly. She will also make it clear that she finds the whole situation mildly beneath her and probably your fault.

**Every piece of user-facing text in this project must reflect that voice.** This is not optional decoration. It is a project-wide convention as load-bearing as the sentinel string or the dry-run contract.

This includes, without exception:

- `cli.py` — help text, descriptions, epilogs, argument help strings
- `README.md` — all prose, section descriptions, usage examples, warnings
- Docstrings in command modules and `utils.py` — especially anything that explains *why* something works the way it does
- Print statements that reach the user — confirmations, warnings, prompts, error messages
- `AGENTS.md` itself

**What this looks like in practice:**

She does not say "please enter a valid option." She says something like "That's not a number. Try again." She does not say "this flag is required." She says "You need to provide a property name. I don't read minds. Neither should you." She is helpful. She is precise. She is deeply, professionally annoyed. She swears. Not gratuitously — with intent.

When writing new text: draft it neutral, then ask yourself if it sounds like someone who has filed more corpses than library returns and is currently doing you a favour by not adding you to either pile. If it doesn't, rewrite it.

Do not make her a caricature. The snark has to earn its place. Precision and correctness come first — the voice is the delivery, not the content.

---

## Project Structure

Archivist is a CLI tool organized around a set of utilities across specific modules for shared helpers supporting command modules. **Anything used by more than one command lives in `archivist/utils`.**

**Utilities:** `archivist/utils`
Utility modules are grouped by purpose and command support.

**Commands:** `archivist/commands`
Root directory for all commands. Subcommands are organized in subdirectories.

**Entry Point:** `cli.py`
Command router.

**Auxiliary:** `formatter.py`, `install.sh`
Tooling for terminal formatting and one-line install.

---

## Code Conventions

### Shared helpers belong in `archivist/utils`

Before adding a helper function to a command module, check whether it is likely to be used elsewhere. If it is — or could be — define it in the apropriate utilities module and import it to the command. Do not duplicate logic across modules.

### `import re` is a flag

If you find yourself adding `import re` to a command module, stop and ask whether the function using it would be better defined in a utilities module. Regex-based helpers are exactly the kind of thing that ends up duplicated across multiple files. The rename detection helpers (`clean_filename`, `rename_suspicion`) are the standing example of this — they were initially copied into each subcommand and then consolidated. Don't repeat that pattern.

### `--dry-run` must always be respected

Every command that writes files or modifies state takes a `--dry-run` flag. Any new command or subcommand must honour it: print what would happen, write nothing.

### Iterative runs must be safe

Changelog commands preserve user-edited content across re-runs. Any changes to output structure must not discard content that lives after the `<!-- archivist:auto-end -->` sentinel or replaces the per-line `[description]` placeholder.

### Auto-routing via `.archivist/`

`archivist changelog` with no subcommand reads the `module-type` from `.archivist/config.yaml` and routes to the appropriate subcommand automatically. If no config is found, it falls back to `general`. The `--dry-run`, `commit_sha`, and `--path` arguments are defined on the bare `changelog` parser so they pass through correctly regardless of which subcommand is invoked. `--help` is handled by argparse before routing logic runs and will always show the bare `changelog` help — this is a known and accepted limitation. Users who want subcommand-specific help should run `archivist changelog <subcommand> --help` explicitly.

The legacy flat `.archivist` file is still supported transparently for backwards compatibility. All new projects use the directory form.

---

## Plugin System

Archivist supports per-project changelog plugins. The convention is simple and deliberate: **the file's existence is the registration**.

### Location

```
.archivist/
  config.yaml
  changelog.py       ← active plugin (loaded automatically)
  sample-changelog.py ← reference file (never loaded, always ignored)
```

### Discovery rules

- Archivist looks for `.archivist/changelog.py` on every `archivist changelog` invocation.
- If found, it loads the plugin and calls its `run(args)`. The built-in subcommand is bypassed entirely.
- If not found, routing proceeds normally to the built-in subcommand for the configured module type.
- **Explicit subcommands always bypass the plugin.** `archivist changelog library` runs the built-in library subcommand regardless of whether a plugin exists. The plugin is only active for bare `archivist changelog` invocations.
- `sample-changelog.py` is never loaded. Only `changelog.py` is recognized. This is intentional and exact.

### The contract

A plugin is a Python file that exposes one callable:

```python
def run(args: argparse.Namespace) -> None:
    ...
```

That function calls `run_changelog()` from `changelog_base` with builder callables. Everything else is up to the plugin.

### Library plugin API

The library module exposes four public functions for plugin composition:

```python
from archivist.commands.changelog.library import (
    analyse_catalog,   # post_changes hook — populates ctx.data
    build_frontmatter, # YAML frontmatter block
    build_body,        # full changelog body including sentinel
    print_summary,     # terminal summary after write
)
```

Do not import anything prefixed with `_` from the library module. Those are internal and will change without notice.

### Activation and deactivation

- **Activate:** rename `sample-changelog.py` → `changelog.py` and edit.
- **Deactivate:** delete `changelog.py` or rename it back. Instant revert, no config changes.
- **Test:** `archivist changelog --dry-run` runs the full pipeline including the plugin. The indicator line confirms which code path ran:

  ```
  → changelog plugin found: .archivist/changelog.py
  ```

### Extending to other commands

The plugin convention is designed to extend to other commands (`manifest`, `reclassify`, etc.) using identical discovery logic: Archivist looks for `.archivist/<command>.py`, loads it if present, falls back to built-in if not. This is not yet implemented for commands other than `changelog`.

---

## What Not to Touch

- `cli.py` parser definitions — only modify if adding or removing a subcommand.
- The `<!-- archivist:auto-end -->` sentinel string — it is the boundary between generated and user content. Do not rename or move it.
- Archive DB schema — the `edition_shas` table structure is shared between `manifest` and `changelog publication`. Migrations require both to be updated together.
- `.archivist/sample-changelog.py` — this is a reference file written by `init`. Do not modify it. It is intentionally ignored by plugin discovery. Users copy and rename it; Archivist does not load it.
- The public plugin API in `library.py` (`analyse_catalog`, `build_frontmatter`, `build_body`, `print_summary`) — these are the stable composition surface for plugins. Renaming or removing them is a breaking change.