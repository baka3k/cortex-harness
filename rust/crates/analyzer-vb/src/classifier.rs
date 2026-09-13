//! Port `vb_path_classifier.py::VBPathClassifier` — routing extension-based
//! + heuristic nội dung cho overlap .bas/.cls/.frm (vb6 vs vba) và .asp.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::LazyLock;

use regex::Regex;

use crate::pycompat::py_splitext;

static VB6_PROJECT_EXTS: [&str; 2] = [".vbp", ".vbw"];

static VB6_STRONG_PATTERNS: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    vec![
        Regex::new(r"(?im)^\s*VERSION\s+5\.00").unwrap(),
        Regex::new(r"(?i)\bBegin\s+VB\.").unwrap(),
        Regex::new(r"(?i)\bAttribute\s+VB_GlobalNameSpace\b").unwrap(),
    ]
});

static VBA_STRONG_PATTERNS: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    vec![
        Regex::new(r"(?i)\bThisWorkbook\b").unwrap(),
        Regex::new(r"(?i)\bWorksheets?\s*\(").unwrap(),
        Regex::new(r"(?i)\bWorkbook\b").unwrap(),
        Regex::new(r"(?i)\bApplication\.WorksheetFunction\b").unwrap(),
        Regex::new(r"(?i)\bRange\s*\(").unwrap(),
        Regex::new(r"(?i)\bActiveWorkbook\b").unwrap(),
    ]
});

static VBA_OFFICE_PATTERNS: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    vec![
        Regex::new(r"(?i)\b(ActiveSheet|ActiveCell|Selection)\b").unwrap(),
        Regex::new(r"(?i)\bApplication\.(Excel|Word|Access|PowerPoint|Outlook)\b").unwrap(),
        Regex::new(r"(?i)\bCells\s*\(").unwrap(),
        Regex::new(r"(?i)\bRange\s*\(").unwrap(),
    ]
});

static VB6_CONTROL_PATTERNS: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    vec![
        Regex::new(r"(?i)\bBegin\s+(VB\.|Form\.)").unwrap(),
        Regex::new(r"(?i)\bAttribute\s+VB_\w+\b").unwrap(),
        Regex::new(r"(?i)\b(Click|DoubleClick|MouseDown|MouseUp|KeyDown|KeyUp|KeyPress)\b").unwrap(),
    ]
});

static VBSCRIPT_HINTS: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    vec![
        Regex::new(r#"(?i)\bLanguage\s*=\s*"?VBScript"?"#).unwrap(),
        Regex::new(r"(?i)<%").unwrap(),
    ]
});

static ASP_NON_VBSCRIPT_PATTERNS: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    vec![
        Regex::new(r"(?i)<%@").unwrap(), // ASP.NET
        Regex::new(r#"(?i)<\s*%@\s*Page\s+Language="?C#""#).unwrap(), // ASP.NET C#
        Regex::new(r#"(?i)<\s*%@\s*Language="?JScript""#).unwrap(), // JScript
        Regex::new(r"(?i)<\?php").unwrap(),                            // PHP
        Regex::new(r"(?i)<jsp:").unwrap(),                             // JSP
    ]
});

static CLASSIC_VBSCRIPT_PATTERNS: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    vec![
        Regex::new(r"(?i)\bResponse\.(Write|End|Redirect)\b").unwrap(),
        Regex::new(r"(?i)\bRequest\.(QueryString|Form|ServerVariables)\b").unwrap(),
        Regex::new(r"(?i)\bServer\.(CreateObject|MapPath)\b").unwrap(),
        Regex::new(r"(?i)\bSession\s*\(").unwrap(),
        Regex::new(r"(?i)\bApplication\s*\(").unwrap(),
    ]
});

/// `str.strip("./")` — bỏ TẤT CẢ ký tự '.'/'/' ở HAI ĐẦU.
fn strip_dot_slash(path: &str) -> &str {
    path.trim_matches(|c| c == '.' || c == '/')
}

/// Python `os.path.splitext(name.lower())[1]` cho FILE NAME (không separator).
pub fn filename_ext_lower(name: &str) -> String {
    let lowered = name.to_lowercase();
    let (_, ext) = py_splitext(&lowered);
    ext
}

pub struct VBPathClassifier {
    root: PathBuf,
    vb6_ancestor_cache: std::sync::Mutex<HashMap<String, bool>>,
    vbnet_ancestor_cache: std::sync::Mutex<HashMap<String, bool>>,
    content_cache: std::sync::Mutex<HashMap<String, String>>,
}

impl VBPathClassifier {
    pub fn new(root: &Path) -> Self {
        // os.path.realpath(os.path.abspath(root))
        let abs = cortex_analyzer_framework::cli::abs_root(&root.to_string_lossy());
        let real = std::fs::canonicalize(&abs).unwrap_or(abs);
        Self {
            root: real,
            vb6_ancestor_cache: std::sync::Mutex::new(HashMap::new()),
            vbnet_ancestor_cache: std::sync::Mutex::new(HashMap::new()),
            content_cache: std::sync::Mutex::new(HashMap::new()),
        }
    }

    /// `select_parser_for_path` — trả Some(dialect) hoặc None.
    pub fn select_parser_for_path(&self, rel_path: &str) -> Option<String> {
        self.select_parser_for_path_mode(rel_path, "heuristic")
    }

    pub fn select_parser_for_path_mode(&self, rel_path: &str, owner_mode: &str) -> Option<String> {
        let normalized = rel_path.replace('\\', "/");
        let rel = strip_dot_slash(&normalized);
        if rel.is_empty() {
            return None;
        }
        let lower = rel.to_lowercase();
        let (_, ext) = py_splitext(&lower);

        if VB6_PROJECT_EXTS.contains(&ext.as_str()) || ext == ".frx" {
            return Some("vb6".to_string());
        }
        if ext == ".vbproj" || ext == ".vbproj.user" || ext == ".sln" {
            return Some("vbnet".to_string());
        }
        if ext == ".vbs" || ext == ".wsf" || ext == ".hta" {
            return Some("vbscript".to_string());
        }
        if ext == ".asp" {
            return if self.looks_vbscript(rel) {
                Some("vbscript".to_string())
            } else {
                None
            };
        }
        if ext == ".vb" {
            // By contract, .vb belongs to VB.NET context — luôn route vbnet.
            return Some("vbnet".to_string());
        }
        if ext == ".bas" || ext == ".cls" || ext == ".frm" {
            return Some(self.classify_vb6_vba_overlap(rel, owner_mode));
        }
        None
    }

    fn classify_vb6_vba_overlap(&self, rel_path: &str, owner_mode: &str) -> String {
        let mode = owner_mode.trim().to_lowercase();
        if mode == "prefer-vb6" {
            return "vb6".to_string();
        }
        if mode == "prefer-vba" {
            return "vba".to_string();
        }

        let mut vb6_score = 0i32;
        let mut vba_score = 0i32;

        if self.has_vb6_project_ancestor(rel_path) {
            vb6_score += 3;
        }
        if self.has_vbnet_project_ancestor(rel_path) {
            vb6_score += 1;
        }

        let text = self.read_limited(rel_path);
        if !text.is_empty() {
            for pattern in VB6_STRONG_PATTERNS.iter() {
                if pattern.is_match(&text) {
                    vb6_score += 2;
                }
            }
            for pattern in VBA_STRONG_PATTERNS.iter() {
                if pattern.is_match(&text) {
                    vba_score += 2;
                }
            }
            for pattern in VBA_OFFICE_PATTERNS.iter() {
                if pattern.is_match(&text) {
                    vba_score += 1;
                }
            }
            for pattern in VB6_CONTROL_PATTERNS.iter() {
                if pattern.is_match(&text) {
                    vb6_score += 1;
                }
            }
        }

        // Ambiguous overlap mặc định là vb6.
        if vba_score > vb6_score {
            "vba".to_string()
        } else {
            "vb6".to_string()
        }
    }

    fn looks_vbscript(&self, rel_path: &str) -> bool {
        let text = self.read_limited(rel_path);
        if text.is_empty() {
            return true;
        }
        if ASP_NON_VBSCRIPT_PATTERNS.iter().any(|p| p.is_match(&text)) {
            return false;
        }
        if VBSCRIPT_HINTS.iter().any(|p| p.is_match(&text)) {
            return true;
        }
        if CLASSIC_VBSCRIPT_PATTERNS.iter().any(|p| p.is_match(&text)) {
            return true;
        }
        true
    }

    fn has_project_ancestor(
        &self,
        rel_path: &str,
        cache: &std::sync::Mutex<HashMap<String, bool>>,
        predicate: &dyn Fn(&str) -> bool,
    ) -> bool {
        // os.path.dirname(rel) + strip("/")
        let mut probe = match rel_path.rfind('/') {
            Some(pos) => rel_path[..pos].trim_matches('/').to_string(),
            None => String::new(),
        };
        loop {
            let key = if probe.is_empty() { "." } else { &probe };
            let cached = {
                let guard = cache.lock().unwrap();
                guard.get(key).copied()
            };
            let found = match cached {
                Some(found) => found,
                None => {
                    let base: PathBuf = if probe.is_empty() {
                        self.root.clone()
                    } else {
                        self.root.join(&probe)
                    };
                    let mut found = false;
                    if let Ok(entries) = std::fs::read_dir(&base) {
                        for entry in entries.flatten() {
                            let name = entry.file_name().to_string_lossy().to_lowercase();
                            if predicate(&name) {
                                found = true;
                                break;
                            }
                        }
                    }
                    cache.lock().unwrap().insert(key.to_string(), found);
                    found
                }
            };
            if found {
                return true;
            }
            if probe.is_empty() {
                return false;
            }
            probe = match probe.rfind('/') {
                Some(pos) => probe[..pos].to_string(),
                None => String::new(),
            };
        }
    }

    fn has_vb6_project_ancestor(&self, rel_path: &str) -> bool {
        self.has_project_ancestor(rel_path, &self.vb6_ancestor_cache, &|name| {
            let (_, ext) = py_splitext(name);
            VB6_PROJECT_EXTS.contains(&ext.as_str())
        })
    }

    fn has_vbnet_project_ancestor(&self, rel_path: &str) -> bool {
        self.has_project_ancestor(rel_path, &self.vbnet_ancestor_cache, &|name| {
            name.ends_with(".vbproj") || name.ends_with(".vbproj.user") || name == ".sln"
        })
    }

    /// `_read_limited` — 128KB đầu, utf-8 → latin-1 fallback, OSError → "".
    fn read_limited(&self, rel_path: &str) -> String {
        let rel = rel_path.replace('\\', "/");
        if let Some(cached) = self
            .content_cache
            .lock()
            .unwrap()
            .get(&rel)
        {
            return cached.clone();
        }
        let abs_path = self.root.join(&rel);
        let mut text = String::new();
        if let Ok(bytes) = std::fs::read(&abs_path) {
            let truncated = &bytes[..bytes.len().min(128 * 1024)];
            text = match String::from_utf8(truncated.to_vec()) {
                Ok(text) => text,
                Err(_) => truncated.iter().map(|&b| b as char).collect(),
            };
        }
        self.content_cache.lock().unwrap().insert(rel, text.clone());
        text
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ext_routing() {
        let dir = std::env::temp_dir().join(format!("vb_cls_test_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let classifier = VBPathClassifier::new(&dir);
        assert_eq!(
            classifier.select_parser_for_path("src/App.vb").as_deref(),
            Some("vbnet")
        );
        assert_eq!(
            classifier.select_parser_for_path("Scripts/boot.vbs").as_deref(),
            Some("vbscript")
        );
        assert_eq!(
            classifier.select_parser_for_path("proj.vbp").as_deref(),
            Some("vb6")
        );
        // .ctl/.pag → None (dead extension trong SOURCE_EXTS vb6)
        assert_eq!(classifier.select_parser_for_path("UserControl1.ctl"), None);
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn overlap_prefers_vba_on_office_patterns() {
        let dir = std::env::temp_dir().join(format!("vb_cls_test2_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join("Macros.bas"),
            "Public Sub Run()\n    Dim ws As Worksheet\n    Set ws = ThisWorkbook.Worksheets(\"Data\")\nEnd Sub\n",
        )
        .unwrap();
        let classifier = VBPathClassifier::new(&dir);
        assert_eq!(
            classifier.select_parser_for_path("Macros.bas").as_deref(),
            Some("vba")
        );
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn overlap_defaults_to_vb6_on_tie() {
        let dir = std::env::temp_dir().join(format!("vb_cls_test3_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join("Module1.bas"),
            "Attribute VB_Name = \"Module1\"\nPublic Sub Main()\nEnd Sub\n",
        )
        .unwrap();
        let classifier = VBPathClassifier::new(&dir);
        assert_eq!(
            classifier.select_parser_for_path("Module1.bas").as_deref(),
            Some("vb6")
        );
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn vb6_project_ancestor_boosts_vb6() {
        let dir = std::env::temp_dir().join(format!("vb_cls_test4_{}", std::process::id()));
        let sub = dir.join("src");
        std::fs::create_dir_all(&sub).unwrap();
        std::fs::write(dir.join("Project1.vbp"), "Type=Exe\n").unwrap();
        // .bas có VBA patterns mạnh nhưng ancestor .vbp +3 vs vba +4? — vba 4>3
        std::fs::write(
            sub.join("Macros.bas"),
            "Sub Run()\n    ThisWorkbook.Worksheets(\"D\").Range(\"A1\") = 1\nEnd Sub\n",
        )
        .unwrap();
        let classifier = VBPathClassifier::new(&dir);
        // vba: ThisWorkbook +2, Worksheets( +2, Range( +2 strong +1 office = 7? Range trong VBA_STRONG(+2) và OFFICE(+1); vb6: ancestor +3
        assert_eq!(
            classifier.select_parser_for_path("src/Macros.bas").as_deref(),
            Some("vba")
        );
        // Case thuần: ancestor .vbp +3, content không pattern → vb6
        std::fs::write(sub.join("Plain.bas"), "Sub Go()\nEnd Sub\n").unwrap();
        assert_eq!(
            classifier.select_parser_for_path("src/Plain.bas").as_deref(),
            Some("vb6")
        );
        std::fs::remove_dir_all(&dir).ok();
    }
}
