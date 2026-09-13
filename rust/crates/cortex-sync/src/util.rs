//! Small helpers shared across the cortex-sync orchestrator port.
//!
//! Mirrors the Python helpers used by `incremental_sync.py`:
//! `os.path.realpath`, `_normalize_project_path`, `_safe_segment`,
//! `_normalize_slug`, `_now_iso`, `fnmatch.fnmatch`, and canonical
//! JSON/hashing helpers.

use std::path::{Component, Path, PathBuf};

use sha1::{Digest as Sha1Digest, Sha1};
use sha2::{Digest, Sha256};

/// `os.path.realpath(os.path.abspath(path))` — resolve symlinks on the
/// longest existing prefix, then append the remainder lexically.
pub fn realpath(input: &str) -> PathBuf {
    let abs = absolute(input);
    realpath_of(&abs)
}

/// `os.path.abspath` — join with cwd and normalize without resolving symlinks.
pub fn absolute(input: &str) -> PathBuf {
    let path = Path::new(input);
    let joined = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")).join(path)
    };
    normalize_lexical(&joined)
}

fn normalize_lexical(path: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for component in path.components() {
        match component {
            Component::CurDir => {}
            Component::ParentDir => {
                if !out.pop() {
                    out.push("..");
                }
            }
            other => out.push(other.as_os_str()),
        }
    }
    if out.as_os_str().is_empty() {
        out.push("/");
    }
    out
}

fn realpath_of(path: &Path) -> PathBuf {
    // Component-wise: canonicalize the deepest existing ancestor, then append
    // the remaining (non-existing) components lexically. This mirrors
    // os.path.realpath without byte-offset slicing hazards.
    let mut cursor: PathBuf = path.to_path_buf();
    let mut removed: Vec<std::ffi::OsString> = Vec::new();
    loop {
        if let Ok(resolved) = std::fs::canonicalize(&cursor) {
            if removed.is_empty() {
                return resolved;
            }
            let mut result = resolved;
            for part in removed.iter().rev() {
                result.push(part);
            }
            return result;
        }
        match cursor.file_name() {
            Some(name) if !name.is_empty() => {
                removed.push(name.to_os_string());
                if !cursor.pop() {
                    return normalize_lexical(path);
                }
            }
            _ => return normalize_lexical(path),
        }
    }
}

pub fn path_to_string(path: &Path) -> String {
    path.as_os_str().to_string_lossy().to_string()
}

/// `_normalize_project_path` — relative, posix, inside `root`; else None.
pub fn normalize_project_path(root: &Path, raw_path: &str) -> Option<String> {
    let text = raw_path.trim();
    if text.is_empty() {
        return None;
    }
    let normalized_root = realpath(&path_to_string(root));
    let path_text = text.replace('\\', "/");
    let path = Path::new(&path_text);
    if path.is_absolute() {
        let abs_path = realpath(&path_text);
        let rel = abs_path.strip_prefix(&normalized_root).ok()?;
        let rel = path_to_string(rel);
        if rel.starts_with("../") || rel == ".." {
            return None;
        }
        return Some(rel);
    }
    let rel = path_to_string(&normalize_lexical(path)).replace('\\', "/");
    if rel == "." || rel.starts_with("../") || rel == ".." {
        return None;
    }
    Some(rel)
}

/// `_safe_segment` — `[A-Za-z0-9_.-]` otherwise `_`, trimmed of `._`.
pub fn safe_segment(value: &str) -> String {
    let mut cleaned: String = value
        .trim()
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || "._-".contains(c) { c } else { '_' })
        .collect();
    cleaned = cleaned
        .trim_start_matches(['.', '_'])
        .trim_end_matches(['.', '_'])
        .to_string();
    if cleaned.is_empty() {
        "project".to_string()
    } else {
        cleaned
    }
}

/// `_normalize_slug` — lowercase `[a-z0-9-]` slug.
pub fn normalize_slug(value: &str) -> String {
    let mut cleaned: String = value
        .trim()
        .to_lowercase()
        .chars()
        .map(|c| if c.is_ascii_lowercase() || c.is_ascii_digit() { c } else { '-' })
        .collect();
    cleaned = cleaned.trim_matches('-').to_string();
    if cleaned.is_empty() {
        "project".to_string()
    } else {
        cleaned
    }
}

/// `time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())`.
pub fn now_iso() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    let secs = now.as_secs() as i64;
    let (year, month, day, hour, minute, second) = civil_from_unix(secs);
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}Z")
}

/// `datetime.now(timezone.utc).isoformat()` — microseconds, `+00:00` suffix.
pub fn now_iso_micros() -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    let secs = now.as_secs() as i64;
    let micros = now.subsec_micros();
    let (year, month, day, hour, minute, second) = civil_from_unix(secs);
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}.{micros:06}+00:00")
}

fn civil_from_unix(secs: i64) -> (i64, u32, u32, u32, u32, u32) {
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    let hour = (rem / 3600) as u32;
    let minute = ((rem % 3600) / 60) as u32;
    let second = (rem % 60) as u32;
    // Howard Hinnant's civil_from_days algorithm.
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let year = if m <= 2 { y + 1 } else { y };
    (year, m, d, hour, minute, second)
}

/// `fnmatch.fnmatch` on POSIX (case-sensitive): `*`, `?`, `[seq]`.
pub fn fnmatch(name: &str, pattern: &str) -> bool {
    let regex = fnmatch_regex(pattern);
    regex.is_match(name)
}

pub fn fnmatch_regex(pattern: &str) -> regex::Regex {
    let mut out = String::from("^");
    let mut chars = pattern.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '*' => out.push_str(".*"),
            '?' => out.push('.'),
            '[' => {
                let mut class = String::from("[");
                let mut closed = false;
                let mut first = true;
                while let Some(&nc) = chars.peek() {
                    if nc == ']' && !first {
                        chars.next();
                        closed = true;
                        break;
                    }
                    first = false;
                    if nc == '!' && class.len() == 1 {
                        class.push('^');
                    } else if nc == '\\' {
                        class.push_str("\\\\");
                    } else if nc == ']' {
                        class.push_str("\\]");
                    } else {
                        class.push(nc);
                    }
                    chars.next();
                }
                class.push(']');
                if closed {
                    out.push_str(&class);
                } else {
                    out.push_str("\\[");
                }
            }
            c => {
                out.push_str(&regex::escape(&c.to_string()));
            }
        }
    }
    out.push('$');
    regex::Regex::new(&out).unwrap_or_else(|_| regex::Regex::new("^$").unwrap())
}

pub fn sha1_hex(data: &[u8]) -> String {
    let mut hasher = Sha1::new();
    hasher.update(data);
    let digest = hasher.finalize();
    hex(&digest)
}

pub fn sha256_hex(data: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data);
    let digest = hasher.finalize();
    hex(&digest)
}

pub fn hex(data: &[u8]) -> String {
    let mut out = String::with_capacity(data.len() * 2);
    for byte in data {
        out.push_str(&format!("{byte:02x}"));
    }
    out
}

pub fn sha256_of_file(path: &Path) -> std::io::Result<String> {
    let data = std::fs::read(path)?;
    Ok(sha256_hex(&data))
}

/// `uuid.uuid4().hex` — 32 lowercase hex chars.
pub fn uuid4_hex() -> String {
    let mut bytes = [0u8; 16];
    getrandom::getrandom(&mut bytes).expect("getrandom failure");
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    hex(&bytes)
}

/// `json.dumps(value, sort_keys=True, separators=(",", ":"))` as a String.
pub fn canonical_json_string(value: &serde_json::Value) -> String {
    String::from_utf8(cortex_graph_core::identity::canonical_json(value)).unwrap()
}

/// Round-half-even of a float to `digits` decimals, mirroring Python `round`.
pub fn round_digits(value: f64, digits: u32) -> f64 {
    let factor = 10f64.powi(digits as i32);
    (value * factor).round() / factor
}
