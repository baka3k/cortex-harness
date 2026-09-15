//! Vendored Dart grammar binding (efrenbl/tree-sitter-dart v0.1.0 — cùng
//! nguồn với PyPI `tree-sitter-dart==0.1.0` mà `code-tiny/tools/flutter/
//! dart_parser.py` dùng).

use tree_sitter_language::LanguageFn;

unsafe extern "C" {
    fn tree_sitter_dart() -> *const ();
}

/// The tree-sitter [`LanguageFn`] for this grammar.
pub const LANGUAGE: LanguageFn = unsafe { LanguageFn::from_raw(tree_sitter_dart) };

#[cfg(test)]
mod tests {
    #[test]
    fn parses_a_dart_program() {
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&super::LANGUAGE.into())
            .expect("dart grammar ABI must be supported by the runtime");
        let tree = parser
            .parse("void main() { print('hi'); }", None)
            .expect("tree");
        assert_eq!(tree.root_node().kind(), "program");
    }

    #[test]
    fn parses_class_and_function_kinds() {
        let source = b"class Greeter extends Base {\n  final String prefix;\n}\n";
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&super::LANGUAGE.into())
            .expect("dart grammar ABI must be supported by the runtime");
        let tree = parser.parse(source, None).expect("tree");
        let root = tree.root_node();
        let class_node = root.child(0).expect("class node");
        assert_eq!(class_node.kind(), "class_definition");
        assert_eq!(
            class_node.child_by_field_name("name").map(|n| n.kind()),
            Some("identifier")
        );
    }

    /// AST-shape golden vs phía Python: dump 6 dòng đầu của cùng nguồn qua
    /// `.venv/bin/python` + `tree_sitter_dart` phải ra đúng chuỗi node kinds
    /// này (parity gate phase-02, plan 260915-analyzer-layer-rust-cutover).
    #[test]
    fn ast_shape_matches_python_reference_dump() {
        let source = "\
import 'package:flutter/material.dart';

class Greeter extends Base {
  final String prefix;
  Greeter(this.prefix);
  String greet(String name) => '$prefix hello $name';
}

void main() {
  final g = Greeter('hi');
  print(g.greet('world'));
}
";
        let mut parser = tree_sitter::Parser::new();
        parser
            .set_language(&super::LANGUAGE.into())
            .expect("dart grammar ABI must be supported by the runtime");
        let tree = parser.parse(source, None).expect("tree");
        let mut kinds = Vec::new();
        let root = tree.root_node();
        let mut walk_import = root.child(0).expect("import node");
        kinds.push(walk_import.kind().to_string());
        walk_import = walk_import.child(0).expect("library_import");
        kinds.push(walk_import.kind().to_string());
        let class_node = root.child(1).expect("class node");
        kinds.push(class_node.kind().to_string());
        kinds.push(
            class_node
                .child(0)
                .map(|n| n.kind().to_string())
                .expect("class keyword"),
        );
        kinds.push(
            class_node
                .child(1)
                .map(|n| n.kind().to_string())
                .expect("class name identifier"),
        );
        assert_eq!(
            kinds,
            vec![
                "import_or_export",
                "library_import",
                "class_definition",
                "class",
                "identifier",
            ]
        );
    }
}
