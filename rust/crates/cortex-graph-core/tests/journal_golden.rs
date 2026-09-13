//! Golden parity test cho journal read path:
//! 1. `inspect_python_created_journal` — mở DB do `SQLiteJournal` Python tạo.
//! 2. `inspect_rust_created_journal` — tạo DB bằng DDL Rust, insert rows từ
//!    fixture, inspect → so expected (skip `journal_bytes` vì file size khác).
//! 3. `ddl_matches_python` — DDL strings Rust == DDL Python byte-for-byte.

use cortex_graph_core::journal::{create_schema, inspect_journal, schema_statements};
use rusqlite::Connection;
use serde::Deserialize;
use serde_json::Value;
use std::collections::BTreeMap;

#[derive(Deserialize)]
struct Fixture {
    schema_version: i64,
    now_epoch: f64,
    ddl_statements: Vec<String>,
    row_columns: BTreeMap<String, Vec<String>>,
    rows: BTreeMap<String, Vec<BTreeMap<String, Value>>>,
    expected: Vec<Value>,
}

const TOLERANCE: f64 = 1e-9;

fn insert_rows(conn: &Connection, fixture: &Fixture) -> rusqlite::Result<()> {
    // Thứ tự FK: runs → artifacts → batches → events (BTreeMap không giữ
    // insertion order của Python dict).
    for table in ["runs", "artifacts", "batches", "events"] {
        let rows = &fixture.rows[table];
        let columns = &fixture.row_columns[table];
        let placeholders = vec!["?"; columns.len()].join(", ");
        let column_list = columns.join(", ");
        let sql = format!("INSERT INTO {table} ({column_list}) VALUES ({placeholders})");
        let mut stmt = conn.prepare(&sql)?;
        for row in rows {
            let params: Vec<rusqlite::types::Value> = columns
                .iter()
                .map(|column| match row.get(column) {
                    Some(Value::String(s)) => rusqlite::types::Value::Text(s.clone()),
                    Some(Value::Number(n)) => {
                        rusqlite::types::Value::Integer(n.as_i64().unwrap_or_default())
                    }
                    Some(Value::Bool(b)) => rusqlite::types::Value::Integer(*b as i64),
                    _ => rusqlite::types::Value::Null,
                })
                .collect();
            stmt.execute(rusqlite::params_from_iter(params))?;
        }
    }
    Ok(())
}

fn compare_summaries(
    actual: &[cortex_graph_core::journal::RunSummary],
    expected: &[Value],
    skip_journal_bytes: bool,
) {
    assert_eq!(actual.len(), expected.len(), "số runs khác Python");
    for (summary, expected_value) in actual.iter().zip(expected) {
        let expected_map = expected_value.as_object().expect("summary là object");
        let actual_value = serde_json::to_value(summary).expect("serialize summary");
        let actual_map = actual_value.as_object().expect("summary là object");
        for (key, expected_field) in expected_map {
            if skip_journal_bytes && key == "journal_bytes" {
                continue;
            }
            let actual_field = actual_map
                .get(key)
                .unwrap_or_else(|| panic!("thiếu field `{key}` trong RunSummary"));
            match (actual_field.as_f64(), expected_field.as_f64()) {
                (Some(a), Some(b)) => assert!(
                    (a - b).abs() <= TOLERANCE,
                    "field `{key}`: rust={a} python={b}"
                ),
                _ => assert_eq!(
                    actual_field, expected_field,
                    "field `{key}` khác Python"
                ),
            }
        }
    }
}

#[test]
fn inspect_python_created_journal() {
    let fixture: Fixture =
        serde_json::from_str(include_str!("fixtures/journal_golden.json")).expect("fixture hợp lệ");
    let db_path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/journal_python.sqlite");
    let summaries = inspect_journal(&db_path, fixture.now_epoch).expect("inspect phải thành công");
    compare_summaries(&summaries, &fixture.expected, false);
}

#[test]
fn inspect_rust_created_journal() {
    let fixture: Fixture =
        serde_json::from_str(include_str!("fixtures/journal_golden.json")).expect("fixture hợp lệ");
    let temp_dir = std::env::temp_dir().join("cortex_graph_core_journal_test");
    std::fs::create_dir_all(&temp_dir).expect("tạo temp dir");
    let db_path = temp_dir.join("journal_rust.sqlite");
    let _ = std::fs::remove_file(&db_path);

    {
        let mut conn = Connection::open(&db_path).expect("open");
        create_schema(&mut conn).expect("create schema");
        insert_rows(&conn, &fixture).expect("insert rows");
    }

    let summaries = inspect_journal(&db_path, fixture.now_epoch).expect("inspect phải thành công");
    compare_summaries(&summaries, &fixture.expected, true);

    // Roundtrip 2 chiều (scripts/rust_parity/check_journal_roundtrip.py):
    // export DB do Rust tạo để Python `inspect_journal` đọc kiểm chứng.
    if let Ok(export_path) = std::env::var("CORTEX_EXPORT_JOURNAL_DB") {
        std::fs::copy(&db_path, &export_path).expect("export journal db");
    }
    let _ = std::fs::remove_file(&db_path);
}

#[test]
fn ddl_matches_python() {
    let fixture: Fixture =
        serde_json::from_str(include_str!("fixtures/journal_golden.json")).expect("fixture hợp lệ");
    let rust_ddl = schema_statements();
    assert_eq!(
        rust_ddl.len(),
        fixture.ddl_statements.len(),
        "số DDL statements khác Python"
    );
    for (index, (rust_stmt, python_stmt)) in rust_ddl.iter().zip(&fixture.ddl_statements).enumerate()
    {
        assert_eq!(
            rust_stmt, python_stmt,
            "DDL statement #{index} khác Python:\n--- rust ---\n{rust_stmt}\n--- python ---\n{python_stmt}"
        );
    }
}

#[test]
fn schema_version_matches_python() {
    let fixture: Fixture =
        serde_json::from_str(include_str!("fixtures/journal_golden.json")).expect("fixture hợp lệ");
    assert_eq!(
        cortex_graph_core::journal::JOURNAL_SCHEMA_VERSION,
        fixture.schema_version
    );
}
