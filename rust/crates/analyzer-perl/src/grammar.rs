//! tree-sitter-perl v1.2.1 loader — vendored C build (vendor/src).
//!
//! Grammar C sources trùng sdist `tree-sitter-perl==1.2.1` (chính là wheel
//! Python reference đang chạy) nên parse tree / error recovery khớp.
//! Module này là chỗ DUY NHẤT của crate đụng `unsafe` (FFI load grammar).

#![allow(unsafe_code)]

use tree_sitter::Language;
use tree_sitter_language::LanguageFn;

// Giống scaffold `bindings/rust/lib.rs` của CLI: C fn trả `const TSLanguage *`
// (thành `*const ()` FFI-safe), bọc bằng LanguageFn::from_raw.
unsafe extern "C" {
    fn tree_sitter_perl() -> *const ();
}

/// `parser_runtime.load_language()` — capsule tương đương py-tree-sitter.
pub fn language() -> Language {
    Language::new(unsafe { LanguageFn::from_raw(tree_sitter_perl) })
}
