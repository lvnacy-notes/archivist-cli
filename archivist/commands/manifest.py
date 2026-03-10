"""
archivist manifest

Generate a {edition-name}-manifest.md for a specified edition directory,
written to the edition's parent directory. Or, in --register mode, register
a commit SHA in the archive DB (no edition directory required).
"""

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from archivist.utils import (
    ensure_staged,
    extract_frontmatter,
    get_db_path,
    get_file_class,
    get_file_frontmatter,
    get_repo_root,
    init_db,
)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _get_git_changes(scope_path: Path, commit_sha: str | None, git_root: Path) -> dict:
    """
    Get file changes from git, scoped to scope_path.
    Uses diff-tree for a committed SHA, or diff-index --cached for staged changes.
    """
    try:
        rel_scope = scope_path.relative_to(git_root)
    except ValueError:
        print(
            f"Error: Edition path '{scope_path}' is not inside the git repo at '{git_root}'.",
            file=sys.stderr,
        )
        sys.exit(1)

    if commit_sha:
        cmd = ["git", "-c", "core.quotepath=false", "diff-tree",
               "--name-status", "-M", "-r", commit_sha, "--", str(rel_scope)]
    else:
        cmd = ["git", "-c", "core.quotepath=false", "diff-index",
               "--cached", "--name-status", "-M", "HEAD", "--", str(rel_scope)]

    try:
        output = subprocess.check_output(cmd, stderr=subprocess.PIPE, text=True)
    except subprocess.CalledProcessError as e:
        print(f"Error running git command: {e}", file=sys.stderr)
        sys.exit(1)

    changes = {"M": [], "A": [], "D": [], "R": []}
    for line in output.strip().splitlines():
        if not line:
            continue
        parts = line.split("\t")
        status = parts[0].strip()[0]
        if status == "R" and len(parts) == 3:
            changes["R"].append((parts[1].strip(), parts[2].strip()))
        elif status in changes:
            changes[status].append(parts[-1].strip())

    return changes


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------

def _find_manifest_template(git_root: Path) -> Path:
    archive_root = git_root / "ARCHIVE"
    if not archive_root.is_dir():
        print(f"Error: No ARCHIVE/ directory found at repo root ({git_root}).", file=sys.stderr)
        sys.exit(1)

    matches = list(archive_root.rglob("MANIFEST_TEMPLATE.md"))
    if not matches:
        print(f"Error: MANIFEST_TEMPLATE.md not found anywhere under {archive_root}.", file=sys.stderr)
        sys.exit(1)

    if len(matches) > 1:
        matches.sort(key=lambda p: len(p.parts))
        print(f"Warning: Multiple MANIFEST_TEMPLATE.md found; using {matches[0]}", file=sys.stderr)

    return matches[0]


# ---------------------------------------------------------------------------
# Edition directory scanner
# ---------------------------------------------------------------------------

def _scan_edition_files(
    edition_path: Path,
) -> tuple[int, list[Path], list[Path], str | None]:
    """
    Walk the edition directory and return:
        (total_file_count, article_files, edition_files, publish_date)
    """
    all_files = [
        p for p in edition_path.rglob("*")
        if p.is_file() and not p.name.endswith("-manifest.md")
    ]

    article_files = []
    edition_files = []

    for f in all_files:
        cls = get_file_class(f)
        if cls == "article":
            article_files.append(f)
        elif cls == "edition":
            edition_files.append(f)

    publish_date = None
    if edition_files:
        if len(edition_files) > 1:
            print(
                f"Warning: Multiple class: edition files found; "
                f"using publish-date from {edition_files[0].name}",
                file=sys.stderr,
            )
        fm = get_file_frontmatter(edition_files[0])
        if fm:
            raw = fm.get("publish-date")
            publish_date = str(raw) if raw is not None else None

    return len(all_files), article_files, edition_files, publish_date


# ---------------------------------------------------------------------------
# Frontmatter builder
# ---------------------------------------------------------------------------

def _edition_wikilink(edition_name: str) -> str:
    return "[[" + edition_name.replace("-", " ") + "]]"


def _build_manifest_frontmatter(
    template_fm: dict,
    edition_name: str,
    commit_sha: str | None,
    num_modified: int,
    num_added: int,
    num_archived: int,
    num_articles: int,
    num_editions: int,
    num_assets: int,
    volume: str | None,
    publish_date: str | None,
) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    wikilink = f'"{_edition_wikilink(edition_name)}"'

    auto = {
        "class":              "archive",
        "category":           ["manifest", "edition"],
        "modified":           today,
        "updated":            today,
        "log-scope":          "edition",
        "edition":            wikilink,
        "commit-sha":         commit_sha or "",
        "volume":             volume or "",
        "publish-date":       publish_date or "",
        "articles-published": num_articles + num_editions,
        "assets-included":    num_assets,
        "files-modified":     num_modified,
        "files-created":      num_added,
        "files-archived":     num_archived,
    }

    def get_value(key):
        if key in auto:
            return auto[key]
        val = template_fm.get(key)
        return val if val is not None else ""

    def render_field(key, value):
        if isinstance(value, list):
            return [f"{key}:"] + [f"  - {item}" for item in value]
        return [f"{key}: {value}"]

    lines = ["---"]
    for key in template_fm.keys():
        lines.extend(render_field(key, get_value(key)))
    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Manifest body builder
# ---------------------------------------------------------------------------

def _clean_filename(filepath: str) -> str:
    p = Path(filepath)
    stem = re.sub(r'[^a-zA-Z0-9]+$', '', p.stem)
    return stem + p.suffix


def _build_manifest_body(
    edition_name: str,
    changes: dict,
    commit_sha: str | None,
    git_root: Path,
    num_assets: int,
    num_articles: int,
    num_editions: int,
    volume: str | None,
    publish_date: str | None,
) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    wikilink = _edition_wikilink(edition_name)
    num_published = num_articles + num_editions

    def file_list(files, fallback):
        if not files:
            return f"- {fallback}\n"
        return "".join(f"- `{_clean_filename(f)}`: [description]\n" for f in files)

    def rename_list(renames, fallback):
        if not renames:
            return f"- {fallback}\n"
        return "".join(
            f"- `{_clean_filename(old)}` → `{_clean_filename(new)}`\n"
            for old, new in renames
        )

    def classify(filepath):
        return get_file_class(git_root / filepath) or "asset"

    new_articles  = [f for f in changes["A"] if classify(f) == "article"]
    new_editions  = [f for f in changes["A"] if classify(f) == "edition"]
    new_assets    = [f for f in changes["A"] if classify(f) == "asset"]
    mod_articles  = [f for f in changes["M"] if classify(f) == "article"]
    mod_editions  = [f for f in changes["M"] if classify(f) == "edition"]
    mod_assets    = [f for f in changes["M"] if classify(f) == "asset"]
    moved         = changes["R"]

    volume_row   = f"| Volume | {volume} |" if volume else "| Volume | [fill in] |"
    pub_date_row = f"| Publish Date | {publish_date} |" if publish_date else "| Publish Date | [fill in] |"

    return f"""

# Manifest — {wikilink}

*Generated {today}*

## Edition Overview

| Field | Value |
|-------|-------|
| Edition | {wikilink} |
{volume_row}
{pub_date_row}
| Commit SHA | {commit_sha or "[fill in after commit]"} |
| Articles Published | {num_published} |
| Assets Included | {num_assets} |

## Content

### Articles
{file_list(new_articles, "No new articles")}
### Edition Files
{file_list(new_editions, "No new edition files")}

## Modified Content

### Articles
{file_list(mod_articles, "No articles modified")}
### Edition Files
{file_list(mod_editions, "No edition files modified")}

## Assets & Supporting Files

### New Assets
{file_list(new_assets, "No new assets")}
### Modified Assets
{file_list(mod_assets, "No assets modified")}

### Removed / Archived
{file_list(changes["D"], "No files archived")}

### Moved
{rename_list(moved, "No files moved")}

## Content Checklist

- [ ] All articles proofread
- [ ] Images / assets included
- [ ] Metadata frontmatter complete on each piece
- [ ] Edition dashboard updated
- [ ] Social media copy drafted
- [ ] Archive entry created

## Notes

[Any edition-specific notes, decisions, or context go here.]

---

*Manifest auto-generated by archivist manifest — fill in bracketed fields before committing.*
"""


# ---------------------------------------------------------------------------
# Archive DB helpers
# ---------------------------------------------------------------------------

def _verify_sha(sha: str) -> bool:
    try:
        result = subprocess.check_output(
            ["git", "cat-file", "-t", sha],
            stderr=subprocess.PIPE, text=True,
        ).strip()
        return result == "commit"
    except subprocess.CalledProcessError:
        return False


def _get_commit_message(sha: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "log", "--format=%s", "-n", "1", sha],
            stderr=subprocess.PIPE, text=True,
        ).strip()
    except subprocess.CalledProcessError:
        return ""


def _register_sha(git_root: Path, sha: str, manifest_file: str) -> str:
    if not sha or not sha.strip():
        return "no_sha"
    if not _verify_sha(sha):
        return "invalid_sha"

    commit_message = _get_commit_message(sha)
    db_path = get_db_path(git_root)
    conn = init_db(db_path)

    try:
        row = conn.execute(
            "SELECT included_in FROM edition_shas WHERE sha = ?", (sha,)
        ).fetchone()

        if row is None:
            conn.execute(
                """INSERT INTO edition_shas
                   (sha, commit_message, manifest_file, discovered_at, included_in)
                   VALUES (?, ?, ?, ?, NULL)""",
                (sha, commit_message, manifest_file, datetime.now().strftime("%Y-%m-%d")),
            )
            conn.commit()
            return "inserted"
        elif row[0]:
            return "already_included"
        else:
            return "already_registered"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    git_root = get_repo_root()

    # --- Register-only mode ---
    if args.register:
        if args.dry_run:
            if _verify_sha(args.register):
                msg = _get_commit_message(args.register)
                print(f"DRY RUN: would register SHA '{args.register}' — {msg}")
            else:
                print(f"DRY RUN: SHA '{args.register}' is invalid — nothing would be registered")
            return

        status = _register_sha(git_root, args.register, "[manual registration]")
        messages = {
            "inserted":           f"✓ Registered '{args.register}' — {_get_commit_message(args.register)}",
            "already_registered": f"  '{args.register}' already in DB (not yet included in a changelog)",
            "already_included":   f"  '{args.register}' already claimed by a changelog — skipping",
            "invalid_sha":        f"✗ '{args.register}' is not a valid commit SHA in this repo",
            "no_sha":             "  No SHA provided",
        }
        print(messages.get(status, status))
        return

    # --- Manifest generation mode ---
    if not args.edition_dir:
        print("❌  edition_dir is required when not using --register", file=sys.stderr)
        sys.exit(1)

    edition_path = Path(args.edition_dir).resolve()

    if not edition_path.exists():
        print(f"Error: Edition directory not found: '{args.edition_dir}'", file=sys.stderr)
        sys.exit(1)
    if not edition_path.is_dir():
        print(f"Error: '{args.edition_dir}' is not a directory.", file=sys.stderr)
        sys.exit(1)

    edition_name = edition_path.name
    parent_dir = edition_path.parent

    template_path = _find_manifest_template(git_root)
    template_fm = extract_frontmatter(template_path.read_text())

    num_assets, article_files, edition_files, publish_date = _scan_edition_files(edition_path)
    num_articles = len(article_files)
    num_editions = len(edition_files)

    # Ensure edition files are staged before diffing
    if not args.dry_run:
        ensure_staged(edition_path, git_root)

    changes = _get_git_changes(edition_path, args.commit_sha, git_root)
    num_modified = len(changes["M"])
    num_added = len(changes["A"])
    num_archived = len(changes["D"])

    frontmatter = _build_manifest_frontmatter(
        template_fm, edition_name, args.commit_sha,
        num_modified, num_added, num_archived,
        num_articles, num_editions, num_assets,
        args.volume, publish_date,
    )
    body = _build_manifest_body(
        edition_name, changes, args.commit_sha,
        git_root, num_assets,
        num_articles, num_editions,
        args.volume, publish_date,
    )
    manifest_content = frontmatter + body
    output_path = parent_dir / f"{edition_name}-manifest.md"

    if args.dry_run:
        print("=== DRY RUN — no file written ===\n")
        print(manifest_content)
        print(f"\n=== Would write to: {output_path} ===")
    else:
        output_path.write_text(manifest_content)
        print(f"✓ Manifest written to: {output_path}")

    print(f"  Edition  : {_edition_wikilink(edition_name)}")
    print(f"  Scoped   : {edition_path}")
    print(f"  Articles : {num_articles} (class: article) + {num_editions} (class: edition) = {num_articles + num_editions} published")
    print(f"  Assets   : {num_assets} total files in edition dir")
    print(f"  Changes  : {num_added} added, {num_modified} modified, {num_archived} archived")
    if args.volume:
        print(f"  Volume   : {args.volume}")
    if publish_date:
        print(f"  Pub date : {publish_date}")
    if args.commit_sha:
        print(f"  SHA      : {args.commit_sha}")
    else:
        print("  SHA      : (staged changes — run after your commit to lock it in)")