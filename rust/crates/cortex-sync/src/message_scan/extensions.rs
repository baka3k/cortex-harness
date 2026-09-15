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