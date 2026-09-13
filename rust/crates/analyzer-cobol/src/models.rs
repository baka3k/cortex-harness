//! Versioned, deterministic fact and parser models — port `models.py`.
//! `stable_id` dùng sha256(`\x1f`.join(parts)).hexdigest()[:24] — byte-exact.

use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

pub const SCHEMA_VERSION: &str = "1";
pub const ANALYZER_VERSION: &str = "1.0.0";

pub type Row = Map<String, Value>;

/// `stable_id` — project-scoped checkout-independent identity.
pub fn stable_id(project_id: &str, kind: &str, parts: &[&dyn std::fmt::Display]) -> String {
    let mut value = String::from(project_id);
    value.push('\u{1f}');
    value.push_str(kind);
    for part in parts {
        value.push('\u{1f}');
        value.push_str(&part.to_string());
    }
    let digest = Sha256::digest(value.as_bytes());
    let hex: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
    format!("cobol:{}:{}", kind.to_lowercase(), &hex[..24])
}

/// `SourceEvidence` — Python default: start_column=1, end_line=1, end_column=1,
/// start_byte=0, end_byte=0.
#[derive(Debug, Clone, PartialEq)]
pub struct SourceEvidence {
    pub file: String,
    pub start_line: i64,
    pub start_column: i64,
    pub end_line: i64,
    pub end_column: i64,
    pub start_byte: i64,
    pub end_byte: i64,
}

impl SourceEvidence {
    pub fn new(file: impl Into<String>, start_line: i64) -> Self {
        Self {
            file: file.into(),
            start_line,
            start_column: 1,
            end_line: 1,
            end_column: 1,
            start_byte: 0,
            end_byte: 0,
        }
    }

    #[allow(clippy::too_many_arguments)]
    pub fn full(
        file: impl Into<String>,
        start_line: i64,
        start_column: i64,
        end_line: i64,
        end_column: i64,
        start_byte: i64,
        end_byte: i64,
    ) -> Self {
        Self {
            file: file.into(),
            start_line,
            start_column,
            end_line,
            end_column,
            start_byte,
            end_byte,
        }
    }

    pub fn to_json(&self) -> Value {
        serde_json::json!({
            "file": self.file,
            "start_line": self.start_line,
            "start_column": self.start_column,
            "end_line": self.end_line,
            "end_column": self.end_column,
            "start_byte": self.start_byte,
            "end_byte": self.end_byte,
        })
    }
}

/// `Diagnostic` — severity default "warning", recoverable True.
#[derive(Debug, Clone)]
pub struct Diagnostic {
    pub code: String,
    pub message: String,
    pub severity: String,
    pub recoverable: bool,
    pub evidence: Option<SourceEvidence>,
    pub details: Value,
}

impl Diagnostic {
    pub fn new(code: &str, message: impl Into<String>, severity: &str) -> Self {
        Self {
            code: code.to_string(),
            message: message.into(),
            severity: severity.to_string(),
            recoverable: true,
            evidence: None,
            details: Value::Object(Map::new()),
        }
    }

    pub fn with_evidence(mut self, evidence: SourceEvidence) -> Self {
        self.evidence = Some(evidence);
        self
    }

    pub fn with_evidence_opt(self, evidence: Option<SourceEvidence>) -> Self {
        match evidence {
            Some(evidence) => self.with_evidence(evidence),
            None => self,
        }
    }

    pub fn with_details(mut self, details: Value) -> Self {
        self.details = details;
        self
    }

    pub fn to_json(&self) -> Value {
        serde_json::json!({
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "recoverable": self.recoverable,
            "evidence": self.evidence.as_ref().map(SourceEvidence::to_json),
            "details": self.details,
        })
    }
}

/// `SemanticNode`.
#[derive(Debug, Clone)]
pub struct SemanticNode {
    pub id: String,
    pub label: String,
    pub name: String,
    pub file_path: String,
    pub evidence: SourceEvidence,
    pub properties: Value,
    pub confidence: f64,
}

impl SemanticNode {
    pub fn to_json(&self) -> Value {
        serde_json::json!({
            "id": self.id,
            "label": self.label,
            "name": self.name,
            "file_path": self.file_path,
            "evidence": self.evidence.to_json(),
            "properties": self.properties,
            "confidence": self.confidence,
        })
    }
}

/// `SemanticEdge`.
#[derive(Debug, Clone)]
pub struct SemanticEdge {
    pub id: String,
    pub source_id: String,
    pub target_id: String,
    pub relationship: String,
    pub evidence: SourceEvidence,
    pub properties: Value,
    pub confidence: f64,
    pub dynamic: bool,
}

impl SemanticEdge {
    pub fn to_json(&self) -> Value {
        serde_json::json!({
            "id": self.id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relationship": self.relationship,
            "evidence": self.evidence.to_json(),
            "properties": self.properties,
            "confidence": self.confidence,
            "dynamic": self.dynamic,
        })
    }
}

/// `ParsedStatement`.
#[derive(Debug, Clone)]
pub struct ParsedStatement {
    pub kind: String,
    pub text: String,
    pub evidence: SourceEvidence,
    pub properties: Value,
    pub confidence: f64,
}

/// `ParsedParagraph`.
#[derive(Debug, Clone)]
pub struct ParsedParagraph {
    pub name: String,
    pub section: String,
    pub ordinal: i64,
    pub evidence: SourceEvidence,
    pub statements: Vec<ParsedStatement>,
}

/// `ParsedDataItem`.
#[derive(Debug, Clone)]
pub struct ParsedDataItem {
    pub name: String,
    pub level: i64,
    pub storage: String,
    pub evidence: SourceEvidence,
    pub picture: String,
    pub usage: String,
    pub value: String,
    pub redefines: String,
    pub occurs: String,
}

/// `ParsedCopy`.
#[derive(Debug, Clone)]
pub struct ParsedCopy {
    pub name: String,
    pub evidence: SourceEvidence,
    pub replacing: String,
}

/// `ParsedFileBinding`.
#[derive(Debug, Clone)]
pub struct ParsedFileBinding {
    pub name: String,
    pub evidence: SourceEvidence,
    pub assignment: String,
    pub has_description: bool,
}

/// `ParsedFile`.
#[derive(Debug, Clone)]
pub struct ParsedFile {
    pub path: String,
    pub program_name: String,
    pub source_format: String,
    pub dialect: String,
    pub encoding: String,
    pub is_copybook: bool,
    pub divisions: Vec<String>,
    pub sections: Vec<String>,
    pub paragraphs: Vec<ParsedParagraph>,
    pub data_items: Vec<ParsedDataItem>,
    pub copies: Vec<ParsedCopy>,
    pub file_bindings: Vec<ParsedFileBinding>,
    pub diagnostics: Vec<Diagnostic>,
    pub tree_error_count: i64,
}

/// `AnalysisSummary` + `AnalysisResult`.
pub struct AnalysisSummary {
    pub processed_files: i64,
    pub node_count: i64,
    pub edge_count: i64,
    pub diagnostic_count: i64,
    pub syntax_error_count: i64,
    pub runtime: Value,
    pub invalidated_files: i64,
}

pub struct AnalysisResult {
    pub project_id: String,
    pub root: String,
    pub nodes: Vec<SemanticNode>,
    pub edges: Vec<SemanticEdge>,
    pub diagnostics: Vec<Diagnostic>,
    pub summary: AnalysisSummary,
}

impl AnalysisResult {
    /// `to_json` — json.dumps(asdict, indent=2, sort_keys=True, ensure_ascii)
    /// + "\n".
    ///
    /// Rust dùng serde Value (BTreeMap → sorted keys, pretty 2-space); nội dung
    /// artifact là debug-plane, không so parity (khác runtime info).
    pub fn to_json(&self) -> String {
        let value = serde_json::json!({
            "project_id": self.project_id,
            "root": self.root,
            "nodes": self.nodes.iter().map(SemanticNode::to_json).collect::<Vec<_>>(),
            "edges": self.edges.iter().map(SemanticEdge::to_json).collect::<Vec<_>>(),
            "diagnostics": self.diagnostics.iter().map(Diagnostic::to_json).collect::<Vec<_>>(),
            "summary": {
                "processed_files": self.summary.processed_files,
                "node_count": self.summary.node_count,
                "edge_count": self.summary.edge_count,
                "diagnostic_count": self.summary.diagnostic_count,
                "syntax_error_count": self.summary.syntax_error_count,
                "analyzer_version": ANALYZER_VERSION,
                "schema_version": SCHEMA_VERSION,
                "runtime": self.summary.runtime,
                "invalidated_files": self.summary.invalidated_files,
            },
        });
        format!("{}\n", serde_json::to_string_pretty(&value).unwrap_or_default())
    }
}

/// `sorted_result` — nodes theo (label, file_path, id); edges theo
/// (relationship, source_id, target_id, id); diagnostics theo
/// (file, start_line, code, message) — sort ổn định (stable).
#[allow(clippy::too_many_arguments)]
pub fn sorted_result(
    project_id: &str,
    root: &str,
    mut nodes: Vec<SemanticNode>,
    mut edges: Vec<SemanticEdge>,
    mut diagnostics: Vec<Diagnostic>,
    runtime: Value,
    processed_files: i64,
    syntax_error_count: i64,
) -> AnalysisResult {
    nodes.sort_by(|a, b| {
        (&a.label, &a.file_path, &a.id).cmp(&(&b.label, &b.file_path, &b.id))
    });
    edges.sort_by(|a, b| {
        (&a.relationship, &a.source_id, &a.target_id, &a.id).cmp(&(
            &b.relationship,
            &b.source_id,
            &b.target_id,
            &b.id,
        ))
    });
    diagnostics.sort_by(|a, b| {
        let key = |item: &Diagnostic| {
            (
                item.evidence
                    .as_ref()
                    .map(|e| e.file.clone())
                    .unwrap_or_default(),
                item.evidence.as_ref().map(|e| e.start_line).unwrap_or(0),
                item.code.clone(),
                item.message.clone(),
            )
        };
        key(a).cmp(&key(b))
    });
    let node_count = nodes.len() as i64;
    let edge_count = edges.len() as i64;
    let diagnostic_count = diagnostics.len() as i64;
    AnalysisResult {
        project_id: project_id.to_string(),
        root: root.to_string(),
        nodes,
        edges,
        diagnostics,
        summary: AnalysisSummary {
            processed_files,
            node_count,
            edge_count,
            diagnostic_count,
            syntax_error_count,
            runtime,
            invalidated_files: 0,
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stable_id_matches_python() {
        // Python: stable_id("p", "CobolParagraph", "a/main.cbl", "MAINPROG", "INIT-PARA")
        // = hashlib.sha256("p\x1fCobolParagraph\x1fa/main.cbl\x1fMAINPROG\x1fINIT-PARA")[:24]
        import_hash_check();
    }

    fn import_hash_check() {
        // golden vector sinh bằng .venv/bin/python -c
        // 'from tools.cobol.models import stable_id; print(stable_id("p","CobolParagraph","a/main.cbl","MAINPROG","INIT-PARA"))'
        let expected = "cobol:cobolparagraph:d3b8635fe2a796de47abf2d6";
        let got = stable_id(
            "p",
            "CobolParagraph",
            &[&"a/main.cbl", &"MAINPROG", &"INIT-PARA"],
        );
        assert_eq!(got, expected);
        let expected_edge = "cobol:edge:f74bc207b0de0d5d3a33c227";
        let got_edge = stable_id("p", "edge", &[&"CALLS", &"s1", &"t2", &"a.cbl", &10, &7]);
        assert_eq!(got_edge, expected_edge);
    }
}
