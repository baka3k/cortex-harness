//! Lỗi của embedder — cùng shape với `ProviderError` ở cortex-doc để caller không
//! phải rẽ nhánh theo loại backend.

use std::fmt;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EmbedError(pub String);

impl EmbedError {
    pub fn new(message: impl Into<String>) -> Self {
        EmbedError(message.into())
    }
}

impl fmt::Display for EmbedError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl std::error::Error for EmbedError {}

impl From<std::io::Error> for EmbedError {
    fn from(value: std::io::Error) -> Self {
        EmbedError(value.to_string())
    }
}

impl From<serde_json::Error> for EmbedError {
    fn from(value: serde_json::Error) -> Self {
        EmbedError(value.to_string())
    }
}

impl From<ort::Error> for EmbedError {
    fn from(value: ort::Error) -> Self {
        EmbedError(value.to_string())
    }
}

impl From<tokenizers::Error> for EmbedError {
    fn from(value: tokenizers::Error) -> Self {
        EmbedError(value.to_string())
    }
}

pub type Result<T> = std::result::Result<T, EmbedError>;
