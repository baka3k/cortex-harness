//! Swift tree-sitter walker — Tier 1 port of `swift_analyzer.py`.
//!
//! Faithful Rust port of the Python `parse_swift_file` → `_walk_tree` pipeline.
//! Swift (like Go / Rust-lang) uses list-typed `using_imports` / `macros` / `includes`,
//! so this module shares the same shape as `rust_lang.rs` and `go.rs`.

use std::collections::HashMap;

use tree_sitter::{Node, Parser};

use crate::grammar::{Grammar, SwiftGrammar};
use crate::symbols::{
    AliasDef, CallEdge, FieldDef, FileDef, FunctionDef, NamespaceDef, ParseMeta, RelationEdge,
    TemplateDef, TypeDef,
};
use crate::text::{extract_file_comment, node_text};

// ── Node-type sets (mirror the Python module constants) ─────────────────

const COMMENT_TYPES: &[&str] = &["comment", "multiline_comment"];

/// Compute snippet + adjusted start_line + end_line, including preceding
/// contiguous comments. Mirrors Python `_node_snippet`.
fn node_snippet_swift<'a>(node: Node<'a>, source: &[u8]) -> (String, u32, u32) {
    let mut start_byte = node.start_byte();
    let mut prev = node.prev_sibling();
    while let Some(p) = prev {
        if COMMENT_TYPES.contains(&p.kind()) {
            start_byte = p.start_byte();
            prev = p.prev_sibling();
        } else {
            break;
        }
    }
    let snippet = std::str::from_utf8(&source[start_byte..node.end_byte()])
        .unwrap_or("")
        .to_string();
    let start_line = line_from_byte(source, start_byte);
    let end_line = node.end_position().row as u32 + 1;
    (snippet, start_line, end_line)
}

/// Maps tree-sitter node kind → Swift semantic type kind.
///
/// Python `_TYPE_NODES` only matches `class_declaration` / `protocol_declaration`
/// (no `struct_declaration`, no `enum_declaration`, no `extension_declaration`).
/// This module mirrors that *exact* behavior — the differential test asserts the
/// match; the kind string is normalized through `_normalize_type_kind`.
fn type_kind_for(node_kind: &str) -> Option<&'static str> {
    match node_kind {
        "class_declaration" => Some("class"),
        "protocol_declaration" => Some("interface"),
        _ => None,
    }
}

/// Normalize the raw declaration-kind string from `_extract_decl_kind`.
/// Mirrors Python `_normalize_type_kind`.
fn normalize_type_kind(kind: &str) -> &'static str {
    match kind {
        "protocol" => "interface",
        "actor" => "class",
        "extension" => "record",
        "class" | "struct" | "enum" => "record", // never reached in differential path
        _ => "record",
    }
}

const FUNCTION_NODES: &[&str] = &[
    "function_declaration",
    "protocol_function_declaration",
    "init_declaration",
    "deinit_declaration",
    "subscript_declaration",
];
const ALIAS_NODES: &[&str] = &["typealias_declaration", "associatedtype_declaration"];
const CALL_NODES: &[&str] = &["call_expression", "constructor_expression", "macro_invocation"];
const PROPERTY_NODES: &[&str] = &["property_declaration", "protocol_property_declaration"];

fn branch_kind_of(node_kind: &str) -> Option<&'static str> {
    match node_kind {
        "if_statement" => Some("if"),
        "guard_statement" => Some("guard"),
        "switch_statement" => Some("switch"),
        "catch_block" => Some("catch"),
        "do_statement" => Some("do"),
        _ => None,
    }
}

fn is_loop_node(node_kind: &str) -> bool {
    matches!(
        node_kind,
        "for_statement" | "while_statement" | "repeat_while_statement" | "repeat"
    )
}

// ── Swift-specific ParseOutput (list-typed using_imports/macros/includes) ─

/// Swift payload — mirrors the dict shape returned by Python `parse_swift_file`.
#[derive(Debug, Default)]
pub struct SwiftParseOutput {
    pub file_def: FileDef,
    pub functions: Vec<FunctionDef>,
    pub calls: Vec<CallEdge>,
    pub types: Vec<TypeDef>,
    pub namespaces: Vec<NamespaceDef>,
    pub relations: Vec<RelationEdge>,
    pub function_types: Vec<crate::symbols::FunctionTypeDef>,
    pub fields: Vec<FieldDef>,
    pub aliases: Vec<AliasDef>,
    pub templates: Vec<TemplateDef>,
    pub using_namespaces: Vec<String>,
    pub using_imports: Vec<String>,
    pub includes: Vec<String>,
    pub macros: Vec<String>,
    pub parse_meta: ParseMeta,
}

// ── Helpers (ports of the Python `_foo` helpers) ────────────────────────

#[inline]
fn line_from_byte(source: &[u8], byte_index: usize) -> u32 {
    source[..byte_index]
        .iter()
        .filter(|&&b| b == b'\n')
        .count() as u32
        + 1
}

fn extract_name<'a>(node: Node<'a>, source: &[u8]) -> Option<String> {
    if let Some(name_node) = node.child_by_field_name("name") {
        // Python: if name_node.type == "pattern", look for bound_identifier inside it.
        if name_node.kind() == "pattern" {
            if let Some(bound) = name_node.child_by_field_name("bound_identifier") {
                return Some(clean_identifier(node_text(bound, source)));
            }
        }
        let text = clean_identifier(node_text(name_node, source));
        if !text.is_empty() {
            return Some(text);
        }
    }
    if let Some(bound) = node.child_by_field_name("bound_identifier") {
        return Some(node_text(bound, source).trim().to_string());
    }
    if let Some(found) = first_named_child_of_types(node, &["type_identifier", "simple_identifier", "identifier"]) {
        return Some(clean_identifier(node_text(found, source)));
    }
    first_descendant_of_types(node, &["type_identifier", "simple_identifier", "identifier"])
        .map(|n| clean_identifier(node_text(n, source)))
}

/// Strip whitespace, take the last dotted segment, strip backticks.
/// Mirrors Python `_clean_identifier`.
fn clean_identifier(text: &str) -> String {
    let collapsed: String = text.split_whitespace().collect::<Vec<_>>().join(" ");
    let trimmed = collapsed.trim();
    let final_seg = if trimmed.contains('.') {
        trimmed.split('.').next_back().unwrap_or(trimmed)
    } else {
        trimmed
    };
    final_seg.trim_matches('`').to_string()
}

fn first_named_child_of_types<'a>(node: Node<'a>, types: &[&str]) -> Option<Node<'a>> {
    let allowed: std::collections::HashSet<&str> = types.iter().copied().collect();
    for child in node.children(&mut node.walk()) {
        if child.is_named() && allowed.contains(child.kind()) {
            return Some(child);
        }
    }
    None
}

fn first_descendant_of_types<'a>(node: Node<'a>, types: &[&str]) -> Option<Node<'a>> {
    let allowed: std::collections::HashSet<&str> = types.iter().copied().collect();
    for child in node.children(&mut node.walk()) {
        if child.is_named() && allowed.contains(child.kind()) {
            return Some(child);
        }
        if let Some(found) = first_descendant_of_types(child, &allowed.iter().copied().collect::<Vec<_>>()) {
            return Some(found);
        }
    }
    None
}

fn find_nodes_by_type<'a>(node: Node<'a>, node_type: &str) -> Vec<Node<'a>> {
    let mut found = Vec::new();
    let mut stack = vec![node];
    while let Some(current) = stack.pop() {
        if current.kind() == node_type {
            found.push(current);
        }
        let mut children = current
            .children(&mut current.walk())
            .collect::<Vec<_>>();
        children.reverse();
        stack.extend(children);
    }
    found
}

fn first_named_child_of_type<'a>(node: Node<'a>, types: &[&str]) -> Option<Node<'a>> {
    let allowed: std::collections::HashSet<&str> = types.iter().copied().collect();
    for child in node.children(&mut node.walk()) {
        if !child.is_named() {
            continue;
        }
        if allowed.contains(child.kind()) {
            return Some(child);
        }
    }
    None
}

/// Scope separator for Swift is `.` (like Go, unlike Rust's `::`).
#[inline]
fn qualified_name(scope_stack: &[String], name: &str) -> String {
    if scope_stack.is_empty() {
        name.to_string()
    } else {
        let mut parts: Vec<&str> = scope_stack.iter().map(|s| s.as_str()).collect();
        parts.push(name);
        parts.join(".")
    }
}

#[inline]
fn scope_name_of(scope_stack: &[String]) -> Option<String> {
    if scope_stack.is_empty() {
        None
    } else {
        Some(scope_stack.join("."))
    }
}

fn symbol_id(qualified_name: &str, arity: u32, rel_path: &str) -> String {
    format!("{}/{}@{}", qualified_name, arity, rel_path)
}

fn type_id(qualified_name: &str) -> String {
    qualified_name.to_string()
}

fn namespace_id(qualified_name: &str) -> String {
    format!("namespace::{}", qualified_name)
}

fn anonymous_name(prefix: &str, node: Node) -> String {
    let pos = node.start_position();
    format!(
        "Anonymous{}@{}:{}",
        prefix,
        pos.row as u32 + 1,
        pos.column as u32 + 1
    )
}

fn extract_comment<'a>(node: Node<'a>, source: &[u8]) -> String {
    let mut parts: Vec<String> = Vec::new();
    let mut prev = node.prev_sibling();
    while let Some(p) = prev {
        if !COMMENT_TYPES.contains(&p.kind()) {
            break;
        }
        let text = node_text(p, source).trim();
        if !text.is_empty() {
            parts.push(text.to_string());
        }
        prev = p.prev_sibling();
    }
    parts.reverse();
    parts.join("\n")
}

fn count_parameters<'a>(node: Node<'a>) -> u32 {
    let mut count = 0u32;
    for child in node.children(&mut node.walk()) {
        if child.is_named() && child.kind() == "parameter" {
            count += 1;
        }
    }
    count
}

/// Mirrors Python `_top_level_argument_count` — bracket-aware comma split.
fn top_level_argument_count(text: &str) -> u32 {
    let text = text.trim();
    if text.is_empty() {
        return 0;
    }
    let mut depth: i32 = 0;
    let mut count: u32 = 1;
    let mut in_string: Option<char> = None;
    let mut escaped = false;
    for ch in text.chars() {
        if let Some(q) = in_string {
            if escaped {
                escaped = false;
            } else if ch == '\\' {
                escaped = true;
            } else if ch == q {
                in_string = None;
            }
            continue;
        }
        if ch == '\'' || ch == '"' {
            in_string = Some(ch);
            continue;
        }
        match ch {
            '(' | '[' | '{' | '<' => depth += 1,
            ')' | ']' | '}' | '>' => depth = (depth - 1).max(0),
            ',' if depth == 0 => count += 1,
            _ => {}
        }
    }
    count
}

fn count_arguments<'a>(node: Node<'a>, source: &[u8]) -> u32 {
    let text = node_text(node, source);
    if let Some(caps) = regex::Regex::new(r"\((.*)\)\s*$")
        .ok()
        .and_then(|re| re.captures(text))
    {
        if let Some(inner) = caps.get(1) {
            return top_level_argument_count(inner.as_str());
        }
        return 0;
    }
    if let Some(suffix) = first_named_child_of_type(node, &["call_suffix", "constructor_suffix"]) {
        let suffix_text = node_text(suffix, source);
        if let Some(caps) = regex::Regex::new(r"\((.*)\)")
            .ok()
            .and_then(|re| re.captures(suffix_text))
        {
            if let Some(inner) = caps.get(1) {
                return top_level_argument_count(inner.as_str());
            }
        }
    }
    0
}

fn extract_type_signature<'a>(node: Node<'a>, source: &[u8]) -> String {
    if let Some(type_node) = node.child_by_field_name("type") {
        return node_text(type_node, source).trim().to_string();
    }
    if let Some(annotation) = first_named_child_of_type(node, &["type_annotation"]) {
        if let Some(type_node) = annotation.child_by_field_name("type") {
            return node_text(type_node, source).trim().to_string();
        }
        if let Some(name_node) = annotation.child_by_field_name("name") {
            return node_text(name_node, source).trim().to_string();
        }
        let text = node_text(annotation, source).trim();
        if let Some(colon_idx) = text.find(':') {
            return text[colon_idx + 1..].trim().to_string();
        }
    }
    let text = node_text(node, source).trim();
    if let Some(colon_idx) = text.find(':') {
        let after = text[colon_idx + 1..].trim();
        if let Some(eq_idx) = after.find('=') {
            return after[..eq_idx].trim().to_string();
        }
        return after.trim_end_matches(',').to_string();
    }
    String::new()
}

fn extract_alias_target<'a>(node: Node<'a>, source: &[u8]) -> Option<String> {
    if let Some(value_node) = node.child_by_field_name("value") {
        return Some(node_text(value_node, source).trim().to_string());
    }
    let text = node_text(node, source);
    let re = regex::Regex::new(r"=\s*(.*)$").ok()?;
    let caps = re.captures(text)?;
    let target = caps.get(1)?.as_str();
    Some(
        regex::Regex::new(r"\s+")
            .unwrap()
            .replace_all(target, " ")
            .trim()
            .to_string(),
    )
}

/// Mirrors Python `_extract_decl_kind` — regex scan of the declaration text
/// to find the kind keyword (`actor|class|struct|enum|extension|protocol`).
fn extract_decl_kind<'a>(node: Node<'a>, source: &[u8]) -> String {
    if let Some(kind_node) = node.child_by_field_name("declaration_kind") {
        let text = node_text(kind_node, source).trim();
        if !text.is_empty() {
            return text.to_string();
        }
    }
    let text = node_text(node, source);
    let re = regex::Regex::new(r"\b(actor|class|struct|enum|extension|protocol)\b").unwrap();
    if let Some(caps) = re.captures(text) {
        if let Some(m) = caps.get(1) {
            return m.as_str().to_string();
        }
    }
    "record".to_string()
}

fn extract_import_path<'a>(node: Node<'a>, source: &[u8]) -> String {
    // First try concatenating named identifier children (Swift `import A.B.C`).
    let mut parts: Vec<String> = Vec::new();
    for child in node.children(&mut node.walk()) {
        if child.is_named() && child.kind() == "identifier" {
            let text = node_text(child, source).trim();
            if !text.is_empty() {
                parts.push(text.to_string());
            }
        }
    }
    if !parts.is_empty() {
        return parts.join(".");
    }
    // Fallback: regex strip leading `import ...` prefix and any kind keyword.
    let text = node_text(node, source).trim();
    let re = regex::Regex::new(r"^import\s+(class|struct|enum|protocol|func|var|typealias)?\s*").unwrap();
    let cleaned = re.replace(&text, "");
    cleaned.trim().to_string()
}

fn call_name<'a>(call_node: Node<'a>, source: &[u8]) -> String {
    if call_node.kind() == "macro_invocation" {
        let text = node_text(call_node, source).trim();
        if let Some(caps) = regex::Regex::new(r"#?([A-Za-z_][A-Za-z0-9_]*)")
            .ok()
            .and_then(|re| re.captures(text))
        {
            if let Some(m) = caps.get(1) {
                return m.as_str().to_string();
            }
        }
        return anonymous_name("Macro", call_node);
    }
    if call_node.kind() == "constructor_expression" {
        if let Some(name) = extract_name(call_node, source) {
            return name;
        }
    }
    let text = node_text(call_node, source).trim();
    let collapsed: String = text.split_whitespace().collect::<Vec<_>>().join(" ");
    let before_paren = collapsed.split('(').next().unwrap_or("").trim();
    let before_paren = before_paren.split('?').next_back().unwrap_or(before_paren);
    let before_paren = before_paren.split('!').next_back().unwrap_or(before_paren);
    let before_paren = if before_paren.contains('.') {
        before_paren.split('.').next_back().unwrap_or(before_paren)
    } else {
        before_paren
    };
    let cleaned = before_paren.trim_matches(|c: char| c == '`' || c == '#');
    if cleaned.is_empty() {
        anonymous_name("Call", call_node)
    } else {
        cleaned.to_string()
    }
}

fn control_context<'a>(node: Node<'a>) -> (String, u32, String) {
    let mut frames: Vec<HashMap<String, serde_json::Value>> = Vec::new();
    let mut branch_kind = "none".to_string();
    let mut loop_depth = 0u32;
    let mut parent = node.parent();
    while let Some(p) = parent {
        if let Some(kind) = branch_kind_of(p.kind()) {
            if branch_kind == "none" {
                branch_kind = kind.to_string();
            }
            let mut frame = HashMap::new();
            frame.insert(
                "kind".to_string(),
                serde_json::Value::String(kind.to_string()),
            );
            frame.insert(
                "line".to_string(),
                serde_json::Value::Number(serde_json::Number::from(
                    p.start_position().row as u32 + 1,
                )),
            );
            frames.push(frame);
        } else if is_loop_node(p.kind()) {
            loop_depth += 1;
            let mut frame = HashMap::new();
            frame.insert(
                "kind".to_string(),
                serde_json::Value::String("loop".to_string()),
            );
            frame.insert(
                "line".to_string(),
                serde_json::Value::Number(serde_json::Number::from(
                    p.start_position().row as u32 + 1,
                )),
            );
            frames.push(frame);
        }
        parent = p.parent();
    }
    frames.reverse();
    let frames_json: Vec<serde_json::Value> = frames
        .into_iter()
        .map(|f| serde_json::Value::Object(f.into_iter().collect()))
        .collect();
    let compact = serde_json::Value::Array(frames_json).to_string();
    // Match Python json.dumps default formatting (spaces after : and ,)
    let spaced = compact.replace(":", ": ").replace(",", ", ");
    (branch_kind, loop_depth, spaced)
}

fn record_relation(
    relations: &mut Vec<RelationEdge>,
    source_id: &str,
    source_label: &str,
    target_id: &str,
    target_label: &str,
    rel_type: &str,
) {
    relations.push(RelationEdge {
        source_id: source_id.to_string(),
        source_label: source_label.to_string(),
        target_id: target_id.to_string(),
        target_label: target_label.to_string(),
        rel_type: rel_type.to_string(),
        properties: HashMap::new(),
    });
}

/// Swift-specific type-use edge.
///
/// Unlike Rust, Swift has no `&` / `*const` / `*mut` pointer distinction —
/// everything is `USES_TYPE`. Strips Swift's `<>`/`[]`/`()` punctuation and
/// Swift's type-attribute keywords, then keeps only the leading-uppercase
/// tokens as candidate types.
fn add_type_use(
    owner_id: &str,
    owner_label: &str,
    type_text: &str,
    rel_path: &str,
    types: &mut Vec<TypeDef>,
    relations: &mut Vec<RelationEdge>,
    external_types: &mut HashMap<String, TypeDef>,
    rel_type: &str,
) {
    let cleaned = regex::Regex::new(r"[<>\[\](),:?!=&|]")
        .unwrap()
        .replace_all(type_text, " ");
    let cleaned = regex::Regex::new(
        r"\b(any|some|inout|async|throws|rethrows|where|Self|self)\b",
    )
    .unwrap()
    .replace_all(&cleaned, " ");

    let candidates: Vec<String> = cleaned
        .split(|c: char| c.is_whitespace() || c == '.')
        .filter(|s| {
            if s.is_empty() {
                return false;
            }
            s.chars().next().map(|c| c.is_uppercase()).unwrap_or(false)
        })
        .map(|s| s.to_string())
        .collect();

    for candidate in candidates {
        let target = type_id(&candidate);
        if !external_types.contains_key(&target) {
            let ext = TypeDef {
                symbol_id: target.clone(),
                qualified_name: candidate.clone(),
                name: candidate.clone(),
                kind: "external".to_string(),
                file_path: rel_path.to_string(),
                start_line: 0,
                end_line: 0,
                code: candidate,
                ..Default::default()
            };
            external_types.insert(target.clone(), ext.clone());
            types.push(ext);
        }
        record_relation(relations, owner_id, owner_label, &target, "Type", rel_type);
    }
}

fn extract_templates<'a>(node: Node<'a>, rel_path: &str, source: &[u8]) -> Vec<TemplateDef> {
    let mut templates = Vec::new();
    for template_node in find_nodes_by_type(node, "type_parameters") {
        let text = node_text(template_node, source).trim().to_string();
        let start_line = template_node.start_position().row as u32 + 1;
        let end_line = template_node.end_position().row as u32 + 1;
        templates.push(TemplateDef {
            symbol_id: format!("template::{}:{}:{}", rel_path, start_line, end_line),
            name: text.clone(),
            file_path: rel_path.to_string(),
            start_line,
            end_line,
            code: text,
        });
    }
    templates
}

// ── The recursive walker (mirrors Python `_walk_tree`) ──────────────────

/// Mutable state threaded through the Swift walk.
struct SwiftWalkCtx<'a> {
    source: &'a [u8],
    rel_path: &'a str,
    functions: &'a mut Vec<FunctionDef>,
    calls: &'a mut Vec<CallEdge>,
    types: &'a mut Vec<TypeDef>,
    namespaces: &'a mut Vec<NamespaceDef>,
    relations: &'a mut Vec<RelationEdge>,
    fields: &'a mut Vec<FieldDef>,
    aliases: &'a mut Vec<AliasDef>,
    templates: &'a mut Vec<TemplateDef>,
    type_registry: &'a mut HashMap<String, TypeDef>,
    external_types: &'a mut HashMap<String, TypeDef>,
}

#[allow(clippy::too_many_arguments)]
fn walk<'a>(
    node: Node<'a>,
    scope_stack: &[String],
    active_function: Option<&FunctionDef>,
    ctx: &mut SwiftWalkCtx<'a>,
) {
    let kind = node.kind();

    // ── Types (class_declaration / protocol_declaration) ──
    if let Some(base_type_kind) = type_kind_for(kind) {
        let name =
            extract_name(node, ctx.source).unwrap_or_else(|| anonymous_name("Type", node));
        let qualified = qualified_name(scope_stack, &name);
        let tid = type_id(&qualified);
        // _normalize_type_kind: protocol → interface; everything else → kind string as-is.
        let normalized_kind = if kind == "protocol_declaration" {
            "interface"
        } else {
            let raw = extract_decl_kind(node, ctx.source);
            normalize_type_kind(&raw)
        };
        // The differential test compares against the *Python* kind string.
        // Python uses _normalize_type_kind which maps `class` → `class`, but
        // for `class_declaration` it sets kind = "class" via _extract_decl_kind.
        // Override for direct `class_declaration` so it matches Python output
        // ("class", not "record").
        let final_kind = if base_type_kind == "class" && kind == "class_declaration" {
            "class"
        } else {
            normalized_kind
        };
        let (snippet, start_line, end_line) = node_snippet_swift(node, ctx.source);
        let comment = extract_comment(node, ctx.source);
        let type_def = TypeDef {
            symbol_id: tid.clone(),
            qualified_name: qualified.clone(),
            name: name.clone(),
            kind: final_kind.to_string(),
            file_path: ctx.rel_path.to_string(),
            start_line,
            end_line,
            code: snippet,
            comment: comment.clone(),
            ..Default::default()
        };
        if !ctx.type_registry.contains_key(&tid) {
            ctx.type_registry.insert(tid.clone(), type_def.clone());
            ctx.types.push(type_def);
        }
        if let Some(owner_scope) = scope_name_of(scope_stack) {
            let owner_id = if ctx.type_registry.contains_key(&type_id(&owner_scope)) {
                type_id(&owner_scope)
            } else {
                namespace_id(&owner_scope)
            };
            let owner_label = if ctx.type_registry.contains_key(&type_id(&owner_scope)) {
                "Type"
            } else {
                "Namespace"
            };
            record_relation(ctx.relations, &owner_id, owner_label, &tid, "Type", "DECLARES");
        }
        // inheritance_specifier → EXTENDS
        for inherit in find_nodes_by_type(node, "inheritance_specifier") {
            let inherit_node = inherit.child_by_field_name("inherits_from").unwrap_or(inherit);
            add_type_use(
                &tid,
                "Type",
                node_text(inherit_node, ctx.source),
                ctx.rel_path,
                ctx.types,
                ctx.relations,
                ctx.external_types,
                "EXTENDS",
            );
        }
        // type_parameters → templates + TEMPLATES edge
        for template in extract_templates(node, ctx.rel_path, ctx.source) {
            record_relation(
                ctx.relations,
                &template.symbol_id,
                "Template",
                &tid,
                "Type",
                "TEMPLATES",
            );
            ctx.templates.push(template);
        }
        let mut child_scope = scope_stack.to_vec();
        child_scope.push(name);
        for child in node.children(&mut node.walk()) {
            walk(child, &child_scope, active_function, ctx);
        }
        return;
    }

    // ── Functions (function_declaration / protocol_function_declaration /
    //    init_declaration / deinit_declaration / subscript_declaration) ──
    if FUNCTION_NODES.contains(&kind) {
        let (name, func_kind) = match kind {
            "init_declaration" => ("init".to_string(), "constructor".to_string()),
            "deinit_declaration" => ("deinit".to_string(), "destructor".to_string()),
            "subscript_declaration" => (
                "subscript".to_string(),
                if !scope_stack.is_empty() {
                    "method".to_string()
                } else {
                    "function".to_string()
                },
            ),
            _ => {
                let n = extract_name(node, ctx.source)
                    .unwrap_or_else(|| anonymous_name("Function", node));
                let k = if kind == "protocol_function_declaration" {
                    "declaration"
                } else if !scope_stack.is_empty() {
                    "method"
                } else {
                    "function"
                };
                (n, k.to_string())
            }
        };
        let arity = count_parameters(node);
        let qualified = qualified_name(scope_stack, &name);
        let func = FunctionDef {
            symbol_id: symbol_id(&qualified, arity, ctx.rel_path),
            qualified_name: qualified.clone(),
            name: name.clone(),
            kind: func_kind,
            scope_name: scope_name_of(scope_stack),
            file_path: ctx.rel_path.to_string(),
            start_byte: node.start_byte() as u32,
            end_byte: node.end_byte() as u32,
            start_line: node.start_position().row as u32 + 1,
            end_line: node.end_position().row as u32 + 1,
            arity,
            code: node_text(node, ctx.source).to_string(),
            comment: extract_comment(node, ctx.source),
            ..Default::default()
        };
        ctx.functions.push(func.clone());
        if let Some(owner_scope) = scope_name_of(scope_stack) {
            let owner_id = if ctx.type_registry.contains_key(&type_id(&owner_scope)) {
                type_id(&owner_scope)
            } else {
                namespace_id(&owner_scope)
            };
            let owner_label = if ctx.type_registry.contains_key(&type_id(&owner_scope)) {
                "Type"
            } else {
                "Namespace"
            };
            record_relation(
                ctx.relations,
                &owner_id,
                owner_label,
                &func.symbol_id,
                "Function",
                "DECLARES",
            );
        }
        // return_type → USES_TYPE
        if let Some(return_type) = node.child_by_field_name("return_type") {
            add_type_use(
                &func.symbol_id,
                "Function",
                node_text(return_type, ctx.source),
                ctx.rel_path,
                ctx.types,
                ctx.relations,
                ctx.external_types,
                "USES_TYPE",
            );
        }
        // each parameter → USES_TYPE
        for child in node.children(&mut node.walk()) {
            if child.is_named() && child.kind() == "parameter" {
                let param_type = extract_type_signature(child, ctx.source);
                if !param_type.is_empty() {
                    add_type_use(
                        &func.symbol_id,
                        "Function",
                        &param_type,
                        ctx.rel_path,
                        ctx.types,
                        ctx.relations,
                        ctx.external_types,
                        "USES_TYPE",
                    );
                }
            }
        }
        // type_parameters → templates + TEMPLATES edge
        for template in extract_templates(node, ctx.rel_path, ctx.source) {
            record_relation(
                ctx.relations,
                &template.symbol_id,
                "Template",
                &func.symbol_id,
                "Function",
                "TEMPLATES",
            );
            ctx.templates.push(template);
        }
        // Recurse WITHOUT pushing scope (Python: scope_stack stays as-is).
        for child in node.children(&mut node.walk()) {
            walk(child, scope_stack, Some(&func), ctx);
        }
        return;
    }

    // ── Type aliases (typealias_declaration / associatedtype_declaration) ──
    if ALIAS_NODES.contains(&kind) {
        let name =
            extract_name(node, ctx.source).unwrap_or_else(|| anonymous_name("Alias", node));
        let qualified = qualified_name(scope_stack, &name);
        let target = extract_alias_target(node, ctx.source);
        let alias = AliasDef {
            symbol_id: format!("alias::{}@{}", qualified, ctx.rel_path),
            qualified_name: qualified.clone(),
            name: name.clone(),
            kind: "type".to_string(),
            target_name: target.clone(),
            file_path: ctx.rel_path.to_string(),
            start_line: node.start_position().row as u32 + 1,
            end_line: node.end_position().row as u32 + 1,
            code: node_text(node, ctx.source).to_string(),
            ..Default::default()
        };
        ctx.aliases.push(alias.clone());
        if let Some(owner_scope) = scope_name_of(scope_stack) {
            let owner_id = if ctx.type_registry.contains_key(&type_id(&owner_scope)) {
                type_id(&owner_scope)
            } else {
                namespace_id(&owner_scope)
            };
            let owner_label = if ctx.type_registry.contains_key(&type_id(&owner_scope)) {
                "Type"
            } else {
                "Namespace"
            };
            record_relation(
                ctx.relations,
                &owner_id,
                owner_label,
                &alias.symbol_id,
                "Alias",
                "DECLARES",
            );
        }
        if let Some(ref target) = target {
            let target_id = type_id(target);
            record_relation(
                ctx.relations,
                &alias.symbol_id,
                "Alias",
                &target_id,
                "Type",
                "ALIASES",
            );
            add_type_use(
                &alias.symbol_id,
                "Alias",
                target,
                ctx.rel_path,
                ctx.types,
                ctx.relations,
                ctx.external_types,
                "USES_TYPE",
            );
        }
        // Fall through — aliases don't return so we descend into children.
    }

    // ── Calls (only inside an active function) ──
    if let Some(active) = active_function {
        if CALL_NODES.contains(&kind) {
            let callee_name = call_name(node, ctx.source);
            let (branch_kind_val, loop_depth, control_frames) = control_context(node);
            let call_type = if kind == "macro_invocation" {
                "macro"
            } else if kind == "constructor_expression" {
                "constructor"
            } else {
                "function"
            };
            ctx.calls.push(CallEdge {
                caller_id: active.symbol_id.clone(),
                caller_file: ctx.rel_path.to_string(),
                caller_scope: active.scope_name.clone(),
                call_line: node.start_position().row as u32 + 1,
                call_column: node.start_position().column as u32 + 1,
                call_start_byte: node.start_byte() as u32,
                call_branch_kind: branch_kind_val,
                call_loop_depth: loop_depth,
                call_control_frames_json: control_frames,
                call_type: call_type.to_string(),
                call_arity: count_arguments(node, ctx.source),
                callee_name,
                callee_id: None,
            });
        }
    }

    // ── Properties / fields (only when not in a function, and we have scope) ──
    if active_function.is_none() && !scope_stack.is_empty() && PROPERTY_NODES.contains(&kind) {
        if let Some(name) = extract_name(node, ctx.source) {
            let owner = scope_name_of(scope_stack).unwrap_or_default();
            let qualified = if owner.is_empty() {
                name.clone()
            } else {
                format!("{}.{}", owner, name)
            };
            let type_signature = extract_type_signature(node, ctx.source);
            let field_id = format!("field::{}@{}", qualified, ctx.rel_path);
            ctx.fields.push(FieldDef {
                symbol_id: field_id.clone(),
                qualified_name: qualified,
                name: name.clone(),
                scope_name: Some(owner.clone()),
                type_signature: type_signature.clone(),
                file_path: ctx.rel_path.to_string(),
                start_line: node.start_position().row as u32 + 1,
                end_line: node.end_position().row as u32 + 1,
                code: node_text(node, ctx.source).to_string(),
            });
            if !owner.is_empty() {
                let owner_type_id = type_id(&owner);
                record_relation(
                    ctx.relations,
                    &owner_type_id,
                    "Type",
                    &field_id,
                    "Field",
                    "DECLARES",
                );
            }
            if !type_signature.is_empty() {
                add_type_use(
                    &field_id,
                    "Field",
                    &type_signature,
                    ctx.rel_path,
                    ctx.types,
                    ctx.relations,
                    ctx.external_types,
                    "USES_TYPE",
                );
            }
        }
    }

    // ── Default: descend into children with same state ──
    for child in node.children(&mut node.walk()) {
        walk(child, scope_stack, active_function, ctx);
    }
}

// ── Call resolution (mirrors Python `_resolve_calls`) ───────────────────

fn resolve_calls(
    functions: &[FunctionDef],
    calls: &mut [CallEdge],
    relations: &mut Vec<RelationEdge>,
) {
    let mut by_name: HashMap<String, Vec<&FunctionDef>> = HashMap::new();
    let mut by_name_arity: HashMap<(String, u32), Vec<&FunctionDef>> = HashMap::new();
    for func in functions {
        by_name.entry(func.name.clone()).or_default().push(func);
        by_name_arity
            .entry((func.name.clone(), func.arity))
            .or_default()
            .push(func);
    }

    for call in calls.iter_mut() {
        let key = (call.callee_name.clone(), call.call_arity);
        let mut candidates: Vec<&FunctionDef> = by_name_arity
            .get(&key)
            .cloned()
            .or_else(|| by_name.get(&call.callee_name).cloned())
            .unwrap_or_default();
        if candidates.is_empty() {
            continue;
        }
        if candidates.len() > 1 {
            if let Some(ref scope) = call.caller_scope {
                let scoped: Vec<&FunctionDef> = candidates
                    .iter()
                    .copied()
                    .filter(|f| f.scope_name.as_deref() == Some(scope.as_str()))
                    .collect();
                if !scoped.is_empty() {
                    candidates = scoped;
                }
            }
        }
        if candidates.len() == 1 {
            let callee_id = candidates[0].symbol_id.clone();
            call.callee_id = Some(callee_id.clone());
            // POSSIBLE_CALLS edge WITH rich properties (Python carries line/column/call_type/arity).
            let mut properties = HashMap::new();
            properties.insert("line".to_string(), call.call_line.to_string());
            properties.insert("column".to_string(), call.call_column.to_string());
            properties.insert("call_type".to_string(), call.call_type.clone());
            properties.insert("arity".to_string(), call.call_arity.to_string());
            relations.push(RelationEdge {
                source_id: call.caller_id.clone(),
                source_label: "Function".to_string(),
                target_id: callee_id,
                target_label: "Function".to_string(),
                rel_type: "POSSIBLE_CALLS".to_string(),
                properties,
            });
        }
    }
}

// ── Import / macro / includes collection ────────────────────────────────

/// Collect `import` declarations — returns `(namespaces, imports)`.
/// Mirrors Python `_collect_imports` — `using_namespaces` is sorted-unique
/// top-segment of each import path.
fn collect_imports<'a>(root: Node<'a>, source: &[u8]) -> (Vec<String>, Vec<String>) {
    let mut using_imports: Vec<String> = Vec::new();
    let mut using_namespaces: Vec<String> = Vec::new();
    for node in find_nodes_by_type(root, "import_declaration") {
        let path = extract_import_path(node, source);
        if path.is_empty() {
            continue;
        }
        using_imports.push(path.clone());
        if let Some(first) = path.split('.').next() {
            if !first.is_empty() && !using_namespaces.contains(&first.to_string()) {
                using_namespaces.push(first.to_string());
            }
        }
    }
    using_namespaces.sort();
    using_namespaces.dedup();
    (using_namespaces, using_imports)
}

fn collect_macros<'a>(root: Node<'a>, source: &[u8]) -> Vec<String> {
    let mut macros: Vec<String> = Vec::new();
    let node_types = ["macro_declaration", "macro_invocation"];
    for nt in node_types {
        for node in find_nodes_by_type(root, nt) {
            let name = extract_name(node, source).unwrap_or_else(|| call_name(node, source));
            if !macros.contains(&name) {
                macros.push(name);
            }
        }
    }
    macros
}

// ── Error scanning helpers ──────────────────────────────────────────────

fn check_has_error<'a>(root: Node<'a>) -> bool {
    let mut stack = vec![root];
    while let Some(n) = stack.pop() {
        if n.is_error() || n.is_missing() {
            return true;
        }
        for c in n.children(&mut n.walk()) {
            stack.push(c);
        }
    }
    false
}

fn count_error_nodes<'a>(root: Node<'a>) -> u32 {
    let mut count = 0u32;
    let mut stack = vec![root];
    while let Some(n) = stack.pop() {
        if n.is_error() || n.is_missing() {
            count += 1;
        }
        for c in n.children(&mut n.walk()) {
            stack.push(c);
        }
    }
    count
}

// ── Public entry point ──────────────────────────────────────────────────

/// Parse Swift source bytes and run the full extraction pipeline.
///
/// This is the Swift equivalent of Python `parse_swift_file(path, root)` minus
/// file I/O — callers pass the already-read `source` and the `rel_path` that
/// should appear in `file_def.file_path`.
pub fn parse_swift_source(source: &[u8], rel_path: &str) -> Option<SwiftParseOutput> {
    let mut parser = Parser::new();
    parser.set_language(&SwiftGrammar.language()).ok()?;
    let tree = parser.parse(source, None)?;
    let root = tree.root_node();

    let (using_namespaces, using_imports) = collect_imports(root, source);
    let includes = using_imports.clone();
    let macros = collect_macros(root, source);
    let code = String::from_utf8_lossy(source).into_owned();
    let file_comment = extract_file_comment(root, source);
    let has_error = check_has_error(root);
    let error_nodes = count_error_nodes(root);

    let mut functions: Vec<FunctionDef> = Vec::new();
    let mut calls: Vec<CallEdge> = Vec::new();
    let mut types: Vec<TypeDef> = Vec::new();
    let mut namespaces: Vec<NamespaceDef> = Vec::new();
    let mut relations: Vec<RelationEdge> = Vec::new();
    let mut fields: Vec<FieldDef> = Vec::new();
    let mut aliases: Vec<AliasDef> = Vec::new();
    let mut templates: Vec<TemplateDef> = Vec::new();
    let mut type_registry: HashMap<String, TypeDef> = HashMap::new();
    let mut external_types: HashMap<String, TypeDef> = HashMap::new();

    let file_lines = source.iter().filter(|&&b| b == b'\n').count() as u32 + 1;

    {
        let mut ctx = SwiftWalkCtx {
            source,
            rel_path,
            functions: &mut functions,
            calls: &mut calls,
            types: &mut types,
            namespaces: &mut namespaces,
            relations: &mut relations,
            fields: &mut fields,
            aliases: &mut aliases,
            templates: &mut templates,
            type_registry: &mut type_registry,
            external_types: &mut external_types,
        };
        for child in root.children(&mut root.walk()) {
            walk(child, &[], None, &mut ctx);
        }
    }

    resolve_calls(&functions, &mut calls, &mut relations);

    let file_def = FileDef {
        file_path: rel_path.to_string(),
        start_line: 1,
        end_line: file_lines,
        code,
        comment: file_comment.clone(),
        summary: file_comment,
        note: String::new(),
    };

    Some(SwiftParseOutput {
        file_def,
        functions,
        calls,
        types,
        namespaces,
        relations,
        function_types: Vec::new(),
        fields,
        aliases,
        templates,
        using_namespaces,
        using_imports,
        includes,
        macros,
        parse_meta: ParseMeta {
            parser_language: "swift_tree_sitter".to_string(),
            parser_language_initial: "swift".to_string(),
            has_error,
            error_nodes,
            header_retry_attempted: false,
            header_retry_selected: false,
            error_nodes_initial: error_nodes,
            header_retry_error_nodes: Some(0),
            header_retry_has_error: Some(false),
        },
    })
}

// ── Tests ───────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    const SIMPLE_SWIFT: &[u8] = b"\
import Foundation

class Greeter {
    var name: String

    init(name: String) {
        self.name = name
    }

    func hello(who: String) -> String {
        return formatGreeting(self.name, who)
    }
}

func formatGreeting(_ name: String, _ who: String) -> String {
    return \"Hello, \\(name) -> \\(who)\"
}
";

    #[test]
    fn parse_extracts_imports() {
        let out = parse_swift_source(SIMPLE_SWIFT, "main.swift").unwrap();
        assert!(
            out.using_imports.contains(&"Foundation".to_string()),
            "using_imports = {:?}",
            out.using_imports
        );
        assert!(
            out.using_namespaces.contains(&"Foundation".to_string()),
            "using_namespaces = {:?}",
            out.using_namespaces
        );
    }

    #[test]
    fn parse_extracts_class_and_methods_and_function() {
        let out = parse_swift_source(SIMPLE_SWIFT, "main.swift").unwrap();
        let has_greeter = out
            .types
            .iter()
            .any(|t| t.name == "Greeter" && t.kind == "class");
        assert!(
            has_greeter,
            "Greeter class missing; types = {:?}",
            out.types.iter().map(|t| (&t.name, &t.kind)).collect::<Vec<_>>()
        );
        let has_init = out
            .functions
            .iter()
            .any(|f| f.name == "init" && f.kind == "constructor");
        assert!(has_init, "init constructor missing");
        let has_hello = out
            .functions
            .iter()
            .any(|f| f.name == "hello" && f.kind == "method");
        assert!(has_hello, "hello method missing");
        let has_format = out
            .functions
            .iter()
            .any(|f| f.name == "formatGreeting" && f.kind == "function");
        assert!(has_format, "formatGreeting function missing");
    }

    #[test]
    fn parse_extracts_property() {
        let out = parse_swift_source(SIMPLE_SWIFT, "main.swift").unwrap();
        let has_name = out
            .fields
            .iter()
            .any(|f| f.name == "name" && f.scope_name.as_deref() == Some("Greeter"));
        assert!(
            has_name,
            "name property missing; fields = {:?}",
            out.fields
        );
    }

    #[test]
    fn parse_extracts_calls_and_resolves() {
        let out = parse_swift_source(SIMPLE_SWIFT, "main.swift").unwrap();
        let has_format_call = out.calls.iter().any(|c| c.callee_name == "formatGreeting");
        assert!(
            has_format_call,
            "formatGreeting call missing; calls = {:?}",
            out.calls.iter().map(|c| (&c.callee_name, &c.caller_id)).collect::<Vec<_>>()
        );
        // formatGreeting has unique name+arity → should resolve
        let resolved = out
            .calls
            .iter()
            .find(|c| c.callee_name == "formatGreeting")
            .unwrap();
        assert!(
            resolved.callee_id.is_some(),
            "formatGreeting call not resolved"
        );
    }

    #[test]
    fn parse_meta_is_swift_language() {
        let out = parse_swift_source(SIMPLE_SWIFT, "main.swift").unwrap();
        assert_eq!(out.parse_meta.parser_language, "swift_tree_sitter");
        assert_eq!(out.parse_meta.parser_language_initial, "swift");
    }

    #[test]
    fn parse_extracts_protocol() {
        let src = b"\
protocol Drawable {
    func draw()
}

class Square: Drawable {
    func draw() {
        render()
    }
}
";
        let out = parse_swift_source(src, "proto.swift").unwrap();
        let has_protocol = out
            .types
            .iter()
            .any(|t| t.name == "Drawable" && t.kind == "interface");
        assert!(has_protocol, "Drawable protocol missing");
        let has_square = out
            .types
            .iter()
            .any(|t| t.name == "Square" && t.kind == "class");
        assert!(has_square, "Square class missing");
        // Square → EXTENDS Drawable
        let has_extends = out.relations.iter().any(|r| {
            r.source_id.ends_with("Square") && r.target_id == "Drawable" && r.rel_type == "EXTENDS"
        });
        assert!(has_extends, "Square→Drawable EXTENDS missing");
        // protocol_function_declaration should be kind "declaration"
        let has_draw_decl = out
            .functions
            .iter()
            .any(|f| f.name == "draw" && f.kind == "declaration");
        assert!(has_draw_decl, "draw declaration missing");
    }

    #[test]
    fn parse_extracts_typealias() {
        let src = b"typealias MyInt = Int\n";
        let out = parse_swift_source(src, "alias.swift").unwrap();
        let has_alias = out.aliases.iter().any(|a| a.name == "MyInt");
        assert!(
            has_alias,
            "MyInt alias missing; aliases = {:?}",
            out.aliases
        );
    }

    #[test]
    fn parse_extracts_subscript_and_deinit() {
        let src = b"\
class Container {
    var items: [Int] = []

    subscript(idx: Int) -> Int {
        return items[idx]
    }

    deinit {
        cleanup()
    }
}
";
        let out = parse_swift_source(src, "container.swift").unwrap();
        let has_sub = out
            .functions
            .iter()
            .any(|f| f.name == "subscript" && f.kind == "method");
        assert!(has_sub, "subscript method missing");
        let has_deinit = out
            .functions
            .iter()
            .any(|f| f.name == "deinit" && f.kind == "destructor");
        assert!(has_deinit, "deinit destructor missing");
    }

    #[test]
    fn parse_extracts_guard_branch_kind() {
        let src = b"\
class Safe {
    func unwrap(_ x: Int?) -> Int {
        guard let v = x else {
            log(\"missing\")
            return 0
        }
        log(\"ok\")
        return v + 1
    }
    func log(_ s: String) {}
}
";
        let out = parse_swift_source(src, "guard.swift").unwrap();
        // The walker should emit at least the `log(...)` calls inside
        // `guard`'s else branch and after it.
        assert!(!out.calls.is_empty(), "no calls emitted");
        // The first `log` call lives under guard's else block — branch_kind
        // should be "guard" (innermost branch wins).
        let first_log = out
            .calls
            .iter()
            .find(|c| c.callee_name == "log")
            .expect("expected log call");
        assert_eq!(
            first_log.call_branch_kind, "guard",
            "guard branch_kind missing on first log call"
        );
    }
}