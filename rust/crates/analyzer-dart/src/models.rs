//! Typed fact records — Rust port của `tools/flutter/models.py` +
//! `protocol.py` (chỉ phần shape phục vụ artifact JSON; stream validation của
//! Python không cần vì Rust tự sinh facts trong bộ nhớ).

use std::collections::BTreeMap;

use serde::Serialize;
use serde_json::Value;

/// `PROTOCOL_VERSION` (protocol.py) — artifact header `schema_version`.
pub const PROTOCOL_VERSION: &str = "1.0";

/// `analyzer_version` — Python ghi `tree-sitter-dart/{version}`. Chuỗi này
/// được nhúng vào property `analyzer_version` của MỌI relation row (qua
/// `normalize_facts`) nên graph parity yêu cầu byte-identical: grammar được
/// vendor từ đúng sdist PyPI tree-sitter-dart 0.1.0 (phase-02 entry 1).
pub const ANALYZER_VERSION: &str = "tree-sitter-dart/0.1.0";

#[derive(Debug, Clone, Serialize)]
pub struct SourceEvidence {
    pub file: String,
    pub offset: usize,
    pub length: usize,
    pub start_line: usize,
    pub start_column: usize,
    pub end_line: usize,
    pub end_column: usize,
}

#[derive(Debug, Clone, Serialize)]
pub struct HeaderRecord {
    #[serde(rename = "type")]
    pub record_type: &'static str, // "header"
    pub schema_version: String,
    pub analyzer_version: String,
    pub sdk_version: String,
    pub root: String,
    pub project_id: String,
    pub mode: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct NodeRecord {
    #[serde(rename = "type")]
    pub record_type: &'static str, // "node"
    pub identity: String,
    pub kind: String,
    pub properties: BTreeMap<String, Value>,
    pub evidence: SourceEvidence,
}

#[derive(Debug, Clone, Serialize)]
pub struct EdgeRecord {
    #[serde(rename = "type")]
    pub record_type: &'static str, // "edge"
    pub source: String,
    pub target: String,
    pub relationship: String,
    #[serde(default)]
    pub properties: BTreeMap<String, Value>,
    pub confidence: f64,
    pub evidence: SourceEvidence,
}

#[derive(Debug, Clone, Serialize)]
pub struct DiagnosticRecord {
    #[serde(rename = "type")]
    pub record_type: &'static str, // "diagnostic"
    pub severity: String,
    pub code: String,
    pub message: String,
    pub recoverable: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub evidence: Option<SourceEvidence>,
}

#[derive(Debug, Clone, Serialize)]
pub struct SummaryRecord {
    #[serde(rename = "type")]
    pub record_type: &'static str, // "summary"
    pub processed_files: usize,
    pub skipped_files: usize,
    pub error_count: usize,
    pub elapsed_ms: i64,
}

#[derive(Debug, Clone)]
pub struct AnalysisFacts {
    pub header: HeaderRecord,
    pub nodes: Vec<NodeRecord>,
    pub edges: Vec<EdgeRecord>,
    pub diagnostics: Vec<DiagnosticRecord>,
    pub summary: SummaryRecord,
}
