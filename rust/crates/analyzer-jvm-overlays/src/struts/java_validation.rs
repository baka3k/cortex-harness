//! Port `tools/struts/java_validation.py` — tìm `validate()` hook qua
//! tree-sitter-java (grammar pin 0.23.5 như analyzer-java).

use tree_sitter::{Parser, Tree};

use super::models::{Diagnostic, SourceSpan, ValidationData, ValidationRule};

const TYPE_NODES: [&str; 3] = ["class_declaration", "record_declaration", "enum_declaration"];

pub fn parse_java_bytes(source: &[u8]) -> Result<Tree, String> {
    let mut parser = Parser::new();
    parser
        .set_language(&tree_sitter_java::LANGUAGE.into())
        .map_err(|error| error.to_string())?;
    parser.parse(source, None).ok_or_else(|| "failed to parse java source".to_string())
}

fn node_text<'a>(node: tree_sitter::Node<'a>, source: &'a [u8]) -> String {
    source[node.start_byte()..node.end_byte()]
        .iter()
        .map(|&byte| byte as char)
        .collect()
}

/// `_node_text` Python decode utf-8 errors=replace — dùng lossy cho đúng.
fn node_text_lossy(node: tree_sitter::Node, source: &[u8]) -> String {
    String::from_utf8_lossy(&source[node.start_byte()..node.end_byte()]).to_string()
}

fn name_of(node: tree_sitter::Node, source: &[u8]) -> String {
    match node.child_by_field_name("name") {
        Some(name_node) => node_text_lossy(name_node, source).trim().to_string(),
        None => String::new(),
    }
}

pub fn parse_java_validation_hooks(root: &str, file_path: &str) -> ValidationData {
    let project_root = crate::pyutil::realpath(std::path::Path::new(root));
    let candidate = std::path::PathBuf::from(file_path);
    let absolute = if candidate.is_absolute() {
        crate::pyutil::realpath(&candidate)
    } else {
        crate::pyutil::realpath(&project_root.join(&candidate))
    };
    let Ok(relative_path) = absolute.strip_prefix(&project_root) else {
        return ValidationData {
            diagnostics: vec![Diagnostic::new(
                "struts.java.read_error",
                &format!("path escapes project root: {file_path}"),
                "error",
                file_path,
            )],
            ..Default::default()
        };
    };
    let relative = crate::pyutil::to_posix(relative_path);
    let Ok(source) = std::fs::read(&absolute) else {
        return ValidationData {
            diagnostics: vec![Diagnostic::new(
                "struts.java.read_error",
                &format!("unable to read file: {file_path}"),
                "error",
                file_path,
            )],
            ..Default::default()
        };
    };
    let tree = match parse_java_bytes(&source) {
        Ok(tree) => tree,
        Err(error) => {
            return ValidationData {
                diagnostics: vec![Diagnostic::new(
                    "struts.java.parser_unavailable",
                    &error,
                    "warning",
                    &relative,
                )],
                ..Default::default()
            };
        }
    };

    let mut diagnostics: Vec<Diagnostic> = Vec::new();
    if tree.root_node().has_error() {
        diagnostics.push(Diagnostic::new(
            "struts.java.parse_error",
            "Java source contains Tree-sitter parse errors",
            "warning",
            &relative,
        ));
    }

    let mut rules: Vec<ValidationRule> = Vec::new();
    walk(tree.root_node(), &source, &relative, &[], &mut |node, source, relative, stack| {
        if node.kind() == "method_declaration" && !stack.is_empty() && name_of(node, source) == "validate" {
            rules.push(ValidationRule {
                target: stack[stack.len() - 1].clone(),
                method: String::new(),
                validator_type: "validate_method".to_string(),
                field_name: String::new(),
                message: "Java validate() hook".to_string(),
                message_key: String::new(),
                params: ParamsEmpty::new(),
                source: SourceSpan {
                    file_path: relative.to_string(),
                    start_line: node.start_position().row as i64 + 1,
                    end_line: node.end_position().row as i64 + 1,
                    ..Default::default()
                },
            });
        }
    });
    ValidationData {
        rules,
        diagnostics,
    }
}

type ParamsEmpty = std::collections::BTreeMap<String, String>;

fn walk(
    node: tree_sitter::Node,
    source: &[u8],
    relative: &str,
    type_stack: &[String],
    visit: &mut impl FnMut(tree_sitter::Node, &[u8], &str, &[String]),
) {
    let mut next_stack: Vec<String> = type_stack.to_vec();
    if TYPE_NODES.contains(&node.kind()) {
        let type_name = name_of(node, source);
        if !type_name.is_empty() {
            next_stack.push(type_name);
        }
    }
    visit(node, source, relative, &next_stack);
    let mut cursor = node.walk();
    for child in node.children(&mut cursor) {
        walk(child, source, relative, &next_stack, visit);
    }
}

/// `node_text` giữ cho API compat (dùng bytes decode ascii char map).
#[allow(dead_code)]
fn unused_text(node: tree_sitter::Node, source: &[u8]) -> String {
    node_text(node, source)
}
