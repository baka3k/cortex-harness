//! Port `tools/servlet_jsp/path_resolver.py` — root-confined path resolution.

use std::path::{Path, PathBuf};

use crate::pyutil::{abs_path, realpath};

#[derive(Debug, Clone)]
pub struct PathResolution {
    pub status: String,
    pub reference: String,
    pub relative_path: String,
    pub absolute_path: String,
    pub target_kind: String,
    pub message: String,
}

fn control_char(text: &str) -> bool {
    text.chars().any(|ch| ch <= '\u{1f}' || ch == '\u{7f}')
}

/// `normalize_relative_path` — backslash → slash, bỏ `./` đầu, strip `/` 2 đầu.
pub fn normalize_relative_path(path: &str) -> String {
    let value = path.replace('\\', "/");
    let mut value = value.as_str();
    while let Some(rest) = value.strip_prefix("./") {
        value = rest;
    }
    value.trim_matches('/').to_string()
}

/// `unquote` của urllib.parse (percent-decoding).
fn unquote(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut index = 0usize;
    while index < bytes.len() {
        if bytes[index] == b'%' && index + 2 < bytes.len() {
            let hex = &text[index + 1..index + 3];
            if let Ok(byte) = u8::from_str_radix(hex, 16) {
                out.push(byte);
                index += 3;
                continue;
            }
        }
        out.push(bytes[index]);
        index += 1;
    }
    String::from_utf8_lossy(&out).to_string()
}

/// `urlsplit` subset — scheme + path.
fn split_url(text: &str) -> (String, String) {
    for (index, ch) in text.char_indices() {
        if ch == ':' {
            let scheme = &text[..index];
            if !scheme.is_empty()
                && scheme
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || c == '+' || c == '-' || c == '.')
                && scheme.chars().next().map(|c| c.is_ascii_alphabetic()).unwrap_or(false)
            {
                let rest = &text[index + 1..];
                let path = rest.trim_start_matches('/');
                return (scheme.to_lowercase(), path.to_string());
            }
            break;
        }
        if !ch.is_ascii_alphanumeric() && ch != '+' && ch != '-' && ch != '.' {
            break;
        }
    }
    (String::new(), text.to_string())
}

fn is_drive_qualified(text: &str) -> bool {
    let bytes = text.as_bytes();
    bytes.len() >= 3
        && bytes[0].is_ascii_alphabetic()
        && bytes[1] == b':'
        && (bytes[2] == b'/' || bytes[2] == b'\\')
}

#[allow(clippy::too_many_arguments)]
pub fn resolve_project_path(
    root: &str,
    reference: &str,
    base_file: &str,
    web_root_relative: bool,
    require_exists: bool,
) -> PathResolution {
    let raw = reference.trim().to_string();
    let resolution = |status: &str, message: &str, target_kind: &str| PathResolution {
        status: status.to_string(),
        reference: raw.clone(),
        relative_path: String::new(),
        absolute_path: String::new(),
        target_kind: target_kind.to_string(),
        message: message.to_string(),
    };
    if raw.is_empty() {
        return resolution("invalid", "empty path", "file");
    }
    if control_char(&raw) {
        return resolution("rejected", "control characters are not allowed", "file");
    }
    let decoded = unquote(&raw);
    if control_char(&decoded) {
        return resolution("rejected", "decoded control characters are not allowed", "file");
    }
    let (scheme, path_part_full) = split_url(&decoded);
    // urlsplit().path giữ nguyên phần path (cắt ?query, #fragment).
    let path_part = path_part_full
        .split(['?', '#'])
        .next()
        .unwrap_or("")
        .to_string();
    if !scheme.is_empty() {
        if scheme == "http" || scheme == "https" || scheme == "mailto" {
            return resolution("external", "external resources are not fetched", "external");
        }
        return resolution(
            "rejected",
            &format!("unsupported scheme: {scheme}"),
            "external",
        );
    }
    if path_part.starts_with("\\\\") || path_part.starts_with("//") || is_drive_qualified(&path_part) {
        return resolution("rejected", "UNC and drive-qualified paths are not allowed", "file");
    }
    let root_real = realpath(Path::new(root));
    let candidate_rel = if path_part.starts_with('/') {
        if !web_root_relative {
            return resolution("rejected", "absolute filesystem paths are not allowed", "file");
        }
        normalize_relative_path(&path_part)
    } else {
        let base_dir = if base_file.is_empty() {
            String::new()
        } else {
            crate::pyutil::dirname(&normalize_relative_path(base_file))
        };
        let joined = if base_dir.is_empty() {
            path_part.clone()
        } else {
            format!("{base_dir}/{path_part}")
        };
        normalize_relative_path(&normalize_dots(&joined))
    };
    if candidate_rel == ".." || candidate_rel.starts_with("../") {
        return resolution("rejected", "path traversal escapes the project root", "file");
    }
    let candidate_abs = abs_path(&root_real.join(&candidate_rel));
    let candidate_real = realpath(&candidate_abs);
    let root_str = root_real.to_string_lossy().to_string();
    let candidate_str = candidate_real.to_string_lossy().to_string();
    if candidate_str != root_str && !candidate_str.starts_with(&format!("{root_str}/")) {
        return resolution("rejected", "resolved path escapes the project root", "file");
    }
    if require_exists && !candidate_real.exists() {
        return PathResolution {
            status: "missing".to_string(),
            reference: raw,
            relative_path: candidate_rel,
            absolute_path: candidate_str,
            target_kind: "file".to_string(),
            message: "target does not exist".to_string(),
        };
    }
    if candidate_real.exists() && !candidate_real.is_file() {
        return PathResolution {
            status: "rejected".to_string(),
            reference: raw,
            relative_path: candidate_rel,
            absolute_path: candidate_str,
            target_kind: "file".to_string(),
            message: "target is not a regular file".to_string(),
        };
    }
    PathResolution {
        status: "resolved".to_string(),
        reference: raw,
        relative_path: candidate_rel,
        absolute_path: candidate_str,
        target_kind: "file".to_string(),
        message: String::new(),
    }
}

/// `os.path.normpath` cho posix path — collapse `.`/`..`.
fn normalize_dots(path: &str) -> String {
    let mut out: Vec<&str> = Vec::new();
    for component in path.split('/') {
        match component {
            "" | "." => continue,
            ".." => {
                out.pop();
            }
            other => out.push(other),
        }
    }
    out.join("/")
}

/// `read_bounded_file` — đọc tối đa max_bytes, trả (data, truncated).
pub fn read_bounded_file(path: &Path, max_bytes: usize) -> std::io::Result<(Vec<u8>, bool)> {
    let data = std::fs::read(path)?;
    if data.len() > max_bytes {
        Ok((data[..max_bytes].to_vec(), true))
    } else {
        Ok((data, false))
    }
}

/// `PathBuf` tiện dụng.
#[allow(dead_code)]
pub fn unused(_: PathBuf) {}
