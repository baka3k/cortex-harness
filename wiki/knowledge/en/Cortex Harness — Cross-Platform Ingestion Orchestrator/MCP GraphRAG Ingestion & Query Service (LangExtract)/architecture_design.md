Two entry points share a common ingestion/query core:
- `mcp_graph_rag.py` — FastMCP server (`FastMCP(..., stateless_http=True)`) exposing tools `query_graph_rag_langextract`, `semantic_search`, `list_source_ids`, `list_qdrant_collections`, `get_paragraph_text`. It lazily constructs a single `QdrantClient`, `SentenceTransformer`, and graph store per process.
- `graphrag_ingest_langextract.py` — CLI pipeline: document reader → paragraph splitter → embedding → entity extraction (spacy/gliner/gemini/langextract) → parallel write to Qdrant (passage vectors + payload with `entity_ids`/`entity_mentions`) and Neo4j/FalkorDB (`Document`/`Paragraph`/`Entity` nodes, `HAS_ENTITY`/`RELATED` edges).

Shared subsystems:
- `graph_store.py` — pluggable graph backend. `Neo4jGraphStore` wraps the official `neo4j` driver; `FalkorDBGraphStore` adapts `tools.graph.driver.falkordb_driver.FalkorDBDriver` (imported by prepending `code-tiny` to `sys.path`). Both expose `session()`/`setup_indexes()` so callers never see the concrete provider.
- `embedding_utils.py` — resolves `SentenceTransformer` model name/path and device from env (`EMBEDDING_MODEL_PATH`, `EMBEDDING_DEVICE`, `HF_HUB_OFFLINE`).
- `entity_extractors.py` — uniform interface returning `(entities, relations)` dicts for spacy, GLiNER, Gemini, and LangExtract providers.
- `model.py` — Pydantic schemas used by LangExtract output parsing.
- `gliner/labels*.txt` — zero-shot label sets; `rules/ruler.*.json` — spaCy EntityRuler configs; `testdata/` — sample PDFs for demos.

Dependency direction is one-way: MCP server and ingest script depend on `graph_store`, `embedding_utils`, `entity_extractors`; nothing in those helpers imports back into the scripts. The bridge between Qdrant and Neo4j is the `entity_ids` list stored in each passage's payload.