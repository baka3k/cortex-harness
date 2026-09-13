//! cortex-doc CLI — ports the ingest CLI portion of
//! `doc-tiny/graphrag_ingest_langextract.py` plus deterministic query helpers
//! of `doc-tiny/graphrag_query_langextract.py`. The MCP tool surface belongs
//! to phase 13 and is intentionally not implemented here (hooks only).

use std::path::PathBuf;
use std::process::ExitCode;

use clap::{Args, Parser, Subcommand};
use cortex_doc::canonical::canonical_json;
use cortex_doc::chunker::split_paragraphs;
use cortex_doc::entity::build_graph_components_from_entities;
use cortex_doc::ingest::{IngestConfig, process_text};
use cortex_doc::langextract::{LangextractProvider, llm_generate};
use cortex_doc::providers::{
    EntityProvider, FixtureProvider, GlinerProvider, SpacyProvider, python_binary,
};
use cortex_doc::store::{DocGraphStore, build_generation_prompt, format_graph_context};
use cortex_doc::text_reader::{iter_input_files, read_text_file, safe_source_id};

#[derive(Parser)]
#[command(
    name = "cortex-doc",
    about = "doc-tiny GraphRAG ingest + deterministic query (Rust port, phase 14)"
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Ingest a document / folder into a FalkorDB doc graph.
    Ingest(Box<IngestArgs>),
    /// Dump the paragraph chunking of a file as JSON (parity gate helper).
    Chunk {
        #[arg(long)]
        file: PathBuf,
        #[arg(long, default_value_t = 1200)]
        max_paragraph_chars: usize,
    },
    /// Run `build_graph_components_from_entities` on an entity JSON file.
    Assemble {
        /// JSON file: array of raw entity payloads, or
        /// `{"entities": [...], "relations": [...]}`.
        #[arg(long)]
        entities: PathBuf,
        #[arg(long)]
        normalize_mode: Option<String>,
        #[arg(long)]
        project_id_normalized: Option<String>,
        #[arg(long)]
        no_merge: bool,
    },
    /// Deterministic query stages: fetch related graph, format context,
    /// build the generation prompt (LLM answer optional).
    Query {
        #[arg(long)]
        falkordb_uri: String,
        #[arg(long)]
        falkordb_graph: String,
        /// Comma-separated entity ids, or a JSON file containing an array.
        #[arg(long)]
        entity_ids: String,
        /// JSON file containing an array of passage strings.
        #[arg(long)]
        passages: PathBuf,
        #[arg(long)]
        query_text: String,
        /// Also generate the answer via `llm_generate` (needs API keys).
        #[arg(long)]
        answer: bool,
        #[arg(long)]
        llm_model: Option<String>,
        #[arg(long)]
        langextract_model_url: Option<String>,
    },
    /// Embed texts via the Python bge-m3 sidecar (Plan B).
    Embed {
        /// Texts to encode (repeatable).
        #[arg(long = "text")]
        texts: Vec<String>,
        #[arg(long)]
        model: Option<String>,
        #[arg(long)]
        device: Option<String>,
    },
    /// Resolve project registry targets (project_contract port check).
    Targets {
        #[arg(long)]
        project_id: String,
        #[arg(long)]
        config_dir: Option<PathBuf>,
    },
}

#[derive(Args)]
struct IngestArgs {
    /// Folder scanned recursively for .pdf/.txt/.md/.docx/.pptx/.xlsx.
    #[arg(long)]
    folder: Option<PathBuf>,
    #[arg(long)]
    text_file: Option<PathBuf>,
    #[arg(long)]
    md: Option<PathBuf>,
    #[arg(long)]
    raw_text: Option<String>,
    #[arg(long)]
    source_id: Option<String>,
    #[arg(long)]
    project_id: Option<String>,
    /// FalkorDB server URI (redis://host:port). Required: the Rust port has
    /// no embedded FalkorDBLite backend.
    #[arg(long)]
    falkordb_uri: Option<String>,
    #[arg(long)]
    falkordb_graph: Option<String>,
    /// Compat flag (vector stage excluded from the Rust port).
    #[arg(long)]
    collection: Option<String>,
    #[arg(long, default_value_t = 1200)]
    max_paragraph_chars: usize,
    #[arg(long, default_value_t = 150)]
    min_paragraph_chars: usize,
    #[arg(long)]
    skip_llm_short: bool,
    #[arg(long, default_value = "fixture")]
    entity_provider: String,
    /// Path to the parity fixture JSON (`--entity-provider fixture`).
    #[arg(long)]
    entity_fixture: Option<PathBuf>,
    #[arg(long, default_value = "aggressive")]
    entity_normalize_mode: String,
    #[arg(long)]
    no_entity_merge: bool,
    #[arg(long, default_value_t = 1)]
    graph_batch_size: usize,
    /// GLiNER model (Python sidecar).
    #[arg(long, default_value = "urchade/gliner_large-v2.1")]
    gliner_model_name: String,
    #[arg(
        long,
        default_value = "PERSON,ORG,PRODUCT,GPE,DATE,TECH,CRYPTO,STANDARD"
    )]
    gliner_labels: String,
    #[arg(long, default_value_t = 0.3)]
    gliner_threshold: f64,
    #[arg(long, default_value = "en_core_web_sm")]
    spacy_model: String,
    #[arg(long)]
    ruler_json: Option<String>,
}

fn resolve_provider(args: &IngestArgs) -> Result<Box<dyn EntityProvider>, String> {
    match args.entity_provider.as_str() {
        "fixture" => {
            let path = args
                .entity_fixture
                .as_ref()
                .ok_or("--entity-fixture is required with --entity-provider fixture")?;
            let payload: serde_json::Value = serde_json::from_str(
                &std::fs::read_to_string(path).map_err(|e| format!("read {path:?}: {e}"))?,
            )
            .map_err(|e| format!("parse fixture: {e}"))?;
            Ok(Box::new(FixtureProvider::from_json(&payload)))
        }
        "langextract" => Ok(Box::new(
            LangextractProvider::from_env().map_err(|e| e.to_string())?,
        )),
        "gemini" => Ok(Box::new(
            LangextractProvider::from_gemini_env().map_err(|e| e.to_string())?,
        )),
        "spacy" => Ok(Box::new(
            SpacyProvider::new(&args.spacy_model, args.ruler_json.as_deref())
                .map_err(|e| e.to_string())?,
        )),
        "gliner" => Ok(Box::new(GlinerProvider::new(
            &args.gliner_model_name,
            GlinerProvider::parse_labels(Some(&args.gliner_labels)),
            args.gliner_threshold,
        ))),
        other => Err(format!(
            "Unsupported entity provider: {other} (fixture|langextract|gemini|spacy|gliner)"
        )),
    }
}

enum IngestInput {
    Folder(PathBuf),
    Text(Option<PathBuf>, String),
}

fn run_ingest(
    args: IngestArgs,
    config: IngestConfig,
    input: IngestInput,
    uri: String,
    graph: String,
) -> Result<(), String> {
    let mut provider = resolve_provider(&args)?;
    let mut store = DocGraphStore::connect(&uri, &graph)?;

    match input {
        IngestInput::Folder(folder) => {
            if !folder.exists() {
                return Err(format!("folder not found: {}", folder.display()));
            }
            let files = iter_input_files(&folder).map_err(|e| e.to_string())?;
            if files.is_empty() {
                return Err("No supported files found in folder.".to_string());
            }
            eprintln!("Found {} files in folder.", files.len());
            let mut totals = cortex_doc::ingest::IngestStats::default();
            for (idx, file_path) in files.iter().enumerate() {
                eprintln!(
                    "[{}/{}] Processing: {}",
                    idx + 1,
                    files.len(),
                    file_path.display()
                );
                let rel = safe_source_id(&folder, file_path).map_err(|e| e.to_string())?;
                let source_id = match &config.cli_source_id {
                    Some(prefix) => format!("{prefix}__{rel}"),
                    None => rel,
                };
                let raw_text = read_text_file(file_path).map_err(|e| e.to_string())?;
                let stats = process_text(
                    &mut store,
                    provider.as_mut(),
                    &raw_text,
                    &source_id,
                    &config,
                )
                .map_err(|e| e.to_string())?;
                totals.paragraphs += stats.paragraphs;
                totals.entity_nodes += stats.entity_nodes;
                totals.entity_relations += stats.entity_relations;
                totals.graph_batches += stats.graph_batches;
                totals.llm_skipped_short += stats.llm_skipped_short;
                totals.skipped_short += stats.skipped_short;
            }
            println!(
                "{}",
                canonical_json(&serde_json::json!({
                    "ok": true,
                    "files": files.len(),
                    "paragraphs": totals.paragraphs,
                    "entity_nodes": totals.entity_nodes,
                    "entity_relations": totals.entity_relations,
                    "graph_batches": totals.graph_batches,
                    "graph": graph,
                }))
            );
        }
        IngestInput::Text(path, raw_text) => {
            let _ = path;
            let source_id = config
                .cli_source_id
                .clone()
                .unwrap_or_else(|| "raw_text".to_string());
            let stats = process_text(
                &mut store,
                provider.as_mut(),
                &raw_text,
                &source_id,
                &config,
            )
            .map_err(|e| e.to_string())?;
            println!(
                "{}",
                canonical_json(&serde_json::json!({
                    "ok": true,
                    "files": 1,
                    "paragraphs": stats.paragraphs,
                    "entity_nodes": stats.entity_nodes,
                    "entity_relations": stats.entity_relations,
                    "graph_batches": stats.graph_batches,
                    "graph": graph,
                }))
            );
        }
    }
    Ok(())
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("cortex-doc: {error}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<(), String> {
    let cli = Cli::parse();
    match cli.command {
        Command::Chunk {
            file,
            max_paragraph_chars,
        } => {
            let text =
                read_text_file(&file).map_err(|e| format!("read {}: {e}", file.display()))?;
            let chunks = split_paragraphs(&text, max_paragraph_chars);
            println!("{}", canonical_json(&serde_json::json!(chunks)));
            Ok(())
        }
        Command::Assemble {
            entities,
            normalize_mode,
            project_id_normalized,
            no_merge,
        } => {
            let payload: serde_json::Value = serde_json::from_str(
                &std::fs::read_to_string(&entities)
                    .map_err(|e| format!("read {}: {e}", entities.display()))?,
            )
            .map_err(|e| format!("parse {}: {e}", entities.display()))?;
            let (raw_entities, raw_relations) = match &payload {
                serde_json::Value::Array(list) => (list.clone(), Vec::new()),
                other => (
                    other
                        .get("entities")
                        .and_then(serde_json::Value::as_array)
                        .cloned()
                        .unwrap_or_default(),
                    other
                        .get("relations")
                        .and_then(serde_json::Value::as_array)
                        .cloned()
                        .unwrap_or_default(),
                ),
            };
            let (nodes, relations) = build_graph_components_from_entities(
                &raw_entities,
                &raw_relations,
                !no_merge,
                normalize_mode.as_deref().unwrap_or("aggressive"),
                project_id_normalized.as_deref(),
            );
            let output = serde_json::json!({
                "nodes": nodes.iter().map(|(_, n)| n).collect::<Vec<_>>(),
                "node_keys_in_order": nodes.iter().map(|(k, _)| k.clone()).collect::<Vec<_>>(),
                "relations": relations,
            });
            println!("{}", canonical_json(&output));
            Ok(())
        }
        Command::Query {
            falkordb_uri,
            falkordb_graph,
            entity_ids,
            passages,
            query_text,
            answer,
            llm_model,
            langextract_model_url,
        } => {
            let ids: Vec<String> = if let Some(path) = entity_ids.strip_prefix('@') {
                serde_json::from_str(
                    &std::fs::read_to_string(path).map_err(|e| format!("read {path}: {e}"))?,
                )
                .map_err(|e| format!("parse {path}: {e}"))?
            } else {
                entity_ids
                    .split(',')
                    .map(str::trim)
                    .map(str::to_string)
                    .collect()
            };
            let passages: Vec<String> = serde_json::from_str(
                &std::fs::read_to_string(&passages)
                    .map_err(|e| format!("read {}: {e}", passages.display()))?,
            )
            .map_err(|e| format!("parse passages: {e}"))?;

            let mut store = DocGraphStore::connect(&falkordb_uri, &falkordb_graph)?;
            let rows = store
                .fetch_related_graph(&ids)
                .map_err(|e| format!("fetch_related_graph: {e}"))?;
            let context = format_graph_context(&rows);
            let prompt = build_generation_prompt(&query_text, &context, &passages);
            let mut output = serde_json::json!({
                "nodes": context.nodes,
                "edges": context.edges,
                "prompt": prompt,
            });
            if answer {
                let model = llm_model.unwrap_or_else(|| {
                    std::env::var("LANGEXTRACT_MODEL_ID")
                        .unwrap_or_else(|_| "gemini-2.5-flash".to_string())
                });
                let generated = llm_generate(&prompt, &model, langextract_model_url.as_deref())
                    .map_err(|e| e.to_string())?;
                output["answer"] = serde_json::Value::String(generated);
            }
            println!("{}", canonical_json(&output));
            Ok(())
        }
        Command::Embed {
            texts,
            model,
            device,
        } => {
            if texts.is_empty() {
                return Err("--text is required at least once".to_string());
            }
            let (dimension, vectors) =
                cortex_doc::embed::encode(&texts, model.as_deref(), device.as_deref())
                    .map_err(|e| e.to_string())?;
            println!(
                "{}",
                canonical_json(&serde_json::json!({
                    "dimension": dimension,
                    "python": python_binary(),
                    "vectors": vectors,
                }))
            );
            Ok(())
        }
        Command::Targets {
            project_id,
            config_dir,
        } => {
            let targets = cortex_doc::project_contract::resolve_project_targets(
                &project_id,
                config_dir.as_deref(),
            )
            .map_err(|e| e.to_string())?;
            println!("{}", canonical_json(&serde_json::json!(targets)));
            Ok(())
        }
        Command::Ingest(args) => run_ingest_command(*args),
    }
}

fn run_ingest_command(mut args: IngestArgs) -> Result<(), String> {
    // Exactly one input, mirroring the argparse check.
    let inputs = [
        args.folder.is_some(),
        args.text_file.is_some(),
        args.md.is_some(),
        args.raw_text.is_some(),
    ];
    if inputs.iter().filter(|b| **b).count() != 1 {
        return Err(
            "Provide exactly one of --folder, --text-file, --md, or --raw-text. \
             (pdf/docx/pptx/xlsx readers are Python-side; use doc-tiny ingest.)"
                .to_string(),
        );
    }

    // project_id fallback semantics (deprecation warning included).
    let mut config = IngestConfig {
        max_paragraph_chars: args.max_paragraph_chars,
        min_paragraph_chars: args.min_paragraph_chars,
        skip_llm_short: args.skip_llm_short,
        merge_entities: !args.no_entity_merge,
        normalize_mode: args.entity_normalize_mode.clone(),
        graph_batch_size: args.graph_batch_size.max(1),
        project_id: None,
        project_id_normalized: None,
        cli_source_id: args.source_id.clone(),
    };
    let raw_project_id = args
        .project_id
        .clone()
        .or_else(|| args.source_id.clone())
        .map(|v| v.trim().to_string())
        .filter(|v| !v.is_empty());
    if args.project_id.is_none() && args.source_id.is_some() {
        eprintln!(
            "[cortex-doc] WARNING: --project-id not set; falling back to --source-id. \
             Pass --project-id explicitly to enable per-project entity isolation."
        );
    }
    config.project_id = raw_project_id.clone();
    config.project_id_normalized = raw_project_id
        .as_deref()
        .map(cortex_doc::project_contract::casefold);

    // Registry routing with the explicit-graph escape hatch (same rule as
    // python: --falkordb-graph flag or FALKORDB_GRAPH/FALKORDB_DATABASE).
    let graph_was_explicit = args.falkordb_graph.is_some()
        || std::env::var("FALKORDB_GRAPH").is_ok_and(|v| !v.is_empty())
        || std::env::var("FALKORDB_DATABASE").is_ok_and(|v| !v.is_empty());
    let mut graph = args
        .falkordb_graph
        .clone()
        .or_else(|| std::env::var("FALKORDB_GRAPH").ok())
        .or_else(|| std::env::var("FALKORDB_DATABASE").ok())
        .unwrap_or_else(|| "neo4j".to_string());
    if let Some(project) = &raw_project_id {
        let targets = cortex_doc::project_contract::resolve_project_targets(project, None)
            .map_err(|e| e.to_string())?;
        if !graph_was_explicit {
            graph = targets.doc_graph;
        }
    }

    let uri = args
        .falkordb_uri
        .take()
        .or_else(|| std::env::var("FALKORDB_URI").ok())
        .filter(|v| !v.trim().is_empty())
        .ok_or(
            "FalkorDB server URI required: pass --falkordb-uri or set FALKORDB_URI \
             (the Rust port has no embedded FalkorDBLite backend)",
        )?;

    let input = if let Some(folder) = args.folder.take() {
        IngestInput::Folder(folder)
    } else if let Some(path) = args.text_file.take().or(args.md.take()) {
        let text = read_text_file(&path).map_err(|e| format!("read {}: {e}", path.display()))?;
        IngestInput::Text(Some(path), text)
    } else {
        let raw = std::mem::take(&mut args.raw_text).expect("checked exactly one input");
        IngestInput::Text(None, raw.trim().to_string())
    };
    run_ingest(args, config, input, uri, graph)
}
