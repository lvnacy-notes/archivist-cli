"""
archivist sync

Idempotent registry backfill. Walks the submodule tree from here down —
recursively, any module type, not just vaults, since nesting a module inside
another module isn't a vault-exclusive privilege — and makes sure every
module that already carries a `.archivist/config.yaml` with a uuid is (a)
present and current in the registry and (b) linked to its direct container
in `module_bays`.

This is deliberately NOT `init`. `sync` itself never prompts and never
invents an apparatus assignment for a module that hasn't already declared
one non-interactively — guessing at that is exactly the kind of thing that
quietly corrupts a registry with associations nobody actually chose. When a
project's config can't be resolved non-interactively — no config at all, a
legacy flat file, or a directory-form config missing a uuid — `sync` hands
off to `init`, which does the actual interactive work. Anything `sync` CAN
resolve on its own, it does; anything it can't, it defers, it doesn't guess.

Typical reasons you'd reach for this instead of `add` or `init`:

  - You `git submodule add`ed something by hand, bypassing `archivist add`
    entirely, and only now want it wired into the registry.
  - You inherited (or built) a vault where nested modules predate this
    containment logic, or predate Archivist being involved at all.
  - A directory got renamed or moved since it was last registered and its
    cached path in the registry has gone stale — every node this walks
    through gets its path refreshed as a side effect of being resolved.
"""

import argparse
from pathlib import Path

from archivist.utils import (
    ConfigSchema,
    get_archivist_config_path,
    get_registry_dir,
    get_registry_path,
    get_repo_root,
    get_superproject_root,
    get_module_by_uuid,
    init_registry,
    link_module_into_container,
    list_direct_submodules,
    progress,
    reactivate_module,
    read_archivist_config,
    register_known_module_with_apparati,
    resolve_container_module,
    success,
)


def _initialize_project(args: argparse.Namespace) -> None:
    from archivist.commands.init import run as init_run
    init_run(args)


def _register_or_refresh(
    module_dir: Path,
    config: ConfigSchema,
    module_uuid: str,
    dry_run: bool
) -> bool:
    """
    Ensure module_uuid is present and current in the registry, per whatever
    config already declares — no prompts, no guessing at an apparatus
    assignment nobody's made yet.

    Returns True if the module is (or, under --dry-run, would be) present
    in the registry afterward, meaning it's safe to link into a container.
    False means "sync can't resolve this non-interactively" — the caller
    reports it as skipped and moves on.

    Writes nothing when dry_run is True. Every branch that would mutate the
    registry is mirrored with a preview line instead.
    """
    apparati = list(config.get("apparati") or [])  # type: ignore[arg-type]
    module_type = str(config.get("module-type") or "general")
    git_remote = config.get("git-remote")
    git_remote_name = config.get("git-remote-name")

    registry_row = get_module_by_uuid(module_uuid) if get_registry_path().exists() else None

    if registry_row is not None:
        if registry_row.get("decimated_at"):
            if dry_run:
                progress(f"    [dry-run] would reactivate: { module_dir }")
            else:
                reactivate_module(module_uuid)
                progress(f"    reactivated: { module_dir }")
        if apparati:
            if not dry_run:
                register_known_module_with_apparati(
                    module_uuid,
                    module_dir,
                    apparati,
                    module_type,
                    git_remote,
                    git_remote_name
                )
            # Already registered and no apparati change to make — nothing
            # to preview or write either way; the path refresh happens
            # inside resolve_container_module() for whichever node ends up
            # being someone's container, which is the only place a stale
            # path actually matters.
        return True

    if not apparati:
        progress(
            f"    skip: { module_dir } has a uuid but no apparatus assignment yet "
            "and isn't registered — run `archivist init` there to decide."
        )
        return False

    if dry_run:
        progress(f"    [dry-run] would register: { module_dir }")
        return True

    register_known_module_with_apparati(
        module_uuid,
        module_dir,
        apparati,
        module_type,
        git_remote,
        git_remote_name
    )
    progress(f"    registered: { module_dir }")
    return True


def _sync_node(module_dir: Path, dry_run: bool) -> tuple[int, int]:
    """
    Ensure module_dir itself is registered per its own config, link it to
    its container if it turns out to be nested in something git and the
    registry both recognize, then recurse into every DIRECT submodule of
    module_dir and do the same, all the way down.

    Every node resolves its OWN container independently via
    get_superproject_root() rather than the caller threading a container
    down through the recursion — that's what makes this correct at
    arbitrary nesting depth without extra bookkeeping.

    Returns (linked_count, skipped_count) accumulated across the whole subtree.
    """
    linked = 0
    skipped = 0

    config = read_archivist_config(module_dir)
    module_uuid = config.get("uuid") if config else None

    if config and module_uuid:
        progress(f"  { module_dir }")
        registered = _register_or_refresh(
            module_dir,
            config,
            str(module_uuid),
            dry_run
        )

        if registered:
            superproject_root = get_superproject_root(module_dir)
            if superproject_root:
                container_row = resolve_container_module(superproject_root)
                if container_row:
                    if dry_run:
                        progress(f"    [dry-run] would link: {container_row['name']} ← {module_dir.name}")
                    else:
                        link_module_into_container(container_row, str(module_uuid), module_dir)
                        success(f"    linked: { container_row['name'] } ← { module_dir.name }")
                    linked += 1
                else:
                    progress(
                        f"    nested under { superproject_root }, but that isn't a "
                        "registered container — no link made."
                    )
                    skipped += 1
        else:
            skipped += 1
    else:
        progress(f"  { module_dir } — no Archivist config, skipping registration.")
        skipped += 1

    for sub_dir in list_direct_submodules(module_dir):
        sub_linked, sub_skipped = _sync_node(sub_dir, dry_run)
        linked += sub_linked
        skipped += sub_skipped

    return linked, skipped


def run(args: argparse.Namespace) -> None:
    dry_run = getattr(args, "dry_run", False)

    git_root = get_repo_root()
    config = read_archivist_config(git_root)
    config_path = get_archivist_config_path(git_root)

    if config is None:
        progress("  No Archivist config found for this project.")
        _initialize_project(args)
        return

    # A flat legacy `.archivist` file, or a directory-form config that's
    # missing a uuid — both mean this project isn't fully wired into the
    # Apparatus Platform yet, and `init` is the one command built to handle
    # either: it reads whatever config already exists, prompts for whatever's
    # missing, upserts the registry, and evicts the flat file the instant it
    # writes the directory form. There used to be a separate `migrate`
    # command here that only handled the flat-file case — and even then did
    # nothing for the registry unless apparati were already declared in the
    # old config. It's gone. `init` covers both cases and does more.
    legacy_config_path = git_root / ".archivist"
    is_legacy_flat_file = (
        config_path == legacy_config_path
        and legacy_config_path.exists()
        and legacy_config_path.is_file()
    )

    if is_legacy_flat_file:
        progress("  Found a legacy flat .archivist config. Running `init` to migrate it.")
        _initialize_project(args)
        return

    if not config.get("uuid"):
        progress("  Archivist config exists but has no uuid. Running `init` to fix it.")
        _initialize_project(args)
        return

    if not dry_run and not get_registry_dir().exists():
        progress("  Registry not found — initializing schema.")
        init_registry()

    progress(f"  Syncing containment tree from: {git_root}\n")
    linked, skipped = _sync_node(git_root, dry_run)

    if linked == 0 and skipped == 0:
        progress("  Nothing under here has Archivist config to sync.")

    verb = "Would link" if dry_run else "Linked"
    success(f"{ verb } { linked } module(s). Skipped { skipped }.")