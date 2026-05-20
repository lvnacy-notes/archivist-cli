---
modified: 2026-05-19
---

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

### Project Layout

```
archivist-cli/
├── archivist/
│   ├── __init__.py/
│   │
│   ├── commands/
│   │   ├── __init__.py
│   │   ├── changelog/
│   │   │   ├── __init__.py
│   │   │   ├── changelog_base.py
│   │   │   ├── general.py
│   │   │   ├── library.py
│   │   │   ├── publication.py
│   │   │   ├── seal.py
│   │   │   ├── story.py
│   │   │   └── vault.py
│   │   ├── frontmatter/
│   │   │   ├── __init__.py
│   │   │   ├── add.py
│   │   │   ├── apply_template.py
│   │   │   ├── remove.py
│   │   │   └── rename.py
│   │   ├── hooks/
│   │   │   ├── __init__.py
│   │   │   └── install.py
│   │   ├── init.py
│   │   ├── manifest.py
│   │   ├── migrate.py
│   │   └── reclassify.py
│   │
│   ├── utils/                         # Shared utilities — barrel-exported via __init__.py
│   │   ├── __init__.py                # Barrel: re-exports everything public
│   │   ├── changelog.py
│   │   ├── config.py
│   │   ├── db.py
│   │   ├── frontmatter.py
│   │   ├── git.py
│   │   ├── note_filter.py
│   │   ├── output.py
│   │   ├── rename_helpers.py
│   │   └── templater.py
│   ├── cli.py                         # Argument parsing and command dispatch
│   └── formatter.py                   # Output formatting (logging, ANSI, help text)
│
├── tests/
│   ├── integration/
│   │   ├── test_changelog_commands.py
│   │   ├── test_frontmatter_commands.py
│   │   └── test_seal.py
│   │
│   ├── unit/
│   │   ├── test_changelog_helpers.py
│   │   ├── test_config.py
│   │   ├── test_frontmatter.py
│   │   ├── test_rename_helpers.py
│   │   └── test_templater.py
│   │
│   └── conftest.py
│
├── docs/
│   ├── ARCHIVE/                       # changelog files
│   ├── ROADMAP/                       # planned features
│   │   ├── CENTRALIZED_DB/
│   │   ├── CUSTODIAN/
│   │   ├── DELEGIT/
│   │   ├── GIT_INTEGRATION/
│   │   ├── GRAPH/
│   │   ├── MULTI_VAULT_ORCHESTRATION/
│   │   ├── PLUGIN_SYSTEM/
│   │   ├── TEMPLATER_SUPPORT/
│   │   ├── DEVELOPMENT_INFRASTRUCTURE.md
│   │   └── ROADMAP.md                 # roadmap overview
│   └── TESTING_SPECIFICATIONS.md
│
├── pyproject.toml
├── CLAUDE.md                          # This file
└── README.md
```

### Module Responsibilities

| Module | Owns |
|--------|------|
| `cli.py` | Argument parsing, logging configuration, command dispatch |
| `formatter.py` | ANSI styling, log formatters and handlers, help formatter |
| `commands/` | Subcommand entry points (`run(args)`) — thin orchestration, no business logic |
| `utils/` | All shared logic — git operations, file I/O, output helpers, etc. |

### Import Rules

- **Commands** import from `utils` via the barrel (`from package_name.utils import ...`). Never import directly from a utils submodule (`utils.whatever`).
- **Utils** import directly from each other (`from package_name.utils.module_a import ...`). The barrel rule does not apply within `utils/` — they are peers.
- **`cli.py`** imports from `formatter` and from `utils` via the barrel.
- **`formatter.py`** has no internal imports. It is a leaf.
---

## Code Conventions

See [[CODE_CONVENTIONS]].

---

## Plugin System

See [[PLUGIN_SYSTEM_SPECIFICATION]].

---

## What Not to Touch

- `cli.py` parser definitions — only modify if adding or removing a subcommand.
- The `<!-- archivist:auto-end -->` sentinel string — it is the boundary between generated and user content. Do not rename or move it.
- Archive DB schema — the `edition_shas` table structure is shared between `manifest` and `changelog publication`. Migrations require both to be updated together.
- `.archivist/sample-changelog.py` — this is a reference file written by `init`. Do not modify it. It is intentionally ignored by plugin discovery. Users copy and rename it; Archivist does not load it.
- The public plugin API in `library.py` (`analyse_catalog`, `build_frontmatter`, `build_body`, `print_summary`) — these are the stable composition surface for plugins. Renaming or removing them is a breaking change.