//! Pipeline `build_call_graph` của cplus_analyzer.py: scan → include-impact
//! selection → cleanup → parse → 2-pass payload validation → index → call
//! resolution → streaming writer planes → `[SCAN_RESULT]`.

use std::collections::{BTreeSet, HashMap, HashSet};
use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value};

use cortex_analyzer_framework::cli::{abs_root, AnalyzerArgs};
use cortex_analyzer_framework::embedding_artifact::{self, EmbeddingEmission};
use cortex_analyzer_framework::manifest::load_manifest_paths;
use cortex_graph_writer::language_writer::{
    FilesVariant, LanguageCodeWriter, WriteAllPayload,
};

use crate::cparse::{self, FilePayload};
use crate::cscan;
use crate::position::{basename, commonpath2, dirname, relpath, splitext};
use crate::quality;
use crate::rcparse;
use crate::validate::{self, COLLECTION_LABELS};
use crate::CplusExtraArgs;

type Row = Map<String, Value>;

const STREAM_BATCH_FILES: usize = 500;

/// Entry index cho call resolution (khớp dict `entry` của Python).
#[derive(Debug, Clone)]
pub struct FunctionEntry {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub scope_name: Option<String>,
    pub arity: i64,
    pub file_path: Option<String>,
}

fn normalize_rel(item: &str) -> String {
    item.replace('\\', "/")
}

fn str_of(row: &Row, key: &str) -> String {
    row.get(key)
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string()
}

fn val_of(row: &Row, key: &str) -> Value {
    row.get(key).cloned().unwrap_or(Value::Null)
}

/// Row node chuẩn hoá: chọn đúng tập key Python đưa vào `write_all` và đổi
/// `symbol_id` → `id` + project scope fields.
fn project_row(
    source: &Row,
    keys: &[&str],
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    let mut row = Map::new();
    for key in keys {
        if *key == "id" {
            row.insert("id".into(), val_of(source, "symbol_id"));
            continue;
        }
        row.insert((*key).to_string(), val_of(source, key));
    }
    row.insert("project_id".into(), json!(project_id));
    row.insert("project_name".into(), json!(project_name));
    row.insert("language".into(), json!(language));
    row.insert("repo".into(), json!(repo));
    row.insert("build_system".into(), json!(build_system));
    row
}

const NS_KEYS: [&str; 10] = [
    "id", "name", "qualified_name", "file_path", "start_line", "end_line", "code",
    "comment", "summary", "note",
];
const TYPE_KEYS: [&str; 11] = [
    "id", "name", "qualified_name", "kind", "file_path", "start_line", "end_line",
    "code", "comment", "summary", "note",
];
const FUNC_TYPE_KEYS: [&str; 6] = [
    "id", "type_signature", "file_path", "start_line", "end_line", "code",
];
const FUNC_KEYS: [&str; 15] = [
    "id", "name", "qualified_name", "kind", "scope_name", "file_path", "start_byte",
    "end_byte", "start_line", "end_line", "arity", "code", "comment", "summary", "note",
];
const FIELD_KEYS: [&str; 9] = [
    "id", "name", "qualified_name", "scope_name", "type_signature", "file_path",
    "start_line", "end_line", "code",
];
const ALIAS_KEYS: [&str; 9] = [
    "id", "name", "qualified_name", "kind", "target_name", "file_path", "start_line",
    "end_line", "code",
];
const TEMPLATE_KEYS: [&str; 6] = [
    "id", "name", "file_path", "start_line", "end_line", "code",
];

/// `attach_compact_quality_provenance`.
fn attach_compact_quality_provenance(payload: &mut FilePayload) {
    let quality_obj = payload
        .parse_meta
        .get("quality")
        .and_then(Value::as_object)
        .cloned()
        .filter(|q| !q.is_empty());
    let Some(quality_obj) = quality_obj else {
        return;
    };
    let context = quality_obj.get("context").cloned().unwrap_or(Value::Null);
    let ctx_str = |key: &str, fallback: Option<&str>| -> String {
        context
            .get(key)
            .and_then(Value::as_str)
            .map(str::to_string)
            .or_else(|| fallback.map(str::to_string))
            .unwrap_or_else(|| "unknown".into())
    };
    let mut compact = Map::new();
    compact.insert(
        "schema_version".into(),
        quality_obj
            .get("schema_version")
            .cloned()
            .unwrap_or(json!("1")),
    );
    compact.insert(
        "tier".into(),
        quality_obj.get("tier").cloned().unwrap_or(json!("retry_required")),
    );
    compact.insert(
        "backend".into(),
        json!(ctx_str(
            "backend",
            payload.parse_meta.get("parser_backend").and_then(Value::as_str)
        )),
    );
    compact.insert(
        "parser_language".into(),
        json!(ctx_str(
            "parser_language",
            payload.parse_meta.get("parser_language").and_then(Value::as_str)
        )),
    );
    compact.insert(
        "context_fingerprint".into(),
        json!(quality_obj
            .get("context_fingerprint")
            .and_then(Value::as_str)
            .or_else(|| payload
                .parse_meta
                .get("context_fingerprint")
                .and_then(Value::as_str))
            .unwrap_or("")),
    );
    compact.insert(
        "recovery_policy_version".into(),
        json!(quality_obj
            .get("recovery_policy_version")
            .and_then(Value::as_str)
            .or_else(|| payload
                .parse_meta
                .get("recovery_policy_version")
                .and_then(Value::as_str))
            .unwrap_or("1")),
    );
    compact.insert(
        "selected_candidate".into(),
        json!(quality_obj
            .get("selected_candidate")
            .and_then(Value::as_str)
            .unwrap_or("baseline")),
    );
    compact.insert(
        "selection_reason".into(),
        json!(quality_obj
            .get("selection_reason")
            .and_then(Value::as_str)
            .unwrap_or("first_pass")),
    );
    payload
        .parse_meta
        .insert("quality_provenance".into(), Value::Object(compact.clone()));
    if let Some(file_def) = payload.file_def.as_mut() {
        file_def.insert("parse_quality".into(), Value::Object(compact.clone()));
    }
    let tier = compact.get("tier").and_then(Value::as_str).unwrap_or("retry_required");
    payload.parse_meta.insert(
        "evidence_policy".into(),
        json!({
            "strong_relation_types": ["CALLS", "INHERITS", "CONTAINS"],
            "strong_relations_allowed": tier != "quarantined",
            "weak_evidence_allowed": true,
        }),
    );
}

fn stable_point_id(symbol_id: &str) -> String {
    uuid::Uuid::new_v5(&uuid::Uuid::NAMESPACE_URL, symbol_id.as_bytes()).to_string()
}

fn unknown_function_id(callee_name: &str) -> String {
    let normalized = callee_name.trim();
    let normalized = if normalized.is_empty() { "unknown" } else { normalized };
    format!("unknown::{}", stable_point_id(&normalized.to_lowercase()))
}

fn call_site_id(
    caller_id: &str,
    callee_id: &str,
    file_path: &str,
    line: i64,
    column: i64,
    call_type: &str,
) -> String {
    let key = format!("{caller_id}:{callee_id}:{file_path}:{line}:{column}:{call_type}");
    uuid::Uuid::new_v5(&uuid::Uuid::NAMESPACE_URL, key.as_bytes()).to_string()
}

/// `_detect_git_commit_sha` — `git -C root rev-parse --short HEAD`.
fn detect_git_commit_sha(root: &Path) -> String {
    let output = std::process::Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["rev-parse", "--short", "HEAD"])
        .stderr(std::process::Stdio::null())
        .output();
    match output {
        Ok(out) if out.status.success() => {
            let sha = String::from_utf8_lossy(&out.stdout).trim().to_string();
            if sha.is_empty() {
                "unknown".to_string()
            } else {
                sha
            }
        }
        _ => "unknown".to_string(),
    }
}

/// Chạy pipeline — trả exit code (0 OK, 2 root not found / args invalid).
#[allow(clippy::too_many_lines)]
pub fn execute(args: &AnalyzerArgs, extra: &CplusExtraArgs) -> Result<i32, String> {
    let root = abs_root(&args.root);
    if !root.is_dir() {
        eprintln!("Root not found: {}", args.root);
        return Ok(2);
    }
    if extra.neo4j_state.is_some() {
        eprintln!(
            "[state] C++ graph resume is disabled until durable checkpoint \
             fingerprints are implemented; remove --neo4j-state and restart"
        );
        return Ok(2);
    }
    let verbose = args.verbose;

    let parse_quality = extra.parse_quality.clone();
    if !matches!(parse_quality.as_str(), "off" | "report" | "repair") {
        return Err(format!(
            "argument --parse-quality: invalid choice: '{parse_quality}' \
             (choose from 'off', 'report', 'repair')"
        ));
    }
    let mut disable_compile_db_bootstrap = extra.disable_compile_db_bootstrap;
    if matches!(parse_quality.as_str(), "report" | "repair") {
        disable_compile_db_bootstrap = true;
    }

    let compile_db_path = extra
        .compile_commands_path
        .clone()
        .map(|p| abs_root(&p).to_string_lossy().to_string())
        .unwrap_or_else(|| root.join("compile_commands.json").to_string_lossy().to_string());
    if disable_compile_db_bootstrap {
        println!("[compile-db] executable bootstrap disabled; using validated existing file if present");
    }
    let compile_db = load_compile_commands_index(Path::new(&compile_db_path), &root);
    if let Some(index) = &compile_db {
        println!(
            "[compile-db] loaded {} entries (cpp_files={}, c_files={}) from {}",
            index.entries,
            index.cpp_files.len(),
            index.c_files.len(),
            compile_db_path
        );
    } else {
        println!(
            "[compile-db] not found at {}; parser mode falls back to extension/content heuristics",
            compile_db_path
        );
    }

    let store = if args.graph_writes_disabled() {
        None
    } else {
        Some(args.open_store().map_err(|e| e.to_string())?)
    };

    // ── Manifests (incremental) ─────────────────────────────────────────────
    let mut changed_files: BTreeSet<String> = BTreeSet::new();
    let mut deleted_files: BTreeSet<String> = BTreeSet::new();
    if args.incremental {
        if let Some(manifest) = &args.changed_files_manifest {
            changed_files = load_manifest_paths(manifest, &root);
        }
        if let Some(manifest) = &args.deleted_files_manifest {
            deleted_files = load_manifest_paths(manifest, &root);
        }
        if verbose {
            println!(
                "[diff] incremental manifests changed={} deleted={}",
                changed_files.len(),
                deleted_files.len()
            );
        }
    }

    // ── Dry run ─────────────────────────────────────────────────────────────
    if args.dry_run {
        let files = cscan::scan_c_family_files(&root);
        if args.incremental && !changed_files.is_empty() {
            let rel_to_abs: HashMap<String, PathBuf> = files
                .iter()
                .cloned()
                .map(|path| (cscan::rel_posix(&root, &path), path))
                .collect();
            let changed_existing: HashSet<String> = changed_files
                .iter()
                .filter(|path| rel_to_abs.contains_key(*path))
                .cloned()
                .collect();
            let deps_by_file = cscan::collect_include_graph(&files, &root);
            let impacted = cscan::expand_impacted_files_by_includes(&changed_existing, &deps_by_file);
            let selected: BTreeSet<String> = changed_existing
                .iter()
                .chain(impacted.iter())
                .cloned()
                .collect();
            println!(
                "Dry run (incremental): {} C/C++ files selected (manifest={} impacted={})",
                selected.len(),
                changed_files.len(),
                impacted.len()
            );
        } else {
            println!("Dry run: {} C/C++ files found", files.len());
        }
        return Ok(0);
    }

    // ── Project scope ───────────────────────────────────────────────────────
    let project_id = args.project_id_or_root();
    let project_name = args.project_name();
    let language = if args.language.as_deref().unwrap_or("").is_empty() {
        "cplus".to_string()
    } else {
        args.language.clone().unwrap()
    };
    let repo = if args.repo.as_deref().unwrap_or("").is_empty() {
        root.to_string_lossy().to_string()
    } else {
        args.repo.clone().unwrap()
    };
    let build_system = args.build_system();
    let commit_sha = if !args.commit_sha_after.is_empty() {
        args.commit_sha_after.clone()
    } else {
        detect_git_commit_sha(&root)
    };
    let parse_run_id = format!(
        "parse-{}-{}",
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs(),
        &uuid::Uuid::new_v4().simple().to_string()[..8]
    );

    let start_time = std::time::Instant::now();
    println!(
        "[start] kicking off cplus scan; this can take several minutes on a large repo (progress will be reported below)"
    );

    // ── Scan ────────────────────────────────────────────────────────────────
    let all_scanned_paths = cscan::scan_c_family_files(&root);
    let all_rel_paths: Vec<String> = all_scanned_paths
        .iter()
        .map(|path| cscan::rel_posix(&root, path))
        .collect();
    let rel_to_abs: HashMap<String, PathBuf> = all_scanned_paths
        .iter()
        .cloned()
        .zip(all_rel_paths.iter().cloned())
        .map(|(path, rel)| (rel, path))
        .collect();
    let changed_set: HashSet<String> = changed_files
        .iter()
        .map(|item| normalize_rel(item))
        .filter(|item| !item.is_empty())
        .collect();
    let deleted_set: HashSet<String> = deleted_files
        .iter()
        .map(|item| normalize_rel(item))
        .filter(|item| !item.is_empty())
        .collect();

    let mut selected: Option<BTreeSet<String>> = None;
    let all_file_paths: Vec<PathBuf> = if args.incremental {
        let changed_existing: HashSet<String> = changed_set
            .iter()
            .filter(|path| rel_to_abs.contains_key(*path))
            .cloned()
            .collect();
        let deps_by_file = cscan::collect_include_graph(&all_scanned_paths, &root);
        let impacted = cscan::expand_impacted_files_by_includes(&changed_existing, &deps_by_file);
        let mut sel = BTreeSet::new();
        sel.extend(changed_existing.iter().cloned());
        sel.extend(impacted.iter().cloned());
        selected = Some(sel);
        all_rel_paths
            .iter()
            .filter(|path| selected.as_ref().unwrap().contains(*path))
            .map(|path| rel_to_abs[path].clone())
            .collect()
    } else {
        all_scanned_paths.clone()
    };
    println!(
        "[scan] Found {} C/C++/resource files under {}",
        all_file_paths.len(),
        args.root
    );
    let total_files = all_file_paths.len();

    // ── Parse ───────────────────────────────────────────────────────────────
    let mut raw_payloads: Vec<(String, FilePayload)> = Vec::with_capacity(total_files);
    for (index, file_path) in all_file_paths.iter().enumerate() {
        if verbose && (index == 0 || (index + 1) % 50 == 0 || index + 1 == total_files) {
            println!("[parse] {}/{}: {}", index + 1, total_files, file_path.display());
        }
        let is_resource = rcparse::is_windows_resource_file(file_path);
        let mut payload = if is_resource {
            parse_resource_payload(file_path, &root)?
        } else {
            let is_cpp = cscan::is_cpp_file(file_path, &root);
            cparse::load_or_parse_payload(file_path, &root, is_cpp)
        };
        attach_compact_quality_provenance(&mut payload);
        raw_payloads.push((cscan::rel_posix(&root, file_path), payload));
    }

    // ── Preflight — accepted identities scan-wide + blocked conflicts ───────
    let mut accepted_payload_identities: HashSet<(String, String)> = HashSet::new();
    accepted_payload_identities.insert(("Project".to_string(), project_id.clone()));
    let mut identity_fingerprints: HashMap<(String, String), String> = HashMap::new();
    let mut blocked_payload_identities: HashSet<(String, String)> = HashSet::new();
    for (_, payload) in &raw_payloads {
        let validated = validate::validate_cplus_payload(payload, &project_id, None, None);
        if let Some(file_def) = &validated.payload.file_def {
            let file_path = file_def.get("file_path").and_then(Value::as_str).unwrap_or("");
            if !file_path.is_empty() && !validated.file_quarantined {
                accepted_payload_identities.insert(("File".to_string(), file_path.to_string()));
            }
        }
        for (collection, default_label) in COLLECTION_LABELS {
            for row in validated.payload.rows_of_pub(collection) {
                let identity = row
                    .get("id")
                    .and_then(Value::as_str)
                    .or_else(|| row.get("symbol_id").and_then(Value::as_str))
                    .unwrap_or("")
                    .to_string();
                let label = row
                    .get("label")
                    .and_then(Value::as_str)
                    .unwrap_or(default_label)
                    .to_string();
                if identity.is_empty() {
                    continue;
                }
                let key = (label.clone(), identity.clone());
                let canonical = validate::identity_merge_fingerprint(&label, row);
                match identity_fingerprints.get(&key) {
                    None => {
                        identity_fingerprints.insert(key.clone(), canonical);
                    }
                    Some(previous) if previous != &canonical => {
                        blocked_payload_identities.insert(key.clone());
                    }
                    _ => {}
                }
                accepted_payload_identities.insert(key);
            }
        }
    }
    for blocked in &blocked_payload_identities {
        accepted_payload_identities.remove(blocked);
    }

    // ── Cleanup (changed ∪ deleted) — qua LanguageCodeWriter contract ───────
    let cleanup_targets: Vec<String> = changed_set
        .union(&deleted_set)
        .cloned()
        .collect::<BTreeSet<String>>()
        .into_iter()
        .collect();
    let mut writer = store.map(|store| {
        let mut writer = LanguageCodeWriter::new(
            store,
            args.neo4j_db.clone(),
            extra.neo4j_batch_size.unwrap_or(1000).max(1) as usize,
            verbose,
        );
        let _ = writer.ensure_schema();
        writer
    });
    if args.incremental
        && !cleanup_targets.is_empty()
        && let Some(writer) = writer.as_mut()
    {
        writer
            .cleanup_incremental_files(&project_id, &cleanup_targets)
            .map_err(|e| e.to_string())?;
    }

    // ── Pass 1 — index ──────────────────────────────────────────────────────
    let mut function_index_by_name: HashMap<String, Vec<FunctionEntry>> = HashMap::new();
    let mut function_index_by_name_arity: HashMap<(String, i64), Vec<FunctionEntry>> =
        HashMap::new();
    let mut function_index_by_scope_name: HashMap<(Option<String>, String), Vec<FunctionEntry>> =
        HashMap::new();
    let mut function_index_by_scope_name_arity: HashMap<
        (Option<String>, String, i64),
        Vec<FunctionEntry>,
    > = HashMap::new();
    let mut function_index_by_qualified: HashMap<String, FunctionEntry> = HashMap::new();
    let mut function_index_by_qualified_arity: HashMap<(String, i64), FunctionEntry> =
        HashMap::new();
    let mut function_index_by_file_name: HashMap<(String, String), Vec<FunctionEntry>> =
        HashMap::new();
    let mut function_index_by_file_name_arity: HashMap<(String, String, i64), Vec<FunctionEntry>> =
        HashMap::new();
    let mut using_namespaces_by_file: HashMap<String, Vec<String>> = HashMap::new();
    let mut using_imports_by_file: HashMap<String, std::collections::BTreeMap<String, String>> = HashMap::new();
    let mut includes_by_file: HashMap<String, Vec<String>> = HashMap::new();
    let mut macros_by_file: HashMap<String, std::collections::BTreeMap<String, String>> = HashMap::new();
    let mut alias_targets_by_name: HashMap<String, String> = HashMap::new();
    let mut resource_index_by_symbol: HashMap<String, Vec<Row>> = HashMap::new();
    let mut class_methods: HashMap<String, Vec<FunctionEntry>> = HashMap::new();
    let mut base_relations: Vec<(String, String)> = Vec::new();
    let mut function_count = 0usize;
    let mut type_count = 0usize;
    let mut resource_count = 0usize;

    for (rel_path, payload) in &raw_payloads {
        let validated = validate::validate_cplus_payload(
            payload,
            &project_id,
            Some(&accepted_payload_identities),
            Some(&blocked_payload_identities),
        );
        let payload = &validated.payload;
        using_namespaces_by_file.insert(rel_path.clone(), payload.using_namespaces.clone());
        using_imports_by_file.insert(rel_path.clone(), payload.using_imports.clone());
        includes_by_file.insert(rel_path.clone(), payload.includes.clone());
        macros_by_file.insert(rel_path.clone(), payload.macros.clone());
        for alias in &payload.aliases {
            let name = str_of(alias, "name");
            let target = str_of(alias, "target_name");
            if !name.is_empty() && !target.is_empty() {
                alias_targets_by_name.insert(name, target);
            }
        }
        for resource in &payload.resources {
            resource_count += 1;
            let symbol = str_of(resource, "resource_symbol");
            if !symbol.is_empty() {
                resource_index_by_symbol.entry(symbol).or_default().push(resource.clone());
            }
        }
        for element in &payload.resource_elements {
            let symbol = str_of(element, "resource_symbol");
            if !symbol.is_empty() && symbol != "IDC_STATIC" {
                resource_index_by_symbol.entry(symbol).or_default().push(element.clone());
            }
        }
        for func in &payload.functions {
            function_count += 1;
            let entry = FunctionEntry {
                symbol_id: str_of(func, "symbol_id"),
                qualified_name: str_of(func, "qualified_name"),
                name: str_of(func, "name"),
                scope_name: func.get("scope_name").and_then(Value::as_str).map(str::to_string),
                arity: func.get("arity").and_then(Value::as_i64).unwrap_or(0),
                file_path: func.get("file_path").and_then(Value::as_str).map(str::to_string),
            };
            function_index_by_name
                .entry(entry.name.clone())
                .or_default()
                .push(entry.clone());
            function_index_by_name_arity
                .entry((entry.name.clone(), entry.arity))
                .or_default()
                .push(entry.clone());
            function_index_by_scope_name
                .entry((entry.scope_name.clone(), entry.name.clone()))
                .or_default()
                .push(entry.clone());
            function_index_by_scope_name_arity
                .entry((entry.scope_name.clone(), entry.name.clone(), entry.arity))
                .or_default()
                .push(entry.clone());
            function_index_by_qualified
                .insert(entry.qualified_name.clone(), entry.clone());
            function_index_by_qualified_arity
                .insert((entry.qualified_name.clone(), entry.arity), entry.clone());
            if let Some(file_path) = &entry.file_path {
                function_index_by_file_name
                    .entry((file_path.clone(), entry.name.clone()))
                    .or_default()
                    .push(entry.clone());
                function_index_by_file_name_arity
                    .entry((file_path.clone(), entry.name.clone(), entry.arity))
                    .or_default()
                    .push(entry.clone());
            }
            if let Some(scope_name) = &entry.scope_name {
                class_methods
                    .entry(scope_name.clone())
                    .or_default()
                    .push(entry.clone());
            }
        }
        type_count += payload.types.len();
        for rel in &payload.relations {
            if str_of(rel, "rel_type") == "EXTENDS"
                && str_of(rel, "source_label") == "Type"
                && str_of(rel, "target_label") == "Type"
            {
                base_relations.push((str_of(rel, "source_id"), str_of(rel, "target_id")));
            }
        }
    }

    let known_resource_symbols: BTreeSet<String> =
        resource_index_by_symbol.keys().cloned().collect();

    // ── Resolved includes (build_call_graph version — ABS + commonpath) ─────
    let all_files_set: HashSet<String> = all_scanned_paths
        .iter()
        .map(|p| p.to_string_lossy().to_string())
        .collect();
    let mut file_lookup_by_basename: HashMap<String, Vec<String>> = HashMap::new();
    for path in &all_scanned_paths {
        file_lookup_by_basename
            .entry(basename(&path.to_string_lossy()))
            .or_default()
            .push(path.to_string_lossy().to_string());
    }

    let resolve_include_path = |source_file: &str, include_name: &str| -> Option<String> {
        if include_name.is_empty() {
            return None;
        }
        let include_norm = include_name.replace('\\', "/");
        if include_norm.contains('/') {
            let candidate =
                crate::position::normpath(&format!("{}/{}", dirname(source_file), include_norm));
            if all_files_set.contains(&candidate) {
                return Some(candidate);
            }
            let candidate = crate::position::normpath(&include_norm);
            if all_files_set.contains(&candidate) {
                return Some(candidate);
            }
        }
        let candidates = file_lookup_by_basename.get(&basename(&include_norm))?;
        if candidates.is_empty() {
            return None;
        }
        if candidates.len() == 1 {
            return Some(candidates[0].clone());
        }
        let source_dir = dirname(source_file);
        let score = |candidate_path: &str| -> (i64, i64, String) {
            let candidate_dir = dirname(candidate_path);
            let common = commonpath2(&source_dir, &candidate_dir);
            let common_parts = if common.is_empty() || common == "/" {
                0
            } else {
                common.split('/').filter(|p| !p.is_empty()).count() as i64
            };
            let rel = relpath(&candidate_dir, &source_dir);
            let rel_depth = rel.matches('/').count() as i64;
            (common_parts, -rel_depth, candidate_path.to_string())
        };
        candidates
            .iter()
            .max_by(|a, b| score(a).cmp(&score(b)))
            .cloned()
    };

    let mut resolved_includes_by_file: HashMap<String, Vec<String>> = HashMap::new();
    for (file_path, includes) in &includes_by_file {
        let mut resolved: Vec<String> = Vec::new();
        for inc in includes {
            let abs_source = root.join(file_path).to_string_lossy().to_string();
            if let Some(resolved_path) = resolve_include_path(&abs_source, inc) {
                let rel_inc = cscan::rel_posix(&root, Path::new(&resolved_path));
                resolved.push(rel_inc);
            }
        }
        resolved_includes_by_file.insert(file_path.clone(), resolved);
    }

    let mut deferred_include_relations: Vec<Row> = Vec::new();
    for (file_id, included_files) in &resolved_includes_by_file {
        for inc_file in included_files {
            let mut row = Map::new();
            row.insert("source_label".into(), json!("File"));
            row.insert("target_label".into(), json!("File"));
            row.insert("rel_type".into(), json!("INCLUDES"));
            row.insert("source_id".into(), json!(file_id));
            row.insert("target_id".into(), json!(inc_file));
            row.insert("properties".into(), json!({}));
            deferred_include_relations.push(row);
        }
    }

    // ── Include closure (memo) ──────────────────────────────────────────────
    let mut include_closure_cache: HashMap<String, HashSet<String>> = HashMap::new();
    for file_path in includes_by_file.keys() {
        let mut stack: HashSet<String> = HashSet::new();
        let closure = include_closure(
            file_path,
            &resolved_includes_by_file,
            &mut include_closure_cache,
            &mut stack,
        );
        include_closure_cache.insert(file_path.clone(), closure);
    }

    // ── Transitive using/macros (Python replace-in-place) ───────────────────
    for file_path in using_namespaces_by_file.keys().cloned().collect::<Vec<_>>() {
        let mut visited: HashSet<String> = HashSet::new();
        let mut ns: Vec<String> = Vec::new();
        let mut im: std::collections::BTreeMap<String, String> = std::collections::BTreeMap::new();
        let mut ma: std::collections::BTreeMap<String, String> = std::collections::BTreeMap::new();
        collect_transitive(
            &file_path,
            &resolved_includes_by_file,
            &using_namespaces_by_file,
            &using_imports_by_file,
            &macros_by_file,
            &mut visited,
            &mut ns,
            &mut im,
            &mut ma,
        );
        using_namespaces_by_file.insert(file_path.clone(), ns);
        using_imports_by_file.insert(file_path.clone(), im);
        macros_by_file.insert(file_path, ma);
    }

    // ── POSSIBLE_CALLS override (base→derived method match) ────────────────
    let mut base_to_derived: HashMap<String, Vec<String>> = HashMap::new();
    for (derived, base) in &base_relations {
        if derived.is_empty() || base.is_empty() {
            continue;
        }
        base_to_derived.entry(base.clone()).or_default().push(derived.clone());
    }
    let mut possible_call_relations: Vec<(String, String, String, String)> = Vec::new();
    for (base, derived_list) in &base_to_derived {
        let Some(base_methods) = class_methods.get(base) else {
            continue;
        };
        if base_methods.is_empty() {
            continue;
        }
        for derived in derived_list {
            let Some(derived_methods) = class_methods.get(derived) else {
                continue;
            };
            let derived_index: HashMap<(String, i64), String> = derived_methods
                .iter()
                .map(|entry| ((entry.name.clone(), entry.arity), entry.symbol_id.clone()))
                .collect();
            for base_method in base_methods {
                if let Some(derived_symbol) = derived_index
                    .get(&(base_method.name.clone(), base_method.arity))
                {
                    possible_call_relations.push((
                        base_method.symbol_id.clone(),
                        derived_symbol.clone(),
                        base.clone(),
                        derived.clone(),
                    ));
                }
            }
        }
    }

    // ── Event map ───────────────────────────────────────────────────────────
    let mut event_relations: Vec<(String, String, String, Row)> = Vec::new();
    if let Some(event_map_path) = &extra.event_map {
        let Ok(text) = std::fs::read_to_string(event_map_path) else {
            return Err(format!("Event map not readable: {event_map_path}"));
        };
        let data: Value = serde_json::from_str(&text)
            .map_err(|e| format!("Event map must be valid JSON: {e}"))?;
        let Some(events) = data.get("events").and_then(Value::as_array) else {
            return Err("Event map 'events' must be a list".into());
        };
        for event in events {
            let event_id = event_id_of(event)?;
            for (field, rel_type) in [("emits", "EMITS_EVENT"), ("handles", "HANDLES_EVENT")] {
                let empty = Vec::new();
                for emitter in event.get(field).and_then(Value::as_array).unwrap_or(&empty) {
                    if let Some(pid) = emitter.get("project_id").and_then(Value::as_str)
                        && pid != project_id
                    {
                        continue;
                    }
                    let mut func_id = emitter
                        .get("function_id")
                        .and_then(Value::as_str)
                        .map(str::to_string);
                    if func_id.is_none()
                        && let Some(qualified) =
                            emitter.get("function_qualified").and_then(Value::as_str)
                            && let Some(entry) = function_index_by_qualified.get(qualified)
                        {
                            func_id = Some(entry.symbol_id.clone());
                        }
                    if func_id.is_none()
                        && let Some(name) = emitter.get("function_name").and_then(Value::as_str)
                            && let Some(candidates) = function_index_by_name.get(name)
                            && let Some(first) = candidates.first()
                        {
                            func_id = Some(first.symbol_id.clone());
                        }
                    if let Some(func_id) = func_id {
                        let props = json!({
                            "file_path": emitter.get("file_path").and_then(Value::as_str).unwrap_or(""),
                            "line": emitter.get("line").and_then(Value::as_i64).unwrap_or(0),
                            "column": emitter.get("column").and_then(Value::as_i64).unwrap_or(0),
                            "note": emitter.get("note").and_then(Value::as_str).unwrap_or(""),
                        })
                        .as_object()
                        .cloned()
                        .unwrap();
                        event_relations.push((func_id, event_id.clone(), rel_type.to_string(), props));
                    }
                }
            }
        }
    }

    // ── Streaming graph write ───────────────────────────────────────────────
    if let Some(mut writer) = writer {
        println!("[graph] Streaming nodes and relations in batches...");

        let mut buf_files: Vec<Row> = Vec::new();
        let mut buf_namespaces: Vec<Row> = Vec::new();
        let mut buf_types: Vec<Row> = Vec::new();
        let mut buf_function_types: Vec<Row> = Vec::new();
        let mut buf_functions: Vec<Row> = Vec::new();
        let mut buf_fields: Vec<Row> = Vec::new();
        let mut buf_aliases: Vec<Row> = Vec::new();
        let mut buf_templates: Vec<Row> = Vec::new();
        let mut buf_resources: Vec<Row> = Vec::new();
        let mut buf_resource_elements: Vec<Row> = Vec::new();
        let mut buf_relations: Vec<Row> = Vec::new();
        let mut buf_possible_calls: Vec<Row> = Vec::new();
        let mut buf_unknown_calls: Vec<Row> = Vec::new();
        let mut files_in_buf = 0usize;
        // Phase-02: accumulate embedding categories across all flush batches
        // (plan `260916-1432-legacy-17-vector-emit`).
        let mut embedding_accumulator: Vec<(String, Vec<Value>)> = Vec::new();

        let mut func_metas: Vec<(String, Option<String>, String, String)> = Vec::new();
        let mut infer_type_ids: HashSet<String> = HashSet::new();
        let mut infer_ns_qnames: HashSet<String> = HashSet::new();
        let mut infer_rel_keys: HashSet<[String; 5]> = HashSet::new();

        let mut resource_reference_relations: Vec<Row> = Vec::new();
        let mut resource_reference_keys: HashSet<(String, String, String)> = HashSet::new();

        let allowed_rel_types: HashSet<&str> = [
            "CONTAINS", "DECLARES", "EXTENDS", "TAKES_FUNCTION", "USES_TYPE", "POINTER_TO",
            "ALIASES", "TEMPLATES", "DECLARES_STATEMENT", "DECLARES_DIRECTIVE",
            "BINDS_PARAMETER", "DECLARES_CURSOR", "REFERENCES_CURSOR",
            "REFERENCES_STATEMENT", "READS_FROM", "WRITES_TO", "REFERENCES_TABLE",
            "INCLUDES", "EMITS_EVENT", "HANDLES_EVENT", "CALLS_FUNCTION_POINTER",
            "POSSIBLE_CALLS", "USES_RESOURCE", "BINDS_CONTROL", "HANDLES_CONTROL",
            "OWNS_DIALOG",
        ]
        .into_iter()
        .collect();

        for (_rel_path, payload) in &raw_payloads {
            let validated = validate::validate_cplus_payload(
                payload,
                &project_id,
                Some(&accepted_payload_identities),
                Some(&blocked_payload_identities),
            );
            let payload = &validated.payload;
            let Some(file_def) = &payload.file_def else {
                continue;
            };
            let file_id = str_of(file_def, "file_path");

            buf_files.push(json!({
                "id": file_id,
                "path": file_id,
                "start_line": val_of(file_def, "start_line"),
                "end_line": val_of(file_def, "end_line"),
                "code": val_of(file_def, "code"),
                "comment": val_of(file_def, "comment"),
                "summary": val_of(file_def, "summary"),
                "note": val_of(file_def, "note"),
                "project_id": project_id,
                "project_name": project_name,
                "language": language,
                "repo": repo,
                "build_system": build_system,
            })
            .as_object()
            .cloned()
            .unwrap());

            for ns in &payload.namespaces {
                infer_ns_qnames.insert(str_of(ns, "qualified_name"));
                buf_namespaces.push(project_row(ns, &NS_KEYS, &project_id, &project_name, &language, &repo, &build_system));
            }
            for type_def in &payload.types {
                infer_type_ids.insert(str_of(type_def, "symbol_id"));
                buf_types.push(project_row(type_def, &TYPE_KEYS, &project_id, &project_name, &language, &repo, &build_system));
            }
            for func_type in &payload.function_types {
                buf_function_types.push(project_row(func_type, &FUNC_TYPE_KEYS, &project_id, &project_name, &language, &repo, &build_system));
            }
            for func in &payload.functions {
                let code = str_of(func, "code");
                let func_file = str_of(func, "file_path");
                let (visibility, is_public_api, export_evidence) =
                    cparse::cplus_api_visibility(&code, &func_file);
                func_metas.push((
                    str_of(func, "symbol_id"),
                    func.get("scope_name").and_then(Value::as_str).map(str::to_string),
                    str_of(func, "name"),
                    func_file,
                ));
                let head = code.split('{').next().unwrap_or("");
                let signature: String = head
                    .split_whitespace()
                    .collect::<Vec<_>>()
                    .join(" ")
                    .chars()
                    .take(500)
                    .collect();
                let mut row = project_row(func, &FUNC_KEYS, &project_id, &project_name, &language, &repo, &build_system);
                row.insert("exported".into(), json!(is_public_api));
                row.insert("visibility".into(), json!(visibility));
                row.insert("is_public_api".into(), json!(is_public_api));
                row.insert("visibility_source".into(), json!("source-export"));
                row.insert("export_evidence".into(), json!(export_evidence));
                row.insert("signature".into(), json!(signature));
                buf_functions.push(row);

                for resource_symbol in
                    rcparse::extract_resource_tokens(&code, &known_resource_symbols)
                {
                    let Some(target) = resolve_resource_node(
                        &resource_symbol,
                        &file_id,
                        &resource_index_by_symbol,
                    ) else {
                        continue;
                    };
                    let bind_pattern = regex::Regex::new(&format!(
                        r"\bDDX_[A-Za-z0-9_]+\s*\([^;]*\b{}\b",
                        regex::escape(&resource_symbol)
                    ))
                    .map(|re| re.is_match(&code))
                    .unwrap_or(false);
                    let rel_type = if bind_pattern { "BINDS_CONTROL" } else { "USES_RESOURCE" };
                    add_resource_relation(
                        &mut resource_reference_relations,
                        &mut resource_reference_keys,
                        "Function",
                        &str_of(func, "symbol_id"),
                        rel_type,
                        &target,
                        json!({
                            "file_path": file_id,
                            "evidence": "identifier_reference",
                        })
                        .as_object()
                        .cloned()
                        .unwrap(),
                    );
                    if matches!(
                        target.get("kind").and_then(Value::as_str),
                        Some("dialog") | Some("dialogex")
                    ) && func.get("scope_name").and_then(Value::as_str).is_some()
                    {
                        let ctor_pattern = regex::Regex::new(&format!(
                            r"\b(?:CDialog|CDialogEx)\s*\([^;]*\b{}\b",
                            regex::escape(&resource_symbol)
                        ))
                        .map(|re| re.is_match(&code))
                        .unwrap_or(false);
                        if ctor_pattern {
                            let scope_name =
                                func.get("scope_name").and_then(Value::as_str).unwrap_or("");
                            let owning_type = cparse::type_id_for(None, scope_name);
                            add_resource_relation(
                                &mut resource_reference_relations,
                                &mut resource_reference_keys,
                                "Type",
                                &owning_type,
                                "OWNS_DIALOG",
                                &target,
                                json!({
                                    "file_path": file_id,
                                    "evidence": "dialog_constructor",
                                })
                                .as_object()
                                .cloned()
                                .unwrap(),
                            );
                        }
                    }
                }

                for handler_spec in
                    rcparse::extract_message_map_handlers(&str_of(file_def, "code"), &known_resource_symbols)
                {
                    let resource_symbol = str_of(&handler_spec, "resource_symbol");
                    let Some(target) =
                        resolve_resource_node(&resource_symbol, &file_id, &resource_index_by_symbol)
                    else {
                        continue;
                    };
                    let Some(handler) = resolve_resource_handler(
                        &str_of(&handler_spec, "handler"),
                        &file_id,
                        &function_index_by_qualified,
                        &function_index_by_file_name,
                        &function_index_by_name,
                    ) else {
                        continue;
                    };
                    let mut props = Map::new();
                    props.insert("file_path".into(), json!(file_id));
                    props.insert("line".into(), handler_spec.get("line").cloned().unwrap_or(json!(0)));
                    props.insert(
                        "macro".into(),
                        handler_spec.get("macro").cloned().unwrap_or(json!("")),
                    );
                    props.insert("evidence".into(), json!("mfc_message_map"));
                    add_resource_relation(
                        &mut resource_reference_relations,
                        &mut resource_reference_keys,
                        "Function",
                        &handler,
                        "HANDLES_CONTROL",
                        &target,
                        props,
                    );
                }
            }
            for field in &payload.fields {
                buf_fields.push(project_row(field, &FIELD_KEYS, &project_id, &project_name, &language, &repo, &build_system));
            }
            for alias in &payload.aliases {
                buf_aliases.push(project_row(alias, &ALIAS_KEYS, &project_id, &project_name, &language, &repo, &build_system));
            }
            for template in &payload.templates {
                buf_templates.push(project_row(template, &TEMPLATE_KEYS, &project_id, &project_name, &language, &repo, &build_system));
            }
            for resource in &payload.resources {
                let mut row = json!({
                    "id": str_of(resource, "symbol_id"),
                    "name": str_of(resource, "name"),
                    "qualified_name": str_of(resource, "qualified_name"),
                    "kind": str_of(resource, "kind"),
                    "resource_symbol": str_of(resource, "resource_symbol"),
                    "numeric_id": numeric_id_for(resource, &macros_by_file),
                    "language": language,
                    "resource_language": str_of(resource, "language"),
                    "caption": str_of(resource, "caption"),
                    "style": str_of(resource, "style"),
                    "asset_path": str_of(resource, "asset_path"),
                    "condition": str_of(resource, "condition"),
                    "encoding": str_of(resource, "encoding"),
                    "metadata_json": str_of(resource, "metadata_json"),
                    "file_path": str_of(resource, "file_path"),
                    "start_line": val_of(resource, "start_line"),
                    "end_line": val_of(resource, "end_line"),
                    "code": str_of(resource, "code"),
                    "comment": str_of(resource, "comment"),
                    "summary": str_of(resource, "summary"),
                    "note": str_of(resource, "note"),
                    "project_id": project_id,
                    "project_name": project_name,
                    "repo": repo,
                    "build_system": build_system,
                })
                .as_object()
                .cloned()
                .unwrap();
                row.insert("node_type".into(), json!("code"));
                buf_resources.push(row);
            }
            for element in &payload.resource_elements {
                let mut row = json!({
                    "id": str_of(element, "symbol_id"),
                    "name": str_of(element, "name"),
                    "qualified_name": str_of(element, "qualified_name"),
                    "kind": str_of(element, "kind"),
                    "control_type": str_of(element, "control_type"),
                    "resource_symbol": str_of(element, "resource_symbol"),
                    "numeric_id": numeric_id_for(element, &macros_by_file),
                    "resource_ref": str_of(element, "resource_ref"),
                    "dialog_id": str_of(element, "dialog_id"),
                    "dialog_symbol": str_of(element, "dialog_symbol"),
                    "text": str_of(element, "text"),
                    "style": str_of(element, "style"),
                    "x": val_of(element, "x"),
                    "y": val_of(element, "y"),
                    "width": val_of(element, "width"),
                    "height": val_of(element, "height"),
                    "condition": str_of(element, "condition"),
                    "file_path": str_of(element, "file_path"),
                    "start_line": val_of(element, "start_line"),
                    "end_line": val_of(element, "end_line"),
                    "code": str_of(element, "code"),
                    "comment": str_of(element, "comment"),
                    "summary": str_of(element, "summary"),
                    "note": str_of(element, "note"),
                    "project_id": project_id,
                    "project_name": project_name,
                    "language": language,
                    "repo": repo,
                    "build_system": build_system,
                })
                .as_object()
                .cloned()
                .unwrap();
                row.insert("node_type".into(), json!("code"));
                buf_resource_elements.push(row);
            }

            // ── Relations ───────────────────────────────────────────────────
            buf_relations.push(rel_row("Project", "File", "CONTAINS", &project_id, &file_id));
            for ns in &payload.namespaces {
                buf_relations.push(rel_row("File", "Namespace", "CONTAINS", &file_id, &str_of(ns, "symbol_id")));
            }
            for resource in &payload.resources {
                buf_relations.push(rel_row("File", "Resource", "CONTAINS", &file_id, &str_of(resource, "symbol_id")));
            }
            for type_def in &payload.types {
                infer_rel_keys.insert([
                    "File".into(), "Type".into(), "CONTAINS".into(),
                    file_id.clone(), str_of(type_def, "symbol_id"),
                ]);
                buf_relations.push(rel_row("File", "Type", "CONTAINS", &file_id, &str_of(type_def, "symbol_id")));
            }
            for func in &payload.functions {
                buf_relations.push(rel_row("File", "Function", "CONTAINS", &file_id, &str_of(func, "symbol_id")));
            }
            for field in &payload.fields {
                buf_relations.push(rel_row("File", "Field", "CONTAINS", &file_id, &str_of(field, "symbol_id")));
            }
            for alias in &payload.aliases {
                buf_relations.push(rel_row("File", "Alias", "CONTAINS", &file_id, &str_of(alias, "symbol_id")));
            }
            for template in &payload.templates {
                buf_relations.push(rel_row("File", "Template", "CONTAINS", &file_id, &str_of(template, "symbol_id")));
            }

            for rel in &payload.relations {
                let rel_type = str_of(rel, "rel_type");
                if !allowed_rel_types.contains(rel_type.as_str()) {
                    continue;
                }
                let key = [
                    str_of(rel, "source_label"),
                    str_of(rel, "target_label"),
                    rel_type.clone(),
                    str_of(rel, "source_id"),
                    str_of(rel, "target_id"),
                ];
                if rel_type == "DECLARES" {
                    infer_rel_keys.insert(key.clone());
                }
                let mut row = Map::new();
                row.insert("source_label".into(), json!(key[0]));
                row.insert("target_label".into(), json!(key[1]));
                row.insert("rel_type".into(), json!(key[2]));
                row.insert("source_id".into(), json!(key[3]));
                row.insert("target_id".into(), json!(key[4]));
                row.insert(
                    "properties".into(),
                    rel.get("properties").cloned().unwrap_or(json!({})),
                );
                buf_relations.push(row);
            }

            // ── Calls ───────────────────────────────────────────────────────
            let mut calls = payload.calls.clone();
            calls.sort_by(cparse::compare_call_rows);
            for call in &calls {
                let call_file = call
                    .get("caller_file")
                    .and_then(Value::as_str)
                    .filter(|s| !s.is_empty())
                    .map(str::to_string)
                    .unwrap_or_else(|| file_id.clone());
                let call_line = call.get("call_line").and_then(Value::as_i64).unwrap_or(0);
                let call_column = call.get("call_column").and_then(Value::as_i64).unwrap_or(0);
                let call_start_byte = call.get("call_start_byte").and_then(Value::as_i64).unwrap_or(0);
                let call_type = call
                    .get("call_type")
                    .and_then(Value::as_str)
                    .unwrap_or("call_expression")
                    .to_string();
                let call_branch_kind = call
                    .get("call_branch_kind")
                    .and_then(Value::as_str)
                    .unwrap_or("none")
                    .to_string();
                let call_loop_depth = call.get("call_loop_depth").and_then(Value::as_i64).unwrap_or(0);
                let call_control_frames_json = call
                    .get("call_control_frames_json")
                    .and_then(Value::as_str)
                    .unwrap_or("[]")
                    .to_string();
                let callee_name = str_of(call, "callee_name");
                let caller_scope = call.get("caller_scope").and_then(Value::as_str).map(str::to_string);
                // Python: `caller_scope = call.get("caller_scope") or ""` cho props.
                let caller_scope_prop = caller_scope.clone().unwrap_or_default();
                let caller_id = str_of(call, "caller_id");
                let call_arity = call.get("call_arity").and_then(Value::as_i64).unwrap_or(0);
                let Some(callee_id) = resolve_callee_id(
                    &callee_name,
                    caller_scope.as_deref(),
                    call_arity,
                    Some(&call_file),
                    &macros_by_file,
                    &alias_targets_by_name,
                    &include_closure_cache,
                    &using_namespaces_by_file,
                    &using_imports_by_file,
                    &function_index_by_qualified_arity,
                    &function_index_by_qualified,
                    &function_index_by_file_name_arity,
                    &function_index_by_file_name,
                    &function_index_by_scope_name_arity,
                    &function_index_by_scope_name,
                    &function_index_by_name_arity,
                    &function_index_by_name,
                ) else {
                    let unknown_id = unknown_function_id(&callee_name);
                    let site_id = call_site_id(&caller_id, &unknown_id, &call_file, call_line, call_column, &call_type);
                    buf_unknown_calls.push(json!({
                        "caller_id": caller_id,
                        "unknown_id": unknown_id,
                        "site_id": site_id,
                        "props": {
                            "file_path": call_file,
                            "line": call_line,
                            "column": call_column,
                            "call_start_byte": call_start_byte,
                            "call_branch_kind": call_branch_kind,
                            "call_loop_depth": call_loop_depth,
                            "call_control_frames_json": call_control_frames_json,
                            "call_type": call_type,
                            "call_arity": call_arity,
                            "callee_name": callee_name,
                            "caller_scope": caller_scope_prop,
                            "resolution_class": "unresolved",
                            "semantic_provider": "tree_sitter",
                            "parse_run_id": parse_run_id,
                            "commit_sha": commit_sha,
                            "stable_site_id": site_id,
                        },
                        "project_id": project_id,
                    })
                    .as_object()
                    .cloned()
                    .unwrap());
                    continue;
                };
                let site_id = call_site_id(&caller_id, &callee_id, &call_file, call_line, call_column, &call_type);
                buf_possible_calls.push(json!({
                    "caller_id": caller_id,
                    "callee_id": callee_id,
                    "site_id": site_id,
                    "props": {
                        "file_path": call_file,
                        "line": call_line,
                        "column": call_column,
                        "call_start_byte": call_start_byte,
                        "call_branch_kind": call_branch_kind,
                        "call_loop_depth": call_loop_depth,
                        "call_control_frames_json": call_control_frames_json,
                        "call_type": call_type,
                        "call_arity": call_arity,
                        "callee_name": callee_name,
                        "caller_scope": caller_scope_prop,
                        "resolution_class": "lexical_candidate",
                        "semantic_provider": "tree_sitter",
                        "parse_run_id": parse_run_id,
                        "commit_sha": commit_sha,
                        "stable_site_id": site_id,
                    },
                    "project_id": project_id,
                })
                .as_object()
                .cloned()
                .unwrap());
            }

            files_in_buf += 1;
            if files_in_buf >= STREAM_BATCH_FILES {
                flush_write_buffers(
                    &mut writer,
                    &mut buf_files, &mut buf_namespaces, &mut buf_types,
                    &mut buf_function_types, &mut buf_functions, &mut buf_fields,
                    &mut buf_aliases, &mut buf_templates, &mut buf_resources,
                    &mut buf_resource_elements, &mut buf_relations,
                    &mut buf_possible_calls, &mut buf_unknown_calls,
                    &mut files_in_buf,
                    &mut embedding_accumulator,
                )?;
            }
        }

        flush_write_buffers(
            &mut writer,
            &mut buf_files, &mut buf_namespaces, &mut buf_types,
            &mut buf_function_types, &mut buf_functions, &mut buf_fields,
            &mut buf_aliases, &mut buf_templates, &mut buf_resources,
            &mut buf_resource_elements, &mut buf_relations,
            &mut buf_possible_calls, &mut buf_unknown_calls,
            &mut files_in_buf,
            &mut embedding_accumulator,
        )?;

        // Deferred INCLUDES (đích có thể thuộc buffer sau).
        if !deferred_include_relations.is_empty() {
            writer
                .write_relations_typed(&deferred_include_relations, Some(&project_id))
                .map_err(|e| e.to_string())?;
        }

        // ── Tail relations + inferred DECLARES ──────────────────────────────
        let mut tail_relations: Vec<Row> = resource_reference_relations.clone();
        for (func_id, event_id, rel_type, props) in &event_relations {
            let mut row = Map::new();
            row.insert("source_label".into(), json!("Function"));
            row.insert("target_label".into(), json!("Event"));
            row.insert("rel_type".into(), json!(rel_type));
            row.insert("source_id".into(), json!(func_id));
            row.insert("target_id".into(), json!(event_id));
            row.insert("properties".into(), Value::Object(props.clone()));
            tail_relations.push(row);
        }
        for (source, target, base, derived) in &possible_call_relations {
            let mut row = Map::new();
            row.insert("source_label".into(), json!("Function"));
            row.insert("target_label".into(), json!("Function"));
            row.insert("rel_type".into(), json!("POSSIBLE_CALLS"));
            row.insert("source_id".into(), json!(source));
            row.insert("target_id".into(), json!(target));
            row.insert("properties".into(), json!({"base_type": base, "derived_type": derived}));
            tail_relations.push(row);
        }

        let mut scopes_with_ctor_dtor: HashSet<String> = HashSet::new();
        for (_id, scope_name, name, _file) in &func_metas {
            let Some(scope) = scope_name.as_deref().map(str::trim).filter(|s| !s.is_empty()) else {
                continue;
            };
            let scope_leaf = scope.rsplit("::").next().unwrap_or(scope);
            let name = name.trim();
            if name == scope_leaf || name == format!("~{scope_leaf}") {
                scopes_with_ctor_dtor.insert(scope.to_string());
            }
        }
        let mut inferred_types: Vec<Row> = Vec::new();
        let mut inferred_relations: Vec<Row> = Vec::new();
        for (id, scope_name, _name, file_path) in &func_metas {
            let Some(scope) = scope_name.as_deref().map(str::trim).filter(|s| !s.is_empty()) else {
                continue;
            };
            let type_id = cparse::type_id_for(None, scope);
            let rel_key = [
                "Type".to_string(), "Function".to_string(), "DECLARES".to_string(),
                type_id.clone(), id.clone(),
            ];
            if infer_rel_keys.contains(&rel_key) {
                continue;
            }
            let has_type = infer_type_ids.contains(&type_id);
            if !has_type {
                if infer_ns_qnames.contains(scope) && !scopes_with_ctor_dtor.contains(scope) {
                    continue;
                }
                if !scopes_with_ctor_dtor.contains(scope) {
                    continue;
                }
                inferred_types.push(json!({
                    "id": type_id,
                    "name": scope.rsplit("::").next().unwrap_or(scope),
                    "qualified_name": scope,
                    "kind": "external",
                    "file_path": file_path,
                    "start_line": 0,
                    "end_line": 0,
                    "code": scope,
                    "comment": "",
                    "summary": "",
                    "note": "",
                    "project_id": project_id,
                    "project_name": project_name,
                    "language": language,
                    "repo": repo,
                    "build_system": build_system,
                })
                .as_object()
                .cloned()
                .unwrap());
                infer_type_ids.insert(type_id.clone());
                let file_contains_key = [
                    "File".to_string(), "Type".to_string(), "CONTAINS".to_string(),
                    file_path.clone(), type_id.clone(),
                ];
                if !infer_rel_keys.contains(&file_contains_key) {
                    inferred_relations.push(rel_row("File", "Type", "CONTAINS", file_path, &type_id));
                    infer_rel_keys.insert(file_contains_key);
                }
            }
            let mut row = rel_row("Type", "Function", "DECLARES", &type_id, id);
            row.insert("properties".into(), json!({"inferred": "scope_name"}));
            inferred_relations.push(row);
            infer_rel_keys.insert(rel_key);
        }

        // ── ParseRun node ───────────────────────────────────────────────────
        let parse_run_rows = vec![json!({
            "id": parse_run_id,
            "project_id": project_id,
            "project_name": project_name,
            "language": language,
            "repo": repo,
            "build_system": build_system,
            "commit_sha": commit_sha,
        })
        .as_object()
        .cloned()
        .unwrap()];
        writer
            .write_node_properties_batch("cplus:parse_run", "ParseRun", &parse_run_rows, "id", "id", None)
            .map_err(|e| e.to_string())?;

        // ── Tail + inferred write_all ───────────────────────────────────────
        let tail_and_inferred: Vec<Row> = tail_relations
            .iter()
            .chain(inferred_relations.iter())
            .map(|rel| {
                let mut row = rel.clone();
                if row.get("project_id").and_then(Value::as_str).unwrap_or("").is_empty() {
                    row.insert("project_id".into(), json!(project_id));
                }
                row
            })
            .collect();
        if !inferred_types.is_empty() || !tail_and_inferred.is_empty() {
            writer
                .write_all(&WriteAllPayload {
                    projects: &[],
                    packages: &[],
                    namespaces: &[],
                    files: &[],
                    classes: &[],
                    types: &inferred_types,
                    function_types: &[],
                    functions: &[],
                    fields: &[],
                    aliases: &[],
                    templates: &[],
                    relations: &tail_and_inferred,
                    calls: &[],
                    calls_with_site: &[],
                    properties: &[],
                    events: &[],
                    interfaces: &[],
                    enums: &[],
                    constants: &[],
                    variables: &[],
                    navigators: &[],
                    has_routes: &[],
                    param_lists: &[],
                    workflows: &[],
                    workflow_steps: &[],
                    call_evidence_sites: &[],
                    call_evidence_observations: &[],
                    build_configurations: &[],
                    semantic_coverage: &[],
                    proc_function_joins: &[],
                    proc_host_declarations: &[],
                    use_full_writers: true,
                    files_variant: FilesVariant::Default,
                })
                .map_err(|e| e.to_string())?;
        }

        // Phase-02: emit EmbeddingInputArtifact after all flushes
        // (plan `260916-1432-legacy-17-vector-emit`).
        if let Some(output) = args.embedding_input_output() {
            let files_selected: Vec<String> = if let Some(set) = &selected {
                set.iter().cloned().collect()
            } else {
                all_rel_paths.clone()
            };
            if let Err(error) = embedding_artifact::maybe_emit_embedding_artifact(
                Some(output),
                EmbeddingEmission {
                    parser: "cplus",
                    project_id: &project_id,
                    root_scope: &repo,
                    full_replace: !args.incremental,
                    scanned_directory: true,
                    files_selected,
                    files_deleted: deleted_files.iter().cloned().collect(),
                    categories: embedding_accumulator,
                },
            ) {
                eprintln!("cplus embedding-input artifact failed: {error}");
                return Ok(1);
            }
        }
    }

    println!(
        "[SCAN_RESULT] parser=cplus files={} functions={} classes={} resources={}",
        total_files, function_count, type_count, resource_count
    );
    if verbose {
        println!("[done] Total time: {:.2}s", start_time.elapsed().as_secs_f64());
    }
    Ok(0)
}

// ── Buffer flush ───────────────────────────────────────────────────────────

#[allow(clippy::too_many_arguments)]
fn flush_write_buffers(
    writer: &mut LanguageCodeWriter,
    buf_files: &mut Vec<Row>,
    buf_namespaces: &mut Vec<Row>,
    buf_types: &mut Vec<Row>,
    buf_function_types: &mut Vec<Row>,
    buf_functions: &mut Vec<Row>,
    buf_fields: &mut Vec<Row>,
    buf_aliases: &mut Vec<Row>,
    buf_templates: &mut Vec<Row>,
    buf_resources: &mut Vec<Row>,
    buf_resource_elements: &mut Vec<Row>,
    buf_relations: &mut Vec<Row>,
    buf_possible_calls: &mut Vec<Row>,
    buf_unknown_calls: &mut Vec<Row>,
    files_in_buf: &mut usize,
    embedding_accumulator: &mut Vec<(String, Vec<Value>)>,
) -> Result<(), String> {
    if *files_in_buf == 0 {
        return Ok(());
    }
    if !buf_resources.is_empty() {
        writer
            .write_node_properties_batch("resources", "Resource", buf_resources, "id", "id", None)
            .map_err(|e| e.to_string())?;
    }
    if !buf_resource_elements.is_empty() {
        writer
            .write_node_properties_batch(
                "resource_elements",
                "UIControl",
                buf_resource_elements,
                "id",
                "id",
                None,
            )
            .map_err(|e| e.to_string())?;
    }
    let has_nodes = !buf_files.is_empty()
        || !buf_namespaces.is_empty()
        || !buf_types.is_empty()
        || !buf_function_types.is_empty()
        || !buf_functions.is_empty()
        || !buf_fields.is_empty()
        || !buf_aliases.is_empty()
        || !buf_templates.is_empty();
    if has_nodes || !buf_relations.is_empty() {
        let payload = WriteAllPayload {
            projects: &[],
            packages: &[],
            namespaces: buf_namespaces,
            files: buf_files,
            classes: &[],
            types: buf_types,
            function_types: buf_function_types,
            functions: buf_functions,
            fields: buf_fields,
            aliases: buf_aliases,
            templates: buf_templates,
            relations: buf_relations,
            calls: &[],
            calls_with_site: &[],
            properties: &[],
            events: &[],
            interfaces: &[],
            enums: &[],
            constants: &[],
            variables: &[],
            navigators: &[],
            has_routes: &[],
            param_lists: &[],
            workflows: &[],
            workflow_steps: &[],
            call_evidence_sites: &[],
            call_evidence_observations: &[],
            build_configurations: &[],
            semantic_coverage: &[],
            proc_function_joins: &[],
            proc_host_declarations: &[],
            use_full_writers: true,
            files_variant: FilesVariant::Default,
        };
        // Phase-02: capture embedding categories BEFORE write_all consumes
        // the row buffers (plan `260916-1432-legacy-17-vector-emit`).
        embedding_accumulator.extend(payload.embedding_categories());
        writer
            .write_all(&payload)
            .map_err(|e| e.to_string())?;
    }
    if !buf_possible_calls.is_empty() {
        writer
            .write_possible_calls_with_site(buf_possible_calls)
            .map_err(|e| e.to_string())?;
    }
    if !buf_unknown_calls.is_empty() {
        let mut unknown_nodes_by_id: Vec<Row> = Vec::new();
        let mut seen: HashSet<String> = HashSet::new();
        for row in buf_unknown_calls.iter() {
            let unknown_id = str_of(row, "unknown_id");
            if !seen.insert(unknown_id.clone()) {
                continue;
            }
            unknown_nodes_by_id.push(json!({
                "id": unknown_id,
                "name": row["props"]["callee_name"].clone(),
                "node_type": "code",
                "project_id": row["project_id"].clone(),
                "project_id_normalized": row["project_id"]
                    .as_str()
                    .unwrap_or("")
                    .to_lowercase(),
            })
            .as_object()
            .cloned()
            .unwrap());
        }
        writer
            .write_node_properties_batch(
                "cplus:unknown_functions",
                "UnknownFunction",
                &unknown_nodes_by_id,
                "id",
                "id",
                None,
            )
            .map_err(|e| e.to_string())?;
        let unknown_edges: Vec<Row> = buf_unknown_calls
            .iter()
            .map(|row| {
                json!({
                    "source_label": "Function",
                    "source_property": "id",
                    "source_id": str_of(row, "caller_id"),
                    "target_label": "UnknownFunction",
                    "target_property": "id",
                    "target_id": str_of(row, "unknown_id"),
                    "rel_type": "UNKNOWN_CALL",
                    "edge_property": "site_id",
                    "edge_id": str_of(row, "site_id"),
                    "props": row["props"].clone(),
                    "project_id": row["project_id"].clone(),
                })
                .as_object()
                .cloned()
                .unwrap()
            })
            .collect();
        writer
            .write_evidence_edges(&unknown_edges)
            .map_err(|e| e.to_string())?;
    }
    buf_files.clear();
    buf_namespaces.clear();
    buf_types.clear();
    buf_function_types.clear();
    buf_functions.clear();
    buf_fields.clear();
    buf_aliases.clear();
    buf_templates.clear();
    buf_resources.clear();
    buf_resource_elements.clear();
    buf_relations.clear();
    buf_possible_calls.clear();
    buf_unknown_calls.clear();
    *files_in_buf = 0;
    Ok(())
}

// ── Helpers ────────────────────────────────────────────────────────────────

/// `include_closure` — recursive memo khớp Python (stack guard).
fn include_closure(
    file_path: &str,
    resolved: &HashMap<String, Vec<String>>,
    cache: &mut HashMap<String, HashSet<String>>,
    stack: &mut HashSet<String>,
) -> HashSet<String> {
    if let Some(cached) = cache.get(file_path) {
        return cached.clone();
    }
    if !stack.insert(file_path.to_string()) {
        return HashSet::new();
    }
    let mut result = HashSet::new();
    for inc in resolved.get(file_path).cloned().unwrap_or_default() {
        result.insert(inc.clone());
        result.extend(include_closure(&inc, resolved, cache, stack));
    }
    stack.remove(file_path);
    cache.insert(file_path.to_string(), result.clone());
    result
}

/// `collect_transitive` — merge using/macros bắc cầu.
#[allow(clippy::too_many_arguments)]
fn collect_transitive(
    start_file: &str,
    resolved: &HashMap<String, Vec<String>>,
    using_by_file: &HashMap<String, Vec<String>>,
    imports_by_file: &HashMap<String, std::collections::BTreeMap<String, String>>,
    macros_by_file: &HashMap<String, std::collections::BTreeMap<String, String>>,
    visited: &mut HashSet<String>,
    namespaces: &mut Vec<String>,
    imports: &mut std::collections::BTreeMap<String, String>,
    macros: &mut std::collections::BTreeMap<String, String>,
) {
    if !visited.insert(start_file.to_string()) {
        return;
    }
    for item in using_by_file.get(start_file).cloned().unwrap_or_default() {
        if !namespaces.contains(&item) {
            namespaces.push(item);
        }
    }
    for (key, value) in imports_by_file.get(start_file).cloned().unwrap_or_default() {
        imports.entry(key).or_insert(value);
    }
    for (key, value) in macros_by_file.get(start_file).cloned().unwrap_or_default() {
        macros.entry(key).or_insert(value);
    }
    for inc in resolved.get(start_file).cloned().unwrap_or_default() {
        collect_transitive(&inc, resolved, using_by_file, imports_by_file, macros_by_file, visited, namespaces, imports, macros);
    }
}

fn rel_row(
    source_label: &str,
    target_label: &str,
    rel_type: &str,
    source_id: &str,
    target_id: &str,
) -> Row {
    let mut row = Map::new();
    row.insert("source_label".into(), json!(source_label));
    row.insert("target_label".into(), json!(target_label));
    row.insert("rel_type".into(), json!(rel_type));
    row.insert("source_id".into(), json!(source_id));
    row.insert("target_id".into(), json!(target_id));
    row.insert("properties".into(), json!({}));
    row
}

fn add_resource_relation(
    relations: &mut Vec<Row>,
    keys: &mut HashSet<(String, String, String)>,
    source_label: &str,
    source_id: &str,
    rel_type: &str,
    target: &Row,
    properties: Row,
) {
    let target_id = str_of(target, "symbol_id");
    if source_id.is_empty() || target_id.is_empty() {
        return;
    }
    let key = (source_id.to_string(), rel_type.to_string(), target_id.clone());
    if !keys.insert(key) {
        return;
    }
    let target_label = if str_of(target, "kind") == "ui_control" { "UIControl" } else { "Resource" };
    let mut row = Map::new();
    row.insert("source_label".into(), json!(source_label));
    row.insert("target_label".into(), json!(target_label));
    row.insert("rel_type".into(), json!(rel_type));
    row.insert("source_id".into(), json!(source_id));
    row.insert("target_id".into(), json!(target_id));
    row.insert("properties".into(), Value::Object(properties));
    relations.push(row);
}

/// `resolve_resource_node` — priority khớp Python max().
fn resolve_resource_node(
    symbol: &str,
    source_file: &str,
    resource_index_by_symbol: &HashMap<String, Vec<Row>>,
) -> Option<Row> {
    let candidates = resource_index_by_symbol.get(symbol)?;
    if candidates.is_empty() {
        return None;
    }
    if candidates.len() == 1 {
        return Some(candidates[0].clone());
    }
    let source_dir = dirname(source_file);
    let kind_priority = |kind: &str| -> i64 {
        match kind {
            "ui_control" => 100,
            "dialogex" | "dialog" => 90,
            "string" => 80,
            "icon" | "bitmap" | "menu" | "menuex" => 70,
            "afx_dialog_layout" => 10,
            "designinfo" => 5,
            "textinclude" => 1,
            _ => 50,
        }
    };
    let score = |item: &Row| -> (i64, bool, String, String, String) {
        (
            kind_priority(str_of(item, "kind").as_str()),
            dirname(&str_of(item, "file_path")) == source_dir,
            str_of(item, "language"),
            str_of(item, "file_path"),
            str_of(item, "symbol_id"),
        )
    };
    let mut best: Option<(&Row, (i64, bool, String, String, String))> = None;
    for item in candidates {
        let key = score(item);
        let is_better = match &best {
            None => true,
            Some((_, best_key)) => key > *best_key,
        };
        if is_better {
            best = Some((item, key));
        }
    }
    best.map(|(row, _)| row.clone())
}

/// `resource_numeric_id`.
fn numeric_id_for(item: &Row, macros_by_file: &HashMap<String, std::collections::BTreeMap<String, String>>) -> Value {
    let symbol = str_of(item, "resource_symbol");
    let file_path = str_of(item, "file_path");
    let expansion = macros_by_file
        .get(&file_path)
        .and_then(|macros| macros.get(&symbol))
        .map(|s| s.trim().to_string())
        .unwrap_or_default();
    let re = regex::Regex::new(r"^(0[xX][0-9A-Fa-f]+|\d+)(?:[uUlL]*)\b").expect("numeric regex");
    let Some(caps) = re.captures(&expansion) else {
        return Value::Null;
    };
    let value = if let Some(hex) = caps[1].strip_prefix("0x").or_else(|| caps[1].strip_prefix("0X")) {
        i64::from_str_radix(hex, 16).ok()
    } else {
        caps[1].parse::<i64>().ok()
    };
    value.map(|v| json!(v)).unwrap_or(Value::Null)
}

/// `resolve_resource_handler`.
fn resolve_resource_handler(
    handler: &str,
    file_path: &str,
    by_qualified: &HashMap<String, FunctionEntry>,
    by_file_name: &HashMap<(String, String), Vec<FunctionEntry>>,
    by_name: &HashMap<String, Vec<FunctionEntry>>,
) -> Option<String> {
    let normalized = handler.trim_start_matches('&');
    if let Some(entry) = by_qualified.get(normalized) {
        return Some(entry.symbol_id.clone());
    }
    let short_name = normalized.rsplit("::").next().unwrap_or(normalized);
    let same_file = by_file_name
        .get(&(file_path.to_string(), short_name.to_string()))
        .cloned()
        .unwrap_or_default();
    if same_file.len() == 1 {
        return Some(same_file[0].symbol_id.clone());
    }
    let candidates = by_name.get(short_name).cloned().unwrap_or_default();
    if normalized.contains("::") {
        let scope = normalized.rsplit_once("::").map(|(s, _)| s).unwrap_or("");
        let scoped: Vec<&FunctionEntry> = candidates
            .iter()
            .filter(|item| item.scope_name.as_deref() == Some(scope))
            .collect();
        if scoped.len() == 1 {
            return Some(scoped[0].symbol_id.clone());
        }
    }
    if candidates.len() == 1 {
        Some(candidates[0].symbol_id.clone())
    } else {
        None
    }
}

/// `event_id_of`.
fn event_id_of(event: &Value) -> Result<String, String> {
    if let Some(id) = event
        .get("id")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|s| !s.is_empty())
    {
        return Ok(id.to_string());
    }
    let name = event.get("name").and_then(Value::as_str).map(str::trim).unwrap_or("");
    if name.is_empty() {
        return Err("Event mapping entry missing 'id' or 'name'".into());
    }
    let namespace = event.get("namespace").and_then(Value::as_str).unwrap_or("").trim();
    let version = event.get("version").and_then(Value::as_str).unwrap_or("").trim();
    if !namespace.is_empty() && !version.is_empty() {
        return Ok(format!("event::{namespace}::{name}::{version}"));
    }
    if !namespace.is_empty() {
        return Ok(format!("event::{namespace}::{name}"));
    }
    if !version.is_empty() {
        return Ok(format!("event::{name}::{version}"));
    }
    Ok(format!("event::{name}"))
}

/// `resolve_callee_id` — heuristic scoring y hệt build_call_graph Python.
#[allow(clippy::too_many_arguments)]
fn resolve_callee_id(
    callee_name: &str,
    caller_scope: Option<&str>,
    call_arity: i64,
    caller_file: Option<&str>,
    macros_by_file: &HashMap<String, std::collections::BTreeMap<String, String>>,
    alias_targets_by_name: &HashMap<String, String>,
    closure_cache: &HashMap<String, HashSet<String>>,
    using_namespaces_by_file: &HashMap<String, Vec<String>>,
    using_imports_by_file: &HashMap<String, std::collections::BTreeMap<String, String>>,
    by_qualified_arity: &HashMap<(String, i64), FunctionEntry>,
    by_qualified: &HashMap<String, FunctionEntry>,
    by_file_name_arity: &HashMap<(String, String, i64), Vec<FunctionEntry>>,
    by_file_name: &HashMap<(String, String), Vec<FunctionEntry>>,
    by_scope_name_arity: &HashMap<(Option<String>, String, i64), Vec<FunctionEntry>>,
    by_scope_name: &HashMap<(Option<String>, String), Vec<FunctionEntry>>,
    by_name_arity: &HashMap<(String, i64), Vec<FunctionEntry>>,
    by_name: &HashMap<String, Vec<FunctionEntry>>,
) -> Option<String> {
    fn normalize_call_name(text: &str) -> String {
        crate::cparse::normalize_call_name_pub(text)
    }
    let expand_macros = |name: &str, file_path: Option<&str>| -> Vec<String> {
        let Some(file_path) = file_path else {
            return vec![name.to_string()];
        };
        let Some(expansion) = macros_by_file.get(file_path).and_then(|m| m.get(name)) else {
            return vec![name.to_string()];
        };
        let expanded = normalize_call_name(expansion);
        if !expanded.is_empty() && expanded != name {
            vec![name.to_string(), expanded]
        } else {
            vec![name.to_string()]
        }
    };
    let expand_aliases = |name: &str| -> Vec<String> {
        if !name.contains("::") {
            return vec![name.to_string()];
        }
        let Some((prefix, rest)) = name.split_once("::") else {
            return vec![name.to_string()];
        };
        if let Some(target) = alias_targets_by_name.get(prefix) {
            return vec![name.to_string(), format!("{target}::{rest}")];
        }
        vec![name.to_string()]
    };
    let scope_chain = |scope: Option<&str>| -> Vec<Option<String>> {
        let Some(scope) = scope else {
            return vec![None];
        };
        let parts: Vec<&str> = scope.split("::").collect();
        let mut chain: Vec<Option<String>> = (1..=parts.len())
            .rev()
            .map(|idx| Some(parts[..idx].join("::")))
            .collect();
        chain.push(None);
        chain
    };

    let mut best_by_symbol: HashMap<String, (i64, String)> = HashMap::new();
    let include_scope: HashSet<String> = caller_file
        .and_then(|f| closure_cache.get(f))
        .cloned()
        .unwrap_or_default();
    let scope_chain_values = scope_chain(caller_scope);
    let scope_depth: HashMap<Option<String>, usize> = scope_chain_values
        .iter()
        .enumerate()
        .map(|(idx, scope)| (scope.clone(), idx))
        .collect();

    fn consider(
        best_by_symbol: &mut HashMap<String, (i64, String)>,
        entry: &FunctionEntry,
        base_score: i64,
        caller_file: Option<&str>,
        caller_scope: Option<&str>,
        include_scope: &HashSet<String>,
        scope_depth: &HashMap<Option<String>, usize>,
    ) {
        let symbol_id = entry.symbol_id.clone();
        let mut score = base_score;
        if let Some(caller_file) = caller_file
            && let Some(entry_file) = &entry.file_path {
                if entry_file == caller_file {
                    score += 10;
                } else if include_scope.contains(entry_file) {
                    score += 5;
                }
            }
        if let (Some(caller_scope), Some(entry_scope)) = (caller_scope, &entry.scope_name)
            && entry_scope == caller_scope {
                score += 8;
            }
        if let Some(depth) = scope_depth.get(&entry.scope_name) {
            score += (6 - *depth as i64).max(0);
        }
        let tie = if !entry.qualified_name.is_empty() {
            entry.qualified_name.clone()
        } else if !entry.name.is_empty() {
            entry.name.clone()
        } else {
            symbol_id.clone()
        };
        match best_by_symbol.get(&symbol_id) {
            None => {
                best_by_symbol.insert(symbol_id, (score, tie));
            }
            Some(current) if (score, tie.clone()) > *current => {
                best_by_symbol.insert(symbol_id, (score, tie));
            }
            _ => {}
        }
    }

    let mut expanded: Vec<String> = Vec::new();
    for variant in expand_aliases(callee_name) {
        for macro_variant in expand_macros(&variant, caller_file) {
            if !expanded.contains(&macro_variant) {
                expanded.push(macro_variant);
            }
        }
    }

    for variant in &expanded {
        if variant.contains("::") {
            if let Some(entry) = by_qualified_arity.get(&(variant.clone(), call_arity)) {
                consider(&mut best_by_symbol, entry, 130, caller_file, caller_scope, &include_scope, &scope_depth);
            }
            if let Some(entry) = by_qualified.get(variant) {
                consider(&mut best_by_symbol, entry, 120, caller_file, caller_scope, &include_scope, &scope_depth);
            }
        }
    }

    let short_name = callee_name.rsplit("::").next().unwrap_or(callee_name);
    if let Some(caller_file_str) = caller_file {
        let caller_file: &str = caller_file_str;
        for entry in by_file_name_arity
            .get(&(caller_file.to_string(), short_name.to_string(), call_arity))
            .cloned()
            .unwrap_or_default()
        {
            consider(&mut best_by_symbol, &entry, 115, Some(caller_file), caller_scope, &include_scope, &scope_depth);
        }
        for entry in by_file_name
            .get(&(caller_file.to_string(), short_name.to_string()))
            .cloned()
            .unwrap_or_default()
        {
            consider(&mut best_by_symbol, &entry, 105, Some(caller_file), caller_scope, &include_scope, &scope_depth);
        }
        for ns in using_namespaces_by_file.get(caller_file).cloned().unwrap_or_default() {
            let qualified = format!("{ns}::{short_name}");
            if let Some(entry) = by_qualified_arity.get(&(qualified.clone(), call_arity)) {
                consider(&mut best_by_symbol, entry, 100, Some(caller_file), caller_scope, &include_scope, &scope_depth);
            }
            if let Some(entry) = by_qualified.get(&qualified) {
                consider(&mut best_by_symbol, entry, 90, Some(caller_file), caller_scope, &include_scope, &scope_depth);
            }
        }
        if let Some(imported) = using_imports_by_file
            .get(caller_file)
            .and_then(|imports| imports.get(short_name))
            .cloned()
        {
            if let Some(entry) = by_qualified_arity.get(&(imported.clone(), call_arity)) {
                consider(&mut best_by_symbol, entry, 102, Some(caller_file), caller_scope, &include_scope, &scope_depth);
            }
            if let Some(entry) = by_qualified.get(&imported) {
                consider(&mut best_by_symbol, entry, 92, Some(caller_file), caller_scope, &include_scope, &scope_depth);
            }
        }
    }

    for (depth, scope) in scope_chain_values.iter().enumerate() {
        for entry in by_scope_name_arity
            .get(&(scope.clone(), short_name.to_string(), call_arity))
            .cloned()
            .unwrap_or_default()
        {
            consider(&mut best_by_symbol, &entry, 95 - (depth.min(20) as i64), caller_file, caller_scope, &include_scope, &scope_depth);
        }
        for entry in by_scope_name
            .get(&(scope.clone(), short_name.to_string()))
            .cloned()
            .unwrap_or_default()
        {
            consider(&mut best_by_symbol, &entry, 85 - (depth.min(20) as i64), caller_file, caller_scope, &include_scope, &scope_depth);
        }
    }

    for entry in by_name_arity
        .get(&(short_name.to_string(), call_arity))
        .cloned()
        .unwrap_or_default()
    {
        consider(&mut best_by_symbol, &entry, 70, caller_file, caller_scope, &include_scope, &scope_depth);
    }
    for entry in by_name.get(short_name).cloned().unwrap_or_default() {
        consider(&mut best_by_symbol, &entry, 55, caller_file, caller_scope, &include_scope, &scope_depth);
    }

    let mut best: Option<(String, (i64, String))> = None;
    for (symbol_id, score) in &best_by_symbol {
        let is_better = match &best {
            None => true,
            Some((_, best_score)) => score > best_score,
        };
        if is_better {
            best = Some((symbol_id.clone(), score.clone()));
        }
    }
    best.map(|(symbol_id, _)| symbol_id)
}

// ── Resource payload (rc files) ────────────────────────────────────────────

fn parse_resource_payload(file_path: &Path, root: &Path) -> Result<FilePayload, String> {
    let rc = rcparse::parse_rc_file(file_path, root);
    let mut row = FilePayload::default();
    row.functions = vec_rows(&rc, "functions");
    row.calls = vec_rows(&rc, "calls");
    row.types = vec_rows(&rc, "types");
    row.namespaces = vec_rows(&rc, "namespaces");
    row.relations = vec_rows(&rc, "relations");
    row.function_types = vec_rows(&rc, "function_types");
    row.fields = vec_rows(&rc, "fields");
    row.aliases = vec_rows(&rc, "aliases");
    row.templates = vec_rows(&rc, "templates");
    row.resources = vec_rows(&rc, "resources");
    row.resource_elements = vec_rows(&rc, "resource_elements");
    row.file_def = rc.get("file_def").and_then(Value::as_object).cloned();
    row.includes = rc
        .get("includes")
        .and_then(Value::as_array)
        .map(|items| items.iter().filter_map(Value::as_str).map(str::to_string).collect())
        .unwrap_or_default();
    row.macros = rc
        .get("macros")
        .and_then(Value::as_object)
        .map(|map| {
            map.iter()
                .filter_map(|(k, v)| v.as_str().map(|v| (k.clone(), v.to_string())))
                .collect()
        })
        .unwrap_or_default();
    row.parse_meta = rc
        .get("parse_meta")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let decoded = cscan::read_legacy_text(file_path);
    let resource_source = decoded.text.clone().into_bytes();
    let resource_yield = quality::SemanticYield {
        declaration_count: (row.resources.len() + row.resource_elements.len()) as i64,
        include_count: row.includes.len() as i64,
        ..Default::default()
    };
    let context = quality::ParseContext {
        backend: "windows_resource".into(),
        parser_language: "windows_rc".into(),
        parser_version: "1".into(),
        grammar_version: "1".into(),
        source_encoding: decoded.encoding.clone(),
        lossy_decode: decoded.lossy,
        ..Default::default()
    };
    let record = quality::build_rc_quality_record(
        &cscan::rel_posix(root, file_path),
        &resource_source,
        &resource_yield,
        &context,
    );
    row.parse_meta.insert("quality".into(), Value::Object(record.to_dict()));
    row.parse_meta.insert("quality_tier".into(), json!(record.tier));
    row.parse_meta.insert("parser_backend".into(), json!(record.context.backend));
    row.parse_meta.insert("parser_language".into(), json!("windows_rc"));
    row.parse_meta.insert("context_fingerprint".into(), json!(record.context_fingerprint));
    row.parse_meta.insert(
        "recovery_policy_version".into(),
        json!(record.context.recovery_policy_version),
    );
    row.parse_meta.insert("has_error".into(), json!(false));
    row.parse_meta.insert("error_nodes".into(), json!(0));
    row.parse_meta.insert("missing_nodes".into(), json!(0));
    row.parse_meta.insert("source_encoding".into(), json!(decoded.encoding));
    row.parse_meta.insert("lossy_decode".into(), json!(decoded.lossy));
    Ok(row)
}

fn vec_rows(rc: &Row, key: &str) -> Vec<Row> {
    rc.get(key)
        .and_then(Value::as_array)
        .map(|items| items.iter().filter_map(Value::as_object).cloned().collect())
        .unwrap_or_default()
}

// ── Compile db index (message + is_cpp decision) ───────────────────────────

pub struct CompileDbIndex {
    pub entries: i64,
    pub cpp_files: HashSet<String>,
    pub c_files: HashSet<String>,
}

/// `_load_compile_commands_index` — subset fallback mode.
fn load_compile_commands_index(path: &Path, root: &Path) -> Option<CompileDbIndex> {
    let data = std::fs::read(path).ok()?;
    let payload: Value = serde_json::from_slice(&data).ok()?;
    let entries_json = payload.as_array()?;
    let root_abs = abs_root(&root.to_string_lossy()).to_string_lossy().to_string();
    let mut index = CompileDbIndex {
        entries: 0,
        cpp_files: HashSet::new(),
        c_files: HashSet::new(),
    };
    for item in entries_json {
        let Some(item_obj) = item.as_object() else {
            continue;
        };
        let Some(file_path) = item_obj.get("file").and_then(Value::as_str).map(str::trim) else {
            continue;
        };
        if file_path.is_empty() {
            continue;
        }
        let entry_dir = item_obj
            .get("directory")
            .and_then(Value::as_str)
            .filter(|d| !d.trim().is_empty())
            .map(str::to_string)
            .unwrap_or_else(|| dirname(&path.to_string_lossy()));
        let abs_file = if Path::new(file_path).is_absolute() {
            file_path.to_string()
        } else {
            format!("{entry_dir}/{file_path}")
        };
        let abs_file = crate::position::normpath(&abs_file);
        if commonpath2(&root_abs, &abs_file) != root_abs {
            continue;
        }
        let rel_file = relpath(&abs_file, &root_abs).replace('\\', "/");
        let tokens = parse_compile_command_tokens(item_obj);
        if command_implies_cpp(&tokens, &abs_file) {
            index.cpp_files.insert(rel_file);
        } else {
            index.c_files.insert(rel_file);
        }
        index.entries += 1;
    }
    Some(index)
}

fn parse_compile_command_tokens(entry: &serde_json::Map<String, Value>) -> Vec<String> {
    if let Some(args) = entry.get("arguments").and_then(Value::as_array) {
        return args
            .iter()
            .filter_map(Value::as_str)
            .map(str::to_string)
            .collect();
    }
    if let Some(command) = entry.get("command").and_then(Value::as_str)
        && !command.trim().is_empty() {
            return command.split_whitespace().map(str::to_string).collect();
        }
    Vec::new()
}

fn command_implies_cpp(tokens: &[String], file_path: &str) -> bool {
    if !tokens.is_empty() {
        let compiler = basename(&tokens[0]).to_lowercase();
        if compiler.contains("++") {
            return true;
        }
        for (idx, token) in tokens.iter().enumerate() {
            if token == "-x" && idx + 1 < tokens.len() {
                let lang = tokens[idx + 1].to_lowercase();
                if lang.contains("c++") {
                    return true;
                }
                if lang == "c" {
                    return false;
                }
            }
        }
    }
    let ext = splitext(&file_path.to_lowercase()).1;
    [".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"].contains(&ext.as_str())
}
