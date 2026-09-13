//! Port `tools/shell/models.py` — dataclass shapes khớp `to_dict()`.

use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
pub struct ShellDiagnostic {
    pub code: String,
    pub message: String,
    pub file_path: String,
    pub line: i64,
    pub severity: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct ShellFunction {
    pub symbol_id: String,
    pub name: String,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    pub code: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct ShellInvocation {
    pub symbol_id: String,
    pub source_id: String,
    pub source_label: String,
    pub file_path: String,
    pub line: i64,
    pub ordinal: i64,
    pub raw_command: String,
    pub command_name: String,
    pub dynamic: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct ShellRelation {
    pub source_id: String,
    pub source_label: String,
    pub target_id: String,
    pub target_label: String,
    pub rel_type: String,
    pub line: i64,
    pub raw_target: String,
    pub resolved: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct ShellFile {
    pub file_path: String,
    pub line_count: i64,
    pub encoding: String,
    pub functions: Vec<ShellFunction>,
    pub invocations: Vec<ShellInvocation>,
    pub relations: Vec<ShellRelation>,
    pub diagnostics: Vec<ShellDiagnostic>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ShellAnalysisResult {
    pub project_id: String,
    pub files: Vec<ShellFile>,
    pub changed_paths: Vec<String>,
    pub deleted_paths: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProgramMapping {
    pub program_id: String,
    pub source_path: String,
    pub evidence_hash: String,
}
