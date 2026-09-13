//! Port `tools/common/aspnet/project_metadata.py` — module discovery/detect
//! cho 2 overlay ASP.NET.

use std::path::{Path, PathBuf};

use fancy_regex::Regex;

use crate::aspnet::builders::module_id as make_module_id;
use crate::aspnet::safe_formats::{parse_xml_file, read_bounded_text};
use crate::pyutil::normalize_relative_path;

pub const IGNORED_DIRS: [&str; 19] = [
    ".git", ".hg", ".svn", ".idea", ".vscode", ".settings", ".cache", ".venv",
    "venv", "__pycache__", "bin", "obj", "build", "dist", "out", "target",
    "node_modules", "packages", "roslyn_worker",
];

#[derive(Debug, Clone)]
pub struct ModuleDetection {
    pub framework: String,
    pub module_path: String,
    pub module_id: String,
    pub detected: bool,
    pub evidence: Vec<String>,
    pub supporting_evidence: Vec<String>,
    pub confidence: f64,
    pub artifacts: Vec<String>,
}

fn is_ignored(name: &str) -> bool {
    IGNORED_DIRS.contains(&name) || name.starts_with('.')
}

fn dir_contains_project_file(path: &Path) -> bool {
    let entries = match std::fs::read_dir(path) {
        Ok(entries) => entries,
        Err(_) => return false,
    };
    for entry in entries.flatten() {
        let is_file = entry.file_type().map(|t| t.is_file()).unwrap_or(false);
        if is_file {
            let name = entry.file_name().to_string_lossy().to_lowercase();
            if name.ends_with(".csproj") || name.ends_with(".vbproj") {
                return true;
            }
        }
    }
    false
}

/// `discover_project_roots` — walk với prune như os.walk topdown.
pub fn discover_project_roots(root: &Path) -> Vec<String> {
    let mut modules: Vec<String> = Vec::new();
    discover_walk(root, root, &mut modules);
    if modules.is_empty() {
        modules.push(".".to_string());
    }
    modules.sort();
    modules
}

fn discover_walk(root: &Path, current: &Path, modules: &mut Vec<String>) {
    let entries = match std::fs::read_dir(current) {
        Ok(entries) => entries,
        Err(_) => return,
    };
    let mut dirs: Vec<PathBuf> = Vec::new();
    let mut has_project = false;
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        let file_type = match entry.file_type() {
            Ok(file_type) => file_type,
            Err(_) => continue,
        };
        if file_type.is_dir() {
            if !is_ignored(&name) {
                dirs.push(entry.path());
            }
        } else if file_type.is_file() {
            let lower = name.to_lowercase();
            if lower.ends_with(".csproj") || lower.ends_with(".vbproj") {
                has_project = true;
            }
        }
    }
    if has_project {
        let rel = current
            .strip_prefix(root)
            .map(crate::pyutil::to_posix)
            .unwrap_or_default();
        modules.push(if rel.is_empty() { ".".to_string() } else { rel });
    }
    dirs.sort();
    for dir in dirs {
        discover_walk(root, &dir, modules);
    }
}

/// `iter_module_files` — walk module với prune (không descend vào dir chứa
/// project file, không lấy file trong dir bị ignore).
pub fn iter_module_files(root: &Path, module_path: &str) -> Vec<String> {
    let root_abs = crate::pyutil::realpath(root);
    let base = if module_path.is_empty() || module_path == "." {
        root_abs.clone()
    } else {
        root_abs.join(module_path)
    };
    if !base.is_dir() {
        return Vec::new();
    }
    let mut values: Vec<String> = Vec::new();
    module_walk(&root_abs, &base, &mut values);
    values
}

fn module_walk(root: &Path, current: &Path, values: &mut Vec<String>) {
    let entries = match std::fs::read_dir(current) {
        Ok(entries) => entries,
        Err(_) => return,
    };
    let mut dirs: Vec<PathBuf> = Vec::new();
    let mut files: Vec<PathBuf> = Vec::new();
    for entry in entries.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        let file_type = match entry.file_type() {
            Ok(file_type) => file_type,
            Err(_) => continue,
        };
        if file_type.is_dir() {
            if !is_ignored(&name) && !dir_contains_project_file(&entry.path()) {
                dirs.push(entry.path());
            }
        } else if file_type.is_file() {
            files.push(entry.path());
        }
    }
    dirs.sort();
    files.sort();
    for file in files {
        if let Ok(rel) = file.strip_prefix(root) {
            values.push(normalize_relative_path(&crate::pyutil::to_posix(rel)));
        }
    }
    for dir in dirs {
        module_walk(root, &dir, values);
    }
}

/// `infer_deleted_module_path`.
pub fn infer_deleted_module_path(framework: &str, path: &str) -> String {
    let normalized = normalize_relative_path(path);
    let name = crate::pyutil::basename(&normalized).to_lowercase();
    let mut marker = name.ends_with(".csproj") || name.ends_with(".vbproj");
    if framework == "aspnet_core" {
        marker = marker
            || name == "program.cs"
            || name == "startup.cs"
            || (name.starts_with("appsettings") && name.ends_with(".json"));
    } else if framework == "aspnet_framework" {
        marker = marker || name == "web.config" || name == "global.asax" || name == "packages.config";
    }
    if !marker {
        return String::new();
    }
    let directory = crate::pyutil::dirname(&normalized);
    let directory = normalize_relative_path(&directory);
    if directory.is_empty() {
        ".".to_string()
    } else {
        directory
    }
}

fn target_framework_version_regex() -> &'static Regex {
    use std::sync::OnceLock;
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| {
        Regex::new(r"(?i)<TargetFrameworkVersion>v(?:2|3|4)").expect("static regex")
    })
}

fn system_web_config_regex() -> &'static Regex {
    use std::sync::OnceLock;
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| Regex::new(r"(?i)<\s*system\.web(?:Server)?\b").expect("static regex"))
}

/// `detect_modules` — đúng thứ tự + evidence của Python.
pub fn detect_modules(root: &Path, framework: &str) -> Vec<ModuleDetection> {
    let mut detections: Vec<ModuleDetection> = Vec::new();
    for module_path in discover_project_roots(root) {
        let files = iter_module_files(root, &module_path);
        let mut strong: Vec<String> = Vec::new();
        let mut supporting: Vec<String> = Vec::new();
        for path in &files {
            let lower = path.to_lowercase();
            let name = crate::pyutil::basename(&lower);
            if lower.ends_with(".csproj") {
                let (sdk, values) = match parse_xml_file(root, path, 1024 * 1024) {
                    Ok((tree, _, _)) => {
                        let sdk = tree.attrib("Sdk").unwrap_or("").to_lowercase();
                        let values = csproj_values(&tree).to_lowercase();
                        (sdk, values)
                    }
                    Err(_) => (String::new(), String::new()),
                };
                if framework == "aspnet_core" {
                    if sdk.contains("microsoft.net.sdk.web") {
                        strong.push(format!("{path}:web-sdk"));
                    }
                    if values.contains("microsoft.aspnetcore.app") || values.contains("microsoft.aspnetcore") {
                        strong.push(format!("{path}:aspnet-core-reference"));
                    }
                } else {
                    if values.contains("system.web") || values.contains("349c5851-65df-11da-9384-00065b846f21") {
                        strong.push(format!("{path}:legacy-web-project"));
                    }
                    if target_framework_version_regex().is_match(&values).unwrap_or(false) {
                        supporting.push(format!("{path}:legacy-target"));
                    }
                }
            } else if framework == "aspnet_core" {
                if name.starts_with("appsettings") && name.ends_with(".json") {
                    supporting.push(format!("{path}:appsettings"));
                } else if lower.ends_with(".cshtml") || lower.ends_with(".razor") {
                    supporting.push(format!("{path}:razor"));
                } else if name == "program.cs" || name == "startup.cs" {
                    let text = match read_bounded_text(root, path, 256 * 1024) {
                        Ok((text, _, _)) => text,
                        Err(_) => String::new(),
                    };
                    if text.contains("Microsoft.AspNetCore")
                        || web_application_regex().is_match(&text).unwrap_or(false)
                    {
                        supporting.push(format!("{path}:host-bootstrap"));
                    }
                }
            } else {
                if name == "web.config" {
                    let text = match read_bounded_text(root, path, 512 * 1024) {
                        Ok((text, _, _)) => text,
                        Err(_) => String::new(),
                    };
                    if system_web_config_regex().is_match(&text).unwrap_or(false) {
                        strong.push(format!("{path}:system-web-config"));
                    }
                } else if name == "global.asax"
                    || lower.ends_with(".aspx")
                    || lower.ends_with(".ascx")
                    || lower.ends_with(".master")
                    || lower.ends_with(".asmx")
                    || lower.ends_with(".ashx")
                {
                    strong.push(format!("{path}:legacy-web-artifact"));
                } else if format!("/{lower}").contains("/app_start/") {
                    supporting.push(format!("{path}:app-start"));
                } else if name == "packages.config" {
                    supporting.push(format!("{path}:packages-config"));
                }
            }
        }
        strong.sort();
        strong.dedup();
        supporting.sort();
        supporting.dedup();
        let detected = !strong.is_empty() || supporting.len() >= 2;
        let confidence = if detected {
            ((if strong.is_empty() { 0.0 } else { 0.75 }) + 0.12 * supporting.len() as f64).min(1.0)
        } else {
            0.0
        };
        detections.push(ModuleDetection {
            framework: framework.to_string(),
            module_path: module_path.clone(),
            module_id: make_module_id(framework, &module_path),
            detected,
            evidence: strong,
            supporting_evidence: supporting,
            confidence,
            artifacts: files,
        });
    }
    detections
}

fn web_application_regex() -> &'static Regex {
    use std::sync::OnceLock;
    static REGEX: OnceLock<Regex> = OnceLock::new();
    REGEX.get_or_init(|| Regex::new(r"\bWebApplication\s*\.\s*CreateBuilder\b").expect("static regex"))
}

/// `" ".join(str(element.text or "") + " " + " ".join(element.attrib.values())
/// for element in tree.iter())`.
fn csproj_values(tree: &crate::xmlmini::Element) -> String {
    let mut parts = Vec::new();
    for element in crate::xmlmini::iter_elements(tree) {
        let text = if element.text.is_empty() {
            String::new()
        } else {
            element.text.clone()
        };
        parts.push(format!("{} {}", text, element.attrib_values_joined()));
    }
    parts.join(" ")
}

/// `_in_module` (pipeline.py) — path nằm trong module (prefix theo "/").
pub fn in_module(path: &str, module_path: &str) -> bool {
    let normalized = path.replace('\\', "/");
    let normalized = normalized.trim_matches('/').to_string();
    let module = module_path.replace('\\', "/");
    let module = module.trim_matches('/').to_string();
    module.is_empty()
        || module == "." 
        || normalized == module
        || normalized.starts_with(&format!("{module}/"))
}

/// `_module_project_path` — csproj đầu tiên trong evidence, fallback artifacts.
pub fn module_project_path(detection: &ModuleDetection) -> String {
    let mut evidence_projects: Vec<String> = Vec::new();
    for item in detection.evidence.iter().chain(detection.supporting_evidence.iter()) {
        let head = match item.rsplit_once(':') {
            Some((head, _)) => head.to_string(),
            None => item.clone(),
        };
        if head.to_lowercase().ends_with(".csproj") {
            evidence_projects.push(head);
        }
    }
    evidence_projects.sort();
    evidence_projects.dedup();
    if !evidence_projects.is_empty() {
        return evidence_projects[0].clone();
    }
    let mut projects: Vec<String> = detection
        .artifacts
        .iter()
        .filter(|path| path.to_lowercase().ends_with(".csproj"))
        .cloned()
        .collect();
    projects.sort();
    projects.first().cloned().unwrap_or_default()
}

/// Detector class (`AspNetCoreDetector` / `AspNetFrameworkDetector`).
pub struct AspNetDetector {
    modules: Vec<ModuleDetection>,
}

impl AspNetDetector {
    pub fn new(root: &Path, framework: &str) -> Self {
        Self {
            modules: detect_modules(root, framework),
        }
    }

    pub fn discover_modules(&self) -> Vec<ModuleDetection> {
        self.modules.iter().filter(|item| item.detected).cloned().collect()
    }

    /// `detect_path(path, include_undetected)` — max theo len(module_path),
    /// trả None khi không có candidate.
    pub fn detect_path(&self, path: &str, include_undetected: bool) -> Option<ModuleDetection> {
        let normalized = path.replace('\\', "/");
        let normalized = normalized.trim_matches('/').to_string();
        let mut best: Option<&ModuleDetection> = None;
        for item in &self.modules {
            if !(item.detected || include_undetected) {
                continue;
            }
            let matches_scope = item.module_path.is_empty() || item.module_path == "."
                || normalized == item.module_path
                || normalized.starts_with(&format!("{}/", item.module_path.trim_end_matches('/')));
            if matches_scope {
                let better = match best {
                    None => true,
                    Some(current) => item.module_path.len() > current.module_path.len(),
                };
                if better {
                    best = Some(item);
                }
            }
        }
        best.cloned()
    }
}
