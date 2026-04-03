"""
archivist — Obsidian vault frontmatter and archive management tools.

Usage:
    archivist init

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

    archivist reclassify --from <old-class> --to <new-class> [--path <path>] [--dry-run]

    archivist hooks install              [--dry-run]
    archivist hooks sync                 [--dry-run]
"""

import argparse
import importlib.metadata

from archivist.formatter import (
    ArchivistHelpFormatter,
    fmt_examples,
    fmt_warning,
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
        prog="archivist",
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
        formatter_class=ArchivistHelpFormatter,
    )
    try:
        _version = importlib.metadata.version("archivist")
    except importlib.metadata.PackageNotFoundError:
        _version = "unknown"
    parser.add_argument(
        "--version", "-V",
        action="version",
        version=f"archivist {_version}",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    subparsers.required = True

    # -----------------------------------------------------------------------
    # init
    # -----------------------------------------------------------------------
    init_p = subparsers.add_parser(
        "init",
        help="Initialize archivist for this project",
        description=(
            "Run once per project. Once per machine after cloning.\n\n"
            "No .archivist found: asks what kind of project this is, writes the\n"
            "config, installs the hooks, and leaves you to it. Already configured:\n"
            "shows you what's there and offers to update it. Just don't overthink it.\n"
            + fmt_examples(
                "archivist init",
                "archivist init --dry-run",
            )
        ),
        epilog=fmt_warning(
            "Overwrites any existing git hooks in .git/hooks/ — no backup, no undo.\n"
            "  Check what's in there first: `ls .git/hooks/`\n"
            "  Don't be an idiot. Preserve anything you need before you confirm."
        ),
        formatter_class=ArchivistHelpFormatter,
    )
    init_p.add_argument("--dry-run", action="store_true",
                        help="Preview without writing any files")

    # -----------------------------------------------------------------------
    # frontmatter
    # -----------------------------------------------------------------------
    fm_parser = subparsers.add_parser(
        "frontmatter",
        help="Bulk-manage YAML frontmatter properties across all notes",
        description=(
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
        formatter_class=ArchivistHelpFormatter,
    )
    fm_sub = fm_parser.add_subparsers(dest="fm_command", metavar="<subcommand>")
    fm_sub.required = True

    # frontmatter add
    add_p = fm_sub.add_parser(
        "add",
        help="Add a property to all notes",
        description=(
            "I don't know what to tell you. Add means add, as in this adds a\n"
            "property to every .md file in the repo. But it also creates a\n"
            "frontmatter block if there isn't one. It skips notes that already\n"
            "have the property unless you insist with --overwrite."
            + fmt_examples(
                "archivist frontmatter add -p reviewed",
                "archivist frontmatter add -p status -v draft",
                "archivist frontmatter add -p status -v published --overwrite",
                "archivist frontmatter add -p status -v draft --dry-run",
            )
        ),
        formatter_class=ArchivistHelpFormatter,
    )
    add_p.add_argument("-p", "--property", required=True, metavar="PROP",
                       help="Property name to add")
    add_p.add_argument("-v", "--value", default=None, metavar="VALUE",
                       help="Value to pair with the property (omit for bare key)")
    add_p.add_argument("--overwrite", action="store_true",
                       help="Overwrite the property if it already exists")
    add_p.add_argument("--dry-run", action="store_true",
                       help="Preview changes without writing to disk")

    # frontmatter remove
    rm_p = fm_sub.add_parser(
        "remove",
        help="Remove a property from all notes",
        description=(
            "You are smart enough to use my services, so I trust you to understand\n"
            "what remove means. But just in case: it removes a property and\n"
            "its value from every .md file in the repo. If removal leaves the\n"
            "frontmatter block empty, the block is dropped.\n"
            + fmt_examples(
                "archivist frontmatter remove -p status",
                "archivist frontmatter remove -p status --dry-run",
            )
        ),
        formatter_class=ArchivistHelpFormatter,
    )
    rm_p.add_argument("-p", "--property", required=True, metavar="PROP",
                      help="Property name to remove")
    rm_p.add_argument("--dry-run", action="store_true",
                      help="Preview changes without writing to disk")

    # frontmatter rename
    ren_p = fm_sub.add_parser(
        "rename",
        help="Rename a property across all notes",
        description=(
            "Rename is rename, but with a few caveats. Listen (or, read rather)\n"
            "carefully: this will rename a property across all notes, and it\n"
            "will preserve its value EXACTLY. You will end up with strings\n"
            "in fields that previously contained numbers. So check your\n"
            "fucking work. Handles scalar values, inline lists, and\n"
            "multi-line block sequences.\n"
            + fmt_examples(
                "archivist frontmatter rename -p status -n state",
                "archivist frontmatter rename -p tags -n keywords --dry-run",
            )
        ),
        formatter_class=ArchivistHelpFormatter,
    )
    ren_p.add_argument("-p", "--property", required=True, metavar="PROP",
                       help="Current property name")
    ren_p.add_argument("-n", "--new-name", required=True, metavar="NEW",
                       help="New property name")
    ren_p.add_argument("--dry-run", action="store_true",
                       help="Preview changes without writing to disk")

    # frontmatter apply-template
    tpl_p = fm_sub.add_parser(
        "apply-template",
        help="Apply a frontmatter template to notes matching specified criteria",
        description=(
            "Provide a template note with the properties and structure you want,\n"
            "and provide the criteria for which notes it applies to. I'll handle the rest.\n\n"
            "The template is the authority. The template is the law. You built it.\n\n"
            "Filter by any combination of class, path, and tag — all provided\n"
            "filters must match (AND logic). At least one is required. I am not\n"
            "rewriting your entire fucking vault because you forgot to be specific.\n\n"
            "For each matching note:\n\n"
            "  · Adds properties from the template that the note is missing\n"
            "  · Leaves existing values alone\n"
            "  · Removes properties the template doesn't include\n"
            "  · Reorders everything to match the template\n\n"
            + fmt_examples(
                "archivist frontmatter apply-template -t template.md -c character",
                "archivist frontmatter apply-template -t template.md --path content/essays",
                "archivist frontmatter apply-template -t template.md --tag draft",
                "archivist frontmatter apply-template -t template.md -c article --tag draft --path content/",
                "archivist frontmatter apply-template -t template.md -c location --dry-run",
            )
        ),
        formatter_class=ArchivistHelpFormatter,
    )
    tpl_p.add_argument("-t", "--template", required=True, metavar="FILE",
                       help="Path to the template markdown file")
    tpl_p.add_argument("-c", "--class", dest="note_class", default=None, metavar="CLASS",
                       help="Class value to match (e.g. 'character', 'location')")
    tpl_p.add_argument("--class-property", default="class", metavar="PROP",
                       help="Frontmatter property used to identify the class (default: class)")
    tpl_p.add_argument("--path", default=None, metavar="PATH",
                       help="Limit search to this directory (relative to repo root)")
    tpl_p.add_argument("--tag", default=None, metavar="TAG",
                       help="Match notes that carry this tag in their frontmatter")
    tpl_p.add_argument("--dry-run", action="store_true",
                       help="Preview changes without writing to disk")

    # -----------------------------------------------------------------------
    # manifest
    # -----------------------------------------------------------------------
    mf_parser = subparsers.add_parser(
        "manifest",
        help="Generate an edition manifest, or register a commit SHA",
        description=(
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
        formatter_class=ArchivistHelpFormatter,
    )
    mf_parser.add_argument("edition_dir", nargs="?", default=None,
                            metavar="EDITION-DIR",
                            help="Path to the edition directory")
    mf_parser.add_argument("commit_sha", nargs="?", default=None,
                            metavar="COMMIT-SHA",
                            help="Diff against a specific commit (default: staged changes)")
    mf_parser.add_argument("-v", "--volume", default=None, metavar="NUM",
                            help="Volume number/identifier for the manifest")
    mf_parser.add_argument("--register", metavar="SHA", default=None,
                            help="Register a commit SHA in the archive DB (standalone mode)")
    mf_parser.add_argument("--dry-run", action="store_true",
                            help="Preview without writing to disk or DB")

    # -----------------------------------------------------------------------
    # changelog
    # -----------------------------------------------------------------------
    cl_parser = subparsers.add_parser(
        "changelog",
        help="Generate a changelog (auto-routes by module type if .archivist is present)",
        description=(
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
                "archivist changelog a1b2c3d",
                "archivist changelog --dry-run",
                "archivist changelog --path src/",
                "archivist changelog publication --help",
            )
        ),
        formatter_class=ArchivistHelpFormatter,
    )
    cl_parser.add_argument("--dry-run", action="store_true",
                           help="Preview without writing to disk")
    cl_parser.add_argument("commit_sha", nargs="?", default=None, metavar="COMMIT-SHA",
                       help="Diff against a specific commit (default: staged changes)")
    cl_parser.add_argument("--path", default=None, metavar="PATH",
                        help="File or directory to stage and scope the diff to")

    cl_sub = cl_parser.add_subparsers(dest="cl_command", metavar="<subcommand>")
    cl_sub.required = False

    # changelog general
    gen_p = cl_sub.add_parser(
        "general",
        help="Generic changelog — same as bare `archivist changelog`",
        description=(
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
        formatter_class=ArchivistHelpFormatter,
    )
    gen_p.add_argument("commit_sha", nargs="?", default=None, metavar="COMMIT-SHA",
                       help="Diff against a specific commit (default: staged changes)")
    gen_p.add_argument("--path", default=None, metavar="PATH",
                       help="File or directory to stage and scope the changelog to")
    gen_p.add_argument("--dry-run", action="store_true",
                       help="Preview without writing to disk")

    # changelog publication
    pub_p = cl_sub.add_parser(
        "publication",
        help="Changelog for a newsletter or publication module",
        description=(
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
        formatter_class=ArchivistHelpFormatter,
    )
    pub_p.add_argument("commit_sha", nargs="?", default=None, metavar="COMMIT-SHA",
                       help="Diff against a specific commit (default: staged changes)")
    pub_p.add_argument("--dry-run", action="store_true",
                       help="Preview without writing to disk or DB")

    # changelog story
    story_p = cl_sub.add_parser(
        "story",
        help="Changelog for a story or creative writing module",
        description=(
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
        formatter_class=ArchivistHelpFormatter,
    )
    story_p.add_argument("commit_sha", nargs="?", default=None, metavar="COMMIT-SHA",
                         help="Diff against a specific commit (default: staged changes)")
    story_p.add_argument("--dry-run", action="store_true",
                         help="Preview without writing to disk")

    # changelog vault
    vault_p = cl_sub.add_parser(
        "vault",
        help="Changelog for a vault-level commit, including submodule status",
        description=(
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
        formatter_class=ArchivistHelpFormatter,
    )
    vault_p.add_argument("commit_sha", nargs="?", default=None, metavar="COMMIT-SHA",
                         help="Diff against a specific commit (default: staged changes)")
    vault_p.add_argument("--dry-run", action="store_true",
                         help="Preview without writing to disk")

    # changelog library
    lib_p = cl_sub.add_parser(
        "library",
        help="Changelog for a library or catalog module",
        description=(
            "This generates a changelog for library modules. It tracks works\n"
            "catalogued, authors, publications, and definitions in symmetry.\n"
            + fmt_examples(
                "archivist changelog library",
                "archivist changelog library a1b2c3d",
                "archivist changelog library --dry-run",
            )
        ),
        formatter_class=ArchivistHelpFormatter,
    )
    lib_p.add_argument("commit_sha", nargs="?", default=None, metavar="COMMIT-SHA",
                       help="Diff against a specific commit (default: staged changes)")
    lib_p.add_argument("--dry-run", action="store_true",
                       help="Preview without writing to disk")

    # -----------------------------------------------------------------------
    # reclassify
    # -----------------------------------------------------------------------
    rc_parser = subparsers.add_parser(
        "reclassify",
        help="Replace a frontmatter class value across all matching notes",
        description=(
            "Find every .md file whose frontmatter `class` field matches the\n"
            "given value and rewrite it to a new value. Surgical: only the\n"
            "`class:` line is touched. Everything else in the frontmatter is\n"
            "left exactly where it is.\n\n"
            "Matching is case-insensitive. The --to value is written verbatim.\n"
            "Scope with --path to limit the search to a directory or file.\n"
            + fmt_examples(
                "archivist reclassify --from article --to column",
                "archivist reclassify --from article --to column --path content/",
                "archivist reclassify --from article --to column --dry-run",
            )
        ),
        formatter_class=ArchivistHelpFormatter,
    )
    rc_parser.add_argument("--from", dest="from_class", required=True, metavar="OLD",
                           help="Current class value to match (case-insensitive)")
    rc_parser.add_argument("--to", dest="to_class", required=True, metavar="NEW",
                           help="New class value to write")
    rc_parser.add_argument("--path", default=None, metavar="PATH",
                           help="Limit search to this file or directory")
    rc_parser.add_argument("--dry-run", action="store_true",
                           help="Preview changes without writing to disk")

    # -----------------------------------------------------------------------
    # hooks
    # -----------------------------------------------------------------------
    hooks_parser = subparsers.add_parser(
        "hooks",
        help="Install or sync archivist git hooks",
        description=(
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
        formatter_class=ArchivistHelpFormatter,
    )
    hooks_sub = hooks_parser.add_subparsers(dest="hooks_command", metavar="<subcommand>")
    hooks_sub.required = True

    # hooks install
    hi_p = hooks_sub.add_parser(
        "install",
        help="Install hooks globally into ~/.git-templates/hooks/",
        description=(
            "Write hook scripts into `~/.git-templates/hooks/` and ensure git is\n"
            "configured to use that directory as its template source. All future\n"
            "`git clone` and `git init` operations will automatically include the hooks.\n"
            + fmt_examples(
                "archivist hooks install",
                "archivist hooks install --dry-run",
            )
        ),
        formatter_class=ArchivistHelpFormatter,
    )
    hi_p.add_argument("--dry-run", action="store_true",
                      help="Preview without writing any files")

    # hooks sync
    hs_p = hooks_sub.add_parser(
        "sync",
        help="Sync hooks into the current repo's .git/hooks/",
        description=(
            "Copy hooks directly into the current repo's `.git/hooks/`. Use this\n"
            "for repos that existed before `hooks install` was run."
            + fmt_examples(
                "archivist hooks sync",
                "archivist hooks sync --dry-run",
            )
        ),
        formatter_class=ArchivistHelpFormatter,
    )
    hs_p.add_argument("--dry-run", action="store_true",
                      help="Preview without writing any files")

    return parser


def main():
    parser = build_parser()
    import argcomplete
    argcomplete.autocomplete(parser)
    args = parser.parse_args()

    if args.command == "init":
        from archivist.commands.init import run
        run(args)

    elif args.command == "frontmatter":
        if args.fm_command == "add":
            from archivist.commands.frontmatter.add import run
        elif args.fm_command == "remove":
            from archivist.commands.frontmatter.remove import run
        elif args.fm_command == "rename":
            from archivist.commands.frontmatter.rename import run
        elif args.fm_command == "apply-template":
            from archivist.commands.frontmatter.apply_template import run
        run(args)

    elif args.command == "manifest":
        from archivist.commands.manifest import run
        run(args)

    elif args.command == "changelog":
        cl_command = getattr(args, "cl_command", None)

        # Auto-detect from .archivist when no subcommand was explicitly given
        if cl_command is None:
            from archivist.utils import get_repo_root, get_module_type, MODULE_CHANGELOG_COMMAND
            git_root = get_repo_root()
            module_type = get_module_type(git_root)
            if module_type and module_type in MODULE_CHANGELOG_COMMAND:
                cl_command = MODULE_CHANGELOG_COMMAND[module_type]
                print(f"  → .archivist: module-type '{module_type}' → archivist changelog {cl_command}")
            else:
                cl_command = "general"

        # Normalize attrs that subcommand run() functions expect but
        # aren't present when routing through the bare `changelog` parser
        if not hasattr(args, "commit_sha"):
            args.commit_sha = None
        if not hasattr(args, "path"):
            args.path = None

        if cl_command == "general":
            from archivist.commands.changelog.general import run
        elif cl_command == "publication":
            from archivist.commands.changelog.publication import run
        elif cl_command == "story":
            from archivist.commands.changelog.story import run
        elif cl_command == "vault":
            from archivist.commands.changelog.vault import run
        elif cl_command == "library":
            from archivist.commands.changelog.library import run
        else:
            from archivist.commands.changelog.general import run
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