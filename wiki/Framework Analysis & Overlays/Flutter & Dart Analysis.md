# Flutter & Dart Analysis

<cite>
**Referenced Files in This Document**
- [flutter_analyzer.py](file://code-tiny/tools/flutter/flutter_analyzer.py)
- [dart_parser.py](file://code-tiny/tools/flutter/dart_parser.py)
- [models.py](file://code-tiny/tools/flutter/models.py)
- [pipeline.py](file://code-tiny/tools/flutter/pipeline.py)
- [detector.py](file://code-tiny/tools/flutter/detector.py)
- [normalizer.py](file://code-tiny/tools/flutter/normalizer.py)
- [cache.py](file://code-tiny/tools/flutter/cache.py)
- [protocol.py](file://code-tiny/tools/flutter/protocol.py)
- [test_flutter_analyzer_imports.py](file://tests/test_flutter_analyzer_imports.py)
- [test_flutter_project_detection.py](file://tests/test_flutter_project_detection.py)
- [test_flutter_protocol.py](file://tests/test_flutter_protocol.py)
- [test_dart_fixture_analysis.py](file://tests/test_dart_fixture_analysis.py)
- [test_dart_incremental_resolution.py](file://tests/test_dart_incremental_resolution.py)
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
This document explains the Flutter and Dart analysis capabilities implemented in the repository. It covers how Dart source code is parsed, how Flutter widgets and state management patterns are modeled, how navigation structures and pubspec.yaml dependencies are analyzed, and how widget trees and UI component relationships are represented. It also includes guidance for analyzing complex applications with multiple screens, custom widgets, and platform-specific implementations, as well as support for different state management libraries, testing frameworks, and build configurations. Finally, it provides recommendations for hot reload workflows, incremental analysis, and performance optimization for large mobile applications.

## Project Structure
The Flutter analyzer is implemented under a dedicated module with clear separation of concerns: parsing, modeling, pipeline orchestration, detection, normalization, caching, and protocol definitions. Tests validate imports, project detection, protocol contracts, fixture-based analysis, and incremental resolution behavior.

```mermaid
graph TB
subgraph "Flutter Module"
A["flutter_analyzer.py"]
B["dart_parser.py"]
C["models.py"]
D["pipeline.py"]
E["detector.py"]
F["normalizer.py"]
G["cache.py"]
H["protocol.py"]
end
subgraph "Tests"
T1["test_flutter_analyzer_imports.py"]
T2["test_flutter_project_detection.py"]
T3["test_flutter_protocol.py"]
T4["test_dart_fixture_analysis.py"]
T5["test_dart_incremental_resolution.py"]
end
A --> B
A --> C
A --> D
A --> E
A --> F
A --> G
A --> H
T1 --> A
T2 --> E
T3 --> H
T4 --> B
T5 --> G
```

**Diagram sources**
- [flutter_analyzer.py](file://code-tiny/tools/flutter/flutter_analyzer.py)
- [dart_parser.py](file://code-tiny/tools/flutter/dart_parser.py)
- [models.py](file://code-tiny/tools/flutter/models.py)
- [pipeline.py](file://code-tiny/tools/flutter/pipeline.py)
- [detector.py](file://code-tiny/tools/flutter/detector.py)
- [normalizer.py](file://code-tiny/tools/flutter/normalizer.py)
- [cache.py](file://code-tiny/tools/flutter/cache.py)
- [protocol.py](file://code-tiny/tools/flutter/protocol.py)
- [test_flutter_analyzer_imports.py](file://tests/test_flutter_analyzer_imports.py)
- [test_flutter_project_detection.py](file://tests/test_flutter_project_detection.py)
- [test_flutter_protocol.py](file://tests/test_flutter_protocol.py)
- [test_dart_fixture_analysis.py](file://tests/test_dart_fixture_analysis.py)
- [test_dart_incremental_resolution.py](file://tests/test_dart_incremental_resolution.py)

**Section sources**
- [flutter_analyzer.py](file://code-tiny/tools/flutter/flutter_analyzer.py)
- [dart_parser.py](file://code-tiny/tools/flutter/dart_parser.py)
- [models.py](file://code-tiny/tools/flutter/models.py)
- [pipeline.py](file://code-tiny/tools/flutter/pipeline.py)
- [detector.py](file://code-tiny/tools/flutter/detector.py)
- [normalizer.py](file://code-tiny/tools/flutter/normalizer.py)
- [cache.py](file://code-tiny/tools/flutter/cache.py)
- [protocol.py](file://code-tiny/tools/flutter/protocol.py)
- [test_flutter_analyzer_imports.py](file://tests/test_flutter_analyzer_imports.py)
- [test_flutter_project_detection.py](file://tests/test_flutter_project_detection.py)
- [test_flutter_protocol.py](file://tests/test_flutter_protocol.py)
- [test_dart_fixture_analysis.py](file://tests/test_dart_fixture_analysis.py)
- [test_dart_incremental_resolution.py](file://tests/test_dart_incremental_resolution.py)

## Core Components
- Analyzer entrypoint orchestrates scanning, parsing, and graph construction for Flutter projects.
- Dart parser extracts symbols, classes, methods, and references from .dart files.
- Models define canonical representations for Dart constructs, Flutter widgets, providers, blocs, riverpod elements, navigation routes, and pubspec dependencies.
- Pipeline coordinates stages such as discovery, parsing, normalization, and indexing.
- Detector identifies Flutter projects and relevant artifacts (pubspec.yaml, lib structure).
- Normalizer standardizes identifiers, paths, and semantic labels across modules.
- Cache stores intermediate results to support incremental updates.
- Protocol defines request/response schemas used by higher-level services or MCP integration.

Key responsibilities:
- Parse Dart AST-like structures and resolve imports.
- Identify Flutter widget classes and their composition relationships.
- Detect state management patterns (Provider, Bloc, Riverpod) via symbol and usage heuristics.
- Extract navigation routes and screen relationships.
- Analyze pubspec.yaml for dependencies and version constraints.
- Build normalized graphs suitable for downstream querying and visualization.

**Section sources**
- [flutter_analyzer.py](file://code-tiny/tools/flutter/flutter_analyzer.py)
- [dart_parser.py](file://code-tiny/tools/flutter/dart_parser.py)
- [models.py](file://code-tiny/tools/flutter/models.py)
- [pipeline.py](file://code-tiny/tools/flutter/pipeline.py)
- [detector.py](file://code-tiny/tools/flutter/detector.py)
- [normalizer.py](file://code-tiny/tools/flutter/normalizer.py)
- [cache.py](file://code-tiny/tools/flutter/cache.py)
- [protocol.py](file://code-tiny/tools/flutter/protocol.py)

## Architecture Overview
The Flutter analyzer follows a layered architecture:
- Detection layer locates Flutter projects and scopes.
- Parsing layer reads Dart sources and builds internal models.
- Normalization layer harmonizes names and relationships.
- Pipeline layer sequences operations and manages incremental updates.
- Protocol layer exposes structured interfaces for consumers.

```mermaid
sequenceDiagram
participant Client as "Client"
participant Analyzer as "Analyzer"
participant Detector as "Detector"
participant Parser as "DartParser"
participant Normalizer as "Normalizer"
participant Cache as "Cache"
participant Protocol as "Protocol"
Client->>Analyzer : "Analyze Flutter Project"
Analyzer->>Detector : "Detect project root and scope"
Detector-->>Analyzer : "Project metadata"
Analyzer->>Parser : "Parse Dart sources"
Parser-->>Analyzer : "Parsed nodes and edges"
Analyzer->>Normalizer : "Normalize identifiers and types"
Normalizer-->>Analyzer : "Normalized graph"
Analyzer->>Cache : "Persist incremental state"
Cache-->>Analyzer : "Cache keys and deltas"
Analyzer->>Protocol : "Serialize results"
Protocol-->>Client : "Structured response"
```

**Diagram sources**
- [flutter_analyzer.py](file://code-tiny/tools/flutter/flutter_analyzer.py)
- [detector.py](file://code-tiny/tools/flutter/detector.py)
- [dart_parser.py](file://code-tiny/tools/flutter/dart_parser.py)
- [normalizer.py](file://code-tiny/tools/flutter/normalizer.py)
- [cache.py](file://code-tiny/tools/flutter/cache.py)
- [protocol.py](file://code-tiny/tools/flutter/protocol.py)

## Detailed Component Analysis

### Analyzer Orchestration
The analyzer coordinates detection, parsing, normalization, and result serialization. It integrates with the pipeline to sequence steps and uses cache to avoid reprocessing unchanged files.

```mermaid
classDiagram
class Analyzer {
+analyze(project_path) Result
-detect_scope() ProjectScope
-parse_sources() ParsedGraph
-normalize_graph(Graph) Graph
-persist_cache(Deltas) void
-serialize_result(Graph) Response
}
class Detector {
+find_flutter_root(path) Path
+collect_scopes() Scope[]
}
class DartParser {
+parse_file(file_path) Nodes
+resolve_imports(nodes) ResolvedRefs
}
class Normalizer {
+normalize_names(nodes) Nodes
+standardize_edges(edges) Edges
}
class Cache {
+load_state() State
+save_delta(delta) void
}
class Protocol {
+to_response(graph) Response
+from_request(request) Request
}
Analyzer --> Detector : "uses"
Analyzer --> DartParser : "uses"
Analyzer --> Normalizer : "uses"
Analyzer --> Cache : "reads/writes"
Analyzer --> Protocol : "serializes"
```

**Diagram sources**
- [flutter_analyzer.py](file://code-tiny/tools/flutter/flutter_analyzer.py)
- [detector.py](file://code-tiny/tools/flutter/detector.py)
- [dart_parser.py](file://code-tiny/tools/flutter/dart_parser.py)
- [normalizer.py](file://code-tiny/tools/flutter/normalizer.py)
- [cache.py](file://code-tiny/tools/flutter/cache.py)
- [protocol.py](file://code-tiny/tools/flutter/protocol.py)

**Section sources**
- [flutter_analyzer.py](file://code-tiny/tools/flutter/flutter_analyzer.py)

### Dart Parser
The Dart parser extracts symbols, classes, methods, and references from .dart files. It resolves imports and produces a graph of nodes and edges representing code relationships.

```mermaid
flowchart TD
Start(["Start parse"]) --> ReadFile["Read Dart file"]
ReadFile --> Tokenize["Tokenize and parse"]
Tokenize --> ExtractSymbols["Extract symbols<br/>classes, methods, fields"]
ExtractSymbols --> ResolveImports["Resolve imports and references"]
ResolveImports --> BuildNodes["Build node list"]
BuildNodes --> BuildEdges["Build edge list"]
BuildEdges --> ReturnResult["Return parsed graph"]
```

**Diagram sources**
- [dart_parser.py](file://code-tiny/tools/flutter/dart_parser.py)

**Section sources**
- [dart_parser.py](file://code-tiny/tools/flutter/dart_parser.py)

### Models
Models define canonical data structures for Dart constructs, Flutter widgets, state management components, navigation routes, and pubspec dependencies. They provide a consistent schema for downstream processing and queries.

```mermaid
erDiagram
SYMBOL {
string id PK
string name
string kind
string file_path
int line
int column
}
WIDGET {
string id PK
string class_name
string parent_class
string file_path
bool is_screen
}
STATE_PROVIDER {
string id PK
string provider_type
string target_symbol_id FK
string file_path
}
ROUTE {
string id PK
string route_name
string target_widget_id FK
string file_path
}
PUBSPEC_DEP {
string id PK
string package_name
string version_constraint
string file_path
}
SYMBOL ||--o{ WIDGET : "defines"
WIDGET ||--o{ ROUTE : "navigated_by"
SYMBOL ||--o{ STATE_PROVIDER : "consumed_by"
SYMBOL ||--o{ PUBSPEC_DEP : "imported_by"
```

**Diagram sources**
- [models.py](file://code-tiny/tools/flutter/models.py)

**Section sources**
- [models.py](file://code-tiny/tools/flutter/models.py)

### Pipeline
The pipeline sequences discovery, parsing, normalization, and indexing. It supports incremental updates by leveraging cache and delta computation.

```mermaid
flowchart TD
Init(["Initialize pipeline"]) --> Discover["Discover Flutter project and files"]
Discover --> Parse["Parse Dart sources"]
Parse --> Normalize["Normalize identifiers and edges"]
Normalize --> Index["Index into graph store"]
Index --> Persist["Persist cache and state"]
Persist --> Done(["Pipeline complete"])
```

**Diagram sources**
- [pipeline.py](file://code-tiny/tools/flutter/pipeline.py)

**Section sources**
- [pipeline.py](file://code-tiny/tools/flutter/pipeline.py)

### Detector
The detector identifies Flutter projects by locating pubspec.yaml and validating typical directory structures. It returns project metadata and scopes for analysis.

```mermaid
flowchart TD
Start(["Start detection"]) --> FindPubspec["Find pubspec.yaml"]
FindPubspec --> ValidateStructure{"Validate lib/ and test/"}
ValidateStructure --> |Yes| CollectScopes["Collect scopes and roots"]
ValidateStructure --> |No| Error["Report invalid Flutter project"]
CollectScopes --> ReturnMeta["Return project metadata"]
```

**Diagram sources**
- [detector.py](file://code-tiny/tools/flutter/detector.py)

**Section sources**
- [detector.py](file://code-tiny/tools/flutter/detector.py)

### Normalizer
The normalizer standardizes identifiers, file paths, and relationship labels to ensure consistency across modules and reduce ambiguity.

```mermaid
flowchart TD
Start(["Start normalization"]) --> CleanNames["Clean and normalize names"]
CleanNames --> StandardizePaths["Standardize file paths"]
StandardizePaths --> AlignTypes["Align type labels and kinds"]
AlignTypes --> MergeDuplicates["Merge duplicate nodes"]
MergeDuplicates --> Output["Output normalized graph"]
```

**Diagram sources**
- [normalizer.py](file://code-tiny/tools/flutter/normalizer.py)

**Section sources**
- [normalizer.py](file://code-tiny/tools/flutter/normalizer.py)

### Cache
The cache persists incremental state and deltas to speed up subsequent analyses and support hot reload workflows.

```mermaid
flowchart TD
Start(["Start cache operation"]) --> LoadState["Load previous state"]
LoadState --> ComputeDelta["Compute file changes"]
ComputeDelta --> ApplyDelta["Apply deltas to graph"]
ApplyDelta --> SaveState["Save updated state"]
SaveState --> End(["Cache updated"])
```

**Diagram sources**
- [cache.py](file://code-tiny/tools/flutter/cache.py)

**Section sources**
- [cache.py](file://code-tiny/tools/flutter/cache.py)

### Protocol
The protocol defines request and response schemas for interacting with the analyzer, enabling integration with higher-level services or MCP clients.

```mermaid
classDiagram
class Request {
+string action
+object params
}
class Response {
+bool success
+object data
+string error
}
class AnalyzerProtocol {
+handle_request(Request) Response
+validate_params(params) bool
}
Request <.. AnalyzerProtocol : "consumes"
Response <.. AnalyzerProtocol : "produces"
```

**Diagram sources**
- [protocol.py](file://code-tiny/tools/flutter/protocol.py)

**Section sources**
- [protocol.py](file://code-tiny/tools/flutter/protocol.py)

### Conceptual Overview
Widget tree analysis involves identifying widget classes, their composition, and relationships to state providers and navigation routes. State flow tracking maps how state changes propagate through providers, blocs, or riverpod elements. Pubspec dependency analysis captures package versions and constraints to inform impact and compatibility checks.

```mermaid
graph TB
Widgets["Widgets"] --> Providers["Providers/Bloc/Riverpod"]
Widgets --> Routes["Navigation Routes"]
Providers --> Services["Business Logic / Services"]
Routes --> Screens["Screens / Pages"]
Dependencies["pubspec.yaml"] --> Packages["External Packages"]
Packages --> Widgets
Packages --> Providers
```

[No sources needed since this diagram shows conceptual workflow, not actual code structure]

## Dependency Analysis
The Flutter analyzer depends on core utilities for parsing, normalization, caching, and protocol handling. Tests validate import correctness, project detection, protocol contracts, fixture-based analysis, and incremental resolution behavior.

```mermaid
graph TB
Analyzer["Analyzer"] --> Parser["DartParser"]
Analyzer --> Detector["Detector"]
Analyzer --> Normalizer["Normalizer"]
Analyzer --> Cache["Cache"]
Analyzer --> Protocol["Protocol"]
TestImports["test_flutter_analyzer_imports.py"] --> Analyzer
TestDetection["test_flutter_project_detection.py"] --> Detector
TestProtocol["test_flutter_protocol.py"] --> Protocol
TestFixture["test_dart_fixture_analysis.py"] --> Parser
TestIncremental["test_dart_incremental_resolution.py"] --> Cache
```

**Diagram sources**
- [flutter_analyzer.py](file://code-tiny/tools/flutter/flutter_analyzer.py)
- [dart_parser.py](file://code-tiny/tools/flutter/dart_parser.py)
- [detector.py](file://code-tiny/tools/flutter/detector.py)
- [normalizer.py](file://code-tiny/tools/flutter/normalizer.py)
- [cache.py](file://code-tiny/tools/flutter/cache.py)
- [protocol.py](file://code-tiny/tools/flutter/protocol.py)
- [test_flutter_analyzer_imports.py](file://tests/test_flutter_analyzer_imports.py)
- [test_flutter_project_detection.py](file://tests/test_flutter_project_detection.py)
- [test_flutter_protocol.py](file://tests/test_flutter_protocol.py)
- [test_dart_fixture_analysis.py](file://tests/test_dart_fixture_analysis.py)
- [test_dart_incremental_resolution.py](file://tests/test_dart_incremental_resolution.py)

**Section sources**
- [flutter_analyzer.py](file://code-tiny/tools/flutter/flutter_analyzer.py)
- [dart_parser.py](file://code-tiny/tools/flutter/dart_parser.py)
- [detector.py](file://code-tiny/tools/flutter/detector.py)
- [normalizer.py](file://code-tiny/tools/flutter/normalizer.py)
- [cache.py](file://code-tiny/tools/flutter/cache.py)
- [protocol.py](file://code-tiny/tools/flutter/protocol.py)
- [test_flutter_analyzer_imports.py](file://tests/test_flutter_analyzer_imports.py)
- [test_flutter_project_detection.py](file://tests/test_flutter_project_detection.py)
- [test_flutter_protocol.py](file://tests/test_flutter_protocol.py)
- [test_dart_fixture_analysis.py](file://tests/test_dart_fixture_analysis.py)
- [test_dart_incremental_resolution.py](file://tests/test_dart_incremental_resolution.py)

## Performance Considerations
- Incremental analysis: Use cache to persist state and apply deltas based on file changes, reducing full re-parsing overhead.
- Scope limiting: Restrict analysis to changed modules or affected screens to minimize work during hot reload cycles.
- Parallel parsing: Where feasible, parse independent files concurrently to improve throughput.
- Memory management: Stream large files and release intermediate structures after normalization.
- Indexing efficiency: Prefer stable identifiers and deduplicate nodes to keep graph size manageable.
- Dependency pruning: Skip irrelevant packages when focusing on application-specific analysis.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and resolutions:
- Invalid Flutter project: Ensure pubspec.yaml exists and lib/ and test/ directories are present; detector will report errors if structure is missing.
- Import resolution failures: Verify that imported modules exist within the project scope and that path normalization is applied consistently.
- Duplicate nodes: Normalizer should merge duplicates; check normalization rules if conflicts persist.
- Stale cache: Clear cache state if incremental updates produce inconsistent results; force full reanalysis.
- Protocol mismatches: Validate request parameters against protocol schema before invoking analyzer endpoints.

Validation points:
- Import correctness verified by tests.
- Project detection validated by dedicated tests.
- Protocol contract enforced by protocol tests.
- Fixture-based analysis ensures parser outputs match expected shapes.
- Incremental resolution tests confirm cache behavior and delta application.

**Section sources**
- [test_flutter_analyzer_imports.py](file://tests/test_flutter_analyzer_imports.py)
- [test_flutter_project_detection.py](file://tests/test_flutter_project_detection.py)
- [test_flutter_protocol.py](file://tests/test_flutter_protocol.py)
- [test_dart_fixture_analysis.py](file://tests/test_dart_fixture_analysis.py)
- [test_dart_incremental_resolution.py](file://tests/test_dart_incremental_resolution.py)

## Conclusion
The Flutter and Dart analyzer provides a modular, scalable foundation for analyzing Dart sources, Flutter widgets, state management patterns, navigation structures, and pubspec dependencies. Its layered design separates detection, parsing, normalization, and orchestration, while caching and protocol layers enable efficient incremental updates and integration with higher-level services. The included tests validate core behaviors and help maintain reliability as features evolve.

[No sources needed since this section summarizes without analyzing specific files]

## Appendices

### Example Scenarios
- Complex multi-screen app: Identify screen widgets, map navigation routes, and trace state providers consumed by each screen.
- Custom widgets: Classify reusable widgets, detect composition relationships, and link them to state providers.
- Platform-specific implementations: Separate platform channels and native integrations by filtering imports and annotations.
- State management libraries: Recognize Provider, Bloc, and Riverpod patterns via symbol heuristics and usage contexts.
- Testing frameworks: Include test files in scope to analyze widget tests and mock providers/blocs.
- Build configurations: Parse pubspec.yaml for dependencies and constraints; flag incompatible versions.

[No sources needed since this section provides general guidance]