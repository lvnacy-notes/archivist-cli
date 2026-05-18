---
class: spec
category:
  - feature
  - changelog
affiliations:
created: 2026-05-17
modified: 2026-05-18
version:
related:
  - "[[PLUGIN_SYSTEM_FEATURE_CHECKLIST]]"
  - "[[PLUGIN_SYSTEM]]"
tags:
---
Archivist supports per-project changelog plugins. The convention is simple and deliberate: **the file's existence is the registration**.

### Location

```
.archivist/
  config.yaml
  changelog.py       ← active plugin (loaded automatically)
  sample-changelog.py ← reference file (never loaded, always ignored)
```

### Discovery rules

- Archivist looks for `.archivist/changelog.py` on every `archivist changelog` invocation.
- If found, it loads the plugin and calls its `run(args)`. The built-in subcommand is bypassed entirely.
- If not found, routing proceeds normally to the built-in subcommand for the configured module type.
- **Explicit subcommands always bypass the plugin.** `archivist changelog library` runs the built-in library subcommand regardless of whether a plugin exists. The plugin is only active for bare `archivist changelog` invocations.
- `sample-changelog.py` is never loaded. Only `changelog.py` is recognized. This is intentional and exact.

### The contract

A plugin is a Python file that exposes one callable:

```python
def run(args: argparse.Namespace) -> None:
    ...
```

That function calls `run_changelog()` from `changelog_base` with builder callables. Everything else is up to the plugin.

### Library plugin API

The library module exposes four public functions for plugin composition:

```python
from archivist.commands.changelog.library import (
    analyse_catalog,   # post_changes hook — populates ctx.data
    build_frontmatter, # YAML frontmatter block
    build_body,        # full changelog body including sentinel
    print_summary,     # terminal summary after write
)
```

Do not import anything prefixed with `_` from the library module. Those are internal and will change without notice.

### Activation and deactivation

- **Activate:** rename `sample-changelog.py` → `changelog.py` and edit.
- **Deactivate:** delete `changelog.py` or rename it back. Instant revert, no config changes.
- **Test:** `archivist changelog --dry-run` runs the full pipeline including the plugin. The indicator line confirms which code path ran:

  ```
  → changelog plugin found: .archivist/changelog.py
  ```

### Extending to other commands

The plugin convention is designed to extend to other commands (`manifest`, `reclassify`, etc.) using identical discovery logic: Archivist looks for `.archivist/<command>.py`, loads it if present, falls back to built-in if not. This is not yet implemented for commands other than `changelog`.