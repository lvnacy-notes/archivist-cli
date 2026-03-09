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
    archivist changelog publication      [commit-sha] [--path <path>] [--dry-run]
    archivist changelog story            [commit-sha] [--path <path>] [--dry-run]
    archivist changelog vault            [commit-sha] [--path <path>] [--dry-run]

    archivist hooks install              [--dry-run]
    archivist hooks sync                 [--dry-run]
"""

import argparse

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
  │                                                                     │
  └─────────────────────────────────────────────────────────────────────┘
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="archivist",
        description=BANNER + "  Bulk-manage YAML frontmatter and generate archive documents.\n  Scopes automatically to the current git repo or submodule root.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    subparsers.required = True

    # -----------------------------------------------------------------------
    # init
    # -----------------------------------------------------------------------
    init_p = subparsers.add_parser(
        "init",
        help="Initialize archivist for this project — writes .archivist and installs hooks",
    )
    init_p.add_argument("--dry-run", action="store_true",
                        help="Preview without writing any files")

    # -----------------------------------------------------------------------
    # frontmatter
    # -----------------------------------------------------------------------
    fm_parser = subparsers.add_parser(
        "frontmatter",
        help="Bulk-manage YAML frontmatter properties across all notes",
    )
    fm_sub = fm_parser.add_subparsers(dest="fm_command", metavar="<subcommand>")
    fm_sub.required = True

    # frontmatter add
    add_p = fm_sub.add_parser("add", help="Add a property to all notes")
    add_p.add_argument("-p", "--property", required=True, metavar="PROP",
                       help="Property name to add")
    add_p.add_argument("-v", "--value", default=None, metavar="VALUE",
                       help="Value to pair with the property (omit for bare key)")
    add_p.add_argument("--overwrite", action="store_true",
                       help="Overwrite the property if it already exists")
    add_p.add_argument("--dry-run", action="store_true",
                       help="Preview changes without writing to disk")

    # frontmatter remove
    rm_p = fm_sub.add_parser("remove", help="Remove a property from all notes")
    rm_p.add_argument("-p", "--property", required=True, metavar="PROP",
                      help="Property name to remove")
    rm_p.add_argument("--dry-run", action="store_true",
                      help="Preview changes without writing to disk")

    # frontmatter rename
    ren_p = fm_sub.add_parser("rename", help="Rename a property across all notes")
    ren_p.add_argument("-p", "--property", required=True, metavar="PROP",
                       help="Current property name")
    ren_p.add_argument("-n", "--new-name", required=True, metavar="NEW",
                       help="New property name")
    ren_p.add_argument("--dry-run", action="store_true",
                       help="Preview changes without writing to disk")

    # frontmatter apply-template
    tpl_p = fm_sub.add_parser(
        "apply-template",
        help="Apply a frontmatter template to all notes of a matching class",
    )
    tpl_p.add_argument("-t", "--template", required=True, metavar="FILE",
                       help="Path to the template markdown file")
    tpl_p.add_argument("-c", "--class", dest="note_class", required=True,
                       metavar="CLASS",
                       help="Class value to match (e.g. 'character', 'location')")
    tpl_p.add_argument("--class-property", default="class", metavar="PROP",
                       help="Frontmatter property used to identify the class (default: class)")
    tpl_p.add_argument("--dry-run", action="store_true",
                       help="Preview changes without writing to disk")

    # -----------------------------------------------------------------------
    # manifest
    # -----------------------------------------------------------------------
    mf_parser = subparsers.add_parser(
        "manifest",
        help="Generate an edition manifest, or register a commit SHA",
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
        help="Generate a changelog (default: general)",
    )
    # --dry-run lives on the parent so bare `archivist changelog --dry-run` works.
    # commit_sha is only on subcommands — use `archivist changelog general <sha>`
    # for SHA diffing without an explicit subcommand.
    cl_parser.add_argument("--dry-run", action="store_true",
                           help="Preview without writing to disk")

    cl_sub = cl_parser.add_subparsers(dest="cl_command", metavar="<subcommand>")
    cl_sub.required = False  # bare `archivist changelog` is valid — routes to general

    # changelog general
    gen_p = cl_sub.add_parser(
        "general",
        help="Generate a generic changelog (same as bare `archivist changelog`)",
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
        help="Generate a project-level changelog for a newsletter/publication vault",
    )
    pub_p.add_argument("commit_sha", nargs="?", default=None, metavar="COMMIT-SHA",
                       help="Diff against a specific commit (default: staged changes)")
    pub_p.add_argument("--path", default=None, metavar="PATH",
                       help="File or directory to stage and scope the changelog to")
    pub_p.add_argument("--dry-run", action="store_true",
                       help="Preview without writing to disk or DB")

    # changelog story
    story_p = cl_sub.add_parser(
        "story",
        help="Generate a session changelog for a story/creative writing vault",
    )
    story_p.add_argument("commit_sha", nargs="?", default=None, metavar="COMMIT-SHA",
                         help="Diff against a specific commit (default: staged changes)")
    story_p.add_argument("--path", default=None, metavar="PATH",
                         help="File or directory to stage and scope the changelog to")
    story_p.add_argument("--dry-run", action="store_true",
                         help="Preview without writing to disk")

    # changelog vault
    vault_p = cl_sub.add_parser(
        "vault",
        help="Generate a vault-level changelog including submodule status",
    )
    vault_p.add_argument("commit_sha", nargs="?", default=None, metavar="COMMIT-SHA",
                         help="Diff against a specific commit (default: staged changes)")
    vault_p.add_argument("--path", default=None, metavar="PATH",
                         help="File or directory to stage and scope the changelog to")
    vault_p.add_argument("--dry-run", action="store_true",
                         help="Preview without writing to disk")

    # -----------------------------------------------------------------------
    # hooks
    # -----------------------------------------------------------------------
    hooks_parser = subparsers.add_parser(
        "hooks",
        help="Install or sync archivist git hooks",
    )
    hooks_sub = hooks_parser.add_subparsers(dest="hooks_command", metavar="<subcommand>")
    hooks_sub.required = True

    # hooks install
    hi_p = hooks_sub.add_parser(
        "install",
        help="Install hooks globally into ~/.git-templates/hooks/",
    )
    hi_p.add_argument("--dry-run", action="store_true",
                      help="Preview without writing any files")

    # hooks sync
    hs_p = hooks_sub.add_parser(
        "sync",
        help="Sync hooks into the current repo's .git/hooks/",
    )
    hs_p.add_argument("--dry-run", action="store_true",
                      help="Preview without writing any files")

    return parser


def main():
    parser = build_parser()
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
        if cl_command in (None, "general"):
            from archivist.commands.changelog.general import run
            if not hasattr(args, "commit_sha"):
                args.commit_sha = None
            if not hasattr(args, "path"):
                args.path = None
        elif cl_command == "publication":
            from archivist.commands.changelog.publication import run
        elif cl_command == "story":
            from archivist.commands.changelog.story import run
        elif cl_command == "vault":
            from archivist.commands.changelog.vault import run
        run(args)

    elif args.command == "hooks":
        from archivist.commands.hooks.install import run_install, run_sync
        if args.hooks_command == "install":
            run_install(args)
        elif args.hooks_command == "sync":
            run_sync(args)