//! `dev journal status/purge` — native journal inspection and safe purge
//! (the retired bridge ops `journal_status` / `journal_purge` from phase-03;
//! see `crate::journalx` for the ported scope-lock + purge validation).

use crate::parser::Matches;
use crate::util::echo;
use serde_json::Value;

pub fn status(m: &Matches) {
    let payload = match crate::journalx::status_payload(&m.value_or("--journal-path", "")) {
        Ok(v) => v,
        Err(line) => {
            eprintln!("{line}");
            std::process::exit(1);
        }
    };
    if m.flag("--json-output") {
        echo(&to_sorted_json(&payload));
        return;
    }
    let resolved = payload
        .get("journal_path")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    echo(&format!("Journal: {}", resolved));
    let runs = payload
        .get("runs")
        .and_then(|r| r.as_array())
        .cloned()
        .unwrap_or_default();
    if runs.is_empty() {
        echo("  no runs");
        return;
    }
    for item in &runs {
        echo(&format!(
            "  run={} parser={} status={} resumed={} produced={} acked={} \
             pending={} retrying={} reconciling={} blocked={} bytes={} \
             oldest_age={} next={}",
            jget(item, "run_id"),
            jget(item, "parser"),
            jget(item, "status"),
            jget(item, "resumed").to_lowercase(),
            jget(item, "produced"),
            jget(item, "acked"),
            jget(item, "pending"),
            jget(item, "retrying"),
            jget(item, "reconciling"),
            jget(item, "blocked"),
            jget(item, "artifact_bytes"),
            jget(item, "oldest_unfinished_age_seconds"),
            jget(item, "next_action"),
        ));
    }
}

pub fn purge(m: &Matches) {
    let payload = crate::journalx::purge(
        &m.value_or("--journal-path", ""),
        &m.value_or("--run-id", ""),
        &m.value_or("--project-id", ""),
        &m.value_or("--root", ""),
    );
    match payload {
        Ok(value) => echo(&to_sorted_json(&value)),
        Err(line) => {
            eprintln!("{line}");
            std::process::exit(1);
        }
    }
}

fn jget(v: &Value, key: &str) -> String {
    match v.get(key) {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Bool(b)) => b.to_string(),
        Some(Value::Number(n)) => n.to_string(),
        Some(Value::Null) | None => "None".to_string(),
        Some(other) => other.to_string(),
    }
}

fn to_sorted_json(v: &Value) -> String {
    fn sort(value: &Value) -> Value {
        match value {
            Value::Object(map) => {
                let mut keys: Vec<&String> = map.keys().collect();
                keys.sort();
                let mut out = serde_json::Map::new();
                for k in keys {
                    out.insert(k.clone(), sort(&map[k]));
                }
                Value::Object(out)
            }
            Value::Array(items) => Value::Array(items.iter().map(sort).collect()),
            other => other.clone(),
        }
    }
    serde_json::to_string(&sort(v)).unwrap_or_else(|_| "{}".to_string())
}
