//! Port `tools/servlet_jsp/java_identity.py` — canonical Class/Function ID
//! index build từ base java analyzer semantics (tree-sitter-java 0.23.5, cùng
//! grammar với analyzer-java).

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use crate::struts::java_validation::parse_java_bytes;

#[derive(Debug, Clone, Default)]
pub struct JavaIdentityIndex {
    pub file_path: String,
    pub classes_by_fqcn: BTreeMap<String, String>,
    pub methods_exact: BTreeMap<(String, String, i64, i64), String>,
    pub methods_by_signature: BTreeMap<(String, String, i64), Vec<String>>,
}

impl JavaIdentityIndex {
    pub fn class_id(&self, fqcn: &str) -> String {
        self.classes_by_fqcn.get(fqcn).cloned().unwrap_or_default()
    }

    /// `method_id` — trả (id, status) với status ∈ exact|unique_signature|ambiguous|missing.
    pub fn method_id(&self, class_name: &str, name: &str, arity: i64, start_line: Option<i64>) -> (String, String) {
        if let Some(line) = start_line
            && let Some(exact) = self.methods_exact.get(&(class_name.to_string(), name.to_string(), arity, line))
        {
            return (exact.clone(), "exact".to_string());
        }
        let candidates = self
            .methods_by_signature
            .get(&(class_name.to_string(), name.to_string(), arity))
            .cloned()
            .unwrap_or_default();
        if candidates.len() == 1 {
            return (candidates[0].clone(), "unique_signature".to_string());
        }
        if !candidates.is_empty() {
            return (String::new(), "ambiguous".to_string());
        }
        (String::new(), "missing".to_string())
    }
}

pub struct JavaIdentityProvider {
    root: PathBuf,
    cache: Option<(PathBuf, JavaIdentityIndex)>,
}

impl JavaIdentityProvider {
    pub fn new(root: &Path) -> Self {
        Self {
            root: crate::pyutil::realpath(root),
            cache: None,
        }
    }

    pub fn index_file(&mut self, path: &str) -> Result<JavaIdentityIndex, String> {
        let absolute = if Path::new(path).is_absolute() {
            PathBuf::from(path)
        } else {
            self.root.join(path)
        };
        if let Some((cached_path, cached)) = &self.cache
            && cached_path == &absolute
        {
            return Ok(cached.clone());
        }
        let relative = absolute
            .strip_prefix(&self.root)
            .map(crate::pyutil::to_posix)
            .map_err(|error| error.to_string())?;
        let source = std::fs::read(&absolute).map_err(|error| error.to_string())?;
        let tree = parse_java_bytes(&source)?;
        let package_name = identity_package_name(tree.root_node(), &source);
        let mut index = JavaIdentityIndex {
            file_path: relative.clone(),
            ..Default::default()
        };

        // Pass 1: class declarations (named) — class_id = pkg.Outer.Inner.
        {
            let mut stack: Vec<(tree_sitter::Node, Vec<String>)> = vec![(tree.root_node(), Vec::new())];
            while let Some((node, class_stack)) = stack.pop() {
                if class_kind_of(node.kind()).is_some() {
                    let class_name = name_field(&node, &source);
                    let mut next_stack = class_stack.clone();
                    if !class_name.is_empty() {
                        next_stack.push(class_name);
                        let class_path = next_stack.join(".");
                        let qualified = qualified_join(&package_name, &class_path);
                        index
                            .classes_by_fqcn
                            .entry(qualified.clone())
                            .or_insert(qualified);
                    }
                    let mut cursor = node.walk();
                    for child in node.children(&mut cursor) {
                        stack.push((child, next_stack.clone()));
                    }
                    continue;
                }
                let mut cursor = node.walk();
                for child in node.children(&mut cursor) {
                    stack.push((child, class_stack.clone()));
                }
            }
        }

        // Pass 2: methods + constructors — symbol_id = pkg.C.m/arity@rel_path.
        {
            let mut stack: Vec<(tree_sitter::Node, Vec<String>)> = vec![(tree.root_node(), Vec::new())];
            while let Some((node, class_stack)) = stack.pop() {
                if class_kind_of(node.kind()).is_some() {
                    let class_name = name_field(&node, &source);
                    let mut next_stack = class_stack.clone();
                    if !class_name.is_empty() {
                        next_stack.push(class_name);
                    }
                    let mut cursor = node.walk();
                    for child in node.children(&mut cursor) {
                        stack.push((child, next_stack.clone()));
                    }
                    continue;
                }
                if node.kind() == "object_creation_expression" {
                    // Anonymous class — stack thêm Anonymous@line:col.
                    if let Some(class_body) = node.children(&mut node.walk()).find(|child| child.kind() == "class_body") {
                        let anonymous = format!(
                            "Anonymous@{}:{}",
                            node.start_position().row + 1,
                            node.start_position().column + 1
                        );
                        let mut next_stack = class_stack.clone();
                        next_stack.push(anonymous);
                        let mut cursor = class_body.walk();
                        for child in class_body.children(&mut cursor) {
                            stack.push((child, next_stack.clone()));
                        }
                        continue;
                    }
                }
                if node.kind() == "method_declaration" || node.kind() == "constructor_declaration" {
                    let method_name = name_field(&node, &source);
                    if !method_name.is_empty() {
                        let arity = java_arity(node);
                        let class_name = if class_stack.is_empty() {
                            String::new()
                        } else {
                            class_stack.join(".")
                        };
                        let qualified = qualified_join3(&package_name, &class_name, &method_name);
                        let symbol_id = format!("{qualified}/{arity}@{}", index.file_path);
                        let start_line = node.start_position().row as i64 + 1;
                        index.methods_exact.insert(
                            (class_name.clone(), method_name.clone(), arity, start_line),
                            symbol_id.clone(),
                        );
                        index
                            .methods_by_signature
                            .entry((class_name, method_name, arity))
                            .or_default()
                            .push(symbol_id);
                    }
                }
                let mut cursor = node.walk();
                for child in node.children(&mut cursor) {
                    stack.push((child, class_stack.clone()));
                }
            }
        }
        for values in index.methods_by_signature.values_mut() {
            values.sort();
        }
        self.cache = Some((absolute, index.clone()));
        Ok(index)
    }
}

fn class_kind_of(kind: &str) -> Option<&'static str> {
    match kind {
        "class_declaration" => Some("class"),
        "interface_declaration" => Some("interface"),
        "enum_declaration" => Some("enum"),
        "record_declaration" => Some("record"),
        _ => None,
    }
}

fn name_field(node: &tree_sitter::Node, source: &[u8]) -> String {
    node.child_by_field_name("name")
        .map(|name| String::from_utf8_lossy(&source[name.start_byte()..name.end_byte()]).trim().to_string())
        .unwrap_or_default()
}

fn java_arity(method_node: tree_sitter::Node) -> i64 {
    let Some(parameters) = method_node.child_by_field_name("parameters") else {
        return 0;
    };
    parameters
        .children(&mut parameters.walk())
        .filter(|child| child.kind() == "formal_parameter")
        .count() as i64
}

fn qualified_join(package: &str, class_path: &str) -> String {
    if package.is_empty() {
        class_path.to_string()
    } else {
        format!("{package}.{class_path}")
    }
}

fn qualified_join3(package: &str, class_name: &str, method: &str) -> String {
    let mut parts: Vec<&str> = Vec::new();
    if !package.is_empty() {
        parts.push(package);
    }
    if !class_name.is_empty() {
        parts.push(class_name);
    }
    parts.push(method);
    parts.join(".")
}

fn identity_package_name(root: tree_sitter::Node, source: &[u8]) -> String {
    for child in root.children(&mut root.walk()) {
        if child.kind() == "package_declaration" {
            let text = String::from_utf8_lossy(&source[child.start_byte()..child.end_byte()])
                .trim()
                .to_string();
            let text = text.strip_prefix("package").unwrap_or(&text).trim();
            return text.trim_end_matches(';').trim().to_string();
        }
    }
    String::new()
}
