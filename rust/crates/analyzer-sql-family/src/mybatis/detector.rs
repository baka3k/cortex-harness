//! Port `tools/mybatis/detector.py` — MyBatis project detection.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::{Path, PathBuf};

use regex::Regex;

use crate::pyutil::strip_chars;

pub const MYBATIS_BUILD_FILES: [&str; 5] = [
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
];
const SOURCE_EXTS: [&str; 3] = [".java", ".kt", ".kts"];
pub const XML_EXTS: [&str; 1] = [".xml"];

pub const EXCLUDED_DIRS: [&str; 24] = [
    ".git", ".hg", ".svn", ".gradle", ".idea", ".mvn", ".settings", ".cache", ".parcel-cache",
    ".venv", "venv", "env", "__pycache__", ".pytest_cache", ".mypy_cache", "bin", "build",
    "coverage", "dist", "gen", "generated", "node_modules", "out", "target",
];
const ANDROID_MARKERS: [&str; 5] = [
    "com.android.application",
    "com.android.library",
    "com.android.dynamic-feature",
    "com.android.test",
    "com.android.instantapp",
];
const MYBATIS_BUILD_MARKERS: [&str; 4] = [
    "org.mybatis",
    "mybatis",
    "mybatis-spring",
    "mybatis-spring-boot-starter",
];
const MYBATIS_JAVA_MARKERS: [&str; 14] = [
    "org.apache.ibatis.annotations.Mapper",
    "org.apache.ibatis.annotations.Select",
    "org.apache.ibatis.annotations.Insert",
    "org.apache.ibatis.annotations.Update",
    "org.apache.ibatis.annotations.Delete",
    "org.apache.ibatis.annotations.Results",
    "org.apache.ibatis.annotations.ResultMap",
    "@Mapper",
    "@Select",
    "@Insert",
    "@Update",
    "@Delete",
    "@Results",
    "@ResultMap",
];
const MYBATIS_SPRING_MARKERS: [&str; 5] = [
    "org.mybatis.spring.SqlSessionFactoryBean",
    "org.mybatis.spring.mapper.MapperScannerConfigurer",
    "SqlSessionFactoryBean",
    "MapperScannerConfigurer",
    "mybatis:scan",
];

fn mapper_root_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?is)<\s*mapper\b[^>]*\bnamespace\s*=").unwrap())
}

fn config_root_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)<\s*configuration\b").unwrap())
}

fn xml_decl_or_misc_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)\A\s*(?:<\?xml\b[^?]*\?>\s*|<!--.*?-->\s*|<!DOCTYPE\b[^>]*>\s*)*").unwrap()
    })
}

fn first_element_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?s)<\s*([A-Za-z_][\w:.-]*)\b").unwrap())
}

fn mybatis_config_child_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?i)<\s*(properties|settings|typeAliases|typeHandlers|objectFactory|objectWrapperFactory|reflectorFactory|plugins|environments|databaseIdProvider|mappers)\b",
        )
        .unwrap()
    })
}

fn mybatis_dtd_re() -> &'static Regex {
    static RE: std::sync::OnceLock<Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)mybatis\.org//DTD\s+(?:Mapper|Config)").unwrap())
}

/// `safe_rel_path` — backslash→slash + strip "./" chars hai đầu.
pub fn safe_rel_path(path: &str) -> String {
    strip_chars(&path.replace('\\', "/"), "./").to_string()
}

/// `read_limited` — đọc tối đa 256KB, errors="ignore"; OSError → "".
pub fn read_limited(path: &Path) -> String {
    use std::io::Read;
    let mut handle = match std::fs::File::open(path) {
        Ok(handle) => handle,
        Err(_) => return String::new(),
    };
    let mut buffer = vec![0u8; 256 * 1024];
    let mut read_total = 0usize;
    loop {
        match handle.read(&mut buffer[read_total..]) {
            Ok(0) => break,
            Ok(n) => {
                read_total += n;
                if read_total == buffer.len() {
                    break;
                }
            }
            Err(_) => break,
        }
    }
    buffer.truncate(read_total);
    cortex_analyzer_framework::ts::decode_ignore(&buffer)
}

/// `infer_module_dir`.
pub fn infer_module_dir(rel_path: &str) -> String {
    let rel = safe_rel_path(rel_path);
    for prefix in ["src/main/", "src/test/", "src/integrationTest/", "src/androidTest/"] {
        if rel.starts_with(prefix) {
            return String::new();
        }
    }
    for token in [
        "/src/main/",
        "/src/test/",
        "/src/integrationTest/",
        "/src/androidTest/",
    ] {
        if let Some(idx) = rel.find(token) {
            return rel[..idx].to_string();
        }
    }
    match rel.rfind('/') {
        Some(idx) => rel[..idx].to_string(),
        None => String::new(),
    }
}

/// `MyBatisDetectionResult`.
#[derive(Debug, Clone, Default)]
pub struct DetectionResult {
    pub is_mybatis: bool,
    pub module_root: String,
    pub artifact_kind: String,
    pub evidence: Vec<String>,
    pub supporting_evidence: Vec<String>,
    pub confidence: f64,
}

/// `MyBatisProjectDetector`.
type ModuleEvidence = (Vec<String>, Vec<String>, f64);

pub struct MyBatisProjectDetector {
    pub root: PathBuf,
    module_cache: std::sync::Mutex<HashMap<String, ModuleEvidence>>,
    android_cache: std::sync::Mutex<HashMap<String, bool>>,
}

impl MyBatisProjectDetector {
    pub fn new(root: &Path) -> Self {
        Self {
            // os.path.realpath(os.path.abspath(root))
            root: std::fs::canonicalize(root).unwrap_or_else(|_| root.to_path_buf()),
            module_cache: std::sync::Mutex::new(HashMap::new()),
            android_cache: std::sync::Mutex::new(HashMap::new()),
        }
    }

    pub fn detect_path(&self, rel_path: &str) -> DetectionResult {
        let rel = safe_rel_path(rel_path);
        let module_dir = infer_module_dir(&rel);
        if self.module_has_android_evidence(&module_dir) {
            return DetectionResult {
                is_mybatis: false,
                module_root: module_dir,
                artifact_kind: String::new(),
                evidence: vec!["android-module".to_string()],
                supporting_evidence: Vec::new(),
                confidence: 0.0,
            };
        }

        let abs_path = self.root.join(&rel);
        let text = read_limited(&abs_path);
        let lower = rel.to_lowercase();
        let name = Path::new(&lower)
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_default();
        let mut evidence: Vec<String> = Vec::new();
        let mut supporting: Vec<String> = Vec::new();
        let mut artifact_kind = String::new();

        if lower.ends_with(".xml") {
            let (xml_kind, xml_evidence) = classify_xml_text(&rel, &text);
            if !xml_kind.is_empty() {
                artifact_kind = xml_kind;
                evidence.extend(xml_evidence);
            } else if name.ends_with("mapper.xml") {
                supporting.push(format!("{rel}:mapper-xml-name"));
            }
        } else if SOURCE_EXTS.iter().any(|ext| lower.ends_with(ext)) {
            let java_evidence = source_evidence(&rel, &text);
            if !java_evidence.is_empty() {
                artifact_kind = "java_mapper".to_string();
                evidence.extend(java_evidence);
            } else if name.ends_with("mapper.java") || name.ends_with("mapper.kt") {
                supporting.push(format!("{rel}:mapper-source-name"));
            }
        } else if MYBATIS_BUILD_FILES.contains(&name.as_str()) {
            let build_evidence =
                marker_evidence(&rel, &text.to_lowercase(), &MYBATIS_BUILD_MARKERS);
            if !build_evidence.is_empty() {
                artifact_kind = "build".to_string();
                evidence.extend(build_evidence);
            }
        }

        let direct_evidence: Vec<String> = dedup(evidence);
        let (module_evidence, module_supporting, module_confidence) =
            self.module_evidence(&module_dir);
        supporting.extend(module_supporting);
        let supporting = dedup(supporting);
        if direct_evidence.is_empty() {
            return DetectionResult {
                is_mybatis: false,
                module_root: module_dir,
                artifact_kind: String::new(),
                evidence: Vec::new(),
                supporting_evidence: supporting,
                confidence: 0.0,
            };
        }

        let mut evidence = direct_evidence;
        evidence.extend(module_evidence);
        let evidence = dedup(evidence);
        let confidence = module_confidence.max(confidence_of(&evidence, &supporting));
        DetectionResult {
            is_mybatis: true,
            module_root: module_dir,
            artifact_kind,
            evidence,
            supporting_evidence: supporting,
            confidence,
        }
    }

    /// `discover_modules` — dict module theo rel_path, files sorted.
    pub fn discover_modules(&self, languages: &[&str]) -> Vec<ModuleEntry> {
        let mut modules: BTreeMap<String, ModuleBucket> = BTreeMap::new();
        let requested: BTreeSet<&str> = languages.iter().copied().collect();
        for (_abs_path, rel_path) in self.iter_files() {
            let lower = rel_path.to_lowercase();
            let name = Path::new(&lower)
                .file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_default();
            if !(lower.ends_with(".xml")
                || SOURCE_EXTS.iter().any(|ext| lower.ends_with(ext))
                || MYBATIS_BUILD_FILES.contains(&name.as_str()))
            {
                continue;
            }
            let result = self.detect_path(&rel_path);
            if !result.is_mybatis {
                continue;
            }
            let module_key = if result.module_root.is_empty() {
                ".".to_string()
            } else {
                result.module_root.clone()
            };
            let bucket = modules.entry(module_key).or_insert_with(|| ModuleBucket {
                rel_path_default: String::new(),
                mapper_xml_files: BTreeSet::new(),
                config_xml_files: BTreeSet::new(),
                java_files: BTreeSet::new(),
                build_files: BTreeSet::new(),
                spring_config_files: BTreeSet::new(),
                evidence: BTreeSet::new(),
                confidence: result.confidence,
            });
            bucket.evidence.extend(result.evidence.iter().cloned());
            bucket.confidence = bucket.confidence.max(result.confidence);
            match result.artifact_kind.as_str() {
                "mapper_xml" => {
                    bucket.mapper_xml_files.insert(rel_path.clone());
                }
                "config_xml" => {
                    bucket.config_xml_files.insert(rel_path.clone());
                }
                "spring_xml" => {
                    bucket.spring_config_files.insert(rel_path.clone());
                }
                "java_mapper"
                    if lower.ends_with(".java") && requested.contains("java") =>
                {
                    bucket.java_files.insert(rel_path.clone());
                }
                "java_mapper"
                    if (lower.ends_with(".kt") || lower.ends_with(".kts"))
                        && requested.contains("kotlin") =>
                {
                    bucket.java_files.insert(rel_path.clone());
                }
                "build" if MYBATIS_BUILD_FILES.contains(&name.as_str()) => {
                    bucket.build_files.insert(rel_path.clone());
                }
                _ => {}
            }
        }
        modules
            .into_iter()
            .map(|(rel_path, bucket)| ModuleEntry {
                rel_path,
                mapper_xml_files: bucket.mapper_xml_files.into_iter().collect(),
                config_xml_files: bucket.config_xml_files.into_iter().collect(),
                java_files: bucket.java_files.into_iter().collect(),
                build_files: bucket.build_files.into_iter().collect(),
                spring_config_files: bucket.spring_config_files.into_iter().collect(),
                evidence: bucket.evidence.into_iter().collect(),
                confidence: bucket.confidence,
            })
            .collect()
    }

    fn iter_files(&self) -> Vec<(PathBuf, String)> {
        let mut out = Vec::new();
        walk(&self.root, &self.root, &mut out);
        out
    }

    fn module_evidence(&self, module_dir: &str) -> (Vec<String>, Vec<String>, f64) {
        let key = if module_dir.is_empty() {
            ".".to_string()
        } else {
            module_dir.to_string()
        };
        if let Some(cached) = self.module_cache.lock().unwrap().get(&key) {
            return cached.clone();
        }

        if self.module_has_android_evidence(module_dir) {
            let result = (Vec::new(), vec!["android-module".to_string()], 0.0);
            self.module_cache
                .lock()
                .unwrap()
                .insert(key.clone(), result.clone());
            return result;
        }

        let mut evidence: Vec<String> = Vec::new();
        let mut supporting: Vec<String> = Vec::new();
        let base = if module_dir.is_empty() {
            self.root.clone()
        } else {
            self.root.join(module_dir)
        };
        for build_name in ["pom.xml", "build.gradle", "build.gradle.kts"] {
            let path = base.join(build_name);
            if path.is_file() {
                let rel = if module_dir.is_empty() {
                    build_name.to_string()
                } else {
                    format!("{module_dir}/{build_name}")
                };
                evidence.extend(marker_evidence(
                    &rel,
                    &read_limited(&path).to_lowercase(),
                    &MYBATIS_BUILD_MARKERS,
                ));
            }
        }

        let src_root = base.join("src");
        if src_root.is_dir() {
            let mut early: Option<(Vec<String>, Vec<String>, f64)> = None;
            walk_excluded(&src_root, &mut |dirpath, name| {
                if early.is_some() {
                    return;
                }
                let abs_path = dirpath.join(name);
                let rel = safe_rel_path(&abs_path
                    .strip_prefix(&self.root)
                    .unwrap_or(&abs_path)
                    .to_string_lossy());
                let lower = rel.to_lowercase();
                let text = read_limited(&abs_path);
                if lower.ends_with(".xml") {
                    let (kind, xml_evidence) = classify_xml_text(&rel, &text);
                    evidence.extend(xml_evidence);
                    if kind.is_empty() && name.to_lowercase().ends_with("mapper.xml") {
                        supporting.push(format!("{rel}:mapper-xml-name"));
                    }
                } else if SOURCE_EXTS.iter().any(|ext| lower.ends_with(ext)) {
                    evidence.extend(source_evidence(&rel, &text));
                    if name.to_lowercase().ends_with("mapper.java")
                        || name.to_lowercase().ends_with("mapper.kt")
                    {
                        supporting.push(format!("{rel}:mapper-source-name"));
                    }
                }
                if evidence.len() >= 80 {
                    let confidence = confidence_of(&evidence, &supporting);
                    early = Some((
                        dedup(evidence.clone()),
                        dedup(supporting.clone()),
                        confidence,
                    ));
                }
            });
            if let Some(result) = early {
                self.module_cache
                    .lock()
                    .unwrap()
                    .insert(key.clone(), result.clone());
                return result;
            }
        }

        let confidence = confidence_of(&evidence, &supporting);
        let result = (dedup(evidence), dedup(supporting), confidence);
        self.module_cache
            .lock()
            .unwrap()
            .insert(key.clone(), result.clone());
        result
    }

    fn module_has_android_evidence(&self, module_dir: &str) -> bool {
        let key = if module_dir.is_empty() {
            ".".to_string()
        } else {
            module_dir.to_string()
        };
        if let Some(cached) = self.android_cache.lock().unwrap().get(&key) {
            return *cached;
        }
        let base = if module_dir.is_empty() {
            self.root.to_path_buf()
        } else {
            self.root.join(module_dir)
        };
        let mut result = false;
        let manifest = base.join("src").join("main").join("AndroidManifest.xml");
        if manifest.is_file() {
            result = true;
        } else {
            for gradle_name in ["build.gradle", "build.gradle.kts"] {
                let gradle = base.join(gradle_name);
                if gradle.is_file() {
                    let text = read_limited(&gradle).to_lowercase();
                    if ANDROID_MARKERS.iter().any(|marker| text.contains(marker)) {
                        result = true;
                        break;
                    }
                }
            }
        }
        self.android_cache.lock().unwrap().insert(key, result);
        result
    }
}

#[derive(Debug, Clone)]
pub struct ModuleEntry {
    pub rel_path: String,
    pub mapper_xml_files: Vec<String>,
    pub config_xml_files: Vec<String>,
    pub java_files: Vec<String>,
    pub build_files: Vec<String>,
    pub spring_config_files: Vec<String>,
    pub evidence: Vec<String>,
    pub confidence: f64,
}

struct ModuleBucket {
    #[allow(dead_code)]
    rel_path_default: String,
    mapper_xml_files: BTreeSet<String>,
    config_xml_files: BTreeSet<String>,
    java_files: BTreeSet<String>,
    build_files: BTreeSet<String>,
    spring_config_files: BTreeSet<String>,
    evidence: BTreeSet<String>,
    confidence: f64,
}

fn walk(root: &Path, dir: &Path, out: &mut Vec<(PathBuf, String)>) {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(_) => return,
    };
    let mut subdirs: Vec<PathBuf> = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().to_string();
        if path.is_dir() {
            if !EXCLUDED_DIRS.contains(&name.as_str())
                && !name.starts_with('.')
                && !cortex_analyzer_framework::scan::matches_extra_ignore(&name)
            {
                subdirs.push(path);
            }
            continue;
        }
        let rel = safe_rel_path(&path
            .strip_prefix(root)
            .unwrap_or(&path)
            .to_string_lossy());
        out.push((path, rel));
    }
    for sub in subdirs {
        walk(root, &sub, out);
    }
}

fn walk_excluded(dir: &Path, visit: &mut impl FnMut(&Path, &str)) {
    let entries = match std::fs::read_dir(dir) {
        Ok(entries) => entries,
        Err(_) => return,
    };
    let mut subdirs: Vec<PathBuf> = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().to_string();
        if path.is_dir() {
            if !EXCLUDED_DIRS.contains(&name.as_str())
                && !name.starts_with('.')
                && !cortex_analyzer_framework::scan::matches_extra_ignore(&name)
            {
                subdirs.push(path);
            }
            continue;
        }
        visit(dir, &name);
    }
    for sub in subdirs {
        walk_excluded(&sub, visit);
    }
}

/// `classify_xml_text` → (kind, evidence).
pub fn classify_xml_text(rel_path: &str, text: &str) -> (String, Vec<String>) {
    let mut evidence: Vec<String> = Vec::new();
    let root_tag = first_element_name(text);
    if root_tag == "mapper" && mapper_root_re().is_match(text) {
        evidence.push(format!("{rel_path}:mapper-root-namespace"));
        return ("mapper_xml".to_string(), evidence);
    }
    if mybatis_dtd_re().is_match(text) {
        evidence.push(format!("{rel_path}:mybatis-dtd"));
        if text.contains("DTD Mapper") || text.contains("<mapper") {
            return ("mapper_xml".to_string(), evidence);
        }
        if text.contains("DTD Config")
            || root_tag == "configuration"
            || config_root_re().is_match(text)
        {
            return ("config_xml".to_string(), evidence);
        }
    }
    let spring_evidence = marker_evidence(rel_path, text, &MYBATIS_SPRING_MARKERS);
    if !spring_evidence.is_empty() {
        return ("spring_xml".to_string(), spring_evidence);
    }
    if root_tag == "configuration"
        && (text.to_lowercase().contains("mybatis") || mybatis_config_child_re().is_match(text))
    {
        evidence.push(format!("{rel_path}:mybatis-configuration-root"));
        return ("config_xml".to_string(), evidence);
    }
    (String::new(), evidence)
}

/// `_first_element_name`.
pub fn first_element_name(text: &str) -> String {
    let matched = xml_decl_or_misc_re().find(text).map(|m| m.end()).unwrap_or(0);
    let rest = &text[matched..];
    match first_element_re().captures(rest) {
        Some(caps) => caps[1]
            .rsplit(':')
            .next()
            .unwrap_or(&caps[1])
            .to_lowercase(),
        None => String::new(),
    }
}

/// `_source_evidence`.
fn source_evidence(rel_path: &str, text: &str) -> Vec<String> {
    marker_evidence(rel_path, text, &MYBATIS_JAVA_MARKERS)
}

/// `_marker_evidence`.
fn marker_evidence(rel_path: &str, text: &str, markers: &[&str]) -> Vec<String> {
    markers
        .iter()
        .filter(|marker| text.contains(*marker))
        .map(|marker| format!("{rel_path}:{marker}"))
        .collect()
}

/// `_confidence`.
pub fn confidence_of(evidence: &[String], supporting: &[String]) -> f64 {
    if evidence.is_empty() {
        return 0.0;
    }
    (0.55 + 0.12 * evidence.len() as f64 + 0.04 * supporting.len() as f64).min(1.0)
}

fn dedup(items: Vec<String>) -> Vec<String> {
    let mut seen = BTreeSet::new();
    let mut out = Vec::with_capacity(items.len());
    for item in items {
        if seen.insert(item.clone()) {
            out.push(item);
        }
    }
    out
}
