# Dependency & Impact Analysis Tools

<cite>
**Referenced Files in This Document**
- [code-tiny/mcp/services/graph_service.py](file://code-tiny/mcp/services/graph_service.py)
- [code-tiny/mcp/services/impact_service.py](file://code-tiny/mcp/services/impact_service.py)
- [code-tiny/mcp/services/symbol_service.py](file://code-tiny/mcp/services/symbol_service.py)
- [code-tiny/mcp/services/explore_service.py](file://code-tiny/mcp/services/explore_service.py)
- [code-tiny/mcp/services/workflow_service.py](file://code-tiny/mcp/services/workflow_service.py)
- [code-tiny/mcp/services/flow_reconstructor.py](file://code-tiny/mcp/services/flow_reconstructor.py)
- [code-tiny/mcp/unified_mcp.py](file://code-tiny/mcp/unified_mcp.py)
- [code-tiny/mcp/framework_registry.py](file://code-tiny/mcp/framework_registry.py)
- [code-tiny/mcp/tool_metadata.py](file://code-tiny/mcp/tool_metadata.py)
- [code-tiny/mcp/semantic_graph_expansion.py](file://code-tiny/mcp/semantic_graph_expansion.py)
- [code-tiny/tools/common/call_graph_builder.py](file://code-tiny/tools/common/call_graph_builder.py)
- [code-tiny/tools/common/graph_expander.py](file://code-tiny/tools/common/graph_expander.py)
- [code-tiny/tools/common/workflow_impact_scorer.py](file://code-tiny/tools/common/workflow_impact_scorer.py)
- [code-tino/tools/common/incremental_sync_state.py](file://code-tiny/tools/common/incremental_sync_state.py)
- [code-tiny/tools/common/incremental_cleanup.py](file://code-tiny/tools/common/incremental_cleanup.py)
- [code-tiny/tools/graph/core/base.py](file://code-tiny/tools/graph/core/base.py)
- [code-tiny/tools/graph/driver/falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)
- [code-tiny/tools/graph/driver/neo4j_driver.py](file://code-tiny/tools/graph/driver/neo4j_driver.py)
- [code-tiny/tools/graph/operations/function_ops.py](file://code-tiny/tools/graph/operations/function_ops.py)
- [code-tiny/tools/graph/operations/class_ops.py](file://code-tiny/tools/graph/operations/class_ops.py)
- [code-tiny/tools/graph/operations/package_ops.py](file://code-tiny/tools/graph/operations/package_ops.py)
- [code-tiny/tools/graph/operations/document_ops.py](file://code-tiny/tools/graph/operations/document_ops.py)
- [code-tiny/tools/graph/operations/infra_ops.py](file://code-tiny/tools/graph/operations/infra_ops.py)
- [code-tiny/tools/graph/operations/cross_edge_ops.py](file://code-tiny/tools/graph/operations/cross_edge_ops.py)
- [code-tiny/tools/graph/operations/namespace_ops.py](file://code-tiny/tools/graph/operations/namespace_ops.py)
- [code-tiny/tools/graph/operations/type_ops.py](file://code-tiny/tools/graph/operations/type_ops.py)
- [code-tiny/tools/sync/incremental_sync.py](file://code-tiny/tools/sync/incremental_sync.py)
- [code-tiny/tools/sync/message_scan.py](file://code-tiny/tools/sync/message_scan.py)
- [code-tiny/tools/sync/build_owner_manifests.py](file://code-tiny/tools/sync/build_owner_manifests.py)
- [code-tiny/tools/sync/dead_code_report.py](file://code-tiny/tools/sync/dead_code_report.py)
- [code-tiny/tools/sync/owner_manifest.py](file://code-tiny/tools/sync/owner_manifest.py)
- [code-tiny/tools/common/analyzer_cache.py](file://code-tiny/tools/common/analyzer_cache.py)
- [code-tiny/tools/common/harness_config.py](file://code-tiny/tools/common/harness_config.py)
- [code-tiny/tools/common/source_inventory.py](file://code-tiny/tools/common/source_inventory.py)
- [code-tiny/tools/common/git_diff.py](file://code-tiny/tools/common/git_diff.py)
- [code-tiny/tools/common/query_intent_classifier.py](file://code-tiny/tools/common/query_intent_classifier.py)
- [code-tiny/tools/common/retrieval_scorer.py](file://code-tiny/tools/common/retrieval_scorer.py)
- [code-tiny/tools/common/intelligent_retrieval.py](file://code-tiny/tools/common/intelligent_retrieval.py)
- [code-tiny/tools/common/result_packager.py](file://code-tiny/tools/common/result_packager.py)
- [code-tiny/tools/common/confidence_scorer.py](file://code-tiny/tools/common/confidence_scorer.py)
- [code-tiny/tools/common/bm25_ranker.py](file://code-tiny/tools/common/bm25_ranker.py)
- [code-tiny/tools/common/frontend_relationship_extractor.py](file://code-tiny/tools/common/frontend_relationship_extractor.py)
- [code-tiny/tools/common/api_match_engine.py](file://code-tiny/tools/common/api_match_engine.py)
- [code-tiny/tools/common/signal_normalizer.py](file://code-tiny/tools/common/signal_normalizer.py)
- [code-tiny/tools/common/url_normalizer.py](file://code-tiny/tools/common/url_normalizer.py)
- [code-tiny/tools/common/workflow_classifier.py](file://code-tiny/tools/common/workflow_classifier.py)
- [code-tiny/tools/common/llm_summary.py](file://code-tiny/tools/common/llm_summary.py)
- [code-tiny/tools/common/react_role_classifier.py](file://code-tiny/tools/common/react_role_classifier.py)
- [code-tiny/tools/common/semantic_inference.py](file://code-tiny/tools/common/semantic_inference.py)
- [code-tiny/tools/common/sync_scope.py](file://code-tiny/tools/common/sync_scope.py)
- [code-tiny/tools/common/cloc_stats.py](file://code-tiny/tools/common/cloc_stats.py)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Dependency Analysis](#dependency-analysis)
7. [Performance Considerations](#performance-considerations)
8. [Troubleshooting Guide](#troubleshooting-guide)
9. [Conclusion](#conclusion)
10. [Appendices](#appendices)

## Introduction
This document explains the dependency and impact analysis tools in Cortex Harness MCP. It focuses on how call graphs are constructed, dependencies are mapped, impacts are assessed, and changes propagate through codebases. You will learn:
- How to build and query call graphs across languages and frameworks
- How to map dependencies between modules, classes, functions, documents, and infrastructure
- How to assess impact and score risk for proposed changes
- How incremental analysis accelerates re-computation after diffs
- How to interpret results and make informed development decisions

## Project Structure
The dependency and impact capabilities are implemented as a layered system:
- MCP services expose high-level operations (graph queries, impact assessment, symbol lookups, workflow orchestration)
- Common tooling provides reusable algorithms (call graph builder, graph expander, impact scorer, retrieval scorers)
- Graph core and drivers provide storage and traversal abstractions
- Sync utilities support incremental updates and change detection

```mermaid
graph TB
subgraph "MCP Services"
U["unified_mcp.py"]
G["services/graph_service.py"]
I["services/impact_service.py"]
S["services/symbol_service.py"]
E["services/explore_service.py"]
W["services/workflow_service.py"]
F["services/flow_reconstructor.py"]
end
subgraph "Common Tools"
CGB["common/call_graph_builder.py"]
GE["common/graph_expander.py"]
WIS["common/workflow_impact_scorer.py"]
ISC["common/incremental_sync_state.py"]
IC["common/incremental_cleanup.py"]
AC["common/analyzer_cache.py"]
HC["common/harness_config.py"]
SI["common/source_inventory.py"]
GD["common/git_diff.py"]
QIC["common/query_intent_classifier.py"]
RS["common/retrieval_scorer.py"]
IR["common/intelligent_retrieval.py"]
RP["common/result_packager.py"]
CS["common/confidence_scorer.py"]
BM["common/bm25_ranker.py"]
FE["common/frontend_relationship_extractor.py"]
AME["common/api_match_engine.py"]
SN["common/signal_normalizer.py"]
UN["common/url_normalizer.py"]
WC["common/workflow_classifier.py"]
LLM["common/llm_summary.py"]
RRC["common/react_role_classifier.py"]
SEI["common/semantic_inference.py"]
SS["common/sync_scope.py"]
CL["common/cloc_stats.py"]
end
subgraph "Graph Core & Drivers"
GB["graph/core/base.py"]
FD["graph/driver/falkordb_driver.py"]
ND["graph/driver/neo4j_driver.py"]
FO["graph/operations/function_ops.py"]
CO["graph/operations/class_ops.py"]
PO["graph/operations/package_ops.py"]
DO["graph/operations/document_ops.py"]
IO["graph/operations/infra_ops.py"]
XE["graph/operations/cross_edge_ops.py"]
NO["graph/operations/namespace_ops.py"]
TO["graph/operations/type_ops.py"]
end
subgraph "Sync Utilities"
IS["sync/incremental_sync.py"]
MS["sync/message_scan.py"]
BOM["sync/build_owner_manifests.py"]
DCR["sync/dead_code_report.py"]
OM["sync/owner_manifest.py"]
end
U --> G
U --> I
U --> S
U --> E
U --> W
G --> CGB
G --> GE
G --> GB
G --> FO
G --> CO
G --> PO
G --> DO
G --> IO
G --> XE
G --> NO
G --> TO
I --> WIS
I --> RS
I --> CS
I --> BM
I --> RP
I --> QIC
I --> IR
I --> FE
I --> AME
I --> SN
I --> UN
I --> WC
I --> LLM
I --> RRC
I --> SEI
I --> SS
I --> CL
G --> FD
G --> ND
IS --> ISC
IS --> IC
IS --> MS
IS --> BOM
IS --> DCR
IS --> OM
G --> AC
G --> HC
G --> SI
G --> GD
```

**Diagram sources**
- [code-tiny/mcp/unified_mcp.py](file://code-tiny/mcp/unified_mcp.py)
- [code-tiny/mcp/services/graph_service.py](file://code-tiny/mcp/services/graph_service.py)
- [code-tiny/mcp/services/impact_service.py](file://code-tiny/mcp/services/impact_service.py)
- [code-tiny/mcp/services/symbol_service.py](file://code-tiny/mcp/services/symbol_service.py)
- [code-tiny/mcp/services/explore_service.py](file://code-tiny/mcp/services/explore_service.py)
- [code-tiny/mcp/services/workflow_service.py](file://code-tiny/mcp/services/workflow_service.py)
- [code-tiny/mcp/services/flow_reconstructor.py](file://code-tiny/mcp/services/flow_reconstructor.py)
- [code-tiny/tools/common/call_graph_builder.py](file://code-tiny/tools/common/call_graph_builder.py)
- [code-tiny/tools/common/graph_expander.py](file://code-tiny/tools/common/graph_expander.py)
- [code-tiny/tools/common/workflow_impact_scorer.py](file://code-tiny/tools/common/workflow_impact_scorer.py)
- [code-tiny/tools/common/incremental_sync_state.py](file://code-tiny/tools/common/incremental_sync_state.py)
- [code-tiny/tools/common/incremental_cleanup.py](file://code-tiny/tools/common/incremental_cleanup.py)
- [code-tiny/tools/graph/core/base.py](file://code-tiny/tools/graph/core/base.py)
- [code-tiny/tools/graph/driver/falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)
- [code-tiny/tools/graph/driver/neo4j_driver.py](file://code-tiny/tools/graph/driver/neo4j_driver.py)
- [code-tiny/tools/graph/operations/function_ops.py](file://code-tiny/tools/graph/operations/function_ops.py)
- [code-tiny/tools/graph/operations/class_ops.py](file://code-tiny/tools/graph/operations/class_ops.py)
- [code-tiny/tools/graph/operations/package_ops.py](file://code-tiny/tools/graph/operations/package_ops.py)
- [code-tiny/tools/graph/operations/document_ops.py](file://code-tiny/tools/graph/operations/document_ops.py)
- [code-tiny/tools/graph/operations/infra_ops.py](file://code-tiny/tools/graph/operations/infra_ops.py)
- [code-tiny/tools/graph/operations/cross_edge_ops.py](file://code-tiny/tools/graph/operations/cross_edge_ops.py)
- [code-tiny/tools/graph/operations/namespace_ops.py](file://code-tiny/tools/graph/operations/namespace_ops.py)
- [code-tiny/tools/graph/operations/type_ops.py](file://code-tiny/tools/graph/operations/type_ops.py)
- [code-tiny/tools/sync/incremental_sync.py](file://code-tiny/tools/sync/incremental_sync.py)
- [code-tiny/tools/sync/message_scan.py](file://code-tiny/tools/sync/message_scan.py)
- [code-tiny/tools/sync/build_owner_manifests.py](file://code-tiny/tools/sync/build_owner_manifests.py)
- [code-tiny/tools/sync/dead_code_report.py](file://code-tiny/tools/sync/dead_code_report.py)
- [code-tiny/tools/sync/owner_manifest.py](file://code-tiny/tools/sync/owner_manifest.py)
- [code-tiny/tools/common/analyzer_cache.py](file://code-tiny/tools/common/analyzer_cache.py)
- [code-tiny/tools/common/harness_config.py](file://code-tiny/tools/common/harness_config.py)
- [code-tiny/tools/common/source_inventory.py](file://code-tiny/tools/common/source_inventory.py)
- [code-tiny/tools/common/git_diff.py](file://code-tiny/tools/common/git_diff.py)
- [code-tiny/tools/common/query_intent_classifier.py](file://code-tiny/tools/common/query_intent_classifier.py)
- [code-tiny/tools/common/retrieval_scorer.py](file://code-tiny/tools/common/retrieval_scorer.py)
- [code-tiny/tools/common/intelligent_retrieval.py](file://code-tiny/tools/common/intelligent_retrieval.py)
- [code-tiny/tools/common/result_packager.py](file://code-tiny/tools/common/result_packager.py)
- [code-tiny/tools/common/confidence_scorer.py](file://code-tiny/tools/common/confidence_scorer.py)
- [code-tiny/tools/common/bm25_ranker.py](file://code-tiny/tools/common/bm25_ranker.py)
- [code-tiny/tools/common/frontend_relationship_extractor.py](file://code-tiny/tools/common/frontend_relationship_extractor.py)
- [code-tiny/tools/common/api_match_engine.py](file://code-tiny/tools/common/api_match_engine.py)
- [code-tiny/tools/common/signal_normalizer.py](file://code-tiny/tools/common/signal_normalizer.py)
- [code-tiny/tools/common/url_normalizer.py](file://code-tiny/tools/common/url_normalizer.py)
- [code-tiny/tools/common/workflow_classifier.py](file://code-tiny/tools/common/workflow_classifier.py)
- [code-tiny/tools/common/llm_summary.py](file://code-tiny/tools/common/llm_summary.py)
- [code-tiny/tools/common/react_role_classifier.py](file://code-tiny/tools/common/react_role_classifier.py)
- [code-tiny/tools/common/semantic_inference.py](file://code-tiny/tools/common/semantic_inference.py)
- [code-tiny/tools/common/sync_scope.py](file://code-tiny/tools/common/sync_scope.py)
- [code-tiny/tools/common/cloc_stats.py](file://code-tiny/tools/common/cloc_stats.py)

**Section sources**
- [code-tiny/mcp/unified_mcp.py](file://code-tiny/mcp/unified_mcp.py)
- [code-tiny/mcp/services/graph_service.py](file://code-tiny/mcp/services/graph_service.py)
- [code-tiny/mcp/services/impact_service.py](file://code-tiny/mcp/services/impact_service.py)
- [code-tiny/mcp/services/symbol_service.py](file://code-tiny/mcp/services/symbol_service.py)
- [code-tiny/mcp/services/explore_service.py](file://code-tiny/mcp/services/explore_service.py)
- [code-tiny/mcp/services/workflow_service.py](file://code-tiny/mcp/services/workflow_service.py)
- [code-tiny/mcp/services/flow_reconstructor.py](file://code-tiny/mcp/services/flow_reconstructor.py)
- [code-tiny/tools/common/call_graph_builder.py](file://code-tiny/tools/common/call_graph_builder.py)
- [code-tiny/tools/common/graph_expander.py](file://code-tiny/tools/common/graph_expander.py)
- [code-tiny/tools/common/workflow_impact_scorer.py](file://code-tiny/tools/common/workflow_impact_scorer.py)
- [code-tiny/tools/graph/core/base.py](file://code-tiny/tools/graph/core/base.py)
- [code-tiny/tools/graph/driver/falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)
- [code-tiny/tools/graph/driver/neo4j_driver.py](file://code-tiny/tools/graph/driver/neo4j_driver.py)
- [code-tiny/tools/graph/operations/function_ops.py](file://code-tiny/tools/graph/operations/function_ops.py)
- [code-tiny/tools/graph/operations/class_ops.py](file://code-tiny/tools/graph/operations/class_ops.py)
- [code-tiny/tools/graph/operations/package_ops.py](file://code-tiny/tools/graph/operations/package_ops.py)
- [code-tiny/tools/graph/operations/document_ops.py](file://code-tiny/tools/graph/operations/document_ops.py)
- [code-tiny/tools/graph/operations/infra_ops.py](file://code-tiny/tools/graph/operations/infra_ops.py)
- [code-tiny/tools/graph/operations/cross_edge_ops.py](file://code-tiny/tools/graph/operations/cross_edge_ops.py)
- [code-tiny/tools/graph/operations/namespace_ops.py](file://code-tiny/tools/graph/operations/namespace_ops.py)
- [code-tiny/tools/graph/operations/type_ops.py](file://code-tiny/tools/graph/operations/type_ops.py)
- [code-tiny/tools/sync/incremental_sync.py](file://code-tiny/tools/sync/incremental_sync.py)
- [code-tiny/tools/sync/message_scan.py](file://code-tiny/tools/sync/incremental_sync.py)
- [code-tiny/tools/sync/build_owner_manifests.py](file://code-tiny/tools/sync/build_owner_manifests.py)
- [code-tiny/tools/sync/dead_code_report.py](file://code-tiny/tools/sync/dead_code_report.py)
- [code-tiny/tools/sync/owner_manifest.py](file://code-tiny/tools/sync/owner_manifest.py)
- [code-tiny/tools/common/analyzer_cache.py](file://code-tiny/tools/common/analyzer_cache.py)
- [code-tiny/tools/common/harness_config.py](file://code-tiny/tools/common/harness_config.py)
- [code-tiny/tools/common/source_inventory.py](file://code-tiny/tools/common/source_inventory.py)
- [code-tiny/tools/common/git_diff.py](file://code-tiny/tools/common/git_diff.py)
- [code-tiny/tools/common/query_intent_classifier.py](file://code-tiny/tools/common/query_intent_classifier.py)
- [code-tiny/tools/common/retrieval_scorer.py](file://code-tiny/tools/common/retrieval_scorer.py)
- [code-tiny/tools/common/intelligent_retrieval.py](file://code-tiny/tools/common/intelligent_retrieval.py)
- [code-tiny/tools/common/result_packager.py](file://code-tiny/tools/common/result_packager.py)
- [code-tiny/tools/common/confidence_scorer.py](file://code-tiny/tools/common/confidence_scorer.py)
- [code-tiny/tools/common/bm25_ranker.py](file://code-tiny/tools/common/bm25_ranker.py)
- [code-tiny/tools/common/frontend_relationship_extractor.py](file://code-tiny/tools/common/frontend_relationship_extractor.py)
- [code-tiny/tools/common/api_match_engine.py](file://code-tiny/tools/common/api_match_engine.py)
- [code-tiny/tools/common/signal_normalizer.py](file://code-tiny/tools/common/signal_normalizer.py)
- [code-tiny/tools/common/url_normalizer.py](file://code-tiny/tools/common/url_normalizer.py)
- [code-tiny/tools/common/workflow_classifier.py](file://code-tiny/tools/common/workflow_classifier.py)
- [code-tiny/tools/common/llm_summary.py](file://code-tiny/tools/common/llm_summary.py)
- [code-tiny/tools/common/react_role_classifier.py](file://code-tiny/tools/common/react_role_classifier.py)
- [code-tiny/tools/common/semantic_inference.py](file://code-tiny/tools/common/semantic_inference.py)
- [code-tiny/tools/common/sync_scope.py](file://code-tiny/tools/common/sync_scope.py)
- [code-tiny/tools/common/cloc_stats.py](file://code-tiny/tools/common/cloc_stats.py)

## Core Components
- Graph Service: Provides graph construction, traversal, and querying APIs used by MCP tools. It composes call graph building, expansion, and operation primitives over graph drivers.
- Impact Service: Implements impact scoring, risk assessment, and change propagation workflows using graph traversals, retrieval scorers, and semantic inference.
- Symbol Service: Resolves symbols and maps them to graph nodes for precise dependency queries.
- Explore Service: Offers exploratory queries and path-finding across heterogeneous relationships.
- Workflow Service: Orchestrates multi-step analyses such as end-to-end impact assessments and report generation.
- Flow Reconstructor: Reconstructs execution flows from partial traces or logs to augment call graphs.

Key responsibilities:
- Call graph construction via common builders and language-specific analyzers
- Dependency mapping across packages, classes, functions, documents, and infra
- Impact scoring combining structural proximity, usage frequency, and semantic similarity
- Incremental updates leveraging state tracking and cleanup routines

**Section sources**
- [code-tiny/mcp/services/graph_service.py](file://code-tiny/mcp/services/graph_service.py)
- [code-tiny/mcp/services/impact_service.py](file://code-tiny/mcp/services/impact_service.py)
- [code-tiny/mcp/services/symbol_service.py](file://code-tiny/mcp/services/symbol_service.py)
- [code-tiny/mcp/services/explore_service.py](file://code-tiny/mcp/services/explore_service.py)
- [code-tiny/mcp/services/workflow_service.py](file://code-tiny/mcp/services/workflow_service.py)
- [code-tiny/mcp/services/flow_reconstructor.py](file://code-tiny/mcp/services/flow_reconstructor.py)
- [code-tiny/tools/common/call_graph_builder.py](file://code-tiny/tools/common/call_graph_builder.py)
- [code-tiny/tools/common/graph_expander.py](file://code-tiny/tools/common/graph_expander.py)
- [code-tiny/tools/common/workflow_impact_scorer.py](file://code-tiny/tools/common/workflow_impact_scorer.py)

## Architecture Overview
The architecture separates concerns into MCP services, shared analysis tools, and graph storage/traversal layers. The unified MCP entrypoint routes requests to specialized services, which compose lower-level operations and drivers.

```mermaid
sequenceDiagram
participant Client as "Client"
participant Unified as "Unified MCP"
participant GraphSvc as "Graph Service"
participant ImpactSvc as "Impact Service"
participant Ops as "Graph Operations"
participant Driver as "Graph Driver"
Client->>Unified : "Request dependency/impact analysis"
Unified->>GraphSvc : "Build/Query call graph"
GraphSvc->>Ops : "Traverse edges (functions/classes/packages)"
Ops->>Driver : "Execute graph queries"
Driver-->>Ops : "Results"
Ops-->>GraphSvc : "Structured nodes/edges"
GraphSvc-->>Unified : "Call graph snapshot"
Unified->>ImpactSvc : "Compute impact for change set"
ImpactSvc->>Ops : "Expand scope and compute scores"
Ops->>Driver : "Run expanded queries"
Driver-->>Ops : "Expanded results"
Ops-->>ImpactSvc : "Nodes with scores"
ImpactSvc-->>Unified : "Impact report"
Unified-->>Client : "Analysis results"
```

**Diagram sources**
- [code-tiny/mcp/unified_mcp.py](file://code-tiny/mcp/unified_mcp.py)
- [code-tiny/mcp/services/graph_service.py](file://code-tiny/mcp/services/graph_service.py)
- [code-tiny/mcp/services/impact_service.py](file://code-tiny/mcp/services/impact_service.py)
- [code-tiny/tools/graph/operations/function_ops.py](file://code-tiny/tools/graph/operations/function_ops.py)
- [code-tiny/tools/graph/operations/class_ops.py](file://code-tiny/tools/graph/operations/class_ops.py)
- [code-tiny/tools/graph/operations/package_ops.py](file://code-tiny/tools/graph/operations/package_ops.py)
- [code-tiny/tools/graph/driver/falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)
- [code-tiny/tools/graph/driver/neo4j_driver.py](file://code-tiny/tools/graph/driver/neo4j_driver.py)

## Detailed Component Analysis

### Call Graph Construction
Call graph construction is orchestrated by the graph service and relies on common builders and graph operations. The process typically involves:
- Discovering source files and symbols
- Parsing and extracting call relationships
- Normalizing identifiers and resolving cross-file references
- Writing nodes and edges into the graph store
- Expanding the graph with framework overlays and inferred relationships

```mermaid
flowchart TD
Start(["Start Build"]) --> Inventory["Source Inventory<br/>and Scope Selection"]
Inventory --> Parse["Parse Sources<br/>Extract Calls"]
Parse --> Normalize["Normalize Symbols<br/>Resolve References"]
Normalize --> WriteEdges["Write Nodes/Edges<br/>to Graph Store"]
WriteEdges --> Expand["Expand with Framework<br/>and Semantic Overlays"]
Expand --> Cache["Update Analyzer Cache"]
Cache --> End(["Build Complete"])
```

**Diagram sources**
- [code-tiny/mcp/services/graph_service.py](file://code-tiny/mcp/services/graph_service.py)
- [code-tiny/tools/common/call_graph_builder.py](file://code-tiny/tools/common/call_graph_builder.py)
- [code-tiny/tools/common/graph_expander.py](file://code-tiny/tools/common/graph_expander.py)
- [code-tiny/tools/common/analyzer_cache.py](file://code-tiny/tools/common/analyzer_cache.py)
- [code-tiny/tools/common/source_inventory.py](file://code-tiny/tools/common/source_inventory.py)
- [code-tiny/tools/graph/operations/function_ops.py](file://code-tiny/tools/graph/operations/function_ops.py)
- [code-tiny/tools/graph/operations/class_ops.py](file://code-tiny/tools/graph/operations/class_ops.py)
- [code-tiny/tools/graph/operations/package_ops.py](file://code-tiny/tools/graph/operations/package_ops.py)
- [code-tiny/tools/graph/operations/document_ops.py](file://code-tiny/tools/graph/operations/document_ops.py)
- [code-tiny/tools/graph/operations/infra_ops.py](file://code-tiny/tools/graph/operations/infra_ops.py)
- [code-tiny/tools/graph/operations/cross_edge_ops.py](file://code-tiny/tools/graph/operations/cross_edge_ops.py)
- [code-tiny/tools/graph/operations/namespace_ops.py](file://code-tiny/tools/graph/operations/namespace_ops.py)
- [code-tiny/tools/graph/operations/type_ops.py](file://code-tiny/tools/graph/operations/type_ops.py)

**Section sources**
- [code-tiny/mcp/services/graph_service.py](file://code-tiny/mcp/services/graph_service.py)
- [code-tiny/tools/common/call_graph_builder.py](file://code-tiny/tools/common/call_graph_builder.py)
- [code-tiny/tools/common/graph_expander.py](file://code-tiny/tools/common/graph_expander.py)
- [code-tiny/tools/common/analyzer_cache.py](file://code-tiny/tools/common/analyzer_cache.py)
- [code-tiny/tools/common/source_inventory.py](file://code-tiny/tools/common/source_inventory.py)
- [code-tiny/tools/graph/operations/function_ops.py](file://code-tiny/tools/graph/operations/function_ops.py)
- [code-tiny/tools/graph/operations/class_ops.py](file://code-tiny/tools/graph/operations/class_ops.py)
- [code-tiny/tools/graph/operations/package_ops.py](file://code-tiny/tools/graph/operations/package_ops.py)
- [code-tiny/tools/graph/operations/document_ops.py](file://code-tiny/tools/graph/operations/document_ops.py)
- [code-tiny/tools/graph/operations/infra_ops.py](file://code-tiny/tools/graph/operations/infra_ops.py)
- [code-tiny/tools/graph/operations/cross_edge_ops.py](file://code-tiny/tools/graph/operations/cross_edge_ops.py)
- [code-tiny/tools/graph/operations/namespace_ops.py](file://code-tiny/tools/graph/operations/namespace_ops.py)
- [code-tiny/tools/graph/operations/type_ops.py](file://code-tiny/tools/graph/operations/type_ops.py)

### Dependency Mapping
Dependency mapping leverages graph operations to traverse relationships across multiple abstraction levels:
- Functions: calls, imports, invocations
- Classes: inheritance, composition, method calls
- Packages: module boundaries and cross-package references
- Documents: API docs, contracts, and configuration links
- Infrastructure: databases, queues, external services

```mermaid
classDiagram
class FunctionOps {
+get_calls(node_id)
+get_callers(node_id)
+get_imports(node_id)
}
class ClassOps {
+get_inheritance(node_id)
+get_composition(node_id)
+get_method_calls(node_id)
}
class PackageOps {
+get_module_edges(node_id)
+get_cross_package_deps(node_id)
}
class DocumentOps {
+link_api_docs(node_id)
+link_configs(node_id)
}
class InfraOps {
+link_databases(node_id)
+link_queues(node_id)
+link_services(node_id)
}
class CrossEdgeOps {
+resolve_cross_language(node_id)
+resolve_framework_overlays(node_id)
}
class NamespaceOps {
+get_namespace_tree(root_id)
+get_symbols_in_ns(ns_id)
}
class TypeOps {
+get_type_signature(node_id)
+match_param_types(call_node, target_node)
}
FunctionOps <.. ClassOps : "method calls"
ClassOps <.. PackageOps : "module boundaries"
DocumentOps <.. InfraOps : "config-driven links"
CrossEdgeOps <.. FunctionOps : "cross-language resolution"
NamespaceOps <.. FunctionOps : "symbol scoping"
TypeOps <.. FunctionOps : "signature matching"
```

**Diagram sources**
- [code-tiny/tools/graph/operations/function_ops.py](file://code-tiny/tools/graph/operations/function_ops.py)
- [code-tiny/tools/graph/operations/class_ops.py](file://code-tiny/tools/graph/operations/class_ops.py)
- [code-tiny/tools/graph/operations/package_ops.py](file://code-tiny/tools/graph/operations/package_ops.py)
- [code-tiny/tools/graph/operations/document_ops.py](file://code-tiny/tools/graph/operations/document_ops.py)
- [code-tiny/tools/graph/operations/infra_ops.py](file://code-tiny/tools/graph/operations/infra_ops.py)
- [code-tiny/tools/graph/operations/cross_edge_ops.py](file://code-tiny/tools/graph/operations/cross_edge_ops.py)
- [code-tiny/tools/graph/operations/namespace_ops.py](file://code-tiny/tools/graph/operations/namespace_ops.py)
- [code-tiny/tools/graph/operations/type_ops.py](file://code-tiny/tools/graph/operations/type_ops.py)

**Section sources**
- [code-tiny/tools/graph/operations/function_ops.py](file://code-tiny/tools/graph/operations/function_ops.py)
- [code-tiny/tools/graph/operations/class_ops.py](file://code-tiny/tools/graph/operations/class_ops.py)
- [code-tiny/tools/graph/operations/package_ops.py](file://code-tiny/tools/graph/operations/package_ops.py)
- [code-tiny/tools/graph/operations/document_ops.py](file://code-tiny/tools/graph/operations/document_ops.py)
- [code-tiny/tools/graph/operations/infra_ops.py](file://code-tiny/tools/graph/operations/infra_ops.py)
- [code-tiny/tools/graph/operations/cross_edge_ops.py](file://code-tiny/tools/graph/operations/cross_edge_ops.py)
- [code-tiny/tools/graph/operations/namespace_ops.py](file://code-tiny/tools/graph/operations/namespace_ops.py)
- [code-tiny/tools/graph/operations/type_ops.py](file://code-tiny/tools/graph/operations/type_ops.py)

### Impact Assessment and Change Propagation
Impact assessment combines structural traversal with semantic signals to produce actionable insights:
- Identify affected nodes based on change set
- Expand scope using callers/callees and framework overlays
- Score impact using proximity, usage frequency, and semantic similarity
- Generate reports and recommendations

```mermaid
sequenceDiagram
participant User as "User"
participant Unified as "Unified MCP"
participant ImpactSvc as "Impact Service"
participant Scorer as "Workflow Impact Scorer"
participant Retrieval as "Retrieval Scorers"
participant Ops as "Graph Operations"
participant Driver as "Graph Driver"
User->>Unified : "Propose change (files/functions)"
Unified->>ImpactSvc : "Compute impact"
ImpactSvc->>Ops : "Find direct dependents"
Ops->>Driver : "Query caller/callee edges"
Driver-->>Ops : "Dependents"
Ops-->>ImpactSvc : "Affected nodes"
ImpactSvc->>Scorer : "Score impact"
Scorer->>Retrieval : "Combine BM25, confidence, semantic signals"
Retrieval-->>Scorer : "Weighted scores"
Scorer-->>ImpactSvc : "Ranked impact list"
ImpactSvc-->>Unified : "Impact report"
Unified-->>User : "Results and guidance"
```

**Diagram sources**
- [code-tiny/mcp/services/impact_service.py](file://code-tiny/mcp/services/impact_service.py)
- [code-tiny/tools/common/workflow_impact_scorer.py](file://code-tiny/tools/common/workflow_impact_scorer.py)
- [code-tiny/tools/common/retrieval_scorer.py](file://code-tiny/tools/common/retrieval_scorer.py)
- [code-tiny/tools/common/bm25_ranker.py](file://code-tiny/tools/common/bm25_ranker.py)
- [code-tiny/tools/common/confidence_scorer.py](file://code-tiny/tools/common/confidence_scorer.py)
- [code-tiny/tools/common/intelligent_retrieval.py](file://code-tiny/tools/common/intelligent_retrieval.py)
- [code-tiny/tools/graph/operations/function_ops.py](file://code-tiny/tools/graph/operations/function_ops.py)
- [code-tiny/tools/graph/driver/falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)
- [code-tiny/tools/graph/driver/neo4j_driver.py](file://code-tiny/tools/graph/driver/neo4j_driver.py)

**Section sources**
- [code-tiny/mcp/services/impact_service.py](file://code-tiny/mcp/services/impact_service.py)
- [code-tiny/tools/common/workflow_impact_scorer.py](file://code-tiny/tools/common/workflow_impact_scorer.py)
- [code-tiny/tools/common/retrieval_scorer.py](file://code-tiny/tools/common/retrieval_scorer.py)
- [code-tiny/tools/common/bm25_ranker.py](file://code-tiny/tools/common/bm25_ranker.py)
- [code-tiny/tools/common/confidence_scorer.py](file://code-tiny/tools/common/confidence_scorer.py)
- [code-tiny/tools/common/intelligent_retrieval.py](file://code-tiny/tools/common/intelligent_retrieval.py)
- [code-tiny/tools/graph/operations/function_ops.py](file://code-tiny/tools/graph/operations/function_ops.py)
- [code-tiny/tools/graph/driver/falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)
- [code-tiny/tools/graph/driver/neo4j_driver.py](file://code-tiny/tools/graph/driver/neo4j_driver.py)

### Incremental Analysis Integration
Incremental analysis minimizes recomputation by focusing on changed scopes and updating only necessary parts of the graph:
- Detect changes via diff and owner manifests
- Compute sync scope and update state
- Clean up stale artifacts and rebuild impacted segments
- Persist updated state for subsequent runs

```mermaid
flowchart TD
A["Detect Changes<br/>Git Diff + Owner Manifests"] --> B["Compute Sync Scope"]
B --> C{"Scope Valid?"}
C --> |No| D["Fallback Full Scan"]
C --> |Yes| E["Incremental Update<br/>Rebuild Affected Segments"]
E --> F["Cleanup Stale Artifacts"]
F --> G["Persist State"]
D --> G
G --> H["Ready for Queries"]
```

**Diagram sources**
- [code-tiny/tools/sync/incremental_sync.py](file://code-tiny/tools/sync/incremental_sync.py)
- [code-tiny/tools/sync/message_scan.py](file://code-tiny/tools/sync/message_scan.py)
- [code-tiny/tools/sync/build_owner_manifests.py](file://code-tiny/tools/sync/build_owner_manifests.py)
- [code-tiny/tools/sync/dead_code_report.py](file://code-tiny/tools/sync/dead_code_report.py)
- [code-tiny/tools/sync/owner_manifest.py](file://code-tiny/tools/sync/owner_manifest.py)
- [code-tiny/tools/common/incremental_sync_state.py](file://code-tiny/tools/common/incremental_sync_state.py)
- [code-tiny/tools/common/incremental_cleanup.py](file://code-tiny/tools/common/incremental_cleanup.py)
- [code-tiny/tools/common/git_diff.py](file://code-tiny/tools/common/git_diff.py)
- [code-tiny/tools/common/sync_scope.py](file://code-tiny/tools/common/sync_scope.py)

**Section sources**
- [code-tiny/tools/sync/incremental_sync.py](file://code-tiny/tools/sync/incremental_sync.py)
- [code-tiny/tools/sync/message_scan.py](file://code-tiny/tools/sync/message_scan.py)
- [code-tiny/tools/sync/build_owner_manifests.py](file://code-tiny/tools/sync/build_owner_manifests.py)
- [code-tiny/tools/sync/dead_code_report.py](file://code-tiny/tools/sync/dead_code_report.py)
- [code-tiny/tools/sync/owner_manifest.py](file://code-tiny/tools/sync/owner_manifest.py)
- [code-tiny/tools/common/incremental_sync_state.py](file://code-tiny/tools/common/incremental_sync_state.py)
- [code-tiny/tools/common/incremental_cleanup.py](file://code-tiny/tools/common/incremental_cleanup.py)
- [code-tiny/tools/common/git_diff.py](file://code-tiny/tools/common/git_diff.py)
- [code-tiny/tools/common/sync_scope.py](file://code-tiny/tools/common/sync_scope.py)

### MCP Tooling and Routing
The MCP layer exposes tools that clients can invoke for dependency and impact tasks. The unified entrypoint coordinates routing and parameter coercion, while framework registry and metadata define available capabilities.

```mermaid
graph TB
U["unified_mcp.py"] --> GR["framework_registry.py"]
U --> TM["tool_metadata.py"]
U --> GS["services/graph_service.py"]
U --> IS["services/impact_service.py"]
U --> SS["services/symbol_service.py"]
U --> ES["services/explore_service.py"]
U --> WS["services/workflow_service.py"]
U --> FR["services/flow_reconstructor.py"]
```

**Diagram sources**
- [code-tiny/mcp/unified_mcp.py](file://code-tiny/mcp/unified_mcp.py)
- [code-tiny/mcp/framework_registry.py](file://code-tiny/mcp/framework_registry.py)
- [code-tiny/mcp/tool_metadata.py](file://code-tiny/mcp/tool_metadata.py)
- [code-tiny/mcp/services/graph_service.py](file://code-tiny/mcp/services/graph_service.py)
- [code-tiny/mcp/services/impact_service.py](file://code-tiny/mcp/services/impact_service.py)
- [code-tiny/mcp/services/symbol_service.py](file://code-tiny/mcp/services/symbol_service.py)
- [code-tiny/mcp/services/explore_service.py](file://code-tiny/mcp/services/explore_service.py)
- [code-tiny/mcp/services/workflow_service.py](file://code-tiny/mcp/services/workflow_service.py)
- [code-tiny/mcp/services/flow_reconstructor.py](file://code-tiny/mcp/services/flow_reconstructor.py)

**Section sources**
- [code-tiny/mcp/unified_mcp.py](file://code-tiny/mcp/unified_mcp.py)
- [code-tiny/mcp/framework_registry.py](file://code-tiny/mcp/framework_registry.py)
- [code-tiny/mcp/tool_metadata.py](file://code-tiny/mcp/tool_metadata.py)
- [code-tiny/mcp/services/graph_service.py](file://code-tiny/mcp/services/graph_service.py)
- [code-tiny/mcp/services/impact_service.py](file://code-tiny/mcp/services/impact_service.py)
- [code-tiny/mcp/services/symbol_service.py](file://code-tiny/mcp/services/symbol_service.py)
- [code-tiny/mcp/services/explore_service.py](file://code-tiny/mcp/services/explore_service.py)
- [code-tiny/mcp/services/workflow_service.py](file://code-tiny/mcp/services/workflow_service.py)
- [code-tiny/mcp/services/flow_reconstructor.py](file://code-tiny/mcp/services/flow_reconstructor.py)

## Dependency Analysis
This section outlines practical dependency queries and workflows enabled by the graph operations and services.

- Call graph queries
  - Retrieve callers and callees for a function node
  - Traverse import edges and resolve cross-language references
  - Expand with framework overlays to include injected calls

- Class and package dependencies
  - Inheritance and composition edges
  - Cross-package module edges and namespace trees

- Document and infrastructure links
  - API documentation associations
  - Database, queue, and external service connections

- Symbol resolution
  - Resolve symbols to canonical IDs
  - Match signatures and parameters for accurate call binding

Example query patterns (described):
- Find all downstream functions affected by a change in a given function
- List upstream consumers of a public API endpoint
- Map database schema changes to affected queries and ORM models
- Identify configuration-driven integrations impacted by environment changes

Interpretation guidance:
- Prioritize nodes with higher impact scores and broader reach
- Validate inferred edges against runtime behavior when possible
- Use symbol resolution to avoid false positives from name collisions

**Section sources**
- [code-tiny/tools/graph/operations/function_ops.py](file://code-tiny/tools/graph/operations/function_ops.py)
- [code-tiny/tools/graph/operations/class_ops.py](file://code-tiny/tools/graph/operations/class_ops.py)
- [code-tiny/tools/graph/operations/package_ops.py](file://code-tiny/tools/graph/operations/package_ops.py)
- [code-tiny/tools/graph/operations/document_ops.py](file://code-tiny/tools/graph/operations/document_ops.py)
- [code-tiny/tools/graph/operations/infra_ops.py](file://code-tiny/tools/graph/operations/infra_ops.py)
- [code-tiny/tools/graph/operations/cross_edge_ops.py](file://code-tiny/tools/graph/operations/cross_edge_ops.py)
- [code-tiny/tools/graph/operations/namespace_ops.py](file://code-tiny/tools/graph/operations/namespace_ops.py)
- [code-tiny/tools/graph/operations/type_ops.py](file://code-tiny/tools/graph/operations/type_ops.py)
- [code-tiny/mcp/services/symbol_service.py](file://code-tiny/mcp/services/symbol_service.py)
- [code-tiny/mcp/services/explore_service.py](file://code-tiny/mcp/services/explore_service.py)

## Performance Considerations
- Prefer incremental updates for frequent changes; leverage sync scope and owner manifests to limit work
- Cache analyzer outputs and intermediate results to reduce repeated parsing
- Use targeted graph operations instead of broad scans where possible
- Combine BM25 ranking with semantic inference to prune irrelevant candidates early
- Monitor driver performance and choose Falkordb or Neo4j based on workload characteristics

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and remedies:
- Stale graph state after partial updates
  - Ensure incremental cleanup runs after updates
  - Verify state persistence and lock consistency
- False positives in call edges
  - Enable signature matching and type checks
  - Validate cross-language resolution rules
- Slow impact queries
  - Narrow scope using intent classification and intelligent retrieval
  - Precompute frequently used expansions and cache results
- Missing infrastructure links
  - Re-run document and config link extraction
  - Confirm normalization of URLs and identifiers

Operational checks:
- Inspect sync state and owner manifests for correctness
- Review analyzer cache validity and invalidation policies
- Validate driver connectivity and index health

**Section sources**
- [code-tiny/tools/common/incremental_cleanup.py](file://code-tiny/tools/common/incremental_cleanup.py)
- [code-tiny/tools/common/incremental_sync_state.py](file://code-tiny/tools/common/incremental_sync_state.py)
- [code-tiny/tools/common/analyzer_cache.py](file://code-tiny/tools/common/analyzer_cache.py)
- [code-tiny/tools/common/query_intent_classifier.py](file://code-tiny/tools/common/query_intent_classifier.py)
- [code-tiny/tools/common/intelligent_retrieval.py](file://code-tiny/tools/common/intelligent_retrieval.py)
- [code-tiny/tools/graph/driver/falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)
- [code-tiny/tools/graph/driver/neo4j_driver.py](file://code-tiny/tools/graph/driver/neo4j_driver.py)

## Conclusion
Cortex Harness MCP provides a comprehensive suite for dependency and impact analysis. By constructing robust call graphs, mapping multi-layered dependencies, scoring impact with combined structural and semantic signals, and integrating incremental updates, it enables efficient and reliable change risk assessment. Teams can use these tools to prioritize refactors, plan safe releases, and understand the ripple effects of modifications across complex codebases.

[No sources needed since this section summarizes without analyzing specific files]

## Appendices

### Example Workflows and Best Practices
- Dependency discovery workflow
  - Build or refresh call graph
  - Query callers/callees for target symbols
  - Expand with framework overlays and cross-language edges
  - Summarize findings and export reports

- Impact assessment workflow
  - Define change set (files/functions)
  - Compute affected nodes and expand scope
  - Apply scoring (proximity, usage, semantic similarity)
  - Produce prioritized list and recommended mitigations

- Incremental maintenance workflow
  - Detect diffs and compute sync scope
  - Rebuild impacted segments and clean stale artifacts
  - Persist state and validate integrity

Guidance for interpretation:
- Focus on high-score nodes with broad downstream reach
- Validate critical edges with runtime tests or tracing
- Use symbol resolution to disambiguate overloaded names
- Leverage document and infrastructure links to capture non-code dependencies

[No sources needed since this section provides general guidance]