//! Python-parity helpers: canonical JSON, timestamps, path resolution, and
//! durable atomic file writes. These exist so manifests, fingerprints, and
//! on-disk payloads are byte-identical to the Python storage layer.

use std::collections::VecDeque;
use std::ffi::OsString;
use std::fs;
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Component, Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::Value;

/// Serialize like `json.dumps(value, sort_keys=True, separators=(",", ":"),
/// ensure_ascii=True)` — the canonical payload format used by manifests.
pub fn canonical_json(value: &Value) -> String {
    encode(value, true, true)
}

/// Serialize like `json.dumps(value, sort_keys=True)` — default separators
/// `(", ", ": ")`, ensure_ascii. Used where Python used the defaults.
pub fn default_dumps_sorted(value: &Value) -> String {
    encode(value, true, false)
}

/// Serialize like `json.dumps(value, indent=2, sort_keys=True)` — the
/// layout-manifest format.
pub fn default_dumps_sorted_pretty(value: &Value) -> String {
    let mut out = String::new();
    encode_pretty(value, true, 0, &mut out);
    out
}

fn encode_pretty(value: &Value, sort_keys: bool, depth: usize, out: &mut String) {
    match value {
        Value::Array(items) if !items.is_empty() => {
            out.push_str("[\n");
            for (i, item) in items.iter().enumerate() {
                push_indent(out, depth + 1);
                encode_pretty(item, sort_keys, depth + 1, out);
                if i + 1 < items.len() {
                    out.push(',');
                }
                out.push('\n');
            }
            push_indent(out, depth);
            out.push(']');
        }
        Value::Object(map) if !map.is_empty() => {
            let mut entries: Vec<(&String, &Value)> = map.iter().collect();
            if sort_keys {
                entries.sort_by(|a, b| a.0.cmp(b.0));
            }
            out.push_str("{\n");
            for (i, (key, item)) in entries.iter().enumerate() {
                push_indent(out, depth + 1);
                encode_string(key, out);
                out.push_str(": ");
                encode_pretty(item, sort_keys, depth + 1, out);
                if i + 1 < entries.len() {
                    out.push(',');
                }
                out.push('\n');
            }
            push_indent(out, depth);
            out.push('}');
        }
        other => encode_into(other, sort_keys, true, out),
    }
}

fn push_indent(out: &mut String, depth: usize) {
    for _ in 0..depth {
        out.push_str("  ");
    }
}

fn encode(value: &Value, sort_keys: bool, compact: bool) -> String {
    let mut out = String::new();
    encode_into(value, sort_keys, compact, &mut out);
    out
}

fn encode_into(value: &Value, sort_keys: bool, compact: bool, out: &mut String) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Number(n) => out.push_str(&encode_number(n)),
        Value::String(s) => encode_string(s, out),
        Value::Array(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                    if !compact {
                        out.push(' ');
                    }
                }
                encode_into(item, sort_keys, compact, out);
            }
            out.push(']');
        }
        Value::Object(map) => {
            let mut entries: Vec<(&String, &Value)> = map.iter().collect();
            if sort_keys {
                entries.sort_by(|a, b| a.0.cmp(b.0));
            }
            out.push('{');
            for (i, (key, item)) in entries.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                    if !compact {
                        out.push(' ');
                    }
                }
                encode_string(key, out);
                out.push(':');
                if !compact {
                    out.push(' ');
                }
                encode_into(item, sort_keys, compact, out);
            }
            out.push('}');
        }
    }
}

fn encode_number(n: &serde_json::Number) -> String {
    n.to_string()
}

fn encode_string(s: &str, out: &mut String) {
    out.push('"');
    for ch in s.chars() {
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
                // ensure_ascii=True: escape every non-ASCII code point.
                let code = c as u32;
                if code <= 0xffff {
                    out.push_str(&format!("\\u{:04x}", code));
                } else {
                    let v = code - 0x1_0000;
                    let hi = 0xd800 + (v >> 10);
                    let lo = 0xdc00 + (v & 0x3ff);
                    out.push_str(&format!("\\u{hi:04x}\\u{lo:04x}"));
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

/// `datetime.now(timezone.utc).isoformat(timespec="milliseconds")` with the
/// `+00:00` suffix replaced by `Z` — the manifest timestamp format.
pub fn utc_now() -> String {
    isoformat_now(true)
}

/// `isoformat(timespec="seconds")` variant used by leases and layout manifests.
pub fn utc_now_seconds() -> String {
    isoformat_now(false)
}

fn isoformat_now(milliseconds: bool) -> String {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let secs = now.as_secs() as i64;
    let millis = now.subsec_millis();
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    let (h, m, s) = (rem / 3600, (rem % 3600) / 60, rem % 60);
    let (year, month, day) = civil_from_days(days);
    if milliseconds {
        format!("{year:04}-{month:02}-{day:02}T{h:02}:{m:02}:{s:02}.{millis:03}Z")
    } else {
        format!("{year:04}-{month:02}-{day:02}T{h:02}:{m:02}:{s:02}Z")
    }
}

/// Days since 1970-01-01 to (year, month, day); Howard Hinnant's algorithm.
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let y = if m <= 2 { y + 1 } else { y };
    (y, m, d)
}

/// `Path.expanduser()` for the leading `~` / `~user` (only `~` supported;
/// matching POSIX harness usage).
pub fn expanduser(path: &Path) -> PathBuf {
    let text = path.to_string_lossy();
    if (text == "~" || text.starts_with("~/"))
        && let Some(home) = std::env::var_os("HOME") {
            let home = PathBuf::from(home);
            if text == "~" {
                return home;
            }
            return home.join(&text[2..]);
        }
    path.to_path_buf()
}

/// `Path(...).resolve()` with `strict=False` (posixpath `_joinrealpath`):
/// resolve symlinks in the existing prefix, keep non-existent tails verbatim,
/// normalize `.` and `..` against resolved components.
pub fn resolve_path(path: &Path) -> PathBuf {
    let expanded = expanduser(path);
    let absolute = if expanded.is_absolute() {
        expanded
    } else {
        match std::env::current_dir() {
            Ok(cwd) => cwd.join(expanded),
            Err(_) => expanded,
        }
    };
    let mut queue: VecDeque<OsString> = absolute
        .components()
        .filter(|c| !matches!(c, Component::RootDir | Component::Prefix(_)))
        .map(|c| c.as_os_str().to_os_string())
        .collect();
    let mut parts: Vec<OsString> = vec![OsString::from("/")];
    let mut symlink_hops = 0usize;
    while let Some(name) = queue.pop_front() {
        if name.is_empty() || name == "." {
            continue;
        }
        if name == ".." {
            if parts.len() > 1 {
                parts.pop();
            }
            continue;
        }
        let mut candidate: PathBuf = parts.iter().collect();
        candidate.push(&name);
        match fs::symlink_metadata(&candidate) {
            Ok(meta) if meta.file_type().is_symlink() => {
                symlink_hops += 1;
                if symlink_hops > 40 {
                    // ELOOP: Python returns the path as-is in non-strict mode.
                    return absolute;
                }
                match fs::read_link(&candidate) {
                    Ok(link) => {
                        if link.is_absolute() {
                            for component in link.components().rev() {
                                if let Component::Normal(part) = component {
                                    queue.push_front(part.to_os_string());
                                }
                            }
                        } else {
                            // Relative link: resolve against current parts.
                            let mut base: Vec<OsString> = parts.clone();
                            for component in link.components() {
                                match component {
                                    Component::Normal(part) => base.push(part.to_os_string()),
                                    Component::ParentDir => {
                                        base.pop();
                                    }
                                    _ => {}
                                }
                            }
                            parts = base;
                        }
                    }
                    Err(_) => return absolute,
                }
            }
            _ => parts.push(name),
        }
    }
    parts.into_iter().collect()
}

/// Write `payload` to `target` atomically via `<stem>.tmp` + rename + dir fsync.
/// Mirrors the Python `temporary.open("w"); fsync; os.replace; dir fsync` idiom.
pub fn write_atomic(target: &Path, payload: &str) -> io::Result<()> {
    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent)?;
    }
    let temporary = with_suffix(target, ".tmp");
    {
        let mut handle = fs::OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o644)
            .open(&temporary)?;
        handle.write_all(payload.as_bytes())?;
        handle.flush()?;
        handle.sync_all()?;
    }
    fs::rename(&temporary, target)?;
    fsync_directory(target.parent().unwrap_or_else(|| Path::new("/")))?;
    Ok(())
}

/// `Path.with_suffix(suffix)` equivalent used by the Python port.
pub fn with_suffix(path: &Path, suffix: &str) -> PathBuf {
    let os = path.as_os_str().to_os_string();
    let text = os.to_string_lossy();
    match text.rfind('.') {
        Some(idx) if idx > text.rfind('/').map_or(0, |v| v) => {
            PathBuf::from(format!("{}{}", &text[..idx], suffix))
        }
        _ => PathBuf::from(format!("{}{}", text, suffix)),
    }
}

/// `open(dir, O_RDONLY); fsync(dir); close(dir)` — POSIX-only durability sync.
pub fn fsync_directory(dir: &Path) -> io::Result<()> {
    let handle = fs::File::open(dir)?;
    handle.sync_all()
}

/// Append-write helper matching Python `open(path, "a+")` for lock files.
pub fn open_read_write_create(path: &Path) -> io::Result<fs::File> {
    fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o644)
        .open(path)
}

/// Read a whole file as UTF-8, mapping NotFound to `Ok(None)`.
pub fn read_optional(path: &Path) -> io::Result<Option<String>> {
    match fs::File::open(path) {
        Ok(mut handle) => {
            let mut text = String::new();
            handle.read_to_string(&mut text)?;
            Ok(Some(text))
        }
        Err(err) if err.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(err) => Err(err),
    }
}

/// Truncate a file to zero bytes from the start (portalocker release idiom).
pub fn truncate_file(handle: &mut fs::File) -> io::Result<()> {
    handle.seek(SeekFrom::Start(0))?;
    handle.set_len(0)?;
    handle.flush()
}

/// Best-effort recursive removal (`shutil.rmtree`).
pub fn rmtree(path: &Path) -> io::Result<()> {
    match fs::symlink_metadata(path) {
        Ok(meta) => {
            if meta.file_type().is_symlink() || meta.is_file() {
                fs::remove_file(path)
            } else {
                fs::remove_dir_all(path)
            }
        }
        Err(err) if err.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(err) => Err(err),
    }
}

/// mode bits of a path if it exists (used by copy helpers).
pub fn mode_bits(path: &Path) -> Option<u32> {
    fs::metadata(path).ok().map(|meta| meta.permissions().mode())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn canonical_json_matches_python_semantics() {
        let value = json!({
            "b": 1,
            "a": "x\u{00e9}\n",
            "c": [true, null, 1.5],
        });
        assert_eq!(
            canonical_json(&value),
            "{\"a\":\"x\\u00e9\\n\",\"b\":1,\"c\":[true,null,1.5]}"
        );
    }

    #[test]
    fn utc_now_shape() {
        let stamp = utc_now();
        assert_eq!(stamp.len(), 24);
        assert!(stamp.ends_with('Z'));
        assert_eq!(&stamp[4..5], "-");
        let secs = utc_now_seconds();
        assert_eq!(secs.len(), 20);
        assert!(secs.ends_with('Z'));
    }

    #[test]
    fn with_suffix_matches_python() {
        assert_eq!(
            with_suffix(Path::new("/x/active-generation.json"), ".tmp"),
            PathBuf::from("/x/active-generation.tmp")
        );
        assert_eq!(
            with_suffix(Path::new("/x/manifest.json"), ".json.tmp"),
            PathBuf::from("/x/manifest.json.tmp")
        );
        assert_eq!(
            with_suffix(Path::new("/x/noext"), ".tmp"),
            PathBuf::from("/x/noext.tmp")
        );
    }

    #[test]
    fn resolve_path_handles_missing_tail() {
        let base = std::env::temp_dir().join("cortex-storage-util-test");
        let _ = fs::remove_dir_all(&base);
        fs::create_dir_all(&base).unwrap();
        let missing = base.join("a/b/c");
        let resolved = resolve_path(&missing);
        assert!(resolved.starts_with(fs::canonicalize(&base).unwrap()));
        assert!(resolved.ends_with("a/b/c"));
        let _ = fs::remove_dir_all(&base);
    }
}
