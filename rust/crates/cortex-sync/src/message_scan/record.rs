//! Message record + id/ hash helpers. Port of `MessageRecord` dataclass and
//! `_stable_message_id` / `_hash_vector` of `tools/common/message_scan.py`.

use serde::{Deserialize, Serialize};

use crate::util::sha1_hex;

pub const DEFAULT_MESSAGE_VECTOR_SIZE: usize = 1024;
pub const MESSAGE_SCHEMA_VERSION: u32 = 1;

/// Stable message identity — `msg::<sha1[:24]>` over the 7-tuple
/// `(project_id, file_path, line, sender, receiver, message_name, payload)`.
pub fn stable_message_id(
    project_id: &str,
    file_path: &str,
    line: u32,
    sender: &str,
    receiver: &str,
    message_name: &str,
    payload: &str,
) -> String {
    let joined = [
        project_id.trim(),
        file_path.trim(),
        &line.to_string(),
        sender.trim(),
        receiver.trim(),
        message_name.trim(),
        payload.trim(),
    ]
    .join("||");
    format!("msg::{}", &sha1_hex(joined.as_bytes())[..24])
}

/// `hashlib.sha1(token).hexdigest()` mapped to a bucket via modular hash
/// into `buckets` (length `size`), L2-normalized — port of `_hash_vector`.
pub fn hash_vector(text: &str, size: usize) -> Vec<f32> {
    let mut buckets = vec![0.0_f32; size];
    for token in tokenize(text) {
        let digest = sha1_hex(token.as_bytes());
        let n = u64::from_str_radix(&digest[..16], 16).unwrap_or(0);
        let idx = (n % size as u64) as usize;
        buckets[idx] += 1.0;
    }
    let norm: f32 = buckets.iter().map(|v| v * v).sum::<f32>().sqrt();
    if norm <= 0.0 {
        return buckets;
    }
    buckets.iter().map(|v| v / norm).collect()
}

fn tokenize(text: &str) -> impl Iterator<Item = String> + '_ {
    // Python regex: `[A-Za-z0-9_:.]+` lowercased. We split on whitespace + a
    // small set of punctuation that the regex would naturally skip.
    let mut buf = String::new();
    let mut out = Vec::new();
    for ch in text.chars() {
        if ch.is_ascii_alphanumeric() || matches!(ch, '_' | ':' | '.') {
            buf.push(ch.to_ascii_lowercase());
        } else if !buf.is_empty() {
            out.push(std::mem::take(&mut buf));
        }
    }
    if !buf.is_empty() {
        out.push(buf);
    }
    out.into_iter()
}

/// Serialize a `MessageRecord` to JSON the way Python `json.dump` would —
/// preserving Python's `round(confidence, 4)` rounding for confidence.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct MessageRecord {
    pub id: String,
    pub name: String,
    pub sender: String,
    pub receiver: String,
    pub payload: String,
    pub response: Option<String>,
    pub explanation: String,
    pub file_path: String,
    pub line: u32,
    pub confidence: f32,
    pub language: String,
    pub project_id: String,
}

impl MessageRecord {
    pub fn new(
        project_id: &str,
        language: &str,
        file_path: &str,
        line: u32,
        sender: &str,
        receiver: &str,
        message_name: &str,
        payload: &str,
        explanation: &str,
        confidence: f32,
    ) -> Self {
        let id = stable_message_id(project_id, file_path, line, sender, receiver, message_name, payload);
        Self {
            id,
            name: message_name.to_string(),
            sender: sender.to_string(),
            receiver: receiver.to_string(),
            payload: payload.to_string(),
            response: None,
            explanation: explanation.to_string(),
            file_path: file_path.to_string(),
            line,
            confidence: round_confidence(confidence),
            language: language.to_string(),
            project_id: project_id.to_string(),
        }
    }

    /// Override id with caller-supplied value (kept for parity test fixtures).
    pub fn with_id(mut self, id: String) -> Self {
        self.id = id;
        self
    }
}

fn round_confidence(value: f32) -> f32 {
    // Python `round(confidence, 4)` — banker's rounding via `f32::round`.
    let factor = 10_000.0_f32;
    (value * factor).round() / factor
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stable_id_is_deterministic_and_prefixed() {
        let id1 = stable_message_id("p", "src/A.java", 12, "S", "R", "M", "P");
        let id2 = stable_message_id("p", "src/A.java", 12, "S", "R", "M", "P");
        assert_eq!(id1, id2);
        assert!(id1.starts_with("msg::"));
        assert_eq!(id1.len(), "msg::".len() + 24);
    }

    #[test]
    fn hash_vector_normalizes_and_deterministic() {
        let v1 = hash_vector("hello world hello", 64);
        let v2 = hash_vector("hello world hello", 64);
        assert_eq!(v1, v2);
        let norm: f32 = v1.iter().map(|x| x * x).sum::<f32>().sqrt();
        assert!((norm - 1.0).abs() < 1e-4 || norm == 0.0);
        assert_eq!(v1.len(), 64);
    }

    #[test]
    fn hash_vector_zero_norm_returns_zero_vector() {
        let v = hash_vector("$$$%%%", 32);
        assert!(v.iter().all(|x| *x == 0.0));
        assert_eq!(v.len(), 32);
    }
}