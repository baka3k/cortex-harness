//! Port `tools/servlet_jsp/detector.py`.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::pyutil::{basename, realpath};
use crate::servlet_jsp::models::stable_digest;
use crate::servlet_jsp::path_resolver::{normalize_relative_path, read_bounded_file};

pub const SERVLET_JSP_BUILD_FILES: [&str; 6] = [
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
    "build.xml",
];
pub const SERVLET_JSP_VIEW_EXTS: [&str; 3] = [".jsp", ".jspx", ".jspf"];
pub const SERVLET_JSP_SOURCE_EXTS: [&str; 1] = [".java"];
pub const SERVLET_JSP_PROPERTIES_EXTS: [&str; 1] = [".properties"];
pub const SERVLET_JSP_STATIC_EXTS: [&str; 4] = [".html", ".htm", ".css", ".js"];

const EXCLUDED_DIRS: [&str; 24] = [
    ".git",
    ".hg",
    ".svn",
    ".gradle",
    ".idea",
    ".mvn",
    ".settings",
    ".cache",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "bin",
    "build",
    "coverage",
    "dist",
    "gen",
    "generated",
    "node_modules",
    "out",
    "target",
    "vendor",
];

const ANDROID_MARKERS: [&str; 4] = [
    "com.android.application",
    "com.android.library",
    "com.android.dynamic-feature",
    "com.android.test",
];

const JAVA_STRONG: &str = r"(?:javax|jakarta)\.servlet\.|@(?:WebServlet|WebFilter|WebListener)\b|\bextends\s+HttpServlet\b|\bimplements\s+(?:Filter|ServletContextListener|HttpSessionListener|ServletRequestListener)\b";
const DESCRIPTOR_ROOT: &str = r"<\s*(?:[A-Za-z_][\w.-]*:)?web-app\b";
const DESCRIPTOR_CONTENT: &str = r"<\s*(?:[A-Za-z_][\w.-]*:)?(?:servlet|servlet-mapping|filter|filter-mapping|listener|jsp-config)\b";
const JSP_MARKERS: &str = r"<%@|<jsp:|\$\{|#\{|<\w+:\w+";

const BUILD_MARKERS: [&str; 7] = [
    "jakarta.servlet",
    "javax.servlet",
    "servlet-api",
    "jakarta.servlet-api",
    "jsp-api",
    "jakarta.servlet.jsp",
    "tomcat-embed-jasper",
];

fn java_strong_re() -> &'static regex::Regex {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(JAVA_STRONG).unwrap())
}
fn descriptor_root_re() -> &'static regex::Regex {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(DESCRIPTOR_ROOT).unwrap())
}
fn descriptor_content_re() -> &'static regex::Regex {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(DESCRIPTOR_CONTENT).unwrap())
}
fn jsp_re() -> &'static regex::Regex {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(JSP_MARKERS).unwrap())
}

#[derive(Debug, Clone)]
pub struct ServletJspDetectionResult {
    pub is_servlet_jsp: bool,
    pub module_root: String,
    pub module_id: String,
    pub artifact_kind: String,
    pub evidence: Vec<String>,
    pub supporting_evidence: Vec<String>,
    pub confidence: f64,
}

/// `infer_module_dir`.
pub fn infer_module_dir(rel_path: &str) -> String {
    let rel = normalize_relative_path(rel_path);
    for prefix in ["src/main/", "src/test/", "src/integrationTest/"] {
        if rel.starts_with(prefix) {
            return String::new();
        }
    }
    for token in ["/src/main/", "/src/test/", "/src/integrationTest/"] {
        if let Some(index) = rel.find(token) {
            return rel[..index].to_string();
        }
    }
    if let Some(index) = rel.find("/WEB-INF/") {
        return rel[..index].to_string();
    }
    crate::pyutil::dirname(&rel)
}

/// `module_id_for_path`.
pub fn module_id_for_path(module_path: &str) -> String {
    let normalized = {
        let value = normalize_relative_path(module_path);
        if value.is_empty() {
            ".".to_string()
        } else {
            value
        }
    };
    format!("servlet_jsp_module::{}", stable_digest(&[normalized], 20))
}

#[derive(Debug, Clone, Default)]
pub struct DiscoveredServletModule {
    pub module_id: String,
    pub rel_path: String,
    pub java_files: Vec<String>,
    pub descriptor_files: Vec<String>,
    pub jsp_files: Vec<String>,
    pub properties_files: Vec<String>,
    pub build_files: Vec<String>,
    pub static_files: Vec<String>,
    pub evidence: Vec<String>,
    pub confidence: f64,
}

pub struct ServletJspProjectDetector {
    pub root: PathBuf,
    read_limit: usize,
    module_cache: HashMap<String, (Vec<String>, Vec<String>, f64, bool)>,
}

impl ServletJspProjectDetector {
    pub fn new(root: &Path) -> Self {
        Self {
            root: realpath(root),
            read_limit: 256 * 1024,
            module_cache: HashMap::new(),
        }
    }

    pub fn detect_path(&mut self, rel_path: &str) -> ServletJspDetectionResult {
        let rel = normalize_relative_path(rel_path);
        let module_root = infer_module_dir(&rel);
        let module_id = module_id_for_path(&module_root);
        if self.module_is_android(&module_root) {
            return ServletJspDetectionResult {
                is_servlet_jsp: false,
                module_root,
                module_id,
                artifact_kind: String::new(),
                evidence: vec!["android-module".to_string()],
                supporting_evidence: Vec::new(),
                confidence: 0.0,
            };
        }
        let (kind, direct, supporting) = self.classify(&rel);
        let (module_evidence, module_supporting, module_confidence, module_is_web) = self.module_evidence(&module_root);
        if direct.is_empty() && !module_is_web {
            return ServletJspDetectionResult {
                is_servlet_jsp: false,
                module_root,
                module_id,
                artifact_kind: kind,
                evidence: Vec::new(),
                supporting_evidence: supporting,
                confidence: 0.0,
            };
        }
        let mut evidence: Vec<String> = Vec::new();
        for item in direct.iter().chain(module_evidence.iter()) {
            if !evidence.contains(item) {
                evidence.push(item.clone());
            }
        }
        let mut support: Vec<String> = Vec::new();
        for item in supporting.iter().chain(module_supporting.iter()) {
            if !support.contains(item) {
                support.push(item.clone());
            }
        }
        let direct_confidence = confidence_of(&evidence, &support);
        let confidence = module_confidence.max(direct_confidence);
        ServletJspDetectionResult {
            is_servlet_jsp: !evidence.is_empty(),
            module_root,
            module_id,
            artifact_kind: kind,
            evidence,
            supporting_evidence: support,
            confidence,
        }
    }

    pub fn discover_modules(&mut self) -> Vec<DiscoveredServletModule> {
        let mut modules: HashMap<String, DiscoveredServletModule> = HashMap::new();
        for (_, rel) in self.iter_files() {
            let (kind, direct, supporting) = self.classify(&rel);
            if kind.is_empty() {
                continue;
            }
            let result = self.detect_path(&rel);
            if !result.is_servlet_jsp {
                continue;
            }
            let key = if result.module_root.is_empty() {
                ".".to_string()
            } else {
                result.module_root.clone()
            };
            let bucket = modules
                .entry(key.clone())
                .or_insert_with(|| DiscoveredServletModule {
                    module_id: result.module_id.clone(),
                    rel_path: key.clone(),
                    ..Default::default()
                });
            match kind.as_str() {
                "java" => bucket.java_files.push(rel.clone()),
                "web_xml" => bucket.descriptor_files.push(rel.clone()),
                "jsp" | "jspx" | "jsp_fragment" => bucket.jsp_files.push(rel.clone()),
                "properties" => bucket.properties_files.push(rel.clone()),
                "build" => bucket.build_files.push(rel.clone()),
                "static" => bucket.static_files.push(rel.clone()),
                _ => {}
            }
            for item in result.evidence.iter().chain(direct.iter()).chain(supporting.iter()) {
                if !bucket.evidence.contains(item) {
                    bucket.evidence.push(item.clone());
                }
            }
            bucket.confidence = bucket.confidence.max(result.confidence);
        }
        let mut normalized: Vec<DiscoveredServletModule> = modules.into_values().collect();
        for bucket in &mut normalized {
            bucket.java_files.sort();
            bucket.descriptor_files.sort();
            bucket.jsp_files.sort();
            bucket.properties_files.sort();
            bucket.build_files.sort();
            bucket.static_files.sort();
            bucket.evidence.sort();
        }
        normalized.sort_by(|a, b| a.rel_path.cmp(&b.rel_path));
        normalized
    }

    /// `_classify` — trả (kind, direct, supporting).
    fn classify(&self, rel_path: &str) -> (String, Vec<String>, Vec<String>) {
        let lower = rel_path.to_lowercase();
        let name = basename(&lower);
        let text = self.read_text(rel_path);
        let mut direct: Vec<String> = Vec::new();
        let mut supporting: Vec<String> = Vec::new();
        let mut kind = String::new();
        if lower.ends_with(".java") {
            kind = "java".to_string();
            if java_strong_re().is_match(&text) {
                direct.push(format!("{rel_path}:servlet-api-java"));
            }
        } else if name == "web.xml" {
            kind = "web_xml".to_string();
            if descriptor_root_re().is_match(&text) && descriptor_content_re().is_match(&text) {
                direct.push(format!("{rel_path}:web-app-descriptor"));
            } else {
                supporting.push(format!("{rel_path}:web-xml-name"));
            }
        } else if SERVLET_JSP_VIEW_EXTS.iter().any(|ext| lower.ends_with(ext)) {
            kind = if lower.ends_with(".jspx") {
                "jspx".to_string()
            } else if lower.ends_with(".jspf") {
                "jsp_fragment".to_string()
            } else {
                "jsp".to_string()
            };
            if jsp_re().is_match(&text) || lower.ends_with(".jsp") || lower.ends_with(".jspx") || lower.ends_with(".jspf") {
                direct.push(format!("{rel_path}:jsp-view"));
            }
        } else if SERVLET_JSP_PROPERTIES_EXTS.iter().any(|ext| lower.ends_with(ext)) {
            kind = "properties".to_string();
            supporting.push(format!("{rel_path}:properties"));
        } else if SERVLET_JSP_BUILD_FILES.contains(&name.as_str()) {
            kind = "build".to_string();
            let normalized = text.to_lowercase();
            let hits: Vec<&str> = BUILD_MARKERS
                .iter()
                .filter(|marker| normalized.contains(*marker))
                .copied()
                .collect();
            for marker in hits {
                supporting.push(format!("{rel_path}:build:{marker}"));
            }
        } else if SERVLET_JSP_STATIC_EXTS.iter().any(|ext| lower.ends_with(ext)) {
            kind = "static".to_string();
            supporting.push(format!("{rel_path}:static-target"));
        }
        (kind, direct, supporting)
    }

    fn module_evidence(&mut self, module_root: &str) -> (Vec<String>, Vec<String>, f64, bool) {
        let key = normalize_relative_path(module_root);
        if let Some(cached) = self.module_cache.get(&key) {
            return cached.clone();
        }
        let mut evidence: Vec<String> = Vec::new();
        let mut supporting: Vec<String> = Vec::new();
        let prefix = if key.is_empty() { String::new() } else { format!("{key}/") };
        for (_, rel) in self.iter_files() {
            if !key.is_empty() && !rel.starts_with(&prefix) {
                continue;
            }
            if key.is_empty() && rel.contains("/src/main/") {
                continue;
            }
            let (kind, direct, support) = self.classify(&rel);
            if !kind.is_empty() {
                evidence.extend(direct);
                supporting.extend(support);
            }
        }
        let unique_evidence = dedupe(&evidence);
        let unique_support = dedupe(&supporting);
        let confidence = confidence_of(&unique_evidence, &unique_support);
        let is_web = !unique_evidence.is_empty();
        let result = (unique_evidence, unique_support, confidence, is_web);
        self.module_cache.insert(key, result.clone());
        result
    }

    fn module_is_android(&self, module_root: &str) -> bool {
        let base = if module_root.is_empty() {
            self.root.clone()
        } else {
            self.root.join(module_root)
        };
        if base.join("src/main/AndroidManifest.xml").is_file() {
            return true;
        }
        for name in ["build.gradle", "build.gradle.kts"] {
            let path = base.join(name);
            if !path.is_file() {
                continue;
            }
            let Ok((data, _)) = read_bounded_file(&path, 64 * 1024) else {
                continue;
            };
            let text = String::from_utf8_lossy(&data).to_string();
            if ANDROID_MARKERS.iter().any(|marker| text.contains(marker)) {
                return true;
            }
        }
        false
    }

    fn read_text(&self, rel_path: &str) -> String {
        let path = self.root.join(normalize_relative_path(rel_path));
        let Ok((data, _)) = read_bounded_file(&path, self.read_limit) else {
            return String::new();
        };
        // decode utf-8 errors=ignore.
        crate::pyutil::decode_utf8_ignore(&data)
    }

    fn iter_files(&self) -> Vec<(PathBuf, String)> {
        let mut out = Vec::new();
        walk_inner(&self.root, &self.root, &mut out);
        out
    }
}

fn walk_inner(root: &Path, current: &Path, out: &mut Vec<(PathBuf, String)>) {
    let entries = match std::fs::read_dir(current) {
        Ok(entries) => entries,
        Err(_) => return,
    };
    let mut dirs: Vec<PathBuf> = Vec::new();
    let mut files: Vec<PathBuf> = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            dirs.push(path);
        } else if path.is_file() {
            files.push(path);
        }
    }
    dirs.sort();
    files.sort();
    dirs.retain(|dir| {
        let name = dir.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
        !EXCLUDED_DIRS.contains(&name.as_str())
    });
    for file in files {
        if let Ok(rel) = file.strip_prefix(root) {
            out.push((file.clone(), normalize_relative_path(&crate::pyutil::to_posix(rel))));
        }
    }
    for dir in dirs {
        walk_inner(root, &dir, out);
    }
}

fn dedupe(items: &[String]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for item in items {
        if !out.contains(item) {
            out.push(item.clone());
        }
    }
    out
}

fn confidence_of(evidence: &[String], supporting: &[String]) -> f64 {
    if evidence.is_empty() {
        return 0.0;
    }
    (0.72 + 0.08 * (evidence.len() - 1).min(2) as f64 + 0.03 * supporting.len().min(3) as f64).min(1.0)
}

/// JSON value helper (không dùng — giữ import cân bằng).
#[allow(dead_code)]
fn unused(_: Value) {}
