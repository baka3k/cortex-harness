//! analyzer-cobol library surface — để integration tests import các module
//! parser/runtime; binary `analyzer-cobol` dùng lib này.

pub mod analyzer;
pub mod cfg;
pub mod codec_tables;
pub mod models;
pub mod parser;
pub mod parser_runtime;
pub mod pipeline;
pub mod pycompat;
pub mod resolver;
pub mod semantics;
