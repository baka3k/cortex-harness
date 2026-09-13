//! Port của `tools/android/android_common.py` (phần android_kotlin_analyzer
//! dùng): dataclass defs, symbol-id helpers, `_parse_android_manifest`
//! (xml.etree.ElementTree → DOM thuần Rust, kèm serializer khớp
//! `ET.tostring(elem, encoding="unicode")` cho `code`), directory nodes +
//! relations, `_normalize_rel_path`.
//!
//! Scope KhÁC có chủ đích: `_scan_android_manifest_files` của common (bản
//! common có skip-set riêng) KHÔNG được analyzer dùng — analyzer tự scan bằng
//! `_ANDROID_SKIP_DIRS` của nó (xem `scan.rs`).

use serde_json::{json, Map, Value};

pub type Row = Map<String, Value>;

// ── Symbol id helpers (khớp android_common.py) ──────────────────────────────

pub fn manifest_symbol_id(rel_path: &str) -> String {
    format!("manifest::{rel_path}")
}

pub fn component_symbol_id(
    component_type: &str,
    class_name: Option<&str>,
    rel_path: &str,
    line: i64,
) -> String {
    let base = class_name.unwrap_or("unknown");
    format!("component::{component_type}:{base}@{rel_path}:{line}")
}

pub fn resource_symbol_id(res_type: &str, name: &str) -> String {
    format!("resource::{res_type}/{name}")
}

pub fn module_symbol_id(module_path: &str) -> String {
    format!("module::{module_path}")
}

pub fn dependency_symbol_id(coordinate: &str) -> String {
    format!("dependency::{coordinate}")
}

pub fn annotation_symbol_id(name: &str) -> String {
    format!("annotation::{name}")
}

pub fn nav_route_symbol_id(route: &str) -> String {
    format!("nav_route::{route}")
}

pub fn intent_action_symbol_id(action: &str) -> String {
    format!("intent_action::{action}")
}

pub fn handler_message_symbol_id(token: &str) -> String {
    format!("handler_message::{token}")
}

pub fn directory_symbol_id(dir_path: &str) -> String {
    format!("dir::{dir_path}")
}

/// `_resolve_android_class_name` — relative class name → fully qualified.
pub fn resolve_android_class_name(
    name: Option<&str>,
    package_name: Option<&str>,
) -> Option<String> {
    let name = name?;
    if name.is_empty() {
        return None;
    }
    if let Some(stripped) = name.strip_prefix('.') {
        return Some(match package_name {
            Some(package) => format!("{package}.{stripped}"),
            None => stripped.to_string(),
        });
    }
    if name.contains('.') {
        return Some(name.to_string());
    }
    Some(match package_name {
        Some(package) => format!("{package}.{name}"),
        None => name.to_string(),
    })
}

/// `_display_android_component_name` — tên component thân thiện (không "." đầu).
pub fn display_android_component_name(
    raw_name: Option<&str>,
    class_name: Option<&str>,
) -> String {
    if let Some(class_name) = class_name
        && let Some(simple) = class_name.trim().split('.').next_back()
        && !simple.is_empty()
    {
        return simple.to_string();
    }
    let value = (raw_name.unwrap_or("")).trim();
    value.strip_prefix('.').unwrap_or(value).to_string()
}

/// `_parse_bool` — "true"/"1"/"false"/"0" (case-insensitive).
pub fn parse_bool(value: Option<&str>) -> Option<bool> {
    let value = value?;
    match value.trim().to_lowercase().as_str() {
        "true" | "1" => Some(true),
        "false" | "0" => Some(false),
        _ => None,
    }
}

/// `_strip_ns` — bỏ namespace `{uri}` khỏi tag.
pub fn strip_ns(tag: &str) -> &str {
    match tag.split_once('}') {
        Some((_, local)) => local,
        None => tag,
    }
}

/// `_normalize_rel_path` — slash-hoá, bỏ "./" đầu và "/" hai đầu.
pub fn normalize_rel_path(path: &str) -> String {
    let mut normalized = path.replace('\\', "/").trim().to_string();
    while let Some(stripped) = normalized.strip_prefix("./") {
        normalized = stripped.to_string();
    }
    normalized.trim_matches('/').to_string()
}

// ── XML DOM khớp hành vi xml.etree.ElementTree ──────────────────────────────

/// Phần tử XML tối giản: tag đã resolve `{uri}local`, attrs theo thứ tự văn
/// bản (tên đã resolve), text/tail nguyên bản, sourceline 1-based.
#[derive(Debug, Clone, Default)]
pub struct XmlElement {
    /// `{uri}local` hoặc `local`.
    pub tag: String,
    /// `(resolved_name, decoded_value)` theo thứ tự văn bản; không chứa xmlns.
    pub attrib: Vec<(String, String)>,
    /// Text sau start tag trước con đầu (nguyên bản, chưa escape).
    pub text: String,
    /// Tail của element này (text sau end tag, do parent sở hữu khi serialize).
    pub tail: String,
    pub children: Vec<XmlElement>,
    /// ET `sourceline` — ET của Python 3.12 không set nên luôn 0; giữ field
    /// để phản chiếu shape (không đọc).
    #[allow(dead_code)]
    pub sourceline: i64,
}

impl XmlElement {
    pub fn get(&self, name: &str) -> Option<&str> {
        self.attrib
            .iter()
            .find(|(key, _)| key == name)
            .map(|(_, value)| value.as_str())
    }

    /// `_android_attr` — name trần, `{android}name`, rồi `android:name`.
    pub fn android_attr(&self, name: &str) -> Option<&str> {
        self.get(name)
            .or_else(|| {
                self.get(&format!(
                    "{{http://schemas.android.com/apk/res/android}}{name}"
                ))
            })
            .or_else(|| self.get(&format!("android:{name}")))
    }

    /// `root_elem.iter()` — depth-first pre-order, gồm chính nó.
    pub fn iter(&self) -> Vec<&XmlElement> {
        let mut out = vec![self];
        for child in &self.children {
            out.extend(child.iter());
        }
        out
    }

    /// `ET.tostring(elem, encoding="unicode")` cho subtree không default
    /// namespace — khớp `_serialize_xml` của stdlib: xmlns decl tự sinh
    /// (ns0, ns1… theo thứ tự xuất hiện đầu tiên) đứng trước attrs, attrs
    /// giữ thứ tự văn bản với prefix đã resolve, empty element tự đóng ` />`,
    /// text/tail giữ nguyên (escape & < > và \r).
    pub fn et_tostring(&self) -> String {
        // Bước 1: gán prefix cho mọi URI xuất hiện trong subtree (theo thứ tự
        // document: element tag trước, rồi attrs theo thứ tự).
        let mut prefixes: Vec<(String, String)> = Vec::new(); // (uri, prefix)
        let prefix_of = |prefixes: &mut Vec<(String, String)>, uri: &str| -> String {
            if let Some((_, prefix)) = prefixes.iter().find(|(known, _)| known == uri) {
                return prefix.clone();
            }
            // stdlib dùng bảng _namespace_map cho vài URI nổi tiếng; android
            // không nằm trong bảng → ns{i} theo counter tăng dần.
            let prefix = format!("ns{}", prefixes.len());
            prefixes.push((uri.to_string(), prefix.clone()));
            prefix
        };
        let mut walk: Vec<&XmlElement> = self.iter();
        for element in walk.drain(..) {
            if let Some((uri, _)) = split_qname(&element.tag) {
                prefix_of(&mut prefixes, &uri);
            }
            for (key, _) in &element.attrib {
                if let Some((uri, _)) = split_qname(key) {
                    prefix_of(&mut prefixes, &uri);
                }
            }
        }

        // Bước 2: serialize.
        let mut out = String::new();
        serialize_element(self, &prefixes, &mut out, true);
        // ET.tostring(root) ghi cả tail của root (khác ElementTree.write).
        out.push_str(&escape_cdata(&self.tail));
        out
    }
}

/// Tách `{uri}local` → Some((uri, local)); tên trần → None.
fn split_qname(name: &str) -> Option<(String, String)> {
    if name.starts_with('{') {
        let close = name.find('}')?;
        Some((name[1..close].to_string(), name[close + 1..].to_string()))
    } else {
        None
    }
}

fn qname_serial(name: &str, prefixes: &[(String, String)]) -> String {
    match split_qname(name) {
        Some((uri, local)) => {
            let prefix = prefixes
                .iter()
                .find(|(known, _)| *known == uri)
                .map(|(_, prefix)| prefix.clone())
                .unwrap_or_default();
            format!("{prefix}:{local}")
        }
        None => name.to_string(),
    }
}

fn escape_cdata(text: &str) -> String {
    // _escape_cdata: & < > và \r.
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('\r', "&#13;")
}

fn escape_attrib(text: &str) -> String {
    // _escape_attrib: & < > " và \r \n \t.
    text.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\r', "&#13;")
        .replace('\n', "&#10;")
        .replace('\t', "&#09;")
}

fn serialize_element(
    element: &XmlElement,
    prefixes: &[(String, String)],
    out: &mut String,
    is_root: bool,
) {
    let tag = qname_serial(&element.tag, prefixes);
    out.push('<');
    out.push_str(&tag);
    // xmlns decls tự sinh — chỉ ở root của subtree, đứng trước attrs (khớp
    // _serialize_xml của stdlib).
    if is_root {
        for (uri, prefix) in prefixes {
            out.push_str(&format!(" xmlns:{prefix}=\"{}\"", escape_attrib(uri)));
        }
    }
    for (key, value) in &element.attrib {
        out.push_str(&format!(
            " {}=\"{}\"",
            qname_serial(key, prefixes),
            escape_attrib(value)
        ));
    }
    if element.children.is_empty() && element.text.is_empty() {
        out.push_str(" />");
        // ET.tostring include tail của root element (khác ElementTree.write).
        return;
    }
    out.push('>');
    out.push_str(&escape_cdata(&element.text));
    for child in &element.children {
        serialize_element(child, prefixes, out, false);
        out.push_str(&escape_cdata(&child.tail));
    }
    out.push_str(&format!("</{tag}>"));
}

// ── Parse manifest ──────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct AndroidManifestDef {
    pub symbol_id: String,
    pub package_name: Option<String>,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    pub code: String,
    pub summary: String,
    pub note: String,
}

#[derive(Debug, Clone)]
pub struct AndroidComponentDef {
    pub symbol_id: String,
    pub name: String,
    pub component_type: String,
    pub class_name: Option<String>,
    pub exported: Option<bool>,
    pub process: Option<String>,
    pub permission: Option<String>,
    pub enabled: Option<bool>,
    pub direct_boot_aware: Option<bool>,
    pub target_activity: Option<String>,
    pub intent_actions: Vec<String>,
    pub intent_categories: Vec<String>,
    pub intent_data: Vec<String>,
    pub file_path: String,
    pub start_line: i64,
    pub end_line: i64,
    pub code: String,
    pub summary: String,
    pub note: String,
}

/// `_parse_android_manifest` — (manifest_def, components); ParseError ⇒
/// manifest_def với package None + components rỗng.
pub fn parse_android_manifest(
    path: &std::path::Path,
    root: &std::path::Path,
) -> (AndroidManifestDef, Vec<AndroidComponentDef>) {
    let rel_path = crate::scan::rel_path_posix(root, path);
    let content = match std::fs::read(path) {
        Ok(bytes) => cortex_analyzer_framework::ts::decode_ignore(&bytes),
        Err(_) => String::new(),
    };
    let end_line = content.matches('\n').count() as i64 + 1;
    let mut package_name: Option<String> = None;
    let mut components: Vec<AndroidComponentDef> = Vec::new();

    if let Some(root_elem) = crate::xmldom::parse_document(&content) {
        package_name = root_elem.get("package").map(str::to_string);
        for elem in root_elem.iter() {
            let tag = strip_ns(&elem.tag).to_string();
            if !matches!(
                tag.as_str(),
                "activity" | "activity-alias" | "service" | "receiver" | "provider"
            ) {
                continue;
            }
            let raw_name = elem.android_attr("name").map(str::to_string);
            let class_name = resolve_android_class_name(
                raw_name.as_deref(),
                package_name.as_deref(),
            );
            let display_name =
                display_android_component_name(raw_name.as_deref(), class_name.as_deref());
            let exported = parse_bool(elem.android_attr("exported"));
            let enabled = parse_bool(elem.android_attr("enabled"));
            let direct_boot = parse_bool(elem.android_attr("directBootAware"));
            let process = elem.android_attr("process").map(str::to_string);
            let permission = elem.android_attr("permission").map(str::to_string);
            let target_activity = elem.android_attr("targetActivity").map(str::to_string);
            // Python: getattr(elem, "sourceline", 0) or 0 — ET của Python
            // 3.12 không set sourceline ⇒ luôn 0 (cả symbol_id lẫn start_line).
            let start_line = 0i64;
            let code = elem.et_tostring();
            let mut intent_actions: Vec<String> = Vec::new();
            let mut intent_categories: Vec<String> = Vec::new();
            let mut intent_data: Vec<String> = Vec::new();

            for child in &elem.children {
                if strip_ns(&child.tag) != "intent-filter" {
                    continue;
                }
                for sub in &child.children {
                    let sub_tag = strip_ns(&sub.tag);
                    if sub_tag == "action" {
                        if let Some(action_name) = sub.android_attr("name") {
                            intent_actions.push(action_name.to_string());
                        }
                    } else if sub_tag == "category" {
                        if let Some(category_name) = sub.android_attr("name") {
                            intent_categories.push(category_name.to_string());
                        }
                    } else if sub_tag == "data" {
                        let mut data_parts: Vec<String> = Vec::new();
                        for key in [
                            "scheme",
                            "host",
                            "port",
                            "path",
                            "pathPrefix",
                            "pathPattern",
                            "mimeType",
                        ] {
                            if let Some(value) = sub.android_attr(key) {
                                data_parts.push(format!("{key}={value}"));
                            }
                        }
                        if !data_parts.is_empty() {
                            intent_data.push(data_parts.join(","));
                        }
                    }
                }
            }

            let component_id = component_symbol_id(
                &tag,
                class_name.as_deref(),
                &rel_path,
                start_line,
            );
            components.push(AndroidComponentDef {
                symbol_id: component_id,
                name: display_name,
                component_type: tag,
                class_name,
                exported,
                process,
                permission,
                enabled,
                direct_boot_aware: direct_boot,
                target_activity,
                intent_actions,
                intent_categories,
                intent_data,
                file_path: rel_path.clone(),
                start_line,
                end_line: start_line,
                code,
                summary: String::new(),
                note: String::new(),
            });
        }
    }

    let manifest_def = AndroidManifestDef {
        symbol_id: manifest_symbol_id(&rel_path),
        package_name,
        file_path: rel_path,
        start_line: 1,
        end_line,
        code: content,
        summary: String::new(),
        note: String::new(),
    };
    (manifest_def, components)
}

// ── Directory nodes + relations ─────────────────────────────────────────────

/// `_scan_android_directory_paths` — relative dir paths (loại root), sorted
/// unique. Caller truyền skip-set của analyzer (xem scan.rs).
pub fn scan_android_directory_paths(
    root: &std::path::Path,
    is_skipped: &dyn Fn(&str) -> bool,
) -> Vec<String> {
    let mut directory_paths: Vec<String> = Vec::new();
    let mut stack: Vec<std::path::PathBuf> = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let entries = match std::fs::read_dir(&dir) {
            Ok(entries) => entries,
            Err(_) => continue,
        };
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            if !entry.path().is_dir() || is_skipped(&name) {
                continue;
            }
            stack.push(entry.path());
        }
        let rel_dir = crate::scan::rel_path_posix(root, &dir);
        if rel_dir == "." || rel_dir.is_empty() {
            continue;
        }
        let normalized = normalize_rel_path(&rel_dir);
        if !normalized.is_empty() {
            directory_paths.push(normalized);
        }
    }
    directory_paths.sort();
    directory_paths.dedup();
    directory_paths
}

/// `_build_directory_nodes_and_relations` — Directory nodes + CONTAINS edges
/// cho file tree (Project → dir… → File).
pub fn build_directory_nodes_and_relations(
    file_paths: &[String],
    directory_paths: &[String],
    project_id: &str,
    project_name: &str,
    language: &str,
    repo: &str,
    build_system: &str,
) -> (Vec<Row>, Vec<Row>) {
    let mut normalized_files: Vec<String> = Vec::new();
    for file_path in file_paths {
        let rel = normalize_rel_path(file_path);
        if !rel.is_empty() {
            normalized_files.push(rel);
        }
    }
    normalized_files.sort();
    normalized_files.dedup();

    let mut dir_paths: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
    for dir_path in directory_paths {
        let normalized = normalize_rel_path(dir_path);
        if normalized.is_empty() {
            continue;
        }
        let mut current = normalized;
        loop {
            dir_paths.insert(current.clone());
            let Some((parent, _)) = current.rsplit_once('/') else {
                break;
            };
            current = parent.to_string();
        }
    }

    for rel in &normalized_files {
        let dirname = parent_dir(rel);
        let mut current = normalize_rel_path(&dirname);
        loop {
            if current.is_empty() {
                break;
            }
            dir_paths.insert(current.clone());
            let Some((parent, _)) = current.rsplit_once('/') else {
                break;
            };
            current = parent.to_string();
        }
    }

    let normalized_project_id =
        cortex_graph_writer::project_scope::project_id_lookup_key(Some(project_id))
            .unwrap_or_else(|| project_id.to_string());
    let mut directory_rows: Vec<Row> = Vec::new();
    for dir_path in &dir_paths {
        let parts: Vec<&str> = dir_path.split('/').filter(|p| !p.is_empty()).collect();
        let mut row = Row::new();
        row.insert("id".into(), json!(directory_symbol_id(dir_path)));
        row.insert(
            "name".into(),
            json!(parts.last().copied().unwrap_or(dir_path)),
        );
        row.insert("path".into(), json!(dir_path));
        row.insert("depth".into(), json!(parts.len() as i64));
        row.insert("project_id".into(), json!(project_id));
        row.insert("project_id_normalized".into(), json!(normalized_project_id));
        row.insert("project_name".into(), json!(project_name));
        row.insert("language".into(), json!(language));
        row.insert("repo".into(), json!(repo));
        row.insert("build_system".into(), json!(build_system));
        directory_rows.push(row);
    }

    let mut relation_seen: std::collections::BTreeSet<(String, String)> =
        std::collections::BTreeSet::new();
    let mut relation_rows: Vec<Row> = Vec::new();

    for dir_path in &dir_paths {
        let child_id = directory_symbol_id(dir_path);
        let (parent_dir, parent_id) = match dir_path.rsplit_once('/') {
            Some((parent, _)) => (parent.to_string(), directory_symbol_id(parent)),
            None => (String::new(), project_id.to_string()),
        };
        let key = (parent_id.clone(), child_id.clone());
        if !relation_seen.insert(key) {
            continue;
        }
        let mut row = Row::new();
        row.insert(
            "source_label".into(),
            json!(if parent_dir.is_empty() { "Project" } else { "Directory" }),
        );
        row.insert("source_id".into(), json!(parent_id));
        row.insert("target_label".into(), json!("Directory"));
        row.insert("target_id".into(), json!(child_id));
        row.insert("rel_type".into(), json!("CONTAINS"));
        row.insert("properties".into(), Value::Object(Row::new()));
        relation_rows.push(row);
    }

    for rel in &normalized_files {
        let parent_dir = normalize_rel_path(&parent_dir(rel));
        if parent_dir.is_empty() {
            continue;
        }
        let source_id = directory_symbol_id(&parent_dir);
        let key = (source_id.clone(), rel.clone());
        if !relation_seen.insert(key) {
            continue;
        }
        let mut row = Row::new();
        row.insert("source_label".into(), json!("Directory"));
        row.insert("source_id".into(), json!(source_id));
        row.insert("target_label".into(), json!("File"));
        row.insert("target_id".into(), json!(rel));
        row.insert("rel_type".into(), json!("CONTAINS"));
        row.insert("properties".into(), Value::Object(Row::new()));
        relation_rows.push(row);
    }

    (directory_rows, relation_rows)
}

/// `os.path.dirname` theo POSIX.
fn parent_dir(rel: &str) -> String {
    match rel.rsplit_once('/') {
        Some((parent, _)) => parent.to_string(),
        None => String::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn symbol_ids_match_python() {
        assert_eq!(manifest_symbol_id("a/b.xml"), "manifest::a/b.xml");
        assert_eq!(
            component_symbol_id("activity", Some("com.A"), "m.xml", 7),
            "component::activity:com.A@m.xml:7"
        );
        assert_eq!(
            component_symbol_id("service", None, "m.xml", 1),
            "component::service:unknown@m.xml:1"
        );
        assert_eq!(resource_symbol_id("layout", "main"), "resource::layout/main");
        assert_eq!(nav_route_symbol_id("home"), "nav_route::home");
    }

    #[test]
    fn resolve_class_name_matches_python() {
        assert_eq!(
            resolve_android_class_name(Some(".ui.Detail"), Some("com.demo")),
            Some("com.demo.ui.Detail".to_string())
        );
        assert_eq!(
            resolve_android_class_name(Some("Detail"), Some("com.demo")),
            Some("com.demo.Detail".to_string())
        );
        assert_eq!(
            resolve_android_class_name(Some("com.other.Detail"), None),
            Some("com.other.Detail".to_string())
        );
        assert_eq!(resolve_android_class_name(Some(""), None), None);
        assert_eq!(resolve_android_class_name(None, Some("x")), None);
    }

    #[test]
    fn normalize_rel_path_matches_python() {
        assert_eq!(normalize_rel_path("a\\b/c/"), "a/b/c");
        assert_eq!(normalize_rel_path("./a/b"), "a/b");
        assert_eq!(normalize_rel_path(""), "");
    }

    #[test]
    fn parse_bool_variants() {
        assert_eq!(parse_bool(Some("True")), Some(true));
        assert_eq!(parse_bool(Some("0")), Some(false));
        assert_eq!(parse_bool(Some("maybe")), None);
        assert_eq!(parse_bool(None), None);
    }
}
