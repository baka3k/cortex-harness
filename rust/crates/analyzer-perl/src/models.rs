//! Port của `tools/perl/models.py` — immutable, deterministic contracts cho
//! Perl structural analysis (dataclass shapes, redaction, stable ids, JSON).
//!
//! JSON shape khớp `_to_primitive` phía Python: tuple → array, dataclass →
//! object; serde map mặc định (BTreeMap) cho sort_keys=True.

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use regex::Regex;

pub const ANALYZER_VERSION: &str = "perl-tree-sitter-v1";
pub const SUPPORTED_EXTENSIONS: [&str; 3] = [".pl", ".pm", ".t"];

/// Python `_SECRET_PATTERNS` — không có lookaround nên `regex` crate biểu
/// diễn trực tiếp được. Thứ tự apply phải giữ nguyên.
fn secret_patterns() -> [Regex; 3] {
    [
        Regex::new(r"(?i)(password|passwd|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*([^\s,;]+)")
            .expect("secret pattern 1"),
        Regex::new(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{12,}").expect("secret pattern 2"),
        Regex::new(r"\bAKIA[0-9A-Z]{16}\b").expect("secret pattern 3"),
    ]
}

/// `redact_text` — redact credential shapes rồi bound theo số KÝ TỰ
/// (Python slice theo codepoint, không phải byte).
pub fn redact_text(value: &str, limit: usize) -> String {
    let mut text = value.to_string();
    for pattern in secret_patterns().into_iter() {
        // Python: pattern.groups >= 2 ⇒ giữ group(1); ngược lại thay toàn match.
        // (captures_len đếm cả group 0 nên ngưỡng là 3.)
        if pattern.captures_len() >= 3 {
            text = pattern
                .replace_all(&text, |caps: &regex::Captures| {
                    format!("{}=<redacted>", caps.get(1).map(|m| m.as_str()).unwrap_or("None"))
                })
                .into_owned();
        } else {
            text = pattern.replace_all(&text, "<redacted>").into_owned();
        }
    }
    text.chars().take(limit).collect()
}

/// `stable_id` — checkout-independent semantic identifier.
pub fn stable_id(
    project_id: &str,
    file_path: &str,
    package: &str,
    scope: &str,
    kind: &str,
    qualified_name: &str,
) -> String {
    let material = [
        project_id.trim(),
        file_path.replace('\\', "/").trim_matches('/'),
        package.trim(),
        scope.trim(),
        kind.trim(),
        qualified_name.trim(),
    ]
    .join("\u{0}");
    let digest = Sha256::digest(material.as_bytes());
    let hex: String = digest.iter().map(|byte| format!("{byte:02x}")).collect();
    format!("perl:{kind}:{}", &hex[..24])
}

#[derive(
    Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize, Default,
)]
pub struct SourceSpan {
    pub start_line: usize,
    pub start_column: usize,
    pub end_line: usize,
    pub end_column: usize,
    pub start_byte: usize,
    pub end_byte: usize,
}

/// Python `Diagnostic` — `order=True`: so theo (code, severity, message,
/// file_path, span, details). `span: Optional` phía Python không thể so
/// None với SourceSpan (TypeError); Rust quy ước None < Some (documented).
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct Diagnostic {
    pub code: String,
    pub severity: String,
    pub message: String,
    #[serde(default)]
    pub file_path: String,
    #[serde(default)]
    pub span: Option<SourceSpan>,
    #[serde(default)]
    pub details: Vec<(String, String)>,
}

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct ParserCapabilities {
    pub analyzer_version: String,
    pub runtime_package: String,
    pub runtime_version: String,
    pub grammar_package: String,
    pub grammar_version: String,
    pub grammar_abi: usize,
    pub grammar_semantic_version: String,
    pub language_name: String,
    pub extensions: Vec<String>,
    pub supported_nodes: Vec<String>,
}

#[derive(
    Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize,
)]
pub struct DocumentationRecord {
    pub kind: String,
    pub text: String,
    pub file_path: String,
    pub span: SourceSpan,
    #[serde(default)]
    pub truncated: bool,
}

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct FileRecord {
    pub file_path: String,
    pub language: String,
    pub parser_version: String,
    pub grammar_version: String,
    pub parse_status: String,
    pub coverage: String,
    pub content_sha256: String,
    pub size_bytes: usize,
    pub line_count: usize,
    #[serde(default)]
    pub error_count: usize,
    #[serde(default)]
    pub truncated: bool,
}

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct SymbolRecord {
    pub symbol_id: String,
    pub name: String,
    pub kind: String,
    pub fq_name: String,
    pub file_path: String,
    pub span: SourceSpan,
    pub package: String,
    #[serde(default)]
    pub scope: String,
    #[serde(default)]
    pub declaration_kind: String,
    #[serde(default)]
    pub arity: usize,
    #[serde(default)]
    pub prototype: String,
    #[serde(default)]
    pub attributes: Vec<String>,
    #[serde(default)]
    pub code: String,
    #[serde(default)]
    pub documentation: String,
}

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct ImportRecord {
    pub import_id: String,
    pub kind: String,
    pub module: String,
    pub raw_text: String,
    pub file_path: String,
    pub span: SourceSpan,
    #[serde(default)]
    pub is_dynamic: bool,
    #[serde(default)]
    pub is_conditional: bool,
    #[serde(default)]
    pub resolved_path: String,
}

/// Python sort theo toàn bộ trường, trong đó `confidence` là float — Rust
/// không derive Ord trên f64 nên port tay bằng `total_cmp` (không có NaN).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ReferenceRecord {
    pub ref_id: String,
    pub source_symbol_id: String,
    pub source_name: String,
    pub target_name: String,
    pub kind: String,
    pub file_path: String,
    pub span: SourceSpan,
    pub confidence: f64,
    pub resolution_status: String,
    #[serde(default)]
    pub target_symbol_id: String,
    #[serde(default)]
    pub reason: String,
    #[serde(default)]
    pub raw_text: String,
}

impl Eq for ReferenceRecord {}

impl Ord for ReferenceRecord {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        self.ref_id
            .cmp(&other.ref_id)
            .then_with(|| self.source_symbol_id.cmp(&other.source_symbol_id))
            .then_with(|| self.source_name.cmp(&other.source_name))
            .then_with(|| self.target_name.cmp(&other.target_name))
            .then_with(|| self.kind.cmp(&other.kind))
            .then_with(|| self.file_path.cmp(&other.file_path))
            .then_with(|| self.span.cmp(&other.span))
            .then_with(|| self.confidence.total_cmp(&other.confidence))
            .then_with(|| self.resolution_status.cmp(&other.resolution_status))
            .then_with(|| self.target_symbol_id.cmp(&other.target_symbol_id))
            .then_with(|| self.reason.cmp(&other.reason))
            .then_with(|| self.raw_text.cmp(&other.raw_text))
    }
}

impl PartialOrd for ReferenceRecord {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize, Default)]
pub struct DependencyIndex {
    pub forward: Vec<(String, Vec<String>)>,
    pub reverse: Vec<(String, Vec<String>)>,
}

impl DependencyIndex {
    /// `DependencyIndex.from_mappings` — key sorted, values sorted + deduped.
    pub fn from_mappings(
        forward: &std::collections::BTreeMap<String, std::collections::BTreeSet<String>>,
        reverse: &std::collections::BTreeMap<String, std::collections::BTreeSet<String>>,
    ) -> Self {
        let normalize = |source: &std::collections::BTreeMap<
            String,
            std::collections::BTreeSet<String>,
        >| {
            source
                .iter()
                .map(|(key, values)| (key.clone(), values.iter().cloned().collect::<Vec<_>>()))
                .collect::<Vec<_>>()
        };
        Self {
            forward: normalize(forward),
            reverse: normalize(reverse),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ParsedFile {
    pub file: FileRecord,
    #[serde(default)]
    pub symbols: Vec<SymbolRecord>,
    #[serde(default)]
    pub imports: Vec<ImportRecord>,
    #[serde(default)]
    pub references: Vec<ReferenceRecord>,
    #[serde(default)]
    pub documentation: Vec<DocumentationRecord>,
    #[serde(default)]
    pub diagnostics: Vec<Diagnostic>,
}

/// `AnalysisResult` — thứ tự trường khớp dataclass Python (JSON sort key
/// nên thứ tự field declaration chỉ ảnh hưởng inside-struct, không JSON).
#[derive(Clone, Debug, Serialize)]
pub struct AnalysisResult {
    pub project_id: String,
    pub normalized_root: String,
    pub analyzer_version: String,
    pub capabilities: ParserCapabilities,
    pub coverage: String,
    pub files: Vec<FileRecord>,
    pub symbols: Vec<SymbolRecord>,
    pub imports: Vec<ImportRecord>,
    pub references: Vec<ReferenceRecord>,
    pub documentation: Vec<DocumentationRecord>,
    pub diagnostics: Vec<Diagnostic>,
    pub dependency_index: DependencyIndex,
    pub changed_paths: Vec<String>,
    pub deleted_paths: Vec<String>,
    pub counters: Vec<(String, usize)>,
}

impl AnalysisResult {
    /// `to_json(pretty=False)` — json.dumps(sort_keys=True, separators=(",",":")).
    pub fn to_json(&self) -> String {
        serde_json::to_string(self).unwrap_or_else(|_| "null".to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stable_id_matches_python_golden() {
        assert_eq!(
            stable_id("p", "lib/App.pm", "Pkg", "Pkg", "subroutine", "Pkg::sub"),
            "perl:subroutine:0dc37a1ebb5b570da8ed03a8"
        );
        // path backslash→slash, strip('/'), kind KHÔNG strip trong format.
        assert_eq!(
            stable_id("proj", "a b\\c/", " p ", "  ", " k ", " q "),
            "perl: k :bfd498df85616b4bbd57e2dd"
        );
    }

    #[test]
    fn redact_matches_python_golden() {
        assert_eq!(
            redact_text("password=hunter2 and secret: abc,def", 100), // sensitive-guard:allow (test sample)
            "password=<redacted> and secret=<redacted>,def" // sensitive-guard:allow (test sample)
        );
        assert_eq!(
            redact_text("Authorization: Bearer abcdef1234567890abcd", 100),
            "Authorization: <redacted>"
        );
        assert_eq!(redact_text("key AKIAIOSFODNN7EXAMPLE end", 100), "key <redacted> end"); // sensitive-guard:allow (AWS docs sample)
        assert_eq!(redact_text("nothing here", 5), "nothi");
        assert_eq!(
            redact_text("token access_token = abc, x", 100),
            "token access_token=<redacted>, x"
        );
    }
}
