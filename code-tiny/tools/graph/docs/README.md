# Graph Database Abstraction Layer

This package provides a database-agnostic abstraction layer for graph operations in the code analysis system.

## 🎯 Purpose

Consolidates 11 duplicated `Neo4jWriter` classes (~500 lines each) into a single, reusable abstraction layer. **Saves ~5,000 lines of code** while making the system database-agnostic.

## 📁 Architecture

````
tools/graph/
├── __init__.py              # Public API exports
├── base.py                  # Abstract base classes (GraphDriver, QueryExecutor)
├── neo4j_driver.py          # Neo4j implementation
├── factory.py               # Driver factory (GraphProvider enum)
├── language_writer.py       # ✨ NEW - Unified writer for all languages
├── record_parsers.py        # Data parsing utilities
├── operations/              # Domain-specific operations
│   ├── __init__.py
│   ├── function_ops.py      # Function/method operations
│   ├── document_ops.py      # Documentation operations
│   ├── infra_ops.py         # Infrastructure/module operations (Phase 3)
│   ├── cross_edge_ops.py    # Cross-reference operations
│   ├── package_ops.py       # ✨ NEW - Package operations (Java/Kotlin/Python)
│   ├── class_ops.py         # ✨ NEW - Class/OOP operations
│   ├── namespace_ops.py     # ✨ NEW - Namespace operations (C++/C#)
│   └── type_ops.py          # ✨ NEW - Type operations (typed languages)
├── README.md                # This file
├── QUICK_REFERENCE.md       # ✨ NEW - Migration quick reference
├── MIGRATION_GUIDE.py       # ✨ NEW - Detailed migration steps
└── example_usage.py         # Usage examples

## Design Principles

### 1. Abstraction Layer (Repository Pattern)
- All database operations go through abstract interfaces
- Easy to swap Neo4j for Kuzu, FalkorDB, etc. in the future
- No direct database queries scattered throughout the codebase

### 2. Domain-Driven Design
- Operations split by entity type (functions, documents, infrastructure)
- Each operation file is a bounded context
- Clear separation of concerns

### 3. Future-Proof
- Support for multiple graph databases through single interface
- Extensible for new entity types and operations
- Easy to test with mock drivers

## Usage

### Basic Setup

```python
from tools.graph import GraphDriverFactory, GraphProvider

# Create driver from config
config = {
    "uri": "bolt://localhost:7687",
    "user": "neo4j",
    "password": "password",
    "database": "mydb"
}

driver = GraphDriverFactory.create_driver(GraphProvider.NEO4J, config)

# Or from environment variables
driver = GraphDriverFactory.create_from_env(GraphProvider.NEO4J)
````

### Function Operations

```python
from tools.graph.operations import FunctionNodeOperations

ops = FunctionNodeOperations()

# Create a function node
function_id = await ops.create_function_node(
    driver,
    {
        "id": "func_123",
        "name": "calculate_sum",
        "qualified_name": "math.utils.calculate_sum",
        "code": "def calculate_sum(a, b): return a + b",
        "language": "python",
        "file_path": "math/utils.py",
        "start_line": 10,
        "end_line": 11,
    }
)

# Link function calls
await ops.link_function_call(
    driver,
    caller_id="func_123",
    callee_id="func_456"
)

# Get functions needing summary
functions = await ops.get_functions_without_summary(driver, limit=100)

# Update summary
await ops.update_function_summary(
    driver,
    function_id="func_123",
    summary="Calculates the sum of two numbers"
)
```

### Document Operations

```python
from tools.graph.operations import DocumentNodeOperations

ops = DocumentNodeOperations()

# Create document
doc_id = await ops.create_document_node(
    driver,
    {
        "id": "doc_001",
        "title": "README",
        "file_path": "README.md",
        "content": "# Project Documentation...",
        "doc_type": "readme"
    }
)

# Create paragraph chunks
para_id = await ops.create_paragraph_node(
    driver,
    {
        "id": "para_001",
        "content": "This section explains...",
        "embedding": [0.1, 0.2, ...],  # Vector embedding
        "chunk_index": 0
    }
)

# Link them
await ops.link_document_to_paragraph(driver, doc_id, para_id)
```

### Infrastructure Operations (Phase 3)

```python
from tools.graph.operations import InfraNodeOperations

ops = InfraNodeOperations()

# Run Louvain clustering
communities = await ops.run_louvain_clustering(
    driver,
    label="Function",
    relationship="CALLS",
    min_community_size=5
)

# Create infrastructure nodes from communities
for community in communities:
    infra_id = await ops.create_infra_node(
        driver,
        {
            "id": f"module_{community['communityId']}",
            "name": f"Module {community['communityId']}",
            "type": "module",
            "description": "",
            "module_path": "",
            "cohesion_score": 0.0,
            "coupling_score": 0.0,
            "status": "pending_summary"
        }
    )

    # Link members to infrastructure
    for member in community['members']:
        await ops.link_node_to_infra(
            driver,
            node_id=member['id'],
            infra_id=infra_id
        )

# Calculate metrics
metrics = await ops.calculate_module_metrics(driver, infra_id)
print(f"Cohesion: {metrics['cohesion_score']}")
print(f"Coupling: {metrics['coupling_score']}")
```

### Cross-Edge Operations

```python
from tools.graph.operations import CrossEdgeOperations

ops = CrossEdgeOperations()

# Link code to documentation
await ops.link_code_to_document(
    driver,
    code_id="func_123",
    document_id="para_001",
    link_type="IMPLEMENTS_LOGIC",
    confidence=0.95
)

# Find code without documentation
undocumented = await ops.find_code_without_documentation(
    driver,
    code_label="Function",
    limit=50
)
```

## Extending for New Databases

To add support for a new database (e.g., KuzuDB):

1. Create `tools/graph/kuzu_driver.py`:

```python
from tools.graph.base import GraphDriver, GraphProvider

class KuzuDriver(GraphDriver):
    @property
    def provider(self) -> GraphProvider:
        return GraphProvider.KUZU

    # Implement all abstract methods...
```

2. Update `factory.py`:

```python
def create_driver(provider: GraphProvider, config: Dict[str, Any]) -> GraphDriver:
    if provider == GraphProvider.KUZU:
        return KuzuDriver(**config)
    # ...
```

3. Existing code continues to work without changes!

## Testing

Use mock drivers for testing:

```python
from unittest.mock import AsyncMock
from tools.graph.base import GraphDriver, GraphProvider

class MockDriver(GraphDriver):
    def __init__(self):
        self.queries = []

    @property
    def provider(self) -> GraphProvider:
        return GraphProvider.NEO4J

    async def execute_query(self, query, parameters=None, database=None):
        self.queries.append((query, parameters))
        return [], [], None

# Use in tests
mock_driver = MockDriver()
await ops.create_function_node(mock_driver, {...})
assert len(mock_driver.queries) == 1
```

## Migration from Old Code

Old pattern (scattered throughout analyzers):

```python
# IN kotlin_analyzer.py, java_analyzer.py, etc.
driver = GraphDatabase.driver(uri, auth=(user, password))
with driver.session(database=db) as session:
    session.run("CREATE (f:Function {...})", ...)
```

New pattern (clean separation):

```python
# In any analyzer
from tools.graph import GraphDriverFactory, GraphProvider
from tools.graph.operations import FunctionNodeOperations

driver = GraphDriverFactory.create_from_env(GraphProvider.NEO4J)
ops = FunctionNodeOperations()
await ops.create_function_node(driver, function_data)
```

## Benefits

1. **Testability**: Easy to mock drivers for unit tests
2. **Maintainability**: All queries in one place, not scattered
3. **Flexibility**: Swap databases without rewriting logic
4. **Readability**: Clear semantic operations instead of raw Cypher
5. **Type Safety**: Strongly typed interfaces catch errors early
6. **Future-Proof**: Easy to add new databases or operations
