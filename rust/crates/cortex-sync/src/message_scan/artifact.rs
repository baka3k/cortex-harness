//! `write_message_artifact` — port của Python `write_message_artifact`.
//!
//! Ghi JSON artifact với schema:
//! ```json
//! {
//     "schema_version": 1,
//     "generated_at": "<iso8601 utc z>",
//     "project_id": "...",
//     "project_name": "...",
//     "parser": "...",
//     "commit_sha_before": "...",
//     "commit_sha_after": "...",
//     "message_count": N,
//     "messages": [
//!         {
//!             "id": "msg::...",
//!             "name": "...",
//!             "sender": "...",
//!             "receiver": "...",
//!             "payload": "...",
//!             "response": null,
//!             "explanation": "...",
//!             "source": {"file": "...", "line": 1},
//!             "confidence": 0.55,
//!             "language": "..."
//!         }, ...
//!     ]
//! }
//! ```

use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value};

use crate::util;

use super::record::{MessageRecord, MESSAGE_SCHEMA_VERSION};
use super::safe_segment;

/// `safe_cache_root` của Python — `output_dir` nếu có, ngược lại derive từ
/// `cache_dir/message_scan_artifacts/<root-hash>`.
pub fn safe_cache_root(cache_dir: Option<&Path>, root: &Path) -> PathBuf {
    if let Some(dir) = cache_dir {
        return dir.join("message_scan_artifacts").join(root_hash(root));
    }
    PathBuf::from(".cache/message_scan_artifacts").join(root_hash(root))
}

fn root_hash(root: &Path) -> String {
    let abs = util::realpath(&util::path_to_string(root));
    let digest = util::sha1_hex(util::path_to_string(&abs).as_bytes());
    digest[..10].to_string()
}

#[allow(clippy::too_many_arguments)]
pub fn write_message_artifact(
    root: &Path,
    parser: &str,
    project_id: &str,
    project_name: &str,
    records: &[MessageRecord],
    output_dir: Option<&Path>,
    cache_dir: Option<&Path>,
    commit_sha_before: &str,
    commit_sha_after: &str,
) -> std::io::Result<PathBuf> {
    let base_dir = if let Some(dir) = output_dir {
        let abs = util::absolute(&util::path_to_string(dir));
        fs::create_dir_all(&abs)?;
        abs
    } else {
        safe_cache_root(cache_dir, root)
    };
    let project_dir = base_dir.join(safe_segment(project_id));
    fs::create_dir_all(&project_dir)?;
    let output_path = project_dir.join(format!("{}_messages.json", safe_segment(parser)));

    let mut messages = Vec::with_capacity(records.len());
    for record in records {
        let mut obj = Map::new();
        obj.insert("id".into(), Value::String(record.id.clone()));
        obj.insert("name".into(), Value::String(record.name.clone()));
        obj.insert("sender".into(), Value::String(record.sender.clone()));
        obj.insert("receiver".into(), Value::String(record.receiver.clone()));
        obj.insert("payload".into(), Value::String(record.payload.clone()));
        obj.insert(
            "response".into(),
            record
                .response
                .as_ref()
                .map(|s| Value::String(s.clone()))
                .unwrap_or(Value::Null),
        );
        obj.insert(
            "explanation".into(),
            Value::String(record.explanation.clone()),
        );
        let mut source = Map::new();
        source.insert("file".into(), Value::String(record.file_path.clone()));
        source.insert("line".into(), Value::Number(record.line.into()));
        obj.insert("source".into(), Value::Object(source));
        obj.insert(
            "confidence".into(),
            Value::Number(serde_json::Number::from_f64(record.confidence as f64).unwrap()),
        );
        obj.insert("language".into(), Value::String(record.language.clone()));
        messages.push(Value::Object(obj));
    }

    let payload = json!({
        "schema_version": MESSAGE_SCHEMA_VERSION,
        "generated_at": util::now_iso(),
        "project_id": project_id,
        "project_name": project_name,
        "parser": parser,
        "commit_sha_before": commit_sha_before,
        "commit_sha_after": commit_sha_after,
        "message_count": records.len(),
        "messages": messages,
    });

    let serialized = serde_json::to_string_pretty(&payload)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?;
    let temp_path = output_path.with_extension("json.tmp");
    fs::write(&temp_path, serialized)?;
    fs::rename(&temp_path, &output_path)?;
    Ok(output_path)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::message_scan::record::MessageRecord;

    #[test]
    fn artifact_is_written_atomically_and_parses_back() {
        let temp = std::env::temp_dir().join("cortex-message-scan-artifact-test");
        let _ = fs::remove_dir_all(&temp);
        let root = std::env::temp_dir();
        let records = vec![MessageRecord::new(
            "p",
            "java",
            "src/A.java",
            12,
            "Foo",
            "Bus",
            "evt",
            "payload",
            "Foo.publish() inferred by java detector",
            0.95,
        )];
        let out = write_message_artifact(
            &root,
            "java",
            "p",
            "p",
            &records,
            Some(&temp),
            None,
            "before",
            "after",
        )
        .unwrap();
        let text = fs::read_to_string(&out).unwrap();
        let parsed: Value = serde_json::from_str(&text).unwrap();
        assert_eq!(parsed["schema_version"], json!(MESSAGE_SCHEMA_VERSION));
        assert_eq!(parsed["message_count"], json!(1));
        assert_eq!(parsed["messages"][0]["id"], json!(records[0].id));
        assert_eq!(parsed["messages"][0]["source"]["line"], json!(12));
    }
}