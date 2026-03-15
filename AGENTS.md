# AGENTS.md

Guidelines for AI agents working on this codebase.

---

## Project Structure

Archivist is a CLI tool organized around a single `utils.py` for shared helpers and individual command modules under `archivist/commands/`. The docstring at the top of `utils.py` is the canonical statement of intent: **anything used by more than one command lives there.**

---

## Code Conventions

### Shared helpers belong in `utils.py`

Before adding a helper function to a command module, check whether it is likely to be used elsewhere. If it is — or could be — define it in `utils.py` and import it. Do not duplicate logic across modules.

### `import re` is a flag

If you find yourself adding `import re` to a command module, stop and ask whether the function using it would be better defined in `utils.py`. Regex-based helpers are exactly the kind of thing that ends up duplicated across five files. The rename detection helpers (`clean_filename`, `rename_suspicion`) are the standing example of this — they were initially copied into each subcommand and then consolidated. Don't repeat that pattern.

### `--dry-run` must always be respected

Every command that writes files or modifies state takes a `--dry-run` flag. Any new command or subcommand must honour it: print what would happen, write nothing.

### Iterative runs must be safe

Changelog commands preserve user-edited content across re-runs via `extract_descriptions` and `extract_user_content`. Any changes to output structure must not silently discard content that lives after the `<!-- archivist:auto-end -->` sentinel.

---

## What Not to Touch

- `cli.py` parser definitions — only modify if adding or removing a subcommand.
- The `<!-- archivist:auto-end -->` sentinel string — it is the boundary between generated and user content. Do not rename or move it.
- Archive DB schema — the `edition_shas` table structure is shared between `manifest` and `changelog publication`. Migrations require both to be updated together.