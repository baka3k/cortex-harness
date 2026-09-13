# Phase 14 — doc-tiny parity (Scope A)

- date: 2026-09-14, harness: `scripts/rust_parity/doctiny_parity.py`
- corpus: 7 files (deerflow_same_ec2_deploy.md, ops-runbook.md, mcp_pipeline_tools.md, n8n_stock_api.md, system-architecture.md, zz_crafted.md, zz_notes.txt), 117 fixture entities
- graphs: `p14_doc_py` (python doc-tiny ingest) vs `p14_doc_rs` (rust cortex-doc ingest) @ 127.0.0.1:6379
- LLM/NER: disabled identically on both sides (fixture replay provider; regex miner `mine_entities`); vector stage: python side stubbed, rust side excluded (local-mode Qdrant has no wire protocol — see exclusions).

## Gates

| gate | result | detail |
|---|---|---|
| G1 chunking | **PASS** | deerflow_same_ec2_deploy.md: 33 paragraphs byte-identical <br> guides/ops-runbook.md: 2 paragraphs byte-identical <br> mcp_pipeline_tools.md: 24 paragraphs byte-identical <br> n8n_stock_api.md: 34 paragraphs byte-identical <br> system-architecture.md: 11 paragraphs byte-identical <br> zz_crafted.md: 7 paragraphs byte-identical <br> zz_notes.txt: 2 paragraphs byte-identical |
| G2 assembly | **PASS** | 114/114 entity sets canonical-identical |
| G3a python ingest | **PASS** | python ingest done: 7 files, 113 vector-upserts stubbed |
| G3b rust ingest | **PASS** | {"entity_nodes":105,"entity_relations":0,"files":7,"graph":"p14_doc_rs","graph_batches":113,"ok":true,"paragraphs":113} |
| G3 graph diff | **PASS** | nodes py=168 rs=168; edges py=218 rs=218; diff_total=0 |
| G4 query | **PASS** | python query on both graphs byte-identical (10 ids) <br> rust query on rs-graph byte-identical to python; {'entity_ids': 10, 'passages': 5} |
| G5 embed smoke (optional) | **PASS** | dimension=1024 via /Users/hieplq1.aip/AI/cortex-harness/.venv/bin/python |

**OVERALL: PASS** (optional gates excluded from verdict)

### Port matrix (stage-by-stage)

| stage | python (doc-tiny) | rust (cortex-doc) | mode |
|---|---|---|---|
| text reading (.txt/.md) | `read_text_file` | `text_reader::read_text_file` | rust native |
| folder scan + excludes | `_iter_input_files` + `code-tiny scan_ignore` (89 dirs) | `text_reader::iter_input_files` + `COMMON_SCAN_EXCLUDE` | rust native |
| source_id | `_safe_source_id` | `text_reader::safe_source_id` | rust native |
| chunking | `split_paragraphs` | `chunker::split_paragraphs` | rust native (G1) |
| entity id / normalize | `_entity_id`, `_normalize_entity_name` | `entity::{entity_id, normalize_entity_name}` (uuid5 SHA-1) | rust native |
| graph assembly | `build_graph_components_from_entities` | `entity::build_graph_components_from_entities` | rust native (G2) |
| graph writes | `ingest_to_graph_batch` → FalkorDB | `store::DocGraphStore::ingest_to_graph_batch` (Cypher byte-identical) | rust native (G3) |
| project registry | `project_contract.py` | `project_contract.rs` | rust native |
| entity provider seam | `build_graph_components` | `providers::EntityProvider` | rust native |
| langextract provider | `entity_extractors.extract_entities_langextract` (langextract lib) | `langextract::LangextractProvider` (reqwest: Gemini/OpenAI/Ollama, same env + retry ladder) | rust native (LLM — excluded from parity) |
| spacy provider | `build_spacy_pipeline` | `providers::SpacyProvider` (EntityRuler subset native; statistical NER → python sidecar) | rust native + sidecar |
| gliner provider | `extract_entities_gliner*` | `providers::GlinerProvider` (python sidecar subprocess) | python sidecar |
| LLM answer | `graphrag_query_langextract.llm_generate` | `langextract::llm_generate` | rust native (LLM — excluded) |
| query: graph fetch + context + prompt | `fetch_related_graph`/`format_graph_context`/`build_generation_prompt` | `store::{fetch_related_graph, format_graph_context, build_generation_prompt}` + `py_dedent` | rust native (G4, byte-level) |
| embeddings bge-m3 dense | `SentenceTransformer` in-process | `embed::encode` — Plan B: python sidecar subprocess (ONNX `ort` = later spike) | python sidecar (optional G5) |
| Qdrant vector upsert/search | local-mode embedded Qdrant | excluded | python-side |
| pdf/docx/pptx/xlsx readers + xlsx structured pipeline | pypdf/python-docx/python-pptx/openpyxl + `extractor/excel` | excluded | python-side |
| neo4j provider | `graph_store.Neo4jGraphStore` (rollback path) | excluded (falkordb only) | python-side |
| MCP tool surface | `mcp_graph_rag.py` | phase 13 surface — not ported here | out of scope |

### Mock/parity protocol

- LLM/NER disabled identically: deterministic regex miner (`mine_entities`) records entities per paragraph into `fixtures/doctiny_fixture.json`; python side replays it through a monkeypatched `build_graph_components` (real `process_text` + real `build_graph_components_from_entities` + real FalkorDB writes), rust side replays it through the `fixture` provider. Vector upserts stubbed on the python side.
- Entity ids are deterministic: `uuid5(NAMESPACE_URL, \`{project}::{type}::{name_norm}\`)`; graph write Cypher is byte-identical; `textwrap.dedent` semantics (incl. whitespace-only line emptying) replicated in `store::py_dedent`.
- Masked in graph diff: volatile timestamps + engine-internal ids (`MASKED_PROPS` of `dual_write_diff.py` + `_start_id`/`_end_id` — FalkorDB edge endpoint ids are creation-order dependent).

### Exclusions

- Live LLM extraction/answers (langextract/gemini/openai) — provider ports exist; outputs are non-deterministic, hence mocked in parity.
- Binary document readers + xlsx structured pipeline — python-side; deterministic but out of the phase 14 Scope A parity corpus (markdown).
- Local-mode (embedded) Qdrant — python-embedded store, no wire protocol; vector stage stubbed/excluded. Remote Qdrant would need a follow-up.
- neo4j provider (rollback-only), `neo4j_loader.py` legacy opt-in — excluded; falkordb is the parity flow target.
- MCP server (`mcp_graph_rag.py` tools, `mcp.sh`) — phase 13 surface; ingest CLI only here.

### Suspected shared bugs

- None found in shared crates. One port-fidelity finding (not a bug in python): `graphrag_query_langextract.build_generation_prompt` keeps its 8-space indentation whenever `passages_str` is multi-line, because `textwrap.dedent` computes an empty margin — the rust port replicates this byte-for-byte.
