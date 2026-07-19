# Database Migration & Schema Management

<cite>
**Referenced Files in This Document**
- [README.md](file://ReadMe.md)
- [Makefile](file://Makefile)
- [pyproject.toml](file://pyproject.toml)
- [requirements.txt](file://requirements.txt)
- [graph_store.py](file://doc-tiny/graph_store.py)
- [neo4j_loader.py](file://doc-tiny/neo4j_loader.py)
- [falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)
- [neo4j_driver.py](file://code-tiny/tools/graph/driver/neo4j_driver.py)
- [database_schema_analyzer.py](file://code-tiny/tools/database_schema/database_schema_analyzer.py)
- [models.py](file://code-tiny/tools/database_schema/models.py)
- [pipeline.py](file://code-tiny/tools/database_schema/pipeline.py)
- [MIGRATION_GUIDE.py](file://code-tiny/tools/graph/docs/MIGRATION_GUIDE.py)
- [MIGRATION_EXAMPLE.md](file://code-tiny/tools/graph/docs/MIGRATION_EXAMPLE.md)
- [run_migration.py](file://code-tiny/run_migration.py)
- [plan.md](file://plans/neo4j-to-falkordb-migration/plan.md)
- [validation.md](file://plans/neo4j-to-falkordb-migration/validation.md)
- [red-team.md](file://plans/neo4j-to-falkordb-migration/red-team.md)
- [6_setup_indexes.py](file://doc-tiny/6_setup_indexes.py)
- [setup_constraints.py](file://code-tiny/scripts/setup_constraints.py)
- [test_falkordb_driver.py](file://tests/test_falkordb_driver.py)
- [test_explore_graph_falkor_compat.py](file://tests/test_explore_graph_falkor_compat.py)
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
This document explains the database migration and schema management approach used by Cortex Harness, focusing on:
- The migration framework architecture for graph databases
- Version control and automated execution of schema changes
- Differences between Neo4j and FalkorDB schemas and how to migrate data across them
- Rollback procedures for failed migrations
- Schema validation, integrity checks, and consistency verification tools
- Step-by-step guides for upgrading versions, migrating from legacy schemas, and performing emergency repairs
- Best practices for planning migrations, minimizing downtime, and handling large datasets

The repository includes a dedicated plan for migrating from Neo4j to FalkorDB, driver implementations for both backends, and utilities for schema analysis and index/constraint setup.

## Project Structure
Cortex Harness organizes graph-related code under a modular structure:
- Graph drivers for Neo4j and FalkorDB
- A shared graph operations layer that abstracts provider-specific details
- Database schema analysis and modeling utilities
- Scripts and documentation for migration workflows
- Tests validating compatibility and behavior across providers

```mermaid
graph TB
subgraph "Graph Layer"
N4J["Neo4j Driver"]
FDB["FalkorDB Driver"]
OPS["Graph Operations (shared)"]
end
subgraph "Schema Tools"
SA["Database Schema Analyzer"]
M["Models"]
P["Pipeline"]
end
subgraph "Migration Utilities"
MG["Migration Guide"]
ME["Migration Example"]
RM["Run Migration Script"]
end
subgraph "Validation"
IDX["Setup Indexes"]
CTR["Setup Constraints"]
T1["Driver Tests"]
T2["Compatibility Tests"]
end
OPS --> N4J
OPS --> FDB
SA --> M
SA --> P
RM --> OPS
RM --> SA
IDX --> OPS
CTR --> OPS
T1 --> N4J
T1 --> FDB
T2 --> OPS
```

**Diagram sources**
- [neo4j_driver.py](file://code-tiny/tools/graph/driver/neo4j_driver.py)
- [falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)
- [database_schema_analyzer.py](file://code-tiny/tools/database_schema/database_schema_analyzer.py)
- [models.py](file://code-tiny/tools/database_schema/models.py)
- [pipeline.py](file://code-tiny/tools/database_schema/pipeline.py)
- [MIGRATION_GUIDE.py](file://code-tiny/tools/graph/docs/MIGRATION_GUIDE.py)
- [MIGRATION_EXAMPLE.md](file://code-tiny/tools/graph/docs/MIGRATION_EXAMPLE.md)
- [run_migration.py](file://code-tiny/run_migration.py)
- [6_setup_indexes.py](file://doc-tiny/6_setup_indexes.py)
- [setup_constraints.py](file://code-tiny/scripts/setup_constraints.py)
- [test_falkordb_driver.py](file://tests/test_falkordb_driver.py)
- [test_explore_graph_falkor_compat.py](file://tests/test_explore_graph_falkor_compat.py)

**Section sources**
- [README.md](file://ReadMe.md)
- [Makefile](file://Makefile)
- [pyproject.toml](file://pyproject.toml)
- [requirements.txt](file://requirements.txt)

## Core Components
- Graph Drivers
  - Neo4j driver provides Cypher-based connectivity and query execution.
  - FalkorDB driver implements compatible interfaces for FalkorDB’s API surface.
- Shared Graph Operations
  - Encapsulates common operations (nodes, edges, queries) behind a consistent interface.
- Schema Analysis and Modeling
  - Analyzes existing graph schemas, models node/edge types, and generates pipeline steps for transformations.
- Migration Utilities
  - Guides and examples for migrating between providers.
  - Orchestration script to run migrations with logging and error handling.
- Validation and Integrity
  - Index setup and constraint enforcement scripts.
  - Tests ensuring cross-provider compatibility and correct behavior.

**Section sources**
- [neo4j_driver.py](file://code-tiny/tools/graph/driver/neo4j_driver.py)
- [falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)
- [database_schema_analyzer.py](file://code-tiny/tools/database_schema/database_schema_analyzer.py)
- [models.py](file://code-tiny/tools/database_schema/models.py)
- [pipeline.py](file://code-tiny/tools/database_schema/pipeline.py)
- [MIGRATION_GUIDE.py](file://code-tiny/tools/graph/docs/MIGRATION_GUIDE.py)
- [MIGRATION_EXAMPLE.md](file://code-tiny/tools/graph/docs/MIGRATION_EXAMPLE.md)
- [run_migration.py](file://code-tiny/run_migration.py)
- [6_setup_indexes.py](file://doc-tiny/6_setup_indexes.py)
- [setup_constraints.py](file://code-tiny/scripts/setup_constraints.py)
- [test_falkordb_driver.py](file://tests/test_falkordb_driver.py)
- [test_explore_graph_falkor_compat.py](file://tests/test_explore_graph_falkor_compat.py)

## Architecture Overview
The migration architecture centers on a provider-agnostic operations layer backed by specific drivers. Schema analysis informs transformation pipelines, while migration orchestration coordinates execution and rollback.

```mermaid
sequenceDiagram
participant CLI as "CLI / Orchestrator"
participant MIG as "Migration Runner"
participant OPS as "Graph Operations"
participant DRV as "Driver (Neo4j/FalkorDB)"
participant SCH as "Schema Analyzer"
participant VAL as "Validators (Indexes/Constraints)"
CLI->>MIG : Start migration
MIG->>SCH : Analyze current schema
SCH-->>MIG : Schema model + differences
MIG->>OPS : Apply schema changes (create/alter)
OPS->>DRV : Execute provider commands
DRV-->>OPS : Results
OPS-->>MIG : Status
MIG->>VAL : Validate indexes/constraints
VAL-->>MIG : Validation report
MIG-->>CLI : Final status and logs
```

**Diagram sources**
- [run_migration.py](file://code-tiny/run_migration.py)
- [database_schema_analyzer.py](file://code-tiny/tools/database_schema/database_schema_analyzer.py)
- [neo4j_driver.py](file://code-tiny/tools/graph/driver/neo4j_driver.py)
- [falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)
- [6_setup_indexes.py](file://doc-tiny/6_setup_indexes.py)
- [setup_constraints.py](file://code-tiny/scripts/setup_constraints.py)

## Detailed Component Analysis

### Graph Drivers and Provider Abstraction
- Neo4j Driver
  - Implements connection management, transaction boundaries, and Cypher execution.
  - Exposes methods aligned with shared operations for nodes, edges, and queries.
- FalkorDB Driver
  - Provides equivalent capabilities using FalkorDB’s API surface.
  - Ensures method signatures match the shared interface to minimize refactoring.
- Shared Operations
  - Centralizes logic for creating/updating nodes and edges, executing queries, and handling results.
  - Abstracts provider-specific differences so higher layers remain portable.

```mermaid
classDiagram
class GraphOperations {
+create_node(label, properties)
+update_node(node_id, properties)
+delete_node(node_id)
+create_edge(from_id, to_id, type, properties)
+query(cypher_or_native)
+transaction(callback)
}
class Neo4jDriver {
+connect()
+execute(query)
+close()
}
class FalkorDBDriver {
+connect()
+execute(query)
+close()
}
GraphOperations --> Neo4jDriver : "uses"
GraphOperations --> FalkorDBDriver : "uses"
```

**Diagram sources**
- [neo4j_driver.py](file://code-tiny/tools/graph/driver/neo4j_driver.py)
- [falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)

**Section sources**
- [neo4j_driver.py](file://code-tiny/tools/graph/driver/neo4j_driver.py)
- [falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)

### Schema Analysis and Modeling
- Schema Analyzer
  - Inspects existing graph structures (labels, relationships, properties).
  - Produces a normalized schema model suitable for diffing and transformation.
- Models
  - Define canonical representations of nodes, edges, and constraints.
- Pipeline
  - Generates ordered steps to transform source schema to target schema.
  - Supports idempotent operations and safe retries.

```mermaid
flowchart TD
Start(["Start"]) --> Discover["Discover Existing Schema"]
Discover --> Model["Build Canonical Schema Model"]
Model --> Diff{"Target Schema Defined?"}
Diff --> |No| Abort["Abort: No Target"]
Diff --> |Yes| Plan["Generate Transformation Plan"]
Plan --> ValidatePlan["Validate Plan (dry-run)"]
ValidatePlan --> Apply["Apply Changes"]
Apply --> Verify["Verify Integrity"]
Verify --> End(["End"])
Abort --> End
```

**Diagram sources**
- [database_schema_analyzer.py](file://code-tiny/tools/database_schema/database_schema_analyzer.py)
- [models.py](file://code-tiny/tools/database_schema/models.py)
- [pipeline.py](file://code-tiny/tools/database_schema/pipeline.py)

**Section sources**
- [database_schema_analyzer.py](file://code-tiny/tools/database_schema/database_schema_analyzer.py)
- [models.py](file://code-tiny/tools/database_schema/models.py)
- [pipeline.py](file://code-tiny/tools/database_schema/pipeline.py)

### Migration Orchestration and Execution
- Migration Runner
  - Coordinates discovery, planning, application, and verification phases.
  - Logs progress and errors; supports dry-run mode.
- Guides and Examples
  - Provide step-by-step instructions and example flows for provider transitions.
- Cross-Provider Data Transformation
  - Maps labels and relationship types to target semantics.
  - Normalizes property names and types where necessary.

```mermaid
sequenceDiagram
participant User as "User"
participant Runner as "Migration Runner"
participant Analyzer as "Schema Analyzer"
participant Ops as "Graph Operations"
participant Driver as "Driver"
User->>Runner : Run migration (target=FalkorDB)
Runner->>Analyzer : Analyze current schema
Analyzer-->>Runner : Schema model
Runner->>Ops : Generate and apply plan
Ops->>Driver : Execute provider commands
Driver-->>Ops : Results
Ops-->>Runner : Status
Runner-->>User : Report and logs
```

**Diagram sources**
- [run_migration.py](file://code-tiny/run_migration.py)
- [MIGRATION_GUIDE.py](file://code-tiny/tools/graph/docs/MIGRATION_GUIDE.py)
- [MIGRATION_EXAMPLE.md](file://code-tiny/tools/graph/docs/MIGRATION_EXAMPLE.md)
- [database_schema_analyzer.py](file://code-tiny/tools/database_schema/database_schema_analyzer.py)
- [neo4j_driver.py](file://code-tiny/tools/graph/driver/neo4j_driver.py)
- [falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)

**Section sources**
- [run_migration.py](file://code-tiny/run_migration.py)
- [MIGRATION_GUIDE.py](file://code-tiny/tools/graph/docs/MIGRATION_GUIDE.py)
- [MIGRATION_EXAMPLE.md](file://code-tiny/tools/graph/docs/MIGRATION_EXAMPLE.md)

### Validation, Integrity Checks, and Consistency Verification
- Index Setup
  - Ensures performance-critical indexes exist post-migration.
- Constraint Enforcement
  - Applies uniqueness or existence constraints to maintain integrity.
- Compatibility Tests
  - Validates that operations behave consistently across Neo4j and FalkorDB.

```mermaid
flowchart TD
PostMig["Post-Migration"] --> CheckIdx["Check/Create Indexes"]
CheckIdx --> CheckCtr["Check/Enforce Constraints"]
CheckCtr --> RunTests["Run Compatibility Tests"]
RunTests --> Report["Generate Validation Report"]
Report --> Done(["Complete"])
```

**Diagram sources**
- [6_setup_indexes.py](file://doc-tiny/6_setup_indexes.py)
- [setup_constraints.py](file://code-tiny/scripts/setup_constraints.py)
- [test_falkordb_driver.py](file://tests/test_falkordb_driver.py)
- [test_explore_graph_falkor_compat.py](file://tests/test_explore_graph_falkor_compat.py)

**Section sources**
- [6_setup_indexes.py](file://doc-tiny/6_setup_indexes.py)
- [setup_constraints.py](file://code-tiny/scripts/setup_constraints.py)
- [test_falkordb_driver.py](file://tests/test_falkordb_driver.py)
- [test_explore_graph_falkor_compat.py](file://tests/test_explore_graph_falkor_compat.py)

### Neo4j vs FalkorDB Schema Differences and Transformation Rules
- Labels and Relationship Types
  - Map Neo4j labels to FalkorDB entity types; ensure naming conventions align.
- Properties and Types
  - Normalize property names and coerce types where needed (e.g., strings to integers).
- Constraints and Indexes
  - Recreate equivalent constraints/indexes in the target system.
- Query Semantics
  - Translate Cypher-like patterns to FalkorDB-compatible calls via the shared operations layer.

```mermaid
flowchart TD
Source["Source Schema (Neo4j)"] --> MapLabels["Map Labels to Target Types"]
MapLabels --> MapProps["Normalize Properties"]
MapProps --> MapEdges["Map Relationships"]
MapEdges --> ApplyConstraints["Apply Constraints/Index"]
ApplyConstraints --> Verify["Verify Data Consistency"]
Verify --> Target["Target Schema (FalkorDB)"]
```

[No sources needed since this diagram shows conceptual workflow, not actual code structure]

**Section sources**
- [MIGRATION_GUIDE.py](file://code-tiny/tools/graph/docs/MIGRATION_GUIDE.py)
- [MIGRATION_EXAMPLE.md](file://code-tiny/tools/graph/docs/MIGRATION_EXAMPLE.md)
- [database_schema_analyzer.py](file://code-tiny/tools/database_schema/database_schema_analyzer.py)

### Rollback Procedures for Failed Migrations
- Dry-Run First
  - Always validate plans without applying changes.
- Idempotent Steps
  - Ensure each step can be safely retried without side effects.
- Transaction Boundaries
  - Wrap batches of changes in transactions where supported.
- Rollback Strategy
  - Revert applied steps in reverse order; restore indexes and constraints if they were dropped.
- State Tracking
  - Record migration state and version to avoid partial applications.

```mermaid
flowchart TD
Fail["Migration Step Failed"] --> Detect["Detect Failure Point"]
Detect --> Reverse["Reverse Applied Steps"]
Reverse --> Restore["Restore Indexes/Constraints"]
Restore --> Log["Log Rollback Details"]
Log --> Resume{"Retry Safe?"}
Resume --> |Yes| Retry["Reapply Remaining Steps"]
Resume --> |No| Halt["Halt and Alert"]
Retry --> End(["End"])
Halt --> End
```

[No sources needed since this diagram shows conceptual workflow, not actual code structure]

**Section sources**
- [run_migration.py](file://code-tiny/run_migration.py)
- [neo4j_driver.py](file://code-tiny/tools/graph/driver/neo4j_driver.py)
- [falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)

## Dependency Analysis
The migration subsystem depends on:
- Graph drivers for connectivity and execution
- Schema analyzer and models for planning
- Validation utilities for integrity assurance
- Tests for regression prevention

```mermaid
graph TB
RM["run_migration.py"] --> SA["database_schema_analyzer.py"]
RM --> OPS["Graph Operations (shared)"]
OPS --> N4J["neo4j_driver.py"]
OPS --> FDB["falkordb_driver.py"]
RM --> IDX["6_setup_indexes.py"]
RM --> CTR["setup_constraints.py"]
RM --> T1["test_falkordb_driver.py"]
RM --> T2["test_explore_graph_falkor_compat.py"]
```

**Diagram sources**
- [run_migration.py](file://code-tiny/run_migration.py)
- [database_schema_analyzer.py](file://code-tiny/tools/database_schema/database_schema_analyzer.py)
- [neo4j_driver.py](file://code-tiny/tools/graph/driver/neo4j_driver.py)
- [falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)
- [6_setup_indexes.py](file://doc-tiny/6_setup_indexes.py)
- [setup_constraints.py](file://code-tiny/scripts/setup_constraints.py)
- [test_falkordb_driver.py](file://tests/test_falkordb_driver.py)
- [test_explore_graph_falkor_compat.py](file://tests/test_explore_graph_falkor_compat.py)

**Section sources**
- [run_migration.py](file://code-tiny/run_migration.py)
- [database_schema_analyzer.py](file://code-tiny/tools/database_schema/database_schema_analyzer.py)
- [neo4j_driver.py](file://code-tiny/tools/graph/driver/neo4j_driver.py)
- [falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)
- [6_setup_indexes.py](file://doc-tiny/6_setup_indexes.py)
- [setup_constraints.py](file://code-tiny/scripts/setup_constraints.py)
- [test_falkordb_driver.py](file://tests/test_falkordb_driver.py)
- [test_explore_graph_falkor_compat.py](file://tests/test_explore_graph_falkor_compat.py)

## Performance Considerations
- Batched Writes
  - Group node/edge updates into batches to reduce round-trips.
- Index Pre-Planning
  - Create indexes before bulk loads when possible.
- Read-Only Phases
  - Use read-only connections during validation to avoid contention.
- Backpressure and Retries
  - Implement retry logic with exponential backoff for transient failures.
- Monitoring
  - Track throughput, latency, and error rates during migrations.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and resolutions:
- Connection Errors
  - Verify credentials, endpoints, and network reachability for Neo4j/FalkorDB.
- Constraint Violations
  - Review uniqueness constraints; normalize keys prior to insertion.
- Index Not Found
  - Ensure indexes are created post-migration; re-run index setup.
- Partial Migrations
  - Use dry-run and idempotent steps; roll back and reapply remaining steps.
- Compatibility Failures
  - Run provider compatibility tests; adjust mapping rules accordingly.

**Section sources**
- [test_falkordb_driver.py](file://tests/test_falkordb_driver.py)
- [test_explore_graph_falkor_compat.py](file://tests/test_explore_graph_falkor_compat.py)
- [6_setup_indexes.py](file://doc-tiny/6_setup_indexes.py)
- [setup_constraints.py](file://code-tiny/scripts/setup_constraints.py)

## Conclusion
Cortex Harness provides a structured, provider-agnostic migration framework with clear separation between operations, drivers, and schema analysis. By leveraging idempotent steps, robust validation, and comprehensive testing, teams can confidently upgrade versions, migrate between Neo4j and FalkorDB, and perform emergency repairs with minimal risk.

[No sources needed since this section summarizes without analyzing specific files]

## Appendices

### Step-by-Step Migration Guides

#### Upgrading Between Cortex Harness Versions
- Backup current graph state
- Run schema analyzer to detect changes
- Generate and validate transformation plan
- Apply changes in batches with monitoring
- Post-migration validation (indexes, constraints, tests)

**Section sources**
- [run_migration.py](file://code-tiny/run_migration.py)
- [database_schema_analyzer.py](file://code-tiny/tools/database_schema/database_schema_analyzer.py)
- [6_setup_indexes.py](file://doc-tiny/6_setup_indexes.py)
- [setup_constraints.py](file://code-tiny/scripts/setup_constraints.py)

#### Migrating from Legacy Schemas
- Inventory legacy labels and relationships
- Map to canonical models
- Transform properties and types
- Apply constraints and indexes
- Validate with compatibility tests

**Section sources**
- [MIGRATION_GUIDE.py](file://code-tiny/tools/graph/docs/MIGRATION_GUIDE.py)
- [MIGRATION_EXAMPLE.md](file://code-tiny/tools/graph/docs/MIGRATION_EXAMPLE.md)
- [models.py](file://code-tiny/tools/database_schema/models.py)
- [pipeline.py](file://code-tiny/tools/database_schema/pipeline.py)

#### Performing Emergency Schema Repairs
- Identify affected entities and relationships
- Isolate problematic batches
- Apply targeted fixes with transaction boundaries
- Re-validate integrity and consistency
- Document incident and update migration plan

**Section sources**
- [run_migration.py](file://code-tiny/run_migration.py)
- [neo4j_driver.py](file://code-tiny/tools/graph/driver/neo4j_driver.py)
- [falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)

### Planning and Best Practices
- Plan migrations incrementally with small, reversible steps
- Minimize downtime by scheduling off-peak runs
- Handle large datasets with batching and streaming
- Maintain detailed logs and metrics
- Keep rollback procedures tested and ready

[No sources needed since this section provides general guidance]

### Reference Plans and Documentation
- Neo4j to FalkorDB migration plan, validation strategy, and red team review

**Section sources**
- [plan.md](file://plans/neo4j-to-falkordb-migration/plan.md)
- [validation.md](file://plans/neo4j-to-falkordb-migration/validation.md)
- [red-team.md](file://plans/neo4j-to-falkordb-migration/red-team.md)