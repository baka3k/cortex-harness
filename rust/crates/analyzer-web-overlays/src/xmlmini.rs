//! XML mini-parser mô phỏng phần `xml.etree.ElementTree` mà overlay ASP.NET
//! dùng (`parse_xml_file`): cây Element với attribute GIỮ THỨ TỰ TÀI LIỆU,
//! `text` = text TRƯỚC element con đầu tiên, `local_name` tách namespace/prefix.
//! Comments/PIs bị bỏ như ET default parser; undefined entity → ParseError.

use std::collections::BTreeMap;

use quick_xml::events::Event;
use quick_xml::Reader;

#[derive(Debug, Clone)]
pub struct Element {
    /// Tên tag nguyên bản (có thể chứa prefix `ns:tag`).
    pub tag: String,
    /// Attribute theo thứ tự tài liệu.
    pub attributes: Vec<(String, String)>,
    /// Text đứng trước element con đầu tiên (≈ `element.text` của ET).
    pub text: String,
    pub children: Vec<Element>,
}

impl Element {
    pub fn attrib(&self, name: &str) -> Option<&str> {
        self.attributes
            .iter()
            .find(|(key, _)| key == name)
            .map(|(_, value)| value.as_str())
    }

    /// `" ".join(str(v) for v in element.attrib.values())` — thứ tự tài liệu.
    pub fn attrib_values_joined(&self) -> String {
        self.attributes
            .iter()
            .map(|(_, value)| value.as_str())
            .collect::<Vec<_>>()
            .join(" ")
    }

    /// `local_name(tag)` — tách `}` (namespace) rồi `:` (prefix).
    pub fn local_name(&self) -> String {
        local_name(&self.tag)
    }
}

/// `tools.common.aspnet.safe_formats.local_name`.
pub fn local_name(tag: &str) -> String {
    let without_namespace = match tag.split_once('}') {
        Some((_, rest)) => rest,
        None => tag,
    };
    match without_namespace.split_once(':') {
        Some((_, rest)) => rest.to_string(),
        None => without_namespace.to_string(),
    }
}

/// Parse XML thành cây `Element`. Lỗi → message dạng `ParseError: ...` (chỉ
/// loại lỗi này xuất hiện trong diagnostics của fixture parity).
pub fn parse_xml(text: &str) -> Result<Element, String> {
    let mut parser = Reader::from_str(text);
    parser.config_mut().trim_text(false);
    let mut stack: Vec<Element> = Vec::new();
    let mut root: Option<Element> = None;
    loop {
        let event = match parser.read_event() {
            Ok(event) => event,
            Err(error) => return Err(format!("ParseError: {error}")),
        };
        match event {
            Event::Start(start) => {
                let tag = match String::from_utf8(start.name().as_ref().to_vec()) {
                    Ok(tag) => tag,
                    Err(_) => return Err("ParseError: invalid tag encoding".into()),
                };
                let mut attributes = Vec::new();
                for attribute in start.attributes() {
                    let attribute = match attribute {
                        Ok(attribute) => attribute,
                        Err(error) => return Err(format!("ParseError: {error}")),
                    };
                    let key = match String::from_utf8(attribute.key.as_ref().to_vec()) {
                        Ok(key) => key,
                        Err(_) => return Err("ParseError: invalid attribute name".into()),
                    };
                    if attributes.iter().any(|(existing, _)| *existing == key) {
                        return Err(format!("ParseError: duplicate attribute {key}"));
                    }
                    let value = match attribute.unescape_value() {
                        Ok(value) => value.into_owned(),
                        Err(error) => return Err(format!("ParseError: {error}")),
                    };
                    attributes.push((key, value));
                }
                stack.push(Element {
                    tag,
                    attributes,
                    text: String::new(),
                    children: Vec::new(),
                });
            }
            Event::Empty(start) => {
                // `<tag/>` — element không có con; xử lý như Start+End.
                let tag = match String::from_utf8(start.name().as_ref().to_vec()) {
                    Ok(tag) => tag,
                    Err(_) => return Err("ParseError: invalid tag encoding".into()),
                };
                let mut attributes = Vec::new();
                for attribute in start.attributes() {
                    let attribute = match attribute {
                        Ok(attribute) => attribute,
                        Err(error) => return Err(format!("ParseError: {error}")),
                    };
                    let key = match String::from_utf8(attribute.key.as_ref().to_vec()) {
                        Ok(key) => key,
                        Err(_) => return Err("ParseError: invalid attribute name".into()),
                    };
                    if attributes.iter().any(|(existing, _)| *existing == key) {
                        return Err(format!("ParseError: duplicate attribute {key}"));
                    }
                    let value = match attribute.unescape_value() {
                        Ok(value) => value.into_owned(),
                        Err(error) => return Err(format!("ParseError: {error}")),
                    };
                    attributes.push((key, value));
                }
                let element = Element {
                    tag,
                    attributes,
                    text: String::new(),
                    children: Vec::new(),
                };
                append_element(&mut stack, &mut root, element)?;
            }
            Event::Text(text) => {
                let raw = match text.unescape() {
                    Ok(raw) => raw.into_owned(),
                    Err(error) => return Err(format!("ParseError: {error}")),
                };
                if let Some(bad) = undefined_entity(&raw) {
                    return Err(format!("ParseError: undefined entity {bad};"));
                }
                push_text(&mut stack, raw);
            }
            Event::CData(text) => {
                let raw = String::from_utf8_lossy(text.as_ref()).into_owned();
                push_text(&mut stack, raw);
            }
            Event::End(_) => {
                let element = match stack.pop() {
                    Some(element) => element,
                    None => return Err("ParseError: unbalanced end tag".into()),
                };
                append_element(&mut stack, &mut root, element)?;
            }
            Event::Eof => break,
            // Comment / PI / DocType / Decl — ET default parser bỏ comment+PI
            // +declaration. quick-xml 0.37 unescape sẵn entity chuẩn trong
            // Text/attribute; entity KHÔNG định nghĩa nằm lại trong Text như
            // literal (`&nope;`) — hàm `undefined_entity` báo lỗi như ET
            // ParseError.
            Event::Comment(_) | Event::PI(_) | Event::DocType(_) | Event::Decl(_) => {}
        }
    }
    match root {
        Some(root) if stack.is_empty() => Ok(root),
        _ => Err("ParseError: no element found".into()),
    }
}

/// Entity không định nghĩa còn sót literal `&name;` sau unescape của
/// quick-xml → ET báo ParseError.
fn undefined_entity(raw: &str) -> Option<String> {
    let mut rest = raw;
    while let Some(start) = rest.find('&') {
        let tail = &rest[start + 1..];
        if let Some(end) = tail.find(';') {
            let name = &tail[..end];
            let hex_value = |text: &str| u32::from_str_radix(text, 16);
            let numeric = name.len() >= 2
                && name.starts_with('#')
                && (name[1..].parse::<u32>().is_ok()
                    || name[1..]
                        .strip_prefix(['x', 'X'])
                        .map(hex_value)
                        .transpose()
                        .is_ok());
            let known = matches!(name, "amp" | "lt" | "gt" | "quot" | "apos") || numeric;
            if !known {
                return Some(format!("&{name};"));
            }
            rest = &tail[end + 1..];
        } else {
            rest = tail;
        }
    }
    None
}

fn push_text(stack: &mut [Element], text: String) {
    if let Some(current) = stack.last_mut() {
        current.text.push_str(&text);
    }
}

fn append_element(
    stack: &mut [Element],
    root: &mut Option<Element>,
    element: Element,
) -> Result<(), String> {
    if let Some(parent) = stack.last_mut() {
        parent.children.push(element);
        Ok(())
    } else if root.is_none() {
        *root = Some(element);
        Ok(())
    } else {
        Err("ParseError: multiple elements on top level".into())
    }
}

/// Duyệt cả cây theo thứ tự tài liệu (≈ `tree.iter()`).
pub fn iter_elements(element: &Element) -> Vec<&Element> {
    let mut out = vec![element];
    let mut index = 0;
    while index < out.len() {
        let children = out[index].children.iter().collect::<Vec<_>>();
        out.extend(children);
        index += 1;
    }
    out
}

/// Thuộc tính của toàn cây dưới dạng map (dùng trong detect csproj) — chỉ để
/// kiểm tra chuỗi con, không cần thứ tự.
pub fn all_text_and_attributes(element: &Element) -> String {
    let mut parts = Vec::new();
    for current in iter_elements(element) {
        if !current.text.is_empty() {
            parts.push(current.text.clone());
        }
        for (_, value) in &current.attributes {
            parts.push(value.clone());
        }
    }
    parts.join(" ")
}

/// Map attribute name → value cho lookup đã sort (dùng khi port cần BTreeMap).
pub fn attrib_map(element: &Element) -> BTreeMap<String, String> {
    element.attributes.iter().cloned().collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_attributes_in_document_order() {
        let tree = parse_xml(r#"<Project Sdk="B" Other="A"><PropertyGroup><T>v1</T></PropertyGroup></Project>"#).unwrap();
        assert_eq!(tree.tag, "Project");
        let values = tree.attrib_values_joined();
        assert_eq!(values, "B A");
        assert_eq!(tree.attrib("Sdk"), Some("B"));
        assert_eq!(tree.children.len(), 1);
        let text = all_text_and_attributes(&tree);
        assert!(text.contains("v1"));
    }

    #[test]
    fn local_name_matches_python() {
        assert_eq!(local_name("{http://schemas}data"), "data");
        assert_eq!(local_name("asp:Label"), "Label");
        assert_eq!(local_name("add"), "add");
    }

    #[test]
    fn undefined_entity_is_error() {
        assert!(parse_xml("<a>&nope;</a>").is_err());
        assert!(parse_xml("<a>&amp;</a>").is_ok());
    }
}
