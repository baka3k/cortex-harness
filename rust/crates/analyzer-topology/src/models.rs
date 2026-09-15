//! Port `tools/project_topology/models.py` — typed facts + Python-compatible
//! value model.
//!
//! `PyValue` giữ **insertion order** của dict/list Python vì `str(properties)`
//! (resolver marker text) và `safe_summary` phụ thuộc repr Python; serde_json
//! Map (BTreeMap) chỉ dùng ở biên write/summary nơi key được sort.

use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

pub const SCHEMA_VERSION: &str = "1.0";
pub const ANALYZER_VERSION: &str = "1.0";

// ── enum values (string-valued như Python _ValueEnum) ──

pub mod module_kind {
    pub const ROOT: &str = "root";
    pub const ANDROID_APPLICATION: &str = "android_application";
    pub const ANDROID_DYNAMIC_FEATURE: &str = "android_dynamic_feature";
    pub const ANDROID_LIBRARY: &str = "android_library";
    pub const JVM_APPLICATION: &str = "jvm_application";
    pub const JVM_LIBRARY: &str = "jvm_library";
    pub const MAVEN_MODULE: &str = "maven_module";
    pub const NATIVE_EXECUTABLE: &str = "native_executable";
    pub const NATIVE_LIBRARY: &str = "native_library";
    pub const PACKAGE: &str = "package";
    pub const DATABASE: &str = "database";
    pub const TEST: &str = "test";
    pub const UNKNOWN: &str = "unknown";
}

pub mod descriptor_type {
    pub const GRADLE_SETTINGS: &str = "gradle_settings";
    pub const GRADLE_BUILD: &str = "gradle_build";
    pub const MAVEN_POM: &str = "maven_pom";
    pub const ANT_BUILD: &str = "ant_build";
    pub const CMAKE: &str = "cmake";
    pub const MAKE: &str = "make";
    pub const ANDROID_MANIFEST: &str = "android_manifest";
    pub const PROTOBUF: &str = "protobuf";
    pub const PACKAGE_MANIFEST: &str = "package_manifest";
    pub const RUNTIME_CONFIG: &str = "runtime_config";
    pub const RESOURCE: &str = "resource";
    pub const UNKNOWN: &str = "unknown";
}

pub mod descriptor_role {
    pub const IDENTITY: &str = "identity";
    pub const TOPOLOGY: &str = "topology";
    pub const DEPENDENCY: &str = "dependency";
    pub const CONFIGURATION: &str = "configuration";
    pub const FRAMEWORK: &str = "framework";
    pub const INTERFACE: &str = "interface";
    pub const RESOURCE: &str = "resource";
    pub const DEPLOYMENT: &str = "deployment";
    pub const GENERATED: &str = "generated";
    pub const SECRET_BEARING: &str = "secret-bearing";
}

pub mod parse_depth {
    pub const IDENTITY: &str = "identity";
    pub const TOPOLOGY: &str = "topology";
    pub const DEPENDENCY: &str = "dependency";
    pub const SEMANTIC: &str = "semantic";
    pub const UNSUPPORTED: &str = "unsupported";
}

pub mod dependency_scope {
    pub const COMPILE: &str = "compile";
    pub const RUNTIME: &str = "runtime";
    pub const TEST: &str = "test";
    pub const PROVIDED: &str = "provided";
    pub const PLUGIN: &str = "plugin";
    pub const BUILD: &str = "build";
    pub const UNKNOWN: &str = "unknown";
}

pub mod confidence {
    pub const HIGH: &str = "high";
    pub const MEDIUM: &str = "medium";
    pub const LOW: &str = "low";
    #[allow(dead_code)]
    pub const UNKNOWN: &str = "unknown";
}

pub mod diagnostic_code {
    pub const MALFORMED_DESCRIPTOR: &str = "malformed_descriptor";
    pub const DESCRIPTOR_TOO_LARGE: &str = "descriptor_too_large";
    pub const DYNAMIC_EXPRESSION: &str = "dynamic_expression";
    pub const UNRESOLVED_REFERENCE: &str = "unresolved_reference";
    pub const AMBIGUOUS_MODULE_KIND: &str = "ambiguous_module_kind";
    pub const MODULE_PATH_ESCAPE: &str = "module_path_escape";
    pub const UNSUPPORTED_CONSTRUCT: &str = "unsupported_construct";
    pub const SECRET_REDACTED: &str = "secret_redacted";
    pub const IO_ERROR: &str = "io_error";
    pub const XML_UNSAFE: &str = "xml_unsafe";
    pub const LIMIT_EXCEEDED: &str = "limit_exceeded";
}

// ── PyValue — giá trị kiểu Python với insertion-ordered dict ──

#[derive(Debug, Clone, PartialEq)]
pub enum PyValue {
    #[allow(dead_code)]
    None,
    Bool(bool),
    Int(i64),
    #[allow(dead_code)]
    Float(f64),
    Str(String),
    List(Vec<PyValue>),
    Dict(Vec<(String, PyValue)>),
}

impl PyValue {
    pub fn dict() -> PyValue {
        PyValue::Dict(Vec::new())
    }

    pub fn set(&mut self, key: &str, value: PyValue) {
        if let PyValue::Dict(entries) = self {
            entries.push((key.to_string(), value));
        }
    }

    pub fn get(&self, key: &str) -> Option<&PyValue> {
        match self {
            PyValue::Dict(entries) => {
                entries.iter().find(|(k, _)| k == key).map(|(_, v)| v)
            }
            _ => None,
        }
    }

    pub fn get_mut(&mut self, key: &str) -> Option<&mut PyValue> {
        match self {
            PyValue::Dict(entries) => {
                entries.iter_mut().find(|(k, _)| k == key).map(|(_, v)| v)
            }
            _ => None,
        }
    }

    pub fn as_str(&self) -> &str {
        match self {
            PyValue::Str(text) => text,
            _ => "",
        }
    }

    /// Python truthiness cho `properties.get(key)` guards.
    pub fn truthy(&self) -> bool {
        match self {
            PyValue::None => false,
            PyValue::Bool(value) => *value,
            PyValue::Int(value) => *value != 0,
            PyValue::Float(value) => *value != 0.0,
            PyValue::Str(text) => !text.is_empty(),
            PyValue::List(items) => !items.is_empty(),
            PyValue::Dict(entries) => !entries.is_empty(),
        }
    }

    /// `isinstance(value, (list, tuple, set))` — chỉ List của ta.
    pub fn as_list(&self) -> Option<&Vec<PyValue>> {
        match self {
            PyValue::List(items) => Some(items),
            _ => None,
        }
    }

    pub fn to_json(&self) -> Value {
        match self {
            PyValue::None => Value::Null,
            PyValue::Bool(value) => Value::Bool(*value),
            PyValue::Int(value) => Value::Number((*value).into()),
            PyValue::Float(value) => serde_json::Number::from_f64(*value)
                .map(Value::Number)
                .unwrap_or(Value::Null),
            PyValue::Str(text) => Value::String(text.clone()),
            PyValue::List(items) => Value::Array(items.iter().map(PyValue::to_json).collect()),
            PyValue::Dict(entries) => {
                let mut map = Map::new();
                for (key, value) in entries {
                    map.insert(key.clone(), value.to_json());
                }
                Value::Object(map)
            }
        }
    }

    /// `repr()` của Python cho str/int/bool/None/list/dict (resolver marker
    /// text dùng `str(properties)`).
    pub fn repr(&self) -> String {
        match self {
            PyValue::None => "None".to_string(),
            PyValue::Bool(true) => "True".to_string(),
            PyValue::Bool(false) => "False".to_string(),
            PyValue::Int(value) => value.to_string(),
            PyValue::Float(value) => {
                let text = format!("{value}");
                if text.contains('.') {
                    text
                } else {
                    format!("{text}.0")
                }
            }
            PyValue::Str(text) => py_repr_str(text),
            PyValue::List(items) => {
                let inner: Vec<String> = items.iter().map(PyValue::repr).collect();
                format!("[{}]", inner.join(", "))
            }
            PyValue::Dict(entries) => {
                if entries.is_empty() {
                    return "{}".to_string();
                }
                let inner: Vec<String> = entries
                    .iter()
                    .map(|(key, value)| format!("{}: {}", py_repr_str(key), value.repr()))
                    .collect();
                format!("{{{}}}", inner.join(", "))
            }
        }
    }
}

/// `repr(string)` của Python — single quotes, escape \n \r \t \\ và quote.
pub fn py_repr_str(text: &str) -> String {
    let mut out = String::with_capacity(text.len() + 2);
    let has_single = text.contains('\'');
    let has_double = text.contains('"');
    let quote = if has_single && !has_double { '"' } else { '\'' };
    out.push(quote);
    for ch in text.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

// ── path helpers (models.py::normalize_module_path / posixpath.normpath) ──

/// `posixpath.normpath` — từng chữ theo CPython.
pub fn posix_normpath(path: &str) -> String {
    if path.is_empty() {
        return ".".to_string();
    }
    let mut initial_slashes = path.starts_with('/');
    if initial_slashes && path.starts_with("//") && !path.starts_with("///") {
        initial_slashes = true; // 2 slashes giữ nguyên dưới dạng "//"
    }
    let slash_count: usize = if path.starts_with("//") && !path.starts_with("///") {
        2
    } else if initial_slashes {
        1
    } else {
        0
    };
    let keep_two = initial_slashes && path.starts_with("//") && !path.starts_with("///");
    let mut new_comps: Vec<&str> = Vec::new();
    for comp in path.split('/') {
        if comp.is_empty() || comp == "." {
            continue;
        }
        if comp != ".." || (slash_count == 0 && new_comps.is_empty()) || new_comps.last() == Some(&"..")
        {
            new_comps.push(comp);
        } else if !new_comps.is_empty() {
            new_comps.pop();
        }
    }
    let mut joined = new_comps.join("/");
    if keep_two {
        joined = format!("//{joined}");
    } else if slash_count == 1 {
        joined = format!("/{joined}");
    }
    if joined.is_empty() {
        ".".to_string()
    } else {
        joined
    }
}

const fn is_drive_path(text: &str) -> bool {
    // ^[A-Za-z]:/
    let bytes = text.as_bytes();
    bytes.len() >= 3
        && bytes[0].is_ascii_alphabetic()
        && bytes[1] == b':'
        && bytes[2] == b'/'
}

/// `normalize_module_path` — repo-relative; path escape → Err(message).
pub fn normalize_module_path(value: &str) -> Result<String, String> {
    let text = value.trim().replace('\\', "/");
    if text.is_empty() || text == "." {
        return Ok(".".to_string());
    }
    if text.starts_with('/') || is_drive_path(&text) {
        return Err(format!(
            "module path must be repository-relative: {}",
            py_repr_str(value)
        ));
    }
    let normalized = posix_normpath(&text);
    if normalized.is_empty() || normalized == "." {
        return Ok(".".to_string());
    }
    if normalized == ".." || normalized.starts_with("../") {
        return Err(format!(
            "module path escapes repository root: {}",
            py_repr_str(value)
        ));
    }
    // Python: normalized.lstrip("./") — strip mọi ký tự '.'/'/' đầu chuỗi.
    Ok(normalized.trim_start_matches(['.', '/']).to_string())
}

/// Wrapper không-fail cho đường đã được chuẩn hoá trước đó (test/fixture).
pub fn normalize_ok(value: &str) -> String {
    normalize_module_path(value).unwrap_or_else(|_| ".".to_string())
}

// ── identity (project_scope + sha256) ──

/// `project_id_lookup_key` — casefold (lowercase cho ASCII set của ids).
pub fn project_id_lookup_key(value: Option<&str>) -> Option<String> {
    let raw = value?;
    let normalized = raw.trim();
    if normalized.is_empty() {
        None
    } else {
        Some(normalized.to_lowercase())
    }
}

/// `stable_module_id` — `project-module:{scope}:{module_path}`.
pub fn stable_module_id(project_id: &str, module_path: &str) -> Result<String, String> {
    let scope = project_id_lookup_key(Some(project_id))
        .ok_or_else(|| "project_id is required for module identity".to_string())?;
    let normalized = normalize_module_path(module_path)?;
    Ok(format!("project-module:{scope}:{normalized}"))
}

fn sha256_hex(material: &str) -> String {
    let digest = Sha256::digest(material.as_bytes());
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// `stable_fact_id` — `{kind}:{sha256(scope\x1fkind\x1fparts...)[:24]}`.
pub fn stable_fact_id(project_id: &str, kind: &str, parts: &[&str]) -> Result<String, String> {
    let scope = project_id_lookup_key(Some(project_id))
        .ok_or_else(|| "project_id is required for fact identity".to_string())?;
    let mut material = String::new();
    material.push_str(&scope);
    material.push('\x1f');
    material.push_str(kind.trim().to_lowercase().as_str());
    for part in parts {
        material.push('\x1f');
        material.push_str(part);
    }
    let hex = sha256_hex(&material);
    Ok(format!("{kind}:{}", &hex[..24]))
}

// ── safe_summary ──

const SECRET_PATTERN: &str = "(api[_-]?key|secret|token|password|passwd|private[_-]?key|credential)";

/// `safe_summary(value)` — collapse whitespace, redact secret-like, truncate.
pub fn safe_summary_limited(value: &str, limit: usize) -> String {
    let text = value.split_whitespace().collect::<Vec<_>>().join(" ");
    let re = regex::RegexBuilder::new(SECRET_PATTERN)
        .case_insensitive(true)
        .build()
        .expect("static regex");
    if re.is_match(&text) {
        return "[redacted]".to_string();
    }
    let cap = limit.max(1);
    // Python text[:limit] cắt theo code point.
    text.chars().take(cap).collect()
}

pub fn safe_summary(value: &str) -> String {
    safe_summary_limited(value, 240)
}

// ── fact structs + to_dict (asdict + _json_value) ──

#[derive(Debug, Clone)]
pub struct SourceEvidence {
    pub file_path: String,
    pub start_line: i64,
    pub end_line: Option<i64>,
    pub source: String,
}

impl SourceEvidence {
    pub fn new(file_path: &str) -> Self {
        SourceEvidence {
            file_path: file_path.to_string(),
            start_line: 1,
            end_line: None,
            source: "static".to_string(),
        }
    }

    pub fn at_line(file_path: &str, line: i64) -> Self {
        SourceEvidence {
            file_path: file_path.to_string(),
            start_line: line,
            end_line: None,
            source: "static".to_string(),
        }
    }

    pub fn to_dict(&self) -> Map<String, Value> {
        let mut map = Map::new();
        map.insert(
            "file_path".into(),
            Value::String(normalize_ok(&self.file_path)),
        );
        map.insert("start_line".into(), Value::from(self.start_line.max(1)));
        map.insert(
            "end_line".into(),
            self.end_line.map(Value::from).unwrap_or(Value::Null),
        );
        map.insert("source".into(), Value::String(self.source.clone()));
        map
    }
}

#[derive(Debug, Clone)]
pub struct AnalysisDiagnostic {
    pub code: &'static str,
    pub message: String,
    pub severity: &'static str,
    pub file_path: String,
    pub module_path: String,
    pub details: PyValue,
}

impl AnalysisDiagnostic {
    pub fn new(code: &'static str, message: &str) -> Self {
        AnalysisDiagnostic {
            code,
            message: message.to_string(),
            severity: "warning",
            file_path: String::new(),
            module_path: ".".to_string(),
            details: PyValue::dict(),
        }
    }

    pub fn severity(mut self, severity: &'static str) -> Self {
        self.severity = severity;
        self
    }

    pub fn file_path(mut self, file_path: &str) -> Self {
        self.file_path = file_path.to_string();
        self
    }

    pub fn module_path(mut self, module_path: &str) -> Self {
        self.module_path = module_path.to_string();
        self
    }

    pub fn details(mut self, details: PyValue) -> Self {
        self.details = details;
        self
    }

    pub fn to_dict(&self) -> Map<String, Value> {
        let mut map = Map::new();
        map.insert("code".into(), Value::String(self.code.to_string()));
        map.insert("message".into(), Value::String(self.message.clone()));
        map.insert("severity".into(), Value::String(self.severity.to_string()));
        map.insert(
            "file_path".into(),
            Value::String(if self.file_path.is_empty() {
                String::new()
            } else {
                normalize_ok(&self.file_path)
            }),
        );
        map.insert(
            "module_path".into(),
            Value::String(normalize_ok(&self.module_path)),
        );
        map.insert("details".into(), self.details.to_json());
        map
    }
}

#[derive(Debug, Clone)]
pub struct DescriptorFact {
    pub id: String,
    pub project_id: String,
    pub module_path: String,
    pub path: String,
    pub descriptor_type: &'static str,
    pub role: &'static str,
    pub parser: String,
    pub parse_depth: &'static str,
    pub parser_version: &'static str,
    pub canonical: bool,
    pub generated: bool,
    pub secret_bearing: bool,
    pub redacted: bool,
    pub summary: String,
    pub properties: PyValue,
    pub confidence: &'static str,
    pub evidence: Vec<SourceEvidence>,
    pub diagnostics: Vec<AnalysisDiagnostic>,
}

impl DescriptorFact {
    /// `DescriptorFact.create` — id = stable_fact_id("project-descriptor", path).
    #[allow(clippy::too_many_arguments)]
    pub fn create(
        project_id: &str,
        module_path: &str,
        path: &str,
        descriptor_type: &'static str,
        role: &'static str,
        parser: &str,
        parse_depth: &'static str,
        summary: String,
        properties: PyValue,
        confidence: &'static str,
        evidence: Vec<SourceEvidence>,
        diagnostics: Vec<AnalysisDiagnostic>,
    ) -> Result<Self, String> {
        let normalized_path = normalize_module_path(path)?;
        Ok(DescriptorFact {
            id: stable_fact_id(project_id, "project-descriptor", &[&normalized_path])?,
            project_id: project_id.to_string(),
            module_path: normalize_module_path(module_path)?,
            path: normalized_path,
            descriptor_type,
            role,
            parser: parser.to_string(),
            parse_depth,
            parser_version: ANALYZER_VERSION,
            canonical: true,
            generated: false,
            secret_bearing: false,
            redacted: false,
            summary,
            properties,
            confidence,
            evidence,
            diagnostics,
        })
    }

    pub fn to_dict(&self) -> Map<String, Value> {
        let mut map = Map::new();
        map.insert("id".into(), Value::String(self.id.clone()));
        map.insert("project_id".into(), Value::String(self.project_id.clone()));
        map.insert("module_path".into(), Value::String(self.module_path.clone()));
        map.insert("path".into(), Value::String(self.path.clone()));
        map.insert(
            "descriptor_type".into(),
            Value::String(self.descriptor_type.to_string()),
        );
        map.insert("role".into(), Value::String(self.role.to_string()));
        map.insert("parser".into(), Value::String(self.parser.clone()));
        map.insert(
            "parse_depth".into(),
            Value::String(self.parse_depth.to_string()),
        );
        map.insert(
            "parser_version".into(),
            Value::String(self.parser_version.to_string()),
        );
        map.insert("canonical".into(), Value::Bool(self.canonical));
        map.insert("generated".into(), Value::Bool(self.generated));
        map.insert("secret_bearing".into(), Value::Bool(self.secret_bearing));
        map.insert("redacted".into(), Value::Bool(self.redacted));
        map.insert("summary".into(), Value::String(self.summary.clone()));
        map.insert("properties".into(), self.properties.to_json());
        map.insert("confidence".into(), Value::String(self.confidence.to_string()));
        map.insert(
            "evidence".into(),
            Value::Array(self.evidence.iter().map(SourceEvidence::to_dict).map(Value::Object).collect()),
        );
        map.insert(
            "diagnostics".into(),
            Value::Array(
                self.diagnostics
                    .iter()
                    .map(AnalysisDiagnostic::to_dict)
                    .map(Value::Object)
                    .collect(),
            ),
        );
        map
    }
}

#[derive(Debug, Clone)]
pub struct DependencyFact {
    pub id: String,
    pub project_id: String,
    pub source_module_path: String,
    pub target: String,
    pub scope: &'static str,
    pub internal: bool,
    pub target_module_path: Option<String>,
    pub source: String,
    pub confidence: &'static str,
    pub evidence: Vec<SourceEvidence>,
    pub properties: PyValue,
}

impl DependencyFact {
    /// `DependencyFact.create`.
    #[allow(clippy::too_many_arguments)]
    pub fn create(
        project_id: &str,
        source_module_path: &str,
        target: &str,
        scope: &'static str,
        target_module_path: Option<String>,
        internal: bool,
        source: &str,
        evidence: Vec<SourceEvidence>,
        properties: PyValue,
    ) -> Result<Self, String> {
        let source_path = normalize_module_path(source_module_path)?;
        Ok(DependencyFact {
            id: stable_fact_id(
                project_id,
                "project-dependency",
                &[&source_path, scope, target],
            )?,
            project_id: project_id.to_string(),
            source_module_path: source_path,
            target: target.to_string(),
            scope,
            internal,
            target_module_path,
            source: source.to_string(),
            confidence: confidence::HIGH,
            evidence,
            properties,
        })
    }

    pub fn to_dict(&self) -> Map<String, Value> {
        let mut map = Map::new();
        map.insert("id".into(), Value::String(self.id.clone()));
        map.insert("project_id".into(), Value::String(self.project_id.clone()));
        map.insert(
            "source_module_path".into(),
            Value::String(self.source_module_path.clone()),
        );
        map.insert("target".into(), Value::String(self.target.clone()));
        map.insert("scope".into(), Value::String(self.scope.to_string()));
        map.insert("internal".into(), Value::Bool(self.internal));
        map.insert(
            "target_module_path".into(),
            self.target_module_path
                .clone()
                .map(Value::String)
                .unwrap_or(Value::Null),
        );
        map.insert("source".into(), Value::String(self.source.clone()));
        map.insert("confidence".into(), Value::String(self.confidence.to_string()));
        map.insert(
            "evidence".into(),
            Value::Array(self.evidence.iter().map(SourceEvidence::to_dict).map(Value::Object).collect()),
        );
        map.insert("properties".into(), self.properties.to_json());
        map
    }
}

#[derive(Debug, Clone)]
pub struct ModuleFact {
    pub id: String,
    pub project_id: String,
    pub module_path: String,
    pub name: String,
    pub kind: &'static str,
    pub languages: Vec<String>,
    pub frameworks: Vec<String>,
    pub build_systems: Vec<String>,
    pub source_roots: Vec<String>,
    pub descriptor_ids: Vec<String>,
    pub confidence: &'static str,
    pub diagnostics: Vec<AnalysisDiagnostic>,
    pub properties: PyValue,
}

impl ModuleFact {
    pub fn to_dict(&self) -> Map<String, Value> {
        let strings = |items: &[String]| Value::Array(items.iter().map(|s| Value::String(s.clone())).collect());
        let mut map = Map::new();
        map.insert("id".into(), Value::String(self.id.clone()));
        map.insert("project_id".into(), Value::String(self.project_id.clone()));
        map.insert("module_path".into(), Value::String(self.module_path.clone()));
        map.insert("name".into(), Value::String(self.name.clone()));
        map.insert("kind".into(), Value::String(self.kind.to_string()));
        map.insert("languages".into(), strings(&self.languages));
        map.insert("frameworks".into(), strings(&self.frameworks));
        map.insert("build_systems".into(), strings(&self.build_systems));
        map.insert("source_roots".into(), strings(&self.source_roots));
        map.insert("descriptor_ids".into(), strings(&self.descriptor_ids));
        map.insert("confidence".into(), Value::String(self.confidence.to_string()));
        map.insert(
            "diagnostics".into(),
            Value::Array(
                self.diagnostics
                    .iter()
                    .map(AnalysisDiagnostic::to_dict)
                    .map(Value::Object)
                    .collect(),
            ),
        );
        map.insert("properties".into(), self.properties.to_json());
        map
    }
}

#[derive(Debug, Clone)]
pub struct EndpointFact {
    pub id: String,
    pub project_id: String,
    pub module_id: String,
    pub protocol: &'static str,
    pub name: String,
    pub path: String,
    pub method: String,
    pub framework: String,
    pub handler_id: String,
    pub service: String,
    pub request_type: String,
    pub response_type: String,
    pub client_streaming: bool,
    pub server_streaming: bool,
    pub file_path: String,
    pub start_line: Option<i64>,
    pub security: PyValue,
    pub confidence: &'static str,
    pub evidence: Vec<SourceEvidence>,
    pub original_kind: String,
}

impl EndpointFact {
    pub fn to_dict(&self) -> Map<String, Value> {
        let mut map = Map::new();
        map.insert("id".into(), Value::String(self.id.clone()));
        map.insert("project_id".into(), Value::String(self.project_id.clone()));
        map.insert("module_id".into(), Value::String(self.module_id.clone()));
        map.insert("protocol".into(), Value::String(self.protocol.to_string()));
        map.insert("name".into(), Value::String(self.name.clone()));
        map.insert("path".into(), Value::String(self.path.clone()));
        map.insert("method".into(), Value::String(self.method.clone()));
        map.insert("framework".into(), Value::String(self.framework.clone()));
        map.insert("handler_id".into(), Value::String(self.handler_id.clone()));
        map.insert("service".into(), Value::String(self.service.clone()));
        map.insert("request_type".into(), Value::String(self.request_type.clone()));
        map.insert("response_type".into(), Value::String(self.response_type.clone()));
        map.insert("client_streaming".into(), Value::Bool(self.client_streaming));
        map.insert("server_streaming".into(), Value::Bool(self.server_streaming));
        map.insert("file_path".into(), Value::String(self.file_path.clone()));
        map.insert(
            "start_line".into(),
            self.start_line.map(Value::from).unwrap_or(Value::Null),
        );
        map.insert("security".into(), self.security.to_json());
        map.insert("confidence".into(), Value::String(self.confidence.to_string()));
        map.insert(
            "evidence".into(),
            Value::Array(self.evidence.iter().map(SourceEvidence::to_dict).map(Value::Object).collect()),
        );
        map.insert("original_kind".into(), Value::String(self.original_kind.clone()));
        map
    }
}

#[derive(Debug, Clone)]
pub struct SpecialFileFact {
    pub descriptor_id: String,
    pub project_id: String,
    pub module_id: String,
    pub path: String,
    pub role: &'static str,
    pub parser: String,
    pub parse_depth: &'static str,
    pub status: String,
    pub framework: String,
    pub canonical: bool,
    pub generated: bool,
    pub secret_bearing: bool,
    pub redacted: bool,
    pub safe_summary: String,
    pub freshness: String,
    pub diagnostics: Vec<AnalysisDiagnostic>,
}

impl SpecialFileFact {
    pub fn to_dict(&self) -> Map<String, Value> {
        let mut map = Map::new();
        map.insert("descriptor_id".into(), Value::String(self.descriptor_id.clone()));
        map.insert("project_id".into(), Value::String(self.project_id.clone()));
        map.insert("module_id".into(), Value::String(self.module_id.clone()));
        map.insert("path".into(), Value::String(self.path.clone()));
        map.insert("role".into(), Value::String(self.role.to_string()));
        map.insert("parser".into(), Value::String(self.parser.clone()));
        map.insert(
            "parse_depth".into(),
            Value::String(self.parse_depth.to_string()),
        );
        map.insert("status".into(), Value::String(self.status.clone()));
        map.insert("framework".into(), Value::String(self.framework.clone()));
        map.insert("canonical".into(), Value::Bool(self.canonical));
        map.insert("generated".into(), Value::Bool(self.generated));
        map.insert("secret_bearing".into(), Value::Bool(self.secret_bearing));
        map.insert("redacted".into(), Value::Bool(self.redacted));
        map.insert("safe_summary".into(), Value::String(self.safe_summary.clone()));
        map.insert("freshness".into(), Value::String(self.freshness.clone()));
        map.insert(
            "diagnostics".into(),
            Value::Array(
                self.diagnostics
                    .iter()
                    .map(AnalysisDiagnostic::to_dict)
                    .map(Value::Object)
                    .collect(),
            ),
        );
        map
    }
}

#[derive(Debug, Clone)]
pub struct FrameworkInstanceFact {
    pub id: String,
    pub project_id: String,
    pub module_id: String,
    pub framework: String,
    pub version: String,
    pub confidence: &'static str,
    pub evidence: Vec<SourceEvidence>,
    pub dimensions: PyValue,
    pub facts: PyValue,
    pub diagnostics: Vec<AnalysisDiagnostic>,
}

impl FrameworkInstanceFact {
    pub fn to_dict(&self) -> Map<String, Value> {
        let mut map = Map::new();
        map.insert("id".into(), Value::String(self.id.clone()));
        map.insert("project_id".into(), Value::String(self.project_id.clone()));
        map.insert("module_id".into(), Value::String(self.module_id.clone()));
        map.insert("framework".into(), Value::String(self.framework.clone()));
        map.insert("version".into(), Value::String(self.version.clone()));
        map.insert("confidence".into(), Value::String(self.confidence.to_string()));
        map.insert(
            "evidence".into(),
            Value::Array(self.evidence.iter().map(SourceEvidence::to_dict).map(Value::Object).collect()),
        );
        map.insert("dimensions".into(), self.dimensions.to_json());
        map.insert("facts".into(), self.facts.to_json());
        map.insert(
            "diagnostics".into(),
            Value::Array(
                self.diagnostics
                    .iter()
                    .map(AnalysisDiagnostic::to_dict)
                    .map(Value::Object)
                    .collect(),
            ),
        );
        map
    }
}

/// `TopologyAnalysisResult.to_dict()` — JSON payload consumed bởi writer half.
pub struct TopologyAnalysisResult {
    pub project_id: String,
    pub root: String,
    pub modules: Vec<ModuleFact>,
    pub descriptors: Vec<DescriptorFact>,
    pub dependencies: Vec<DependencyFact>,
    pub endpoints: Vec<EndpointFact>,
    pub special_files: Vec<SpecialFileFact>,
    pub frameworks: Vec<FrameworkInstanceFact>,
    pub diagnostics: Vec<AnalysisDiagnostic>,
}

impl TopologyAnalysisResult {
    pub fn to_json(&self) -> Value {
        let array = |maps: Vec<Map<String, Value>>| Value::Array(maps.into_iter().map(Value::Object).collect());
        let mut map = Map::new();
        map.insert("schema_version".into(), Value::String(SCHEMA_VERSION.into()));
        map.insert("analyzer_version".into(), Value::String(ANALYZER_VERSION.into()));
        map.insert("project_id".into(), Value::String(self.project_id.clone()));
        map.insert("root".into(), Value::String(self.root.clone()));
        map.insert("modules".into(), array(self.modules.iter().map(|m| m.to_dict()).collect()));
        map.insert(
            "descriptors".into(),
            array(self.descriptors.iter().map(|d| d.to_dict()).collect()),
        );
        map.insert(
            "dependencies".into(),
            array(self.dependencies.iter().map(|d| d.to_dict()).collect()),
        );
        map.insert("public_apis".into(), Value::Array(Vec::new()));
        map.insert(
            "endpoints".into(),
            array(self.endpoints.iter().map(|e| e.to_dict()).collect()),
        );
        map.insert(
            "special_files".into(),
            array(self.special_files.iter().map(|s| s.to_dict()).collect()),
        );
        map.insert(
            "frameworks".into(),
            array(self.frameworks.iter().map(|f| f.to_dict()).collect()),
        );
        map.insert(
            "diagnostics".into(),
            array(self.diagnostics.iter().map(|d| d.to_dict()).collect()),
        );
        Value::Object(map)
    }
}

/// `deterministic_unique` — sorted unique non-empty.
pub fn deterministic_unique(values: impl IntoIterator<Item = String>) -> Vec<String> {
    let set: std::collections::BTreeSet<String> =
        values.into_iter().filter(|v| !v.is_empty()).collect();
    set.into_iter().collect()
}

/// `sorted(set(items))` — sorted unique.
pub fn sorted_unique(values: impl IntoIterator<Item = String>) -> Vec<String> {
    let set: std::collections::BTreeSet<String> = values.into_iter().collect();
    set.into_iter().collect()
}
