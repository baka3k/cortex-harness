//! Port `tools/cplus/function_identity.py` — canonical overload-safe C/C++
//! function identity (schema `cplus-function-v2`), byte-exact với Python.

use sha2::{Digest, Sha256};

pub const FUNCTION_IDENTITY_SCHEMA: &str = "cplus-function-v2";

fn is_special(c: char) -> bool {
    matches!(c, '*' | '&' | '(' | ')' | ',' | '<' | '>')
}

/// `normalize_syntax`:
/// `re.sub(r"\s+", " ", value.strip())` rồi
/// `re.sub(r"\s*([*&(),<>])\s*", r"\1", value)`.
pub fn normalize_syntax(value: &str) -> String {
    // Pass 1: collapse whitespace runs thành một space, strip hai đầu.
    let mut collapsed = String::with_capacity(value.len());
    let mut in_space = false;
    for ch in value.trim().chars() {
        if ch.is_whitespace() {
            in_space = true;
            continue;
        }
        if in_space && !collapsed.is_empty() {
            collapsed.push(' ');
        }
        in_space = false;
        collapsed.push(ch);
    }
    // Pass 2: bỏ space kề trái/phải từng ký tự đặc biệt.
    let chars: Vec<char> = collapsed.chars().collect();
    let mut out = String::with_capacity(chars.len());
    for (i, &c) in chars.iter().enumerate() {
        if c == ' ' {
            let prev_special = i > 0 && is_special(chars[i - 1]);
            let next_special = chars.get(i + 1).copied().map(is_special).unwrap_or(false);
            if prev_special || next_special {
                continue;
            }
        }
        out.push(c);
    }
    out
}

#[derive(Debug, Clone)]
pub struct FunctionIdentity {
    pub logical_id: String,
    pub canonical_signature: String,
    pub parameter_types: Vec<String>,
    pub qualifiers: String,
    pub template_arity: i64,
    pub linkage: String,
    pub legacy_alias: String,
}

/// `build_function_identity` — JSON canonical (sort_keys, separators compact,
/// ensure_ascii) → sha256 hex[:24].
pub fn build_function_identity(
    qualified_name: &str,
    parameter_types: &[String],
    qualifiers: &str,
    template_arity: i64,
    linkage: &str,
    rel_path: &str,
    start_byte: i64,
    parseable: bool,
) -> FunctionIdentity {
    let qualified_step = normalize_syntax(qualified_name);
    let qualified = if qualified_step.is_empty() {
        "<anonymous>".to_string()
    } else {
        qualified_step
    };
    let params: Vec<String> = parameter_types
        .iter()
        .map(|v| {
            let n = normalize_syntax(v);
            if n.is_empty() {
                "?".to_string()
            } else {
                n
            }
        })
        .collect();
    let normalized_qualifiers = normalize_syntax(qualifiers);
    let linkage_step = normalize_syntax(linkage).to_lowercase();
    let normalized_linkage = if linkage_step.is_empty() {
        "external".to_string()
    } else {
        linkage_step
    };
    let mut discriminator = String::new();
    if matches!(
        normalized_linkage.as_str(),
        "internal" | "unique_external" | "no_linkage"
    ) {
        discriminator = rel_path.replace('\\', "/");
    }
    if !parseable || qualified == "<anonymous>" {
        let byte = start_byte.max(0);
        discriminator = format!("{discriminator}#byte:{byte}");
    }
    let template_arity_clamped = template_arity.max(0);
    // json.dumps(canonical, ensure_ascii=True, sort_keys=True,
    //            separators=(",", ":")) — key order alphabetical:
    // discriminator, linkage, parameter_types, qualified_name, qualifiers,
    // template_arity.
    let params_canonical = canonical_json_string_array(&params);
    let canonical = format!(
        "{{\"discriminator\":{},\"linkage\":{},\"parameter_types\":{params_canonical},\"qualified_name\":{},\"qualifiers\":{},\"template_arity\":{}}}",
        json_ensure_ascii(&discriminator),
        json_ensure_ascii(&normalized_linkage),
        json_ensure_ascii(&qualified),
        json_ensure_ascii(&normalized_qualifiers),
        template_arity_clamped,
    );
    let mut hasher = Sha256::new();
    hasher.update(canonical.as_bytes());
    let digest = hex(&hasher.finalize())[..24].to_string();
    let qualifier_display = if normalized_qualifiers.is_empty() {
        "-"
    } else {
        normalized_qualifiers.as_str()
    };
    let discriminator_display = if discriminator.is_empty() {
        "-"
    } else {
        discriminator.as_str()
    };
    let signature = format!(
        "{qualified}({})|qualifiers:{qualifier_display}|template:{template_arity_clamped}|linkage:{normalized_linkage}|discriminator:{discriminator_display}",
        params.join(",")
    );
    let legacy_rel = rel_path.replace('\\', "/");
    let legacy = format!("{qualified}/{}@{legacy_rel}", params.len());
    FunctionIdentity {
        logical_id: format!("{FUNCTION_IDENTITY_SCHEMA}::{qualified}::{digest}"),
        canonical_signature: signature,
        parameter_types: params,
        qualifiers: normalized_qualifiers,
        template_arity: template_arity_clamped,
        linkage: normalized_linkage,
        legacy_alias: legacy,
    }
}

/// `json.dumps(list_of_str, ensure_ascii=True, separators=(",", ":"))`.
pub fn canonical_json_string_array(items: &[String]) -> String {
    let inner: Vec<String> = items.iter().map(|s| json_ensure_ascii(s)).collect();
    format!("[{}]", inner.join(","))
}

/// JSON string literal với ensure_ascii=True: escape `"`,`\`, control chars,
/// mọi non-ASCII thành `\uXXXX` (surrogate pair cho > BMP).
pub fn json_ensure_ascii(value: &str) -> String {
    let mut out = String::with_capacity(value.len() + 2);
    out.push('"');
    for ch in value.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => {
                out.push_str(&format!("\\u{:04x}", c as u32));
            }
            c if (c as u32) > 0x7e => {
                let mut buf = [0u16; 2];
                for unit in c.encode_utf16(&mut buf) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn hex(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        out.push_str(&format!("{b:02x}"));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Golden fingerprint từ Python:
    /// build_function_identity(qualified_name="Derived::run",
    ///   parameter_types=("int",), ...) -> logical_id suffix khớp.
    #[test]
    fn matches_python_logical_id() {
        let ident = build_function_identity(
            "outer::helper",
            &["int".to_string()],
            "",
            0,
            "external",
            "t.cpp",
            0,
            true,
        );
        assert_eq!(
            ident.logical_id,
            "cplus-function-v2::outer::helper::b1c53f751d688d5534ff9605"
        );
    }

    #[test]
    fn normalize_syntax_strips_symbol_spaces() {
        assert_eq!(normalize_syntax("int * x"), "int*x");
        assert_eq!(normalize_syntax("unsigned long , int"), "unsigned long,int");
        assert_eq!(normalize_syntax("  "), "");
    }
}
