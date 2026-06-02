---
name: ts-backend-analyzer-missing-cli-args
description: Fix ts_backend_analyzer.py missing --commit-sha-before/after and message-scan args
source: auto-skill
extracted_at: '2026-06-02T03:00:06.829Z'
---

# Fixing `ts_backend_analyzer.py` Missing CLI Arguments

## Problem

When `dev sync code` runs on a TypeScript project detected as "backend" (or monorepo with both frontend/backend), it uses `ts_backend_analyzer.py`. However, this analyzer is missing CLI arguments that `ts_analyzer.py` (frontend) supports:

- `--commit-sha-before`
- `--commit-sha-after`
- `--message-output-dir`
- `--message-qdrant-collection`

This causes incremental sync to fail with `error: unrecognized arguments`.

## Root Cause

In `code-tiny/tools/sync/incremental_sync.py`, the `_build_analyzer_cmd()` function passes these args to ALL analyzers:

```python
# Lines ~480-490 in _build_analyzer_cmd
if message_scan_enabled:
    cmd.append("--enable-message-scan")
    if message_output_dir:
        cmd.extend(["--message-output-dir", message_output_dir])
    if message_qdrant_collection:
        cmd.extend(["--message-qdrant-collection", message_qdrant_collection])
```

And `incremental_sync.py` calls analyzers with `--commit-sha-before` and `--commit-sha-after` internally for impact analysis.

## Solution

Add the missing arguments to `ts_backend_analyzer.py` `parse_args()` function around line 2099:

```python
# Add these to ts_backend_analyzer.py parse_args()
p.add_argument("--commit-sha-before", dest="commit_sha_before", default=None,
               help="(reserved) Commit SHA before — not used by backend analyzer.")
p.add_argument("--commit-sha-after", dest="commit_sha_after", default=None,
               help="(reserved) Commit SHA after — not used by backend analyzer.")
```

For `message-*` args, they already exist in `parse_args()` (see lines 2096-2098), but the parsing fails before reaching it.

## Alternative Workaround

For monorepos with frontend in subfolder (e.g., `/project/frontend`), add the subfolder as a separate source in config:

```json
"code": {
  "source": {
    "folder": [
      "",           // root (backend)
      "frontend"    // frontend subfolder - analyzed separately
    ]
  }
}
```

This way each folder is analyzed with its appropriate analyzer.