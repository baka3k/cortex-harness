//! analyzer-jvm-overlays — Rust port của 3 framework-overlay JVM analyzers
//! (phase 08 rust-full-migration):
//!
//! * `spring`  → bin `analyzer-spring`      (tools/spring/spring_analyzer.py)
//! * `struts`  → bin `analyzer-struts`      (tools/struts/struts_analyzer.py)
//! * `servlet_jsp` → bin `analyzer-servlet-jsp` (tools/servlet_jsp/)
//!
//! Đặc thù overlay: chạy SAU base java/kotlin analyzer, đọc source + XML config
//! và ghi framework facts lên cùng graph. Parity gate: dual-run Python vs Rust
//! trên 2 graph FalkorDB được seed giống hệt bằng base java analyzer Python
//! (`scripts/rust_parity/analyzer_parity_{spring,struts,servlet_jsp}.py`).

pub mod pyjson;
pub mod pyutil;
pub mod spring;
pub mod struts;
pub mod servlet_jsp;
