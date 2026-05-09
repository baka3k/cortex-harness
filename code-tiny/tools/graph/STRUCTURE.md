# Graph Abstraction Layer - Directory Structure

## Overview

This directory contains the graph database abstraction layer, organized into logical subdirectories for better maintainability.

```
tools/graph/
├── __init__.py                 # Main package exports
│
├── core/                       # Core abstractions and factory
│   ├── __init__.py
│   ├── base.py                # GraphDriver abstract base class
│   ├── factory.py             # GraphDriverFactory & GraphProvider enum
│   └── record_parsers.py      # Record parsing utilities
│
├── driver/                     # Database driver implementations
│   ├── __init__.py
│   └── neo4j_driver.py        # Neo4j driver implementation
│   # Future: kuzu_driver.py, falkordb_driver.py, neptune_driver.py
│
├── writer/                     # High-level writers
│   ├── __init__.py
│   └── language_writer.py     # LanguageCodeWriter (unified for all analyzers)
│
├── operations/                 # Entity-specific operations
│   ├── __init__.py
│   ├── class_ops.py           # Class/Type operations
│   ├── cross_edge_ops.py      # Cross-project edge operations
│   ├── document_ops.py        # File/Document operations
│   ├── function_ops.py        # Function operations
│   ├── infra_ops.py           # Infrastructure node operations
│   ├── namespace_ops.py       # Namespace operations
│   ├── package_ops.py         # Package operations
│   └── type_ops.py            # Type operations
│
├── docs/                       # Documentation
│   ├── __init__.py
│   ├── README.md              # Main documentation
│   ├── MIGRATION_GUIDE.py     # Migration guide for analyzers
│   ├── QUICK_REFERENCE.md     # Quick reference guide
│   └── IMPLEMENTATION_SUMMARY.md  # Implementation details
│
└── examples/                   # Usage examples
    ├── __init__.py
    └── example_usage.py       # Example code
```

## Import Patterns

### Public API (Recommended)

```python
# Import from main package
from tools.graph import GraphDriverFactory, GraphProvider, LanguageCodeWriter

# Create driver
driver = GraphDriverFactory.create_driver(
    GraphProvider.NEO4J,
    {"uri": "bolt://localhost:7687", "user": "neo4j", "password": "password"}
)

# Create writer
writer = LanguageCodeWriter(driver, batch_size=1000, verbose=True)
```

### Internal Imports (For Development)

```python
# Core components
from tools.graph.core.base import GraphDriver, QueryExecutor
from tools.graph.core.factory import GraphDriverFactory, GraphProvider
from tools.graph.core.record_parsers import parse_neo4j_record

# Driver implementations
from tools.graph.driver.neo4j_driver import Neo4jDriver

# Writer
from tools.graph.writer.language_writer import LanguageCodeWriter

# Operations
from tools.graph.operations.function_ops import FunctionNodeOperations
from tools.graph.operations.class_ops import ClassNodeOperations
```

## Component Responsibilities

### core/

**Purpose**: Foundational abstractions and factory pattern

- **base.py**: Abstract base classes (`GraphDriver`, `QueryExecutor`)
- **factory.py**: Driver creation and provider enumeration
- **record_parsers.py**: Utilities for parsing database records

### driver/

**Purpose**: Concrete database driver implementations

- **neo4j_driver.py**: Production Neo4j implementation
- Future drivers: Kuzu, FalkorDB, Neptune

### writer/

**Purpose**: High-level writer abstractions

- **language_writer.py**: Unified writer for all language analyzers
  - Replaces 11 duplicate `Neo4jWriter` classes
  - Provides batch writing with state management
  - Database-agnostic operations

### operations/

**Purpose**: Entity-specific CRUD operations

Each file provides operations for a specific entity type:

- Creating nodes/edges
- Batch operations
- Querying
- Updating

### docs/

**Purpose**: Documentation and guides

- Architecture documentation
- Migration guides
- Quick references
- Implementation summaries

### examples/

**Purpose**: Usage examples and tutorials

- Example code demonstrating API usage
- Integration examples
- Testing patterns

## Migration Notes

### From Old Structure (Pre-reorganization)

**Old imports:**

```python
from tools.graph.base import GraphDriver
from tools.graph.factory import GraphDriverFactory
from tools.graph.language_writer import LanguageCodeWriter
```

**New imports:**

```python
# Option 1: Use public API (recommended)
from tools.graph import GraphDriver, GraphDriverFactory, LanguageCodeWriter

# Option 2: Use explicit paths (for development)
from tools.graph.core.base import GraphDriver
from tools.graph.core.factory import GraphDriverFactory
from tools.graph.writer.language_writer import LanguageCodeWriter
```

The main `tools/graph/__init__.py` re-exports all public APIs, so external code can continue using simple imports.

## Dependencies

### External Dependencies

- `neo4j` - Neo4j Python driver (for Neo4jDriver)
- `typing` - Type hints
- `logging` - Logging utilities

### Internal Dependencies

- `tools.graph.core.base` - Used by all drivers and operations
- `tools.graph.operations.*` - Used by LanguageCodeWriter

## Testing

Tests should follow the same structure:

```
tests/
├── test_core/
│   ├── test_base.py
│   ├── test_factory.py
│   └── test_record_parsers.py
├── test_driver/
│   └── test_neo4j_driver.py
├── test_writer/
│   └── test_language_writer.py
└── test_operations/
    ├── test_function_ops.py
    ├── test_class_ops.py
    └── ...
```

## Extension Guide

### Adding a New Database Driver

1. Create `tools/graph/driver/your_driver.py`
2. Implement `GraphDriver` interface from `core.base`
3. Update `core/factory.py` to include your driver
4. Add enum value to `GraphProvider`
5. Update documentation

Example:

```python
# tools/graph/driver/kuzu_driver.py
from tools.graph.core.base import GraphDriver, GraphProvider

class KuzuDriver(GraphDriver):
    def __init__(self, config):
        # Initialize Kuzu connection
        pass

    async def execute_query(self, query, params, database):
        # Implement Kuzu query execution
        pass

    # ... implement other abstract methods
```

### Adding New Operations

1. Create `tools/graph/operations/your_entity_ops.py`
2. Define operation class with CRUD methods
3. Use `GraphDriver` for database operations
4. Update `LanguageCodeWriter` if needed

## Architecture Principles

1. **Separation of Concerns**: Core abstractions, drivers, writers, and operations are separated
2. **Database Agnostic**: All code uses `GraphDriver` interface, not specific implementations
3. **Single Responsibility**: Each module has one clear purpose
4. **Open/Closed**: Easy to extend (new drivers) without modifying existing code
5. **Dependency Inversion**: Depend on abstractions (`GraphDriver`), not concretions

## Version History

- **v2.0** (Feb 2026): Reorganized into subdirectories
  - Moved core files to `core/`
  - Moved drivers to `driver/`
  - Moved writer to `writer/`
  - Moved docs to `docs/`
  - Moved examples to `examples/`
  - Updated all imports across codebase

- **v1.0** (Feb 2026): Initial abstraction layer
  - Created GraphDriver interface
  - Implemented Neo4jDriver
  - Created LanguageCodeWriter
  - Migrated 11 analyzers
  - Migrated 5 MCP servers
