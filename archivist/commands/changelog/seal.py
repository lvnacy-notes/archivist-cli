"""
archivist changelog seal

Backfill a commit SHA into any unsealed changelogs and manifests included
in the given commit, rename changelogs to mark them as sealed, and update
the archive DB where a UUID is present.

Called automatically by the post-commit hook — you shouldn't need to
run this by hand. But if the hook misfired, a seal got missed, or you're
just the kind of person who needs to touch things manually, here it is.

    archivist changelog seal <full-commit-sha>

What "sealed" means:

  Changelogs — backfilled frontmatter + filename carries the short SHA
  suffix (CHANGELOG-YYYY-MM-DD-{sha}.md). Sealed changelogs are excluded
  from find_active_changelog() and will never be picked up as an existing
  changelog on future runs.

  Manifests — backfilled frontmatter + body table, in place. No rename.
  Manifests don't need the rename lock because find_todays_manifest()
  matches by convention, not by the absence of a SHA suffix.

The post-commit hook handles manifests via bash sed as a belt-and-suspenders
fallback, but this command is the authoritative implementation. If the hook
misfired and left your manifest with a placeholder SHA, run this to fix it.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import cast

from archivist.utils import (
    UNSEALED_RE,
    error,
    extract_frontmatter,
    get_repo_root,
    progress,
    seal_changelog_in_db,
    warning,
)

# A backfilled SHA looks like 7+ hex characters. Empty string, placeholder
# text, or anything else means it hasn't been filled in yet.
_LOOKS_LIKE_A_SHA = re.compile(r"^[0-9a-f]{7,}$")


def _get_committed_files(commit_sha: str, git_root: Path) -> list[str]:
    """
    Return all files touched by the given commit.

    -M is load-bearing here, not decoration. The hook fires on every commit,
    not just the ones you intend to seal — including the follow-up commit
    that sweeps up a previous seal run's rename (per the documented workflow,
    that rename is left unstaged and gets committed standalone or bundled
    with whatever comes next). Without -M, git reports that follow-up commit
    as a plain delete of the old unsealed name + add of the new sealed name,
    and the stale old filename leaks back into changelog_candidates for a
    commit that has nothing to do with that file's actual sealing. With -M,
    git correctly reports it as a rename and the stale name never surfaces
    as a candidate at all. Same convention as get_git_changes() in git.py —
    don't diverge from it here.
    """
    try:
        output = subprocess.check_output(
            [
                "git",
                "-c",
                "core.quotepath=false",
                "diff-tree",
                "--no-commit-id",
                "-r",
                "-M",
                "--name-only",
                commit_sha
            ],
            stderr=subprocess.PIPE, text=True, cwd=git_root,
        )
        return output.strip().splitlines()
    except subprocess.CalledProcessError as e:
        error(f"git diff-tree blew up: {e}")
        sys.exit(1)


def _is_unsealed_changelog(filepath: str) -> bool:
    return UNSEALED_RE.match(Path(filepath).name) is not None


# Manifest filenames always end in -manifest.md. No SHA suffix — manifests
# get backfilled in place and never renamed. The pattern is dead simple;
# resist the urge to make it smarter than it needs to be.
_MANIFEST_RE = re.compile(r"^.+-manifest\.md$")


def _is_manifest(filepath: str) -> bool:
    return _MANIFEST_RE.match(Path(filepath).name) is not None


def _is_already_sealed(content: str) -> bool:
    fm = extract_frontmatter(content)
    sha_val = str(fm.get("commit-sha", "")).strip()
    return bool(_LOOKS_LIKE_A_SHA.match(sha_val))


def _backfill_sha(
    content: str,
    short_sha: str,
    full_sha: str
) -> str:
    """
    Replace empty commit-sha in frontmatter and the placeholder in the body table.

    The regex uses [^\\n]* instead of \\s*.*$ — the latter is a footgun because
    \\s* is greedy and includes newlines, so on a line like `commit-sha: \\n` the
    greedy match crosses the line boundary and consumes the next frontmatter field
    along with it. [^\\n]* stays on its own damn line.
    """
    content = re.sub(
        r"^commit-sha:[^\n]*",
        f'commit-sha: "{short_sha}"',
        content,
        flags=re.MULTILINE,
    )
    content = content.replace(
        "| Commit SHA | [fill in after commit] |",
        f"| Commit SHA | {full_sha} |",
    )
    return content


def run(args: argparse.Namespace) -> None:
    git_root = get_repo_root()

    if not getattr(args, "commit_sha", None):
        error(
            "No commit SHA provided. I'm not clairvoyant. "
            "Usage: archivist changelog seal <full-commit-sha>"
        )
        sys.exit(1)

    full_sha = args.commit_sha.strip()
    short_sha = subprocess.check_output(
        ["git", "rev-parse", "--short", full_sha],
        stderr=subprocess.PIPE, text=True, cwd=git_root,
    ).strip()

    committed_files = _get_committed_files(full_sha, git_root)
    changelog_candidates = [f for f in committed_files if _is_unsealed_changelog(f)]
    manifest_candidates = [f for f in committed_files if _is_manifest(f)]

    if not changelog_candidates and not manifest_candidates:
        progress("  No unsealed changelogs or manifests in this commit. Nothing to do.")
        return

    # ── Changelogs — backfill, rename, update DB ──────────────────────────

    sealed_count  = 0
    skipped_count = 0

    for filepath in changelog_candidates:
        full_path = git_root / filepath
        filename = Path(filepath).name

        if not full_path.exists():
            # This filename is the *unsealed* form. The hook fires on every
            # commit, not just the ones where something actually needs
            # sealing — including the follow-up commit that sweeps up a
            # previous seal run's rename (that rename is intentionally left
            # unstaged; see module docstring). -M above should keep that
            # follow-up commit's diff-tree output clean of this stale name
            # entirely, but don't make this branch depend on -M working
            # perfectly in every git version or every edge case (e.g. someone
            # runs this command by hand on an old commit). Glob for any
            # already-sealed sibling — don't guess one exact filename by
            # gluing today's short_sha onto the stem, since that's exactly
            # what produced the false "deleted" warning in the first place.
            sealed_sibling_re = re.compile(
                rf"^{re.escape(full_path.stem)}-[0-9a-f]{{7,}}\.md$"
            )
            sealed_siblings = [
                p for p in full_path.parent.glob(f"{full_path.stem}-*.md")
                if sealed_sibling_re.match(p.name)
            ]
            if sealed_siblings:
                skipped_count += 1
                continue

            warning(
                f"{ filename } was committed, isn't on disk, and there's no "
                f"sealed version either. Something actually deleted this. "
                f"Go find out what — I'm not psychic. Skipping."
            )
            continue

        content = full_path.read_text(encoding="utf-8")

        if _is_already_sealed(content):
            skipped_count += 1
            continue

        fm = extract_frontmatter(content)
        changelog_uuid: str = cast(str, fm.get("UUID") or fm.get("uuid"))

        new_name = f"{full_path.stem}-{short_sha}.md"
        new_path = full_path.parent / new_name

        try:
            full_path.write_text(_backfill_sha(content, short_sha, full_sha), encoding="utf-8")
            full_path.rename(new_path)
        except OSError as e:
            error(f"Couldn't seal {filename}: {e}")
            continue

        progress(f"  🔒 Sealed: {filename} → {new_name}")
        sealed_count += 1

        if changelog_uuid:
            seal_changelog_in_db(git_root, changelog_uuid, short_sha)
            progress(f"     ↳ DB: {changelog_uuid[:8]}... → {short_sha}")
        else:
            progress(
                f"     ↳ No UUID in frontmatter — changelog predates the system. "
                f"DB untouched."
            )

    if skipped_count:
        progress(f"  {skipped_count} already sealed — left alone.")
    if sealed_count:
        progress(f"\n  ✓ {sealed_count} changelog(s) sealed.")
        progress(f"  Updated file(s) left unstaged — add and commit when ready.")

    # ── Manifests — backfill in place, no rename, no DB ──────────────────
    # Manifests don't get the rename treatment — they're identified by naming
    # convention, not by the absence of a SHA suffix. Backfill and move on.

    manifest_patched_count  = 0
    manifest_skipped_count  = 0

    for filepath in manifest_candidates:
        full_path = git_root / filepath
        filename = Path(filepath).name

        if not full_path.exists():
            # Manifests don't get renamed by us — if it's not where the
            # commit says, it most likely just moved, because reorganizing
            # the vault is a normal thing people do. Search by filename
            # before assuming anything's actually gone.
            relocated = [
                p for p in git_root.rglob(filename)
                if p.is_file() and ".git" not in p.relative_to(git_root).parts
            ]

            if len(relocated) == 1:
                full_path = relocated[0]
                progress(
                    f"  📦 {filename} moved since the commit — found it at "
                    f"{full_path.relative_to(git_root)}. Backfilling there."
                )
            elif len(relocated) > 1:
                warning(
                    f"{filename} isn't where the commit says, and there's "
                    f"more than one file with that name now. Not guessing "
                    f"which one is yours: "
                    f"{', '.join(str(p.relative_to(git_root)) for p in relocated)}"
                )
                continue
            else:
                warning(
                    f"{filename} was in the commit and isn't anywhere in "
                    f"the repo now. That's an actual deletion, not a "
                    f"reorg. Skipping."
                )
                continue

        content = full_path.read_text(encoding="utf-8")

        if _is_already_sealed(content):
            manifest_skipped_count += 1
            continue

        try:
            full_path.write_text(_backfill_sha(content, short_sha, full_sha), encoding="utf-8")
        except OSError as e:
            error(f"Couldn't backfill manifest {filename}: {e}")
            continue

        progress(f"  📋 Backfilled: {filename}")
        manifest_patched_count += 1

    if manifest_skipped_count:
        progress(f"  {manifest_skipped_count} manifest(s) already backfilled — left alone.")
    if manifest_patched_count:
        progress(f"\n  ✓ {manifest_patched_count} manifest(s) backfilled.")
        progress(f"  Updated file(s) left unstaged — add and commit when ready.")