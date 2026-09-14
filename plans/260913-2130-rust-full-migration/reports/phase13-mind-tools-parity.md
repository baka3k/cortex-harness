# Phase 13 — MCP mind tools + semantic expansion parity

- date: 2026-09-14
- reference: `doc-tiny/mcp_graph_rag.py` (mind_mcp server, :8789) + `code-tiny/mcp/semantic_graph_expansion.py`
- rust surface: `rust/crates/cortex-mcp/src/mind/` (new module), served by the same
  binary in `--server mind` mode (Python deployment also runs two servers:
  unified `graph_mcp` + doc `mind_mcp`)
- fixture store: project `mindfix` — qdrant collection `mindfix_doc` (25 points,
  bge-m3 1024-dim, COSINE) + FalkorDB graph `mindfix_doc` (46 entities,
  66 RELATED edges, 25 paragraphs) on the shared remote servers
  (`127.0.0.1:6333` / `127.0.0.1:6379`), registered via
  `scripts/rust_mcp/fixtures/mind_config/mindfix.json`
  (`storage_backend: remote`); unscoped/local-fallback lanes isolated through a
  fresh `CORTEX_DATA_HOME`
- harness: `scripts/rust_mcp/record_mind.py` (live python mind server,
  production launch per `doc-tiny/mcp.sh`) + `scripts/rust_mcp/compare_mind.py`
  (per-case subprocess client isolation, `compare_graph.py` pattern)

## Gates

| gate | result | detail |
|---|---|---|
| mind tools/call | **PASS** | 30/30 cases byte-match per key (parsed wire equality) outside declared masks: scores/`rerank_score`/`confidence` tolerance 1e-9; `query_graph_rag.depth2` relations compared as multiset (frontier built from a Python set → row order is hash-iteration order, implementation-defined) |
| mind tools/list | **PASS** | 5/5 tools: names, byte-exact descriptions (docstrings), schema property sets |
| mind initialize | **PASS** | `mind_mcp` / `1.29.0` (fastmcp lib version), no instructions — byte-match |
| GLiNER sidecar contract | **PASS** | `{text, labels, threshold} → [{entity, type, score, span}]` byte-identical to doc-tiny's in-process `extract_entities_gliner` (same code path; verify-once gate: 3+4 entities on EN/VI samples) |
| embedding decision (Plan B) | **PASS** | sidecar contract in service: persistent `embed_worker.py` (newline-JSON over stdio), same `embedding_utils` model/device resolution → identical query vectors; P95 gate green |
| latency P95 semantic_search | **PASS** | Rust **P95 49.8 ms / P50 48.7 ms** vs Python **P95 57.0 ms / P50 52.6 ms** (15 timed rounds, 2 warmup, one persistent client session per server, fixture corpus) |
| regression: cargo test -p cortex-mcp | **PASS** | 65/65 (57 lib + 1 bin + 7 integration) |
| regression: compare_contract.py | **PASS** | 106/106 (initialize 1 + tools/list 39 + tools/call 66) |
| regression: compare_graph.py | **PASS** | 38/38 |

**OVERALL: PASS**

## Decision record — semantic expansion runtime

**Plan B (Python sidecar) — chosen.** The bge-m3 query embedder stays on torch
behind a subprocess boundary: `scripts/rust_mcp/embed_worker.py` is spawned
once per Rust server lifetime and serves newline-JSON requests; model/device
resolution mirrors `doc-tiny/embedding_utils.py` (`EMBEDDING_MODEL_PATH` /
`EMBEDDING_MODEL` / `EMBEDDING_DEVICE`, default `BAAI/bge-m3`, `cpu`), so the
vectors are identical to the live server's in-process encoder. Because the
worker is persistent (unlike the phase-14 one-shot sidecar), the model load is
amortized and the latency gate is met with margin. ONNX `ort` remains a later
spike and is intentionally NOT implemented. GLiNER extraction likewise stays a
Python sidecar (no official ONNX export) — at *ingest* time only; the mind
tools read entities from the FalkorDB store, so there is no query-time NER in
either runtime.

## Port matrix (stage-by-stage)

| stage | python (doc-tiny) | rust (cortex-mcp mind/) | mode |
|---|---|---|---|
| `semantic_search` body | `mcp_graph_rag.register_tools` | `mind/tools.rs::tool_semantic_search` | rust native (G) |
| `query_graph_rag_langextract` body | same | `mind/tools.rs::tool_query_graph_rag` | rust native (G) |
| `list_source_ids` / `get_paragraph_text` bodies | same | `mind/tools.rs` + `mind/graphstore.rs` | rust native (G) |
| `list_qdrant_collections` | same | `mind/tools.rs` | rust native (G) |
| qdrant search (remote) | `qdrant-client` HTTP | `mind/qdrant.rs` ureq REST (mirrors `cortex_storage::qdrant_remote`) | rust native (G) |
| qdrant collection listing (local embedded store) | `QdrantClient(path=)` engine | directory scan `<qdrant_doc>/collection/*` | approximation (G on fixtures) |
| per-project backend resolution | `get_qdrant` → `StorageFactory` | `mind/qdrant.rs::resolve_backend` over `project_registry` | rust native (G) |
| heuristic rerank `_compute_heuristic_rerank_score` | pure f64 math | `mind/tools.rs::compute_heuristic_rerank_score` | rust native (G, exact op order) |
| expansion filter `min_score_to_expand` / `min_entity_occurrences` | pure | `mind/tools.rs::filter_entity_ids_for_expansion` | rust native (G) |
| `fetch_entities_by_ids` / `fetch_relations_by_entity_ids` / `fetch_relations_with_depth` | Cypher over `FalkorDBGraphStore` | `mind/graphstore.rs` over `cortex-falkordb` (Cypher byte-identical) | rust native (G) |
| doc graph store candidates `_graph_store_candidates` / `get_neo4j` | registry + factory | `mind/graphstore.rs::graph_store_candidates` | rust native (G) |
| FastMCP signature validation | pydantic `Error executing tool …` | `mind/mod.rs::signature_guard` (byte-matched) | rust native (G) |
| tool metadata (names/descriptions) | FastMCP docstrings | `mind/catalog.rs` (byte-copied) | rust native (G) |
| `_standard_tool` envelope | `normalize_error/normalize_success` + `result_meta/summary` | shared `contract` layer (phase 11) | rust native (G) |
| query embedding bge-m3 | `SentenceTransformer` in-process | `mind/embed.rs` → persistent python sidecar | **python sidecar (Plan B)** |
| GLiNER entity extraction | `entity_extractors` (ingest-time) | python sidecar (`scripts/rust_mcp/gliner_sidecar.py`), same code path | python sidecar (G, verified once) |
| local-mode Qdrant vector search | `QdrantClient(path=)` embedded engine | excluded (no wire protocol) | python-plane |

G = covered by a passing gate above.

## Mock/parity protocol

- Fixture population runs the PYTHON doc ingest once
  (`scripts/rust_mcp/ingest_mind_fixture.py`): real `process_text` chunking,
  real bge-m3 embedding + remote-qdrant upserts, real FalkorDB writes; entities
  come from the deterministic phase-14 regex miner (`mine_entities`, LLM/GLiNER
  disabled deterministically via the fixture-provider monkeypatch of
  `build_graph_components`), plus synthetic `CO_OCCURS` relations between
  co-located entities so the graph-expansion lanes have RELATED edges
  (66 edges). Re-runs are idempotent (`--force` rebuilds).
- Both servers (python record + rust compare) run with the identical
  environment (`mind_contract.server_env`): `CORTEX_HARNESS_CONFIG_PATH` →
  `fixtures/mind_config`, `CORTEX_DATA_HOME` → fresh
  `fixtures/mind_local_store`, `FALKORDB_URI=redis://127.0.0.1:6379`,
  `PYTHONHASHSEED=0`, `EMBEDDING_MODEL=BAAI/bge-m3`, `EMBEDDING_DEVICE=cpu`.
- Comparator: `compare_contract.deep_diff` + absolute tolerance 1e-9 on
  `score`/`rerank_score`/`confidence`/`graph_proximity` keys; declared volatile
  masks inherited; per-case `relations_multiset` for hop ≥ 2 (Python set
  iteration order is implementation-defined; the ROW SET is compared exactly).
- Fixtures: `scripts/rust_mcp/fixtures/mind_fixtures.json` (30 cases, mode
  `live-python-mind-server`), latency samples `fixtures/mind_latency.json`.

## Exclusions

- Local-mode (embedded) Qdrant **vector search** — python-embedded engine, no
  wire protocol. Collection *listing* for a local store is served by a
  directory scan (name-equivalent); the fixtures pin the remote backend for
  searches, and the unscoped/local lanes are exercised deterministically as
  empty stores on both sides.
- Live LLM extraction/answers (langextract) — out of scope, as in phase 14.
- `semantic_graph_expansion.py` (260 LOC) — its CALLS-graph expansion lanes
  were already ported in phase 12 (`graph/tools_semantic.rs::expand_semantic_results`,
  covered by the 38/38 graph gate); phase 13 adds the *mind* flavor only.
- `resultType`/session-dialect fields — masked per the shared comparator.
- GLiNER/torch teardown abort (`recursive_mutex lock failed` at interpreter
  exit) — environmental, happens after the verdict is printed; the gate reads
  the printed verdict line.

## Suspected shared bugs (python reference)

1. **Dead `ProjectNotRegisteredError` fallbacks in `mcp_graph_rag.py`.**
   `get_qdrant` and `get_neo4j` catch `project_contract.ProjectNotRegisteredError`
   (doc-tiny's class) to implement the naming-convention fallback, but the
   registry they call raises `tools.common.project_registry.ProjectNotRegisteredError`
   (code-tiny) — a *different class with the same name* (verified:
   `A is B == False`). Every unregistered/prefix `project_id` therefore escapes
   as a `project_not_registered` error envelope and the local-store /
   `for_graph("{id}_doc")` fallbacks are unreachable. The Rust port replicates
   the observable behavior (raise) and documents the fallback as dead code.
2. **`_resolve_doc_collection(None, None)` ignores the registry.** The output
   `collection` field of `semantic_search`/`query_graph_rag_langextract`
   returns the raw `QDRANT_COLLECTION_DOC` default (`"documents"`) when both
   `project_id` and `collection` are absent, while `collections_searched`
   next to it lists the registry-resolved collections — inconsistent
   reporting, pinned byte-for-byte in the Rust port.
3. **Set-iteration order in `fetch_relations_with_depth`.** Hop ≥ 2 builds the
   BFS frontier from a Python `set`, so relation row order is
   hash-iteration-dependent (PYTHONHASHSEED-sensitive). Not fixed (shared
   contract surface); the comparator masks the order, not the content.
