//! Message-scan plane (phase-05) — cross-parser detector + collector + artifact.
//!
//! Port của `code-tiny/tools/common/message_scan.py` và
//! `code-tiny/tools/common/message_detectors/`:
//!
//! - [`detectors`] — 17 detector ngôn ngữ + `GenericMessageDetector` +
//!   `get_detector`/`supported_parsers` registry.
//! - [`collect`] — `collect_messages_for_parser` (file walking + sender
//!   line-tracking + per-call field extraction + confidence scoring).
//! - [`record`] — `MessageRecord` + `stable_message_id` + `hash_vector`.
//! - [`artifact`] — `write_message_artifact` (atomic JSON write).
//! - [`extensions`] — parser → file extension map.
//! - [`graph`] — graph emission `Message` + `MessageEndpoint` qua
//!   `cortex_graph_writer::store::GraphStore` (byte-parity với
//!   `upsert_messages_to_neo4j` / cleanup queries của Python).
//!
//! ## Ownership
//!
//! Plan phase-05 chuyển message-scan từ Python children sang native code ở
//! tầng orchestrator. Lane này chạy **sau** primary parser phase, đọc trực
//! tiếp source files (không qua artifact). Graph upsert (`Message` +
//! `MessageEndpoint` nodes/rels) chạy native qua [`graph`]. Qdrant vector
//! upsert thuộc ownership của phase-06 (cortex-embed) — quyết định được ghi
//! trong report `phase05-message-scan-parity.md` phần "Ownership resolution".

pub mod artifact;
pub mod collect;
pub mod detectors;
pub mod extensions;
pub mod graph;
pub mod record;

use std::path::Path;

use crate::util;

/// `_safe_segment` Python — `[A-Za-z0-9_.-]+` keep, ngược lại `_` (fold
/// consecutive invalid chars thành 1 `_` giống Python `re.sub`), trim
/// `.`/`_` ở 2 đầu, fallback `project`.
pub fn safe_segment(value: &str) -> String {
    let mut cleaned: String = String::with_capacity(value.len());
    let mut last_was_sep = false;
    for c in value.trim().chars() {
        if c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-') {
            cleaned.push(c);
            last_was_sep = false;
        } else if !last_was_sep {
            cleaned.push('_');
            last_was_sep = true;
        }
    }
    while cleaned.starts_with(['.', '_']) {
        cleaned.remove(0);
    }
    while cleaned.ends_with(['.', '_']) {
        cleaned.pop();
    }
    if cleaned.is_empty() {
        "project".to_string()
    } else {
        cleaned
    }
}

pub use artifact::write_message_artifact;
pub use collect::collect_messages_for_parser;
pub use detectors::{get_detector, has_specific_detector, supported_parsers};
pub use extensions::{file_matches_parser, message_language, parser_extensions};
pub use graph::{
    cleanup_all_message_nodes, cleanup_message_nodes, message_endpoint_id,
    upsert_messages_to_graph,
};
pub use record::{
    stable_message_id, MessageRecord, DEFAULT_MESSAGE_VECTOR_SIZE, MESSAGE_SCHEMA_VERSION,
};

/// Per-file detector iteration entry. Used by tests and by future per-parser
/// driver loops.
pub fn detector_for(parser: &str) -> &'static dyn detectors::MessageDetector {
    detectors::get_detector(parser)
}

/// Normalize parser language alias (`c++` → `cplus`, `pascal` → `delphi`,
/// `android-kotlin` → `android`).
pub fn parser_from_language(language: &str) -> String {
    let text = language.trim().to_ascii_lowercase();
    match text.as_str() {
        "c++" | "cpp" => "cplus".to_string(),
        "pascal" => "delphi".to_string(),
        "android-kotlin" | "kotlin-android" => "android".to_string(),
        other => other.to_string(),
    }
}

/// Default Qdrant collection name for messages (Python
/// `default_message_collection_name`).
pub fn default_message_collection_name(code_collection: &str) -> String {
    let base = code_collection.trim();
    if base.is_empty() {
        return "messages".to_string();
    }
    for suffix in ["_functions", "__functions", "_function"] {
        if let Some(stripped) = base.strip_suffix(suffix) {
            return format!("{stripped}_mess");
        }
    }
    format!("{base}_mess")
}

/// Project id → lookup key for payload indexing (matches Python
/// `project_id_lookup_key` — currently a no-op normalization, kept here for
/// future-proofing parity).
pub fn project_id_lookup_key(project_id: &str) -> String {
    project_id.trim().to_string()
}

/// Normalized project_id field name on payload (`project_id_normalized`).
pub const PROJECT_ID_NORMALIZED_FIELD: &str = "project_id_normalized";

/// Ensure output directory exists; return absolute path.
pub fn ensure_output_dir(path: &Path) -> std::io::Result<()> {
    if path.exists() {
        return Ok(());
    }
    std::fs::create_dir_all(path)
}

/// Pre-flight: validate that a parser is supported by message-scan.
pub fn validate_parser(parser: &str) -> Result<(), String> {
    if supported_parsers().contains(parser) {
        Ok(())
    } else {
        Err(format!("Unsupported parser(s): {parser}"))
    }
}

#[allow(dead_code)]
pub(crate) fn path_to_string(path: &Path) -> String {
    util::path_to_string(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn safe_segment_drops_invalid_chars_and_trims() {
        assert_eq!(safe_segment("abc/def"), "abc_def");
        assert_eq!(safe_segment("a//b"), "a_b");
        assert_eq!(safe_segment("a b c"), "a_b_c");
        assert_eq!(safe_segment("___leading"), "leading");
        assert_eq!(safe_segment("trailing___"), "trailing");
        assert_eq!(safe_segment(""), "project");
        assert_eq!(safe_segment("..."), "project");
    }

    #[test]
    fn default_message_collection_handles_suffix_variants() {
        // Python checks `_functions` first — `proj__functions` matches the
        // 10-char suffix literally, so it produces `proj__mess`.
        assert_eq!(
            default_message_collection_name("proj_functions"),
            "proj_mess"
        );
        assert_eq!(
            default_message_collection_name("proj__functions"),
            "proj__mess"
        );
        assert_eq!(
            default_message_collection_name("proj_function"),
            "proj_mess"
        );
        assert_eq!(default_message_collection_name("proj"), "proj_mess");
        assert_eq!(default_message_collection_name(""), "messages");
    }

    #[test]
    fn parser_from_language_aliases() {
        assert_eq!(parser_from_language("C++"), "cplus");
        assert_eq!(parser_from_language("pascal"), "delphi");
        assert_eq!(parser_from_language("android-kotlin"), "android");
        assert_eq!(parser_from_language("java"), "java");
    }
}