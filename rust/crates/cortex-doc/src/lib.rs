//! cortex-doc — Rust port của doc-tiny GraphRAG pipeline (Scope A, phase 14
//! của plans/260913-2130-rust-full-migration).
//!
//! Ported modules (Python reference in `doc-tiny/`):
//! - [`chunker`] ← `graphrag_ingest_langextract.split_paragraphs`
//! - [`entity`] ← `_normalize_entity_name`, `_entity_id`,
//!   `build_graph_components_from_entities`, `_select_primary_mention`,
//!   `entity_extractors._normalize_entities/_normalize_relations/_find_span`
//! - [`text_reader`] ← `read_text_file`, `_iter_input_files`, `_safe_source_id`
//!   + `code-tiny/tools/common/scan_ignore`
//! - [`project_contract`] ← `project_contract.py`
//! - [`store`] ← `ingest_to_graph_batch`/`ingest_to_graph` (FalkorDB writes,
//!   Cypher byte-identical) + `graphrag_query_langextract` deterministic reads
//! - [`providers`] ← entity provider seam (langextract/spacy/gliner/fixture)
//! - [`langextract`] ← LLM API path via reqwest + `llm_generate`
//! - [`ingest`] ← `process_text`
//! - [`embed`] ← bge-m3 dense head via the Python embedding sidecar (Plan B)
//!
//! Exclusions (documented in the phase 14 parity report): binary document
//! readers (pdf/docx/pptx/xlsx), the embedded local-mode Qdrant vector store,
//! and the MCP server surface (phase 13).

pub mod canonical;
pub mod chunker;
pub mod embed;
pub mod entity;
pub mod ingest;
pub mod langextract;
pub mod project_contract;
pub mod providers;
pub mod store;
pub mod text_reader;
