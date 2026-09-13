//! Port của `tools/perl/parser_runtime.py` — capability contract + error
//! node collection cho grammar tree-sitter-perl v1.2.1 (vendored).

use tree_sitter::{Node, Tree};

use crate::grammar;
use crate::models::{ParserCapabilities, SUPPORTED_EXTENSIONS};

pub const GRAMMAR_PACKAGE: &str = "tree-sitter-perl";
/// `metadata.version("tree-sitter-perl")` phía Python reference — phiên bản
/// grammar vendored trùng khớp.
pub const GRAMMAR_VERSION: &str = "1.2.1";
/// `metadata.version("tree-sitter")` phía Python reference (py-tree-sitter
/// 0.26.0). Chỉ đi vào `capabilities.runtime_version` (payload JSON + cache
/// fingerprint), không vào graph rows — chốt contract với reference runtime.
pub const RUNTIME_VERSION: &str = "0.26.0";
pub const MIN_GRAMMAR_ABI: usize = 14;
pub const REQUIRED_NODE_TYPES: [&str; 13] = [
    "source_file",
    "package_statement",
    "subroutine_declaration_statement",
    "variable_declaration",
    "localization_expression",
    "use_statement",
    "require_expression",
    "function_call_expression",
    "method_call_expression",
    "coderef_call_expression",
    "comment",
    "pod",
    "data_section",
];

/// `capabilities()` — lru_cache phía Python; Rust tính mỗi lần (rẻ, dữ liệu
/// tĩnh từ TSLanguage).
pub fn capabilities() -> Result<ParserCapabilities, String> {
    let language = grammar::language();
    let abi = language.abi_version();
    if abi < MIN_GRAMMAR_ABI {
        return Err(format!(
            "Unsupported Perl grammar ABI {abi}; expected ABI {MIN_GRAMMAR_ABI} or newer."
        ));
    }
    let semantic_text = language
        .metadata()
        .map(|metadata| {
            format!(
                "{}.{}.{}",
                metadata.major_version, metadata.minor_version, metadata.patch_version
            )
        })
        .unwrap_or_else(|| "unknown".to_string());
    Ok(ParserCapabilities {
        analyzer_version: crate::models::ANALYZER_VERSION.to_string(),
        runtime_package: "tree-sitter".to_string(),
        runtime_version: RUNTIME_VERSION.to_string(),
        grammar_package: GRAMMAR_PACKAGE.to_string(),
        grammar_version: GRAMMAR_VERSION.to_string(),
        grammar_abi: abi,
        grammar_semantic_version: semantic_text,
        language_name: language.name().unwrap_or("perl").to_string(),
        extensions: SUPPORTED_EXTENSIONS.iter().map(|s| s.to_string()).collect(),
        supported_nodes: REQUIRED_NODE_TYPES.iter().map(|s| s.to_string()).collect(),
    })
}

/// `parser_runtime.new_parser()`.
pub fn new_parser() -> tree_sitter::Parser {
    let mut parser = tree_sitter::Parser::new();
    let ok = parser.set_language(&grammar::language()).is_ok();
    debug_assert!(ok, "vendored grammar must be ABI-compatible");
    parser
}

/// `parser_runtime.error_nodes` — preorder DFS (stack + reversed children),
/// node `ERROR` / is_error / is_missing.
pub fn error_nodes(root: Node<'_>) -> Vec<Node<'_>> {
    let mut found = Vec::new();
    let mut stack = vec![root];
    while let Some(current) = stack.pop() {
        if current.is_error() || current.is_missing() {
            found.push(current);
        }
        let mut children: Vec<Node<'_>> =
            current.children(&mut current.walk()).collect();
        children.reverse();
        stack.extend(children);
    }
    found
}

/// Parse bytes → tree (helper dùng chung bởi parser.rs).
pub fn parse(source: &[u8]) -> Tree {
    let mut parser = new_parser();
    parser.parse(source, None).expect("tree-sitter parse")
}

#[cfg(test)]
mod tests {
    #[test]
    fn capabilities_match_python_reference() {
        let caps = super::capabilities().expect("capabilities");
        assert_eq!(caps.grammar_version, "1.2.1");
        assert_eq!(caps.grammar_abi, 15);
        assert_eq!(caps.grammar_semantic_version, "1.0.0");
        assert_eq!(caps.language_name, "perl");
        assert_eq!(caps.extensions, vec![".pl", ".pm", ".t"]);
    }

    #[test]
    fn parses_and_finds_error_nodes() {
        let source = b"package App::Broken;\nsub unfinished {\n    my $value = (\n\n1;\n";
        let tree = super::parse(source);
        let errors = super::error_nodes(tree.root_node());
        assert!(!errors.is_empty(), "broken snippet must yield error nodes");
    }
}
