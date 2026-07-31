//! Tree-sitter parser factory (thread-local singleton).
//!
//! Mirrors `_get_cpp_parser()` / `_get_c_parser()` in `cplus_analyzer.py`.
//! Each thread owns its own parser wrapped in `RefCell` for interior
//! mutability (Parser::parse takes `&mut self`).

use std::cell::RefCell;
use std::path::Path;
use tree_sitter::Parser;

/// Returns true if a path's extension suggests C++ over C.
pub fn is_cpp_path(path: &str) -> bool {
    match Path::new(path).extension().and_then(|e| e.to_str()) {
        Some(ext) => matches!(
            ext.to_ascii_lowercase().as_str(),
            "cpp" | "cc" | "cxx" | "hpp" | "hh" | "hxx"
        ),
        None => false,
    }
}

thread_local! {
    static CPP_PARSER: RefCell<Parser> = RefCell::new(build_cpp_parser());
    static C_PARSER: RefCell<Parser> = RefCell::new(build_c_parser());
}

fn build_cpp_parser() -> Parser {
    let language = tree_sitter_cpp::LANGUAGE;
    let mut parser = Parser::new();
    parser
        .set_language(&language.into())
        .expect("Failed to set tree-sitter C++ language");
    parser
}

fn build_c_parser() -> Parser {
    let language = tree_sitter_c::LANGUAGE;
    let mut parser = Parser::new();
    parser
        .set_language(&language.into())
        .expect("Failed to set tree-sitter C language");
    parser
}

/// Run `f` with the thread-local C++ parser borrowed mutably.
pub fn with_cpp_parser_mut<R>(f: impl FnOnce(&mut Parser) -> R) -> R {
    CPP_PARSER.with(|cell| f(&mut cell.borrow_mut()))
}

/// Run `f` with the thread-local C parser borrowed mutably.
pub fn with_c_parser_mut<R>(f: impl FnOnce(&mut Parser) -> R) -> R {
    C_PARSER.with(|cell| f(&mut cell.borrow_mut()))
}

/// Parse `source` using the parser matching `is_cpp`.
pub fn parse_source(source: &[u8], is_cpp: bool) -> Option<tree_sitter::Tree> {
    if is_cpp {
        with_cpp_parser_mut(|p| p.parse(source, None))
    } else {
        with_c_parser_mut(|p| p.parse(source, None))
    }
}

/// File extension → language hint. The Python analyzer uses heuristics that
/// retry with the other parser if the first attempt has many error nodes.
pub fn default_is_cpp(path: &str) -> bool {
    is_cpp_path(path)
}
