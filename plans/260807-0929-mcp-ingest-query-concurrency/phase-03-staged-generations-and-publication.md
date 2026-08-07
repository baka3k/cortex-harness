# Phase 03: Staged graph/vector generations and atomic publication

## Context

MCP cannot remain available if ingestion mutates the active graph and vector
stores in place. The writer must build a complete pair outside the active
generation, validate it, and publish one manifest.

## Requirements

- Build graph and vector data in isolated generation paths that each satisfy
  the existing local-storage ownership rules.
- Validate cross-store identity and representative retrieval before publish.
- Atomically publish one active manifest and retain a rollback generation.
- Retire old generations only after in-flight references drain.

## Architecture

Use generation-aware path resolution in `cortex_harness/storage/layout.py`
and `generation.py`. The graph factory and `LocalQdrantStore` receive explicit
staging paths. Readers resolve the active manifest once per request and pin
the resulting pair.

## Related files

- New: `cortex_harness/storage/generation.py`.
- Update: `cortex_harness/storage/layout.py`, `config.py`, `migration.py`,
  graph factory/driver, Qdrant adapter, and setup/validation helpers.
- Tests: manifest recovery, validation failure, partial staging cleanup,
  generation swap with in-flight readers, restart recovery, and rollback.

## Implementation steps

1. Allocate a staging generation with unique, owner-scoped graph/vector paths.
2. Run existing parse/embed/write flows against staging-only adapters.
3. Validate graph schema/counts, vector collections/counts, representative
   query results, stable IDs, and source revision.
4. Write and fsync the new manifest, atomically replace the active manifest,
   and expose the publication event.
5. Hold the publication mutex only for the final state recheck and
   temp-write/fsync/rename. Do not hold scheduler/refcount/client-cache locks
   during storage validation, close, or cleanup.
6. Drain references on the previous generation, retain the configured rollback
   set, and clean only unreferenced failed/retired generations.
7. Check estimated active+staging/rollback disk footprint and safety headroom
   before build; reject early with required/free-byte diagnostics.
8. Recover deterministically after a process crash during build, publish,
   graceful drain, or forced shutdown.

## Risks

Atomic manifest replacement does not make two databases transactional. The
manifest must remain the only pair-selection boundary, and validation plus
recovery must prove that an incomplete pair is never selected.

## Success criteria

A failed or cancelled staging build leaves generation `N` queryable; a
successful build swaps both stores as one visible generation; an in-flight
query can finish on `N` after `N+1` is published.
