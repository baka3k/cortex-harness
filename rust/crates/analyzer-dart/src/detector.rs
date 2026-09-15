//! Rust port của `tools/flutter/detector.py` — Flutter project discovery từ
//! pubspec.yaml (`detect_flutter_project` + `project_package_name`).
//!
//! YAML: thay vì kéo thêm dependency (PyYAML phía Python), module này parse
//! đúng tập construct pubspec cần cho detection — block mapping lồng nhau,
//! scalar có/không quote, comment `#`. List/flow-style fallback về scalar
//! thô (không dùng cho detection). Pubspec malformed ngoài tập này được nuốt
//! thành map rỗng thay vì hard error — hành vi chỉ khác Python ở error text
//! của trường hợp hỏng hẳn (không ảnh hưởng graph).

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// Cây YAML tối thiểu cho pubspec detection.
#[derive(Debug, Clone, PartialEq)]
pub enum Yaml {
    Map(BTreeMap<String, Yaml>),
    Scalar(String),
}

impl Yaml {
    pub fn get(&self, key: &str) -> Option<&Yaml> {
        match self {
            Yaml::Map(map) => map.get(key),
            Yaml::Scalar(_) => None,
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Yaml::Scalar(value) => Some(value.as_str()),
            Yaml::Map(_) => None,
        }
    }
}

struct Line {
    indent: usize,
    key: String,
    value: Option<String>,
}

/// Parse pubspec text thành cây block mapping (YAML subset: mapping lồng,
/// scalar có/không quote, comment `#`; list item `- x` được bỏ qua — không
/// dùng cho detection). Trả Err khi không có dòng `key:` hợp lệ nào.
pub fn parse_pubspec(text: &str) -> Result<Yaml, String> {
    let mut lines: Vec<Line> = Vec::new();
    for raw in text.lines() {
        let stripped = strip_comment(raw);
        if stripped.trim().is_empty() {
            continue;
        }
        let indent = stripped.len() - stripped.trim_start().len();
        let content = stripped.trim_start();
        if content.starts_with('-') || content.starts_with('%') || content.starts_with("---") {
            continue;
        }
        let Some((key, value)) = content.split_once(':') else {
            continue;
        };
        let key = unquote(key.trim());
        if key.is_empty() {
            continue;
        }
        let value = value.trim();
        lines.push(Line {
            indent,
            key,
            value: if value.is_empty() {
                None
            } else {
                Some(unquote(value))
            },
        });
    }
    let Some(first_indent) = lines.first().map(|line| line.indent) else {
        return Err("pubspec does not contain a YAML mapping".to_string());
    };
    let (map, _) = parse_block(&lines, 0, first_indent);
    Ok(Yaml::Map(map))
}

/// Recursive descent cho một block mapping ở `indent` cho trước.
fn parse_block(lines: &[Line], start: usize, indent: usize) -> (BTreeMap<String, Yaml>, usize) {
    let mut map = BTreeMap::new();
    let mut index = start;
    while index < lines.len() {
        let line = &lines[index];
        if line.indent < indent {
            break;
        }
        if line.indent > indent {
            // Dòng sâu hơn mà không có key cha không value phía trước —
            // defensive skip (không xảy ra trên pubspec well-formed).
            index += 1;
            continue;
        }
        match &line.value {
            Some(value) => {
                map.insert(line.key.clone(), Yaml::Scalar(value.clone()));
                index += 1;
            }
            None => {
                if index + 1 < lines.len() && lines[index + 1].indent > indent {
                    let (child, next) = parse_block(lines, index + 1, lines[index + 1].indent);
                    map.insert(line.key.clone(), Yaml::Map(child));
                    index = next;
                } else {
                    map.insert(line.key.clone(), Yaml::Map(BTreeMap::new()));
                    index += 1;
                }
            }
        }
    }
    (map, index)
}

/// Bỏ comment `#` ngoài chuỗi quote (xấp xỉ yaml; pubspec thực tế đủ dùng).
fn strip_comment(line: &str) -> &str {
    let bytes = line.as_bytes();
    let mut quote: Option<u8> = None;
    for (index, byte) in bytes.iter().enumerate() {
        match quote {
            Some(q) if *byte == q => quote = None,
            Some(_) => {}
            None => {
                if *byte == b'\'' || *byte == b'"' {
                    quote = Some(*byte);
                } else if *byte == b'#' && (index == 0 || bytes[index - 1] == b' ' || bytes[index - 1] == b'\t') {
                    return &line[..index];
                }
            }
        }
    }
    line
}

fn unquote(value: &str) -> String {
    let trimmed = value.trim();
    let bytes = trimmed.as_bytes();
    if trimmed.len() >= 2 && (bytes[0] == b'\'' || bytes[0] == b'"') && bytes[trimmed.len() - 1] == bytes[0] {
        return trimmed[1..trimmed.len() - 1].to_string();
    }
    trimmed.to_string()
}

/// `load_pubspec` — None khi không có pubspec.yaml.
pub fn load_pubspec(root: &Path) -> Result<Option<Yaml>, String> {
    let pubspec_path = root.join("pubspec.yaml");
    if !pubspec_path.is_file() {
        return Ok(None);
    }
    let text = std::fs::read_to_string(&pubspec_path)
        .map_err(|e| format!("cannot parse pubspec {}: {}", pubspec_path.display(), e))?;
    match parse_pubspec(&text) {
        Ok(value) => Ok(Some(value)),
        Err(reason) => Err(format!("cannot parse pubspec {}: {}", pubspec_path.display(), reason)),
    }
}

/// `project_package_name` — pubspec `name` hoặc basename của root.
pub fn project_package_name(root: &Path) -> Result<String, String> {
    let pubspec = load_pubspec(root)?;
    let fallback = basename(root);
    Ok(match pubspec {
        Some(value) => value
            .get("name")
            .and_then(Yaml::as_str)
            .filter(|name| !name.is_empty())
            .map(str::to_string)
            .unwrap_or(fallback),
        None => fallback,
    })
}

/// `FlutterProject` — rút gọn đúng các field orchestrator phase09 cần đối
/// chiếu (package_name + evidence).
#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct FlutterProject {
    pub root: PathBuf,
    pub package_name: String,
    pub sdk_constraint: String,
    pub flutter_constraint: String,
    pub evidence: Vec<String>,
}

fn sdk_dependency(value: Option<&Yaml>) -> bool {
    value
        .and_then(|dep| dep.get("sdk"))
        .and_then(Yaml::as_str)
        .map(|sdk| sdk.trim().to_lowercase() == "flutter")
        .unwrap_or(false)
}

/// `detect_flutter_project` — pubspec có `dependencies.flutter.sdk: flutter`.
pub fn detect_flutter_project(root: &Path) -> Result<Option<FlutterProject>, String> {
    let Some(value) = load_pubspec(root)? else {
        return Ok(None);
    };
    let flutter_dep = value.get("dependencies").and_then(|deps| deps.get("flutter"));
    if !sdk_dependency(flutter_dep) {
        return Ok(None);
    }
    let mut evidence = vec!["dependencies.flutter.sdk=flutter".to_string()];
    if value.get("flutter").is_some() {
        evidence.push("flutter-section".to_string());
    }
    Ok(Some(FlutterProject {
        root: root.to_path_buf(),
        package_name: value
            .get("name")
            .and_then(Yaml::as_str)
            .filter(|name| !name.is_empty())
            .map(str::to_string)
            .unwrap_or_else(|| basename(root)),
        sdk_constraint: value
            .get("environment")
            .and_then(|env| env.get("sdk"))
            .and_then(Yaml::as_str)
            .unwrap_or_default()
            .to_string(),
        flutter_constraint: value
            .get("environment")
            .and_then(|env| env.get("flutter"))
            .and_then(Yaml::as_str)
            .unwrap_or_default()
            .to_string(),
        evidence,
    }))
}

fn basename(path: &Path) -> String {
    path.file_name()
        .map(|name| name.to_string_lossy().to_string())
        .unwrap_or_else(|| path.to_string_lossy().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detects_flutter_from_pubspec() {
        let dir = std::env::temp_dir().join(format!("analyzer_dart_detect_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join("pubspec.yaml"),
            "name: my_app\ndescription: test\nenvironment:\n  sdk: \">=3.3.0 <4.0.0\"\ndependencies:\n  flutter:\n    sdk: flutter\n  cupertino_icons: ^1.0.6\nflutter:\n  uses-material-design: true\n",
        )
        .unwrap();
        let project = detect_flutter_project(&dir).unwrap().expect("flutter");
        assert_eq!(project.package_name, "my_app");
        assert_eq!(project.sdk_constraint, ">=3.3.0 <4.0.0");
        assert_eq!(project.evidence, vec!["dependencies.flutter.sdk=flutter", "flutter-section"]);
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn plain_dart_package_is_not_flutter() {
        let dir = std::env::temp_dir().join(format!("analyzer_dart_plain_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("pubspec.yaml"), "name: plain_pkg\ndependencies:\n  http: ^1.0.0\n").unwrap();
        assert!(detect_flutter_project(&dir).unwrap().is_none());
        assert_eq!(project_package_name(&dir).unwrap(), "plain_pkg");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn missing_pubspec_falls_back_to_dir_name() {
        let dir = std::env::temp_dir().join(format!("analyzer_dart_nopub_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        assert!(load_pubspec(&dir).unwrap().is_none());
        let name = project_package_name(&dir).unwrap();
        assert!(name.starts_with("analyzer_dart_nopub_"));
        std::fs::remove_dir_all(&dir).ok();
    }
}
