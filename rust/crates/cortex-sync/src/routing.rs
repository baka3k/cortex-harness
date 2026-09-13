//! `_select_parser_for_path`, `_AndroidPathClassifier`, `VBPathClassifier`,
//! and `_group_paths_by_parser` ports.

use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

use crate::util;
use crate::walk;

// ── Android path classifier ────────────────────────────────────────────────

const ANDROID_PLUGIN_MARKERS: [&str; 5] = [
    "com.android.application",
    "com.android.library",
    "com.android.dynamic-feature",
    "com.android.test",
    "com.android.instantapp",
];

pub struct AndroidPathClassifier {
    root: std::path::PathBuf,
    manifest_cache: BTreeMap<String, bool>,
    gradle_cache: BTreeMap<String, bool>,
}

impl AndroidPathClassifier {
    pub fn new(root: &Path) -> Self {
        AndroidPathClassifier {
            root: util::realpath(&util::path_to_string(root)),
            manifest_cache: BTreeMap::new(),
            gradle_cache: BTreeMap::new(),
        }
    }

    pub fn is_android_path(&mut self, rel_path: &str) -> bool {
        let rel = rel_path.replace('\\', "/");
        let lower = rel.to_lowercase();
        let name = lower.rsplit('/').next().unwrap_or(&lower).to_string();
        if name == "androidmanifest.xml" {
            return true;
        }
        if name.ends_with(".gradle") || name.ends_with(".gradle.kts") {
            return true;
        }
        if lower.contains("/src/main/res/") {
            return true;
        }
        if lower.ends_with(".xml") && lower.contains("/res/") && lower.contains("/src/") {
            return true;
        }
        let ext = file_ext(&lower);
        if ![".java", ".kt", ".kts", ".xml"].contains(&ext.as_str()) {
            return false;
        }
        let module_dir = self.module_dir_from_path(&rel);
        if let Some(module_dir) = &module_dir
            && self.module_has_android_manifest(module_dir) {
                return true;
            }
        let file_dir = match rel.rfind('/') {
            Some(index) => rel[..index].to_string(),
            None => String::new(),
        };
        self.has_android_gradle_ancestor(&file_dir)
    }

    fn module_dir_from_path(&self, rel_path: &str) -> Option<String> {
        let parts: Vec<&str> = rel_path.split('/').filter(|p| !p.is_empty()).collect();
        for index in 0..parts.len().saturating_sub(1) {
            if parts[index] == "src" {
                return Some(parts[..index].join("/"));
            }
        }
        None
    }

    fn module_has_android_manifest(&mut self, module_dir: &str) -> bool {
        let key = if module_dir.is_empty() { ".".to_string() } else { module_dir.to_string() };
        if let Some(cached) = self.manifest_cache.get(&key) {
            return *cached;
        }
        let candidate = self
            .root
            .join(module_dir)
            .join("src/main/AndroidManifest.xml");
        let found = candidate.is_file();
        self.manifest_cache.insert(key, found);
        found
    }

    fn has_android_gradle_ancestor(&mut self, rel_dir: &str) -> bool {
        let mut probe = rel_dir.replace('\\', "/").trim_matches('/').to_string();
        loop {
            let key = if probe.is_empty() { ".".to_string() } else { probe.clone() };
            let cached = match self.gradle_cache.get(&key) {
                Some(cached) => *cached,
                None => {
                    let detected = self.detect_android_gradle(&probe);
                    self.gradle_cache.insert(key.clone(), detected);
                    detected
                }
            };
            if cached {
                return true;
            }
            if probe.is_empty() {
                return false;
            }
            probe = match probe.rfind('/') {
                Some(index) => probe[..index].to_string(),
                None => String::new(),
            };
        }
    }

    fn detect_android_gradle(&self, rel_dir: &str) -> bool {
        let base = if rel_dir.is_empty() {
            self.root.clone()
        } else {
            self.root.join(rel_dir)
        };
        for gradle_name in ["build.gradle", "build.gradle.kts"] {
            let gradle_path = base.join(gradle_name);
            if !gradle_path.is_file() {
                continue;
            }
            let Ok(bytes) = std::fs::read(&gradle_path) else { continue };
            let content = String::from_utf8_lossy(&bytes[..bytes.len().min(65_536)]).to_lowercase();
            if ANDROID_PLUGIN_MARKERS.iter().any(|marker| content.contains(marker)) {
                return true;
            }
        }
        false
    }
}

// ── VB path classifier ─────────────────────────────────────────────────────

pub struct VbPathClassifier {
    root: std::path::PathBuf,
}

const VB6_PROJECT_FILES: [&str; 2] = [".vbp", ".vbw"];
const VB6_SOURCE_EXTS: [&str; 6] = [".bas", ".cls", ".frm", ".frx", ".ctl", ".pag"];
const VBA_SOURCE_EXTS: [&str; 3] = [".bas", ".cls", ".frm"];
const VBNET_SOURCE_EXTS: [&str; 1] = [".vb"];

fn regex_matches(pattern: &str, text: &str) -> bool {
    regex::Regex::new(pattern).map(|re| re.is_match(text)).unwrap_or(false)
}

impl VbPathClassifier {
    pub fn new(root: &Path) -> Self {
        VbPathClassifier {
            root: util::realpath(&util::path_to_string(root)),
        }
    }

    pub fn select_parser_for_path(&self, rel_path: &str) -> Option<String> {
        let rel = rel_path.replace('\\', "/");
        let rel = rel.trim_start_matches(['.', '/']).trim_end_matches(['.', '/']).to_string();
        if rel.is_empty() {
            return None;
        }
        let lower = rel.to_lowercase();
        let ext = file_ext(&lower);
        if VB6_PROJECT_FILES.contains(&ext.as_str()) || ext == ".frx" {
            return Some("vb6".to_string());
        }
        if [".vbproj", ".vbproj.user", ".sln"].contains(&ext.as_str()) {
            return Some("vbnet".to_string());
        }
        if [".vbs", ".wsf", ".hta"].contains(&ext.as_str()) {
            return Some("vbscript".to_string());
        }
        if ext == ".asp" {
            return if self.looks_vbscript(&rel) { Some("vbscript".to_string()) } else { None };
        }
        if VBNET_SOURCE_EXTS.contains(&ext.as_str()) {
            return Some("vbnet".to_string());
        }
        if VBA_SOURCE_EXTS.contains(&ext.as_str()) {
            return Some(self.classify_vb6_vba_overlap(&rel));
        }
        if VB6_SOURCE_EXTS.contains(&ext.as_str()) {
            // Non-overlap VB6-only extensions never reach this branch in the
            // Python classifier (select_parser_for_path only routes .bas/.cls/.frm).
            return None;
        }
        None
    }

    fn classify_vb6_vba_overlap(&self, rel_path: &str) -> String {
        let mut vb6_score = 0;
        let mut vba_score = 0;
        if self.has_vb6_project_ancestor(rel_path) {
            vb6_score += 3;
        }
        if self.has_vbnet_project_ancestor(rel_path) {
            vb6_score += 1;
        }
        let text = self.read_limited(rel_path);
        if !text.is_empty() {
            for pattern in [
                r"(?im)^\s*VERSION\s+5\.00",
                r"(?i)\bBegin\s+VB\.",
                r"(?i)\bAttribute\s+VB_GlobalNameSpace\b",
            ] {
                if regex_matches(pattern, &text) {
                    vb6_score += 2;
                }
            }
            for pattern in [
                r"(?i)\bThisWorkbook\b",
                r"(?i)\bWorksheets?\s*\(",
                r"(?i)\bWorkbook\b",
                r"(?i)\bApplication\.WorksheetFunction\b",
                r"(?i)\bRange\s*\(",
                r"(?i)\bActiveWorkbook\b",
            ] {
                if regex_matches(pattern, &text) {
                    vba_score += 2;
                }
            }
            for pattern in [
                r"(?i)\b(ActiveSheet|ActiveCell|Selection)\b",
                r"(?i)\bApplication\.(Excel|Word|Access|PowerPoint|Outlook)\b",
                r"(?i)\bCells\s*\(",
                r"(?i)\bRange\s*\(",
            ] {
                if regex_matches(pattern, &text) {
                    vba_score += 1;
                }
            }
            for pattern in [
                r"(?i)\bBegin\s+(VB\.|Form\.)",
                r"(?i)\bAttribute\s+VB_\w+\b",
                r"(?i)\b(Click|DoubleClick|MouseDown|MouseUp|KeyDown|KeyUp|KeyPress)\b",
            ] {
                if regex_matches(pattern, &text) {
                    vb6_score += 1;
                }
            }
        }
        if vba_score > vb6_score {
            "vba".to_string()
        } else {
            "vb6".to_string()
        }
    }

    fn has_vb6_project_ancestor(&self, rel_path: &str) -> bool {
        let rel = rel_path.replace('\\', "/");
        let dir = match rel.rfind('/') {
            Some(index) => rel[..index].to_string(),
            None => String::new(),
        };
        let mut probe = dir.trim_matches('/').to_string();
        loop {
            let base = if probe.is_empty() {
                self.root.clone()
            } else {
                self.root.join(&probe)
            };
            let mut found = false;
            if let Ok(entries) = std::fs::read_dir(&base) {
                for entry in entries.flatten() {
                    let name = entry.file_name().to_string_lossy().to_lowercase();
                    if name.ends_with(".vbp") || name.ends_with(".vbw") {
                        found = true;
                        break;
                    }
                }
            }
            if found {
                return true;
            }
            if probe.is_empty() {
                return false;
            }
            probe = match probe.rfind('/') {
                Some(index) => probe[..index].to_string(),
                None => String::new(),
            };
        }
    }

    fn has_vbnet_project_ancestor(&self, rel_path: &str) -> bool {
        let rel = rel_path.replace('\\', "/");
        let dir = match rel.rfind('/') {
            Some(index) => rel[..index].to_string(),
            None => String::new(),
        };
        let mut probe = dir.trim_matches('/').to_string();
        loop {
            let base = if probe.is_empty() {
                self.root.clone()
            } else {
                self.root.join(&probe)
            };
            let mut found = false;
            if let Ok(entries) = std::fs::read_dir(&base) {
                for entry in entries.flatten() {
                    let name = entry.file_name().to_string_lossy().to_lowercase();
                    if name.ends_with(".vbproj") || name.ends_with(".vbproj.user") || name == ".sln" {
                        found = true;
                        break;
                    }
                }
            }
            if found {
                return true;
            }
            if probe.is_empty() {
                return false;
            }
            probe = match probe.rfind('/') {
                Some(index) => probe[..index].to_string(),
                None => String::new(),
            };
        }
    }

    fn read_limited(&self, rel_path: &str) -> String {
        let absolute = self.root.join(rel_path.replace('\\', "/"));
        match std::fs::read(&absolute) {
            Ok(bytes) => {
                let truncated: &[u8] = &bytes[..bytes.len().min(128 * 1024)];
                match std::str::from_utf8(truncated) {
                    Ok(text) => text.to_string(),
                    // latin-1 accepts every byte value (Python decode("latin-1")).
                    Err(_) => truncated.iter().map(|&b| b as char).collect(),
                }
            }
            Err(_) => String::new(),
        }
    }

    fn looks_vbscript(&self, rel_path: &str) -> bool {
        let text = self.read_limited(rel_path);
        if text.is_empty() {
            return true;
        }
        // Non-VBScript ASP patterns first.
        if [
            r"(?i)<%@",
            r#"(?i)<\s*%@\s*Page\s+Language="?C#""#,
            r#"(?i)<\s*%@\s*Language="?JScript""#,
            r"(?i)<\?php",
            r"(?i)<jsp:",
        ]
        .iter()
        .any(|pattern| regex_matches(pattern, &text))
        {
            return false;
        }
        if [
            r#"(?i)\bLanguage\s*=\s*"?VBScript"?""#,
            r"(?i)<%",
        ]
        .iter()
        .any(|pattern| regex_matches(pattern, &text))
        {
            return true;
        }
        if [
            r"(?i)\bResponse\.(Write|End|Redirect)\b",
            r"(?i)\bRequest\.(QueryString|Form|ServerVariables)\b",
            r"(?i)\bServer\.(CreateObject|MapPath)\b",
            r"(?i)\bSession\s*\(",
            r"(?i)\bApplication\s*\(",
        ]
        .iter()
        .any(|pattern| regex_matches(pattern, &text))
        {
            return true;
        }
        // Default to True for .asp files with no conflicting patterns.
        true
    }
}

fn file_ext(path: &str) -> String {
    match path.rfind('.') {
        Some(index) if !path[index..].contains('/') => path[index..].to_string(),
        _ => String::new(),
    }
}

// ── Path → parser selection ────────────────────────────────────────────────

pub fn select_parser_for_path(
    path: &str,
    android: &mut AndroidPathClassifier,
    vb: &VbPathClassifier,
) -> Option<String> {
    let rel = path.replace('\\', "/");
    let lower = rel.to_lowercase();
    let name = lower.rsplit('/').next().unwrap_or(&lower).to_string();
    let ext = file_ext(&lower);

    let in_set = |set: &[&str]| set.contains(&ext.as_str());
    if in_set(&[".cbl", ".cob", ".cpy", ".copy"]) {
        return Some("cobol".to_string());
    }
    if in_set(&[
        ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".pc", ".pcc", ".rc", ".rc2",
    ]) {
        return Some("cplus".to_string());
    }
    if in_set(&[".pas", ".dpr", ".inc"]) {
        return Some("delphi".to_string());
    }
    if ext == ".py" {
        return Some("python".to_string());
    }
    if ext == ".go" {
        return Some("go".to_string());
    }
    if in_set(&[".pl", ".pm", ".t"]) {
        return Some("perl".to_string());
    }
    if ext == ".sh" {
        return Some("shell".to_string());
    }
    if ext == ".txt" {
        let jp1_root = android.root.clone();
        if walk::is_jp1_file(&jp1_root.join(&rel)) {
            return Some("jp1".to_string());
        }
    }
    if ext == ".rs" {
        return Some("rust".to_string());
    }
    if ext == ".swift" {
        return Some("swift".to_string());
    }
    if ext == ".dart" {
        return Some("dart".to_string());
    }
    if in_set(&[".js", ".jsx"]) {
        return Some("js".to_string());
    }
    if in_set(&[".ts", ".tsx"]) {
        return Some("ts".to_string());
    }
    if ext == ".php" {
        return Some("php".to_string());
    }
    if ext == ".cs" {
        return Some("csharp".to_string());
    }
    if ext == ".sql" {
        return Some("sql".to_string());
    }
    if in_set(&[
        ".pls", ".plsql", ".pks", ".pkb", ".pkg", ".pck", ".spc", ".spb", ".trg", ".fnc",
    ]) {
        return Some("plsql".to_string());
    }
    if in_set(&[".vb", ".vbproj", ".vbp", ".vbw", ".frx", ".bas", ".cls", ".frm", ".vbs", ".wsf", ".asp"]) {
        return vb.select_parser_for_path(&rel);
    }
    if name.ends_with(".gradle") || name.ends_with(".gradle.kts") {
        return Some("android".to_string());
    }
    if ext == ".xml" {
        return if android.is_android_path(&rel) { Some("android".to_string()) } else { None };
    }
    if ext == ".java" {
        return if android.is_android_path(&rel) {
            Some("android".to_string())
        } else {
            Some("java".to_string())
        };
    }
    if ext == ".kt" || ext == ".kts" {
        return if android.is_android_path(&rel) {
            Some("android".to_string())
        } else {
            Some("kotlin".to_string())
        };
    }
    None
}

/// `_group_paths_by_parser`.
pub fn group_paths_by_parser(paths: &BTreeSet<String>, root: &Path) -> BTreeMap<String, BTreeSet<String>> {
    let mut grouped: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    let mut android = AndroidPathClassifier::new(root);
    let vb = VbPathClassifier::new(root);
    for path in paths {
        let parser = select_parser_for_path(path, &mut android, &vb);
        if let Some(parser) = parser {
            grouped.entry(parser).or_default().insert(path.clone());
        }
    }
    grouped
}
