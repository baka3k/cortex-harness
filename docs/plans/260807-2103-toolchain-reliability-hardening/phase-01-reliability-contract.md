# Phase 01: Reliability contract and incident corpus

## Context

The current layers expose incompatible notions of success: analyzers return
process exit codes, incremental sync stores a string error, graph writers return
counts, and the outer CLI retries most nonzero results. Before behavior changes,
the project needs one versioned contract and a reproducible corpus that captures
the real failure modes.

## Requirements

- Define stable run states, outcomes, failure classes, phase results, exit codes,
  retry semantics, and artifact references.
- Preserve backward-compatible CLI exit behavior during observe-only rollout.
- Inventory every analyzer and storage writer against the contract.
- Capture minimized, privacy-reviewed incident fixtures without copying the
  customer repository or absolute host paths.
- Establish cold/warm performance, memory, disk, cardinality, and failure-output
  baselines before implementation.

## Architecture

Create one JSON-serializable result schema usable in-process and across
subprocesses. The schema is append-only within a major version, has bounded
detail fields, and maps to stable exit codes only at the orchestrator boundary.
Internal exceptions remain implementation details.

The incident corpus must include:

- malformed C/C++/Pro*C symbol names and IDs;
- duplicate declarations/definitions with conflicting properties;
- required and optional missing endpoints;
- FalkorDB count/readback disagreement;
- timeout before submission, timeout after submission, and process kill;
- corrupted/stale cache and incompatible journal fingerprints;
- CP932/Unicode paths and source-changed-during-scan;
- disk-full/artifact-write failure and lock contention.

## Related Files

- New shared reliability/result contract under `code-tiny/tools/common/`.
- `cortex_harness/dev.py`
- `code-tiny/tools/sync/incremental_sync.py`
- analyzer registry/configuration and graph/vector writer result models
- root and `code-tiny/tests/` fixture conventions

## Implementation Steps

1. Inventory analyzer entry points, payload shapes, exit behavior, caches,
   graph/vector paths, and direct writer bypasses.
2. Freeze names and semantics for run states, outcomes, failure classes,
   phase results, retryability, and exit-code mapping.
3. Define JSON Schema or equivalent deterministic validation plus schema
   version/fingerprint rules.
4. Add result serialization/deserialization and redaction/size-limit tests.
5. Capture minimized fixtures and expected outcomes from the motivating run.
6. Record current output, retries, runtime, RSS, disk, and final graph/vector
   counts for comparison.
7. Publish an ownership matrix showing which active plan supplies each
   lower-level mechanism.

## Todo

- [ ] Reliability/result schema is reviewed and versioned.
- [ ] Failure-to-exit mapping distinguishes terminal, retryable, ambiguous, and
      internal-defect outcomes.
- [ ] Analyzer/writer inventory has no unclassified mutation path.
- [ ] Incident corpus reproduces the current failure on temporary storage.
- [ ] Baseline report records correctness and resource metrics.
- [ ] Observe-only compatibility strategy is documented.

## Risks

- A large generic schema may become meaningless. Keep the core small and use
  typed bounded details per failure class.
- Captured fixtures may leak source. Store normalized/minimized records only and
  test artifact privacy.
- Exit-code changes can break automation. Dual-write result artifacts before
  changing default CLI behavior.

## Success Criteria

- Every current analyzer and writer maps to one declared contract adapter.
- The motivating incident is reproducible without the customer repository.
- The same failure has one stable code and retry decision across all layers.
- Baseline evidence is sufficient to detect correctness or performance
  regression in later phases.
