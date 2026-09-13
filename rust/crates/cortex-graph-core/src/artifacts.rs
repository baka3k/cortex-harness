//! Port `code-tiny/tools/graph/journal/artifacts.py` — ArtifactStore
//! (write_jsonl / verify / read_jsonl / path_for).
//!
//! Lệch có chủ ý: bỏ filesystem-type probing (9p/nfs fail-closed placement
//! check) — tạo file lỗi tự surface qua OS errors; bổ sung sandbox
//! platform-specific nếu cần sau.

use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::identity::{canonical_json, sha256_hex};
use crate::models::{ArtifactRef, JournalError, TerminalErrorCode};

pub struct ArtifactStore {
    pub root: PathBuf,
    pub max_artifact_bytes: i64,
    pub min_free_bytes: i64,
}

impl ArtifactStore {
    pub fn new(root: &Path, max_artifact_bytes: i64, min_free_bytes: i64) -> Self {
        Self {
            root: root.to_path_buf(),
            max_artifact_bytes,
            min_free_bytes,
        }
    }

    fn run_directory(&self, run_id: &str) -> PathBuf {
        let dir = self.root.join(run_id);
        let _ = std::fs::create_dir_all(&dir);
        dir
    }

    /// `write_jsonl` — canonical JSON per row + newline, content-addressed
    /// filename `<sha256>.jsonl`, chmod 0o400 sau khi rename.
    pub fn write_jsonl(
        &self,
        run_id: &str,
        rows: &[Value],
    ) -> Result<ArtifactRef, JournalError> {
        let run_dir = self.run_directory(run_id);
        let temp = run_dir.join(format!(
            ".payload.{}.{}.tmp",
            std::process::id(),
            sha256_hex(format!("{:?}", std::time::SystemTime::now()).as_bytes())
                .get(..16)
                .unwrap_or("nonce")
        ));
        let byte_limit = self.max_artifact_bytes;
        let mut hasher = Sha256::new();
        let mut byte_count: i64 = 0;
        let mut row_count: i64 = 0;
        let mut payload: Vec<u8> = Vec::new();
        for row in rows {
            let mut encoded = canonical_json(row);
            encoded.push(b'\n');
            byte_count += encoded.len() as i64;
            if byte_count > byte_limit {
                let code = if byte_count > self.max_artifact_bytes {
                    TerminalErrorCode::AdmissionRejected
                } else {
                    TerminalErrorCode::DiskFull
                };
                return Err(JournalError::new(
                    code,
                    "artifact exceeds its bounded storage admission",
                ));
            }
            hasher.update(&encoded);
            payload.extend_from_slice(&encoded);
            row_count += 1;
        }
        // Lưu ý: hex của digest — KHÔNG double-hash qua sha256_hex.
        let digest: String = hasher.finalize().iter().map(|b| format!("{b:02x}")).collect();
        let target = run_dir.join(format!("{digest}.jsonl"));
        let relative = target
            .strip_prefix(&self.root)
            .map_err(|_| {
                JournalError::new(
                    TerminalErrorCode::UnsafePlacement,
                    "artifact reference escapes the artifact root",
                )
            })?
            .to_string_lossy()
            .to_string();
        let reference = ArtifactRef {
            sha256: digest.clone(),
            relative_path: relative,
            byte_count,
            row_count,
        };
        if target.exists() {
            self.verify(&reference)?;
        } else {
            std::fs::write(&temp, &payload)
                .map_err(|e| os_error("cannot persist journal artifact", &e))?;
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                std::fs::set_permissions(&temp, std::fs::Permissions::from_mode(0o400))
                    .map_err(|e| os_error("cannot persist journal artifact", &e))?;
            }
            std::fs::rename(&temp, &target)
                .map_err(|e| os_error("cannot persist journal artifact", &e))?;
        }
        Ok(reference)
    }

    pub fn path_for(&self, reference: &ArtifactRef) -> Result<PathBuf, JournalError> {
        let path = self.root.join(&reference.relative_path);
        if !path.starts_with(&self.root) {
            return Err(JournalError::new(
                TerminalErrorCode::UnsafePlacement,
                "artifact reference escapes the artifact root",
            ));
        }
        Ok(path)
    }

    /// `verify` — file tồn tại, size + sha256 khớp ref.
    pub fn verify(&self, reference: &ArtifactRef) -> Result<PathBuf, JournalError> {
        let path = self.path_for(reference)?;
        let bytes = std::fs::read(&path).map_err(|e| {
            JournalError::new(
                TerminalErrorCode::ArtifactHashMismatch,
                format!("cannot read artifact {}: {e}", reference.relative_path),
            )
        })?;
        if bytes.len() as i64 != reference.byte_count {
            return Err(JournalError::new(
                TerminalErrorCode::ArtifactHashMismatch,
                format!(
                    "artifact byte_count mismatch for {}: file={} ref={}",
                    reference.relative_path,
                    bytes.len(),
                    reference.byte_count
                ),
            ));
        }
        let digest = sha256_hex(&bytes);
        if digest != reference.sha256 {
            return Err(JournalError::new(
                TerminalErrorCode::ArtifactHashMismatch,
                format!("artifact sha256 mismatch for {}", reference.relative_path),
            ));
        }
        Ok(path)
    }

    /// `read_jsonl` — verify trước rồi parse từng dòng.
    pub fn read_jsonl(&self, reference: &ArtifactRef) -> Result<Vec<Value>, JournalError> {
        let path = self.verify(reference)?;
        let bytes = std::fs::read(&path).map_err(|e| {
            JournalError::new(
                TerminalErrorCode::JournalCorrupt,
                format!("cannot read artifact: {e}"),
            )
        })?;
        let mut rows = Vec::new();
        for line in bytes.split(|b| *b == b'\n') {
            if line.is_empty() {
                continue;
            }
            let value: Value = serde_json::from_slice(line)
                .map_err(|e| JournalError::new(TerminalErrorCode::JournalCorrupt, e.to_string()))?;
            rows.push(value);
        }
        Ok(rows)
    }
}

fn os_error(message: &str, error: &std::io::Error) -> JournalError {
    JournalError::new(TerminalErrorCode::InvalidContract, format!("{message}: {error}"))
}

use sha2::{Digest, Sha256};
