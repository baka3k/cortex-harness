# Phase 03: Wire Orchestration and Configuration

## Context

`incremental_sync.py` already derives and passes per-parser collection names, but `cortex_harness/dev.py` does not pass the full configured embedding environment into the sync subprocess. Direct analyzer invocation and incremental orchestration must resolve the same effective settings.

## Requirements

- Propagate model, device, batch size, maximum text length, URL, cache, and collection settings consistently.
- Keep existing analyzer CLI compatibility.
- Preserve per-project/per-parser/root collection naming.
- Report vector intent and outcome in sync summaries.

## Architecture

- Extend `_run_with_retry()` with an optional subprocess environment.
- Use `_code_env_for_process(cfg)` when `dev sync code` launches `incremental_sync.py`.
- Let `incremental_sync.py` normalize optional embedding CLI inputs into common environment variables inherited by analyzers.
- Continue passing the derived collection explicitly to primary analyzers.
- Keep `qdrant_collection=None` for graph-only overlays unless Phase 04 activates a measured fallback.

## Related Files

- `cortex_harness/dev.py`
- `code-tiny/tools/sync/incremental_sync.py`
- `tests/test_dev_init_graph_provider.py`
- Incremental-sync command and summary tests

## Implementation Steps

1. Add optional environment support to the retry runner without changing existing callers.
2. Pass normalized code configuration to both interactive and `all` sync subprocesses.
3. Add incremental-sync options/environment mapping for embedding model, device, batch size, and max characters.
4. Record expected collection, vector capability, and vector result in primary parser summaries.
5. Keep framework summaries explicit about `writes_vectors=False` and `semantic_seed_collections`.
6. Add command-construction and environment-propagation tests for full and incremental modes.

## Todo

- [x] Root CLI configuration reaches analyzer subprocesses.
- [x] Primary collection naming remains unchanged.
- [x] Sync summaries expose vector strategy and result.
- [x] Graph-only overlay commands receive no unused collection.

## Risks

- Passing new flags to every analyzer can break heterogeneous CLIs; prefer environment normalization and targeted analyzer flags.
- Logging subprocess environments could expose secrets; tests and diagnostics must redact credentials.

## Success Criteria

- Direct and orchestrated runs resolve equivalent vector settings.
- Existing sync, retry, registry, and provider tests pass.
- A vector failure prevents clean-state promotion.
