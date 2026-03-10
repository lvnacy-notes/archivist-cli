<div align="center">
    <img
        src="./assets/lvnacy_emblem_plain.png"
        alt="LVNACY emblem in grey with black V"
        width="256px"
    />
    <br />
</div>
<div align="center">
    <h1>Archivist</h1>
    <b>Obsidian Vault & Archive Management</b><br>
    <i>Frontmatter • Manifests • Changelogs</i><br>
    Part of the LVNACY Apparatus for Obsidian
</div>
<br>
<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-purple.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/Python-3.10+-red.svg)](https://www.python.org/)

</div>
<div align="center">
    •
    <a href="https://github.com/lvnacy">GitHub</a>
    •
    <a href="https://bsky.app/profile/lvnacy.xyz">Bluesky</a>
    •
</div>
<br />

---

## What is Archivist?

Archivist is a system-wide CLI tool for bulk-managing YAML frontmatter and generating structured archive documents — manifests and changelogs — across Obsidian vaults. It is built for the [LVNACY Apparatus](#inspiration), a modular, git-backed Obsidian architecture for managing stories, publications, research libraries, and related creative projects.

Archivist automatically scopes every command to the current git repo or submodule root via `git rev-parse --show-toplevel`. It does not matter where you are in a vault hierarchy — run it from anywhere inside the repo and it finds its footing.

The `ARCHIVE/` directory in the [LVNACY Apparatus](https://github.com/lvnacy-notes/apparatus-vault-template) serves as the living example and output target for this tool.

---

## How It Works

Archivist is built around three conventions.

### `.archivist` config

A small YAML file at the root of any project Archivist manages:

```yaml
# .archivist
apparatus: true
module-type: story  # story | publication | library | vault | general
```

This file tells Archivist what kind of project it is dealing with, which drives changelog routing and git hook behavior. Projects without a `.archivist` file are ignored by the hooks entirely — Archivist never touches repos it has not been asked to manage.

### `ARCHIVE/` directory

Each Apparatus module maintains an `ARCHIVE/` directory at its root. Archivist searches this directory recursively for its templates:

- `CHANGELOG_TEMPLATE.md` — drives all changelog output
- `MANIFEST_TEMPLATE.md` — drives edition manifest output (publication modules)

Field order in generated documents is determined entirely by the template. To add, remove, or reorder a field, edit the template — no code changes required.

### Git hooks

Archivist installs two git hooks:

- **`pre-commit`** — checks whether a manifest or changelog is staged. If not, prompts you to generate one before the commit proceeds.
- **`post-commit`** — always prints commit details (SHA, message, branch). In Archivist-managed repos, also backfills the commit SHA into any manifest or changelog that was included in the commit.

Hooks are installed globally into `~/.git-templates/hooks/` and copied automatically into new clones. Existing repos can be synced with `archivist hooks sync`.

---

## Installation

Archivist is designed to live in a permanent location outside any vault and be installed as an editable package via pip.

### Quick install
```bash
curl -fsSL https://raw.githubusercontent.com/lvnacy-notes/archivist-cli/main/install.sh | bash
```

The script checks for Python 3.10+ and git, clones the repo to `~/tools/archivist-cli`, installs the package, and runs `archivist hooks install` automatically. The manual steps below are available if you prefer to inspect and run each step yourself:

```bash
# Clone somewhere permanent
mkdir -p ~/tools && cd ~/tools
git clone https://github.com/lvnacy-notes/archivist-cli.git

# Install with your pyenv-managed Python
cd archivist-cli
$(pyenv which pip) install -e .
```

The `-e` flag installs in editable mode — edits to source files take effect immediately without reinstalling.

**Requirements:** Python 3.10+, `git` in your `$PATH`.

**Dependencies:** `pyyaml`, installed automatically. The frontmatter commands are stdlib only; `pyyaml` is required by `manifest` and `changelog`.

### First-time setup

After installing, run these two commands:

```bash
# Install hooks globally — applies to all future clones automatically
archivist hooks install

# Inside each existing project you want Archivist to manage
cd path/to/your/project
archivist init
```

---

## Getting Started

### `archivist init`

Interactive project setup. Run once per project — or once per machine after cloning an existing Apparatus module.

```bash
archivist init
```

If no `.archivist` config is found, it walks you through setup:

1. **Is this an Apparatus project?** Yes / No
2. If yes: select a module type from the available list
3. Writes `.archivist` to the repo root
4. Installs git hooks locally

If `.archivist` already exists, it displays the current config and offers to update it or reinstall hooks. Safe to re-run at any time.

```bash
# Preview without writing anything
archivist init --dry-run
```

---

## Commands

```
archivist <command> [subcommand] [options]
```

---

### `archivist frontmatter`

Bulk-manage YAML frontmatter properties across every `.md` file in the repo. All subcommands recurse from the git root and support `--dry-run`.

---

#### `archivist frontmatter add`

Adds a property to the frontmatter of every note in the repo. Creates a frontmatter block if one does not exist. Skips notes that already have the property unless `--overwrite` is passed.

```bash
# Add a bare key with no value
archivist frontmatter add -p reviewed

# Add a key with a value
archivist frontmatter add -p status -v draft

# Overwrite if already exists
archivist frontmatter add -p status -v published --overwrite

# Preview without writing
archivist frontmatter add -p status -v draft --dry-run
```

| Flag | Short | Required | Description |
|---|---|---|---|
| `--property` | `-p` | ✅ | Property name to add |
| `--value` | `-v` | ❌ | Value to pair with the property |
| `--overwrite` | | ❌ | Overwrite if the property already exists |
| `--dry-run` | | ❌ | Preview without writing to disk |

---

#### `archivist frontmatter remove`

Removes a property and its value from every note in the repo. If the removal leaves the frontmatter block empty, the block is dropped entirely.

```bash
archivist frontmatter remove -p status
archivist frontmatter remove -p status --dry-run
```

| Flag | Short | Required | Description |
|---|---|---|---|
| `--property` | `-p` | ✅ | Property name to remove |
| `--dry-run` | | ❌ | Preview without writing to disk |

---

#### `archivist frontmatter rename`

Renames a property key across all notes, preserving its value exactly. Handles scalar values, inline lists, and multi-line block sequences.

```bash
archivist frontmatter rename -p status -n state
archivist frontmatter rename -p tags -n keywords --dry-run
```

| Flag | Short | Required | Description |
|---|---|---|---|
| `--property` | `-p` | ✅ | Current property name |
| `--new-name` | `-n` | ✅ | New property name |
| `--dry-run` | | ❌ | Preview without writing to disk |

---

#### `archivist frontmatter apply-template`

Applies a frontmatter template to all notes whose `class` property matches a specified value. For each matching note:

- Adds properties present in the template but missing from the note (using template defaults)
- Removes properties present in the note but absent from the template
- Reorders properties to match the template order
- Preserves existing values for retained properties

```bash
archivist frontmatter apply-template -t templates/character.md -c character
archivist frontmatter apply-template -t templates/location.md -c location --class-property type
archivist frontmatter apply-template -t templates/character.md -c character --dry-run
```

| Flag | Short | Required | Description |
|---|---|---|---|
| `--template` | `-t` | ✅ | Path to the template markdown file |
| `--class` | `-c` | ✅ | Class value to match (e.g. `character`, `location`) |
| `--class-property` | | ❌ | Frontmatter property used to identify the class (default: `class`) |
| `--dry-run` | | ❌ | Preview without writing to disk |

---

### `archivist manifest`

Generates a `{edition-name}-manifest.md` for a specified edition directory, written to the edition's parent directory. Specific to `publication` modules.

Operates in two modes: manifest generation and SHA registration.

Frontmatter structure is driven entirely by `MANIFEST_TEMPLATE.md`, searched recursively under `ARCHIVE/` — shallowest match wins. Expected path: `ARCHIVE/EDITIONS/MANIFEST_TEMPLATE.md`.

#### Manifest generation

Scopes all git diff tracking to the edition directory only — staging the entire project is safe. Classifies each changed file by reading its `class` frontmatter property directly, sorting articles, edition files, and assets into the correct sections. Renames detected by `git mv` appear as `old → new` in a dedicated Moved section.

If the edition's files are not yet staged, Archivist stages them automatically before diffing.

Auto-populated frontmatter fields:

| Field | Source |
|---|---|
| `articles-published` | Count of `class: article` + `class: edition` files |
| `assets-included` | Total file count in the edition directory |
| `files-created` / `files-modified` / `files-archived` | Scoped git diff counts |
| `edition` | Quoted wikilink from directory name — `VOL-II-NO-27` → `"[[VOL II NO 27]]"` |
| `publish-date` | Pulled from the `class: edition` file's frontmatter if present |
| `class`, `category`, `log-scope`, `modified`, `updated`, `commit-sha` | Auto-set |

```bash
# From the edition's parent directory
archivist manifest "./VOL II NO 27"

# With a volume number
archivist manifest "./VOL II NO 27" -v 2

# Diff against a specific commit
archivist manifest "./VOL II NO 27" a1b2c3d -v 2

# Preview without writing
archivist manifest "./VOL II NO 27" --dry-run
```

| Argument | Required | Description |
|---|---|---|
| `edition-dir` | ✅ | Path to the edition directory (relative or absolute) |
| `commit-sha` | ❌ | Diff against a specific commit instead of staged changes |
| `-v` / `--volume` | ❌ | Volume number for the `volume` frontmatter field |
| `--dry-run` | ❌ | Print to stdout without writing to disk |

#### SHA registration

Registers an edition's commit SHA in the archive DB after committing. Verifies the SHA is a valid commit before inserting and stores the commit message alongside it.

```bash
archivist manifest --register a1b2c3d
archivist manifest --register a1b2c3d --dry-run
```

Reports one of four outcomes: `inserted`, `already registered`, `already claimed by a changelog`, or `invalid SHA`.

| Argument | Required | Description |
|---|---|---|
| `--register SHA` | ✅ | Verify and register a commit SHA in the archive DB |
| `--dry-run` | ❌ | Preview without writing to the DB |

**Archive DB:** `ARCHIVE/archive.db` — SQLite, created automatically on first use.

---

### `archivist changelog`

Generates a `CHANGELOG-{date}.md` capturing project changes. The appropriate subcommand is determined by the module type declared in `.archivist`.

Running `archivist changelog` without a subcommand generates a general changelog. Subcommands are available for specific module types.

Frontmatter is driven by `CHANGELOG_TEMPLATE.md`, searched recursively under `ARCHIVE/` — shallowest match wins. Output is written to the same directory the template lives in.

If files are not staged, Archivist stages them automatically. Pass `--path` to scope staging and diffing to a specific file or directory; omit it to operate repo-wide.

```bash
# General changelog — bare invocation
archivist changelog
archivist changelog --dry-run

# Explicit subcommands
archivist changelog general
archivist changelog publication
archivist changelog story
archivist changelog vault

# Scope to a specific path
archivist changelog story --path ./chapters

# Diff against a specific commit (subcommand required)
archivist changelog general a1b2c3d
```

**Shared flags:**

| Argument | Required | Description |
|---|---|---|
| `commit-sha` | ❌ | Diff against a specific commit (subcommand required) |
| `--path` | ❌ | File or directory to stage and scope the diff to |
| `--dry-run` | ❌ | Print to stdout without writing to disk or DB |

---

#### `archivist changelog general`

A clean, minimal changelog with no project-type-specific sections. Suitable for any project. Also invoked by bare `archivist changelog`.

---

#### `archivist changelog publication`

For newsletter and publication modules. Queries the archive DB for edition commit SHAs not yet recorded in any changelog, includes them in `editions-sha` frontmatter, and marks them as claimed in a single transaction — each SHA appears in exactly one changelog, never duplicated, never silently dropped.

Auto-populated frontmatter fields:

| Field | Source |
|---|---|
| `editions-sha` | SHAs from the archive DB with `included_in = NULL` |
| `files-created` / `files-modified` / `files-archived` | Repo-wide git diff counts |
| `class`, `category`, `log-scope`, `modified`, `updated`, `commit-sha`, `tags` | Auto-set |

**Archive DB:** `ARCHIVE/archive.db` — shared with `archivist manifest`.

---

#### `archivist changelog story`

For story and creative writing modules. Includes writing-specific sections: scene development, character arcs, plot advancement, creative considerations, and next steps structured around narrative milestones.

---

#### `archivist changelog vault`

For vault-level commits. In addition to standard file change tracking, captures:

- Which submodules were updated in this commit
- A status table of all registered submodules: current SHA, uncommitted changes, unpushed commits

Useful for tracking the state of the full Apparatus ecosystem at commit time.

---

### `archivist hooks`

Manage Archivist's git hooks.

#### `archivist hooks install`

Writes hook scripts into `~/.git-templates/hooks/` and ensures git is configured to use that directory as its template source. All future `git clone` and `git init` operations will automatically include the hooks.

```bash
archivist hooks install
archivist hooks install --dry-run
```

#### `archivist hooks sync`

Copies hooks directly into the current repo's `.git/hooks/`. Use this for repos that existed before `hooks install` was run.

```bash
archivist hooks sync
archivist hooks sync --dry-run
```

#### Hook behavior

**`pre-commit`:** Checks whether a manifest or changelog is staged. If none is found, prompts:

```
📋 archivist: No manifest or changelog found in staged files.

Generate one now?
  1. no — proceed with commit as-is
  2. manifest — generate an edition manifest
  3. changelog — generate a changelog
```

Selecting `manifest` prompts for an edition directory path, runs `archivist manifest`, and stages the result. Selecting `changelog` runs the appropriate changelog command for the module type and stages the result.

**`post-commit`:** Runs in every repo — Archivist-managed or not — and prints commit details:

```
🚀 Commit Details:
   ✅ SHA:     abc123def456...
   📝 Short:   abc123d
   💬 Message: your commit message
   🌿 Branch:  main

📋 For PR creation, use:
   "Create PR for commit abc123d from main to main"
   or
   "Create PR from main to main"
```

In Archivist-managed repos, it additionally backfills the commit SHA into any manifest or changelog included in the commit that has an empty `commit-sha` frontmatter field:

- Short SHA → `commit-sha:` in frontmatter
- Full SHA → `Commit SHA` row in the body table

The updated file is left unstaged — commit it deliberately, typically alongside the next edition or changelog.

---

## Archive DB

`archivist manifest` and `archivist changelog publication` share a SQLite database at `ARCHIVE/archive.db`, created automatically on first use. It tracks edition commit SHAs from registration through inclusion in a project changelog.

```sql
CREATE TABLE edition_shas (
    sha             TEXT PRIMARY KEY,
    commit_message  TEXT,
    manifest_file   TEXT,
    discovered_at   TEXT,
    included_in     TEXT   -- NULL until claimed by a changelog
);
```

To inspect or correct entries directly:

```bash
sqlite3 ARCHIVE/archive.db

SELECT * FROM edition_shas;

-- Fix a wrong SHA
UPDATE edition_shas SET sha = 'correctsha' WHERE sha = 'wrongsha';

-- Delete and re-register a bad entry
DELETE FROM edition_shas WHERE sha = 'wrongsha';
.quit

archivist manifest --register correctsha
```

---

## Adapting Archivist

Archivist is opinionated — built around the conventions of the LVNACY Apparatus. If you want to adapt it to a different project structure, the pattern is intentionally straightforward.

### Adding a new changelog type

1. Create `archivist/commands/changelog/yourtype.py` with a `run(args)` function. Use any existing changelog module as a reference — they all follow the same structure: find template → ensure staged → diff → build frontmatter → build body → write.

2. Add the subcommand parser to `build_parser()` in `cli.py`:

```python
yourtype_p = cl_sub.add_parser("yourtype", help="...")
yourtype_p.add_argument("commit_sha", nargs="?", default=None, metavar="COMMIT-SHA")
yourtype_p.add_argument("--path", default=None, metavar="PATH")
yourtype_p.add_argument("--dry-run", action="store_true")
```

3. Add the routing branch to `main()` in `cli.py`:

```python
elif cl_command == "yourtype":
    from archivist.commands.changelog.yourtype import run
```

4. Add your module type to `APPARATUS_MODULE_TYPES` and `MODULE_CHANGELOG_COMMAND` in `utils.py`.

No reinstall needed — editable installs pick up changes immediately.

### Adding a new top-level command

1. Create `archivist/commands/yourcommand.py` with a `run(args)` function.
2. Add the parser to `build_parser()` in `cli.py`.
3. Add the routing branch to `main()` in `cli.py`.

### Changing template conventions

Archivist finds templates by recursively searching `ARCHIVE/` for a filename. To use a different directory structure or template name, update the relevant `_find_*_template()` function in the command module. Template field order is always respected — frontmatter is rendered by iterating template keys in order.

---

## Contributing

This project is not accepting unsolicited PRs. Archivist is purpose-built for the LVNACY Apparatus, and its feature roadmap reflects that specific use case.

That said, discussion is welcome. If you have a suggestion, open an issue. If a discussion produces a viable feature request aligned with the Apparatus workflow, a PR may be invited.

If you want to adapt this for your own use — which is actively encouraged — fork it, modify freely, and build something useful. The [adapting section above](#adapting-archivist) is a good starting point.

---

## Changelog

Archivist uses Archivist. Generated changelogs live in [`ARCHIVE/CHANGELOG/`](./ARCHIVE/CHANGELOG/).

---

## Inspiration

This CLI was developed in collaboration with [Mad Alex](https://github.com/madalexxx), the driving force behind the LVNACY Apparatus. It was built to compile comprehensive changelogs and track the progress of their stories and the evolution of their workflows.

"Archivist" is inspired by a concept of the same name being written by Mad Alex, who has been kind enough to allow software and plugins that do not contain proprietary content to be made available as open source. Please follow and subscribe:

- **Newsletter:** [The Backstage Pass](https://backstage.carnivalofcalamity.xyz)
- **GitHub:** [madalexxx](https://github.com/madalexxx)

---

## License

This software is available under the MIT License. See [LICENSE](./LICENSE) for details.