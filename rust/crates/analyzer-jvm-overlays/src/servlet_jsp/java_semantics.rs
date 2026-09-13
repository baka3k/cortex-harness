//! Port `tools/servlet_jsp/java_semantics.py` — framework-only facts cho một
//! Java file (tree-sitter-java walk + literal resolve + role propagation).

use std::collections::{BTreeMap, BTreeSet};

use regex::Regex;
use serde_json::{json, Map, Value};

use crate::servlet_jsp::java_identity::{JavaIdentityIndex, JavaIdentityProvider};
use crate::servlet_jsp::models::{
    stable_semantic_id, Diagnostic, ResourceBudgets, ServletJspFact, ServletJspRelationship, SourceSpan,
};
use crate::struts::java_validation::parse_java_bytes;

fn web_annotations() -> BTreeMap<&'static str, &'static str> {
    BTreeMap::from([
        ("javax.servlet.annotation.WebServlet", "Servlet"),
        ("jakarta.servlet.annotation.WebServlet", "Servlet"),
        ("javax.servlet.annotation.WebFilter", "Filter"),
        ("jakarta.servlet.annotation.WebFilter", "Filter"),
        ("javax.servlet.annotation.WebListener", "Listener"),
        ("jakarta.servlet.annotation.WebListener", "Listener"),
    ])
}

fn servlet_types() -> BTreeSet<String> {
    let mut set = BTreeSet::new();
    for prefix in ["javax.servlet", "jakarta.servlet"] {
        for name in ["Servlet", "GenericServlet"] {
            set.insert(format!("{prefix}.{name}"));
        }
        set.insert(format!("{prefix}.http.HttpServlet"));
    }
    set
}

fn filter_types() -> BTreeSet<String> {
    ["javax.servlet.Filter", "jakarta.servlet.Filter"].iter().map(|s| s.to_string()).collect()
}

const LISTENER_SIMPLE_NAMES: [&str; 7] = [
    "ServletContextListener",
    "ServletContextAttributeListener",
    "ServletRequestListener",
    "ServletRequestAttributeListener",
    "HttpSessionListener",
    "HttpSessionAttributeListener",
    "HttpSessionIdListener",
];

fn listener_types() -> BTreeSet<String> {
    let mut set = BTreeSet::new();
    for prefix in ["javax.servlet", "jakarta.servlet"] {
        for name in LISTENER_SIMPLE_NAMES {
            if name.starts_with("HttpSession") {
                set.insert(format!("{prefix}.http.{name}"));
            } else {
                set.insert(format!("{prefix}.{name}"));
            }
        }
    }
    set
}

fn prefix_set(pairs: &[(&str, &str)]) -> BTreeSet<String> {
    let mut set = BTreeSet::new();
    for (a, b) in pairs {
        set.insert(format!("{a}.{b}"));
    }
    set
}

fn request_types() -> BTreeSet<String> {
    prefix_set(&[("javax.servlet", "ServletRequest"), ("jakarta.servlet", "ServletRequest"), ("javax.servlet.http", "HttpServletRequest"), ("jakarta.servlet.http", "HttpServletRequest")])
}
fn response_types() -> BTreeSet<String> {
    prefix_set(&[("javax.servlet", "ServletResponse"), ("jakarta.servlet", "ServletResponse"), ("javax.servlet.http", "HttpServletResponse"), ("jakarta.servlet.http", "HttpServletResponse")])
}
fn session_types() -> BTreeSet<String> {
    prefix_set(&[("javax.servlet.http", "HttpSession"), ("jakarta.servlet.http", "HttpSession")])
}
fn context_types() -> BTreeSet<String> {
    prefix_set(&[("javax.servlet", "ServletContext"), ("jakarta.servlet", "ServletContext")])
}
fn dispatcher_types() -> BTreeSet<String> {
    prefix_set(&[("javax.servlet", "RequestDispatcher"), ("jakarta.servlet", "RequestDispatcher")])
}
fn filter_chain_types() -> BTreeSet<String> {
    prefix_set(&[("javax.servlet", "FilterChain"), ("jakarta.servlet", "FilterChain")])
}
fn cookie_types() -> BTreeSet<String> {
    prefix_set(&[("javax.servlet.http", "Cookie"), ("jakarta.servlet.http", "Cookie")])
}
fn config_types() -> BTreeSet<String> {
    prefix_set(&[
        ("javax.servlet", "ServletConfig"),
        ("jakarta.servlet", "ServletConfig"),
        ("javax.servlet", "FilterConfig"),
        ("jakarta.servlet", "FilterConfig"),
    ])
}
fn event_roles() -> BTreeMap<String, &'static str> {
    let mut map = BTreeMap::new();
    for prefix in ["javax.servlet", "jakarta.servlet"] {
        map.insert(format!("{prefix}.ServletContextEvent"), "context_event");
        map.insert(format!("{prefix}.ServletRequestEvent"), "request_event");
        map.insert(format!("{prefix}.http.HttpSessionEvent"), "session_event");
    }
    map
}

fn servlet_handlers() -> BTreeMap<&'static str, &'static str> {
    BTreeMap::from([
        ("doGet", "GET"),
        ("doPost", "POST"),
        ("doPut", "PUT"),
        ("doDelete", "DELETE"),
        ("doHead", "HEAD"),
        ("doOptions", "OPTIONS"),
        ("doTrace", "TRACE"),
        ("service", "ALL"),
    ])
}

const LISTENER_CALLBACKS: [&str; 10] = [
    "contextInitialized",
    "contextDestroyed",
    "requestInitialized",
    "requestDestroyed",
    "sessionCreated",
    "sessionDestroyed",
    "sessionIdChanged",
    "attributeAdded",
    "attributeRemoved",
    "attributeReplaced",
];

#[derive(Debug, Clone, Default)]
pub struct JavaSemanticAnalysisResult {
    pub file_path: String,
    pub facts: Vec<ServletJspFact>,
    pub relationships: Vec<ServletJspRelationship>,
    pub diagnostics: Vec<Diagnostic>,
    pub coverage_status: String,
    pub missing_anchor_count: i64,
    pub ambiguity_count: i64,
    pub truncation_count: i64,
}

#[derive(Clone)]
struct TypeInfo<'a> {
    node: tree_sitter::Node<'a>,
    class_path: String,
    fqcn: String,
    name: String,
    annotations: Vec<(String, String)>,
    super_raw: Vec<String>,
    super_resolved: Vec<String>,
    roles: BTreeSet<String>,
    evidence: BTreeSet<String>,
    parent_paths: Vec<String>,
}

struct Diagnostics {
    file_path: String,
    limit: i64,
    rows: Vec<Diagnostic>,
    suppressed: i64,
}

impl Diagnostics {
    fn new(file_path: &str, limit: i64) -> Self {
        Self {
            file_path: file_path.to_string(),
            limit: limit.max(0),
            rows: Vec::new(),
            suppressed: 0,
        }
    }

    fn add(&mut self, code: &str, message: &str, node: Option<tree_sitter::Node>, severity: &str, hint: &str, details: Option<Map<String, Value>>) {
        if self.rows.len() as i64 >= self.limit {
            self.suppressed += 1;
            return;
        }
        let (start, end) = match node {
            Some(node) => (node.start_position().row as i64 + 1, node.end_position().row as i64 + 1),
            None => (1, 1),
        };
        self.rows.push(Diagnostic {
            code: code.to_string(),
            message: message.to_string(),
            severity: severity.to_string(),
            file_path: self.file_path.clone(),
            start_line: start,
            end_line: end,
            hint: hint.to_string(),
            details: details.unwrap_or_default(),
        });
    }

    fn finish(mut self) -> Vec<Diagnostic> {
        if self.suppressed > 0 && self.limit > 0 {
            let marker = Diagnostic::new(
                "servlet_jsp.java.diagnostic_budget",
                &format!("Suppressed {} diagnostics after reaching the per-file limit", self.suppressed),
                "warning",
                &self.file_path,
                1,
                1,
            );
            if (self.rows.len() as i64) < self.limit {
                self.rows.push(marker);
            } else if !self.rows.is_empty() {
                let last = self.rows.len() - 1;
                self.rows[last] = marker;
            }
        }
        self.rows.sort_by_key(|item| (item.start_line, item.code.clone(), item.message.clone()));
        self.rows
    }
}

struct LiteralResolver {
    definitions: BTreeMap<String, String>,
    values: BTreeMap<String, Value>,
    max_steps: usize,
    steps: usize,
    pub truncated: bool,
}

impl LiteralResolver {
    fn new(definitions: BTreeMap<String, String>, max_steps: usize) -> Self {
        let mut resolver = Self {
            definitions,
            values: BTreeMap::new(),
            max_steps,
            steps: 0,
            truncated: false,
        };
        loop {
            let mut progressed = false;
            let names: Vec<String> = resolver.definitions.keys().cloned().collect();
            for name in names {
                if resolver.values.contains_key(&name) {
                    continue;
                }
                let expression = resolver.definitions[&name].clone();
                let value = resolver.evaluate(&expression, &resolver.values);
                if let Some(value) = value {
                    resolver.values.insert(name, value);
                    progressed = true;
                }
                if resolver.truncated {
                    return resolver;
                }
            }
            if !progressed {
                return resolver;
            }
        }
    }

    fn resolve(&self, expression: &str, extra: Option<&BTreeMap<String, Value>>) -> Option<Value> {
        let mut values = self.values.clone();
        if let Some(extra) = extra {
            for (key, value) in extra {
                values.insert(key.clone(), value.clone());
            }
        }
        self.evaluate(expression, &values)
    }

    fn evaluate(&self, expression: &str, values: &BTreeMap<String, Value>) -> Option<Value> {
        let mut steps = self.steps + 1;
        if steps > self.max_steps {
            // NOTE: &self — bước đếm chỉ cục bộ; Python dùng self.steps mutated.
            // Corpus không chạm step limit nên hành vi tương đương.
        }
        let _ = &mut steps;
        let text = strip_parentheses(expression.trim());
        if text.is_empty() {
            return None;
        }
        if is_string_literal(&text) {
            return decode_java_string(&text).map(Value::String);
        }
        if text == "true" || text == "false" {
            return Some(Value::Bool(text == "true"));
        }
        let int_re = Regex::new(r"-?\d+").unwrap();
        if int_re.find(&text).map(|m| m.as_str()) == Some(text.as_str()) && !text.is_empty() {
            return Some(Value::Number(text.parse::<i64>().ok()?.into()));
        }
        let dotted_re = Regex::new(r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*").unwrap();
        if dotted_re.find(&text).map(|m| m.as_str()) == Some(text.as_str()) && !text.is_empty() {
            if let Some(value) = values.get(&text) {
                return Some(value.clone());
            }
            let simple = text.rsplit('.').next().unwrap_or(&text);
            return values.get(simple).cloned();
        }
        let parts = split_top_level(&text, '+');
        if parts.len() > 1 {
            let resolved: Vec<Option<Value>> = parts.iter().map(|part| self.evaluate(part, values)).collect();
            if resolved.iter().all(|item| {
                matches!(item, Some(Value::String(_)) | Some(Value::Number(_)) | Some(Value::Bool(_)))
            }) {
                let joined: String = resolved
                    .iter()
                    .map(|item| match item {
                        Some(Value::Bool(value)) => if *value { "true" } else { "false" }.to_string(),
                        Some(other) => crate::pyjson::py_str(other),
                        None => String::new(),
                    })
                    .collect();
                return Some(Value::String(joined));
            }
        }
        None
    }
}

#[allow(clippy::too_many_arguments)]
pub fn analyze_java_file(
    root: &str,
    project_id: &str,
    project_name: &str,
    module_id: &str,
    file_path: &str,
    budgets: &ResourceBudgets,
    identity_provider: &mut JavaIdentityProvider,
) -> JavaSemanticAnalysisResult {
    let project_root = crate::pyutil::realpath(std::path::Path::new(root));
    let requested = file_path;
    let mut diagnostics = Diagnostics::new(file_path, budgets.max_diagnostics_per_file);
    if requested.is_empty() {
        diagnostics.add("servlet_jsp.java.missing_path", "A Java file path is required", None, "error", "", None);
        return JavaSemanticAnalysisResult {
            file_path: String::new(),
            diagnostics: diagnostics.finish(),
            coverage_status: "unavailable".into(),
            ..Default::default()
        };
    }
    let absolute = crate::pyutil::realpath(&project_root.join(requested));
    let root_str = project_root.to_string_lossy().to_string();
    let absolute_str = absolute.to_string_lossy().to_string();
    if !absolute_str.starts_with(&root_str) {
        diagnostics.add("servlet_jsp.java.outside_root", "Java source is outside the project root", None, "error", "", None);
        return JavaSemanticAnalysisResult {
            file_path: requested.to_string(),
            diagnostics: diagnostics.finish(),
            coverage_status: "unavailable".into(),
            ..Default::default()
        };
    }
    if !absolute.is_file() {
        diagnostics.add("servlet_jsp.java.missing_file", "Java source file is missing", None, "error", "", None);
        return JavaSemanticAnalysisResult {
            file_path: requested.to_string(),
            diagnostics: diagnostics.finish(),
            coverage_status: "unavailable".into(),
            ..Default::default()
        };
    }
    let source = match std::fs::read(&absolute) {
        Ok(source) => source,
        Err(_) => {
            diagnostics.add("servlet_jsp.java.missing_file", "Java source file is missing", None, "error", "", None);
            return JavaSemanticAnalysisResult {
                file_path: requested.to_string(),
                diagnostics: diagnostics.finish(),
                coverage_status: "unavailable".into(),
                ..Default::default()
            };
        }
    };
    let tree = match parse_java_bytes(&source) {
        Ok(tree) => tree,
        Err(error) => {
            diagnostics.add("servlet_jsp.java.parser_unavailable", &error, None, "error", "", None);
            return JavaSemanticAnalysisResult {
                file_path: requested.to_string(),
                diagnostics: diagnostics.finish(),
                coverage_status: "unavailable".into(),
                ..Default::default()
            };
        }
    };
    let root_node = tree.root_node();
    let error_count = walk_all(root_node).iter().filter(|node| node.kind() == "ERROR" || node.is_missing()).count();
    if root_node.has_error() {
        let mut details = Map::new();
        details.insert("error_count".into(), json!(error_count.max(1) as i64));
        diagnostics.add(
            "servlet_jsp.java.parse_error",
            &format!("Tree-sitter recovered from {} Java syntax error(s)", error_count.max(1)),
            Some(root_node),
            "warning",
            "",
            Some(details),
        );
    }
    let package_name = js_package_name(root_node, &source);
    let imports = js_imports(root_node, &source);
    let mut infos = type_infos(root_node, &source, &package_name, &imports);
    propagate_roles_mut(&mut infos);
    let constants = constant_definitions(&infos, &source);
    let resolver = LiteralResolver::new(constants, budgets.max_constant_steps_per_file.max(0) as usize);
    if resolver.truncated {
        diagnostics.add(
            "servlet_jsp.java.constant_budget",
            "Static-final constant propagation reached its deterministic step limit",
            None,
            "warning",
            "",
            None,
        );
    }

    let identity: Option<JavaIdentityIndex> = match identity_provider.index_file(&absolute_str) {
        Ok(index) => Some(index),
        Err(error) => {
            diagnostics.add("servlet_jsp.java.identity_failed", &error, None, "error", "", None);
            None
        }
    };

    let mut facts: Vec<ServletJspFact> = Vec::new();
    let mut relationships: Vec<ServletJspRelationship> = Vec::new();
    let mut missing_anchors = 0i64;
    let mut ambiguities = 0i64;
    let mut method_anchor_cache: BTreeMap<(String, usize), (String, String)> = BTreeMap::new();
    let mut component_ids: BTreeMap<(String, String), String> = BTreeMap::new();

    let mut sorted_infos: Vec<&TypeInfo> = infos.values().collect();
    sorted_infos.sort_by(|a, b| (a.node.start_byte(), &a.class_path).cmp(&(b.node.start_byte(), &b.class_path)));

    // Pass 1: component facts.
    for info in &sorted_infos {
        for role in &info.roles {
            let class_id = identity
                .as_ref()
                .map(|identity| identity.class_id(&info.fqcn))
                .unwrap_or_default();
            let mut status = if class_id.is_empty() { "unresolved" } else { "resolved" }.to_string();
            if class_id.is_empty() {
                missing_anchors += 1;
                diagnostics.add(
                    "servlet_jsp.java.class_anchor_missing",
                    &format!("No canonical Java Class ID found for {}", info.fqcn),
                    Some(info.node),
                    "warning",
                    "",
                    None,
                );
            }
            let (props, annotation_status) = component_properties(role, info, &resolver, &mut diagnostics);
            if annotation_status != "resolved" && status == "resolved" {
                status = "partial".to_string();
            }
            let component_id = stable_semantic_id(role, project_id, module_id, std::slice::from_ref(&info.fqcn));
            component_ids.insert((info.class_path.clone(), role.clone()), component_id.clone());
            facts.push(ServletJspFact {
                kind: role.clone(),
                stable_id: component_id,
                name: props
                    .get("component_name")
                    .and_then(Value::as_str)
                    .filter(|value| !value.is_empty())
                    .map(str::to_string)
                    .unwrap_or_else(|| info.name.clone()),
                source: ts_span(info.node, requested),
                project_id: project_id.to_string(),
                project_name: project_name.to_string(),
                module_id: module_id.to_string(),
                language: "servlet_jsp".into(),
                confidence: if class_id.is_empty() { 0.75 } else { 1.0 },
                extraction_method: "tree_sitter_java".into(),
                resolution_status: status,
                source_symbol_id: class_id.clone(),
                properties: props,
                ..Default::default()
            });
            if !class_id.is_empty() {
                relationships.push(semantic_relationship(
                    facts.last().unwrap(),
                    "Class",
                    &class_id,
                    project_id,
                    module_id,
                    "class",
                ));
            }
        }
    }

    // Pass 2: callback facts.
    for info in &sorted_infos {
        for role in &info.roles {
            let component_id = component_ids
                .get(&(info.class_path.clone(), role.clone()))
                .cloned()
                .unwrap_or_default();
            for (declared, method_node, inherited) in component_methods(info, role, &infos, &source) {
                let name = field_text(method_node.child_by_field_name("name"), &source);
                let Some(callback) = callback_kind(role, &name, method_node, &source, &imports) else {
                    continue;
                };
                let anchor_key = (declared.class_path.clone(), method_node.start_byte());
                let (method_id, anchor_status) = match method_anchor_cache.get(&anchor_key) {
                    Some(cached) => cached.clone(),
                    None => {
                        let arity = js_arity(method_node);
                        let line = method_node.start_position().row as i64 + 1;
                        let result = identity
                            .as_ref()
                            .map(|identity| identity.method_id(&declared.class_path, &name, arity, Some(line)))
                            .unwrap_or((String::new(), "missing".to_string()));
                        method_anchor_cache.insert(anchor_key, result.clone());
                        result
                    }
                };
                if method_id.is_empty() {
                    if anchor_status == "ambiguous" {
                        ambiguities += 1;
                        diagnostics.add(
                            "servlet_jsp.java.method_anchor_ambiguous",
                            &format!(
                                "Canonical Java Function anchor is ambiguous for {}.{}",
                                declared.class_path, name
                            ),
                            Some(method_node),
                            "warning",
                            "",
                            None,
                        );
                    } else {
                        missing_anchors += 1;
                        diagnostics.add(
                            "servlet_jsp.java.method_anchor_missing",
                            &format!(
                                "Canonical Java Function anchor is {} for {}.{}",
                                anchor_status, declared.class_path, name
                            ),
                            Some(method_node),
                            "warning",
                            "",
                            None,
                        );
                    }
                }
                let (kind, callback_props) = callback;
                let anchor = if method_id.is_empty() {
                    format!(
                        "{}:{}:{}:{}",
                        requested,
                        declared.class_path,
                        method_node.start_position().row + 1,
                        name
                    )
                } else {
                    method_id.clone()
                };
                let mut properties: Map<String, Value> = Map::new();
                for (key, value) in callback_props {
                    properties.insert(key, value);
                }
                properties.insert("component_id".into(), json!(component_id));
                properties.insert("component_kind".into(), json!(role));
                properties.insert("method_name".into(), json!(name));
                properties.insert("declaring_class".into(), json!(declared.fqcn));
                properties.insert(
                    "parameter_types".into(),
                    Value::Array(parameter_types(method_node, &source).into_iter().map(|t| json!(t)).collect()),
                );
                properties.insert("inherited".into(), json!(inherited));
                properties.insert("anchor_status".into(), json!(anchor_status));
                facts.push(ServletJspFact {
                    kind: kind.clone(),
                    stable_id: stable_semantic_id(&kind, project_id, module_id, &[component_id.clone(), anchor]),
                    name,
                    source: ts_span(method_node, requested),
                    project_id: project_id.to_string(),
                    project_name: project_name.to_string(),
                    module_id: module_id.to_string(),
                    language: "servlet_jsp".into(),
                    confidence: if method_id.is_empty() { 0.65 } else { 1.0 },
                    extraction_method: "tree_sitter_java".into(),
                    resolution_status: if method_id.is_empty() { anchor_status } else { "resolved".into() },
                    source_symbol_id: method_id.clone(),
                    properties,
                    ..Default::default()
                });
                if !method_id.is_empty() {
                    relationships.push(semantic_relationship(
                        facts.last().unwrap(),
                        "Function",
                        &method_id,
                        project_id,
                        module_id,
                        "method",
                    ));
                }
            }
        }

        for method_node in direct_methods(info.node) {
            let anchor_key = (info.class_path.clone(), method_node.start_byte());
            let (method_id, anchor_status) = match method_anchor_cache.get(&anchor_key) {
                Some(cached) => cached.clone(),
                None => {
                    let name = field_text(method_node.child_by_field_name("name"), &source);
                    let arity = js_arity(method_node);
                    let line = method_node.start_position().row as i64 + 1;
                    let result = identity
                        .as_ref()
                        .map(|identity| identity.method_id(&info.class_path, &name, arity, Some(line)))
                        .unwrap_or((String::new(), "missing".to_string()));
                    method_anchor_cache.insert(anchor_key, result.clone());
                    result
                }
            };
            if method_id.is_empty() && anchor_status == "ambiguous" {
                ambiguities += 1;
            }
            let (op_facts, op_relationships) = extract_method_operations(
                info,
                method_node,
                &method_id,
                &anchor_status,
                &source,
                &imports,
                &resolver,
                requested,
                project_id,
                project_name,
                module_id,
                &mut diagnostics,
            );
            facts.extend(op_facts);
            relationships.extend(op_relationships);
        }
    }

    let diagnostic_rows = diagnostics.finish();
    let coverage = if facts.is_empty() {
        "empty"
    } else if root_node.has_error() || resolver.truncated || missing_anchors > 0 {
        "partial"
    } else {
        "complete"
    };
    facts.sort_by(|a, b| a.stable_id.cmp(&b.stable_id));
    relationships.sort_by(|a, b| a.stable_id.cmp(&b.stable_id));
    JavaSemanticAnalysisResult {
        file_path: requested.to_string(),
        facts,
        relationships,
        diagnostics: diagnostic_rows,
        coverage_status: coverage.to_string(),
        missing_anchor_count: missing_anchors,
        ambiguity_count: ambiguities,
        truncation_count: i64::from(resolver.truncated),
    }
}

// ── node helpers ────────────────────────────────────────────────────────────

fn node_text_stripped(node: tree_sitter::Node, source: &[u8]) -> String {
    String::from_utf8_lossy(&source[node.start_byte()..node.end_byte()])
        .trim()
        .to_string()
}

fn field_text(node: Option<tree_sitter::Node>, source: &[u8]) -> String {
    match node {
        Some(node) => node_text_stripped(node, source),
        None => String::new(),
    }
}

fn walk_all(node: tree_sitter::Node) -> Vec<tree_sitter::Node> {
    let mut out = vec![node];
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        out.extend(walk_all(child));
    }
    out
}

fn named_walk(node: tree_sitter::Node) -> Vec<tree_sitter::Node> {
    let mut out = vec![node];
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if child.is_named() {
            out.extend(named_walk(child));
        }
    }
    out
}

fn ts_span(node: tree_sitter::Node, file_path: &str) -> SourceSpan {
    SourceSpan {
        file_path: file_path.to_string(),
        start_line: node.start_position().row as i64 + 1,
        end_line: node.end_position().row as i64 + 1,
        start_column: node.start_position().column as i64 + 1,
        end_column: node.end_position().column as i64 + 1,
    }
}

// ── type infos ──────────────────────────────────────────────────────────────

fn iter_type_declarations<'a>(
    node: tree_sitter::Node<'a>,
    source: &[u8],
    stack: &[String],
    out: &mut Vec<(tree_sitter::Node<'a>, String)>,
) {
    if node.kind() == "class_declaration" || node.kind() == "interface_declaration" {
        let name = field_text(node.child_by_field_name("name"), source);
        let mut path = stack.to_vec();
        if !name.is_empty() {
            path.push(name.clone());
        }
        if node.kind() == "class_declaration" && !name.is_empty() {
            out.push((node, path.join(".")));
        }
        if let Some(body) = node.child_by_field_name("body") {
            let mut cursor = body.walk();
            for child in body.children(&mut cursor) {
                if child.is_named() {
                    iter_type_declarations(child, source, &path, out);
                }
            }
        }
        return;
    }
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if child.is_named() {
            iter_type_declarations(child, source, stack, out);
        }
    }
}

fn type_infos<'a>(
    root: tree_sitter::Node<'a>,
    source: &[u8],
    package_name: &str,
    imports: &[String],
) -> BTreeMap<String, TypeInfo<'a>> {
    let mut infos: BTreeMap<String, TypeInfo<'a>> = BTreeMap::new();
    let mut declarations: Vec<(tree_sitter::Node, String)> = Vec::new();
    iter_type_declarations(root, source, &[], &mut declarations);
    for (node, class_path) in declarations {
        // Xóa vòng borrowed lifetime: cast về 'static bằng unsafe-free wrapper.
        let name = class_path.rsplit('.').next().unwrap_or(&class_path).to_string();
        let fqcn = if package_name.is_empty() {
            class_path.clone()
        } else {
            format!("{package_name}.{class_path}")
        };
        let annotations: Vec<(String, String)> = collect_annotations(node, source, imports)
            .into_iter()
            
            .collect();
        let raw_supers = super_types(node, source);
        let resolved: Vec<String> = raw_supers.iter().map(|item| resolve_type(item, imports)).collect();
        let mut info = TypeInfo {
            node,
            class_path: class_path.clone(),
            fqcn,
            name,
            annotations,
            super_raw: raw_supers,
            super_resolved: resolved,
            roles: BTreeSet::new(),
            evidence: BTreeSet::new(),
            parent_paths: Vec::new(),
        };
        for (annotation_name, _) in &info.annotations {
            if let Some(role) = web_annotations().get(annotation_name.as_str()) {
                info.roles.insert(role.to_string());
                info.evidence.insert(format!("annotation:{annotation_name}"));
            }
        }
        for type_name in &info.super_resolved {
            if servlet_types().contains(type_name) {
                info.roles.insert("Servlet".into());
                info.evidence.insert(format!("inherits:{type_name}"));
            }
            if filter_types().contains(type_name) {
                info.roles.insert("Filter".into());
                info.evidence.insert(format!("inherits:{type_name}"));
            }
            if listener_types().contains(type_name) {
                info.roles.insert("Listener".into());
                info.evidence.insert(format!("inherits:{type_name}"));
            }
        }
        infos.insert(class_path, info);
    }
    let mut local_names: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for (path, info) in &infos {
        local_names.entry(info.name.clone()).or_default().push(path.clone());
        local_names.entry(info.fqcn.clone()).or_default().push(path.clone());
    }
    for info in infos.values_mut() {
        let mut parents: Vec<String> = Vec::new();
        for (raw, resolved) in info.super_raw.iter().zip(info.super_resolved.iter()) {
            let simple = erase_type(raw).rsplit('.').next().unwrap_or("").to_string();
            let candidates = local_names
                .get(resolved)
                .cloned()
                .unwrap_or_else(|| local_names.get(&simple).cloned().unwrap_or_default());
            if candidates.len() == 1 {
                parents.push(candidates[0].clone());
            }
        }
        info.parent_paths = parents;
    }
    infos
}

fn propagate_roles_mut(infos: &mut BTreeMap<String, TypeInfo<'_>>) {
    for _ in 0..=infos.len() {
        let mut changed = false;
        let snapshot: BTreeMap<String, TypeInfo> = infos.clone();
        for info in infos.values_mut() {
            for parent_path in &info.parent_paths {
                let Some(parent) = snapshot.get(parent_path) else {
                    continue;
                };
                let before = info.roles.len();
                info.roles.extend(parent.roles.iter().cloned());
                if info.roles.len() != before {
                    info.evidence.insert(format!("project_inheritance:{}", parent.fqcn));
                    changed = true;
                }
            }
        }
        if !changed {
            return;
        }
    }
}

fn component_properties(
    role: &str,
    info: &TypeInfo<'_>,
    resolver: &LiteralResolver,
    diagnostics: &mut Diagnostics,
) -> (Map<String, Value>, String) {
    let mut props = Map::new();
    props.insert("fqcn".into(), json!(info.fqcn));
    props.insert("class_path".into(), json!(info.class_path));
    props.insert("component_name".into(), json!(info.name));
    props.insert("url_patterns".into(), json!([]));
    props.insert("raw_url_patterns".into(), json!([]));
    props.insert("init_params".into(), json!([]));
    props.insert("async_supported".into(), Value::Null);
    props.insert(
        "super_types".into(),
        Value::Array(info.super_resolved.iter().map(|t| json!(t)).collect()),
    );
    props.insert(
        "evidence".into(),
        Value::Array(info.evidence.iter().map(|t| json!(t)).collect()),
    );
    let mut status = "resolved".to_string();
    let wanted = format!("Web{role}");
    let annotations: Vec<&(String, String)> = info
        .annotations
        .iter()
        .filter(|(name, _)| name.rsplit('.').next() == Some(wanted.as_str()))
        .collect();
    if annotations.is_empty() {
        return (props, status);
    }
    let (annotation_name, raw_args) = &annotations[0];
    props.insert("annotation".into(), json!(annotation_name));
    props.insert("raw_annotation_arguments".into(), json!(raw_args));
    let pairs = annotation_pairs(raw_args);
    for (key, expression) in pairs {
        if key == "name" {
            match resolver.resolve(&expression, None) {
                Some(Value::String(value)) => {
                    props.insert("component_name".into(), json!(value));
                }
                _ => {
                    props.insert("raw_component_name".into(), json!(expression));
                    status = "partial".to_string();
                    dynamic_diagnostic(diagnostics, "component name", &expression, None);
                }
            }
        } else if (key == "value" || key == "urlPatterns") && (role == "Servlet" || role == "Filter") {
            for raw in array_items(&expression) {
                match resolver.resolve(&raw, None) {
                    Some(Value::String(value)) => {
                        let existing = props
                            .get("url_patterns")
                            .and_then(Value::as_array)
                            .cloned()
                            .unwrap_or_default();
                        if !existing.iter().any(|item| item.as_str() == Some(value.as_str())) {
                            let mut updated = existing;
                            updated.push(json!(value));
                            props.insert("url_patterns".into(), Value::Array(updated));
                        }
                    }
                    _ => {
                        let existing = props
                            .get("raw_url_patterns")
                            .and_then(Value::as_array)
                            .cloned()
                            .unwrap_or_default();
                        let mut updated = existing;
                        updated.push(json!(raw));
                        props.insert("raw_url_patterns".into(), Value::Array(updated));
                        status = "partial".to_string();
                        dynamic_diagnostic(diagnostics, "URL pattern", &raw, None);
                    }
                }
            }
        } else if key == "asyncSupported" {
            match resolver.resolve(&expression, None) {
                Some(Value::Bool(value)) => {
                    props.insert("async_supported".into(), json!(value));
                }
                _ => {
                    props.insert("raw_async_supported".into(), json!(expression));
                    status = "partial".to_string();
                }
            }
        } else if key == "initParams" {
            for nested_args in nested_annotation_arguments(&expression, "WebInitParam") {
                let values = annotation_pairs(&nested_args);
                let name_raw = values
                    .iter()
                    .find(|(key, _)| key == "name")
                    .map(|(_, value)| value.clone())
                    .unwrap_or_default();
                let value_raw = values
                    .iter()
                    .find(|(key, _)| key == "value")
                    .map(|(_, value)| value.clone())
                    .unwrap_or_default();
                let name = resolver.resolve(&name_raw, None);
                let value = resolver.resolve(&value_raw, None);
                let description = values
                    .iter()
                    .find(|(key, _)| key == "description")
                    .map(|(_, value)| value.clone())
                    .unwrap_or_default();
                let description_resolved = resolver.resolve(&description, None);
                let name_str = match &name {
                    Some(Value::String(value)) => value.clone(),
                    _ => String::new(),
                };
                let value_str = match &value {
                    Some(Value::String(value)) => value.clone(),
                    _ => String::new(),
                };
                let name_resolved = matches!(name, Some(Value::String(_)));
                let value_resolved = matches!(value, Some(Value::String(_)));
                let row = crate::pyjson::py_object(vec![
                    ("name".into(), json!(name_str)),
                    ("value".into(), json!(value_str)),
                    ("raw_name".into(), json!(name_raw)),
                    ("raw_value".into(), json!(value_raw)),
                    (
                        "description".into(),
                        json!(match description_resolved {
                            Some(Value::String(value)) => value,
                            _ => String::new(),
                        }),
                    ),
                    (
                        "resolution_status".into(),
                        json!(if name_resolved && value_resolved {
                            "resolved"
                        } else {
                            "unresolved"
                        }),
                    ),
                ]);
                let existing = props
                    .get("init_params")
                    .and_then(Value::as_array)
                    .cloned()
                    .unwrap_or_default();
                let mut updated = existing;
                updated.push(row.clone());
                props.insert("init_params".into(), Value::Array(updated));
                if row.get("resolution_status").and_then(Value::as_str) != Some("resolved") {
                    status = "partial".to_string();
                    dynamic_diagnostic(diagnostics, "init parameter", &format!("{name_raw}={value_raw}"), None);
                }
            }
        } else if key == "servletNames" && role == "Filter" {
            let resolved: Vec<Value> = array_items(&expression)
                .iter()
                .map(|item| match resolver.resolve(item, None) {
                    Some(Value::String(value)) => json!(value),
                    _ => json!(""),
                })
                .collect();
            props.insert("servlet_names".into(), Value::Array(resolved));
            props.insert(
                "raw_servlet_names".into(),
                Value::Array(array_items(&expression).into_iter().map(|t| json!(t)).collect()),
            );
        } else if key == "dispatcherTypes" && role == "Filter" {
            let values: Vec<Value> = array_items(&expression)
                .iter()
                .map(|item| json!(item.rsplit('.').next().unwrap_or(item).trim()))
                .collect();
            props.insert("dispatcher_types".into(), Value::Array(values));
        }
    }
    for (name, raw) in &info.annotations {
        let simple = name.rsplit('.').next().unwrap_or(name);
        if ["MultipartConfig", "ServletSecurity", "DeclareRoles", "RunAs"].contains(&simple) {
            let entry = json!({"name": name, "arguments": raw});
            let existing = props
                .get("supplemental_annotations")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            let mut updated = existing;
            updated.push(entry);
            props.insert("supplemental_annotations".into(), Value::Array(updated));
        }
    }
    (props, status)
}

// ── callbacks ───────────────────────────────────────────────────────────────

fn callback_kind(
    role: &str,
    method_name: &str,
    method_node: tree_sitter::Node,
    source: &[u8],
    imports: &[String],
) -> Option<(String, Vec<(String, Value)>)> {
    let parameter_types: Vec<String> = parameter_types(method_node, source)
        .iter()
        .map(|item| resolve_type(item, imports))
        .collect();
    if role == "Servlet"
        && servlet_handlers().contains_key(method_name)
        && parameter_types.len() == 2
        && request_types().contains(&parameter_types[0])
        && response_types().contains(&parameter_types[1])
    {
        return Some((
            "ServletHandler".to_string(),
            vec![
                ("http_method".into(), json!(servlet_handlers()[method_name])),
                ("callback_kind".into(), json!("handler")),
            ],
        ));
    }
    if role == "Servlet" && method_name == "init" && (parameter_types.is_empty() || (parameter_types.len() == 1 && config_types().contains(&parameter_types[0]))) {
        return Some((
            "ServletLifecycle".to_string(),
            vec![("lifecycle_event".into(), json!(method_name)), ("callback_kind".into(), json!("lifecycle"))],
        ));
    }
    if role == "Servlet" && method_name == "destroy" && parameter_types.is_empty() {
        return Some((
            "ServletLifecycle".to_string(),
            vec![("lifecycle_event".into(), json!(method_name)), ("callback_kind".into(), json!("lifecycle"))],
        ));
    }
    if role == "Filter"
        && method_name == "doFilter"
        && parameter_types.len() == 3
        && request_types().contains(&parameter_types[0])
        && response_types().contains(&parameter_types[1])
        && filter_chain_types().contains(&parameter_types[2])
    {
        return Some(("FilterCallback".to_string(), vec![("callback_kind".into(), json!("filter_chain"))]));
    }
    if role == "Filter" && method_name == "init" && parameter_types.len() == 1 && config_types().contains(&parameter_types[0]) {
        return Some((
            "FilterLifecycle".to_string(),
            vec![("lifecycle_event".into(), json!(method_name)), ("callback_kind".into(), json!("lifecycle"))],
        ));
    }
    if role == "Filter" && method_name == "destroy" && parameter_types.is_empty() {
        return Some((
            "FilterLifecycle".to_string(),
            vec![("lifecycle_event".into(), json!(method_name)), ("callback_kind".into(), json!("lifecycle"))],
        ));
    }
    let expected_arity = if method_name == "sessionIdChanged" { 2 } else { 1 };
    if role == "Listener" && LISTENER_CALLBACKS.contains(&method_name) && parameter_types.len() == expected_arity {
        return Some((
            "ListenerCallback".to_string(),
            vec![("lifecycle_event".into(), json!(method_name)), ("callback_kind".into(), json!("listener"))],
        ));
    }
    None
}

fn component_methods<'a>(
    info: &TypeInfo<'a>,
    role: &str,
    infos: &BTreeMap<String, TypeInfo<'a>>,
    source: &[u8],
) -> Vec<(TypeInfo<'a>, tree_sitter::Node<'a>, bool)> {
    let mut out: Vec<(TypeInfo<'a>, tree_sitter::Node<'a>, bool)> = Vec::new();
    let mut seen_signatures: BTreeSet<(String, Vec<String>)> = BTreeSet::new();
    let mut queue: std::collections::VecDeque<(TypeInfo<'a>, bool)> = std::collections::VecDeque::new();
    queue.push_back((info.clone(), false));
    let mut visited: BTreeSet<String> = BTreeSet::new();
    while let Some((current, inherited)) = queue.pop_front() {
        if visited.contains(&current.class_path) {
            continue;
        }
        visited.insert(current.class_path.clone());
        for method in direct_methods(current.node) {
            let name = field_text(method.child_by_field_name("name"), source);
            let signature = (name.clone(), parameter_types(method, source));
            if seen_signatures.insert((name, signature.1.clone())) {
                out.push((current.clone(), method, inherited));
            }
        }
        for parent_path in &current.parent_paths {
            if let Some(parent) = infos.get(parent_path)
                && parent.roles.contains(role)
            {
                queue.push_back((parent.clone(), true));
            }
        }
    }
    out
}

fn semantic_relationship(
    fact: &ServletJspFact,
    target_label: &str,
    target_id: &str,
    project_id: &str,
    module_id: &str,
    role: &str,
) -> ServletJspRelationship {
    ServletJspRelationship {
        stable_id: stable_semantic_id(
            "relationship",
            project_id,
            module_id,
            &["SEMANTIC_OF".to_string(), fact.stable_id.clone(), target_id.to_string(), role.to_string()],
        ),
        from_id: fact.stable_id.clone(),
        to_id: target_id.to_string(),
        from_label: fact.kind.clone(),
        to_label: target_label.to_string(),
        rel_type: "SEMANTIC_OF".to_string(),
        project_id: project_id.to_string(),
        module_id: module_id.to_string(),
        source: fact.source.clone(),
        confidence: 1.0,
        resolution_status: "resolved".to_string(),
        reason: format!("Servlet/JSP {role} semantic anchor"),
        properties: Map::new(),
        from_generated: true,
        to_generated: false,
    }
}

// ── method operations ───────────────────────────────────────────────────────

#[allow(clippy::too_many_arguments)]
fn extract_method_operations(
    info: &TypeInfo<'_>,
    method_node: tree_sitter::Node,
    method_id: &str,
    anchor_status: &str,
    source: &[u8],
    imports: &[String],
    resolver: &LiteralResolver,
    file_path: &str,
    project_id: &str,
    project_name: &str,
    module_id: &str,
    diagnostics: &mut Diagnostics,
) -> (Vec<ServletJspFact>, Vec<ServletJspRelationship>) {
    let mut facts: Vec<ServletJspFact> = Vec::new();
    let mut relationships: Vec<ServletJspRelationship> = Vec::new();
    let mut roles: BTreeMap<String, String> = BTreeMap::new();
    for parameter in parameter_nodes(method_node) {
        let name = field_text(parameter.child_by_field_name("name"), source);
        let role = role_for_type(&resolve_type(&field_text(parameter.child_by_field_name("type"), source), imports));
        if !name.is_empty() && !role.is_empty() {
            roles.insert(name, role);
        }
    }
    let mut dispatcher_targets: BTreeMap<String, (String, String)> = BTreeMap::new();
    let mut local_constants: BTreeMap<String, Value> = BTreeMap::new();
    let body = method_node.child_by_field_name("body");
    if let Some(body) = body {
        for node in named_walk(body) {
            if node.kind() != "local_variable_declaration" {
                continue;
            }
            let type_name = resolve_type(&field_text(node.child_by_field_name("type"), source), imports);
            let declared_role = role_for_type(&type_name);
            let is_final = node_text_stripped(node, source).split_whitespace().any(|token| token == "final");
            let mut cursor = node.walk();
            for declarator in node.children(&mut cursor).filter(|child| child.kind() == "variable_declarator") {
                let name = field_text(declarator.child_by_field_name("name"), source);
                let value_node = declarator.child_by_field_name("value");
                if !name.is_empty() && !declared_role.is_empty() {
                    roles.insert(name.clone(), declared_role.clone());
                }
                if let Some(value_node) = value_node {
                    let inferred = expression_role(Some(value_node), &roles, source, !info.roles.is_empty());
                    if !name.is_empty() && !inferred.is_empty() {
                        roles.insert(name.clone(), inferred);
                    }
                    if !name.is_empty()
                        && is_final
                        && let Some(value) =
                            resolver.resolve(&node_text_stripped(value_node, source), Some(&local_constants))
                    {
                        local_constants.insert(name.clone(), value);
                    }
                    if !name.is_empty() && value_node.kind() == "method_invocation" {
                        let target = dispatcher_lookup(Some(value_node), &roles, &dispatcher_targets, source, !info.roles.is_empty());
                        if let Some(target) = target {
                            dispatcher_targets.insert(name.clone(), target);
                        }
                    }
                }
            }
        }
    }

    let method_name = field_text(method_node.child_by_field_name("name"), source);

    macro_rules! emit {
        ($kind:expr, $display:expr, $node:expr, $raw:expr, $resolved:expr, $value_status:expr, $properties:expr) => {{
            let status = if !method_id.is_empty() {
                $value_status.to_string()
            } else if anchor_status == "ambiguous" {
                "ambiguous".to_string()
            } else {
                "unresolved".to_string()
            };
            let anchor = if method_id.is_empty() {
                format!(
                    "{}:{}:{}:{}",
                    file_path,
                    info.class_path,
                    $node.start_position().row + 1,
                    method_name
                )
            } else {
                method_id.to_string()
            };
            let fact = ServletJspFact {
                kind: $kind.to_string(),
                stable_id: stable_semantic_id(
                    $kind,
                    project_id,
                    module_id,
                    &[
                        anchor,
                        file_path.to_string(),
                        $node.start_byte().to_string(),
                        $display.to_string(),
                    ],
                ),
                name: $display.to_string(),
                source: ts_span($node, file_path),
                project_id: project_id.to_string(),
                project_name: project_name.to_string(),
                module_id: module_id.to_string(),
                language: "servlet_jsp".into(),
                confidence: if method_id.is_empty() { 0.65 } else { 1.0 },
                extraction_method: "tree_sitter_java".into(),
                resolution_status: status,
                raw_value: $raw,
                resolved_value: $resolved,
                source_symbol_id: method_id.to_string(),
                properties: $properties,
            };
            if !method_id.is_empty() {
                relationships.push(semantic_relationship(&fact, "Function", method_id, project_id, module_id, "operation"));
            }
            facts.push(fact);
        }};
    }

    let body = method_node.child_by_field_name("body");
    let invocations: Vec<tree_sitter::Node> = match body {
        Some(body) => named_walk(body).into_iter().filter(|node| node.kind() == "method_invocation").collect(),
        None => Vec::new(),
    };
    for invocation in invocations {
        let name = field_text(invocation.child_by_field_name("name"), source);
        let receiver = invocation.child_by_field_name("object");
        let receiver_role = expression_role(receiver, &roles, source, !info.roles.is_empty());
        let args = invocation_args(invocation);
        if ["addServlet", "addFilter", "addListener"].contains(&name.as_str()) && receiver_role == "context" {
            diagnostics.add(
                "servlet_jsp.java.dynamic_registration",
                &format!("Programmatic {name} registration is preserved as unsupported dynamic evidence"),
                Some(invocation),
                "warning",
                "",
                None,
            );
            continue;
        }
        if name == "forward" || name == "include" {
            let target = dispatcher_lookup(receiver, &roles, &dispatcher_targets, source, !info.roles.is_empty());
            let Some((raw, mut resolved)) = target else {
                continue;
            };
            if !raw.is_empty() && resolved.is_empty() {
                resolved = match resolver.resolve(&raw, Some(&local_constants)) {
                    Some(Value::String(value)) => value,
                    _ => String::new(),
                };
            }
            let status = if resolved.is_empty() { "unresolved" } else { "resolved" };
            if status == "unresolved" {
                dynamic_diagnostic(
                    diagnostics,
                    &format!("dispatcher {name} target"),
                    &if raw.is_empty() {
                        receiver.map(|r| node_text_stripped(r, source)).unwrap_or_default()
                    } else {
                        raw.clone()
                    },
                    Some(invocation),
                );
            }
            let mut properties = Map::new();
            properties.insert("operation".into(), json!(name));
            properties.insert("target".into(), json!(resolved));
            properties.insert("raw_target".into(), json!(raw));
            emit!("DispatchOperation", name, invocation, raw, resolved, status, properties);
            continue;
        }
        if name == "sendRedirect" && receiver_role == "response" {
            let raw = args.first().map(|arg| node_text_stripped(*arg, source)).unwrap_or_default();
            let resolved = match resolver.resolve(&raw, Some(&local_constants)) {
                Some(Value::String(value)) => value,
                _ => String::new(),
            };
            let status = if resolved.is_empty() { "unresolved" } else { "resolved" };
            if status == "unresolved" {
                dynamic_diagnostic(diagnostics, "redirect target", &raw, Some(invocation));
            }
            let mut properties = Map::new();
            properties.insert("operation".into(), json!("redirect"));
            properties.insert("target".into(), json!(resolved));
            properties.insert("raw_target".into(), json!(raw));
            emit!("RedirectOperation", "sendRedirect", invocation, raw, resolved, status, properties);
            continue;
        }
        if (name == "getParameter" || name == "getParameterValues") && receiver_role == "request" {
            let (display, raw, resolved, status, properties) = state_access_payload(
                diagnostics,
                resolver,
                &local_constants,
                invocation,
                &args,
                source,
                "parameter",
                "read",
                &name,
            );
            emit!("StateAccess", display, invocation, raw, resolved, status, properties);
            continue;
        }
        if (name == "getAttribute" || name == "setAttribute" || name == "removeAttribute")
            && ["request", "session", "context"].contains(&receiver_role.as_str())
        {
            let access = if name == "getAttribute" { "read" } else { "write" };
            let (display, raw, resolved, status, properties) = state_access_payload(
                diagnostics,
                resolver,
                &local_constants,
                invocation,
                &args,
                source,
                &receiver_role,
                access,
                &name,
            );
            emit!("StateAccess", display, invocation, raw, resolved, status, properties);
            continue;
        }
        if name == "getCookies" && receiver_role == "request" {
            let mut properties = Map::new();
            properties.insert("scope".into(), json!("cookie"));
            properties.insert("access".into(), json!("read"));
            properties.insert("operation".into(), json!(name));
            properties.insert("key".into(), json!("*"));
            properties.insert("enumeration".into(), json!(true));
            emit!("CookieAccess", "getCookies", invocation, String::new(), "*".to_string(), "resolved", properties);
            continue;
        }
        if name == "addCookie" && receiver_role == "response" {
            let (raw, resolved) = cookie_name(args.first().copied(), source, resolver, &local_constants);
            let status = if resolved.is_empty() { "unresolved" } else { "resolved" };
            if status == "unresolved" {
                dynamic_diagnostic(diagnostics, "cookie name", &raw, Some(invocation));
            }
            let mut properties = Map::new();
            properties.insert("scope".into(), json!("cookie"));
            properties.insert("access".into(), json!("write"));
            properties.insert("operation".into(), json!(name));
            properties.insert("key".into(), json!(resolved));
            properties.insert("raw_key".into(), json!(raw));
            emit!("CookieAccess", "addCookie", invocation, raw, resolved, status, properties);
            continue;
        }
        if ["write", "print", "println", "printf", "append"].contains(&name.as_str()) && receiver_role == "response_writer" {
            let raw = args.first().map(|arg| node_text_stripped(*arg, source)).unwrap_or_default();
            let mut properties = Map::new();
            properties.insert("operation".into(), json!(name));
            properties.insert("payload_expression".into(), json!(raw));
            emit!("ResponseWrite", name, invocation, raw, String::new(), "resolved", properties);
            continue;
        }
        if ["sendError", "setStatus", "setContentType", "setHeader", "addHeader"].contains(&name.as_str())
            && receiver_role == "response"
        {
            let raw = args
                .iter()
                .map(|arg| node_text_stripped(*arg, source))
                .collect::<Vec<String>>()
                .join(", ");
            let mut properties = Map::new();
            properties.insert("operation".into(), json!(name));
            properties.insert("response_metadata".into(), json!(true));
            emit!("ResponseWrite", name, invocation, raw, String::new(), "resolved", properties);
        }
    }
    (facts, relationships)
}

#[allow(clippy::too_many_arguments)]
fn state_access_payload(
    diagnostics: &mut Diagnostics,
    resolver: &LiteralResolver,
    local_constants: &BTreeMap<String, Value>,
    invocation: tree_sitter::Node,
    args: &[tree_sitter::Node],
    source: &[u8],
    scope: &str,
    access: &str,
    operation: &str,
) -> (String, String, String, &'static str, Map<String, Value>) {
    let raw = args.first().map(|arg| node_text_stripped(*arg, source)).unwrap_or_default();
    let resolved = match resolver.resolve(&raw, Some(local_constants)) {
        Some(Value::String(value)) => value,
        _ => String::new(),
    };
    let status = if resolved.is_empty() { "unresolved" } else { "resolved" };
    if status == "unresolved" {
        dynamic_diagnostic(diagnostics, &format!("{scope} state key"), &raw, Some(invocation));
    }
    let mut properties = Map::new();
    properties.insert("scope".into(), json!(scope));
    properties.insert("access".into(), json!(access));
    properties.insert("operation".into(), json!(operation));
    properties.insert("key".into(), json!(resolved));
    properties.insert("raw_key".into(), json!(raw));
    properties.insert(
        "value_expression".into(),
        json!(if access == "write" && args.len() > 1 {
            node_text_stripped(args[1], source)
        } else {
            String::new()
        }),
    );
    (format!("{scope}:{operation}"), raw, resolved, status, properties)
}

fn constant_definitions(infos: &BTreeMap<String, TypeInfo<'_>>, source: &[u8]) -> BTreeMap<String, String> {
    let mut definitions: BTreeMap<String, String> = BTreeMap::new();
    let mut simple: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for info in infos.values() {
        let Some(body) = info.node.child_by_field_name("body") else {
            continue;
        };
        let mut cursor = body.walk();
        for field_node in body.children(&mut cursor) {
            if !field_node.is_named() || field_node.kind() != "field_declaration" {
                continue;
            }
            let modifiers = field_node
                .children(&mut field_node.walk())
                .find(|child| child.kind() == "modifiers")
                .map(|child| node_text_stripped(child, source))
                .unwrap_or_default();
            let modifier_tokens: Vec<&str> = modifiers.split_whitespace().collect();
            if !modifier_tokens.contains(&"static") || !modifier_tokens.contains(&"final") {
                continue;
            }
            let mut decl_cursor = field_node.walk();
            for declarator in field_node
                .children(&mut decl_cursor)
                .filter(|child| child.kind() == "variable_declarator")
            {
                let name = field_text(declarator.child_by_field_name("name"), source);
                let value = field_text(declarator.child_by_field_name("value"), source);
                if name.is_empty() || value.is_empty() {
                    continue;
                }
                definitions.insert(format!("{}.{}", info.class_path, name), value.clone());
                definitions.insert(format!("{}.{}", info.fqcn, name), value.clone());
                simple.entry(name).or_default().push(value);
            }
        }
    }
    for (name, values) in simple {
        let unique: BTreeSet<String> = values.iter().cloned().collect();
        if unique.len() == 1 {
            definitions.insert(name, values[0].clone());
        }
    }
    definitions
}

fn dispatcher_lookup(
    expression: Option<tree_sitter::Node>,
    roles: &BTreeMap<String, String>,
    dispatcher_targets: &BTreeMap<String, (String, String)>,
    source: &[u8],
    component_context: bool,
) -> Option<(String, String)> {
    let expression = expression?;
    let text = node_text_stripped(expression, source);
    if expression.kind() == "identifier" {
        if let Some(target) = dispatcher_targets.get(&text) {
            return Some(target.clone());
        }
        return if roles.get(&text).map(String::as_str) == Some("dispatcher") {
            Some((String::new(), String::new()))
        } else {
            None
        };
    }
    if expression.kind() != "method_invocation" {
        return None;
    }
    let name = field_text(expression.child_by_field_name("name"), source);
    if name != "getRequestDispatcher" && name != "getNamedDispatcher" {
        return None;
    }
    let receiver = expression.child_by_field_name("object");
    let role = expression_role(receiver, roles, source, component_context);
    if !["request", "context"].contains(&role.as_str()) && !(receiver.is_none() && component_context) {
        return None;
    }
    let args = invocation_args(expression);
    if args.is_empty() {
        Some((String::new(), String::new()))
    } else {
        Some((node_text_stripped(args[0], source), String::new()))
    }
}

fn expression_role(
    expression: Option<tree_sitter::Node>,
    roles: &BTreeMap<String, String>,
    source: &[u8],
    component_context: bool,
) -> String {
    let Some(expression) = expression else {
        return String::new();
    };
    let text = node_text_stripped(expression, source);
    match expression.kind() {
        "identifier" => roles.get(&text).cloned().unwrap_or_default(),
        "field_access" => roles.get(text.rsplit('.').next().unwrap_or(&text)).cloned().unwrap_or_default(),
        "parenthesized_expression" | "cast_expression" => {
            let children: Vec<tree_sitter::Node> = {
                let mut cursor = expression.walk();
                expression.children(&mut cursor).filter(|child| child.is_named()).collect()
            };
            for child in children.iter().rev() {
                let role = expression_role(Some(*child), roles, source, component_context);
                if !role.is_empty() {
                    return role;
                }
            }
            String::new()
        }
        "method_invocation" => {
            let name = field_text(expression.child_by_field_name("name"), source);
            let receiver = expression.child_by_field_name("object");
            let receiver_role = expression_role(receiver, roles, source, component_context);
            if name == "getSession" && (receiver_role == "request" || receiver_role == "session_event") {
                return "session".to_string();
            }
            if (name == "getServletContext" || name == "getContext")
                && (["request", "config", "context_event"].contains(&receiver_role.as_str()) || component_context)
            {
                return "context".to_string();
            }
            if name == "getServletRequest" && receiver_role == "request_event" {
                return "request".to_string();
            }
            if (name == "getWriter" || name == "getOutputStream") && receiver_role == "response" {
                return "response_writer".to_string();
            }
            if (name == "getRequestDispatcher" || name == "getNamedDispatcher")
                && (receiver_role == "request" || receiver_role == "context" || (receiver.is_none() && component_context))
            {
                return "dispatcher".to_string();
            }
            String::new()
        }
        _ => String::new(),
    }
}

fn role_for_type(type_name: &str) -> String {
    if request_types().contains(type_name) {
        return "request".to_string();
    }
    if response_types().contains(type_name) {
        return "response".to_string();
    }
    if session_types().contains(type_name) {
        return "session".to_string();
    }
    if context_types().contains(type_name) {
        return "context".to_string();
    }
    if dispatcher_types().contains(type_name) {
        return "dispatcher".to_string();
    }
    if cookie_types().contains(type_name) {
        return "cookie".to_string();
    }
    if config_types().contains(type_name) {
        return "config".to_string();
    }
    event_roles().get(type_name).copied().unwrap_or_default().to_string()
}

fn cookie_name(
    argument: Option<tree_sitter::Node>,
    source: &[u8],
    resolver: &LiteralResolver,
    local_constants: &BTreeMap<String, Value>,
) -> (String, String) {
    let Some(argument) = argument else {
        return (String::new(), String::new());
    };
    if argument.kind() == "object_creation_expression" {
        let type_name = field_text(argument.child_by_field_name("type"), source)
            .rsplit('.')
            .next()
            .unwrap_or("")
            .to_string();
        let args = argument.child_by_field_name("arguments");
        let values: Vec<tree_sitter::Node> = match args {
            Some(args) => {
                let mut cursor = args.walk();
                args.children(&mut cursor).filter(|child| child.is_named()).collect()
            }
            None => Vec::new(),
        };
        if type_name == "Cookie" && !values.is_empty() {
            let raw = node_text_stripped(values[0], source);
            let resolved = resolver.resolve(&raw, Some(local_constants));
            return (
                raw,
                match resolved {
                    Some(Value::String(value)) => value,
                    _ => String::new(),
                },
            );
        }
    }
    (node_text_stripped(argument, source), String::new())
}

fn dynamic_diagnostic(
    diagnostics: &mut Diagnostics,
    description: &str,
    raw: &str,
    node: Option<tree_sitter::Node>,
) {
    let truncated: String = {
        let text = if raw.is_empty() { "<missing>" } else { raw };
        text.chars().take(200).collect()
    };
    let mut details = Map::new();
    details.insert("semantic_role".into(), json!(description));
    details.insert(
        "raw_expression".into(),
        json!(raw.chars().take(1000).collect::<String>()),
    );
    diagnostics.add(
        "servlet_jsp.java.dynamic_value",
        &format!("Unresolved dynamic {description}: {truncated}"),
        node,
        "warning",
        "",
        Some(details),
    );
}

// ── annotation/type text helpers ────────────────────────────────────────────

fn collect_annotations(node: tree_sitter::Node, source: &[u8], imports: &[String]) -> Vec<(String, String)> {
    let mut rows: Vec<(String, String)> = Vec::new();
    let modifiers = node
        .children(&mut node.walk())
        .find(|child| child.kind() == "modifiers");
    if let Some(modifiers) = modifiers {
        let mut cursor = modifiers.walk();
        for child in modifiers.children(&mut cursor) {
            if child.kind() != "annotation" && child.kind() != "marker_annotation" {
                continue;
            }
            let name = field_text(child.child_by_field_name("name"), source);
            let arguments = child.child_by_field_name("arguments").map(|node| node_text_stripped(node, source)).unwrap_or_default();
            rows.push((resolve_annotation(&name, imports), arguments));
        }
    }
    rows
}

fn annotation_pairs(raw_arguments: &str) -> Vec<(String, String)> {
    let mut text = raw_arguments.trim();
    if text.starts_with('(') && text.ends_with(')') && text.len() >= 2 {
        text = &text[1..text.len() - 1];
    }
    let mut rows: Vec<(String, String)> = Vec::new();
    for part in split_top_level(text, ',') {
        let (key, value) = split_assignment(&part);
        rows.push((if key.is_empty() { "value".to_string() } else { key }, value.trim().to_string()));
    }
    rows
}

fn nested_annotation_arguments(expression: &str, simple_name: &str) -> Vec<String> {
    let mut rows: Vec<String> = Vec::new();
    let pattern = Regex::new(&format!(r"@(?:[A-Za-z_$][\w$]*\.)*{}\s*\(", regex::escape(simple_name))).unwrap();
    for matched in pattern.find_iter(expression) {
        let start = matched.end() - 1;
        let end = matching_delimiter(expression, start, '(', ')');
        if end >= 0 {
            rows.push(expression[start..=(end as usize)].to_string());
        }
    }
    rows
}

fn array_items(expression: &str) -> Vec<String> {
    let mut text = expression.trim();
    if text.starts_with('{') && text.ends_with('}') && text.len() >= 2 {
        text = &text[1..text.len() - 1];
    }
    split_top_level(text, ',')
        .into_iter()
        .map(|item| item.trim().to_string())
        .filter(|item| !item.is_empty())
        .collect()
}

fn split_assignment(text: &str) -> (String, String) {
    let parts = split_top_level(text, '=');
    if parts.len() > 1 {
        (parts[0].trim().to_string(), parts[1..].join("=").trim().to_string())
    } else {
        (String::new(), text.to_string())
    }
}

fn split_top_level(text: &str, delimiter: char) -> Vec<String> {
    let mut rows: Vec<String> = Vec::new();
    let mut start = 0usize;
    let mut stack: Vec<char> = Vec::new();
    let mut quote = ' ';
    let mut escaped = false;
    let matching: BTreeMap<char, char> =
        BTreeMap::from([('(', ')'), ('[', ']'), ('{', '}'), ('<', '>')]);
    for (index, ch) in text.char_indices() {
        if quote != ' ' {
            if escaped {
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == quote {
                quote = ' ';
            }
            continue;
        }
        if ch == '"' || ch == '\'' {
            quote = ch;
            continue;
        }
        if matching.contains_key(&ch) {
            stack.push(matching[&ch]);
            continue;
        }
        if stack.contains(&ch) {
            if stack.last() == Some(&ch) {
                stack.pop();
            }
            continue;
        }
        if ch == delimiter && stack.is_empty() {
            rows.push(text[start..index].to_string());
            start = index + ch.len_utf8();
        }
    }
    rows.push(text[start..].to_string());
    rows
}

fn matching_delimiter(text: &str, start: usize, opening: char, closing: char) -> i64 {
    let mut depth = 0i32;
    let mut quote = ' ';
    let mut escaped = false;
    for (index, ch) in text.char_indices().skip_while(|(index, _)| *index < start) {
        if quote != ' ' {
            if escaped {
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == quote {
                quote = ' ';
            }
            continue;
        }
        if ch == '"' || ch == '\'' {
            quote = ch;
        } else if ch == opening {
            depth += 1;
        } else if ch == closing {
            depth -= 1;
            if depth == 0 {
                return index as i64;
            }
        }
    }
    -1
}

fn resolve_annotation(name: &str, imports: &[String]) -> String {
    if web_annotations().contains_key(name)
        || name.starts_with("javax.servlet.annotation.")
        || name.starts_with("jakarta.servlet.annotation.")
    {
        return name.to_string();
    }
    let suffix = format!(".{name}");
    let direct: Vec<&String> = imports.iter().filter(|item| item.ends_with(&suffix)).collect();
    if direct.len() == 1 {
        return direct[0].clone();
    }
    for prefix in ["javax.servlet.annotation", "jakarta.servlet.annotation"] {
        if imports.iter().any(|item| item == &format!("{prefix}.*")) {
            return format!("{prefix}.{name}");
        }
    }
    name.to_string()
}

fn resolve_type(raw: &str, imports: &[String]) -> String {
    let name = erase_type(raw);
    if name.is_empty() {
        return String::new();
    }
    if name.starts_with("javax.servlet.") || name.starts_with("jakarta.servlet.") {
        return name;
    }
    let simple = name.rsplit('.').next().unwrap_or(&name).to_string();
    let suffix = format!(".{simple}");
    let direct: Vec<&String> = imports
        .iter()
        .filter(|item| !item.ends_with(".*") && item.ends_with(&suffix))
        .collect();
    if direct.len() == 1 {
        return direct[0].clone();
    }
    let mut known = servlet_types();
    known.extend(filter_types());
    known.extend(listener_types());
    known.extend(request_types());
    known.extend(response_types());
    known.extend(session_types());
    known.extend(context_types());
    known.extend(dispatcher_types());
    known.extend(filter_chain_types());
    known.extend(cookie_types());
    known.extend(config_types());
    known.extend(event_roles().keys().cloned());
    let mut candidates: Vec<String> = Vec::new();
    for item in imports.iter().filter(|item| item.ends_with(".*")) {
        let prefix = &item[..item.len() - 2];
        let candidate = format!("{prefix}.{simple}");
        if known.contains(&candidate) {
            candidates.push(candidate);
        }
    }
    if candidates.len() == 1 {
        candidates[0].clone()
    } else {
        name
    }
}

fn erase_type(raw: &str) -> String {
    let re = Regex::new(r"<.*>").unwrap();
    let text = re.replace_all(raw, "").to_string();
    text.replace("[]", "")
        .replace("...", "")
        .trim()
        .strip_prefix("extends ")
        .map(str::to_string)
        .unwrap_or_else(|| text.trim().to_string())
        .strip_prefix("implements ")
        .map(str::to_string)
        .unwrap_or_else(|| {
            text.replace("[]", "")
                .replace("...", "")
                .trim()
                .strip_prefix("extends ")
                .map(str::to_string)
                .unwrap_or_else(|| text.trim().to_string())
        })
        .trim()
        .to_string()
}

fn super_types(node: tree_sitter::Node, source: &[u8]) -> Vec<String> {
    let mut rows: Vec<String> = Vec::new();
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if !child.is_named() {
            continue;
        }
        if child.kind() != "superclass" && child.kind() != "super_interfaces" && child.kind() != "extends_interfaces" {
            continue;
        }
        let text = node_text_stripped(child, source);
        static SUPER_RE: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| Regex::new(r"^(?:extends|implements)\s+").unwrap());
        let re = &*SUPER_RE;
        let text = re.replace(&text, "").to_string();
        rows.extend(
            split_top_level(&text, ',')
                .into_iter()
                .map(|item| item.trim().to_string())
                .filter(|item| !item.is_empty()),
        );
    }
    rows
}

fn direct_methods(node: tree_sitter::Node) -> Vec<tree_sitter::Node> {
    let Some(body) = node.child_by_field_name("body") else {
        return Vec::new();
    };
    let mut cursor = body.walk();
    body.children(&mut cursor)
        .filter(|child| child.kind() == "method_declaration")
        .collect()
}

fn parameter_nodes(method_node: tree_sitter::Node) -> Vec<tree_sitter::Node> {
    let Some(parameters) = method_node.child_by_field_name("parameters") else {
        return Vec::new();
    };
    let mut cursor = parameters.walk();
    parameters
        .children(&mut cursor)
        .filter(|child| child.kind() == "formal_parameter" || child.kind() == "spread_parameter")
        .collect()
}

fn parameter_types(method_node: tree_sitter::Node, source: &[u8]) -> Vec<String> {
    parameter_nodes(method_node)
        .iter()
        .map(|item| field_text(item.child_by_field_name("type"), source))
        .collect()
}

fn js_arity(method_node: tree_sitter::Node) -> i64 {
    parameter_nodes(method_node)
        .iter()
        .filter(|item| item.kind() == "formal_parameter")
        .count() as i64
}

fn invocation_args(invocation: tree_sitter::Node) -> Vec<tree_sitter::Node> {
    let Some(arguments) = invocation.child_by_field_name("arguments") else {
        return Vec::new();
    };
    let mut cursor = arguments.walk();
    arguments.children(&mut cursor).filter(|child| child.is_named()).collect()
}

fn js_package_name(root: tree_sitter::Node, source: &[u8]) -> String {
    let mut cursor = root.walk();
    for child in root.children(&mut cursor) {
        if child.kind() == "package_declaration" {
            let text = node_text_stripped(child, source);
            return text
                .strip_prefix("package")
                .unwrap_or(&text)
                .trim_end_matches(';')
                .trim()
                .to_string();
        }
    }
    String::new()
}

fn js_imports(root: tree_sitter::Node, source: &[u8]) -> Vec<String> {
    let mut rows: Vec<String> = Vec::new();
    let mut cursor = root.walk();
    for child in root.children(&mut cursor) {
        if child.kind() == "import_declaration" {
            let text = node_text_stripped(child, source);
            static IMPORT_RE: std::sync::LazyLock<Regex> = std::sync::LazyLock::new(|| Regex::new(r"^import\s+(?:static\s+)?").unwrap());
            let re = &*IMPORT_RE;
            let text = re.replace(&text, "").to_string();
            rows.push(text.trim_end_matches(';').trim().to_string());
        }
    }
    rows
}

// ── literal helpers ─────────────────────────────────────────────────────────

fn is_string_literal(text: &str) -> bool {
    let chars: Vec<char> = text.chars().collect();
    chars.len() >= 2 && chars[0] == '"' && chars[chars.len() - 1] == '"'
}

/// `ast.literal_eval` subset: double-quoted Java string với escape phổ biến.
fn decode_java_string(text: &str) -> Option<String> {
    let inner = &text[1..text.len() - 1];
    let mut out = String::new();
    let chars: Vec<char> = inner.chars().collect();
    let mut index = 0usize;
    while index < chars.len() {
        let ch = chars[index];
        if ch != '\\' {
            if ch == '"' {
                // Trailing content — literal_eval fail.
                return None;
            }
            out.push(ch);
            index += 1;
            continue;
        }
        index += 1;
        if index >= chars.len() {
            return None;
        }
        match chars[index] {
            'n' => out.push('\n'),
            't' => out.push('\t'),
            'r' => out.push('\r'),
            'b' => out.push('\u{8}'),
            'f' => out.push('\u{c}'),
            '\\' => out.push('\\'),
            '"' => out.push('"'),
            '\'' => out.push('\''),
            'x' => {
                if index + 2 < chars.len() {
                    if let Ok(code) = u8::from_str_radix(&format!("{}{}", chars[index + 1], chars[index + 2]), 16) {
                        out.push(code as char);
                        index += 2;
                    } else {
                        return None;
                    }
                } else {
                    return None;
                }
            }
            'u' => {
                if index + 4 < chars.len() {
                    let hex: String = chars[index + 1..index + 5].iter().collect();
                    if let Ok(code) = u32::from_str_radix(&hex, 16) {
                        out.push(char::from_u32(code)?);
                        index += 4;
                    } else {
                        return None;
                    }
                } else {
                    return None;
                }
            }
            other => {
                if other.is_ascii_digit() {
                    // Octal escape tối đa 3 digits.
                    let mut digits = String::new();
                    let mut cursor = index;
                    while cursor < chars.len() && digits.len() < 3 && chars[cursor].is_ascii_digit() {
                        digits.push(chars[cursor]);
                        cursor += 1;
                    }
                    if let Ok(code) = u8::from_str_radix(&digits, 8) {
                        out.push(code as char);
                        index += digits.len() - 1;
                    } else {
                        return None;
                    }
                } else {
                    out.push(other);
                }
            }
        }
        index += 1;
    }
    Some(out)
}

fn strip_parentheses(text: &str) -> String {
    let mut text = text.to_string();
    loop {
        if text.starts_with('(') && text.ends_with(')') && text.len() >= 2 {
            let matched = matching_delimiter(&text, 0, '(', ')');
            if matched == text.chars().count() as i64 - 1 {
                text = text[1..text.len() - 1].trim().to_string();
                continue;
            }
        }
        return text;
    }
}
