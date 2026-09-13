//! Port của `tools/graph/writer/project_topology_writer.py` +
//! `project_topology/models.py::stable_fact_id` (writer half).
//!
//! `ProjectTopologyWriter.write` nhận JSON của `TopologyAnalysisResult`
//! (bên Python sinh bằng `result.to_dict()` rồi ghi ra fixture/pipe) và dựng
//! rows đúng thứ tự Python. Query templates giữ nguyên chữ.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use cortex_graph_core::schema_manifest::validate_cypher_identifier;

use crate::json_row::Row;
use crate::project_scope::project_id_lookup_key;
use crate::store::{GraphStore, StoreError};

/// `stable_fact_id` — `{kind}:{sha256(material)[:24]}`, material =
/// `scope\x1fkind.lower()\x1fpart...`.
pub fn stable_fact_id(project_id: &str, kind: &str, parts: &[&str]) -> Result<String, StoreError> {
    let scope = project_id_lookup_key(Some(project_id))
        .ok_or_else(|| StoreError::Invalid("project_id is required for fact identity".into()))?;
    let mut material = String::new();
    material.push_str(&scope);
    material.push('\x1f');
    material.push_str(kind.trim().to_lowercase().as_str());
    for part in parts {
        material.push('\x1f');
        material.push_str(part);
    }
    let digest = Sha256::digest(material.as_bytes());
    let hex: String = digest
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<Vec<_>>()
        .join("");
    Ok(format!("{kind}:{}", &hex[..24]))
}

const MODULE_QUERY: &str = r#"
UNWIND $rows AS row
MERGE (project:Project {project_id: row.project_id})
SET project.project_id_normalized = row.project_id_normalized
MERGE (module:ProjectModule {id: row.id})
SET module += row, module.topology_owned = true
MERGE (project)-[rel:CONTAINS {topology_owner: 'project_topology'}]->(module)
SET rel.project_id = row.project_id
RETURN count(module) AS count
"#;

const GRADLE_COMPATIBILITY_QUERY: &str = r#"
UNWIND $rows AS row
MATCH (module:ProjectModule {id: row.id})
SET module:GradleModule
RETURN count(module) AS count
"#;

const GRADLE_LEGACY_LINK_QUERY: &str = r#"
UNWIND $rows AS row
MATCH (module:ProjectModule {id: row.id})
MATCH (legacy:GradleModule)
WHERE legacy.id <> module.id
  AND legacy.project_id = row.project_id
  AND legacy.module_path = CASE
    WHEN row.module_path = '.' THEN ':'
    ELSE ':' + replace(row.module_path, '/', ':')
  END
MERGE (legacy)-[rel:SAME_MODULE {
  id: row.id + ':legacy-gradle'
}]->(module)
SET rel.project_id = row.project_id,
    rel.topology_owner = 'project_topology'
RETURN count(legacy) AS count
"#;

const DESCRIPTOR_QUERY: &str = r#"
UNWIND $rows AS row
MATCH (module:ProjectModule {id: row.module_id})
MERGE (descriptor:BuildDescriptor {id: row.id})
SET descriptor += row, descriptor.topology_owned = true
MERGE (module)-[rel:HAS_DESCRIPTOR {id: row.edge_id}]->(descriptor)
SET rel.project_id = row.project_id,
    rel.topology_owner = 'project_topology',
    rel.file_path = row.file_path
RETURN count(descriptor) AS count
"#;

const DEPENDENCY_NODE_QUERY: &str = r#"
UNWIND $rows AS row
MERGE (dependency:Dependency {id: row.id})
SET dependency += row, dependency.topology_owned = true
RETURN count(dependency) AS count
"#;

fn dependency_edge_query(target_label: &str) -> Result<String, StoreError> {
    let label = validate_cypher_identifier(target_label, "dependency target label")
        .map_err(StoreError::Invalid)?;
    if label != "ProjectModule" && label != "Dependency" {
        return Err(StoreError::Invalid(format!(
            "unsupported dependency target label: {label}"
        )));
    }
    Ok(format!(
        r#"
    UNWIND $rows AS row
    MATCH (source:ProjectModule {{id: row.source_id}})
    MATCH (target:{label} {{id: row.target_id}})
    MERGE (source)-[rel:DEPENDS_ON {{id: row.id}}]->(target)
    SET rel += row, rel.topology_owner = 'project_topology'
    RETURN count(rel) AS count
    "#
    ))
}

const ENDPOINT_QUERY: &str = r#"
UNWIND $rows AS row
MATCH (module:ProjectModule {id: row.module_id})
MERGE (endpoint:GrpcEndpoint {id: row.id})
SET endpoint += row, endpoint.topology_owned = true
MERGE (module)-[rel:EXPOSES_ENDPOINT {id: row.edge_id}]->(endpoint)
SET rel.project_id = row.project_id, rel.topology_owner = 'project_topology'
RETURN count(endpoint) AS count
"#;

const GRPC_SERVICE_QUERY: &str = r#"
UNWIND $rows AS row
MATCH (module:ProjectModule {id: row.module_id})
MATCH (endpoint:GrpcEndpoint {id: row.endpoint_id})
MERGE (service:GrpcService {id: row.id})
SET service += row, service.topology_owned = true
MERGE (module)-[declares:DECLARES_SERVICE {id: row.module_edge_id}]->(service)
SET declares.project_id = row.project_id,
    declares.topology_owner = 'project_topology'
MERGE (service)-[rpc:HAS_RPC {id: row.rpc_edge_id}]->(endpoint)
SET rpc.project_id = row.project_id,
    rpc.topology_owner = 'project_topology'
RETURN count(service) AS count
"#;

const FRAMEWORK_QUERY: &str = r#"
UNWIND $rows AS row
MATCH (module:ProjectModule {id: row.module_id})
MERGE (framework:FrameworkInstance {id: row.id})
SET framework += row, framework.topology_owned = true
MERGE (module)-[rel:USES_FRAMEWORK {id: row.edge_id}]->(framework)
SET rel.project_id = row.project_id, rel.topology_owner = 'project_topology'
RETURN count(framework) AS count
"#;

const PUBLIC_API_LINK_QUERY: &str = r#"
UNWIND $rows AS row
MATCH (module:ProjectModule {id: row.module_id})
MATCH (symbol)
WHERE (symbol:Class OR symbol:Function OR symbol:Type OR symbol:Interface)
  AND symbol.project_id_normalized = row.project_id_normalized
  AND coalesce(symbol.is_public_api, false) = true
  AND (
    row.module_path = '.'
    OR symbol.file_path = row.module_path
    OR substring(symbol.file_path, 0, size(row.module_path) + 1) = row.module_path + '/'
  )
OPTIONAL MATCH (more_specific:ProjectModule)
WHERE more_specific.project_id_normalized = row.project_id_normalized
  AND more_specific.id <> module.id
  AND more_specific.module_path <> '.'
  AND size(more_specific.module_path) > size(row.module_path)
  AND substring(symbol.file_path, 0, size(more_specific.module_path) + 1)
      = more_specific.module_path + '/'
WITH row, module, symbol, count(more_specific) AS more_specific_count
WHERE more_specific_count = 0
SET symbol.module_id = row.module_id
MERGE (module)-[rel:EXPOSES_API {
  id: row.module_id + ':EXPOSES_API:' + symbol.id
}]->(symbol)
SET rel.project_id = row.project_id,
    rel.topology_owner = 'project_topology'
RETURN count(symbol) AS count
"#;

const EXISTING_ENDPOINT_LINK_QUERY: &str = r#"
UNWIND $rows AS row
MATCH (module:ProjectModule {id: row.module_id})
MATCH (endpoint)
WHERE (
    endpoint:ApiEndpoint
    OR endpoint:HttpEndpoint
    OR endpoint:Route
    OR endpoint:ControllerAction
    OR endpoint:ServletEndpoint
  )
  AND coalesce(endpoint.topology_owned, false) = false
  AND endpoint.project_id_normalized = row.project_id_normalized
  AND (
    row.module_path = '.'
    OR endpoint.file_path = row.module_path
    OR substring(endpoint.file_path, 0, size(row.module_path) + 1) = row.module_path + '/'
  )
OPTIONAL MATCH (more_specific:ProjectModule)
WHERE more_specific.project_id_normalized = row.project_id_normalized
  AND more_specific.id <> module.id
  AND more_specific.module_path <> '.'
  AND size(more_specific.module_path) > size(row.module_path)
  AND substring(endpoint.file_path, 0, size(more_specific.module_path) + 1)
      = more_specific.module_path + '/'
WITH row, module, endpoint, count(more_specific) AS more_specific_count
WHERE more_specific_count = 0
SET endpoint.module_id = row.module_id
MERGE (module)-[rel:EXPOSES_ENDPOINT {
  id: row.module_id + ':EXPOSES_ENDPOINT:' + endpoint.id
}]->(endpoint)
SET rel.project_id = row.project_id,
    rel.topology_owner = 'project_topology'
RETURN count(endpoint) AS count
"#;

const ANDROID_FACT_LINK_QUERY: &str = r#"
UNWIND $rows AS row
MATCH (module:ProjectModule {id: row.module_id})
MATCH (fact)
WHERE (
    fact:AndroidManifest
    OR fact:AndroidComponent
    OR fact:AndroidResource
  )
  AND (
    fact.project_id_normalized = row.project_id_normalized
    OR toLower(coalesce(fact.project_id, '')) = row.project_id_normalized
  )
  AND (
    row.module_path = '.'
    OR fact.file_path = row.module_path
    OR substring(fact.file_path, 0, size(row.module_path) + 1) = row.module_path + '/'
  )
OPTIONAL MATCH (more_specific:ProjectModule)
WHERE more_specific.project_id_normalized = row.project_id_normalized
  AND more_specific.id <> module.id
  AND more_specific.module_path <> '.'
  AND size(more_specific.module_path) > size(row.module_path)
  AND substring(fact.file_path, 0, size(more_specific.module_path) + 1)
      = more_specific.module_path + '/'
WITH row, module, fact, count(more_specific) AS more_specific_count
WHERE more_specific_count = 0
SET fact.module_id = row.module_id
MERGE (module)-[rel:CONTAINS {
  id: row.module_id + ':CONTAINS:' + fact.id
}]->(fact)
SET rel.project_id = row.project_id,
    rel.topology_owner = 'project_topology'
RETURN count(fact) AS count
"#;

const CLEANUP_PATHS_QUERY: &str = r#"
MATCH (node)
WHERE node.project_id_normalized = $project_id_normalized
  AND node.topology_owned = true
  AND (
    node.file_path IN $paths
    OR node.path IN $paths
    OR any(path IN $paths WHERE
      node.module_path = path OR
      substring(node.module_path, 0, size(path) + 1) = path + '/'
    )
  )
WITH collect(node) AS nodes
FOREACH (node IN nodes | DETACH DELETE node)
RETURN size(nodes) AS count
"#;

const CLEANUP_PROJECT_QUERY: &str = r#"
MATCH (node)
WHERE node.project_id_normalized = $project_id_normalized
  AND node.topology_owned = true
WITH collect(node) AS nodes
FOREACH (node IN nodes | DETACH DELETE node)
RETURN size(nodes) AS count
"#;

/// `_graph_value` — map/tuple-of-non-scalars → canonical JSON string.
fn graph_value(value: &Value) -> Value {
    match value {
        Value::Object(map) => Value::String(canonical_json(map)),
        Value::Array(items) => {
            if items.iter().all(|item| {
                item.is_null()
                    || item.is_string()
                    || item.is_i64()
                    || item.is_u64()
                    || item.is_f64()
                    || item.is_boolean()
            }) {
                value.clone()
            } else {
                // tuple/list không scalar-only: render như JSON array map
                // (Python dumps cả list lồng nhau).
                let rendered: Vec<Value> = items
                    .iter()
                    .map(|item| match item {
                        Value::Object(map) => Value::String(canonical_json(map)),
                        other => other.clone(),
                    })
                    .collect();
                Value::String(
                    serde_json::to_string(&rendered).unwrap_or_else(|_| "[]".to_string()),
                )
            }
        }
        other => other.clone(),
    }
}

/// `json.dumps(value, sort_keys=True, separators=(",", ":"))`.
fn canonical_json(map: &Map<String, Value>) -> String {
    let sorted: BTreeMap<&String, &Value> = map.iter().collect();
    let mut out = String::from("{");
    for (index, (key, value)) in sorted.iter().enumerate() {
        if index > 0 {
            out.push(',');
        }
        let key_json = serde_json::to_string(key).unwrap_or_default();
        out.push_str(&key_json);
        out.push(':');
        match value {
            Value::Object(inner) => out.push_str(&canonical_json(inner)),
            other => out.push_str(&other.to_string()),
        }
    }
    out.push('}');
    out
}

fn graph_row(row: &Row) -> Row {
    row.iter()
        .map(|(key, value)| (key.clone(), graph_value(value)))
        .collect()
}

fn count_from(records: &[Row], fallback: usize) -> i64 {
    records
        .first()
        .and_then(|record| record.get("count"))
        .and_then(|value| {
            value
                .as_i64()
                .or_else(|| value.as_f64().map(|f| f as i64))
        })
        .unwrap_or(fallback as i64)
}

/// Provider-neutral topology writer — additive facts, delete topology-owned.
pub struct ProjectTopologyWriter {
    store: Box<dyn GraphStore>,
    database: Option<String>,
    batch_size: usize,
}

impl ProjectTopologyWriter {
    pub fn new(store: Box<dyn GraphStore>, database: Option<String>, batch_size: usize) -> Self {
        Self {
            store,
            database,
            batch_size: batch_size.max(1),
        }
    }

    fn write_batches(&mut self, query: &str, rows: &[Row]) -> Result<i64, StoreError> {
        let mut total = 0i64;
        for chunk in rows.chunks(self.batch_size) {
            let batch: Vec<Row> = chunk.iter().map(graph_row).collect();
            let mut params = BTreeMap::new();
            params.insert(
                "rows".to_string(),
                Value::Array(batch.into_iter().map(Value::Object).collect()),
            );
            let records = self
                .store
                .execute_query(query, &params, self.database.as_deref())?;
            total += count_from(&records, chunk.len());
        }
        Ok(total)
    }

    /// `write(result)` — result là JSON của `TopologyAnalysisResult.to_dict()`.
    pub fn write(&mut self, result: &Value) -> Result<BTreeMap<String, i64>, StoreError> {
        self.store.ensure_schema(self.database.as_deref())?;
        let project_id = result
            .get("project_id")
            .and_then(Value::as_str)
            .ok_or_else(|| StoreError::Invalid("project_id is required".into()))?;
        let project_key = project_id_lookup_key(Some(project_id))
            .ok_or_else(|| StoreError::Invalid("project_id is required".into()))?;

        let modules_json = result
            .get("modules")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let mut module_rows: Vec<Row> = Vec::new();
        let mut module_ids: BTreeMap<String, String> = BTreeMap::new();
        let mut module_frameworks: BTreeMap<String, Vec<String>> = BTreeMap::new();
        for module in &modules_json {
            let mut row = module
                .as_object()
                .cloned()
                .ok_or_else(|| StoreError::Invalid("module row phải là object".into()))?;
            let module_path = row
                .get("module_path")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let module_id = row.get("id").and_then(Value::as_str).unwrap_or("").to_string();
            module_ids.insert(module_path.clone(), module_id.clone());
            module_frameworks.insert(
                module_path,
                row.get("frameworks")
                    .and_then(Value::as_array)
                    .map(|items| {
                        items
                            .iter()
                            .filter_map(Value::as_str)
                            .map(str::to_string)
                            .collect()
                    })
                    .unwrap_or_default(),
            );
            row.insert(
                "project_id_normalized".to_string(),
                Value::String(project_key.clone()),
            );
            module_rows.push(row);
        }

        let descriptors_json = result
            .get("descriptors")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let mut descriptor_rows: Vec<Row> = Vec::new();
        for descriptor in &descriptors_json {
            let mut row = descriptor
                .as_object()
                .cloned()
                .ok_or_else(|| StoreError::Invalid("descriptor row phải là object".into()))?;
            let module_path = row
                .get("module_path")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let path = row
                .get("path")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let descriptor_id = row.get("id").and_then(Value::as_str).unwrap_or("").to_string();
            let module_id = module_ids
                .get(&module_path)
                .ok_or_else(|| {
                    StoreError::Invalid(format!(
                        "descriptor module_path {module_path:?} không có trong modules"
                    ))
                })?
                .clone();
            row.insert("module_id".to_string(), Value::String(module_id.clone()));
            row.insert("file_path".to_string(), Value::String(path.clone()));
            let name = path.rsplit('/').next().unwrap_or("").to_string();
            row.insert("name".to_string(), Value::String(name));
            row.insert(
                "project_id_normalized".to_string(),
                Value::String(project_key.clone()),
            );
            row.insert(
                "frameworks".to_string(),
                Value::Array(
                    module_frameworks
                        .get(&module_path)
                        .cloned()
                        .unwrap_or_default()
                        .into_iter()
                        .map(Value::String)
                        .collect(),
                ),
            );
            row.insert(
                "edge_id".to_string(),
                Value::String(stable_fact_id(
                    project_id,
                    "topology-edge",
                    &[module_id.as_str(), "HAS_DESCRIPTOR", descriptor_id.as_str()],
                )?),
            );
            descriptor_rows.push(row);
        }

        let dependencies_json = result
            .get("dependencies")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let mut external_rows: Vec<Row> = Vec::new();
        let mut dependency_rows: Vec<Row> = Vec::new();
        for dependency in &dependencies_json {
            let obj = dependency
                .as_object()
                .ok_or_else(|| StoreError::Invalid("dependency row phải là object".into()))?;
            let source_module_path = obj
                .get("source_module_path")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let Some(source_id) = module_ids.get(&source_module_path) else {
                continue;
            };
            let target = obj
                .get("target")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let internal = obj.get("internal").and_then(Value::as_bool).unwrap_or(false);
            let target_module_path = obj
                .get("target_module_path")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let (target_id, target_label) = if internal
                && module_ids.contains_key(&target_module_path)
            {
                (module_ids[&target_module_path].clone(), "ProjectModule".to_string())
            } else {
                let external_id = stable_fact_id(project_id, "external-dependency", &[&target])?;
                external_rows.push({
                    let mut row = Map::new();
                    row.insert("id".to_string(), Value::String(external_id.clone()));
                    row.insert("project_id".to_string(), Value::String(project_id.to_string()));
                    row.insert(
                        "project_id_normalized".to_string(),
                        Value::String(project_key.clone()),
                    );
                    row.insert("name".to_string(), Value::String(target.clone()));
                    row.insert("coordinate".to_string(), Value::String(target.clone()));
                    row.insert("kind".to_string(), Value::String("external".to_string()));
                    row
                });
                (external_id, "Dependency".to_string())
            };
            let mut row = obj.clone();
            row.insert("source_id".to_string(), Value::String(source_id.clone()));
            row.insert("target_id".to_string(), Value::String(target_id));
            row.insert("target_label".to_string(), Value::String(target_label));
            dependency_rows.push(row);
        }

        let endpoints_json = result
            .get("endpoints")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let mut endpoint_rows: Vec<Row> = Vec::new();
        let mut grpc_service_rows: Vec<Row> = Vec::new();
        for endpoint in &endpoints_json {
            let mut row = endpoint
                .as_object()
                .cloned()
                .ok_or_else(|| StoreError::Invalid("endpoint row phải là object".into()))?;
            let endpoint_id = row.get("id").and_then(Value::as_str).unwrap_or("").to_string();
            let endpoint_module_id = row
                .get("module_id")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            row.insert(
                "project_id_normalized".to_string(),
                Value::String(project_key.clone()),
            );
            row.insert(
                "edge_id".to_string(),
                Value::String(stable_fact_id(
                    project_id,
                    "topology-edge",
                    &[endpoint_module_id.as_str(), "EXPOSES_ENDPOINT", endpoint_id.as_str()],
                )?),
            );
            let service = row
                .get("service")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let endpoint_framework = row
                .get("framework")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let endpoint_file_path = row
                .get("file_path")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            endpoint_rows.push(row);
            if !service.is_empty() {
                let service_id = stable_fact_id(
                    project_id,
                    "grpc-service",
                    &[endpoint_module_id.as_str(), service.as_str()],
                )?;
                let service_name = service.rsplit('.').next().unwrap_or("").to_string();
                let framework = endpoint_framework.clone();
                let file_path = endpoint_file_path.clone();
                grpc_service_rows.push({
                    let mut service_row = Map::new();
                    service_row.insert("id".to_string(), Value::String(service_id.clone()));
                    service_row.insert(
                        "project_id".to_string(),
                        Value::String(project_id.to_string()),
                    );
                    service_row.insert(
                        "project_id_normalized".to_string(),
                        Value::String(project_key.clone()),
                    );
                    service_row.insert(
                        "module_id".to_string(),
                        Value::String(endpoint_module_id.clone()),
                    );
                    service_row.insert("name".to_string(), Value::String(service_name));
                    service_row.insert("qualified_name".to_string(), Value::String(service));
                    service_row.insert("framework".to_string(), Value::String(framework));
                    service_row.insert("file_path".to_string(), Value::String(file_path));
                    service_row.insert("endpoint_id".to_string(), Value::String(endpoint_id.clone()));
                    service_row.insert(
                        "module_edge_id".to_string(),
                        Value::String(stable_fact_id(
                            project_id,
                            "topology-edge",
                            &[
                                endpoint_module_id.as_str(),
                                "DECLARES_SERVICE",
                                service_id.as_str(),
                            ],
                        )?),
                    );
                    service_row.insert(
                        "rpc_edge_id".to_string(),
                        Value::String(stable_fact_id(
                            project_id,
                            "topology-edge",
                            &[service_id.as_str(), "HAS_RPC", endpoint_id.as_str()],
                        )?),
                    );
                    service_row
                });
            }
        }

        let frameworks_json = result
            .get("frameworks")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let mut framework_rows: Vec<Row> = Vec::new();
        for framework in &frameworks_json {
            let mut row = framework
                .as_object()
                .cloned()
                .ok_or_else(|| StoreError::Invalid("framework row phải là object".into()))?;
            let framework_id = row.get("id").and_then(Value::as_str).unwrap_or("").to_string();
            let module_id = row
                .get("module_id")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            row.insert(
                "project_id_normalized".to_string(),
                Value::String(project_key.clone()),
            );
            row.insert(
                "edge_id".to_string(),
                Value::String(stable_fact_id(
                    project_id,
                    "topology-edge",
                    &[module_id.as_str(), "USES_FRAMEWORK", framework_id.as_str()],
                )?),
            );
            framework_rows.push(row);
        }

        let public_api_link_rows: Vec<Row> = modules_json
            .iter()
            .filter_map(Value::as_object)
            .filter(|module| {
                let module_path = module
                    .get("module_path")
                    .and_then(Value::as_str)
                    .unwrap_or("");
                module_path != "." || modules_json.len() == 1
            })
            .map(|module| {
                let mut row = Map::new();
                row.insert(
                    "module_id".to_string(),
                    Value::String(
                        module.get("id").and_then(Value::as_str).unwrap_or("").to_string(),
                    ),
                );
                row.insert(
                    "module_path".to_string(),
                    Value::String(
                        module
                            .get("module_path")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_string(),
                    ),
                );
                row.insert("project_id".to_string(), Value::String(project_id.to_string()));
                row.insert(
                    "project_id_normalized".to_string(),
                    Value::String(project_key.clone()),
                );
                row
            })
            .collect();

        let gradle_compatibility_rows: Vec<Row> = module_rows
            .iter()
            .filter(|row| {
                row.get("build_systems")
                    .and_then(Value::as_array)
                    .map(|items| {
                        items
                            .iter()
                            .filter_map(Value::as_str)
                            .any(|item| item.contains("gradle"))
                    })
                    .unwrap_or(false)
            })
            .cloned()
            .collect();

        // Dependency edges group theo target label (sorted như Python).
        let mut dependency_count = 0i64;
        let mut dependency_rows_by_label: BTreeMap<String, Vec<Row>> = BTreeMap::new();
        for row in dependency_rows {
            let label = row
                .get("target_label")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            dependency_rows_by_label.entry(label).or_default().push(row);
        }
        for (target_label, rows) in &dependency_rows_by_label {
            dependency_count += self.write_batches(&dependency_edge_query(target_label)?, rows)?;
        }

        let mut counts = BTreeMap::new();
        counts.insert("modules".to_string(), self.write_batches(MODULE_QUERY, &module_rows)?);
        counts.insert(
            "gradle_compatibility_labels".to_string(),
            self.write_batches(GRADLE_COMPATIBILITY_QUERY, &gradle_compatibility_rows)?,
        );
        counts.insert(
            "gradle_legacy_links".to_string(),
            self.write_batches(GRADLE_LEGACY_LINK_QUERY, &gradle_compatibility_rows)?,
        );
        counts.insert(
            "descriptors".to_string(),
            self.write_batches(DESCRIPTOR_QUERY, &descriptor_rows)?,
        );
        let mut external_sorted: BTreeMap<String, Row> = BTreeMap::new();
        for row in external_rows {
            let id = row.get("id").and_then(Value::as_str).unwrap_or("").to_string();
            external_sorted.insert(id, row);
        }
        let external_values: Vec<Row> = external_sorted.into_values().collect();
        counts.insert(
            "external_dependencies".to_string(),
            self.write_batches(DEPENDENCY_NODE_QUERY, &external_values)?,
        );
        counts.insert("dependencies".to_string(), dependency_count);
        counts.insert(
            "endpoints".to_string(),
            self.write_batches(ENDPOINT_QUERY, &endpoint_rows)?,
        );
        counts.insert(
            "grpc_services".to_string(),
            self.write_batches(GRPC_SERVICE_QUERY, &grpc_service_rows)?,
        );
        counts.insert(
            "frameworks".to_string(),
            self.write_batches(FRAMEWORK_QUERY, &framework_rows)?,
        );
        counts.insert(
            "public_api_links".to_string(),
            self.write_batches(PUBLIC_API_LINK_QUERY, &public_api_link_rows)?,
        );
        counts.insert(
            "existing_endpoint_links".to_string(),
            self.write_batches(EXISTING_ENDPOINT_LINK_QUERY, &public_api_link_rows)?,
        );
        counts.insert(
            "android_fact_links".to_string(),
            self.write_batches(ANDROID_FACT_LINK_QUERY, &public_api_link_rows)?,
        );
        Ok(counts)
    }

    /// `cleanup_paths`.
    pub fn cleanup_paths(&mut self, project_id: &str, paths: &[String]) -> Result<i64, StoreError> {
        let normalized: BTreeSet<String> = paths
            .iter()
            .filter(|path| !path.is_empty())
            .map(|path| path.replace('\\', "/").trim_matches('/').to_string())
            .collect();
        if normalized.is_empty() {
            return Ok(0);
        }
        let sorted: Vec<String> = normalized.into_iter().collect();
        let mut params = BTreeMap::new();
        params.insert(
            "project_id_normalized".to_string(),
            json!(project_id_lookup_key(Some(project_id)).unwrap_or_default()),
        );
        params.insert(
            "paths".to_string(),
            Value::Array(sorted.into_iter().map(Value::String).collect()),
        );
        let records = self
            .store
            .execute_query(CLEANUP_PATHS_QUERY, &params, self.database.as_deref())?;
        Ok(count_from(&records, 0))
    }

    /// `cleanup_project`.
    pub fn cleanup_project(&mut self, project_id: &str) -> Result<i64, StoreError> {
        let mut params = BTreeMap::new();
        params.insert(
            "project_id_normalized".to_string(),
            json!(project_id_lookup_key(Some(project_id)).unwrap_or_default()),
        );
        let records = self
            .store
            .execute_query(CLEANUP_PROJECT_QUERY, &params, self.database.as_deref())?;
        Ok(count_from(&records, 0))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stable_fact_id_matches_python_shape() {
        // Python: stable_fact_id("Bank", "topology-edge", ":app", "HAS_DESCRIPTOR", "d1")
        // material = "bank\x1ftopology-edge\x1f:app\x1fHAS_DESCRIPTOR\x1fd1"
        let id = stable_fact_id("Bank", "topology-edge", &[":app", "HAS_DESCRIPTOR", "d1"])
            .unwrap();
        assert!(id.starts_with("topology-edge:"));
        assert_eq!(id.len(), "topology-edge:".len() + 24);
    }

    #[test]
    fn canonical_json_sorted_separators() {
        let mut map = Map::new();
        map.insert("b".to_string(), json!(1));
        map.insert("a".to_string(), json!("x"));
        assert_eq!(canonical_json(&map), r#"{"a":"x","b":1}"#);
    }

    #[test]
    fn graph_value_dumps_maps() {
        let value = graph_value(&json!({"k": 1}));
        assert_eq!(value, json!(r#"{"k":1}"#));
        assert_eq!(graph_value(&json!([1, 2])), json!([1, 2]));
    }
}
