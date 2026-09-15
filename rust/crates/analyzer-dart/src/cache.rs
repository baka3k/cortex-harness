//! Rust port của `tools/flutter/cache.py` — versioned import dependency
//! cache cho incremental Dart analysis (impact expansion + selection).

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use serde_json::Value;

use crate::models::{AnalysisFacts, NodeRecord, SummaryRecord};

pub const CACHE_VERSION: i64 = 1;
const DEPENDENCY_RELATIONSHIPS: [&str; 4] = ["IMPORTS", "EXPORTS", "HAS_PART", "PART_OF"];

/// `DependencyIndex` — file → tập file nó phụ thuộc (qua import edges).
#[derive(Debug, Clone, Default)]
pub struct DependencyIndex {
    pub dependencies: BTreeMap<String, BTreeSet<String>>,
}

impl DependencyIndex {
    /// `from_facts` — owner map identity → evidence.file rồi gom dependency
    /// theo relationship.
    pub fn from_facts(facts: &AnalysisFacts) -> Self {
        let owner: BTreeMap<&str, &str> = facts
            .nodes
            .iter()
            .map(|node| (node.identity.as_str(), node.evidence.file.as_str()))
            .collect();
        let mut dependencies: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
        for edge in &facts.edges {
            if !DEPENDENCY_RELATIONSHIPS.contains(&edge.relationship.as_str()) {
                continue;
            }
            let Some(source) = owner.get(edge.source.as_str()) else {
                continue;
            };
            let Some(target) = owner.get(edge.target.as_str()) else {
                continue;
            };
            if source == target {
                continue;
            }
            dependencies
                .entry((*source).to_string())
                .or_default()
                .insert((*target).to_string());
        }
        Self { dependencies }
    }

    /// `impacted_files` — changed ∪ deleted + closure ngược theo dependency.
    pub fn impacted_files(
        &self,
        changed: &BTreeSet<String>,
        deleted: &BTreeSet<String>,
    ) -> BTreeSet<String> {
        let mut impacted: BTreeSet<String> = changed.union(deleted).cloned().collect();
        let mut reverse: BTreeMap<&str, Vec<&str>> = BTreeMap::new();
        for (source, targets) in &self.dependencies {
            for target in targets {
                reverse.entry(target.as_str()).or_default().push(source);
            }
        }
        let mut queue: Vec<String> = impacted.iter().cloned().collect();
        while let Some(target) = queue.pop() {
            if let Some(dependents) = reverse.get(target.as_str()) {
                for dependent in dependents {
                    if impacted.insert((*dependent).to_string()) {
                        queue.push((*dependent).to_string());
                    }
                }
            }
        }
        impacted
    }

    /// `save` — JSON byte-compatible với bản Python (indent 2, sort keys).
    pub fn save(&self, path: &Path) -> Result<(), String> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        let dependencies: BTreeMap<&str, Vec<&str>> = self
            .dependencies
            .iter()
            .map(|(key, values)| (key.as_str(), values.iter().map(String::as_str).collect()))
            .collect();
        let mut root = serde_json::Map::new();
        root.insert("version".to_string(), Value::from(CACHE_VERSION));
        root.insert(
            "dependencies".to_string(),
            serde_json::to_value(dependencies).map_err(|e| e.to_string())?,
        );
        let value = sorted_value(&Value::Object(root));
        let mut text = serde_json::to_string_pretty(&value).map_err(|e| e.to_string())?;
        text.push('\n');
        std::fs::write(path, text).map_err(|e| e.to_string())
    }

    /// `load` — version mismatch → Err (caller fallback full re-analysis).
    pub fn load(path: &Path) -> Result<Self, String> {
        let text = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
        let value: Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
        let version = value.get("version").and_then(Value::as_i64).unwrap_or(-1);
        if version != CACHE_VERSION {
            return Err(format!(
                "unsupported Dart dependency cache version {version:?}; expected {CACHE_VERSION}"
            ));
        }
        let raw = value.get("dependencies").cloned().unwrap_or(Value::Null);
        let Value::Object(map) = raw else {
            return Err("Dart dependency cache dependencies must be an object".to_string());
        };
        let mut dependencies = BTreeMap::new();
        for (key, values) in map {
            let Value::Array(items) = values else {
                continue;
            };
            let set: BTreeSet<String> = items
                .into_iter()
                .filter_map(|item| item.as_str().map(str::to_string))
                .collect();
            dependencies.insert(key, set);
        }
        Ok(Self { dependencies })
    }
}

/// `select_incremental_facts` — facts của file impacted, endpoint ngoài
/// impacted được giữ làm `_reference_only` (không ghi row, chỉ giữ id).
pub fn select_incremental_facts(
    facts: &AnalysisFacts,
    paths: &BTreeSet<String>,
) -> AnalysisFacts {
    if paths.is_empty() {
        return AnalysisFacts {
            header: facts.header.clone(),
            nodes: Vec::new(),
            edges: Vec::new(),
            diagnostics: Vec::new(),
            summary: SummaryRecord {
                record_type: "summary",
                processed_files: 0,
                skipped_files: facts.summary.processed_files,
                error_count: 0,
                elapsed_ms: facts.summary.elapsed_ms,
            },
        };
    }
    let nodes_by_identity: BTreeMap<&str, &NodeRecord> = facts
        .nodes
        .iter()
        .map(|node| (node.identity.as_str(), node))
        .collect();
    let owned: BTreeMap<&str, &NodeRecord> = facts
        .nodes
        .iter()
        .filter(|node| paths.contains(&node.evidence.file))
        .map(|node| (node.identity.as_str(), node))
        .collect();
    let edges: Vec<_> = facts
        .edges
        .iter()
        .filter(|edge| paths.contains(&edge.evidence.file))
        .cloned()
        .collect();
    let mut endpoint_ids: BTreeSet<&str> = BTreeSet::new();
    for edge in &edges {
        endpoint_ids.insert(edge.source.as_str());
        endpoint_ids.insert(edge.target.as_str());
    }
    let mut selected: BTreeMap<String, NodeRecord> = owned
        .iter()
        .map(|(identity, node)| ((*identity).to_string(), (*node).clone()))
        .collect();
    for identity in endpoint_ids {
        if owned.contains_key(identity) {
            continue;
        }
        let Some(node) = nodes_by_identity.get(identity) else {
            continue;
        };
        let mut properties = node.properties.clone();
        properties.insert("_reference_only".to_string(), serde_json::json!(true));
        selected.insert(
            (*identity).to_string(),
            NodeRecord {
                record_type: "node",
                identity: node.identity.clone(),
                kind: node.kind.clone(),
                properties,
                evidence: node.evidence.clone(),
            },
        );
    }
    let diagnostics: Vec<_> = facts
        .diagnostics
        .iter()
        .filter(|item| {
            item.evidence
                .as_ref()
                .map(|ev| paths.contains(&ev.file))
                .unwrap_or(true)
        })
        .cloned()
        .collect();
    let processed = owned
        .values()
        .filter(|node| node.kind == "file")
        .map(|node| node.evidence.file.as_str())
        .collect::<BTreeSet<_>>()
        .len();
    let error_count = diagnostics
        .iter()
        .filter(|item| item.severity == "error")
        .count();
    AnalysisFacts {
        header: facts.header.clone(),
        nodes: selected.into_values().collect(),
        edges,
        diagnostics,
        summary: SummaryRecord {
            record_type: "summary",
            processed_files: processed,
            skipped_files: facts.summary.processed_files.saturating_sub(processed),
            error_count,
            elapsed_ms: facts.summary.elapsed_ms,
        },
    }
}

/// Sắp key object đệ quy — đảm bảo JSON artifact/cache sort_keys giống
/// Python kể cả khi `preserve_order` được bật ở đâu đó trong build graph.
pub fn sorted_value(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let keys: BTreeMap<&str, &Value> = map
                .iter()
                .map(|(key, item)| (key.as_str(), item))
                .collect();
            Value::Object(
                keys.into_iter()
                    .map(|(key, item)| (key.to_string(), sorted_value(item)))
                    .collect(),
            )
        }
        Value::Array(items) => Value::Array(items.iter().map(sorted_value).collect()),
        other => other.clone(),
    }
}
