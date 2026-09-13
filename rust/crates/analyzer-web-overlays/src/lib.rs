//! analyzer-web-overlays — Rust port của 5 web framework-overlay analyzers
//! (phase 08 rust-full-migration):
//!
//! * `fastapi_django`    → bin `analyzer-fastapi-django` (tools/web_framework/)
//! * `express_js`        → bin `analyzer-express-js`      (tools/web_framework/)
//! * `laravel`           → bin `analyzer-laravel`        (tools/web_framework/)
//! * `aspnet_framework`  → bin `analyzer-aspnet-framework` (tools/aspnet_framework/)
//! * `aspnet_core`       → bin `analyzer-aspnet-core`    (tools/aspnet_core/)
//!
//! Đặc thù overlay: chạy SAU base language analyzer (python/js/php/csharp),
//! đọc source + config artifact và ghi framework facts lên cùng graph. Điểm
//! semantic duy nhất: 2 overlay ASP.NET gọi ASP.NET Roslyn worker (dotnet) —
//! cùng worker phía Python dùng nên evidence byte-identical. Parity gate:
//! dual-run Python vs Rust trên 2 graph FalkorDB được seed giống hệt bằng base
//! python analyzer (`scripts/rust_parity/analyzer_parity_<key>.py`).

pub mod pyjson;
pub mod pyrepr;
pub mod pyutil;
pub mod xmlmini;

pub mod aspnet;
pub mod aspnet_cli;
pub mod web;
pub mod web_cli;
