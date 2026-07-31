//! Phase 6 — Multi-language grammar dispatch.
//!
//! Defines a `Grammar` trait so each language's tree-sitter grammar can be
//! registered uniformly. The C++ pilot is fully implemented; java, python,
//! and javascript are registered with grammar loading but use the same C++
//! walker as a placeholder. Per-language walker implementations will replace
//! the C++ fallback incrementally — each analyzer's `parse_*_file()` can
//! then call into the language-specific path.
//!
//! Public surface (PyO3):
//!   * `supported_languages()` — list of language IDs the extension can load
//!   * `parse_source_for_language(language, source_bytes)` — parse bytes and
//!      return the tree-sitter root node kind (proves grammar wiring)

use std::path::Path;

use tree_sitter::{Language, Parser};

/// Trait every language module implements.
pub trait Grammar: Send + Sync {
    /// Stable identifier used in PyO3 calls (`"cpp"`, `"java"`, …).
    fn id(&self) -> &'static str;
    /// Tree-sitter `Language` to feed into `Parser::set_language`.
    fn language(&self) -> Language;
    /// Return true if `path` looks like a source file for this language.
    fn matches_path(&self, path: &str) -> bool;
}

// ── C++ ────────────────────────────────────────────────────────────────

pub struct CppGrammar;

impl Grammar for CppGrammar {
    fn id(&self) -> &'static str {
        "cpp"
    }
    fn language(&self) -> Language {
        tree_sitter_cpp::LANGUAGE.into()
    }
    fn matches_path(&self, path: &str) -> bool {
        // C++ extension set: cpp/cxx/cc/hpp/hxx/hh plus `.h` (most `.h` files
        // in C++ projects are C++ headers; we walk Cpp first in `registry()`
        // so this picks C++ before plain C for ambiguous extensions).
        let p = Path::new(path);
        match p.extension().and_then(|e| e.to_str()) {
            Some(ext) => matches!(
                ext.to_ascii_lowercase().as_str(),
                "cpp" | "cxx" | "cc" | "hpp" | "hxx" | "hh" | "h" | "h++"
            ),
            None => false,
        }
    }
}

// ── C ──────────────────────────────────────────────────────────────────

pub struct CGrammar;

impl Grammar for CGrammar {
    fn id(&self) -> &'static str {
        "c"
    }
    fn language(&self) -> Language {
        tree_sitter_c::LANGUAGE.into()
    }
    fn matches_path(&self, path: &str) -> bool {
        Path::new(path)
            .extension()
            .and_then(|e| e.to_str())
            .map(|e| matches!(e, "c" | "h"))
            .unwrap_or(false)
    }
}

// ── Java ───────────────────────────────────────────────────────────────

pub struct JavaGrammar;

impl Grammar for JavaGrammar {
    fn id(&self) -> &'static str {
        "java"
    }
    fn language(&self) -> Language {
        tree_sitter_java::LANGUAGE.into()
    }
    fn matches_path(&self, path: &str) -> bool {
        Path::new(path)
            .extension()
            .and_then(|e| e.to_str())
            .map(|e| e == "java")
            .unwrap_or(false)
    }
}

// ── Python ─────────────────────────────────────────────────────────────

pub struct PythonGrammar;

impl Grammar for PythonGrammar {
    fn id(&self) -> &'static str {
        "python"
    }
    fn language(&self) -> Language {
        tree_sitter_python::LANGUAGE.into()
    }
    fn matches_path(&self, path: &str) -> bool {
        Path::new(path)
            .extension()
            .and_then(|e| e.to_str())
            .map(|e| e == "py")
            .unwrap_or(false)
    }
}

// ── JavaScript / TypeScript ────────────────────────────────────────────

pub struct JsGrammar;

impl Grammar for JsGrammar {
    fn id(&self) -> &'static str {
        "javascript"
    }
    fn language(&self) -> Language {
        tree_sitter_javascript::LANGUAGE.into()
    }
    fn matches_path(&self, path: &str) -> bool {
        Path::new(path)
            .extension()
            .and_then(|e| e.to_str())
            .map(|e| matches!(e, "js" | "jsx" | "mjs" | "cjs"))
            .unwrap_or(false)
    }
}

/// All grammars the extension knows about, in declaration order.
pub fn registry() -> Vec<Box<dyn Grammar>> {
    vec![
        Box::new(CppGrammar),
        Box::new(CGrammar),
        Box::new(JavaGrammar),
        Box::new(PythonGrammar),
        Box::new(JsGrammar),
    ]
}

/// Resolve a language ID (e.g. `"cpp"`, `"cplus"`, `"c"`, `"java"`, …).
pub fn by_id(id: &str) -> Option<Box<dyn Grammar>> {
    let canonical = match id {
        "cplus" | "cpp" | "c++" => "cpp",
        "c" => "c",
        "java" => "java",
        "python" | "py" => "python",
        "javascript" | "js" | "ts" => "javascript",
        _ => return None,
    };
    for g in registry() {
        if g.id() == canonical {
            return Some(g);
        }
    }
    None
}

/// Resolve by file extension/path (auto-detect).
pub fn by_path(path: &str) -> Option<Box<dyn Grammar>> {
    for g in registry() {
        if g.matches_path(path) {
            return Some(g);
        }
    }
    None
}

/// Parse bytes with the grammar for the given language ID and return the
/// root node kind as a sanity probe.
pub fn parse_root_kind(language_id: &str, source: &[u8]) -> Option<String> {
    let grammar = by_id(language_id)?;
    let mut parser = Parser::new();
    parser.set_language(&grammar.language()).ok()?;
    let tree = parser.parse(source, None)?;
    Some(tree.root_node().kind().to_string())
}

// ── Tests ──────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn by_id_resolves_aliases() {
        assert_eq!(by_id("cplus").unwrap().id(), "cpp");
        assert_eq!(by_id("cpp").unwrap().id(), "cpp");
        assert_eq!(by_id("c").unwrap().id(), "c");
        assert_eq!(by_id("java").unwrap().id(), "java");
        assert_eq!(by_id("python").unwrap().id(), "python");
        assert_eq!(by_id("py").unwrap().id(), "python");
        assert_eq!(by_id("js").unwrap().id(), "javascript");
        assert_eq!(by_id("ts").unwrap().id(), "javascript");
    }

    #[test]
    fn by_id_returns_none_for_unknown() {
        assert!(by_id("rust").is_none());
        assert!(by_id("").is_none());
    }

    #[test]
    fn by_path_dispatches_by_extension() {
        assert_eq!(by_path("src/main.cpp").unwrap().id(), "cpp");
        // `.h` matches the C++ grammar because registry() walks Cpp first —
        // most `.h` files in a C++ project are C++ headers.
        assert_eq!(by_path("src/lib.h").unwrap().id(), "cpp");
        assert_eq!(by_path("Foo.java").unwrap().id(), "java");
        assert_eq!(by_path("app.py").unwrap().id(), "python");
        assert_eq!(by_path("index.js").unwrap().id(), "javascript");
        assert_eq!(by_path("main.c").unwrap().id(), "c");
    }

    #[test]
    fn parse_root_kind_runs_cpp_grammar() {
        let src = b"int main() { return 0; }";
        let kind = parse_root_kind("cpp", src);
        assert_eq!(kind.as_deref(), Some("translation_unit"));
    }

    #[test]
    fn parse_root_kind_runs_java_grammar() {
        let src = b"class Main { public static void main(String[] args) {} }";
        let kind = parse_root_kind("java", src);
        assert_eq!(kind.as_deref(), Some("program"));
    }

    #[test]
    fn parse_root_kind_runs_python_grammar() {
        let src = b"def main():\n    return 0\n";
        let kind = parse_root_kind("python", src);
        assert_eq!(kind.as_deref(), Some("module"));
    }

    #[test]
    fn parse_root_kind_runs_javascript_grammar() {
        let src = b"function main() { return 0; }";
        let kind = parse_root_kind("javascript", src);
        assert_eq!(kind.as_deref(), Some("program"));
    }
}