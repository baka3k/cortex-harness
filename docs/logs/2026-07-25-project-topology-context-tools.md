# Project Topology and Context Tools — 2026-07-25

## Context

`code-tiny` could analyze language symbols and framework facts but lacked an executable contract for project modules, decisive descriptors, cross-module dependencies, public API surfaces, and normalized endpoints across its 22 primary analyzers and 12 framework overlays (`plans/260725-1703-project-topology-context-tools/plan.md:55`). The plan selected a non-exclusive topology overlay so build and configuration files can enrich existing analyzers without taking over primary source ownership (`plans/260725-1703-project-topology-context-tools/plan.md:64`).

## Change

- Added a bounded, static descriptor pipeline that never executes project builds, resolves canonical topology, and records diagnostics for scan limits and deleted descriptors (`code-tiny/tools/project_topology/pipeline.py:23`). The registry declares explicit parse depth and evidence coverage for every primary analyzer and framework overlay (`code-tiny/tools/project_topology/registry.py:266`, `code-tiny/tools/project_topology/registry.py:294`).
- Added provider-neutral, topology-owned graph writes for modules, descriptors, dependencies, gRPC endpoints, framework instances, and links to canonical public APIs and existing endpoints (`code-tiny/tools/graph/writer/project_topology_writer.py:13`, `code-tiny/tools/graph/writer/project_topology_writer.py:95`, `code-tiny/tools/graph/writer/project_topology_writer.py:124`). Java, Kotlin, and C/C++ now persist explicit source-level visibility or clearly marked inferred export evidence (`code-tiny/tools/java/java_analyzer.py:168`, `code-tiny/tools/kotlin/kotlin_analyzer.py:133`, `code-tiny/tools/cplus/cplus_analyzer.py:153`).
- Added six project-scoped, bounded MCP context tools behind shared capability routing (`code-tiny/mcp/unified_mcp.py:94`, `code-tiny/mcp/unified_mcp.py:1995`) and integrated descriptor-triggered topology recomputation into incremental sync without vector writes (`code-tiny/tools/sync/incremental_sync.py:118`, `code-tiny/tools/sync/incremental_sync.py:2015`).

## Impact

Risk level: **medium**. AI clients can retrieve module topology, public APIs, endpoints, special files, framework context, and bounded architecture summaries from the indexed graph instead of reconstructing them from low-level searches. The additive ownership boundary limits cleanup to topology-owned facts, while XML safety, symlink, size-limit, redaction, and explicit parse-depth contracts reduce ingestion risk (`tests/test_project_topology_security.py:15`, `tests/test_project_topology_acceptance_matrix.py:28`). Focused validation reported 92 passed tests and 72 passed subtests, plus compilation, lint, diff, and bundled driver checks (`plans/260725-1703-project-topology-context-tools/reports/implementation-report.md:25`). Live FalkorDB and Neo4j execution was not validated; cross-provider live parity remains an explicit environmental exclusion (`plans/260725-1703-project-topology-context-tools/reports/implementation-report.md:52`).

## Decision

Keep topology as a graph-only overlay, preserve specialized Android and framework facts, and add canonical module semantics without destructive label replacement. Static evidence was chosen over executing Gradle, Maven, Ant, CMake, Make, or compiled ABI inspection. Public API classification follows language rules, with C/C++ header inference opt-in, and identity-only descriptor support remains labeled `identity` until fixture-backed deeper semantics exist (`plans/260725-1703-project-topology-context-tools/plan.md:71`, `plans/260725-1703-project-topology-context-tools/plan.md:86`, `plans/260725-1703-project-topology-context-tools/plan.md:94`).

## References

- Plan: [Project Topology, Parser Coverage, and Context MCP Tools](../../plans/260725-1703-project-topology-context-tools/plan.md)
- Implementation report: [Project topology/context-tools implementation report](../../plans/260725-1703-project-topology-context-tools/reports/implementation-report.md)
- Executable coverage: `docs/PROJECT_TOPOLOGY_ACCEPTANCE_MATRIX.json`
- Acceptance assertions: `tests/test_project_topology_acceptance_matrix.py:28`, `tests/test_project_topology_acceptance_matrix.py:58`
