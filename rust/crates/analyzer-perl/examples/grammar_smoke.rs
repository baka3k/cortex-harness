//! Minimal smoke: parse 1 file với grammar vendored — debug SIGBUS dùng.

#![allow(unsafe_code)]

use tree_sitter::Parser;
use tree_sitter_language::LanguageFn;

unsafe extern "C" {
    fn tree_sitter_perl() -> *const ();
}

fn main() {
    let source = std::env::args().nth(1).unwrap_or_else(|| "1;\n".to_string());
    eprintln!("start");
    let lang = unsafe { tree_sitter::Language::new(LanguageFn::from_raw(tree_sitter_perl)) };
    eprintln!("abi={}", lang.abi_version());
    eprintln!("name={:?}", lang.name());
    let mut parser = Parser::new();
    match parser.set_language(&lang) {
        Ok(()) => eprintln!("set_language ok"),
        Err(error) => {
            eprintln!("set_language error: {error:?}");
            return;
        }
    }
    let tree = parser.parse(source.as_bytes(), None);
    match tree {
        Some(tree) => eprintln!("sexp: {}", tree.root_node().to_sexp()),
        None => eprintln!("parse returned None"),
    }
}
