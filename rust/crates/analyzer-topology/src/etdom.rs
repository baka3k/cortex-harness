//! Mini XML DOM tương đương mặt `xml.etree.ElementTree` mà các descriptor
//! parser dùng: tag namespace-expanded (`android:name` → `{uri}name`),
//! attrib theo thứ tự văn bản, `text` đã unescape, children theo thứ tự.
//!
//! Parse lỗi → `None` + `(line, column)` kiểu expat cho diagnostic byte-parity
//! ("mismatched tag: line L, column C" / "not well-formed (invalid token): ...").

#[derive(Debug, Clone)]
pub struct EtNode {
    pub tag: String,
    pub attrib: Vec<(String, String)>,
    pub text: String,
    pub children: Vec<EtNode>,
}

impl EtNode {
    /// `element.attrib.get(name, default)`.
    pub fn attr(&self, name: &str) -> Option<&str> {
        self.attrib
            .iter()
            .find(|(key, _)| key == name)
            .map(|(_, value)| value.as_str())
    }

    /// `element.find(name)` — direct child đầu tiên với tag khớp.
    pub fn find(&self, tag: &str) -> Option<&EtNode> {
        self.children.iter().find(|child| child.tag == tag)
    }

    /// `element.findall(name)` — mọi direct child với tag khớp.
    pub fn find_all(&self, tag: &str) -> Vec<&EtNode> {
        self.children
            .iter()
            .filter(|child| child.tag == tag)
            .collect()
    }

    /// `element.iter()` — depth-first pre-order, gồm self (ET semantics).
    pub fn iter_nodes(&self) -> Vec<&EtNode> {
        fn walk<'a>(node: &'a EtNode, out: &mut Vec<&'a EtNode>) {
            out.push(node);
            for child in &node.children {
                walk(child, out);
            }
        }
        let mut out = Vec::new();
        walk(self, &mut out);
        out
    }
}

/// `xml.etree.ElementTree.fromstring` lỗi — message expat-compatible.
#[derive(Debug)]
pub struct ParseError {
    pub message: String,
}

pub fn parse_document(text: &str) -> Result<EtNode, ParseError> {
    let mut parser = DomParser::new(text);
    parser.run()
}

struct Scope {
    decls: Vec<(String, String)>,
}

struct Open {
    node: EtNode,
    has_child: bool,
}

struct DomParser<'a> {
    input: &'a str,
    byte_offset: usize,
    line: usize,
    line_start: usize,
    stack: Vec<Open>,
    scopes: Vec<Scope>,
    root: Option<EtNode>,
    root_closed: bool,
    pending_text: String,
}

impl<'a> DomParser<'a> {
    fn new(input: &'a str) -> Self {
        DomParser {
            input,
            byte_offset: 0,
            line: 1,
            line_start: 0,
            stack: Vec::new(),
            scopes: vec![Scope { decls: Vec::new() }],
            root: None,
            root_closed: false,
            pending_text: String::new(),
        }
    }

    fn error(&self, kind: &str) -> ParseError {
        ParseError {
            message: format!("{kind}: line {}, column {}", self.line, self.col()),
        }
    }

    fn col(&self) -> usize {
        self.byte_offset - self.line_start + 1
    }


    fn advance(&mut self, count: usize) {
        for _ in 0..count {
            let Some(next) = self.input.as_bytes().get(self.byte_offset).copied() else {
                self.byte_offset += 1;
                continue;
            };
            self.byte_offset += 1;
            if next == b'\n' {
                self.line += 1;
                self.line_start = self.byte_offset;
            }
        }
    }

    fn lookup_prefix(&self, prefix: &str) -> Option<String> {
        // scope chain: innermost ra ngoài (XML namespace semantics).
        for scope in self.scopes.iter().rev() {
            if let Some((_, uri)) = scope.decls.iter().find(|(p, _)| p == prefix) {
                return Some(uri.clone());
            }
        }
        None
    }

    fn expand_tag(&self, raw: &str) -> Result<String, ParseError> {
        match raw.split_once(':') {
            Some((prefix, local)) => {
                if prefix == "xmlns" {
                    return Ok(raw.to_string());
                }
                match self.lookup_prefix(prefix) {
                    Some(uri) => Ok(format!("{{{uri}}}{local}")),
                    None => Err(self.error("unbound prefix")),
                }
            }
            None => {
                // default namespace áp cho element tag.
                match self.lookup_prefix("") {
                    Some(uri) => Ok(format!("{{{uri}}}{raw}")),
                    None => Ok(raw.to_string()),
                }
            }
        }
    }

    fn expand_attr_key(&self, raw: &str) -> String {
        match raw.split_once(':') {
            Some((prefix, local)) => {
                if prefix == "xmlns" || prefix == "xml" {
                    return raw.to_string();
                }
                match self.lookup_prefix(prefix) {
                    Some(uri) => format!("{{{uri}}}{local}"),
                    None => raw.to_string(),
                }
            }
            None => raw.to_string(),
        }
    }

    fn flush_text(&mut self) {
        if let Some(open) = self.stack.last_mut() {
            if open.has_child {
                // tail không cần cho các parser hiện có — gộp vào text cuối.
            } else {
                open.node.text.push_str(&self.pending_text);
            }
        }
        self.pending_text.clear();
    }

    fn push_decls(&mut self, attrib: &[(String, String)]) {
        let mut decls: Vec<(String, String)> = Vec::new();
        for (key, value) in attrib {
            if let Some(prefix) = key.strip_prefix("xmlns:") {
                decls.push((prefix.to_string(), value.clone()));
            } else if key == "xmlns" {
                decls.push((String::new(), value.clone()));
            }
        }
        self.scopes.push(Scope { decls });
    }

    fn run(&mut self) -> Result<EtNode, ParseError> {
        let input = self.input;
        let bytes = input.as_bytes();
        while self.byte_offset < bytes.len() {
            if bytes[self.byte_offset] != b'<' {
                // text run
                let start = self.byte_offset;
                while self.byte_offset < bytes.len() && bytes[self.byte_offset] != b'<' {
                    self.advance(1);
                }
                let raw = &input[start..self.byte_offset];
                let decoded = unescape(raw).ok_or_else(|| self.error("not well-formed (invalid token)"))?;
                if self.stack.is_empty() && !decoded.trim().is_empty() {
                    // text ngoài root — expat: syntax error (hoặc junk khi đã
                    // có root; nhánh dưới xử lý trailing).
                    if self.root_closed {
                        return Err(self.error("junk after document element"));
                    }
                    return Err(self.error("syntax error"));
                }
                self.pending_text.push_str(&decoded);
                continue;
            }
            // byte_offset tại '<'
            let rest = &input[self.byte_offset..];
            if rest.starts_with("<!--") {
                self.flush_text();
                match rest.find("-->") {
                    Some(end) => self.advance(end + 3),
                    None => return Err(self.error("not well-formed (invalid token)")),
                }
                continue;
            }
            if rest.starts_with("<![CDATA[") {
                match rest.find("]]>") {
                    Some(end) => {
                        let raw = &rest[9..end];
                        self.pending_text.push_str(raw);
                        self.advance(end + 3);
                    }
                    None => return Err(self.error("not well-formed (invalid token)")),
                }
                continue;
            }
            if rest.starts_with("<?") {
                self.flush_text();
                match rest.find("?>") {
                    Some(end) => self.advance(end + 2),
                    None => return Err(self.error("not well-formed (invalid token)")),
                }
                continue;
            }
            if rest.starts_with("<!") {
                // DOCTYPE (các parser đã chặn DOCTYPE trước khi parse).
                self.flush_text();
                let mut depth = 0usize;
                let start = self.byte_offset;
                while self.byte_offset < bytes.len() {
                    let byte = bytes[self.byte_offset];
                    if byte == b'<' {
                        depth += 1;
                    } else if byte == b'>' {
                        depth = depth.saturating_sub(1);
                        if depth == 0 {
                            self.advance(1);
                            break;
                        }
                    }
                    self.advance(1);
                }
                if depth != 0 && self.byte_offset >= bytes.len() {
                    let _ = start;
                    return Err(self.error("not well-formed (invalid token)"));
                }
                continue;
            }
            if rest.starts_with("</") {
                self.flush_text();
                let Some(gt) = rest.find('>') else {
                    return Err(self.error("not well-formed (invalid token)"));
                };
                let raw_name = rest[2..gt].trim();
                let name = raw_name.to_string();
                // expat báo lỗi tại byte '/' của `</name>` (column 1-based).
                let name_offset = self.byte_offset + 1;
                let Some(open) = self.stack.pop() else {
                    self.advance(gt + 1);
                    return Err(ParseError {
                        message: format!(
                            "mismatched tag: line {}, column {}",
                            self.line_of(name_offset),
                            name_offset - self.line_start_of(name_offset) + 1
                        ),
                    });
                };
                self.scopes.pop();
                let expected = open.node.tag.rsplit('}').next().unwrap_or(&open.node.tag);
                if expected != name {
                    return Err(ParseError {
                        message: format!(
                            "mismatched tag: line {}, column {}",
                            self.line_of(name_offset),
                            name_offset - self.line_start_of(name_offset) + 1
                        ),
                    });
                }
                if let Some(parent) = self.stack.last_mut() {
                    parent.node.children.push(open.node);
                    parent.has_child = true;
                    self.advance(gt + 1);
                } else {
                    self.root = Some(open.node);
                    self.root_closed = true;
                    self.advance(gt + 1);
                    break;
                }
                continue;
            }
            // Start hoặc Empty element
            if self.root_closed {
                return Err(self.error("junk after document element"));
            }
            let Some(gt) = find_tag_end(input, self.byte_offset) else {
                return Err(self.error("not well-formed (invalid token)"));
            };
            let inner = &input[self.byte_offset + 1..gt];
            let empty = inner.trim_end().ends_with('/');
            let inner_trimmed = inner.trim_end().trim_end_matches('/').trim_end();
            let (raw_name, attrib_raw) = split_tag(inner_trimmed)
                .ok_or_else(|| self.error("not well-formed (invalid token)"))?;
            self.flush_text();
            // ET/expat: xmlns decl của chính element áp cho TAG và ATTRS của
            // chính nó → parse raw trước, push scope, rồi expand.
            let raw_attrib = self.parse_attribs_raw(attrib_raw)?;
            self.push_decls(&raw_attrib);
            let tag = self.expand_tag(raw_name)?;
            let mut attrib: Vec<(String, String)> = Vec::new();
            for (key, value) in &raw_attrib {
                let expanded_key = self.expand_attr_key(key);
                if attrib.iter().any(|(existing, _)| *existing == expanded_key) {
                    return Err(self.error("not well-formed (invalid token)"));
                }
                attrib.push((expanded_key, value.clone()));
            }
            let node = EtNode {
                tag,
                attrib,
                text: String::new(),
                children: Vec::new(),
            };
            if empty {
                let element = node;
                if let Some(parent) = self.stack.last_mut() {
                    parent.node.children.push(element);
                    parent.has_child = true;
                } else if self.root_closed {
                    return Err(self.error("junk after document element"));
                } else {
                    self.root = Some(element);
                    self.root_closed = true;
                }
                self.scopes.pop();
                // gt là offset tuyệt đối của '>' — advance theo khoảng cách.
                self.advance(gt + 1 - self.byte_offset);
                continue;
            }
            self.advance(gt + 1 - self.byte_offset);
            self.stack.push(Open {
                node,
                has_child: false,
            });
        }
        if let Some(open) = self.stack.pop() {
            let _ = open;
            return Err(self.error("no element found"));
        }
        match self.root.take() {
            Some(root) => Ok(root),
            None => Err(self.error("no element found")),
        }
    }

    fn line_of(&self, offset: usize) -> usize {
        self.input[..offset.min(self.input.len())].matches('\n').count() + 1
    }

    fn line_start_of(&self, offset: usize) -> usize {
        let capped = offset.min(self.input.len());
        self.input[..capped].rfind('\n').map(|p| p + 1).unwrap_or(0)
    }

    /// Parse attribute list — raw keys/values (expansion làm ở caller sau khi
    /// push scope của chính element).
    fn parse_attribs_raw(&self, raw: &str) -> Result<Vec<(String, String)>, ParseError> {
        let mut attrib: Vec<(String, String)> = Vec::new();
        let bytes = raw.as_bytes();
        let mut index = 0usize;
        while index < bytes.len() {
            while index < bytes.len() && bytes[index].is_ascii_whitespace() {
                index += 1;
            }
            if index >= bytes.len() {
                break;
            }
            let name_start = index;
            while index < bytes.len()
                && bytes[index] != b'='
                && !bytes[index].is_ascii_whitespace()
            {
                index += 1;
            }
            if name_start == index {
                return Err(ParseError {
                    message: "not well-formed (invalid token)".to_string(),
                });
            }
            let name = raw[name_start..index].to_string();
            while index < bytes.len() && bytes[index].is_ascii_whitespace() {
                index += 1;
            }
            if index >= bytes.len() || bytes[index] != b'=' {
                return Err(ParseError {
                    message: "not well-formed (invalid token)".to_string(),
                });
            }
            index += 1;
            while index < bytes.len() && bytes[index].is_ascii_whitespace() {
                index += 1;
            }
            if index >= bytes.len() || (bytes[index] != b'"' && bytes[index] != b'\'') {
                return Err(ParseError {
                    message: "not well-formed (invalid token)".to_string(),
                });
            }
            let quote = bytes[index];
            index += 1;
            let value_start = index;
            while index < bytes.len() && bytes[index] != quote {
                index += 1;
            }
            if index >= bytes.len() {
                return Err(ParseError {
                    message: "not well-formed (invalid token)".to_string(),
                });
            }
            let value = unescape(&raw[value_start..index]).ok_or_else(|| ParseError {
                message: "not well-formed (invalid token)".to_string(),
            })?;
            index += 1;
            // duplicate attribute (expat fail-loud) — so theo raw key; check
            // theo expanded key làm ở caller.
            if attrib.iter().any(|(key, _)| *key == name) {
                return Err(ParseError {
                    message: "not well-formed (invalid token)".to_string(),
                });
            }
            attrib.push((name, value));
        }
        Ok(attrib)
    }
}

fn find_tag_end(input: &str, start: usize) -> Option<usize> {
    let bytes = input.as_bytes();
    let mut index = start + 1;
    let mut quote: Option<u8> = None;
    while index < bytes.len() {
        let byte = bytes[index];
        match quote {
            Some(q) => {
                if byte == q {
                    quote = None;
                }
            }
            None => {
                if byte == b'"' || byte == b'\'' {
                    quote = Some(byte);
                } else if byte == b'>' {
                    return Some(index);
                }
            }
        }
        index += 1;
    }
    None
}

fn split_tag(inner: &str) -> Option<(&str, &str)> {
    let trimmed = inner.trim();
    let first = trimmed.chars().next()?;
    if !(first.is_ascii_alphabetic() || first == '_' || first == ':') {
        return None;
    }
    let mut split_at = trimmed.len();
    let mut in_quote: Option<u8> = None;
    let bytes = trimmed.as_bytes();
    for (index, &byte) in bytes.iter().enumerate() {
        match in_quote {
            Some(q) => {
                if byte == q {
                    in_quote = None;
                }
            }
            None => {
                if byte == b'"' || byte == b'\'' {
                    in_quote = Some(byte);
                } else if byte.is_ascii_whitespace() {
                    split_at = index;
                    break;
                }
            }
        }
    }
    let name = &trimmed[..split_at];
    let rest = trimmed[split_at..].trim();
    Some((name, rest))
}

/// Unescape XML entities như ET/expat: 5 entity chuẩn + numeric. Unknown
/// entity → None (expat: undefined entity).
fn unescape(raw: &str) -> Option<String> {
    if !raw.contains('&') {
        return Some(raw.to_string());
    }
    let mut out = String::with_capacity(raw.len());
    let bytes = raw.as_bytes();
    let mut index = 0usize;
    while index < bytes.len() {
        if bytes[index] != b'&' {
            let start = index;
            while index < bytes.len() && bytes[index] != b'&' {
                index += 1;
            }
            out.push_str(&raw[start..index]);
            continue;
        }
        let semi = raw[index..].find(';')?;
        let entity = &raw[index + 1..index + semi];
        let replacement = match entity {
            "amp" => "&".to_string(),
            "lt" => "<".to_string(),
            "gt" => ">".to_string(),
            "quot" => "\"".to_string(),
            "apos" => "'".to_string(),
            other if other.starts_with("#x") || other.starts_with("#X") => {
                let digits = &other[2..];
                if digits.is_empty() || !digits.chars().all(|c| c.is_ascii_hexdigit()) {
                    return None;
                }
                let code = u32::from_str_radix(digits, 16).ok()?;
                let parsed = char::from_u32(code)?;
                if parsed == '\0'
                    || (0xd800u32..0xe000u32).contains(&(parsed as u32))
                    || code > 0x10ffff
                {
                    return None;
                }
                parsed.to_string()
            }
            other if other.starts_with('#') => {
                let digits = &other[1..];
                if digits.is_empty() || !digits.chars().all(|c| c.is_ascii_digit()) {
                    return None;
                }
                let code: u32 = digits.parse().ok()?;
                let parsed = char::from_u32(code)?;
                if parsed == '\0'
                    || (0xd800u32..0xe000u32).contains(&(parsed as u32))
                    || code > 0x10ffff
                {
                    return None;
                }
                parsed.to_string()
            }
            _ => return None,
        };
        out.push_str(&replacement);
        index += semi + 1;
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_namespaced_tags() {
        let doc = parse_document(
            r#"<?xml version="1.0"?><manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.a"><application android:label="App"><activity android:name=".Main"/></application></manifest>"#,
        )
        .unwrap();
        assert_eq!(doc.attr("package"), Some("com.a"));
        let app = doc.find("application").unwrap();
        assert_eq!(app.attr("{http://schemas.android.com/apk/res/android}label"), Some("App"));
        let activity = app.find("activity").unwrap();
        assert_eq!(
            activity.attr("{http://schemas.android.com/apk/res/android}name"),
            Some(".Main")
        );
    }

    #[test]
    fn mismatched_tag_error_matches_expat() {
        let error = parse_document("<project><artifactId>broken</project>").unwrap_err();
        assert_eq!(error.message, "mismatched tag: line 1, column 29");
    }

    #[test]
    fn undefined_entity_fails() {
        assert!(parse_document("<a>&nope;</a>").is_err());
    }

    #[test]
    fn iter_is_preorder_with_self() {
        let doc = parse_document("<a><b><c/></b><d/></a>").unwrap();
        let tags: Vec<&str> = doc.iter_nodes().iter().map(|n| n.tag.as_str()).collect();
        assert_eq!(tags, ["a", "b", "c", "d"]);
    }
}


#[cfg(test)]
mod debug_tests {
    use super::*;

    #[test]
    fn debug_attribs() {
        let doc = parse_document(r#"<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.a"><application android:label="App"><activity android:name=".Main"/></application></manifest>"#).unwrap();
        println!("root attribs: {:?}", doc.attrib);
        let app = doc.find("application").unwrap();
        println!("app attribs: {:?}", app.attrib);
    }
}
