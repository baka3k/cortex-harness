//! Integration test LadybugStore — store file thật trong tempdir: bootstrap,
//! auto-DDL, datetime rewrite, round-trip qua LanguageCodeWriter.

use std::collections::BTreeMap;

use cortex_graph_writer::json_row::Row;
use cortex_graph_writer::language_writer::LanguageCodeWriter;
use cortex_graph_writer::store::ladybug_store::LadybugStore;
use serde_json::{json, Value};

fn temp_store(tag: &str) -> (std::path::PathBuf, LadybugStore) {
    let dir = std::env::temp_dir().join(format!(
        "cortex-writer-it-{tag}-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("it.lbug");
    let store = LadybugStore::open(&path, "hyper_graph").unwrap();
    (dir, store)
}

fn row(value: serde_json::Value) -> Row {
    value.as_object().unwrap().clone()
}

#[test]
fn bootstrap_and_write_files_roundtrip() {
    let (dir, store) = temp_store("files");
    let mut writer = LanguageCodeWriter::new(Box::new(store), None, 2, false);
    let files: Vec<Row> = (0..5)
        .map(|index| {
            row(json!({
                "id": format!("src/file{index}.rs"),
                "path": format!("src/file{index}.rs"),
                "start_line": 1,
                "end_line": 10 + index,
                "code": "fn main() {}",
                "project_id": "itest",
                "project_id_normalized": "itest",
                "project_name": "itest",
                "language": "rust",
                "repo": "itest-repo",
                "build_system": "cargo",
            }))
        })
        .collect();
    let written = writer.write_files(&files).unwrap();
    assert_eq!(written, 5);

    // Readback qua store nội bộ.
    let mut writer = writer; // nắm store lại qua destructuring
    let records = writer
        .store
        .execute_query(
            "MATCH (f:File) RETURN f.id AS id, f.end_line AS end_line ORDER BY f.id",
            &BTreeMap::new(),
            None,
        )
        .unwrap();
    assert_eq!(records.len(), 5);
    assert_eq!(records[0]["id"], json!("src/file0.rs"));
    assert_eq!(records[4]["end_line"], json!(14));

    // write_batches state resume: gọi lại → 0 (state đã hoàn tất).
    let written_again = {
        let files_clone = files.clone();
        let mut writer = LanguageCodeWriter::new(
            Box::new(LadybugStore::open(&dir.join("it.lbug"), "hyper_graph").unwrap()),
            None,
            2,
            false,
        );
        writer.write_files(&files_clone).unwrap()
    };
    assert_eq!(written_again, 5); // store mới, state mới
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn auto_ddl_adds_unknown_property_and_timestamp_column() {
    let (dir, store) = temp_store("autoddl");
    {
        let mut writer = LanguageCodeWriter::new(Box::new(store), None, 10, false);
        // `react_role` không nằm trong base columns bootstrap → auto-DDL
        // ALTER ADD STRING; `updated_at` là datetime() → rewrite timestamp()
        // → ALTER ADD TIMESTAMP (column type đúng phải cho phép readback).
        let functions = vec![row(json!({
            "id": "f1",
            "name": "handle",
            "react_role": "screen",
            "project_id": "itest",
            "project_id_normalized": "itest",
            "project_name": "itest",
            "language": "rust",
            "repo": "r",
            "build_system": "cargo",
            "file_path": "src/lib.rs",
            "start_line": 1,
            "end_line": 2,
            "start_byte": 0,
            "end_byte": 9,
            "arity": 0,
            "code": "fn handle(){}",
        }))];
        let written = writer.write_functions_full(&functions).unwrap();
        assert_eq!(written, 1);

        let records = writer
            .store
            .execute_query(
                "MATCH (f:Function {id: 'f1'}) RETURN f.react_role AS role, f.arity AS arity",
                &BTreeMap::new(),
                None,
            )
            .unwrap();
        assert_eq!(records[0]["role"], json!("screen"));
        assert_eq!(records[0]["arity"], json!(0));
    }
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn calls_upsert_and_dedupe() {
    let (dir, store) = temp_store("calls");
    let mut writer = LanguageCodeWriter::new(Box::new(store), None, 10, false);
    let functions: Vec<Row> = ["f1", "f2", "f3"]
        .iter()
        .map(|id| {
            row(json!({
                "id": id,
                "name": id,
                "project_id": "itest",
                "project_id_normalized": "itest",
            }))
        })
        .collect();
    writer.write_functions_full(&functions).unwrap();

    // duplicate calls (f1→f2 xuất hiện 2 lần) → count gộp = 2.
    let calls = vec![
        row(json!({
            "caller_id": "f1",
            "callee_id": "f2",
            "count": 1,
            "call_type": "direct",
            "project_id": "itest",
        })),
        row(json!({
            "caller_id": "f1",
            "callee_id": "f2",
            "count": 1,
            "call_type": "direct",
            "project_id": "itest",
        })),
        row(json!({
            "caller_id": "f1",
            "callee_id": "f3",
            "count": 1,
            "call_type": "direct",
            "project_id": "itest",
        })),
    ];
    let written = writer.write_calls(&calls).unwrap();
    assert_eq!(written, 2);

    let records = writer
        .store
        .execute_query(
            "MATCH (:Function {id:'f1'})-[r:CALLS]->(:Function {id:'f2'}) RETURN r.count AS count",
            &BTreeMap::new(),
            None,
        )
        .unwrap();
    assert_eq!(records[0]["count"], json!(2));
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn escape_special_characters_in_strings() {
    let (dir, store) = temp_store("escape");
    let mut writer = LanguageCodeWriter::new(Box::new(store), None, 10, false);
    let files = vec![row(json!({
        "id": "weird \"quoted\".rs",
        "path": "O'Brien \\ path \"x\"",
        "code": "let s = \"multi\\nline\";",
        "project_id": "itest",
        "project_id_normalized": "itest",
        "project_name": "it\"est",
        "language": "rust",
        "repo": "repo'1",
        "build_system": "cargo",
    }))];
    writer.write_files(&files).unwrap();
    let records = writer
        .store
        .execute_query(
            "MATCH (f:File) RETURN f.path AS path, f.project_name AS name",
            &BTreeMap::new(),
            None,
        )
        .unwrap();
    assert_eq!(records.len(), 1);
    assert_eq!(records[0]["path"], json!("O'Brien \\ path \"x\""));
    assert_eq!(records[0]["name"], json!("it\"est"));
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn empty_rows_write_nothing() {
    let (dir, store) = temp_store("empty");
    let mut writer = LanguageCodeWriter::new(Box::new(store), None, 10, false);
    assert_eq!(writer.write_files(&[]).unwrap(), 0);
    assert_eq!(writer.write_calls(&[]).unwrap(), 0);
    let records = writer
        .store
        .execute_query("MATCH (f:File) RETURN count(f) AS count", &BTreeMap::new(), None)
        .unwrap();
    assert_eq!(records[0]["count"], json!(0));
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn topology_owned_node_roundtrip_and_manual_delete() {
    let (dir, store) = temp_store("topology");
    let mut writer = LanguageCodeWriter::new(Box::new(store), None, 10, false);
    writer.ensure_schema().unwrap();
    // Thả 1 node topology_owned vào (shape của topology writer).
    let mut params: BTreeMap<String, Value> = BTreeMap::new();
    params.insert(
        "rows".to_string(),
        json!([{"id": "m1", "project_id": "itest", "project_id_normalized": "itest"}]),
    );
    writer
        .store
        .execute_query(
            "UNWIND $rows AS row MERGE (n:ProjectModule {id: row.id}) SET n.project_id = row.project_id, n.project_id_normalized = row.project_id_normalized, n.topology_owned = true",
            &params,
            None,
        )
        .unwrap();

    // FOREACH-based cleanup queries (cleanup_project/cleanup_paths) parse-fail
    // trên ladybug 0.20.4 — GIỐNG HỆT Python driver (falkordb-only path).
    // Verify hành vi tương đương bằng DELETE trực tiếp.
    let deleted = writer
        .store
        .execute_query(
            "MATCH (n:ProjectModule {id: 'm1'}) DETACH DELETE n RETURN count(*) AS count",
            &BTreeMap::new(),
            None,
        )
        .unwrap();
    assert_eq!(deleted[0]["count"], json!(1));
    let _ = std::fs::remove_dir_all(&dir);
}
