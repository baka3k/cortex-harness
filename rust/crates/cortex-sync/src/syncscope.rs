//! Port of `tools/common/sync_scope.py` — scope id, cache-dir resolution and
//! the OS-backed project run lock (portalocker ⇒ `flock`).

use std::collections::BTreeMap;
use std::fs::OpenOptions;
use std::os::unix::io::AsRawFd;
use std::path::{Path, PathBuf};

use serde_json::json;

/// `canonical_root` — normcase(realpath(abspath)). POSIX normcase is identity
/// (lowercasing only happens on Windows).
pub fn canonical_root(root: &Path) -> String {
    let real = crate::util::realpath(&crate::util::path_to_string(root));
    #[cfg(windows)]
    {
        real.to_string_lossy().to_lowercase()
    }
    #[cfg(not(windows))]
    {
        real.to_string_lossy().to_string()
    }
}

/// `scan_scope_id` — sha256(f"{project_id}\0{canonical_root}")[:24].
pub fn scan_scope_id(project_id: &str, root: &Path) -> String {
    use sha2::{Digest, Sha256};
    let identity = format!("{project_id}\0{}", canonical_root(root));
    let digest = Sha256::digest(identity.as_bytes());
    crate::util::hex(&digest)[..24].to_string()
}

/// `resolve_sync_cache_dir`.
pub fn resolve_sync_cache_dir(cache_dir: Option<&str>, root: &Path) -> PathBuf {
    match cache_dir {
        Some(dir) if !dir.trim().is_empty() => crate::util::realpath(
            shellexpand_home(&crate::util::path_to_string(&PathBuf::from(dir))).as_str(),
        ),
        _ => crate::util::realpath(&crate::util::path_to_string(root)).join(".cache"),
    }
}

fn shellexpand_home(input: &str) -> String {
    if let Some(rest) = input.strip_prefix("~/") {
        if let Ok(home) = std::env::var("HOME") {
            return Path::new(&home).join(rest).to_string_lossy().to_string();
        }
    }
    input.to_string()
}

fn safe_project_id(project_id: &str) -> String {
    crate::util::safe_segment(project_id)
}

/// `state_file_path`.
pub fn state_file_path(cache_dir: &Path, project_id: &str, root: &Path) -> PathBuf {
    let scope = scan_scope_id(project_id, root);
    cache_dir
        .join("incremental_sync")
        .join(format!("{}_{}.json", safe_project_id(project_id), scope))
}

/// `legacy_state_file_path`.
pub fn legacy_state_file_path(cache_dir: &Path, project_id: &str, _root: &Path) -> PathBuf {
    cache_dir
        .join("incremental_sync")
        .join(format!("{}.json", safe_project_id(project_id)))
}

/// `read_lock_metadata` — best-effort JSON read of the diagnostic metadata.
pub fn read_lock_metadata(path: &Path) -> serde_json::Value {
    let mut candidates = vec![PathBuf::from(format!("{}.metadata.json", path.to_string_lossy()))];
    candidates.push(path.to_path_buf());
    for candidate in candidates {
        if let Ok(text) = std::fs::read_to_string(&candidate) {
            if let Ok(value) = serde_json::from_str::<serde_json::Value>(&text) {
                if value.is_object() {
                    return value;
                }
            }
        }
    }
    json!({})
}

pub struct ProjectRunLock {
    pub path: PathBuf,
    pub scope_id: String,
    handle: Option<std::fs::File>,
}

impl ProjectRunLock {
    pub fn new(path: PathBuf, scope_id: &str) -> Self {
        ProjectRunLock {
            path: crate::util::realpath(&crate::util::path_to_string(&path)),
            scope_id: scope_id.to_string(),
            handle: None,
        }
    }

    /// `ProjectRunLock.acquire` — flock(LOCK_EX|LOCK_NB) polled until the
    /// timeout expires, then `LockBusyError`.
    pub fn acquire(&mut self, description: &str, root: &Path, timeout_seconds: f64) -> Result<(), String> {
        if self.handle.is_some() {
            return Ok(());
        }
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(&self.path)
            .map_err(|error| error.to_string())?;
        let deadline = std::time::Instant::now()
            + std::time::Duration::from_secs_f64(timeout_seconds.max(0.0));
        loop {
            let result = flock_raw(file.as_raw_fd());
            match result {
                Ok(()) => break,
                Err(_) => {
                    if std::time::Instant::now() >= deadline {
                        return Err(format!(
                            "scan scope is busy: {description} ({})",
                            self.scope_id
                        ));
                    }
                    std::thread::sleep(std::time::Duration::from_millis(20));
                }
            }
        }
        let metadata = json!({
            "pid": std::process::id(),
            "scope_id": self.scope_id,
            "description": description,
            "root": canonical_root(root),
            "started_at": crate::util::now_iso_micros(),
        });
        let payload = format!("{metadata}\n");
        use std::io::Write;
        let mut handle = file;
        handle.set_len(0).ok();
        handle.write_all(payload.as_bytes()).ok();
        handle.flush().ok();
        #[allow(unused_mut)]
        let _ = &mut handle;
        let metadata_path = PathBuf::from(format!("{}.metadata.json", self.path.to_string_lossy()));
        let temp_path = PathBuf::from(format!("{}.metadata.json.tmp", self.path.to_string_lossy()));
        if std::fs::write(&temp_path, format!("{metadata}\n")).is_ok() {
            std::fs::rename(&temp_path, &metadata_path).ok();
        }
        self.handle = Some(handle);
        Ok(())
    }

    pub fn release(&mut self) {
        if let Some(file) = self.handle.take() {
            unflock_raw(file.as_raw_fd());
        }
    }
}

impl Drop for ProjectRunLock {
    fn drop(&mut self) {
        self.release();
    }
}

/// Safe wrapper around `flock(LOCK_EX|LOCK_NB)`.
#[allow(unsafe_code)]
fn flock_raw(fd: std::os::unix::io::RawFd) -> Result<(), String> {
    // SAFETY: fd is a live file descriptor; LOCK_EX|LOCK_NB is nonblocking.
    let result = unsafe { libc::flock(fd, libc::LOCK_EX | libc::LOCK_NB) };
    if result == 0 {
        Ok(())
    } else {
        Err(std::io::Error::last_os_error().to_string())
    }
}

/// Safe wrapper around `flock(LOCK_UN)`.
#[allow(unsafe_code)]
fn unflock_raw(fd: std::os::unix::io::RawFd) {
    // SAFETY: fd is a live file descriptor previously flocked.
    unsafe { libc::flock(fd, libc::LOCK_UN) };
}

/// `safe_cache_root` from `tools/common/analyzer_cache.py`.
pub fn safe_cache_root(
    cache_dir: Option<&str>,
    default_name: &str,
    project_root: Option<&Path>,
) -> PathBuf {
    let base_root: PathBuf = match cache_dir {
        Some(dir) if !dir.trim().is_empty() => PathBuf::from(dir),
        _ => std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")).join(".cache"),
    };
    let mut root = base_root.join(default_name);
    if let Some(project_root) = project_root {
        if let Some(segment) = project_cache_segment(project_root) {
            root = root.join(segment);
        }
    }
    std::fs::create_dir_all(&root).ok();
    root
}

fn project_cache_segment(project_root: &Path) -> Option<String> {
    let normalized = crate::util::realpath(&crate::util::path_to_string(project_root));
    let basename = normalized
        .file_name()
        .map(|name| name.to_string_lossy().to_string())
        .unwrap_or_else(|| "root".to_string());
    let safe: String = basename
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || "._-".contains(c) { c } else { '_' })
        .collect();
    let safe = if safe.is_empty() { "root".to_string() } else { safe };
    let digest = crate::util::sha1_hex(normalized.to_string_lossy().as_bytes())[..12].to_string();
    Some(format!("{safe}_{digest}"))
}

/// Key ordering helper for deterministic JSON maps.
pub type JsonMap = BTreeMap<String, serde_json::Value>;
