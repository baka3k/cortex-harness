//! Parser → file-extension map. Mirrors `_PARSER_EXTENSIONS` Python dict.
//! Used to filter walk output to message-scan eligible files.

use std::collections::BTreeMap;

pub fn parser_extensions() -> BTreeMap<&'static str, Vec<&'static str>> {
    BTreeMap::from([
        ("cplus", vec![".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"]),
        ("delphi", vec![".pas", ".dpr", ".inc"]),
        ("java", vec![".java"]),
        ("csharp", vec![".cs"]),
        ("kotlin", vec![".kt", ".kts"]),
        ("android", vec![".kt", ".kts", ".java", ".xml", ".gradle", ".gradle.kts"]),
        ("vbnet", vec![".vb"]),
        ("vb6", vec![".vbp", ".vbw", ".bas", ".cls", ".frm", ".frx"]),
        ("vba", vec![".bas", ".cls", ".frm"]),
        ("vbscript", vec![".vbs", ".wsf", ".asp"]),
        ("python", vec![".py"]),
        ("swift", vec![".swift"]),
        ("js", vec![".js", ".jsx", ".mjs", ".cjs"]),
        ("ts", vec![".ts", ".tsx", ".mts", ".cts"]),
        ("php", vec![".php"]),
        ("sql", vec![".sql", ".ddl", ".dml", ".psql"]),
        ("plsql", vec![
            ".pls", ".plsql", ".pks", ".pkb", ".pkg", ".pck", ".spc", ".spb", ".trg", ".fnc",
        ]),
    ])
}

pub fn file_matches_parser(rel_path: &str, parser: &str) -> bool {
    let lower = rel_path.to_ascii_lowercase();
    let binding = parser_extensions();
    let exts = match binding.get(parser) {
        Some(exts) => exts,
        None => return false,
    };
    exts.iter().any(|ext| lower.ends_with(ext))
}

/// Language string the Python children pass to `run_message_scan_pipeline`
/// (`language = args.language or "<child default>"`):
/// ts child → `"typescript"`, js → `"javascript"`, android-kotlin →
/// `"android-kotlin"`, mọi child khác → tên parser. Thuộc tính
/// `Message.language` trên graph phải khớp từng chữ với baseline.
pub fn message_language(parser: &str) -> String {
    match parser {
        "ts" => "typescript".to_string(),
        "js" => "javascript".to_string(),
        "android" => "android-kotlin".to_string(),
        other => other.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn message_language_matches_child_defaults() {
        assert_eq!(message_language("ts"), "typescript");
        assert_eq!(message_language("js"), "javascript");
        assert_eq!(message_language("android"), "android-kotlin");
        assert_eq!(message_language("java"), "java");
        assert_eq!(message_language("python"), "python");
        assert_eq!(message_language("cplus"), "cplus");
    }
}