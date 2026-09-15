//! Rust port của `tools/flutter/dart_parser.py` — tree-sitter Dart parse +
//! symbol extraction (class/mixin/enum/extension/type_alias, method,
//! constructor, top-level function, `static final` field, import/export/part)
//! + project-local resolution (IMPORTS/EXPORTS/HAS_PART/EXTENDS/CALLS).
//!
//! Node kinds và identity contract giữ nguyên từng chữ với bản Python vì
//! identity (`{package_uri}|{kind}|{qualified}|{offset}`) là khoá sinh
//! `dart::{project_id}::{sha24}` phía graph — lệch 1 ký tự là lệch graph.
//!
//! Borrow note: tree-sitter `Node` mượn `Tree`, nên extraction tách thành
//! `ExtractCtx` (immutable read-only) trả về `Extracted` (owned) — tuân theo
//! pattern `pyparse.rs` của analyzer-python.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::time::Instant;

use serde_json::json;
use tree_sitter::{Node, Parser, Tree};

use crate::models::{
    AnalysisFacts, DiagnosticRecord, EdgeRecord, HeaderRecord, NodeRecord, SourceEvidence,
    SummaryRecord, ANALYZER_VERSION, PROTOCOL_VERSION,
};
use cortex_analyzer_framework::scan::matches_extra_ignore;

/// `SKIPPED_DIRECTORIES` (dart_parser.py) — tập riêng của dart analyzer,
/// match EXACT từng path part (như Python `part in SKIPPED_DIRECTORIES`;
/// `"*.egg-info"` là string trong set nhưng không bao giờ khớp exact).
pub const SKIPPED_DIRECTORIES: [&str; 27] = [
    ".dart_tool",
    ".git",
    ".idea",
    "build",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "*.egg-info",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "out",
    "target",
    ".gradle",
    "dist",
    "bin",
    "obj",
    "Pods",
    "DerivedData",
    "vendor",
    ".vscode",
    ".cache",
    ".cortext-harness",
    ".flutter-plugins",
    ".flutter-plugins-dependencies",
];

/// `DECLARATION_TYPES` — node kind grammar → kind record.
fn declaration_kind(node_kind: &str) -> Option<&'static str> {
    match node_kind {
        "class_definition" => Some("class"),
        "mixin_declaration" => Some("mixin"),
        "enum_declaration" => Some("enum"),
        "extension_declaration" => Some("extension"),
        "extension_type_declaration" => Some("extension_type"),
        "type_alias" => Some("type_alias"),
        _ => None,
    }
}

/// `create_parser` — parser với grammar vendored (cùng nguồn PyPI wheel).
pub fn create_parser() -> Result<Parser, String> {
    let mut parser = Parser::new();
    parser
        .set_language(&dart_grammar_vendored::LANGUAGE.into())
        .map_err(|e| format!("tree-sitter-dart grammar load failed: {e}"))?;
    Ok(parser)
}

/// `parser_version` — pinned vendored grammar version.
pub fn parser_version() -> &'static str {
    "0.1.0"
}

/// Kết quả extraction owned của một unit (tách khỏi Tree lifetime).
#[derive(Default)]
struct Extracted {
    imports: Vec<(String, SourceEvidence)>,
    exports: Vec<(String, SourceEvidence)>,
    parts: Vec<(String, SourceEvidence)>,
    declarations: Vec<NodeRecord>,
    contains: Vec<EdgeRecord>,
}

/// Unit đã parse — giữ Tree + metadata, extraction chạy qua `ExtractCtx`.
struct Unit {
    path: PathBuf,
    relative_path: String,
    package_uri: String,
    source: Vec<u8>,
    tree: Tree,
    file_node: NodeRecord,
    extracted: Extracted,
}

/// Immutable view cho extraction (Node/Tree mượn ở đây, mutation bên ngoài).
struct ExtractCtx<'a> {
    source: &'a [u8],
    relative_path: &'a str,
    package_uri: &'a str,
    file_node: &'a NodeRecord,
    root: Node<'a>,
}

fn walk<'a>(node: Node<'a>, out: &mut Vec<Node<'a>>) {
    out.push(node);
    for index in 0..node.named_child_count() {
        if let Some(child) = node.named_child(index) {
            walk(child, out);
        }
    }
}

fn walked(node: Node<'_>) -> Vec<Node<'_>> {
    let mut out = Vec::new();
    walk(node, &mut out);
    out
}

fn text(source: &[u8], node: Node<'_>) -> String {
    String::from_utf8_lossy(&source[node.start_byte()..node.end_byte()]).into_owned()
}

fn evidence(relative_path: &str, node: Node<'_>) -> SourceEvidence {
    SourceEvidence {
        file: relative_path.to_string(),
        offset: node.start_byte(),
        length: node.end_byte() - node.start_byte(),
        start_line: node.start_position().row + 1,
        start_column: node.start_position().column + 1,
        end_line: node.end_position().row + 1,
        end_column: node.end_position().column + 1,
    }
}

fn generated(relative_path: &str) -> bool {
    relative_path.split('/').any(|part| part == "generated")
        || relative_path.ends_with(".g.dart")
        || relative_path.ends_with(".freezed.dart")
        || relative_path.ends_with(".mocks.dart")
}

fn package_uri_of(relative_path: &str, package_name: &str) -> String {
    if let Some(rest) = relative_path.strip_prefix("lib/") {
        format!("package:{package_name}/{rest}")
    } else {
        relative_path.to_string()
    }
}

fn identity(package_uri: &str, kind: &str, qualified_name: &str, offset: usize) -> String {
    format!("{package_uri}|{kind}|{qualified_name}|{offset}")
}

/// `_name_node` — field `name` hoặc named child đầu tiên dạng identifier.
fn name_node<'a>(node: Node<'a>) -> Option<Node<'a>> {
    if let Some(named) = node.child_by_field_name("name") {
        return Some(named);
    }
    (0..node.named_child_count())
        .filter_map(|index| node.named_child(index))
        .find(|child| child.kind() == "identifier" || child.kind() == "type_identifier")
}

fn string_value(source: &[u8], node: Node<'_>) -> String {
    let value = text(source, node).trim().to_string();
    let bytes = value.as_bytes();
    if value.len() >= 2
        && (bytes[0] == b'\'' || bytes[0] == b'"')
        && bytes[value.len() - 1] == bytes[0]
    {
        return value[1..value.len() - 1].to_string();
    }
    value
}

/// `_uri` — string_literal đầu tiên trong subtree.
fn uri_of(ctx: &ExtractCtx<'_>, node: Node<'_>) -> Option<(String, SourceEvidence)> {
    walked(node)
        .into_iter()
        .find(|item| item.kind() == "string_literal")
        .map(|literal| {
            (
                string_value(ctx.source, literal),
                evidence(ctx.relative_path, literal),
            )
        })
}

fn make_node(
    ctx: &ExtractCtx<'_>,
    node: Node<'_>,
    kind: &str,
    name: &str,
    qualified_name: Option<&str>,
    class_name: &str,
    end_node: Option<Node<'_>>,
) -> NodeRecord {
    let end = end_node.unwrap_or(node);
    let mut ev = evidence(ctx.relative_path, node);
    if end.end_byte() > node.end_byte() {
        ev.length = end.end_byte() - node.start_byte();
        ev.end_line = end.end_position().row + 1;
        ev.end_column = end.end_position().column + 1;
    }
    let qualified = qualified_name.unwrap_or(name).to_string();
    let code = String::from_utf8_lossy(&ctx.source[node.start_byte()..end.end_byte()]).into_owned();
    let mut properties = BTreeMap::new();
    properties.insert("name".to_string(), json!(name));
    properties.insert("qualified_name".to_string(), json!(qualified));
    properties.insert("class_name".to_string(), json!(class_name));
    properties.insert("scope_name".to_string(), json!(class_name));
    properties.insert("package_uri".to_string(), json!(ctx.package_uri));
    properties.insert("code".to_string(), json!(code));
    properties.insert("generated".to_string(), json!(generated(ctx.relative_path)));
    properties.insert("exported".to_string(), json!(!name.starts_with('_')));
    NodeRecord {
        record_type: "node",
        identity: identity(ctx.package_uri, kind, &qualified, node.start_byte()),
        kind: kind.to_string(),
        properties,
        evidence: ev,
    }
}

/// `_next_body` — `function_body` đứng ngay sau signature trong cùng block.
fn next_body<'a>(children: &[Node<'a>], index: usize) -> Option<Node<'a>> {
    if index + 1 < children.len() && children[index + 1].kind() == "function_body" {
        return Some(children[index + 1]);
    }
    None
}

/// `_function_name` — field `name` hoặc identifier đầu tiên của signature.
fn function_name(source: &[u8], signature: Node<'_>) -> Option<String> {
    let name = match signature.child_by_field_name("name") {
        Some(named) => Some(named),
        None => (0..signature.named_child_count())
            .filter_map(|index| signature.named_child(index))
            .find(|item| item.kind() == "identifier"),
    }?;
    Some(text(source, name))
}

fn add_containment(extracted: &mut Extracted, owner: &NodeRecord, child: &NodeRecord) {
    extracted.contains.push(EdgeRecord {
        record_type: "edge",
        source: owner.identity.clone(),
        target: child.identity.clone(),
        relationship: "CONTAINS".to_string(),
        properties: BTreeMap::new(),
        confidence: 1.0,
        evidence: child.evidence.clone(),
    });
}

/// `_extract_class_members` — method + constructor trong class body.
fn extract_class_members(
    ctx: &ExtractCtx<'_>,
    class_node: Node<'_>,
    owner: &NodeRecord,
    extracted: &mut Extracted,
) {
    let body = match class_node.child_by_field_name("body") {
        Some(body) => Some(body),
        None => (0..class_node.named_child_count())
            .filter_map(|index| class_node.named_child(index))
            .find(|child| child.kind() == "class_body"),
    };
    let Some(body) = body else {
        return;
    };
    let children: Vec<Node<'_>> = (0..body.named_child_count())
        .filter_map(|index| body.named_child(index))
        .collect();
    let class_name = owner
        .properties
        .get("name")
        .and_then(|value| value.as_str())
        .unwrap_or_default()
        .to_string();
    for (index, child) in children.iter().enumerate() {
        let mut signature = *child;
        if child.kind() == "method_signature" && child.named_child_count() > 0 {
            signature = child
                .named_child(child.named_child_count() - 1)
                .expect("checked named_child_count");
        }
        let declaration = if signature.kind() == "function_signature" {
            let Some(name) = function_name(ctx.source, signature) else {
                continue;
            };
            make_node(
                ctx,
                *child,
                "method",
                &name,
                Some(&format!("{class_name}.{name}")),
                &class_name,
                next_body(&children, index),
            )
        } else if child.kind() == "declaration" {
            let constructor = walked(*child).into_iter().find(|item| {
                matches!(
                    item.kind(),
                    "constant_constructor_signature"
                        | "constructor_signature"
                        | "factory_constructor_signature"
                )
            });
            let Some(constructor) = constructor else {
                continue;
            };
            let name = match name_node(constructor) {
                Some(node) => text(ctx.source, node),
                None => class_name.clone(),
            };
            make_node(
                ctx,
                *child,
                "constructor",
                &name,
                Some(&format!("{class_name}.{name}")),
                &class_name,
                next_body(&children, index),
            )
        } else {
            continue;
        };
        add_containment(extracted, owner, &declaration);
        extracted.declarations.push(declaration);
    }
}

/// `_extract_fields` — chỉ `static_final_declaration` (quirk của grammar
/// efrenbl mà bản Python đang dựa vào — giữ nguyên để parity).
fn extract_fields(ctx: &ExtractCtx<'_>, owners: &[(usize, usize, NodeRecord)], extracted: &mut Extracted) {
    for declaration in walked(ctx.root) {
        if declaration.kind() != "static_final_declaration" {
            continue;
        }
        let Some(node) = name_node(declaration) else {
            continue;
        };
        let name = text(ctx.source, node);
        let owner: NodeRecord = owners
            .iter()
            .find(|(start, end, _)| start <= &declaration.start_byte() && &declaration.end_byte() <= end)
            .map(|(_, _, record)| record.clone())
            .unwrap_or_else(|| ctx.file_node.clone());
        let class_name = if owner.kind == "file" {
            String::new()
        } else {
            owner
                .properties
                .get("name")
                .and_then(|value| value.as_str())
                .unwrap_or_default()
                .to_string()
        };
        let qualified = if class_name.is_empty() {
            name.clone()
        } else {
            format!("{class_name}.{name}")
        };
        let field = make_node(
            ctx,
            declaration,
            "field",
            &name,
            Some(&qualified),
            &class_name,
            None,
        );
        add_containment(extracted, &owner, &field);
        extracted.declarations.push(field);
    }
}

fn extract_unit(ctx: &ExtractCtx<'_>) -> Extracted {
    let mut extracted = Extracted::default();
    let root_children: Vec<Node<'_>> = (0..ctx.root.named_child_count())
        .filter_map(|index| ctx.root.named_child(index))
        .collect();

    // ── import/export/part directives ────────────────────────────────────
    for child in &root_children {
        if child.kind() != "import_or_export" && child.kind() != "part_directive" {
            continue;
        }
        let Some(uri) = uri_of(ctx, *child) else {
            continue;
        };
        let kinds: Vec<&str> = walked(*child).iter().map(|item| item.kind()).collect();
        if kinds.contains(&"library_import") {
            extracted.imports.push(uri);
        } else if kinds.contains(&"library_export") {
            extracted.exports.push(uri);
        } else if child.kind() == "part_directive" {
            extracted.parts.push(uri);
        }
    }

    // ── top-level declarations ───────────────────────────────────────────
    let mut owners: Vec<(usize, usize, NodeRecord)> = Vec::new();
    for (index, child) in root_children.iter().enumerate() {
        if let Some(kind) = declaration_kind(child.kind()) {
            let Some(node) = name_node(*child) else {
                continue;
            };
            let name = text(ctx.source, node);
            let mut declaration = make_node(ctx, *child, kind, &name, None, "", None);
            if let Some(superclass) = child.child_by_field_name("superclass")
                && let Some(type_name) = walked(superclass)
                    .into_iter()
                    .find(|item| item.kind() == "type_identifier")
            {
                declaration
                    .properties
                    .insert("superclass".to_string(), json!(text(ctx.source, type_name)));
            }
            if matches!(kind, "class" | "mixin" | "extension" | "extension_type") {
                extract_class_members(ctx, *child, &declaration, &mut extracted);
            }
            add_containment(&mut extracted, ctx.file_node, &declaration);
            owners.push((child.start_byte(), child.end_byte(), declaration.clone()));
            extracted.declarations.push(declaration);
        } else if child.kind() == "function_signature" {
            let Some(name) = function_name(ctx.source, *child) else {
                continue;
            };
            let declaration = make_node(
                ctx,
                *child,
                "function",
                &name,
                None,
                "",
                next_body(&root_children, index),
            );
            add_containment(&mut extracted, ctx.file_node, &declaration);
            extracted.declarations.push(declaration);
        }
    }
    extract_fields(ctx, &owners, &mut extracted);
    extracted
}

/// `_resolve_uri` — package:{self}/… → lib/…; package:/scheme khác → None.
fn resolve_uri(uri: &str, unit_path: &Path, root: &Path, package_name: &str) -> Option<String> {
    let self_prefix = format!("package:{package_name}/");
    if let Some(rest) = uri.strip_prefix(&self_prefix) {
        return Some(format!("lib/{rest}"));
    }
    if uri.starts_with("package:") || uri.contains(':') {
        return None;
    }
    // Python: (unit.path.parent / uri).resolve() → relative_to(root).
    // Rust canonicalize fail trên path không tồn tại — quan sát được thì cả
    // hai phía đều bỏ qua (file node không tồn tại ⇒ edge không ghi).
    let target = unit_path.parent()?.join(uri).canonicalize().ok()?;
    target
        .strip_prefix(root)
        .ok()
        .map(|rel| rel.to_string_lossy().replace('\\', "/"))
}

/// `_call_name` — tên callee từ argument_part (phần cuối của selector chain).
fn call_name(source: &[u8], argument_part: Node<'_>) -> Option<String> {
    let selector = argument_part.parent()?;
    let expression = selector.parent()?;
    let siblings: Vec<Node<'_>> = (0..expression.named_child_count())
        .filter_map(|index| expression.named_child(index))
        .collect();
    let stop = siblings.iter().position(|item| *item == selector)?;
    let mut names: Vec<String> = Vec::new();
    for sibling in &siblings[..=stop] {
        if sibling.kind() == "identifier" || sibling.kind() == "type_identifier" {
            names.push(text(source, *sibling));
        } else if sibling.kind() == "selector"
            && let Some(member) = walked(*sibling).into_iter().find(|item| {
                item.kind() == "identifier" && item.end_byte() <= argument_part.start_byte()
            })
        {
            names.push(text(source, member));
        }
    }
    names.pop()
}

/// `_owner_for_offset` — declaration chứa offset, nhỏ nhất theo length.
fn owner_for_offset<'a>(declarations: &'a [NodeRecord], file_node: &'a NodeRecord, offset: usize) -> &'a NodeRecord {
    let matches: Vec<&NodeRecord> = declarations
        .iter()
        .filter(|node| {
            node.evidence.offset <= offset && offset <= node.evidence.offset + node.evidence.length
        })
        .collect();
    if matches.is_empty() {
        return file_node;
    }
    matches.into_iter().min_by_key(|node| node.evidence.length).unwrap()
}

fn unit_diagnostics(tree: &Tree, relative_path: &str) -> Vec<DiagnosticRecord> {
    let mut values = Vec::new();
    for node in walked(tree.root_node()) {
        if node.kind() != "ERROR" && !node.is_missing() {
            continue;
        }
        values.push(DiagnosticRecord {
            record_type: "diagnostic",
            severity: "error".to_string(),
            code: "dart_syntax_error".to_string(),
            message: "Dart syntax could not be parsed at this source range".to_string(),
            recoverable: true,
            evidence: Some(evidence(relative_path, node)),
        });
    }
    values
}

/// `_discover` — rglob *.dart bỏ SKIPPED_DIRECTORIES (exact-part) + extra
/// ignore env; trả relative path sorted (BTreeSet đơn giản hoá sort của
/// Python `sorted()` trên path tuyệt đối — cùng thứ tự lexicographic).
pub fn discover(root: &Path) -> Vec<PathBuf> {
    let mut files = Vec::new();
    discover_into(root, root, &mut files);
    files.sort();
    files
}

fn discover_into(root: &Path, dir: &Path, files: &mut Vec<PathBuf>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(_) => return,
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let Ok(relative) = path.strip_prefix(root) else {
            continue;
        };
        let name = entry.file_name().to_string_lossy().to_string();
        if path.is_dir() {
            if !SKIPPED_DIRECTORIES.contains(&name.as_str()) && !matches_extra_ignore(&name) {
                discover_into(root, &path, files);
            }
            continue;
        }
        if name.ends_with(".dart") {
            files.push(relative.to_path_buf());
        }
    }
}

/// `_choose_call_target` — unique-candidate resolution của Python.
fn choose_call_target<'a>(candidates: &[&'a NodeRecord], object_creation: bool) -> Option<&'a NodeRecord> {
    let class_kinds = ["class", "mixin", "enum", "extension_type"];
    if object_creation {
        let class_candidates: Vec<&&NodeRecord> = candidates
            .iter()
            .filter(|candidate| class_kinds.contains(&candidate.kind.as_str()))
            .collect();
        return if class_candidates.len() == 1 {
            Some(class_candidates[0])
        } else {
            None
        };
    }
    let callable: Vec<&&NodeRecord> = candidates
        .iter()
        .filter(|candidate| {
            candidate.kind == "function" || class_kinds.contains(&candidate.kind.as_str())
        })
        .collect();
    if callable.len() == 1 {
        Some(callable[0])
    } else {
        None
    }
}

fn relative_posix(path: &Path) -> String {
    path.components()
        .map(|component| component.as_os_str().to_string_lossy().to_string())
        .collect::<Vec<_>>()
        .join("/")
}

/// `analyze_project` — parse toàn bộ project + resolve project-local.
pub fn analyze_project(
    root: &Path,
    project_id: &str,
    package_name: Option<&str>,
    mode: &str,
) -> Result<AnalysisFacts, String> {
    let started = Instant::now();
    let project_root = root.canonicalize().map_err(|e| e.to_string())?;
    let package = package_name
        .map(str::to_string)
        .unwrap_or_else(|| basename(&project_root));
    let mut parser = create_parser()?;
    let mut units: Vec<Unit> = Vec::new();
    let mut diagnostics: Vec<DiagnosticRecord> = Vec::new();

    for relative in discover(&project_root) {
        let path = project_root.join(&relative);
        let source = std::fs::read(&path).map_err(|e| e.to_string())?;
        let tree = parser
            .parse(&source, None)
            .ok_or_else(|| format!("tree-sitter failed to parse {}", relative.display()))?;
        let relative_path = relative_posix(&relative);
        let package_uri = package_uri_of(&relative_path, &package);
        let file_name = path
            .file_name()
            .map(|name| name.to_string_lossy().to_string())
            .unwrap_or_default();
        let mut properties = BTreeMap::new();
        properties.insert("name".to_string(), json!(file_name));
        properties.insert("path".to_string(), json!(relative_path));
        properties.insert("qualified_name".to_string(), json!(package_uri));
        properties.insert("package_uri".to_string(), json!(package_uri));
        properties.insert("generated".to_string(), json!(generated(&relative_path)));
        let file_node = NodeRecord {
            record_type: "node",
            identity: format!("file:{package_uri}"),
            kind: "file".to_string(),
            properties,
            evidence: evidence(&relative_path, tree.root_node()),
        };
        diagnostics.extend(unit_diagnostics(&tree, &relative_path));
        units.push(Unit {
            path: path.clone(),
            relative_path,
            package_uri,
            source,
            tree,
            file_node,
            extracted: Extracted::default(),
        });
    }

    // Extraction per-unit qua ctx immutable (Node mượn tạm, kết quả owned).
    for unit in &mut units {
        let extracted = {
            let ctx = ExtractCtx {
                source: &unit.source,
                relative_path: &unit.relative_path,
                package_uri: &unit.package_uri,
                file_node: &unit.file_node,
                root: unit.tree.root_node(),
            };
            extract_unit(&ctx)
        };
        unit.extracted = extracted;
    }

    // ── resolution phase (toàn bộ immutable trên units) ──────────────────
    let files_by_path: BTreeMap<&str, usize> = units
        .iter()
        .enumerate()
        .map(|(index, unit)| (unit.relative_path.as_str(), index))
        .collect();
    let mut declarations_by_name: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for unit in &units {
        for declaration in &unit.extracted.declarations {
            let name = declaration
                .properties
                .get("name")
                .and_then(|value| value.as_str())
                .unwrap_or_default()
                .to_string();
            declarations_by_name
                .entry(name)
                .or_default()
                .push(declaration.identity.clone());
        }
    }
    let mut declarations_by_identity: BTreeMap<String, &NodeRecord> = BTreeMap::new();
    for unit in &units {
        for declaration in &unit.extracted.declarations {
            declarations_by_identity.insert(declaration.identity.clone(), declaration);
        }
    }

    let mut edges: Vec<EdgeRecord> = Vec::new();
    for unit in &units {
        edges.extend(unit.extracted.contains.iter().cloned());
        let mut visible_paths: BTreeSet<String> = BTreeSet::from([unit.relative_path.clone()]);
        let directives: [(&str, &Vec<(String, SourceEvidence)>); 3] = [
            ("IMPORTS", &unit.extracted.imports),
            ("EXPORTS", &unit.extracted.exports),
            ("HAS_PART", &unit.extracted.parts),
        ];
        for (_, entries) in &directives {
            for (uri, _) in entries.iter() {
                if let Some(target_path) =
                    resolve_uri(uri, &unit.path, &project_root, &package)
                {
                    visible_paths.insert(target_path);
                }
            }
        }
        for (relationship, entries) in directives {
            for (uri, ev) in entries.iter() {
                let Some(target_path) = resolve_uri(uri, &unit.path, &project_root, &package)
                else {
                    continue;
                };
                let Some(target_index) = files_by_path.get(target_path.as_str()) else {
                    continue;
                };
                edges.push(EdgeRecord {
                    record_type: "edge",
                    source: unit.file_node.identity.clone(),
                    target: units[*target_index].file_node.identity.clone(),
                    relationship: (*relationship).to_string(),
                    properties: BTreeMap::from([("uri".to_string(), json!(uri))]),
                    confidence: 1.0,
                    evidence: ev.clone(),
                });
            }
        }

        // EXTENDS — superclass + unique candidate trong visible paths.
        for declaration in &unit.extracted.declarations {
            let superclass = declaration
                .properties
                .get("superclass")
                .and_then(|value| value.as_str())
                .unwrap_or_default();
            let candidates: Vec<&NodeRecord> = if superclass.is_empty() {
                Vec::new()
            } else {
                declarations_by_name
                    .get(superclass)
                    .map(|entries| {
                        entries
                            .iter()
                            .filter_map(|id| declarations_by_identity.get(id).copied())
                            .filter(|candidate| visible_paths.contains(&candidate.evidence.file))
                            .collect()
                    })
                    .unwrap_or_default()
            };
            if candidates.len() == 1 {
                edges.push(EdgeRecord {
                    record_type: "edge",
                    source: declaration.identity.clone(),
                    target: candidates[0].identity.clone(),
                    relationship: "EXTENDS".to_string(),
                    properties: BTreeMap::new(),
                    confidence: 1.0,
                    evidence: declaration.evidence.clone(),
                });
            }
        }

        // CALLS — const_object_expression / argument_part, owner-scoped.
        let declarations = &unit.extracted.declarations;
        let mut seen_calls: BTreeSet<(String, String, usize)> = BTreeSet::new();
        for node in walked(unit.tree.root_node()) {
            let mut call: Option<(String, bool)> = None;
            if node.kind() == "const_object_expression" {
                let type_node = (0..node.named_child_count())
                    .filter_map(|index| node.named_child(index))
                    .find(|item| item.kind() == "type_identifier");
                call = type_node.map(|item| (text(&unit.source, item), true));
            } else if node.kind() == "argument_part" {
                call = call_name(&unit.source, node).map(|name| (name, false));
            }
            let Some((call_name, object_creation)) = call else {
                continue;
            };
            if call_name.is_empty() {
                continue;
            }
            let candidates: Vec<&NodeRecord> = declarations_by_name
                .get(&call_name)
                .map(|entries| {
                    entries
                        .iter()
                        .filter_map(|id| declarations_by_identity.get(id).copied())
                        .filter(|candidate| visible_paths.contains(&candidate.evidence.file))
                        .collect()
                })
                .unwrap_or_default();
            let Some(target) = choose_call_target(&candidates, object_creation) else {
                continue;
            };
            let owner = owner_for_offset(declarations, &unit.file_node, node.start_byte());
            let key = (owner.identity.clone(), target.identity.clone(), node.start_byte());
            if seen_calls.contains(&key) || owner.identity == target.identity {
                continue;
            }
            seen_calls.insert(key);
            edges.push(EdgeRecord {
                record_type: "edge",
                source: owner.identity.clone(),
                target: target.identity.clone(),
                relationship: "CALLS".to_string(),
                properties: BTreeMap::from([("resolved_name".to_string(), json!(call_name))]),
                confidence: 1.0,
                evidence: evidence(&unit.relative_path, node),
            });
        }
    }

    let mut nodes: Vec<NodeRecord> = units
        .iter()
        .flat_map(|unit| {
            std::iter::once(unit.file_node.clone())
                .chain(unit.extracted.declarations.iter().cloned())
        })
        .collect();
    nodes.sort_by(|left, right| left.identity.cmp(&right.identity));

    let mut unique_edges: BTreeMap<(String, String, String, usize), EdgeRecord> = BTreeMap::new();
    for edge in edges {
        unique_edges
            .entry((
                edge.source.clone(),
                edge.target.clone(),
                edge.relationship.clone(),
                edge.evidence.offset,
            ))
            .or_insert(edge);
    }
    let mut unique_edges: Vec<EdgeRecord> = unique_edges.into_values().collect();
    unique_edges.sort_by(|left, right| {
        (
            left.relationship.as_str(),
            left.source.as_str(),
            left.target.as_str(),
            left.evidence.offset,
        )
            .cmp(&(
                right.relationship.as_str(),
                right.source.as_str(),
                right.target.as_str(),
                right.evidence.offset,
            ))
    });

    let error_count = diagnostics
        .iter()
        .filter(|item| item.severity == "error")
        .count();
    Ok(AnalysisFacts {
        header: HeaderRecord {
            record_type: "header",
            schema_version: PROTOCOL_VERSION.to_string(),
            analyzer_version: ANALYZER_VERSION.to_string(),
            sdk_version: "not-required-python-runtime".to_string(),
            root: project_root.to_string_lossy().to_string(),
            project_id: project_id.to_string(),
            mode: mode.to_string(),
        },
        nodes,
        edges: unique_edges,
        diagnostics,
        summary: SummaryRecord {
            record_type: "summary",
            processed_files: units.len(),
            skipped_files: 0,
            error_count,
            elapsed_ms: started.elapsed().as_millis() as i64,
        },
    })
}

fn basename(path: &Path) -> String {
    path.file_name()
        .map(|name| name.to_string_lossy().to_string())
        .unwrap_or_else(|| path.to_string_lossy().to_string())
}
