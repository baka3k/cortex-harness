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

**OVERALL: PASS** (optional gates excluded from verdict)

### Notes

- Chunking/assembly/write/query Cypher text is byte-identical to the python module; entity ids are uuid5(NAMESPACE_URL, `{project}::{type}::{name_norm}`).
- Excluded from parity (documented): live LLM extraction + answers, PDF/DOCX/PPTX/XLSX readers (python-side), xlsx structured pipeline (python-side), local-mode Qdrant vector store (python-embedded, no wire protocol), neo4j provider (optional rollback path only), MCP server surface (phase 13).
