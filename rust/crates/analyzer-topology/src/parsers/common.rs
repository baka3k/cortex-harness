//! Port `parsers/common.py` — dynamic-marker diagnostics, evidence helpers.

use fancy_regex::Regex as FancyRegex;

use crate::models::{normalize_module_path, AnalysisDiagnostic, PyValue, SourceEvidence};

pub const MAX_XML_DEPTH: usize = 64;
pub const MAX_XML_NODES: usize = 50_000;

const DYNAMIC_MARKERS: &str = "(?i)(?:(?:\\$\\{?)?(?:System\\.getenv|providers\\.|project\\.findProperty|exec\\b)|\\b(?:eval|shell)\\s*\\(|`[^`]+`)";

fn dynamic_marker_re() -> FancyRegex {
    // lazy-static không cần — regex tổng chi phí thấp, parser gọi 1 lần/file.
    FancyRegex::new(DYNAMIC_MARKERS).expect("static regex")
}

/// `module_path_for_file`.
pub fn module_path_for_file(path: &str) -> String {
    let normalized = path.replace('\\', "/");
    let parent = match normalized.rfind('/') {
        Some(index) => &normalized[..index],
        None => "",
    };
    // PurePosixPath('').parent == '.' — pathlib parent của bare name là '.'.
    let parent = if parent.is_empty() { "." } else { parent };
    normalize_module_path(parent).unwrap_or_else(|_| ".".to_string())
}

/// `evidence(path, line=1, end_line=None)`.
pub fn evidence(path: &str) -> Vec<SourceEvidence> {
    vec![SourceEvidence::new(path)]
}

/// `line_number(text, offset)`.
pub fn line_number(text: &str, offset: usize) -> i64 {
    let capped = offset.min(text.len());
    text[..capped].matches('\n').count() as i64 + 1
}

/// `dynamic_diagnostics` — mỗi marker match 1 diagnostic với line.
pub fn dynamic_diagnostics(text: &str, path: &str, module_path: &str) -> Vec<AnalysisDiagnostic> {
    let mut diagnostics = Vec::new();
    let re = dynamic_marker_re();
    for matched in re.find_iter(text).flatten() {
        let start = matched.start();
        diagnostics.push(
            AnalysisDiagnostic::new(
                crate::models::diagnostic_code::DYNAMIC_EXPRESSION,
                "Dynamic descriptor expression was retained as unresolved evidence.",
            )
            .file_path(path)
            .module_path(module_path)
            .details({
                let mut details = PyValue::dict();
                details.set("line", PyValue::Int(line_number(text, start)));
                details
            }),
        );
    }
    diagnostics
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn module_path_for_parent() {
        assert_eq!(module_path_for_file("app/build.gradle"), "app");
        assert_eq!(module_path_for_file("build.gradle"), ".");
    }

    #[test]
    fn line_number_counts() {
        assert_eq!(line_number("a\nb\nc", 4), 3);
        assert_eq!(line_number("abc", 2), 1);
    }

    #[test]
    fn dynamic_markers_fire() {
        let diagnostics = dynamic_diagnostics(
            "x = System.getenv('A')\ny = eval(z)",
            "build.gradle",
            "app",
        );
        assert_eq!(diagnostics.len(), 2);
        assert_eq!(diagnostics[0].code, "dynamic_expression");
    }
}
