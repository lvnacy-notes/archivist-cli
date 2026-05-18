---
class: spec
category:
  - infrastructure
  - agents
affiliations:
created: 2026-05-17
modified: 2026-05-18
version:
related:
  - "[[DOCUMENTATION_TAXONOMY]]"
tags:
---
## Shared helpers belong in `archivist/utils`

Before adding a helper function to a command module, check whether it is likely to be used elsewhere. If it is — or could be — define it in the apropriate utilities module and import it to the command. Do not duplicate logic across modules.

## `import re` is a flag

If you find yourself adding `import re` to a command module, stop and ask whether the function using it would be better defined in a utilities module. Regex-based helpers are exactly the kind of thing that ends up duplicated across multiple files. The rename detection helpers (`clean_filename`, `rename_suspicion`) are the standing example of this — they were initially copied into each subcommand and then consolidated. Don't repeat that pattern.

## `--dry-run` must always be respected

Every command that writes files or modifies state takes a `--dry-run` flag. Any new command or subcommand must honour it: print what would happen, write nothing.

## Iterative runs must be safe

Changelog commands preserve user-edited content across re-runs. Any changes to output structure must not discard content that lives after the `<!-- archivist:auto-end -->` sentinel or replaces the per-line `[description]` placeholder.

## Auto-routing via `.archivist/`

`archivist changelog` with no subcommand reads the `module-type` from `.archivist/config.yaml` and routes to the appropriate subcommand automatically. If no config is found, it falls back to `general`. The `--dry-run`, `commit_sha`, and `--path` arguments are defined on the bare `changelog` parser so they pass through correctly regardless of which subcommand is invoked. `--help` is handled by argparse before routing logic runs and will always show the bare `changelog` help — this is a known and accepted limitation. Users who want subcommand-specific help should run `archivist changelog <subcommand> --help` explicitly.

The legacy flat `.archivist` file is still supported transparently for backwards compatibility. All new projects use the directory form.