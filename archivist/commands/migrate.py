"""
archivist migrate

One-shot migration from the legacy flat `.archivist` config file to the
`.archivist/` directory form.

What it does:
  1. Reads the existing flat `.archivist` file
  2. Migrates the `apparatus` field:
       - `true`/`false` (bool or quoted string) are placeholder values from
         before the Apparatus Platform. `true` becomes a real apparatus name
         (prompted); `false` removes the key entirely.
       - A real string value (e.g. `apparatus: "writing"`) is the v2 single-
         apparatus form. It becomes `apparati: ["writing"]` — same name, new
         key, wrapped in a list for the v3 multi-apparatus model.
  3. Adds a UUID if absent — the registry requires one.
  4. Creates `.archivist/` directory
  5. Writes the config to `.archivist/config.yaml` with UUID as the first field
  6. Registers the module in the Apparatus registry if one is configured
  7. Copies `sample-changelog.py` from the package bundle if module-type is library
  8. Deletes the flat `.archivist` file
  9. Stages both sides of the migration
  10. Offers to sync git hooks locally so they recognise the new config form

What it does NOT do:
  - Touch any other files in the repo
  - Modify config values that are already correct — if `apparatus` already
    holds a proper name, it's left alone.
  - Run if `.archivist/config.yaml` already exists — the job is already done.
  - Run if there's no `.archivist` flat file to migrate from.
  - Touch global hook templates — that's `archivist hooks install`'s job.

This command is intentionally narrow. It does one thing, it tells you exactly
what it did, and it gets out of your way. Run it once per project. Then never
again.
"""

import argparse
import importlib.resources
import subprocess
import sys
import uuid as _uuid_module
from pathlib import Path

from archivist.utils import (
    ConfigSchema,
    add_module_to_apparatus,
    error,
    get_git_remotes,
    get_project_name,
    get_registry_path,
    get_repo_root,
    progress,
    prompt_apparatus_names,
    read_archivist_config,
    register_apparatus,
    register_module,
    success,
    warning,
    write_archivist_config,
)


def _get_legacy_path(git_root: Path) -> Path:
    return git_root / ".archivist"


def _get_config_yaml_path(git_root: Path) -> Path:
    return git_root / ".archivist" / "config.yaml"


def _copy_sample_changelog(git_root: Path, dry_run: bool) -> None:
    """
    Copy the bundled sample-changelog.py into .archivist/ if the project
    is a library module and the file isn't already there.

    Non-fatal on read failure — the migration succeeds without it, and the
    user can always grab it manually from the Archivist repo.
    """
    dest = git_root / ".archivist" / "sample-changelog.py"

    if dest.exists():
        progress(f"  sample-changelog.py already present — leaving it alone.")
        return

    try:
        ref = importlib.resources.files("archivist.data").joinpath("sample-changelog.py")
        content = ref.read_text(encoding="utf-8")
    except Exception as e:
        warning(
            f"  Couldn't read bundled sample-changelog.py: {e}\n"
            "     Migration will complete without it. Grab it from the Archivist\n"
            "     repo if you need the plugin reference."
        )
        return

    if dry_run:
        progress(f"  [dry-run] Would write: .archivist/sample-changelog.py")
        return

    dest.write_text(content, encoding="utf-8")
    success(f"  Written: .archivist/sample-changelog.py")


def _sync_hooks_local(git_root: Path, dry_run: bool) -> None:
    """
    Sync hooks into this repo only.

    The old hooks used `[ -f .archivist ]` which is false for a directory,
    so every post-migration commit would run with broken hooks until the user
    remembered to sync manually. Offering this here closes that gap without
    making it mandatory or touching global templates.

    Non-fatal — a hook sync failure is annoying but doesn't invalidate the
    config migration. Warn loudly and let the user finish by hand if needed.
    """
    from archivist.commands.hooks.install import install_hooks_local
    try:
        install_hooks_local(git_root, dry_run=dry_run)
    except Exception as e:
        warning(
            f"Hook sync failed — migration succeeded but hooks are stale.\n"
            f"     Run `archivist hooks sync` to fix it.\n"
            f"     ({e})"
        )


def _resolve_git_remote(git_root: Path, config: "ConfigSchema") -> tuple[str | None, str | None]:
    """
    Determine which git remote URL and name to register with the module.

    Resolution order:
      1. If the existing config already has `git-remote`, use it — the user
         set it intentionally and we're not here to second-guess them.
      2. Otherwise, ask git what remotes exist:
         - None found: shrug, return (None, None), continue.
         - Exactly one: use it automatically and say so.
         - Multiple: show the names, make the user pick one, grab its URL.

    Returns (remote_url, remote_name). Both None if nothing was found or
    the user had nothing to pick from. The caller decides what to do with that.
    """
    # Existing config value wins — don't clobber an explicit setting.
    if config.get("git-remote"):
        return str(config["git-remote"]), config.get("git-remote-name")  # type: ignore[return-value]

    remotes = get_git_remotes(git_root)

    if not remotes:
        progress("  No git remotes configured — skipping remote registration.")
        return None, None

    if len(remotes) == 1:
        name, url = next(iter(remotes.items()))
        progress(f"  Remote detected: {name} → {url}")
        return url, name

    # Multiple remotes — the user needs to pick one. We're not psychic.
    print(f"\n  Multiple git remotes found. Pick one to register with this module:")
    names = list(remotes.keys())
    for i, name in enumerate(names, 1):
        print(f"    [{i}] {name}  ({remotes[name]})")
    print(f"    [0] None — skip remote registration")

    while True:
        raw = input("  Choice: ").strip()
        if raw == "0":
            progress("  Skipping remote registration.")
            return None, None
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(names):
                chosen_name = names[idx]
                return remotes[chosen_name], chosen_name
        except ValueError:
            pass
        print(f"  That's not a valid choice. Pick a number between 0 and {len(names)}.")


def run(args: argparse.Namespace) -> None:
    git_root = get_repo_root()
    dry_run: bool = getattr(
        args,
        "dry_run",
        False
    )

    legacy_path = _get_legacy_path(git_root)
    config_yaml_path = _get_config_yaml_path(git_root)

    progress(f"\n  📁 Repo root: {git_root}")

    # --- Guard: already migrated ---
    if config_yaml_path.exists():
        progress(
            "  .archivist/config.yaml already exists. "
            "Nothing to migrate — you're already on the directory form."
        )
        sys.exit(0)

    # --- Guard: nothing to migrate from ---
    if not legacy_path.exists() or legacy_path.is_dir():
        error(
            "   No flat .archivist file found. Nothing to migrate.\n"
            "   If you're starting fresh, run `archivist init` instead."
        )
        sys.exit(1)

    # --- Read the existing config ---
    config: ConfigSchema | None = read_archivist_config(git_root)
    if config is None:
        # Should be unreachable given the guard above, but be explicit.
        error(
            "Found .archivist but couldn't read it. "
            "Check the file for YAML errors before retrying."
        )
        sys.exit(1)

    module_type = config.get("module-type") if config else None

    # --- Apparatus field migration ---
    # YAML parses `apparatus: true` (no quotes) as Python bool True.
    # YAML parses `apparatus: "true"` (quoted) as Python str "true".
    # Both are placeholder values from before the Apparatus Platform.
    # Both need the same treatment: replace with a real name.
    apparatus_raw = config.get("apparatus")
    apparatus_names: list[str] | None = None  # set when true case: new name(s) to write
    remove_apparatus: bool = False       # set when false case: drop the key

    if isinstance(apparatus_raw, bool):
        if apparatus_raw:
            progress(
                "\n  The `apparatus` field holds a placeholder value ('true') from"
                "\n  before the Apparatus Platform. It needs a real name now."
            )
            apparatus_names = prompt_apparatus_names()
        else:
            remove_apparatus = True
    elif isinstance(apparatus_raw, str):
        if apparatus_raw.lower() == "true":
            progress(
                "\n  The `apparatus` field holds a placeholder value ('true') from"
                "\n  before the Apparatus Platform. It needs a real name now."
            )
            apparatus_names = prompt_apparatus_names()
        elif apparatus_raw.lower() == "false":
            remove_apparatus = True
        # else: real name — v2 single-apparatus string form.
        # No prompt needed; final_config building migrates it to apparati: [name].

    # --- Preview ---
    has_uuid = bool(config.get("uuid"))
    progress(f"\n  Migration plan:")
    progress(f"    Read   : .archivist  (flat file)")
    progress(f"    Create : .archivist/ (directory)")
    progress(f"    Write  : .archivist/config.yaml")
    if module_type == "library":
        progress(f"    Write  : .archivist/sample-changelog.py  (if not present)")
    progress(f"    Delete : .archivist  (flat file)")
    progress(f"    Stage  : .archivist/ (new) + .archivist deletion")

    apparati_repr = ", ".join(repr(n) for n in apparatus_names) if apparatus_names else ""

    if apparatus_names:
        progress(f"\n  Config change: apparatus: {apparatus_raw!r} → apparati: [{apparati_repr}]")
    elif remove_apparatus:
        progress(f"\n  Config change: apparatus: {apparatus_raw!r} → (removed)")
    elif apparatus_raw and not isinstance(apparatus_raw, bool) and str(apparatus_raw).lower() not in ("true", "false"):
        progress(f"\n  Config change: apparatus: {apparatus_raw!r} → apparati: [{apparatus_raw!r}]")
    if not has_uuid:
        progress("  Config change: uuid (absent) → uuid: <generated>")

    progress(f"\n  Config content (as written):")
    # Simulate the final field order so the preview matches reality
    if not has_uuid:
        progress(f"    uuid: <will be generated>")
    for k, v in config.items():
        if k == "uuid":
            progress(f"    {k}: {v}")
        elif k == "apparatus":
            if apparatus_names:
                progress(f"    apparati: [{apparati_repr}]")
            elif remove_apparatus:
                pass  # key will not appear
            else:
                progress(f"    apparati: [{v!r}]")
        else:
            progress(f"    {k}: {v}")

    if dry_run:
        progress("\n  [dry-run] No files written, deleted, or staged.")
        return

    # --- Confirm ---
    answer = input(
        "\n  This will delete the flat .archivist file. "
        "It's not recoverable unless you're in git. Proceed? [y/N] "
    ).strip().lower()
    if answer not in ("y", "yes"):
        progress("  Aborted.")
        sys.exit(0)

    # --- Execute ---

    # 1. Delete flat file first — write_archivist_config would evict it anyway,
    #    but doing it explicitly here gives us the progress message and keeps
    #    the order legible.
    legacy_path.unlink()
    progress(f"  🗑   Deleted: .archivist (flat file)")

    # 2. UUID resolution + optional Apparatus registration.
    #    For apparatus modules where a registry exists: register_module() is
    #    the source of truth for the UUID. For everything else: preserve the
    #    existing UUID or generate a fresh one.
    effective_apparati: list[str] = apparatus_names or (
        [str(apparatus_raw)] if apparatus_raw and not remove_apparatus
        and not isinstance(apparatus_raw, bool)
        and str(apparatus_raw).lower() not in ("true", "false")
        else []
    )

    module_uuid: str
    if effective_apparati and get_registry_path().exists():
        resolved_remote_url, resolved_remote_name = _resolve_git_remote(git_root, config)
        primary_apparatus, *extra_apparati = effective_apparati
        register_apparatus(primary_apparatus, git_remote = None)
        module_uuid = register_module(
            apparatus_name = primary_apparatus,
            name = get_project_name(git_root),
            module_type = str(module_type or "general"),
            path = git_root,
            git_remote = resolved_remote_url,
            git_remote_name = resolved_remote_name,
        )
        # As ever: register_module only associates the FIRST apparatus on
        # creation. Anything else picked at the prompt needs its own
        # membership row.
        for extra_name in extra_apparati:
            extra_uuid = register_apparatus(extra_name, git_remote = None)
            add_module_to_apparatus(module_uuid, extra_uuid)
        names_str = ", ".join(f"'{n}'" for n in effective_apparati)
        progress(f"  Registered in Apparat{'us' if len(effective_apparati) == 1 else 'i'} {names_str}.")
    else:
        module_uuid = str(config.get("uuid") or _uuid_module.uuid4())

    # 3. Build the final config: uuid first, then all other fields with
    #    apparatus value migrated. The UUID-first requirement means we can't
    #    just pass the original config dict — build explicitly.
    final_config: ConfigSchema = { "uuid": module_uuid }  # type: ignore[typeddict-item]
    for k, v in config.items():
        if k == "uuid":
            continue  # already set above
        if k == "apparatus":
            if apparatus_names:
                # bool/str "true" case — user picked one or more names at the prompt
                final_config["apparati"] = apparatus_names  # type: ignore[typeddict-item]
            elif remove_apparatus:
                pass  # drop the key entirely
            else:
                # v2 real-name case — wrap in list under the new key
                final_config["apparati"] = [str(v)]  # type: ignore[typeddict-item]
            continue
        final_config[k] = v  # type: ignore[literal-required]

    write_archivist_config(git_root, final_config)
    success(f"  Written: .archivist/config.yaml")

    # 4. Sample changelog for library projects.
    if module_type == "library":
        _copy_sample_changelog(git_root, dry_run = False)

    # 5. Stage both sides of the migration automatically — same pattern as
    #    `git submodule add`, which stages .gitmodules and the submodule
    #    directory without asking. The deletion and the new directory are one
    #    logical operation; they should land in the index together.
    try:
        subprocess.run(
            ["git", "add", ".archivist/"],
            check = True,
            cwd = git_root,
            capture_output = True,
        )
        # Stage the flat file deletion. git add on a deleted path records the
        # removal in the index — equivalent to git rm --cached but works whether
        # the file is already gone from disk (which it is) or not.
        subprocess.run(
            ["git", "add", ".archivist"],
            check = True,
            cwd = git_root,
            capture_output = True,
        )
        success("  Staged: .archivist/ (new) + .archivist deletion")
    except subprocess.CalledProcessError as e:
        warning(
            "Auto-staging failed — stage manually before committing:\n"
            "     git add .archivist/\n"
            f"     ({e})"
        )

    # 6. Offer to sync hooks locally. The old flat-file hooks check `[ -f
    #    .archivist ]` which is false for a directory — they're broken the
    #    moment migration completes. Syncing now fixes that immediately.
    #    Global templates are the user's call; `archivist hooks install` handles
    #    that separately.
    progress(
        "\n  The existing git hooks check for a flat .archivist file and will"
        "\n  silently skip all archivist work on migrated repos until updated."
    )
    answer = input("  Sync hooks for this repo now? [Y/n] ").strip().lower()
    if answer in ("", "y", "yes"):
        _sync_hooks_local(git_root, dry_run=False)
    else:
        progress(
            "  Skipping hook sync. Run `archivist hooks sync` before your next commit"
            " or changelog sealing will not work."
        )

    # --- Done ---
    success(
        "\n  Migration complete. Commit when ready:\n"
        "\n"
        "      git commit -m 'chore: migrate .archivist to directory form'\n"
        "\n"
        "  If your .gitignore mentions .archivist specifically, update it.\n"
        "  If it ignores dotfiles wholesale, you may need to un-ignore .archivist/."
    )