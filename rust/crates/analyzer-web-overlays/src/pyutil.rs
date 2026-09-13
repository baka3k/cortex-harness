//! Tiện ích chung cho các overlay: decode/đọc giới hạn kiểu Python, walk thư
//! mục deterministic, hash helper, path helpers.

use std::path::{Path, PathBuf};

/// Decode UTF-8 với `errors="replace"` của Python: byte lỗi → U+FFFD.
pub fn decode_utf8_replace(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes).into_owned()
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

pub fn sha256_hex(data: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(data);
    hex(&hasher.finalize())
}

fn hex(data: &[u8]) -> String {
    data.iter().map(|b| format!("{b:02x}")).collect()
}

/// POSIX hoá đường dẫn relative.
pub fn to_posix(path: &Path) -> String {
    path.components()
        .map(|c| c.as_os_str().to_string_lossy().to_string())
        .collect::<Vec<_>>()
        .join("/")
}

/// Walk thư mục deterministic (sort dirs + files, không theo symlink).
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
        let Ok(file_type) = entry.file_type() else {
            continue;
        };
        let path = entry.path();
        // Python os.walk(followlinks=False): symlink-to-dir vẫn liệt kê vào
        // dirnames nhưng không descend; is_file() trên symlink tới file = True.
        if file_type.is_symlink() {
            if path.is_file() {
                files.push(path);
            } else {
                dirs.push(path);
            }
        } else if file_type.is_dir() {
            dirs.push(path);
        } else if file_type.is_file() {
            files.push(path);
        }
    }
    dirs.sort();
    files.sort();
    for file in &files {
        if let Ok(rel) = file.strip_prefix(root) {
            out.push((file.clone(), to_posix(rel)));
        }
    }
    for dir in &dirs {
        walk_inner(root, dir, out);
    }
}

/// `tools.common.aspnet.identity.normalize_relative_path`.
pub fn normalize_relative_path(path: &str) -> String {
    let mut value = path.replace('\\', "/");
    while value.starts_with("./") {
        value = value[2..].to_string();
    }
    value.trim_start_matches('/').to_string()
}

/// `stable_digest(*parts, length=24)` — sha256(`"\x1f".join(str(part))`)[:length].
pub fn stable_digest(parts: &[String], length: usize) -> String {
    let payload = parts.join("\u{1f}");
    sha256_hex(payload.as_bytes())[..length].to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_relative_path_matches_python() {
        assert_eq!(normalize_relative_path("a\\b\\c.txt"), "a/b/c.txt");
        assert_eq!(normalize_relative_path("./a/b"), "a/b");
        assert_eq!(normalize_relative_path("/a/b/"), "a/b/");
        assert_eq!(normalize_relative_path(""), "");
    }

    #[test]
    fn digest_matches_python() {
        // stable_digest("a", "b") == sha256("a\x1fb")[:24]
        let expected_prefix = &{
            use sha2::{Digest, Sha256};
            let payload = "a\u{1f}b";
            let mut hasher = Sha256::new();
            hasher.update(payload.as_bytes());
            hex(&hasher.finalize())
        }[..24];
        assert_eq!(stable_digest(&["a".into(), "b".into()], 24), expected_prefix);
    }

    #[test]
    fn walk_is_sorted_and_skips_symlink_dirs() {
        let tmp = std::env::temp_dir().join(format!("p08_walk_test_{}", std::process::id()));
        let sub = tmp.join("b_dir");
        std::fs::create_dir_all(&sub).unwrap();
        std::fs::create_dir_all(tmp.join("a_dir")).unwrap();
        std::fs::write(tmp.join("z.txt"), "z").unwrap();
        std::fs::write(sub.join("a.txt"), "a").unwrap();
        let files = walk_sorted(&tmp);
        let rels: Vec<String> = files.into_iter().map(|(_, rel)| rel).collect();
        // Root files trước, rồi descend từng dir theo thứ tự sort (khớp
        // os.walk topdown); a_dir rỗng nên không góp phần tử.
        assert_eq!(rels, vec!["z.txt".to_string(), "b_dir/a.txt".to_string()]);
        std::fs::remove_dir_all(&tmp).ok();
    }
}
