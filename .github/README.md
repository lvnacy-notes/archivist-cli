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

Additional optional fields:

```yaml
# For library modules — where archivist scans for catalogued works
works-dir: works

# Override the default changelog output directory (relative to repo root)
# Defaults: story/publication → ARCHIVE/CHANGELOG/, everything else → ARCHIVE/
changelog-output-dir: ARCHIVE/LOGS

# Templater expression handling. Set by archivist init — can also be edited directly.
# resolve  — Archivist resolves tp.date.*, tp.file.*, tp.frontmatter.* at write time
# preserve — Archivist safely round-trips <% %> expressions without touching them
# false    — Treat <% %> as plain strings. No handling at all.
templater: preserve
```

### `ARCHIVE/` directory

Each Apparatus module maintains an `ARCHIVE/` directory at its root. Archivist writes all generated changelogs directly into this directory (or `ARCHIVE/CHANGELOG/` for story and publication modules by default). For publication modules, `ARCHIVE/` also serves as the search root for `MANIFEST_TEMPLATE.md` — the template that drives edition manifest output.

- `MANIFEST_TEMPLATE.md` — drives edition manifest output (publication modules only)
- `archive.db` — SQLite database shared by `manifest` and `changelog publication`

Field order in generated manifests is determined entirely by the template. To add, remove, or reorder a field, edit the template — no code changes required. Changelog frontmatter fields are defined in each subcommand module directly.

### Git hooks

Archivist installs two git hooks:

- **`pre-commit`** — checks whether a manifest or changelog is staged. If not, prompts you to generate one before the commit proceeds.
- **`post-commit`** — always prints commit details (SHA, message, branch). In Archivist-managed repos, also backfills the commit SHA into any manifest included in the commit, and delegates changelog sealing to `archivist changelog seal`.

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
3. For `library` modules: set the `works-dir` path (where Archivist scans for catalogued works)
4. Optionally set a custom `changelog-output-dir` to override the default output location
5. Prompts for Templater expression handling mode (`resolve`, `preserve`, or `false`) and writes it to `.archivist` — see [Templater support](#templater-support) below
6. Writes `.archivist` to the repo root
7. Installs git hooks locally

If `.archivist` already exists, it displays the current config and offers to update it or reinstall hooks.

> ⚠️ **Warning:** `archivist init` **will overwrite any existing git hooks** in the repo's `.git/hooks/` directory without a backup. If you have custom hooks, read every prompt carefully before confirming. Review your existing hooks first with `ls .git/hooks/` and preserve anything you need before proceeding.

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

Applies a frontmatter template to all notes matching a set of filter criteria. For each matching note:

- Adds properties present in the template but missing from the note (using template defaults)
- Removes properties present in the note but absent from the template
- Reorders properties to match the template order
- Preserves existing values for retained properties

**The template is the authority. The template is the law.**

Filter by any combination of `--class`, `--path`, and `--tag`. All provided filters must match (AND logic). At least one filter is required — Archivist will not rewrite your entire vault on a hunch.

```bash
archivist frontmatter apply-template -t templates/character.md -c character
archivist frontmatter apply-template -t templates/article.md --tag draft
archivist frontmatter apply-template -t templates/location.md -c location --class-property type
archivist frontmatter apply-template -t templates/character.md -c character --path content/characters
archivist frontmatter apply-template -t templates/character.md -c character --tag hero --dry-run
```

| Flag | Short | Required | Description |
|---|---|---|---|
| `--template` | `-t` | ✅ | Path to the template markdown file |
| `--class` | `-c` | ❌ | Class value to match (e.g. `character`, `location`) |
| `--class-property` | | ❌ | Frontmatter property used as the class discriminator (default: `class`) |
| `--path` | | ❌ | Limit search to this directory (relative to repo root) |
| `--tag` | | ❌ | Match notes that carry this tag in their frontmatter |
| `--dry-run` | | ❌ | Preview without writing to disk |

At least one of `--class`, `--path`, or `--tag` is required.

---

#### Templater support

If your notes use the [Obsidian Templater plugin](https://github.com/SilentVoid13/Templater), Archivist handles `<% %>` expressions in frontmatter values without corrupting them. Behavior is controlled by the `templater` key in `.archivist`, set during `archivist init`:

| Mode | Behavior |
|---|---|
| `preserve` | Detects `<% %>` expressions and round-trips them safely. Archivist masks them before any frontmatter manipulation and restores them verbatim afterward — no resolution, no corruption. Open the file in Obsidian and run Templater yourself. |
| `resolve` | Resolves a static subset of Templater expressions at write time using a Python reimplementation of the relevant `tp.*` API surface. No Obsidian required, no Node.js required. Unresolvable expressions are preserved verbatim with a warning. |
| `false` | Treats `<% %>` as plain strings. Zero overhead. Use this if your project has no Templater expressions. |

**What `resolve` mode handles:**

```yaml
# tp.date — all of these resolve correctly
created:  <% tp.date.now("YYYY-MM-DD") %>
due:      <% tp.date.now("YYYY-MM-DD", 7) %>
tomorrow: <% tp.date.tomorrow() %>

# tp.file — resolved against the target note, not the template file
title:    <% tp.file.title %>
folder:   <% tp.file.folder() %>
modified: <% tp.file.last_modified_date("YYYY-MM-DD") %>

# tp.frontmatter — cross-property references within the same note
slug:     <% tp.frontmatter["title"] %>
```

**What `resolve` mode does not handle:**

`tp.system`, `tp.user`, `tp.obsidian`, and any expression that requires a running Obsidian instance or user interaction. These are left verbatim with a `⚠️` warning. Switch to `preserve` if your templates rely on them.

**Important:** In `resolve` mode, `apply-template` resolves template default values against the *target note's* context — `tp.file.title` gives you the target note's title, not the template file's. This is the correct behavior.

---

### `archivist reclassify`

Find every `.md` file whose frontmatter `class` field matches a given value and rewrite it to a new value. Surgical — only the `class:` line is touched. Nothing else in the frontmatter moves.

Matching is case-insensitive. The `--to` value is written verbatim. Scope with `--path` to limit the search.

```bash
archivist reclassify --from article --to column
archivist reclassify --from article --to column --path content/
archivist reclassify --from article --to column --dry-run
```

| Flag | Required | Description |
|---|---|---|
| `--from` | ✅ | Current class value to match (case-insensitive) |
| `--to` | ✅ | New class value to write |
| `--path` | ❌ | Limit search to this file or directory |
| `--dry-run` | ❌ | Preview changes without writing to disk |

---

### `archivist manifest`

Generates a `{edition-name}-manifest.md` for a specified edition directory, written to the edition's parent directory. Specific to `publication` modules.

Operates in two modes: manifest generation and SHA registration.

Frontmatter structure is driven entirely by `MANIFEST_TEMPLATE.md`, searched recursively under `ARCHIVE/` — shallowest match wins. Expected path: `ARCHIVE/EDITIONS/MANIFEST_TEMPLATE.md`.

#### Manifest generation

Scopes all git diff tracking to the edition directory only — staging the entire project is safe. Classifies each changed file by reading its `class` frontmatter property directly, sorting columns, edition files, and assets into the correct sections:

- `class: column` — editorial columns and articles
- `class: edition` — the edition's primary file (drives `publish-date` extraction)
- Everything else — assets and supporting files

Renames detected by `git mv` appear as `old → new` in a dedicated Moved section.

If the edition's files are not yet staged, Archivist stages them automatically before diffing.

Re-running `archivist manifest` for the same edition updates the existing manifest in place. User content below the `<!-- archivist:auto-end -->` sentinel and any file descriptions you've written are preserved across re-runs — only the auto-generated block is regenerated.

Auto-populated frontmatter fields:

| Field | Source |
|---|---|
| `columns-published` | Count of `class: column` + `class: edition` files |
| `assets-included` | Count of files in the edition directory not classified as columns or edition files |
| `files-created` / `files-modified` / `files-archived` | Scoped git diff counts |
| `edition` | Quoted wikilink from directory name — `VOL-II-NO-27` → `"[[VOL II NO 27]]"` |
| `publish-date` | Pulled from the `class: edition` file's frontmatter if present |
| `class`, `category`, `log-scope`, `modified`, `commit-sha` | Auto-set |

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

Generates a `CHANGELOG-{date}.md` capturing project changes. Output is written to `ARCHIVE/` or `ARCHIVE/CHANGELOG/` depending on module type (or your `changelog-output-dir` config). Frontmatter fields are defined per subcommand — no template file required.

#### Auto-routing

Running `archivist changelog` bare — no subcommand — reads `module-type` from `.archivist` and dispatches to the appropriate subcommand automatically.

| `.archivist` module-type | Runs |
|---|---|
| `general` | `archivist changelog general` |
| `story` | `archivist changelog story` |
| `publication` | `archivist changelog publication` |
| `library` | `archivist changelog library` |
| `vault` | `archivist changelog vault` |

No `.archivist` config, or an unrecognized module type? Falls back to `general`. It'll manage.

`--dry-run`, `--commit-sha`, and `--path` are all accepted by the bare invocation and passed through to whichever subcommand gets invoked:

```bash
archivist changelog --dry-run
archivist changelog --commit-sha a1b2c3d
archivist changelog --path ./chapters
```

> ⚠️ **`--help` does not auto-route.** Argparse handles it before any routing logic runs, so `archivist changelog --help` always shows the bare command help regardless of your `.archivist` config. For subcommand-specific help, be explicit:
> ```bash
> archivist changelog publication --help
> archivist changelog story --help
> ```

If files are not staged, Archivist stages them automatically. Pass `--path` to scope staging and diffing to a specific file or directory; omit it to operate repo-wide.

#### Iterative runs

Re-running a changelog command pre-commit updates the existing file rather than creating a new one. Archivist preserves two things across re-runs:

- **User content** — everything after the `<!-- archivist:auto-end -->` sentinel (your notes, checklist, any edits you've made below the auto-generated block) is carried forward untouched.
- **Descriptions** — any file descriptions you've filled in (single-line or sub-bullet format) are extracted from the existing changelog and reinjected against the same filenames in the new output. Descriptions still showing `[description]` are not preserved — only ones you've actually written.

The auto-generated block above the sentinel is always fully regenerated from the current staged state, so file counts, SHA, and the file list stay accurate no matter how many times you re-run.

#### `--path` scoping and the nag prompts

When `--path` is active, Archivist stages and diffs only the specified directory. Two interactive prompts exist to keep you from shooting yourself in the foot:

**Out-of-scope prompt** (Step 3): If there are unstaged changes — modified tracked files or untracked files — sitting outside your scope, Archivist lists them and asks if you want to stage them too. Say `y` and they get staged. Say `n` and they're left alone. Either way, the run continues.

**Save-before-overwrite prompt** (Step 6): If an existing changelog has working-tree edits that haven't been staged yet, Archivist warns you before overwriting it. Say `y` and it stages the file first. Say `n` and the rerun proceeds at your own risk. Both prompts are completely suppressed during `--dry-run`.

```bash
# Auto-routes based on .archivist
archivist changelog
archivist changelog --dry-run
archivist changelog --commit-sha a1b2c3d

# Explicit subcommands — always available, always route directly
archivist changelog general
archivist changelog library
archivist changelog publication
archivist changelog story
archivist changelog vault

# Scope to a specific path
archivist changelog story --path ./chapters

# Diff against a specific commit (subcommand required for explicit routing)
archivist changelog general a1b2c3d
```

**Shared flags:**

| Argument | Required | Description |
|---|---|---|
| `commit-sha` | ❌ | Diff against a specific commit |
| `--path` | ❌ | File or directory to stage and scope the diff to |
| `--dry-run` | ❌ | Print to stdout without writing to disk or DB |

#### Rename detection

Archivist runs a three-pass rename detection pipeline on every changelog run, recovering renames that git's similarity threshold missed:

1. **Pass 0 (git):** `git diff -M` — renames git detected natively (>50% content similarity, any directory).
2. **Pass 1 (filename):** Pairs unmatched deleted/added files by identical filename across directories. Ambiguous matches (same filename added in two places) are left alone.
3. **Pass 2 (content):** Compares file content against HEAD for remaining unmatched pairs using sequence similarity. Catches the worst case: file renamed AND moved simultaneously.

Same-directory renames are annotated as `renamed from old-name.md`. Cross-directory moves are annotated as `moved from old/path/file.md` — because "`renamed from note.md`" is a useless hint when there are forty files called `note.md` in the vault. Suspicious renames (unrelated stems, or crossing directories) get a ⚠️ flag in the output so you can verify before committing.

---

#### `archivist changelog general`

A clean, minimal changelog with no project-type-specific sections. Suitable for any project. Also invoked by bare `archivist changelog` when no `.archivist` config is present.

Output goes to `ARCHIVE/`.

---

#### `archivist changelog library`

For library modules. Reads frontmatter from every changed `.md` file and routes it into named class buckets:

- **Works** — files carrying a `work-stage` field (regardless of `class` value), bucketed by current stage
- **Author Cards** — `class: author` files
- **Publication Cards** — `class: collection` files
- **Definitions** — `class: entry` files (word + aliases surfaced)
- **Other File Changes** — everything that didn't claim a named bucket

Auto-populated frontmatter counters: `works-added`, `works-updated`, `works-removed`, `authors-added`, `authors-updated`, `publications-added`, `definitions-added`.

**Catalog Snapshot** — the crown jewel of the library changelog. Generated at run time from the full works directory and frozen as static Mermaid charts:

- **Stage Distribution** — pie chart of work counts per `work-stage` across the entire catalog
- **Throughput** — table of `work-stage` transitions detected in this commit (e.g., `raw` → `active`)
- **Author Landscape** — pie chart of work counts per author (top 8, rest bucketed as "Others")
- **Reading Velocity** — bar chart of `date-consumed` entries over the rolling 12 months
- **Placeholder Debt** — count of works stuck at `placeholder` with no `date-consumed`

The works directory defaults to `works/` and is configurable via `works-dir` in `.archivist`.

Output goes to `ARCHIVE/`.

---

#### `archivist changelog publication`

For newsletter and publication modules. Queries the archive DB for edition commit SHAs not yet recorded in any changelog, includes them in `editions-sha` frontmatter, and marks them as claimed in a single transaction — each SHA appears in exactly one changelog, never duplicated, never silently dropped.

**The UUID lifecycle:** When a publication changelog is first generated, it receives a UUID written into its `UUID` frontmatter field. Edition SHAs are claimed against this UUID (`included_in = UUID`) rather than a file path — stable across renames and post-commit hook renaming. At seal time, `archivist changelog seal` transitions `included_in` from UUID to the commit SHA, completing the handoff. Iterative re-runs before sealing correctly re-surface SHAs already claimed by the current changelog's UUID.

Auto-populated frontmatter fields:

| Field | Source |
|---|---|
| `editions-sha` | SHAs from the archive DB with `included_in = NULL` or `included_in = current UUID` |
| `files-created` / `files-modified` / `files-archived` | Repo-wide git diff counts |
| `class`, `category`, `log-scope`, `modified`, `UUID`, `commit-sha`, `tags` | Auto-set |

Output goes to `ARCHIVE/CHANGELOG/`.

**Archive DB:** `ARCHIVE/archive.db` — shared with `archivist manifest`.

---

#### `archivist changelog story`

For story and creative writing modules. Includes writing-specific sections: scene development, character arcs, plot advancement, creative considerations, and next steps structured around narrative milestones.

Output goes to `ARCHIVE/CHANGELOG/`.

---

#### `archivist changelog vault`

For vault-level commits. In addition to standard file change tracking (with routing by `template`, `scaffold`, `script`, and `.archivist` keywords into named sections), captures:

- **Updated in This Commit** — which submodule paths were touched in the staged changes or given commit
- **Status Overview** — a full status table for all registered submodules: current short SHA, whether uncommitted changes exist, whether there are unpushed commits

```
| Module | SHA | Uncommitted | Unpushed |
|--------|-----|-------------|----------|
| `stories/my-story` | a1b2c3d | clean | pushed |
| `publications/the-bsp` | deadbee | ⚠️ yes | ⚠️ yes |
```

Useful for knowing exactly where everything stands before it matters.

Output goes to `ARCHIVE/`.

---

#### `archivist changelog seal`

Backfills a commit SHA into any unsealed changelogs included in the given commit, renames them to mark them as sealed, and updates the archive DB where a `UUID` is present in frontmatter.

Called automatically by the post-commit hook. You shouldn't need to run this by hand — but if the hook misfired, a seal got missed, or you're just that kind of person, here it is.

What "sealed" means in practice:

- **Frontmatter:** `commit-sha:` is backfilled with the short SHA.
- **Body table:** The `| Commit SHA | [fill in after commit] |` placeholder is replaced with the full SHA.
- **Filename:** The changelog is renamed from `CHANGELOG-YYYY-MM-DD.md` to `CHANGELOG-YYYY-MM-DD-{short_sha}.md`. This is the lock — sealed files are excluded from `find_active_changelog()` and will never be picked up as an existing changelog on future runs.
- **Archive DB:** If the changelog has a `UUID` in frontmatter, the `changelogs` table is upserted with the commit SHA and seal timestamp, and `edition_shas.included_in` transitions from UUID to short SHA for all claimed edition SHAs.

A commit with no unsealed changelogs exits cleanly. Running seal twice against the same commit is idempotent — the second pass sees the unsealed filename is gone from disk, warns, and exits without touching the sealed file.

```bash
archivist changelog seal abc123def456...
```

| Argument | Required | Description |
|---|---|---|
| `commit-sha` | ✅ | Full commit SHA to seal against |

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

Copies hooks directly into the current repo's `.git/hooks/`. Use this for repos that existed before `hooks install` was run. Detects submodules and offers to sync into each one as well.

```bash
archivist hooks sync
archivist hooks sync --dry-run
```

#### Hook behavior

**`pre-commit`:** Checks whether a manifest or changelog is staged. Only unsealed changelogs count — sealed files (carrying a SHA suffix) are explicitly excluded, because they're closed records from a past commit and have nothing to do with what you're staging right now. If nothing is found, prompts:

```
📋 archivist: No manifest or changelog found in staged files.

Generate one now?
  1. no — proceed with commit as-is
  2. manifest — generate an edition manifest
  3. changelog — generate a changelog
  4. stage existing — add an existing file to staging
  5. cancel — abort the commit
```

Selecting `manifest` prompts for an edition directory path, runs `archivist manifest`, and stages the result. Selecting `changelog` runs the appropriate changelog command for the module type and stages the result. Selecting `stage existing` prompts for a file path and stages it directly.

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

In Archivist-managed repos, it additionally:
1. Backfills the commit SHA into any **manifests** included in the commit that have an empty `commit-sha` frontmatter field (bash-native, runs unconditionally).
2. Delegates changelog sealing to `archivist changelog seal <full-sha>` — which handles backfill, rename, and DB update for changelogs atomically from Python.

The updated files are left unstaged — commit them deliberately, typically alongside the next edition or changelog.

---

## Archive DB

`archivist manifest` and `archivist changelog publication` share a SQLite database at `ARCHIVE/archive.db`, created automatically on first use.

```sql
-- Tracks edition commit SHAs from registration through changelog inclusion
CREATE TABLE edition_shas (
    sha             TEXT PRIMARY KEY,
    commit_message  TEXT,
    manifest_file   TEXT,
    discovered_at   TEXT,
    included_in     TEXT   -- NULL until claimed; holds UUID until sealed, then short_sha
);

-- Registry of all generated changelogs, keyed by UUID. Populated at seal time.
CREATE TABLE changelogs (
    uuid        TEXT PRIMARY KEY,
    commit_sha  TEXT,
    log_scope   TEXT,
    created_at  TEXT,
    sealed_at   TEXT,
    file_path   TEXT
);
```

The `included_in` lifecycle for `edition_shas`:

1. **`NULL`** — registered by `archivist manifest --register`, not yet in any changelog
2. **UUID** — claimed by `archivist changelog publication` on first (or any iterative) run
3. **Short SHA** — transitioned by `archivist changelog seal` at commit time

This chain is what makes iterative publication changelog re-runs correct (UUID re-surfaces the same SHAs) and makes it impossible for a SHA to appear in two changelogs (once it's been sealed to a commit SHA, the publication query never returns it again).

To inspect or correct entries directly:

```bash
sqlite3 ARCHIVE/archive.db

SELECT * FROM edition_shas;
SELECT * FROM changelogs;

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

1. Create `archivist/commands/changelog/yourtype.py` with a `run(args)` function. Use any existing changelog module as a reference — they all follow the same structure: call `run_changelog()` from `changelog_base.py` with your `build_frontmatter`, `build_body`, and optionally `post_changes`, `post_write`, and `print_summary` callables. Frontmatter fields are defined directly in the `auto` dict inside your frontmatter builder.

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

4. Add your module type to `APPARATUS_MODULE_TYPES` and `MODULE_CHANGELOG_COMMAND` in `utils/config.py`.

No reinstall needed — editable installs pick up changes immediately.

### Adding a new top-level command

1. Create `archivist/commands/yourcommand.py` with a `run(args)` function.
2. Add the parser to `build_parser()` in `cli.py`.
3. Add the routing branch to `main()` in `cli.py`.

### Changing template conventions

Archivist finds the manifest template by recursively searching `ARCHIVE/` for `MANIFEST_TEMPLATE.md`. To use a different directory structure or template name, update `_find_manifest_template()` in `manifest.py`. Template field order is always respected — frontmatter is rendered by iterating template keys in order.

Changelog frontmatter fields are not template-driven. To add, remove, or reorder fields for a changelog subcommand, edit the `auto` dict inside its `_build_frontmatter()` function directly.

### The `run_changelog()` base runner

All five changelog subcommands delegate to `run_changelog()` in `changelog_base.py`. If you're adding a new subcommand, you don't reimplement the pipeline — you provide callables:

| Parameter | Signature | Purpose |
|---|---|---|
| `build_frontmatter` | `(ctx: ChangelogContext) -> str` | Build the YAML frontmatter block |
| `build_body` | `(ctx: ChangelogContext) -> str` | Build the markdown body |
| `post_changes` | `(ctx: ChangelogContext) -> None` | Analyse the diff; mutate `ctx.data` |
| `get_extra_paths` | `(git_root: Path) -> list[Path]` | Extra paths to stage and diff |
| `print_summary` | `(ctx: ChangelogContext) -> None` | Custom summary output |
| `post_write` | `(ctx: ChangelogContext) -> None` | Side-effects after write (DB, etc.) |

`ChangelogContext` carries everything your callables could want: `git_root`, `output_dir`, raw and processed git changes, the full rename lookup, extracted descriptions, preserved user content, the changelog UUID, and a `data` dict for module-specific state.

---

## Contributing

This project is not accepting unsolicited PRs. Archivist is purpose-built for the LVNACY Apparatus, and its feature roadmap reflects that specific use case.

That said, discussion is welcome. If you have a suggestion, open an issue. If a discussion produces a viable feature request aligned with the Apparatus workflow, a PR may be invited.

If you want to adapt this for your own use — which is actively encouraged — fork it, modify freely, and build something useful. The [adapting section above](#adapting-archivist) is a good starting point.

---

## Changelog

Archivist uses Archivist. Generated changelogs live in [`ARCHIVE/`](./ARCHIVE/).

---

## Inspiration

This CLI was developed in collaboration with [Mad Alex](https://github.com/madalexxx), the driving force behind the LVNACY Apparatus. It was built to compile comprehensive changelogs and track the progress of their stories and the evolution of their workflows.

"Archivist" is inspired by a concept of the same name being written by Mad Alex, who has been kind enough to allow software and plugins that do not contain proprietary content to be made available as open source. Please follow and subscribe:

- **Newsletter:** [The Backstage Pass](https://backstage.carnivalofcalamity.xyz)
- **GitHub:** [madalexxx](https://github.com/madalexxx)

---

## License

This software is available under the MIT License. See [LICENSE](./LICENSE) for details.