//! Port `tools/mybatis/mapper_interface_analyzer.py` — tree-sitter-java parse
//! của mapper interfaces + java type property index.

use std::collections::{BTreeSet, HashMap};
use std::path::Path;

use regex::Regex;
use tree_sitter::{Node, Parser};

use cortex_analyzer_framework::ts::decode_ignore;

use crate::mybatis::detector::EXCLUDED_DIRS;
use crate::mybatis::java_symbols::java_symbol_maps;
use crate::mybatis::models::{
    AnnotationFact, Diagnostic, JavaPropertyFact, MapperInterfaceFact, MapperMethodFact,
    MapperParameterFact, SourceSpan,
};

pub const MYBATIS_ANNOTATION_PREFIX: &str = "org.apache.ibatis.annotations.";

const SQL_ANNOTATIONS: [&str; 19] = [
    "Select",
    "Insert",
    "Update",
    "Delete",
    "SelectProvider",
    "InsertProvider",
    "UpdateProvider",
    "DeleteProvider",
    "Results",
    "Result",
    "ResultMap",
    "Options",
    "Flush",
    "CacheNamespace",
    "CacheNamespaceRef",
    "ConstructorArgs",
    "Arg",
    "One",
    "Many",
];

const SPECIAL_PARAMETERS: [(&str, &str); 4] = [
    ("RowBounds", "row_bounds"),
    ("org.apache.ibatis.session.RowBounds", "row_bounds"),
    ("ResultHandler", "result_handler"),
    ("org.apache.ibatis.session.ResultHandler", "result_handler"),
];

pub struct MapperInterfaceAnalysis {
    pub interfaces: Vec<MapperInterfaceFact>,
    pub methods: Vec<MapperMethodFact>,
    pub parameters: Vec<MapperParameterFact>,
    pub java_properties: Vec<JavaPropertyFact>,
    pub diagnostics: Vec<Diagnostic>,
}

/// `analyze_mapper_interfaces`.
pub fn analyze_mapper_interfaces(
    root: &Path,
    java_files: &[String],
    project_id: &str,
    _project_name: &str,
) -> MapperInterfaceAnalysis {
    let mut interfaces: Vec<MapperInterfaceFact> = Vec::new();
    let mut methods: Vec<MapperMethodFact> = Vec::new();
    let mut parameters: Vec<MapperParameterFact> = Vec::new();
    let mut diagnostics: Vec<Diagnostic> = Vec::new();
    let mut referenced_types: BTreeSet<String> = BTreeSet::new();

    let unique_files: BTreeSet<&String> = java_files.iter().collect();
    for rel_path in unique_files {
        let abs_path = root.join(rel_path.as_str());
        if !abs_path.is_file() {
            diagnostics.push(Diagnostic::new(
                "mybatis.java.missing_file",
                "Mapper Java file is missing",
            ));
            continue;
        }
        let Ok(source_bytes) = std::fs::read(&abs_path) else {
            diagnostics.push(Diagnostic::new(
                "mybatis.java.parse_failed",
                "unable to read file",
            ));
            continue;
        };
        let file_result = match analyze_mapper_interface_file(&source_bytes, rel_path, project_id, root)
        {
            Ok(result) => result,
            Err(message) => {
                diagnostics.push(Diagnostic::new("mybatis.java.parse_failed", &message));
                continue;
            }
        };
        let default_package = file_result
            .interfaces
            .first()
            .map(|item| item.package_name.clone())
            .unwrap_or_default();
        for method in &file_result.methods {
            referenced_types.extend(referenced_type_candidates(&method.return_type, &default_package));
            for parameter_type in &method.parameter_types {
                referenced_types
                    .extend(referenced_type_candidates(parameter_type, &default_package));
            }
        }
        interfaces.extend(file_result.interfaces);
        methods.extend(file_result.methods);
        parameters.extend(file_result.parameters);
        diagnostics.extend(file_result.diagnostics);
    }

    let properties =
        build_java_type_index(root, &referenced_types, project_id);
    MapperInterfaceAnalysis {
        interfaces,
        methods,
        parameters,
        java_properties: properties,
        diagnostics,
    }
}

struct FileAnalysis {
    interfaces: Vec<MapperInterfaceFact>,
    methods: Vec<MapperMethodFact>,
    parameters: Vec<MapperParameterFact>,
    diagnostics: Vec<Diagnostic>,
}

/// `analyze_mapper_interface_file` — có rel_path/root cho symbol maps.
fn analyze_mapper_interface_file(
    source_bytes: &[u8],
    rel_path: &str,
    project_id: &str,
    root: &Path,
) -> Result<FileAnalysis, String> {
    let mut parser = Parser::new();
    parser
        .set_language(&tree_sitter_java::LANGUAGE.into())
        .map_err(|e| format!("{e:?}"))?;
    let tree = parser
        .parse(source_bytes, None)
        .ok_or_else(|| "java parse failed".to_string())?;
    let root_node = tree.root_node();
    let package_name = package_name_of(root_node, source_bytes);
    let imports = imports_of(root_node, source_bytes);
    let rel_for_symbols = rel_posix_of(rel_path, root);
    let java_symbols = java_symbol_maps(source_bytes, &rel_for_symbols);

    let mut interfaces: Vec<MapperInterfaceFact> = Vec::new();
    let mut methods: Vec<MapperMethodFact> = Vec::new();
    let mut parameters: Vec<MapperParameterFact> = Vec::new();
    let mut diagnostics: Vec<Diagnostic> = Vec::new();

    for (node, class_path) in iter_type_declarations(root_node, source_bytes, true) {
        let decl = decl_name(node, source_bytes);
        let name = decl.trim().to_string();
        if name.is_empty() {
            continue;
        }
        let fqcn = fqcn(&package_name, &class_path);
        let class_symbol_id = java_symbols
            .class_ids
            .get(&fqcn)
            .cloned()
            .unwrap_or_else(|| format!("class::{fqcn}"));
        if !interface_has_mybatis_evidence(node, source_bytes, &imports, rel_path) {
            continue;
        }
        let annotations = annotations_of(node, source_bytes, &imports, rel_path);
        let mapper_id = format!("mybatis_mapper::{project_id}::{fqcn}");
        let (method_facts, param_facts, method_diags) = extract_methods(
            node,
            source_bytes,
            &imports,
            project_id,
            &fqcn,
            &class_path,
            &java_symbols,
            rel_path,
        );
        interfaces.push(MapperInterfaceFact {
            stable_id: mapper_id,
            java_class_symbol_id: class_symbol_id,
            name,
            fqcn: fqcn.clone(),
            file_path: rel_path.to_string(),
            source: span_of(node, rel_path),
            package_name: package_name.clone(),
            type_parameters: type_parameters_of(node, source_bytes),
            extended_interfaces: extends_interfaces_of(node, source_bytes),
            modifiers: modifiers_of(node, source_bytes),
            imports: imports.clone(),
            annotations,
            methods: method_facts.clone(),
        });
        methods.extend(method_facts);
        parameters.extend(param_facts);
        diagnostics.extend(method_diags);
    }

    Ok(FileAnalysis {
        interfaces,
        methods,
        parameters,
        diagnostics,
    })
}

// ── tree helpers ────────────────────────────────────────────────────────────

fn text_of(node: Option<Node<'_>>, source: &[u8]) -> String {
    match node {
        None => String::new(),
        Some(node) => decode_ignore(&source[node.start_byte()..node.end_byte()])
            .trim()
            .to_string(),
    }
}

fn span_of(node: Node<'_>, file_path: &str) -> SourceSpan {
    SourceSpan::with_points(
        file_path,
        node.start_position().row as i64 + 1,
        node.end_position().row as i64 + 1,
        node.start_position().column as i64 + 1,
        node.end_position().column as i64 + 1,
    )
}

/// `_iter_type_declarations` — yield (node, class_path) cho want kinds;
/// khi `want_interfaces=false` dùng cho property index (class+record).
fn iter_type_declarations<'tree>(
    node: Node<'tree>,
    source: &[u8],
    interfaces: bool,
) -> Vec<(Node<'tree>, String)> {
    let mut out = Vec::new();
    walk_decls(node, source, &[], interfaces, &mut out);
    out
}

fn walk_decls<'tree>(
    node: Node<'tree>,
    source: &[u8],
    stack: &[String],
    interfaces: bool,
    out: &mut Vec<(Node<'tree>, String)>,
) {
    if node.kind() == "class_declaration"
        || node.kind() == "interface_declaration"
        || node.kind() == "record_declaration"
    {
        let name = decl_name(node, source);
        let mut path = stack.to_vec();
        if !name.is_empty() {
            path.push(name);
        }
        let wanted = if interfaces {
            node.kind() == "interface_declaration"
        } else {
            node.kind() == "class_declaration" || node.kind() == "record_declaration"
        };
        if wanted {
            out.push((node, path.join(".")));
        }
        if let Some(body) = node.child_by_field_name("body") {
            let mut cursor = body.walk();
            for child in body.children(&mut cursor) {
                walk_decls(child, source, &path, interfaces, out);
            }
        }
        return;
    }
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        walk_decls(child, source, stack, interfaces, out);
    }
}

fn static_import_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^import\s+static\s+").unwrap())
}

fn import_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| Regex::new(r"^import\s+").unwrap())
}

fn package_name_of(root: Node<'_>, source: &[u8]) -> String {
    let mut cursor = root.walk();
    for child in root.children(&mut cursor) {
        if child.kind() == "package_declaration" {
            let text = text_of(Some(child), source);
            let text = text.strip_prefix("package").unwrap_or(&text);
            let text = text.trim_end_matches(';').trim();
            return text.to_string();
        }
    }
    String::new()
}

fn imports_of(root: Node<'_>, source: &[u8]) -> Vec<String> {
    let mut rows: Vec<String> = Vec::new();
    let mut cursor = root.walk();
    for child in root.children(&mut cursor) {
        if child.kind() == "import_declaration" {
            let mut text = text_of(Some(child), source);
            text = static_import_re().replace(&text, "").into_owned();
            text = import_re().replace(&text, "").into_owned();
            let text = text.trim_end_matches(';').trim().to_string();
            rows.push(text);
        }
    }
    rows
}

fn decl_name(node: Node<'_>, source: &[u8]) -> String {
    text_of(node.child_by_field_name("name"), source)
}

fn fqcn(package_name: &str, class_path: &str) -> String {
    if package_name.is_empty() {
        class_path.to_string()
    } else {
        format!("{package_name}.{class_path}")
    }
}

fn modifiers_node<'a>(node: Node<'a>) -> Option<Node<'a>> {
    if let Some(mods) = node.child_by_field_name("modifiers") {
        return Some(mods);
    }
    let mut cursor = node.walk();
    node.children(&mut cursor)
        .find(|child| child.kind() == "modifiers")
}

/// `_modifiers`.
fn modifiers_of(node: Node<'_>, source: &[u8]) -> Vec<String> {
    let Some(mods) = modifiers_node(node) else {
        return Vec::new();
    };
    let mut cursor = mods.walk();
    mods.children(&mut cursor)
        .filter(|child| child.kind() != "annotation" && child.kind() != "marker_annotation")
        .map(|child| text_of(Some(child), source))
        .collect()
}

/// `_annotations`.
fn annotations_of(
    node: Node<'_>,
    source: &[u8],
    imports: &[String],
    file_path: &str,
) -> Vec<AnnotationFact> {
    let mut results: Vec<AnnotationFact> = Vec::new();
    let mods = match modifiers_node(node) {
        Some(mods) => Some(mods),
        None => {
            if node.kind() == "formal_parameter" {
                let mut cursor = node.walk();
                node.children(&mut cursor).find(|child| child.kind() == "modifiers")
            } else {
                None
            }
        }
    };
    let Some(mods) = mods else {
        return results;
    };
    let mut cursor = mods.walk();
    for child in mods.children(&mut cursor) {
        if child.kind() != "annotation" && child.kind() != "marker_annotation" {
            continue;
        }
        let name = text_of(child.child_by_field_name("name"), source);
        let args = text_of(child.child_by_field_name("arguments"), source);
        results.push(AnnotationFact {
            resolved_name: resolve_annotation(&name, imports),
            name,
            raw_arguments: args,
            source: span_of(child, file_path),
        });
    }
    results
}

/// `_resolve_annotation`.
fn resolve_annotation(name: &str, imports: &[String]) -> String {
    if name.contains('.') {
        return name.to_string();
    }
    for item in imports {
        if item.ends_with(&format!(".{name}")) {
            return item.clone();
        }
        if item == "org.apache.ibatis.annotations.*" {
            return format!("{MYBATIS_ANNOTATION_PREFIX}{name}");
        }
    }
    if SQL_ANNOTATIONS.contains(&name) || name == "Mapper" || name == "Param" {
        if name == "Mapper" {
            return "org.apache.ibatis.annotations.Mapper".to_string();
        }
        return format!("{MYBATIS_ANNOTATION_PREFIX}{name}");
    }
    name.to_string()
}

/// `_type_parameters`.
fn type_parameters_of(node: Node<'_>, source: &[u8]) -> Vec<String> {
    let mut cursor = node.walk();
    let child = node
        .children(&mut cursor)
        .find(|item| item.kind() == "type_parameters");
    let Some(child) = child else {
        return Vec::new();
    };
    let mut inner = child.walk();
    child
        .children(&mut inner)
        .filter(|item| item.kind() == "type_parameter")
        .map(|item| text_of(Some(item), source))
        .collect()
}

/// `_extends_interfaces`.
fn extends_interfaces_of(node: Node<'_>, source: &[u8]) -> Vec<String> {
    let mut cursor = node.walk();
    let child = node
        .children(&mut cursor)
        .find(|item| item.kind() == "extends_interfaces");
    let Some(child) = child else {
        return Vec::new();
    };
    let mut inner = child.walk();
    let type_list = child
        .children(&mut inner)
        .find(|item| item.kind() == "type_list")
        .unwrap_or(child);
    let mut list_cursor = type_list.walk();
    type_list
        .children(&mut list_cursor)
        .filter(tree_sitter::Node::is_named)
        .map(|item| text_of(Some(item), source))
        .collect()
}

/// `_return_type`.
fn return_type_of(node: Node<'_>, source: &[u8]) -> String {
    let text = text_of(node.child_by_field_name("type"), source);
    if text.is_empty() {
        "void".to_string()
    } else {
        text
    }
}

/// `_parameter_nodes`.
fn parameter_nodes(node: Node<'_>) -> Vec<Node<'_>> {
    let params = match node.child_by_field_name("parameters") {
        Some(params) => params,
        None => {
            if node.kind() == "formal_parameters" {
                node
            } else {
                return Vec::new();
            }
        }
    };
    let mut cursor = params.walk();
    params
        .children(&mut cursor)
        .filter(|child| child.kind() == "formal_parameter" || child.kind() == "spread_parameter")
        .collect()
}

/// `_parameter_type`.
fn parameter_type_of(node: Node<'_>, source: &[u8]) -> String {
    let mut text = text_of(node.child_by_field_name("type"), source);
    if text.is_empty() && node.kind() == "spread_parameter" {
        let mut cursor = node.walk();
        let type_node = node.children(&mut cursor).find(|child| {
            child.kind() == "type_identifier"
                || child.kind() == "scoped_type_identifier"
                || child.kind() == "generic_type"
                || child.kind().ends_with("type")
        });
        text = text_of(type_node, source);
    }
    if node.kind() == "spread_parameter" && !text.is_empty() && !text.ends_with("...") {
        format!("{text}...")
    } else {
        text
    }
}

/// `_throws`.
fn throws_of(node: Node<'_>, source: &[u8]) -> Vec<String> {
    let mut cursor = node.walk();
    let child = node.children(&mut cursor).find(|item| item.kind() == "throws");
    let Some(child) = child else {
        return Vec::new();
    };
    let mut inner = child.walk();
    child
        .children(&mut inner)
        .filter(tree_sitter::Node::is_named)
        .map(|item| text_of(Some(item), source))
        .collect()
}

/// `_canonical_type`.
fn canonical_type(raw: &str, imports: &[String]) -> String {
    let re_ws = Regex::new(r"\s+").unwrap();
    let raw = re_ws.replace_all(raw.trim(), " ").into_owned();
    if raw.is_empty() {
        return String::new();
    }
    let mut suffix = "";
    let mut base = raw.clone();
    if raw.ends_with("...") {
        suffix = "...";
        base = raw[..raw.len() - 3].to_string();
    } else if raw.ends_with("[]") {
        suffix = "[]";
        base = raw[..raw.len() - 2].to_string();
    }
    let Some(head_match) = Regex::new(r"^([A-Za-z_][\w.]*)").unwrap().captures(&base) else {
        return raw;
    };
    let head = head_match.get(1).unwrap().as_str();
    let simple = head.rsplit('.').next().unwrap_or(head);
    let mut resolved = head.to_string();
    for item in imports {
        if item.ends_with(&format!(".{simple}")) {
            resolved = item.clone();
            break;
        }
        if item.ends_with(".*") && !head.contains('.') && item.starts_with("org.apache.ibatis.") {
            resolved = format!("{}.{simple}", &item[..item.len() - 2]);
        }
    }
    format!("{}{}{}", resolved, &base[head.len()..], suffix)
}

/// `_simple_type_name`.
fn simple_type_name(raw: &str) -> String {
    let re = Regex::new(r"<.*>").unwrap();
    let text = re.replace_all(raw, "").into_owned();
    let text = text.replace("[]", "").replace("...", "").trim().to_string();
    if text.is_empty()
        || ["void", "boolean", "byte", "short", "int", "long", "float", "double", "char"]
            .contains(&text.as_str())
    {
        return String::new();
    }
    text.rsplit('.').next().unwrap_or(&text).to_string()
}

/// `_is_xml_bindable_candidate`.
fn is_xml_bindable_candidate(node: Node<'_>, source: &[u8], imports: &[String]) -> bool {
    let annotations = annotations_of(node, source, imports, "");
    let has_mybatis_annotation = annotations
        .iter()
        .any(|item| item.resolved_name.starts_with(MYBATIS_ANNOTATION_PREFIX));
    if has_mybatis_annotation {
        return true;
    }
    let modifiers: BTreeSet<String> = modifiers_of(node, source).into_iter().collect();
    if modifiers.contains("default") || modifiers.contains("static") {
        return false;
    }
    node.kind() == "method_declaration"
}

/// `_interface_has_mybatis_evidence`.
fn interface_has_mybatis_evidence(
    node: Node<'_>,
    source: &[u8],
    imports: &[String],
    file_path: &str,
) -> bool {
    let annotations = annotations_of(node, source, imports, file_path);
    if annotations
        .iter()
        .any(|item| item.resolved_name == "org.apache.ibatis.annotations.Mapper")
    {
        return true;
    }
    let Some(body) = node.child_by_field_name("body") else {
        return false;
    };
    let mut cursor = body.walk();
    for child in body.children(&mut cursor) {
        if !child.is_named() || child.kind() != "method_declaration" {
            continue;
        }
        let method_annotations = annotations_of(child, source, imports, file_path);
        if method_annotations
            .iter()
            .any(|item| item.resolved_name.starts_with(MYBATIS_ANNOTATION_PREFIX))
        {
            return true;
        }
    }
    false
}

fn java_analyzer_arity(param_nodes: &[Node<'_>]) -> i64 {
    param_nodes
        .iter()
        .filter(|item| item.kind() == "formal_parameter")
        .count() as i64
}

fn signature_suffix(parameter_types: &[String]) -> String {
    crate::pyutil::sha1_hex(parameter_types.join("|").as_bytes())[..12].to_string()
}

/// `_referenced_type_candidates`.
fn referenced_type_candidates(type_text: &str, default_package: &str) -> BTreeSet<String> {
    const IGNORED: [&str; 15] = [
        "void", "boolean", "byte", "short", "int", "long", "float", "double", "char", "String",
        "List", "Map", "Set", "Collection", "Optional",
    ];
    let mut rows = BTreeSet::new();
    let re = Regex::new(r"[A-Za-z_][\w.]*").unwrap();
    for token in re.find_iter(type_text) {
        let token = token.as_str();
        let simple = token.rsplit('.').next().unwrap_or(token);
        if IGNORED.contains(&simple) {
            continue;
        }
        if token.contains('.') || default_package.is_empty() {
            rows.insert(token.to_string());
        } else {
            rows.insert(format!("{default_package}.{token}"));
        }
    }
    rows
}

fn first_string_literal(text: &str) -> String {
    let re = Regex::new(r#""([^"]*)""#).unwrap();
    re.captures(text)
        .map(|caps| caps.get(1).unwrap().as_str().to_string())
        .unwrap_or_default()
}

fn decap(text: &str) -> String {
    let mut chars = text.chars();
    match chars.next() {
        Some(first) => first.to_lowercase().collect::<String>() + chars.as_str(),
        None => String::new(),
    }
}

// ── method extraction ───────────────────────────────────────────────────────

#[allow(clippy::too_many_arguments)]
fn extract_methods(
    node: Node<'_>,
    source_bytes: &[u8],
    imports: &[String],
    project_id: &str,
    mapper_fqcn: &str,
    class_path: &str,
    java_symbols: &crate::mybatis::java_symbols::JavaSymbolMaps,
    file_path: &str,
) -> (Vec<MapperMethodFact>, Vec<MapperParameterFact>, Vec<Diagnostic>) {
    let body = node.child_by_field_name("body");
    let method_nodes: Vec<Node<'_>> = match body {
        None => Vec::new(),
        Some(body) => {
            let mut cursor = body.walk();
            body.children(&mut cursor)
                .filter(|child| {
                    child.kind() == "method_declaration" || child.kind() == "constructor_declaration"
                })
                .collect()
        }
    };
    let mut name_counts: HashMap<String, i64> = HashMap::new();
    for method_node in &method_nodes {
        if let Some(name) = method_name_of(*method_node, source_bytes)
            && is_xml_bindable_candidate(*method_node, source_bytes, imports)
        {
            *name_counts.entry(name).or_insert(0) += 1;
        }
    }

    let mut methods: Vec<MapperMethodFact> = Vec::new();
    let mut parameters: Vec<MapperParameterFact> = Vec::new();
    let mut diagnostics: Vec<Diagnostic> = Vec::new();
    for method_node in &method_nodes {
        let Some(name) = method_name_of(*method_node, source_bytes) else {
            continue;
        };
        let param_nodes = parameter_nodes(*method_node);
        let param_types: Vec<String> = param_nodes
            .iter()
            .map(|param| {
                canonical_type(&parameter_type_of(*param, source_bytes), imports)
            })
            .collect();
        let return_type = canonical_type(&return_type_of(*method_node, source_bytes), imports);
        let start_line = method_node.start_position().row as i64 + 1;
        let java_arity = java_analyzer_arity(&param_nodes);
        let java_symbol_id = java_symbols
            .method_ids
            .get(&(class_path.to_string(), name.clone(), java_arity, start_line))
            .cloned()
            .or_else(|| {
                java_symbols
                    .method_ids_fallback
                    .get(&(class_path.to_string(), name.clone(), java_arity))
                    .cloned()
            })
            .unwrap_or_default();
        let base_method_id = if !java_symbol_id.is_empty() {
            java_symbol_id.clone()
        } else {
            format!("{mapper_fqcn}#{name}/{}", param_nodes.len())
        };
        let mut stable_id = format!("mybatis_method::{project_id}::{base_method_id}");
        if name_counts.get(&name).copied().unwrap_or(0) > 1 {
            stable_id = format!("{stable_id}::{}", signature_suffix(&param_types));
        }
        let annotations = annotations_of(*method_node, source_bytes, imports, file_path);
        let modifiers = modifiers_of(*method_node, source_bytes);
        let has_body = method_node.child_by_field_name("body").is_some();
        let bindable = is_xml_bindable_candidate(*method_node, source_bytes, imports);
        let overload_count = if bindable {
            name_counts.get(&name).copied().unwrap_or(0)
        } else {
            0
        };
        let ambiguity = if overload_count > 1 {
            "ambiguous"
        } else {
            "unique"
        };
        if overload_count > 1 {
            diagnostics.push(Diagnostic::new(
                "mybatis.mapper_method.overloaded_statement_id",
                &format!(
                    "Mapper statement id {name:?} has {overload_count} bindable overloads"
                ),
            ));
        }
        let mut method_params: Vec<MapperParameterFact> = Vec::new();
        for (idx, param_node) in param_nodes.iter().enumerate() {
            let param = parameter_fact(
                *param_node,
                source_bytes,
                imports,
                project_id,
                &stable_id,
                idx as i64,
                file_path,
            );
            method_params.push(param.clone());
            parameters.push(param);
        }
        methods.push(MapperMethodFact {
            stable_id,
            java_symbol_id,
            mapper_fqcn: mapper_fqcn.to_string(),
            signature: format!("{}({})", name, param_types.join(", ")),
            return_type,
            parameter_types: param_types,
            source: span_of(*method_node, file_path),
            bindable,
            overload_count: if overload_count == 0 { 1 } else { overload_count },
            ambiguity_status: ambiguity.to_string(),
            name,
            modifiers,
            throws: throws_of(*method_node, source_bytes),
            annotations,
            parameters: method_params,
            has_body,
        });
    }
    (methods, parameters, diagnostics)
}

fn method_name_of(node: Node<'_>, source: &[u8]) -> Option<String> {
    let name = text_of(node.child_by_field_name("name"), source);
    if name.is_empty() {
        None
    } else {
        Some(name)
    }
}

#[allow(clippy::too_many_arguments)]
fn parameter_fact(
    param_node: Node<'_>,
    source_bytes: &[u8],
    imports: &[String],
    project_id: &str,
    method_id: &str,
    position: i64,
    file_path: &str,
) -> MapperParameterFact {
    let name = text_of(param_node.child_by_field_name("name"), source_bytes);
    let raw_type = parameter_type_of(param_node, source_bytes);
    let canonical = canonical_type(&raw_type, imports);
    let annotations = annotations_of(param_node, source_bytes, imports, file_path);
    let mut alias = String::new();
    for annotation in &annotations {
        if annotation.resolved_name == format!("{MYBATIS_ANNOTATION_PREFIX}Param") {
            alias = first_string_literal(&annotation.raw_arguments);
        }
    }
    let special_role = SPECIAL_PARAMETERS
        .iter()
        .find(|(key, _)| *key == canonical || *key == raw_type)
        .map(|(_, role)| role.to_string())
        .unwrap_or_default();
    MapperParameterFact {
        stable_id: format!("{method_id}::param::{position}"),
        mapper_method_id: method_id.to_string(),
        name,
        position,
        java_type: raw_type,
        canonical_type: canonical,
        param_alias: alias,
        special_role,
        source: span_of(param_node, file_path),
        annotations: {
            let _ = project_id;
            annotations
        },
    }
}

// ── java type property index ────────────────────────────────────────────────

/// `build_java_type_index`.
pub fn build_java_type_index(
    root: &Path,
    referenced_types: &BTreeSet<String>,
    project_id: &str,
) -> Vec<JavaPropertyFact> {
    let wanted: BTreeSet<&String> = referenced_types.iter().filter(|item| !item.is_empty()).collect();
    if wanted.is_empty() {
        return Vec::new();
    }
    let mut properties: HashMap<String, JavaPropertyFact> = HashMap::new();
    let mut java_files: Vec<(std::path::PathBuf, String)> = Vec::new();
    iter_java_files(root, root, &mut java_files);
    for (abs_path, rel_path) in java_files {
        let Ok(source_bytes) = std::fs::read(&abs_path) else {
            continue;
        };
        let mut parser = Parser::new();
        if parser
            .set_language(&tree_sitter_java::LANGUAGE.into())
            .is_err()
        {
            continue;
        }
        let Some(tree) = parser.parse(&source_bytes, None) else {
            continue;
        };
        let root_node = tree.root_node();
        let package_name = package_name_of(root_node, &source_bytes);
        for (node, class_path) in iter_type_declarations(root_node, &source_bytes, false) {
            let fqcn = fqcn(&package_name, &class_path);
            if !wanted.contains(&fqcn) && !wanted.contains(&simple_type_name(&fqcn)) {
                continue;
            }
            let mut rows: Vec<(String, String, String, Node<'_>, bool, bool)> = Vec::new();
            property_rows(node, &source_bytes, &mut rows);
            for (prop_name, prop_type, source_kind, prop_node, readable, writable) in rows {
                let stable_id =
                    format!("mybatis_java_property::{project_id}::{fqcn}::{prop_name}");
                let next_fact = JavaPropertyFact {
                    stable_id: stable_id.clone(),
                    java_type_fqcn: fqcn.clone(),
                    property_name: prop_name,
                    property_type: canonical_type(&prop_type, &[]),
                    source_kind: source_kind.clone(),
                    source: span_of(prop_node, &rel_path),
                    readable,
                    writable,
                    source_symbol_id: String::new(),
                };
                match properties.get_mut(&stable_id) {
                    None => {
                        properties.insert(stable_id, next_fact);
                    }
                    Some(existing) => {
                        let mut kinds: BTreeSet<String> = existing
                            .source_kind
                            .split(',')
                            .map(str::to_string)
                            .collect();
                        kinds.insert(source_kind);
                        let merged = JavaPropertyFact {
                            stable_id: existing.stable_id.clone(),
                            java_type_fqcn: existing.java_type_fqcn.clone(),
                            property_name: existing.property_name.clone(),
                            property_type: if existing.property_type.is_empty() {
                                next_fact.property_type
                            } else {
                                existing.property_type.clone()
                            },
                            source_kind: {
                                let mut sorted: Vec<String> = kinds.into_iter().collect();
                                sorted.sort();
                                sorted.join(",")
                            },
                            source: existing.source.clone(),
                            readable: existing.readable || readable,
                            writable: existing.writable || writable,
                            source_symbol_id: String::new(),
                        };
                        *existing = merged;
                    }
                }
            }
        }
    }
    let mut keys: Vec<String> = properties.keys().cloned().collect();
    keys.sort();
    keys.into_iter().map(|key| properties[&key].clone()).collect()
}

/// `_property_rows` — record components + fields + getters/setters.
fn property_rows<'a>(
    node: Node<'a>,
    source: &[u8],
    rows: &mut Vec<(String, String, String, Node<'a>, bool, bool)>,
) {
    if node.kind() == "record_declaration"
        && let Some(params) = node.child_by_field_name("parameters")
    {
        {
            for param in parameter_nodes(params) {
                let name = text_of(param.child_by_field_name("name"), source);
                rows.push((
                    name,
                    parameter_type_of(param, source),
                    "record_component".to_string(),
                    param,
                    true,
                    true,
                ));
            }
        }
    }
    walk_all(node, &mut |child| {
        if child.kind() == "field_declaration" {
            let type_text = text_of(child.child_by_field_name("type"), source);
            let declarator = child.child_by_field_name("declarator");
            let name_node = declarator.and_then(|decl| decl.child_by_field_name("name"));
            let name = text_of(name_node, source);
            if !name.is_empty() {
                rows.push((name, type_text, "field".to_string(), child, true, true));
            }
        } else if child.kind() == "method_declaration" {
            let Some(name) = method_name_of(child, source) else {
                return;
            };
            let params = parameter_nodes(child);
            let return_type = return_type_of(child, source);
            if params.is_empty() && name.starts_with("get") && name.len() > 3 {
                rows.push((decap(&name[3..]), return_type, "getter".to_string(), child, true, false));
            } else if params.is_empty()
                && name.starts_with("is")
                && name.len() > 2
                && (return_type == "boolean" || return_type == "Boolean")
            {
                rows.push((decap(&name[2..]), return_type, "getter".to_string(), child, true, false));
            } else if params.len() == 1 && name.starts_with("set") && name.len() > 3 {
                rows.push((
                    decap(&name[3..]),
                    parameter_type_of(params[0], source),
                    "setter".to_string(),
                    child,
                    false,
                    true,
                ));
            }
        }
    });
}

fn walk_all<'a>(node: Node<'a>, visit: &mut impl FnMut(Node<'a>)) {
    visit(node);
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        walk_all(child, visit);
    }
}

/// `_iter_java_files`.
fn iter_java_files(root: &Path, dir: &Path, out: &mut Vec<(std::path::PathBuf, String)>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(_) => return,
    };
    let mut subdirs: Vec<std::path::PathBuf> = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().to_string();
        if path.is_dir() {
            if !EXCLUDED_DIRS.contains(&name.as_str())
                && !name.starts_with('.')
                && !cortex_analyzer_framework::scan::matches_extra_ignore(&name)
            {
                subdirs.push(path);
            }
            continue;
        }
        if name.ends_with(".java") {
            let rel = path
                .strip_prefix(root)
                .unwrap_or(&path)
                .components()
                .map(|c| c.as_os_str().to_string_lossy().to_string())
                .collect::<Vec<_>>()
                .join("/");
            out.push((path, rel));
        }
    }
    for sub in subdirs {
        iter_java_files(root, &sub, out);
    }
}

fn rel_posix_of(rel_path: &str, root: &Path) -> String {
    // `_java_symbol_maps(path, root)` dùng os.path.relpath(path, root);
    // rel_path của mapper đã là posix rel — chỉ normalize lại.
    let _ = root;
    rel_path.replace('\\', "/")
}
