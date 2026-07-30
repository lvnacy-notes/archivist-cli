# ---------------------------------------------------------------------------
# Argparse construction helpers for cli.py
# ---------------------------------------------------------------------------
#
# Reusable wiring for build_parser() — a subparser wrapper and the handful of
# flags/positionals repeated verbatim across multiple subcommands (--dry-run,
# commit_sha, the note-selection group). None of this is business logic and
# none of it is shared by command modules — it exists solely so build_parser()
# isn't repeating the same six lines twenty different times.
#
# Leading underscores are kept on every name even though they're imported into
# cli.py from here. That's deliberate: these are not part of any public
# interface, just an internal split of what used to be one file. Nothing
# outside cli.py should ever import from this module.
#
# This module is a leaf, serving to support the CLI argument parsing. Do not 
# import anything from here into any other module.
# ---------------------------------------------------------------------------

import argparse
import logging
import re

from archivist.formatter import (
    ArchivistFileFormatter,
    ArchivistHelpFormatter,
    ArchivistStreamHandler,
    ArchivistTerminalFormatter,
)


def add_commit_sha_arg(p: argparse.ArgumentParser) -> None:
    """
    Attach the optional commit_sha positional shared verbatim by manifest and
    every changelog subcommand except seal (which takes a REQUIRED sha and
    is rightly left the hell alone).
    """
    p.add_argument(
        "commit_sha",
        nargs = "?",
        default = None,
        metavar = "COMMIT-SHA",
        help = "Diff against a specific commit (default: staged changes)",
    )


def add_dry_run(p: argparse.ArgumentParser, help: str = "Preview without writing to disk") -> None:
    """
    Attach the standard --dry-run flag. Every command that writes files or
    touches the archive DB gets one — no exceptions, per CODE_CONVENTIONS.

    Most subcommands are happy with the default phrasing. A few (add, deinit,
    manifest, publication) have their own more specific help text —
    pass it in, don't fork the flag.
    """
    p.add_argument(
        "--dry-run",
        action = "store_true",
        help = help
    )


def add_note_selection_args(
    p: argparse.ArgumentParser,
    *,
    require_one: bool = False
) -> None:
    """
    Register the four shared note-selection arguments onto a frontmatter subparser.

    --file, --path, --class, --class-property, --tag

    All optional by default. Pass require_one=True for apply-template, which
    demands at least one selection criterion because it absolutely refuses to
    restructure your entire fucking vault on a whim. This only changes the
    --help text the group shows — it does NOT enforce anything by itself.
    The actual enforcement lives in note_filter.py's validate_note_filter(),
    called with require_at_least_one=True from the command's own run().
    Keep the two in sync: if a subcommand's call here doesn't match its
    validate_note_filter() call, --help will lie about what the command
    actually requires.
    """
    scope = p.add_argument_group(
        "note selection",
        (
            "At least one selector is required — --file, --path, --class, or --tag.\n"
            "--file is mutually exclusive with every other selector."
        )
        if require_one
        else (
            "Scope the operation. Omit all flags to target every .md file in the repo.\n"
            "--file is mutually exclusive with every other selector."
        ),
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


def configure_logging(args: argparse.Namespace) -> None:
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
    if getattr(
        args,
        "quiet",
        False
    ): terminal.setLevel(logging.ERROR)
    elif getattr(
        args,
        "verbose",
        False
        ): terminal.setLevel(logging.DEBUG)
    else:
        terminal.setLevel(logging.INFO)
    ledger.addHandler(terminal)

    log_file = getattr(args, "log_file", None)
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(ArchivistFileFormatter())
        ledger.addHandler(file_handler)


def split_passthrough(argv: list[str]) -> tuple[list[str], list[str]]:
    """
    Split raw argv on the first literal '--', BEFORE any of it reaches argparse.

    This exists because `argparse.REMAINDER` is a lying piece of shit when
    combined with a '--' separator and an optional positional (`path`,
    `nargs='?'`). Hand argparse `repo.git -- --name blah` and it "helpfully"
    strips the '--' and lets `path` swallow `--name` as its own value,
    leaving `blah` as the sole survivor in passthrough. Nobody asked for that.

    So: do it ourselves, first, with dumb reliable string splitting. Anything
    after the first '--' is unconditionally passthrough, full stop, no
    argparse involved, no ambiguity, no surprises.

    Returns (archivist_args, forced_passthrough). forced_passthrough is
    empty if no '--' was present — the normal case, where passthrough flags
    (--name, --depth, whatever) are picked up separately via
    parser.parse_known_args() in main() instead.
    """
    if "--" in argv:
        idx = argv.index("--")
        return argv[:idx], argv[idx + 1:]
    return argv, []


def subparser(
    subparsers_obj: argparse._SubParsersAction,
    name: str,
    **kwargs
) -> argparse.ArgumentParser:
    """
    add_parser() wrapper that always sets formatter_class.

    Every single subparser in this file wants ArchivistHelpFormatter — there
    is no case where it doesn't, and there never has been. Repeating that
    kwarg 21 separate times just means 21 separate places to forget it when
    a new subcommand shows up. Now there's exactly one.
    """
    kwargs.setdefault("formatter_class", ArchivistHelpFormatter)
    return subparsers_obj.add_parser(name, **kwargs)


# ---------------------------------------------------------------------------
# add / deinit — git-flag passthrough resolution
# ---------------------------------------------------------------------------
#
# `add` and `deinit` accept arbitrary git flags verbatim, forwarded straight
# to `git submodule add`/`git clone`/`git submodule deinit`. That grammar is:
#
#     <git-options...> [--] <target> [<path>] <archivist-options...>
#
# where <target> is the repository url (add) or the local path (deinit).
# Nothing here tries to know git's own flag arity — that's an arms race
# against every future git release. Instead we recognize the SHAPE of the
# target itself and treat everything before it as git's, whatever it is.
# ---------------------------------------------------------------------------

# The shape of a git repository source: scheme://, scp-like user@host:, or a
# leading ./, ../, /, ~ for local paths. Not a heuristic we invented — it's
# git's OWN disambiguation rule (git itself requires a leading ./ on a local
# path, or it'll misread it as scp-like host syntax). We're just recognizing
# a convention that already exists.
_GIT_SOURCE_RE = re.compile(
    r'^(?:[a-zA-Z][a-zA-Z0-9+.-]*://'   # scheme://
    r'|[^@\s]+@[^:\s]+:'                # user@host: (scp-like)
    r'|\.{1,2}/'                        # ./ or ../
    r'|~'                               # ~ or ~/...
    r'|/)'                              # absolute path
)


def locate_git_target(tokens: list[str]) -> int | None:
    """
    Find the index of the token that IS the git target — the repository url
    for `add`, the local path for `deinit`. Scans for the first token that
    structurally looks like one; falls back to the first non-flag token if
    nothing matches (e.g. no preceding options at all, nothing to
    disambiguate against — and for `deinit`, whose git flags are all
    boolean, this fallback is ALWAYS what fires, correctly, since a plain
    submodule path never matches the url shape). Returns None if there's no
    candidate token at all.
    """
    for i, tok in enumerate(tokens):
        if not tok.startswith("-") and _GIT_SOURCE_RE.match(tok):
            return i
    for i, tok in enumerate(tokens):
        if not tok.startswith("-"):
            return i
    return None


def split_git_passthrough(tokens: list[str]) -> tuple[list[str], list[str]]:
    """
    Split the tokens following `add`/`deinit` into (git_passthrough, remainder).

    An explicit '--' is fully positional and needs no shape detection: git's
    own convention (`[<options>] [--] <repo> [<path>]`) means everything
    before it is git's, unconditionally, and everything after is
    [target, (path), (archivist flags...)] in that fixed order.

    Without '--', shape detection locates the target and everything before
    it — however many tokens, whatever they are — is git's.

    `remainder` gets fed straight into the existing argparse subparser
    (url/path/--dry-run for add; path/--retain/--dry-run for deinit) for
    final, unambiguous parsing of archivist's own trailing flags.
    """
    if "--" in tokens:
        idx = tokens.index("--")
        return tokens[:idx], tokens[idx + 1:]
    idx = locate_git_target(tokens)
    if idx is None:
        return tokens, []
    return tokens[:idx], tokens[idx:]


# ---------------------------------------------------------------------------
# Subcommand detection — lets add/deinit's git-aware parsing kick in
# regardless of where archivist's own global flags land in the command line
# ---------------------------------------------------------------------------
#
# Unlike git's flags, archivist's own global flags are a small, fixed,
# fully-controlled set — there's no arity guessing game here, we wrote them.
_GLOBAL_VALUE_FLAGS = { "--log-file" }
_GLOBAL_BOOL_FLAGS = {
    "-q",
    "--quiet",
    "--verbose",
    "--debug"
}
_HELP_VERSION_FLAGS = {
    "-h",
    "--help",
    "-V",
    "--version"
}
_KNOWN_SUBCOMMANDS = {
    "add",
    "changelog",
    "deinit",
    "frontmatter",
    "hooks",
    "init",
    "manifest",
    "reclassify",
    "sync",
    "_registry-sync",
}


def find_subcommand(argv: list[str]) -> tuple[list[str], str, list[str]] | None:
    """
    Scan argv for the subcommand name, skipping past archivist's own known
    global flags (and their values) along the way — so `--quiet add ...` and
    plain `add ...` both resolve identically, regardless of flag placement.

    Returns (global_prefix, command, rest) if a known subcommand is found
    cleanly. Returns None the moment anything ambiguous shows up first —
    `-h`/`--help`/`-V`/`--version` (which argparse already handles correctly
    and shouldn't be reimplemented), an unrecognized flag, or a token that
    isn't a known subcommand. The caller falls back to handing argv to
    argparse untouched in every one of those cases; nothing here ever
    invents a wrong answer, it just declines to answer.
    """
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in _GLOBAL_VALUE_FLAGS:
            i += 2
            continue
        if tok in _GLOBAL_BOOL_FLAGS:
            i += 1
            continue
        if tok in _HELP_VERSION_FLAGS:
            return None
        if tok.startswith("-"):
            return None
        if tok in _KNOWN_SUBCOMMANDS:
            return argv[:i], tok, argv[i + 1:]
        return None
    return None