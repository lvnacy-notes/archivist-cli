"""
archivist — Obsidian vault frontmatter and archive management tools.

Usage:
    archivist init

    archivist add                     <git-flags...> <url> [path] [--dry-run]
    archivist deinit                  <git-flags...> <path> [--retain] [--dry-run]

    archivist frontmatter add            -p <prop> [-v <value>] [--overwrite] [--dry-run]
    archivist frontmatter remove         -p <prop> [--dry-run]
    archivist frontmatter rename         -p <old> -n <new> [--dry-run]
    archivist frontmatter apply-template -t <template> -c <class> [--dry-run]

    archivist manifest <edition-dir> [commit-sha] [-v <volume>] [--dry-run]
    archivist manifest --register <sha> [--dry-run]

    archivist changelog                  [--dry-run]  ← general
    archivist changelog general          [commit-sha] [--path <path>] [--dry-run]
    archivist changelog library          [commit-sha] [--dry-run]
    archivist changelog publication      [commit-sha] [--dry-run]
    archivist changelog story            [commit-sha] [--dry-run]
    archivist changelog vault            [commit-sha] [--dry-run]
    archivist changelog seal             <commit-sha>

    archivist reclassify --from <old-class> --to <new-class> [--path <path>] [--dry-run]

    archivist sync                       [--dry-run]

    archivist hooks install              [--dry-run]
    archivist hooks sync                 [--dry-run]
"""

import argparse
import importlib.metadata
import sys

from archivist.formatter import (
    ArchivistHelpFormatter,
    fmt_examples,
    fmt_warning,
)
from archivist.utils import error, progress
from archivist.utils.cli_helpers import (
    add_commit_sha_arg,
    add_dry_run,
    add_note_selection_args,
    configure_logging,
    find_subcommand,
    split_git_passthrough,
    split_passthrough,
    subparser,
)

BANNER = r"""
  ┌─────────────────────────────────────────────────────────────────────┐
  │                                                                     │
  │   █████╗ ██████╗  ██████╗██╗  ██╗██╗██╗   ██╗██╗███████╗████████╗   │
  │  ██╔══██╗██╔══██╗██╔════╝██║  ██║██║██║   ██║██║██╔════╝╚══██╔══╝   │
  │  ███████║██████╔╝██║     ███████║██║██║   ██║██║███████╗   ██║      │
  │  ██╔══██║██╔══██╗██║     ██╔══██║██║╚██╗ ██╔╝██║╚════██║   ██║      │
  │  ██║  ██║██║  ██║╚██████╗██║  ██║██║ ╚████╔╝ ██║███████║   ██║      │
  │  ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝╚═╝  ╚═══╝  ╚═╝╚══════╝   ╚═╝      │
  │                                                                     │
  │                Obsidian vault & archive management                  │
  │              frontmatter  ·  manifest  ·  changelog                 │
  │                  everything in its fucking place                    │
  │                                                                     │
  └─────────────────────────────────────────────────────────────────────┘
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog = "archivist",
        description=(
            BANNER
            + "  Bulk-manage YAML frontmatter and generate archive documents.\n"
            + "  Finds the git root automatically. Run from anywhere in the repo.\n"
            + fmt_examples(
                "archivist init",
                "archivist frontmatter add -p status -v draft",
                "archivist changelog story --dry-run",
                "archivist manifest editions/042 --dry-run",
            )
        ),
        formatter_class = ArchivistHelpFormatter,
    )
    try:
        _version = importlib.metadata.version("archivist")
    except importlib.metadata.PackageNotFoundError:
        _version = "unknown"
    parser.add_argument(
        "--version", "-V",
        action = "version",
        version = f"archivist {_version}",
    )
    parser.add_argument(
        "--quiet", "-q",
        action = "store_true",
        default = False,
        help = "Suppress all output except errors. Useful for scripting and cron jobs.",
    )
    parser.add_argument(
        "--verbose", "--debug",
        dest = "verbose",
        action = "store_true",
        default = False,
        help = "Enable debug output. --debug is an alias because it's more honest.",
    )
    parser.add_argument(
        "--log-file",
        dest = "log_file",
        default = None,
        metavar = "PATH",
        help = "Write a full debug log to this path (timestamps, all levels, no emoji).",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    subparsers.required = True

    # -----------------------------------------------------------------------
    # init
    # -----------------------------------------------------------------------

    init_p = subparser(
        subparsers,
        "init",
        help = "Initialize archivist for this project",
        description = (
            "Run once per project. Once per machine after cloning.\n\n"
            "No .archivist found: asks what kind of project this is, writes the\n"
            "config, installs the hooks, and leaves you to it. Already configured:\n"
            "shows you what's there and offers to update it. Just don't overthink it.\n"
            + fmt_examples(
                "archivist init",
                "archivist init --dry-run",
            )
        ),
        epilog = fmt_warning(
            "Overwrites any existing git hooks in .git/hooks/ — no backup, no undo.\n"
            "  Check what's in there first: `ls .git/hooks/`\n"
            "  Don't be an idiot. Preserve anything you need before you confirm."
        ),
    )
    add_dry_run(init_p, help = "Preview without writing any files")


    # -----------------------------------------------------------------------
    # add
    # -----------------------------------------------------------------------

    add_module_p = subparser(
        subparsers,
        "add",
        help = "Clone a module and register it with the Apparatus",
        description = (
            "Clones a remote module as a git submodule (if inside a git repo) or\n"
            "a standalone clone (if not). Registers the module in the Apparatus\n"
            "registry and records the containment relationship if the current\n"
            "directory is a registered superproject.\n\n"
            "Git runs first. If git fails, nothing is written to the registry.\n"
            "The module doesn't exist until git says it does.\n\n"
            "Any flag archivist doesn't recognize is forwarded straight to git\n"
            "(git submodule add or git clone, whichever applies), written exactly\n"
            "as you'd type it for git itself — no '--' or '=' required:\n\n"
            "    archivist add <git-flags...> <url> [path] [--dry-run]\n\n"
            "archivist finds the url by its shape (scheme://, user@host:, or a\n"
            "leading ./, ../, /, ~), the same way git itself tells a repository\n"
            "source apart from a local path. Everything before it is forwarded to\n"
            "git verbatim; --dry-run (archivist's own flag, not git's) goes after\n"
            "the url/path, not mixed in with the git flags.\n\n"
            "Prefer to be explicit about where the git flags end? A bare '--'\n"
            "right before the url works too, same as git's own convention:\n\n"
            "    archivist add <git-flags...> -- <url> [path] [--dry-run]\n"
            + fmt_examples(
                "archivist add git@github.com:user/repo.git",
                "archivist add git@github.com:user/repo.git modules/repo",
                "archivist add --depth 1 git@github.com:user/repo.git modules/repo",
                "archivist add --name custom git@github.com:user/repo.git --dry-run",
                "archivist add --name custom -- git@github.com:user/repo.git",
            )
        ),
    )
    add_module_p.add_argument(
        "url",
        help = "Remote URL to clone or add as a submodule.",
    )
    add_module_p.add_argument(
        "path",
        nargs = "?",
        default = None,
        metavar = "PATH",
        help = "Local destination path. Defaults to whatever git decides.",
    )
    add_dry_run(add_module_p, help = "Print the git command and registration plan without executing either")

    # -----------------------------------------------------------------------
    # deinit
    # -----------------------------------------------------------------------

    deinit_p = subparser(
        subparsers,
        "deinit",
        help = "Deregister a module and remove it from the Apparatus",
        description = (
            "Removes a module from the Apparatus registry and from git.\n\n"
            "Operation order is non-negotiable: Apparatus first, git second.\n"
            "If git runs first and succeeds, config.yaml is gone — the registry\n"
            "has lost its recovery information. Apparatus cleanup fails? Module\n"
            "is still on disk; retry is possible. Reverse the order and you get\n"
            "an unrecoverable mess. Don't.\n\n"
            "Confirmation prompt fires even with --dry-run, because a dry run\n"
            "that skips the confirmation tells you nothing useful about what\n"
            "would actually happen.\n\n"
            "If git already ran (manually or in a previous failed attempt) and\n"
            "only the registry needs cleaning, use --retain.\n\n"
            "Any flag archivist doesn't recognize is forwarded straight to\n"
            "`git submodule deinit`, written exactly as you'd type it for git\n"
            "itself — no '--' or '=' required:\n\n"
            "    archivist deinit <git-flags...> <path> [--retain] [--dry-run]\n\n"
            "--retain and --dry-run are archivist's own flags, not git's — they\n"
            "go after the path, not mixed in with the git flags.\n\n"
            "Prefer to be explicit about where the git flags end? A bare '--'\n"
            "right before the path works too:\n\n"
            "    archivist deinit <git-flags...> -- <path> [--retain] [--dry-run]\n"
            + fmt_examples(
                "archivist deinit modules/repo",
                "archivist deinit --force modules/repo --retain",
                "archivist deinit modules/repo --dry-run",
                "archivist deinit --force -- modules/repo",
            )
        ),
        epilog = fmt_warning(
            "This operation removes a module from the registry and from disk.\n"
            "  Apparatus cleanup runs first. Git cleanup runs second.\n"
            "  There is no undo. Use --dry-run first. You know the drill."
        ),
    )
    deinit_p.add_argument(
        "path",
        help = "Path to the module to remove.",
    )
    deinit_p.add_argument(
        "--retain",
        action = "store_true",
        help = (
            "Registry cleanup only — skip the git operation entirely. "
            "Use when the git step already ran and only the registry needs cleaning."
        ),
    )
    add_dry_run(deinit_p, help = "Preview the Apparatus changes and git command without executing either")


    # -----------------------------------------------------------------------
    # frontmatter
    # -----------------------------------------------------------------------

    fm_parser = subparser(
        subparsers,
        "frontmatter",
        help = "Bulk-manage YAML frontmatter properties across all notes",
        description = (
            "Bulk-manage YAML frontmatter across every .md file in the repo.\n"
            "All subcommands recurse from the git root. --dry-run is always available\n"
            "and you should probably use it first."
            + fmt_examples(
                "archivist frontmatter add -p status -v draft",
                "archivist frontmatter remove -p reviewed",
                "archivist frontmatter rename -p status -n state",
                "archivist frontmatter apply-template -t template.md -c character",
            )
        ),
    )
    fm_sub = fm_parser.add_subparsers(dest = "fm_command", metavar = "<subcommand>")
    fm_sub.required = True

    # frontmatter add
    add_p = subparser(
        fm_sub,
        "add",
        help = "Add a property to notes (all, or a scoped selection)",
        description = (
            "Add means add. Adds a property to every .md file that matches\n"
            "the selection criteria — which defaults to 'all of them' if you\n"
            "don't bother scoping it. Also creates a frontmatter block if\n"
            "there isn't one. Skips notes that already have the property\n"
            "unless you insist with --overwrite.\n\n"
            "Scope with --file, --path, --class, or --tag. Combine freely.\n"
            "--file is mutually exclusive with the rest."
            + fmt_examples(
                "archivist frontmatter add -p reviewed",
                "archivist frontmatter add -p status -v draft",
                "archivist frontmatter add -p status -v draft --class article",
                "archivist frontmatter add -p status -v published --overwrite --path content/",
                "archivist frontmatter add -p status -v draft --file notes/one.md",
                "archivist frontmatter add -p status -v draft --dry-run",
            )
        ),
    )
    add_p.add_argument(
        "-p",
        "--property",
        required = True,
        metavar = "PROP",
        help = "Property name to add"
    )
    add_p.add_argument(
        "-v",
        "--value",
        default = None,
        metavar = "VALUE",
        help = "Value to pair with the property (omit for bare key)"
    )
    add_p.add_argument(
        "--overwrite",
        action = "store_true",
        help = "Overwrite the property if it already exists"
    )
    add_note_selection_args(add_p)
    add_dry_run(add_p, help = "Preview changes without writing to disk")

    # frontmatter remove
    rm_p = subparser(
        fm_sub,
        "remove",
        help = "Remove a property from notes (all, or a scoped selection)",
        description = (
            "You are smart enough to use my services, so I trust you to understand\n"
            "what remove means. But just in case: it removes a property and its\n"
            "value from every matching .md file. If removal leaves the frontmatter\n"
            "block empty, the block is dropped.\n\n"
            "Scope with --file, --path, --class, or --tag. Combine freely.\n"
            "--file is mutually exclusive with the rest.\n"
            + fmt_examples(
                "archivist frontmatter remove -p status",
                "archivist frontmatter remove -p status --class article",
                "archivist frontmatter remove -p status --path content/drafts",
                "archivist frontmatter remove -p status --file notes/one.md",
                "archivist frontmatter remove -p status --dry-run",
            )
        ),
    )
    rm_p.add_argument(
        "-p",
        "--property",
        required = True,
        metavar = "PROP",
        help = "Property name to remove"
    )
    add_note_selection_args(rm_p)
    add_dry_run(rm_p, help = "Preview changes without writing to disk")

    # frontmatter rename
    ren_p = subparser(
        fm_sub,
        "rename",
        help = "Rename a property across notes (all, or a scoped selection)",
        description = (
            "Rename is rename, but with a few caveats. Listen carefully: this\n"
            "will rename a property across all matching notes, and it will\n"
            "preserve its value EXACTLY. You will end up with strings in fields\n"
            "that previously contained numbers. So check your fucking work.\n"
            "Handles scalar values, inline lists, and multi-line block sequences.\n\n"
            "Scope with --file, --path, --class, or --tag. Combine freely.\n"
            "--file is mutually exclusive with the rest.\n"
            + fmt_examples(
                "archivist frontmatter rename -p status -n state",
                "archivist frontmatter rename -p status -n state --class article",
                "archivist frontmatter rename -p tags -n keywords --path content/",
                "archivist frontmatter rename -p tags -n keywords --file notes/one.md",
                "archivist frontmatter rename -p tags -n keywords --dry-run",
            )
        ),
    )
    ren_p.add_argument(
        "-p",
        "--property",
        required = True,
        metavar = "PROP",
        help = "Current property name"
    )
    ren_p.add_argument(
        "-n",
        "--new-name",
        required = True,
        metavar = "NEW",
        help = "New property name"
    )
    add_note_selection_args(ren_p)
    add_dry_run(ren_p, help = "Preview changes without writing to disk")

    # frontmatter apply-template
    tpl_p = subparser(
        fm_sub,
        "apply-template",
        help = "Apply a frontmatter template to notes matching specified criteria",
        description = (
            "Provide a template note with the properties and structure you want,\n"
            "and provide the criteria for which notes it applies to. I'll handle the rest.\n\n"
            "The template is the authority. The template is the law. You built it.\n\n"
            "At least one selection flag is required — --file, --class, --path, or\n"
            "--tag. All provided filters must match (AND logic). I am not rewriting\n"
            "your entire fucking vault because you forgot to be specific.\n\n"
            "--file targets exactly one note and is mutually exclusive with the rest.\n\n"
            "For each matching note:\n\n"
            "  · Adds properties from the template that the note is missing\n"
            "  · Leaves existing values alone\n"
            "  · Removes properties the template doesn't include\n"
            "  · Reorders everything to match the template\n\n"
            + fmt_examples(
                "archivist frontmatter apply-template -t template.md -c character",
                "archivist frontmatter apply-template -t template.md --path content/essays",
                "archivist frontmatter apply-template -t template.md --tag draft",
                "archivist frontmatter apply-template -t template.md --file notes/one.md",
                "archivist frontmatter apply-template -t template.md -c article --tag draft --path content/",
                "archivist frontmatter apply-template -t template.md -c location --dry-run",
            )
        ),
    )
    tpl_p.add_argument(
        "-t",
        "--template",
        required = True,
        metavar = "FILE",
        help = "Path to the template markdown file"
    )
    add_note_selection_args(tpl_p, require_one=True)
    add_dry_run(tpl_p, help = "Preview changes without writing to disk")


    # -----------------------------------------------------------------------
    # manifest
    # -----------------------------------------------------------------------

    mf_parser = subparser(
        subparsers,
        "manifest",
        help = "Generate an edition manifest, or register a commit SHA",
        description = (
            "You know what I like? When something is delivered and well\n"
            "documented, so I know exactly what's in it. THat's what this\n"
            "does. It generates an edition manifest in ARCHIVE/. Sure, it's\n"
            "highly opinionated, but remember, this is about simplifying\n"
            "processes.\n"
            + fmt_examples(
                "archivist manifest editions/042",
                "archivist manifest editions/042 a1b2c3d",
                "archivist manifest editions/042 -v 3 --dry-run",
                "archivist manifest --register a1b2c3d",
            )
        ),
    )
    mf_parser.add_argument(
        "edition_dir",
        nargs = "?",
        default = None,
        metavar = "EDITION-DIR",
        help = "Path to the edition directory"
    )
    add_commit_sha_arg(mf_parser)
    mf_parser.add_argument(
        "-v",
        "--volume",
        default = None,
        metavar = "NUM",
        help="Volume number/identifier for the manifest"
    )
    mf_parser.add_argument(
        "--register",
        metavar = "SHA",
        default = None,
        help = "Register a commit SHA in the archive DB (standalone mode)"
    )
    add_dry_run(mf_parser, help = "Preview without writing to disk or DB")

    # -----------------------------------------------------------------------
    # changelog
    # -----------------------------------------------------------------------

    cl_parser = subparser(
        subparsers,
        "changelog",
        help = "Generate a changelog (auto-routes by module type if .archivist is present)",
        description = (
            "Run this bare and Archivist will check your .archivist config,\n"
            "figure out what kind of project you're in, and run the right\n"
            "subcommand without you having to think about it. You're welcome.\n\n"
            "If there's no .archivist — or you've somehow managed to set an\n"
            "unrecognized module type — it falls back to general. Also fine.\n\n"
            "Note: --help is handled before any of that routing happens, so\n"
            "this is always what you'll see here regardless of your config.\n"
            "For subcommand-specific help, use:\n\n"
            "    archivist changelog <subcommand> --help\n"
            + fmt_examples(
                "archivist changelog",
                "archivist changelog --commit-sha a1b2c3d",
                "archivist changelog --dry-run",
                "archivist changelog --path src/",
                "archivist changelog publication --help",
            )
        ),
    )
    add_dry_run(cl_parser)
    cl_parser.add_argument(
        "--commit-sha",
        dest = "commit_sha",
        default = None,
        metavar = "SHA",
        help = "Diff against a specific commit (default: staged changes)"
    )
    cl_parser.add_argument(
        "--path",
        default = None,
        metavar = "PATH",
        help = "File or directory to stage and scope the diff to"
    )

    cl_sub = cl_parser.add_subparsers(dest="cl_command", metavar="<subcommand>")
    cl_sub.required = False

    # changelog general
    gen_p = subparser(
        cl_sub,
        "general",
        help = "Generic changelog — same as bare `archivist changelog`",
        description = (
            "Clean and minimal. No project-type-specific sections, no opinions\n"
            "about what kind of work you're doing. Just the diff, the table,\n"
            "and fields for you to fill in.\n\n"
            "Running `archivist changelog` bare does the same thing — unless\n"
            "you have a .archivist config, in which case it routes to whatever\n"
            "subcommand matches your module type. If you're explicitly calling\n"
            "this, you either have no config or you're overriding it. Both fine.\n"
            + fmt_examples(
                "archivist changelog general",
                "archivist changelog general a1b2c3d",
                "archivist changelog general --path src/ --dry-run",
            )
        ),
    )
    add_commit_sha_arg(gen_p)
    gen_p.add_argument(
        "--path",
        default = None,
        metavar = "PATH",
        help = "File or directory to stage and scope the changelog to"
    )
    add_dry_run(gen_p)

    # changelog publication
    pub_p = subparser(
        cl_sub,
        "publication",
        help = "Changelog for a newsletter or publication module",
        description = (
            "This command generates a changelog for newsletter and publication\n"
            "modules. It queries the archive DB for edition commit hashes that\n"
            "have not yet been recorded in any changelog. If it finds some, it\n"
            "includes them in the editions-sha frontmatter and marks them\n"
            "as claimed in a single transaction. Each hash appears in exactly\n"
            "one changelog, never duplicated, never silently dropped.\n\n"
            "This requires ARCHIVE/archive.db, which is created automatically\n"
            "on the first run.\n"
            + fmt_examples(
                "archivist changelog publication",
                "archivist changelog publication a1b2c3d",
                "archivist changelog publication --dry-run",
            )
        ),
    )
    add_commit_sha_arg(pub_p)
    add_dry_run(pub_p, help = "Preview without writing to disk or DB")

    # changelog story
    story_p = subparser(
        cl_sub,
        "story",
        help = "Changelog for a story or creative writing module",
        description = (
            "This generates a session changelog for story and creative writing\n"
            "modules. It includes writing-specific sections: scene development,\n"
            "character arcs, plot advancement, creative considerations, and\n"
            "next steps structured around narrative milestones.\n"
            + fmt_examples(
                "archivist changelog story",
                "archivist changelog story a1b2c3d",
                "archivist changelog story --dry-run",
            )
        ),
    )
    add_commit_sha_arg(story_p)
    add_dry_run(story_p)

    # changelog vault
    vault_p = subparser(
        cl_sub,
        "vault",
        help = "Changelog for a vault-level commit, including submodule status",
        description = (
            "This generates the Vault-level changelog. It tracks standard file\n"
            "changes and submodule state: current SHAs, what's dirty, what\n"
            "hasn't been pushed.\n\n"
            "Useful for knowing exactly where everything stands before it matters."
            + fmt_examples(
                "archivist changelog vault",
                "archivist changelog vault a1b2c3d",
                "archivist changelog vault --dry-run",
            )
        ),
    )
    add_commit_sha_arg(vault_p)
    add_dry_run(vault_p)

    # changelog library
    lib_p = subparser(
        cl_sub,
        "library",
        help = "Changelog for a library or catalog module",
        description = (
            "This generates a changelog for library modules. It tracks works\n"
            "catalogued, authors, publications, and definitions in symmetry.\n"
            + fmt_examples(
                "archivist changelog library",
                "archivist changelog library a1b2c3d",
                "archivist changelog library --dry-run",
            )
        ),
    )
    add_commit_sha_arg(lib_p)
    add_dry_run(lib_p)

    # changelog seal
    seal_p = subparser(
        cl_sub,
        "seal",
        help = "Backfill a commit SHA into changelogs and update the archive DB",
        description = (
            "Called automatically by the post-commit hook. You probably don't\n"
            "need to run this manually — but if a hook failed, a seal got\n"
            "missed, or you're just that kind of person, here it is.\n\n"
            "Finds any unsealed changelogs in the given commit, backfills the\n"
            "SHA into frontmatter and the body table, renames the file to mark\n"
            "it as sealed, and updates the archive DB if a UUID is present in\n"
            "the frontmatter.\n\n"
            "Sealed changelogs are never picked up as an existing changelog on\n"
            "future runs. The rename is the lock.\n"
            + fmt_examples(
                "archivist changelog seal abc123def456...",
            )
        ),
    )
    seal_p.add_argument(
        "commit_sha",
        metavar = "COMMIT-SHA",
        help = "Full commit SHA to seal against"
    )

    # -----------------------------------------------------------------------
    # reclassify
    # -----------------------------------------------------------------------
    rc_parser = subparser(
        subparsers,
        "reclassify",
        help = "Replace a frontmatter class value across all matching notes",
        description = (
            "Find every .md file whose frontmatter `class` field matches the\n"
            "given value and rewrite it to a new value. Surgical: only the\n"
            "`class:` line is touched. Everything else in the frontmatter is\n"
            "left exactly where it is.\n\n"
            "Matching is case-insensitive. The --to value is written verbatim.\n"
            "Scope with --path, --file, --class, or --tag. Combine freely.\n"
            "--file is mutually exclusive with the rest.\n"
            "Patterns in .archivist `ignores` are respected automatically."
            + fmt_examples(
                "archivist reclassify --from article --to column",
                "archivist reclassify --from article --to column --path content/",
                "archivist reclassify --from article --to column --class article --tag published",
                "archivist reclassify --from article --to column --dry-run",
            )
        ),
    )
    rc_parser.add_argument(
        "--from",
        dest="from_class",
        required = True,
        metavar = "OLD",
        help = "Current class value to match (case-insensitive)"
    )
    rc_parser.add_argument(
        "--to",
        dest = "to_class",
        required = True,
        metavar = "NEW",
        help="New class value to write"
    )
    add_note_selection_args(rc_parser)
    add_dry_run(rc_parser, help = "Preview changes without writing to disk")
    

    # -----------------------------------------------------------------------
    # sync
    # -----------------------------------------------------------------------
    sync_p = subparser(
        subparsers,
        "sync",
        help = "Backfill registry containment for modules that already have Archivist config",
        description = (
            "Non-interactive. Walks the submodule tree from here down — recursively,\n"
            "any module type, not just vaults, since nesting a module inside another\n"
            "module isn't some vault-exclusive privilege — and makes sure every module\n"
            "that already carries a .archivist/config.yaml with a uuid is registered\n"
            "and correctly linked to its direct container in module_bays.\n\n"
            "This is not init. It never asks you a single fucking question. Anything\n"
            "it can't resolve from what's already committed to disk — no config, no\n"
            "uuid, a uuid with no apparatus assignment yet made — gets reported and\n"
            "skipped. Go run `archivist init` there yourself if it needs a decision.\n\n"
            "Reach for this instead of add/init when:\n\n"
            "  · You `git submodule add`ed something by hand, bypassing archivist add\n"
            "  · You inherited nesting that predates this containment logic entirely\n"
            "  · A directory got renamed or moved and its cached registry path is stale\n"
            + fmt_examples(
                "archivist sync",
                "archivist sync --dry-run",
            )
        ),
    )
    add_dry_run(sync_p, help = "Preview what would be registered and linked without touching the registry")


    # -----------------------------------------------------------------------
    # hooks
    # -----------------------------------------------------------------------
    hooks_parser = subparser(
        subparsers,
        "hooks",
        help = "Install or sync archivist git hooks",
        description = (
            "Use this command to manage Archivist's git hooks. But, like, just\n"
            "barely. Hooks are installed globally into `~/.git-templates/hooks/`\n"
            "and copied automatically into new clones. Don't be dumb; back up\n"
            "your shit before you wipe everything out. Because this will wipe\n"
            "everything out."
            "Existing repos can be synced manually with `hooks sync`."
            + fmt_examples(
                "archivist hooks install",
                "archivist hooks install --dry-run",
                "archivist hooks sync",
            )
        ),
    )
    hooks_sub = hooks_parser.add_subparsers(dest="hooks_command", metavar="<subcommand>")
    hooks_sub.required = True

    # hooks install
    hi_p = subparser(
        hooks_sub,
        "install",
        help = "Install hooks globally into ~/.git-templates/hooks/",
        description = (
            "Write hook scripts into `~/.git-templates/hooks/` and ensure git is\n"
            "configured to use that directory as its template source. All future\n"
            "`git clone` and `git init` operations will automatically include the hooks.\n"
            + fmt_examples(
                "archivist hooks install",
                "archivist hooks install --dry-run",
            )
        ),
    )
    add_dry_run(hi_p, help = "Preview without writing any files")

    # hooks sync
    hs_p = subparser(
        hooks_sub,
        "sync",
        help = "Sync hooks into the current repo's .git/hooks/",
        description = (
            "Copy hooks directly into the current repo's `.git/hooks/`. Use this\n"
            "for repos that existed before `hooks install` was run."
            + fmt_examples(
                "archivist hooks sync",
                "archivist hooks sync --dry-run",
            )
        ),
    )
    add_dry_run(hs_p, help = "Preview without writing any files")

    # -----------------------------------------------------------------------
    # migrate
    # -----------------------------------------------------------------------

    migrate_p = subparser(
        subparsers,
        "migrate",
        help = "Migrate legacy flat .archivist config to .archivist/ directory form",
        description = (
            "One-shot. Reads your flat .archivist file, creates .archivist/,\n"
            "writes the config to .archivist/config.yaml, and deletes the flat\n"
            "file. Content is not modified — this is a structural migration only.\n\n"
            "Safe to preview with --dry-run first. Requires explicit confirmation\n"
            "before deleting anything because you're an adult and you should know\n"
            "what you're agreeing to.\n\n"
            "Already on the directory form? This will tell you and exit.\n"
            "No flat .archivist to migrate? Same.\n\n"
            "After migration, stage and commit the changes:\n\n"
            "    git add .archivist/\n"
            "    git rm --cached .archivist\n"
            "    git commit -m 'chore: migrate .archivist to directory form'\n"
            + fmt_examples(
                "archivist migrate --dry-run",
                "archivist migrate",
            )
        ),
        epilog = fmt_warning(
            "This deletes your flat .archivist file. It is not recoverable\n"
            "  outside of git. Run --dry-run first. Then run the real thing.\n"
            "  Don't say you weren't told."
        ),
    )
    add_dry_run(migrate_p, help = "Preview the migration plan without writing or deleting anything")

    # -----------------------------------------------------------------------
    # _registry-sync (internal — not user-facing)
    # -----------------------------------------------------------------------

    subparser(
        subparsers,
        "_registry-sync",
        help = argparse.SUPPRESS,
        description = argparse.SUPPRESS,
    )

    return parser


def main():
    parser = build_parser()
    import argcomplete
    argcomplete.autocomplete(parser)

    raw_argv = sys.argv[1:]
    found = find_subcommand(raw_argv)

    if found and found[1] in ("add", "deinit"):
        # add/deinit have a grammar split_passthrough()/parse_known_args()
        # can't resolve alone: git flags and archivist flags share the same
        # argv, with only the TARGET's shape (not its position) telling a
        # repository url or submodule path apart from a preceding flag's
        # value. find_subcommand() locates the subcommand regardless of
        # where archivist's own global flags land; split_git_passthrough()
        # then separates git's tokens from archivist's for this subcommand.
        global_prefix, command_name, rest = found
        git_passthrough, remainder = split_git_passthrough(rest)
        args, unrecognized = parser.parse_known_args(global_prefix + [command_name] + remainder)
        args.passthrough = git_passthrough + unrecognized
    else:
        # Every other command has a fully-defined argument surface, or
        # find_subcommand() declined to guess (--help, --version, an
        # unrecognized flag) — argparse handles all of those correctly on
        # its own and shouldn't be second-guessed.
        archivist_argv, forced_passthrough = split_passthrough(raw_argv)
        args, unrecognized = parser.parse_known_args(archivist_argv)
        passthrough = unrecognized + forced_passthrough

        if getattr(args, "command", None) in ("add", "deinit"):
            # find_subcommand() bailed (unusual global-flag ordering it
            # couldn't confidently parse) but argparse still resolved the
            # command the old way — preserve that instead of erroring.
            args.passthrough = passthrough
        elif passthrough:
            # Every other command has a fully-defined argument surface.
            # Leftover garbage here isn't passthrough, it's a typo, and
            # pretending otherwise just defers the confusion to git or
            # nowhere at all.
            parser.error(f"unrecognized arguments: {' '.join(passthrough)}")

    configure_logging(args)

    if args.command == "init":
        from archivist.commands.init import run
        run(args)

    # If a command has three or more subcommands,
    # route through a dict to avoid a long if-elif chain
    elif args.command == "frontmatter":
        fm_commands = {
            "add": "archivist.commands.frontmatter.add",
            "remove": "archivist.commands.frontmatter.remove",
            "rename": "archivist.commands.frontmatter.rename",
            "apply-template": "archivist.commands.frontmatter.apply_template",
        }
        
        if args.fm_command not in fm_commands:
            error(f"Unknown frontmatter subcommand '{ args.fm_command }'")
            return
        
        module_path = fm_commands[args.fm_command]
        run = importlib.import_module(module_path).run
        run(args)

    elif args.command == "manifest":
        from archivist.commands.manifest import run
        run(args)

    elif args.command == "changelog":
        cl_command = getattr(args, "cl_command", None)

        from archivist.utils import (
            get_repo_root,
            get_module_type,
            MODULE_CHANGELOG_COMMAND,
        )
        from archivist.utils.config import find_changelog_plugin, load_changelog_plugin

        # Auto-detect from .archivist when no subcommand was explicitly given
        if cl_command is None:
            git_root = get_repo_root()
            module_type = get_module_type(git_root)
            if module_type and module_type in MODULE_CHANGELOG_COMMAND:
                cl_command = MODULE_CHANGELOG_COMMAND[module_type]
                progress(f"  → .archivist: module-type '{ module_type }' → archivist changelog {cl_command}")
            else:
                cl_command = "general"
        else:
            git_root = get_repo_root()

        # Normalize attrs that subcommand run() functions expect but
        # aren't present when routing through the bare `changelog` parser
        if not hasattr(args, "commit_sha"):
            args.commit_sha = None
        if not hasattr(args, "path"):
            args.path = None

        # Plugin check — only for module-type-routed commands (ie. when no
        # explicit subcommand was given). Explicit subcommands like
        # `archivist changelog library` bypass the plugin intentionally:
        # if you're naming the subcommand, you want the built-in.
        if getattr(args, "cl_command", None) is None:
            plugin_path = find_changelog_plugin(git_root)
            if plugin_path:
                progress(f"  → changelog plugin found: { plugin_path.relative_to(git_root) }")
                plugin = load_changelog_plugin(plugin_path)
                plugin.run(args)
                return

        cl_commands = {
            "general": "archivist.commands.changelog.general",
            "publication": "archivist.commands.changelog.publication",
            "story": "archivist.commands.changelog.story",
            "vault": "archivist.commands.changelog.vault",
            "library": "archivist.commands.changelog.library",
            "seal": "archivist.commands.changelog.seal",
        }

        module_path = cl_commands.get(cl_command, "archivist.commands.changelog.general")
        run = importlib.import_module(module_path).run
        run(args)

    elif args.command == "reclassify":
        from archivist.commands.reclassify import run
        run(args)

    elif args.command == "sync":
        from archivist.commands.sync import run
        run(args)

    elif args.command == "hooks":
        from archivist.commands.hooks.install import run_install, run_sync
        if args.hooks_command == "install":
            run_install(args)
        elif args.hooks_command == "sync":
            run_sync(args)

    elif args.command == "add":
        from archivist.commands.add import run
        run(args)
 
    elif args.command == "deinit":
        from archivist.commands.deinit import run
        run(args)
 
    elif args.command == "_registry-sync":
        from archivist.commands.hooks.install import run_registry_sync
        run_registry_sync()