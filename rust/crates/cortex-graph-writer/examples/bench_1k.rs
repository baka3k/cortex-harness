//! Benchmark helper — ghi N function rows (full metadata) vào 1 graph
//! FalkorDB. Được `scripts/rust_parity/bench_writer_1k.py` gọi; timing wall
//! clock đo ở phía script (gồm process spawn + connect).

use std::collections::BTreeMap;

use cortex_falkordb::client::FalkorDbClient;
use cortex_graph_writer::language_writer::LanguageCodeWriter;
use cortex_graph_writer::store::FalkorDbStore;
use serde_json::{json, Value};

fn arg(flag: &str, default: &str) -> String {
    let args: Vec<String> = std::env::args().collect();
    args.iter()
        .position(|a| a == flag)
        .and_then(|index| args.get(index + 1))
        .cloned()
        .unwrap_or_else(|| default.to_string())
}

fn main() {
    let graph = arg("--graph", "bench_rw");
    let host = arg("--host", "127.0.0.1");
    let port: u16 = arg("--port", "6379").parse().expect("port");
    let rows_count: usize = arg("--rows", "1000").parse().expect("rows");

    let rows: Vec<serde_json::Map<String, Value>> = (0..rows_count)
        .map(|index| {
            json!({
                "id": format!("bench:fn:{index:05}"),
                "name": format!("bench_fn_{index}"),
                "node_type": "code",
                "qualified_name": format!("bench::mod{}::bench_fn_{index}", index / 50),
                "kind": "function",
                "class_name": "",
                "package_name": format!("pkg{}", index / 50),
                "scope_name": "",
                "file_path": format!("bench/src/mod{}.rs", index / 50),
                "start_byte": index * 120,
                "end_byte": index * 120 + 110,
                "start_line": (index % 400) + 1,
                "end_line": (index % 400) + 12,
                "arity": index % 6,
                "code": "fn bench() { /* x */ }",
                "comment": "",
                "summary": format!("bench fn {index}"),
                "note": "",
                "exported": index % 3 == 0,
                "visibility": if index % 2 == 0 { "public" } else { "private" },
                "is_public_api": index % 5 == 0,
                "visibility_source": "tree_sitter",
                "export_evidence": "",
                "signature": "",
                "external": false,
                "builtin": false,
                "react_role": "",
                "middleware_kind": "",
                "project_id": "bench",
                "project_id_normalized": "bench",
                "project_name": "bench",
                "language": "rust",
                "repo": "bench-repo",
                "build_system": "cargo",
            })
            .as_object()
            .cloned()
            .expect("row object")
        })
        .collect();

    let client = FalkorDbClient::connect_verified(&host, port).expect("connect");
    let mut writer = LanguageCodeWriter::new(Box::new(FalkorDbStore::new(client, graph)), None, 1000, false);

    // Graph key chưa tồn tại trên server → "Invalid graph operation on empty
    // key" — cleanup bare bỏ qua lỗi đó (không có gì để xoá).
    if let Err(error) = writer
        .store
        .execute_query("MATCH (n) DETACH DELETE n", &BTreeMap::new(), None)
    {
        let message = error.to_string();
        if !message.contains("empty key") {
            panic!("cleanup: {error}");
        }
    }
    let written = writer.write_functions_full(&rows).expect("write");
    let verify = writer
        .store
        .execute_query(
            "MATCH (f:Function) RETURN count(f) AS count",
            &BTreeMap::new(),
            None,
        )
        .expect("verify");
    println!(
        "written={written} nodes={}",
        verify
            .first()
            .and_then(|record| record.get("count"))
            .map(|value| value.to_string())
            .unwrap_or_default()
    );
}
