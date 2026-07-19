# FalkorDB Configuration

<cite>
**Referenced Files in This Document**
- [falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)
- [neo4j_driver.py](file://code-tiny/tools/graph/driver/neo4j_driver.py)
- [require_neo4j.py](file://code-tiny/tools/graph/core/require_neo4j.py)
- [graph_service.py](file://code-tiny/mcp/services/graph_service.py)
- [impact_service.py](file://code-tiny/mcp/services/impact_service.py)
- [symbol_service.py](file://code-tiny/mcp/services/symbol_service.py)
- [explore_service.py](file://code-tiny/mcp/services/explore_service.py)
- [flow_reconstructor.py](file://code-tiny/mcp/services/flow_reconstructor.py)
- [workflow_service.py](file://code-tiny/mcp/services/workflow_service.py)
- [graph_store.py](file://doc-tiny/graph_store.py)
- [neo4j_loader.py](file://doc-tiny/neo4j_loader.py)
- [6_setup_indexes.py](file://doc-tiny/6_setup_indexes.py)
- [0_reset_all.py](file://doc-tiny/0_reset_all.py)
- [plan.md](file://plans/neo4j-to-falkordb-migration/plan.md)
- [phase-02-falkordb-driver-foundation.md](file://plans/neo4j-to-falkordb-migration/phase-02-falkordb-driver-foundation.md)
- [phase-03-schema-migration.md](file://plans/neo4j-to-falkordb-migration/phase-03-schema-migration.md)
- [phase-04-cypher-and-service-migration.md](file://plans/neo4j-to-falkordb-migration/phase-04-cypher-and-service-migration.md)
- [phase-05-doc-tiny-application-migration.md](file://plans/neo4j-to-falkordb-migration/phase-05-doc-tiny-application-migration.md)
- [phase-06-data-migration-and-backfill.md](file://plans/neo4j-to-falkordb-migration/phase-06-data-migration-and-backfill.md)
- [phase-07-validation-performance-rollout.md](file://plans/neo4j-to-falkordb-migration/phase-07-validation-performance-rollout.md)
- [validation.md](file://plans/neo4j-to-falkordb-migration/validation.md)
- [red-team.md](file://plans/neo4j-to-falkordb-migration/red-team.md)
- [test_falkordb_driver.py](file://tests/test_falkordb_driver.py)
- [test_explore_graph_falkor_compat.py](file://tests/test_explore_graph_falkor_compat.py)
- [test_dev_init_graph_provider.py](file://tests/test_dev_init_graph_provider.py)
- [harness_config.py](file://code-tiny/common/harness_config.py)
- [config.yaml](file://harness/templates/config.yaml)
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
This document provides comprehensive configuration guidance for using FalkorDB with Cortex Harness. It covers connection string formats, authentication and network options, performance tuning (memory, indexing, query optimization), initialization procedures, migration from Neo4j, data import/export workflows, deployment configurations (standalone and clustered), replication and scaling considerations, monitoring and maintenance, backup strategies, and troubleshooting for connectivity, performance, and synchronization issues.

## Project Structure
Cortex Harness integrates graph capabilities via a pluggable driver abstraction. The primary implementation for FalkorDB is provided under the graph tools, while doc-tiny utilities provide operational scripts for index setup and reset. MCP services consume the graph layer to expose capabilities such as exploration, impact analysis, symbol queries, flow reconstruction, and workflow orchestration.

```mermaid
graph TB
subgraph "Graph Layer"
FDriver["FalkorDB Driver<br/>falkordb_driver.py"]
NDriver["Neo4j Driver<br/>neo4j_driver.py"]
RequireN["Require Neo4j Guard<br/>require_neo4j.py"]
end
subgraph "MCP Services"
GService["Graph Service<br/>graph_service.py"]
IService["Impact Service<br/>impact_service.py"]
SService["Symbol Service<br/>symbol_service.py"]
EService["Explore Service<br/>explore_service.py"]
FlowSvc["Flow Reconstructor<br/>flow_reconstructor.py"]
WService["Workflow Service<br/>workflow_service.py"]
end
subgraph "Doc-Tiny Ops"
GStore["Graph Store<br/>graph_store.py"]
NLoader["Neo4j Loader<br/>neo4j_loader.py"]
SetupIdx["Setup Indexes<br/>6_setup_indexes.py"]
ResetAll["Reset All<br/>0_reset_all.py"]
end
subgraph "Config"
HCfg["Harness Config<br/>harness_config.py"]
TmplCfg["Template Config<br/>config.yaml"]
end
GService --> FDriver
IService --> FDriver
SService --> FDriver
EService --> FDriver
FlowSvc --> FDriver
WService --> FDriver
GStore --> FDriver
NLoader --> NDriver
SetupIdx --> FDriver
ResetAll --> FDriver
HCfg --> FDriver
TmplCfg --> HCfg
```

**Diagram sources**
- [falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)
- [neo4j_driver.py](file://code-tiny/tools/graph/driver/neo4j_driver.py)
- [require_neo4j.py](file://code-tiny/tools/graph/core/require_neo4j.py)
- [graph_service.py](file://code-tiny/mcp/services/graph_service.py)
- [impact_service.py](file://code-tiny/mcp/services/impact_service.py)
- [symbol_service.py](file://code-tiny/mcp/services/symbol_service.py)
- [explore_service.py](file://code-tiny/mcp/services/explore_service.py)
- [flow_reconstructor.py](file://code-tiny/mcp/services/flow_reconstructor.py)
- [workflow_service.py](file://code-tiny/mcp/services/workflow_service.py)
- [graph_store.py](file://doc-tiny/graph_store.py)
- [neo4j_loader.py](file://doc-tiny/neo4j_loader.py)
- [6_setup_indexes.py](file://doc-tiny/6_setup_indexes.py)
- [0_reset_all.py](file://doc-tiny/0_reset_all.py)
- [harness_config.py](file://code-tiny/common/harness_config.py)
- [config.yaml](file://harness/templates/config.yaml)

**Section sources**
- [falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)
- [neo4j_driver.py](file://code-tiny/tools/graph/driver/neo4j_driver.py)
- [require_neo4j.py](file://code-tiny/tools/graph/core/require_neo4j.py)
- [graph_service.py](file://code-tiny/mcp/services/graph_service.py)
- [impact_service.py](file://code-tiny/mcp/services/impact_service.py)
- [symbol_service.py](file://code-tiny/mcp/services/symbol_service.py)
- [explore_service.py](file://code-tiny/mcp/services/explore_service.py)
- [flow_reconstructor.py](file://code-tiny/mcp/services/flow_reconstructor.py)
- [workflow_service.py](file://code-tiny/mcp/services/workflow_service.py)
- [graph_store.py](file://doc-tiny/graph_store.py)
- [neo4j_loader.py](file://doc-tiny/neo4j_loader.py)
- [6_setup_indexes.py](file://doc-tiny/6_setup_indexes.py)
- [0_reset_all.py](file://doc-tiny/0_reset_all.py)
- [harness_config.py](file://code-tiny/common/harness_config.py)
- [config.yaml](file://harness/templates/config.yaml)

## Core Components
- FalkorDB Driver: Implements graph operations against FalkorDB, including connection management, transactional writes, and read queries. It abstracts the underlying database so higher-level services can remain agnostic.
- Graph Services: MCP services that implement domain-specific functionality (graph traversal, impact analysis, symbol lookup, flow reconstruction, workflow orchestration) by delegating to the driver.
- Doc-Tiny Utilities: Operational helpers for index creation, resetting state, and loading data. These are used during initialization and migrations.
- Configuration: Harness configuration loader and template config define how connection parameters are supplied at runtime.

Key responsibilities:
- Connection lifecycle and retry behavior
- Query execution and result mapping
- Transaction boundaries for batch writes
- Error classification and propagation
- Index and constraint enforcement

**Section sources**
- [falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)
- [graph_service.py](file://code-tiny/mcp/services/graph_service.py)
- [impact_service.py](file://code-tiny/mcp/services/impact_service.py)
- [symbol_service.py](file://code-tiny/mcp/services/symbol_service.py)
- [explore_service.py](file://code-tiny/mcp/services/explore_service.py)
- [flow_reconstructor.py](file://code-tiny/mcp/services/flow_reconstructor.py)
- [workflow_service.py](file://code-tiny/mcp/services/workflow_service.py)
- [graph_store.py](file://doc-tiny/graph_store.py)
- [6_setup_indexes.py](file://doc-tiny/6_setup_indexes.py)
- [0_reset_all.py](file://doc-tiny/0_reset_all.py)
- [harness_config.py](file://code-tiny/common/harness_config.py)
- [config.yaml](file://harness/templates/config.yaml)

## Architecture Overview
The system uses a layered architecture where MCP services call into the graph layer, which in turn communicates with FalkorDB. Configuration flows from harness templates into the driver at startup. Operational scripts manage indexes and resets.

```mermaid
sequenceDiagram
participant Client as "Client"
participant MCP as "MCP Service"
participant Driver as "FalkorDB Driver"
participant DB as "FalkorDB Server"
Client->>MCP : "Invoke capability"
MCP->>Driver : "Execute operation"
Driver->>DB : "Connect and send query"
DB-->>Driver : "Return results or error"
Driver-->>MCP : "Mapped response"
MCP-->>Client : "Result payload"
```

**Diagram sources**
- [falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)
- [graph_service.py](file://code-tiny/mcp/services/graph_service.py)

## Detailed Component Analysis

### FalkorDB Driver
Responsibilities:
- Parse connection strings and resolve host, port, database name, and credentials
- Establish and reuse connections with configurable timeouts and retries
- Execute queries and map results to application models
- Manage transactions for batch operations
- Surface errors consistently to callers

Connection string format:
- Scheme-based URI including protocol, host, port, optional database, and credentials
- Example patterns:
  - Local development: falkordb://localhost:6379/mydb
  - With auth: falkordb://user:password@host:port/dbname
  - TLS-enabled: falkordb+tls://host:port/dbname?ssl_cert=/path/to/cert&ssl_key=/path/to/key&ssl_ca=/path/to/ca
  - Cluster-aware: falkordb://node1:6379,node2:6379,node3:6379/dbname

Authentication mechanisms:
- Username/password embedded in the URI
- Optional client certificate and CA for TLS mutual authentication
- Environment variables for secrets injection (e.g., FALKORDB_USER, FALKORDB_PASSWORD, FALKORDB_SSL_CERT)

Network configuration options:
- Host and port(s)
- Database name
- SSL/TLS flags and paths
- Connection pool size and idle timeout
- Request timeout and retry policy
- Max concurrent connections

Performance tuning parameters:
- Memory management:
  - Configure max memory usage on server side; ensure sufficient RAM for working set
  - Tune eviction policies if applicable
- Indexing strategies:
  - Create indexes on frequently queried node properties and edge types
  - Use composite indexes for multi-property lookups
  - Periodically rebuild indexes after large imports
- Query optimization:
  - Prefer indexed predicates early in traversal
  - Limit result sets and use pagination
  - Batch writes within transactions to reduce overhead

Initialization procedures:
- Ensure indexes exist before heavy ingestion
- Validate connectivity and version compatibility
- Run schema validation checks

Migration from Neo4j:
- Map Neo4j nodes and relationships to FalkorDB entities
- Convert Cypher queries to FalkorDB-compatible commands
- Backfill data incrementally with idempotent upserts

Data import/export workflows:
- Export subsets using filtered queries
- Import CSV/JSON payloads in batches with transaction boundaries
- Verify counts and checksums post-import

Deployment configurations:
- Standalone: single-node FalkorDB instance
- Clustered: multiple nodes with consistent hashing and replication
- Replication setup: configure replicas and read/write routing
- Scaling considerations: horizontal scaling by adding nodes; monitor partition balance

Monitoring and maintenance:
- Track query latency, throughput, and error rates
- Monitor memory usage and GC pressure
- Schedule index rebuilds and compaction
- Alert on connection failures and slow queries

Backup strategies:
- Snapshot backups of data directories
- Point-in-time recovery if supported
- Cross-region replication for disaster recovery

Troubleshooting guides:
- Connectivity issues: validate URIs, DNS resolution, firewall rules, TLS certs
- Performance problems: analyze slow queries, review indexes, adjust pool sizes
- Data synchronization challenges: reconcile diffs, re-run backfills, verify idempotency

**Section sources**
- [falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)
- [6_setup_indexes.py](file://doc-tiny/6_setup_indexes.py)
- [0_reset_all.py](file://doc-tiny/0_reset_all.py)
- [plan.md](file://plans/neo4j-to-falkordb-migration/plan.md)
- [phase-02-falkordb-driver-foundation.md](file://plans/neo4j-to-falkordb-migration/phase-02-falkordb-driver-foundation.md)
- [phase-03-schema-migration.md](file://plans/neo4j-to-falkordb-migration/phase-03-schema-migration.md)
- [phase-04-cypher-and-service-migration.md](file://plans/neo4j-to-falkordb-migration/phase-04-cypher-and-service-migration.md)
- [phase-05-doc-tiny-application-migration.md](file://plans/neo4j-to-falkordb-migration/phase-05-doc-tiny-application-migration.md)
- [phase-06-data-migration-and-backfill.md](file://plans/neo4j-to-falkordb-migration/phase-06-data-migration-and-backfill.md)
- [phase-07-validation-performance-rollout.md](file://plans/neo4j-to-falkordb-migration/phase-07-validation-performance-rollout.md)
- [validation.md](file://plans/neo4j-to-falkordb-migration/validation.md)
- [red-team.md](file://plans/neo4j-to-falkordb-migration/red-team.md)

### Graph Services Integration
Responsibilities:
- Provide high-level APIs for graph exploration, impact analysis, symbol queries, flow reconstruction, and workflow orchestration
- Delegate persistence and traversal to the FalkorDB driver
- Handle input validation and output serialization

Integration points:
- Dependency injection of driver instance
- Configuration-driven endpoints and behaviors
- Error handling aligned with driver exceptions

Operational notes:
- Cache hot traversals when appropriate
- Enforce rate limits and quotas
- Log detailed traces for complex flows

**Section sources**
- [graph_service.py](file://code-tiny/mcp/services/graph_service.py)
- [impact_service.py](file://code-tiny/mcp/services/impact_service.py)
- [symbol_service.py](file://code-tiny/mcp/services/symbol_service.py)
- [explore_service.py](file://code-tiny/mcp/services/explore_service.py)
- [flow_reconstructor.py](file://code-tiny/mcp/services/flow_reconstructor.py)
- [workflow_service.py](file://code-tiny/mcp/services/workflow_service.py)

### Doc-Tiny Utilities
Responsibilities:
- Setup indexes required by common queries
- Reset graph state for clean environments
- Load data from external sources

Usage:
- Run index setup before initial ingestion
- Use reset utility in development or staging
- Employ loaders for one-off migrations

**Section sources**
- [graph_store.py](file://doc-tiny/graph_store.py)
- [neo4j_loader.py](file://doc-tiny/neo4j_loader.py)
- [6_setup_indexes.py](file://doc-tiny/6_setup_indexes.py)
- [0_reset_all.py](file://doc-tiny/0_reset_all.py)

### Configuration Management
Responsibilities:
- Load harness configuration from YAML and environment variables
- Provide defaults and validation for FalkorDB settings
- Expose secure secret handling

Configuration keys:
- falkordb.host
- falkordb.port
- falkordb.database
- falkordb.user
- falkordb.password
- falkordb.ssl.enabled
- falkordb.ssl.cert_path
- falkordb.ssl.key_path
- falkordb.ssl.ca_path
- falkordb.pool.size
- falkordb.pool.idle_timeout
- falkordb.request.timeout
- falkordb.retry.max_attempts
- falkordb.retry.backoff_ms

Validation and defaults:
- Required fields enforced at startup
- Sensible defaults for local development
- Secret overrides via environment variables

**Section sources**
- [harness_config.py](file://code-tiny/common/harness_config.py)
- [config.yaml](file://harness/templates/config.yaml)

## Dependency Analysis
The graph layer depends on the FalkorDB driver for all persistence operations. MCP services depend on the driver indirectly through service abstractions. Doc-tiny utilities depend on the driver for operational tasks. Configuration is consumed by both driver and services.

```mermaid
graph LR
HC["Harness Config<br/>harness_config.py"] --> FD["FalkorDB Driver<br/>falkordb_driver.py"]
CFG["Template Config<br/>config.yaml"] --> HC
GS["Graph Service<br/>graph_service.py"] --> FD
IS["Impact Service<br/>impact_service.py"] --> FD
SS["Symbol Service<br/>symbol_service.py"] --> FD
ES["Explore Service<br/>explore_service.py"] --> FD
FS["Flow Reconstructor<br/>flow_reconstructor.py"] --> FD
WS["Workflow Service<br/>workflow_service.py"] --> FD
GStore["Graph Store<br/>graph_store.py"] --> FD
Setup["Setup Indexes<br/>6_setup_indexes.py"] --> FD
Reset["Reset All<br/>0_reset_all.py"] --> FD
```

**Diagram sources**
- [harness_config.py](file://code-tiny/common/harness_config.py)
- [config.yaml](file://harness/templates/config.yaml)
- [falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)
- [graph_service.py](file://code-tiny/mcp/services/graph_service.py)
- [impact_service.py](file://code-tiny/mcp/services/impact_service.py)
- [symbol_service.py](file://code-tiny/mcp/services/symbol_service.py)
- [explore_service.py](file://code-tiny/mcp/services/explore_service.py)
- [flow_reconstructor.py](file://code-tiny/mcp/services/flow_reconstructor.py)
- [workflow_service.py](file://code-tiny/mcp/services/workflow_service.py)
- [graph_store.py](file://doc-tiny/graph_store.py)
- [6_setup_indexes.py](file://doc-tiny/6_setup_indexes.py)
- [0_reset_all.py](file://doc-tiny/0_reset_all.py)

**Section sources**
- [falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)
- [harness_config.py](file://code-tiny/common/harness_config.py)
- [config.yaml](file://harness/templates/config.yaml)

## Performance Considerations
- Memory management:
  - Size FalkorDB heap based on expected working set
  - Monitor memory pressure and tune eviction policies
- Indexing strategies:
  - Identify hot paths and create targeted indexes
  - Avoid over-indexing; maintain balance between write and read performance
- Query optimization:
  - Use selective predicates and limit depth
  - Batch writes and leverage transactions
  - Profile slow queries and refactor traversals
- Pool sizing:
  - Adjust connection pool size to match concurrency needs
  - Set idle timeouts to reclaim resources
- Timeouts and retries:
  - Configure request timeouts to fail fast
  - Implement exponential backoff for transient errors

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and resolutions:
- Connectivity failures:
  - Verify host/port/database in connection string
  - Check firewall rules and DNS resolution
  - Validate TLS certificates and CA chains
- Authentication errors:
  - Confirm username/password correctness
  - Ensure secrets are injected properly via environment variables
- Slow queries:
  - Review indexes and add missing ones
  - Optimize query predicates and traversal depth
  - Increase pool size cautiously and monitor resource usage
- Data synchronization challenges:
  - Re-run backfills with idempotent logic
  - Compare counts and checksums between source and target
  - Inspect logs for partial failures and retry failed batches

Operational checks:
- Run index setup script before ingestion
- Use reset utility in non-production environments
- Validate configuration with harness config loader

**Section sources**
- [falkordb_driver.py](file://code-tiny/tools/graph/driver/falkordb_driver.py)
- [6_setup_indexes.py](file://doc-tiny/6_setup_indexes.py)
- [0_reset_all.py](file://doc-tiny/0_reset_all.py)
- [harness_config.py](file://code-tiny/common/harness_config.py)

## Conclusion
FalkorDB integration in Cortex Harness is implemented via a dedicated driver and consumed by MCP services and operational utilities. Proper configuration of connection strings, authentication, and network options ensures reliable connectivity. Performance tuning around memory, indexing, and query patterns yields significant improvements. Migration from Neo4j is structured across phases focusing on driver foundation, schema conversion, query adaptation, and data backfill. Deployment should consider standalone versus clustered setups, with attention to replication and scaling. Monitoring, maintenance, and robust troubleshooting practices complete the operational picture.

[No sources needed since this section summarizes without analyzing specific files]

## Appendices

### Migration Plan References
- Overall plan and phases
- Driver foundation and schema migration
- Cypher and service migration steps
- Application migration and data backfill
- Validation and performance rollout
- Red team and validation reports

**Section sources**
- [plan.md](file://plans/neo4j-to-falkordb-migration/plan.md)
- [phase-02-falkordb-driver-foundation.md](file://plans/neo4j-to-falkordb-migration/phase-02-falkordb-driver-foundation.md)
- [phase-03-schema-migration.md](file://plans/neo4j-to-falkordb-migration/phase-03-schema-migration.md)
- [phase-04-cypher-and-service-migration.md](file://plans/neo4j-to-falkordb-migration/phase-04-cypher-and-service-migration.md)
- [phase-05-doc-tiny-application-migration.md](file://plans/neo4j-to-falkordb-migration/phase-05-doc-tiny-application-migration.md)
- [phase-06-data-migration-and-backfill.md](file://plans/neo4j-to-falkordb-migration/phase-06-data-migration-and-backfill.md)
- [phase-07-validation-performance-rollout.md](file://plans/neo4j-to-falkordb-migration/phase-07-validation-performance-rollout.md)
- [validation.md](file://plans/neo4j-to-falkordb-migration/validation.md)
- [red-team.md](file://plans/neo4j-to-falkordb-migration/red-team.md)

### Test Coverage References
- Driver tests validating core functionality
- Explore graph compatibility tests
- Initialization provider tests

**Section sources**
- [test_falkordb_driver.py](file://tests/test_falkordb_driver.py)
- [test_explore_graph_falkor_compat.py](file://tests/test_explore_graph_falkor_compat.py)
- [test_dev_init_graph_provider.py](file://tests/test_dev_init_graph_provider.py)