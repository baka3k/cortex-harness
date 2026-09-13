//! Port `tools/servlet_jsp/web_xml_parser.py` — tree-sitter XML parse của
//! web.xml thành WebXmlRecord + generic facts/relationships.

use std::collections::BTreeMap;

use regex::Regex;
use serde_json::{json, Map, Value};
use tree_sitter::{Node, Parser, Tree};

use crate::servlet_jsp::models::{
    is_sensitive_key, stable_semantic_id, Diagnostic, ResourceBudgets, ServletJspFact, ServletJspRelationship,
    SourceSpan,
};
use crate::servlet_jsp::path_resolver::{normalize_relative_path, read_bounded_file};

const SUPPORTED_VERSIONS: [&str; 9] = ["2.3", "2.4", "2.5", "3.0", "3.1", "4.0", "5.0", "6.0", "6.1"];
const KNOWN_NAMESPACES: [&str; 5] = [
    "",
    "http://java.sun.com/xml/ns/j2ee",
    "http://java.sun.com/xml/ns/javaee",
    "http://xmlns.jcp.org/xml/ns/javaee",
    "https://jakarta.ee/xml/ns/jakartaee",
];
const RAW_TEXT_LIMIT: usize = 4096;

const KNOWN_ELEMENT_NAMES: [&str; 107] = [
    "absolute-ordering", "async-supported", "auth-constraint", "auth-method", "comment",
    "connection-factory-resource", "context-param", "cookie-config", "deny-uncovered-http-methods", "description",
    "data-source", "default-content-type", "deferred-syntax-allowed-as-literal", "dispatcher", "display-name",
    "distributable", "domain", "ejb-local-ref", "ejb-ref", "enabled", "encoding", "env-entry", "error-code",
    "error-page", "exception-type", "extension", "file-size-threshold", "filter", "filter-class", "filter-mapping",
    "filter-name", "form-error-page", "form-login-config", "form-login-page", "http-method",
    "http-method-omission", "http-only", "icon", "include-coda", "include-prelude", "injection-target",
    "injection-target-class", "injection-target-name", "init-param", "jsp-config", "jsp-file", "jsp-property-group",
    "large-icon", "listener", "listener-class", "load-on-startup", "locale-encoding-mapping-list",
    "locale-encoding-mapping", "locale", "location", "login-config", "max-age", "max-file-size", "max-request-size",
    "message-destination", "message-destination-ref", "mime-type", "mime-mapping", "multipart-config", "name",
    "ordering", "param-name", "param-value", "path", "persistence-context-ref", "persistence-unit-ref",
    "post-construct", "pre-destroy", "realm-name", "request-character-encoding", "resource-env-ref", "resource-ref",
    "response-character-encoding", "role-link", "role-name", "run-as", "security-constraint", "security-role",
    "security-role-ref", "secure", "service-ref", "servlet", "servlet-class", "servlet-mapping", "servlet-name",
    "session-config", "session-timeout", "scripting-invalid", "small-icon", "taglib", "taglib-location", "taglib-uri",
    "tracking-mode", "trim-directive-whitespaces", "transport-guarantee", "url-pattern", "user-data-constraint",
    "web-app", "web-resource-collection", "web-resource-name", "welcome-file", "welcome-file-list",
];

const XML_ENTITIES: [(&str, &str); 5] = [("amp", "&"), ("lt", "<"), ("gt", ">"), ("apos", "'"), ("quot", "\"")];

pub fn parse_xml_bytes(source: &[u8]) -> Result<Tree, String> {
    let mut parser = Parser::new();
    parser
        .set_language(&tree_sitter_xml::LANGUAGE_XML.into())
        .map_err(|error| error.to_string())?;
    parser.parse(source, None).ok_or_else(|| "failed to parse xml".to_string())
}

#[derive(Debug, Clone, Default)]
pub struct WebXmlRecord {
    pub stable_id: String,
    pub kind: String,
    pub name: String,
    pub order: i64,
    pub source: SourceSpan,
    pub raw_text: String,
    pub values: Map<String, Value>,
    pub children: Vec<WebXmlRecord>,
}

impl WebXmlRecord {
    pub fn get(&self, key: &str) -> Option<&Value> {
        self.values.get(key)
    }

    pub fn get_str(&self, key: &str) -> String {
        self.values.get(key).and_then(Value::as_str).unwrap_or("").to_string()
    }

    pub fn payload(&self) -> Value {
        crate::pyjson::py_object(vec![
            ("stable_id".into(), json!(self.stable_id)),
            ("kind".into(), json!(self.kind)),
            ("name".into(), json!(self.name)),
            ("order".into(), json!(self.order)),
            ("source".into(), span_dict(&self.source)),
            ("values".into(), Value::Object(self.values.clone())),
            ("raw_text".into(), json!(self.raw_text)),
        ])
    }
}

#[derive(Debug, Clone, Default)]
pub struct WebXmlDescriptorData {
    pub stable_id: String,
    pub file_path: String,
    pub module_path: String,
    pub namespace: String,
    pub version: String,
    pub metadata_complete: Option<bool>,
    pub metadata_complete_raw: String,
    pub doctype: String,
    pub source: SourceSpan,
    pub root_attributes: Map<String, Value>,
    pub servlets: Vec<WebXmlRecord>,
    pub servlet_mappings: Vec<WebXmlRecord>,
    pub filters: Vec<WebXmlRecord>,
    pub filter_mappings: Vec<WebXmlRecord>,
    pub listeners: Vec<WebXmlRecord>,
    pub context_params: Vec<WebXmlRecord>,
    pub welcome_files: Vec<WebXmlRecord>,
    pub error_pages: Vec<WebXmlRecord>,
    pub session_configs: Vec<WebXmlRecord>,
    pub security_constraints: Vec<WebXmlRecord>,
    pub security_roles: Vec<WebXmlRecord>,
    pub login_configs: Vec<WebXmlRecord>,
    pub other_elements: Vec<WebXmlRecord>,
    pub unknown_elements: Vec<WebXmlRecord>,
}

impl WebXmlDescriptorData {
    /// `records` — concat buckets theo thứ tự rồi sort theo
    /// (descriptor_order||order, order, start_line, start_column).
    pub fn records(&self) -> Vec<WebXmlRecord> {
        let mut rows: Vec<WebXmlRecord> = Vec::new();
        for bucket in [
            &self.servlets,
            &self.servlet_mappings,
            &self.filters,
            &self.filter_mappings,
            &self.listeners,
            &self.context_params,
            &self.welcome_files,
            &self.error_pages,
            &self.session_configs,
            &self.security_constraints,
            &self.security_roles,
            &self.login_configs,
            &self.other_elements,
        ] {
            rows.extend(bucket.iter().cloned());
        }
        rows.sort_by(|a, b| {
            let a_order = a.values.get("descriptor_order").and_then(Value::as_i64).unwrap_or(a.order);
            let b_order = b.values.get("descriptor_order").and_then(Value::as_i64).unwrap_or(b.order);
            (
                a_order,
                a.order,
                a.source.start_line,
                a.source.start_column,
            )
                .cmp(&(
                    b_order,
                    b.order,
                    b.source.start_line,
                    b.source.start_column,
                ))
        });
        rows
    }
}

#[derive(Debug, Clone, Default)]
pub struct WebXmlParseResult {
    pub descriptor: Option<WebXmlDescriptorData>,
    pub facts: Vec<ServletJspFact>,
    pub relationships: Vec<ServletJspRelationship>,
    pub diagnostics: Vec<Diagnostic>,
    pub claimed: bool,
    pub truncated: bool,
}

#[derive(Default)]
struct Diagnostics {
    file_path: String,
    limit: i64,
    rows: Vec<Diagnostic>,
    dropped: i64,
}

impl Diagnostics {
    fn new(file_path: &str, limit: i64) -> Self {
        Self {
            file_path: file_path.to_string(),
            limit: limit.max(1),
            rows: Vec::new(),
            dropped: 0,
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn add(&mut self, code: &str, message: &str, severity: &str, node: Option<Node>, source: &[u8], hint: &str, details: Option<Map<String, Value>>) {
        if self.rows.len() as i64 >= self.limit - 1 {
            self.dropped += 1;
            return;
        }
        let span = match node {
            Some(node) => ts_span(node, &self.file_path, source),
            None => SourceSpan::new(&self.file_path),
        };
        self.rows.push(Diagnostic {
            code: code.to_string(),
            message: message.to_string(),
            severity: severity.to_string(),
            file_path: self.file_path.clone(),
            start_line: span.start_line,
            end_line: span.end_line,
            hint: hint.to_string(),
            details: details.unwrap_or_default(),
        });
    }

    fn finish(mut self) -> Vec<Diagnostic> {
        if self.dropped > 0 {
            self.rows.push(Diagnostic {
                code: "servlet_jsp.web_xml.diagnostics_truncated".into(),
                message: format!("Diagnostic budget reached; {} additional diagnostics omitted", self.dropped),
                severity: "warning".into(),
                file_path: self.file_path.clone(),
                start_line: 1,
                end_line: 1,
                hint: "Increase max_diagnostics_per_file to retain more malformed/unknown regions.".into(),
                details: {
                    let mut map = Map::new();
                    map.insert("dropped_count".into(), json!(self.dropped));
                    map.insert("limit".into(), json!(self.limit));
                    map
                },
            });
        }
        self.rows.truncate(self.limit.max(0) as usize);
        self.rows
    }
}

struct Context<'a> {
    source: &'a [u8],
    file_path: String,
    project_id: String,
    project_name: String,
    module_id: String,
    #[allow(dead_code)]
    module_path: String,
    diagnostics: &'a mut Diagnostics,
}

impl<'a> Context<'a> {
    #[allow(clippy::too_many_arguments)]
    fn record(
        &mut self,
        kind: &str,
        name: &str,
        order: i64,
        node: Node,
        values: Option<Map<String, Value>>,
        children: Vec<WebXmlRecord>,
    ) -> WebXmlRecord {
        let record_id = stable_semantic_id(
            kind,
            &self.project_id,
            &self.module_id,
            &[
                self.file_path.clone(),
                (node.start_position().row + 1).to_string(),
                (node.start_position().column + 1).to_string(),
                order.to_string(),
            ],
        );
        let mut payload = values.unwrap_or_default();
        payload
            .entry("descriptor_order".to_string())
            .or_insert_with(|| json!(order));
        payload
            .entry("child_values".to_string())
            .or_insert_with(|| Value::Array(direct_child_value_rows(node, self.source, &self.file_path)));
        let (mut raw, mut was_capped) = bounded_raw(node, self.source);
        let param_name = payload
            .get("param_name")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if !param_name.is_empty() && is_sensitive_key(&param_name) {
            payload.insert("param_value".into(), json!("[REDACTED]"));
            payload.insert("param_value_redacted".into(), json!(true));
            if let Some(Value::Array(rows)) = payload.get_mut("child_values") {
                for row in rows.iter_mut() {
                    if let Some(map) = row.as_object_mut()
                        && map.get("name").and_then(Value::as_str) == Some("param-value")
                    {
                        map.insert("value".into(), json!("[REDACTED]"));
                    }
                }
            }
            raw = "[REDACTED]".to_string();
            was_capped = false;
        }
        if was_capped {
            payload.insert("raw_text_truncated".into(), json!(true));
            payload.insert(
                "raw_text_sha256".into(),
                json!(crate::pyutil::sha256_hex(&node_bytes(node, self.source))),
            );
        }
        WebXmlRecord {
            stable_id: record_id,
            kind: kind.to_string(),
            name: name.to_string(),
            order,
            source: ts_span(node, &self.file_path, self.source),
            raw_text: raw,
            values: payload,
            children,
        }
    }
}

pub fn parse_web_xml_file(
    root: &str,
    file_path: &str,
    project_id: &str,
    project_name: &str,
    module_id: &str,
    module_path: &str,
    budgets: &ResourceBudgets,
) -> WebXmlParseResult {
    let requested_path = normalize_relative_path(file_path);
    let diagnostic_path = if requested_path.is_empty() { file_path } else { &requested_path };
    let mut diagnostics = Diagnostics::new(diagnostic_path, budgets.max_diagnostics_per_file);
    if requested_path.is_empty() {
        diagnostics.add("servlet_jsp.web_xml.invalid_path", "Descriptor path is empty", "error", None, &[], "", None);
        return WebXmlParseResult {
            diagnostics: diagnostics.finish(),
            ..Default::default()
        };
    }

    let resolution = crate::servlet_jsp::path_resolver::resolve_project_path(root, &requested_path, "", false, true);
    if resolution.status != "resolved" {
        let code = if resolution.status == "rejected" {
            "servlet_jsp.web_xml.path_rejected"
        } else {
            "servlet_jsp.web_xml.file_unavailable"
        };
        let message = if resolution.message.is_empty() {
            format!("Unable to read descriptor: {}", resolution.status)
        } else {
            resolution.message
        };
        let mut details = Map::new();
        details.insert("status".into(), json!(resolution.status));
        details.insert("reference".into(), json!(requested_path));
        diagnostics.add(code, &message, "error", None, &[], "", Some(details));
        return WebXmlParseResult {
            diagnostics: diagnostics.finish(),
            ..Default::default()
        };
    }

    let (source_bytes, truncated) =
        match read_bounded_file(std::path::Path::new(&resolution.absolute_path), budgets.max_source_bytes as usize) {
            Ok(result) => result,
            Err(error) => {
                diagnostics.add("servlet_jsp.web_xml.read_failed", &error.to_string(), "error", None, &[], "", None);
                return WebXmlParseResult {
                    diagnostics: diagnostics.finish(),
                    ..Default::default()
                };
            }
        };
    if truncated {
        let mut hint = Map::new();
        hint.insert(
            "hint".into(),
            json!("Increase max_source_bytes to parse the complete descriptor."),
        );
        diagnostics.add(
            "servlet_jsp.web_xml.source_truncated",
            &format!("Descriptor exceeds max_source_bytes={}", budgets.max_source_bytes),
            "warning",
            None,
            &[],
            "Increase max_source_bytes to parse the complete descriptor.",
            None,
        );
        let _ = hint;
    }

    let module_path = {
        let normalized = normalize_relative_path(module_path);
        if normalized.is_empty() {
            infer_module_path(&requested_path)
        } else {
            normalized
        }
    };
    let module_id = if module_id.is_empty() {
        format!(
            "servlet_jsp::module::{}",
            crate::servlet_jsp::models::stable_digest(&[project_id.to_string(), if module_path.is_empty() { ".".to_string() } else { module_path.clone() }], 20)
        )
    } else {
        module_id.to_string()
    };
    let project_name = if project_name.is_empty() { project_id } else { project_name };

    let tree = match parse_xml_bytes(&source_bytes) {
        Ok(tree) => tree,
        Err(error) => {
            diagnostics.add("servlet_jsp.web_xml.parser_unavailable", &error, "error", None, &[], "", None);
            return WebXmlParseResult {
                diagnostics: diagnostics.finish(),
                truncated,
                ..Default::default()
            };
        }
    };
    let root_node = tree.root_node();

    if root_node.has_error() {
        diagnostics.add(
            "servlet_jsp.web_xml.parse_error",
            "Tree-sitter reported XML syntax errors",
            "error",
            Some(root_node),
            &source_bytes,
            "",
            None,
        );
        for error_node in syntax_error_nodes(root_node) {
            diagnostics.add(
                "servlet_jsp.web_xml.malformed_region",
                "Malformed XML region retained as a diagnostic",
                "error",
                Some(error_node),
                &source_bytes,
                "",
                None,
            );
        }
    }

    let Some(root_element) = document_element(root_node) else {
        diagnostics.add(
            "servlet_jsp.web_xml.no_root",
            "XML document has no structurally bounded root element",
            "error",
            Some(root_node),
            &source_bytes,
            "",
            None,
        );
        return WebXmlParseResult {
            diagnostics: diagnostics.finish(),
            truncated,
            ..Default::default()
        };
    };
    let root_name = local_name(&element_name(root_element, &source_bytes));
    if root_name != "web-app" {
        diagnostics.add(
            "servlet_jsp.web_xml.unclaimed_root",
            &format!("Expected web-app root, found {}", if root_name.is_empty() { "<unknown>" } else { &root_name }),
            "warning",
            Some(root_element),
            &source_bytes,
            "",
            None,
        );
        return WebXmlParseResult {
            diagnostics: diagnostics.finish(),
            truncated,
            ..Default::default()
        };
    }

    let attrs = ts_attrs(root_element, &source_bytes);
    let namespace = root_namespace(&element_name(root_element, &source_bytes), &attrs);
    if !KNOWN_NAMESPACES.contains(&namespace.as_str()) {
        diagnostics.add(
            "servlet_jsp.web_xml.unknown_namespace",
            &format!("Unrecognized web-app namespace {namespace:?}; local-name extraction continues"),
            "warning",
            Some(root_element),
            &source_bytes,
            "",
            None,
        );
    }
    let doctype = extract_doctype(&source_bytes);
    let version = {
        let attr_version = attrs.get("version").and_then(Value::as_str).unwrap_or("").trim().to_string();
        if attr_version.is_empty() {
            doctype_version(&doctype)
        } else {
            attr_version
        }
    };
    if !version.is_empty() && !SUPPORTED_VERSIONS.contains(&version.as_str()) {
        let mut details = Map::new();
        let mut supported: Vec<String> = SUPPORTED_VERSIONS.iter().map(|s| s.to_string()).collect();
        supported.sort();
        details.insert("supported_versions".into(), json!(supported));
        diagnostics.add(
            "servlet_jsp.web_xml.unsupported_version",
            &format!("Servlet descriptor version {version:?} is preserved but has no selected merge semantics"),
            "warning",
            Some(root_element),
            &source_bytes,
            "",
            Some(details),
        );
    }
    let metadata_raw = attrs.get("metadata-complete").and_then(Value::as_str).unwrap_or("").trim().to_string();
    let metadata_complete = optional_bool(&metadata_raw);
    if !metadata_raw.is_empty() && metadata_complete.is_none() {
        diagnostics.add(
            "servlet_jsp.web_xml.invalid_metadata_complete",
            &format!("Invalid metadata-complete value {metadata_raw:?}"),
            "warning",
            Some(root_element),
            &source_bytes,
            "",
            None,
        );
    }
    if Regex::new(r"<!ENTITY\b").unwrap().is_match(&doctype) {
        diagnostics.add(
            "servlet_jsp.web_xml.entity_declaration_ignored",
            "Internal entity declarations are inert and are never expanded",
            "warning",
            Some(root_element),
            &source_bytes,
            "",
            None,
        );
    }

    let mut context = Context {
        source: &source_bytes,
        file_path: requested_path.clone(),
        project_id: project_id.to_string(),
        project_name: project_name.to_string(),
        module_id: module_id.clone(),
        module_path: module_path.clone(),
        diagnostics: &mut diagnostics,
    };
    let parsed = parse_children(&mut context, root_element, &source_bytes);
    let descriptor_id = stable_semantic_id(
        "web_descriptor",
        project_id,
        &module_id,
        std::slice::from_ref(&requested_path)
    );
    let descriptor = WebXmlDescriptorData {
        stable_id: descriptor_id,
        file_path: requested_path.clone(),
        module_path,
        namespace,
        version,
        metadata_complete,
        metadata_complete_raw: metadata_raw,
        doctype,
        source: ts_span(root_element, &requested_path, &source_bytes),
        root_attributes: attrs,
        servlets: parsed.servlets,
        servlet_mappings: parsed.servlet_mappings,
        filters: parsed.filters,
        filter_mappings: parsed.filter_mappings,
        listeners: parsed.listeners,
        context_params: parsed.context_params,
        welcome_files: parsed.welcome_files,
        error_pages: parsed.error_pages,
        session_configs: parsed.session_configs,
        security_constraints: parsed.security_constraints,
        security_roles: parsed.security_roles,
        login_configs: parsed.login_configs,
        other_elements: parsed.other_elements,
        unknown_elements: parsed.unknown_elements,
    };
    let (facts, relationships) = build_generic_facts(&mut context, &descriptor);
    let dropped = context.diagnostics.dropped;
    WebXmlParseResult {
        descriptor: Some(descriptor),
        facts,
        relationships,
        diagnostics: finish_context_diagnostics(&mut context),
        claimed: true,
        truncated: truncated || dropped > 0,
    }
}

#[derive(Default)]
struct ParsedChildren {
    servlets: Vec<WebXmlRecord>,
    servlet_mappings: Vec<WebXmlRecord>,
    filters: Vec<WebXmlRecord>,
    filter_mappings: Vec<WebXmlRecord>,
    listeners: Vec<WebXmlRecord>,
    context_params: Vec<WebXmlRecord>,
    welcome_files: Vec<WebXmlRecord>,
    error_pages: Vec<WebXmlRecord>,
    session_configs: Vec<WebXmlRecord>,
    security_constraints: Vec<WebXmlRecord>,
    security_roles: Vec<WebXmlRecord>,
    login_configs: Vec<WebXmlRecord>,
    other_elements: Vec<WebXmlRecord>,
    unknown_elements: Vec<WebXmlRecord>,
}

fn parse_children(context: &mut Context, root: Node, source: &[u8]) -> ParsedChildren {
    let mut out = ParsedChildren::default();
    let mut counters: BTreeMap<String, i64> = BTreeMap::new();
    let mut seen_servlets: BTreeMap<String, ()> = BTreeMap::new();
    let mut seen_filters: BTreeMap<String, ()> = BTreeMap::new();

    let unknown_nodes: Vec<Node> = descendant_elements(root)
        .into_iter()
        .filter(|node| !KNOWN_ELEMENT_NAMES.contains(&local_name(&element_name(*node, source)).as_str()))
        .collect();
    for (unknown_order, node) in unknown_nodes.iter().enumerate() {
        let name = element_name(*node, source);
        let name_local = local_name(&name);
        let record = context.record(
            "unknown_element",
            if name_local.is_empty() { &name } else { &name_local },
            unknown_order as i64,
            *node,
            Some({
                let mut map = Map::new();
                map.insert("qualified_name".into(), json!(name));
                map
            }),
            Vec::new(),
        );
        out.unknown_elements.push(record);
        let mut details = Map::new();
        details.insert("qualified_name".into(), json!(name));
        details.insert(
            "raw_sha256".into(),
            json!(crate::pyutil::sha256_hex(&node_bytes(*node, source))),
        );
        context.diagnostics.add(
            "servlet_jsp.web_xml.unknown_element",
            &format!("Unknown descriptor element {name:?} preserved"),
            "warning",
            Some(*node),
            source,
            "",
            Some(details),
        );
    }

    let unknown_keys: Vec<(usize, usize)> = unknown_nodes
        .iter()
        .map(|node| (node.start_byte(), node.end_byte()))
        .collect();

    for (descriptor_order, node) in child_elements(root).into_iter().enumerate() {
        let tag = local_name(&element_name(node, source));
        let category_order = *counters.get(&tag).unwrap_or(&0);
        counters.insert(tag.clone(), category_order + 1);
        let common_key = "descriptor_order";
        let _ = common_key;
        let mut common = Map::new();
        common.insert("descriptor_order".into(), json!(descriptor_order as i64));
        if tag == "servlet" {
            let record = component_record(context, node, "servlet", category_order, &common, source);
            let name = record.get_str("servlet_name");
            if !name.is_empty() && seen_servlets.contains_key(&name) {
                context.diagnostics.add(
                    "servlet_jsp.web_xml.duplicate_servlet",
                    &format!("Duplicate servlet-name {name:?} preserved"),
                    "warning",
                    Some(node),
                    source,
                    "",
                    None,
                );
            } else if !name.is_empty() {
                seen_servlets.insert(name, ());
            }
            out.servlets.push(record);
        } else if tag == "servlet-mapping" {
            out.servlet_mappings.push(mapping_record(context, node, "servlet_mapping", category_order, &common, source));
        } else if tag == "filter" {
            let record = component_record(context, node, "filter", category_order, &common, source);
            let name = record.get_str("filter_name");
            if !name.is_empty() && seen_filters.contains_key(&name) {
                context.diagnostics.add(
                    "servlet_jsp.web_xml.duplicate_filter",
                    &format!("Duplicate filter-name {name:?} preserved"),
                    "warning",
                    Some(node),
                    source,
                    "",
                    None,
                );
            } else if !name.is_empty() {
                seen_filters.insert(name, ());
            }
            out.filters.push(record);
        } else if tag == "filter-mapping" {
            out.filter_mappings.push(mapping_record(context, node, "filter_mapping", category_order, &common, source));
        } else if tag == "listener" {
            let listener_class = first_child_text(node, source, "listener-class");
            let mut values = common.clone();
            values.insert("listener_class".into(), json!(listener_class));
            let name = if listener_class.is_empty() {
                format!("listener[{category_order}]")
            } else {
                listener_class
            };
            out.listeners
                .push(context.record("listener", &name, category_order, node, Some(values), Vec::new()));
        } else if tag == "context-param" {
            out.context_params.push(param_record(context, node, "context_param", category_order, &common, source));
        } else if tag == "welcome-file-list" {
            for child in children_named(node, source, "welcome-file") {
                let welcome_order = out.welcome_files.len() as i64;
                let path = direct_text_diagnostics(child, source, Some(context.diagnostics));
                let mut values = common.clone();
                values.insert("welcome_order".into(), json!(welcome_order));
                values.insert("path".into(), json!(path));
                let name = if path.is_empty() {
                    format!("welcome[{welcome_order}]")
                } else {
                    path.clone()
                };
                out.welcome_files
                    .push(context.record("welcome_file", &name, welcome_order, child, Some(values), Vec::new()));
            }
        } else if tag == "error-page" {
            let error_code = first_child_text(node, source, "error-code");
            let exception_type = first_child_text(node, source, "exception-type");
            let location = first_child_text(node, source, "location");
            let mut values = common.clone();
            values.insert("error_code".into(), json!(error_code));
            values.insert("exception_type".into(), json!(exception_type));
            values.insert("location".into(), json!(location));
            let name = if !error_code.is_empty() {
                error_code
            } else if !exception_type.is_empty() {
                exception_type
            } else {
                format!("error-page[{category_order}]")
            };
            out.error_pages
                .push(context.record("error_page", &name, category_order, node, Some(values), Vec::new()));
        } else if tag == "session-config" {
            out.session_configs.push(session_record(context, node, category_order, &common, source));
        } else if tag == "security-constraint" {
            out.security_constraints
                .push(security_constraint_record(context, node, category_order, &common, source));
        } else if tag == "security-role" {
            let role = first_child_text(node, source, "role-name");
            let mut values = common.clone();
            values.insert("role_name".into(), json!(role));
            values.insert(
                "descriptions".into(),
                Value::Array(child_texts(node, source, "description").into_iter().map(|t| json!(t)).collect()),
            );
            let name = if role.is_empty() {
                format!("role[{category_order}]")
            } else {
                role
            };
            out.security_roles
                .push(context.record("security_role", &name, category_order, node, Some(values), Vec::new()));
        } else if tag == "login-config" {
            out.login_configs.push(login_record(context, node, category_order, &common, source));
        } else if !unknown_keys.contains(&(node.start_byte(), node.end_byte())) {
            let mut values = common.clone();
            values.insert("element_name".into(), json!(element_name(node, source)));
            out.other_elements
                .push(context.record("descriptor_element", &tag, category_order, node, Some(values), Vec::new()));
        }
    }
    out
}

#[allow(clippy::too_many_arguments)]
fn component_record(
    context: &mut Context,
    node: Node,
    component: &str,
    order: i64,
    common: &Map<String, Value>,
    source: &[u8],
) -> WebXmlRecord {
    let name_key = format!("{component}_name");
    let class_key = format!("{component}_class");
    let mut init_params: Vec<WebXmlRecord> = Vec::new();
    for (index, child) in children_named(node, source, "init-param").into_iter().enumerate() {
        let mut owner_common = Map::new();
        owner_common.insert("owner_kind".into(), json!(component));
        init_params.push(param_record(context, child, &format!("{component}_init_param"), index as i64, &owner_common, source));
    }
    let async_raw = first_child_text(node, source, "async-supported");
    let mut values = common.clone();
    values.insert("declaration_order".into(), json!(order));
    values.insert(name_key.clone(), json!(first_child_text(node, source, &format!("{component}-name"))));
    values.insert(class_key.clone(), json!(first_child_text(node, source, &format!("{component}-class"))));
    values.insert("async_supported".into(), match optional_bool(&async_raw) {
        Some(value) => json!(value),
        None => Value::Null,
    });
    values.insert("async_supported_raw".into(), json!(async_raw));
    values.insert(
        "init_params".into(),
        Value::Array(init_params.iter().map(|item| item.payload()).collect()),
    );
    values.insert(
        "descriptions".into(),
        Value::Array(child_texts(node, source, "description").into_iter().map(|t| json!(t)).collect()),
    );
    let mut children_records: Vec<WebXmlRecord> = init_params.clone();
    if component == "servlet" {
        let load_raw = first_child_text(node, source, "load-on-startup");
        values.insert("jsp_file".into(), json!(first_child_text(node, source, "jsp-file")));
        values.insert("load_on_startup".into(), json!(load_raw));
        values.insert(
            "load_on_startup_value".into(),
            match optional_int(&load_raw) {
                Some(value) => json!(value),
                None => Value::Null,
            },
        );
        values.insert(
            "run_as_roles".into(),
            Value::Array(
                children_named(node, source, "run-as")
                    .iter()
                    .map(|item| json!(first_child_text(*item, source, "role-name")))
                    .collect(),
            ),
        );
        values.insert(
            "security_role_refs".into(),
            Value::Array(
                children_named(node, source, "security-role-ref")
                    .iter()
                    .map(|item| {
                        crate::pyjson::py_object(vec![
                            ("role_name".into(), json!(first_child_text(*item, source, "role-name"))),
                            ("role_link".into(), json!(first_child_text(*item, source, "role-link"))),
                            ("source".into(), span_dict(&ts_span(*item, &context.file_path, source))),
                        ])
                    })
                    .collect(),
            ),
        );
        let multipart_nodes = children_named(node, source, "multipart-config");
        let mut multipart_rows: Vec<WebXmlRecord> = Vec::new();
        for (index, multipart) in multipart_nodes.iter().enumerate() {
            let mut multipart_values = Map::new();
            multipart_values.insert("location".into(), json!(first_child_text(*multipart, source, "location")));
            multipart_values.insert("max_file_size".into(), json!(first_child_text(*multipart, source, "max-file-size")));
            multipart_values.insert(
                "max_request_size".into(),
                json!(first_child_text(*multipart, source, "max-request-size")),
            );
            multipart_values.insert(
                "file_size_threshold".into(),
                json!(first_child_text(*multipart, source, "file-size-threshold")),
            );
            multipart_rows.push(context.record("multipart_config", "multipart", index as i64, *multipart, Some(multipart_values), Vec::new()));
        }
        children_records.extend(multipart_rows.iter().cloned());
        values.insert(
            "multipart_configs".into(),
            Value::Array(multipart_rows.iter().map(|item| item.payload()).collect()),
        );
        let servlet_class = values.get(&class_key).and_then(Value::as_str).unwrap_or("");
        let jsp_file = values.get("jsp_file").and_then(Value::as_str).unwrap_or("");
        if !servlet_class.is_empty() && !jsp_file.is_empty() {
            context.diagnostics.add(
                "servlet_jsp.web_xml.servlet_class_and_jsp_file",
                "Servlet declares both servlet-class and jsp-file; both values are preserved",
                "warning",
                Some(node),
                source,
                "",
                None,
            );
        }
    }
    let name = {
        let declared = values.get(&name_key).and_then(Value::as_str).unwrap_or("").to_string();
        if declared.is_empty() {
            format!("{component}[{order}]")
        } else {
            declared
        }
    };
    context.record(component, &name, order, node, Some(values), children_records)
}

fn mapping_record(
    context: &mut Context,
    node: Node,
    kind: &str,
    order: i64,
    common: &Map<String, Value>,
    source: &[u8],
) -> WebXmlRecord {
    if kind == "servlet_mapping" {
        let servlet_name = first_child_text(node, source, "servlet-name");
        let mut values = common.clone();
        values.insert("mapping_order".into(), json!(order));
        values.insert("servlet_name".into(), json!(servlet_name));
        values.insert(
            "url_patterns".into(),
            Value::Array(child_texts(node, source, "url-pattern").into_iter().map(|t| json!(t)).collect()),
        );
        let name = if servlet_name.is_empty() {
            format!("servlet-mapping[{order}]")
        } else {
            servlet_name
        };
        return context.record(kind, &name, order, node, Some(values), Vec::new());
    }
    let filter_name = first_child_text(node, source, "filter-name");
    let dispatcher_values = child_texts(node, source, "dispatcher");
    let mut values = common.clone();
    values.insert("mapping_order".into(), json!(order));
    values.insert("filter_name".into(), json!(filter_name));
    values.insert(
        "url_patterns".into(),
        Value::Array(child_texts(node, source, "url-pattern").into_iter().map(|t| json!(t)).collect()),
    );
    values.insert(
        "servlet_names".into(),
        Value::Array(child_texts(node, source, "servlet-name").into_iter().map(|t| json!(t)).collect()),
    );
    values.insert(
        "dispatchers".into(),
        Value::Array(
            dispatcher_values
                .iter()
                .map(|value| json!(value.to_uppercase()))
                .collect(),
        ),
    );
    values.insert(
        "dispatcher_values".into(),
        Value::Array(dispatcher_values.iter().map(|t| json!(t)).collect()),
    );
    let name = if filter_name.is_empty() {
        format!("filter-mapping[{order}]")
    } else {
        filter_name
    };
    context.record(kind, &name, order, node, Some(values), Vec::new())
}

fn param_record(
    context: &mut Context,
    node: Node,
    kind: &str,
    order: i64,
    common: &Map<String, Value>,
    source: &[u8],
) -> WebXmlRecord {
    let name = first_child_text(node, source, "param-name");
    let value = first_child_text(node, source, "param-value");
    let mut values = common.clone();
    values.insert("param_name".into(), json!(name));
    values.insert("param_value".into(), json!(value));
    values.insert(
        "descriptions".into(),
        Value::Array(child_texts(node, source, "description").into_iter().map(|t| json!(t)).collect()),
    );
    let name_out = if name.is_empty() {
        format!("param[{order}]")
    } else {
        name
    };
    context.record(kind, &name_out, order, node, Some(values), Vec::new())
}

fn session_record(
    context: &mut Context,
    node: Node,
    order: i64,
    common: &Map<String, Value>,
    source: &[u8],
) -> WebXmlRecord {
    let mut cookie_rows: Vec<WebXmlRecord> = Vec::new();
    for (cookie_order, cookie) in children_named(node, source, "cookie-config").into_iter().enumerate() {
        let mut cookie_values = Map::new();
        for key in ["name", "domain", "path", "comment", "http-only", "secure", "max-age"] {
            cookie_values.insert(key.replace('-', "_"), json!(first_child_text(cookie, source, key)));
        }
        cookie_values.insert(
            "http_only_value".into(),
            match optional_bool(cookie_values["http_only"].as_str().unwrap_or("")) {
                Some(value) => json!(value),
                None => Value::Null,
            },
        );
        cookie_values.insert(
            "secure_value".into(),
            match optional_bool(cookie_values["secure"].as_str().unwrap_or("")) {
                Some(value) => json!(value),
                None => Value::Null,
            },
        );
        cookie_rows.push(context.record("cookie_config", "session-cookie", cookie_order as i64, cookie, Some(cookie_values), Vec::new()));
    }
    let mut values = common.clone();
    values.insert("session_timeout".into(), json!(first_child_text(node, source, "session-timeout")));
    values.insert(
        "tracking_modes".into(),
        Value::Array(
            child_texts(node, source, "tracking-mode")
                .iter()
                .map(|value| json!(value.to_uppercase()))
                .collect(),
        ),
    );
    values.insert(
        "cookie_configs".into(),
        Value::Array(cookie_rows.iter().map(|item| item.payload()).collect()),
    );
    context.record("session_config", "session", order, node, Some(values), cookie_rows)
}

fn security_constraint_record(
    context: &mut Context,
    node: Node,
    order: i64,
    common: &Map<String, Value>,
    source: &[u8],
) -> WebXmlRecord {
    let mut collections: Vec<WebXmlRecord> = Vec::new();
    for (collection_order, collection) in children_named(node, source, "web-resource-collection").into_iter().enumerate() {
        let name = first_child_text(collection, source, "web-resource-name");
        let mut values = Map::new();
        values.insert("web_resource_name".into(), json!(name));
        values.insert(
            "descriptions".into(),
            Value::Array(child_texts(collection, source, "description").into_iter().map(|t| json!(t)).collect()),
        );
        values.insert(
            "url_patterns".into(),
            Value::Array(child_texts(collection, source, "url-pattern").into_iter().map(|t| json!(t)).collect()),
        );
        values.insert(
            "http_methods".into(),
            Value::Array(
                child_texts(collection, source, "http-method")
                    .iter()
                    .map(|value| json!(value.to_uppercase()))
                    .collect(),
            ),
        );
        values.insert(
            "http_method_omissions".into(),
            Value::Array(
                child_texts(collection, source, "http-method-omission")
                    .iter()
                    .map(|value| json!(value.to_uppercase()))
                    .collect(),
            ),
        );
        collections.push(context.record(
            "web_resource_collection",
            &if name.is_empty() {
                format!("collection[{collection_order}]")
            } else {
                name
            },
            collection_order as i64,
            collection,
            Some(values),
            Vec::new(),
        ));
    }
    let auth_nodes = children_named(node, source, "auth-constraint");
    let mut role_names: Vec<String> = Vec::new();
    for auth in &auth_nodes {
        role_names.extend(child_texts(*auth, source, "role-name"));
    }
    let mut transport_guarantees: Vec<String> = Vec::new();
    for user_data in children_named(node, source, "user-data-constraint") {
        transport_guarantees.extend(child_texts(user_data, source, "transport-guarantee"));
    }
    let mut values = common.clone();
    values.insert(
        "display_names".into(),
        Value::Array(child_texts(node, source, "display-name").into_iter().map(|t| json!(t)).collect()),
    );
    values.insert(
        "web_resource_collections".into(),
        Value::Array(collections.iter().map(|item| item.payload()).collect()),
    );
    values.insert("auth_constraint_present".into(), json!(!auth_nodes.is_empty()));
    values.insert("role_names".into(), Value::Array(role_names.into_iter().map(|t| json!(t)).collect()));
    values.insert(
        "transport_guarantees".into(),
        Value::Array(
            transport_guarantees
                .iter()
                .map(|value| json!(value.to_uppercase()))
                .collect(),
        ),
    );
    context.record(
        "security_constraint",
        &format!("security[{order}]"),
        order,
        node,
        Some(values),
        collections,
    )
}

fn login_record(
    context: &mut Context,
    node: Node,
    order: i64,
    common: &Map<String, Value>,
    source: &[u8],
) -> WebXmlRecord {
    let mut form_rows: Vec<WebXmlRecord> = Vec::new();
    for (form_order, form) in children_named(node, source, "form-login-config").into_iter().enumerate() {
        let mut values = Map::new();
        values.insert("form_login_page".into(), json!(first_child_text(form, source, "form-login-page")));
        values.insert("form_error_page".into(), json!(first_child_text(form, source, "form-error-page")));
        form_rows.push(context.record("form_login_config", "form-login", form_order as i64, form, Some(values), Vec::new()));
    }
    let mut values = common.clone();
    values.insert("auth_method".into(), json!(first_child_text(node, source, "auth-method")));
    values.insert("realm_name".into(), json!(first_child_text(node, source, "realm-name")));
    values.insert(
        "form_login_configs".into(),
        Value::Array(form_rows.iter().map(|item| item.payload()).collect()),
    );
    context.record("login_config", "login", order, node, Some(values), form_rows)
}

fn build_generic_facts(context: &mut Context, descriptor: &WebXmlDescriptorData) -> (Vec<ServletJspFact>, Vec<ServletJspRelationship>) {
    let mut properties = Map::new();
    properties.insert("module_path".into(), json!(descriptor.module_path));
    properties.insert("namespace".into(), json!(descriptor.namespace));
    properties.insert("version".into(), json!(descriptor.version));
    properties.insert("metadata_complete".into(), match descriptor.metadata_complete {
        Some(value) => json!(value),
        None => Value::Null,
    });
    properties.insert("metadata_complete_raw".into(), json!(descriptor.metadata_complete_raw));
    properties.insert("root_attributes".into(), Value::Object(descriptor.root_attributes.clone()));
    properties.insert("descriptor_order_preserved".into(), json!(true));
    let mut facts = vec![ServletJspFact {
        kind: "WebDescriptor".into(),
        stable_id: descriptor.stable_id.clone(),
        name: crate::pyutil::basename(&descriptor.file_path),
        source: descriptor.source.clone(),
        project_id: context.project_id.clone(),
        project_name: context.project_name.clone(),
        module_id: context.module_id.clone(),
        language: "servlet_jsp".into(),
        confidence: 1.0,
        extraction_method: "tree_sitter_xml".into(),
        resolution_status: "resolved".into(),
        raw_value: descriptor.doctype.clone(),
        resolved_value: descriptor.file_path.clone(),
        source_symbol_id: String::new(),
        properties,
    }];
    let mut relationships: Vec<ServletJspRelationship> = Vec::new();
    let label_for_kind: BTreeMap<&str, &str> = BTreeMap::from([
        ("servlet", "Servlet"),
        ("servlet_mapping", "ServletMapping"),
        ("filter", "Filter"),
        ("filter_mapping", "FilterMapping"),
        ("listener", "Listener"),
        ("welcome_file", "WelcomePage"),
        ("error_page", "ErrorPage"),
        ("security_constraint", "SecurityConstraint"),
        ("security_role", "Authority"),
    ]);
    for record in descriptor.records() {
        let label = label_for_kind
            .get(record.kind.as_str())
            .copied()
            .unwrap_or("WebConfiguration");
        let mut properties = record.values.clone();
        properties.insert("descriptor_id".into(), json!(descriptor.stable_id));
        properties.insert("record_kind".into(), json!(record.kind));
        properties.insert("provenance".into(), json!("web.xml"));
        let fact = ServletJspFact {
            kind: label.to_string(),
            stable_id: record.stable_id.clone(),
            name: record.name.clone(),
            source: record.source.clone(),
            project_id: context.project_id.clone(),
            project_name: context.project_name.clone(),
            module_id: context.module_id.clone(),
            language: "servlet_jsp".into(),
            confidence: 1.0,
            extraction_method: "tree_sitter_xml".into(),
            resolution_status: "resolved".into(),
            raw_value: record.raw_text.clone(),
            resolved_value: record.name.clone(),
            source_symbol_id: String::new(),
            properties,
        };
        relationships.push(make_relationship(
            context,
            &descriptor.stable_id,
            "WebDescriptor",
            &fact,
            "DECLARES",
            &record.source,
            record.order,
        ));
        for (child_order, child) in record.children.iter().enumerate() {
            let mut child_properties: Map<String, Value> = child.values.clone();
            child_properties.insert("owner_id".into(), json!(record.stable_id));
            child_properties.insert("record_kind".into(), json!(child.kind));
            child_properties.insert("provenance".into(), json!("web.xml"));
            let child_fact = ServletJspFact {
                kind: "WebConfiguration".into(),
                stable_id: child.stable_id.clone(),
                name: child.name.clone(),
                source: child.source.clone(),
                project_id: context.project_id.clone(),
                project_name: context.project_name.clone(),
                module_id: context.module_id.clone(),
                language: "servlet_jsp".into(),
                confidence: 1.0,
                extraction_method: "tree_sitter_xml".into(),
                resolution_status: "resolved".into(),
                raw_value: child.raw_text.clone(),
                resolved_value: child.name.clone(),
                source_symbol_id: String::new(),
                properties: child_properties,
            };
            relationships.push(make_relationship(
                context,
                &record.stable_id,
                label,
                &child_fact,
                "CONFIGURES",
                &child.source,
                child_order as i64,
            ));
            facts.push(child_fact);
        }
        facts.push(fact);
    }
    (facts, relationships)
}

fn make_relationship(
    context: &Context,
    from_id: &str,
    from_label: &str,
    target: &ServletJspFact,
    relationship_type: &str,
    source: &SourceSpan,
    occurrence: i64,
) -> ServletJspRelationship {
    let rel_id = stable_semantic_id(
        "relationship",
        &context.project_id,
        &context.module_id,
        &[
            relationship_type.to_string(),
            from_id.to_string(),
            target.stable_id.clone(),
            context.file_path.clone(),
            source.start_line.to_string(),
            source.start_column.to_string(),
            occurrence.to_string(),
        ],
    );
    let mut properties = Map::new();
    properties.insert("provenance".into(), json!("web.xml"));
    properties.insert("occurrence_order".into(), json!(occurrence));
    ServletJspRelationship {
        stable_id: rel_id,
        from_id: from_id.to_string(),
        to_id: target.stable_id.clone(),
        from_label: from_label.to_string(),
        to_label: target.kind.clone(),
        rel_type: relationship_type.to_string(),
        project_id: context.project_id.clone(),
        module_id: context.module_id.clone(),
        source: source.clone(),
        confidence: 1.0,
        resolution_status: "resolved".to_string(),
        reason: "web.xml descriptor provenance".to_string(),
        properties,
        from_generated: true,
        to_generated: true,
    }
}

// ── tree-sitter helpers ─────────────────────────────────────────────────────

fn document_element(root: Node) -> Option<Node> {
    if root.kind() == "element" {
        return Some(root);
    }
    root.children(&mut root.walk())
        .find(|child| child.kind() == "element")
}

fn child_elements<'a>(node: Node<'a>) -> Vec<Node<'a>> {
    let content = node.children(&mut node.walk()).find(|child| child.kind() == "content");
    let Some(content) = content else {
        return Vec::new();
    };
    content
        .children(&mut content.walk())
        .filter(|child| child.kind() == "element")
        .collect()
}

fn descendant_elements<'a>(node: Node<'a>) -> Vec<Node<'a>> {
    let mut out = Vec::new();
    for child in child_elements(node) {
        out.push(child);
        out.extend(descendant_elements(child));
    }
    out
}

fn children_named<'a>(node: Node<'a>, source: &[u8], name: &str) -> Vec<Node<'a>> {
    child_elements(node)
        .into_iter()
        .filter(|child| local_name(&element_name(*child, source)) == name)
        .collect()
}

fn first_child_text(node: Node, source: &[u8], name: &str) -> String {
    let values = children_named(node, source, name);
    if values.is_empty() {
        String::new()
    } else {
        direct_text(values[0], source)
    }
}

fn child_texts(node: Node, source: &[u8], name: &str) -> Vec<String> {
    children_named(node, source, name)
        .into_iter()
        .map(|child| direct_text(child, source))
        .collect()
}

fn direct_child_value_rows(node: Node, source: &[u8], file_path: &str) -> Vec<Value> {
    let mut rows: Vec<Value> = Vec::new();
    for child in child_elements(node) {
        if !child_elements(child).is_empty() {
            continue;
        }
        rows.push(crate::pyjson::py_object(vec![
            ("name".into(), json!(local_name(&element_name(child, source)))),
            ("qualified_name".into(), json!(element_name(child, source))),
            ("value".into(), json!(direct_text(child, source))),
            ("source".into(), span_dict(&ts_span(child, file_path, source))),
        ]));
    }
    rows
}

fn direct_text(node: Node, source: &[u8]) -> String {
    direct_text_diagnostics(node, source, None)
}

fn finish_context_diagnostics(context: &mut Context) -> Vec<Diagnostic> {
    std::mem::take(context.diagnostics).finish()
}

fn direct_text_diagnostics(node: Node, source: &[u8], mut diagnostics: Option<&mut Diagnostics>) -> String {
    let Some(content) = node.children(&mut node.walk()).find(|child| child.kind() == "content") else {
        return String::new();
    };
    let mut parts: Vec<String> = Vec::new();
    for child in content.children(&mut content.walk()) {
        match child.kind() {
            "CharData" | "CData" | "CharRef" => {
                parts.push(decode_xml_entities(&ts_text(child, source)));
            }
            "CDSect" => {
                for descendant in child.children(&mut child.walk()) {
                    if descendant.kind() == "CData" {
                        parts.push(ts_text(descendant, source));
                    }
                }
            }
            "EntityRef" => {
                let raw = ts_text(child, source);
                let decoded = decode_xml_entities(&raw);
                parts.push(decoded.clone());
                if let Some(diagnostics) = diagnostics.as_deref_mut()
                    && decoded == raw
                {
                    diagnostics.add(
                        "servlet_jsp.web_xml.entity_reference_ignored",
                        &format!("Entity reference {raw:?} was retained without expansion"),
                        "warning",
                        Some(child),
                        source,
                        "",
                        None,
                    );
                }
            }
            _ => {}
        }
    }
    parts.join("").trim().to_string()
}

fn decode_xml_entities(value: &str) -> String {
    let mut out = String::new();
    let mut rest = value;
    while let Some(index) = rest.find('&') {
        out.push_str(&rest[..index]);
        let tail = &rest[index..];
        let Some(close) = tail.find(';') else {
            out.push('&');
            rest = &rest[index + 1..];
            continue;
        };
        let body = &tail[1..close];
        if let Some(decimal) = body.strip_prefix('#') {
            if let Some(hex) = decimal.strip_prefix('x').or_else(|| decimal.strip_prefix('X')) {
                match u32::from_str_radix(hex, 16) {
                    Ok(number) if number != 0 && number <= 0x10FFFF && !(0xD800..=0xDFFF).contains(&number) => {
                        out.push(char::from_u32(number).unwrap_or('\u{FFFD}'));
                    }
                    _ => out.push_str(tail[..=close].trim_end()),
                }
            } else {
                match decimal.parse::<u32>() {
                    Ok(number) if number != 0 && number <= 0x10FFFF && !(0xD800..=0xDFFF).contains(&number) => {
                        out.push(char::from_u32(number).unwrap_or('\u{FFFD}'));
                    }
                    _ => out.push_str(tail[..=close].trim_end()),
                }
            }
        } else if let Some((_, replacement)) = XML_ENTITIES.iter().find(|(name, _)| *name == body) {
            out.push_str(replacement);
        } else {
            out.push_str(tail[..=close].trim_end());
        }
        rest = &rest[index + close + 1..];
    }
    out.push_str(rest);
    out
}

fn element_name(node: Node, source: &[u8]) -> String {
    let tag = node.children(&mut node.walk()).find(|child| child.kind() == "STag" || child.kind() == "EmptyElemTag");
    let Some(tag) = tag else {
        return String::new();
    };
    let name = tag.children(&mut tag.walk()).find(|child| child.kind() == "Name");
    match name {
        Some(name) => ts_text(name, source),
        None => String::new(),
    }
}

fn ts_attrs(node: Node, source: &[u8]) -> Map<String, Value> {
    let mut attrs = Map::new();
    let tag = node.children(&mut node.walk()).find(|child| child.kind() == "STag" || child.kind() == "EmptyElemTag");
    let Some(tag) = tag else {
        return attrs;
    };
    for child in tag.children(&mut tag.walk()) {
        if child.kind() != "Attribute" {
            continue;
        }
        let name = child.children(&mut child.walk()).find(|item| item.kind() == "Name");
        let value = child.children(&mut child.walk()).find(|item| item.kind() == "AttValue");
        let key = match name {
            Some(name) => ts_text(name, source),
            None => String::new(),
        };
        let mut raw = match value {
            Some(value) => ts_text(value, source),
            None => String::new(),
        };
        let chars: Vec<char> = raw.chars().collect();
        if chars.len() >= 2 && (chars[0] == '"' || chars[0] == '\'') && chars[chars.len() - 1] == chars[0] {
            raw = raw
                .chars()
                .take(raw.chars().count() - 1)
                .skip(1)
                .collect();
        }
        attrs.insert(key, json!(decode_xml_entities(&raw)));
    }
    attrs
}

fn root_namespace(qualified_name: &str, attrs: &Map<String, Value>) -> String {
    if let Some((prefix, _)) = qualified_name.split_once(':') {
        return attrs
            .get(&format!("xmlns:{prefix}"))
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
    }
    attrs
        .get("xmlns")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string()
}

fn local_name(name: &str) -> String {
    name.rsplit(':').next().unwrap_or(name).to_string()
}

fn extract_doctype(source: &[u8]) -> String {
    let text = String::from_utf8_lossy(source).to_string();
    let re = Regex::new(r"(?i)<!DOCTYPE\b").unwrap();
    let Some(matched) = re.find(&text) else {
        return String::new();
    };
    let start = matched.start();
    let mut quote = ' ';
    let mut bracket_depth = 0i32;
    for (index, ch) in text.char_indices().skip_while(|(index, _)| *index < start) {
        if quote != ' ' {
            if ch == quote {
                quote = ' ';
            }
            continue;
        }
        if ch == '"' || ch == '\'' {
            quote = ch;
        } else if ch == '[' {
            bracket_depth += 1;
        } else if ch == ']' && bracket_depth > 0 {
            bracket_depth -= 1;
        } else if ch == '>' && bracket_depth == 0 {
            return text[start..=index].to_string();
        }
    }
    text[start..].to_string()
}

fn doctype_version(doctype: &str) -> String {
    let re = Regex::new(r#"(?i)(?:web-app[_-](?P<major>\d+)[_-](?P<minor>\d+)|Web\s+Application\s+(?P<version>\d+\.\d+))"#)
        .unwrap();
    let Some(captures) = re.captures(doctype) else {
        return String::new();
    };
    if let Some(version) = captures.name("version") {
        return version.as_str().to_string();
    }
    format!(
        "{}.{}",
        captures.name("major").map(|m| m.as_str()).unwrap_or(""),
        captures.name("minor").map(|m| m.as_str()).unwrap_or("")
    )
}

fn syntax_error_nodes<'a>(root: Node<'a>) -> Vec<Node<'a>> {
    let mut out = Vec::new();
    let mut stack = vec![root];
    while let Some(node) = stack.pop() {
        if node.kind() == "ERROR" || node.is_missing() {
            out.push(node);
        }
        let children: Vec<Node> = node.children(&mut node.walk()).collect();
        stack.extend(children.into_iter().rev());
    }
    out
}

fn optional_bool(value: &str) -> Option<bool> {
    match value.trim().to_lowercase().as_str() {
        "true" | "1" => Some(true),
        "false" | "0" => Some(false),
        _ => None,
    }
}

fn optional_int(value: &str) -> Option<i64> {
    value.trim().parse::<i64>().ok()
}

fn infer_module_path(file_path: &str) -> String {
    let normalized = normalize_relative_path(file_path);
    if normalized == "WEB-INF/web.xml" {
        return ".".to_string();
    }
    for marker in ["/src/main/webapp/WEB-INF/web.xml", "/WEB-INF/web.xml"] {
        if normalized.ends_with(marker) {
            let prefix = &normalized[..normalized.len() - marker.len()];
            return if prefix.is_empty() { ".".to_string() } else { prefix.to_string() };
        }
    }
    let dir = crate::pyutil::dirname(&normalized);
    if dir.is_empty() {
        ".".to_string()
    } else {
        dir
    }
}

fn ts_span(node: Node, file_path: &str, _source: &[u8]) -> SourceSpan {
    SourceSpan {
        file_path: file_path.to_string(),
        start_line: node.start_position().row as i64 + 1,
        end_line: node.end_position().row as i64 + 1,
        start_column: node.start_position().column as i64 + 1,
        end_column: node.end_position().column as i64 + 1,
    }
}

fn span_dict(span: &SourceSpan) -> Value {
    crate::pyjson::py_object(vec![
        ("file_path".into(), json!(span.file_path)),
        ("start_line".into(), json!(span.start_line)),
        ("end_line".into(), json!(span.end_line)),
        ("start_column".into(), json!(span.start_column)),
        ("end_column".into(), json!(span.end_column)),
    ])
}

fn bounded_raw(node: Node, source: &[u8]) -> (String, bool) {
    let raw = node_bytes(node, source);
    let capped = raw.len() > RAW_TEXT_LIMIT;
    (String::from_utf8_lossy(&raw[..RAW_TEXT_LIMIT.min(raw.len())]).to_string(), capped)
}

fn node_bytes(node: Node, source: &[u8]) -> Vec<u8> {
    source[node.start_byte()..node.end_byte()].to_vec()
}

fn ts_text(node: Node, source: &[u8]) -> String {
    String::from_utf8_lossy(&source[node.start_byte()..node.end_byte()]).to_string()
}

/// entity decl re giữ cho module (dùng trong doctype check).
#[allow(dead_code)]
fn entity_decl_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)<!ENTITY\b").unwrap())
}
