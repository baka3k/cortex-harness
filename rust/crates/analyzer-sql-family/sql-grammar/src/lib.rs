//! Vendored SQL grammar binding (derekstride/tree-sitter-sql v0.3.11).

use tree_sitter_language::LanguageFn;

unsafe extern "C" {
    fn tree_sitter_sql() -> *const ();
}

/// The tree-sitter [`LanguageFn`] for this grammar.
pub const LANGUAGE: LanguageFn = unsafe { LanguageFn::from_raw(tree_sitter_sql) };
