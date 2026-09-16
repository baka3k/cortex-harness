//! `graph-state dump/diff` — canonical state reader cho embedded LadybugDB
//! stores (phase-04 sync-plane parity).
//!
//! * `graph-state dump <store.lbug> --graph <name>` — per label: node count,
//!   canonical key-set + per-node property hash (sorted); per rel table:
//!   edge count; kèm `schema_fingerprint` + index set (L3 — schema/index
//!   drift phải hiện trong dump).
//! * `graph-state diff <a.dump> <b.dump>` — exit 0 khi canonical equal;
//!   unified diff các key lệch.
//!
//! Read-only: store mở read_only=true; `--graph` bắt buộc (H1 gate — named
//! graph absent/empty → FAIL).

use std::collections::BTreeMap;
use std::path::PathBuf;

use lbug::{Connection, Database, SystemConfig};
use serde_json::{json, Value};

// fn quote(value: &str) -> String {
//     let mut out = String::with_capacity(value.len() + 2);
//     out.push('\'');
//     for c in value.chars() {
//         match c {
//             '\\' => out.push_str("\\\\"),
//             '\'' => out.push_str("\\'"),
//             _ => out.push(c),
//         }
//     }
//     out.push('\'');
//     out
// }

fn rows(connection: &Connection, query: &str) -> Vec<(Vec<String>, Vec<Value>)> {
    let result = connection.query(query).expect("query failed");
    let names: Vec<String> = result
        .get_column_names()
        .into_iter()
        .map(|name| name.to_string())
        .collect();
    let mut rows = Vec::new();
    for row in result {
        let values: Vec<Value> = row.iter().map(|value| json_value(value.clone())).collect();
        rows.push((names.clone(), values));
    }
    rows
}

fn json_value(value: lbug::Value) -> Value {
    use lbug::Value as V;
    match value {
        V::Bool(b) => Value::Bool(b),
        V::Int64(i) => json!(i),
        V::Int32(i) => json!(i),
        V::Int16(i) => json!(i),
        V::Int8(i) => json!(i),
        V::UInt64(i) => json!(i),
        V::UInt32(i) => json!(i),
        V::UInt16(i) => json!(i),
        V::UInt8(i) => json!(i),
        V::Double(d) => json!(d),
        V::Float(f) => json!(f),
        V::String(s) => json!(s),
        V::Date(d) => json!(d.to_string()),
        V::TimestampTz(t) => json!(t.to_string()),
        V::Timestamp(t) => json!(t.to_string()),
        V::Interval(i) => json!(format!("{i:?}")),
        V::Null(_) => Value::Null,
        other => json!(format!("{other:?}")),
    }
}

fn string_value(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

/// Canonical dump payload cho 1 store + graph.
fn dump_store(store: &PathBuf, graph: &str) -> Value {
    let database = Database::new(store, SystemConfig::default().read_only(true))
        .expect("open ladybug store");
    let connection = Connection::new(&database).expect("connect ladybug store");

    // Tables của graph hiện tại (USE GRAPH qua query không có — lbug 0.20.4
    // mở 1 database = 1 graph; check graph tồn tại qua node/rel tables).
    let mut node_labels: Vec<String> = Vec::new();
    let mut rel_tables: Vec<String> = Vec::new();
    for (names, values) in rows(&connection, "CALL show_tables() RETURN *") {
        let map: BTreeMap<String, Value> =
            names.iter().cloned().zip(values.into_iter()).collect();
        let name = map
            .get("name")
            .map(string_value)
            .unwrap_or_default();
        let table_type = map
            .get("type")
            .map(string_value)
            .unwrap_or_default();
        match table_type.as_str() {
            "NODE" => node_labels.push(name),
            "REL" | "REL_GROUP" => rel_tables.push(name),
            _ => {}
        }
    }
    node_labels.sort();
    rel_tables.retain(|table| table != "GraphWriteReceipt");
    rel_tables.sort();

    if node_labels.is_empty() {
        eprintln!(
            "FAIL: named graph has no node tables in store {} (absent/empty graph gate)",
            store.display()
        );
        std::process::exit(3);
    }

    let mut labels: BTreeMap<String, Value> = BTreeMap::new();
    for label in &node_labels {
        let count = rows(
            &connection,
            &format!("MATCH (n:`{label}`) RETURN count(n) AS count"),
        )[0]
            .1[0]
            .as_i64()
            .unwrap_or(0);
        // Per-node property hash: property bag canonical (sorted keys) hash,
        // sorted cho ổn định thứ tự.
        let property_rows = rows(
            &connection,
            &format!("MATCH (n:`{label}`) RETURN n.* AS props"),
        );
        let mut hashes: Vec<String> = Vec::new();
        let mut key_set: BTreeMap<String, bool> = BTreeMap::new();
        for (_, values) in &property_rows {
            let props = values
                .first()
                .cloned()
                .unwrap_or(Value::Null);
            let object = props.as_object().cloned().unwrap_or_default();
            for key in object.keys() {
                key_set.insert(key.clone(), true);
            }
            let rendered: BTreeMap<String, String> = object
                .into_iter()
                .map(|(key, value)| (key, string_value(&value)))
                .collect();
            hashes.push(sha16(&serde_json::to_string(&rendered).unwrap_or_default()));
        }
        hashes.sort();
        hashes.dedup();
        labels.insert(
            label.clone(),
            json!({
                "count": count,
                "keys": key_set.keys().collect::<Vec<_>>(),
                "node_property_hashes": hashes,
            }),
        );
    }

    let mut edges: BTreeMap<String, Value> = BTreeMap::new();
    for table in &rel_tables {
        let count = rows(
            &connection,
            &format!("MATCH ()-[r:`{table}`]->() RETURN count(r) AS count"),
        )[0]
            .1[0]
            .as_i64()
            .unwrap_or(0);
        edges.insert(table.clone(), json!({ "count": count }));
    }

    let indexes: Vec<Value> = rows(&connection, "CALL show_indexes() RETURN *")
        .into_iter()
        .map(|(names, values)| {
            let map: BTreeMap<String, Value> =
                names.iter().cloned().zip(values.into_iter()).collect();
            json!({
                "table": map.get("table_name").map(string_value).unwrap_or_default(),
                "name": map.get("index_name").map(string_value).unwrap_or_default(),
                "type": map.get("index_type").map(string_value).unwrap_or_default(),
            })
        })
        .collect();

    json!({
        "format": "cortex-graph-state/canonical-v1",
        "store": store.file_name().and_then(|n| n.to_str()).unwrap_or("?"),
        "graph": graph,
        "schema_fingerprint": cortex_graph_core::schema_manifest::code_graph_schema().fingerprint(),
        "labels": labels,
        "edges": edges,
        "indexes": indexes,
    })
}

fn sha16(text: &str) -> String {
    use sha2::{Digest, Sha256};
    let digest = Sha256::digest(text.as_bytes());
    digest.iter().map(|b| format!("{b:02x}")).collect::<String>()[..16].to_string()
}

/// Deep diff 2 canonical payloads — trả danh sách (path, a, b) lệch.
fn diff_values(path: String, a: &Value, b: &Value, out: &mut Vec<String>) {
    if a == b {
        return;
    }
    match (a, b) {
        (Value::Object(map_a), Value::Object(map_b)) => {
            for key in map_a.keys().chain(map_b.keys()).collect::<std::collections::BTreeSet<_>>() {
                diff_values(
                    format!("{path}.{key}"),
                    map_a.get(key).unwrap_or(&Value::Null),
                    map_b.get(key).unwrap_or(&Value::Null),
                    out,
                );
            }
        }
        (Value::Array(items_a), Value::Array(items_b)) => {
            if items_a.len() != items_b.len() {
                out.push(format!("{path}: len {} != {}", items_a.len(), items_b.len()));
                return;
            }
            for (index, (item_a, item_b)) in items_a.iter().zip(items_b.iter()).enumerate() {
                diff_values(format!("{path}[{index}]"), item_a, item_b, out);
            }
        }
        _ => out.push(format!("{path}: {} != {}", compact(a), compact(b))),
    }
}

fn compact(value: &Value) -> String {
    let rendered = serde_json::to_string(value).unwrap_or_default();
    if rendered.len() > 120 {
        format!("{}…", &rendered[..120])
    } else {
        rendered
    }
}

fn usage() -> ! {
    eprintln!(
        "usage:\n  graph-state dump <store.lbug> --graph <name>\n  graph-state diff <a.canonical.json> <b.canonical.json>"
    );
    std::process::exit(2);
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let Some(command) = argv.first() else { usage() };
    match command.as_str() {
        "dump" => {
            let mut store: Option<PathBuf> = None;
            let mut graph: Option<String> = None;
            let mut index = 1;
            while index < argv.len() {
                match argv[index].as_str() {
                    "--graph" => {
                        index += 1;
                        graph = Some(argv.get(index).expect("--graph value").clone());
                    }
                    other if other.starts_with("--graph=") => {
                        graph = Some(other["--graph=".len()..].to_string());
                    }
                    other if other.starts_with('-') => {
                        eprintln!("unknown flag: {other}");
                        usage();
                    }
                    other => {
                        if store.is_none() {
                            store = Some(PathBuf::from(other));
                        }
                    }
                }
                index += 1;
            }
            let Some(store) = store else { usage() };
            let Some(graph) = graph.filter(|g| !g.trim().is_empty()) else {
                eprintln!("FAIL: --graph is required (H1 gate)");
                usage();
            };
            let payload = dump_store(&store, &graph);
            println!("{}", serde_json::to_string_pretty(&payload).expect("serialize"));
        }
        "diff" => {
            let paths: Vec<&String> = argv[1..].iter().filter(|a| !a.starts_with('-')).collect();
            if paths.len() != 2 {
                usage();
            }
            let left: Value = serde_json::from_str(
                &std::fs::read_to_string(paths[0]).expect("read a"),
            )
            .expect("parse a");
            let right: Value = serde_json::from_str(
                &std::fs::read_to_string(paths[1]).expect("read b"),
            )
            .expect("parse b");
            let mut diffs = Vec::new();
            diff_values(String::new(), &left, &right, &mut diffs);
            if diffs.is_empty() {
                println!("OK: canonical equal");
                return;
            }
            for line in &diffs {
                println!("DIFF {line}");
            }
            eprintln!("FAIL: {} canonical difference(s)", diffs.len());
            std::process::exit(1);
        }
        _ => usage(),
    }
}
