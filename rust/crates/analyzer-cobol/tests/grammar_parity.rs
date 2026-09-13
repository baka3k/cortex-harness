//! Golden test — tree-sitter COBOL error/missing nodes phải byte-identical với
//! Python reference (ctypes dlopen cùng bundle grammar). Golden file sinh từ:
//! `code-tiny/tools/cobol` parser (xem phase07-cobol-parity.md).

use std::path::PathBuf;

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../..")
        .canonicalize()
        .expect("repo root")
}

fn bundled_library() -> PathBuf {
    let dir = repo_root().join("code-tiny/tools/cobol/lib");
    let mut candidates: Vec<PathBuf> = std::fs::read_dir(&dir)
        .expect("bundled lib dir")
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| {
            path.extension()
                .map(|ext| matches!(ext.to_string_lossy().to_lowercase().as_str(), "so" | "dylib" | "dll"))
                .unwrap_or(false)
        })
        .collect();
    candidates.sort();
    candidates.into_iter().next().expect("bundled grammar")
}

#[test]
fn tree_diagnostics_match_python_reference() {
    let golden: serde_json::Map<String, serde_json::Value> = serde_json::from_str(
        &std::fs::read_to_string(
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/cobol_tree_diagnostics.json"),
        )
        .unwrap(),
    )
    .unwrap();
    let (mut loaded, _info) =
        analyzer_cobol::parser_runtime::load_parser(Some(&bundled_library().to_string_lossy()))
            .unwrap();
    let dir = repo_root().join("tests/fixtures/cobol-application");
    let mut checked = 0;
    for entry in std::fs::read_dir(&dir).unwrap().flatten() {
        let path = entry.path();
        let ext = path
            .extension()
            .map(|e| e.to_string_lossy().to_lowercase())
            .unwrap_or_default();
        if !matches!(ext.as_str(), "cbl" | "cob" | "cpy" | "copy") {
            continue;
        }
        let data = std::fs::read(&path).unwrap();
        let decoded = analyzer_cobol::parser::decode_source(&data).unwrap();
        let (offsets, _lengths) = analyzer_cobol::parser::line_metrics(
            &decoded.text,
            &decoded.encoding,
            &data,
        );
        let tree = analyzer_cobol::parser::tree_diagnostics(
            &mut loaded,
            &decoded.text,
            &path.to_string_lossy(),
            &decoded.encoding,
            &offsets,
            data.len(),
        );
        let errs: Vec<serde_json::Value> = tree
            .diagnostics
            .iter()
            .map(|d| {
                let e = d.evidence.as_ref().unwrap();
                serde_json::json!({
                    "t": d.details["node_type"],
                    "s": [e.start_line - 1, e.start_column - 1],
                    "e": [e.end_line - 1, e.end_column - 1],
                    "m": d.code == "COBOL_SYNTAX_MISSING",
                })
            })
            .collect();
        let name = path.file_name().unwrap().to_string_lossy().to_string();
        let expected = &golden[&name];
        assert_eq!(expected["errors"], serde_json::Value::Array(errs), "errors mismatch {name}");
        assert_eq!(expected["err_count"].as_i64().unwrap(), tree.error_count);
        checked += 1;
    }
    assert!(checked >= 8, "expected the full cobol-application fixture set");
}

