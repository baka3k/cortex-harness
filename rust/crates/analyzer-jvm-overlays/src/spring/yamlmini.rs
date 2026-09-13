//! Mini YAML reader — subset PyYAML `safe_load_all` đủ cho Spring config
//! corpus: block mapping/sequence theo thụt lề, flow `{}`/`[]`, scalar
//! resolve (bool/int/float/null/string), quoted strings, comment, multi-doc
//! `---`/`...`. Lỗi parse trả diagnostic (message sai khác PyYAML — chấp
//! nhận, corpus parity dùng YAML hợp lệ).

use serde_json::Value;

#[derive(Debug)]
pub struct YamlError(pub String);

pub fn load_all(text: &str) -> Result<Vec<Value>, YamlError> {
    let lines = split_logical(text);
    // Chia documents theo marker `---` / `...` (giống PyYAML: text không marker
    // → 1 document; `---` mở document mới; document rỗng → Null).
    let mut chunks: Vec<Vec<Line>> = Vec::new();
    let mut current: Vec<Line> = Vec::new();
    for line in &lines {
        if line.is_doc_start() {
            chunks.push(std::mem::take(&mut current));
            continue;
        }
        if line.is_doc_end() {
            chunks.push(std::mem::take(&mut current));
            continue;
        }
        if line.is_blank_or_comment() {
            continue;
        }
        current.push(Line { indent: line.indent, content: line.content.clone() });
    }
    chunks.push(current);
    // Bỏ chunk rỗng đầu khi file không mở đầu bằng `---`.
    let mut docs: Vec<Value> = Vec::new();
    for (index, chunk) in chunks.iter().enumerate() {
        if chunk.is_empty() {
            if index == 0 && chunks.len() > 1 {
                continue;
            }
            docs.push(Value::Null);
            continue;
        }
        let parser = BlockParser { lines: chunk.as_slice() };
        let (value, _next) = parser
            .parse_block(0, 0)
            .map_err(|message| YamlError(message.to_string()))?;
        docs.push(value);
    }
    Ok(docs)
}

struct Line {
    indent: usize,
    content: String,
}

impl Line {
    fn is_blank_or_comment(&self) -> bool {
        self.content.is_empty() || self.content.starts_with('#')
    }
    fn is_doc_start(&self) -> bool {
        self.content == "---" || self.content.starts_with("--- ")
    }
    fn is_doc_end(&self) -> bool {
        self.content == "..." || self.content == "---"
    }
}

fn split_logical(text: &str) -> Vec<Line> {
    let mut out = Vec::new();
    for raw in text.split('\n') {
        let trimmed = raw.strip_suffix('\r').unwrap_or(raw);
        let indent = trimmed.len() - trimmed.trim_start().len();
        let mut content = trimmed.trim_start().to_string();
        if content == "---" || content == "..." {
            // keep marker
        } else {
            content = strip_comment(&content).trim_end().to_string();
        }
        out.push(Line { indent, content });
    }
    out
}

/// Bỏ comment `#` ngoài chuỗi (chuẩn YAML tối thiểu: `#` sau whitespace).
fn strip_comment(text: &str) -> &str {
    let bytes = text.as_bytes();
    let mut quote: Option<u8> = None;
    let mut index = 0usize;
    while index < bytes.len() {
        let byte = bytes[index];
        match quote {
            Some(q) => {
                if byte == q {
                    quote = None;
                }
            }
            None => {
                if byte == b'\'' || byte == b'"' {
                    quote = Some(byte);
                } else if byte == b'#' && (index == 0 || bytes[index - 1] == b' ' || bytes[index - 1] == b'\t') {
                    return &text[..index];
                }
            }
        }
        index += 1;
    }
    text
}

struct BlockParser<'a> {
    lines: &'a [Line],
}

type ParseResult<'a> = Result<(Value, usize), &'a str>;

impl<'a> BlockParser<'a> {
    /// Parse block bắt đầu tại `start` với thụt lề `indent`; trả (value, next index).
    fn parse_block(&self, start: usize, indent: usize) -> ParseResult<'a> {
        if start >= self.lines.len() {
            return Err("unexpected end of YAML input");
        }
        let line = &self.lines[start];
        if line.content.starts_with("- ") || line.content == "-" {
            self.parse_sequence(start, if indent == 0 { line.indent } else { indent })
        } else {
            self.parse_mapping(start, if indent == 0 { line.indent } else { indent })
        }
    }

    fn parse_sequence(&self, start: usize, indent: usize) -> ParseResult<'a> {
        let mut items: Vec<Value> = Vec::new();
        let mut index = start;
        while index < self.lines.len() {
            let line = &self.lines[index];
            if line.is_blank_or_comment() {
                index += 1;
                continue;
            }
            if line.indent < indent {
                break;
            }
            if line.indent > indent {
                return Err("bad indentation in sequence");
            }
            if !(line.content.starts_with("- ") || line.content == "-") {
                break;
            }
            let rest = line.content[1..].trim().to_string();
            if rest.is_empty() {
                // Item trên các dòng tiếp theo.
                let next = index + 1;
                if next < self.lines.len() && self.lines[next].indent > indent {
                    let (value, next_index) = self.parse_block(next, self.lines[next].indent)?;
                    items.push(value);
                    index = next_index;
                } else {
                    items.push(Value::Null);
                    index += 1;
                }
                continue;
            }
            if let Some((key, remainder)) = split_key(&rest) {
                // Mapping inline đầu item: `- key: value`.
                let mut map = serde_json::Map::new();
                let base_indent = line.indent + 2;
                let item_indent = line.indent + (line.content.len() - line.content[1..].trim_start().len() - 1);
                let value_end = if remainder.trim().is_empty() {
                    // value ở dòng sau
                    let next = index + 1;
                    if next < self.lines.len() && self.lines[next].indent > item_indent {
                        let (value, next_index) = self.parse_block(next, self.lines[next].indent)?;
                        map.insert(key, value);
                        next_index
                    } else {
                        map.insert(key, Value::Null);
                        index + 1
                    }
                } else {
                    map.insert(key.clone(), parse_scalar_or_flow(remainder.trim()));
                    // Các dòng key tiếp theo cùng item (indent > dash indent).
                    let mut cursor = index + 1;
                    while cursor < self.lines.len() {
                        let candidate = &self.lines[cursor];
                        if candidate.is_blank_or_comment() {
                            cursor += 1;
                            continue;
                        }
                        if candidate.indent <= line.indent {
                            break;
                        }
                        if candidate.indent < base_indent && !(candidate.content.starts_with("- ") || candidate.content == "-") {
                            break;
                        }
                        let (next_key, next_rest) = match split_key(&candidate.content) {
                            Some(pair) => pair,
                            None => break,
                        };
                        if next_rest.trim().is_empty() {
                            let after = cursor + 1;
                            if after < self.lines.len() && self.lines[after].indent > candidate.indent {
                                let (value, after_index) = self.parse_block(after, self.lines[after].indent)?;
                                map.insert(next_key, value);
                                cursor = after_index;
                            } else {
                                map.insert(next_key, Value::Null);
                                cursor += 1;
                            }
                        } else {
                            map.insert(next_key, parse_scalar_or_flow(next_rest.trim()));
                            cursor += 1;
                        }
                    }
                    cursor
                };
                items.push(Value::Object(map));
                index = value_end;
            } else {
                items.push(parse_scalar_or_flow(&rest));
                index += 1;
            }
        }
        Ok((Value::Array(items), index))
    }

    fn parse_mapping(&self, start: usize, indent: usize) -> ParseResult<'a> {
        let mut map = serde_json::Map::new();
        let mut index = start;
        while index < self.lines.len() {
            let line = &self.lines[index];
            if line.is_blank_or_comment() {
                index += 1;
                continue;
            }
            if line.indent < indent {
                break;
            }
            if line.indent > indent {
                return Err("bad indentation in mapping");
            }
            if line.content.starts_with("- ") || line.content == "-" {
                break;
            }
            let (key, remainder) = match split_key(&line.content) {
                Some(pair) => pair,
                None => return Err("mapping values are not allowed here"),
            };
            if remainder.trim().is_empty() {
                let next = index + 1;
                if next < self.lines.len() && !self.lines[next].is_blank_or_comment() && self.lines[next].indent > indent {
                    let (value, next_index) = self.parse_block(next, self.lines[next].indent)?;
                    map.insert(key, value);
                    index = next_index;
                } else {
                    map.insert(key, Value::Null);
                    index += 1;
                }
            } else {
                map.insert(key, parse_scalar_or_flow(remainder.trim()));
                index += 1;
            }
        }
        Ok((Value::Object(map), index))
    }
}

/// Tách `key: value` (key tới dấu `: ` đầu tiên ngoài quotes/flow).
fn split_key(text: &str) -> Option<(String, &str)> {
    let mut quote: Option<char> = None;
    let mut depth = 0i32;
    for (index, ch) in text.char_indices() {
        match quote {
            Some(q) => {
                if ch == q {
                    quote = None;
                }
            }
            None => match ch {
                '\'' | '"' => quote = Some(ch),
                '{' | '[' => depth += 1,
                '}' | ']' => depth -= 1,
                ':' if depth == 0 => {
                    let after = text.get(index + 1..)?;
                    if after.is_empty() || after.starts_with(' ') {
                        let key = text[..index].trim();
                        return Some((unquote_key(key), &text[index + 1..]));
                    }
                }
                _ => {}
            },
        }
    }
    None
}

fn unquote_key(key: &str) -> String {
    let trimmed = key.trim();
    if (trimmed.starts_with('"') && trimmed.ends_with('"') && trimmed.len() >= 2)
        || (trimmed.starts_with('\'') && trimmed.ends_with('\'') && trimmed.len() >= 2)
    {
        trimmed[1..trimmed.len() - 1].to_string()
    } else {
        trimmed.to_string()
    }
}

fn parse_scalar_or_flow(text: &str) -> Value {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return Value::Null;
    }
    if trimmed.starts_with('{') && trimmed.ends_with('}') {
        return parse_flow(trimmed);
    }
    if trimmed.starts_with('[') && trimmed.ends_with(']') {
        return parse_flow(trimmed);
    }
    if (trimmed.starts_with('"') && trimmed.ends_with('"') && trimmed.len() >= 2)
        || (trimmed.starts_with('\'') && trimmed.ends_with('\'') && trimmed.len() >= 2)
    {
        return Value::String(unquote(trimmed));
    }
    // Multi-line plain scalar: chỉ lấy phần đã gộp dòng (caller không gộp —
    // corpus không dùng plain multi-line).
    resolve_scalar(trimmed)
}

fn unquote(text: &str) -> String {
    let inner = &text[1..text.len() - 1];
    if text.starts_with('"') {
        // Double-quote: xử lý escape phổ biến.
        let mut out = String::new();
        let mut chars = inner.chars();
        while let Some(ch) = chars.next() {
            if ch == '\\' {
                match chars.next() {
                    Some('n') => out.push('\n'),
                    Some('t') => out.push('\t'),
                    Some('r') => out.push('\r'),
                    Some('\\') => out.push('\\'),
                    Some('"') => out.push('"'),
                    Some(other) => {
                        out.push('\\');
                        out.push(other);
                    }
                    None => out.push('\\'),
                }
            } else {
                out.push(ch);
            }
        }
        out
    } else {
        inner.replace("''", "'")
    }
}

/// Split theo dấu phẩy top-level (ngoài quotes/flow).
fn split_flow_items(text: &str) -> Vec<String> {
    let mut items = Vec::new();
    let mut current = String::new();
    let mut quote: Option<char> = None;
    let mut depth = 0i32;
    for ch in text.chars() {
        match quote {
            Some(q) => {
                current.push(ch);
                if ch == q {
                    quote = None;
                }
            }
            None => match ch {
                '\'' | '"' => {
                    quote = Some(ch);
                    current.push(ch);
                }
                '{' | '[' => {
                    depth += 1;
                    current.push(ch);
                }
                '}' | ']' => {
                    depth -= 1;
                    current.push(ch);
                }
                ',' if depth == 0 => {
                    items.push(current.clone());
                    current.clear();
                }
                _ => current.push(ch),
            },
        }
    }
    if !current.trim().is_empty() {
        items.push(current);
    }
    items
}

fn parse_flow(text: &str) -> Value {
    let trimmed = text.trim();
    if trimmed.starts_with('{') {
        let inner = &trimmed[1..trimmed.len() - 1];
        let mut map = serde_json::Map::new();
        for item in split_flow_items(inner) {
            if let Some((key, value)) = split_key(&item) {
                map.insert(key, parse_scalar_or_flow(value.trim()));
            }
        }
        return Value::Object(map);
    }
    if trimmed.starts_with('[') {
        let inner = &trimmed[1..trimmed.len() - 1];
        return Value::Array(
            split_flow_items(inner)
                .iter()
                .map(|item| parse_scalar_or_flow(item.trim()))
                .collect(),
        );
    }
    resolve_scalar(trimmed)
}

/// PyYAML resolve scalar (subset: null/bool/int/float/string).
pub fn resolve_scalar(text: &str) -> Value {
    let trimmed = text.trim();
    match trimmed {
        "" | "~" | "null" | "Null" | "NULL" => return Value::Null,
        "true" | "True" | "TRUE" => return Value::Bool(true),
        "false" | "False" | "FALSE" => return Value::Bool(false),
        _ => {}
    }
    if let Some(value) = parse_yaml_int(trimmed) {
        return value;
    }
    if let Some(value) = parse_yaml_float(trimmed) {
        return value;
    }
    Value::String(trimmed.to_string())
}

fn parse_yaml_int(text: &str) -> Option<Value> {
    let cleaned = text.replace('_', "");
    // YAML 1.1 (PyYAML): `0o17` octal, `017` cũng octal (leading zero).
    if let Some(rest) = cleaned.strip_prefix("0x") {
        return i64::from_str_radix(rest, 16).ok().map(|number| Value::Number(number.into()));
    }
    if let Some(rest) = cleaned.strip_prefix("0o") {
        return i64::from_str_radix(rest, 8).ok().map(|number| Value::Number(number.into()));
    }
    if cleaned.len() > 1
        && cleaned.starts_with('0')
        && cleaned[1..].chars().all(|c| c.is_ascii_digit())
        && cleaned[1..].chars().all(|c| ('0'..='7').contains(&c))
    {
        return i64::from_str_radix(&cleaned[1..], 8).ok().map(|number| Value::Number(number.into()));
    }
    cleaned.parse::<i64>().ok().map(|number| Value::Number(number.into()))
}

fn parse_yaml_float(text: &str) -> Option<Value> {
    let cleaned = text.replace('_', "");
    match cleaned.as_str() {
        ".inf" | ".Inf" | ".INF" | "+.inf" => return Some(Value::from(f64::INFINITY)),
        "-.inf" | "-.Inf" | "-.INF" => return Some(Value::from(f64::NEG_INFINITY)),
        _ => {}
    }
    let looks_like_float = cleaned.contains('.')
        || cleaned.contains('e')
        || cleaned.contains('E')
        || matches!(cleaned.as_str(), ".nan" | ".NaN" | ".NAN");
    if !looks_like_float {
        return None;
    }
    if matches!(cleaned.as_str(), ".nan" | ".NaN" | ".NAN") {
        return Some(Value::from(f64::NAN));
    }
    cleaned.parse::<f64>().ok().map(Value::from)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pyjson::dumps_compact;

    #[test]
    fn parses_nested_block_yaml() {
        let text = "server:\n  port: 8080\n  host: localhost\nlogging:\n  level:\n    root: INFO\n";
        let docs = load_all(text).unwrap();
        assert_eq!(docs.len(), 1);
        assert_eq!(dumps_compact(&docs[0]), r#"{"logging":{"level":{"root":"INFO"}},"server":{"host":"localhost","port":8080}}"#);
    }

    #[test]
    fn resolves_scalars_like_pyyaml() {
        assert_eq!(resolve_scalar("true"), Value::Bool(true));
        assert_eq!(resolve_scalar("0755"), Value::Number(493.into()));
        assert_eq!(resolve_scalar("3.5"), serde_json::json!(3.5));
        assert_eq!(resolve_scalar("hello"), Value::String("hello".into()));
    }
}
