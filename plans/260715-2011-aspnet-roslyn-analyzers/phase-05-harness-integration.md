# Phase 05: Integrate Graph, Sync, CLI, and MCP

## Context

After both tools produce deterministic normalized results, integrate them as detector-gated overlays across the provider-neutral graph, incremental orchestrator, root CLI, and unified MCP. Shared registries must be changed once and remain compatible with active parser plans.

## Requirements

- Register both with prerequisite parser `csharp`; do not modify owner manifests.
- Preserve explicit parser selection and detector-gate auto mode per module.
- Route changed/deleted candidates correctly and run C# before overlay writes.
- Write staged generation-, project-, and module-scoped facts with no orphan relationships.
- Expose names/aliases, labels, properties, and relationships through unified MCP without a dedicated ASP.NET server.

## Integration Map

| Surface | Planned change |
| --- | --- |
| Incremental registry | Add both `FrameworkAnalyzerConfig` entries, prerequisites/order, candidate extensions, detector routing, and strong deleted candidates. |
| Root CLI | Add both to `FRAMEWORK_ANALYZERS`; keep `LANG_EXTENSIONS["csharp"]` unchanged. |
| Graph | Batch nodes/edges, stage/publish generations, and clean only overlay-owned facts. |
| Framework registry | Add separate profiles/aliases with unified labels, relationships, properties, and generation policy. |
| Unified MCP | Route aliases, update discovery/instructions, and verify search/flow/endpoint/impact services. |
| Docs/tests | Update supported tools and additive registry/routing/provider/CLI expectations. |

## Related Files

- `code-tiny/tools/sync/incremental_sync.py`
- `cortex_harness/dev.py`
- `code-tiny/tools/graph/writer/`
- `code-tiny/mcp/framework_registry.py`
- `code-tiny/mcp/unified_mcp.py`
- `code-tiny/mcp/fastmcp_server.py`
- `code-tiny/mcp/cplus/cplus_mcp.py`
- `code-tiny/mcp/services/`
- `code-tiny/scripts/setup_constraints.py`
- Common analyzer registry plus focused sync/MCP/provider tests

## Implementation Steps

1. Implement graph adapters with allowlisted labels/relationships, C# anchors, nodes-before-edges batching, and staged generation publish/cleanup.
2. Test unified facts, scope, framework distinction, active generation, redaction, and no orphan edges.
3. Register both overlays with `(csharp,)` prerequisites, stable order, and no independent vector ownership unless Phase 01 approves it.
4. Add candidate extensions/project metadata and detector routing for code, config, Razor/Web Forms, resources, and deleted paths.
5. Prove full/selected/changed/impacted/deleted behavior for Core-only, Framework-only, mixed, ambiguous, and unrelated C# repositories.
6. Add both to the root CLI framework registry and test discovery/status/auto/explicit selection without changing ownership.
7. Add distinct non-ambiguous query profiles/aliases with unified labels, relationships, properties, and generation policy.
8. Update unified routing/discovery and test `list_parsers`, `activate_project`, search, subgraphs/paths, endpoint chains, workflows, and impact.
9. Inspect MCP services/schema setup; change only surfaces required by the verified contract.
10. Update supported-tool, sync, MCP, aliases, prerequisites, limitations, and troubleshooting docs.
11. Run common regressions and merge additive expectations from Flutter/Perl work.

## Todo

- [ ] Staged graph writes and cleanup pass provider-neutral tests.
- [ ] Sync and root registries agree.
- [ ] Detector-gated changed/deleted routing passes mixed tests.
- [ ] MCP aliases, labels, relationships, and services pass.
- [ ] Documentation and common regressions are updated.

## Risks

- Two overlays can duplicate work or clean the other's generation.
- MCP services may assume Java/Spring/Servlet labels/properties.
- Active plans may edit the same registries and parser expectations.

## Success Criteria

- Shared sync runs C# first and only detected ASP.NET overlays afterward.
- Deleted artifacts remove stale ASP.NET facts without deleting canonical C# or the other overlay.
- Unified MCP discovers and queries both through the existing backend.

