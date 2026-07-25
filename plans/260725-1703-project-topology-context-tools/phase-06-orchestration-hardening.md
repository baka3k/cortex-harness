# Phase 06: Incremental Integration, Provider Parity, and Hardening

## Context

Pure parsers and query services are insufficient unless normal `dev sync code`
runs invoke the topology overlay, incremental deletes are safe, provider schema
is installed, and documentation reflects actual support.

## Requirements

- Integrate topology/config candidates without changing primary ownership.
- Handle add/change/delete/rename and deletion-only runs.
- Preserve existing sync state, locks, module-root, and submodule behavior.
- Validate provider/schema parity and security/performance boundaries.
- Publish truthful documentation and acceptance evidence.

## Architecture

Register `project_topology` as a non-vector overlay with deterministic order
after prerequisite primary/framework analyzers. It receives the normalized
global changed/deleted set plus prior topology state needed for deletion-only
routing.

Topology ownership is isolated from canonical language/framework ownership.
Scoped cleanup removes only topology-owned labels/edges for affected descriptor
and module scopes.

## Related Files

- `code-tiny/tools/sync/incremental_sync.py`
- Existing overlay configuration/selection helpers
- `cortex_harness/dev.py`
- `code-tiny/tools/sync/owner_manifest.py` if additive overlay ownership
  metadata is needed
- `code-tiny/scripts/setup_constraints.py`
- Provider driver/schema tests
- `tests/test_incremental_sync_framework_overlays.py`
- New `tests/test_incremental_sync_project_topology.py`
- New `tests/test_project_topology_security.py`
- New `tests/test_project_topology_performance.py`
- `code-tiny/README.md`
- `code-tiny/mcp/Readme.md`
- `docs/MCP_CAPABILITY_ACCEPTANCE_MATRIX.md`

## Implementation Steps

1. Add topology overlay registration, cheap descriptor candidate detection,
   prerequisite/order metadata, and no-vector behavior.
2. Extend source candidate allowlists for requested descriptor/protobuf files
   without routing them as exclusive primary languages.
3. Pass full/incremental changed/deleted/renamed manifests and prior module
   evidence to the topology analyzer.
4. Implement deletion-safe scope recomputation:
   - descriptor deletion;
   - module rename/move;
   - parent settings/POM change affecting children;
   - dependency target rename;
   - source file movement across modules.
5. Extend sync summaries with topology modules/descriptors/dependencies,
   diagnostics, skipped dynamic constructs, duration, and coverage.
6. Install additive constraints/indexes and capability schema evidence for new
   labels/properties/relationships.
7. Run XML/config hardening:
   - entity/DOCTYPE safety;
   - path containment and symlink behavior;
   - file size/depth/node-count limits;
   - secret-like value redaction;
   - no command/build-script execution.
8. Measure full and no-change incremental fixture runs. Cache descriptor digests
   and avoid reparsing unaffected module subtrees.
9. Run focused regression suites, Python compilation, diff checks, recording
   provider tests, live FalkorDB acceptance, and live Neo4j parity when the
   blocking migration permits it.
10. Update READMEs, tool catalog examples, capability matrix, graph schema
    documentation, limitations, and resync guidance.

## Todo

- [ ] Normal sync invokes topology as a non-exclusive overlay.
- [ ] Add/change/delete/rename cases are fixture-backed.
- [ ] Topology cleanup cannot delete canonical/framework facts.
- [ ] Schema/capability discovery reflects new facts.
- [ ] Security and resource bounds are tested.
- [ ] Provider and regression evidence is recorded.
- [ ] Documentation matches observed support.

## Risks

- Settings/POM changes can affect many modules. Dependency-aware invalidation
  must deliberately widen scope while remaining bounded.
- Deleting the last descriptor can change module identity. Recompute the
  containing subtree and tombstone only facts owned by prior topology state.
- Full monorepo descriptor discovery can be expensive. Use allowlisted filenames,
  normalized repository scopes, digest caches, and no-change short-circuiting.
- Live Neo4j may remain unavailable. Do not mark provider parity complete without
  execution evidence.

## Success Criteria

- Full and incremental sync fixtures produce the same final topology.
- No-change incremental runs invoke no topology parser subprocess or perform a
  bounded cache-only check according to existing orchestration conventions.
- Descriptor/module deletions remove only owned topology facts.
- Security fixtures cannot trigger external entity access, command execution,
  path escape, or secret-value output.
- Focused existing and new tests, compilation, and diff checks pass.
- Live provider results and any environment exclusions are documented in the
  validation report.

