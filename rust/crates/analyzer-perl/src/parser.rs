//! Port của `tools/perl/perl_parser.py` — scope-aware Perl extraction trên
//! tree-sitter (grammar v1.2.1 vendored trùng wheel Python reference).
//!
//! Regex notes:
//! * `_STATIC_MODULE_RE` — fullmatch ⇒ neo `\A..\z` với crate `regex`.
//! * `_VARIABLE_RE` — dùng negative lookbehind `(?<![A-Za-z0-9_])` mà crate
//!   `regex` KHÔNG hỗ trợ ⇒ port thủ công bằng scanner ký tự trong
//!   [`variables::find_variable_names`] (semantics findall leftmost-first,
//!   xem doc của hàm).

use std::collections::{BTreeMap, BTreeSet};

use regex::Regex;
use tree_sitter::Node;

use crate::models::{
    redact_text, stable_id, Diagnostic, DocumentationRecord, FileRecord, ImportRecord, ParsedFile,
    ReferenceRecord, SourceSpan, SymbolRecord, ANALYZER_VERSION,
};
use crate::runtime::{self, capabilities};

const CALL_NODE_TYPES: [&str; 5] = [
    "function_call_expression",
    "ambiguous_function_call_expression",
    "method_call_expression",
    "coderef_call_expression",
    "func0op_call_expression",
];
const CONDITIONAL_ANCESTORS: [&str; 6] = [
    "if_statement",
    "unless_statement",
    "conditional_expression",
    "while_statement",
    "for_statement",
    "given_statement",
];

/// Port thủ công của `_VARIABLE_RE`:
/// `(?<![A-Za-z0-9_])(?:(\$(?:[A-Za-z_][A-Za-z0-9_:]*|[^A-Za-z0-9_\s]))|([@%][A-Za-z_][A-Za-z0-9_:]*))`
///
/// Lookbehind `(?<![A-Za-z0-9_])` không được crate `regex` hỗ trợ nên scan
/// tay: tại vị trí i (char index), match chỉ khả thi khi i == 0 hoặc ký tự
/// trước i không thuộc [A-Za-z0-9_]; sau đó thử match alternative trái-trước
/// exactly như `re.findall` trên `str` (non-overlapping, tiêu thụ match).
pub mod variables {
    use std::collections::HashSet;

    fn is_word_char(c: char) -> bool {
        c.is_ascii_alphanumeric() || c == '_'
    }

    /// Kết quả match tại 1 vị trí: (tên biến gồm sigil, số ký tự tiêu thụ).
    fn match_at(chars: &[char]) -> Option<(String, usize)> {
        let first = *chars.first()?;
        match first {
            '$' => {
                if let Some(&second) = chars.get(1)
                    && (second.is_ascii_alphabetic() || second == '_')
                {
                    let mut end = 2;
                    while end < chars.len()
                        && (chars[end].is_ascii_alphanumeric() || chars[end] == '_' || chars[end] == ':')
                    {
                        end += 1;
                    }
                    return Some((chars[..end].iter().collect(), end));
                }
                // `\$(?:...|[^A-Za-z0-9_\s])` — đúng 1 ký tự không word/space.
                if let Some(&second) = chars.get(1)
                    && !is_word_char(second)
                    && !second.is_whitespace()
                {
                    return Some((format!("${second}"), 2));
                }
                None
            }
            '@' | '%' => {
                if let Some(&second) = chars.get(1)
                    && (second.is_ascii_alphabetic() || second == '_')
                {
                    let mut end = 2;
                    while end < chars.len()
                        && (chars[end].is_ascii_alphanumeric() || chars[end] == '_' || chars[end] == ':')
                    {
                        end += 1;
                    }
                    return Some((chars[..end].iter().collect(), end));
                }
                None
            }
            _ => None,
        }
    }

    /// findall + `next((part for part in match if part), "")` ⇒ tên biến
    /// (toàn bộ match), giữ thứ tự xuất hiện, dedup kiểu dict.fromkeys.
    pub fn find_variable_names(text: &str) -> Vec<String> {
        let chars: Vec<char> = text.chars().collect();
        let mut names: Vec<String> = Vec::new();
        let mut seen: HashSet<String> = HashSet::new();
        let mut index = 0usize;
        while index < chars.len() {
            let previous_is_word = index > 0 && is_word_char(chars[index - 1]);
            if !previous_is_word
                && let Some((name, length)) = match_at(&chars[index..])
            {
                if seen.insert(name.clone()) {
                    names.push(name);
                }
                index += length;
                continue;
            }
            index += 1;
        }
        names
    }
}

fn static_module_re() -> &'static Regex {
    use std::sync::OnceLock;
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        // Python fullmatch ⇒ neo \A..\z.
        Regex::new(r"\A[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)*\z")
            .expect("static module regex")
    })
}

/// `_node_text` — decode utf-8 errors="replace" (CPython phát 1 U+FFFD per
/// invalid byte/subpart — đã verify trùng `String::from_utf8_lossy`).
fn node_text(node: Option<Node<'_>>, source: &[u8]) -> String {
    let Some(node) = node else {
        return String::new();
    };
    String::from_utf8_lossy(&source[node.start_byte()..node.end_byte()]).into_owned()
}

fn span_of(node: Node<'_>) -> SourceSpan {
    let start = node.start_position();
    let end = node.end_position();
    SourceSpan {
        start_line: start.row + 1,
        start_column: start.column + 1,
        end_line: end.row + 1,
        end_column: end.column + 1,
        start_byte: node.start_byte(),
        end_byte: node.end_byte(),
    }
}

/// Con (named + unnamed) — tương đương `node.children` py-tree-sitter.
fn children_of<'t>(node: Node<'t>) -> Vec<Node<'t>> {
    let mut cursor = node.walk();
    node.children(&mut cursor).collect()
}

/// `_descendants` — preorder DFS, bao gồm chính node.
fn descendants<'t>(node: Node<'t>, node_types: &[&str]) -> Vec<Node<'t>> {
    let mut found = Vec::new();
    let mut stack = vec![node];
    while let Some(current) = stack.pop() {
        if node_types.contains(&current.kind()) {
            found.push(current);
        }
        let mut children = children_of(current);
        children.reverse();
        stack.extend(children);
    }
    found
}

/// `_first_descendant` — preorder DFS KHÔNG bao gồm chính node.
fn first_descendant<'t>(node: Node<'t>, node_types: &[&str]) -> Option<Node<'t>> {
    let mut stack: Vec<Node<'t>> = {
        let mut children = children_of(node);
        children.reverse();
        children
    };
    while let Some(current) = stack.pop() {
        if node_types.contains(&current.kind()) {
            return Some(current);
        }
        let mut children = children_of(current);
        children.reverse();
        stack.extend(children);
    }
    None
}

fn field_or_descendant<'t>(
    node: Node<'t>,
    field: &str,
    node_types: &[&str],
) -> Option<Node<'t>> {
    node.child_by_field_name(field).or_else(|| first_descendant(node, node_types))
}

fn is_conditional(mut node: Node<'_>) -> bool {
    while let Some(parent) = node.parent() {
        if CONDITIONAL_ANCESTORS.contains(&parent.kind()) {
            return true;
        }
        node = parent;
    }
    false
}

/// `_normalized_package` — xoá mọi whitespace (Python `\s` trên str);
/// rỗng ⇒ "main".
fn normalized_package(text: &str) -> String {
    let cleaned: String = text.chars().filter(|c| !c.is_whitespace()).collect();
    if cleaned.is_empty() {
        "main".to_string()
    } else {
        cleaned
    }
}

fn subroutine_arity(node: Node<'_>, source: &[u8]) -> usize {
    if let Some(signature) = first_descendant(node, &["signature"]) {
        return descendants(signature, &["mandatory_parameter", "optional_parameter", "slurpy_parameter"]).len();
    }
    let Some(prototype) = first_descendant(node, &["prototype"]) else {
        return 0;
    };
    let text = node_text(Some(prototype), source);
    text.matches(['$', '@', '%', '*', '&']).count()
}

fn leading_documentation(node: Node<'_>, source: &[u8], limit: usize) -> String {
    let mut parts: Vec<String> = Vec::new();
    let mut sibling = node.prev_named_sibling();
    while let Some(current) = sibling {
        if current.kind() != "comment" && current.kind() != "pod" {
            break;
        }
        parts.push(node_text(Some(current), source).trim().to_string());
        sibling = current.prev_named_sibling();
    }
    parts.reverse();
    redact_text(&parts.join("\n"), limit)
}

#[derive(Debug)]
struct Collector<'a> {
    project_id: String,
    file_path: String,
    source: &'a [u8],
    include_docs: bool,
    max_snippet_chars: usize,
    max_doc_chars: usize,
    symbols: Vec<SymbolRecord>,
    imports: Vec<ImportRecord>,
    references: Vec<ReferenceRecord>,
    documentation: Vec<DocumentationRecord>,
    diagnostics: Vec<Diagnostic>,
}

impl<'a> Collector<'a> {
    fn symbol_id(&self, package: &str, scope: &str, kind: &str, fq_name: &str) -> String {
        stable_id(
            &self.project_id,
            &self.file_path,
            package,
            scope,
            kind,
            fq_name,
        )
    }

    fn add_package(&mut self, node: Node<'_>, package: &str) -> SymbolRecord {
        let symbol = SymbolRecord {
            symbol_id: self.symbol_id(package, "", "package", package),
            name: package.rsplit("::").next().unwrap_or(package).to_string(),
            kind: "package".to_string(),
            fq_name: package.to_string(),
            file_path: self.file_path.clone(),
            span: span_of(node),
            package: package.to_string(),
            scope: String::new(),
            declaration_kind: String::new(),
            arity: 0,
            prototype: String::new(),
            attributes: Vec::new(),
            code: redact_text(&node_text(Some(node), self.source), self.max_snippet_chars),
            documentation: if self.include_docs {
                leading_documentation(node, self.source, self.max_doc_chars)
            } else {
                String::new()
            },
        };
        self.symbols.push(symbol.clone());
        symbol
    }

    fn add_subroutine(
        &mut self,
        node: Node<'_>,
        package: &str,
        outer_scope: &[String],
    ) -> SymbolRecord {
        let name_node = field_or_descendant(node, "name", &["bareword"]);
        let line = node.start_position().row + 1;
        let name = {
            let stripped = node_text(name_node, self.source).trim().to_string();
            if stripped.is_empty() {
                format!("anonymous@L{line}")
            } else {
                stripped
            }
        };
        let qualified = if name.contains("::") {
            name.clone()
        } else {
            format!("{package}::{name}")
        };
        let scope = {
            let mut parts = vec![package.to_string()];
            parts.extend(outer_scope.iter().cloned());
            parts.join("::")
        };
        let prototype_node = first_descendant(node, &["prototype", "signature"]);
        let attributes_node = node
            .child_by_field_name("attributes")
            .or_else(|| first_descendant(node, &["attrlist"]));
        let mut attribute_set: BTreeSet<String> = BTreeSet::new();
        if let Some(attributes_node) = attributes_node {
            for item in descendants(attributes_node, &["attribute"]) {
                let stripped = node_text(Some(item), self.source).trim().to_string();
                if !stripped.is_empty() {
                    attribute_set.insert(stripped);
                }
            }
        }
        let attributes: Vec<String> = attribute_set.into_iter().collect();
        let symbol = SymbolRecord {
            symbol_id: self.symbol_id(package, &scope, "subroutine", &qualified),
            name,
            kind: "subroutine".to_string(),
            fq_name: qualified,
            file_path: self.file_path.clone(),
            span: span_of(node),
            package: package.to_string(),
            scope,
            declaration_kind: "named".to_string(),
            arity: subroutine_arity(node, self.source),
            prototype: node_text(prototype_node, self.source).trim().to_string(),
            attributes,
            code: redact_text(&node_text(Some(node), self.source), self.max_snippet_chars),
            documentation: if self.include_docs {
                leading_documentation(node, self.source, self.max_doc_chars)
            } else {
                String::new()
            },
        };
        self.symbols.push(symbol.clone());
        symbol
    }

    fn add_variables(
        &mut self,
        node: Node<'_>,
        package: &str,
        scope: &[String],
        declaration_kind: &str,
    ) {
        let raw = node_text(Some(node), self.source);
        let line = node.start_position().row + 1;
        let names = variables::find_variable_names(&raw);
        for name in names {
            let (fq_name, scope_text, scope_kind) = if declaration_kind == "our" {
                (
                    format!("{package}::{name}"),
                    package.to_string(),
                    "package".to_string(),
                )
            } else {
                let mut parts = vec![package.to_string()];
                parts.extend(scope.iter().cloned());
                let scope_text = parts.join("::");
                let scope_kind = if declaration_kind == "local" {
                    "dynamic-local"
                } else {
                    "lexical"
                };
                (
                    format!("{scope_text}::{name}@L{line}"),
                    scope_text,
                    scope_kind.to_string(),
                )
            };
            self.symbols.push(SymbolRecord {
                symbol_id: self.symbol_id(package, &scope_text, "variable", &fq_name),
                name,
                kind: "variable".to_string(),
                fq_name,
                file_path: self.file_path.clone(),
                span: span_of(node),
                package: package.to_string(),
                scope: scope_text,
                declaration_kind: format!("{declaration_kind}:{scope_kind}"),
                arity: 0,
                prototype: String::new(),
                attributes: Vec::new(),
                code: redact_text(&raw, self.max_snippet_chars),
                documentation: String::new(),
            });
        }
    }

    fn add_use(&mut self, node: Node<'_>, package: &str) {
        let raw = node_text(Some(node), self.source).trim().to_string();
        let first_token = children_of(node)
            .into_iter()
            .find(|child| child.kind() == "use" || child.kind() == "no")
            .map_or("use", |child| child.kind());
        let module_node = node.child_by_field_name("module");
        let module = if let Some(module_node) = module_node {
            normalized_package(&node_text(Some(module_node), self.source))
        } else {
            String::new()
        };
        let is_dynamic = module.is_empty() || !static_module_re().is_match(&module);
        let line = node.start_position().row + 1;
        let import_id = self.symbol_id(
            package,
            package,
            "import",
            &format!(
                "{first_token}:{}@L{line}",
                if module.is_empty() { &raw } else { &module }
            ),
        );
        self.imports.push(ImportRecord {
            import_id,
            kind: first_token.to_string(),
            module,
            raw_text: redact_text(&raw, self.max_snippet_chars),
            file_path: self.file_path.clone(),
            span: span_of(node),
            is_dynamic,
            is_conditional: is_conditional(node),
            resolved_path: String::new(),
        });
    }

    fn add_require(&mut self, node: Node<'_>, package: &str) {
        let raw = node_text(Some(node), self.source).trim().to_string();
        let mut target = raw
            .strip_prefix("require")
            .unwrap_or(&raw)
            .trim()
            .trim_end_matches(';')
            .trim()
            .to_string();
        // Python: target.strip("'\"") — bỏ leading/trailing ' và ".
        target = target
            .trim_matches(|c| c == '\'' || c == '"')
            .to_string();
        let is_static = static_module_re().is_match(&target);
        let line = node.start_position().row + 1;
        let import_id = self.symbol_id(
            package,
            package,
            "import",
            &format!(
                "require:{}@L{line}",
                if target.is_empty() { &raw } else { &target }
            ),
        );
        self.imports.push(ImportRecord {
            import_id,
            kind: "require".to_string(),
            module: if is_static { target } else { String::new() },
            raw_text: redact_text(&raw, self.max_snippet_chars),
            file_path: self.file_path.clone(),
            span: span_of(node),
            is_dynamic: !is_static,
            is_conditional: is_conditional(node),
            resolved_path: String::new(),
        });
    }

    fn add_reference(
        &mut self,
        node: Node<'_>,
        package: &str,
        active_subroutine: Option<&SymbolRecord>,
    ) {
        let raw = node_text(Some(node), self.source).trim().to_string();
        let source_id = active_subroutine
            .map(|subroutine| subroutine.symbol_id.clone())
            .unwrap_or_default();
        let source_name = active_subroutine
            .map(|subroutine| subroutine.fq_name.clone())
            .unwrap_or_else(|| package.to_string());
        let target_name;
        let kind: &str;
        let mut confidence = 0.1;
        let mut status = "dynamic";
        let reason;

        if matches!(
            node.kind(),
            "function_call_expression"
                | "ambiguous_function_call_expression"
                | "func0op_call_expression"
        ) {
            let function_node = node
                .child_by_field_name("function")
                .or_else(|| first_descendant(node, &["function", "bareword"]));
            let candidate = node_text(function_node, self.source).trim().to_string();
            if candidate.is_empty() {
                return;
            }
            target_name = candidate;
            kind = if target_name.contains("::") { "qualified" } else { "direct" };
            confidence = if kind == "qualified" { 1.0 } else { 0.8 };
            status = "unresolved";
            reason = "awaiting project-local resolution";
        } else if node.kind() == "method_call_expression" {
            let method_node = node
                .child_by_field_name("method")
                .or_else(|| first_descendant(node, &["method"]));
            let invocant_node = node.child_by_field_name("invocant");
            let method = node_text(method_node, self.source).trim().to_string();
            let invocant = node_text(invocant_node, self.source).trim().to_string();
            target_name = if !invocant.is_empty() {
                format!("{invocant}->{method}")
            } else {
                method
            };
            kind = "method";
            confidence = 0.35;
            status = "unresolved";
            reason = "Perl method dispatch is runtime-dependent";
        } else if node.kind() == "coderef_call_expression" {
            target_name = raw.split("->").next().unwrap_or("").trim().to_string();
            kind = "coderef";
            reason = "coderef target is dynamic";
        } else {
            return;
        }

        if target_name.is_empty() {
            return;
        }
        let start = node.start_position();
        let ref_name = format!(
            "{source_name}->{target_name}:{kind}@{}:{}",
            start.row + 1,
            start.column + 1
        );
        self.references.push(ReferenceRecord {
            ref_id: self.symbol_id(package, &source_name, "reference", &ref_name),
            source_symbol_id: source_id,
            source_name,
            target_name,
            kind: kind.to_string(),
            file_path: self.file_path.clone(),
            span: span_of(node),
            confidence,
            resolution_status: status.to_string(),
            target_symbol_id: String::new(),
            reason: reason.to_string(),
            raw_text: redact_text(&raw, self.max_snippet_chars),
        });
    }

    fn add_eval_reference(
        &mut self,
        node: Node<'_>,
        package: &str,
        active_subroutine: Option<&SymbolRecord>,
    ) {
        let source_id = active_subroutine
            .map(|subroutine| subroutine.symbol_id.clone())
            .unwrap_or_default();
        let source_name = active_subroutine
            .map(|subroutine| subroutine.fq_name.clone())
            .unwrap_or_else(|| package.to_string());
        let raw = node_text(Some(node), self.source).trim().to_string();
        let start = node.start_position();
        self.references.push(ReferenceRecord {
            ref_id: self.symbol_id(
                package,
                &source_name,
                "reference",
                &format!("eval@{}:{}", start.row + 1, start.column + 1),
            ),
            source_symbol_id: source_id,
            source_name,
            target_name: "eval".to_string(),
            kind: "eval".to_string(),
            file_path: self.file_path.clone(),
            span: span_of(node),
            confidence: 0.0,
            resolution_status: "dynamic".to_string(),
            target_symbol_id: String::new(),
            reason: "eval is never statically resolved".to_string(),
            raw_text: redact_text(&raw, self.max_snippet_chars),
        });
    }

    fn visit(
        &mut self,
        node: Node<'_>,
        package: &str,
        scope: &[String],
        active_subroutine: Option<&SymbolRecord>,
    ) {
        if node.kind() == "subroutine_declaration_statement" {
            let subroutine = self.add_subroutine(node, package, scope);
            let mut inner_scope = scope.to_vec();
            inner_scope.push(subroutine.name.clone());
            for child in children_of(node) {
                self.visit(child, package, &inner_scope, Some(&subroutine));
            }
            return;
        }
        if node.kind() == "variable_declaration" {
            let raw = node_text(Some(node), self.source);
            let declaration = if raw.trim_start().starts_with("our") {
                "our"
            } else {
                "my"
            };
            self.add_variables(node, package, scope, declaration);
        } else if node.kind() == "localization_expression" {
            self.add_variables(node, package, scope, "local");
        } else if node.kind() == "use_statement" {
            self.add_use(node, package);
        } else if node.kind() == "require_expression" {
            self.add_require(node, package);
        } else if CALL_NODE_TYPES.contains(&node.kind()) {
            self.add_reference(node, package, active_subroutine);
        } else if node.kind() == "eval_expression" {
            self.add_eval_reference(node, package, active_subroutine);
        }

        for child in children_of(node) {
            self.visit(child, package, scope, active_subroutine);
        }
    }
}

/// `PerlTreeSitterParser` — trích xuất structural facts cho 1 file Perl.
#[derive(Debug, Clone)]
pub struct PerlTreeSitterParser {
    pub max_snippet_chars: usize,
    pub max_doc_chars: usize,
}

impl PerlTreeSitterParser {
    pub fn new(max_snippet_chars: i64, max_doc_chars: i64) -> Self {
        Self {
            max_snippet_chars: max_snippet_chars.max(0) as usize,
            max_doc_chars: max_doc_chars.max(0) as usize,
        }
    }

    pub fn parse_bytes(
        &self,
        project_id: &str,
        file_path: &str,
        source: &[u8],
        include_docs: bool,
        truncated: bool,
    ) -> ParsedFile {
        let tree = runtime::parse(source);
        let root = tree.root_node();
        let grammar = capabilities().expect("vendored grammar capabilities");
        let errors = runtime::error_nodes(root);
        let mut diagnostics: Vec<Diagnostic> = Vec::new();
        if let Err(error) = std::str::from_utf8(source) {
            // Python: details=(("start", str(exc.start)), ("end", str(exc.end)))
            // — byte offsets của maximal invalid subpart.
            let start = error.valid_up_to();
            let end = start + error.error_len().unwrap_or(source.len() - start);
            diagnostics.push(Diagnostic {
                code: "perl.encoding.invalid_utf8".to_string(),
                severity: "warning".to_string(),
                message: "Invalid UTF-8 bytes were replaced in extracted text.".to_string(),
                file_path: file_path.to_string(),
                span: None,
                details: vec![
                    ("start".to_string(), start.to_string()),
                    ("end".to_string(), end.to_string()),
                ],
            });
        }
        for node in errors.iter().take(100) {
            diagnostics.push(Diagnostic {
                code: "perl.parser.error_node".to_string(),
                severity: "warning".to_string(),
                message: "Tree-sitter recovered from malformed or unsupported Perl syntax."
                    .to_string(),
                file_path: file_path.to_string(),
                span: Some(span_of(*node)),
                details: vec![("node_type".to_string(), node.kind().to_string())],
            });
        }
        if errors.len() > 100 {
            diagnostics.push(Diagnostic {
                code: "perl.parser.error_budget".to_string(),
                severity: "warning".to_string(),
                message: format!(
                    "Parser diagnostics truncated after 100 of {} errors.",
                    errors.len()
                ),
                file_path: file_path.to_string(),
                span: None,
                details: Vec::new(),
            });
        }

        let mut collector = Collector {
            project_id: project_id.to_string(),
            file_path: file_path.to_string(),
            source,
            include_docs,
            max_snippet_chars: self.max_snippet_chars,
            max_doc_chars: self.max_doc_chars,
            symbols: Vec::new(),
            imports: Vec::new(),
            references: Vec::new(),
            documentation: Vec::new(),
            diagnostics,
        };
        let mut package = "main".to_string();
        let mut package_seen = false;
        for child in children_of(root) {
            if child.kind() == "package_statement" {
                let name_node = child
                    .child_by_field_name("name")
                    .or_else(|| first_descendant(child, &["package"]));
                package = normalized_package(&node_text(name_node, source));
                collector.add_package(child, &package);
                package_seen = true;
                continue;
            }
            if include_docs && (child.kind() == "comment" || child.kind() == "pod") {
                let raw = node_text(Some(child), source);
                let bounded = redact_text(&raw, self.max_doc_chars);
                collector.documentation.push(DocumentationRecord {
                    kind: child.kind().to_string(),
                    text: bounded.clone(),
                    file_path: file_path.to_string(),
                    span: span_of(child),
                    truncated: raw.chars().count() > bounded.chars().count(),
                });
            }
            collector.visit(child, &package, &[], None);
        }

        if !package_seen && !source_is_blank(source) {
            collector.symbols.push(SymbolRecord {
                symbol_id: collector.symbol_id("main", "", "package", "main"),
                name: "main".to_string(),
                kind: "package".to_string(),
                fq_name: "main".to_string(),
                file_path: file_path.to_string(),
                span: SourceSpan {
                    start_line: 1,
                    start_column: 1,
                    end_line: 1,
                    end_column: 1,
                    start_byte: 0,
                    end_byte: 0,
                },
                package: "main".to_string(),
                scope: String::new(),
                declaration_kind: "implicit".to_string(),
                arity: 0,
                prototype: String::new(),
                attributes: Vec::new(),
                code: String::new(),
                documentation: String::new(),
            });
        }

        let coverage = if source.is_empty() {
            "empty"
        } else if !errors.is_empty() || !collector.diagnostics.is_empty() || truncated {
            "partial"
        } else {
            "complete"
        };
        let parse_status = if source.is_empty() {
            "empty"
        } else if coverage == "partial" {
            "partial"
        } else {
            "ok"
        };
        let file_record = FileRecord {
            file_path: file_path.to_string(),
            language: "perl".to_string(),
            parser_version: ANALYZER_VERSION.to_string(),
            grammar_version: grammar.grammar_version.clone(),
            parse_status: parse_status.to_string(),
            coverage: coverage.to_string(),
            content_sha256: sha256_hex(source),
            size_bytes: source.len(),
            line_count: source.iter().filter(|&&byte| byte == b'\n').count()
                + usize::from(!source.is_empty()),
            error_count: errors.len(),
            truncated,
        };

        let symbols = dedupe(
            collector
                .symbols
                .into_iter()
                .map(|item| ((item.file_path.clone(), item.span, item.symbol_id.clone()), item))
                .collect(),
        );
        let imports = dedupe(
            collector
                .imports
                .into_iter()
                .map(|item| ((item.file_path.clone(), item.span, item.import_id.clone()), item))
                .collect(),
        );
        let references = dedupe(
            collector
                .references
                .into_iter()
                .map(|item| ((item.file_path.clone(), item.span, item.ref_id.clone()), item))
                .collect(),
        );
        let documentation = dedupe(
            collector
                .documentation
                .into_iter()
                .map(|item| ((item.file_path.clone(), item.span, item.kind.clone()), item))
                .collect(),
        );
        collector.diagnostics.sort();
        ParsedFile {
            file: file_record,
            symbols,
            imports,
            references,
            documentation,
            diagnostics: collector.diagnostics,
        }
    }
}

/// Python `source.strip()` truthiness — bytes strip ASCII whitespace.
fn source_is_blank(source: &[u8]) -> bool {
    source
        .iter()
        .all(|byte| matches!(byte, b' ' | b'\t' | b'\n' | b'\r' | b'\x0b' | b'\x0c'))
}

fn sha256_hex(data: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    let digest = Sha256::digest(data);
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// `dedupe` — dict theo key rồi sort theo key (BTreeMap tự sắp).
fn dedupe<K: Ord, V>(entries: Vec<(K, V)>) -> Vec<V> {
    let mut map: BTreeMap<K, V> = BTreeMap::new();
    for (key, value) in entries {
        map.insert(key, value);
    }
    map.into_values().collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn variable_scan_matches_python_lookbehind_semantics() {
        // Expectations verify trực tiếp với re.findall phía Python.
        assert_eq!(
            variables::find_variable_names("my $value = 2; our $PKG; @list; %map; $1 $$x"),
            vec!["$value", "$PKG", "@list", "%map", "$$"]
        );
        // "a$b" — '$' đứng sau word char ⇒ bị lookbehind chặn (Python: []).
        assert_eq!(variables::find_variable_names("a$b c_$d"), Vec::<String>::new());
        // "$é" — ký tự unicode đơn (không word/space) vẫn match.
        assert_eq!(variables::find_variable_names("$é"), vec!["$é"]);
        // "$1" không match; "$_x" match.
        assert_eq!(variables::find_variable_names("$1 $_x"), vec!["$_x"]);
    }

    #[test]
    fn static_module_fullmatch() {
        assert!(static_module_re().is_match("App::Model"));
        assert!(static_module_re().is_match("strict"));
        assert!(!static_module_re().is_match("App::Model::"));
        assert!(!static_module_re().is_match("1App"));
        assert!(!static_module_re().is_match("App:: Model"));
    }

    #[test]
    fn parses_reference_fixture_shapes() {
        let parser = PerlTreeSitterParser::new(4000, 8000);
        let source = b"package App::Util;\n\nuse strict;\n\nsub helper {\n    return 42;\n}\n\n1;\n";
        let parsed = parser.parse_bytes("parity_perl", "lib/App/Util.pm", source, false, false);
        assert_eq!(parsed.file.error_count, 0);
        assert_eq!(parsed.file.coverage, "complete");
        assert_eq!(parsed.symbols.len(), 2, "1 package + 1 sub");
        assert_eq!(parsed.symbols[0].kind, "package");
        assert_eq!(parsed.imports.len(), 1);
    }
}
