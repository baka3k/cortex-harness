//! Port của `code-tiny/tools/graph/schema/manifest.py` — validated,
//! provider-neutral schema metadata cho code graph ingestion.
//!
//! Parity target chính: `GraphSchemaManifest::fingerprint` phải ra đúng
//! sha256-prefix-16 như Python (canonical JSON `sort_keys + separators(,)`).

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

/// Python: `^[A-Za-z_][A-Za-z0-9_]*$` fullmatch.
pub fn validate_cypher_identifier(value: &str, kind: &str) -> Result<String, String> {
    let mut chars = value.chars();
    let valid = match chars.next() {
        Some(first) if first.is_ascii_alphabetic() || first == '_' => {
            chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
        }
        _ => false,
    };
    if valid {
        Ok(value.to_string())
    } else {
        Err(format!("unsafe Cypher {kind}: {value:?}"))
    }
}

/// Một index label/property bắt buộc — tương đương `SchemaIndex`
/// (ordering theo tuple: label, properties, index_type, entity_type).
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct SchemaIndex {
    pub label: String,
    pub properties: Vec<String>,
    #[serde(default = "default_index_type")]
    pub index_type: String,
    #[serde(default = "default_entity_type")]
    pub entity_type: String,
    #[serde(default = "default_required")]
    pub required: bool,
}

fn default_index_type() -> String {
    "range".to_string()
}

fn default_entity_type() -> String {
    "node".to_string()
}

fn default_required() -> bool {
    true
}

impl SchemaIndex {
    pub fn new(label: &str, properties: &[&str]) -> Result<Self, String> {
        Self::build(label, properties, "range", "node", true)
    }

    #[allow(clippy::too_many_arguments)]
    pub fn build(
        label: &str,
        properties: &[&str],
        index_type: &str,
        entity_type: &str,
        required: bool,
    ) -> Result<Self, String> {
        validate_cypher_identifier(label, "label")?;
        if properties.is_empty() {
            return Err(format!("schema index {label:?} has no properties"));
        }
        for prop in properties {
            validate_cypher_identifier(prop, "property")?;
        }
        if index_type != "range" && index_type != "fulltext" {
            return Err(format!("unsupported index type: {index_type}"));
        }
        if entity_type != "node" {
            return Err(format!("unsupported index entity type: {entity_type}"));
        }
        Ok(Self {
            label: label.to_string(),
            properties: properties.iter().map(|s| s.to_string()).collect(),
            index_type: index_type.to_string(),
            entity_type: entity_type.to_string(),
            required,
        })
    }

    pub fn as_driver_dict(&self) -> serde_json::Value {
        let prop = if self.properties.len() == 1 {
            serde_json::Value::String(self.properties[0].clone())
        } else {
            serde_json::Value::Array(
                self.properties
                    .iter()
                    .map(|p| serde_json::Value::String(p.clone()))
                    .collect(),
            )
        };
        serde_json::json!({
            "label": self.label,
            "property": prop,
            "type": self.index_type,
        })
    }
}

/// Versioned set of indexes — tương đương `GraphSchemaManifest`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct GraphSchemaManifest {
    pub name: String,
    pub version: u32,
    pub indexes: Vec<SchemaIndex>,
    /// (type, source_labels, target_labels, endpoint_policy)
    #[serde(default)]
    pub relationship_types: Vec<(String, Vec<String>, Vec<String>, bool)>,
}

impl GraphSchemaManifest {
    pub fn build(
        name: &str,
        version: u32,
        indexes: Vec<SchemaIndex>,
        relationship_types: Vec<(String, Vec<String>, Vec<String>, bool)>,
    ) -> Result<Self, String> {
        validate_cypher_identifier(name, "manifest name")?;
        if version < 1 {
            return Err("schema manifest version must be positive".to_string());
        }
        let mut seen = std::collections::BTreeSet::new();
        for index in &indexes {
            if !seen.insert((
                index.label.clone(),
                index.properties.clone(),
                index.index_type.clone(),
                index.entity_type.clone(),
            )) {
                return Err(format!(
                    "schema manifest {name:?} contains duplicate indexes"
                ));
            }
        }
        let mut rel_names = std::collections::BTreeSet::new();
        for (rel_name, source, target, _) in &relationship_types {
            if !rel_names.insert(rel_name.clone()) {
                return Err(format!(
                    "schema manifest {name:?} contains duplicate relationship types"
                ));
            }
            validate_cypher_identifier(rel_name, "relationship type")?;
            let _ = (source, target);
        }
        Ok(Self {
            name: name.to_string(),
            version,
            indexes,
            relationship_types,
        })
    }

    /// Fingerprint — replicate `json.dumps(payload, sort_keys=True,
    /// separators=(",", ":"))` + sha256[:16]. serde_json Map là BTreeMap
    /// (keys sorted) nên `to_string` cho canonical string tương đương.
    pub fn fingerprint(&self) -> String {
        let mut indexes = self.indexes.clone();
        indexes.sort();
        let mut rels = self.relationship_types.clone();
        rels.sort();

        let payload = serde_json::json!({
            "indexes": indexes
                .iter()
                .map(|i| serde_json::json!({
                    "entity_type": i.entity_type,
                    "label": i.label,
                    "properties": i.properties,
                    "required": i.required,
                    "type": i.index_type,
                }))
                .collect::<Vec<_>>(),
            "name": self.name,
            "relationship_types": rels
                .iter()
                .map(|(t, source, target, policy)| serde_json::json!({
                    "endpoint_policy": policy,
                    "source_labels": source,
                    "target_labels": target,
                    "type": t,
                }))
                .collect::<Vec<_>>(),
            "version": self.version,
        });
        let encoded = serde_json::to_string(&payload).expect("payload serialize được");
        let digest = Sha256::digest(encoded.as_bytes());
        hex_prefix_16(&digest)
    }

    pub fn driver_indexes(&self) -> Vec<serde_json::Value> {
        self.indexes.iter().map(|i| i.as_driver_dict()).collect()
    }

    pub fn has_identity_index(&self, label: &str, property_name: &str) -> bool {
        self.indexes.iter().any(|i| {
            i.label == label
                && i.properties.len() == 1
                && i.properties[0] == property_name
                && i.index_type == "range"
                && i.required
        })
    }
}

fn hex_prefix_16(digest: &[u8]) -> String {
    let mut out = String::with_capacity(16);
    for byte in digest {
        out.push_str(&format!("{byte:02x}"));
        if out.len() >= 16 {
            break;
        }
    }
    out.truncate(16);
    out
}

fn id_indexes(labels: &[&str]) -> Vec<SchemaIndex> {
    labels
        .iter()
        .map(|label| SchemaIndex::new(label, &["id"]).expect("label hợp lệ"))
        .collect()
}

/// Registry identity dùng chung bởi language writer và direct analyzer mutation
/// paths — port nguyên văn từ `CODE_GRAPH_SCHEMA`.
pub fn code_graph_schema() -> GraphSchemaManifest {
    let labels: &[&str] = &[
        "Alias", "Action", "Advice", "AndroidComponent", "AndroidAnnotation",
        "AndroidHandlerMessage", "AndroidIntentAction", "AndroidManifest",
        "AndroidNavRoute", "AndroidResource", "ApiEndpoint", "ApplicationEvent",
        "AspNetAnalysisState", "Aspect", "AsyncBoundary", "AuthenticationScheme",
        "Authority", "AuthorizationPolicy", "BuildConfiguration", "BuildDescriptor",
        "CacheOperation", "CacheRegion", "CallSite", "Class", "CobolCicsCommand",
        "CobolCopybook", "CobolDataItem", "CobolFile", "CobolParagraph",
        "CobolProgram", "CobolSection", "CobolSqlStatement", "Constant",
        "ConfigurationKey", "Controller", "DataRepository", "Database",
        "DatabaseColumn", "DatabaseTable", "Dependency", "Directory", "Document",
        "Enum", "ErrorPage", "Event", "Field", "File", "Filter", "FilterMapping",
        "FrameworkInstance", "Function", "FunctionType", "GradleDependency",
        "GradleModule", "GrpcEndpoint", "GrpcService", "GraphWriteReceipt",
        "HttpEndpoint", "HttpHandler", "HttpModule", "Interface", "InfraNode",
        "JSPView", "JpaEntity", "Jp1Unit", "JspExpression", "JspTag", "Layout",
        "LifecycleEvent", "Listener", "Message", "MessageDestination",
        "MessageEndpoint", "Middleware", "Model", "MyBatisArtifact", "MyBatisCache",
        "MyBatisConfig", "MyBatisDynamicNode", "MyBatisExtension", "MyBatisInclude",
        "MyBatisJavaProperty", "MyBatisMapper", "MyBatisMapperMethod",
        "MyBatisModule", "MyBatisParameter", "MyBatisResultMap",
        "MyBatisResultMapping", "MyBatisSpringBridge", "MyBatisSqlFragment",
        "MyBatisSqlJoin", "MyBatisSqlParameter", "MyBatisSqlProvider",
        "MyBatisSqlStatement", "MyBatisStatement", "MyBatisXmlDocument",
        "Namespace", "Navigator", "Package", "PageHandler", "Paragraph",
        "ParseRun", "PartialView", "Pointcut", "Procedure", "ProjectModule",
        "Property", "RazorPage", "Repository", "Resource", "Result", "RouteParam",
        "Route", "ScheduledTask", "SemanticCoverage", "SecurityConstraint",
        "SecurityFilterChain", "SecurityRule", "Service", "BatchProgram",
        "ShellFunction", "ShellInvocation", "ShellScript", "Servlet",
        "ServletJspAnalysisState", "ServletJspModule", "ServletMapping",
        "SessionState", "SqlCursor", "SqlDirective", "SqlHostVariable",
        "SqlStatement", "SpringApplication", "SpringBean", "SpringConfiguration",
        "SpringModule", "StateSlot", "Table", "Template", "TransactionBoundary",
        "Type", "UIControl", "UnknownFunction", "ValidationConstraint",
        "ValidationRule", "Variable", "View", "ViewModel", "WebConfiguration",
        "WebDescriptor", "WebFormPage", "WebTarget", "WelcomePage",
    ];
    let mut indexes = vec![
        SchemaIndex::new("Project", &["project_id"]).expect("hợp lệ"),
        SchemaIndex::new("Repository", &["name"]).expect("hợp lệ"),
        SchemaIndex::new("Workflow", &["workflow_id"]).expect("hợp lệ"),
    ];
    indexes.extend(id_indexes(labels));
    for (label, prop) in [
        ("ApiEndpoint", "symbol_id"),
        ("Controller", "symbol_id"),
        ("DataRepository", "symbol_id"),
        ("Database", "symbol_id"),
        ("Middleware", "symbol_id"),
        ("Service", "symbol_id"),
        ("BuildConfiguration", "config_fingerprint"),
        ("CallSite", "site_id"),
        ("SemanticCoverage", "tu_key"),
        // Coverage nodes merge theo content fingerprint; tu_key giữ làm lookup index.
        ("SemanticCoverage", "fingerprint"),
    ] {
        indexes.push(SchemaIndex::new(label, &[prop]).expect("hợp lệ"));
    }

    let rel = |name: &str, source: &[&str], target: &[&str], policy: bool| {
        (
            name.to_string(),
            source.iter().map(|s| s.to_string()).collect::<Vec<_>>(),
            target.iter().map(|s| s.to_string()).collect::<Vec<_>>(),
            policy,
        )
    };
    let relationship_types = vec![
        // Semantic call-evidence staging plane; `direct` = endpoints authoritative.
        rel("HAS_CALLSITE", &["Function"], &["CallSite"], true),
        rel("RESOLVES_TO", &["CallSite"], &["Function"], true),
        rel("OBSERVED_AS", &["CallSite"], &["Function"], false),
        rel("IN_CONFIGURATION", &["CallSite"], &["BuildConfiguration"], true),
        rel("MAPS_TO_SOURCE", &["CallSite"], &["File"], false),
        // Cross-domain evidence joins đã được schema-owner duyệt.
        rel("EXECUTES_SQL", &["Function"], &["SqlStatement"], false),
        rel(
            "RESOLVES_HOST_DECLARATION",
            &["SqlHostVariable"],
            &["Function", "Variable", "Field"],
            false,
        ),
    ];

    GraphSchemaManifest::build("code_graph", 2, indexes, relationship_types)
        .expect("CODE_GRAPH_SCHEMA hợp lệ")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identifier_validation() {
        assert!(validate_cypher_identifier("UserService", "label").is_ok());
        assert!(validate_cypher_identifier("_x1", "label").is_ok());
        assert!(validate_cypher_identifier("1bad", "label").is_err());
        assert!(validate_cypher_identifier("has-dash", "label").is_err());
        assert!(validate_cypher_identifier("", "label").is_err());
    }

    #[test]
    fn duplicate_index_rejected() {
        let indexes = vec![
            SchemaIndex::new("File", &["id"]).expect("hợp lệ"),
            SchemaIndex::new("File", &["id"]).expect("hợp lệ"),
        ];
        assert!(GraphSchemaManifest::build("m", 1, indexes, vec![]).is_err());
    }
}
