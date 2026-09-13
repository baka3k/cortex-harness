//! Mini XML DOM cho struts — tương thích phần `xml.etree.ElementTree` mà
//! `tools/struts/xml_utils.py` dùng: element tree với tag/attributes/
//! children + `itertext()`. Tag được convert sang Clark notation `{uri}local`
//! (như ET), attribute giữ tên raw (như ET). DOCTYPE/comment/PI được bỏ qua;
//! entity predefined được decode; CDATA giữ text.
//!
//! Lỗi parse trả message khác `ET.ParseError` (chấp nhận — corpus parity
//! dùng XML hợp lệ; message chỉ xuất hiện trong diagnostics khi malformed).

#[derive(Debug, Clone, Default)]
pub struct XmlElement {
    pub tag: String,
    pub attributes: Vec<(String, String)>,
    pub children: Vec<XmlNode>,
}

#[derive(Debug, Clone)]
pub enum XmlNode {
    Element(XmlElement),
    Text(String),
}

impl XmlElement {
    pub fn attr(&self, name: &str) -> Option<&str> {
        self.attributes
            .iter()
            .find(|(key, _)| key == name)
            .map(|(_, value)| value.as_str())
    }

    /// `"".join(element.itertext()).strip()` — text của mọi node con theo
    /// document order.
    pub fn itertext(&self) -> String {
        let mut out = String::new();
        collect_text(self, &mut out);
        out
    }

    /// `for child in element` — direct children elements (ET iteration bỏ
    /// text node).
    pub fn elements(&self) -> impl Iterator<Item = &XmlElement> {
        self.children.iter().filter_map(|node| match node {
            XmlNode::Element(element) => Some(element),
            XmlNode::Text(_) => None,
        })
    }
}

fn collect_text(element: &XmlElement, out: &mut String) {
    for child in &element.children {
        match child {
            XmlNode::Text(text) => out.push_str(text),
            XmlNode::Element(inner) => collect_text(inner, out),
        }
    }
}

pub fn parse(source: &[u8]) -> Result<XmlElement, String> {
    let text = String::from_utf8_lossy(source).to_string();
    let bytes = text.as_bytes();
    let mut pos = 0usize;
    let mut stack: Vec<XmlElement> = Vec::new();
    let mut ns_stack: Vec<Vec<(String, String)>> = Vec::new(); // (prefix|"" , uri)
    let mut root: Option<XmlElement> = None;
    while pos < bytes.len() {
        if bytes[pos] == b'<' {
            if text[pos..].starts_with("<!--") {
                let Some(close) = text[pos..].find("-->") else {
                    return Err("unclosed comment".to_string());
                };
                pos += close + 3;
            } else if text[pos..].starts_with("<![CDATA[") {
                let close = text[pos..].find("]]>").ok_or("unclosed CDATA")?;
                let content = text[pos + 9..pos + close].to_string();
                if let Some(top) = stack.last_mut() {
                    top.children.push(XmlNode::Text(content));
                }
                pos += close + 3;
            } else if text[pos..].starts_with("<?") {
                let Some(close) = text[pos..].find("?>") else {
                    return Err("unclosed processing instruction".to_string());
                };
                pos += close + 2;
            } else if text[pos..].starts_with("<!") {
                // DOCTYPE (có thể có internal subset [...]) — skip theo bracket.
                let mut index = pos + 2;
                let mut depth = 0i32;
                while index < bytes.len() {
                    match bytes[index] {
                        b'[' => depth += 1,
                        b']' => depth -= 1,
                        b'>' if depth <= 0 => break,
                        _ => {}
                    }
                    index += 1;
                }
                if index >= bytes.len() {
                    return Err("unclosed doctype".to_string());
                }
                pos = index + 1;
            } else if text[pos..].starts_with("</") {
                let Some(close) = text[pos..].find('>') else {
                    return Err("unclosed end tag".to_string());
                };
                let _name = text[pos + 2..pos + close].trim().to_string();
                let element = stack.pop().ok_or("unbalanced end tag")?;
                ns_stack.pop();
                if let Some(parent) = stack.last_mut() {
                    parent.children.push(XmlNode::Element(element));
                } else {
                    root = Some(element);
                }
                pos += close + 1;
            } else {
                // STag / EmptyElemTag
                let Some(close) = text[pos..].find('>') else {
                    return Err("unclosed start tag".to_string());
                };
                let self_closing = close > 0 && text.as_bytes()[pos + close - 1] == b'/';
                let inner = &text[pos + 1..pos + close - if self_closing { 1 } else { 0 }];
                let (tag, attributes) = parse_tag_head(inner)?;
                let mut ns_decls: Vec<(String, String)> = Vec::new();
                for (name, value) in &attributes {
                    if name == "xmlns" {
                        ns_decls.push((String::new(), value.clone()));
                    } else if let Some(prefix) = name.strip_prefix("xmlns:") {
                        ns_decls.push((prefix.to_string(), value.clone()));
                    }
                }
                // Clark notation như ET.
                let clark = resolve_clark(&tag, &ns_stack, &ns_decls);
                let element = XmlElement {
                    tag: clark,
                    attributes,
                    children: Vec::new(),
                };
                ns_stack.push(ns_decls);
                if self_closing {
                    ns_stack.pop();
                    if let Some(parent) = stack.last_mut() {
                        parent.children.push(XmlNode::Element(element));
                    } else if stack.is_empty() {
                        root = Some(element);
                    }
                } else {
                    stack.push(element);
                }
                pos += close + 1;
            }
        } else {
            match text[pos..].find('<') {
                Some(next) => {
                    let raw = &text[pos..pos + next];
                    if let Some(top) = stack.last_mut() {
                        top.children.push(XmlNode::Text(decode_entities(raw)));
                    }
                    pos += next;
                }
                None => break,
            }
        }
    }
    match root {
        Some(root) if stack.is_empty() => Ok(root),
        Some(_) => Err("no element found: unclosed tags".to_string()),
        None => Err("no element found: line 1, column 0".to_string()),
    }
}

fn split_prefix(name: &str) -> (String, String) {
    match name.split_once(':') {
        Some((prefix, local)) => (prefix.to_string(), local.to_string()),
        None => (String::new(), name.to_string()),
    }
}

fn resolve_clark(tag: &str, ns_stack: &[Vec<(String, String)>], local_decls: &[(String, String)]) -> String {
    let (prefix, local) = split_prefix(tag);
    if prefix.is_empty() {
        // default xmlns nếu có
        for scope in ns_stack.iter().rev() {
            if let Some((_, uri)) = scope.iter().find(|(key, _)| key.is_empty())
                && !uri.is_empty()
            {
                return format!("{{{uri}}}{local}");
            }
        }
        if let Some((_, uri)) = local_decls.iter().find(|(key, _)| key.is_empty())
            && !uri.is_empty()
        {
            return format!("{{{uri}}}{local}");
        }
        return local;
    }
    for scope in ns_stack.iter().rev() {
        if let Some((_, uri)) = scope.iter().find(|(key, _)| key == &prefix) {
            return format!("{{{uri}}}{local}");
        }
    }
    if let Some((_, uri)) = local_decls.iter().find(|(key, _)| key == &prefix) {
        return format!("{{{uri}}}{local}");
    }
    tag.to_string()
}

fn parse_tag_head(inner: &str) -> Result<(String, Vec<(String, String)>), String> {
    let bytes = inner.as_bytes();
    let mut index = 0usize;
    while index < bytes.len() && !bytes[index].is_ascii_whitespace() {
        index += 1;
    }
    let tag = inner[..index].to_string();
    let mut attributes: Vec<(String, String)> = Vec::new();
    while index < bytes.len() {
        while index < bytes.len() && bytes[index].is_ascii_whitespace() {
            index += 1;
        }
        if index >= bytes.len() {
            break;
        }
        let name_start = index;
        while index < bytes.len() && bytes[index] != b'=' && !bytes[index].is_ascii_whitespace() {
            index += 1;
        }
        let name = inner[name_start..index].to_string();
        while index < bytes.len() && bytes[index].is_ascii_whitespace() {
            index += 1;
        }
        if index < bytes.len() && bytes[index] == b'=' {
            index += 1;
            while index < bytes.len() && bytes[index].is_ascii_whitespace() {
                index += 1;
            }
            if index < bytes.len() && (bytes[index] == b'"' || bytes[index] == b'\'') {
                let quote = bytes[index];
                index += 1;
                let value_start = index;
                while index < bytes.len() && bytes[index] != quote {
                    index += 1;
                }
                let value = decode_entities(&inner[value_start..index]);
                attributes.push((name, value));
                index += 1;
            } else {
                let value_start = index;
                while index < bytes.len() && !bytes[index].is_ascii_whitespace() {
                    index += 1;
                }
                attributes.push((name, decode_entities(&inner[value_start..index])));
            }
        } else if !name.is_empty() {
            // attribute không giá trị — ET chấp nhận dạng này như "".
            attributes.push((name, String::new()));
        }
    }
    Ok((tag, attributes))
}

fn decode_entities(text: &str) -> String {
    if !text.contains('&') {
        return text.to_string();
    }
    let mut out = String::new();
    let mut rest = text;
    while let Some(index) = rest.find('&') {
        out.push_str(&rest[..index]);
        let tail = &rest[index..];
        let Some(close) = tail.find(';') else {
            out.push('&');
            rest = &rest[index + 1..];
            continue;
        };
        let entity = &tail[1..close];
        match entity {
            "amp" => out.push('&'),
            "lt" => out.push('<'),
            "gt" => out.push('>'),
            "apos" => out.push('\''),
            "quot" => out.push('"'),
            hex if hex.starts_with("#x") || hex.starts_with("#X") => {
                if let Ok(code) = u32::from_str_radix(&hex[2..], 16)
                    && let Some(ch) = char::from_u32(code)
                {
                    out.push(ch);
                }
            }
            dec if dec.starts_with('#') => {
                if let Ok(code) = dec[1..].parse::<u32>()
                    && let Some(ch) = char::from_u32(code)
                {
                    out.push(ch);
                }
            }
            _ => {
                // ET raise undefined entity — ở đây giữ nguyên để không crash;
                // corpus parity không dùng custom entity.
                out.push_str(tail[..=close].trim_end());
                rest = &rest[index + close + 1..];
                continue;
            }
        }
        rest = &rest[index + close + 1..];
    }
    out.push_str(rest);
    out
}
