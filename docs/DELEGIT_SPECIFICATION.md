# DeleGit — Specification

> Version 0.1 — Draft
> Status: In Progress

---

## delegate

/dĕl′ĭ-gāt″, -gĭt/

**noun**
1. A person authorized to act as representative for another; a deputy or agent.
2. A representative to a conference or convention.
3. A member of a House of Delegates, the lower house of the Maryland, Virginia, or West Virginia legislature.

  - The American Heritage® Dictionary of the English Language, 5th Edition

## Purpose

A minimal, focused Swift wrapper over libgit2, purpose-built for Archivist mobile. Covers local git
operations only. Remote ops (push, pull, fetch, clone) are explicitly out of scope — those belong to
Working Copy via x-callback-url.

Designed to grow into a general-purpose Swift git library across future apps. Every function added
must be driven by a real use case, not completeness for its own sake.

---

## Guiding Principles

**Narrow by design.** Only wrap what's needed. Resist the urge to wrap adjacent functions because
they're nearby in the libgit2 headers.

**Idiomatic Swift.** No C types leak through the public API. No `UnsafePointer`, no `Int32` error
codes, no `OpaquePointer`. Callers see Swift structs, enums, and thrown errors.

**Throwing over optionals.** Functions that can fail throw a typed `GitError`. Never return nil when
the reason for failure is knowable and actionable.

**Async where it matters.** Diff and blob operations can be slow on large repos. Mark them async.
Simple operations like staging a file can be synchronous.

**Memory is the library's problem.** Every libgit2 object that requires a free call gets one, inside
the wrapper, before the Swift type is returned. Callers never touch a libgit2 pointer.

---

## Package Structure

```
DeleGit/
├── Package.swift
├── Sources/
│   ├── DeleGit/           ← public Swift API
│   │   ├── Repository.swift
│   │   ├── Diff.swift
│   │   ├── Staging.swift
│   │   ├── Commit.swift
│   │   ├── Blob.swift
│   │   └── Errors.swift
│   └── Clibgit2/              ← C module map, libgit2 headers
│       └── module.modulemap
└── Tests/
    └── DeleGitTests/
```

`Clibgit2` is a system library target — it maps the libgit2 headers into a Swift-importable module.
Everything in `DeleGit` imports `Clibgit2` and nothing from `Clibgit2` leaks into the public API.

---

## Error Handling

```swift
public enum GitError: Error {
    case repositoryNotFound(path: String)
    case notARepository(path: String)
    case diffFailed(reason: String)
    case stagingFailed(path: String, reason: String)
    case commitFailed(reason: String)
    case blobNotFound(ref: String, path: String)
    case blobReadFailed(reason: String)
    case headUnborn                        // empty repo, no commits yet
    case libgit2Error(code: Int32, message: String)
}
```

`libgit2Error` is the catch-all for error codes that don't map to a named case. Every call site
checks the libgit2 return code and either maps it to a named case or falls through to `libgit2Error`
with the message from `giterr_last()`.

---

## Public API — v0.1

### Repository

```swift
public struct Repository {

    /// Open an existing repository at the given file URL.
    /// Throws repositoryNotFound or notARepository if the path
    /// doesn't exist or isn't a git repo.
    public static func open(at url: URL) throws -> Repository

    /// The working directory URL for this repository.
    public var workdirURL: URL { get }

    /// The path to the .git directory.
    public var gitdirURL: URL { get }
}
```

`Repository` is the entry point for everything else. It holds the `git_repository *` internally,
manages its lifetime, and frees it on deinit. It is a class, not a struct — libgit2 repository
handles are not cheap to copy and the pointer ownership needs to be clear.

---

### Diff

This is the core of what Archivist needs. Two operations: staged diff (HEAD vs index, the `--cached`
equivalent) and committed diff (one commit vs its parent, for post-commit changelog generation).

```swift
public struct FileDelta {
    public enum Status {
        case added
        case modified
        case deleted
        case renamed(oldPath: String, similarityScore: UInt16)
        case copied(oldPath: String, similarityScore: UInt16)
    }

    public let status: Status
    public let newPath: String             // canonical path, always present
    public let oldPath: String?            // only meaningful for renamed/deleted
}

extension Repository {

    /// Equivalent to `git diff --cached --name-status -M`.
    /// Diffs HEAD tree against the index with rename detection enabled.
    /// On an unborn HEAD (empty repo with no commits), diffs the empty
    /// tree against the index so newly staged files in a fresh repo
    /// show up correctly as added rather than blowing up.
    public func stagedDiff(
        similarityThreshold: UInt16 = 50
    ) async throws -> [FileDelta]

    /// Diff a specific commit against its first parent.
    /// Used for post-commit changelog generation — pass the SHA
    /// retrieved from Working Copy's log callback after commit.
    /// Throws if the SHA doesn't resolve or the commit has no parent
    /// (initial commit edge case — handle at the call site).
    public func diff(
        forCommit sha: String,
        similarityThreshold: UInt16 = 50
    ) async throws -> [FileDelta]
}
```

`similarityThreshold` maps directly to libgit2's `git_diff_find_options.rename_threshold`. Default
50 matches git's default. Archivist can pass a different value if needed.

The `renamed` case carries the similarity score so Archivist's suspicion logic can use it — a 51%
match is more suspicious than a 95% match and the UI can surface that.

---

### Staging

```swift
extension Repository {

    /// Stage a file at the given repo-relative path.
    /// Equivalent to `git add <path>`.
    /// Throws stagingFailed if the path doesn't exist or
    /// the index can't be written.
    public func stage(path: String) throws

    /// Stage multiple files in a single index write.
    /// Prefer this over calling stage() in a loop — one index
    /// flush instead of N.
    public func stage(paths: [String]) throws

    /// Stage all modified and untracked files under a directory.
    /// Equivalent to `git add <directory>`.
    public func stageDirectory(path: String) throws
}
```

All staging functions write the index to disk before returning. libgit2 lets you batch index
modifications before writing — `stage(paths:)` takes advantage of that. Calling `stage(path:)` in a
loop is wasteful; the batched version exists so callers don't have to think about it.

---

### Commit

```swift
public struct CommitResult {
    public let sha: String            // full 40-char SHA
    public let shortSha: String       // first 7 chars, for display
    public let message: String
    public let timestamp: Date
}

extension Repository {

    /// Commit staged changes with the given message.
    /// Equivalent to `git commit -m <message>`.
    /// Returns a CommitResult so the caller has the SHA immediately
    /// without a separate log lookup.
    /// Throws commitFailed if the index is empty (nothing staged)
    /// or if the commit can't be written.
    public func commit(message: String) throws -> CommitResult
}
```

Returning the SHA from `commit()` directly means no follow-up `log` call is needed for the
post-commit backfill. This is cleaner than the two-step Working Copy approach and is why handling
commits via DeleGit rather than Working Copy's URL scheme is worth it for Archivist's specific
flow.

That said — the app can still delegate commits to Working Copy if the user prefers Working Copy's
commit UI. In that case, use Working Copy's `commit` URL scheme command and then call `log(limit: 1)`
via Working Copy's URL scheme to get the SHA. Both paths work. DeleGit's `commit()` is for when
your app owns the commit flow.

---

### Blob

```swift
extension Repository {

    /// Read the content of a file at a specific commit SHA.
    /// Used for content-similarity rename detection — fetching
    /// the old content of a deleted file to compare against
    /// a candidate added file.
    /// Throws blobNotFound if the path didn't exist at that ref.
    public func blob(
        at path: String,
        ref: String
    ) async throws -> String

    /// Read the content of a file from the index (staged version).
    /// Used when comparing staged additions against HEAD deletions
    /// for rename detection.
    public func blobFromIndex(at path: String) async throws -> String
}
```

Both return `String` assuming UTF-8, which is correct for markdown files. A `Data` variant can be
added later if binary file support ever becomes relevant — not a concern for Archivist.

---

## Integration with Archivist Mobile — Working Copy Boundary

```
┌─────────────────────────────────────┐
│           Archivist Mobile          │
│                                     │
│  ┌──────────────┐  ┌─────────────┐  │
│  │ DeleGit      │  │  WC Client  │  │
│  │              │  │             │  │
│  │ stagedDiff   │  │ push        │  │
│  │ findRenames  │  │ pull        │  │
│  │ blobAt       │  │ fetch       │  │
│  │ stage        │  │ clone       │  │
│  │ commit ──────┼──┼─► SHA       │  │
│  └──────────────┘  └─────────────┘  │
│                                     │
│  ┌──────────────────────────────┐   │
│  │     Archivist Core (Swift)   │   │
│  │                              │   │
│  │  GitChanges  ◄── stagedDiff  │   │
│  │  rename detection            │   │
│  │  frontmatter manipulation    │   │
│  │  changelog generation        │   │
│  └──────────────────────────────┘   │
└─────────────────────────────────────┘
```

`WCClient` is a thin x-callback-url wrapper — one struct, async methods for each Working Copy
command, continuation-based bridging of the URL scheme callbacks into Swift async/await. Its public
interface mirrors DeleGit's style so call sites don't feel like they're switching paradigms.

---

## Post-Commit Backfill Flow

```
user triggers commit
        │
        ▼
DeleGit.commit(message:)
        │
        ▼
CommitResult { sha: "abc123..." }
        │
        ▼
Archivist writes sha into changelog frontmatter
renames CHANGELOG-YYYY-MM-DD.md
        → CHANGELOG-YYYY-MM-DD-abc123.md
        │
        ▼
DeleGit.stage(path: renamedChangelog)
        │
        ▼
WCClient.push()          ← optional, user's call
```

The seal step happens in the same app session immediately after commit, driven by the SHA from
`CommitResult`. No hook required. No second commit needed for the seal if the changelog was staged
before the commit — the sequence matters and the UI needs to enforce it:

1. Generate changelog
2. Stage changelog
3. Commit
4. Seal in memory (rename file)
5. Stage renamed file
6. Renamed file goes into the next commit naturally

---

## What This Is Not

- **Not a full git client.** Branch management, merge, stash, rebase — none of that is here.
- **Not a replacement for Working Copy.** Remote ops stay there.
- **Not a general-purpose library yet.** It will become one if you build more apps. It isn't one
  now and shouldn't pretend to be.

---

## v0.2 Candidates

Things that are clearly coming but don't belong in v0.1. Add them when an app needs them, not before.

| Feature | Function signature | Needed for |
|---|---|---|
| Commit history | `log(limit:path:)` | Changelog history view |
| Working tree status | `status()` | File browser UI |
| Branch listing | `branch.list()` | Branch management |
| Branch create/switch | `branch.create(_:from:)`, `switch(to:)` | Branch management |
| Arbitrary two-ref diff | `diff(from:to:)` | Branch comparison |

---

## Implementation Notes

### libgit2 function mapping

| DeleGit | libgit2 |
|---|---|
| `Repository.open(at:)` | `git_repository_open` |
| `stagedDiff(similarityThreshold:)` | `git_diff_tree_to_index` + `git_diff_find_similar` |
| `diff(forCommit:similarityThreshold:)` | `git_diff_tree_to_tree` + `git_diff_find_similar` |
| `stage(path:)` | `git_index_add_bypath` + `git_index_write` |
| `stage(paths:)` | `git_index_add_bypath` × N + `git_index_write` |
| `stageDirectory(path:)` | `git_index_add_all` + `git_index_write` |
| `commit(message:)` | `git_commit_create` |
| `blob(at:ref:)` | `git_revparse_single` + `git_blob_rawcontent` |
| `blobFromIndex(at:)` | `git_index_get_bypath` + `git_blob_rawcontent` |

### Rename detection

`git_diff_find_similar` is called after the initial diff with `GIT_DIFF_FIND_RENAMES` set in the
flags and `rename_threshold` set to `similarityThreshold`. This is the libgit2 equivalent of
`git -M`. The resulting deltas with status `GIT_DELTA_RENAMED` map directly to `FileDelta.Status.renamed`.

### Unborn HEAD

`stagedDiff` must handle repos with no commits. Call `git_repository_head_unborn` before attempting
to resolve HEAD to a tree. If unborn, pass `NULL` as the old tree to `git_diff_tree_to_index` —
libgit2 treats a NULL tree as the empty tree, which is correct behaviour for a first-commit diff.

### Memory management

Every libgit2 type that requires an explicit free has a corresponding `defer` block at the call site
inside the wrapper. Nothing escapes without being freed. The Swift type is constructed from the
libgit2 data before the defer runs. This pattern is mechanical and must be followed without
exception — libgit2 does not manage its own memory and neither does ARC.