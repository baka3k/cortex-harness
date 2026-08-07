# Phase 02: Run-scoped diagnostics and CLI integration

## Context

The C/C++ analyzer already supports `--parse-errors-path`, but normal
`dev sync code` execution goes through `incremental_sync.py` and does not provide
a durable per-run parser artifact or return its location in the sync summary.

## Requirements

- Create one run-scoped parser-quality artifact directory per sync invocation.
- Preserve direct-analyzer CLI compatibility.
- Report explicit `ERROR`, `MISSING`, encoding, grammar retry, fallback, and
  quality-tier counts separately.
- Return artifact paths and aggregates to `dev.py` for concise plain-text output.
- Use atomic writes, normalized relative paths, size caps, and restrictive access.

## Architecture

`dev.py` forwards a validated `off|report|repair` policy to incremental sync.
Incremental sync owns artifact paths beside its existing summary, passes the
appropriate report path to C/C++, and merges analyzer aggregates into the child
summary. The analyzer writes the versioned Phase 01 schema atomically.

## Related files

- `cortex_harness/dev.py`
- `code-tiny/tools/sync/incremental_sync.py`
- `code-tiny/tools/cplus/cplus_analyzer.py`
- `tests/test_incremental_sync_parse_quality.py` (new)
- `tests/test_dev_sync_reliability.py`

## Implementation steps

1. Add root CLI policy and validated budget options with `report` as the safe
   default and `repair` opt-in.
2. Make `report` and `repair` pass the analyzer's compile-database-bootstrap
   disable flag so diagnostics never configure or execute the target repository;
   consume only a validated existing or harness-generated synthetic database.
3. Add deterministic run-scoped artifact path construction using existing cache
   and scan-scope helpers.
4. Extend `_build_analyzer_cmd` with capability-aware diagnostic arguments; do
   not pass unsupported flags to other analyzers.
5. Migrate C/C++ report output to the common schema while accepting the old
   `--parse-errors-path` alias.
6. Atomically write capped detail plus aggregates; keep source snippets opt-in.
7. Add artifact path and aggregate fields to incremental and root sync summaries.
8. Render one concise CLI summary with clear file-level/node-level labels.

## Todo

- [x] Add CLI policy and validated budget plumbing.
- [x] Wire C/C++ report generation through normal sync.
- [x] Extend child/root summaries with artifact metadata.
- [x] Add atomic-write, cap, privacy, and compatibility tests.
- [x] Verify dry-run shows the intended arguments without writing artifacts.

## Risks

An unbounded report can become a second ingestion bottleneck. Cap record counts
and bytes, retain complete aggregates, and state explicitly when detail is
truncated.

## Success criteria

Normal and `all` sync commands print an artifact path; the artifact reconciles
exactly with the CLI summary; old direct CLI use still works; no unsupported
analyzer flag, absolute path, or raw source is emitted by default.
