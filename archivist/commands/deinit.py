"""
archivist deinit

Deregister a module from the Apparatus and remove it from the superproject
or machine. Run from outside the module being removed.

Operation order is not negotiable: Apparatus first, git second.

If git runs first and succeeds, .archivist/config.yaml is gone and a
subsequent registry failure has nothing to recover from. If Apparatus
cleanup runs first and fails, the module is still on disk with its config
intact and the user can retry. Get it wrong and someone loses data they
cannot get back. Don't get it wrong.
"""

import argparse
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from archivist.utils import (
    ConfigSchema,
    ModuleBayRow,
    ModuleRow,
    decimate_module,
    error,
    get_module_bays,
    get_module_by_path,
    get_module_by_uuid,
    get_registry_path,
    progress,
    read_archivist_config,
    remove_all_apparatus_memberships,
    remove_all_bays_for_contained,
    remove_module_from_bay,
    success,
    warning,
    write_archivist_config,
)


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def _confirm(question: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    raw = input(f"\n{question} {hint}: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _remove_from_vaults_list(target_dir: Path, vault_name: str) -> None:
    """
    Remove vault_name from the `vaults` list in target_dir's config.yaml.
    No-op if the config doesn't exist, the key is absent, or vault_name
    isn't in the list — all of these are valid partially-cleaned states.
    """
    config = read_archivist_config(target_dir)
    if not config:
        return
    current: list[str] = list(config.get("vaults") or [])  # type: ignore[arg-type]
    if vault_name not in current:
        return
    current.remove(vault_name)
    updated: ConfigSchema = {"uuid": str(config.get("uuid", ""))}  # type: ignore[typeddict-item]
    for k, v in config.items():
        if k != "uuid":
            updated[k] = v  # type: ignore[literal-required]
    updated["vaults"] = current  # type: ignore[typeddict-item]
    write_archivist_config(target_dir, updated)


# ---------------------------------------------------------------------------
# Submodule detection
# ---------------------------------------------------------------------------

def _is_git_submodule(
    target_path: Path,
    cwd: Path | None = None,
) -> tuple[bool, Path | None]:
    """
    Return (is_submodule, parent_git_root).

    Walks up from cwd (defaults to target_path.parent) to find the enclosing
    git repo root, then checks .gitmodules for an entry matching target_path.
    parent_git_root is returned regardless — it's needed for running git
    commands even when the target is not a submodule.

    The cwd parameter is an injection seam: production callers omit it and get
    the natural default; tests pass a controlled path to avoid shelling out to
    git in contexts where the subprocess call is not what's under test.

    Returns (False, None) if no enclosing git repo is found at all.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
            cwd = cwd if cwd is not None else target_path.parent,
        )
        parent_git_root = Path(result.stdout.strip())
    except subprocess.CalledProcessError:
        return False, None

    gitmodules = parent_git_root / ".gitmodules"
    if not gitmodules.exists():
        return False, parent_git_root

    try:
        rel = target_path.relative_to(parent_git_root)
    except ValueError:
        return False, parent_git_root

    # .gitmodules uses forward slashes on all platforms
    rel_str = str(rel).replace("\\", "/")
    content = gitmodules.read_text(encoding="utf-8")
    return f"path = {rel_str}" in content, parent_git_root


# ---------------------------------------------------------------------------
# Apparatus cleanup
# ---------------------------------------------------------------------------

def _apparatus_cleanup(
    target_uuid: str,
    target_name: str,
    target_path: Path,
) -> list[ModuleBayRow]:
    """
    Run the Apparatus half of deinit. Always runs before git. Always.

    Determines whether this is a scoped removal (cwd is a registered
    superproject of the target) or a standalone removal (remove all bay
    relationships). Decimates the module only when no bay rows remain.

    Returns the list of remaining bay containers after cleanup — used by
    run() to print the summary.
    """
    cwd = Path.cwd()
    cwd_module: ModuleRow | None = None
    if get_registry_path().exists():
        cwd_module = get_module_by_path(cwd)

    existing_bays = get_module_bays(target_uuid)

    # Determine whether cwd is an active registered container of this module
    cwd_bay: ModuleBayRow | None = None
    if cwd_module and not cwd_module.get("decimated_at"):
        cwd_bay = next(
            (b for b in existing_bays if b["uuid"] == cwd_module["uuid"]), None
        )

    if cwd_bay is not None:
        # Scoped removal — only this superproject's bay row goes. The module
        # may still live in other containers; decimation is not our call to make.
        assert cwd_module is not None
        remove_module_from_bay(cwd_module["uuid"], target_uuid)
        progress(f"  Bay removed: {cwd_module['name']} ← {target_name}")

        # Remove from target's vaults list if this superproject is vault-type
        if cwd_module.get("module_type") == "vault":
            _remove_from_vaults_list(target_path, cwd_module["name"])
            progress(f"  Removed '{cwd_module['name']}' from target's vaults list.")

        remaining = [b for b in existing_bays if b["uuid"] != cwd_module["uuid"]]
        if remaining:
            names = [b["name"] for b in remaining]
            progress(f"  Module still contained by: {', '.join(names)}. Keeping active.")

    else:
        # Standalone removal — evict from all bay relationships, all apparatus
        # memberships, then decimate.
        if existing_bays:
            # Update vaults list for every vault-type container being removed
            for bay in existing_bays:
                if bay.get("module_type") == "vault":
                    _remove_from_vaults_list(target_path, bay["name"])
            remove_all_bays_for_contained(target_uuid)
            progress(f"  All bay relationships removed ({len(existing_bays)}).")
        else:
            progress("  No bay relationships found — skipping.")

        remove_all_apparatus_memberships(target_uuid)
        progress("  All apparatus memberships removed.")

        remaining = []

        row = get_module_by_uuid(target_uuid)
        if row and row.get("decimated_at"):
            progress("  Module already decimated — skipping.")
        else:
            decimate_module(target_uuid)
            progress(
                "  Module decimated. "
                "History preserved; reactivatable via archivist add."
            )

    return remaining


# ---------------------------------------------------------------------------
# Git cleanup
# ---------------------------------------------------------------------------

def _git_cleanup(
    target_path: Path,
    passthrough: list[str],
    runner: Callable[..., object] = subprocess.run,
    remover: Callable[[Path], None] = shutil.rmtree,
) -> None:
    """
    Run the git half of deinit. Always runs after Apparatus cleanup. Always.

    Submodule path: git submodule deinit + git rm.
    Plain directory:  remover(target_path) — shutil.rmtree by default.
    Missing path: warn and skip; the filesystem is already clean.

    runner and remover are injection seams with production defaults. Pass
    fakes in tests to avoid touching the real filesystem or spawning git
    processes. Production callers omit both and get the real thing.
    """
    if not target_path.exists():
        warning(
            f"  '{target_path}' no longer exists on disk. "
            "Filesystem already clean — skipping git step."
        )
        return

    is_submodule, parent_git_root = _is_git_submodule(target_path)

    if is_submodule and parent_git_root is not None:
        rel = str(target_path.relative_to(parent_git_root)).replace("\\", "/")

        deinit_cmd = ["git", "submodule", "deinit"] + passthrough + [rel]
        progress(f"\n  ▶ {' '.join(deinit_cmd)}")
        try:
            runner(
                deinit_cmd,
                check = True,
                cwd = parent_git_root
            )
        except subprocess.CalledProcessError as e:
            _git_failure(target_path, rel, e)

        rm_cmd = ["git", "rm", rel]
        progress(f"  ▶ {' '.join(rm_cmd)}")
        try:
            runner(
                rm_cmd,
                check = True,
                cwd = parent_git_root
            )
        except subprocess.CalledProcessError as e:
            _git_failure(target_path, rel, e)

    else:
        progress(f"\n  Removing directory: {target_path}")
        try:
            remover(target_path)
        except PermissionError:
            error(
                f"Can't remove '{target_path}' — permission denied.\n"
                "Remove it manually. Do not sudo."
            )
            sys.exit(1)
        except OSError as e:
            error(f"Failed to remove '{target_path}': {e}")
            sys.exit(1)


def _git_failure(
    target_path: Path,
    rel: str,
    e: subprocess.CalledProcessError,
) -> None:
    """
    Print specific recovery instructions after a git operation failure and exit.

    The registry is already clean at this point — Apparatus cleanup ran first.
    The user needs to know exactly what to do to finish the job manually.
    """
    error(
        f"Git operation failed (exit {e.returncode}). "
        "Registry cleanup has already run.\n"
        f"\n  The module directory at '{target_path}' still exists on disk."
        f"\n  To finish cleanup manually:"
        f"\n    git submodule deinit {rel}"
        f"\n    git rm {rel}"
        f"\n  Then run: archivist deinit --retain {target_path}"
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Dry-run plan
# ---------------------------------------------------------------------------

def _print_dry_run_plan(
    module: ModuleRow,
    target_path: Path,
    passthrough: list[str],
    retain: bool,
) -> None:
    target_uuid = module["uuid"]
    target_name = module["name"]

    cwd = Path.cwd()
    cwd_module: ModuleRow | None = None
    if get_registry_path().exists():
        cwd_module = get_module_by_path(cwd)

    existing_bays = get_module_bays(target_uuid)

    cwd_bay: ModuleBayRow | None = None
    if cwd_module and not cwd_module.get("decimated_at"):
        cwd_bay = next(
            (b for b in existing_bays if b["uuid"] == cwd_module["uuid"]), None
        )

    if cwd_bay is not None:
        assert cwd_module is not None
        progress(f"  [dry-run] Would remove bay: {cwd_module['name']} ← {target_name}")
        if cwd_module.get("module_type") == "vault":
            progress(
                f"  [dry-run] Would remove '{cwd_module['name']}' from target's vaults list."
            )
        remaining = [b for b in existing_bays if b["uuid"] != cwd_module["uuid"]]
        if remaining:
            names = [b["name"] for b in remaining]
            progress(
                f"  [dry-run] Module still contained by: {', '.join(names)}. Would stay active."
            )
        else:
            progress("  [dry-run] No other containers — module would stay active (scoped removal).")
    else:
        progress(f"  [dry-run] Would remove all bay relationships ({len(existing_bays)}).")
        progress("  [dry-run] Would remove all apparatus memberships.")
        if module.get("decimated_at"):
            progress(f"  [dry-run] Module already decimated — no change.")
        else:
            progress(f"  [dry-run] Would decimate module '{target_name}'.")

    if retain:
        progress("  [dry-run] --retain: git operation would be skipped.")
        return

    if not target_path.exists():
        progress(
            f"  [dry-run] '{target_path}' not found on disk — git step would be skipped."
        )
        return

    is_submodule, parent_git_root = _is_git_submodule(target_path, cwd = target_path.parent)
    if is_submodule and parent_git_root is not None:
        rel = str(target_path.relative_to(parent_git_root)).replace("\\", "/")
        deinit_cmd = ["git", "submodule", "deinit"] + passthrough + [rel]
        rm_cmd = ["git", "rm", rel]
        progress(f"  [dry-run] Would run: {' '.join(deinit_cmd)}")
        progress(f"  [dry-run] Would run: {' '.join(rm_cmd)}")
    else:
        progress(f"  [dry-run] Would run: shutil.rmtree({target_path})")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(
    args: argparse.Namespace,
    runner: Callable[..., object] = subprocess.run,
    remover: Callable[[Path], None] = shutil.rmtree,
) -> None:
    dry_run = getattr(args, "dry_run", False)
    retain = getattr(args, "retain", False)
    path_arg: str = args.path
    passthrough: list[str] = list(getattr(args, "passthrough", None) or [])

    target_path = Path(path_arg).resolve()

    # Must be able to look the module up before doing anything else
    if not get_registry_path().exists():
        error("Registry not found. Run archivist init first.")
        sys.exit(1)

    module: ModuleRow | None = get_module_by_path(target_path)
    if module is None:
        warning(f"'{target_path}' is not registered in the Apparatus. Nothing to do.")
        sys.exit(0)

    target_uuid: str = module["uuid"]
    target_name: str = module["name"]

    # Refuse to run from inside the module being removed — the git step would
    # destroy the working directory and the Apparatus step can't read config.
    cwd = Path.cwd()
    try:
        cwd.relative_to(target_path)
        # If we get here, cwd is inside or equal to target_path
        error(
            f"You're inside '{target_name}'. "
            "Move to the parent directory and try again."
        )
        sys.exit(1)
    except ValueError:
        pass  # cwd is outside target_path; safe to proceed

    # Show what we're about to do
    print(f"\n  Module: {target_name}")
    print(f"  Path:   {target_path}")
    if retain:
        print("  Mode:   registry cleanup only (--retain; git untouched)")

    # Confirmation prompt fires even on dry-run — a dry run that skips
    # confirmation gives no useful information about what would actually happen.
    if not _confirm(f"Deregister and remove '{target_name}'?", default=False):
        progress("Aborted.")
        sys.exit(0)

    if dry_run:
        _print_dry_run_plan(module, target_path, passthrough, retain)
        return

    # --- Apparatus cleanup (FIRST, always) ---
    remaining = _apparatus_cleanup(target_uuid, target_name, target_path)

    if retain:
        progress("  --retain: skipping git operation.")
        success("Registry cleanup complete.")
        return

    # --- Git cleanup (SECOND, always) ---
    _git_cleanup(
        target_path,
        passthrough,
        runner = runner,
        remover = remover
    )

    # Summary
    if remaining:
        names = [b["name"] for b in remaining]
        success(
            f"'{target_name}' removed from this superproject. "
            f"Still registered under: {', '.join(names)}."
        )
    else:
        success(
            f"'{target_name}' deregistered and removed. "
            "History preserved; reactivatable via archivist add."
        )