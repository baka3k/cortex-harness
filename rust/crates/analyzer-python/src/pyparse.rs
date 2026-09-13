//! Port phần parse của `tools/python/python_analyzer.py`: tree-sitter walk,
//! FunctionDef/ClassDef/FileDef, docstring/signature/base-classes/self-fields,
//! entrypoint detection, calls extraction, imports collection.

use serde_json::{Map, Value};

use cortex_analyzer_framework::ts::{
    decode_ignore, extract_file_comment, extract_leading_comment, find_nodes_by_type, node_snippet,
    node_text,
};

pub type Row = Map<String, Value>;

// ── Data defs (asdict shape khớp Python payload JSON) ───────────────────────

#[derive(Debug, Clone, Default)]
pub struct FunctionDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub kind: String,
    pub scope_name: Option<String>,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    pub arity: i64,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
    pub exported: bool,
    // Semantic fields
    pub intent: String,
    pub inferred_doc: bool,
    pub doc_confidence: f64,
    pub side_effect: bool,
    // Entrypoint
    pub is_entrypoint: bool,
    pub entrypoint_kind: String,
}

#[derive(Debug, Clone)]
pub struct CallEdge {
    pub caller_id: String,
    pub caller_scope: Option<String>,
    pub callee_name: String,
    pub callee_arity: Option<i64>,
    pub callee_receiver: Option<String>,
}

#[derive(Debug, Clone)]
pub struct ClassDef {
    pub symbol_id: String,
    pub qualified_name: String,
    pub name: String,
    pub kind: String,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
    pub exported: bool,
    pub base_classes: Vec<String>,
    pub self_fields: std::collections::BTreeMap<String, String>,
}

#[derive(Debug, Clone)]
pub struct RelationEdge {
    pub source_id: String,
    pub target_id: String,
    pub rel_type: String,
    pub properties: Row,
}

#[derive(Debug, Clone)]
pub struct FileDef {
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    pub code: String,
    pub comment: String,
    pub summary: String,
    pub note: String,
    pub imports: Vec<String>,
    pub exports: Vec<String>,
}

#[derive(Debug, Default)]
pub struct FilePayload {
    pub functions: Vec<FunctionDef>,
    pub calls: Vec<CallEdge>,
    pub classes: Vec<ClassDef>,
    pub namespaces: Vec<Value>,
    pub relations: Vec<RelationEdge>,
    pub file_def: Option<FileDef>,
}

// ── Helpers (port từng hàm `_` của python_analyzer.py) ──────────────────────

fn build_note(code: &str, comment: &str, summary: &str) -> String {
    let mut parts: Vec<String> = Vec::new();
    if !summary.is_empty() {
        parts.push(format!("Summary:\n{summary}"));
    }
    if !comment.is_empty() {
        parts.push(format!("Comment:\n{comment}"));
    }
    if !code.is_empty() {
        parts.push(format!("Code:\n{code}"));
    }
    parts.join("\n\n")
}

/// `_extract_docstring` — string đầu tiên trong body của function/class.
fn extract_docstring(node: tree_sitter::Node, source: &[u8]) -> String {
    let Some(body) = node.child_by_field_name("body") else {
        return String::new();
    };
    for child in body.children(&mut body.walk()) {
        if child.kind() == "expression_statement" {
            for sub in child.children(&mut child.walk()) {
                if sub.kind() == "string" || sub.kind() == "concatenated_string" {
                    let raw = node_text(sub, source).trim().to_string();
                    for q in ["\"\"\"", "'''", "\"", "'"] {
                        if raw.starts_with(q) && raw.ends_with(q) && raw.len() > 2 * q.len() {
                            return raw[q.len()..raw.len() - q.len()].trim().to_string();
                        }
                    }
                    return raw;
                }
            }
        } else if child.is_named() {
            break;
        }
    }
    String::new()
}

/// `_extract_function_signature` — def ... : không body (bracket-depth scan).
fn extract_function_signature(node: tree_sitter::Node, source: &[u8]) -> String {
    let full = node_text(node, source);
    let mut depth: i32 = 0;
    for (i, ch) in full.char_indices() {
        match ch {
            '(' | '[' | '{' => depth += 1,
            ')' | ']' | '}' => depth -= 1,
            ':' if depth == 0 => return full[..i + ch.len_utf8()].trim().to_string(),
            _ => {}
        }
    }
    full.split('\n').next().unwrap_or("").trim().to_string()
}

/// `_build_structured_note` — Signature > Docstring/Comment > truncated body.
fn build_structured_note(signature: &str, docstring: &str, comment: &str, code: &str) -> String {
    const MAX_BODY_CHARS: usize = 800;
    let mut parts: Vec<String> = Vec::new();
    if !signature.is_empty() {
        parts.push(format!("Signature:\n{signature}"));
    }
    if !docstring.is_empty() {
        parts.push(format!("Docstring:\n{docstring}"));
    } else if !comment.is_empty() {
        parts.push(format!("Comment:\n{comment}"));
    }
    let body = if code.chars().count() <= MAX_BODY_CHARS {
        code.to_string()
    } else {
        // Python cắt theo code-point (len/[:n] là char-based).
        let truncated: String = code.chars().take(MAX_BODY_CHARS).collect();
        format!("{truncated}\n# ... (truncated)")
    };
    if !body.is_empty() {
        parts.push(format!("Code:\n{body}"));
    }
    parts.join("\n\n")
}

/// `_extract_base_classes` — tên đơn giản từ superclasses field.
fn extract_base_classes(class_node: tree_sitter::Node, source: &[u8]) -> Vec<String> {
    let mut bases = Vec::new();
    let Some(superclasses) = class_node.child_by_field_name("superclasses") else {
        return bases;
    };
    for child in superclasses.children(&mut superclasses.walk()) {
        if child.kind() == "identifier" || child.kind() == "attribute" {
            let raw = node_text(child, source).trim().to_string();
            if !raw.is_empty() && raw != "," && raw != "(" && raw != ")" {
                bases.push(raw.rsplit('.').next().unwrap_or(&raw).to_string());
            }
        }
    }
    bases
}

/// `_scan_self_assignment` — self.field = ClassName(...) / annotated.
fn scan_self_assignment(
    node: tree_sitter::Node,
    source: &[u8],
    result: &mut std::collections::BTreeMap<String, String>,
) {
    if node.kind() == "expression_statement" {
        for child in node.children(&mut node.walk()) {
            scan_self_assignment(child, source, result);
        }
        return;
    }
    if node.kind() == "assignment" {
        let left = node.child_by_field_name("left");
        let right = node.child_by_field_name("right");
        if let Some(left) = left
            && left.kind() == "attribute" {
                let obj = left.child_by_field_name("object");
                let attr = left.child_by_field_name("attribute");
                if let (Some(obj), Some(attr)) = (obj, attr)
                    && node_text(obj, source) == "self" {
                        let field_name = node_text(attr, source);
                        if let Some(right) = right
                            && right.kind() == "call"
                                && let Some(func) = right.child_by_field_name("function") {
                                    result.insert(
                                        field_name,
                                        node_text(func, source)
                                            .rsplit('.')
                                            .next()
                                            .unwrap_or("")
                                            .to_string(),
                                    );
                                }
                    }
            }
    } else if node.kind() == "annotated_assignment" {
        let target = node.child_by_field_name("lhs").or(node.child_by_field_name("left"));
        let ann = node.child_by_field_name("type");
        if let (Some(target), Some(ann)) = (target, ann)
            && target.kind() == "attribute" {
                let obj = target.child_by_field_name("object");
                let attr_node = target.child_by_field_name("attribute");
                if let (Some(obj), Some(attr_node)) = (obj, attr_node)
                    && node_text(obj, source) == "self" {
                        let field_name = node_text(attr_node, source);
                        let ann_text = node_text(ann, source);
                        let ann_head = ann_text.split('[').next().unwrap_or("");
                        let ann_name = ann_head.rsplit('.').next().unwrap_or("").trim();
                        result.insert(field_name, ann_name.to_string());
                    }
            }
    }
}

/// `_extract_self_fields` — __init__ body scan.
fn extract_self_fields(
    class_node: tree_sitter::Node,
    source: &[u8],
) -> std::collections::BTreeMap<String, String> {
    let mut self_fields = std::collections::BTreeMap::new();
    let Some(body) = class_node.child_by_field_name("body") else {
        return self_fields;
    };
    for child in body.children(&mut body.walk()) {
        if child.kind() == "function_definition"
            && let Some(name_node) = child.child_by_field_name("name")
                && node_text(name_node, source) == "__init__" {
                    if let Some(init_body) = child.child_by_field_name("body") {
                        for stmt in init_body.children(&mut init_body.walk()) {
                            scan_self_assignment(stmt, source, &mut self_fields);
                        }
                    }
                    break;
                }
    }
    self_fields
}

/// Entrypoint decorators — port `_ENTRYPOINT_KIND_MAP` đúng thứ tự.
fn entrypoint_patterns() -> &'static [(regex::Regex, &'static str)] {
    use std::sync::OnceLock;
    static PATTERNS: OnceLock<Vec<(regex::Regex, &'static str)>> = OnceLock::new();
    PATTERNS.get_or_init(|| {
        [
            (
                r"(?i)@(?:app|router|blueprint|api_router)\.(get|post|put|delete|patch|head|options|route|websocket)",
                "http_handler",
            ),
            (r"(?i)@api_view\b", "http_handler"),
            (r"(?i)@(?:celery\.task|shared_task|app\.task)\b", "celery_task"),
            (
                r"(?i)@(?:on_event|event\.listen(?:er)?|signal\.connect)\b",
                "event_handler",
            ),
            (r"(?i)@(?:click|cli|app)\.command\b", "cli_command"),
            (r"(?i)@pytest\.(?:fixture|mark)\b", "test"),
        ]
        .iter()
        .map(|(pat, kind)| (regex::Regex::new(pat).expect("entrypoint pattern"), *kind))
        .collect()
    })
}

fn detect_entrypoint_kind(decorated_node: tree_sitter::Node, source: &[u8]) -> (bool, &'static str) {
    let code = node_text(decorated_node, source);
    for (pattern, kind) in entrypoint_patterns() {
        if pattern.is_match(&code) {
            return (true, kind);
        }
    }
    (false, "")
}

fn resolve_import_to_path(import_str: &str, module_to_rel_path: &std::collections::HashMap<String, String>) -> Option<String> {
    let re_from = regex::Regex::new(r"from\s+([\w.]+)\s+import").expect("from import");
    if let Some(caps) = re_from.captures(import_str)
        && let Some(m) = caps.get(1) {
            return module_to_rel_path.get(m.as_str()).cloned();
        }
    let re_import = regex::Regex::new(r"import\s+([\w.]+)").expect("import");
    if let Some(caps) = re_import.captures(import_str)
        && let Some(m) = caps.get(1) {
            return module_to_rel_path.get(m.as_str()).cloned();
        }
    None
}

/// Public wrapper dùng bởi resolve.rs.
pub fn resolve_import_path(
    import_str: &str,
    module_to_rel_path: &std::collections::HashMap<String, String>,
) -> Option<String> {
    resolve_import_to_path(import_str, module_to_rel_path)
}

/// `_build_module_index` — dotted module → rel path.
pub fn build_module_index(scanned_files: &[std::path::PathBuf], root: &std::path::Path) -> std::collections::HashMap<String, String> {
    let mut index = std::collections::HashMap::new();
    for file_path in scanned_files {
        let rel = cortex_analyzer_framework::scan::rel_posix(root, file_path);
        let mut module = rel.replace('/', ".");
        if module.ends_with(".py") {
            module.truncate(module.len() - 3);
        }
        if module.ends_with(".__init__") {
            module.truncate(module.len() - 9);
        }
        index.insert(module, rel);
    }
    index
}

fn normalize_call_name(text: &str) -> String {
    let re_brackets = regex::Regex::new(r"<[^<>]*>").expect("brackets");
    let cleaned = re_brackets.replace_all(text, "");
    let cleaned = cleaned.replace("?.", ".").replace("::", ".");
    let cleaned = cleaned.trim();
    if cleaned.contains('.') {
        cleaned.rsplit('.').next().unwrap_or(cleaned).trim().to_string()
    } else {
        cleaned.trim().to_string()
    }
}

fn extract_scope_stack(stack: &[String]) -> Option<String> {
    if stack.is_empty() {
        None
    } else {
        Some(stack.join("::"))
    }
}

fn symbol_id(scope: Option<&str>, name: &str, arity: i64, rel_path: &str) -> String {
    let qualified = match scope {
        Some(scope) => format!("{scope}::{name}"),
        None => name.to_string(),
    };
    format!("{qualified}/{arity}@{rel_path}")
}

fn qualified_name(scope: Option<&str>, name: &str) -> String {
    match scope {
        Some(scope) => format!("{scope}::{name}"),
        None => name.to_string(),
    }
}

fn anonymous_name(prefix: &str, node: tree_sitter::Node) -> String {
    format!(
        "Anonymous{prefix}@{}:{}",
        node.start_position().row + 1,
        node.start_position().column + 1
    )
}

/// `_count_parameters` — named children trừ comment.
fn count_parameters(node: tree_sitter::Node) -> i64 {
    let params = node
        .child_by_field_name("parameters")
        .or_else(|| node.child_by_field_name("parameter_list"));
    let Some(params) = params else { return 0 };
    params
        .children(&mut params.walk())
        .filter(|child| child.is_named() && child.kind() != "comment")
        .count() as i64
}

fn count_arguments(node: tree_sitter::Node) -> i64 {
    let args = node
        .child_by_field_name("arguments")
        .or_else(|| node.child_by_field_name("argument_list"));
    let Some(args) = args else { return 0 };
    args.children(&mut args.walk())
        .filter(|child| child.is_named() && child.kind() != "comment")
        .count() as i64
}

/// `_extract_call_name` — (callee_name, receiver_chain).
fn extract_call_name(call_node: tree_sitter::Node, source: &[u8]) -> (Option<String>, Option<String>) {
    if let Some(expr) = call_node.child_by_field_name("function") {
        if expr.kind() == "attribute" {
            let attr = expr.child_by_field_name("attribute");
            let obj = expr.child_by_field_name("object");
            if let Some(attr) = attr {
                let receiver = obj.map(|o| node_text(o, source).trim().to_string());
                return (
                    Some(normalize_call_name(node_text(attr, source).trim())),
                    receiver.filter(|r| !r.is_empty()),
                );
            }
        }
        return (
            Some(normalize_call_name(node_text(expr, source).trim())),
            None,
        );
    }
    let text = node_text(call_node, source).trim().to_string();
    let text = text.split('(').next().unwrap_or("").trim().to_string();
    (Some(normalize_call_name(&text)), None)
}

/// `_collect_imports` — import_statement + import_from_statement (normalized).
fn collect_imports(root: tree_sitter::Node, source: &[u8]) -> Vec<String> {
    let mut imports = Vec::new();
    for node in find_nodes_by_type(root, "import_statement") {
        let text = normalize_ws_import(&node_text(node, source));
        if !text.is_empty() {
            imports.push(text);
        }
    }
    for node in find_nodes_by_type(root, "import_from_statement") {
        let text = normalize_ws_import(&node_text(node, source));
        if !text.is_empty() {
            imports.push(text);
        }
    }
    imports
}

fn normalize_ws_import(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn first_identifier(node: tree_sitter::Node, source: &[u8]) -> Option<String> {
    if node.kind() == "identifier" || node.kind() == "attribute" {
        return Some(node_text(node, source));
    }
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        if let Some(result) = first_identifier(child, source) {
            return Some(result);
        }
    }
    None
}

fn extract_name_field(node: tree_sitter::Node, source: &[u8]) -> Option<String> {
    if let Some(name_node) = node.child_by_field_name("name") {
        return Some(node_text(name_node, source));
    }
    first_identifier(node, source)
}

// ── Tree walk ───────────────────────────────────────────────────────────────

struct WalkState<'a> {
    source: &'a [u8],
    rel_path: String,
    functions: Vec<FunctionDef>,
    calls: Vec<CallEdge>,
    classes: Vec<ClassDef>,
    relations: Vec<RelationEdge>,
    namespaces: Vec<Value>,
}

#[allow(clippy::too_many_arguments)]
fn record_function(
    state: &mut WalkState<'_>,
    node: tree_sitter::Node,
    namespace_stack: &[String],
    class_stack: &[String],
    name_override: Option<String>,
    kind_override: Option<String>,
    calls_root: Option<tree_sitter::Node>,
    exported: bool,
    is_entrypoint: bool,
    entrypoint_kind: &str,
) {
    let source = state.source;
    let mut name = name_override
        .or_else(|| extract_name_field(node, source))
        .unwrap_or_default();
    if name.is_empty() {
        name = anonymous_name("Function", node);
    }
    let kind = kind_override.unwrap_or_else(|| "function".to_string());
    let (snippet, start_line, end_line) = node_snippet(node, source);
    let comment = extract_leading_comment(node, source);
    let docstring = extract_docstring(node, source);
    let signature = extract_function_signature(node, source);
    let effective_comment = if !docstring.is_empty() { docstring.clone() } else { comment.clone() };
    let summary = effective_comment.clone();
    let note = build_structured_note(&signature, &docstring, &comment, &snippet);
    let mut scope_stack: Vec<String> = namespace_stack.to_vec();
    scope_stack.extend_from_slice(class_stack);
    let scope_name = extract_scope_stack(&scope_stack);
    let arity = count_parameters(node);
    let func_id = symbol_id(scope_name.as_deref(), &name, arity, &state.rel_path);
    state.functions.push(FunctionDef {
        symbol_id: func_id.clone(),
        qualified_name: qualified_name(scope_name.as_deref(), &name),
        name,
        kind,
        scope_name: scope_name.clone(),
        file_path: state.rel_path.clone(),
        start_line: start_line as i64,
        end_line: end_line as i64,
        arity,
        code: snippet.clone(),
        comment: effective_comment.clone(),
        summary,
        note,
        exported,
        intent: String::new(),
        inferred_doc: false,
        doc_confidence: 0.0,
        side_effect: false,
        is_entrypoint,
        entrypoint_kind: entrypoint_kind.to_string(),
    });
    if !class_stack.is_empty() {
        let mut full: Vec<String> = namespace_stack.to_vec();
        full.extend_from_slice(class_stack);
        state.relations.push(RelationEdge {
            source_id: full.join("::"),
            target_id: func_id.clone(),
            rel_type: "CONTAINS".to_string(),
            properties: Map::new(),
        });
    } else if !namespace_stack.is_empty() {
        state.relations.push(RelationEdge {
            source_id: format!("namespace::{}", namespace_stack.join("::")),
            target_id: func_id.clone(),
            rel_type: "CONTAINS".to_string(),
            properties: Map::new(),
        });
    }
    let call_root = calls_root.unwrap_or(node);
    for call_node in find_nodes_by_type(call_root, "call") {
        let (callee, receiver) = extract_call_name(call_node, source);
        let Some(callee) = callee else { continue };
        if callee.is_empty() {
            continue;
        }
        state.calls.push(CallEdge {
            caller_id: func_id.clone(),
            caller_scope: scope_name.clone(),
            callee_name: callee,
            callee_arity: Some(count_arguments(call_node)),
            callee_receiver: receiver,
        });
    }
}

fn walk_tree(
    state: &mut WalkState<'_>,
    node: tree_sitter::Node,
    namespace_stack: &[String],
    class_stack: &[String],
) {
    let source = state.source;
    if node.kind() == "decorated_definition" {
        let mut target: Option<tree_sitter::Node> = None;
        for child in node.children(&mut node.walk()) {
            if child.kind() == "class_definition" || child.kind() == "function_definition" {
                target = Some(child);
                break;
            }
        }
        if let Some(target) = target {
            if target.kind() == "function_definition" {
                let (is_ep, ep_kind) = detect_entrypoint_kind(node, source);
                record_function(
                    state,
                    target,
                    namespace_stack,
                    class_stack,
                    None,
                    None,
                    None,
                    false,
                    is_ep,
                    ep_kind,
                );
            } else {
                walk_tree(state, target, namespace_stack, class_stack);
            }
        }
        return;
    }

    if node.kind() == "class_definition" {
        let mut kind = "class".to_string();
        let mut name = extract_name_field(node, source).unwrap_or_default();
        if name.is_empty() {
            name = anonymous_name("Class", node);
            kind = "anonymous_class".to_string();
        }
        let mut full: Vec<String> = namespace_stack.to_vec();
        full.extend_from_slice(class_stack);
        let qualified = if full.is_empty() {
            name.clone()
        } else {
            format!("{}::{name}", full.join("::"))
        };
        let class_id = qualified.clone();
        let (snippet, start_line, end_line) = node_snippet(node, source);
        let comment = extract_leading_comment(node, source);
        let docstring = extract_docstring(node, source);
        let effective_comment = if !docstring.is_empty() { docstring.clone() } else { comment.clone() };
        let summary = effective_comment.clone();
        let note = build_structured_note(&format!("class {name}"), &docstring, &comment, &snippet);
        let base_classes = extract_base_classes(node, source);
        let self_fields = extract_self_fields(node, source);
        let class_name = qualified.rsplit("::").next().unwrap_or(&qualified).to_string();
        state.classes.push(ClassDef {
            symbol_id: class_id.clone(),
            qualified_name: qualified,
            name: class_name,
            kind,
            file_path: state.rel_path.clone(),
            start_line: start_line as i64,
            end_line: end_line as i64,
            code: snippet,
            comment: effective_comment,
            summary,
            note,
            exported: false,
            base_classes,
            self_fields,
        });
        if !namespace_stack.is_empty() {
            state.relations.push(RelationEdge {
                source_id: format!("namespace::{}", namespace_stack.join("::")),
                target_id: class_id.clone(),
                rel_type: "CONTAINS".to_string(),
                properties: Map::new(),
            });
        }
        if !class_stack.is_empty() {
            let mut parent: Vec<String> = namespace_stack.to_vec();
            parent.extend_from_slice(class_stack);
            state.relations.push(RelationEdge {
                source_id: parent.join("::"),
                target_id: class_id.clone(),
                rel_type: "CONTAINS".to_string(),
                properties: Map::new(),
            });
        }
        let mut child_stack: Vec<String> = class_stack.to_vec();
        child_stack.push(name);
        for child in node.children(&mut node.walk()) {
            walk_tree(state, child, namespace_stack, &child_stack);
        }
        return;
    }

    if node.kind() == "function_definition" {
        record_function(state, node, namespace_stack, class_stack, None, None, None, false, false, "");
        return;
    }

    for child in node.children(&mut node.walk()) {
        walk_tree(state, child, namespace_stack, class_stack);
    }
}

/// `parse_python_file` — walk 1 file, trả payload asdict-shape.
pub fn parse_python_file(path: &std::path::Path, root: &std::path::Path) -> Result<FilePayload, String> {
    let rel_path = cortex_analyzer_framework::scan::rel_posix(root, path);
    let source_bytes = std::fs::read(path).map_err(|e| e.to_string())?;
    let mut parser = tree_sitter::Parser::new();
    parser
        .set_language(&tree_sitter_python::LANGUAGE.into())
        .map_err(|e| e.to_string())?;
    let tree = parser
        .parse(&source_bytes, None)
        .ok_or_else(|| format!("parse failed: {rel_path}"))?;

    let snippet = decode_ignore(&source_bytes);
    let end_line = snippet.matches('\n').count() as i64 + 1;
    let file_comment = extract_file_comment(tree.root_node(), &source_bytes);
    let file_summary = file_comment.clone();
    let file_note = build_note(&snippet, &file_comment, &file_summary);
    let imports = collect_imports(tree.root_node(), &source_bytes);
    let file_def = FileDef {
        file_path: rel_path,
        start_line: 1,
        end_line,
        code: snippet,
        comment: file_comment,
        summary: file_summary,
        note: file_note,
        imports,
        exports: Vec::new(),
    };

    let mut state = WalkState {
        source: &source_bytes,
        rel_path: file_def.file_path.clone(),
        functions: Vec::new(),
        calls: Vec::new(),
        classes: Vec::new(),
        relations: Vec::new(),
        namespaces: Vec::new(),
    };
    walk_tree(&mut state, tree.root_node(), &[], &[]);
    Ok(FilePayload {
        functions: state.functions,
        calls: state.calls,
        classes: state.classes,
        namespaces: state.namespaces,
        relations: state.relations,
        file_def: Some(file_def),
    })
}

// ── JSON row builders (asdict shape) ────────────────────────────────────────
