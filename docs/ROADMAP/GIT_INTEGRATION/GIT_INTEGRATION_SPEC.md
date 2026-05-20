---
class: spec
category:
  - feature
  - database
affiliations:
created:
modified: 2026-05-18
version: 0.1
related:
  - "[[GIT_INTEGRATION]]"
  - "[[GIT_INTEGRATION_PREREQUISITES]]"
  - "[[Drawing - git Implementation.png]]"
tags:
---
**Depends on:** Centralized Database feature (must ship first)

---

## 1. Overview

Archivist wraps select git operations to keep the Apparatus registry in sync with the actual state of the filesystem and version control. The principle is simple: any git operation that changes the structure of the Apparatus — bringing a module in, taking one out, initializing a new one — should be manageable through Archivist rather than requiring a git command followed by a separate Archivist command.

This is not a git wrapper in the general sense. Archivist does not intend to replicate git's interface or own git's responsibilities. It owns the Apparatus. Git owns the repos. Where those two concerns intersect — registration, containment, lifecycle — Archivist coordinates both in a single command.

---

## 2. Modularity as a Design Constraint

Every decision in this feature should be evaluated against the Apparatus model: everything is a module, and modules are composable. A vault is a module. A library is a module. A standalone general repo is a module. The git integration exists to make registering, containing, and removing modules frictionless — not to make Archivist into a git frontend.

This framing has a practical consequence: **the commands in this spec manage Apparatus membership, and git is the mechanism, not the product.** When `archivist add` runs `git submodule add`, it does so because adding a submodule is how you express containment in git. The user's goal is registering a module with the Apparatus; the git operation is how that intent is committed to the repository.

---

## 3. Commands

### 3.1 `archivist init`

**Current behavior:** assumes a git repository already exists. Calls `get_repo_root()` early and exits if no repo is found.

**New behavior:** checks for `.git` first. If present, proceeds as before. If absent, runs `git init` under the hood, then proceeds with the standard Archivist configuration and Apparatus registration flow.

**Flow:**

```
1. Check working directory for .git (file or folder)
   → found: proceed directly to step 3
   → not found: run git init, then proceed to step 3

2. [git init, if needed]

3. Check for .archivist/config.yaml
   → found: standard reconfiguration flow (present existing values as defaults)
   → not found: full interactive configuration setup

4. Apparatus registration (unchanged from current centralized DB implementation)
```

**Impact on existing code:** `get_repo_root()` moves to after the `.git` check resolves. Everything downstream is unchanged.

**`--dry-run`:** prints what `git init` would run and what configuration would be written; executes neither.

---

### 3.2 `archivist add`

Registers a module with the Apparatus. The git operation — clone or submodule add — is determined by context, not by flags.

**Context detection:**

- Working directory has no `.git`: run `git clone <url> [path] [passthrough]`
- Working directory has `.git`: run `git submodule add <url> [path] [passthrough]`

This is the correct and unambiguous behavior. `git clone` inside an existing repository is not a legitimate use case this command needs to support. If you are inside a repo, you are adding a submodule. If you are not, you are cloning. The context determines the operation; no flags are needed to disambiguate.

**Flow:**

```
1. Check working directory for .git
   → not found: resolve as git clone
   → found: resolve as git submodule add

2. Execute git operation with all passthrough arguments
   → git errors out: propagate exit code and stderr; abort

3. Enter the target module directory

4. UUID resolution (see CENTRALIZED_DATABASE_SPEC.md §9.1 for full detail):
   → uuid in config, found in registry, decimated: reactivation path
   → uuid in config, found in registry, active: add module_bays row if absent; exit
   → uuid in config, not found in registry: register using config as defaults
   → no config: full interactive registration flow

5. Generate UUID if not already present; write to .archivist/config.yaml

6. Upsert registry.db; add module_bays row if superproject is a registered
   vault module

7. Install git hooks into the target module
```

**`--dry-run`:** prints the git command that would run and the registration changes that would occur; executes neither.

---

### 3.3 `archivist deinit`

Deregisters a module from the Apparatus and removes it from the superproject or machine. **Run this from the working directory outside the module being removed.**

**Operation order: Apparatus first, git second.** This ordering is deliberate and must not be reversed. The rationale is in §5.

**Flow:**

```
1. Look up module by path in registry.db
   → not found: warn clearly; do not proceed

2. Require explicit user confirmation
   (--dry-run does not skip this prompt)

3. Apparatus cleanup (first):
   a. Remove module_bays rows where this module is contained_id
      and the container belongs to the current superproject's vault module
      (if called outside a superproject context: remove all module_bays rows
      where this module is contained_id)
   b. Check remaining module_bays rows where this module is contained_id
      → rows remain: module is still accessible via another container;
        leave modules row intact
      → no rows remain: stamp modules.decimated_at = today

4. Git cleanup (second):
   → module is a git submodule:
       a. git submodule deinit [passthrough] <path>
       b. git rm <path>  (stages the removal in the superproject)
       c. verify superproject registry entry is current
   → module is not a git submodule:
       a. attempt removal via shutil.rmtree (no sudo; handles user-owned paths)
       b. on permission failure: print the path and instruct the user to
          remove it manually; do not attempt sudo
   → git/removal fails: warn; registry is already updated; see §4 for
     recovery path

5. Print summary
   → if decimated: note that history is preserved; module can be reactivated
     via archivist add with the same repo URL
```

**`--retain` flag:** performs Apparatus cleanup only; skips the git operation and leaves the module on disk. Use this when the git state is already clean and only the registry needs updating, or as a manual recovery step after a partial failure in step 4.

**`--dry-run`:** prints what Apparatus changes and git operations would occur; writes nothing. Confirmation prompt still fires.

---

## 4. Argument Passthrough

All git flags and options are passed through to the underlying git command without validation or curation. Archivist does not need to understand them; git will reject invalid arguments with its own error output. Archivist propagates the exit code and stderr verbatim.

Implementation: `nargs=argparse.REMAINDER` captures everything after the known Archivist arguments and is spliced into the git subprocess call.

```
archivist add git@github.com:user/repo.git modules/my-lib --depth 1 -b main
```

In the above, `--depth 1 -b main` are unknown to Archivist's parser and are passed directly to `git submodule add` (or `git clone`).

---

## 5. Failure Semantics and Recovery

### 5.1 Operation Ordering Rationale

`deinit` runs Apparatus cleanup before the git operation. The invariant this protects: **the module's `.archivist/config.yaml` must be on disk when the registry is updated.**

If the git operation runs first and the module is successfully removed, the config is gone. A subsequent registry failure has nothing to work from — there is no config to re-read, no UUID to look up, no recovery path. The user is left with an inconsistent state that cannot be automatically resolved.

If Apparatus cleanup runs first and fails, the module is still on disk with its config intact. The user can retry `archivist deinit` or resolve the registry issue manually. Git has not touched anything.

The inverse scenario — Apparatus cleanup succeeds, git fails — leaves the registry saying the module is gone while the filesystem still has it. This is recoverable: the user can run the git operation manually to finish the cleanup. The `--retain` flag (§3.3) is a surgical tool for this case: if the user needs to re-run only the registry portion, they run `archivist deinit --retain` first, then handle git manually.

### 5.2 Idempotency Requirement

`archivist deinit` must be safe to re-run after a partial failure. If Apparatus cleanup completed but the git step failed:

- Re-running the command must detect that the registry is already updated (no `module_bays` rows to remove, and/or  `decimated_at` already set)
- The Apparatus step must be a no-op
- Only the git step fires

This is not optional. Partial failures are not edge cases; they are guaranteed to happen in production environments.

### 5.3 `archivist add` Failure

If the git operation in step 2 fails, the command aborts immediately. No Archivist configuration or registry work is attempted. The working directory is left as it was. The user addresses the git error and retries.

---

## 6. `archivist restore` — Deferred

`archivist restore` is a planned future command that reconstructs an Apparatus from the registry in the event of system failure or machine migration. It is explicitly out of scope for the initial git integration implementation.

It is documented here because the decisions made in this spec must not foreclose it. Specifically:

**What `restore` will need:**

- A registry (`registry.db`) that is itself version-controlled and available via remote, or that can be reconstructed from module configs
- `git_remote` populated on every `modules` row — this is already in the schema and must be written reliably during `archivist add` and `archivist init`
- Enough information in the registry to re-clone every module and re-establish containment relationships from `module_bays`
- A strategy for the apparatus DB (`[apparatus].db`) — either it is also version-controlled, or it is reconstructible from the git history of each module (changelogs, works cards)

**What this spec must not break:**

- `git_remote` must be populated on registration — both `archivist add` and `archivist init` must write it to the registry. This is already specified in the centralized DB implementation checklist (§1.5.5); it is called out here explicitly because `restore` depends on it.
- The registry must be kept current. The pre-commit hook sync (§1.5.8 of the centralized DB spec) is the mechanism; it must run reliably and must not be degraded during git integration implementation.
- Tombstoning must be clean. `decimated_at` exists precisely so `restore` knows what was intentionally removed versus what is merely absent from the current machine. Do not skip it, do not repurpose it.

**The registry resiliency question:**

The registry (`registry.db`) needs to be queryable from outside any individual module to drive a restore operation. This implies it either lives in a version-controlled location that gets pushed to a remote, or Archivist maintains a separate backup mechanism. This decision is deferred — but the fact that `~/.archivist/` is not a git repository today should be treated as a known gap, not an oversight. The initial git integration should leave room for this to be addressed without a structural rewrite of the registry layer.

---

## 7. Dependencies on the Centralized Database Feature

The following centralized DB behaviors must be in place before git integration implementation begins. These are not new requirements — they are called out here to surface any gaps between the centralized DB spec and what the git integration will need.

**Required and already specced:**

- `get_registry_path()`, `get_registry_connection()` — §Phase 1 of `CENTRALIZED_DATABASE_IMPLEMENTATION.md`
- UUID generation and storage in `.archivist/config.yaml` — §1.5.2
- `register_module()`, `get_module_by_uuid()`, `is_module_registered()` — §1.5.1
- `add_module_to_bay()` — §1.5.1
- `git_remote` written to `modules` on registration — §1.5.5
- Pre-commit hook registry sync — §1.5.8
- `archivist add` scaffolded (git passthrough not yet active) — §1.5.3
- `archivist deinit` scaffolded (git passthrough not yet active) — §1.5.4

**Gap — `git_remote` population and remote selection:**

The centralized DB implementation checklist specifies that `git_remote` is collected via `git remote get-url origin` during `archivist init` (§1.5.5). This is a footgun. Not every user names their remote "origin"; git imposes no such convention, and users who manage multiple remotes per repo — e.g. named by platform — will have no "origin" to get.

Two distinct cases with different correct behaviors:

- **`archivist init`:** No URL is available from command args. Archivist must list configured remotes (`git remote -v`), present them to the user, and let the user select which one to register. If no remotes are configured, allow the user to enter a URL manually or skip (with a warning that restore capability will be limited). "origin" may be offered as a default suggestion only when a remote with that name actually exists.

- **`archivist add`:** The URL is already known — it was passed as an argument to the command. Store it directly. Do not query git for a remote name after the fact; the name git assigns (default "origin", or overridden via passthrough flags) is irrelevant. The URL is what matters for `restore`.

Both behaviors must be specced and implemented in the centralized DB featured before git integration begins. The `git remote get-url origin` call in §1.5.5 of the centralized DB implementation checklist must be updated accordingly.

---

## 8. Open Questions

**Registry version control:** `~/.archivist/` is a git repository. The entire directory — `registry.db`, all apparatus databases, everything — is version controlled. This is not optional and not limited to `registry.db` in isolation; the apparatus databases are derived from the registry and branch off of it, and version controlling one without the others is incoherent.

The registry repo has a remote, configured during first-run `archivist init` on a new machine. That remote is the restoration anchor for `archivist restore`. The pre-commit hook (or a dedicated sync step) commits and pushes changes to `~/.archivist/` automatically so the remote stays current.

SQLite files are binary. Git tracks them but cannot diff or merge them meaningfully. The strategy is explicit: **`~/.archivist/` is never merged — it is overwritten from the remote on restore.** No merge conflicts, no conflict resolution tooling, no surprises. The remote is the source of truth for restore operations; local is the source of truth for active operations. These do not conflict because the pre-commit sync keeps them in agreement.

The shape of the first-run initialization flow — specifically, when and how the user configures the registry remote — is a dependency for this feature and must be resolved in the prerequisite checklist before implementation begins.

**Superproject detection in `deinit`:** Step 3 of the deinit flow removes `module_bays` rows scoped to the current superproject's vault module. Detecting the superproject reliably requires `git rev-parse --show-superproject-working-tree`. This is already used in the centralized DB spec (§1.5.5); confirm it behaves correctly when called from the superproject working directory rather than from within the submodule.