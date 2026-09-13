//! Port `tools/shell/pipeline.py` — scan .sh + legacy decode + parse.

use std::collections::BTreeSet;
use std::path::Path;

use cortex_analyzer_framework::scan::matches_extra_ignore;

use crate::models::{ShellAnalysisResult, ShellFile};
use crate::parser::{py_splitlines, ShellParser};

const SKIP_DIRS: [&str; 6] = [".git", ".venv", "node_modules", "build", "dist", "target"];

/// `scan_shell_files` — walk với skip set RIÊNG của shell (không phải
/// COMMON_SCAN_EXCLUDE), trả rel-posix sorted.
pub fn scan_shell_files(root: &Path) -> Vec<String> {
    let mut paths: BTreeSet<String> = BTreeSet::new();
    walk(root, root, &mut paths);
    paths.into_iter().collect()
}

fn walk(root: &Path, dir: &Path, paths: &mut BTreeSet<String>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(_) => return,
    };
    let mut subdirs: Vec<std::path::PathBuf> = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().to_string();
        if entry.file_type().map(|t| t.is_dir()).unwrap_or(false) {
            if !SKIP_DIRS.contains(&name.as_str()) && !matches_extra_ignore(&name) {
                subdirs.push(path);
            }
            continue;
        }
        if name.to_lowercase().ends_with(".sh") {
            paths.insert(cortex_analyzer_framework::scan::rel_posix(root, &path));
        }
    }
    for sub in subdirs {
        walk(root, &sub, paths);
    }
}

#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct LegacyText {
    pub text: String,
    pub encoding: &'static str,
    pub lossy: bool,
}

/// `decode_legacy_bytes` — BOM → NUL-threshold → utf-8 → cp932 → cp1252.
/// (cp932 decode dùng SHIFT_JIS của encoding_rs — khác biệt vài byte hiếm
/// so với cp932 Windows variant của Python, corpus .sh thực tế là ASCII.)
pub fn decode_legacy_bytes(data: &[u8]) -> LegacyText {
    use encoding_rs::*;

    if data.starts_with(&[0xff, 0xfe]) {
        let (text, _, _) = UTF_16LE.decode(&data[2..]);
        return LegacyText {
            text: text.to_string(),
            encoding: "utf-16-le",
            lossy: false,
        };
    }
    if data.starts_with(&[0xfe, 0xff]) {
        let (text, _, _) = UTF_16BE.decode(&data[2..]);
        return LegacyText {
            text: text.to_string(),
            encoding: "utf-16-be",
            lossy: false,
        };
    }
    if data.starts_with(&[0xef, 0xbb, 0xbf]) {
        let (text, _, _) = UTF_8.decode(&data[3..]);
        return LegacyText {
            text: text.to_string(),
            encoding: "utf-8-sig",
            lossy: false,
        };
    }

    let sample = &data[..std::cmp::min(512, data.len())];
    if !sample.is_empty() {
        let odd_nuls = sample[1..].iter().step_by(2).filter(|&&b| b == 0).count();
        let even_nuls = sample.iter().step_by(2).filter(|&&b| b == 0).count();
        let threshold = std::cmp::max(2, sample.len() / 8);
        if odd_nuls >= threshold {
            let (text, _, _) = UTF_16LE.decode(data);
            return LegacyText {
                text: text.to_string(),
                encoding: "utf-16-le",
                lossy: false,
            };
        }
        if even_nuls >= threshold {
            let (text, _, _) = UTF_16BE.decode(data);
            return LegacyText {
                text: text.to_string(),
                encoding: "utf-16-be",
                lossy: false,
            };
        }
    }

    match std::str::from_utf8(data) {
        Ok(text) => LegacyText {
            text: text.to_string(),
            encoding: "utf-8",
            lossy: false,
        },
        Err(_) => {
            let (text, _, _) = SHIFT_JIS.decode(data);
            // encoding_rs không fail — heuristic: nếu còn replacement char
            // nhiều thì coi như cp1252 lossy như Python.
            let replacement_count = text.matches('\u{fffd}').count();
            if replacement_count > 0 && replacement_count as f64 > data.len() as f64 * 0.01 {
                let (text, _, _) = WINDOWS_1252.decode(data);
                LegacyText {
                    text: text.to_string(),
                    encoding: "cp1252",
                    lossy: true,
                }
            } else {
                LegacyText {
                    text: text.to_string(),
                    encoding: "cp932",
                    lossy: false,
                }
            }
        }
    }
}

pub fn read_legacy_text(path: &Path) -> LegacyText {
    let data = std::fs::read(path).unwrap_or_default();
    decode_legacy_bytes(&data)
}

/// `run_shell_analysis`.
pub fn run_shell_analysis(
    root: &Path,
    project_id: &str,
    changed_paths: Option<&[String]>,
    deleted_paths: &[String],
) -> ShellAnalysisResult {
    let root_real = std::fs::canonicalize(root).unwrap_or_else(|_| root.to_path_buf());
    let selected: Vec<String> = match changed_paths {
        None => scan_shell_files(&root_real),
        Some(changed) => changed
            .iter()
            .map(|path| path.replace('\\', "/"))
            .filter(|path| path.to_lowercase().ends_with(".sh"))
            .collect::<BTreeSet<String>>()
            .into_iter()
            .collect(),
    };
    let parser = ShellParser::new();
    let mut files: Vec<ShellFile> = Vec::new();
    for relative_path in &selected {
        let absolute_path = root_real.join(relative_path);
        if !absolute_path.is_file() {
            continue;
        }
        let decoded = read_legacy_text(&absolute_path);
        let mut parsed =
            parser.parse_shell_text(&decoded.text, relative_path, &root_real);
        parsed.encoding = decoded.encoding.to_string();
        // diagnostics của encoding giữ ở đây không đưa vào file (Python chỉ
        // giữ trong LegacyTextResult, ShellFile.diagnostics là parse-level).
        let _ = py_splitlines; // giữ helper sống cho test
        files.push(parsed);
    }
    ShellAnalysisResult {
        project_id: project_id.to_string(),
        files,
        changed_paths: selected,
        deleted_paths: deleted_paths.to_vec(),
    }
}

#[cfg(test)]
mod tests {
    use super::py_splitlines;

    #[test]
    fn splitlines_matches_python_semantics() {
        assert_eq!(py_splitlines("a\nb\nc"), vec!["a", "b", "c"]);
        assert_eq!(py_splitlines("a\nb\n"), vec!["a", "b"]);
        assert_eq!(py_splitlines("a\r\nb"), vec!["a", "b"]);
        assert_eq!(py_splitlines("a\rb"), vec!["a", "b"]);
        assert_eq!(py_splitlines(""), Vec::<&str>::new());
        assert_eq!(py_splitlines("\n"), vec![""]);
    }
}
