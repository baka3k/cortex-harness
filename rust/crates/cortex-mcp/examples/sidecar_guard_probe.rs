//! Sidecar-guard probe for the phase-05 rollback drill
//! (plans/260916-1154-native-vector-ingest-local). Prints the outcome of the
//! code-lane sidecar call against the store root given as argv[1] — the
//! quarantined-root loud refusal lives in `vector_sidecar::run_request`.

use std::path::Path;

fn main() {
    let store = std::env::args()
        .nth(1)
        .expect("usage: sidecar_guard_probe <store_root>");
    let outcome = match cortex_mcp::vector_sidecar::list_collections(Path::new(&store)) {
        Ok(names) => serde_json::json!({"ok": true, "collections": names}),
        Err(error) => serde_json::json!({"ok": false, "error": error}),
    };
    print!("{outcome}");
}