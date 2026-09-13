//! Spring overlay module — port của `tools/spring/`.

pub mod adapters;
pub mod annotation_catalog;
pub mod cache;
pub mod config;
pub mod detector;
pub mod extractors;
pub mod models;
pub mod pipeline;
pub mod source_scanner;
pub mod value_resolver;
pub mod writer;
pub mod yamlmini;

/// CLI args của `spring_analyzer.py` (parse_args) — phần graph/provider
/// giống `add_graph_provider_arguments` + `add_require_neo4j_argument`.
#[derive(Debug, Clone)]
pub struct SpringCli {
    pub root: String,
    pub languages: String,
    pub project_id: Option<String>,
    pub project_name: Option<String>,
    pub language: Option<String>,
    pub repo: Option<String>,
    pub build_system: String,
    pub commit_sha_before: String,
    pub commit_sha_after: String,
    pub incremental: bool,
    pub changed_files_manifest: String,
    pub deleted_files_manifest: String,
    pub cache_dir: Option<String>,
    pub ignore_cache: bool,
    pub spring_facts_output: String,
    pub dry_run: bool,
    pub verbose: bool,
    pub neo4j_uri: Option<String>,
    pub neo4j_user: Option<String>,
    pub neo4j_password: Option<String>, // sensitive-guard:allow (ten flag / test sample)
    pub neo4j_db: Option<String>,
    pub neo4j_batch_size: i64,
    pub qdrant_url: Option<String>,
    pub qdrant_collection: Option<String>,
    pub device: Option<String>,
    pub enable_message_scan: bool,
    pub message_output_dir: Option<String>,
    pub message_qdrant_collection: Option<String>,
    pub graph_provider: String,
    pub falkordb_uri: Option<String>,
    pub falkordb_path: Option<String>,
    pub falkordb_password: Option<String>, // sensitive-guard:allow (ten flag / test sample)
    pub falkordb_ssl: bool,
    pub falkordb_graph: Option<String>,
    pub ladybug_path: Option<String>,
    pub ladybug_graph: Option<String>,
    pub require_neo4j: String,
}
