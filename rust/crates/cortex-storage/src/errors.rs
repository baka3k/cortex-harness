//! Shared storage error types.
//!
//! Centralizes errors raised by the backend adapters so callers (MCP tools,
//! ingest scripts, the `make doctor` check) see a single set of typed errors
//! regardless of which backend is in use — mirroring
//! `cortex_harness.storage.errors`.

use std::fmt;

/// A remote storage backend is unreachable.
///
/// Raised when a remote Qdrant server or a remote FalkorDB-compatible server
/// cannot be reached. The message is actionable and names the URL plus the
/// underlying cause.
#[derive(Debug, Clone)]
pub struct BackendConnectionError {
    pub backend: String,
    pub url: String,
    pub cause: Option<String>,
}

impl BackendConnectionError {
    pub fn new(backend: &str, url: &str, cause: Option<String>) -> Self {
        Self {
            backend: backend.to_string(),
            url: url.to_string(),
            cause,
        }
    }
}

impl fmt::Display for BackendConnectionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match &self.cause {
            Some(cause) => write!(
                f,
                "{} server at {} is unreachable: {cause}. Check that the server is \
                 running and the URL is correct.",
                self.backend, self.url
            ),
            None => write!(
                f,
                "{} server at {} is unreachable. Check that the server is running \
                 and the URL is correct.",
                self.backend, self.url
            ),
        }
    }
}

impl std::error::Error for BackendConnectionError {}

/// Unified storage-layer error mirroring the Python exception families:
/// `ValueError`, `RuntimeError`, `OSError`, `StoreGatewayError`, and
/// `BackendConnectionError`.
#[derive(Debug)]
pub enum StoreError {
    /// Python `ValueError` (configuration/identity/transition errors).
    Value(String),
    /// Python `RuntimeError` (lease conflicts, gateway misuse).
    Runtime(String),
    /// Python `OSError` / `IOError` family.
    Io(std::io::Error),
    /// Structured gateway error with a stable code.
    Gateway(crate::contracts::StoreGatewayError),
    /// Remote backend unreachable.
    Connection(BackendConnectionError),
}

impl StoreError {
    /// Python `type(exc).__name__` equivalent, used for probe diagnostics.
    pub fn kind_name(&self) -> &'static str {
        match self {
            StoreError::Value(_) => "ValueError",
            StoreError::Runtime(_) => "RuntimeError",
            StoreError::Io(_) => "OSError",
            StoreError::Gateway(_) => "StoreGatewayError",
            StoreError::Connection(_) => "BackendConnectionError",
        }
    }

    /// The stable gateway error code, when this is a gateway error.
    pub fn gateway_code(&self) -> Option<crate::contracts::GatewayErrorCode> {
        match self {
            StoreError::Gateway(err) => Some(err.code),
            _ => None,
        }
    }

    /// The gateway error code as a string (empty when not a gateway error).
    pub fn gateway_code_str(&self) -> Option<&'static str> {
        self.gateway_code().map(|code| code.as_str())
    }
}

impl fmt::Display for StoreError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            StoreError::Value(message) => write!(f, "{message}"),
            StoreError::Runtime(message) => write!(f, "{message}"),
            StoreError::Io(err) => write!(f, "{err}"),
            StoreError::Gateway(err) => write!(f, "{err}"),
            StoreError::Connection(err) => write!(f, "{err}"),
        }
    }
}

impl std::error::Error for StoreError {}

impl From<std::io::Error> for StoreError {
    fn from(err: std::io::Error) -> Self {
        StoreError::Io(err)
    }
}

impl From<crate::contracts::StoreGatewayError> for StoreError {
    fn from(err: crate::contracts::StoreGatewayError) -> Self {
        StoreError::Gateway(err)
    }
}

impl From<BackendConnectionError> for StoreError {
    fn from(err: BackendConnectionError) -> Self {
        StoreError::Connection(err)
    }
}

/// Convenience alias mirroring Python callers that expect typed errors.
pub type StoreResult<T> = Result<T, StoreError>;
