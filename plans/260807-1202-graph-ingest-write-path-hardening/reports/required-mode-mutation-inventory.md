# Required-mode graph mutation inventory

Audit date: 2026-08-28  
Production owner: graph-ingest maintainers  
Migration deadline: before enabling required mode for the named lane

Required mode is fail-closed in the sync parent for every lane except `cplus`.
The blocked lanes may run `shared-shadow` to collect serialization evidence,
but cannot claim publication-grade node-first recovery. This inventory covers
all 37 graph-writing keys selected by incremental sync; it is the release
allowlist, not a statement that legacy direct mutations are safe.

| Lane(s) | Count | Required-mode status | Blocking mutation surface | Owner / deadline |
| --- | ---: | --- | --- | --- |
| `cplus` | 1 | migrated | Durable node-first journal, persisted replay descriptors, producer/manifests, sealed endpoint audit | C++/Pro*C owner / complete |
| `delphi`, `java`, `kotlin`, `vbnet`, `vb6`, `vba`, `vbscript`, `python`, `go`, `perl`, `rust`, `swift`, `js`, `php`, `csharp`, `sql`, `plsql` | 17 | blocked; shadow only | Shared writer still has legacy cleanup and analyzer-owned lifecycle | Language analyzer owners / before lane promotion |
| `ts` | 1 | blocked; shadow only | API nodes/edges, navigation and workflow-style custom descriptors are incomplete | TypeScript owner / before lane promotion |
| `android` | 1 | blocked; shadow only | Custom node Cypher and relationship rows sent through node APIs | Android owner / before lane promotion |
| `cobol`, `dart`, `shell`, `jp1` | 4 | blocked; shadow only | Custom cleanup and/or node-first closure not migrated | Language analyzer owners / before lane promotion |
| `spring`, `servlet_jsp`, `mybatis`, `aspnet_framework`, `aspnet_core` | 5 | blocked; shadow only | Specialized direct writers; some Neo4j branches bypass canonical driver attachment | Framework owners / before lane promotion |
| `struts`, `flutter` | 2 | blocked; shadow only | Unsupported custom cleanup/node descriptors and missing parent closure | Framework owners / before lane promotion |
| `fastapi_django`, `express_js`, `laravel` | 3 | blocked; shadow only | Direct web-framework writer mutations | Web-framework owner / before lane promotion |
| `database_sql`, `database_plsql` | 2 | blocked; shadow only | Direct database-schema writer mutations | Database-schema owner / before lane promotion |
| `project_topology` | 1 | blocked; shadow only | Direct topology node/edge and cleanup mutations | Topology owner / before lane promotion |

## Additional non-registry mutation surfaces

Standalone message scanning and the TypeScript API bridge remain blocked in
required mode by the driver mutation guard. Incremental sync already disables
message-scan graph writes during its analyzer pass. No Neo4j/FalkorDB provider
branch may opt out of canonical driver creation when its lane is promoted.

## Promotion gate

A blocked lane can move to migrated only after all of the following are true:

1. every mutation has a trusted serializable operation descriptor;
2. incremental cleanup is journaled;
3. the sync parent owns producer completion and final node-phase closure;
4. all edge families wait for node drain and the sealed endpoint audit;
5. crash/restart, exact readback, project isolation, and provider parity pass.

