//! Port `vb_analyzer_base._scan_vb_files` — walk root với skip-list RIÊNG của
//! vb_analyzer_base (`_SKIP_DIRS`, không phải COMMON_SCAN_EXCLUDE), lọc theo
//! extension của dialect rồi route qua `VBPathClassifier`; kết quả SORTED.

use std::path::PathBuf;

use crate::classifier::{filename_ext_lower, VBPathClassifier};
use cortex_analyzer_framework::scan::{matches_extra_ignore, rel_posix};

/// `_SOURCE_EXTS` — mirror vb_analyzer_base (`.ctl`/`.pag` vẫn trong tập
/// extension vb6 nhưng classifier trả None ⇒ dead extension giữ nguyên hành vi).
pub fn source_exts(dialect: &str) -> &'static [&'static str] {
    match dialect {
        "vbnet" => &[".vb"],
        "vb6" => &[".bas", ".cls", ".frm", ".ctl", ".pag"],
        "vba" => &[".bas", ".cls", ".frm"],
        "vbscript" => &[".vbs", ".wsf", ".asp", ".hta"],
        _ => &[],
    }
}

/// `_SKIP_DIRS` — đúng bộ của vb_analyzer_base.
pub const SKIP_DIRS: [&str; 13] = [
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".cache",
    "build",
    "dist",
    "out",
    "target",
];

/// `_scan_vb_files` — trả danh sách đường dẫn (root-joined) SORTED theo chuỗi.
pub fn scan_vb_files(root: &std::path::Path, dialect: &str) -> Vec<PathBuf> {
    let classifier = VBPathClassifier::new(root);
    let exts = source_exts(dialect);
    let mut files: Vec<PathBuf> = Vec::new();
    walk(root, root, dialect, exts, &classifier, &mut files);
    files.sort_by(|a, b| a.to_string_lossy().cmp(&b.to_string_lossy()));
    files
}

fn walk(
    root: &std::path::Path,
    dir: &std::path::Path,
    dialect: &str,
    exts: &[&str],
    classifier: &VBPathClassifier,
    files: &mut Vec<PathBuf>,
) {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(_) => return,
    };
    let mut subdirs: Vec<PathBuf> = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().to_string();
        let file_type = match entry.file_type() {
            Ok(t) => t,
            Err(_) => continue,
        };
        if file_type.is_dir() {
            if !SKIP_DIRS.contains(&name.as_str())
                && !name.starts_with('.')
                && !matches_extra_ignore(&name)
            {
                subdirs.push(path);
            }
            continue;
        }
        let ext = filename_ext_lower(&name);
        if !exts.is_empty() && !exts.contains(&ext.as_str()) {
            continue;
        }
        let rel = rel_posix(root, &path);
        if classifier.select_parser_for_path(&rel).as_deref() == Some(dialect) {
            files.push(path);
        }
    }
    // os.walk topdown — đệ quy sau khi xử lý files của dir hiện tại.
    for sub in subdirs {
        walk(root, &sub, dialect, exts, classifier, files);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scan_routes_and_sorts() {
        let dir = std::env::temp_dir().join(format!("vb_scan_test_{}", std::process::id()));
        let sub = dir.join("sub");
        std::fs::create_dir_all(&sub).unwrap();
        std::fs::write(dir.join("Main.vb"), "Class A\nEnd Class\n").unwrap();
        std::fs::write(sub.join("Helper.vb"), "Class B\nEnd Class\n").unwrap();
        // node trong skip list + file sai ext
        std::fs::create_dir_all(dir.join("build")).unwrap();
        std::fs::write(dir.join("build/Ignored.vb"), "Class C\nEnd Class\n").unwrap();
        std::fs::write(dir.join("notes.txt"), "x").unwrap();
        // .bas KHÔNG thuộc dialect vbnet dù classifier chọn vb6
        std::fs::write(dir.join("Module1.bas"), "Sub M()\nEnd Sub\n").unwrap();

        let files = scan_vb_files(&dir, "vbnet");
        let rels: Vec<String> = files
            .iter()
            .map(|p| rel_posix(&dir, p))
            .collect();
        assert_eq!(rels, vec!["Main.vb", "sub/Helper.vb"]);
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn vbscript_scan_includes_asp_with_classic_patterns() {
        let dir = std::env::temp_dir().join(format!("vb_scan_test2_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("boot.vbs"), "Sub Main()\nEnd Sub\n").unwrap();
        std::fs::write(
            dir.join("page.asp"),
            "<%\nResponse.Write Request.QueryString(\"x\")\n%>\n",
        )
        .unwrap();
        std::fs::write(dir.join("aspnet.asp"), "<%@ Page Language=\"C#\" %>\n").unwrap();
        let files = scan_vb_files(&dir, "vbscript");
        let rels: Vec<String> = files.iter().map(|p| rel_posix(&dir, p)).collect();
        assert_eq!(rels, vec!["boot.vbs", "page.asp"]);
        std::fs::remove_dir_all(&dir).ok();
    }
}
