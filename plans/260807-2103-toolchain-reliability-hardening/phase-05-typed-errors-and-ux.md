# Phase 05: Typed orchestration errors and operator UX

## Context

The motivating output appears to contain many errors because one exception is
printed through every Python frame and subprocess layer. The outer CLI then sees
only an exit code and may retry the same deterministic failure. Operators need
one concise result while engineers retain full diagnostics.

## Requirements

- Preserve typed outcome/failure records across analyzer and sync subprocesses.
- Classify retry before the outer CLI acts.
- Print no raw traceback for known failures in normal mode.
- Preserve full traceback, command, bounded output tail, environment metadata
  allowlist, and correlation IDs in debug artifacts.
- Provide status, doctor, resume, cancel, and artifact discovery commands.
- Keep automation-compatible JSON output and stable exit codes.
- Avoid duplicate progress lines and silent operations.

## Architecture

Each child atomically writes a versioned result artifact before exit. Parent
processes consume the artifact rather than reverse-engineering log text. Stdout
is reserved for structured progress/events; stderr is reserved for concise
human diagnostics. The outer CLI renders either human or JSON output from the
same model.

Retry table examples:

| Failure | Retry |
| --- | --- |
| Malformed record within policy | Continue with quarantine |
| Validation threshold exceeded | Terminal, no automatic retry |
| Required endpoint missing after preflight | Terminal, no blind retry |
| Lock/capacity transient | Bounded retry with `retry_after` |
| Timeout before submission | Retryable if budget remains |
| Timeout after submission | Ambiguous; reconcile first |
| Source changed | Restart from discovery with new run identity/policy |
| Internal defect | Terminal; debug artifact and issue fingerprint |

## Related Files

- `cortex_harness/dev.py::_run_with_retry`, `sync_code`, `sync_code_all`
- `code-tiny/tools/sync/incremental_sync.py::_run` and `_run_incremental`
- analyzer main/CLI wrappers
- MCP ingestion/status mapping after gateway integration
- summary/artifact rendering and lifecycle commands

## Implementation Steps

1. Add atomic child result path arguments/environment and result validation.
2. Change analyzer wrappers to catch known domain failures, write typed results,
   and suppress normal-mode tracebacks.
3. Preserve unknown exception tracebacks in bounded debug artifacts and assign
   an issue fingerprint.
4. Replace generic `CalledProcessError` interpretation with result-artifact
   consumption and fallback classification when a child dies before writing.
5. Make `_run_with_retry` use failure class, operation idempotency, retry budget,
   and reconciliation status; remove catch-all retry.
6. Implement concise human summary plus stable JSON output.
7. Add `dev sync code status|doctor|resume|cancel` or integrate equivalent
   commands with the StoreGateway lifecycle owner.
8. Add heartbeat, phase, global progress, quarantine count, queue state, and
   artifact path without duplicate logger/print streams.

## Todo

- [ ] Known failures produce no normal-mode traceback.
- [ ] Unknown defects preserve full debug evidence.
- [ ] Deterministic failures run once and return stable exit codes.
- [ ] Ambiguous failures reconcile before retry.
- [ ] Human and JSON renderers consume the same contract.
- [ ] Status/doctor remains responsive during ingestion.
- [ ] CLI tests cover nested child failures and missing/corrupt result artifacts.

## Risks

- Suppressing tracebacks can hide defects. Only known typed failures are
  suppressed; unknown exceptions always retain a debug artifact.
- Result artifact may be absent after hard crash. Parent synthesizes a typed
  `child_terminated` result from exit/signal/output tail and marks state
  ambiguous where necessary.
- Exit-code changes can break scripts. Roll out with documented mapping and
  compatibility metrics before removal.

## Success Criteria

- The motivating stack trace becomes one concise failure summary with stable
  code, phase, counts, artifact path, and safe action.
- Outer CLI never retries a terminal integrity/validation failure.
- Operators can inspect/resume/cancel without locating lock files or killing
  processes manually.
- Every displayed progress line identifies the current run and phase.
