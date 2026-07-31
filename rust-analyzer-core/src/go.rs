//! Go tree-sitter walker — Phase 2 (Tier 1) port of `go_analyzer.py`.
//!
//! Faithful Rust port of the Python `parse_go_file` → `_walk_tree` pipeline.
//! Go shares the C++ payload *keys* (`functions`, `calls`, `types`, `namespaces`,
//! `relations`, `fields`, `aliases`, `templates`, `file_def`, `parse_meta`)
//! but the scalar fields differ in type:
//!
//! | field           | C++ (`ParseOutput`)   | Go (this module)          |
//! |-----------------|-----------------------|---------------------------|
//! | `using_imports` | `HashMap<String,Str>` | `Vec<String>` (list)      |
//! | `includes`      | `Vec<String>`         | `Vec<String>`             |
//! | `macros`        | `HashMap<String,Str>` | `Vec<String>` (always []) |
//!
//! That's why Go has its own `GoParseOutput` and `go::build_go_payload` rather
//! than reusing the C++ `build_payload`. The extraction logic (functions,
//! methods, structs, interfaces, calls, relations) is Go-specific and lives
//! here in full.

use std::collections::HashMap;

use tree_sitter::{Node, Parser};

use crate::grammar::{GoGrammar, Grammar};
use crate::symbols::{
    AliasDef, CallEdge, FieldDef, FileDef, FunctionDef, NamespaceDef, ParseMeta, RelationEdge,
    TemplateDef, TypeDef,
};
use crate::text::{extract_file_comment, node_text, node_snippet};

// ── Node-type sets (mirror the Python module constants) ─────────────────

const COMMENT_TYPES: &[&str] = &["comment"];
const FUNCTION_NODES: &[&str] = &["function_declaration", "method_declaration"];
const TYPE_NODES: &[&str] = &["type_spec", "type_alias"];
const CALL_NODES: &[&str] = &["call_expression"];

fn branch_kind_of(node_kind: &str) -> Option<&'static str> {
    match node_kind {
        "if_statement" => Some("if"),
        "switch_statement" => Some("switch"),
        "type_switch_statement" => Some("type_switch"),
        "select_statement" => Some("select"),
        "case_clause" => Some("case"),
        "communication_case" => Some("case"),
        "default_case" => Some("default"),
        _ => None,
    }
}

fn is_loop_node(node_kind: &str) -> bool {
    matches!(node_kind, "for_statement" | "range_clause")
}

// ── Go-specific ParseOutput (list-typed using_imports/macros) ───────────

/// Go payload — mirrors the dict shape returned by Python `parse_go_file`.
///
/// Differs from `crate::symbols::ParseOutput` (C++) in:
/// - `using_imports`: `Vec<String>` (Go) vs `HashMap<String,String>` (C++)
/// - `macros`: always empty `Vec<String>` (Go has no preprocessor macros)
#[derive(Debug, Default)]
pub struct GoParseOutput {
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
    pub parse_meta: ParseMeta,
}

// ── Helpers (ports of the Python `_foo` helpers) ────────────────────────

#[inline]
fn line_from_byte(source: &[u8], byte_index: usize) -> u32 {
    source[..byte_index].iter().filter(|&&b| b == b'\n').count() as u32 + 1
}

fn extract_name<'a>(node: Node<'a>, source: &[u8]) -> Option<String> {
    if let Some(name_node) = node.child_by_field_name("name") {
        return Some(node_text(name_node, source).trim().to_string());
    }
    for node_type in [
        "identifier",
        "field_identifier",
        "type_identifier",
        "package_identifier",
    ] {
        if let Some(found) = find_first_descendant(node, &[node_type]) {
            return Some(node_text(found, source).trim().to_string());
        }
    }
    None
}

fn find_first_descendant<'a>(node: Node<'a>, types: &[&str]) -> Option<Node<'a>> {
    let allowed: std::collections::HashSet<&str> = types.iter().copied().collect();
    let mut stack: Vec<Node<'a>> = node.children(&mut node.walk()).collect();
    stack.reverse();
    while let Some(current) = stack.pop() {
        if allowed.contains(current.kind()) {
            return Some(current);
        }
        let mut children = current.children(&mut current.walk()).collect::<Vec<_>>();
        children.reverse();
        stack.extend(children);
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
        let mut children = current.children(&mut current.walk()).collect::<Vec<_>>();
        children.reverse();
        stack.extend(children);
    }
    found
}

fn field_text<'a>(node: Node<'a>, field_name: &str, source: &'a [u8]) -> String {
    node.child_by_field_name(field_name)
        .map(|c| node_text(c, source).trim().to_string())
        .unwrap_or_default()
}

fn first_named_child<'a>(node: Node<'a>, types: Option<&[&str]>) -> Option<Node<'a>> {
    let allowed: Option<std::collections::HashSet<&str>> =
        types.map(|t| t.iter().copied().collect());
    for child in node.children(&mut node.walk()) {
        if !child.is_named() {
            continue;
        }
        if let Some(a) = &allowed {
            if !a.contains(child.kind()) {
                continue;
            }
        }
        return Some(child);
    }
    None
}

fn qualified_name(scope_stack: &[String], name: &str) -> String {
    let mut parts: Vec<&str> = scope_stack.iter().map(|s| s.as_str()).collect();
    parts.push(name);
    parts.join(".")
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
    format!("Anonymous{}@{}:{}", prefix, pos.row + 1, pos.column + 1)
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

fn count_parameters<'a>(node: Node<'a>, source: &[u8]) -> u32 {
    let params = node
        .child_by_field_name("parameters")
        .or_else(|| first_named_child(node, Some(&["parameter_list"])));
    let Some(params) = params else {
        return 0;
    };
    let mut count = 0u32;
    for child in params.children(&mut params.walk()) {
        if child.kind() != "parameter_declaration" {
            continue;
        }
        let names: Vec<Node> = child
            .children(&mut child.walk())
            .filter(|c| c.kind() == "identifier" || c.kind() == "field_identifier")
            .collect();
        count += if !names.is_empty() {
            names.len() as u32
        } else {
            1
        };
    }
    count
}

fn count_arguments<'a>(node: Node<'a>) -> u32 {
    let args = node
        .child_by_field_name("arguments")
        .or_else(|| first_named_child(node, Some(&["argument_list"])));
    let Some(args) = args else {
        return 0;
    };
    args.children(&mut args.walk())
        .filter(|c| c.is_named() && !COMMENT_TYPES.contains(&c.kind()))
        .count() as u32
}

fn receiver_scope<'a>(node: Node<'a>, source: &[u8]) -> Option<String> {
    let receiver = node.child_by_field_name("receiver")?;
    if let Some(type_node) = find_first_descendant(receiver, &["type_identifier"]) {
        return Some(node_text(type_node, source).trim().to_string());
    }
    let text = node_text(receiver, source);
    let trimmed = text.trim();
    // Match `*Name)` or `Name)` at end
    let re = regex::Regex::new(r"\*?\s*([A-Za-z_]\w*)\s*\)?\s*$").ok()?;
    re.captures(trimmed).map(|c| c.get(1).map(|m| m.as_str().to_string()))?
}

fn type_kind<'a>(type_spec: Node<'a>, source: &[u8]) -> &'static str {
    let type_node = type_node(type_spec);
    match type_node.map(|n| n.kind()) {
        Some("struct_type") => "struct",
        Some("interface_type") => "interface",
        _ => "type",
    }
}

fn type_node<'a>(type_spec: Node<'a>) -> Option<Node<'a>> {
    let by_field = type_spec.child_by_field_name("type");
    if by_field.is_some() {
        return by_field;
    }
    first_named_child(type_spec, Some(&["struct_type", "interface_type"]))
}

fn is_type_alias<'a>(type_spec: Node<'a>, source: &[u8]) -> bool {
    if type_spec.kind() == "type_alias" {
        return true;
    }
    let text = node_text(type_spec, source);
    let re = regex::Regex::new(r"^\s*[A-Za-z_]\w*(?:\s*\[[^\]]+\])?\s*=").unwrap();
    re.is_match(text)
}

fn extract_alias_target<'a>(type_spec: Node<'a>, source: &[u8]) -> Option<String> {
    let text = node_text(type_spec, source);
    let re = regex::Regex::new(r"^\s*[A-Za-z_]\w*(?:\s*\[[^\]]+\])?\s*=\s*(.*?)\s*$").unwrap();
    let caps = re.captures(text)?;
    let target = caps.get(1)?.as_str();
    Some(regex::Regex::new(r"\s+").unwrap().replace_all(target, " ").trim().to_string())
}

fn extract_type_signature<'a>(node: Node<'a>, source: &[u8]) -> String {
    if let Some(type_node) = node.child_by_field_name("type") {
        return node_text(type_node, source).trim().to_string();
    }
    let text = node_text(node, source).trim().trim_end_matches(',').to_string();
    let parts: Vec<&str> = text.split_whitespace().collect();
    if parts.len() > 1 {
        parts.last().unwrap().to_string()
    } else {
        String::new()
    }
}

fn field_names<'a>(node: Node<'a>, source: &[u8]) -> Vec<String> {
    node.children(&mut node.walk())
        .filter(|c| c.kind() == "field_identifier" || c.kind() == "identifier")
        .map(|c| node_text(c, source).trim().to_string())
        .filter(|s| !s.is_empty())
        .collect()
}

fn field_type_signature<'a>(node: Node<'a>, source: &[u8]) -> String {
    if let Some(type_node) = node.child_by_field_name("type") {
        return node_text(type_node, source).trim().to_string();
    }
    let text = node_text(node, source).trim().trim_end_matches(',').to_string();
    let names = field_names(node, source);
    if !names.is_empty() {
        let prefix = names.join(", ");
        return text[prefix.len()..].trim().to_string();
    }
    text
}

fn call_name<'a>(call_node: Node<'a>, source: &[u8]) -> String {
    let function_node = match call_node.child_by_field_name("function") {
        Some(f) => f,
        None => {
            return node_text(call_node, source)
                .split('(')
                .next()
                .unwrap_or("")
                .trim()
                .to_string();
        }
    };
    if function_node.kind() == "selector_expression" {
        if let Some(field) = function_node.child_by_field_name("field") {
            return node_text(field, source).trim().to_string();
        }
    }
    let text = node_text(function_node, source).trim().to_string();
    if let Some(idx) = text.rfind('.') {
        text[idx + 1..].to_string()
    } else {
        text
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
            frame.insert("kind".to_string(), serde_json::Value::String(kind.to_string()));
            frame.insert("line".to_string(), serde_json::Value::Number(serde_json::Number::from(p.start_position().row + 1)));
            frames.push(frame);
        } else if is_loop_node(p.kind()) {
            loop_depth += 1;
            let mut frame = HashMap::new();
            frame.insert("kind".to_string(), serde_json::Value::String("loop".to_string()));
            frame.insert("line".to_string(), serde_json::Value::Number(serde_json::Number::from(p.start_position().row + 1)));
            frames.push(frame);
        }
        parent = p.parent();
    }
    frames.reverse();
    let frames_json: Vec<serde_json::Value> = frames
        .into_iter()
        .map(|f| serde_json::Value::Object(f.into_iter().collect()))
        .collect();
    // Match Python json.dumps default formatting (spaces after : and ,)
    let compact = serde_json::Value::Array(frames_json).to_string();
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

fn add_type_use(
    owner_id: &str,
    owner_label: &str,
    type_text: &str,
    rel_path: &str,
    types: &mut Vec<TypeDef>,
    relations: &mut Vec<RelationEdge>,
    external_types: &mut HashMap<String, TypeDef>,
) {
    // Normalize: split on whitespace or dot, keep capitalized candidates
    let cleaned = regex::Regex::new(r"[&*\[\](),{};]").unwrap().replace_all(type_text, " ");
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

    let is_pointer = type_text.contains('*');
    let rel_type = if is_pointer { "POINTER_TO" } else { "USES_TYPE" };

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
    for template_node in find_nodes_by_type(node, "type_parameter_list") {
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

/// Mutable state threaded through the Go walk.
struct GoWalkCtx<'a> {
    source: &'a [u8],
    rel_path: &'a str,
    package_scope: String,
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
    ctx: &mut GoWalkCtx<'a>,
) {
    // ── Types (type_spec / type_alias) ──
    if TYPE_NODES.contains(&node.kind()) {
        let name = extract_name(node, ctx.source).unwrap_or_else(|| anonymous_name("Type", node));
        let qualified = qualified_name(&[ctx.package_scope.clone()].into_iter().chain(scope_stack.iter().cloned()).collect::<Vec<_>>(), &name);
        let (snippet, start_line, end_line) = node_snippet(node, ctx.source);

        if is_type_alias(node, ctx.source) {
            let alias = AliasDef {
                symbol_id: format!("alias::{}@{}", qualified, ctx.rel_path),
                qualified_name: qualified.clone(),
                name: name.clone(),
                kind: "type".to_string(),
                target_name: extract_alias_target(node, ctx.source),
                file_path: ctx.rel_path.to_string(),
                start_line,
                end_line,
                code: snippet,
                ..Default::default()
            };
            if let Some(target) = &alias.target_name {
                let target_id_str = type_id(target);
                record_relation(ctx.relations, &alias.symbol_id, "Alias", &target_id_str, "Type", "ALIASES");
            }
            ctx.aliases.push(alias);
        } else {
            let tid = type_id(&qualified);
            if !ctx.type_registry.contains_key(&tid) {
                let type_def = TypeDef {
                    symbol_id: tid.clone(),
                    qualified_name: qualified.clone(),
                    name: name.clone(),
                    kind: type_kind(node, ctx.source).to_string(),
                    file_path: ctx.rel_path.to_string(),
                    start_line,
                    end_line,
                    code: snippet.clone(),
                    comment: extract_comment(node, ctx.source),
                    ..Default::default()
                };
                ctx.type_registry.insert(tid.clone(), type_def.clone());
                ctx.types.push(type_def);
            }
            let ns_id = namespace_id(&ctx.package_scope);
            record_relation(ctx.relations, &ns_id, "Namespace", &tid, "Type", "DECLARES");
            for template in extract_templates(node, ctx.rel_path, ctx.source) {
                record_relation(ctx.relations, &template.symbol_id, "Template", &tid, "Type", "TEMPLATES");
                ctx.templates.push(template);
            }
            let mut child_scope = scope_stack.to_vec();
            child_scope.push(name.clone());
            for child in node.children(&mut node.walk()) {
                walk(child, &child_scope, active_function, ctx);
            }
        }
        return;
    }

    // ── Functions / methods ──
    if FUNCTION_NODES.contains(&node.kind()) {
        let name = extract_name(node, ctx.source).unwrap_or_else(|| anonymous_name("Function", node));
        let receiver = if node.kind() == "method_declaration" {
            receiver_scope(node, ctx.source)
        } else {
            None
        };
        let mut function_scope: Vec<String> = vec![ctx.package_scope.clone()];
        if let Some(r) = &receiver {
            function_scope.push(r.clone());
        }
        let qualified = qualified_name(&function_scope, &name);
        let arity = count_parameters(node, ctx.source);
        let kind = if receiver.is_some() { "method" } else { "function" };
        let scope_name = if receiver.is_some() {
            function_scope.join(".")
        } else {
            ctx.package_scope.clone()
        };
        let func = FunctionDef {
            symbol_id: symbol_id(&qualified, arity, ctx.rel_path),
            qualified_name: qualified.clone(),
            name: name.clone(),
            kind: kind.to_string(),
            scope_name: Some(scope_name),
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
        if let Some(r) = &receiver {
            let owner_qualified = qualified_name(&[ctx.package_scope.clone()], r);
            let owner_id = type_id(&owner_qualified);
            record_relation(ctx.relations, &owner_id, "Type", &func.symbol_id, "Function", "DECLARES");
        } else {
            let ns_id = namespace_id(&ctx.package_scope);
            record_relation(ctx.relations, &ns_id, "Namespace", &func.symbol_id, "Function", "DECLARES");
        }
        for template in extract_templates(node, ctx.rel_path, ctx.source) {
            record_relation(ctx.relations, &template.symbol_id, "Template", &func.symbol_id, "Function", "TEMPLATES");
            ctx.templates.push(template);
        }
        for child in node.children(&mut node.walk()) {
            walk(child, scope_stack, Some(&func), ctx);
        }
        return;
    }

    // ── Fields (inside a type scope) ──
    if !scope_stack.is_empty() && node.kind() == "field_declaration" {
        let owner = qualified_name(&[ctx.package_scope.clone()], &scope_stack[scope_stack.len() - 1]);
        let type_signature = field_type_signature(node, ctx.source);
        for name in field_names(node, ctx.source) {
            let qualified = format!("{}.{}", owner, name);
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
            let owner_type_id = type_id(&owner);
            record_relation(ctx.relations, &owner_type_id, "Type", &field_id, "Field", "DECLARES");
            if !type_signature.is_empty() {
                add_type_use(
                    &field_id,
                    "Field",
                    &type_signature,
                    ctx.rel_path,
                    ctx.types,
                    ctx.relations,
                    ctx.external_types,
                );
            }
        }
    }

    // ── Calls (only inside an active function) ──
    if let Some(active) = active_function {
        if CALL_NODES.contains(&node.kind()) {
            let callee_name = call_name(node, ctx.source);
            let (branch_kind, loop_depth, control_frames) = control_context(node);
            let function_node_text = node
                .child_by_field_name("function")
                .map(|f| node_text(f, ctx.source).to_string())
                .unwrap_or_default();
            let call_type = if function_node_text.contains('.') {
                "method"
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
                call_branch_kind: branch_kind,
                call_loop_depth: loop_depth,
                call_control_frames_json: control_frames,
                call_type: call_type.to_string(),
                call_arity: count_arguments(node),
                callee_name,
                callee_id: None,
            });
        }
    }

    // ── Default: descend into children with same state ──
    for child in node.children(&mut node.walk()) {
        walk(child, scope_stack, active_function, ctx);
    }
}

// ── Call resolution (mirrors Python `_resolve_calls`) ───────────────────

fn resolve_calls(functions: &[FunctionDef], calls: &mut [CallEdge], relations: &mut Vec<RelationEdge>) {
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
            .map(|v| v.clone())
            .or_else(|| by_name.get(&call.callee_name).map(|v| v.clone()))
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
            record_relation(
                relations,
                &call.caller_id,
                "Function",
                &callee_id,
                "Function",
                "POSSIBLE_CALLS",
            );
        }
    }
}

// ── Package name + import collection ────────────────────────────────────

fn package_name<'a>(root: Node<'a>, source: &[u8]) -> String {
    for child in root.children(&mut root.walk()) {
        if child.kind() == "package_clause" {
            return extract_name(child, source).unwrap_or_else(|| "main".to_string());
        }
    }
    "main".to_string()
}

/// Collect imports — returns `(namespaces, includes)` mirroring Python `_collect_imports`.
fn collect_imports<'a>(root: Node<'a>, source: &[u8]) -> (Vec<String>, Vec<String>) {
    let mut imports: Vec<String> = Vec::new();
    let mut namespaces: Vec<String> = Vec::new();
    for node in find_nodes_by_type(root, "import_spec") {
        let path_node = node
            .child_by_field_name("path")
            .or_else(|| find_first_descendant(node, &["interpreted_string_literal", "raw_string_literal"]));
        let Some(path_node) = path_node else {
            continue;
        };
        let path = node_text(path_node, source)
            .trim()
            .trim_matches(|c| c == '`' || c == '"')
            .to_string();
        if path.is_empty() {
            continue;
        }
        imports.push(path.clone());
        if let Some(alias_node) = node.child_by_field_name("name") {
            let alias = node_text(alias_node, source).trim().to_string();
            if alias != "_" && alias != "." {
                namespaces.push(alias);
            }
        } else {
            namespaces.push(path.rsplit('/').next().unwrap_or(&path).to_string());
        }
    }
    namespaces.sort();
    namespaces.dedup();
    imports.sort();
    imports.dedup();
    (namespaces, imports)
}

// ── Public entry point ──────────────────────────────────────────────────

/// Parse Go source bytes and run the full extraction pipeline.
///
/// This is the Rust equivalent of Python `parse_go_file(path, root)` minus
/// file I/O — callers pass the already-read `source` and the `rel_path` that
/// should appear in `file_def.file_path`.
pub fn parse_go_source(source: &[u8], rel_path: &str) -> Option<GoParseOutput> {
    let mut parser = Parser::new();
    parser.set_language(&GoGrammar.language()).ok()?;
    let tree = parser.parse(source, None)?;
    let root = tree.root_node();

    let package_scope = package_name(root, source);
    let (using_namespaces, includes) = collect_imports(root, source);
    let code = String::from_utf8_lossy(source).into_owned();
    let file_comment = extract_file_comment(root, source);
    let has_error = check_has_error(root);

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

    // The file namespace (package).
    namespaces.push(NamespaceDef {
        symbol_id: namespace_id(&package_scope),
        qualified_name: package_scope.clone(),
        name: package_scope.clone(),
        file_path: rel_path.to_string(),
        start_line: 1,
        end_line: 1,
        code: format!("package {}", package_scope),
        comment: String::new(),
        ..Default::default()
    });

    let file_lines = source.iter().filter(|&&b| b == b'\n').count() as u32 + 1;

    {
        let mut ctx = GoWalkCtx {
            source,
            rel_path,
            package_scope: package_scope.clone(),
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

    // INCLUDES relations for each import.
    for include in &includes {
        record_relation(
            &mut relations,
            rel_path,
            "File",
            include,
            "ExternalModule",
            "INCLUDES",
        );
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

    Some(GoParseOutput {
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
        using_imports: includes.clone(),
        includes,
        parse_meta: ParseMeta {
            parser_language: "go_tree_sitter".to_string(),
            parser_language_initial: "go".to_string(),
            has_error,
            error_nodes: count_error_nodes(root),
            header_retry_attempted: false,
            header_retry_selected: false,
            error_nodes_initial: count_error_nodes(root),
            header_retry_error_nodes: Some(0),
            header_retry_has_error: Some(false),
        },
    })
}

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

// ── Tests ───────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    const SIMPLE_GO: &[u8] = b"\
package main

import \"fmt\"

type Greeter struct {
\tName string
}

func (g Greeter) Hello(name string) string {
\treturn fmt.Sprintf(\"Hello, %s\", name)
}

func main() {
\tg := Greeter{Name: \"world\"}
\tg.Hello(g.Name)
}
";

    #[test]
    fn parse_extracts_package_and_imports() {
        let out = parse_go_source(SIMPLE_GO, "main.go").unwrap();
        assert_eq!(out.file_def.file_path, "main.go");
        assert_eq!(out.namespaces.len(), 1);
        assert_eq!(out.namespaces[0].name, "main");
        assert!(out.includes.contains(&"fmt".to_string()));
        assert!(out.using_namespaces.contains(&"fmt".to_string()));
    }

    #[test]
    fn parse_extracts_struct_and_method_and_function() {
        let out = parse_go_source(SIMPLE_GO, "main.go").unwrap();
        // types: struct Greeter (+ any capitalized external type candidates)
        let has_greeter = out.types.iter().any(|t| t.name == "Greeter" && t.kind == "struct");
        assert!(has_greeter, "Greeter struct missing; types = {:?}", out.types.iter().map(|t| &t.name).collect::<Vec<_>>());
        // functions: Hello (method) + main (function)
        let has_hello = out.functions.iter().any(|f| f.name == "Hello" && f.kind == "method");
        let has_main = out.functions.iter().any(|f| f.name == "main" && f.kind == "function");
        assert!(has_hello, "Hello method missing");
        assert!(has_main, "main function missing");
    }

    #[test]
    fn parse_extracts_fields() {
        let out = parse_go_source(SIMPLE_GO, "main.go").unwrap();
        let has_name = out.fields.iter().any(|f| f.name == "Name" && f.type_signature == "string");
        assert!(has_name, "Name field missing; fields = {:?}", out.fields);
    }

    #[test]
    fn parse_extracts_calls_and_resolves() {
        let out = parse_go_source(SIMPLE_GO, "main.go").unwrap();
        // main calls g.Hello → callee_name "Hello"
        let calls_in_main: Vec<&CallEdge> = out
            .calls
            .iter()
            .filter(|c| c.caller_id.starts_with("main.main/"))
            .collect();
        assert!(!calls_in_main.is_empty(), "no calls recorded in main; calls = {:?}", out.calls);
        let has_hello_call = calls_in_main.iter().any(|c| c.callee_name == "Hello");
        assert!(has_hello_call, "Hello call missing in main");
        // After resolve_calls, Hello should have a callee_id since it's unique
        let hello_call = out.calls.iter().find(|c| c.callee_name == "Hello").unwrap();
        assert!(hello_call.callee_id.is_some(), "Hello call not resolved");
    }

    #[test]
    fn parse_meta_is_go_language() {
        let out = parse_go_source(SIMPLE_GO, "main.go").unwrap();
        assert_eq!(out.parse_meta.parser_language, "go_tree_sitter");
        assert_eq!(out.parse_meta.parser_language_initial, "go");
    }

    #[test]
    fn parse_extracts_interface() {
        let src = b"package repo\n\ntype Reader interface {\n\tRead(p []byte) (n int, err error)\n}\n";
        let out = parse_go_source(src, "reader.go").unwrap();
        let has_reader = out.types.iter().any(|t| t.name == "Reader" && t.kind == "interface");
        assert!(has_reader, "Reader interface missing");
        // Interface methods are `method_elem`, not `method_declaration` — they
        // are NOT extracted as FunctionDef (matching Python's _FUNCTION_NODES).
    }

    #[test]
    fn parse_handles_type_alias() {
        let src = b"package repo\n\ntype MyString = string\n";
        let out = parse_go_source(src, "alias.go").unwrap();
        let has_alias = out.aliases.iter().any(|a| a.name == "MyString");
        assert!(has_alias, "MyString alias missing; aliases = {:?}", out.aliases);
    }

    #[test]
    fn parse_records_includes_relations() {
        let out = parse_go_source(SIMPLE_GO, "main.go").unwrap();
        let has_includes = out
            .relations
            .iter()
            .any(|r| r.rel_type == "INCLUDES" && r.target_id == "fmt");
        assert!(has_includes, "INCLUDES relation for fmt missing");
    }
}
