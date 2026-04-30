"""
archivist.commands.changelog.changelog_base

Shared runner for all changelog subcommands.

Every changelog subcommand follows the same flow: resolve paths, stage
files, get the git diff, process renames, pull existing metadata, build
frontmatter and body, write the file. This module owns that flow.

Subcommands provide the module-specific pieces as callables:

    def run(args: argparse.Namespace) -> None:
        run_changelog(
            args,
            module_type="story",
            build_frontmatter=_build_frontmatter,
            build_body=_build_body,
        )

For modules that need to analyse the diff before building output (library,
vault), supply a `post_changes` hook that receives the context and mutates
`ctx.data` with whatever it needs:

    def run(args: argparse.Namespace) -> None:
        run_changelog(
            args,
            module_type="library",
            build_frontmatter=_build_frontmatter,
            build_body=_build_body,
            post_changes=_analyse_catalog,
        )

All builder callables receive a single `ChangelogContext` and return str.
The `post_changes` hook returns None — side-effects only, into `ctx.data`.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from archivist.utils import (
    GitChanges,
    detect_dir_renames,
    ensure_staged,
    extract_changelog_title,
    extract_descriptions,
    extract_frontmatter,
    extract_user_content,
    find_active_changelog,
    find_changelog_output_dir,
    generate_changelog_uuid,
    get_file_from_git,
    get_git_changes,
    get_project_name,
    get_repo_root,
    get_today,
    infer_renames_by_content,
    infer_undetected_renames,
    is_cross_dir_move,
    print_dry_run_header,
    process_renames_from_changes,
    progress,
    prompt_out_of_scope_changes,
    reassign_deletions,
    report_changes,
    resolve_changelog_title,
    spinner,
    write_changelog,
)


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

@dataclass
class ChangelogContext:
    """
    Everything a changelog builder needs, assembled by run_changelog()
    and passed to each callable.

    `renames` is the full {new_path: old_path} dict for ALL detected renames —
    same-directory and cross-directory alike. Use `moved_files` when you only
    care about files that actually jumped directories (the common case where you
    need to show both full paths instead of just the new filename + a hint).

    `moved_files` is a subset of `renames` containing only cross-directory moves:
    {new_path: old_path} where Path(old).parent != Path(new).parent. Populated
    automatically by run_changelog() so builders don't have to call
    is_cross_dir_move() on every entry themselves.

    `data` is the escape hatch for module-specific state — library stores
    lib_stats and snapshot_block here; vault stores submodule status; etc.
    Builders and the post_changes hook both have full read/write access.
    """
    args: argparse.Namespace
    git_root: Path
    output_dir: Path
    changes: GitChanges                         # raw git changes
    processed_changes: GitChanges               # D and R normalised
    modified: list[str]                         # M + renamed new paths
    true_deleted: list[str]                     # D minus dir-renamed
    renames: dict[str, str]                     # {new_path: old_path} for display — all renames
    moved_files: dict[str, str]                 # {new_path: old_path} — cross-dir moves only
    descriptions: dict[str, str | list[str]]    # preserved from existing changelog
    user_content: str | list[str] | None        # content below sentinel
    changelog_uuid: str
    custom_title: str | None                    # user-set heading title, None if default

    """
    Module-specific data can go here. Use it to pass info from post_changes to
    the builders, or just as scratch space for the builders themselves. As 
    different modules have different needs, this is the catch-all. Anything 
    that goes in here should be cast to the appropriate type by the module 
    that uses it.
    """
    data: dict[str, object] = field(default_factory = dict) # type: ignore


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _wait_for_save_confirmation(existing: Path, git_root: Path) -> None:
    """
    Existing changelog found. Block execution and wait for the user to
    confirm they've saved their edits before we overwrite the file.

    We can't detect whether an editor has a dirty buffer — the check for
    unstaged working-tree changes only sees the last-saved disk version,
    not whatever's still floating in VS Code's memory. So we don't try to
    be clever. We always pause, tell the user to save, and wait for 'y'.

    Anything that isn't 'y' or 'yes' aborts. We're about to overwrite
    their work; "mash enter to skip" is not a sane default here.
    """
    rel = existing.relative_to(git_root) if existing.is_absolute() else existing
    print(f"\n  ⚠️  Existing changelog found: {rel}")
    answer = input("     Save your fucking changes, then press y to continue ... ").strip().lower()
    if answer not in ("y", "yes"):
        print("  Aborted.")
        sys.exit(0)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_changelog(
    args: argparse.Namespace,
    module_type: str,
    build_frontmatter: Callable[[ChangelogContext], str],
    build_body: Callable[[ChangelogContext], str],
    post_changes: Callable[[ChangelogContext], None] | None = None,
    get_extra_paths: Callable[[Path], list[Path] | None] | None = None,
    print_summary: Callable[[ChangelogContext], None] | None = None,
    post_write: Callable[[ChangelogContext], None] | None = None,
) -> None:
    """
    Execute the full changelog generation flow.

    Args:
                     args: Parsed argparse namespace (dry_run, commit_sha, path).
              module_type: Used to resolve the output directory and log-scope.
        build_frontmatter: Callable(ctx) -> frontmatter str.
               build_body: Callable(ctx) -> body str.
             post_changes: Optional hook called after rename processing but
                           before content is built. Receives ctx with changes,
                           processed_changes, modified, true_deleted, renames
                           all populated. Mutate ctx.data with module results.
          get_extra_paths: Optional callable(git_root) -> list[Path] | None.
                           Returns additional paths to stage and include in diff.
            print_summary: Optional callable(ctx) -> None for module-specific
                           summary output. Falls back to a minimal default.
               post_write: Optional hook called after the write (or dry-run
                           block) completes. Receives ctx. Use for side-effects
                           that must happen after the file is written — DB
                           updates, secondary writes, etc. Check ctx.args.dry_run
                           if the hook should behave differently in dry-run mode.
    """
    # Step 1: Resolve paths
    git_root = get_repo_root()
    progress(f"  📁 Repo root : {git_root}")

    output_dir = find_changelog_output_dir(git_root, module_type)
    progress(f"  📁 Output dir: {output_dir}")

    extra_paths = get_extra_paths(git_root) if get_extra_paths else None

    # Step 2: Resolve scope path
    scope_path = (
        Path(args.path).resolve()
        if getattr(args, "path", None)
        else None
    )

    # Step 3: Ensure staging — we check, we don't touch.
    # Auto-staging is gone. If nothing is in the index, that's on you.
    if not args.dry_run:
        ensure_staged(git_root)
        if scope_path is not None:
            prompt_out_of_scope_changes(scope_path, git_root)

    # Step 4: Get git changes.
    # cast() here because get_git_changes returns a plain dict — the TypedDict
    # shape is guaranteed by the git layer, not enforceable at the call site
    # without touching that module. If it ever lies to us, we'll find out at
    # runtime the fun way.
    changes = get_git_changes(
        args.commit_sha,
        scope_path,
        extra_paths = extra_paths if scope_path else None,
    )

    # Step 5: Process renames — three passes, each handling a weaker signal
    # than the last.
    #
    # Pass 0 — git already did this: changes["R"] contains renames git's -M
    #           flag detected (>50% content similarity, same or different dir).
    #
    # Pass 1 — same filename, different directory: git missed these because
    #           similarity threshold wasn't met or content changed too much.
    #           infer_undetected_renames() matches D/A pairs by filename only.
    #
    # Pass 2 — different filename AND directory: the nuclear case. File was
    #           renamed AND moved. infer_renames_by_content() compares actual
    #           file content against HEAD for any remaining unmatched D/A pairs.

    dir_renames = detect_dir_renames(changes["R"])
    true_deleted, dir_renamed_files = reassign_deletions(changes["D"], dir_renames)
    all_renames: list[tuple[str, str]] = changes["R"] + dir_renamed_files

    # Pass 1: same filename, different directory
    inferred_by_name = infer_undetected_renames(
        GitChanges(
            M = changes["M"],
            A = changes["A"],
            D = true_deleted,
            R = all_renames
        )
    )
    inferred_name_old = {old for old, _ in inferred_by_name}
    inferred_name_new = {new for _, new in inferred_by_name}
    all_renames = all_renames + inferred_by_name

    # Pass 2: content similarity on whatever's left unmatched
    already_paired_old = {old for old, _ in all_renames}
    already_paired_new = {new for _, new in all_renames}
    unpaired = GitChanges(
        M=[],
        D=[f for f in true_deleted if f not in already_paired_old],
        A=[f for f in changes["A"] if f not in already_paired_new],
        R=[],
    )

    inferred_by_content: list[tuple[str, str]] = []
    if unpaired["D"] and unpaired["A"]:
        def _fetch_content(path: str) -> str | None:
            # Deleted files: read from HEAD (they're gone from the index).
            # Added files: read from the staging area (they're staged but not
            # committed yet). git show :path uses the colon-prefix notation to
            # address the index directly.
            if path in changes["D"]:
                return get_file_from_git(path, git_root, ref="HEAD")
            try:
                raw = subprocess.check_output(
                    ["git", "show", f":{path}"],
                    stderr = subprocess.PIPE,
                    cwd = git_root,
                )
                return raw.decode("utf-8", errors = "replace")
            except subprocess.CalledProcessError:
                return None
        with spinner(f"Comparing {len(unpaired['D'])} deleted with {len(unpaired['A'])} added files"):
            inferred_by_content = infer_renames_by_content(
                unpaired,
                _fetch_content,
                similarity_threshold=0.7,
            )
        all_renames = all_renames + inferred_by_content

    # All inferred new-side paths are no longer additions — they're renames.
    all_inferred_new = inferred_name_new | {new for _, new in inferred_by_content}
    all_inferred_old = inferred_name_old | {old for old, _ in inferred_by_content}

    # Remove inferred sources from true_deleted
    true_deleted = [f for f in true_deleted if f not in all_inferred_old]

    renames = process_renames_from_changes(GitChanges(
        M = [],
        A = [],
        D = [],
        R = all_renames
    ))
    modified: list[str] = changes["M"] + list(renames.keys())

    # Subset of renames where the file actually crossed directory boundaries.
    # Subcommand builders can use this to decide whether to show both full paths
    # (a move deserves that) or just the old filename (a same-dir rename doesn't).
    moved_files: dict[str, str] = {
        new: old
        for new, old in renames.items()
        if is_cross_dir_move(old, new)
    }

    processed_changes = GitChanges(
        M = changes["M"],
        # Strip inferred rename destinations out of A — they've been promoted
        # to R. Leaving them in both would double-count them in any module
        # that iterates A and R separately (library, publication).
        A = [f for f in changes["A"] if f not in all_inferred_new],
        D = true_deleted,
        R = all_renames,
    )

    report_changes(
        changes,
        modified,
        true_deleted
    )

    # Step 6: Extract existing changelog metadata
    today = get_today()
    output_path = output_dir / f"CHANGELOG-{today}.md"

    existing = find_active_changelog(output_dir)
    descriptions: dict[str, str | list[str]] = {}
    user_content: str | list[str] | None = None
    changelog_uuid: str | None = None
    custom_title: str | None = None

    if existing:
        progress(f"  🔍 Found existing changelog: {existing.name} — updating in place")
        if not args.dry_run:
            _wait_for_save_confirmation(existing, git_root)
        existing_text = existing.read_text()
        existing_fm = extract_frontmatter(existing_text)
        changelog_uuid: str | None = cast(str, existing_fm.get("UUID") or existing_fm.get("uuid"))
        descriptions = extract_descriptions(existing_text)
        user_content = extract_user_content(existing_text)
        custom_title = extract_changelog_title(existing_text)
        output_path = existing
    else:
        progress(f"  🆕 No existing changelog found — creating {output_path.name}")

    if not changelog_uuid:
        changelog_uuid = generate_changelog_uuid()

    # Step 7: Assemble context
    ctx = ChangelogContext(
        args = args,
        git_root = git_root,
        output_dir = output_dir,
        changes = changes,
        processed_changes = processed_changes,
        modified = modified,
        true_deleted = true_deleted,
        renames = renames,
        moved_files = moved_files,
        descriptions = descriptions,
        user_content = user_content,
        changelog_uuid = changelog_uuid,
        custom_title = custom_title,
    )

    # Step 8: Module-specific post-changes analysis
    if post_changes:
        post_changes(ctx)

    # Step 9: Build content
    frontmatter = build_frontmatter(ctx)
    body = build_body(ctx)
    changelog_content = frontmatter + body

    # Step 10: Write or dry-run
    if args.dry_run:
        print_dry_run_header()
        print()
        print(changelog_content)
        print(f"\n=== Would write to: {output_path} ===")
    else:
        write_changelog(output_path, changelog_content, existing=bool(existing))

    # Step 11: Post-write hook
    if post_write:
        post_write(ctx)

    # Step 12: Summary
    if print_summary:
        print_summary(ctx)
    else:
        _default_summary(ctx)


def _default_summary(ctx: ChangelogContext) -> None:
    print(f"  Project  : {get_project_name(ctx.git_root)}")
    print(
        f"  Changes  : {len(ctx.changes['A'])} added, "
        f"{len(ctx.modified)} modified, {len(ctx.true_deleted)} archived"
    )
    if ctx.args.commit_sha:
        print(f"  SHA      : {ctx.args.commit_sha}")
    else:
        print("  SHA      : (staged — backfilled by post-commit hook)")