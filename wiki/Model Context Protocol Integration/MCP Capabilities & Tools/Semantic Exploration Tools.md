# Semantic Exploration Tools

<cite>
**Referenced Files in This Document**
- [semantic_graph_expansion.py](file://code-tiny/mcp/semantic_graph_expansion.py)
- [query_intent_classifier.py](file://code-tiny/tools/common/query_intent_classifier.py)
- [intelligent_retrieval.py](file://code-tiny/tools/common/intelligent_retrieval.py)
- [unified_mcp.py](file://code-tiny/mcp/unified_mcp.py)
- [explore_service.py](file://code-tiny/mcp/services/explore_service.py)
- [graph_service.py](file://code-tiny/mcp/services/graph_service.py)
- [symbol_service.py](file://code-tiny/mcp/services/symbol_service.py)
- [impact_service.py](file://code-tiny/mcp/services/impact_service.py)
- [workflow_service.py](file://code-tiny/mcp/services/workflow_service.py)
- [flow_reconstructor.py](file://code-tiny/mcp/services/flow_reconstructor.py)
- [tool_metadata.py](file://code-tiny/mcp/tool_metadata.py)
- [framework_registry.py](file://code-tiny/mcp/framework_registry.py)
- [fastmcp_server.py](file://code-tiny/mcp/fastmcp_server.py)
- [graph_expander.py](file://code-tiny/tools/common/graph_expander.py)
- [query_understanding.py](file://code-tiny/tools/common/query_understanding.py)
- [semantic_inference.py](file://code-tiny/tools/common/semantic_inference.py)
- [retrieval_scorer.py](file://code-tiny/tools/common/retrieval_scorer.py)
- [bm25_ranker.py](file://code-tiny/tools/common/bm25_ranker.py)
- [confidence_scorer.py](file://code-tiny/tools/common/confidence_scorer.py)
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

Cortex Harness MCP provides a sophisticated semantic exploration system that enables natural language interaction with code semantics and relationships. The system combines graph-based code analysis, intent classification, and intelligent query routing to deliver powerful semantic search and exploration capabilities across multiple programming languages and frameworks.

The semantic exploration tools are designed to help developers understand complex codebases through intuitive queries, enabling them to explore relationships between components, trace execution flows, and discover architectural patterns without requiring deep knowledge of the underlying code structure.

## Project Structure

The semantic exploration system is organized into several key layers:

```mermaid
graph TB
subgraph "MCP Layer"
A[Unified MCP Server]
B[Framework Registry]
C[Tool Metadata]
end
subgraph "Service Layer"
D[Explore Service]
E[Graph Service]
F[Symbol Service]
G[Impact Service]
H[Workflow Service]
I[Flow Reconstructor]
end
subgraph "Common Tools"
J[Semantic Graph Expansion]
K[Query Intent Classifier]
L[Intelligent Retrieval]
M[Query Understanding]
N[Semantic Inference]
O[Retrieval Scorer]
P[Benchmark Ranker]
Q[Confidence Scorer]
end
subgraph "Language Support"
R[Android Services]
S[C++ Services]
T[Java Services]
end
A --> D
A --> E
A --> F
A --> G
A --> H
A --> I
D --> J
D --> K
D --> L
E --> J
F --> J
G --> J
H --> J
I --> J
J --> M
J --> N
J --> O
J --> P
J --> Q
R --> D
S --> D
T --> D
```

**Diagram sources**
- [unified_mcp.py](file://code-tiny/mcp/unified_mcp.py)
- [explore_service.py](file://code-tiny/mcp/services/explore_service.py)
- [semantic_graph_expansion.py](file://code-tiny/mcp/semantic_graph_expansion.py)
- [query_intent_classifier.py](file://code-tiny/tools/common/query_intent_classifier.py)
- [intelligent_retrieval.py](file://code-tiny/tools/common/intelligent_retrieval.py)

**Section sources**
- [unified_mcp.py](file://code-tiny/mcp/unified_mcp.py)
- [framework_registry.py](file://code-tiny/mcp/framework_registry.py)
- [tool_metadata.py](file://code-tiny/mcp/tool_metadata.py)

## Core Components

### Semantic Graph Expansion System

The semantic graph expansion system serves as the core engine for exploring code relationships and dependencies. It provides intelligent traversal algorithms that can expand from seed nodes to discover related entities, following various relationship types and applying context-aware filtering.

Key features include:
- **Multi-dimensional expansion**: Supports expansion along different relationship types (imports, calls, inheritance, etc.)
- **Context-aware filtering**: Applies semantic filters based on query context and user preferences
- **Progressive discovery**: Implements depth-limited exploration with configurable expansion strategies
- **Relationship weighting**: Prioritizes important connections based on semantic relevance

### Intent Classification Engine

The intent classification system analyzes natural language queries to determine the user's exploration goals and routes them to appropriate services. It supports multiple intent categories:

- **Structural queries**: Understanding code organization and dependencies
- **Behavioral queries**: Tracing execution flows and call patterns  
- **Impact analysis**: Assessing change propagation and risk assessment
- **Symbol exploration**: Finding specific code elements and their relationships
- **Workflow analysis**: Understanding business logic and process flows

### Intelligent Query Routing

The query routing system acts as an intelligent dispatcher that:
- Parses natural language queries using advanced NLP techniques
- Maps intents to specific service endpoints
- Optimizes query execution paths based on available data
- Provides fallback mechanisms when primary routes fail
- Maintains context across multi-step explorations

**Section sources**
- [semantic_graph_expansion.py](file://code-tiny/mcp/semantic_graph_expansion.py)
- [query_intent_classifier.py](file://code-tiny/tools/common/query_intent_classifier.py)
- [intelligent_retrieval.py](file://code-tiny/tools/common/intelligent_retrieval.py)

## Architecture Overview

The semantic exploration architecture follows a layered approach with clear separation of concerns:

```mermaid
sequenceDiagram
participant Client as "Client Application"
participant MCP as "Unified MCP Server"
participant Router as "Query Router"
participant Classifier as "Intent Classifier"
participant Explorer as "Semantic Explorer"
participant GraphDB as "Graph Database"
participant VectorStore as "Vector Store"
Client->>MCP : Natural Language Query
MCP->>Router : Parse & Route Query
Router->>Classifier : Classify Intent
Classifier-->>Router : Intent + Confidence Score
Router->>Explorer : Execute Strategy
Explorer->>GraphDB : Query Code Relationships
Explorer->>VectorStore : Semantic Search
GraphDB-->>Explorer : Relationship Data
VectorStore-->>Explorer : Similar Entities
Explorer->>Explorer : Apply Filters & Ranking
Explorer-->>Router : Results
Router-->>MCP : Formatted Response
MCP-->>Client : Exploration Results
Note over Explorer,VectorStore : Context-aware expansion with hybrid retrieval
```

**Diagram sources**
- [unified_mcp.py](file://code-tiny/mcp/unified_mcp.py)
- [explore_service.py](file://code-tiny/mcp/services/explore_service.py)
- [semantic_graph_expansion.py](file://code-tiny/mcp/semantic_graph_expansion.py)

The architecture integrates multiple data sources and processing pipelines:

```mermaid
graph LR
subgraph "Input Processing"
A[Natural Language Query]
B[Query Parser]
C[Intent Classifier]
end
subgraph "Data Sources"
D[Code Graph]
E[Symbol Index]
F[Vector Embeddings]
G[Metadata Store]
end
subgraph "Processing Pipeline"
H[Query Understanding]
I[Strategy Selection]
J[Execution Engine]
K[Result Fusion]
end
subgraph "Output Generation"
L[Response Builder]
M[Visualization Data]
N[Context Packager]
end
A --> B --> C
C --> H --> I --> J
J --> D
J --> E
J --> F
J --> G
D --> K --> L
E --> K
F --> K
G --> K
L --> M
L --> N
```

**Diagram sources**
- [query_understanding.py](file://code-tiny/tools/common/query_understanding.py)
- [semantic_inference.py](file://code-tiny/tools/common/semantic_inference.py)
- [intelligent_retrieval.py](file://code-tiny/tools/common/intelligent_retrieval.py)

## Detailed Component Analysis

### Semantic Graph Expansion Engine

The semantic graph expansion engine implements sophisticated algorithms for navigating code relationships:

```mermaid
classDiagram
class SemanticGraphExpander {
-graph_service GraphService
-context Context
-strategy ExpansionStrategy
-filters Filter[]
+expand(seed_nodes, options) GraphExpansion
+apply_filters(nodes) FilteredNodes
+calculate_weights(edges) WeightedEdges
-validate_context(context) bool
-optimize_traversal(strategy) void
}
class ExpansionStrategy {
<<interface>>
+execute(expander, seeds) Node[]
+get_complexity() string
+supports_filtering() bool
}
class BreadthFirstStrategy {
+execute(expander, seeds) Node[]
+get_complexity() string
}
class DepthFirstStrategy {
+execute(expander, seeds) Node[]
+get_complexity() string
}
class ContextAwareStrategy {
-intent Intent
-scope Scope
+execute(expander, seeds) Node[]
+get_complexity() string
}
class Filter {
<<interface>>
+applies_to(node) bool
+weight_modifier(node) float
}
class RelationshipFilter {
+applies_to(node) bool
+weight_modifier(node) float
}
class TypeFilter {
+applies_to(node) bool
+weight_modifier(node) float
}
SemanticGraphExpander --> ExpansionStrategy : uses
SemanticGraphExpander --> Filter : applies
ExpansionStrategy <|-- BreadthFirstStrategy
ExpansionStrategy <|-- DepthFirstStrategy
ExpansionStrategy <|-- ContextAwareStrategy
Filter <|-- RelationshipFilter
Filter <|-- TypeFilter
```

**Diagram sources**
- [semantic_graph_expansion.py](file://code-tiny/mcp/semantic_graph_expansion.py)
- [graph_expander.py](file://code-tiny/tools/common/graph_expander.py)

#### Key Algorithms

The expansion engine implements several core algorithms:

1. **Weighted Path Discovery**: Uses edge weights and node importance scores to prioritize meaningful relationships
2. **Contextual Pruning**: Eliminates irrelevant branches early in the traversal process
3. **Adaptive Depth Control**: Dynamically adjusts exploration depth based on result density and query complexity
4. **Parallel Expansion**: Concurrently explores multiple relationship types for improved performance

**Section sources**
- [semantic_graph_expansion.py](file://code-tiny/mcp/semantic_graph_expansion.py)
- [graph_expander.py](file://code-tiny/tools/common/graph_expander.py)

### Intent Classification System

The intent classification system employs machine learning models and rule-based heuristics to understand user queries:

```mermaid
flowchart TD
A[Raw Query] --> B[Preprocessing]
B --> C[Feature Extraction]
C --> D{Intent Classifier}
D --> |Structural| E[Structure Analysis]
D --> |Behavioral| F[Flow Analysis]
D --> |Impact| G[Impact Assessment]
D --> |Symbol| H[Symbol Lookup]
D --> |Workflow| I[Process Mapping]
E --> J[Service Router]
F --> J
G --> J
H --> J
I --> J
J --> K[Confidence Scoring]
K --> L{High Confidence?}
L --> |Yes| M[Execute Primary Strategy]
L --> |No| N[Fallback Strategy]
M --> O[Result Processing]
N --> O
```

**Diagram sources**
- [query_intent_classifier.py](file://code-tiny/tools/common/query_intent_classifier.py)
- [query_understanding.py](file://code-tiny/tools/common/query_understanding.py)

#### Supported Intent Categories

The classifier recognizes several primary intent types:

- **Structural Analysis**: Understanding code organization, module relationships, and architectural patterns
- **Behavioral Tracing**: Following execution flows, method calls, and data transformations
- **Impact Assessment**: Evaluating the effects of changes and identifying affected components
- **Symbol Exploration**: Locating specific code elements and understanding their usage
- **Workflow Discovery**: Mapping business processes and application workflows

**Section sources**
- [query_intent_classifier.py](file://code-tiny/tools/common/query_intent_classifier.py)
- [query_understanding.py](file://code-tiny/tools/common/query_understanding.py)

### Intelligent Query Routing

The query routing system provides intelligent dispatch and optimization:

```mermaid
sequenceDiagram
participant Client as "Client"
participant Router as "Query Router"
participant Cache as "Query Cache"
participant Validator as "Query Validator"
participant Optimizer as "Query Optimizer"
participant Executor as "Service Executor"
Client->>Router : Structured Query
Router->>Cache : Check Cached Results
alt Cache Hit
Cache-->>Router : Cached Response
Router-->>Client : Cached Results
else Cache Miss
Router->>Validator : Validate Query Schema
Validator-->>Router : Validation Result
Router->>Optimizer : Optimize Execution Plan
Optimizer->>Optimizer : Select Best Strategy
Optimizer->>Executor : Execute Optimized Query
Executor->>Executor : Parallel Service Calls
Executor-->>Optimizer : Combined Results
Optimizer->>Cache : Store Results
Optimizer-->>Router : Final Response
Router-->>Client : Optimized Results
end
```

**Diagram sources**
- [unified_mcp.py](file://code-tiny/mcp/unified_mcp.py)
- [explore_service.py](file://code-tiny/mcp/services/explore_service.py)

#### Query Optimization Strategies

The routing system implements several optimization techniques:

1. **Intelligent Caching**: Caches frequently accessed query results with TTL-based expiration
2. **Parallel Execution**: Executes independent operations concurrently
3. **Lazy Loading**: Defers expensive operations until results are actually needed
4. **Result Streaming**: Returns partial results while continuing computation
5. **Adaptive Throttling**: Adjusts request rates based on system load

**Section sources**
- [unified_mcp.py](file://code-tiny/mcp/unified_mcp.py)
- [explore_service.py](file://code-tiny/mcp/services/explore_service.py)

### Service Layer Components

The service layer provides specialized functionality for different aspects of code exploration:

#### Explore Service

The explore service orchestrates semantic exploration workflows:

```mermaid
classDiagram
class ExploreService {
-graph_service GraphService
-symbol_service SymbolService
-impact_service ImpactService
-workflow_service WorkflowService
-flow_reconstructor FlowReconstructor
+explore(query, options) ExplorationResult
+build_workflow(query) WorkflowPlan
+trace_flow(start_node, end_node) FlowPath
+analyze_impact(target_node, scope) ImpactAnalysis
-validate_query(query) bool
-merge_results(results) UnifiedResult
}
class GraphService {
+get_relationships(node_id, relation_types) Relationships
+find_paths(start_id, end_id, max_depth) Paths
+subgraph_query(query) Subgraph
+batch_operations(operations) BatchResult
}
class SymbolService {
+resolve_symbol(symbol_name, context) SymbolInfo
+find_references(symbol_id) References
+search_symbols(pattern, filters) SymbolResults
+get_symbol_hierarchy(symbol_id) Hierarchy
}
class ImpactService {
+calculate_impact(target_id, scope) ImpactReport
+find_dependents(symbol_id) Dependents
+assess_change_risk(changes) RiskAssessment
+generate_impact_report(report_data) Report
}
ExploreService --> GraphService : uses
ExploreService --> SymbolService : uses
ExploreService --> ImpactService : uses
ExploreService --> WorkflowService : uses
ExploreService --> FlowReconstructor : uses
```

**Diagram sources**
- [explore_service.py](file://code-tiny/mcp/services/explore_service.py)
- [graph_service.py](file://code-tiny/mcp/services/graph_service.py)
- [symbol_service.py](file://code-tiny/mcp/services/symbol_service.py)
- [impact_service.py](file://code-tiny/mcp/services/impact_service.py)
- [workflow_service.py](file://code-tiny/mcp/services/workflow_service.py)
- [flow_reconstructor.py](file://code-tiny/mcp/services/flow_reconstructor.py)

**Section sources**
- [explore_service.py](file://code-tiny/mcp/services/explore_service.py)
- [graph_service.py](file://code-tiny/mcp/services/graph_service.py)
- [symbol_service.py](file://code-tiny/mcp/services/symbol_service.py)
- [impact_service.py](file://code-tiny/mcp/services/impact_service.py)
- [workflow_service.py](file://code-tiny/mcp/services/workflow_service.py)
- [flow_reconstructor.py](file://code-tiny/mcp/services/flow_reconstructor.py)

## Dependency Analysis

The semantic exploration system has well-defined dependency relationships:

```mermaid
graph TD
subgraph "Core Dependencies"
A[Semantic Graph Expansion]
B[Intent Classification]
C[Intelligent Retrieval]
end
subgraph "Service Dependencies"
D[Explore Service]
E[Graph Service]
F[Symbol Service]
G[Impact Service]
end
subgraph "Common Utilities"
H[Query Understanding]
I[Semantic Inference]
J[Retrieval Scorer]
K[BM25 Ranker]
L[Confidence Scorer]
end
subgraph "External Systems"
M[Graph Database]
N[Vector Store]
O[LLM Services]
P[Cache Layer]
end
A --> H
A --> I
A --> J
A --> K
A --> L
B --> H
B --> I
C --> J
C --> K
C --> L
D --> A
D --> B
D --> C
E --> M
F --> M
G --> M
C --> N
C --> O
C --> P
```

**Diagram sources**
- [semantic_graph_expansion.py](file://code-tiny/mcp/semantic_graph_expansion.py)
- [query_intent_classifier.py](file://code-tiny/tools/common/query_intent_classifier.py)
- [intelligent_retrieval.py](file://code-tiny/tools/common/intelligent_retrieval.py)

### Coupling and Cohesion Analysis

The system demonstrates good architectural principles:

- **High Cohesion**: Each component has a focused responsibility
- **Low Coupling**: Clear interfaces minimize inter-component dependencies
- **Layered Architecture**: Separation between presentation, business logic, and data access
- **Plugin Architecture**: Extensible design for new language support and analysis types

**Section sources**
- [semantic_graph_expansion.py](file://code-tiny/mcp/semantic_graph_expansion.py)
- [unified_mcp.py](file://code-tiny/mcp/unified_mcp.py)

## Performance Considerations

### Query Optimization Strategies

The system implements several performance optimization techniques:

1. **Hybrid Retrieval**: Combines exact matching with semantic similarity search
2. **Result Caching**: Implements intelligent caching with adaptive expiration policies
3. **Lazy Evaluation**: Defers expensive computations until results are needed
4. **Batch Operations**: Groups related database queries for improved efficiency
5. **Streaming Responses**: Returns partial results while continuing computation

### Memory Management

Memory usage is optimized through:

- **Streaming Processing**: Processes large datasets incrementally
- **Result Pagination**: Limits result set sizes with cursor-based pagination
- **Garbage Collection**: Explicit cleanup of temporary objects
- **Connection Pooling**: Efficient reuse of database and external service connections

### Scalability Patterns

The system supports horizontal scaling through:

- **Stateless Services**: Enables easy distribution across multiple instances
- **Distributed Caching**: Shared cache layer for consistent results
- **Load Balancing**: Automatic distribution of query load
- **Asynchronous Processing**: Background jobs for long-running operations

## Troubleshooting Guide

### Common Issues and Solutions

#### Query Performance Problems

**Symptoms**: Slow response times, high memory usage
**Solutions**:
- Enable query profiling to identify bottlenecks
- Adjust cache TTL values based on data volatility
- Implement query result limits and pagination
- Monitor database connection pool utilization

#### Intent Classification Errors

**Symptoms**: Incorrect intent routing, low confidence scores
**Solutions**:
- Review query preprocessing pipeline
- Update intent classification models with more training data
- Implement fallback strategies for ambiguous queries
- Add query validation and error handling

#### Graph Traversal Issues

**Symptoms**: Missing relationships, incomplete results
**Solutions**:
- Verify graph database connectivity and schema
- Check relationship type mappings and filters
- Increase traversal depth limits if necessary
- Validate symbol resolution and indexing

### Debugging Techniques

Enable detailed logging for troubleshooting:

1. **Query Logging**: Capture raw queries and parsed intents
2. **Performance Metrics**: Track execution times and resource usage
3. **Error Tracking**: Log exceptions and recovery actions
4. **Cache Monitoring**: Observe hit rates and eviction patterns

**Section sources**
- [explore_service.py](file://code-tiny/mcp/services/explore_service.py)
- [unified_mcp.py](file://code-tiny/mcp/unified_mcp.py)

## Conclusion

The Cortex Harness MCP semantic exploration system provides a comprehensive solution for natural language code exploration. By combining advanced graph algorithms, intelligent intent classification, and optimized query routing, it enables developers to understand complex codebases through intuitive interactions.

The modular architecture ensures extensibility and maintainability, while the performance optimizations guarantee responsive interactions even with large codebases. The integration with LLM services enhances understanding capabilities, making the system particularly effective for complex semantic queries and contextual exploration.

Future enhancements could include additional language support, improved visualization capabilities, and enhanced collaborative features for team-based code exploration.

## Appendices

### Example Semantic Queries

#### Structural Exploration
- "Show me all classes that inherit from UserService"
- "Find dependencies between authentication modules"
- "Display the package hierarchy for the payment system"

#### Behavioral Analysis
- "Trace the execution flow from login API to database access"
- "Show all methods called by the order processing workflow"
- "Find error handling patterns in the notification service"

#### Impact Assessment
- "What would break if I modify the User model?"
- "Show downstream effects of changing the payment gateway interface"
- "Identify all components affected by database schema changes"

#### Symbol Exploration
- "Find all references to the calculateTax function"
- "Show the complete inheritance chain for PaymentProcessor"
- "Display configuration files used by the email service"

### Best Practices for Effective Queries

1. **Be Specific**: Include relevant context like module names or file paths
2. **Use Domain Terminology**: Leverage business domain language for better results
3. **Iterative Refinement**: Start broad and narrow down based on initial results
4. **Combine Approaches**: Mix structural and behavioral queries for comprehensive understanding
5. **Leverage Filters**: Use available filters to focus on relevant parts of the codebase