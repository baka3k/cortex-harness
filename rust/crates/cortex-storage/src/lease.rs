//! Cross-process leases for embedded storage owners.
//!
//! Port of `cortex_harness.storage.lease`: portalocker's `LOCK_EX |
//! LOCK_NB` file lock is reproduced directly with `flock(2)` on an open
//! descriptor, so ownership semantics — including automatic release when a
//! process is killed — are identical.

use std::fmt;
use std::fs;
use std::io::{self, Write};
use std::os::unix::io::AsRawFd;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use crate::errors::{StoreError, StoreResult};
use crate::ffi;
use crate::util::{
    open_read_write_create, read_optional, resolve_path, truncate_file, utc_now_seconds,
};

/// Another process currently owns an embedded store.
#[derive(Debug, Clone)]
pub struct StorageLeaseConflictError {
    pub message: String,
    pub lock_path: PathBuf,
    pub current_lease: String,
}

impl fmt::Display for StorageLeaseConflictError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for StorageLeaseConflictError {}

impl From<StorageLeaseConflictError> for StoreError {
    fn from(err: StorageLeaseConflictError) -> Self {
        StoreError::Runtime(err.message)
    }
}

/// A cross-process advisory lease held beside one embedded store file.
#[derive(Debug)]
pub struct StorageLease {
    pub target: PathBuf,
    pub instance_id: String,
    pub owner_id: String,
    pub backend: String,
    pub lock_path: PathBuf,
    handle: Option<fs::File>,
}

impl StorageLease {
    pub fn new(target: &Path, instance_id: &str, owner_id: &str, backend: &str) -> Self {
        let target = resolve_path(target);
        // The lease lives beside the store; acquiring it must not create the
        // target itself because migration copies into previously absent
        // directories atomically.
        let base = target
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_else(|| PathBuf::from("."));
        let lock_path = base.join(format!(".{}.cortex-owner.lock", target_name(&target)));
        Self {
            target,
            instance_id: instance_id.to_string(),
            owner_id: owner_id.to_string(),
            backend: backend.to_string(),
            lock_path,
            handle: None,
        }
    }

    /// Lease metadata written into the lock file while it is held.
    pub fn metadata(&self) -> Value {
        json!({
            "instance_id": self.instance_id,
            "owner_id": self.owner_id,
            "backend": self.backend,
            "target": self.target.to_string_lossy(),
            "pid": std::process::id(),
            "host": ffi::hostname(),
            "acquired_at": utc_now_seconds(),
        })
    }

    /// Acquire the exclusive lease or fail immediately with a conflict.
    #[allow(clippy::result_large_err)]
    pub fn acquire(mut self) -> Result<StorageLease, (Self, StorageLeaseConflictError)> {
        let lock_path = self.lock_path.clone();
        let conflict = move |message: String, current: String| -> StorageLeaseConflictError {
            StorageLeaseConflictError {
                message,
                lock_path: lock_path.clone(),
                current_lease: current,
            }
        };
        if let Err(err) = fs::create_dir_all(self.lock_path.parent().unwrap_or_else(|| Path::new("/")))
        {
            return Err((
                self,
                conflict(format!("cannot create lease directory: {err}"), String::new()),
            ));
        }
        let mut handle = match open_read_write_create(&self.lock_path) {
            Ok(handle) => handle,
            Err(err) => {
                return Err((
                    self,
                    conflict(format!("cannot open lease file: {err}"), String::new()),
                ));
            }
        };
        match ffi::flock_exclusive_nonblocking(handle.as_raw_fd()) {
            Ok(()) => {
                let metadata = self.metadata();
                // Canonical sorted compact JSON like json.dumps(sort_keys=True).
                let payload = crate::util::canonical_json(&metadata);
                if let Err(err) = write_lease_payload(&mut handle, &payload) {
                    let _ = ffi::flock_release(handle.as_raw_fd());
                    return Err((
                        self,
                        conflict(format!("cannot write lease metadata: {err}"), String::new()),
                    ));
                }
                self.handle = Some(handle);
                Ok(self)
            }
            Err(err) if is_conflict(&err) => {
                let current = read_optional(&self.lock_path)
                    .ok()
                    .flatten()
                    .map(|text| text.trim().to_string())
                    .filter(|text| !text.is_empty())
                    .unwrap_or_else(|| "unknown owner".to_string());
                let message = format!(
                    "Embedded {} store is already owned: {}. Current lease: {current}. \
                     Stop that owner process or select a different \
                     CORTEX_STORAGE_INSTANCE/CORTEX_STORAGE_OWNER.",
                    self.backend,
                    self.target.to_string_lossy()
                );
                Err((self, conflict(message, current)))
            }
            Err(err) => Err((
                self,
                conflict(format!("cannot lock lease file: {err}"), String::new()),
            )),
        }
    }

    /// Whether the lease is currently held by this instance.
    pub fn is_acquired(&self) -> bool {
        self.handle.is_some()
    }

    /// Release the lease; safe to call more than once.
    pub fn release(&mut self) {
        let Some(mut handle) = self.handle.take() else {
            return;
        };
        let _ = truncate_file(&mut handle);
        let _ = handle.sync_all();
        let _ = ffi::flock_release(handle.as_raw_fd());
    }

    /// The raw metadata text currently stored in the lock file (stale
    /// metadata included — a lease left behind by a killed process).
    pub fn current_lock_content(&self) -> Option<String> {
        read_optional(&self.lock_path)
            .ok()
            .flatten()
            .map(|text| text.trim().to_string())
            .filter(|text| !text.is_empty())
    }
}

impl Drop for StorageLease {
    fn drop(&mut self) {
        self.release();
    }
}

fn target_name(target: &Path) -> String {
    target
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default()
}

fn write_lease_payload(handle: &mut fs::File, payload: &str) -> io::Result<()> {
    use std::io::{Seek, SeekFrom};
    handle.seek(SeekFrom::Start(0))?;
    handle.set_len(0)?;
    handle.write_all(payload.as_bytes())?;
    handle.flush()?;
    handle.sync_all()
}

fn is_conflict(err: &io::Error) -> bool {
    matches!(
        err.kind(),
        io::ErrorKind::WouldBlock | io::ErrorKind::PermissionDenied | io::ErrorKind::TimedOut
    )
}

/// Assert the previous owner of a target has stopped by briefly acquiring and
/// releasing its lease (`assert_owner_stopped`).
pub fn assert_owner_stopped(
    target: &Path,
    instance_id: &str,
    owner_id: &str,
    backend: &str,
) -> StoreResult<()> {
    let lease = StorageLease::new(target, instance_id, owner_id, backend);
    let mut lease = lease
        .acquire()
        .map_err(|(_, conflict)| StoreError::Runtime(conflict.message))?;
    lease.release();
    Ok(())
}

/// Recovery outcome for a lease left behind by a dead owner.
#[derive(Debug, Clone, PartialEq)]
pub struct LeaseRecovery {
    /// True when the expired lease was reclaimed successfully.
    pub recovered: bool,
    /// Stale lease metadata (JSON text) observed before the reclaim.
    pub stale_metadata: Option<String>,
}

/// Reclaim an expired lease after owner death (kill -9 or crash).
///
/// `flock` ownership is released by the kernel when the owning process dies,
/// so "recovery" is exactly: acquire the lease and overwrite the stale
/// metadata. The stale payload is surfaced for diagnostics before it is
/// replaced — mirroring what the Python layer observes when a new owner
/// acquires a lease abandoned by a killed process.
pub fn recover_expired_leases(
    target: &Path,
    instance_id: &str,
    owner_id: &str,
    backend: &str,
) -> StoreResult<LeaseRecovery> {
    let lease = StorageLease::new(target, instance_id, owner_id, backend);
    let stale_metadata = lease.current_lock_content();
    let mut lease = lease
        .acquire()
        .map_err(|(_, conflict)| StoreError::Runtime(conflict.message))?;
    lease.release();
    Ok(LeaseRecovery {
        recovered: true,
        stale_metadata,
    })
}
