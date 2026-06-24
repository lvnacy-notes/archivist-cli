"""
archivist — Obsidian vault frontmatter and archive management tools.

Usage:
    archivist init

    archivist add                     <url> [path] [git-flags...] [--dry-run]
    archivist deinit                  <path> [git-flags...] [--retain] [--dry-run]

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

    archivist hooks install              [--dry-run]
    archivist hooks sync                 [--dry-run]

    archivist migrate                    [--dry-run]
"""

import argparse
import importlib.metadata
import logging

from archivist.formatter import (
    ArchivistFileFormatter,
    ArchivistHelpFormatter,
    ArchivistStreamHandler,
    ArchivistTerminalFormatter,
    fmt_examples,
    fmt_warning,
)
from archivist.utils import error, progress

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


def _configure_logging(args: argparse.Namespace) -> None:
    """
    Configure the "archivist" logger based on CLI flags. Called once in
    main(), immediately after parse_args(), before any command module runs.

    The logger is set to DEBUG at the root so all records are captured —
    the terminal handler's level determines what actually hits the screen.

    --quiet:          ERROR and above only (errors, nothing else)
    default:          INFO and above (progress, success, warnings, errors)
    --verbose/--debug: DEBUG and above (everything, including per-file noise)
    --log-file <path>: full DEBUG log to file regardless of terminal verbosity
    """
    ledger = logging.getLogger("archivist")
    ledger.setLevel(logging.DEBUG)  # capture everything; handlers filter down

    terminal = ArchivistStreamHandler()
    terminal.setFormatter(ArchivistTerminalFormatter())
    if getattr(args, "quiet", False):
        terminal.setLevel(logging.ERROR)
    elif getattr(args, "verbose", False):
        terminal.setLevel(logging.DEBUG)
    else:
        terminal.setLevel(logging.INFO)
    ledger.addHandler(terminal)

    log_file = getattr(args, "log_file", None)
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(ArchivistFileFormatter())
        ledger.addHandler(file_handler)


def _add_note_selection_args(p: argparse.ArgumentParser, *, require_one: bool = False) -> None:
    """
    Register the four shared note-selection arguments onto a frontmatter subparser.

    --file, --path, --class, --class-property, --tag

    All optional by default. Pass require_one=True for apply-template, which
    demands at least one selection criterion because it absolutely refuses to
    restructure your entire fucking vault on a whim.
    """
    scope = p.add_argument_group(
        "note selection",
        "Scope the operation. Omit all flags to target every .md file in the repo.\n"
        "--file is mutually exclusive with every other selector.",
    )
    scope.add_argument(
        "--file",
        default = None,
        metavar = "FILE",
        help = "Target exactly this one .md file. Cannot be combined with other selectors.",
    )
    scope.add_argument(
        "--path",
        default = None,
        metavar = "PATH",
        help = "Limit the directory walk to this subtree (relative to repo root)",
    )
    scope.add_argument(
        "-c",
        "--class",
        dest = "note_class",
        default = None,
        metavar = "CLASS",
        help = "Only notes whose class frontmatter value matches (e.g. 'character')",
    )
    scope.add_argument(
        "--class-property",
        default = "class",
        metavar = "PROP",
        help = "Frontmatter key used to identify the class (default: class)",
    )
    scope.add_argument(
        "--tag",
        default = None,
        metavar = "TAG",
        help = "Only notes carrying this tag in their frontmatter",
    )


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

    init_p = subparsers.add_parser(
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
        formatter_class = ArchivistHelpFormatter,
    )
    init_p.add_argument(
        "--dry-run",
        action = "store_true",
        help = "Preview without writing any files"
    )


    # -----------------------------------------------------------------------
    # add
    # -----------------------------------------------------------------------

    add_module_p = subparsers.add_parser(
        "add",
        help = "Clone a module and register it with the Apparatus",
        description = (
            "Clones a remote module as a git submodule (if inside a git repo) or\n"
            "a standalone clone (if not). Registers the module in the Apparatus\n"
            "registry and records the containment relationship if the current\n"
            "directory is a registered superproject.\n\n"
            "Git runs first. If git fails, nothing is written to the registry.\n"
            "The module doesn't exist until git says it does.\n\n"
            "Argument ordering matters when using passthrough flags — url and\n"
            "path must come before any git flags you're passing through:\n\n"
            "    archivist add <url> [path] [-- <git-flags>...]\n\n"
            "Flags after the known arguments are forwarded to git as-is.\n"
            + fmt_examples(
                "archivist add git@github.com:user/repo.git",
                "archivist add git@github.com:user/repo.git modules/repo",
                "archivist add git@github.com:user/repo.git modules/repo --depth 1",
                "archivist add git@github.com:user/repo.git --dry-run",
            )
        ),
        formatter_class = ArchivistHelpFormatter,
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
    add_module_p.add_argument(
        "passthrough",
        nargs = argparse.REMAINDER,
        help = "Additional flags forwarded directly to git. Put these last.",
    )
    add_module_p.add_argument(
        "--dry-run",
        action = "store_true",
        help = "Print the git command and registration plan without executing either",
    )

    # -----------------------------------------------------------------------
    # deinit
    # -----------------------------------------------------------------------

    deinit_p = subparsers.add_parser(
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
            "Flags after the known arguments are forwarded to git as-is:\n\n"
            "    archivist deinit <path> [-- <git-flags>...]\n"
            + fmt_examples(
                "archivist deinit modules/repo",
                "archivist deinit modules/repo --retain",
                "archivist deinit modules/repo --dry-run",
            )
        ),
        epilog = fmt_warning(
            "This operation removes a module from the registry and from disk.\n"
            "  Apparatus cleanup runs first. Git cleanup runs second.\n"
            "  There is no undo. Use --dry-run first. You know the drill."
        ),
        formatter_class = ArchivistHelpFormatter,
    )
    deinit_p.add_argument(
        "path",
        help = "Path to the module to remove.",
    )
    deinit_p.add_argument(
        "passthrough",
        nargs = argparse.REMAINDER,
        help = "Additional flags forwarded directly to git submodule deinit. Put these last.",
    )
    deinit_p.add_argument(
        "--retain",
        action = "store_true",
        help = (
            "Registry cleanup only — skip the git operation entirely. "
            "Use when the git step already ran and only the registry needs cleaning."
        ),
    )
    deinit_p.add_argument(
        "--dry-run",
        action = "store_true",
        help = "Preview the Apparatus changes and git command without executing either",
    )


    # -----------------------------------------------------------------------
    # frontmatter
    # -----------------------------------------------------------------------

    fm_parser = subparsers.add_parser(
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
        formatter_class = ArchivistHelpFormatter,
    )
    fm_sub = fm_parser.add_subparsers(dest = "fm_command", metavar = "<subcommand>")
    fm_sub.required = True

    # frontmatter add
    add_p = fm_sub.add_parser(
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
        formatter_class = ArchivistHelpFormatter,
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
    _add_note_selection_args(add_p)
    add_p.add_argument(
        "--dry-run",
        action = "store_true",
        help = "Preview changes without writing to disk"
    )

    # frontmatter remove
    rm_p = fm_sub.add_parser(
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
        formatter_class=ArchivistHelpFormatter,
    )
    rm_p.add_argument(
        "-p",
        "--property",
        required = True,
        metavar = "PROP",
        help = "Property name to remove"
    )
    _add_note_selection_args(rm_p)
    rm_p.add_argument(
        "--dry-run",
        action = "store_true",
        help = "Preview changes without writing to disk"
    )

    # frontmatter rename
    ren_p = fm_sub.add_parser(
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
        formatter_class = ArchivistHelpFormatter,
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
    _add_note_selection_args(ren_p)
    ren_p.add_argument(
        "--dry-run",
        action = "store_true",
        help = "Preview changes without writing to disk"
    )

    # frontmatter apply-template
    tpl_p = fm_sub.add_parser(
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
        formatter_class = ArchivistHelpFormatter,
    )
    tpl_p.add_argument(
        "-t",
        "--template",
        required = True,
        metavar = "FILE",
        help = "Path to the template markdown file"
    )
    _add_note_selection_args(tpl_p, require_one=True)
    tpl_p.add_argument(
        "--dry-run",
        action = "store_true",
        help = "Preview changes without writing to disk"
    )


    # -----------------------------------------------------------------------
    # manifest
    # -----------------------------------------------------------------------

    mf_parser = subparsers.add_parser(
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
        formatter_class = ArchivistHelpFormatter,
    )
    mf_parser.add_argument(
        "edition_dir",
        nargs = "?",
        default = None,
        metavar = "EDITION-DIR",
        help = "Path to the edition directory"
    )
    mf_parser.add_argument(
        "commit_sha",
        nargs = "?",
        default = None,
        metavar = "COMMIT-SHA",
        help = "Diff against a specific commit (default: staged changes)"
    )
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
    mf_parser.add_argument(
        "--dry-run",
        action = "store_true",
        help = "Preview without writing to disk or DB"
    )

    # -----------------------------------------------------------------------
    # changelog
    # -----------------------------------------------------------------------

    cl_parser = subparsers.add_parser(
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
        formatter_class = ArchivistHelpFormatter,
    )
    cl_parser.add_argument(
        "--dry-run",
        action = "store_true",
        help = "Preview without writing to disk"
    )
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
    gen_p = cl_sub.add_parser(
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
        formatter_class = ArchivistHelpFormatter,
    )
    gen_p.add_argument(
        "commit_sha",
        nargs = "?",
        default = None,
        metavar = "COMMIT-SHA",
        help="Diff against a specific commit (default: staged changes)"
    )
    gen_p.add_argument(
        "--path",
        default = None,
        metavar = "PATH",
        help = "File or directory to stage and scope the changelog to"
    )
    gen_p.add_argument(
        "--dry-run",
        action = "store_true",
        help = "Preview without writing to disk"
    )

    # changelog publication
    pub_p = cl_sub.add_parser(
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
        formatter_class = ArchivistHelpFormatter,
    )
    pub_p.add_argument(
        "commit_sha",
        nargs = "?",
        default = None,
        metavar = "COMMIT-SHA",
        help = "Diff against a specific commit (default: staged changes)"
    )
    pub_p.add_argument(
        "--dry-run",
        action = "store_true",
        help = "Preview without writing to disk or DB"
    )

    # changelog story
    story_p = cl_sub.add_parser(
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
        formatter_class = ArchivistHelpFormatter,
    )
    story_p.add_argument(
        "commit_sha",
        nargs = "?",
        default = None,
        metavar = "COMMIT-SHA",
        help = "Diff against a specific commit (default: staged changes)"
    )
    story_p.add_argument(
        "--dry-run",
        action = "store_true",
        help = "Preview without writing to disk"
    )

    # changelog vault
    vault_p = cl_sub.add_parser(
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
        formatter_class = ArchivistHelpFormatter,
    )
    vault_p.add_argument(
        "commit_sha",
        nargs = "?",
        default = None,
        metavar = "COMMIT-SHA",
        help = "Diff against a specific commit (default: staged changes)"
    )
    vault_p.add_argument(
        "--dry-run",
        action = "store_true",
        help = "Preview without writing to disk"
    )

    # changelog library
    lib_p = cl_sub.add_parser(
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
        formatter_class = ArchivistHelpFormatter,
    )
    lib_p.add_argument(
        "commit_sha",
        nargs = "?",
        default = None,
        metavar = "COMMIT-SHA",
        help = "Diff against a specific commit (default: staged changes)"
    )
    lib_p.add_argument(
        "--dry-run",
        action = "store_true",
        help = "Preview without writing to disk"
    )

    # changelog seal
    seal_p = cl_sub.add_parser(
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
        formatter_class = ArchivistHelpFormatter,
    )
    seal_p.add_argument(
        "commit_sha",
        metavar = "COMMIT-SHA",
        help = "Full commit SHA to seal against"
    )

    # -----------------------------------------------------------------------
    # reclassify
    # -----------------------------------------------------------------------
    rc_parser = subparsers.add_parser(
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
        formatter_class = ArchivistHelpFormatter,
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
    _add_note_selection_args(rc_parser)
    rc_parser.add_argument(
        "--dry-run",
        action = "store_true",
        help = "Preview changes without writing to disk"
    )
    

    # -----------------------------------------------------------------------
    # hooks
    # -----------------------------------------------------------------------
    hooks_parser = subparsers.add_parser(
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
        formatter_class = ArchivistHelpFormatter,
    )
    hooks_sub = hooks_parser.add_subparsers(dest="hooks_command", metavar="<subcommand>")
    hooks_sub.required = True

    # hooks install
    hi_p = hooks_sub.add_parser(
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
        formatter_class = ArchivistHelpFormatter,
    )
    hi_p.add_argument(
        "--dry-run",
        action = "store_true",
        help = "Preview without writing any files"
    )

    # hooks sync
    hs_p = hooks_sub.add_parser(
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
        formatter_class = ArchivistHelpFormatter,
    )
    hs_p.add_argument(
        "--dry-run",
        action = "store_true",
        help = "Preview without writing any files"
    )

    # -----------------------------------------------------------------------
    # migrate
    # -----------------------------------------------------------------------

    migrate_p = subparsers.add_parser(
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
        formatter_class = ArchivistHelpFormatter,
    )
    migrate_p.add_argument(
        "--dry-run",
        action = "store_true",
        help = "Preview the migration plan without writing or deleting anything"
    )

    # -----------------------------------------------------------------------
    # _registry-sync (internal — not user-facing)
    # -----------------------------------------------------------------------

    subparsers.add_parser(
        "_registry-sync",
        help = argparse.SUPPRESS,
        description = argparse.SUPPRESS,
    )

    return parser


def main():
    parser = build_parser()
    import argcomplete
    argcomplete.autocomplete(parser)
    args: argparse.Namespace = parser.parse_args()
    _configure_logging(args)

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

    elif args.command == "hooks":
        from archivist.commands.hooks.install import run_install, run_sync
        if args.hooks_command == "install":
            run_install(args)
        elif args.hooks_command == "sync":
            run_sync(args)

    elif args.command == "migrate":
        from archivist.commands.migrate import run
        run(args)

    elif args.command == "add":
        from archivist.commands.add import run
        run(args)
 
    elif args.command == "deinit":
        from archivist.commands.deinit import run
        run(args)
 
    elif args.command == "_registry-sync":
        from archivist.commands.hooks.install import run_registry_sync
        run_registry_sync()