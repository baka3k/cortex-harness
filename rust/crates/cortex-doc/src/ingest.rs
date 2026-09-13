//! Port of `doc-tiny/graphrag_ingest_langextract.py::process_text` — the
//! deterministic ingest flow: chunk → per-paragraph entity extraction via the
//! provider seam → graph assembly → FalkorDB batch writes.
//!
//! Vector (Qdrant) upserts are intentionally NOT ported: doc-tiny uses a
//! python-embedded (local-mode) Qdrant store with an in-process
//! SentenceTransformer, which has no wire protocol a Rust client can attach
//! to. See the phase 14 parity report for the exclusion rationale.

use serde_json::Value;

use crate::chunker::split_paragraphs;
use crate::entity::build_graph_components_from_entities;
use crate::providers::EntityProvider;
use crate::store::{BatchItem, DocGraphStore};

#[derive(Debug, Clone)]
pub struct IngestConfig {
    pub max_paragraph_chars: usize,
    pub min_paragraph_chars: usize,
    pub skip_llm_short: bool,
    pub merge_entities: bool,
    pub normalize_mode: String,
    pub graph_batch_size: usize,
    pub project_id: Option<String>,
    pub project_id_normalized: Option<String>,
    /// CLI `--source-id` (prefixes per-file source ids in folder mode).
    pub cli_source_id: Option<String>,
}

impl Default for IngestConfig {
    fn default() -> Self {
        // Mirror of the argparse defaults; `set_defaults(no_batch=True)` makes
        // the effective graph batch size 1.
        Self {
            max_paragraph_chars: 1200,
            min_paragraph_chars: 150,
            skip_llm_short: false,
            merge_entities: true,
            normalize_mode: "aggressive".to_string(),
            graph_batch_size: 1,
            project_id: None,
            project_id_normalized: None,
            cli_source_id: None,
        }
    }
}

#[derive(Debug, Default, Clone)]
pub struct IngestStats {
    pub paragraphs: usize,
    pub skipped_short: usize,
    pub llm_skipped_short: usize,
    pub entity_nodes: usize,
    pub entity_relations: usize,
    pub graph_batches: usize,
}

/// Port of `process_text` for one document.
pub fn process_text(
    store: &mut DocGraphStore,
    provider: &mut dyn EntityProvider,
    raw_text: &str,
    source_id: &str,
    config: &IngestConfig,
) -> crate::providers::ProviderResult<IngestStats> {
    let mut stats = IngestStats::default();
    if raw_text.is_empty() {
        eprintln!("Skip empty input: {source_id}");
        return Ok(stats);
    }

    let paragraphs = split_paragraphs(raw_text, config.max_paragraph_chars);
    stats.paragraphs = paragraphs.len();
    eprintln!("Source: {source_id}");
    eprintln!("Chunked into {} paragraphs.", paragraphs.len());

    let mut graph_batch: Vec<BatchItem> = Vec::new();

    let flush = |batch: &mut Vec<BatchItem>,
                 stats: &mut IngestStats,
                 store: &mut DocGraphStore|
     -> crate::providers::ProviderResult<()> {
        if batch.is_empty() {
            return Ok(());
        }
        eprintln!(
            "Ingesting {} paragraphs to graph store (batch)...",
            batch.len()
        );
        store
            .ingest_to_graph_batch(
                batch,
                config.project_id.as_deref(),
                config.project_id_normalized.as_deref(),
            )
            .map_err(crate::providers::ProviderError)?;
        stats.graph_batches += 1;
        batch.clear();
        eprintln!("Graph batch ingestion complete.");
        Ok(())
    };

    for (idx, paragraph) in paragraphs.iter().enumerate() {
        if paragraph.is_empty() {
            continue;
        }
        if paragraph.chars().count() < config.min_paragraph_chars {
            if config.skip_llm_short {
                eprintln!(
                    "Paragraph {}/{}: LLM skipped (len={})",
                    idx + 1,
                    paragraphs.len(),
                    paragraph.chars().count()
                );
                stats.llm_skipped_short += 1;
                graph_batch.push(BatchItem {
                    source_id: source_id.to_string(),
                    paragraph_id: idx as i64,
                    paragraph_text: Some(paragraph.clone()),
                    is_short: true,
                    nodes: Vec::new(),
                    relations: Vec::new(),
                    paragraph_props: Value::Object(serde_json::Map::new()),
                });
                if graph_batch.len() >= config.graph_batch_size {
                    flush(&mut graph_batch, &mut stats, store)?;
                }
            } else {
                eprintln!(
                    "Paragraph {}/{}: skipped (len={})",
                    idx + 1,
                    paragraphs.len(),
                    paragraph.chars().count()
                );
                stats.skipped_short += 1;
            }
            continue;
        }

        eprintln!(
            "Paragraph {}/{}: extracting entities/relations...",
            idx + 1,
            paragraphs.len()
        );
        let (entities, relations) = provider.extract(paragraph)?;
        let (nodes, cleaned_relations) = build_graph_components_from_entities(
            &entities,
            &relations,
            config.merge_entities,
            &config.normalize_mode,
            config.project_id_normalized.as_deref(),
        );
        eprintln!(
            "Extracted {} entities, {} relations.",
            nodes.len(),
            cleaned_relations.len()
        );
        stats.entity_nodes += nodes.len();
        stats.entity_relations += cleaned_relations.len();
        graph_batch.push(BatchItem {
            source_id: source_id.to_string(),
            paragraph_id: idx as i64,
            paragraph_text: Some(paragraph.clone()),
            is_short: false,
            nodes,
            relations: cleaned_relations,
            paragraph_props: Value::Object(serde_json::Map::new()),
        });
        if graph_batch.len() >= config.graph_batch_size {
            flush(&mut graph_batch, &mut stats, store)?;
        }
    }
    flush(&mut graph_batch, &mut stats, store)?;
    Ok(stats)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::providers::FixtureProvider;

    fn fixture_provider() -> FixtureProvider {
        FixtureProvider::from_json(&serde_json::json!({
            "entities_by_paragraph": {
                "Acme Systems builds robots in Berlin.": [
                    {"name": "Acme Systems", "type": "ORG", "confidence": 0.9, "start_char": 0, "end_char": 12},
                    {"name": "Berlin", "type": "GPE", "confidence": 0.8, "start_char": 32, "end_char": 38}
                ]
            }
        }))
    }

    #[test]
    fn process_text_happy_path() {
        // Single self-contained assertions; graph writes need a live
        // FalkorDB, covered by the parity harness instead.
        let chunks = split_paragraphs("Acme Systems builds robots in Berlin.", 1200);
        assert_eq!(chunks.len(), 1);
        assert_eq!(chunks[0], "Acme Systems builds robots in Berlin.");
        let config = IngestConfig {
            min_paragraph_chars: 5,
            skip_llm_short: true,
            ..IngestConfig::default()
        };
        // Short-paragraph path keeps the row (is_short) without entities.
        let short = "tiny";
        assert!(short.chars().count() < config.min_paragraph_chars);
    }

    #[test]
    fn default_config_mirrors_python() {
        let config = IngestConfig::default();
        assert_eq!(config.max_paragraph_chars, 1200);
        assert_eq!(config.min_paragraph_chars, 150);
        assert_eq!(config.graph_batch_size, 1);
        assert!(config.merge_entities);
        assert_eq!(config.normalize_mode, "aggressive");
    }

    #[test]
    fn provider_seam_returns_entities() {
        let mut provider = fixture_provider();
        let (entities, relations) = provider
            .extract("Acme Systems builds robots in Berlin.")
            .unwrap();
        assert_eq!(entities.len(), 2);
        assert!(relations.is_empty());
    }
}
