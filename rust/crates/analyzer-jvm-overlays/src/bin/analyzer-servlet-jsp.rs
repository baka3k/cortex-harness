//! analyzer-servlet-jsp — Rust port của `tools/servlet_jsp/servlet_jsp_analyzer.py`.
//!
//! Pipeline: analyze (foundation + per-module java/web/jsp/properties +
//! resolve) → preview artifact → apply_graph (stage/promote/cleanup theo
//! module generation). In summary JSON (`_summary`) như Python.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use clap::Parser;
use serde_json::{json, Value};

use cortex_analyzer_framework::cli::{parse_falkordb_uri, validate_falkordb_uri};
use cortex_falkordb::client::FalkorDbClient;
use cortex_graph_writer::store::{FalkorDbStore, GraphStore, LadybugStore, StoreError};

use analyzer_jvm_overlays::pyjson;
use analyzer_jvm_overlays::servlet_jsp::cache::{
    generation_snapshot_checksum, generation_snapshot_path, preview_artifact_path, write_generation_snapshot,
    write_preview_artifact,
};
use analyzer_jvm_overlays::servlet_jsp::models::{ResourceBudgets, ServletJspAnalysisResult, ServletJspModule};
use analyzer_jvm_overlays::servlet_jsp::pipeline::run_servlet_jsp_analysis;
use analyzer_jvm_overlays::servlet_jsp::writer::ServletJspFactWriter;

#[derive(Debug, Parser)]
#[command(no_binary_name = true)]
struct ServletJspArgs {
    #[arg(long, required = true)]
    root: String,
    #[arg(long)]
    project_id: Option<String>,
    #[arg(long = "project_id", hide = true)]
    project_id_alt: Option<String>,
    #[arg(long)]
    project_name: Option<String>,
    #[arg(long = "project_name", hide = true)]
    project_name_alt: Option<String>,
    #[arg(long)]
    language: Option<String>,
    #[arg(long)]
    repo: Option<String>,
    #[arg(long)]
    build_system: Option<String>,
    #[arg(long = "build_system", hide = true)]
    build_system_alt: Option<String>,
    #[arg(long, default_value = "")]
    commit_sha_before: String,
    #[arg(long, default_value = "")]
    commit_sha_after: String,
    #[arg(long)]
    incremental: bool,
    #[arg(long, default_value = "")]
    changed_files_manifest: String,
    #[arg(long, default_value = "")]
    deleted_files_manifest: String,
    #[arg(long)]
    cache_dir: Option<String>,
    #[arg(long)]
    ignore_cache: bool,
    #[arg(long, default_value = "")]
    servlet_jsp_preview_output: String,
    #[arg(long, default_value = "")]
    diagnostics_output: String,
    #[arg(long, default_value = "")]
    fail_on: String,
    #[arg(long)]
    dry_run: bool,
    #[arg(long)]
    quiet: bool,
    #[arg(long)]
    verbose: bool,
    #[arg(long)]
    neo4j_uri: Option<String>,
    #[arg(long)]
    neo4j_user: Option<String>,
    #[arg(long)]
    neo4j_password: Option<String>, // sensitive-guard:allow
    #[arg(long)]
    neo4j_db: Option<String>,
    #[arg(long, default_value_t = 1000)]
    neo4j_batch_size: i64,
    #[arg(long)]
    qdrant_url: Option<String>,
    #[arg(long)]
    qdrant_collection: Option<String>,
    #[arg(long, default_value = "auto")]
    device: String,
    #[arg(long)]
    disable_message_scan: bool,
    #[arg(long)]
    enable_message_scan: bool,
    #[arg(long)]
    message_output_dir: Option<String>,
    #[arg(long)]
    message_qdrant_collection: Option<String>,
    #[arg(long, default_value = "falkordb")]
    graph_provider: String,
    #[arg(long)]
    falkordb_uri: Option<String>,
    #[arg(long)]
    falkordb_path: Option<String>,
    #[arg(long)]
    falkordb_password: Option<String>, // sensitive-guard:allow
    #[arg(long)]
    falkordb_ssl: bool,
    #[arg(long)]
    falkordb_graph: Option<String>,
    #[arg(long)]
    ladybug_path: Option<String>,
    #[arg(long)]
    ladybug_graph: Option<String>,
    #[arg(long, default_value = "auto")]
    require_neo4j: String,
}

fn main() {
    let args = ServletJspArgs::parse_from(std::env::args().skip(1));
    std::process::exit(run(&args));
}

fn graph_writes_disabled() -> bool {
    matches!(
        std::env::var("CORTEX_DISABLE_GRAPH")
            .unwrap_or_default()
            .trim()
            .to_lowercase()
            .as_str(),
        "1" | "true" | "yes" | "on"
    )
}

fn resolve_require_neo4j(args: &ServletJspArgs) -> bool {
    match args.require_neo4j.to_lowercase().as_str() {
        "1" => true,
        "0" => false,
        _ => args.neo4j_uri.as_deref().map(|v| !v.is_empty()).unwrap_or(false),
    }
}

fn open_store(args: &ServletJspArgs) -> Result<Box<dyn GraphStore>, StoreError> {
    match args.graph_provider.to_lowercase().as_str() {
        "ladybug" => {
            let path = args
                .ladybug_path
                .clone()
                .or_else(|| std::env::var("LADYBUG_PATH").ok().filter(|v| !v.is_empty()))
                .ok_or_else(|| StoreError::Invalid("provider=ladybug cần --ladybug-path".into()))?;
            let graph = args
                .ladybug_graph
                .clone()
                .or_else(|| std::env::var("LADYBUG_GRAPH").ok().filter(|v| !v.is_empty()))
                .unwrap_or_else(|| "hyper_graph".to_string());
            Ok(Box::new(LadybugStore::open(Path::new(&path), &graph)?))
        }
        _ => {
            let uri = args
                .falkordb_uri
                .clone()
                .or_else(|| std::env::var("FALKORDB_URI").ok().filter(|v| !v.is_empty()))
                .unwrap_or_else(|| "127.0.0.1:6379".to_string());
            validate_falkordb_uri(&uri)?;
            let (host, port) = parse_falkordb_uri(&uri);
            let client = FalkorDbClient::connect_verified(&host, port)?;
            let graph = args
                .falkordb_graph
                .clone()
                .or_else(|| std::env::var("FALKORDB_GRAPH").ok().filter(|v| !v.is_empty()))
                .unwrap_or_else(|| args.project_id.clone().unwrap_or_else(|| "hyper_graph".into()));
            Ok(Box::new(FalkorDbStore::new(client, graph)))
        }
    }
}

fn normalize_project_path(path: &str) -> String {
    let value = path.replace('\\', "/");
    let mut value = value.as_str();
    while let Some(rest) = value.strip_prefix("./") {
        value = rest;
    }
    value.trim_start_matches('/').to_string()
}

fn load_incremental_paths(manifest: &str, root: &str) -> BTreeSet<String> {
    if manifest.is_empty() {
        return BTreeSet::new();
    }
    cortex_analyzer_framework::manifest::load_manifest_paths(manifest, Path::new(root))
        .into_iter()
        .map(|path| normalize_project_path(&path))
        .collect()
}

fn module_matches_paths(module: &ServletJspModule, paths: &BTreeSet<String>) -> bool {
    let module_files: BTreeSet<String> = [
        &module.java_files,
        &module.descriptor_files,
        &module.jsp_files,
        &module.properties_files,
        &module.build_files,
        &module.static_files,
    ]
    .into_iter()
    .flatten()
    .map(|path| normalize_project_path(path))
    .collect();
    if module_files.intersection(paths).next().is_some() {
        return true;
    }
    let prefix = normalize_project_path(&module.rel_path);
    if prefix.is_empty() || prefix == "." {
        return true;
    }
    paths
        .iter()
        .any(|path| path == &prefix || path.starts_with(&format!("{prefix}/")))
}

fn module_dependency_index(
    result: &ServletJspAnalysisResult,
    module_facts: &[analyzer_jvm_overlays::servlet_jsp::models::ServletJspFact],
) -> analyzer_jvm_overlays::servlet_jsp::models::ServletJspDependencyIndex {
    let mut allowed: BTreeSet<String> = module_facts.iter().map(|fact| fact.stable_id.clone()).collect();
    for fact in module_facts {
        if !fact.source.file_path.is_empty() {
            allowed.insert(normalize_project_path(&fact.source.file_path));
        }
    }
    let mut values: BTreeMap<&str, BTreeMap<String, Vec<String>>> = BTreeMap::new();
    for category in ["files", "components", "mappings", "views", "state_slots"] {
        let source = match category {
            "files" => &result.dependency_index.files,
            "components" => &result.dependency_index.components,
            "mappings" => &result.dependency_index.mappings,
            "views" => &result.dependency_index.views,
            "state_slots" => &result.dependency_index.state_slots,
            _ => unreachable!(),
        };
        let filtered: BTreeMap<String, Vec<String>> = source
            .iter()
            .filter(|(key, _)| allowed.contains(*key))
            .map(|(key, targets)| (key.clone(), targets.clone()))
            .collect();
        values.insert(category, filtered);
    }
    analyzer_jvm_overlays::servlet_jsp::models::ServletJspDependencyIndex {
        files: values.remove("files").unwrap_or_default(),
        components: values.remove("components").unwrap_or_default(),
        mappings: values.remove("mappings").unwrap_or_default(),
        views: values.remove("views").unwrap_or_default(),
        state_slots: values.remove("state_slots").unwrap_or_default(),
    }
}

fn expand_snapshot_dependency_closure(snapshot: &Value, paths: &BTreeSet<String>) -> BTreeSet<String> {
    let mut adjacency: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let result = snapshot.get("result").cloned().unwrap_or(Value::Null);

    let connect = |adjacency: &mut BTreeMap<String, BTreeSet<String>>, left: String, right: String| {
        if left.is_empty() || right.is_empty() {
            return;
        }
        adjacency.entry(left.clone()).or_default().insert(right.clone());
        adjacency.entry(right).or_default().insert(left);
    };

    if let Some(facts) = result.get("semantic_facts").and_then(Value::as_array) {
        for fact in facts {
            let source = fact.get("source").cloned().unwrap_or(Value::Null);
            let file_path = source.get("file_path").and_then(Value::as_str).unwrap_or("");
            let stable_id = fact.get("stable_id").and_then(Value::as_str).unwrap_or("");
            connect(
                &mut adjacency,
                normalize_project_path(file_path),
                stable_id.to_string(),
            );
        }
    }
    let dependency_index = result.get("dependency_index").cloned().unwrap_or(Value::Null);
    for category in ["files", "components", "mappings", "views", "state_slots"] {
        if let Some(map) = dependency_index.get(category).and_then(Value::as_object) {
            for (source, targets) in map {
                if let Some(targets) = targets.as_array() {
                    for target in targets {
                        connect(
                            &mut adjacency,
                            source.clone(),
                            crate::pyjson::py_str(target),
                        );
                    }
                }
            }
        }
    }

    let mut closure: BTreeSet<String> = paths.iter().filter(|path| !path.is_empty()).cloned().collect();
    let mut queue: std::collections::VecDeque<String> = closure.iter().cloned().collect();
    while let Some(token) = queue.pop_front() {
        let neighbors = adjacency.get(&token).cloned().unwrap_or_default();
        for neighbor in neighbors {
            if !closure.contains(&neighbor) {
                closure.insert(neighbor.clone());
                queue.push_back(neighbor);
            }
        }
    }
    closure
}

fn snapshot_mentions_tokens(snapshot: &Value, tokens: &BTreeSet<String>) -> bool {
    let result = snapshot.get("result").cloned().unwrap_or(Value::Null);
    let mut known: BTreeSet<String> = BTreeSet::new();
    if let Some(artifacts) = result.get("artifacts").and_then(Value::as_array) {
        for artifact in artifacts {
            let path = artifact.get("file_path").and_then(Value::as_str).unwrap_or("");
            known.insert(normalize_project_path(path));
        }
    }
    if let Some(facts) = result.get("semantic_facts").and_then(Value::as_array) {
        for fact in facts {
            known.insert(fact.get("stable_id").and_then(Value::as_str).unwrap_or("").to_string());
            let source = fact.get("source").cloned().unwrap_or(Value::Null);
            let file_path = source.get("file_path").and_then(Value::as_str).unwrap_or("");
            known.insert(normalize_project_path(file_path));
        }
    }
    known.remove("");
    known.intersection(tokens).next().is_some()
}

fn summary(result: &ServletJspAnalysisResult, preview: &str, graph: &Value) -> String {
    let payload = pyjson::py_object(vec![
        ("analyzer".into(), json!("servlet_jsp")),
        ("modules".into(), json!(result.modules.len())),
        ("artifacts".into(), json!(result.artifacts.len())),
        ("facts".into(), json!(result.semantic_facts.len())),
        ("relationships".into(), json!(result.relationships.len())),
        ("diagnostics".into(), json!(result.diagnostics.len())),
        ("coverage_status".into(), json!(result.coverage_status)),
        ("truncation_count".into(), json!(result.truncation_count)),
        (
            "baseline_advanced".into(),
            json!(graph.get("baseline_advanced").and_then(Value::as_bool).unwrap_or(false)),
        ),
        (
            "stage".into(),
            json!(graph.get("stage").and_then(Value::as_str).unwrap_or("preview")),
        ),
        ("applied".into(), json!(graph.get("applied").and_then(Value::as_i64).unwrap_or(0))),
        ("created".into(), json!(graph.get("created").and_then(Value::as_i64).unwrap_or(0))),
        ("updated".into(), json!(graph.get("updated").and_then(Value::as_i64).unwrap_or(0))),
        ("deleted".into(), json!(graph.get("deleted").and_then(Value::as_i64).unwrap_or(0))),
        ("preserved".into(), json!(graph.get("preserved").and_then(Value::as_i64).unwrap_or(0))),
        ("preview".into(), json!(preview)),
    ]);
    pyjson::dumps_compact(&payload)
}

fn run(args: &ServletJspArgs) -> i32 {
    let root = crate_root(&args.root);
    if !root.is_dir() {
        eprintln!("[servlet_jsp] ERROR: root not found: {}", args.root);
        return 2;
    }
    let project_id = args
        .project_id
        .clone()
        .or_else(|| args.project_id_alt.clone())
        .unwrap_or_else(|| basename(&args.root));
    let project_name = args
        .project_name
        .clone()
        .or_else(|| args.project_name_alt.clone())
        .unwrap_or_else(|| project_id.clone());
    let budgets = ResourceBudgets::default();
    let result = run_servlet_jsp_analysis(
        &root.to_string_lossy(),
        &project_id,
        &project_name,
        &manifest_paths(&args.deleted_files_manifest, &root),
    );
    let preview = if !args.servlet_jsp_preview_output.is_empty() {
        args.servlet_jsp_preview_output.clone()
    } else {
        preview_artifact_path(args.cache_dir.as_deref(), &root.to_string_lossy(), &project_id)
            .to_string_lossy()
            .to_string()
    };
    if let Err(error) = write_preview_artifact(Path::new(&preview), &result) {
        eprintln!("[servlet_jsp] ERROR: preview write failed: {error}");
        return 3;
    }
    if !args.diagnostics_output.is_empty() {
        let payload = pyjson::py_object(vec![
            ("artifact_role".into(), json!("diagnostics")),
            (
                "diagnostics".into(),
                Value::Array(result.diagnostics.iter().map(|item| item.to_value()).collect()),
            ),
        ]);
        let _ = pyjson::dumps_pretty(&payload);
        // diagnostics-output chỉ là side channel — ghi qua secure write.
        let _ = analyzer_jvm_overlays::servlet_jsp::cache::secure_atomic_json_write(
            Path::new(&args.diagnostics_output),
            &payload,
        );
    }
    let failed = match args.fail_on.as_str() {
        "error" if result.diagnostics.iter().any(|item| item.severity == "error") => 4,
        "partial" if result.coverage_status != "complete" => 5,
        "truncation" if result.truncation_count != 0 => 6,
        _ => 0,
    };
    if failed != 0 {
        let graph = json!({"stage": "validation_failed", "baseline_advanced": false});
        eprintln!("{}", summary(&result, &preview, &graph));
        return failed;
    }
    let mut graph = json!({
        "stage": if args.dry_run { "preview" } else { "graphless" },
        "baseline_advanced": false,
        "applied": 0,
        "created": 0,
        "updated": 0,
        "deleted": 0,
        "preserved": result.semantic_facts.len(),
    });
    if !args.dry_run {
        graph = match apply_graph(args, &result, &budgets) {
            Ok(graph) => graph,
            Err(error) => {
                eprintln!("[servlet_jsp] ERROR: {error:?}");
                return 3;
            }
        };
    }
    if !args.quiet {
        println!("{}", summary(&result, &preview, &graph));
    }
    0
}

fn crate_root(root: &str) -> PathBuf {
    cortex_analyzer_framework::cli::abs_root(root)
}

fn basename(root: &str) -> String {
    Path::new(root)
        .file_name()
        .map(|n| n.to_string_lossy().to_string())
        .unwrap_or_else(|| root.to_string())
}

fn manifest_paths(manifest: &str, root: &Path) -> Vec<String> {
    if manifest.is_empty() {
        return Vec::new();
    }
    cortex_analyzer_framework::manifest::load_manifest_paths(manifest, root)
        .into_iter()
        .collect()
}

fn apply_graph(
    args: &ServletJspArgs,
    result: &ServletJspAnalysisResult,
    budgets: &ResourceBudgets,
) -> Result<Value, String> {
    if graph_writes_disabled() {
        return Ok(json!({
            "stage": "graphless", "baseline_advanced": false, "applied": 0,
            "created": 0, "updated": 0, "deleted": 0,
            "preserved": result.semantic_facts.len(),
        }));
    }
    if args.graph_provider != "falkordb" {
        // Neo4j cần credentials; Rust backend chỉ support falkordb/ladybug.
        let credentials = args.neo4j_uri.is_some() && args.neo4j_user.is_some() && args.neo4j_password.is_some();
        if !credentials {
            if resolve_require_neo4j(args) {
                return Err("--require-neo4j is on but Neo4j credentials are incomplete".to_string());
            }
            return Ok(json!({
                "stage": "graphless", "baseline_advanced": false, "applied": 0,
                "created": 0, "updated": 0, "deleted": 0,
                "preserved": result.semantic_facts.len(),
            }));
        }
        return Err("Neo4j provider is not supported on the Rust backend".to_string());
    }
    let mut store = open_store(args).map_err(|error| error.to_string())?;
    let database = args.falkordb_graph.clone();
    let mut summary = json!({
        "stage": "apply", "baseline_advanced": false, "applied": 0,
        "created": 0, "updated": 0, "deleted": 0, "preserved": 0,
    });
    let mut writer =
        ServletJspFactWriter::new(store.as_mut(), database.clone(), args.neo4j_batch_size.max(1) as usize, args.verbose);
    let previously_active: BTreeSet<String> = writer
        .list_active_modules(&result.project_id)
        .map_err(|error| error.to_string())?
        .into_iter()
        .collect();
    let current_modules: BTreeSet<String> = result.modules.iter().map(|module| module.module_id.clone()).collect();
    let stale_modules: Vec<String> = previously_active.difference(&current_modules).cloned().collect();
    let modules_to_apply = modules_to_apply(&mut writer, args, result, budgets, &previously_active)?;

    for module in &result.modules {
        let module_facts: Vec<analyzer_jvm_overlays::servlet_jsp::models::ServletJspFact> = result
            .semantic_facts
            .iter()
            .filter(|item| item.module_id == module.module_id)
            .cloned()
            .collect();
        let module_relationships: Vec<analyzer_jvm_overlays::servlet_jsp::models::ServletJspRelationship> = result
            .relationships
            .iter()
            .filter(|item| item.module_id == module.module_id)
            .cloned()
            .collect();
        if !modules_to_apply.contains(&module.module_id) {
            let preserved = summary.get("preserved").and_then(Value::as_i64).unwrap_or(0)
                + (module_facts.len() + module_relationships.len()) as i64;
            summary["preserved"] = json!(preserved);
            continue;
        }
        let prior_ids = if previously_active.contains(&module.module_id) {
            prior_semantic_ids(&mut writer, args, result, budgets, &module.module_id)?
        } else {
            BTreeSet::new()
        };
        let mut current_ids: BTreeSet<String> =
            module_facts.iter().map(|item| item.stable_id.clone()).collect();
        current_ids.extend(module_relationships.iter().map(|item| item.stable_id.clone()));
        summary["created"] = json!(summary.get("created").and_then(Value::as_i64).unwrap_or(0)
            + (current_ids.difference(&prior_ids).count() as i64));
        summary["updated"] = json!(summary.get("updated").and_then(Value::as_i64).unwrap_or(0)
            + (current_ids.intersection(&prior_ids).count() as i64));
        summary["deleted"] = json!(summary.get("deleted").and_then(Value::as_i64).unwrap_or(0)
            + (prior_ids.difference(&current_ids).count() as i64));
        let module_result = build_module_result(result, module, &module_facts, &module_relationships);
        let generation_id = module_generation_id(&module_result, budgets);
        let snapshot_path = generation_snapshot_path(
            args.cache_dir.as_deref(),
            &result.root,
            &result.project_id,
            &module.module_id,
            &generation_id,
        );
        let checksum =
            generation_snapshot_checksum(&module_result, &module.module_id, &generation_id, budgets);
        let node_rows: Vec<Value> = module_facts
            .iter()
            .map(|item| Value::Object(item.to_graph_node(&generation_id)))
            .collect();
        let relationship_rows: Vec<Value> = module_relationships
            .iter()
            .map(|item| Value::Object(item.to_graph_row(&generation_id)))
            .collect();
        writer
            .stage_generation(
                &result.project_id,
                &module.module_id,
                &generation_id,
                node_rows.clone(),
                relationship_rows.clone(),
            )
            .map_err(|error| error.to_string())?;
        if !args.quiet {
            println!("[{}] servlet_jsp_facts {}/{}", args.graph_provider, module_facts.len(), module_facts.len());
            println!(
                "[{}] servlet_jsp_relationships {}/{}",
                args.graph_provider,
                module_relationships.len(),
                module_relationships.len()
            );
        }
        writer
            .promote_generation(
                &result.project_id,
                &module.module_id,
                &generation_id,
                &checksum,
                &result.coverage_status,
            )
            .map_err(|error| error.to_string())?;
        let written_checksum = write_generation_snapshot(
            &snapshot_path,
            &module_result,
            &module.module_id,
            &generation_id,
            budgets,
        )
        .map_err(|error| error.to_string())?;
        if written_checksum != checksum {
            return Err("Applied Servlet/JSP snapshot checksum changed during serialization".to_string());
        }
        let cleanup = writer
            .cleanup_inactive_generations(&result.project_id, &module.module_id)
            .map_err(|error| error.to_string())?;
        if !args.quiet {
            println!(
                "[cleanup][{}] deleted_nodes={} deleted_unknown_functions=0",
                args.graph_provider, cleanup
            );
        }
        summary["applied"] = json!(summary.get("applied").and_then(Value::as_i64).unwrap_or(0)
            + (module_facts.len() + module_relationships.len()) as i64);
    }
    for module_id in &stale_modules {
        let prior_ids = prior_semantic_ids(&mut writer, args, result, budgets, module_id)?;
        summary["deleted"] = json!(summary.get("deleted").and_then(Value::as_i64).unwrap_or(0) + prior_ids.len() as i64);
        let tombstone = tombstone_result(result);
        let generation_id = tombstone_generation_id(&tombstone, budgets, module_id);
        let snapshot_path = generation_snapshot_path(
            args.cache_dir.as_deref(),
            &result.root,
            &result.project_id,
            module_id,
            &generation_id,
        );
        let checksum = generation_snapshot_checksum(&tombstone, module_id, &generation_id, budgets);
        writer
            .stage_generation(&result.project_id, module_id, &generation_id, Vec::new(), Vec::new())
            .map_err(|error| error.to_string())?;
        if !args.quiet {
            println!("[{}] servlet_jsp_facts 0/0", args.graph_provider);
            println!("[{}] servlet_jsp_relationships 0/0", args.graph_provider);
        }
        writer
            .promote_generation(&result.project_id, module_id, &generation_id, &checksum, "empty")
            .map_err(|error| error.to_string())?;
        let written_checksum =
            write_generation_snapshot(&snapshot_path, &tombstone, module_id, &generation_id, budgets)
                .map_err(|error| error.to_string())?;
        if written_checksum != checksum {
            return Err("Applied Servlet/JSP tombstone snapshot checksum changed during serialization".to_string());
        }
        let cleanup = writer
            .cleanup_inactive_generations(&result.project_id, module_id)
            .map_err(|error| error.to_string())?;
        if !args.quiet {
            println!(
                "[cleanup][{}] deleted_nodes={} deleted_unknown_functions=0",
                args.graph_provider, cleanup
            );
        }
    }
    if current_modules.is_empty() && stale_modules.is_empty() && !args.quiet {
        println!("[cleanup][{}] deleted_nodes=0 deleted_unknown_functions=0", args.graph_provider);
    }
    let advanced = !modules_to_apply.is_empty() || !stale_modules.is_empty();
    summary["baseline_advanced"] = json!(advanced);
    summary["stage"] = json!("complete");
    Ok(summary)
}

fn modules_to_apply(
    writer: &mut ServletJspFactWriter,
    args: &ServletJspArgs,
    result: &ServletJspAnalysisResult,
    _budgets: &ResourceBudgets,
    previously_active: &BTreeSet<String>,
) -> Result<BTreeSet<String>, String> {
    let current: BTreeSet<String> = result.modules.iter().map(|module| module.module_id.clone()).collect();
    if !args.incremental {
        return Ok(current);
    }
    if args.ignore_cache {
        return Ok(current);
    }
    let changed_paths = load_incremental_paths(&args.changed_files_manifest, &result.root);
    let deleted_paths = load_incremental_paths(&args.deleted_files_manifest, &result.root);
    let requested_paths: BTreeSet<String> = changed_paths.union(&deleted_paths).cloned().collect();
    if requested_paths.is_empty() {
        return Ok(current);
    }
    let mut affected: BTreeSet<String> = current.difference(previously_active).cloned().collect();
    let mut matched_requested_paths: BTreeSet<String> = BTreeSet::new();
    let intersection: BTreeSet<String> = current.intersection(previously_active).cloned().collect();
    for module_id in intersection {
        let state = writer
            .get_active_generation(&result.project_id, &module_id)
            .map_err(|error| error.to_string())?;
        let generation_id = state
            .get("active_generation")
            .map(crate::pyjson::py_str)
            .unwrap_or_default();
        let checksum = state
            .get("snapshot_checksum")
            .map(crate::pyjson::py_str)
            .unwrap_or_default();
        if generation_id.is_empty() || checksum.is_empty() {
            return Ok(current);
        }
        let snapshot_file = generation_snapshot_path(
            args.cache_dir.as_deref(),
            &result.root,
            &result.project_id,
            &module_id,
            &generation_id,
        );
        let snapshot = load_generation_snapshot_checked(
            &snapshot_file,
            &result.root,
            &result.project_id,
            &module_id,
            &generation_id,
            &checksum,
        );
        let Some(snapshot) = snapshot else {
            if !args.quiet {
                eprintln!(
                    "[servlet_jsp] incremental snapshot fallback module={module_id} status=invalid"
                );
            }
            return Ok(current);
        };
        let module = result
            .modules
            .iter()
            .find(|module| module.module_id == module_id)
            .unwrap();
        let module_matches: BTreeSet<String> = requested_paths
            .iter()
            .filter(|path| module_matches_paths(module, &BTreeSet::from([(*path).clone()])))
            .cloned()
            .collect();
        let snapshot_matches: BTreeSet<String> = requested_paths
            .iter()
            .filter(|path| {
                snapshot_mentions_tokens(
                    &snapshot,
                    &expand_snapshot_dependency_closure(&snapshot, &BTreeSet::from([(*path).clone()])),
                )
            })
            .cloned()
            .collect();
        let matched: BTreeSet<String> = module_matches.union(&snapshot_matches).cloned().collect();
        if !matched.is_empty() {
            affected.insert(module_id.clone());
            matched_requested_paths.extend(matched);
        }
    }
    if matched_requested_paths != requested_paths {
        return Ok(current);
    }
    Ok(affected)
}

/// `load_generation_snapshot` — verify envelope metadata + checksum; trả
/// envelope Value hoặc None (fallback full apply).
fn load_generation_snapshot_checked(
    path: &Path,
    root: &str,
    project_id: &str,
    module_id: &str,
    generation_id: &str,
    expected_checksum: &str,
) -> Option<Value> {
    let metadata = std::fs::symlink_metadata(path).ok()?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return None;
    }
    if metadata.len() > 512 * 1024 * 1024 {
        return None;
    }
    let raw = std::fs::read(path).ok()?;
    let envelope: Value = serde_json::from_slice(&raw).ok()?;
    let mut envelope = envelope.as_object().cloned()?;
    let checksum = envelope
        .remove("payload_sha256")
        .and_then(|value| value.as_str().map(str::to_string))
        .unwrap_or_default();
    let actual = analyzer_jvm_overlays::servlet_jsp::cache::payload_checksum(&Value::Object(envelope.clone()));
    if checksum.is_empty() || checksum != actual || (!expected_checksum.is_empty() && expected_checksum != actual) {
        return None;
    }
    let expected = [
        ("artifact_role", json!("graph_applied_generation")),
        ("schema_version", json!(1)),
        ("parser_version", json!("servlet-jsp-v2026-07-13-1")),
        ("project_id", json!(project_id)),
        (
            "project_root_digest",
            json!(analyzer_jvm_overlays::pyutil::sha256_hex(
                analyzer_jvm_overlays::pyutil::realpath(Path::new(root)).to_string_lossy().as_bytes()
            )),
        ),
        ("module_id", json!(module_id)),
        ("generation_id", json!(generation_id)),
        ("budget_fingerprint", json!(ResourceBudgets::default().fingerprint())),
    ];
    for (key, value) in expected {
        if envelope.get(key) != Some(&value) {
            return None;
        }
    }
    Some(Value::Object(envelope))
}

fn prior_semantic_ids(
    writer: &mut ServletJspFactWriter,
    args: &ServletJspArgs,
    result: &ServletJspAnalysisResult,
    _budgets: &ResourceBudgets,
    module_id: &str,
) -> Result<BTreeSet<String>, String> {
    if args.ignore_cache {
        return Ok(BTreeSet::new());
    }
    let state = writer
        .get_active_generation(&result.project_id, module_id)
        .map_err(|error| error.to_string())?;
    let generation_id = state
        .get("active_generation")
        .map(crate::pyjson::py_str)
        .unwrap_or_default();
    let checksum = state
        .get("snapshot_checksum")
        .map(crate::pyjson::py_str)
        .unwrap_or_default();
    if generation_id.is_empty() || checksum.is_empty() {
        return Ok(BTreeSet::new());
    }
    let snapshot_file = generation_snapshot_path(
        args.cache_dir.as_deref(),
        &result.root,
        &result.project_id,
        module_id,
        &generation_id,
    );
    let Some(snapshot) = load_generation_snapshot_checked(
        &snapshot_file,
        &result.root,
        &result.project_id,
        module_id,
        &generation_id,
        &checksum,
    ) else {
        return Ok(BTreeSet::new());
    };
    let payload = snapshot.get("result").cloned().unwrap_or(Value::Null);
    let mut ids = BTreeSet::new();
    for category in ["semantic_facts", "relationships"] {
        if let Some(items) = payload.get(category).and_then(Value::as_array) {
            for item in items {
                if let Some(stable_id) = item.get("stable_id").and_then(Value::as_str)
                    && !stable_id.is_empty()
                {
                    ids.insert(stable_id.to_string());
                }
            }
        }
    }
    Ok(ids)
}

fn build_module_result(
    result: &ServletJspAnalysisResult,
    module: &ServletJspModule,
    module_facts: &[analyzer_jvm_overlays::servlet_jsp::models::ServletJspFact],
    module_relationships: &[analyzer_jvm_overlays::servlet_jsp::models::ServletJspRelationship],
) -> ServletJspAnalysisResult {
    ServletJspAnalysisResult {
        project_id: result.project_id.clone(),
        project_name: result.project_name.clone(),
        root: result.root.clone(),
        modules: vec![module.clone()],
        artifacts: result
            .artifacts
            .iter()
            .filter(|item| item.module_id == module.module_id)
            .cloned()
            .collect(),
        parser_capabilities: result.parser_capabilities.clone(),
        semantic_facts: module_facts.to_vec(),
        relationships: module_relationships.to_vec(),
        dependency_index: module_dependency_index(result, module_facts),
        diagnostics: result.diagnostics.clone(),
        coverage_status: result.coverage_status.clone(),
        missing_anchor_count: result.missing_anchor_count,
        ambiguity_count: result.ambiguity_count,
        truncation_count: result.truncation_count,
    }
}

fn module_generation_id(result: &ServletJspAnalysisResult, budgets: &ResourceBudgets) -> String {
    let payload = pyjson::dumps_compact(&result.to_dict());
    analyzer_jvm_overlays::servlet_jsp::models::stable_digest(
        &[
            analyzer_jvm_overlays::servlet_jsp::models::SERVLET_JSP_PARSER_VERSION.to_string(),
            budgets.fingerprint(),
            payload,
        ],
        24,
    )
}

fn tombstone_result(result: &ServletJspAnalysisResult) -> ServletJspAnalysisResult {
    ServletJspAnalysisResult {
        project_id: result.project_id.clone(),
        project_name: result.project_name.clone(),
        root: result.root.clone(),
        coverage_status: "empty".to_string(),
        ..Default::default()
    }
}

fn tombstone_generation_id(
    tombstone: &ServletJspAnalysisResult,
    budgets: &ResourceBudgets,
    module_id: &str,
) -> String {
    analyzer_jvm_overlays::servlet_jsp::models::stable_digest(
        &[
            "tombstone".to_string(),
            module_id.to_string(),
            module_generation_id(tombstone, budgets),
        ],
        24,
    )
}
