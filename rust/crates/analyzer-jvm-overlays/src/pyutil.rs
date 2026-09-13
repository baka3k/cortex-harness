//! Tiện ích chung cho các overlay: decode/đọc giới hạn kiểu Python, walk
//! thư mục deterministic, hash helper, path helpers.

use std::path::{Path, PathBuf};

/// Decode UTF-8 với `errors="ignore"` của Python: bỏ byte lỗi (không thay U+FFFD).
pub fn decode_utf8_ignore(bytes: &[u8]) -> String {
    let mut out = String::new();
    let mut rest = bytes;
    loop {
        match std::str::from_utf8(rest) {
            Ok(text) => {
                out.push_str(text);
                break;
            }
            Err(error) => {
                let (valid, tail) = rest.split_at(error.valid_up_to());
                if let Ok(text) = std::str::from_utf8(valid) {
                    out.push_str(text);
                }
                let skip = error.error_len().unwrap_or(1);
                if skip >= tail.len() {
                    break;
                }
                rest = &tail[skip..];
            }
        }
    }
    out
}

/// `detector.read_limited` — đọc tối đa `limit` ký tự (Python text stream
/// `handle.read(limit)` đếm ký tự, không phải byte), decode utf-8 ignore.
/// Trả "" khi OSError (file không tồn tại/không đọc được).
pub fn read_limited(path: &Path, limit: usize) -> String {
    match std::fs::read(path) {
        Ok(bytes) => {
            let text = decode_utf8_ignore(&bytes);
            if text.chars().count() > limit {
                text.chars().take(limit).collect()
            } else {
                text
            }
        }
        Err(_) => String::new(),
    }
}

/// `pathlib.Path.resolve()` — realpath (resolve symlink), fallback abspath.
pub fn realpath(path: &Path) -> PathBuf {
    std::fs::canonicalize(path).unwrap_or_else(|_| abs_path(path))
}

/// `os.path.abspath` — normalize `.`/`..` không resolve symlink.
pub fn abs_path(path: &Path) -> PathBuf {
    if path.is_absolute() {
        normalize_absolute(path)
    } else {
        let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
        normalize_absolute(&cwd.join(path))
    }
}

fn normalize_absolute(path: &Path) -> PathBuf {
    let mut out = PathBuf::from("/");
    for component in path.components() {
        match component {
            std::path::Component::RootDir | std::path::Component::CurDir => {}
            std::path::Component::ParentDir => {
                out.pop();
            }
            other => out.push(other.as_os_str()),
        }
    }
    out
}

/// `safe_rel_path` — `path.replace("\\", "/").strip("./")` của Python
/// (strip theo tập ký tự '.' và '/' ở HAI đầu).
pub fn strip_dot_slash(text: &str) -> String {
    text.replace('\\', "/")
        .trim_matches(|c| c == '.' || c == '/')
        .to_string()
}

/// Walk thư mục deterministic (sort dirs + files) — OS listing order của
/// Python `os.walk` không ổn định; mọi consumer cuối đều sort lại nên thứ tự
/// walk chỉ ảnh hưởng budget-cap (không gặp trong corpus parity).
pub fn walk_sorted(root: &Path) -> Vec<(PathBuf, String)> {
    let mut out = Vec::new();
    walk_inner(root, root, &mut out);
    out
}

fn walk_inner(root: &Path, current: &Path, out: &mut Vec<(PathBuf, String)>) {
    let entries = match std::fs::read_dir(current) {
        Ok(entries) => entries,
        Err(_) => return,
    };
    let mut dirs: Vec<PathBuf> = Vec::new();
    let mut files: Vec<PathBuf> = Vec::new();
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            dirs.push(path);
        } else if path.is_file() {
            files.push(path);
        }
    }
    dirs.sort();
    files.sort();
    for file in files {
        if let Ok(rel) = file.strip_prefix(root) {
            out.push((file.clone(), to_posix(rel)));
        }
    }
    for dir in dirs {
        walk_inner(root, &dir, out);
    }
}

/// POSIX hoá đường dẫn relative.
pub fn to_posix(path: &Path) -> String {
    path.components()
        .map(|c| c.as_os_str().to_string_lossy().to_string())
        .collect::<Vec<_>>()
        .join("/")
}

pub fn sha1_hex16(text: &str) -> String {
    use sha1::{Digest, Sha1};
    let mut hasher = Sha1::new();
    hasher.update(text.as_bytes());
    let digest = hasher.finalize();
    hex(&digest)[..16].to_string()
}

pub fn sha256_hex(data: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(data);
    hex(&hasher.finalize())
}

fn hex(data: &[u8]) -> String {
    data.iter().map(|b| format!("{b:02x}")).collect()
}

/// `os.path.basename`.
pub fn basename(path: &str) -> String {
    match path.rsplit('/').next() {
        Some(name) => name.to_string(),
        None => path.to_string(),
    }
}

/// `os.path.dirname` cho đường dẫn posix.
pub fn dirname(path: &str) -> String {
    match path.rfind('/') {
        Some(index) => path[..index].to_string(),
        None => String::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decode_ignore_skips_bad_bytes() {
        let bytes = vec![b'a', b'b', 0xFF, b'c'];
        assert_eq!(decode_utf8_ignore(&bytes), "abc");
    }

    #[test]
    fn strip_dot_slash_matches_python() {
        assert_eq!(strip_dot_slash("./a/b/"), "a/b");
        assert_eq!(strip_dot_slash("..a."), "a");
        assert_eq!(strip_dot_slash("src/x.java"), "src/x.java");
    }

    #[test]
    fn sha1_matches_python() {
        // hashlib.sha1(b"demo").hexdigest()[:16]
        assert_eq!(sha1_hex16("demo"), "89e495e7941cf9e4");
    }
}
