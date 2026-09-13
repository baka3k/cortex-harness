//! Port `tools/spring/detector.py` — SpringProjectDetector: module discovery,
//! evidence scan, android gate.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use crate::pyutil::{basename, read_limited, realpath, strip_dot_slash, walk_sorted};

pub const SPRING_BUILD_FILES_SET: [&str; 5] = [
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
];
pub const SPRING_CONFIG_EXTS: [&str; 5] = [".properties", ".yml", ".yaml", ".json", ".xml"];

const SPRING_TEXT_MARKERS: [&str; 5] = [
    "org.springframework.boot",
    "spring-boot-starter",
    "org.springframework",
    "springframework",
    "io.spring.dependency-management",
];
const SPRING_SOURCE_MARKERS: [&str; 9] = [
    "@SpringBootApplication",
    "@EnableAutoConfiguration",
    "@RestController",
    "@Controller",
    "@Service",
    "@Repository",
    "@Component",
    "SpringApplication.run",
    "org.springframework.",
];
const ANDROID_MARKERS: [&str; 5] = [
    "com.android.application",
    "com.android.library",
    "com.android.dynamic-feature",
    "com.android.test",
    "com.android.instantapp",
];
pub const EXCLUDED_DIRS: [&str; 24] = [
    ".git",
    ".hg",
    ".svn",
    ".gradle",
    ".idea",
    ".mvn",
    ".settings",
    ".cache",
    ".parcel-cache",
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
];

/// `is_application_config_name` — `^application(?:-([A-Za-z0-9_.-]+))?\.(properties|ya?ml|json)$` case-insensitive.
pub fn is_application_config_name(name: &str) -> bool {
    let lower = name.to_lowercase();
    let Some(rest) = lower.strip_prefix("application") else {
        return false;
    };
    // Profile part: optional `-<profile>` rồi extension.
    let (profile, ext) = match rest.strip_prefix('-') {
        Some(value) => match value.find('.') {
            Some(index) => (&value[..index], &value[index..]),
            None => return false,
        },
        None => match rest.find('.') {
            Some(index) => ("", &rest[index..]),
            None => return false,
        },
    };
    if !profile.chars().all(|c| c.is_ascii_alphanumeric() || "-_.".contains(c)) {
        return false;
    }
    matches!(ext, ".properties" | ".yml" | ".yaml" | ".json")
}

/// `profile_from_config_name` — nhóm profile (pattern IGNORECASE nhưng group
/// trả nguyên văn như Python).
pub fn profile_from_config_name(path: &str) -> String {
    let name = basename(path);
    let lower = name.to_lowercase();
    let Some(rest) = lower.strip_prefix("application") else {
        return String::new();
    };
    let offset = name.len() - rest.len();
    let (profile_len, ext) = match rest.strip_prefix('-') {
        Some(value) => match value.find('.') {
            Some(index) => (index, &value[index..]),
            None => return String::new(),
        },
        None => match rest.find('.') {
            Some(index) => (0, &rest[index..]),
            None => return String::new(),
        },
    };
    if !matches!(ext, ".properties" | ".yml" | ".yaml" | ".json") {
        return String::new();
    }
    if profile_len == 0 {
        return String::new();
    }
    name[offset + 1..offset + 1 + profile_len].to_string()
}

#[derive(Debug, Clone)]
pub struct SpringDetectionResult {
    pub is_spring: bool,
    pub module_root: String,
    pub evidence: Vec<String>,
    pub confidence: f64,
}

pub fn infer_module_dir(rel_path: &str) -> String {
    let rel = strip_dot_slash(rel_path);
    for prefix in ["src/main/", "src/test/", "src/integrationTest/", "src/androidTest/"] {
        if rel.starts_with(prefix) {
            return String::new();
        }
    }
    for token in ["/src/main/", "/src/test/", "/src/integrationTest/", "/src/androidTest/"] {
        if let Some(index) = rel.find(token) {
            return rel[..index].to_string();
        }
    }
    crate::pyutil::dirname(&rel)
}

pub struct SpringProjectDetector {
    pub root: PathBuf,
    module_cache: HashMap<String, SpringDetectionResult>,
    android_cache: HashMap<String, bool>,
}

impl SpringProjectDetector {
    pub fn new(root: &Path) -> Self {
        Self {
            root: realpath(root),
            module_cache: HashMap::new(),
            android_cache: HashMap::new(),
        }
    }

    pub fn detect_path(&mut self, rel_path: &str) -> SpringDetectionResult {
        let rel = strip_dot_slash(rel_path);
        let module_dir = infer_module_dir(&rel);
        let key = if module_dir.is_empty() { ".".to_string() } else { module_dir.clone() };
        if let Some(cached) = self.module_cache.get(&key) {
            return cached.clone();
        }

        if self.module_has_android_evidence(&module_dir) {
            let result = SpringDetectionResult {
                is_spring: false,
                module_root: module_dir,
                evidence: vec!["android-module".to_string()],
                confidence: 0.0,
            };
            self.module_cache.insert(key, result.clone());
            return result;
        }

        let mut evidence: Vec<String> = Vec::new();
        for build_name in ["pom.xml", "build.gradle", "build.gradle.kts"] {
            let build_rel = if module_dir.is_empty() {
                build_name.to_string()
            } else {
                format!("{module_dir}/{build_name}")
            };
            let build_abs = self.root.join(&build_rel);
            if !build_abs.is_file() {
                continue;
            }
            let text = read_limited(&build_abs, 128 * 1024).to_lowercase();
            for marker in SPRING_TEXT_MARKERS {
                if text.contains(marker) {
                    evidence.push(format!("{build_rel}:{marker}"));
                }
            }
        }

        evidence.extend(self.scan_module_source_evidence(&module_dir));

        if is_application_config_name(&basename(&rel)) {
            evidence.push(format!("{rel}:application-config"));
        }

        let confidence = if evidence.is_empty() {
            0.0
        } else {
            (0.45 + 0.15 * evidence.len() as f64).min(1.0)
        };
        // dict.fromkeys — dedupe giữ thứ tự.
        let unique = dedupe_keep_order(&evidence);
        let result = SpringDetectionResult {
            is_spring: !unique.is_empty(),
            module_root: module_dir,
            evidence: unique,
            confidence,
        };
        self.module_cache.insert(key, result.clone());
        result
    }

    /// `discover_modules` — trả về danh sách module đã normalized (sorted keys,
    /// tuple(sorted(...)) từng trường), khớp Python.
    pub fn discover_modules(&mut self, languages: &[&str]) -> Vec<DiscoveredModule> {
        let mut modules: HashMap<String, ModuleBucket> = HashMap::new();
        for (_, rel_path) in walk_sorted(&self.root) {
            let lower = rel_path.to_lowercase();
            let name = basename(&lower);
            let is_source = lower.ends_with(".java") || lower.ends_with(".kt") || lower.ends_with(".kts");
            let is_build = SPRING_BUILD_FILES_SET.contains(&name.as_str());
            let is_config = is_application_config_name(&name);
            if !is_source && !is_build && !is_config {
                continue;
            }
            let result = self.detect_path(&rel_path);
            if !result.is_spring {
                continue;
            }
            let module_key = if result.module_root.is_empty() {
                ".".to_string()
            } else {
                result.module_root.clone()
            };
            if !modules.contains_key(&module_key) {
                modules.insert(
                    module_key.clone(),
                    ModuleBucket {
                        rel_path: module_key.clone(),
                        languages: Vec::new(),
                        build_files: Vec::new(),
                        config_files: Vec::new(),
                        evidence: Vec::new(),
                        confidence: 0.0,
                    },
                );
            }
            let bucket = modules.get_mut(&module_key).unwrap();
            for item in &result.evidence {
                if !bucket.evidence.contains(item) {
                    bucket.evidence.push(item.clone());
                }
            }
            bucket.confidence = bucket.confidence.max(result.confidence);
            if lower.ends_with(".java") && languages.contains(&"java") {
                if !bucket.languages.contains(&"java".to_string()) {
                    bucket.languages.push("java".to_string());
                }
            } else if (lower.ends_with(".kt") || lower.ends_with(".kts")) && languages.contains(&"kotlin") {
                if !bucket.languages.contains(&"kotlin".to_string()) {
                    bucket.languages.push("kotlin".to_string());
                }
            } else if is_build {
                if !bucket.build_files.contains(&rel_path) {
                    bucket.build_files.push(rel_path.clone());
                }
            } else if is_config && !bucket.config_files.contains(&rel_path) {
                bucket.config_files.push(rel_path.clone());
            }
        }

        let mut normalized: Vec<DiscoveredModule> = modules
            .into_values()
            .map(|mut bucket| {
                bucket.languages.sort();
                bucket.build_files.sort();
                bucket.config_files.sort();
                bucket.evidence.sort();
                DiscoveredModule {
                    rel_path: bucket.rel_path,
                    languages: bucket.languages,
                    build_files: bucket.build_files,
                    config_files: bucket.config_files,
                    evidence: bucket.evidence,
                    confidence: bucket.confidence,
                }
            })
            .collect();
        normalized.sort_by(|a, b| a.rel_path.cmp(&b.rel_path));
        normalized
    }

    fn module_has_android_evidence(&mut self, module_dir: &str) -> bool {
        let key = if module_dir.is_empty() { ".".to_string() } else { module_dir.to_string() };
        if let Some(cached) = self.android_cache.get(&key) {
            return *cached;
        }
        let base = if module_dir.is_empty() {
            self.root.clone()
        } else {
            self.root.join(module_dir)
        };
        if base.join("src/main/AndroidManifest.xml").is_file() {
            self.android_cache.insert(key, true);
            return true;
        }
        for gradle_name in ["build.gradle", "build.gradle.kts"] {
            let gradle = base.join(gradle_name);
            if !gradle.is_file() {
                continue;
            }
            let text = read_limited(&gradle, 128 * 1024).to_lowercase();
            if ANDROID_MARKERS.iter().any(|marker| text.contains(marker)) {
                self.android_cache.insert(key, true);
                return true;
            }
        }
        self.android_cache.insert(key, false);
        false
    }

    fn scan_module_source_evidence(&mut self, module_dir: &str) -> Vec<String> {
        let base = if module_dir.is_empty() {
            self.root.clone()
        } else {
            self.root.join(module_dir)
        };
        let src_root = base.join("src");
        if !src_root.is_dir() {
            return Vec::new();
        }
        let mut evidence: Vec<String> = Vec::new();
        for (abs_path, rel_path) in walk_sorted(&src_root) {
            let lower = abs_path.file_name().map(|n| n.to_string_lossy().to_lowercase()).unwrap_or_default();
            if !lower.ends_with(".java") && !lower.ends_with(".kt") {
                continue;
            }
            let _ = rel_path;
            let text = read_limited(&abs_path, 128 * 1024);
            for marker in SPRING_SOURCE_MARKERS {
                if text.contains(marker) {
                    let rel = strip_dot_slash(
                        &abs_path
                            .strip_prefix(&self.root)
                            .map(crate::pyutil::to_posix)
                            .unwrap_or_default(),
                    );
                    evidence.push(format!("{rel}:{marker}"));
                    break;
                }
            }
            if evidence.len() >= 50 {
                return evidence;
            }
        }
        evidence
    }
}

struct ModuleBucket {
    rel_path: String,
    languages: Vec<String>,
    build_files: Vec<String>,
    config_files: Vec<String>,
    evidence: Vec<String>,
    confidence: f64,
}

#[derive(Debug, Clone)]
pub struct DiscoveredModule {
    pub rel_path: String,
    pub languages: Vec<String>,
    pub build_files: Vec<String>,
    pub config_files: Vec<String>,
    pub evidence: Vec<String>,
    pub confidence: f64,
}

pub fn dedupe_keep_order(items: &[String]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for item in items {
        if !out.contains(item) {
            out.push(item.clone());
        }
    }
    out
}
