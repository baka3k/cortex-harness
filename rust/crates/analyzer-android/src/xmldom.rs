//! XML parser dựng `common::XmlElement` từ source text — tương đương mặt parse
//! mà `_parse_android_manifest` cần từ `xml.etree.ElementTree`:
//! * tag/attr namespace resolve (`android:name` → `{uri}name`);
//! * attr theo thứ tự văn bản, giá trị đã unescape;
//! * `text`/`tail` nguyên bản (đã unescape) cho serializer ET-compatible;
//! * `sourceline` 1-based.
//!
//! Parse lỗi → None (khớp nhánh `except ET.ParseError`).

use crate::common::XmlElement;

/// Parse một document XML; trả root element hoặc None khi lỗi.
pub fn parse_document(content: &str) -> Option<XmlElement> {
    let mut reader = quick_xml::Reader::from_str(content);

    // Stack các element đang mở. Node đang mở đón text vào `text` hoặc tail
    // của con cuối (ET semantics).
    struct Open {
        element: XmlElement,
        has_child: bool,
    }
    let mut stack: Vec<Open> = Vec::new();
    let mut root: Option<XmlElement> = None;
    // (prefix → uri) theo scope; mỗi level giữ các decl riêng.
    let mut ns_scopes: Vec<Vec<(String, String)>> = vec![Vec::new()];

    macro_rules! bail_root {
        ($node:expr) => {{
            if root.is_none() && stack.is_empty() {
                root = Some($node);
            }
        }};
    }

    let mut loop_guard: usize = 0;
    let reader_iter = &mut reader;
    loop {
        loop_guard += 1;
        if loop_guard > content.len() + 16 {
            return None;
        }
        let event = match reader_iter.read_event() {
            Ok(event) => event,
            Err(_) => return None,
        };
        match event {
            quick_xml::events::Event::Start(start) => {
                let parent_scope = ns_scopes.last().cloned().unwrap_or_default();
                let scope = merge_decls(&start, &parent_scope);
                let (tag, attrib) = resolve_start(&start, &scope)?;
                ns_scopes.push(scope);
                stack.push(Open {
                    element: XmlElement {
                        tag,
                        attrib,
                        text: String::new(),
                        tail: String::new(),
                        children: Vec::new(),
                        sourceline: 0,
                    },
                    has_child: false,
                });
            }
            quick_xml::events::Event::End(_) => {
                let open = stack.pop()?;
                ns_scopes.pop();
                if let Some(parent) = stack.last_mut() {
                    parent.element.children.push(open.element);
                    parent.has_child = true;
                } else {
                    bail_root!(open.element);
                    break;
                }
            }
            quick_xml::events::Event::Empty(start) => {
                // ET: Empty ≡ Start+End ngay lập tức.
                let parent_scope = ns_scopes.last().cloned().unwrap_or_default();
                let scope = merge_decls(&start, &parent_scope);
                let (tag, attrib) = resolve_start(&start, &scope)?;
                let element = XmlElement {
                    tag,
                    attrib,
                    text: String::new(),
                    tail: String::new(),
                    children: Vec::new(),
                    sourceline: 0,
                };
                if let Some(parent) = stack.last_mut() {
                    parent.element.children.push(element);
                    parent.has_child = true;
                } else {
                    bail_root!(element);
                    break;
                }
            }
            quick_xml::events::Event::Text(text) => {
                let decoded = text.unescape().ok()?.to_string();
                if let Some(open) = stack.last_mut() {
                    if open.has_child {
                        open.element.children.last_mut()?.tail += &decoded;
                    } else {
                        open.element.text += &decoded;
                    }
                }
            }
            quick_xml::events::Event::CData(text) => {
                let raw = String::from_utf8_lossy(text.as_ref()).to_string();
                if let Some(open) = stack.last_mut() {
                    if open.has_child {
                        open.element.children.last_mut()?.tail += &raw;
                    } else {
                        open.element.text += &raw;
                    }
                }
            }
            quick_xml::events::Event::Eof => break,
            _ => {}
        }
    }

    root
}

/// Resolve Start/Empty event thành (tag đã resolve, attrs đã resolve theo thứ tự).
fn resolve_start(
    start: &quick_xml::events::BytesStart<'_>,
    scope: &[(String, String)],
) -> Option<(String, Vec<(String, String)>)> {
    let raw_name = start.name();
    let raw_name = std::str::from_utf8(raw_name.as_ref()).ok()?;
    let tag = resolve_name(raw_name, scope, true)?;
    let mut attrib: Vec<(String, String)> = Vec::new();
    for attr in start.attributes() {
        let attr = attr.ok()?;
        let raw_key = std::str::from_utf8(attr.key.as_ref()).ok()?;
        if raw_key == "xmlns" || raw_key.starts_with("xmlns:") {
            // xmlns decl không nằm trong .attrib của ET.
            continue;
        }
        let key = resolve_name(raw_key, scope, false)?;
        let value = attr.unescape_value().ok()?.to_string();
        attrib.push((key, value));
    }
    Some((tag, attrib))
}

/// Scope mới = parent + xmlns decl khai báo trên chính start tag này
/// (declaration áp cho element và hậu duệ — XML/ET semantics).
fn merge_decls(
    start: &quick_xml::events::BytesStart<'_>,
    parent: &[(String, String)],
) -> Vec<(String, String)> {
    let mut merged: Vec<(String, String)> = parent.to_vec();
    for attr in start.attributes().flatten() {
        let Ok(raw_key) = std::str::from_utf8(attr.key.as_ref()) else {
            continue;
        };
        if raw_key == "xmlns" {
            merged.push((String::new(), attr_decoded(&attr)));
        } else if let Some(prefix) = raw_key.strip_prefix("xmlns:") {
            merged.push((prefix.to_string(), attr_decoded(&attr)));
        }
    }
    merged
}

fn attr_decoded(attr: &quick_xml::events::attributes::Attribute<'_>) -> String {
    attr.unescape_value().map(|c| c.to_string()).unwrap_or_default()
}

/// `android:name` → `{uri}name`; tag trần với default ns → `{uri}tag`.
fn resolve_name(
    raw: &str,
    scope: &[(String, String)],
    is_tag: bool,
) -> Option<String> {
    if let Some((prefix, local)) = raw.split_once(':') {
        let uri = scope
            .iter()
            .rev()
            .find(|(declared, _)| declared == prefix)
            .map(|(_, resolved)| resolved.clone())?;
        Some(format!("{{{uri}}}{local}"))
    } else if is_tag {
        // Default namespace áp cho tag trần (ET semantics); không có → tên trần.
        match scope.iter().rev().find(|(declared, _)| declared.is_empty()) {
            Some((_, uri)) if !uri.is_empty() => Some(format!("{{{uri}}}{raw}")),
            _ => Some(raw.to_string()),
        }
    } else {
        Some(raw.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolves_android_namespaced_attributes() {
        let doc = parse_document(
            r#"<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.p">
    <application>
        <activity android:name=".Main" android:exported="true" />
    </application>
</manifest>
"#,
        )
        .expect("parse");
        assert_eq!(doc.get("package"), Some("com.p"));
        let application = doc.children.first().expect("application");
        assert_eq!(application.tag, "application");
        let activity = application.children.first().expect("activity");
        assert_eq!(activity.tag, "activity");
        assert_eq!(
            activity.android_attr("name"),
            Some(".Main")
        );
        assert_eq!(activity.android_attr("exported"), Some("true"));
        // ET của Python 3.12 không set sourceline ⇒ DOM lưu 0.
        assert_eq!(activity.sourceline, 0);
    }

    #[test]
    fn serialization_matches_et_tostring() {
        let doc = parse_document(
            "<manifest xmlns:android=\"http://schemas.android.com/apk/res/android\">\n    <receiver android:name=\".R\" android:enabled=\"true\">\n        <intent-filter>\n            <action android:name=\"A.B\" />\n        </intent-filter>\n    </receiver>\n</manifest>\n",
        )
        .expect("parse");
        let receiver = &doc.children[0];
        let code = receiver.et_tostring();
        // ET.tostring(root) include tail của root ("\n" trước </manifest>).
        assert_eq!(
            code,
            "<receiver xmlns:ns0=\"http://schemas.android.com/apk/res/android\" ns0:name=\".R\" ns0:enabled=\"true\">\n        <intent-filter>\n            <action ns0:name=\"A.B\" />\n        </intent-filter>\n    </receiver>\n"
        );
    }

    #[test]
    fn parse_error_returns_none() {
        assert!(parse_document("<manifest><unclosed></manifest>").is_none());
    }
}
