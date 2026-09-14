//! Port của `code-tiny/mcp/framework_registry.py` — canonical parser
//! capabilities shared by unified MCP backends.
//!
//! Byte-compatibility: `capability_catalog()` / `list_parsers` payloads are
//! golden-compared against `framework_registry.capability_catalog()` via
//! `tests/fixtures/capability_catalog.json` (generated from the Python
//! module by `scripts/rust_mcp/generate_data.py`).
//!
//! The constants below are generated verbatim from
//! `code-tiny/mcp/framework_registry.py` (see `scripts/rust_mcp/generate_data.py`
//! provenance; label/relationship sets are exact).

use std::collections::{BTreeMap, BTreeSet};
use std::sync::OnceLock;

use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

pub const CAPABILITY_CONTRACT_VERSION: u64 = 1;

const CORE_RELATIONSHIPS: &[&str] = &[
    "CALLS", "USES_TYPE", "REFERENCES", "INHERITS", "ALIASES", "ALIAS_OF",
];

const GENERIC_LABELS: &[&str] = &[
    "Alias", "BuildDescriptor", "Class", "Dependency", "Enum", "Event", "Field", "File",
    "FrameworkInstance", "Function", "FunctionType", "GrpcEndpoint", "GrpcService",
    "Interface", "Method", "Module", "Namespace", "Package", "Project", "ProjectModule",
    "Repository", "Resource", "Template", "Type", "UIControl",
];

const GENERIC_SEARCHABLE_PROPERTIES: &[&str] = &[
    "name", "qualified_name", "target_name", "signature", "type_signature", "file_path",
    "path", "summary", "code", "comment", "module_id", "module_path", "visibility",
    "is_public_api", "descriptor_type", "role", "parse_depth", "protocol", "framework",
];

const NON_TEXT_SEARCH_PROPERTIES: &[&str] = &[
    "column", "count", "declared", "end_line", "index", "is_public_api", "line", "ordinal",
    "parse_depth", "position", "size", "start_line",
];

const GENERIC_RELATIONSHIPS: &[&str] = &[
    "CALLS", "DECLARES", "CONTAINS", "DEPENDS_ON", "IMPORTS", "EXPORTS", "IMPLEMENTS",
    "EXTENDS", "ALIASES", "ALIAS_OF", "HAS_DESCRIPTOR", "EXPOSES_API", "EXPOSES_ENDPOINT",
    "USES_FRAMEWORK",
];

const CONTEXT_RELATIONSHIPS: &[&str] = &[
    "EXPOSES_API", "EXPOSES_ENDPOINT", "HAS_DESCRIPTOR", "USES_FRAMEWORK",
];

// C++ / Pro*C relationship set (additive cross-domain joins at the end).
const CPLUS_RELATIONSHIPS: &[&str] = &[
    "CALLS", "POSSIBLE_CALLS", "CALLS_FUNCTION_POINTER", "DECLARES", "CONTAINS",
    "USES_RESOURCE", "BINDS_CONTROL", "HANDLES_CONTROL", "OWNS_DIALOG", "DEPENDS_ON",
    "ALIASES", "ALIAS_OF", "DECLARES_STATEMENT", "DECLARES_DIRECTIVE", "BINDS_PARAMETER",
    "DECLARES_CURSOR", "REFERENCES_CURSOR", "REFERENCES_STATEMENT", "READS_FROM", "WRITES_TO",
    "REFERENCES_TABLE", "EXECUTES_SQL", "RESOLVES_HOST_DECLARATION",
];

const CPLUS_LABELS_EXTRA: &[&str] = &[
    "BuildConfiguration", "CallSite", "DatabaseTable", "SemanticCoverage", "SqlCursor",
    "SqlDirective", "SqlHostVariable", "SqlStatement",
];

const SHELL_LABELS_EXTRA: &[&str] = &[
    "ShellFunction", "ShellScript",
];

const JP1_LABELS_EXTRA: &[&str] = &[
    "Jp1Unit", "ShellScript",
];

const ANDROID_RELATIONSHIPS: &[&str] = &[
    "CALLS", "DECLARES", "CONTAINS", "USES_RESOURCE", "DECLARES_ROUTE", "STARTS_WITH_ROUTE",
    "ROUTE_CALLS", "DECLARES_COMPONENT", "STARTS_COMPONENT", "STARTS_INTENT",
    "SENDS_BROADCAST", "REGISTERS_RECEIVER", "DECLARES_INTENT_ACTION", "SENDS_HANDLER_MESSAGE",
    "ACTION_TARGETS_COMPONENT", "EMITS_EVENT", "HANDLES_EVENT", "ANNOTATED_WITH", "DEPENDS_ON",
    "TAKES_FUNCTION", "IMPLEMENTS", "EXTENDS",
];

const WEB_LABELS_EXTRA: &[&str] = &[
    "ApiCall", "ApiEndpoint", "Controller", "Database", "HttpEndpoint", "Middleware", "Route",
    "Service",
];

const WEB_RELATIONSHIPS_EXTRA: &[&str] = &[
    "CALLS_API", "MATCHES", "HANDLES", "HANDLED_BY", "MAPPED_TO", "USES", "QUERIES", "RETURNS",
    "INJECTS",
];

const WEB_SEARCHABLE_PROPERTIES_EXTRA: &[&str] = &[
    "route", "url_pattern", "http_method", "handler_name", "controller_name",
];

const DATABASE_LABELS_EXTRA: &[&str] = &[
    "Procedure", "Table", "View",
];

const DATABASE_RELATIONSHIPS_EXTRA: &[&str] = &[
    "READS_FROM", "WRITES_TO", "REFERENCES_TABLE",
];

const DATABASE_SEARCHABLE_PROPERTIES_EXTRA: &[&str] = &[
    "schema_name", "dialect", "object_kind", "declared",
];

// C# primary analyzer upgrade (Phase 05) — additive labels.
const CSHARP_LABELS_EXTRA: &[&str] = &[
    "AuthPolicy", "Parameter", "SignalRHub", "Delegate", "Model", "GenericParameter",
    "LoggingTelemetry", "BackgroundService", "PackageReference", "Service", "EfEntityMapping",
    "Route", "Property", "NuGetDependency",
];

const CSHARP_RELATIONSHIPS_EXTRA: &[&str] = &[
    "HAS_PROPERTY", "HAS_FIELD", "HAS_EVENT", "HAS_DELEGATE", "HAS_PARAMETER",
    "HAS_GENERIC_PARAMETER", "HAS_ATTRIBUTE", "EXTENDS_CLASS", "IMPLEMENTS_INTERFACE",
    "DEPENDS_ON_PACKAGE", "REFERENCES_PROJECT", "MAPS_ENTITY", "EXPOSES_GRPC", "EXPOSES_HUB",
    "RUNS_BACKGROUND", "ENFORCES_POLICY", "EMITS_LOG", "TRACES_ACTIVITY", "SEMANTIC_OF",
];

const CSHARP_SEARCHABLE_PROPERTIES_EXTRA: &[&str] = &[
    "return_type", "type_name", "delegate_type", "accessibility", "is_async", "is_static",
    "service_type", "execute_method", "config_path", "section_name", "entity_type",
    "table_name", "route", "http_method", "policy_name", "middleware_type", "package_name",
    "version", "is_development", "activity_name", "instrumentation_type", "logger_category",
];

const GENERIC_FEATURES: &[&str] = &[
    "architecture_summary_queries", "dependency_planning", "endpoint_inventory_queries",
    "framework_context_queries", "graph_exploration", "graph_flow", "graph_paths",
    "graph_search", "module_queries", "public_api_queries", "semantic_search",
    "special_file_queries",
];

const FRAMEWORK_FEATURES_EXTRA: &[&str] = &[
    "framework_query", "profile_labels", "profile_relationships",
];

/// `SUPPORT_DIMENSIONS` — dimension order is part of the contract.
pub const SUPPORT_DIMENSIONS: &[&str] = &["symbols", "calls", "endpoints", "database"];

/// `PUBLIC_QUERY_ENGINES` — internal dispatch backend → public query engine.
const PUBLIC_QUERY_ENGINES: &[(&str, &str)] = &[
    ("android", "android_graph"),
    ("cplus", "graph_generic"),
    ("fast", "fast_graph"),
];

/// `_DIMENSION_LABEL_EVIDENCE`.
const DIMENSION_LABEL_EVIDENCE: &[(&str, &[&str])] = &[
    (
        "symbols",
        &[
            "File", "Namespace", "Package", "Module", "Class", "Interface", "Enum", "Type",
            "Function", "Method", "Field", "Alias", "Template", "FunctionType", "CobolProgram",
            "CobolSection", "CobolParagraph", "CobolDataItem", "CobolCopybook", "Widget",
            "Screen", "AndroidComponent", "Project", "Repository",
        ],
    ),
    ("calls", &[]),
    (
        "endpoints",
        &["ApiEndpoint", "HttpEndpoint", "GrpcEndpoint", "Route"],
    ),
    (
        "database",
        &[
            "Table", "View", "Procedure", "Database", "DataRepository", "Repository",
            "MyBatisStatement", "SqlStatement", "CobolSqlStatement",
        ],
    ),
];

/// `_DIMENSION_RELATIONSHIP_EVIDENCE`.
const DIMENSION_RELATIONSHIP_EVIDENCE: &[(&str, &[&str])] = &[
    ("symbols", &[]),
    (
        "calls",
        &[
            "CALLS", "POSSIBLE_CALLS", "CALLS_FUNCTION_POINTER", "ROUTE_CALLS", "PERFORMS",
            "STARTS_COMPONENT", "SENDS_MESSAGE", "TARGETS_ENDPOINT",
        ],
    ),
    (
        "endpoints",
        &[
            "HANDLES", "HANDLED_BY", "MAPPED_TO", "SEMANTIC_OF", "DECLARES_ROUTE",
            "STARTS_WITH_ROUTE", "MATCHES", "TARGETS_ENDPOINT",
        ],
    ),
    (
        "database",
        &[
            "READS_FROM", "WRITES_TO", "REFERENCES_TABLE", "QUERIES", "DECLARES_QUERY",
            "DERIVES_QUERY", "BINDS_STATEMENT", "DECLARES_STATEMENT",
        ],
    ),
];

/// `FrameworkQueryConfig` — backward-compatible name for a canonical parser
/// capability profile. `support` and `default_query_profiles` are the derived
/// values computed by Python's `__post_init__`.
#[derive(Debug, Clone)]
pub struct FrameworkQueryConfig {
    /// Canonical (lowercased) parser name.
    pub name: String,
    /// Lowercased, trimmed aliases.
    pub aliases: BTreeSet<String>,
    pub labels: BTreeSet<String>,
    pub relationships: Vec<String>,
    pub searchable_properties: Vec<String>,
    pub generation_scoped: bool,
    pub backend: String,
    /// Legacy support level as declared.
    pub support_level: String,
    /// Profile overrides supplied at construction (before setdefaults).
    provided_profiles: Vec<(String, Vec<String>)>,
    pub features: BTreeSet<String>,
    /// Explicit per-dimension support overrides.
    support_overrides: Vec<(String, String)>,
    /// Derived: default query profiles (after the setdefault chain).
    profiles: BTreeMap<String, Vec<String>>,
    /// Derived: dimension → level map (4 dimensions + overrides).
    pub support: BTreeMap<String, String>,
}

fn dedup_keep_order<'a, I: IntoIterator<Item = &'a str>>(items: I) -> Vec<String> {
    let mut seen = BTreeSet::new();
    let mut result = Vec::new();
    for item in items {
        if seen.insert(item.to_string()) {
            result.push(item.to_string());
        }
    }
    result
}

/// Concatenate string slices (mirrors Python tuple concatenation before
/// `dict.fromkeys` dedup).
fn chain_slices(slices: &[&[&'static str]]) -> Vec<&'static str> {
    slices
        .iter()
        .flat_map(|slice| slice.iter().copied())
        .collect()
}

fn set_of(items: &[&str]) -> BTreeSet<String> {
    items.iter().map(|item| item.to_string()).collect()
}

fn union_of(base: &[&str], extra: &[&str]) -> BTreeSet<String> {
    let mut set = set_of(base);
    for item in extra {
        set.insert(item.to_string());
    }
    set
}

impl FrameworkQueryConfig {
    #[allow(clippy::too_many_arguments)]
    fn new(
        name: &str,
        aliases: &[&str],
        labels: BTreeSet<String>,
        relationships: &[&str],
        searchable_properties: Vec<String>,
        generation_scoped: bool,
        backend: &str,
        support_level: &str,
        provided_profiles: Vec<(&str, Vec<&str>)>,
        features: BTreeSet<String>,
        support_overrides: Vec<(&str, &str)>,
    ) -> Self {
        let mut config = FrameworkQueryConfig {
            name: name.trim().to_lowercase(),
            aliases: aliases
                .iter()
                .map(|alias| alias.trim().to_lowercase())
                .filter(|alias| !alias.is_empty())
                .collect(),
            labels,
            relationships: relationships.iter().map(|item| item.to_string()).collect(),
            // Python builds these with `dict.fromkeys((*GENERIC, *extra))` —
            // first-occurrence dedup.
            searchable_properties: dedup_keep_order(searchable_properties.iter().map(String::as_str)),
            generation_scoped,
            backend: backend.trim().to_lowercase(),
            support_level: support_level.trim().to_lowercase(),
            provided_profiles: provided_profiles
                .into_iter()
                .filter(|(profile, _)| !profile.trim().is_empty())
                .map(|(profile, relationships)| {
                    (
                        profile.trim().to_string(),
                        dedup_keep_order(relationships),
                    )
                })
                .collect(),
            features,
            support_overrides: support_overrides
                .into_iter()
                .map(|(key, value)| (key.trim().to_lowercase(), value.trim().to_lowercase()))
                .collect(),
            profiles: BTreeMap::new(),
            support: BTreeMap::new(),
        };
        config.post_init();
        config
    }

    /// Python `__post_init__` derivation.
    fn post_init(&mut self) {
        let mut profiles: BTreeMap<String, Vec<String>> = self
            .provided_profiles
            .drain(..)
            .map(|(key, relationships)| {
                let deduped = dedup_keep_order(relationships.iter().map(String::as_str));
                (key, deduped)
            })
            .collect();

        let default_relationships = dedup_keep_order(
            CORE_RELATIONSHIPS
                .iter()
                .copied()
                .chain(
                    self.relationships
                        .iter()
                        .map(String::as_str)
                        .filter(|relationship| !CONTEXT_RELATIONSHIPS.contains(relationship)),
                ),
        );

        let setdefault = |profiles: &mut BTreeMap<String, Vec<String>>, key: &str, value: Vec<String>| {
            profiles.entry(key.to_string()).or_insert(value);
        };
        setdefault(&mut profiles, "default", default_relationships.clone());
        setdefault(
            &mut profiles,
            "find_callers_of_endpoint",
            vec!["CALLS_API".into(), "MATCHES".into()],
        );
        setdefault(
            &mut profiles,
            "get_api_call_chain",
            dedup_keep_order(
                default_relationships
                    .iter()
                    .map(String::as_str)
                    .chain([
                        "CALLS_API",
                        "MATCHES",
                        "HANDLES",
                        "SEMANTIC_OF",
                        "DECLARES_QUERY",
                        "DERIVES_QUERY",
                        "QUERIES",
                        "BINDS_STATEMENT",
                        "DECLARES_STATEMENT",
                        "READS_FROM",
                        "WRITES_TO",
                        "REFERENCES_TABLE",
                    ]),
            ),
        );
        setdefault(
            &mut profiles,
            "find_screen_workflows",
            vec!["NAVIGATE".into()],
        );
        setdefault(
            &mut profiles,
            "find_workflows_containing",
            dedup_keep_order(
                std::iter::once("HAS_STEP")
                    .chain(default_relationships.iter().map(String::as_str)),
            ),
        );
        setdefault(
            &mut profiles,
            "analyze_workflow_impact",
            dedup_keep_order(
                default_relationships
                    .iter()
                    .map(String::as_str)
                    .chain(["HAS_STEP", "NAVIGATE"]),
            ),
        );
        setdefault(
            &mut profiles,
            "get_project_modules",
            vec!["HAS_DESCRIPTOR".into(), "DEPENDS_ON".into()],
        );
        setdefault(&mut profiles, "get_public_apis", vec!["EXPOSES_API".into()]);
        setdefault(&mut profiles, "get_endpoints", vec!["EXPOSES_ENDPOINT".into()]);
        setdefault(
            &mut profiles,
            "get_module_architecture_summary",
            vec![
                "HAS_DESCRIPTOR".into(),
                "DEPENDS_ON".into(),
                "EXPOSES_API".into(),
                "EXPOSES_ENDPOINT".into(),
                "USES_FRAMEWORK".into(),
            ],
        );
        setdefault(
            &mut profiles,
            "get_project_special_files",
            vec!["HAS_DESCRIPTOR".into()],
        );
        setdefault(
            &mut profiles,
            "get_framework_context",
            vec!["USES_FRAMEWORK".into()],
        );
        self.profiles = profiles;

        // Dimensional support derivation.
        let legacy_level = self.support_level.clone();
        let default_symbol_level = if legacy_level == "generic" { "generic" } else { "full" };
        let default_call_level = match legacy_level.as_str() {
            "generic" => "generic",
            "partial" => "partial",
            _ => "full",
        };
        let endpoint_level = if !self.features.contains("endpoint_queries") {
            "none"
        } else if legacy_level == "full" {
            "full"
        } else {
            "partial"
        };
        let database_semantics_labels: [&str; 5] =
            ["Table", "View", "Procedure", "Database", "DataRepository"];
        let database_semantics_relationships: [&str; 4] =
            ["READS_FROM", "WRITES_TO", "REFERENCES_TABLE", "QUERIES"];
        let database_semantics = database_semantics_labels
            .iter()
            .any(|label| self.labels.contains(*label))
            || database_semantics_relationships
                .iter()
                .any(|relationship| self.relationships.iter().any(|item| item == relationship));
        let database_level = if !database_semantics {
            "none"
        } else if legacy_level == "full" {
            "full"
        } else {
            "partial"
        };

        let mut support = BTreeMap::new();
        support.insert("symbols".to_string(), default_symbol_level.to_string());
        support.insert("calls".to_string(), default_call_level.to_string());
        support.insert("endpoints".to_string(), endpoint_level.to_string());
        support.insert("database".to_string(), database_level.to_string());
        for (key, value) in self.support_overrides.drain(..) {
            support.insert(key, value);
        }
        self.support = support;
    }

    /// `relationships_for` — default profile for a tool, else the `default`
    /// profile.
    pub fn relationships_for(&self, tool_name: Option<&str>) -> &[String] {
        let key = tool_name.unwrap_or_default().trim();
        if key.is_empty() {
            return self.profiles.get("default").map(Vec::as_slice).unwrap_or(&[]);
        }
        self.profiles
            .get(key)
            .map(Vec::as_slice)
            .unwrap_or_else(|| self.profiles.get("default").map(Vec::as_slice).unwrap_or(&[]))
    }

    /// `to_dict` — the capability catalog record (byte-compatible ordering
    /// not required; keys compared per-key).
    pub fn to_dict(&self) -> Value {
        let support: Map<String, Value> = self
            .support
            .iter()
            .map(|(key, value)| (key.clone(), json!(value)))
            .collect();
        let default_query_profiles: Map<String, Value> = self
            .profiles
            .iter()
            .map(|(name, relationships)| (name.clone(), json!(relationships)))
            .collect();
        json!({
            "canonical_parser": self.name,
            "aliases": Value::Array(self.aliases.iter().map(|alias| json!(alias)).collect()),
            "query_engine": query_engine_for_backend(Some(&self.backend)),
            "support_level": self.support_level,
            "support": Value::Object(support),
            "labels": Value::Array(self.labels.iter().map(|label| json!(label)).collect()),
            "relationships": Value::Array(
                self.relationships_for(None).iter().map(|item| json!(item)).collect()
            ),
            "searchable_properties": Value::Array(
                self.searchable_properties.iter().map(|item| json!(item)).collect()
            ),
            "default_query_profiles": Value::Object(default_query_profiles),
            "features": Value::Array(self.features.iter().map(|item| json!(item)).collect()),
            "generation_scoped": self.generation_scoped,
        })
    }
}

/// `query_engine_for_backend` — public query-engine name for an internal
/// dispatch backend.
pub fn query_engine_for_backend(backend: Option<&str>) -> String {
    let internal = backend
        .unwrap_or("cplus")
        .trim()
        .to_lowercase();
    if internal.is_empty() {
        return "graph_generic".to_string();
    }
    PUBLIC_QUERY_ENGINES
        .iter()
        .find(|(name, _)| *name == internal)
        .map(|(_, engine)| engine.to_string())
        .unwrap_or_else(|| "graph_generic".to_string())
}

/// `DEFAULT_BACKEND` — `MCP_UNIFIED_DEFAULT_BACKEND` env override, default
/// `cplus` (resolved once per process like the Python module constant).
pub fn default_backend() -> &'static str {
    static DEFAULT_BACKEND: OnceLock<String> = OnceLock::new();
    DEFAULT_BACKEND.get_or_init(|| {
        let configured = std::env::var("MCP_UNIFIED_DEFAULT_BACKEND").ok();
        configured
            .map(|value| value.trim().to_lowercase())
            .filter(|value| value == "android" || value == "cplus")
            .unwrap_or_else(|| "cplus".to_string())
    })
}

#[allow(clippy::too_many_arguments)]
fn generic_profile(
    name: &str,
    aliases: &[&str],
    relationships: &[&str],
    backend: &str,
    support_level: &str,
    features: BTreeSet<String>,
    labels: BTreeSet<String>,
    profiles: Vec<(&str, Vec<&str>)>,
) -> FrameworkQueryConfig {
    FrameworkQueryConfig::new(
        name,
        aliases,
        labels,
        relationships,
        GENERIC_SEARCHABLE_PROPERTIES
            .iter()
            .map(|item| item.to_string())
            .collect(),
        false,
        backend,
        support_level,
        profiles,
        features,
        Vec::new(),
    )
}

fn framework_features() -> BTreeSet<String> {
    let mut features = set_of(GENERIC_FEATURES);
    for feature in FRAMEWORK_FEATURES_EXTRA {
        features.insert(feature.to_string());
    }
    features
}

struct Registry {
    capabilities: BTreeMap<String, FrameworkQueryConfig>,
    alias_index: BTreeMap<String, String>,
}

fn build_registry() -> Registry {
    let framework = framework_features();

    let capabilities: Vec<FrameworkQueryConfig> = vec![
        generic_profile(
            "android",
            &["android", "android-kotlin", "kotlin-android"],
            ANDROID_RELATIONSHIPS,
            "android",
            "full",
            {
                let mut features = set_of(GENERIC_FEATURES);
                features.insert("android_queries".to_string());
                features
            },
            set_of(GENERIC_LABELS),
            Vec::new(),
        ),
        {
            // Strict ("CALLS"): accepted direct semantic CALLS only; results
            // must be paired with semantic coverage — absence of an edge is
            // never proof of no call under incomplete coverage.
            // Conservative: strict calls plus explicitly weaker evidence
            // classes, unioned, never flattened.
            // proc_data_impact: Pro*C call-plus-data impact: caller ->
            // function -> SQL -> table plus host-variable evidence joins.
            let profiles: Vec<(&str, Vec<&str>)> = vec![
                ("strict", vec!["CALLS"]),
                (
                    "conservative",
                    vec!["CALLS", "POSSIBLE_CALLS", "CALLS_FUNCTION_POINTER"],
                ),
                (
                    "proc_data_impact",
                    vec![
                        "EXECUTES_SQL",
                        "RESOLVES_HOST_DECLARATION",
                        "READS_FROM",
                        "WRITES_TO",
                        "REFERENCES_TABLE",
                        "REFERENCES_STATEMENT",
                        "BINDS_PARAMETER",
                        "DECLARES_STATEMENT",
                    ],
                ),
            ];
            FrameworkQueryConfig::new(
                "cplus",
                &["cplus", "cpp", "c++", "c", "clang", "proc", "pro*c", "pro-c"],
                union_of(GENERIC_LABELS, CPLUS_LABELS_EXTRA),
                CPLUS_RELATIONSHIPS,
                GENERIC_SEARCHABLE_PROPERTIES
                    .iter()
                    .map(|item| item.to_string())
                    .collect(),
                false,
                "cplus",
                "full",
                profiles,
                set_of(GENERIC_FEATURES),
                Vec::new(),
            )
        },
        {
            let support = vec![("symbols", "full"), ("calls", "partial"), ("endpoints", "partial"), ("database", "none")];
            let mut features = framework.clone();
            features.insert("endpoint_queries".to_string());
            FrameworkQueryConfig::new(
                "python",
                &["python", "py", "fastapi", "django", "flask"],
                union_of(GENERIC_LABELS, WEB_LABELS_EXTRA),
                &chain_slices(&[GENERIC_RELATIONSHIPS, WEB_RELATIONSHIPS_EXTRA]),
                chain_slices(&[GENERIC_SEARCHABLE_PROPERTIES, WEB_SEARCHABLE_PROPERTIES_EXTRA]).iter().map(|item| item.to_string()).collect(),
                false,
                "cplus",
                "partial",
                Vec::new(),
                features,
                support,
            )
        },
        {
            let support = vec![("symbols", "full"), ("calls", "partial"), ("endpoints", "partial"), ("database", "none")];
            let mut features = framework.clone();
            features.insert("endpoint_queries".to_string());
            FrameworkQueryConfig::new(
                "javascript",
                &["javascript", "js", "node", "nodejs", "express", "express.js"],
                union_of(GENERIC_LABELS, WEB_LABELS_EXTRA),
                &chain_slices(&[GENERIC_RELATIONSHIPS, WEB_RELATIONSHIPS_EXTRA]),
                chain_slices(&[GENERIC_SEARCHABLE_PROPERTIES, WEB_SEARCHABLE_PROPERTIES_EXTRA]).iter().map(|item| item.to_string()).collect(),
                false,
                "cplus",
                "partial",
                Vec::new(),
                features,
                support,
            )
        },
        {
            let support = vec![("symbols", "full"), ("calls", "full"), ("endpoints", "none"), ("database", "none")];
            let mut features = framework.clone();
            features.insert("endpoint_queries".to_string());
            FrameworkQueryConfig::new(
                "typescript",
                &["typescript", "ts", "tsx", "nestjs", "nest.js"],
                union_of(GENERIC_LABELS, WEB_LABELS_EXTRA),
                &chain_slices(&[GENERIC_RELATIONSHIPS, WEB_RELATIONSHIPS_EXTRA]),
                chain_slices(&[GENERIC_SEARCHABLE_PROPERTIES, WEB_SEARCHABLE_PROPERTIES_EXTRA]).iter().map(|item| item.to_string()).collect(),
                false,
                "cplus",
                "full",
                Vec::new(),
                features,
                support,
            )
        },
        {
            let support = vec![("symbols", "full"), ("calls", "partial"), ("endpoints", "partial"), ("database", "none")];
            let mut features = framework.clone();
            features.insert("endpoint_queries".to_string());
            FrameworkQueryConfig::new(
                "php",
                &["php", "laravel", "symfony"],
                union_of(GENERIC_LABELS, WEB_LABELS_EXTRA),
                &chain_slices(&[GENERIC_RELATIONSHIPS, WEB_RELATIONSHIPS_EXTRA]),
                chain_slices(&[GENERIC_SEARCHABLE_PROPERTIES, WEB_SEARCHABLE_PROPERTIES_EXTRA]).iter().map(|item| item.to_string()).collect(),
                false,
                "cplus",
                "partial",
                Vec::new(),
                features,
                support,
            )
        },
        {
            let support = vec![("symbols", "full"), ("calls", "full"), ("endpoints", "partial"), ("database", "partial")];
            let mut features = framework.clone();
            features.insert("endpoint_queries".to_string());
            features.insert("framework_context_queries".to_string());
            FrameworkQueryConfig::new(
                "csharp",
                &["csharp", "c#", "cs", "dotnet", ".net"],
                union_of(GENERIC_LABELS, CSHARP_LABELS_EXTRA),
                &chain_slices(&[GENERIC_RELATIONSHIPS, CSHARP_RELATIONSHIPS_EXTRA]),
                chain_slices(&[GENERIC_SEARCHABLE_PROPERTIES, CSHARP_SEARCHABLE_PROPERTIES_EXTRA]).iter().map(|item| item.to_string()).collect(),
                false,
                "cplus",
                "full",
                Vec::new(),
                features,
                support,
            )
        },
        {
            let support = vec![("symbols", "full"), ("calls", "none"), ("database", "full"), ("endpoints", "none")];
            let mut features = set_of(GENERIC_FEATURES);
            features.insert("database_queries".to_string());
            FrameworkQueryConfig::new(
                "sql",
                &["sql"],
                union_of(GENERIC_LABELS, DATABASE_LABELS_EXTRA),
                &chain_slices(&[GENERIC_RELATIONSHIPS, DATABASE_RELATIONSHIPS_EXTRA]),
                chain_slices(&[GENERIC_SEARCHABLE_PROPERTIES, DATABASE_SEARCHABLE_PROPERTIES_EXTRA]).iter().map(|item| item.to_string()).collect(),
                false,
                "cplus",
                "full",
                Vec::new(),
                features,
                support,
            )
        },
        {
            let support = vec![("symbols", "full"), ("calls", "none"), ("database", "full"), ("endpoints", "none")];
            let mut features = set_of(GENERIC_FEATURES);
            features.insert("database_queries".to_string());
            FrameworkQueryConfig::new(
                "plsql",
                &["plsql", "pl/sql", "oracle-plsql"],
                union_of(GENERIC_LABELS, DATABASE_LABELS_EXTRA),
                &chain_slices(&[GENERIC_RELATIONSHIPS, DATABASE_RELATIONSHIPS_EXTRA]),
                chain_slices(&[GENERIC_SEARCHABLE_PROPERTIES, DATABASE_SEARCHABLE_PROPERTIES_EXTRA]).iter().map(|item| item.to_string()).collect(),
                false,
                "cplus",
                "full",
                Vec::new(),
                features,
                support,
            )
        },
        generic_profile(
            "jvm",
            &["jvm", "java", "kotlin"],
            ANDROID_RELATIONSHIPS,
            "cplus",
            "generic",
            set_of(GENERIC_FEATURES),
            set_of(GENERIC_LABELS),
            Vec::new(),
        ),
        generic_profile(
            "go",
            &["go"],
            GENERIC_RELATIONSHIPS,
            "cplus",
            "generic",
            set_of(GENERIC_FEATURES),
            set_of(GENERIC_LABELS),
            Vec::new(),
        ),
        generic_profile(
            "perl",
            &["perl"],
            GENERIC_RELATIONSHIPS,
            "cplus",
            "generic",
            set_of(GENERIC_FEATURES),
            set_of(GENERIC_LABELS),
            Vec::new(),
        ),
        generic_profile(
            "shell",
            &["shell", "sh", "bash"],
            &chain_slices(&[GENERIC_RELATIONSHIPS, &["REFERENCES"]]),
            "cplus",
            "generic",
            set_of(GENERIC_FEATURES),
            union_of(GENERIC_LABELS, SHELL_LABELS_EXTRA),
            Vec::new(),
        ),
        generic_profile(
            "jp1",
            &["jp1", "ajs", "jobnet"],
            &chain_slices(&[GENERIC_RELATIONSHIPS, &["NEXT", "INCLUDES"]]),
            "cplus",
            "generic",
            set_of(GENERIC_FEATURES),
            union_of(GENERIC_LABELS, JP1_LABELS_EXTRA),
            Vec::new(),
        ),
        generic_profile(
            "rust",
            &["rust"],
            GENERIC_RELATIONSHIPS,
            "cplus",
            "generic",
            set_of(GENERIC_FEATURES),
            set_of(GENERIC_LABELS),
            Vec::new(),
        ),
        generic_profile(
            "swift",
            &["swift"],
            GENERIC_RELATIONSHIPS,
            "cplus",
            "generic",
            set_of(GENERIC_FEATURES),
            set_of(GENERIC_LABELS),
            Vec::new(),
        ),
        generic_profile(
            "delphi",
            &["delphi", "pascal"],
            GENERIC_RELATIONSHIPS,
            "cplus",
            "generic",
            set_of(GENERIC_FEATURES),
            set_of(GENERIC_LABELS),
            Vec::new(),
        ),
        generic_profile(
            "vbnet",
            &["vbnet"],
            GENERIC_RELATIONSHIPS,
            "cplus",
            "generic",
            set_of(GENERIC_FEATURES),
            set_of(GENERIC_LABELS),
            Vec::new(),
        ),
        generic_profile(
            "visual_basic",
            &["visual_basic", "vb6", "vba", "vbscript"],
            GENERIC_RELATIONSHIPS,
            "cplus",
            "generic",
            set_of(GENERIC_FEATURES),
            set_of(GENERIC_LABELS),
            Vec::new(),
        ),
        FrameworkQueryConfig::new(
            "cobol",
            &["cobol", "cobol85", "ibm-cobol", "gnucobol"],
            union_of(
                GENERIC_LABELS,
                    &[
                    "CobolProgram",
                    "CobolSection",
                    "CobolParagraph",
                    "CobolDataItem",
                    "CobolCopybook",
                    "CobolFile",
                    "CobolSqlStatement",
                    "CobolCicsCommand",
                ],
            ),
            &[
                "DEFINES", "INCLUDES", "REFERENCES", "CALLS", "PERFORMS", "PERFORMS_THRU",
                "RETURNS", "GOES_TO", "GOES_TO_DYNAMIC", "FALLS_THROUGH", "ALTERS", "CONDITIONAL",
                "EXITS", "READS", "WRITES",
            ],
            [
                "name",
                "qualified_name",
                "file_path",
                "path",
                "raw_text",
                "operation",
                "assignment",
                "picture",
                "storage",
            ]
            .iter()
            .map(|item| item.to_string())
            .collect(),
            false,
            "cplus",
            "full",
            Vec::new(),
            framework.clone(),
            Vec::new(),
        ),
        FrameworkQueryConfig::new(
            "spring",
            &["spring", "spring-boot", "spring_boot"],
            union_of(
                GENERIC_LABELS,
                    &[
                    "SpringModule",
                    "SpringApplication",
                    "SpringConfiguration",
                    "SpringBean",
                    "JpaEntity",
                    "TransactionBoundary",
                    "MessageDestination",
                    "ScheduledTask",
                    "AsyncBoundary",
                    "ApplicationEvent",
                    "SecurityFilterChain",
                    "SecurityRule",
                    "Authority",
                    "Aspect",
                    "Advice",
                    "Pointcut",
                    "ValidationConstraint",
                    "CacheRegion",
                    "CacheOperation",
                    "ApiEndpoint",
                    "Controller",
                    "Service",
                    "DataRepository",
                    "Database",
                    "Middleware",
                    "MessageEndpoint",
                ],
            ),
            &[
                "SEMANTIC_OF",
                "HANDLES",
                "DECLARES_QUERY",
                "DERIVES_QUERY",
                "MANAGES_ENTITY",
                "RELATES_TO_ENTITY",
                "APPLIES_TO",
                "PROTECTS",
                "QUERIES",
                "IMPLEMENTS_REPOSITORY",
                "CONSUMES_FROM",
                "PUBLISHES_TO",
                "PUBLISHES_EVENT",
                "LISTENS_TO",
                "EXECUTES_ASYNC",
                "RUNS",
            ],
            chain_slices(&[GENERIC_SEARCHABLE_PROPERTIES, &["raw_value", "resolved_value"]]).iter().map(|item| item.to_string()).collect(),
            false,
            "cplus",
            "full",
            Vec::new(),
            {
                let mut features = framework.clone();
                features.insert("endpoint_queries".to_string());
                features
            },
            Vec::new(),
        ),
        FrameworkQueryConfig::new(
            "servlet_jsp",
            &["servlet_jsp", "servlet-jsp", "servlet", "jsp"],
            union_of(
                GENERIC_LABELS,
                    &[
                    "ServletJspModule",
                    "WebDescriptor",
                    "Servlet",
                    "ServletMapping",
                    "Filter",
                    "FilterMapping",
                    "Listener",
                    "JSPView",
                    "JspTag",
                    "JspExpression",
                    "ApiEndpoint",
                    "StateSlot",
                    "LifecycleEvent",
                    "SecurityConstraint",
                    "ErrorPage",
                    "WelcomePage",
                    "Authority",
                    "WebTarget",
                    "WebConfiguration",
                ],
            ),
            &[
                "SEMANTIC_OF",
                "HANDLES",
                "MAPS_TO",
                "PASSES_THROUGH",
                "FORWARDS_TO",
                "READS",
                "WRITES",
                "RESOLVES_TO",
                "USES",
                "DECLARES",
                "PROTECTS",
            ],
            chain_slices(&[GENERIC_SEARCHABLE_PROPERTIES, &["raw_value", "resolved_value", "url_pattern", "http_method"]]).iter().map(|item| item.to_string()).collect(),
            true,
            "cplus",
            "full",
            Vec::new(),
            {
                let mut features = framework.clone();
                features.insert("endpoint_queries".to_string());
                features
            },
            Vec::new(),
        ),
        FrameworkQueryConfig::new(
            "mybatis",
            &["mybatis", "my-batis"],
            union_of(
                GENERIC_LABELS,
                    &[
                    "MyBatisModule",
                    "MyBatisArtifact",
                    "MyBatisMapper",
                    "MyBatisMapperMethod",
                    "MyBatisParameter",
                    "MyBatisJavaProperty",
                    "MyBatisXmlDocument",
                    "MyBatisStatement",
                    "MyBatisSqlFragment",
                    "MyBatisResultMap",
                    "MyBatisResultMapping",
                    "MyBatisInclude",
                    "MyBatisDynamicNode",
                    "MyBatisConfig",
                    "MyBatisSqlStatement",
                    "DatabaseTable",
                    "DatabaseColumn",
                    "MyBatisSqlJoin",
                    "MyBatisSqlParameter",
                    "MyBatisSqlProvider",
                    "MyBatisSpringBridge",
                    "MyBatisExtension",
                    "MyBatisCache",
                ],
            ),
            &[
                "SEMANTIC_OF",
                "DECLARES_METHOD",
                "DECLARES_STATEMENT",
                "BINDS_STATEMENT",
                "READS_FROM",
                "WRITES_TO",
                "REFERENCES_TABLE",
                "REFERENCES_COLUMN",
                "JOINS_WITH",
                "DEPENDS_ON_PARAMETER",
                "USES_RESULT_MAP",
                "HAS_RESULT_MAPPING",
                "MAPS_PROPERTY",
                "MAPS_COLUMN",
                "NESTED_SELECT",
                "HAS_ASSOCIATION",
                "HAS_COLLECTION",
                "EXTENDS_RESULT_MAP",
            ],
            chain_slices(&[GENERIC_SEARCHABLE_PROPERTIES, &["raw_value", "resolved_value", "sql"]]).iter().map(|item| item.to_string()).collect(),
            false,
            "cplus",
            "full",
            Vec::new(),
            {
                let mut features = framework.clone();
                features.insert("persistence_queries".to_string());
                features
            },
            Vec::new(),
        ),
        FrameworkQueryConfig::new(
            "struts",
            &["struts", "struts2", "apache-struts", "apache_struts"],
            union_of(
                GENERIC_LABELS,
                    &[
                    "StrutsFact",
                    "Plugin",
                    "Package",
                    "Action",
                    "HttpEndpoint",
                    "InterceptorStack",
                    "Interceptor",
                    "Result",
                    "ResultType",
                    "View",
                    "ExceptionMapping",
                    "ValidationRule",
                ],
            ),
            &[
                "CONTAINS",
                "EXTENDS",
                "MAPPED_TO",
                "PASSES_THROUGH",
                "USES_INTERCEPTOR_STACK",
                "RETURNS_RESULT",
                "INSTANCE_OF",
                "RESOLVES_TO",
                "CHAINS_TO",
                "REDIRECTS_TO",
                "HANDLES_EXCEPTION",
                "VALIDATES_WITH",
            ],
            chain_slices(&[GENERIC_SEARCHABLE_PROPERTIES, &[
                    "route",
                    "class_name",
                    "method",
                    "namespace",
                    "result_type",
                    "location",
                    "validator_type",
                ]]).iter().map(|item| item.to_string()).collect(),
            false,
            "cplus",
            "full",
            Vec::new(),
            {
                let mut features = framework.clone();
                features.insert("endpoint_queries".to_string());
                features
            },
            Vec::new(),
        ),
        FrameworkQueryConfig::new(
            "flutter",
            &["dart", "flutter", "flutter-dart", "flutter_dart"],
            set_of(GENERIC_LABELS),
            &["CONTAINS", "IMPORTS", "EXPORTS", "EXTENDS", "CALLS"],
            chain_slices(&[GENERIC_SEARCHABLE_PROPERTIES, &["package_name", "class_name"]]).iter().map(|item| item.to_string()).collect(),
            false,
            "cplus",
            "full",
            Vec::new(),
            framework.clone(),
            Vec::new(),
        ),
        FrameworkQueryConfig::new(
            "aspnet_framework",
            &[
                "aspnet_framework",
                "aspnet-framework",
                "asp.net-framework",
                "aspnetframework",
            ],
            union_of(
                GENERIC_LABELS,
                    &[
                    "HttpEndpoint",
                    "Route",
                    "Middleware",
                    "Controller",
                    "Action",
                    "RazorPage",
                    "PageHandler",
                    "WebFormPage",
                    "HttpHandler",
                    "HttpModule",
                    "Filter",
                    "Result",
                    "View",
                    "Layout",
                    "PartialView",
                    "Service",
                    "Repository",
                    "Model",
                    "ViewModel",
                    "ValidationRule",
                    "ConfigurationKey",
                    "SessionState",
                    "ApplicationEvent",
                    "AuthenticationScheme",
                    "AuthorizationPolicy",
                ],
            ),
            &[
                "SEMANTIC_OF",
                "MAPPED_TO",
                "HANDLED_BY",
                "PASSES_THROUGH",
                "INVOKES",
                "INJECTS",
                "VALIDATES_WITH",
                "RENDERS",
                "REDIRECTS_TO",
                "FORWARDS_TO",
                "LOADS_FROM",
                "DEPENDS_ON",
                "READS_CONFIG",
                "WRITES_SESSION",
                "POSTS_BACK_TO",
                "INITIALIZES",
                "RETURNS_RESULT",
            ],
            chain_slices(&[GENERIC_SEARCHABLE_PROPERTIES, &[
                    "route",
                    "http_method",
                    "config_key",
                    "resolution_status",
                    "framework",
                ]]).iter().map(|item| item.to_string()).collect(),
            true,
            "cplus",
            "full",
            Vec::new(),
            {
                let mut features = framework.clone();
                features.insert("endpoint_queries".to_string());
                features
            },
            Vec::new(),
        ),
        FrameworkQueryConfig::new(
            "aspnet_core",
            &["aspnet_core", "aspnet-core", "asp.net-core", "aspnetcore"],
            union_of(
                GENERIC_LABELS,
                    &[
                    "HttpEndpoint",
                    "Route",
                    "Middleware",
                    "Controller",
                    "Action",
                    "RazorPage",
                    "PageHandler",
                    "WebFormPage",
                    "HttpHandler",
                    "HttpModule",
                    "Filter",
                    "Result",
                    "View",
                    "Layout",
                    "PartialView",
                    "Service",
                    "Repository",
                    "Model",
                    "ViewModel",
                    "ValidationRule",
                    "ConfigurationKey",
                    "SessionState",
                    "ApplicationEvent",
                    "AuthenticationScheme",
                    "AuthorizationPolicy",
                ],
            ),
            &[
                "SEMANTIC_OF",
                "MAPPED_TO",
                "HANDLED_BY",
                "PASSES_THROUGH",
                "INVOKES",
                "INJECTS",
                "VALIDATES_WITH",
                "RENDERS",
                "REDIRECTS_TO",
                "FORWARDS_TO",
                "LOADS_FROM",
                "DEPENDS_ON",
                "READS_CONFIG",
                "WRITES_SESSION",
                "POSTS_BACK_TO",
                "INITIALIZES",
                "RETURNS_RESULT",
            ],
            chain_slices(&[GENERIC_SEARCHABLE_PROPERTIES, &[
                    "route",
                    "http_method",
                    "config_key",
                    "resolution_status",
                    "framework",
                    "position",
                    "lifetime",
                ]]).iter().map(|item| item.to_string()).collect(),
            true,
            "cplus",
            "full",
            Vec::new(),
            {
                let mut features = framework.clone();
                features.insert("endpoint_queries".to_string());
                features
            },
            Vec::new(),
        ),
    ];

    let mut map: BTreeMap<String, FrameworkQueryConfig> = BTreeMap::new();
    for config in capabilities {
        map.insert(config.name.clone(), config);
    }
    let alias_index = validate_capability_registry(&map).expect("capability registry invariants");
    Registry {
        capabilities: map,
        alias_index,
    }
}

fn registry() -> &'static Registry {
    static REGISTRY: OnceLock<Registry> = OnceLock::new();
    REGISTRY.get_or_init(build_registry)
}

/// `validate_capability_registry` — validate registry invariants and return
/// its deterministic alias index (alias → canonical parser name).
pub fn validate_capability_registry(
    capabilities: &BTreeMap<String, FrameworkQueryConfig>,
) -> Result<BTreeMap<String, String>, String> {
    let allowed_backends = ["android", "cplus"];
    let allowed_support_levels = ["full", "partial", "generic"];
    let allowed_dimension_levels = ["full", "partial", "generic", "none"];
    let mut aliases: BTreeMap<String, String> = BTreeMap::new();
    let mut errors: Vec<String> = Vec::new();
    for (key, config) in capabilities {
        if *key != config.name {
            errors.push(format!(
                "Registry key '{key}' must equal canonical name '{}'.",
                config.name
            ));
        }
        if !config.aliases.contains(&config.name) {
            errors.push(format!(
                "Canonical parser '{}' must be one of its aliases.",
                config.name
            ));
        }
        if !allowed_backends.contains(&config.backend.as_str()) {
            errors.push(format!(
                "Parser '{}' references unsupported backend '{}'.",
                config.name, config.backend
            ));
        }
        if !allowed_support_levels.contains(&config.support_level.as_str()) {
            errors.push(format!(
                "Parser '{}' has invalid support level '{}'.",
                config.name, config.support_level
            ));
        }
        let expected: BTreeSet<&str> = SUPPORT_DIMENSIONS.iter().copied().collect();
        let dimensions_match = config.support.len() == SUPPORT_DIMENSIONS.len()
            && config.support.keys().all(|key| expected.contains(key.as_str()));
        if !dimensions_match {
            errors.push(format!(
                "Parser '{}' must declare support for {:?}.",
                config.name, SUPPORT_DIMENSIONS
            ));
        }
        let invalid_support: Vec<String> = config
            .support
            .iter()
            .filter(|(_, value)| !allowed_dimension_levels.contains(&value.as_str()))
            .map(|(key, value)| format!("{key}: {value}"))
            .collect();
        if !invalid_support.is_empty() {
            errors.push(format!(
                "Parser '{}' has invalid dimensional support {{{}}}.",
                config.name,
                invalid_support.join(", ")
            ));
        }
        let identifiers = config
            .labels
            .iter()
            .chain(config.relationships.iter())
            .chain(config.searchable_properties.iter())
            .chain(config.profiles.values().flatten())
            .cloned()
            .collect::<BTreeSet<String>>();
        let invalid: Vec<String> = identifiers
            .into_iter()
            .filter(|value| !is_identifier(value))
            .collect();
        if !invalid.is_empty() {
            errors.push(format!(
                "Parser '{}' has invalid graph identifiers: {:?}.",
                config.name, invalid
            ));
        }
        for alias in &config.aliases {
            if let Some(previous) = aliases.get(alias)
                && previous != &config.name
            {
                errors.push(format!(
                    "Parser alias '{alias}' collides between '{previous}' and '{}'.",
                    config.name
                ));
            }
            aliases.insert(alias.clone(), config.name.clone());
        }
    }
    if errors.is_empty() {
        Ok(aliases)
    } else {
        Err(format!(
            "Invalid MCP capability registry: {}",
            errors.join(" ")
        ))
    }
}

/// `_IDENTIFIER_RE = ^[A-Za-z_][A-Za-z0-9_]*$`.
fn is_identifier(value: &str) -> bool {
    let mut characters = value.chars();
    match characters.next() {
        Some(first) if first.is_ascii_alphabetic() || first == '_' => {}
        _ => return false,
    }
    characters.all(|character| character.is_ascii_alphanumeric() || character == '_')
}

/// `capability_for_parser`.
pub fn capability_for_parser(parser_type: Option<&str>) -> Option<&'static FrameworkQueryConfig> {
    let parser = parser_type.unwrap_or_default().trim().to_lowercase();
    let name = registry().alias_index.get(&parser)?;
    registry().capabilities.get(name)
}

/// `framework_for_parser` — capability restricted to profiles that support
/// framework queries.
pub fn framework_for_parser(parser_type: Option<&str>) -> Option<&'static FrameworkQueryConfig> {
    let config = capability_for_parser(parser_type)?;
    config
        .features
        .contains("framework_query")
        .then_some(config)
}

/// `parser_aliases` — every alias, optionally scoped to one backend.
pub fn parser_aliases(backend: Option<&str>) -> Vec<String> {
    let backend_name = backend.unwrap_or_default().trim().to_lowercase();
    registry()
        .alias_index
        .iter()
        .filter(|(_, name)| backend_name.is_empty() || {
            registry()
                .capabilities
                .get(*name)
                .map(|config| config.backend == backend_name)
                .unwrap_or(false)
        })
        .map(|(alias, _)| alias.clone())
        .collect()
}

/// `searchable_labels` — sorted label list for a parser, or the union across
/// all parsers.
pub fn searchable_labels(parser_type: Option<&str>) -> Vec<String> {
    match capability_for_parser(parser_type) {
        Some(config) => config.labels.iter().cloned().collect(),
        None => registry()
            .capabilities
            .values()
            .flat_map(|config| config.labels.iter().cloned())
            .collect::<BTreeSet<String>>()
            .into_iter()
            .collect(),
    }
}

/// `searchable_properties` — profile list, or order-stable union.
pub fn searchable_properties(parser_type: Option<&str>) -> Vec<String> {
    match capability_for_parser(parser_type) {
        Some(config) => config.searchable_properties.clone(),
        None => {
            let mut seen = BTreeSet::new();
            let mut result = Vec::new();
            for config in registry().capabilities.values() {
                for property in &config.searchable_properties {
                    if seen.insert(property.clone()) {
                        result.push(property.clone());
                    }
                }
            }
            result
        }
    }
}

/// `text_search_properties` — searchable properties safe inside
/// `toLower(coalesce(...))` predicates.
pub fn text_search_properties(parser_type: Option<&str>) -> Vec<String> {
    searchable_properties(parser_type)
        .into_iter()
        .filter(|property| !NON_TEXT_SEARCH_PROPERTIES.contains(&property.as_str()))
        .collect()
}

/// `backend_text_property_union` — property union filtered to text-safe
/// properties (`NON_TEXT_SEARCH_PROPERTIES` excluded).
pub fn backend_text_property_union(backend: Option<&str>) -> Vec<String> {
    backend_property_union(backend)
        .into_iter()
        .filter(|property| !NON_TEXT_SEARCH_PROPERTIES.contains(&property.as_str()))
        .collect()
}

/// `backend_label_union` — every searchable label mapped to `backend`.
pub fn backend_label_union(backend: Option<&str>) -> Vec<String> {
    let backend_name = backend.unwrap_or_default().trim().to_lowercase();
    registry()
        .capabilities
        .values()
        .filter(|config| backend_name.is_empty() || config.backend == backend_name)
        .flat_map(|config| config.labels.iter().cloned())
        .collect::<BTreeSet<String>>()
        .into_iter()
        .collect()
}

/// `backend_property_union` — every searchable property mapped to `backend`
/// (order-stable).
pub fn backend_property_union(backend: Option<&str>) -> Vec<String> {
    let backend_name = backend.unwrap_or_default().trim().to_lowercase();
    let mut seen = BTreeSet::new();
    let mut result = Vec::new();
    for config in registry().capabilities.values() {
        if !backend_name.is_empty() && config.backend != backend_name {
            continue;
        }
        for property in &config.searchable_properties {
            if seen.insert(property.clone()) {
                result.push(property.clone());
            }
        }
    }
    result
}

/// `default_relationships`.
pub fn default_relationships(parser_type: Option<&str>, tool_name: Option<&str>) -> Vec<String> {
    match capability_for_parser(parser_type) {
        Some(config) => config.relationships_for(tool_name).to_vec(),
        None => CORE_RELATIONSHIPS.iter().map(|item| item.to_string()).collect(),
    }
}

/// `capability_catalog` — capability records sorted by canonical name.
pub fn capability_catalog() -> Vec<Value> {
    registry()
        .capabilities
        .values()
        .map(FrameworkQueryConfig::to_dict)
        .collect()
}

/// `schema_fingerprint` — order-independent fingerprint for an inspectable
/// graph schema (sha256, first 24 hex chars).
pub fn schema_fingerprint(
    available_labels: Option<&[String]>,
    available_relationships: Option<&[String]>,
) -> Option<String> {
    let labels: Vec<String> = available_labels?
        .iter()
        .filter_map(|value| {
            let trimmed = value.trim();
            (!trimmed.is_empty()).then(|| trimmed.to_string())
        })
        .collect::<BTreeSet<String>>()
        .into_iter()
        .collect();
    let relationships: Vec<String> = available_relationships?
        .iter()
        .filter_map(|value| {
            let trimmed = value.trim();
            (!trimmed.is_empty()).then(|| trimmed.to_uppercase())
        })
        .collect::<BTreeSet<String>>()
        .into_iter()
        .collect();
    let material = format!(
        "v{CAPABILITY_CONTRACT_VERSION}|labels={}|relationships={}",
        labels.join("\u{1f}"),
        relationships.join("\u{1f}"),
    );
    let digest = Sha256::digest(material.as_bytes());
    Some(hex_prefix(&digest, 24))
}

fn hex_prefix(digest: &[u8], length: usize) -> String {
    let mut out = String::with_capacity(length);
    for byte in digest {
        out.push_str(&format!("{byte:02x}"));
        if out.len() >= length {
            break;
        }
    }
    out.truncate(length);
    out
}

/// `capability_schema_contract` — provider-schema evidence contract for one
/// parser profile. Returns dimension → `{labels_any, relationships_any}`.
pub fn capability_schema_contract(
    capability: &FrameworkQueryConfig,
) -> BTreeMap<String, (Vec<String>, Vec<String>)> {
    let profile_labels: &BTreeSet<String> = &capability.labels;
    let profile_relationships: BTreeSet<String> =
        capability.relationships_for(None).iter().cloned().collect();
    let mut contracts = BTreeMap::new();
    for dimension in SUPPORT_DIMENSIONS {
        let evidence_labels = DIMENSION_LABEL_EVIDENCE
            .iter()
            .find(|(name, _)| **name == **dimension)
            .map(|(_, labels)| *labels)
            .unwrap_or(&[]);
        let evidence_relationships = DIMENSION_RELATIONSHIP_EVIDENCE
            .iter()
            .find(|(name, _)| **name == **dimension)
            .map(|(_, relationships)| *relationships)
            .unwrap_or(&[]);
        let label_candidates: Vec<String> = if *dimension == "symbols" {
            profile_labels.iter().cloned().collect()
        } else {
            profile_labels
                .iter()
                .filter(|label| evidence_labels.contains(&label.as_str()))
                .cloned()
                .collect()
        };
        let relationship_candidates: Vec<String> = profile_relationships
            .iter()
            .filter(|relationship| evidence_relationships.contains(&relationship.as_str()))
            .cloned()
            .collect();
        contracts.insert(
            dimension.to_string(),
            (
                label_candidates.into_iter().collect(),
                relationship_candidates.into_iter().collect(),
            ),
        );
    }
    contracts
}

/// `evaluate_capability_schema` — compare advertised parser support with the
/// active provider schema.
pub fn evaluate_capability_schema(
    capability: &FrameworkQueryConfig,
    available_labels: Option<&[String]>,
    available_relationships: Option<&[String]>,
) -> Value {
    let label_set: Option<BTreeSet<String>> = available_labels.map(|labels| {
        labels
            .iter()
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
            .collect()
    });
    let relationship_set: Option<BTreeSet<String>> = available_relationships.map(|relationships| {
        relationships
            .iter()
            .map(|value| value.trim().to_uppercase())
            .filter(|value| !value.is_empty())
            .collect()
    });
    let contracts = capability_schema_contract(capability);
    let mut dimensions = Map::new();
    for dimension in SUPPORT_DIMENSIONS {
        let advertised = capability
            .support
            .get(*dimension)
            .cloned()
            .unwrap_or_default();
        let (required_labels, required_relationships) = contracts
            .get(*dimension)
            .cloned()
            .unwrap_or_else(|| (Vec::new(), Vec::new()));

        let dimension_value = if advertised == "none" {
            json!({
                "advertised": advertised,
                "observed": "not_applicable",
                "effective": "none",
                "labels_any": required_labels,
                "relationships_any": required_relationships,
                "matched_labels": [],
                "matched_relationships": [],
                "missing_labels_any": [],
                "missing_relationships_any": [],
            })
        } else {
            let matched_labels: Vec<String> = match &label_set {
                Some(labels) => required_labels
                    .iter()
                    .filter(|label| labels.contains(*label))
                    .cloned()
                    .collect(),
                None => Vec::new(),
            };
            let matched_relationships: Vec<String> = match &relationship_set {
                Some(relationships) => required_relationships
                    .iter()
                    .filter(|relationship| relationships.contains(*relationship))
                    .cloned()
                    .collect(),
                None => Vec::new(),
            };
            // checks: None entries mean "schema not inspectable".
            let mut checks: Vec<Option<bool>> = Vec::new();
            if !required_labels.is_empty() {
                checks.push(label_set.as_ref().map(|_| !matched_labels.is_empty()));
            }
            if !required_relationships.is_empty() {
                checks.push(
                    relationship_set
                        .as_ref()
                        .map(|_| !matched_relationships.is_empty()),
                );
            }
            if checks.is_empty() {
                checks.push(Some(false));
            }
            let (observed, effective) = if checks.iter().any(Option::is_none) {
                ("unknown", "unknown")
            } else if checks.iter().all(|check| check == &Some(true)) {
                ("available", advertised.as_str())
            } else {
                ("unavailable", "none")
            };
            let missing_labels_any: Vec<String> = if !required_labels.is_empty()
                && label_set.is_some()
                && matched_labels.is_empty()
            {
                required_labels.clone()
            } else {
                Vec::new()
            };
            let missing_relationships_any: Vec<String> = if !required_relationships.is_empty()
                && relationship_set.is_some()
                && matched_relationships.is_empty()
            {
                required_relationships.clone()
            } else {
                Vec::new()
            };
            json!({
                "advertised": advertised,
                "observed": observed,
                "effective": effective,
                "labels_any": required_labels,
                "relationships_any": required_relationships,
                "matched_labels": matched_labels,
                "matched_relationships": matched_relationships,
                "missing_labels_any": missing_labels_any,
                "missing_relationships_any": missing_relationships_any,
            })
        };
        dimensions.insert(dimension.to_string(), dimension_value);
    }

    let schema_status = match (&label_set, &relationship_set) {
        (Some(_), Some(_)) => "available",
        (None, None) => "unavailable",
        _ => "partial",
    };
    json!({
        "contract_version": CAPABILITY_CONTRACT_VERSION,
        "schema_status": schema_status,
        "schema_fingerprint": schema_fingerprint(
            available_labels,
            available_relationships,
        ),
        "dimensions": Value::Object(dimensions),
    })
}

/// `servlet_active_generation_predicate` — provider-scoped predicate used by
/// generation-scoped framework queries.
pub fn servlet_active_generation_predicate(alias: &str) -> String {
    let provider = std::env::var("CODE_GRAPH_PROVIDER")
        .or_else(|_| std::env::var("GRAPH_PROVIDER"))
        .unwrap_or_else(|_| "falkordb".to_string())
        .trim()
        .to_lowercase();
    if matches!(provider.as_str(), "falkor" | "falkordb" | "falkor-db") {
        // FalkorDB cleanup removes inactive generations during promotion.
        return "true".to_string();
    }
    format!(
        "(coalesce({alias}.framework, '') <> 'servlet_jsp' OR EXISTS {{ \
         MATCH (state:ServletJspAnalysisState {{project_id: {alias}.project_id, module_id: {alias}.module_id}}) \
         WHERE state.active_generation = {alias}.generation_id }})"
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn query_engine_mapping() {
        assert_eq!(query_engine_for_backend(Some("android")), "android_graph");
        assert_eq!(query_engine_for_backend(Some("cplus")), "graph_generic");
        assert_eq!(query_engine_for_backend(Some("fast")), "fast_graph");
        assert_eq!(query_engine_for_backend(Some("unknown")), "graph_generic");
        assert_eq!(query_engine_for_backend(None), "graph_generic");
        assert_eq!(query_engine_for_backend(Some("")), "graph_generic");
    }

    #[test]
    fn alias_index_is_complete() {
        assert_eq!(
            capability_for_parser(Some("pro*c")).map(|config| config.name.as_str()),
            Some("cplus")
        );
        assert_eq!(
            capability_for_parser(Some(" Kotlin-Android ")).map(|config| config.name.as_str()),
            Some("android")
        );
        assert!(capability_for_parser(Some("bogus_parser")).is_none());
    }

    #[test]
    fn parser_count_and_support_matrix() {
        let catalog = capability_catalog();
        assert_eq!(catalog.len(), 27);
        let android = capability_for_parser(Some("android")).unwrap();
        assert_eq!(android.support.get("endpoints").map(String::as_str), Some("none"));
        let python = capability_for_parser(Some("python")).unwrap();
        assert_eq!(python.support.get("calls").map(String::as_str), Some("partial"));
        assert_eq!(python.support.get("database").map(String::as_str), Some("none"));
        let sql = capability_for_parser(Some("sql")).unwrap();
        assert_eq!(sql.support.get("database").map(String::as_str), Some("full"));
        // servlet_active_generation_predicate etc. carry generation scoping.
        assert!(capability_for_parser(Some("servlet_jsp")).unwrap().generation_scoped);
    }

    #[test]
    fn default_relationships_for_tool_profiles() {
        // cplus `default` profile = CORE + non-context CPLUS relationships.
        let expected: Vec<String> = dedup_keep_order(
            CORE_RELATIONSHIPS
                .iter()
                .copied()
                .chain(CPLUS_RELATIONSHIPS.iter().copied())
                .filter(|relationship| !CONTEXT_RELATIONSHIPS.contains(relationship)),
        );
        assert_eq!(
            default_relationships(Some("cplus"), Some("query_subgraph")),
            expected
        );
        assert_eq!(
            default_relationships(Some("cplus"), Some("find_callers_of_endpoint")),
            vec!["CALLS_API".to_string(), "MATCHES".to_string()]
        );
        assert_eq!(
            default_relationships(None, None),
            CORE_RELATIONSHIPS.iter().map(|item| item.to_string()).collect::<Vec<_>>()
        );
    }

    #[test]
    fn text_search_properties_exclude_non_text() {
        let properties = text_search_properties(Some("cplus"));
        assert!(properties.contains(&"name".to_string()));
        assert!(!properties.contains(&"is_public_api".to_string()));
        assert!(!properties.contains(&"parse_depth".to_string()));
    }

    #[test]
    fn schema_fingerprint_is_stable_and_order_independent() {
        let labels: Vec<String> = vec!["Function".into(), "Class".into()];
        let relationships: Vec<String> = vec!["calls".into(), "DECLARES".into()];
        let flipped: Vec<String> = vec!["Class".into(), "Function".into()];
        let a = schema_fingerprint(Some(&labels), Some(&relationships)).unwrap();
        let b = schema_fingerprint(Some(&flipped), Some(&relationships)).unwrap();
        assert_eq!(a, b);
        assert_eq!(a.len(), 24);
        assert!(schema_fingerprint(None, Some(&relationships)).is_none());
    }

    #[test]
    fn evaluate_schema_reports_unavailable_dimension() {
        let capability = capability_for_parser(Some("cplus")).unwrap();
        let labels: Vec<String> = vec!["File".into(), "Function".into()];
        let relationships: Vec<String> = vec!["CALLS".into()];
        let evaluation = evaluate_capability_schema(capability, Some(&labels), Some(&relationships));
        assert_eq!(evaluation["schema_status"], json!("available"));
        let symbols = &evaluation["dimensions"]["symbols"];
        assert_eq!(symbols["effective"], json!("full"));
        assert_eq!(symbols["observed"], json!("available"));
        // cplus advertises endpoints=none (no endpoint_queries feature) —
        // short-circuits as not_applicable.
        let endpoints = &evaluation["dimensions"]["endpoints"];
        assert_eq!(endpoints["advertised"], json!("none"));
        assert_eq!(endpoints["effective"], json!("none"));
        // cplus advertises database=full (SQL labels/relationships) but the
        // observed schema lacks any database evidence → unavailable.
        let database = &evaluation["dimensions"]["database"];
        assert_eq!(database["advertised"], json!("full"));
        assert_eq!(database["observed"], json!("unavailable"));
        assert_eq!(database["effective"], json!("none"));
    }

    #[test]
    fn servlet_predicate_falkordb_short_circuits() {
        // Reading env only; do not mutate process env (edition 2024 marks
        // env mutation unsafe). The default provider here is falkordb.
        let predicate = servlet_active_generation_predicate("node");
        let provider = std::env::var("CODE_GRAPH_PROVIDER")
            .or_else(|_| std::env::var("GRAPH_PROVIDER"))
            .unwrap_or_else(|_| "falkordb".to_string())
            .trim()
            .to_lowercase();
        if matches!(provider.as_str(), "falkor" | "falkordb" | "falkor-db" | "") {
            assert_eq!(predicate, "true");
        }
    }
}
