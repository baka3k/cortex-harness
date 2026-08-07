---
type: repository evidence bundle
date: 2026-08-07
---

# Repository evidence: toolchain reliability hardening

## Coverage

- `mind_mcp`: unavailable in this session.
- `graph_mcp.semantic_search`: unavailable in this session.
- Serena: used for symbol bodies, call paths, and structural searches.
- Native exact search: used only for plan inventory, frontmatter, and precise
  line excerpts after semantic discovery.
- External research: skipped; the plan is based on local implementation and
  the reproduced local FalkorDB workload.

## Findings

- Deterministic child failures are retried broadly — code —
  `cortex_harness/dev.py::_run_with_retry` — high confidence.
  Evidence: all nonzero codes except an optional set are retried up to three
  times; `sync_code` marks only exit code `2` non-retryable.

- Child traceback output is merged into normal progress — code —
  `code-tiny/tools/sync/incremental_sync.py::_run` — high confidence.
  Evidence: stderr is redirected to stdout, every line is printed, and a
  nonzero child becomes `CalledProcessError` with only a bounded output tail.

- Cross-process failure semantics are lossy — code —
  `code-tiny/tools/sync/incremental_sync.py::_run_incremental` — high confidence.
  Evidence: a broad exception handler writes `str(exc)`, marks dirty, and
  returns `1`; it does not preserve typed phase, retryability, or endpoint data.

- Parse evidence policy is produced but not enforced — code —
  `cplus_analyzer.py:attach_compact_quality_provenance` and
  `parse_recovery.py` — high confidence.
  Evidence: searches found producers of `strong_relations_allowed`; no consumer
  was found before relationship construction/publication.

- Relationship integrity is detected after mutation — code —
  `LanguageCodeWriter.write_relations_typed` — high confidence.
  Evidence: the writer executes the upsert, reads `count`, then raises a generic
  `RuntimeError` when it differs from the input batch length.

- The incident is one propagated exception, not many independent errors —
  runtime evidence — high confidence.
  Evidence: `write_batch -> write_batches -> write_relations_typed -> write_all
  -> C++ buffer -> analyzer main -> incremental child` is one call chain.

- The reproduced first buffer contains malformed function identities — runtime
  evidence — high confidence.
  Evidence: cached payloads for the first 500 files contained function names
  with tabs/newlines and fragments such as `#ifdef` and comment delimiters;
  some affected files were classified `clean` or `recovered`.

- Count acknowledgements do not prove persisted identities in the reproduced
  FalkorDB workload — runtime evidence — high confidence.
  Evidence: isolated replays reported full processed counts while subsequent
  identity readback found missing `Function` nodes. Writer-only deduplication,
  splitting, and immediate retry did not produce a verified end-to-end fix.

## Relationships

```text
dev sync code
  -> cortex_harness.dev.sync_code
  -> _run_with_retry(incremental_sync.py subprocess)
  -> incremental_sync._run(analyzer subprocess)
  -> cplus_analyzer.build_call_graph
  -> _flush_write_buffers
  -> LanguageCodeWriter.write_all
  -> write_relations_typed
  -> write_batches
  -> RuntimeError
```

Active-plan ownership:

- graph write path: `260807-1202-graph-ingest-write-path-hardening`;
- parser quality/recovery: `260807-1329-parser-quality-recovery`;
- Pro*C extraction: `260804-1640-port-proc-cplus-to-code-tiny`;
- store ownership/generation publication:
  `260807-0929-mcp-ingest-query-concurrency`.

## Contradictions

- File-level quality may be `clean`/`recovered` while extracted symbol names are
  structurally implausible. File-level parser quality is therefore necessary
  but insufficient for publication safety.
- A driver mutation count may equal the requested row count while identity
  readback disagrees. Count equality is therefore necessary diagnostic data,
  not sufficient proof of effect.
- The CLI advertises reliable incremental sync and writes useful summaries,
  but raw child tracebacks and generic exit code `1` remain the primary failure
  interface.

## Inferences

- A stable fix must validate records and references before mutation and verify
  effects before publication; either boundary alone leaves a silent-loss path.
- The integrity guard should remain fail-closed. Stability comes from typed
  handling, quarantine, staging, and recovery—not suppressing the exception.
- Reliability certification must be cross-analyzer and cross-provider because
  the shared orchestrator and writer amplify analyzer-specific defects.

## Gaps

- No authoritative repository `docs/development-rules.md` exists at the path
  referenced by the planning skill.
- The exact FalkorDB engine-level cause of the session-specific under-write is
  not established; the plan therefore requires provider conformance and
  deterministic readback rather than assuming an engine behavior.
- Other analyzers have not yet been inventoried against the new payload
  envelope; Phase 01 owns that bounded inventory.
