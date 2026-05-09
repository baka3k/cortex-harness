# Graph Code Architecture Design

## Overview

This document describes the architecture of the Graph Code system after the abstraction layer migration (February 2026). The system provides multi-language code analysis with graph database storage and semantic search capabilities.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          CLIENT LAYER                                    │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐     │
│  │   MCP Servers    │  │  CLI Tools       │  │  External APIs   │     │
│  │  (FastMCP)       │  │  (Analyzers)     │  │                  │     │
│  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘     │
│           │                     │                      │                │
└───────────┼─────────────────────┼──────────────────────┼────────────────┘
            │                     │                      │
            └─────────────────────┴──────────────────────┘
                                  │
┌─────────────────────────────────┼─────────────────────────────────────┐
│                      ABSTRACTION LAYER                                 │
├─────────────────────────────────┴─────────────────────────────────────┤
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │              GraphDriverFactory                                 │  │
│  │  ┌──────────────────────────────────────────────────────────┐  │  │
│  │  │  Creates drivers based on GraphProvider enum             │  │  │
│  │  │  • NEO4J    (✅ Implemented)                             │  │  │
│  │  │  • KUZU     (🔜 Placeholder)                             │  │  │
│  │  │  • FALKORDB (🔜 Placeholder)                             │  │  │
│  │  │  • NEPTUNE  (🔜 Placeholder)                             │  │  │
│  │  └──────────────────────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │              LanguageCodeWriter                                 │  │
│  │  ┌──────────────────────────────────────────────────────────┐  │  │
│  │  │  Unified writer for all language analyzers               │  │  │
│  │  │  • State management & batching                           │  │  │
│  │  │  • write_all() for batch operations                      │  │  │
│  │  │  • Replaces 11 duplicate Neo4jWriter classes             │  │  │
│  │  └──────────────────────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │              GraphDriver (Abstract Base)                        │  │
│  │  ┌──────────────────────────────────────────────────────────┐  │  │
│  │  │  Interface:                                              │  │  │
│  │  │  • execute_query(query, params, database)                │  │  │
│  │  │  • batch_write_nodes(label, nodes, database)             │  │  │
│  │  │  • batch_write_edges(rel_type, edges, database)          │  │  │
│  │  │  • close()                                               │  │  │
│  │  └──────────────────────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │              Operations Layer                                   │  │
│  │  ┌──────────────────────────────────────────────────────────┐  │  │
│  │  │  • FunctionNodeOperations    • PackageNodeOperations     │  │  │
│  │  │  • ClassNodeOperations        • NamespaceNodeOperations  │  │  │
│  │  │  • TypeNodeOperations         • DocumentNodeOperations   │  │  │
│  │  │  • InfraNodeOperations        • CrossEdgeOperations      │  │  │
│  │  └──────────────────────────────────────────────────────────┘  │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        DRIVER IMPLEMENTATIONS                            │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐     │
│  │  Neo4jDriver     │  │  KuzuDriver      │  │  FalkorDBDriver  │     │
│  │  (Production)    │  │  (Planned)       │  │  (Planned)       │     │
│  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘     │
│           │                     │                      │                │
└───────────┼─────────────────────┼──────────────────────┼────────────────┘
            │                     │                      │
            ▼                     ▼                      ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         DATABASE LAYER                                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐     │
│  │     Neo4j        │  │      Kuzu        │  │    FalkorDB      │     │
│  │  (Graph DB)      │  │  (Embedded)      │  │  (Redis-based)   │     │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘     │
│                                                                           │
│  ┌──────────────────────────────────────────────────────────────┐      │
│  │                    Qdrant                                     │      │
│  │                (Vector Database for Semantic Search)          │      │
│  └──────────────────────────────────────────────────────────────┘      │
│                                                                           │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Component Details

### 1. Language Analyzers (11 Analyzers)

**Purpose**: Parse source code and extract structured information

**Languages Supported**:

- Kotlin (`kotlin_analyzer.py`)
- C/C++ (`cplus_analyzer.py`)
- Android Kotlin (`android_kotlin_analyzer.py`)
- Java (`java_analyzer.py`)
- Python (`python_analyzer.py`)
- C# (`csharp_analyzer.py`)
- TypeScript (`ts_analyzer.py`)
- JavaScript (`js_analyzer.py`)
- PHP (`php_analyzer.py`)
- SQL (`sql_analyzer.py`)
- PL/SQL (`plsql_analyzer.py`)

**Architecture Pattern** (After Migration):

```python
# 1. Setup
driver = GraphDriverFactory.create_driver(
    GraphProvider.NEO4J,
    {"uri": uri, "user": user, "password": password, "database": db}
)
writer = LanguageCodeWriter(driver, batch_size=1000, verbose=True)

# 2. Parse & Collect
async def build_call_graph(...):
    all_packages = []
    all_classes = []
    all_functions = []
    all_relations = []
    all_calls = []

    for file in source_files:
        # Parse file and collect entities
        all_functions.extend(parsed_functions)
        all_classes.extend(parsed_classes)
        # ... collect all entities

    # 3. Batch Write
    if code_writer:
        await code_writer.write_all(
            packages=all_packages,
            classes=all_classes,
            functions=all_functions,
            relations=all_relations,
            calls=all_calls
        )

# 4. Cleanup
await driver.close()
```

**Benefits**:

- ✅ Database-agnostic (easy to switch backends)
- ✅ Clean async/await pattern
- ✅ Single batch write instead of streaming
- ✅ ~14% code reduction (3,990 lines removed)

---

### 2. Abstraction Layer

#### 2.1 GraphDriverFactory

**Location**: `tools/graph/factory.py`

**Purpose**: Create graph driver instances based on provider type

```python
class GraphProvider(Enum):
    NEO4J = "neo4j"
    KUZU = "kuzu"
    FALKORDB = "falkordb"
    NEPTUNE = "neptune"

class GraphDriverFactory:
    @staticmethod
    def create_driver(
        provider: GraphProvider,
        config: Dict[str, Any]
    ) -> GraphDriver:
        if provider == GraphProvider.NEO4J:
            return Neo4jDriver(config)
        elif provider == GraphProvider.KUZU:
            return KuzuDriver(config)
        # ... other providers
```

**Configuration Format**:

```python
# Neo4j
config = {
    "uri": "bolt://localhost:7687",
    "user": "neo4j",
    "password": "password",
    "database": "neo4j"  # optional
}

# Kuzu (planned)
config = {
    "database_path": "/path/to/db"
}
```

#### 2.2 GraphDriver (Abstract Base Class)

**Location**: `tools/graph/base.py`

**Interface**:

```python
class GraphDriver(ABC):
    @abstractmethod
    async def execute_query(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
        database: Optional[str] = None
    ) -> Tuple[List[Any], Any, List[str]]:
        """
        Execute a query and return (records, summary, keys)
        """
        pass

    @abstractmethod
    async def batch_write_nodes(
        self,
        label: str,
        nodes: List[Dict[str, Any]],
        database: Optional[str] = None
    ) -> int:
        """
        Write nodes in batch
        Returns: number of nodes written
        """
        pass

    @abstractmethod
    async def batch_write_edges(
        self,
        relationship_type: str,
        edges: List[Dict[str, Any]],
        database: Optional[str] = None
    ) -> int:
        """
        Write edges in batch
        Returns: number of edges written
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close driver connection"""
        pass
```

#### 2.3 LanguageCodeWriter

**Location**: `tools/graph/language_writer.py`

**Purpose**: Unified writer for all language analyzers

**Features**:

- State management for resume capability
- Automatic batching
- Progress logging
- Supports all entity types

**Usage**:

```python
writer = LanguageCodeWriter(
    driver=driver,
    database="neo4j",
    batch_size=1000,
    verbose=True
)

# Write all entities at once
counts = await writer.write_all(
    packages=packages,
    namespaces=namespaces,
    files=files,
    classes=classes,
    types=types,
    functions=functions,
    relations=relations,
    calls=calls,
    state=state,            # optional resume state
    state_writer=save_state # optional state persistence
)

# Returns: {"packages": 100, "classes": 500, "functions": 2000, ...}
```

**Supported Entity Types**:

- Packages
- Namespaces
- Files/Documents
- Classes/Types
- Functions
- Fields (C++)
- Aliases (C++)
- Templates (C++)
- Relations (generic edges)
- Calls (function call edges)

#### 2.4 Operations Layer

**Location**: `tools/graph/operations/`

**Purpose**: Encapsulate entity-specific operations

**Operations Available**:

- `FunctionNodeOperations` - Function CRUD operations
- `ClassNodeOperations` - Class/Type CRUD operations
- `PackageNodeOperations` - Package CRUD operations
- `NamespaceNodeOperations` - Namespace CRUD operations
- `TypeNodeOperations` - Type CRUD operations
- `DocumentNodeOperations` - File/Document operations
- `InfraNodeOperations` - Infrastructure nodes
- `CrossEdgeOperations` - Cross-project edges

**Example**:

```python
from tools.graph.operations.function_ops import FunctionNodeOperations

func_ops = FunctionNodeOperations()

# Create single function
await func_ops.create_function(
    driver=driver,
    function_data={
        "id": "com.example.MyClass.myMethod/0@Main.kt",
        "name": "myMethod",
        "qualified_name": "com.example.MyClass.myMethod",
        "code": "fun myMethod() { ... }"
    },
    database="neo4j"
)

# Batch create functions
await func_ops.batch_create_functions(
    driver=driver,
    functions=[func1, func2, func3, ...],
    database="neo4j"
)
```

---

### 3. MCP Servers (Model Context Protocol)

**Purpose**: Provide graph query capabilities via FastMCP

**Servers**:

- `mcp/android/android_mcp.py` - Android-specific queries
- `mcp/cplus/cplus_mcp.py` - C/C++-specific queries
- `mcp/java/java_mcp.py` - Java-specific queries
- `mcp/fastmcp_server.py` - Generic server
- `mcp/unified_mcp.py` - Unified orchestrator

**Architecture** (After Migration):

```python
from tools.graph import GraphDriverFactory, GraphProvider
from tools.graph.base import GraphDriver

_graph_driver: Optional[GraphDriver] = None

async def _get_graph_driver() -> GraphDriver:
    global _graph_driver
    if _graph_driver is not None:
        return _graph_driver

    config = {
        "uri": DEFAULT_NEO4J_URI,
        "user": DEFAULT_NEO4J_USER,
        "password": DEFAULT_NEO4J_PASSWORD,
    }
    _graph_driver = GraphDriverFactory.create_driver(GraphProvider.NEO4J, config)
    return _graph_driver

async def _run_cypher(query: str, params: Dict[str, Any], db: str):
    driver = await _get_graph_driver()
    records, summary, keys = await driver.execute_query(query, params, db)
    return [dict(record) for record in records]
```

**Tools Provided**:

- `activate_project` - Set parser type and database
- `search_functions` - Search by name/qualified_name
- `get_symbol` - Get node by ID
- `query_subgraph` - Get call graph context
- `find_paths` - Find call paths between functions
- `trace_flow` - Trace execution flow
- `semantic_search` - Vector-based search (Qdrant)
- `annotate_node` - Add annotations
- And 15+ more tools...

**Benefits** (After Migration):

- ✅ True async (no thread pool wrappers)
- ✅ Database-agnostic queries
- ✅ Consistent with abstraction layer
- ✅ Easier to add new databases

---

## Data Flow

### Analysis Flow

```
Source Code Files
      │
      ▼
┌─────────────────┐
│ Tree-sitter     │
│ Parser          │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Language        │
│ Analyzer        │
│ (e.g., Kotlin)  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐      ┌──────────────────┐
│ Collect         │      │ Parse Cache      │
│ Entities        │◄────►│ (Optional)       │
│ (in memory)     │      └──────────────────┘
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Language        │
│ CodeWriter      │
│ .write_all()    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ GraphDriver     │
│ (Neo4j)         │
└────────┬────────┘
         │
         ▼
┌─────────────────┐      ┌──────────────────┐
│ Neo4j           │      │ Embedder         │
│ Database        │      │ (Jina-v3)        │
└─────────────────┘      └────────┬─────────┘
                                  │
                                  ▼
                         ┌──────────────────┐
                         │ Qdrant           │
                         │ Vector DB        │
                         └──────────────────┘
```

### Query Flow (MCP)

```
MCP Client (Claude/Cursor)
      │
      ▼
┌─────────────────┐
│ FastMCP         │
│ Tool Call       │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ MCP Server      │
│ Tool Handler    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ GraphDriver     │
│ .execute_query()│
└────────┬────────┘
         │
         ▼
┌─────────────────┐      ┌──────────────────┐
│ Neo4j           │      │ Qdrant           │
│ (Graph Query)   │      │ (Vector Search)  │
└────────┬────────┘      └────────┬─────────┘
         │                        │
         └────────┬───────────────┘
                  ▼
         ┌─────────────────┐
         │ Result          │
         │ Processing      │
         └────────┬────────┘
                  │
                  ▼
         ┌─────────────────┐
         │ JSON Response   │
         │ (to MCP Client) │
         └─────────────────┘
```

---

## Database Schema

### Node Types

```
┌────────────────┐
│    Project     │ - Project metadata
└────────────────┘
        │
        ├──CONTAINS──┬────────────────┐
        │            │    Package     │ - Package/module
        │            └────────────────┘
        │                    │
        │                    ├──CONTAINS──┬────────────────┐
        │                    │            │   Namespace    │
        │                    │            └────────────────┘
        │                    │
        ├──CONTAINS──┬────────────────┐
        │            │      File      │ - Source file
        │            └────────────────┘
        │                    │
        │                    ├──DECLARES──┬────────────────┐
        │                    │            │     Class      │
        │                    │            │     (Type)     │
        │                    │            └────────────────┘
        │                    │                    │
        │                    │                    ├──DECLARES──┬────────────────┐
        │                    │                    │            │   Function     │
        │                    │                    │            └────────────────┘
        │                    │                    │
        │                    ├──DECLARES──────────┘
        │                    │
        │                    └──USES_RESOURCE────┬────────────────┐
        │                                        │   Resource     │
        │                                        │   (Android)    │
        │                                        └────────────────┘
        │
        └──CONTAINS──┬────────────────┐
                     │  Infrastructure│ - Build files, configs
                     └────────────────┘
```

### Relationship Types

**Core Relationships**:

- `CONTAINS` - Hierarchical containment
- `DECLARES` - Declaration relationship
- `CALLS` - Function call
- `IMPLEMENTS` - Interface implementation
- `EXTENDS` - Class inheritance
- `USES_TYPE` - Type usage
- `DEPENDS_ON` - Dependency

**C++ Specific**:

- `POINTER_TO` - Pointer relationship
- `ALIASES` - Type alias
- `TEMPLATES` - Template usage
- `CALLS_FUNCTION_POINTER` - Function pointer call
- `POSSIBLE_CALLS` - Virtual dispatch

**Android Specific**:

- `USES_RESOURCE` - Resource usage
- `STARTS_COMPONENT` - Activity/Service start
- `SENDS_BROADCAST` - Broadcast intent
- `REGISTERS_RECEIVER` - Receiver registration
- `DECLARES_ROUTE` - Route declaration
- `ANNOTATED_WITH` - Annotation usage

---

## Configuration

### Environment Variables

```bash
# Neo4j Configuration
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
NEO4J_DB=neo4j

# Qdrant Configuration
QDRANT_URL=http://localhost:6333
QDRANT_COLLECTION=kotlin_functions

# Embedding Model
EMBED_MODEL=jinaai/jina-embeddings-v3
JINA_MODEL_PATH=/path/to/local/model
EMBED_DEVICE=cpu

# MCP Configuration
MCP_BACKEND_TIMEOUT=60
MCP_FASTMCP_TRANSPORT=streamable-http

# Project Configuration
PROJECT_ID=my-project
PROJECT_NAME=My Project
PROJECT_LANGUAGE=kotlin
PROJECT_REPO=/path/to/repo
```

### Analyzer Usage

```bash
# Kotlin
python tools/kotlin/kotlin_analyzer.py \
  --root /path/to/kotlin/project \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-pass password \
  --qdrant-url http://localhost:6333 \
  --verbose

# C++
python tools/cplus/cplus_analyzer.py \
  --root /path/to/cpp/project \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-pass password \
  --verbose
```

### MCP Server Usage

```bash
# Start unified MCP server
./mcp.sh

# Or start specific backend
python mcp/android/android_mcp.py --transport streamable-http
```

---

## Migration Summary

### Before Migration

```
┌──────────────────────────────────────┐
│  11 Language Analyzers               │
│  Each with hardcoded Neo4jWriter     │
│  ~500 lines of duplicate code each   │
└──────────┬───────────────────────────┘
           │
           ├─► Direct Neo4j driver usage
           ├─► Manual session management
           ├─► Streaming write logic
           └─► Database-specific code
```

**Problems**:

- ❌ 11 duplicate Neo4jWriter classes (~5,500 lines)
- ❌ Hardcoded Neo4j dependency
- ❌ Cannot switch databases
- ❌ Inconsistent behavior across analyzers

### After Migration

```
┌──────────────────────────────────────┐
│  11 Language Analyzers               │
│  Using LanguageCodeWriter            │
│  Abstraction layer                   │
└──────────┬───────────────────────────┘
           │
           ├─► GraphDriverFactory
           ├─► Database-agnostic
           ├─► Batch write pattern
           └─► Consistent API
```

**Benefits**:

- ✅ 3,990 lines removed (14% reduction)
- ✅ Single source of truth (LanguageCodeWriter)
- ✅ Easy to add new databases (Kuzu, FalkorDB, etc.)
- ✅ Consistent behavior across all analyzers
- ✅ Clean async/await pattern
- ✅ MCP servers also migrated (true async)

### Line Count Changes

| Component                 | Before | After  | Removed | % Change |
| ------------------------- | ------ | ------ | ------- | -------- |
| **Analyzers (11 files)**  | 28,510 | 24,520 | 3,990   | -14.0%   |
| **MCP Servers (4 files)** | -      | -      | ~100    | Improved |
| **Abstraction Layer**     | 0      | ~2,350 | +2,350  | New      |
| **Net Change**            | 28,510 | 26,870 | 1,640   | -5.8%    |

---

## Future Enhancements

### 1. Additional Database Support

**Kuzu** (Embedded Graph Database):

```python
class KuzuDriver(GraphDriver):
    async def execute_query(self, query, params, database):
        # Kuzu-specific implementation
        pass
```

**FalkorDB** (Redis-based):

```python
class FalkorDBDriver(GraphDriver):
    async def execute_query(self, query, params, database):
        # FalkorDB-specific implementation
        pass
```

### 2. Community Detection

Following Graphiti's pattern, add:

- `CommunityNodeOperations` - Community node CRUD
- `CommunityEdgeOperations` - Community edge CRUD
- Louvain algorithm integration
- Community-based queries

### 3. Multi-Database Federation

Support querying across multiple databases:

```python
# Query from both Neo4j and Kuzu
results = await multi_query([
    (neo4j_driver, query1),
    (kuzu_driver, query2)
])
```

### 4. Streaming Write Mode

Add streaming option to LanguageCodeWriter:

```python
writer = LanguageCodeWriter(
    driver=driver,
    streaming=True,  # Write incrementally
    batch_size=1000
)

# Add entities incrementally
await writer.add_function(function_data)
await writer.add_class(class_data)

# Flush at the end
await writer.flush()
```

### 5. Query Optimization

- Query caching layer
- Connection pooling
- Batch query optimization
- Index recommendations

---

## Testing Strategy

### Unit Tests

- Test each operation in isolation
- Mock driver for fast tests
- Validate query generation

### Integration Tests

- Test with real Neo4j instance
- Test analyzer end-to-end
- Verify MCP tool responses

### Performance Tests

- Large codebase analysis
- Batch write performance
- Query response times
- Memory usage profiling

---

## Monitoring & Observability

### Metrics to Track

- Analysis duration per language
- Number of entities extracted
- Batch write performance
- Query latency
- Database connection pool usage

### Logging

- Structured logging (JSON)
- Log levels: DEBUG, INFO, WARN, ERROR
- Context propagation (request ID)
- Performance markers

---

## Security Considerations

### Credentials Management

- Use environment variables
- Support .env files
- Never commit credentials
- Rotate passwords regularly

### Query Injection Prevention

- Parameterized queries only
- Validate all inputs
- Sanitize user-provided data
- Rate limiting on MCP endpoints

### Access Control

- Database-level authentication
- Role-based access (Neo4j)
- API key authentication (optional)
- Audit logging

---

## Conclusion

The abstraction layer migration successfully achieved:

1. **Code Quality**: 14% reduction in analyzer code
2. **Maintainability**: Single source of truth for graph operations
3. **Flexibility**: Easy to add new database backends
4. **Consistency**: All components use same patterns
5. **Performance**: True async throughout the stack
6. **Future-Proof**: Ready for multi-database support

The system is now production-ready with a clean, extensible architecture that supports current needs while being prepared for future growth.

---

**Document Version**: 1.0  
**Last Updated**: February 23, 2026  
**Status**: ✅ Complete Migration
