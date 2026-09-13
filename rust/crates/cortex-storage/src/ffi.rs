//! Minimal libc FFI surface (flock, statvfs, gethostname).
//!
//! All `unsafe` in this crate is confined to this module and its three
//! callers; the semantics mirror portalocker's flock usage so lease
//! behavior (including automatic release on process death) is identical.

use std::ffi::CStr;
use std::io;
use std::os::unix::io::RawFd;

/// Exclusive, non-blocking `flock(2)`. Returns `Ok(())` when acquired,
/// `Err(kind)` otherwise (`WouldBlock` when another process holds the lock).
pub fn flock_exclusive_nonblocking(fd: RawFd) -> io::Result<()> {
    let rc = unsafe { libc::flock(fd, libc::LOCK_EX | libc::LOCK_NB) };
    if rc == 0 {
        return Ok(());
    }
    Err(io::Error::last_os_error())
}

/// Release a `flock(2)` previously taken on `fd`.
pub fn flock_release(fd: RawFd) -> io::Result<()> {
    let rc = unsafe { libc::flock(fd, libc::LOCK_UN) };
    if rc == 0 {
        return Ok(());
    }
    Err(io::Error::last_os_error())
}

/// Free bytes available on the filesystem containing `path` (statvfs).
pub fn disk_free_bytes(path: &std::path::Path) -> io::Result<u64> {
    let c = std::ffi::CString::new(path.as_os_str().to_string_lossy().as_bytes())
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "path contains NUL"))?;
    let mut stat: libc::statvfs = unsafe { std::mem::zeroed() };
    let rc = unsafe { libc::statvfs(c.as_ptr(), &mut stat) };
    if rc != 0 {
        return Err(io::Error::last_os_error());
    }
    let free = stat.f_bavail as u64 * stat.f_frsize as u64;
    Ok(free)
}

/// POSIX host name (`socket.gethostname()` equivalent).
pub fn hostname() -> String {
    let mut buf = vec![0u8; 256]; // MAXHOSTNAMELEN
    let rc = unsafe { libc::gethostname(buf.as_mut_ptr().cast(), buf.len()) };
    if rc != 0 {
        return String::from("unknown");
    }
    let c = unsafe { CStr::from_ptr(buf.as_ptr().cast()) };
    c.to_string_lossy().into_owned()
}
