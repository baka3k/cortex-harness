//! `dev sync doc / doc all / doc stop` — document ingestion orchestration.
//!
//! Phase-04 fallback (plan clause "fallback: giữ spawn + ghi forced"): the
//! spawn of `doc-tiny/graphrag_ingest_langextract.py` STAYS — the Rust
//! `cortex-doc` port has no embedded-FalkorDB backend (server URI only) and
//! excludes the vector stage, so it cannot replace the Python ingestor on a
//! stock local store yet. Everything else here is native.

use super::sync::{
    absolute_or_join, print_summary, run_with_retry, select_folders_interactive,
    env_to_neo4j_args, stop_sync_command, sync_lifecycle, warn_scan_roots_matching_ignores,
    with_extra_ignores, RetryOptions,
};
use crate::config::{ignore_folders, load_active_config};
use crate::parser::Matches;
use crate::util::{
    echo, echo_err, git_head, git_status_since, iso_utc_now, is_excluded_dir_name, is_sensitive,
    load_state, match_any, prompt, save_state,
};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::path::{Path, PathBuf};

pub const DOC_EXT_FLAGS: &[(&str, &str)] = &[
    (".pdf", "--pdf"),
    (".md", "--md"),
    (".docx", "--docx"),
    (".txt", "--text-file"),
    (".pptx", "--pptx"),
    (".xlsx", "--xlsx"),
];

pub fn sync_doc(m: &Matches, force_full: bool) {
    sync_doc_impl(m, force_full)
}

pub fn sync_doc_impl(m: &Matches, force_full: bool) {
    let project_dir = m.value_or("--project-dir", ".");
    let preview = m.flag("--preview");
    let entity_provider = m.value_or("--entity-provider", "gliner");
    let dry_run = m.flag("--dry-run");

    let project_path = super::init::resolve_path(Path::new(&project_dir));
    let (cfg, _) = load_active_config(&project_path);
    let doc_cfg = cfg.get("doc").cloned().unwrap_or(json!({}));
    let env = crate::env::doc_env(&project_path);
    let env = with_extra_ignores(&cfg, &env);
    let extra_ignores = ignore_folders(&cfg);
    let project = cfg.get("project").cloned().unwrap_or(json!({}));
    let folders = crate::config::source_folders(doc_cfg.get("source").unwrap_or(&json!({})));

    if folders.is_empty() {
        echo("[warn] No doc folders configured. Run 'dev init' or 'dev sync doc add'.");
        return;
    }

    let doc_ingestor = crate::util::repo_root().join("doc-tiny/graphrag_ingest_langextract.py");
    if !doc_ingestor.exists() {
        echo_err(&format!("[error] Ingestor not found: {}", doc_ingestor.display()));
        std::process::exit(1);
    }

    let selected: Vec<String> = if force_full {
        folders.clone()
    } else {
        select_folders_interactive(&folders)
    };
    if !force_full {
        warn_scan_roots_matching_ignores(&selected, &extra_ignores);
    } else {
        echo(&format!("\n[sync-doc all]  folders={}", folders.len()));
        warn_scan_roots_matching_ignores(&folders, &extra_ignores);
    }
    if selected.is_empty() {
        echo("[info] No folders selected.");
        return;
    }

    let python = crate::util::harness_python(&crate::util::repo_root().join("doc-tiny"));
    let mut summaries: Vec<Value> = Vec::new();
    let total_start = std::time::Instant::now();

    let guard = sync_lifecycle("doc", &env, &project_path, !dry_run, true);
    for folder in &selected {
        let result = sync_doc_folder(
            &project_path,
            folder,
            &env,
            &python,
            &project,
            if force_full { "full" } else { "auto" },
            &entity_provider,
            dry_run,
            preview && !force_full,
            &extra_ignores,
        );
        summaries.push(result);
    }
    drop(guard);
    print_summary(&summaries, total_start.elapsed().as_secs_f64());
}

pub fn sync_doc_stop(m: &Matches) {
    let project_path = super::init::resolve_path(Path::new(&m.value_or("--project-dir", ".")));
    let env = crate::env::doc_env(&project_path);
    stop_sync_command("doc", &project_path, &env);
}

/// dev.py `_sync_doc_folder`.
#[allow(clippy::too_many_arguments)]
pub(super) fn sync_doc_folder(
    project_path: &Path,
    folder: &str,
    env: &Value,
    python: &str,
    project: &Value,
    force_mode: &str,
    entity_provider: &str,
    dry_run: bool,
    preview: bool,
    extra_ignores: &[String],
) -> Value {
    let folder_path = absolute_or_join(folder, project_path);
    if !folder_path.exists() {
        echo(&format!("\n[warn] Folder not found: {} — skipping", folder_path.display()));
        return json!({"folder": folder, "status": "skipped", "reason": "not found"});
    }

    let state_key = format!("doc:{}", folder);
    let state = load_state(project_path, &state_key);
    let has_state = state.as_object().map(|o| !o.is_empty()).unwrap_or(false);
    let mode = if force_mode != "auto" {
        force_mode.to_string()
    } else if has_state {
        "incremental".to_string()
    } else {
        "full".to_string()
    };

    let project_id = project
        .get("code")
        .and_then(|v| v.as_str())
        .or_else(|| project.get("name").and_then(|v| v.as_str()))
        .unwrap_or("project")
        .to_string();
    let collection = env
        .get("QDRANT_COLLECTION_DOC")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .or_else(|| env.get("QDRANT_COLLECTION").and_then(|v| v.as_str()).filter(|s| !s.is_empty()))
        .map(String::from)
        .unwrap_or_else(|| format!("{}_doc", project_id));

    let doc_ingestor = crate::util::repo_root().join("doc-tiny/graphrag_ingest_langextract.py");
    let mut base_cmd = vec![
        python.to_string(),
        doc_ingestor.to_string_lossy().to_string(),
    ];
    base_cmd.extend(env_to_neo4j_args(env));
    let qdrant_doc_path = env
        .get("QDRANT_DOC_PATH")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    base_cmd.push("--qdrant-path".to_string());
    base_cmd.push(qdrant_doc_path);
    base_cmd.push("--project-id".to_string());
    base_cmd.push(project_id.clone());
    base_cmd.push("--collection".to_string());
    base_cmd.push(collection);
    base_cmd.push("--entity-provider".to_string());
    base_cmd.push(entity_provider.to_string());
    base_cmd.push("--embedding-model".to_string());
    base_cmd.push(
        env.get("EMBEDDING_MODEL")
            .and_then(|v| v.as_str())
            .unwrap_or("BAAI/bge-m3")
            .to_string(),
    );
    base_cmd.push("--embedding-device".to_string());
    base_cmd.push(embed_device_cli_arg(env));
    base_cmd.push("--max-paragraph-chars".to_string());
    base_cmd.push(
        env.get("MAX_PARAGRAPH_CHARS")
            .and_then(|v| v.as_str())
            .unwrap_or("500")
            .to_string(),
    );
    base_cmd.push("--gliner-model-name".to_string());
    base_cmd.push(
        env.get("GLINER_MODEL_NAME")
            .and_then(|v| v.as_str())
            .unwrap_or("urchade/gliner_large-v2.1")
            .to_string(),
    );
    base_cmd.push("--gliner-labels".to_string());
    base_cmd.push(
        env.get("GLINER_LABELS")
            .and_then(|v| v.as_str())
            .unwrap_or("PERSON,ORG,PRODUCT,GPE,DATE,TECH,CRYPTO,STANDARD")
            .to_string(),
    );
    base_cmd.push("--gliner-threshold".to_string());
    base_cmd.push(
        env.get("GLINER_THRESHOLD")
            .and_then(|v| v.as_str())
            .unwrap_or("0.35")
            .to_string(),
    );
    base_cmd.push("--gliner-batch-size".to_string());
    base_cmd.push(
        env.get("GLINER_BATCH_SIZE")
            .and_then(|v| v.as_str())
            .unwrap_or("1")
            .to_string(),
    );
    base_cmd.push("--neo4j-batch-size".to_string());
    base_cmd.push(
        env.get("NEO4J_BATCH_SIZE")
            .and_then(|v| v.as_str())
            .unwrap_or("1")
            .to_string(),
    );
    base_cmd.push("--no-batch".to_string());

    let divider = "─".repeat(52);
    echo(&format!("\n{}", divider));
    echo(&format!(" folder : {}", folder));
    echo(&format!(" mode   : {}", mode));
    echo(&format!(" provider: {}", entity_provider));

    let start_ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64();

    if mode == "full" {
        let all_files = find_doc_files(&folder_path, extra_ignores);
        echo(&format!(" files  : {} document(s)", all_files.len()));
        if all_files.is_empty() {
            echo("  [warn] No supported document files found — skipping");
            return json!({"folder": folder, "status": "skipped", "reason": "no doc files"});
        }

        let mut cmd = base_cmd.clone();
        cmd.push("--folder".to_string());
        cmd.push(folder_path.to_string_lossy().to_string());
        let opts = RetryOptions {
            max_retries: 3,
            dry_run,
            env: Some(env),
            non_retryable_exit_codes: &[],
            result_path: None,
        };
        let rc = run_with_retry(&cmd, &opts);
        let elapsed = start_elapsed(start_ts);

        if rc == 0 && !dry_run {
            let file_hashes = build_file_hashes(&folder_path, extra_ignores);
            save_state(
                project_path,
                &state_key,
                &json!({
                    "folder": folder,
                    "last_sync": iso_utc_now(),
                    "last_sync_ts": start_ts,
                    "mode": "full",
                    "git_commit": git_head(&folder_path),
                    "file_hashes": file_hashes,
                    "file_count": all_files.len(),
                }),
            );
        }

        return json!({
            "folder": folder,
            "status": if rc == 0 { "ok" } else { "error" },
            "mode": mode,
            "elapsed": elapsed,
        });
    }

    // Incremental.
    let (changed_files, deleted_rel) = detect_changed_docs(&folder_path, &state, extra_ignores);
    if changed_files.is_empty() && deleted_rel.is_empty() {
        echo("  [ok] No changes detected — skipping");
        return json!({"folder": folder, "status": "skipped", "reason": "no changes"});
    }
    echo(&format!(
        "  changed: {}  deleted: {}",
        changed_files.len(),
        deleted_rel.len()
    ));

    if preview && !changed_files.is_empty() {
        echo(&format!("\n  Preview — {} file(s) queued:", changed_files.len()));
        for f in sorted_paths(&changed_files).into_iter().take(20) {
            let rel = f
                .strip_prefix(&folder_path)
                .map(|p| p.to_string_lossy().to_string())
                .unwrap_or_else(|_| f.to_string_lossy().to_string());
            echo(&format!("    + {}", rel));
        }
        if changed_files.len() > 20 {
            echo(&format!("    … and {} more", changed_files.len() - 20));
        }
        if !deleted_rel.is_empty() {
            echo(&format!("  Deleted ({}):", deleted_rel.len()));
            let mut sorted_deleted = deleted_rel.clone();
            sorted_deleted.sort();
            for r in sorted_deleted.iter().take(10) {
                echo(&format!("    - {}", r));
            }
        }
        if !prompt("\n  Proceed?", "y").starts_with('y') {
            return json!({"folder": folder, "status": "cancelled"});
        }
    }

    let mut errors = 0u32;
    for file_path in &changed_files {
        let ext = file_path
            .extension()
            .map(|e| format!(".{}", e.to_string_lossy().to_lowercase()))
            .unwrap_or_default();
        let Some(flag) = DOC_EXT_FLAGS
            .iter()
            .find(|(e, _)| *e == ext)
            .map(|(_, f)| f.to_string())
        else {
            continue;
        };
        let name = file_path
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        echo(&format!("  [+] {}", name));
        let mut cmd = base_cmd.clone();
        cmd.push(flag);
        cmd.push(file_path.to_string_lossy().to_string());
        let opts = RetryOptions {
            max_retries: 3,
            dry_run,
            env: Some(env),
            non_retryable_exit_codes: &[],
            result_path: None,
        };
        let rc = run_with_retry(&cmd, &opts);
        if rc != 0 {
            echo(&format!("    [error] exited {}", rc));
            errors += 1;
        }
    }

    let elapsed = start_elapsed(start_ts);
    let success = errors == 0;

    if success && !dry_run {
        let mut new_hashes: serde_json::Map<String, Value> = state
            .get("file_hashes")
            .and_then(|h| h.as_object())
            .cloned()
            .unwrap_or_default();
        for f in &changed_files {
            if let Ok(rel) = f.strip_prefix(&folder_path) {
                new_hashes.insert(
                    rel.to_string_lossy().to_string(),
                    json!(sha256_file(f)),
                );
            }
        }
        for rel in &deleted_rel {
            new_hashes.remove(rel);
        }
        save_state(
            project_path,
            &state_key,
            &json!({
                "folder": folder,
                "last_sync": iso_utc_now(),
                "last_sync_ts": start_ts,
                "mode": "incremental",
                "git_commit": git_head(&folder_path),
                "file_hashes": Value::Object(new_hashes),
                "file_count": changed_files.len(),
            }),
        );
    }

    json!({
        "folder": folder,
        "status": if success { "ok" } else { "error" },
        "mode": mode,
        "elapsed": elapsed,
    })
}
pub(super) fn start_elapsed(start_ts: f64) -> f64 {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64();
    now - start_ts
}

fn sorted_paths(paths: &[PathBuf]) -> Vec<PathBuf> {
    let mut v = paths.to_vec();
    v.sort();
    v
}

fn embed_device_cli_arg(env: &Value) -> String {
    crate::env::normalize_embed_device(env.get("device").and_then(|v| v.as_str()).unwrap_or("cpu"))
}

// ---------------------------------------------------------------------------
// Doc file discovery + change detection
// ---------------------------------------------------------------------------

fn doc_extension(ext: &str) -> bool {
    DOC_EXT_FLAGS.iter().any(|(e, _)| e.eq_ignore_ascii_case(ext))
}

fn find_doc_files(folder: &Path, extra_ignores: &[String]) -> Vec<PathBuf> {
    let mut out = Vec::new();
    collect_doc_files(folder, folder, extra_ignores, &mut out);
    out.sort();
    out
}

fn collect_doc_files(base: &Path, root: &Path, extra_ignores: &[String], out: &mut Vec<PathBuf>) {
    let mut entries: Vec<PathBuf> = std::fs::read_dir(base)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .collect();
    entries.sort();
    for entry in entries {
        if entry.is_dir() {
            let name = entry.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
            if is_excluded_dir_name(&name) || match_any(&name, extra_ignores) {
                continue;
            }
            collect_doc_files(&entry, root, extra_ignores, out);
            continue;
        }
        if !entry.is_file() {
            continue;
        }
        let ext = entry
            .extension()
            .map(|e| format!(".{}", e.to_string_lossy().to_lowercase()))
            .unwrap_or_default();
        if doc_extension(&ext) && !is_sensitive(&entry) {
            let rel_parts: Vec<String> = entry
                .strip_prefix(root)
                .map(|p| p.iter().map(|x| x.to_string_lossy().to_string()).collect())
                .unwrap_or_default();
            if rel_parts.iter().any(|p| is_excluded_dir_name(p) || match_any(p, extra_ignores)) {
                continue;
            }
            out.push(entry);
        }
    }
}

pub(super) fn sha256_file(path: &Path) -> String {
    let Ok(data) = std::fs::read(path) else { return String::new() };
    let mut hasher = Sha256::new();
    hasher.update(&data);
    hex(&hasher.finalize())
}

pub(crate) fn sha256_hex_hex(data: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data);
    hex(&hasher.finalize())[..16].to_string()
}

pub(crate) fn hex(data: &[u8]) -> String {
    data.iter().map(|b| format!("{:02x}", b)).collect()
}

fn detect_changed_docs(
    folder_path: &Path,
    state: &Value,
    extra_ignores: &[String],
) -> (Vec<PathBuf>, Vec<String>) {
    let since_commit = state.get("git_commit").and_then(|v| v.as_str()).unwrap_or("");
    let since_ts = state.get("last_sync_ts").and_then(|v| v.as_f64()).unwrap_or(0.0);
    let stored_hashes = state.get("file_hashes").cloned().unwrap_or(json!({}));

    if !since_commit.is_empty() {
        let current = git_head(folder_path);
        if !current.is_empty() && current == since_commit {
            return (Vec::new(), Vec::new());
        }
        let (changed_rel, deleted_rel) = git_status_since(folder_path, since_commit);
        let changed: Vec<PathBuf> = changed_rel
            .iter()
            .map(|r| folder_path.join(r))
            .filter(|p| {
                let ext = p
                    .extension()
                    .map(|e| format!(".{}", e.to_string_lossy().to_lowercase()))
                    .unwrap_or_default();
                doc_extension(&ext) && !is_sensitive(p)
            })
            .collect();
        let deleted: Vec<String> = deleted_rel
            .into_iter()
            .filter(|r| {
                let ext = Path::new(r)
                    .extension()
                    .map(|e| format!(".{}", e.to_string_lossy().to_lowercase()))
                    .unwrap_or_default();
                doc_extension(&ext)
            })
            .collect();
        return (changed, deleted);
    }

    let stored_map = stored_hashes.as_object().cloned().unwrap_or_default();
    if !stored_map.is_empty() {
        let current_files = find_doc_files(folder_path, extra_ignores);
        let mut changed = Vec::new();
        let mut deleted = Vec::new();
        let mut current_rels: HashSet<String> = HashSet::new();
        for f in &current_files {
            let rel = f
                .strip_prefix(folder_path)
                .map(|p| p.to_string_lossy().to_string())
                .unwrap_or_default();
            current_rels.insert(rel.clone());
            let stored = stored_map.get(&rel).and_then(|v| v.as_str()).unwrap_or("");
            if sha256_file(f) != stored {
                changed.push(f.clone());
            }
        }
        for key in stored_map.keys() {
            if !current_rels.contains(key) {
                deleted.push(key.clone());
            }
        }
        return (changed, deleted);
    }

    if since_ts > 0.0 {
        let changed: Vec<PathBuf> = find_doc_files(folder_path, extra_ignores)
            .into_iter()
            .filter(|f| {
                std::fs::metadata(f)
                    .and_then(|m| m.modified())
                    .ok()
                    .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                    .map(|d| d.as_secs_f64() > since_ts)
                    .unwrap_or(false)
            })
            .collect();
        return (changed, Vec::new());
    }

    (Vec::new(), Vec::new())
}

fn build_file_hashes(folder: &Path, extra_ignores: &[String]) -> Value {
    let mut out = serde_json::Map::new();
    for f in find_doc_files(folder, extra_ignores) {
        if let Ok(rel) = f.strip_prefix(folder) {
            out.insert(rel.to_string_lossy().to_string(), json!(sha256_file(&f)));
        }
    }
    Value::Object(out)
}
