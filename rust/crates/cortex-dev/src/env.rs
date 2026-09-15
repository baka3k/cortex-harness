//! Native per-process environment resolution — port of dev.py
//! `_code_env_for_process` / `_doc_env_for_process` / `_mcp_env_from_config`
//! plus the `status_env` payload. Replaces the `pyexec` bridge ops
//! `status_env`, `code_env`, `doc_env`, `mcp_env` (phase-01 of the
//! dev/make cutover plan): `cortex_storage` already reproduces
//! `resolve_storage` + `storage_overlay` + topology fingerprints
//! byte-for-byte, so the env payload no longer needs a Python round-trip.
//!
//! Forced-Python exception (whitelisted in the plan): `EMBED_DEVICE`
//! normalisation for `auto`/`mps`/`cuda` needs torch; the probe is cached
//! per process and only fires for those values.

use crate::config::graph_provider;
use crate::pyexec;
use cortex_storage::config::{
    resolve_storage, storage_overlay, ConfigMap, ResolveOverrides, StorageRole,
};
use serde_json::{json, Map, Value};
use std::collections::BTreeMap;
use std::path::{Component, Path, PathBuf};
use std::sync::OnceLock;

/// dev.py `_REMOTE_STORAGE_KEYS` — endpoint/credential keys that never leak
/// from the config into a child environment.
pub const REMOTE_STORAGE_KEYS: [&str; 10] = [
    "QDRANT_URL",
    "QDRANT_HOST",
    "QDRANT_PORT",
    "QDRANT_API_KEY",
    "FALKORDB_URI",
    "FALKORDB_URL",
    "FALKORDB_HOST",
    "FALKORDB_PORT",
    "FALKORDB_USER",
    "FALKORDB_PASSWORD", // sensitive-guard:allow (ten flag / test sample)
];

// ---------------------------------------------------------------------------
// String/value helpers (Python `str()` semantics for JSON values)
// ---------------------------------------------------------------------------

/// Python `str(v)` for the JSON value shapes a dev.json env can hold.
fn py_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Null => "None".to_string(),
        Value::Number(n) => n.to_string(),
        other => py_repr(other),
    }
}

/// Python repr for containers (single quotes) — env values are almost always
/// scalars, but dev.py would render lists/dicts with repr().
fn py_repr(v: &Value) -> String {
    match v {
        Value::Array(items) => {
            let inner: Vec<String> = items.iter().map(py_repr).collect();
            format!("[{}]", inner.join(", "))
        }
        Value::Object(map) => {
            let inner: Vec<String> = map
                .iter()
                .map(|(k, v)| format!("'{}': {}", k, py_repr(v)))
                .collect();
            format!("{{{}}}", inner.join(", "))
        }
        other => py_str(other),
    }
}

fn env_map_of<'a>(cfg: &'a Value, section: &str) -> Map<String, Value> {
    cfg.get(section)
        .and_then(|s| s.get("env"))
        .and_then(|e| e.as_object())
        .cloned()
        .unwrap_or_default()
}

fn object_or_null(v: Option<&Value>) -> Value {
    v.cloned()
        .filter(Value::is_object)
        .unwrap_or_else(|| json!({}))
}

/// `os.path.abspath` — absolute + lexical normalisation, no symlink
/// resolution.
pub fn abspath(path: &Path) -> PathBuf {
    let joined = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .unwrap_or_else(|_| PathBuf::from("."))
            .join(path)
    };
    let mut out: Vec<PathBuf> = Vec::new();
    for comp in joined.components() {
        match comp {
            Component::CurDir => {}
            Component::ParentDir => {
                // Keep leading `..` of a relative path; pop otherwise.
                match out.last() {
                    Some(prev) if prev.as_os_str() != ".." => {
                        out.pop();
                    }
                    None if joined.is_absolute() => {}
                    _ => out.push(PathBuf::from("..")),
                }
            }
            other => out.push(PathBuf::from(other.as_os_str())),
        }
    }
    let mut result = PathBuf::new();
    for part in out {
        result.push(part);
    }
    if result.as_os_str().is_empty() {
        result.push(".");
    }
    result
}

/// `Path.resolve()` with a lexical fallback when the path vanished.
fn realpath(path: &Path) -> PathBuf {
    std::fs::canonicalize(path).unwrap_or_else(|_| abspath(path))
}

/// dev.py `_active_config_path_for_process` — the selected config path
/// without terminating when none exists.
pub fn active_config_path(project_root: Option<&Path>) -> Option<PathBuf> {
    let root = project_root?;
    let dir = root.join(".cortext-harness").join("config");
    let mut first: Option<PathBuf> = None;
    let mut names: Vec<PathBuf> = std::fs::read_dir(&dir)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.extension().map(|x| x == "json").unwrap_or(false) && p.is_file())
        .collect();
    names.sort();
    for path in names {
        let payload = std::fs::read_to_string(&path)
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok());
        if first.is_none() {
            first = Some(path.clone());
        }
        if payload
            .as_ref()
            .and_then(|p| p.get("active"))
            .and_then(|a| a.as_bool())
            .unwrap_or(false)
        {
            return Some(abspath(&path));
        }
    }
    first.map(|p| abspath(&p))
}

/// Python truthiness for a JSON value (`or`-chain semantics).
fn py_falsy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) | Some(Value::Bool(false)) => true,
        Some(Value::String(s)) => s.is_empty(),
        Some(Value::Number(n)) => n.as_f64().map(|f| f == 0.0).unwrap_or(false),
        Some(Value::Array(a)) => a.is_empty(),
        Some(Value::Object(o)) => o.is_empty(),
        Some(_) => false,
    }
}

/// dev.py `_code_qdrant_collection`.
pub fn code_qdrant_collection(env: &Value, project: &Value) -> String {
    let raw = if py_falsy(env.get("QDRANT_COLLECTION")) {
        if py_falsy(project.get("code")) {
            Value::String("project".to_string())
        } else {
            project.get("code").cloned().unwrap_or(Value::Null)
        }
    } else {
        env.get("QDRANT_COLLECTION").cloned().unwrap_or(Value::Null)
    };
    py_str(&raw).trim().to_string()
}

/// dev.py `_storage_targets` — logical graph/collection names per role.
fn storage_targets(cfg: &Value) -> (String, String, String, String) {
    let project = object_or_null(cfg.get("project"));
    let code_or_name = project
        .get("code")
        .map(py_str)
        .filter(|s| !s.is_empty() && s != "None")
        .unwrap_or_else(|| "default".to_string());
    let project_id = code_or_name.trim().to_string();
    let code_env = Value::Object(env_map_of(cfg, "code"));
    let doc_env = Value::Object(env_map_of(cfg, "doc"));
    let code_provider = graph_provider(&code_env, "CODE_GRAPH_PROVIDER").unwrap_or_default();
    let doc_provider = graph_provider(&doc_env, "DOC_GRAPH_PROVIDER").unwrap_or_default();

    let graph_key = |provider: &str| match provider {
        "neo4j" => "NEO4J_DB",
        "ladybug" => "LADYBUG_GRAPH",
        _ => "FALKORDB_GRAPH",
    };

    let falsy = |v: Option<&Value>| py_falsy(v);
    let code_graph = if falsy(code_env.get(graph_key(&code_provider))) {
        project_id.clone()
    } else {
        py_str(code_env.get(graph_key(&code_provider)).unwrap_or(&Value::Null))
    };
    let doc_graph = if falsy(doc_env.get(graph_key(&doc_provider))) {
        format!("{project_id}_doc")
    } else {
        py_str(doc_env.get(graph_key(&doc_provider)).unwrap_or(&Value::Null))
    };
    let code_collection = code_qdrant_collection(&code_env, &project);
    let doc_collection = ["QDRANT_COLLECTION_DOC", "QDRANT_COLLECTION"]
        .iter()
        .find(|k| !falsy(doc_env.get(*k)))
        .map(|k| py_str(doc_env.get(*k).unwrap_or(&Value::Null)))
        .unwrap_or_else(|| format!("{project_id}_doc"));
    (code_graph, doc_graph, code_collection, doc_collection)
}

/// dev.py `_isolate_graph_provider_environment` — keep only the selected
/// provider's graph configuration in `env`; returns the provider. Generic
/// over the two map shapes the port uses (ordered `serde_json::Map` for the
/// payload, `BTreeMap` for the `resolve_storage` config).
pub trait EnvMapOps {
    fn get_value(&self, key: &str) -> Option<Value>;
    fn insert_kv(&mut self, key: &str, value: String);
    fn remove_key(&mut self, key: &str);
    fn key_list(&self) -> Vec<String>;
}

impl EnvMapOps for Map<String, Value> {
    fn get_value(&self, key: &str) -> Option<Value> {
        self.get(key).cloned()
    }
    fn insert_kv(&mut self, key: &str, value: String) {
        self.insert(key.to_string(), Value::String(value));
    }
    fn remove_key(&mut self, key: &str) {
        self.shift_remove(key);
    }
    fn key_list(&self) -> Vec<String> {
        self.keys().cloned().collect()
    }
}

impl EnvMapOps for BTreeMap<String, Value> {
    fn get_value(&self, key: &str) -> Option<Value> {
        self.get(key).cloned()
    }
    fn insert_kv(&mut self, key: &str, value: String) {
        self.insert(key.to_string(), Value::String(value));
    }
    fn remove_key(&mut self, key: &str) {
        self.remove(key);
    }
    fn key_list(&self) -> Vec<String> {
        self.keys().cloned().collect()
    }
}

pub fn isolate_graph_provider_environment<M: EnvMapOps>(env: &mut M, scoped_key: &str) -> String {
    let snapshot: Map<String, Value> = env
        .key_list()
        .into_iter()
        .filter_map(|k| env.get_value(&k).map(|v| (k, v)))
        .collect();
    let provider = graph_provider(&Value::Object(snapshot), scoped_key)
        .unwrap_or_else(|e| crate::util::error_exit(&e));
    env.insert_kv("GRAPH_PROVIDER", provider.clone());
    env.insert_kv(scoped_key, provider.clone());
    let drop_prefixes: &[&str] = match provider.as_str() {
        "falkordb" => &["NEO4J_", "LADYBUG_"],
        "neo4j" => &["FALKORDB_", "LADYBUG_"],
        _ => &["FALKORDB_", "NEO4J_"],
    };
    let extra_doc_key = provider != "falkordb";
    for key in env.key_list() {
        if (extra_doc_key && key == "DOC_FALKORDB_GRAPH")
            || drop_prefixes.iter().any(|p| key.starts_with(p))
        {
            env.remove_key(&key);
        }
    }
    provider
}

/// dev.py `_storage_env_for_process` — canonical owner-specific local
/// storage environment via the native `cortex_storage` layer.
fn storage_env_for_process(
    cfg: &Value,
    project_root: Option<&Path>,
    role: StorageRole,
) -> Result<BTreeMap<String, String>, String> {
    let section = if role == StorageRole::Doc { "doc" } else { "code" };
    let env = Value::Object(env_map_of(cfg, section));
    let (code_graph, doc_graph, code_collection, doc_collection) = storage_targets(cfg);

    let mut resolve_config: ConfigMap = env
        .as_object()
        .map(|m| m.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
        .unwrap_or_default();
    if let Some(backend) = cfg.get("storage_backend").filter(|v| !v.is_null()) {
        resolve_config.insert("storage_backend".to_string(), backend.clone());
    }
    if let Some(remote) = cfg.get("remote").filter(|v| !v.is_null()) {
        resolve_config.insert("remote".to_string(), remote.clone());
    }
    let scoped = if role == StorageRole::Doc {
        "DOC_GRAPH_PROVIDER"
    } else {
        "CODE_GRAPH_PROVIDER"
    };
    isolate_graph_provider_environment(&mut resolve_config, scoped);

    let root = project_root.unwrap_or_else(|| Path::new("."));
    let resolved = resolve_storage(
        root,
        Some(&resolve_config),
        &ResolveOverrides {
            code_graph: Some(code_graph),
            doc_graph: Some(doc_graph),
            code_collection: Some(code_collection),
            doc_collection: Some(doc_collection),
            ..Default::default()
        },
    )
    .map_err(|e| e.to_string())?;

    // The overlay's graph provider comes from the raw section env (not the
    // mutated resolve_config), exactly as dev.py reads it.
    let provider = graph_provider(&env, scoped).unwrap_or_else(|e| crate::util::error_exit(&e));
    storage_overlay(
        &resolved,
        role.as_str(),
        &provider,
        None,
        None,
        None,
        None,
    )
    .map_err(|e| e.to_string())
}

// ---------------------------------------------------------------------------
// torch device probe (forced Python; cached per process)
// ---------------------------------------------------------------------------

struct TorchCaps {
    mps: bool,
    cuda: bool,
}

static TORCH_CAPS: OnceLock<Option<TorchCaps>> = OnceLock::new();

/// Probe the harness interpreter for torch accelerator availability
/// (sync.rs's historical `python_resolve_device` script, moved here so every
/// native env path shares one probe per process).
fn torch_caps() -> Option<&'static Option<TorchCaps>> {
    Some(TORCH_CAPS.get_or_init(|| {
        let script = "import json\ncaps={'mps': False, 'cuda': False}\ntry:\n import torch\n m=getattr(torch.backends,'mps',None)\n caps['mps']=bool(m is not None and m.is_available())\n caps['cuda']=bool(torch.cuda.is_available())\nexcept Exception:\n pass\nprint(json.dumps(caps))";
        let output = std::process::Command::new(pyexec::venv_python(&pyexec::repo_root()))
            .arg("-c")
            .arg(script)
            .output();
        let parsed: Value = match output {
            Ok(o) if o.status.success() => {
                serde_json::from_slice(&o.stdout).unwrap_or(Value::Null)
            }
            _ => Value::Null,
        };
        let missing = |key: &str| parsed.get(key).and_then(|v| v.as_bool()).unwrap_or(false);
        if parsed.is_null() {
            None
        } else {
            Some(TorchCaps {
                mps: missing("mps"),
                cuda: missing("cuda"),
            })
        }
    }))
}

/// dev.py `_normalize_embed_device` — drop devices the local torch build
/// cannot serve; `"auto"` resolves into a concrete device here so analyzer
/// CLIs never receive it raw.
pub fn normalize_embed_device(device: &str) -> String {
    let value = device.trim().to_lowercase();
    if value == "auto" {
        return match torch_caps() {
            Some(Some(caps)) if cfg!(target_os = "macos") => {
                if caps.mps {
                    "mps".to_string()
                } else {
                    "cpu".to_string()
                }
            }
            Some(Some(caps)) if caps.cuda => "cuda".to_string(),
            Some(Some(_)) | Some(None) | None => "cpu".to_string(),
        };
    }
    if value != "mps" && value != "cuda" {
        return device.to_string();
    }
    let caps = match torch_caps() {
        Some(Some(caps)) => caps,
        _ => return device.to_string(),
    };
    if value == "cuda" && caps.cuda {
        return value;
    }
    if value == "mps" && cfg!(target_os = "macos") && caps.mps {
        return value;
    }
    if caps.cuda {
        return "cuda".to_string();
    }
    "cpu".to_string()
}

// ---------------------------------------------------------------------------
// code / doc process env
// ---------------------------------------------------------------------------

fn setdefault(map: &mut Map<String, Value>, key: &str, value: String) {
    map.entry(key.to_string())
        .or_insert_with(|| Value::String(value));
}

fn env_string_setdefault(map: &mut Map<String, Value>, key: &str, source: &Map<String, Value>, source_key: &str) {
    if let Some(v) = source.get(source_key) {
        if !v.is_null() {
            setdefault(map, key, py_str(v));
        }
    }
}

/// dev.py `_code_env_for_process`.
pub fn code_env_for_process(
    cfg: &Value,
    project_root: Option<&Path>,
    config_path: Option<&Path>,
) -> Result<Map<String, Value>, String> {
    let project = object_or_null(cfg.get("project"));
    let env = env_map_of(cfg, "code");

    let collection = code_qdrant_collection(&Value::Object(env.clone()), &project);
    let mut result: Map<String, Value> = Map::new();
    for (key, value) in &env {
        if value.is_null() || REMOTE_STORAGE_KEYS.contains(&key.as_str()) {
            continue;
        }
        result.insert(key.clone(), Value::String(py_str(value)));
    }
    let overlay = storage_env_for_process(cfg, project_root, StorageRole::Code)?;
    for (key, value) in overlay {
        result.insert(key, Value::String(value));
    }
    let project_id = if py_falsy(project.get("code")) {
        if py_falsy(project.get("name")) {
            String::new()
        } else {
            py_str(project.get("name").unwrap_or(&Value::Null))
        }
    } else {
        py_str(project.get("code").unwrap_or(&Value::Null))
    }
    .trim()
    .to_string();
    if !project_id.is_empty() {
        setdefault(&mut result, "PROJECT_ID", project_id.clone());
        setdefault(&mut result, "CORTEX_STORAGE_PROJECT_ID", project_id);
    }
    setdefault(&mut result, "QDRANT_COLLECTION", collection.clone());
    setdefault(&mut result, "QDRANT_COLLECTION_CODE", collection);
    env_string_setdefault(&mut result, "CODE_EMBEDDING_MODEL", &env, "EMBEDDING_MODEL");
    env_string_setdefault(&mut result, "EMBED_MODEL", &env, "EMBEDDING_MODEL");
    if let Some(device) = env.get("device").filter(|v| !v.is_null()) {
        setdefault(
            &mut result,
            "EMBED_DEVICE",
            normalize_embed_device(&py_str(device)),
        );
    }
    env_string_setdefault(&mut result, "EMBED_BATCH_SIZE", &env, "BATCH_SIZE");
    env_string_setdefault(&mut result, "MAX_EMBED_CHARS", &env, "MAX_EMBED_CHARS");
    env_string_setdefault(&mut result, "QDRANT_CACHE_DIR", &env, "CACHE_DIR");
    let config_path = match config_path {
        Some(p) => Some(p.to_path_buf()),
        None => active_config_path(project_root),
    };
    if let Some(p) = config_path {
        result.insert(
            "CORTEX_HARNESS_CONFIG_PATH".to_string(),
            Value::String(p.to_string_lossy().to_string()),
        );
    }
    isolate_graph_provider_environment(&mut result, "CODE_GRAPH_PROVIDER");
    Ok(result)
}

/// dev.py `_doc_env_for_process`.
pub fn doc_env_for_process(
    cfg: &Value,
    project_root: Option<&Path>,
    config_path: Option<&Path>,
) -> Result<Map<String, Value>, String> {
    let env = env_map_of(cfg, "doc");
    let mut result: Map<String, Value> = Map::new();
    for (key, value) in &env {
        if value.is_null() || REMOTE_STORAGE_KEYS.contains(&key.as_str()) {
            continue;
        }
        result.insert(key.clone(), Value::String(py_str(value)));
    }
    let overlay = storage_env_for_process(cfg, project_root, StorageRole::Doc)?;
    for (key, value) in overlay {
        result.insert(key, Value::String(value));
    }
    env_string_setdefault(&mut result, "DOC_EMBEDDING_MODEL", &env, "EMBEDDING_MODEL");
    if let Some(device) = env.get("device").filter(|v| !v.is_null()) {
        setdefault(
            &mut result,
            "EMBED_DEVICE",
            normalize_embed_device(&py_str(device)),
        );
    }
    let project_id = if cfg
        .get("project")
        .map(|p| py_falsy(p.get("code")))
        .unwrap_or(true)
    {
        None
    } else {
        Some(py_str(
            cfg.get("project")
                .and_then(|p| p.get("code"))
                .unwrap_or(&Value::Null),
        ))
    };
    let config_path = match config_path {
        Some(p) => Some(p.to_path_buf()),
        None => active_config_path(project_root),
    };
    if let Some(p) = &config_path {
        result.insert(
            "CORTEX_HARNESS_CONFIG_PATH".to_string(),
            Value::String(p.to_string_lossy().to_string()),
        );
    }
    let mut targets: Option<(String, String)> = None; // (doc_graph, doc_qdrant_collection)
    if let Some(project_id) = &project_id {
        targets = config_path
            .as_ref()
            .and_then(|p| resolve_project_doc_targets(p.parent(), project_id));
        setdefault(&mut result, "PROJECT_ID", project_id.clone());
        setdefault(&mut result, "CORTEX_STORAGE_PROJECT_ID", project_id.clone());
    }
    let doc_collection = result
        .get("QDRANT_COLLECTION_DOC")
        .map(py_str)
        .filter(|s| !s.is_empty() && s != "None")
        .or_else(|| {
            result
                .get("QDRANT_COLLECTION")
                .map(py_str)
                .filter(|s| !s.is_empty() && s != "None")
        })
        .or_else(|| targets.as_ref().map(|(_, c)| c.clone()));
    if let Some(doc_collection) = doc_collection {
        setdefault(&mut result, "QDRANT_COLLECTION_DOC", doc_collection);
    }
    if let Some((doc_graph, _)) = &targets {
        let provider = graph_provider(&Value::Object(result.clone()), "DOC_GRAPH_PROVIDER")
            .unwrap_or_else(|e| crate::util::error_exit(&e));
        if provider == "falkordb" {
            result.insert("FALKORDB_GRAPH".to_string(), Value::String(doc_graph.clone()));
        }
    }
    isolate_graph_provider_environment(&mut result, "DOC_GRAPH_PROVIDER");
    Ok(result)
}

/// Minimal native port of `tools.common.project_registry.resolve_project_targets`
/// for the single use in `_doc_env_for_process`: returns
/// `(doc_graph, doc_qdrant_collection)` for `project_id`, or `None` when the
/// registry exists but the project is not registered (Python raises, the
/// caller catches → `targets = None`).
fn resolve_project_doc_targets(config_dir: Option<&Path>, project_id: &str) -> Option<(String, String)> {
    let lookup = project_id.trim().to_lowercase();
    if lookup.is_empty() {
        return None;
    }
    let dir = config_dir?;
    let mut names: Vec<PathBuf> = std::fs::read_dir(dir)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.extension().map(|x| x == "json").unwrap_or(false) && p.is_file())
        .collect();
    names.sort();
    let mut entries: Vec<(String, Map<String, Value>, Map<String, Value>)> = Vec::new();
    for path in names {
        let payload: Value = std::fs::read_to_string(&path)
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok())?;
        let project = payload.get("project").cloned().unwrap_or(json!({}));
        let entry_id = project
            .get("code")
            .map(py_str)
            .filter(|s| !s.is_empty() && s != "None")
            .or_else(|| {
                project
                    .get("name")
                    .map(py_str)
                    .filter(|s| !s.is_empty() && s != "None")
            })?;
        let code_env = payload
            .get("code")
            .and_then(|c| c.get("env"))
            .and_then(|e| e.as_object())
            .cloned()
            .unwrap_or_default();
        let doc_env = payload
            .get("doc")
            .and_then(|d| d.get("env"))
            .and_then(|e| e.as_object())
            .cloned()
            .unwrap_or_default();
        entries.push((entry_id, code_env, doc_env));
    }
    let match_entry = entries.iter().find(|(id, _, _)| id.trim().to_lowercase() == lookup);
    let Some((canonical, _, doc_env)) = match_entry else {
        // A registry exists but this project isn't in it (or the registry is
        // empty with no env seeds) — Python raises → caller treats as None.
        return None;
    };
    let provider_value = |keys: &[&str]| -> Option<String> {
        keys.iter()
            .find_map(|k| doc_env.get(*k).map(py_str))
            .filter(|s| !s.is_empty() && s != "None")
    };
    let doc_provider = provider_value(&["DOC_GRAPH_PROVIDER", "GRAPH_PROVIDER"])
        .map(|v| v.trim().to_lowercase())
        .unwrap_or_default();
    let graph_keys: &[&str] = if doc_provider == "neo4j" {
        &["NEO4J_DB", "FALKORDB_GRAPH", "LADYBUG_GRAPH"]
    } else if matches!(doc_provider.as_str(), "ladybug" | "lbug" | "lady-bug" | "kuzu") {
        &["LADYBUG_GRAPH", "FALKORDB_GRAPH", "NEO4J_DB"]
    } else {
        &["FALKORDB_GRAPH", "LADYBUG_GRAPH", "NEO4J_DB"]
    };
    let doc_graph = graph_keys
        .iter()
        .copied()
        .find(|k| !py_falsy(doc_env.get(*k)))
        .map(|k| py_str(doc_env.get(k).unwrap_or(&Value::Null)))
        .unwrap_or_else(|| format!("{canonical}_doc"));
    let doc_qdrant = if py_falsy(doc_env.get("QDRANT_COLLECTION")) {
        format!("{canonical}_doc")
    } else {
        py_str(doc_env.get("QDRANT_COLLECTION").unwrap_or(&Value::Null))
    };
    Some((doc_graph, doc_qdrant))
}

// ---------------------------------------------------------------------------
// mcp env + start-config resolution
// ---------------------------------------------------------------------------

/// dev.py `resolve_start_config` — nearest `dev.json` walking up from
/// `start`, else the harness install root.
pub fn resolve_start_config(start: &Path, fallback_root: &Path) -> (PathBuf, PathBuf) {
    let dev_config = Path::new(".cortext-harness").join("config").join("dev.json");
    let mut current = if start.is_file() {
        start
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_else(|| start.to_path_buf())
    } else {
        start.to_path_buf()
    };
    let mut candidates = vec![current.clone()];
    while let Some(parent) = current.parent() {
        candidates.push(parent.to_path_buf());
        current = parent.to_path_buf();
    }
    for candidate in candidates {
        let config_path = candidate.join(&dev_config);
        if config_path.is_file() {
            return (candidate, config_path);
        }
    }
    let root = abspath(fallback_root);
    let config_path = root.join(&dev_config);
    (root, config_path)
}

/// dev.py `_mcp_env_from_config` — code/doc process env for one MCP service.
pub fn mcp_env_from_config(project_dir: &Path, service_name: &str) -> Map<String, Value> {
    let (config_root, cfg_path) = resolve_start_config(project_dir, &pyexec::repo_root());
    if !cfg_path.is_file() {
        return Map::new();
    }
    let Ok(text) = std::fs::read_to_string(&cfg_path) else {
        return Map::new();
    };
    let Ok(cfg) = serde_json::from_str::<Value>(&text) else {
        return Map::new();
    };
    let mut result = match service_name {
        "code-tiny" => code_env_for_process(&cfg, Some(&config_root), Some(&cfg_path))
            .unwrap_or_default(),
        "doc-tiny" => doc_env_for_process(&cfg, Some(&config_root), Some(&cfg_path))
            .unwrap_or_default(),
        _ => return Map::new(),
    };
    result.insert(
        "CORTEX_HARNESS_CONFIG_PATH".to_string(),
        Value::String(abspath(&cfg_path).to_string_lossy().to_string()),
    );
    result
}

// ---------------------------------------------------------------------------
// Op-level payloads (bridge-op replacements; the pyexec ops resolve the
// config path with `Path(path).resolve()`, i.e. symlinks resolved)
// ---------------------------------------------------------------------------

fn code_env_payload(project_dir: &Path) -> Value {
    let (cfg, path) = crate::config::load_active_config(project_dir);
    let mut payload = code_env_for_process(&cfg, Some(project_dir), None)
        .unwrap_or_else(|e| crate::util::error_exit(&e));
    payload.insert(
        "CORTEX_HARNESS_CONFIG_PATH".to_string(),
        Value::String(realpath(&path).to_string_lossy().to_string()),
    );
    Value::Object(payload)
}

fn doc_env_payload(project_dir: &Path) -> Value {
    let (cfg, path) = crate::config::load_active_config(project_dir);
    let mut payload = doc_env_for_process(&cfg, Some(project_dir), None)
        .unwrap_or_else(|e| crate::util::error_exit(&e));
    payload.insert(
        "CORTEX_HARNESS_CONFIG_PATH".to_string(),
        Value::String(realpath(&path).to_string_lossy().to_string()),
    );
    Value::Object(payload)
}

/// The `status_env` bridge-op payload — feeds `dev status` text assembly.
pub fn status_env_payload(project_dir: &Path) -> Value {
    let (cfg, path) = crate::config::load_active_config(project_dir);
    let code_env_raw = Value::Object(env_map_of(&cfg, "code"));
    let doc_env_raw = Value::Object(env_map_of(&cfg, "doc"));
    let project = object_or_null(cfg.get("project"));
    let code_env = code_env_for_process(&cfg, Some(project_dir), None)
        .unwrap_or_else(|e| crate::util::error_exit(&e));
    let doc_env = doc_env_for_process(&cfg, Some(project_dir), None)
        .unwrap_or_else(|e| crate::util::error_exit(&e));
    let code_provider = graph_provider(&code_env_raw, "CODE_GRAPH_PROVIDER")
        .unwrap_or_else(|e| crate::util::error_exit(&e));
    let doc_provider = graph_provider(&doc_env_raw, "DOC_GRAPH_PROVIDER")
        .unwrap_or_else(|e| crate::util::error_exit(&e));
    json!({
        "config_name": path
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default(),
        "project": project,
        "code_env_raw": code_env_raw,
        "doc_env_raw": doc_env_raw,
        "code_env": Value::Object(code_env),
        "doc_env": Value::Object(doc_env),
        "code_provider": code_provider,
        "doc_provider": doc_provider,
        "code_collection": code_qdrant_collection(&code_env_raw, &object_or_null(cfg.get("project"))),
    })
}
/// Entry point used by `cmds/sync.rs` — the `code_env` bridge op.
pub fn code_env(project_dir: &Path) -> Value {
    code_env_payload(project_dir)
}

/// Entry point used by `cmds/sync.rs` — the `doc_env` bridge op.
pub fn doc_env(project_dir: &Path) -> Value {
    doc_env_payload(project_dir)
}
