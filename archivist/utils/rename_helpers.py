# ---------------------------------------------------------------------------
# Rename helpers (shared by all changelog subcommands)
# ---------------------------------------------------------------------------


import re
from collections.abc import Callable, Sequence
from difflib import SequenceMatcher
from pathlib import Path

from archivist.utils.git import GitChanges


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean_filename(filepath: str) -> str:
    """
    Return just the filename from a path, stripping trailing Obsidian
    conflict-copy suffixes and non-alphanumeric garbage from the stem.
    """
    p = Path(filepath)
    return _scrub_stem(p.stem) + p.suffix


def detect_dir_renames(renames: Sequence[tuple[str, str]]) -> dict[str, str]:
    """
    From file-level rename pairs, infer directory-level renames.
    Returns {old_dir_prefix: new_dir_prefix}.

    Only a source directory is considered "renamed" if ALL files from that
    directory move to the same target directory. Mixed destinations are ignored
    (they represent per-file moves, not a directory-level rename).
    """
    # Track all distinct targets for each source directory
    src_to_targets: dict[str, set[str]] = {}
    for old, new in renames:
        old_parent = str(Path(old).parent)
        new_parent = str(Path(new).parent)
        if old_parent != new_parent:
            src_to_targets.setdefault(old_parent, set()).add(new_parent)

    # Only map src → target when all files from src go to the same target
    return {
        src: next(iter(targets))
        for src, targets in src_to_targets.items()
        if len(targets) == 1
    }


def infer_undetected_renames(changes: GitChanges) -> list[tuple[str, str]]:
    """
    Find D/A pairs with matching filenames that git's -M didn't detect as renames.
    Returns list of (old, new) tuples to be merged into changes["R"].

    Only pairs files where exactly one candidate exists — ambiguous matches
    (same filename added in multiple locations) are left alone.
    """
    already_paired_old = {old for old, _ in changes["R"]}
    already_paired_new = {new for _, new in changes["R"]}

    unpaired_deleted = [f for f in changes["D"] if f not in already_paired_old]
    unpaired_added   = [f for f in changes["A"] if f not in already_paired_new]

    added_by_name: dict[str, list[str]] = {}
    for f in unpaired_added:
        added_by_name.setdefault(Path(f).name, []).append(f)

    return [
        (old, added_by_name[Path(old).name][0])
        for old in unpaired_deleted
        if len(added_by_name.get(Path(old).name, [])) == 1
    ]


def infer_renames_by_content(
    changes: GitChanges,
    content_fetcher: Callable[[str], str | None],
    similarity_threshold: float = 0.7,
) -> list[tuple[str, str]]:
    """
    Find D/A pairs with similar content that didn't match by filename.
    Handles cases where a file was renamed AND moved — filename matching will
    fail, but content similarity can still catch it.

    This function uses aggressive size-based filtering and fast pre-checks
    to avoid catastrophic slowdowns on large files or vaults with many
    unpaired changes. Before running the expensive SequenceMatcher, we:
    - Skip files differing by >50% in size (likely different files entirely)
    - Run a fast byte-signature check on first/last chunks
    Only after these cheap checks pass do we run full SequenceMatcher.

    Args:
        changes: Git changes dict with "D", "A", "R" keys
        content_fetcher: Callable(filepath: str) -> str | None that retrieves
                        file content from git (typically from HEAD for deleted,
                        from working tree or staged index for added)
        similarity_threshold: Minimum similarity ratio (0.0-1.0) to consider
                             a match. Default 0.7 (70% similar).

    Returns:
        List of (old, new) tuples where content similarity >= threshold,
        excluding pairs already in changes["R"].
    """
    already_paired_old = {old for old, _ in changes["R"]}
    already_paired_new = {new for _, new in changes["R"]}

    unpaired_deleted = [f for f in changes["D"] if f not in already_paired_old]
    unpaired_added   = [f for f in changes["A"] if f not in already_paired_new]

    if not unpaired_deleted or not unpaired_added:
        return []

    matches: list[tuple[str, str]] = []
    for old_path in unpaired_deleted:
        old_content = content_fetcher(old_path)
        if old_content is None:
            continue

        old_len = len(old_content)
        best_sim = 0.0
        best_new = None

        for new_path in unpaired_added:
            new_content = content_fetcher(new_path)
            if new_content is None:
                continue

            new_len = len(new_content)

            # Quick reject: if sizes differ by >50%, it's almost certainly
            # a different file. Avoids comparing tiny files to 10MB PDFs.
            size_ratio = new_len / old_len if old_len > 0 else 1.0
            if not (0.5 <= size_ratio <= 2.0):
                continue

            # Fast pre-check: compare signature of first/last 512 chars.
            # If these don't match at all, full SequenceMatcher will fail.
            # This is ~1000x faster for large files and catches ~80% of
            # non-matches before expensive work.
            chunk_size = min(512, max(old_len, new_len) // 10)
            if chunk_size > 0:
                old_sig = old_content[:chunk_size] + old_content[-chunk_size:]
                new_sig = new_content[:chunk_size] + new_content[-chunk_size:]
                sig_ratio = SequenceMatcher(None, old_sig, new_sig).ratio()
                if sig_ratio < similarity_threshold * 0.5:
                    # Signatures don't match well enough; skip expensive check
                    continue

            # Only now run the expensive SequenceMatcher on full content.
            sim = SequenceMatcher(None, old_content, new_content).ratio()
            if sim >= similarity_threshold and sim > best_sim:
                best_sim = sim
                best_new = new_path

        if best_new is not None:
            matches.append((old_path, best_new))
            unpaired_added.remove(best_new)

    return matches


def is_cross_dir_move(old_filepath: str, new_filepath: str) -> bool:
    """
    Return True if the file physically moved between directories — i.e., old
    and new have different parent paths.

    This is the canonical way to distinguish a genuine move from a
    within-directory rename. Use it wherever you'd otherwise be reinventing
    the Path(x).parent != Path(y).parent wheel for the fourth fucking time.

    Same directory → False (it's a rename, the directory is obvious from the
    new path, no need to show both full paths).
    Different directory → True (it moved; show both full paths so the reader
    isn't left guessing which `file.md` out of forty we're talking about).
    """
    return Path(old_filepath).parent != Path(new_filepath).parent


def process_renames_from_changes(changes: GitChanges) -> dict[str, str]:
    """
    Build a {new_path: old_path} rename lookup from a changes dict whose
    ``R`` list has already been fully populated — i.e., after
    ``detect_dir_renames()`` and ``reassign_deletions()`` have run and
    their dir-rename-inferred pairs have been merged in.

    This is the canonical way to convert ``changes["R"]`` into the dict
    that ``format_file_list()`` expects. Don't roll your own dict
    comprehension; call this. Yes, it's one line internally. That's the
    point — one canonical place, not fifteen bespoke ones.
    """
    return {new: old for old, new in changes["R"]}


def reassign_deletions(
    deleted: list[str],
    dir_renames: dict[str, str],
) -> tuple[list[str], list[tuple[str, str]]]:
    """
    Separate true deletions from files that appear deleted only because
    their parent directory was renamed.

    Returns (true_deleted, dir_renamed_files) where dir_renamed_files is
    a list of (old, new) path tuples ready to be merged into changes["R"].
    """
    true_deleted: list[str] = []
    dir_renamed_files: list[tuple[str, str]] = []
    for f in deleted:
        parent = str(Path(f).parent)
        if parent in dir_renames:
            new_path = str(Path(dir_renames[parent]) / Path(f).name)
            dir_renamed_files.append((f, new_path))
        else:
            true_deleted.append(f)
    return true_deleted, dir_renamed_files


def rename_display_path(old: str, new: str) -> str:
    """
    Return the display string for the source side of a rename annotation.

    Same directory → just the cleaned filename, same as clean_filename().
    Different directory → full relative path from git root, because "renamed
    from `note.md`" is completely fucking useless when the file was actually
    in a different subdirectory and the reader has no idea which one.

    Either way, Obsidian conflict-copy garbage is stripped from the stem.
    Obsidian, you know what you did.
    """
    old_p = Path(old)
    new_p = Path(new)
    cleaned_name = _scrub_stem(old_p.stem) + old_p.suffix

    if old_p.parent == new_p.parent:
        return cleaned_name
    return str(old_p.parent / cleaned_name)


def rename_suspicion(old_filepath: str, new_filepath: str) -> str:
    """
    Return a warning string if a rename looks suspicious, else empty string.
    Two independent checks, either or both may fire:

    - Cross-directory: old and new parent directories differ
    - Name mismatch: neither stem is a substring of the other (case-insensitive)

    These are advisory only — the changelog is a draft and the user
    should verify flagged renames before committing.
    """
    old = Path(old_filepath)
    new = Path(new_filepath)

    reasons: list[str] = []

    if old.parent != new.parent:
        reasons.append("cross-directory")

    old_stem = _scrub_stem(old.stem).lower()
    new_stem = _scrub_stem(new.stem).lower()
    if old_stem not in new_stem and new_stem not in old_stem:
        reasons.append("name mismatch")

    if not reasons:
        return ""
    return f" ⚠️ *rename unverified ({', '.join(reasons)}) — double-check*"


def _scrub_stem(stem: str) -> str:
    """
    Strip Obsidian conflict-copy garbage from a filename stem.

    Obsidian suffixes conflict copies with ' 1', ' 2', etc. — a space followed
    by a digit. The digit is alphanumeric, so a naive '[^a-zA-Z0-9]+$' pattern
    stops dead in front of it and strips nothing. This handles both cases:

        (\\s+\\d+)*      — one or more (whitespace + digits) groups, e.g. ' 1', ' 2 3'
        [^a-zA-Z0-9]*$  — any trailing punctuation/symbols after (or instead of) those

    Legitimate stems like 'chapter01' are untouched: the digit is not preceded
    by whitespace, so (\\s+\\d+)* matches zero times, and '1' is alphanumeric so
    [^a-zA-Z0-9]* also matches nothing.
    """
    return re.sub(r'(\s+\d+)*[^a-zA-Z0-9]*$', '', stem)