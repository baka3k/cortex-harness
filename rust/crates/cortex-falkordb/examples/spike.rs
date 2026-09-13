//! Phase 01 spike runner — chạy 3 gate của plans/.../phase-01.md:
//! 1. read parity: chạy bộ query fixture (do Python sinh từ falkordb_driver)
//!    và so normalized records property-by-property;
//! 2. write: MERGE probe node với đầy đủ kiểu param → Python đọc lại xác nhận;
//! 3. latency: chạy N vòng bộ query đọc, đo tổng/thời gian trung bình.
//!
//! Output ra stdout là JSON để parity script (scripts/rust_parity/
//! falkordb_spike_parity.py) tổng hợp và viết decision record.

use std::collections::BTreeMap;
use std::time::Instant;

use cortex_falkordb::{FalkorDbClient, Param};
use serde_json::{Value, json};

const DEFAULT_GRAPH: &str = "stock";

fn parse_args() -> (String, u16, String, String, Value) {
    let mut host = "127.0.0.1".to_string();
    let mut port: u16 = 6379;
    let mut graph = DEFAULT_GRAPH.to_string();
    let mut fixture_path = String::new();
    let mut extra = json!({});
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--host" => host = args.next().unwrap_or(host),
            "--port" => port = args.next().and_then(|v| v.parse().ok()).unwrap_or(port),
            "--graph" => graph = args.next().unwrap_or(graph),
            "--fixture" => fixture_path = args.next().unwrap_or_default(),
            "--extra" => {
                if let Some(raw) = args.next() {
                    extra = serde_json::from_str(&raw).unwrap_or_else(|e| {
                        eprintln!("--extra không phải JSON hợp lệ: {e}");
                        std::process::exit(2);
                    });
                }
            }
            other => {
                eprintln!("unknown arg {other}");
                std::process::exit(2);
            }
        }
    }
    (host, port, graph, fixture_path, extra)
}

fn json_to_params(params: &Value) -> BTreeMap<String, Param> {
    let mut out = BTreeMap::new();
    if let Value::Object(map) = params {
        for (key, value) in map {
            out.insert(key.clone(), json_to_param(value));
        }
    }
    out
}

fn json_to_param(value: &Value) -> Param {
    match value {
        Value::Null => Param::Null,
        Value::Bool(b) => Param::Bool(*b),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Param::Int(i)
            } else {
                Param::Float(n.as_f64().unwrap_or_default())
            }
        }
        Value::String(s) => Param::Str(s.clone()),
        Value::Array(items) => Param::List(items.iter().map(json_to_param).collect()),
        other => Param::Str(other.to_string()),
    }
}

/// Gate 1 — đọc fixture, chạy từng query, so `records` đã normalize.
fn run_read_gate(client: &mut FalkorDbClient, graph: &str, fixture: &Value) -> Value {
    let mut results = Vec::new();
    let mut pass = 0usize;
    let mut fail = 0usize;
    for query_spec in fixture["queries"].as_array().expect("fixture.queries") {
        let name = query_spec["name"].as_str().unwrap_or("?");
        let query = query_spec["query"].as_str().expect("query");
        let params = json_to_params(&query_spec["params"]);
        let expected = &query_spec["expected"];

        let outcome = client.ro_query(graph, query, &params);
        let entry = match outcome {
            Ok(result) => {
                let keys: Vec<String> = result.header.iter().map(|c| c.name.clone()).collect();
                // Driver Python: record là dict theo tên cột
                // `{key: _normalize(row[i])}` — làm y vậy trước khi so.
                let records: Vec<Value> = result
                    .records
                    .iter()
                    .map(|row| {
                        let mut obj = serde_json::Map::new();
                        for (idx, cell) in row.iter().enumerate() {
                            let key = keys
                                .get(idx)
                                .cloned()
                                .unwrap_or_else(|| format!("col{idx}"));
                            obj.insert(key, cortex_falkordb::normalize::normalize_value(cell));
                        }
                        Value::Object(obj)
                    })
                    .collect();
                let actual = json!({"status": "ok", "keys": keys, "records": records});
                match diff_json(expected, &actual) {
                    None => {
                        pass += 1;
                        json!({"name": name, "ok": true})
                    }
                    Some(diff) => {
                        fail += 1;
                        json!({"name": name, "ok": false, "diff": diff})
                    }
                }
            }
            Err(e) => {
                // Query lỗi ở cả 2 bên (vd procedure không hỗ trợ) vẫn là parity —
                // so prefix message từ fixture.
                let expected_status = expected["status"].as_str().unwrap_or("");
                if expected_status == "error" {
                    let prefix = expected["message_prefix"].as_str().unwrap_or("");
                    let message = e.to_string();
                    if message.contains(prefix) {
                        pass += 1;
                        json!({"name": name, "ok": true, "note": "both sides error"})
                    } else {
                        fail += 1;
                        json!({
                            "name": name, "ok": false,
                            "diff": {"reason": "error message lệch", "expected_prefix": prefix, "actual": message}
                        })
                    }
                } else {
                    fail += 1;
                    json!({"name": name, "ok": false, "diff": {"reason": "rust lỗi", "error": e.to_string()}})
                }
            }
        };
        results.push(entry);
    }
    json!({"pass": pass, "fail": fail, "results": results})
}

/// So sâu expected vs actual (dùng cho fixture parity) — `None` nếu khớp.
fn diff_json(expected: &Value, actual: &Value) -> Option<Value> {
    if expected == actual {
        return None;
    }
    match (expected, actual) {
        (Value::Array(a), Value::Array(b)) if a.len() == b.len() => {
            let diffs: Vec<Value> = a
                .iter()
                .zip(b)
                .enumerate()
                .filter_map(|(i, (x, y))| {
                    diff_json(x, y).map(|d| {
                        let mut d = match d {
                            Value::Object(m) => m,
                            other => {
                                let mut m = serde_json::Map::new();
                                m.insert("mismatch".into(), other);
                                m
                            }
                        };
                        d.insert("index".into(), json!(i));
                        Value::Object(d)
                    })
                })
                .collect();
            if diffs.is_empty() {
                None
            } else {
                Some(Value::Array(diffs))
            }
        }
        (Value::Object(a), Value::Object(b)) => {
            let mut m = serde_json::Map::new();
            for (key, ev) in a {
                match b.get(key) {
                    Some(av) => {
                        if let Some(d) = diff_json(ev, av) {
                            m.insert(key.clone(), d);
                        }
                    }
                    None => {
                        m.insert(key.clone(), json!({"expected": ev, "actual": "<missing>"}));
                    }
                }
            }
            if m.is_empty() {
                None
            } else {
                Some(Value::Object(m))
            }
        }
        _ => Some(json!({"expected": expected, "actual": actual})),
    }
}

/// Gate 2 — MERGE probe node với mọi kiểu param, trả normalized node cho
/// Python xác nhận. Idempotent theo tên probe.
fn run_write_gate(
    client: &mut FalkorDbClient,
    graph: &str,
    extra: &Value,
) -> Result<Value, String> {
    let name = extra["name"].as_str().unwrap_or("rust_spike_probe");
    let mut params = BTreeMap::new();
    params.insert("name".to_string(), Param::Str(name.to_string()));
    params.insert("count".to_string(), Param::Int(42));
    params.insert("ratio".to_string(), Param::Float(1.5));
    params.insert("flag".to_string(), Param::Bool(true));
    params.insert(
        "tags".to_string(),
        Param::List(vec![Param::Str("rust".into()), Param::Str("spike".into())]),
    );
    let merge = "MERGE (n:SpikeProbe {name: $name}) \
                 SET n.created_by = 'rust', n.count = $count, n.ratio = $ratio, \
                     n.flag = $flag, n.tags = $tags \
                 RETURN n";
    let result = client
        .query(graph, merge, &params, Some(30_000))
        .map_err(|e| e.to_string())?;
    let created = result.statistic("Properties set");
    let node = result
        .records
        .first()
        .and_then(|row| row.first())
        .map(cortex_falkordb::normalize::normalize_value)
        .ok_or_else(|| "MERGE không trả node nào".to_string())?;
    Ok(json!({
        "merged_node": node,
        "properties_set": created,
        "internal_ms": result.internal_execution_time_ms(),
    }))
}

/// Gate 2 chiều ngược — Python MERGE trước, Rust đọc lại bằng đúng path client.
fn run_read_probe(client: &mut FalkorDbClient, graph: &str, extra: &Value) -> Result<Value, String> {
    let name = extra["name"].as_str().unwrap_or("py_spike_probe");
    let mut params = BTreeMap::new();
    params.insert("name".to_string(), Param::Str(name.to_string()));
    let query = "MATCH (n:SpikeProbe {name: $name}) RETURN n";
    let result = client.ro_query(graph, query, &params).map_err(|e| e.to_string())?;
    let nodes: Vec<Value> = result
        .records
        .iter()
        .filter_map(|row| row.first())
        .map(cortex_falkordb::normalize::normalize_value)
        .collect();
    Ok(json!({"nodes": nodes}))
}

/// Gate 3 — chạy bộ query đọc N vòng, đo latency.
fn run_latency_gate(
    client: &mut FalkorDbClient,
    graph: &str,
    fixture: &Value,
    runs: usize,
) -> Value {
    let queries: Vec<(String, String, BTreeMap<String, Param>)> = fixture["queries"]
        .as_array()
        .expect("fixture.queries")
        .iter()
        .filter(|q| q["expected"]["status"].as_str().unwrap_or("ok") == "ok")
        .map(|q| {
            (
                q["name"].as_str().unwrap_or("?").to_string(),
                q["query"].as_str().expect("query").to_string(),
                json_to_params(&q["params"]),
            )
        })
        .collect();
    let mut per_query: Vec<(String, f64)> = queries
        .iter()
        .map(|(name, _, _)| (name.clone(), 0.0))
        .collect();
    let start = Instant::now();
    let mut executed = 0usize;
    for _ in 0..runs {
        for (idx, (_, query, params)) in queries.iter().enumerate() {
            let q_start = Instant::now();
            if client.ro_query(graph, query, params).is_ok() {
                per_query[idx].1 += q_start.elapsed().as_secs_f64() * 1000.0;
                executed += 1;
            }
        }
    }
    let total_ms = start.elapsed().as_secs_f64() * 1000.0;
    json!({
        "runs": runs,
        "queries_per_run": queries.len(),
        "executed": executed,
        "total_ms": (total_ms * 1000.0).round() / 1000.0,
        "avg_ms": if executed > 0 { (total_ms / executed as f64 * 1e3).round() / 1e3 } else { 0.0 },
        "per_query_avg_ms": per_query.iter().map(|(name, ms)| {
            json!({"name": name, "avg_ms": (ms / runs as f64 * 1e3).round() / 1e3})
        }).collect::<Vec<_>>(),
    })
}

fn main() {
    let (host, port, graph, fixture_path, extra) = parse_args();
    let mode = extra["mode"].as_str().unwrap_or("read").to_string();

    let mut client = match FalkorDbClient::connect_verified(&host, port) {
        Ok(c) => c,
        Err(e) => {
            println!("{}", json!({"mode": mode, "ok": false, "error": format!("connect: {e}")}));
            std::process::exit(1);
        }
    };

    let fixture = if fixture_path.is_empty() {
        json!({"queries": []})
    } else {
        std::fs::read_to_string(&fixture_path)
            .and_then(|raw| serde_json::from_str(&raw).map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e)))
            .unwrap_or_else(|e| {
                println!("{}", json!({"mode": mode, "ok": false, "error": format!("fixture: {e}")}));
                std::process::exit(1);
            })
    };

    let outcome: Result<Value, String> = match mode.as_str() {
        "read" => Ok({
            let gate = run_read_gate(&mut client, &graph, &fixture);
            json!({"mode": "read", "ok": gate["fail"] == 0, "gate": gate})
        }),
        "write" => run_write_gate(&mut client, &graph, &extra)
            .map(|gate| json!({"mode": "write", "ok": true, "gate": gate})),
        "read-probe" => run_read_probe(&mut client, &graph, &extra)
            .map(|gate| json!({"mode": "read-probe", "ok": true, "gate": gate})),
        "latency" => {
            let runs = extra["runs"].as_u64().unwrap_or(200) as usize;
            Ok(json!({"mode": "latency", "ok": true, "gate": run_latency_gate(&mut client, &graph, &fixture, runs)}))
        }
        other => Err(format!("mode {other:?} không hỗ trợ (read|write|read-probe|latency)")),
    };

    let output = match outcome {
        Ok(mut value) => {
            if value.get("ok").is_none() {
                value["ok"] = json!(false);
            }
            value["graph"] = json!(graph);
            value
        }
        Err(e) => json!({"mode": mode, "ok": false, "error": e, "graph": graph}),
    };
    println!("{output}");
}
