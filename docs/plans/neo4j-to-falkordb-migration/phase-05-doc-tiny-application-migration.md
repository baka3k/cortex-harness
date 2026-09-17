# Phase 05 - doc-tiny Application Migration

## Goal

Migrate `doc-tiny` GraphRAG graph storage from direct Neo4j driver usage to FalkorDB while leaving Qdrant behavior intact.

## Tasks

1. Add a small graph adapter.
   - Suggested file: `doc-tiny/graph_store.py`.
   - Provide `query`, `ro_query`, `delete_graph`, and connection helpers.
   - Hide FalkorDB row/result parsing from application code.

2. Migrate direct driver files.
   - `0_reset_all.py`: replace `MATCH (n) DETACH DELETE n` session usage with FalkorDB graph delete or equivalent clear query.
   - `6_setup_indexes.py`: apply FalkorDB range indexes.
   - `graphrag_ingest_langextract.py`: migrate `ingest_to_neo4j` and `ingest_to_neo4j_batch`.
   - `graphrag_query_langextract.py`: migrate related entity lookups.
   - `mcp_graph_rag.py`: migrate lazy driver creation and query helpers.
   - `neo4j_loader.py` and `open_ai_exec.py`: replace or isolate Neo4j-specific retriever dependencies.

3. Update configuration.
   - Add `FALKORDB_HOST`, `FALKORDB_PORT`, `FALKORDB_USERNAME`, `FALKORDB_PASSWORD`, `FALKORDB_GRAPH`.
   - Keep `NEO4J_*` only as transitional aliases if needed.

4. Preserve public behavior.
   - Keep CLI argument intent stable.
   - Avoid changing entity extraction, embedding, Qdrant collection, or LLM prompt logic.

## Validation

- Ingest a small fixture document and verify `Document`, `Paragraph`, `Entity`, `HAS_PARAGRAPH`, `HAS_ENTITY`, and `RELATED`.
- Query GraphRAG context and compare generated context structure before/after migration.
- Reset script clears FalkorDB graph and Qdrant collection as expected.

## Risks

- `neo4j-graphrag` may not have a FalkorDB-compatible retriever.
- Entity relationship `MERGE` semantics must be checked for duplicate prevention.

