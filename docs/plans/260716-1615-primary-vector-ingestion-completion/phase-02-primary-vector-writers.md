# Phase 02: Implement Missing Primary Vector Writers

## Context

Rust, Go, Swift, Perl, and Dart already produce stable normalized graph facts, but their entry points stop before vector persistence. COBOL demonstrates the required staged order and failure handling.

## Requirements

- Reuse one focused transport implementation for validation, embedding, cleanup, batching, retries, and upsert.
- Keep document mapping close to each analyzer's normalized model.
- Avoid importing one language analyzer from another.
- Preserve graph-only operation when Qdrant is not configured.

## Architecture

- Add a generic vector-sync module under `code-tiny/tools/common/`.
- Rust, Go, and Swift map their prepared canonical rows to vector documents.
- Perl maps bounded package/subroutine/comment/POD facts from `AnalysisResult`.
- Dart reuses the existing `qdrant_payloads` normalization path; Flutter overlay facts remain graph-only in this phase.
- COBOL remains unchanged except for shared regression expectations.

## Related Files

- `code-tiny/tools/common/` new vector module
- `code-tiny/tools/rust/rust_analyzer.py`
- `code-tiny/tools/go/go_analyzer.py`
- `code-tiny/tools/swift/swift_analyzer.py`
- `code-tiny/tools/perl/perl_analyzer.py`
- `code-tiny/tools/flutter/flutter_analyzer.py`
- `code-tiny/tools/flutter/normalizer.py`
- `code-tiny/tools/cobol/qdrant.py`

## Implementation Steps

1. Implement lazy vector dependencies, endpoint/collection validation, deterministic point IDs, bounded embedding, batch upsert, retry, and filtered cleanup.
2. Add parser-specific document mappers with stable IDs and project/language/file metadata.
3. Invoke vector sync only after successful analysis and staged graph persistence.
4. Return and print vector counts; distinguish graph, vector, and analysis failure exit codes.
5. Ensure incremental cleanup uses changed, impacted, and deleted paths before upsert.
6. Add fake-backend tests for mapping, redaction, idempotency, cleanup, batching, and failure propagation.

## Todo

- [x] Shared transport is implemented without language-model coupling.
- [x] Rust, Go, and Swift execute vector writes.
- [x] Perl executes its planned optional vector adapter.
- [x] Dart primary mode executes vector writes without duplicating Flutter overlay facts.
- [x] COBOL behavior remains unchanged and covered.

## Risks

- Canonical rows differ slightly across analyzers; normalize only the vector document boundary, not parser models.
- Embedding dependencies may be unavailable in graph-only environments; imports must remain lazy.

## Success Criteria

- Each missing primary analyzer invokes the vector adapter when URL and collection are configured.
- Stable inputs produce stable point IDs and payloads.
- A configured write failure returns non-zero and does not report success.
