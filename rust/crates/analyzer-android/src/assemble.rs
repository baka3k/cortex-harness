//! Port `build_call_graph` phần graph write của `android_kotlin_analyzer.py`:
//! indexes từ index_payloads, resolution (callee/class/composable), row
//! assembly theo đúng thứ tự Python, rồi `write_nodes_batch` (cypher copy
//! nguyên chữ) → `write_relations_typed` → `write_calls_with_site`.
//!
//! Divergence ghi nhận: call rows Rust gắn `project_id` tường minh (writer
//! contract — bản Python standalone KHÔNG gắn và chỉ chạy được khi có journal
//! env; writer stamp cùng giá trị từ journal metadata ⇒ graph như nhau).

use std::collections::{BTreeSet, HashMap};
use std::path::Path;

use serde_json::{json, Value};

use cortex_graph_writer::language_writer::LanguageCodeWriter;

use crate::common::{self, AndroidComponentDef, Row};
use crate::kotlinparse::{class_id, CallEdge, FilePayload, FunctionTypeDef};
use crate::scan::{
    extract_android_annotations, extract_resource_refs, GradleDependencyDef, GradleModuleDef,
    ResourceDef,
};

/// Row::from([..]) builder — serde_json Map thiếu impl From<[(String, Value); N]>.
fn row(pairs: Vec<(&str, Value)>) -> Row {
    pairs.into_iter().map(|(key, value)| (key.to_string(), value)).collect()
}


// ── Node queries — copy nguyên chữ từ `node_queries` của analyzer ───────────

pub const NODE_QUERIES: [(&str, &str); 18] = [
    (
        "projects",
        r#"
                UNWIND $rows AS row
                MERGE (p:Project {project_id: row.id})
                SET p.id = row.id,
                    p.project_id_normalized = row.project_id_normalized,
                    p.name = row.name,
                    p.language = row.language,
                    p.repo = row.repo,
                    p.root = row.root,
                    p.build_system = row.build_system
                "#,
    ),
    (
        "packages",
        r#"
                UNWIND $rows AS row
                MERGE (p:Package {id: row.id})
                SET p.name = row.name,
                    p.start_line = row.start_line,
                    p.end_line = row.end_line,
                    p.code = row.code,
                    p.comment = row.comment,
                    p.summary = row.summary,
                    p.note = row.note,
                    p.project_id = row.project_id,
    p.project_id_normalized = row.project_id_normalized,
                    p.project_name = row.project_name,
                    p.language = row.language,
                    p.repo = row.repo,
                    p.build_system = row.build_system
                "#,
    ),
    (
        "namespaces",
        r#"
                UNWIND $rows AS row
                MERGE (n:Namespace {id: row.id})
                SET n.name = row.name,
                    n.qualified_name = row.qualified_name,
                    n.file_path = row.file_path,
                    n.start_line = row.start_line,
                    n.end_line = row.end_line,
                    n.code = row.code,
                    n.comment = row.comment,
                    n.summary = row.summary,
                    n.note = row.note,
                    n.project_id = row.project_id,

                    n.project_id_normalized = row.project_id_normalized,
                    n.project_name = row.project_name,
                    n.language = row.language,
                    n.repo = row.repo,
                    n.build_system = row.build_system
                "#,
    ),
    (
        "files",
        r#"
                UNWIND $rows AS row
                MERGE (f:File {id: row.id})
                SET f:File,
                    f.path = row.path,
                    f.package_name = row.package_name,
                    f.start_line = row.start_line,
                    f.end_line = row.end_line,
                    f.code = row.code,
                    f.comment = row.comment,
                    f.summary = row.summary,
                    f.note = row.note,
                    f.project_id = row.project_id,
                    f.project_id_normalized = row.project_id_normalized,
                    f.project_name = row.project_name,
                    f.language = row.language,
                    f.repo = row.repo,
                    f.build_system = row.build_system
                "#,
    ),
    (
        "directories",
        r#"
                UNWIND $rows AS row
                MERGE (d:Directory {id: row.id})
                SET d:Directory,
                    d.name = row.name,
                    d.path = row.path,
                    d.depth = row.depth,
                    d.project_id = row.project_id,
                    d.project_id_normalized = row.project_id_normalized,
                    d.project_name = row.project_name,
                    d.language = row.language,
                    d.repo = row.repo,
                    d.build_system = row.build_system
                "#,
    ),
    (
        "classes",
        r#"
                UNWIND $rows AS row
                MERGE (c:Class {id: row.id})
                SET c:Class,
                    c.name = row.name,
                    c.qualified_name = row.qualified_name,
                    c.kind = row.kind,
                    c.package_name = row.package_name,
                    c.file_path = row.file_path,
                    c.start_line = row.start_line,
                    c.end_line = row.end_line,
                    c.code = row.code,
                    c.comment = row.comment,
                    c.summary = row.summary,
                    c.note = row.note,
                    c.visibility = coalesce(row.visibility, 'unknown'),
                    c.is_public_api = coalesce(row.is_public_api, false),
                    c.visibility_source = coalesce(row.visibility_source, ''),
                    c.export_evidence = coalesce(row.export_evidence, ''),
                    c.signature = coalesce(row.signature, ''),
                    c.project_id = row.project_id,
    c.project_id_normalized = row.project_id_normalized,
                    c.project_name = row.project_name,
                    c.language = row.language,
                    c.repo = row.repo,
                    c.build_system = row.build_system
                "#,
    ),
    (
        "function_types",
        r#"
                UNWIND $rows AS row
                MERGE (t:FunctionType {id: row.id})
                SET t:FunctionType,
                    t.type_signature = row.type_signature,
                    t.file_path = row.file_path,
                    t.start_line = row.start_line,
                    t.end_line = row.end_line,
                    t.code = row.code,
                    t.project_id = row.project_id,

                    t.project_id_normalized = row.project_id_normalized,
                    t.project_name = row.project_name,
                    t.language = row.language,
                    t.repo = row.repo,
                    t.build_system = row.build_system
                "#,
    ),
    (
        "functions",
        r#"
                UNWIND $rows AS row
                MERGE (f:Function {id: row.id})
                SET f:Function,
                    f.name = row.name,
                    f.qualified_name = row.qualified_name,
                    f.kind = row.kind,
                    f.class_name = row.class_name,
                    f.package_name = row.package_name,
                    f.file_path = row.file_path,
                    f.start_line = row.start_line,
                    f.end_line = row.end_line,
                    f.arity = row.arity,
                    f.code = row.code,
                    f.comment = row.comment,
                    f.summary = row.summary,
                    f.note = row.note,
                    f.exported = coalesce(row.is_public_api, false),
                    f.visibility = coalesce(row.visibility, 'unknown'),
                    f.is_public_api = coalesce(row.is_public_api, false),
                    f.visibility_source = coalesce(row.visibility_source, ''),
                    f.export_evidence = coalesce(row.export_evidence, ''),
                    f.signature = coalesce(row.signature, ''),
                    f.project_id = row.project_id,
    f.project_id_normalized = row.project_id_normalized,
                    f.project_name = row.project_name,
                    f.language = row.language,
                    f.repo = row.repo,
                    f.build_system = row.build_system
                "#,
    ),
    (
        "android_manifests",
        r#"
                UNWIND $rows AS row
                MERGE (m:AndroidManifest {id: row.id})
                SET m:AndroidManifest,
                    m.package_name = row.package_name,
                    m.file_path = row.file_path,
                    m.path = row.path,
                    m.start_line = row.start_line,
                    m.end_line = row.end_line,
                    m.code = row.code,
                    m.summary = row.summary,
                    m.note = row.note,
                    m.project_id = row.project_id,

                    m.project_id_normalized = row.project_id_normalized,
                    m.project_name = row.project_name,
                    m.language = row.language,
                    m.repo = row.repo,
                    m.build_system = row.build_system
                "#,
    ),
    (
        "android_components",
        r#"
                UNWIND $rows AS row
                MERGE (c:AndroidComponent {id: row.id})
                SET c:AndroidComponent,
                    c.name = row.name,
                    c.component_type = row.component_type,
                    c.class_name = row.class_name,
                    c.exported = row.exported,
                    c.process = row.process,
                    c.permission = row.permission,
                    c.enabled = row.enabled,
                    c.direct_boot_aware = row.direct_boot_aware,
                    c.target_activity = row.target_activity,
                    c.intent_actions = row.intent_actions,
                    c.intent_categories = row.intent_categories,
                    c.intent_data = row.intent_data,
                    c.file_path = row.file_path,
                    c.path = row.path,
                    c.start_line = row.start_line,
                    c.end_line = row.end_line,
                    c.code = row.code,
                    c.summary = row.summary,
                    c.note = row.note,
                    c.project_id = row.project_id,
    c.project_id_normalized = row.project_id_normalized,
                    c.project_name = row.project_name,
                    c.language = row.language,
                    c.repo = row.repo,
                    c.build_system = row.build_system
                "#,
    ),
    (
        "android_resources",
        r#"
                UNWIND $rows AS row
                MERGE (r:AndroidResource {id: row.id})
                SET r:AndroidResource,
                    r.name = row.name,
                    r.res_type = row.res_type,
                    r.file_path = row.file_path,
                    r.qualifier = row.qualifier,
                    r.summary = row.summary,
                    r.note = row.note,
                    r.project_id = row.project_id,

                    r.project_id_normalized = row.project_id_normalized,
                    r.project_name = row.project_name,
                    r.language = row.language,
                    r.repo = row.repo,
                    r.build_system = row.build_system
                "#,
    ),
    (
        "gradle_modules",
        r#"
                UNWIND $rows AS row
                MERGE (m:GradleModule {id: row.id})
                SET m:GradleModule,
                    m.name = row.name,
                    m.module_path = row.module_path,
                    m.module_type = row.module_type,
                    m.namespace = row.namespace,
                    m.application_id = row.application_id,
                    m.file_path = row.file_path,
                    m.summary = row.summary,
                    m.note = row.note,
                    m.project_id = row.project_id,
    m.project_id_normalized = row.project_id_normalized,
                    m.project_name = row.project_name,
                    m.language = row.language,
                    m.repo = row.repo,
                    m.build_system = row.build_system
                "#,
    ),
    (
        "gradle_dependencies",
        r#"
                UNWIND $rows AS row
                MERGE (d:GradleDependency {id: row.id})
                SET d:GradleDependency,
                    d.coordinate = row.coordinate,
                    d.group = row.group,
                    d.artifact = row.artifact,
                    d.version = row.version,
                    d.summary = row.summary,
                    d.note = row.note,
                    d.project_id = row.project_id,

                    d.project_id_normalized = row.project_id_normalized,
                    d.project_name = row.project_name,
                    d.language = row.language,
                    d.repo = row.repo,
                    d.build_system = row.build_system
                "#,
    ),
    (
        "android_annotations",
        r#"
                UNWIND $rows AS row
                MERGE (a:AndroidAnnotation {id: row.id})
                SET a:AndroidAnnotation,
                    a.name = row.name,
                    a.summary = row.summary,
                    a.note = row.note,
                    a.project_id = row.project_id,
    a.project_id_normalized = row.project_id_normalized,
                    a.project_name = row.project_name,
                    a.language = row.language,
                    a.repo = row.repo,
                    a.build_system = row.build_system
                "#,
    ),
    (
        "android_nav_routes",
        r#"
                UNWIND $rows AS row
                MERGE (r:AndroidNavRoute {id: row.id})
                SET r:AndroidNavRoute,
                    r.route = row.route,
                    r.file_path = row.file_path,
                    r.summary = row.summary,
                    r.note = row.note,
                    r.project_id = row.project_id,

                    r.project_id_normalized = row.project_id_normalized,
                    r.project_name = row.project_name,
                    r.language = row.language,
                    r.repo = row.repo,
                    r.build_system = row.build_system
                "#,
    ),
    (
        "android_intent_actions",
        r#"
                UNWIND $rows AS row
                MERGE (a:AndroidIntentAction {id: row.id})
                SET a:AndroidIntentAction,
                    a.action = row.action,
                    a.summary = row.summary,
                    a.note = row.note,
                    a.project_id = row.project_id,
    a.project_id_normalized = row.project_id_normalized,
                    a.project_name = row.project_name,
                    a.language = row.language,
                    a.repo = row.repo,
                    a.build_system = row.build_system
                "#,
    ),
    (
        "android_handler_messages",
        r#"
                UNWIND $rows AS row
                MERGE (m:AndroidHandlerMessage {id: row.id})
                SET m:AndroidHandlerMessage,
                    m.token = row.token,
                    m.summary = row.summary,
                    m.note = row.note,
                    m.project_id = row.project_id,

                    m.project_id_normalized = row.project_id_normalized,
                    m.project_name = row.project_name,
                    m.language = row.language,
                    m.repo = row.repo,
                    m.build_system = row.build_system
                "#,
    ),
    (
        "events",
        r#"
                UNWIND $rows AS row
                MERGE (e:Event {id: row.id})
                SET e:Event,
                    e.project_id = row.project_id,
                    e.project_id_normalized = row.project_id_normalized,
                    e.name = row.name,
                    e.namespace = row.namespace,
                    e.version = row.version,
                    e.payload_schema = row.payload_schema,
                    e.payload_type = row.payload_type,
                    e.payload_version = row.payload_version,
                    e.payload_example = row.payload_example,
                    e.source = row.source
                "#,
    ),
];

/// (file_id → [(route_id, target_names)]) — nav route targets theo file.
type RouteTargetsEntry = (String, Vec<(String, Vec<String>)>);
type RouteTargetsByFile = Vec<RouteTargetsEntry>;

const ALLOWED_REL_TYPES: [&str; 21] = [
    "CONTAINS",
    "DECLARES",
    "EXTENDS",
    "IMPLEMENTS",
    "TAKES_FUNCTION",
    "USES_RESOURCE",
    "DEPENDS_ON",
    "DECLARES_COMPONENT",
    "ANNOTATED_WITH",
    "DECLARES_ROUTE",
    "STARTS_WITH_ROUTE",
    "ROUTE_CALLS",
    "STARTS_COMPONENT",
    "STARTS_INTENT",
    "SENDS_BROADCAST",
    "REGISTERS_RECEIVER",
    "DECLARES_INTENT_ACTION",
    "SENDS_HANDLER_MESSAGE",
    "ACTION_TARGETS_COMPONENT",
    "EMITS_EVENT",
    "HANDLES_EVENT",
];

// ── uuid5 (Python uuid.uuid5(NAMESPACE_URL, name)) ──────────────────────────

/// `uuid.uuid5(uuid.NAMESPACE_URL, key)` — SHA-1, version 5, RFC4122 variant.
fn stable_point_id(symbol_id: &str) -> String {
    use sha1::{Digest, Sha1};
    const NAMESPACE_URL: [u8; 16] = [
        0x6b, 0xa7, 0xb8, 0x11, 0x9d, 0xad, 0x11, 0xd1, 0x80, 0xb4, 0x00, 0xc0, 0x4f, 0xd4, 0x30,
        0xc8,
    ];
    let mut hasher = Sha1::new();
    hasher.update(NAMESPACE_URL);
    hasher.update(symbol_id.as_bytes());
    let digest = hasher.finalize();
    let mut bytes = [0u8; 16];
    bytes.copy_from_slice(&digest[..16]);
    bytes[6] = (bytes[6] & 0x0f) | 0x50; // version 5
    bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant RFC4122
    let hex: String = bytes.iter().map(|b| format!("{b:02x}")).collect();
    format!(
        "{}-{}-{}-{}-{}",
        &hex[0..8],
        &hex[8..12],
        &hex[12..16],
        &hex[16..20],
        &hex[20..32]
    )
}

/// `_call_site_id`.
fn call_site_id(
    caller_id: &str,
    callee_id: &str,
    file_path: &str,
    line: i64,
    column: i64,
    call_type: &str,
) -> String {
    let key = format!("{caller_id}:{callee_id}:{file_path}:{line}:{column}:{call_type}");
    stable_point_id(&key)
}

// ── Event map ───────────────────────────────────────────────────────────────

fn event_id(event: &Value) -> Result<String, String> {
    let id = event.get("id").and_then(Value::as_str).unwrap_or("").trim();
    if !id.is_empty() {
        return Ok(id.to_string());
    }
    let name = event.get("name").and_then(Value::as_str).unwrap_or("").trim();
    if name.is_empty() {
        return Err("Event mapping entry missing 'id' or 'name'".to_string());
    }
    let namespace = event
        .get("namespace")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim();
    let version = event
        .get("version")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim();
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

// ── Indexes ─────────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
struct FuncEntry {
    symbol_id: String,
    class_name: Option<String>,
    package_name: Option<String>,
}

/// Function index — giữ insertion-order dict Python (qualified_keys cho vòng
/// `endswith`; giá trị qua qualified là lần ghi cuối).
#[derive(Default)]
pub struct FunctionIndex {
    by_name: HashMap<String, Vec<FuncEntry>>,
    qualified: HashMap<String, FuncEntry>,
    qualified_keys: Vec<String>,
    by_class_and_name: HashMap<(String, String), Vec<FuncEntry>>,
}

impl FunctionIndex {
    pub fn build(payloads: &[&FilePayload]) -> Self {
        let mut index = FunctionIndex::default();
        for payload in payloads {
            for func in &payload.functions {
                let entry = FuncEntry {
                    symbol_id: func.symbol_id.clone(),
                    class_name: func.class_name.clone(),
                    package_name: func.package_name.clone(),
                };
                index
                    .by_name
                    .entry(func.name.clone())
                    .or_default()
                    .push(entry.clone());
                if !index.qualified.contains_key(&func.qualified_name) {
                    index.qualified_keys.push(func.qualified_name.clone());
                }
                index.qualified.insert(func.qualified_name.clone(), entry.clone());
                if let Some(class_name) = &func.class_name {
                    index
                        .by_class_and_name
                        .entry((class_name.clone(), func.name.clone()))
                        .or_default()
                        .push(entry);
                }
            }
        }
        index
    }

    fn pick_candidate<'a>(&self, candidates: &'a [FuncEntry], call: &CallEdge) -> Option<&'a FuncEntry> {
        if let Some(caller_class) = call.caller_class.as_deref().filter(|c| !c.is_empty()) {
            for func in candidates {
                if func.class_name.as_deref() == Some(caller_class)
                    && func.package_name == call.caller_package
                {
                    return Some(func);
                }
            }
        }
        if let Some(caller_package) = call.caller_package.as_deref().filter(|p| !p.is_empty()) {
            for func in candidates {
                if func.package_name.as_deref() == Some(caller_package) {
                    return Some(func);
                }
            }
        }
        if !call.imports.is_empty() {
            for func in candidates {
                if let Some(package_name) = &func.package_name
                    && call
                        .imports
                        .iter()
                        .any(|imp| imp.starts_with(package_name.as_str()))
                {
                    return Some(func);
                }
            }
        }
        candidates.first()
    }

    fn resolve_callee_id(&self, call: &CallEdge) -> Option<String> {
        let callee_name = &call.callee_name;
        let mut candidate: Option<&FuncEntry> = None;

        if callee_name.contains('.') {
            if let Some(entry) = self.qualified.get(callee_name) {
                candidate = Some(entry);
            } else {
                let parts: Vec<&str> = callee_name.split('.').collect();
                let method_name = parts[parts.len() - 1];
                let qualifier = if parts.len() >= 2 {
                    Some(parts[parts.len() - 2])
                } else {
                    None
                };
                if let Some(qualifier) = qualifier
                    && let Some(candidates) = self
                        .by_class_and_name
                        .get(&(qualifier.to_string(), method_name.to_string()))
                {
                    candidate = self.pick_candidate(candidates, call);
                }
                if candidate.is_none() {
                    for qual in &self.qualified_keys {
                        if qual.ends_with(callee_name.as_str()) {
                            candidate = self.qualified.get(qual);
                            break;
                        }
                    }
                }
            }
        }

        if candidate.is_none()
            && let Some(candidates) = self.by_name.get(callee_name)
        {
            candidate = self.pick_candidate(candidates, call);
        }
        candidate.map(|entry| entry.symbol_id.clone())
    }

    /// `resolve_function_name` — qualified → by_name[0].
    fn resolve_function_name(&self, name: &str) -> Option<String> {
        if let Some(entry) = self.qualified.get(name) {
            return Some(entry.symbol_id.clone());
        }
        self.by_name
            .get(name)
            .and_then(|candidates| candidates.first())
            .map(|entry| entry.symbol_id.clone())
    }
}

// ── External class entry ────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct ExternalClass {
    pub symbol_id: String,
    pub name: String,
}

// ── Assemble context ────────────────────────────────────────────────────────

#[allow(clippy::too_many_arguments)]
pub fn write_graph(
    writer: &mut LanguageCodeWriter,
    selected_payloads: &[&FilePayload],
    index_payloads: &[&FilePayload],
    manifest_defs: &[common::AndroidManifestDef],
    component_defs: &[AndroidComponentDef],
    resource_defs: &[ResourceDef],
    resource_index: &HashMap<(String, String), String>,
    gradle_modules: &[GradleModuleDef],
    gradle_dependencies: &HashMap<String, GradleDependencyDef>,
    gradle_dependency_order: &[String],
    gradle_dep_edges: &[(String, String, String)],
    external_class_keys: &[String],
    external_classes: &HashMap<String, ExternalClass>,
    class_index: &ClassIndex,
    function_index: &FunctionIndex,
    file_package_by_path: &HashMap<String, Option<String>>,
    event_map: Option<&Value>,
    root: &Path,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Result<(), String> {
    let normalized_project_id = cortex_graph_writer::project_scope::project_id_lookup_key(Some(project_id))
        .unwrap_or_else(|| project_id.to_string());

    // ── Index bổ sung từ index_payloads (composable) ─────────────────────────
    let mut composable_by_name: HashMap<String, Vec<String>> = HashMap::new();
    let mut composable_by_qualified: HashMap<String, String> = HashMap::new();
    let mut composable_by_class_and_name: HashMap<(String, String), Vec<String>> = HashMap::new();
    let mut composable_by_file_name: HashMap<String, HashMap<String, Vec<String>>> = HashMap::new();
    let mut composable_by_file_qualified: HashMap<String, HashMap<String, String>> = HashMap::new();
    let mut composable_qualified_names: Vec<String> = Vec::new();

    for payload in index_payloads {
        let file_path = payload
            .file_def
            .as_ref()
            .map(|f| f.file_path.clone())
            .unwrap_or_default();
        for func in &payload.functions {
            if extract_android_annotations(&func.code).contains(&"Composable".to_string()) {
                composable_by_name
                    .entry(func.name.clone())
                    .or_default()
                    .push(func.symbol_id.clone());
                composable_by_qualified
                    .insert(func.qualified_name.clone(), func.symbol_id.clone());
                composable_qualified_names.push(func.qualified_name.clone());
                if let Some(class_name) = &func.class_name {
                    composable_by_class_and_name
                        .entry((class_name.clone(), func.name.clone()))
                        .or_default()
                        .push(func.symbol_id.clone());
                }
                if !file_path.is_empty() {
                    composable_by_file_name
                        .entry(file_path.clone())
                        .or_default()
                        .entry(func.name.clone())
                        .or_default()
                        .push(func.symbol_id.clone());
                    composable_by_file_qualified
                        .entry(file_path.clone())
                        .or_default()
                        .insert(func.qualified_name.clone(), func.symbol_id.clone());
                }
            }
        }
    }

    // ── Node rows ───────────────────────────────────────────────────────────
    let mut node_rows: Vec<(&'static str, Vec<Row>)> =
        NODE_QUERIES.iter().map(|(label, _)| (*label, Vec::new())).collect();

    fn bucket<'a>(rows: &'a mut [(&'static str, Vec<Row>)], label: &str) -> &'a mut Vec<Row> {
        rows.iter_mut()
            .find(|(name, _)| *name == label)
            .map(|(_, bucket)| bucket)
            .expect("known label")
    }

    {
        let projects = bucket(&mut node_rows, "projects");
        let mut row = Row::new();
        row.insert("id".into(), json!(project_id));
        row.insert("name".into(), json!(project_name));
        row.insert("language".into(), json!(language));
        row.insert("repo".into(), json!(repo));
        row.insert("root".into(), json!(root.to_string_lossy()));
        row.insert("build_system".into(), json!(build_system));
        projects.push(row);
    }

    // Event map nodes + relations.
    let mut event_relations: Vec<Row> = Vec::new();
    if let Some(event_map) = event_map {
        let events = event_map
            .get("events")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let events_node_bucket = bucket(&mut node_rows, "events");
        for event in &events {
            let eid = event_id(event)?;
            let event_row = row(vec![
                ("id", json!(eid)),
                ("name", json!(event.get("name").and_then(Value::as_str).unwrap_or(""))),
                ("namespace", json!(event.get("namespace").and_then(Value::as_str).unwrap_or(""))),
                ("version", json!(event.get("version").and_then(Value::as_str).unwrap_or(""))),
                ("payload_schema", json!(event.get("payload_schema").and_then(Value::as_str).unwrap_or(""))),
                ("payload_type", json!(event.get("payload_type").and_then(Value::as_str).unwrap_or(""))),
                ("payload_version", json!(event.get("payload_version").and_then(Value::as_str).unwrap_or(""))),
                ("payload_example", json!(event.get("payload_example").and_then(Value::as_str).unwrap_or(""))),
                ("source", json!(event.get("source").and_then(Value::as_str).unwrap_or(""))),
            ]);
            events_node_bucket.push(event_row);

            for emitter in event.get("emits").and_then(Value::as_array).into_iter().flatten() {
                if let Some(pid) = emitter.get("project_id").and_then(Value::as_str)
                    && pid != project_id
                {
                    continue;
                }
                if let Some(func_id) = resolve_emitter_function(emitter, function_index) {
                    event_relations.push(event_relation_row(
                        "EMITS_EVENT",
                        func_id,
                        eid.clone(),
                        emitter,
                    ));
                }
            }
            for handler in event.get("handles").and_then(Value::as_array).into_iter().flatten() {
                if let Some(pid) = handler.get("project_id").and_then(Value::as_str)
                    && pid != project_id
                {
                    continue;
                }
                if let Some(func_id) = resolve_emitter_function(handler, function_index) {
                    event_relations.push(event_relation_row(
                        "HANDLES_EVENT",
                        func_id,
                        eid.clone(),
                        handler,
                    ));
                }
            }
        }
    }

    // Manifests + components + intent actions.
    let mut intent_action_defs: BTreeSet<String> = BTreeSet::new();
    let mut android_event_relations: Vec<Row> = Vec::new();
    let mut intent_action_to_components: Vec<(String, String)> = Vec::new();
    for manifest_def in manifest_defs {
        let manifests = bucket(&mut node_rows, "android_manifests");
        manifests.push(row(vec![
            ("id", json!(manifest_def.symbol_id)),
            ("package_name", json!(manifest_def.package_name)),
            ("file_path", json!(manifest_def.file_path)),
            ("path", json!(manifest_def.file_path)),
            ("start_line", json!(manifest_def.start_line)),
            ("end_line", json!(manifest_def.end_line)),
            ("code", json!(manifest_def.code)),
            ("summary", json!(manifest_def.summary)),
            ("note", json!(manifest_def.note)),
            ("project_id", json!(project_id)),
            ("project_name", json!(project_name)),
            ("language", json!(language)),
            ("repo", json!(repo)),
            ("build_system", json!(build_system)),
        ]));
    }
    for component_def in component_defs {
        let components = bucket(&mut node_rows, "android_components");
        components.push(component_row(component_def, project_id, project_name, language, repo, build_system));
        for action in &component_def.intent_actions {
            if component_def.component_type == "receiver" {
                intent_action_to_components.push((action.clone(), component_def.symbol_id.clone()));
            }
            let action_id = common::intent_action_symbol_id(action);
            if !intent_action_defs.contains(&action_id) {
                intent_action_defs.insert(action_id.clone());
                let actions = bucket(&mut node_rows, "android_intent_actions");
                actions.push(intent_action_row(&action_id, action, project_id, project_name, language, repo, build_system));
            }
            android_event_relations.push(row(vec![
                ("source_label", json!("AndroidComponent")),
                ("source_id", json!(component_def.symbol_id)),
                ("target_label", json!("AndroidIntentAction")),
                ("target_id", json!(action_id)),
                ("rel_type", json!("DECLARES_INTENT_ACTION")),
                ("props", Value::Object(Row::new())),
            ]));
        }
    }

    // Resources (resource_defs, dedup theo seen_resources).
    let mut seen_resources: BTreeSet<String> = BTreeSet::new();
    for resource_def in resource_defs {
        if !seen_resources.insert(resource_def.symbol_id.clone()) {
            continue;
        }
        let resources = bucket(&mut node_rows, "android_resources");
        resources.push(row(vec![
            ("id", json!(resource_def.symbol_id)),
            ("name", json!(resource_def.name)),
            ("res_type", json!(resource_def.res_type)),
            ("file_path", json!(resource_def.file_path)),
            ("qualifier", json!(resource_def.qualifier)),
            ("summary", json!(resource_def.summary)),
            ("note", json!(resource_def.note)),
            ("project_id", json!(project_id)),
            ("project_name", json!(project_name)),
            ("language", json!(language)),
            ("repo", json!(repo)),
            ("build_system", json!(build_system)),
        ]));
    }

    // Gradle.
    for module_def in gradle_modules {
        let modules = bucket(&mut node_rows, "gradle_modules");
        modules.push(row(vec![
            ("id", json!(module_def.symbol_id)),
            ("name", json!(module_def.name)),
            ("module_path", json!(module_def.module_path)),
            ("module_type", json!(module_def.module_type)),
            ("namespace", json!(module_def.namespace)),
            ("application_id", json!(module_def.application_id)),
            ("file_path", json!(module_def.file_path)),
            ("summary", json!("")),
            ("note", json!("")),
            ("project_id", json!(project_id)),
            ("project_name", json!(project_name)),
            ("language", json!(language)),
            ("repo", json!(repo)),
            ("build_system", json!(build_system)),
        ]));
    }
    // Python: for dependency_def in gradle_dependencies.values() — dict giữ
    // insertion order (caller truyền thứ tự chèn).
    for dep_id in gradle_dependency_order {
        let Some(dep) = gradle_dependencies.get(dep_id) else {
            continue;
        };
        let deps = bucket(&mut node_rows, "gradle_dependencies");
        deps.push(row(vec![
            ("id", json!(dep.symbol_id)),
            ("coordinate", json!(dep.coordinate)),
            ("group", json!(dep.group)),
            ("artifact", json!(dep.artifact)),
            ("version", json!(dep.version)),
            ("summary", json!("")),
            ("note", json!("")),
            ("project_id", json!(project_id)),
            ("project_name", json!(project_name)),
            ("language", json!(language)),
            ("repo", json!(repo)),
            ("build_system", json!(build_system)),
        ]));
    }

    // ── Per selected payload ────────────────────────────────────────────────
    let mut seen_packages: BTreeSet<String> = BTreeSet::new();
    let mut seen_namespaces: BTreeSet<String> = BTreeSet::new();
    let mut resource_refs_by_file: Vec<(String, Vec<String>)> = Vec::new();
    let mut annotation_defs: BTreeSet<String> = BTreeSet::new();
    let mut annotation_relations: Vec<(String, String, String)> = Vec::new(); // (label, source, target)
    let mut nav_route_defs: BTreeSet<String> = BTreeSet::new();
    let mut nav_routes_by_file: Vec<(String, Vec<String>)> = Vec::new();
    let mut nav_start_routes_by_file: Vec<(String, Vec<String>)> = Vec::new();
    let mut nav_route_targets_by_file: RouteTargetsByFile = Vec::new();
    let mut handler_message_defs: BTreeSet<String> = BTreeSet::new();
    let mut nav_route_node_rows: Vec<Row> = Vec::new();

    for payload in selected_payloads {
        let file_def = payload.file_def.as_ref().ok_or("payload missing file_def")?;
        let normalized_file_path = common::normalize_rel_path(&file_def.file_path);
        {
            let files = bucket(&mut node_rows, "files");
            let mut row = Row::new();
            row.insert("id".into(), json!(normalized_file_path));
            row.insert("path".into(), json!(normalized_file_path));
            row.insert("package_name".into(), json!(file_def.package_name));
            row.insert("start_line".into(), json!(file_def.start_line));
            row.insert("end_line".into(), json!(file_def.end_line));
            row.insert("code".into(), json!(file_def.code));
            row.insert("comment".into(), json!(file_def.comment));
            row.insert("summary".into(), json!(file_def.summary));
            row.insert("note".into(), json!(file_def.note));
            row.insert("project_id".into(), json!(project_id));
            row.insert("project_id_normalized".into(), json!(normalized_project_id));
            row.insert("project_name".into(), json!(project_name));
            row.insert("language".into(), json!(language));
            row.insert("repo".into(), json!(repo));
            row.insert("build_system".into(), json!(build_system));
            files.push(row);
        }
        let file_id = &file_def.file_path;

        // Android events per file.
        for event in &payload.android_events {
            let function_id = &event.function_id;
            for action in &event.actions {
                let action_id = common::intent_action_symbol_id(action);
                if !intent_action_defs.contains(&action_id) {
                    intent_action_defs.insert(action_id.clone());
                    let actions = bucket(&mut node_rows, "android_intent_actions");
                    actions.push(intent_action_row(&action_id, action, project_id, project_name, language, repo, build_system));
                }
                let rel_type = match event.event_type.as_str() {
                    "send_broadcast" => "SENDS_BROADCAST",
                    "register_receiver" => "REGISTERS_RECEIVER",
                    _ => "STARTS_INTENT",
                };
                let mut props = Row::new();
                props.insert("event_type".into(), json!(event.event_type));
                android_event_relations.push(row(vec![
                    ("source_label", json!("Function")),
                    ("source_id", json!(function_id)),
                    ("target_label", json!("AndroidIntentAction")),
                    ("target_id", json!(action_id)),
                    ("rel_type", json!(rel_type)),
                    ("props", Value::Object(props)),
                ]));
            }
            for token in &event.tokens {
                let message_id = common::handler_message_symbol_id(token);
                if !handler_message_defs.contains(&message_id) {
                    handler_message_defs.insert(message_id.clone());
                    let messages = bucket(&mut node_rows, "android_handler_messages");
                    messages.push(row(vec![
                        ("id", json!(message_id)),
                        ("token", json!(token)),
                        ("summary", json!("")),
                        ("note", json!("")),
                        ("project_id", json!(project_id)),
                        ("project_name", json!(project_name)),
                        ("language", json!(language)),
                        ("repo", json!(repo)),
                        ("build_system", json!(build_system)),
                    ]));
                }
                let mut props = Row::new();
                props.insert("event_type".into(), json!(event.event_type));
                android_event_relations.push(row(vec![
                    ("source_label", json!("Function")),
                    ("source_id", json!(function_id)),
                    ("target_label", json!("AndroidHandlerMessage")),
                    ("target_id", json!(message_id)),
                    ("rel_type", json!("SENDS_HANDLER_MESSAGE")),
                    ("props", Value::Object(props)),
                ]));
            }
            for target in &event.targets {
                let mut props = Row::new();
                props.insert("event_type".into(), json!(event.event_type));
                props.insert("file_path".into(), json!(file_id));
                android_event_relations.push(row(vec![
                    ("source_label", json!("Function")),
                    ("source_id", json!(function_id)),
                    ("target_label", json!("Class")),
                    ("target_id", json!(target)),
                    ("rel_type", json!("STARTS_COMPONENT")),
                    ("props", Value::Object(props)),
                ]));
            }
            if let Some(receiver_target) = &event.receiver {
                let mut props = Row::new();
                props.insert("event_type".into(), json!(event.event_type));
                props.insert("file_path".into(), json!(file_id));
                android_event_relations.push(row(vec![
                    ("source_label", json!("Function")),
                    ("source_id", json!(function_id)),
                    ("target_label", json!("Class")),
                    ("target_id", json!(receiver_target)),
                    ("rel_type", json!("REGISTERS_RECEIVER")),
                    ("props", Value::Object(props)),
                ]));
            }
        }

        // Compose routes.
        let mut nav_routes = payload.compose_routes.routes.clone();
        let mut nav_start_routes = payload.compose_routes.start_routes.clone();
        let mut nav_route_targets_payload = payload.compose_routes.route_targets.clone();
        if nav_routes.is_empty()
            && nav_start_routes.is_empty()
            && nav_route_targets_payload.is_empty()
        {
            let file_code = &file_def.code;
            let (routes, start_routes, targets) = scan_fallback_compose(file_code);
            nav_routes = routes;
            nav_start_routes = start_routes;
            nav_route_targets_payload = targets;
        }
        let nav_route_targets: Vec<(String, Vec<String>)> = nav_route_targets_payload
            .iter()
            .filter(|(route, _)| !route.is_empty())
            .cloned()
            .collect();

        if !nav_routes.is_empty() {
            ensure_entry(&mut nav_routes_by_file, file_id);
        }
        for route in &nav_routes {
            let route_id = common::nav_route_symbol_id(route);
            file_rows_push(&mut nav_routes_by_file, file_id, route_id.clone());
            if !nav_route_defs.contains(&route_id) {
                nav_route_defs.insert(route_id.clone());
                nav_route_node_rows.push(nav_route_row(
                    &route_id,
                    route,
                    file_id,
                    project_id,
                    project_name,
                    language,
                    repo,
                    build_system,
                ));
            }
        }
        if !nav_start_routes.is_empty() {
            ensure_entry(&mut nav_start_routes_by_file, file_id);
        }
        for route in &nav_start_routes {
            let route_id = common::nav_route_symbol_id(route);
            file_rows_push(&mut nav_start_routes_by_file, file_id, route_id.clone());
            if !nav_route_defs.contains(&route_id) {
                nav_route_defs.insert(route_id.clone());
            }
        }
        if !nav_route_targets.is_empty() {
            ensure_targets_entry(&mut nav_route_targets_by_file, file_id);
        }
        for (route, targets) in &nav_route_targets {
            let route_id = common::nav_route_symbol_id(route);
            file_targets_push(&mut nav_route_targets_by_file, file_id, (route_id.clone(), targets.clone()));
            if !nav_route_defs.contains(&route_id) {
                nav_route_defs.insert(route_id.clone());
                nav_route_node_rows.push(nav_route_row(
                    &route_id,
                    route,
                    file_id,
                    project_id,
                    project_name,
                    language,
                    repo,
                    build_system,
                ));
            }
        }

        // Resource refs từ code (regex broken ⇒ thường rỗng — port đúng path).
        let resource_refs = extract_resource_refs(&file_def.code);
        if !resource_refs.is_empty() {
            ensure_entry(&mut resource_refs_by_file, file_id);
        }
        for (res_type, name) in &resource_refs {
            let key = (res_type.clone(), name.clone());
            let resource_id = match resource_index.get(&key) {
                Some(id) => id.clone(),
                None => {
                    let id = common::resource_symbol_id(res_type, name);
                    if !seen_resources.contains(&id) {
                        seen_resources.insert(id.clone());
                        let resources = bucket(&mut node_rows, "android_resources");
                        resources.push(row(vec![
                            ("id", json!(id)),
                            ("name", json!(name)),
                            ("res_type", json!(res_type)),
                            ("file_path", json!("")),
                            ("qualifier", json!("")),
                            ("summary", json!("")),
                            ("note", json!("")),
                            ("project_id", json!(project_id)),
                            ("project_name", json!(project_name)),
                            ("language", json!(language)),
                            ("repo", json!(repo)),
                            ("build_system", json!(build_system)),
                        ]));
                    }
                    id
                }
            };
            if !resource_refs.is_empty() {
                file_rows_push(&mut resource_refs_by_file, file_id, resource_id);
            }
        }

        // Package + Namespace.
        if let Some(package_def) = &payload.package_def {
            let package_name = &package_def.name;
            if !seen_packages.contains(package_name) {
                seen_packages.insert(package_name.clone());
                let packages = bucket(&mut node_rows, "packages");
                packages.push(row(vec![
                    ("id", json!(package_def.name)),
                    ("name", json!(package_def.name)),
                    ("start_line", json!(package_def.start_line)),
                    ("end_line", json!(package_def.end_line)),
                    ("code", json!(package_def.code)),
                    ("comment", json!(package_def.comment)),
                    ("summary", json!(package_def.summary)),
                    ("note", json!(package_def.note)),
                    ("project_id", json!(project_id)),
                    ("project_name", json!(project_name)),
                    ("language", json!(language)),
                    ("repo", json!(repo)),
                    ("build_system", json!(build_system)),
                ]));
            }
            let namespace_id = format!("namespace::{package_name}");
            if !seen_namespaces.contains(&namespace_id) {
                seen_namespaces.insert(namespace_id.clone());
                let namespace_summary = package_def.comment.clone();
                let namespace_note = crate::kotlinparse::build_note(
                    &package_def.code,
                    &package_def.comment,
                    &namespace_summary,
                );
                let namespaces = bucket(&mut node_rows, "namespaces");
                namespaces.push(row(vec![
                    ("id", json!(namespace_id)),
                    ("name", json!(package_name)),
                    ("qualified_name", json!(package_name)),
                    ("file_path", json!(file_def.file_path)),
                    ("start_line", json!(package_def.start_line)),
                    ("end_line", json!(package_def.end_line)),
                    ("code", json!(package_def.code)),
                    ("comment", json!(package_def.comment)),
                    ("summary", json!(namespace_summary)),
                    ("note", json!(namespace_note)),
                    ("project_id", json!(project_id)),
                    ("project_name", json!(project_name)),
                    ("language", json!(language)),
                    ("repo", json!(repo)),
                    ("build_system", json!(build_system)),
                ]));
            }
        }

        // Classes (+ annotations).
        for class_def in &payload.classes {
            let classes = bucket(&mut node_rows, "classes");
            classes.push(row(vec![
                ("id", json!(class_def.symbol_id)),
                ("name", json!(class_def.name)),
                ("qualified_name", json!(class_def.qualified_name)),
                ("kind", json!(class_def.kind)),
                ("package_name", json!(class_def.package_name)),
                ("file_path", json!(class_def.file_path)),
                ("start_line", json!(class_def.start_line)),
                ("end_line", json!(class_def.end_line)),
                ("code", json!(class_def.code)),
                ("comment", json!(class_def.comment)),
                ("summary", json!(class_def.summary)),
                ("note", json!(class_def.note)),
                ("visibility", json!(class_def.visibility)),
                ("is_public_api", json!(class_def.is_public_api)),
                ("visibility_source", json!(class_def.visibility_source)),
                ("export_evidence", json!(class_def.export_evidence)),
                ("signature", json!(class_def.signature)),
                ("project_id", json!(project_id)),
                ("project_name", json!(project_name)),
                ("language", json!(language)),
                ("repo", json!(repo)),
                ("build_system", json!(build_system)),
            ]));
            for annotation in extract_android_annotations(&class_def.code) {
                let annotation_id = common::annotation_symbol_id(&annotation);
                if !annotation_defs.contains(&annotation_id) {
                    annotation_defs.insert(annotation_id.clone());
                    let annotations = bucket(&mut node_rows, "android_annotations");
                    annotations.push(annotation_row(&annotation_id, &annotation, project_id, project_name, language, repo, build_system));
                }
                annotation_relations.push(("Class".to_string(), class_def.symbol_id.clone(), annotation_id));
            }
        }

        // Function types.
        for func_type in &payload.function_types {
            let types = bucket(&mut node_rows, "function_types");
            types.push(function_type_row(func_type, project_id, project_name, language, repo, build_system));
        }

        // Functions (+ annotations).
        for func in &payload.functions {
            let functions = bucket(&mut node_rows, "functions");
            functions.push(row(vec![
                ("id", json!(func.symbol_id)),
                ("name", json!(func.name)),
                ("qualified_name", json!(func.qualified_name)),
                ("kind", json!(func.kind)),
                ("class_name", json!(func.class_name)),
                ("package_name", json!(func.package_name)),
                ("file_path", json!(func.file_path)),
                ("start_line", json!(func.start_line)),
                ("end_line", json!(func.end_line)),
                ("arity", json!(func.arity)),
                ("code", json!(func.code)),
                ("comment", json!(func.comment)),
                ("summary", json!(func.summary)),
                ("note", json!(func.note)),
                ("visibility", json!(func.visibility)),
                ("is_public_api", json!(func.is_public_api)),
                ("visibility_source", json!(func.visibility_source)),
                ("export_evidence", json!(func.export_evidence)),
                ("signature", json!(func.signature)),
                ("project_id", json!(project_id)),
                ("project_name", json!(project_name)),
                ("language", json!(language)),
                ("repo", json!(repo)),
                ("build_system", json!(build_system)),
            ]));
            for annotation in extract_android_annotations(&func.code) {
                let annotation_id = common::annotation_symbol_id(&annotation);
                if !annotation_defs.contains(&annotation_id) {
                    annotation_defs.insert(annotation_id.clone());
                    let annotations = bucket(&mut node_rows, "android_annotations");
                    annotations.push(annotation_row(&annotation_id, &annotation, project_id, project_name, language, repo, build_system));
                }
                annotation_relations.push(("Function".to_string(), func.symbol_id.clone(), annotation_id));
            }
        }
    }

    // Nav route node rows (giữ thứ tự append riêng như các add_node_row).
    for row in nav_route_node_rows {
        let routes = bucket(&mut node_rows, "android_nav_routes");
        routes.push(row);
    }

    // External classes.
    for key in external_class_keys {
        let Some(class_def) = external_classes.get(key) else {
            continue;
        };
        let classes = bucket(&mut node_rows, "classes");
        classes.push(row(vec![
            ("id", json!(class_def.symbol_id)),
            ("name", json!(class_def.name)),
            ("qualified_name", json!(class_def.symbol_id)),
            ("kind", json!("external")),
            ("package_name", Value::Null),
            ("file_path", json!("")),
            ("start_line", json!(0)),
            ("end_line", json!(0)),
            ("code", json!("")),
            ("comment", json!("")),
            ("summary", json!("")),
            ("note", json!("")),
            ("visibility", json!("unknown")),
            ("is_public_api", json!(false)),
            ("visibility_source", json!("source-modifier")),
            ("export_evidence", json!("")),
            ("signature", json!("")),
            ("project_id", json!(project_id)),
            ("project_name", json!(project_name)),
            ("language", json!(language)),
            ("repo", json!(repo)),
            ("build_system", json!(build_system)),
        ]));
    }

    // Directory tree.
    let file_paths_for_tree: Vec<String> = bucket(&mut node_rows, "files")
        .iter()
        .filter_map(|row| row.get("path").and_then(Value::as_str).map(str::to_string))
        .filter(|p| !p.is_empty())
        .collect();
    let skip = |name: &str| crate::scan::is_skipped_android_name(name);
    let directory_paths_for_tree = common::scan_android_directory_paths(root, &skip);
    let (directory_rows, directory_relations) = common::build_directory_nodes_and_relations(
        &file_paths_for_tree,
        &directory_paths_for_tree,
        project_id,
        project_name,
        language,
        repo,
        build_system,
    );
    {
        let directories = bucket(&mut node_rows, "directories");
        directories.extend(directory_rows);
    }

    // ── Relations ───────────────────────────────────────────────────────────
    let mut all_relations: Vec<Row> = Vec::new();
    let mut seen_project_resources: BTreeSet<String> = BTreeSet::new();
    let mut all_calls: Vec<Row> = Vec::new();

    let add_relation = |all_relations: &mut Vec<Row>,
                            source_label: &str,
                            target_label: &str,
                            rel_type: &str,
                            source_id: &str,
                            target_id: &str,
                            props: Row| {
        if !ALLOWED_REL_TYPES.contains(&rel_type) {
            panic!("Unsupported relation type: {rel_type}");
        }
        all_relations.push(row(vec![
            ("source_id", json!(source_id)),
            ("source_label", json!(source_label)),
            ("target_id", json!(target_id)),
            ("target_label", json!(target_label)),
            ("rel_type", json!(rel_type)),
            ("properties", Value::Object(props)),
        ]));
    };

    let directory_ids: BTreeSet<String> = bucket(&mut node_rows, "directories")
        .iter()
        .filter_map(|row| row.get("id").and_then(Value::as_str).map(str::to_string))
        .collect();
    for relation in &directory_relations {
        let source_id = relation
            .get("source_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let target_id = relation
            .get("target_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let rel_type = relation
            .get("rel_type")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let props = relation
            .get("properties")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        let source_label = if directory_ids.contains(&source_id) {
            "Directory"
        } else {
            "Project"
        };
        let target_label = if directory_ids.contains(&target_id) {
            "Directory"
        } else {
            "File"
        };
        add_relation(
            &mut all_relations,
            source_label,
            target_label,
            &rel_type,
            &source_id,
            &target_id,
            props,
        );
    }

    for event_rel in &event_relations {
        let rel_type = event_rel
            .get("rel_type")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let source_id = event_rel
            .get("source_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let target_id = event_rel
            .get("target_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let props = event_rel
            .get("props")
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default();
        add_relation(
            &mut all_relations,
            "Function",
            "Event",
            &rel_type,
            &source_id,
            &target_id,
            props,
        );
    }

    for manifest_def in manifest_defs {
        add_relation(
            &mut all_relations,
            "Project",
            "AndroidManifest",
            "CONTAINS",
            project_id,
            &manifest_def.symbol_id,
            Row::new(),
        );
    }
    for resource_def in resource_defs {
        add_relation(
            &mut all_relations,
            "Project",
            "AndroidResource",
            "CONTAINS",
            project_id,
            &resource_def.symbol_id,
            Row::new(),
        );
    }
    for module_def in gradle_modules {
        add_relation(
            &mut all_relations,
            "Project",
            "GradleModule",
            "CONTAINS",
            project_id,
            &module_def.symbol_id,
            Row::new(),
        );
    }
    for dep_id in gradle_dependency_order {
        if let Some(dep) = gradle_dependencies.get(dep_id) {
            add_relation(
                &mut all_relations,
                "Project",
                "GradleDependency",
                "CONTAINS",
                project_id,
                &dep.symbol_id,
                Row::new(),
            );
        }
    }
    for (action, component_id) in &intent_action_to_components {
        let action_id = common::intent_action_symbol_id(action);
        add_relation(
            &mut all_relations,
            "AndroidIntentAction",
            "AndroidComponent",
            "ACTION_TARGETS_COMPONENT",
            &action_id,
            component_id,
            Row::new(),
        );
    }
    for event in &android_event_relations {
        let rel_type = event
            .get("rel_type")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        if rel_type.is_empty() {
            continue;
        }
        let source_label = event
            .get("source_label")
            .and_then(Value::as_str)
            .unwrap_or("Function");
        let target_label = event
            .get("target_label")
            .and_then(Value::as_str)
            .unwrap_or("AndroidIntentAction");
        let mut target_id = event
            .get("target_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        if (rel_type == "STARTS_COMPONENT" || rel_type == "REGISTERS_RECEIVER")
            && target_label == "Class"
        {
            let props = event.get("props").and_then(Value::as_object).cloned().unwrap_or_default();
            let file_path = props
                .get("file_path")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();
            match resolve_class_id(
                &target_id,
                Some(&file_path),
                class_index,
                file_package_by_path,
            ) {
                Some(resolved) => target_id = resolved,
                None => continue,
            }
        }
        let props = event.get("props").and_then(Value::as_object).cloned().unwrap_or_default();
        let source_id = event
            .get("source_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        add_relation(
            &mut all_relations,
            source_label,
            target_label,
            &rel_type,
            &source_id,
            &target_id,
            props,
        );
    }
    for (source_label, source_id, annotation_id) in &annotation_relations {
        add_relation(
            &mut all_relations,
            source_label,
            "AndroidAnnotation",
            "ANNOTATED_WITH",
            source_id,
            annotation_id,
            Row::new(),
        );
    }
    for component_def in component_defs {
        add_relation(
            &mut all_relations,
            "Project",
            "AndroidComponent",
            "CONTAINS",
            project_id,
            &component_def.symbol_id,
            Row::new(),
        );
        if component_def.file_path.ends_with("AndroidManifest.xml") {
            let manifest_id = common::manifest_symbol_id(&component_def.file_path);
            add_relation(
                &mut all_relations,
                "AndroidManifest",
                "AndroidComponent",
                "CONTAINS",
                &manifest_id,
                &component_def.symbol_id,
                Row::new(),
            );
        }
        if let Some(class_name) = &component_def.class_name {
            let mut class_id_resolved = class_index.by_qualified.get(class_name).cloned();
            if class_id_resolved.is_none() {
                class_id_resolved = external_classes
                    .get(class_name)
                    .map(|c| c.symbol_id.clone());
            }
            if class_id_resolved.is_none() {
                let simple = class_name.rsplit('.').next().unwrap_or(class_name);
                class_id_resolved = class_index
                    .by_name
                    .get(simple)
                    .and_then(|candidates| candidates.first().cloned());
            }
            if let Some(class_id_resolved) = class_id_resolved {
                add_relation(
                    &mut all_relations,
                    "AndroidComponent",
                    "Class",
                    "DECLARES_COMPONENT",
                    &component_def.symbol_id,
                    &class_id_resolved,
                    Row::new(),
                );
            }
        }
    }
    for (module_id, dep_id, config) in gradle_dep_edges {
        let mut props = Row::new();
        props.insert("configuration".into(), json!(config));
        add_relation(
            &mut all_relations,
            "GradleModule",
            "GradleDependency",
            "DEPENDS_ON",
            module_id,
            dep_id,
            props,
        );
    }

    for payload in selected_payloads {
        let file_def = payload.file_def.as_ref().ok_or("payload missing file_def")?;
        let file_id = &file_def.file_path;
        add_relation(
            &mut all_relations,
            "Project",
            "File",
            "CONTAINS",
            project_id,
            file_id,
            Row::new(),
        );
        for resource_id in file_rows_get(&resource_refs_by_file, file_id) {
            if !seen_project_resources.contains(&resource_id) {
                seen_project_resources.insert(resource_id.clone());
                add_relation(
                    &mut all_relations,
                    "Project",
                    "AndroidResource",
                    "CONTAINS",
                    project_id,
                    &resource_id,
                    Row::new(),
                );
            }
            add_relation(
                &mut all_relations,
                "File",
                "AndroidResource",
                "USES_RESOURCE",
                file_id,
                &resource_id,
                Row::new(),
            );
        }
        for route_id in file_rows_get(&nav_routes_by_file, file_id) {
            add_relation(
                &mut all_relations,
                "File",
                "AndroidNavRoute",
                "DECLARES_ROUTE",
                file_id,
                &route_id,
                Row::new(),
            );
        }
        for route_id in file_rows_get(&nav_start_routes_by_file, file_id) {
            add_relation(
                &mut all_relations,
                "File",
                "AndroidNavRoute",
                "STARTS_WITH_ROUTE",
                file_id,
                &route_id,
                Row::new(),
            );
        }
        for (route_id, target_names) in file_targets_get(&nav_route_targets_by_file, file_id) {
            let mut target_id: Option<String> = None;
            for target_name in target_names {
                target_id = resolve_composable_name(
                    &target_name,
                    Some(file_id),
                    &composable_by_name,
                    &composable_by_qualified,
                    &composable_by_class_and_name,
                    &composable_by_file_name,
                    &composable_by_file_qualified,
                    &composable_qualified_names,
                    file_package_by_path,
                );
                if target_id.is_some() {
                    break;
                }
                target_id = function_index.resolve_function_name(&target_name);
                if target_id.is_some() {
                    break;
                }
            }
            if let Some(target_id) = target_id {
                add_relation(
                    &mut all_relations,
                    "AndroidNavRoute",
                    "Function",
                    "ROUTE_CALLS",
                    &route_id,
                    &target_id,
                    Row::new(),
                );
            }
        }
        if let Some(package_name) = &file_def.package_name {
            let namespace_id = format!("namespace::{package_name}");
            add_relation(
                &mut all_relations,
                "Package",
                "File",
                "CONTAINS",
                package_name,
                file_id,
                Row::new(),
            );
            add_relation(
                &mut all_relations,
                "Namespace",
                "File",
                "CONTAINS",
                &namespace_id,
                file_id,
                Row::new(),
            );
            add_relation(
                &mut all_relations,
                "Package",
                "Namespace",
                "CONTAINS",
                package_name,
                &namespace_id,
                Row::new(),
            );
        }
        for class_def in &payload.classes {
            if !class_def.file_path.is_empty() {
                add_relation(
                    &mut all_relations,
                    "File",
                    "Class",
                    "CONTAINS",
                    file_id,
                    &class_def.symbol_id,
                    Row::new(),
                );
            }
        }
        for func in &payload.functions {
            add_relation(
                &mut all_relations,
                "File",
                "Function",
                "CONTAINS",
                file_id,
                &func.symbol_id,
                Row::new(),
            );
            if let Some(owner_class) = &func.class_name {
                let owner_id = class_id(func.package_name.as_deref(), owner_class);
                add_relation(
                    &mut all_relations,
                    "Class",
                    "Function",
                    "DECLARES",
                    &owner_id,
                    &func.symbol_id,
                    Row::new(),
                );
            }
        }
        for edge in &payload.type_edges {
            let target_name = &edge.target_name;
            let mut target_id = class_index.resolve_target(target_name, edge.source_package.as_deref());
            if target_id.is_none() {
                target_id = Some(target_name.clone());
            }
            add_relation(
                &mut all_relations,
                "Class",
                "Class",
                &edge.rel_type,
                &edge.source_id,
                &target_id.unwrap(),
                Row::new(),
            );
        }
        for rel in &payload.relations {
            add_relation(
                &mut all_relations,
                &rel.source_label,
                &rel.target_label,
                &rel.rel_type,
                &rel.source_id,
                &rel.target_id,
                rel.properties.clone(),
            );
        }
        for call in &payload.calls {
            let callee_id = call
                .callee_id
                .clone()
                .or_else(|| function_index.resolve_callee_id(call));
            let Some(callee_id) = callee_id else {
                continue;
            };
            let call_file = if call.caller_file.is_empty() {
                file_id.clone()
            } else {
                call.caller_file.clone()
            };
            let call_line = call.call_line;
            let call_column = call.call_column;
            let call_type = &call.call_type;
            let site_id = call_site_id(
                &call.caller_id,
                &callee_id,
                &call_file,
                call_line,
                call_column,
                call_type,
            );
            let mut row = Row::new();
            row.insert("caller_id".into(), json!(call.caller_id));
            row.insert("callee_id".into(), json!(callee_id));
            row.insert("site_id".into(), json!(site_id));
            row.insert("project_id".into(), json!(project_id));
            let mut props = Row::new();
            props.insert("file_path".into(), json!(call_file));
            props.insert("line".into(), json!(call_line));
            props.insert("column".into(), json!(call_column));
            props.insert("call_type".into(), json!(call_type));
            props.insert("callee_name".into(), json!(call.callee_name));
            props.insert("caller_class".into(), json!(call.caller_class.clone().unwrap_or_default()));
            props.insert(
                "caller_package".into(),
                json!(call.caller_package.clone().unwrap_or_default()),
            );
            row.insert("props".into(), Value::Object(props));
            all_calls.push(row);
        }
    }

    // ── Write ───────────────────────────────────────────────────────────────
    // Stamp project_id/project_id_normalized defaults rồi write theo thứ tự
    // node_queries.
    for (label, query) in NODE_QUERIES {
        let rows = bucket(&mut node_rows, label);
        if rows.is_empty() {
            continue;
        }
        for row in rows.iter_mut() {
            if !row.contains_key("project_id") || row.get("project_id") == Some(&Value::Null) || row.get("project_id").and_then(Value::as_str).unwrap_or("").is_empty() {
                row.insert("project_id".into(), json!(project_id));
            }
            let has_normalized = row
                .get("project_id_normalized")
                .and_then(Value::as_str)
                .map(|v| !v.is_empty())
                .unwrap_or(false);
            if !has_normalized {
                row.insert("project_id_normalized".into(), json!(normalized_project_id));
            }
        }
        writer
            .write_nodes_batch(label, query, rows)
            .map_err(|e| format!("[graph] write nodes {label}: {e}"))?;
    }

    writer
        .write_relations_typed(&all_relations, Some(project_id))
        .map_err(|e| format!("[graph] write relations: {e}"))?;

    writer
        .write_calls_with_site(&all_calls)
        .map_err(|e| format!("[graph] write calls: {e}"))?;

    Ok(())
}

// ── Helpers nhỏ ─────────────────────────────────────────────────────────────

/// `_collect_kotlin_import_graph` fallback compose (gọi `_parse_compose_routes`
/// của kotlinparse qua scan_fallback — chống circular import).
fn scan_fallback_compose(
    code: &str,
) -> crate::kotlinparse::ComposeTriple {
    crate::kotlinparse::parse_compose_routes(code)
}

fn ensure_entry(map: &mut Vec<(String, Vec<String>)>, key: &str) {
    if !map.iter().any(|(k, _)| k == key) {
        map.push((key.to_string(), Vec::new()));
    }
}

fn ensure_targets_entry(map: &mut RouteTargetsByFile, key: &str) {
    if !map.iter().any(|(k, _)| k == key) {
        map.push((key.to_string(), Vec::new()));
    }
}

fn file_rows_push(map: &mut Vec<(String, Vec<String>)>, key: &str, value: String) {
    match map.iter_mut().find(|(k, _)| k == key) {
        Some((_, bucket)) => bucket.push(value),
        None => map.push((key.to_string(), vec![value])),
    }
}

fn file_targets_push(map: &mut RouteTargetsByFile, key: &str, value: (String, Vec<String>)) {
    match map.iter_mut().find(|(k, _)| k == key) {
        Some((_, bucket)) => bucket.push(value),
        None => map.push((key.to_string(), vec![value])),
    }
}

fn file_rows_get(map: &[(String, Vec<String>)], key: &str) -> Vec<String> {
    map.iter()
        .find(|(k, _)| k == key)
        .map(|(_, bucket)| bucket.clone())
        .unwrap_or_default()
}

fn file_targets_get(map: &[RouteTargetsEntry], key: &str) -> Vec<(String, Vec<String>)> {
    map.iter()
        .find(|(k, _)| k == key)
        .map(|(_, bucket)| bucket.clone())
        .unwrap_or_default()
}

fn intent_action_row(
    action_id: &str,
    action: &str,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    row(vec![
        ("id", json!(action_id)),
        ("action", json!(action)),
        ("summary", json!("")),
        ("note", json!("")),
        ("project_id", json!(project_id)),
        ("project_name", json!(project_name)),
        ("language", json!(language)),
        ("repo", json!(repo)),
        ("build_system", json!(build_system)),
    ])
}

fn annotation_row(
    annotation_id: &str,
    annotation: &str,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    row(vec![
        ("id", json!(annotation_id)),
        ("name", json!(annotation)),
        ("summary", json!("")),
        ("note", json!("")),
        ("project_id", json!(project_id)),
        ("project_name", json!(project_name)),
        ("language", json!(language)),
        ("repo", json!(repo)),
        ("build_system", json!(build_system)),
    ])
}

#[allow(clippy::too_many_arguments)]
fn nav_route_row(
    route_id: &str,
    route: &str,
    file_path: &str,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    row(vec![
        ("id", json!(route_id)),
        ("route", json!(route)),
        ("file_path", json!(file_path)),
        ("summary", json!("")),
        ("note", json!("")),
        ("project_id", json!(project_id)),
        ("project_name", json!(project_name)),
        ("language", json!(language)),
        ("repo", json!(repo)),
        ("build_system", json!(build_system)),
    ])
}

#[allow(clippy::too_many_arguments)]
fn component_row(
    component_def: &AndroidComponentDef,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    row(vec![
        ("id", json!(component_def.symbol_id)),
        ("name", json!(component_def.name)),
        ("component_type", json!(component_def.component_type)),
        ("class_name", json!(component_def.class_name)),
        ("exported", json!(component_def.exported)),
        ("process", json!(component_def.process)),
        ("permission", json!(component_def.permission)),
        ("enabled", json!(component_def.enabled)),
        ("direct_boot_aware", json!(component_def.direct_boot_aware)),
        ("target_activity", json!(component_def.target_activity)),
        ("intent_actions", json!(component_def.intent_actions)),
        ("intent_categories", json!(component_def.intent_categories)),
        ("intent_data", json!(component_def.intent_data)),
        ("file_path", json!(component_def.file_path)),
        ("path", json!(component_def.file_path)),
        ("start_line", json!(component_def.start_line)),
        ("end_line", json!(component_def.end_line)),
        ("code", json!(component_def.code)),
        ("summary", json!(component_def.summary)),
        ("note", json!(component_def.note)),
        ("project_id", json!(project_id)),
        ("project_name", json!(project_name)),
        ("language", json!(language)),
        ("repo", json!(repo)),
        ("build_system", json!(build_system)),
    ])
}

#[allow(clippy::too_many_arguments)]
fn function_type_row(
    func_type: &FunctionTypeDef,
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> Row {
    row(vec![
        ("id", json!(func_type.symbol_id)),
        ("type_signature", json!(func_type.type_signature)),
        ("file_path", json!(func_type.file_path)),
        ("start_line", json!(func_type.start_line)),
        ("end_line", json!(func_type.end_line)),
        ("code", json!(func_type.code)),
        ("project_id", json!(project_id)),
        ("project_name", json!(project_name)),
        ("language", json!(language)),
        ("repo", json!(repo)),
        ("build_system", json!(build_system)),
    ])
}

fn event_relation_row(rel_type: &str, func_id: String, event_id: String, entry: &Value) -> Row {
    let mut props = Row::new();
    props.insert(
        "file_path".into(),
        json!(entry.get("file_path").and_then(Value::as_str).unwrap_or("")),
    );
    props.insert(
        "line".into(),
        json!(entry.get("line").and_then(Value::as_i64).unwrap_or(0)),
    );
    props.insert(
        "column".into(),
        json!(entry.get("column").and_then(Value::as_i64).unwrap_or(0)),
    );
    props.insert(
        "note".into(),
        json!(entry.get("note").and_then(Value::as_str).unwrap_or("")),
    );
    row(vec![
        ("rel_type", json!(rel_type)),
        ("source_id", json!(func_id)),
        ("target_id", json!(event_id)),
        ("props", Value::Object(props)),
    ])
}

/// Resolve emitter/handler function theo function_id → qualified → name.
fn resolve_emitter_function(entry: &Value, function_index: &FunctionIndex) -> Option<String> {
    if let Some(func_id) = entry.get("function_id").and_then(Value::as_str)
        && !func_id.is_empty()
    {
        return Some(func_id.to_string());
    }
    if let Some(qualified) = entry.get("function_qualified").and_then(Value::as_str)
        && !qualified.is_empty()
        && let Some(symbol) = function_index.resolve_function_name(qualified)
    {
        return Some(symbol);
    }
    if let Some(name) = entry.get("function_name").and_then(Value::as_str)
        && !name.is_empty()
        && let Some(symbol) = function_index.resolve_function_name(name)
    {
        return Some(symbol);
    }
    None
}

/// `resolve_class_id`.
fn resolve_class_id(
    name: &str,
    file_path: Option<&str>,
    class_index: &ClassIndex,
    file_package_by_path: &HashMap<String, Option<String>>,
) -> Option<String> {
    if let Some(id) = class_index.by_qualified.get(name) {
        return Some(id.clone());
    }
    if !name.contains('.')
        && let Some(file_path) = file_path
        && let Some(package_name) = file_package_by_path.get(file_path).cloned().flatten()
    {
        let qualified = format!("{package_name}.{name}");
        if let Some(id) = class_index.by_qualified.get(&qualified) {
            return Some(id.clone());
        }
    }
    let simple = name.rsplit('.').next().unwrap_or(name);
    if let Some(candidates) = class_index.by_name.get(simple)
        && let Some(first) = candidates.first()
    {
        return Some(first.clone());
    }
    for qualified in &class_index.qualified_keys {
        if qualified.ends_with(name) {
            return class_index.by_qualified.get(qualified).cloned();
        }
    }
    None
}

/// `resolve_composable_name`.
#[allow(clippy::too_many_arguments)]
fn resolve_composable_name(
    name: &str,
    file_path: Option<&str>,
    by_name: &HashMap<String, Vec<String>>,
    by_qualified: &HashMap<String, String>,
    by_class_and_name: &HashMap<(String, String), Vec<String>>,
    by_file_name: &HashMap<String, HashMap<String, Vec<String>>>,
    by_file_qualified: &HashMap<String, HashMap<String, String>>,
    qualified_names: &[String],
    file_package_by_path: &HashMap<String, Option<String>>,
) -> Option<String> {
    let short_name = name.rsplit('.').next().unwrap_or(name).to_string();
    if let Some(file_path) = file_path {
        if let Some(per_file_qualified) = by_file_qualified.get(file_path)
            && let Some(id) = per_file_qualified.get(name)
        {
            return Some(id.clone());
        }
        if let Some(per_file_name) = by_file_name.get(file_path)
            && let Some(candidates) = per_file_name.get(&short_name)
            && let Some(first) = candidates.first()
        {
            return Some(first.clone());
        }
        if let Some(package_name) = file_package_by_path.get(file_path).cloned().flatten()
            && !name.contains('.')
        {
            let qualified = format!("{package_name}.{name}");
            if let Some(id) = by_qualified.get(&qualified) {
                return Some(id.clone());
            }
        }
    }
    if name.contains('.') {
        let parts: Vec<&str> = name.split('.').collect();
        if parts.len() >= 2 {
            let qualifier = parts[parts.len() - 2];
            if let Some(candidates) = by_class_and_name.get(&(qualifier.to_string(), short_name.clone()))
                && let Some(first) = candidates.first()
            {
                return Some(first.clone());
            }
        }
    }
    if let Some(id) = by_qualified.get(name) {
        return Some(id.clone());
    }
    if let Some(candidates) = by_name.get(&short_name)
        && let Some(first) = candidates.first()
    {
        return Some(first.clone());
    }
    if name.contains('.') {
        for qualified in qualified_names {
            if qualified.ends_with(name)
                && let Some(id) = by_qualified.get(qualified)
            {
                return Some(id.clone());
            }
        }
    }
    None
}

// ── Class index ─────────────────────────────────────────────────────────────

pub struct ClassIndex {
    pub by_qualified: HashMap<String, String>,
    pub by_name: HashMap<String, Vec<String>>,
    /// Insertion-order keys cho vòng `endswith` (dict Python).
    pub qualified_keys: Vec<String>,
}

impl ClassIndex {
    pub fn build(payloads: &[&FilePayload]) -> Self {
        let mut index = ClassIndex {
            by_qualified: HashMap::new(),
            by_name: HashMap::new(),
            qualified_keys: Vec::new(),
        };
        for payload in payloads {
            for class_def in &payload.classes {
                if !index.by_qualified.contains_key(&class_def.qualified_name) {
                    index.qualified_keys.push(class_def.qualified_name.clone());
                }
                index
                    .by_qualified
                    .insert(class_def.qualified_name.clone(), class_def.symbol_id.clone());
                let simple = class_def
                    .name
                    .rsplit('.')
                    .next()
                    .unwrap_or(&class_def.name)
                    .to_string();
                index
                    .by_name
                    .entry(simple)
                    .or_default()
                    .push(class_def.symbol_id.clone());
            }
        }
        index
    }

    /// 3 bước resolve target của type edge (qualified → package-qualified →
    /// by_name[0]).
    pub fn resolve_target(&self, target_name: &str, source_package: Option<&str>) -> Option<String> {
        if target_name.contains('.')
            && let Some(id) = self.by_qualified.get(target_name)
        {
            return Some(id.clone());
        }
        if let Some(package_name) = source_package {
            let qualified = format!("{package_name}.{target_name}");
            if let Some(id) = self.by_qualified.get(&qualified) {
                return Some(id.clone());
            }
        }
        if let Some(candidates) = self.by_name.get(target_name)
            && let Some(first) = candidates.first()
        {
            return Some(first.clone());
        }
        None
    }
}
