//! Public library surface — exposes the message-scan module to integration
//! tests and external crates. The CLI binary entry point lives in
//! `main.rs` and continues to use the modules via `cortex_sync::xxx`.

pub mod cli;
pub mod frameworks;
pub mod gitdiff;
pub mod graphops;
pub mod hash_vector;
pub mod inventory;
pub mod journalenv;
pub mod journal_replay;
pub mod message_scan;
pub mod orchestrator;
pub mod registry;
#[cfg(test)]
mod registry_tests;
pub mod routing;
pub mod state;
pub mod syncscope;
pub mod tsdetect;
pub mod util;
#[cfg(test)]
mod vector_fixtures;
pub mod vector_store;
pub mod vector_sync;
pub mod walk;