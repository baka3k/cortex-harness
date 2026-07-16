# Phase 01: Freeze the Vector Contract and Coverage Matrix

## Context

The repository currently conflates "accepts Qdrant arguments" with "writes Qdrant points." Before implementation, encode the distinction so future analyzers cannot silently pass registry tests while omitting vector execution.

## Requirements

- Classify every registered primary analyzer and framework overlay as `writes_vectors`, `seeded_by`, or graph-only.
- Define one stable semantic-document contract shared by the missing primary analyzers.
- Preserve graphless and vectorless dry-run behavior.
- Make the audit executable in tests rather than relying on source-text inspection alone.

## Architecture

Use a small adapter boundary:

```text
analyzer result
  -> parser-owned document mapper
  -> VectorDocument{id, text, payload}
  -> shared Qdrant sync{validate, cleanup, embed, upsert}
```

Framework overlays remain separately classified because their semantic nodes may be reached through base-language vector seeds and graph traversal.

## Related Files

- `code-tiny/tools/sync/incremental_sync.py`
- `tests/test_common_analyzer_registry.py`
- `tests/test_aspnet_integration.py`
- New primary-vector contract tests

## Implementation Steps

1. Add a test-owned coverage matrix for all `ANALYZERS` and `FRAMEWORK_ANALYZERS` entries.
2. Define required vector document fields, deterministic identity rules, redaction boundaries, and maximum text behavior.
3. Define full and incremental cleanup semantics, including project/parser/root isolation.
4. Add negative tests proving that CLI arguments without a vector call do not satisfy the contract.
5. Record COBOL and mature analyzers as verified implementations; record graph-only overlays with their semantic seed parsers.

## Todo

- [x] Primary and overlay coverage matrix is complete.
- [x] Vector document and error contracts are testable.
- [x] Cross-scope cleanup invariants are frozen.
- [x] Existing graph-only behavior is explicitly represented.

## Risks

- A source-inspection test would be brittle; assert behavior through injected/fake vector backends and analyzer entry points.
- Treating overlays as missing primary writers would create duplicate collections and points.

## Success Criteria

- Every registered analyzer has an explicit vector strategy.
- Tests fail if a primary analyzer advertises vector capability but never invokes it.
- The contract supports all five missing primary implementations without parser-specific branching in the transport layer.
