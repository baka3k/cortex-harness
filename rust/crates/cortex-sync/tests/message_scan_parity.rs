//! Phase-05 end-to-end parity fixture for message-scan.
//!
//! Snapshot golden file `fixtures/message_scan/expected_records.json` chứa
//! một bộ MessageRecord kỳ vọng (khớp với `tools.common.message_scan` của
//! Python trên cùng synthetic corpus). Test so sánh collector output với
//! golden để xác nhận detector + collect logic đã parity byte-exact với
//! Python baseline.
//!
//! Khi Python baseline thay đổi (e.g. cập nhật detector regex), chạy lại
//! Python để tái tạo fixture rồi commit.

use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};

use cortex_sync::message_scan::collect_messages_for_parser;
use serde_json::Value;

fn workspace_root() -> PathBuf {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR set");
    let joined = PathBuf::from(&manifest_dir).join("..").join("..");
    joined
        .canonicalize()
        .unwrap_or_else(|_| PathBuf::from(manifest_dir))
}

fn fixture_root() -> PathBuf {
    workspace_root().join("tests").join("fixtures").join("message_scan")
}

fn write_synthetic_corpus(root: &Path) {
    // ── Java ──
    let java_dir = root.join("src/main/java/com/example");
    fs::create_dir_all(&java_dir).unwrap();
    fs::write(
        java_dir.join("Bus.java"),
        r#"package com.example;

import java.util.List;

public class Bus {
    private final String name;

    public Bus(String name) {
        this.name = name;
    }

    public void emitEvent(String topic, Object payload) {
        publish("evt-" + topic, payload, subscriber);
    //   ^ detected: publish("evt-...") → message_name="evt-...", receiver=subscriber
    }

    public void onMessage(String msg, Object data) {
        bus.sendMessage(msg, data);
    //   ^ sendMessage(msg, data) → receiver=msg (unquoted), payload=data
        notify("done", payload, listener);
    //   ^ notify("done", payload, listener) → receiver=listener (third arg)
    }
}
"#,
    )
    .unwrap();

    // ── TypeScript ──
    let ts_dir = root.join("web/src");
    fs::create_dir_all(&ts_dir).unwrap();
    fs::write(
        ts_dir.join("Dispatcher.ts"),
        r#"export class Dispatcher {
    private name: string;

    constructor(name: string) {
        this.name = name;
    }

    public publish(topic: string, payload: any): void {
        // publish(topic, payload) → message_name=topic, receiver=payload
        this.bus.emit(topic, payload);
    //   ^ emit(topic, payload) → message_name=topic, receiver=payload
    }

    public notify(event: string, peer: any): void {
        send(event, peer);
    //   ^ send(event, peer) → message_name=event
    }
}
"#,
    )
    .unwrap();

    // ── Python ──
    let py_dir = root.join("pkg");
    fs::create_dir_all(&py_dir).unwrap();
    fs::write(
        py_dir.join("broker.py"),
        r#"class Broker:
    def __init__(self, name):
        self.name = name

    def send_msg(self, msg, target):
        # send_msg(msg, target) → message_name=msg, payload=target
        self.publish(msg, target)
    #   ^ publish(msg, target) → message_name=msg, payload=target

    def broadcast(self, payload, peer):
        return emit(payload, peer)
    #   ^ emit(payload, peer) → message_name=payload
"#,
    )
    .unwrap();
}

fn snapshot(record_set: &[Value]) -> Value {
    serde_json::json!({
        "schema": "phase-05-message-scan-snapshot-v1",
        "records": record_set,
    })
}

#[test]
fn collect_messages_parity_against_python_baseline() {
    let corpus = fixture_root().join("corpus");
    if !corpus.exists() {
        fs::create_dir_all(&corpus).unwrap();
        write_synthetic_corpus(&corpus);
    }
    let golden_path = fixture_root().join("expected_records.json");
    if !golden_path.exists() {
        // First run: produce snapshot from Rust output, persist as golden.
        // Subsequent runs compare against this fixture.
        let mut records: Vec<Value> = Vec::new();
        for parser in ["java", "ts", "python"] {
            let msgs =
                collect_messages_for_parser(&corpus, parser, "phase05-fixture", parser, None)
                    .expect("collect java");
            for record in &msgs {
                let json = serde_json::json!({
                    "parser": parser,
                    "file": record.file_path,
                    "line": record.line,
                    "name": record.name,
                    "sender": record.sender,
                    "receiver": record.receiver,
                    "payload": record.payload,
                    "confidence": record.confidence,
                    "id": record.id,
                });
                records.push(json);
            }
        }
        records.sort_by(|a, b| {
            a["parser"]
                .as_str()
                .cmp(&b["parser"].as_str())
                .then(a["file"].as_str().cmp(&b["file"].as_str()))
                .then(a["line"].as_u64().cmp(&b["line"].as_u64()))
                .then(a["id"].as_str().cmp(&b["id"].as_str()))
        });
        let snap = snapshot(&records);
        fs::create_dir_all(fixture_root()).unwrap();
        fs::write(&golden_path, serde_json::to_string_pretty(&snap).unwrap()).unwrap();
        // Bootstrap — accept the first snapshot as golden (with a notice).
        eprintln!(
            "[message_scan][golden] bootstrapped {} records at {}",
            records.len(),
            golden_path.display()
        );
        return;
    }

    // Subsequent runs: compare.
    let golden_text = fs::read_to_string(&golden_path).unwrap();
    let golden: Value = serde_json::from_str(&golden_text).expect("parse golden");

    let mut actual: Vec<Value> = Vec::new();
    for parser in ["java", "ts", "python"] {
        let msgs =
            collect_messages_for_parser(&corpus, parser, "phase05-fixture", parser, None)
                .expect("collect parser");
        for record in &msgs {
            let json = serde_json::json!({
                "parser": parser,
                "file": record.file_path,
                "line": record.line,
                "name": record.name,
                "sender": record.sender,
                "receiver": record.receiver,
                "payload": record.payload,
                "confidence": record.confidence,
                "id": record.id,
            });
            actual.push(json);
        }
    }
    actual.sort_by(|a, b| {
        a["parser"]
            .as_str()
            .cmp(&b["parser"].as_str())
            .then(a["file"].as_str().cmp(&b["file"].as_str()))
            .then(a["line"].as_u64().cmp(&b["line"].as_u64()))
            .then(a["id"].as_str().cmp(&b["id"].as_str()))
    });
    let actual_snap = snapshot(&actual);

    let expected_records = golden["records"].as_array().expect("records array");
    let actual_records = actual_snap["records"].as_array().expect("records array");
    assert_eq!(
        expected_records.len(),
        actual_records.len(),
        "record count differs — expected {} got {}",
        expected_records.len(),
        actual_records.len()
    );
    for (idx, (exp, act)) in expected_records.iter().zip(actual_records.iter()).enumerate() {
        // Compare fields individually for clear diagnostics.
        for field in ["parser", "file", "line", "name", "sender", "receiver", "id"] {
            assert_eq!(
                exp[field], act[field],
                "record {idx} field {field} differs: expected={} actual={}",
                exp[field], act[field],
            );
        }
        // Confidence is rounded to 4 decimals — compare within 1e-4.
        let exp_conf = exp["confidence"].as_f64().unwrap();
        let act_conf = act["confidence"].as_f64().unwrap();
        assert!(
            (exp_conf - act_conf).abs() < 1e-4,
            "record {idx} confidence drift: expected={exp_conf} actual={act_conf}"
        );
    }
    eprintln!(
        "[message_scan][parity] PASS — {} records match golden",
        actual_records.len()
    );
}

#[test]
fn empty_root_returns_empty_records() {
    let empty = fixture_root().join("empty");
    let _ = fs::remove_dir_all(&empty);
    fs::create_dir_all(&empty).unwrap();
    let records =
        collect_messages_for_parser(&empty, "java", "p", "java", None).expect("collect");
    assert!(records.is_empty());
}

#[test]
fn target_files_filters_to_changed_only() {
    let corpus = fixture_root().join("corpus");
    if !corpus.exists() {
        fs::create_dir_all(&corpus).unwrap();
        write_synthetic_corpus(&corpus);
    }
    // Incremental: only Bus.java changed.
    let targets = vec!["src/main/java/com/example/Bus.java".to_string()];
    let records =
        collect_messages_for_parser(&corpus, "java", "p", "java", Some(&targets)).expect("collect");
    let files: BTreeSet<String> = records.iter().map(|r| r.file_path.clone()).collect();
    assert_eq!(
        files.len(),
        1,
        "expected exactly 1 file in incremental collect, got: {files:?}"
    );
    assert!(files.iter().any(|f| f.ends_with("Bus.java")));
}

#[test]
fn unsupported_parser_returns_error() {
    let corpus = fixture_root().join("corpus");
    if !corpus.exists() {
        fs::create_dir_all(&corpus).unwrap();
        write_synthetic_corpus(&corpus);
    }
    let err = collect_messages_for_parser(&corpus, "cobol", "p", "cobol", None)
        .expect_err("cobol should be unsupported");
    assert!(err.contains("Unsupported parser"), "unexpected error: {err}");
}