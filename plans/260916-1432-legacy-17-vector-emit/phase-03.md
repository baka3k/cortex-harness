# Phase 03 — Orchestrator widening (gate + re-launch short-circuit + semantics)

## Mục tiêu

Orchestrator (`cortex-sync/src/orchestrator.rs`) thay đổi:
1. Gate widening: `SHARED_VECTOR_CLI_PARSERS.contains(...)` → `EMITTING_VECTOR_CLI_PARSERS.contains(...)`
2. Re-launch short-circuit rule (D2) — skip child launch trong embedding pass khi
   primary-pass artifact đã có và còn fresh
3. vector_count semantics (D4) — explicit "0 khi status=success", "disabled-no-emitter" carve-out
4. Flag opt-in `CORTEX_LEGACY_VECTOR_EMIT` (D8) — phase này default off

## Scope

### Patch 1 — Gate widening (`orchestrator.rs:2115-2116`)

```rust
// Trước
let mut native_parser = native_store.is_some()
    && registry::SHARED_VECTOR_CLI_PARSERS.contains(&parser_name);

// Sau
let legacy_emit_enabled = env_flag_truthy("CORTEX_LEGACY_VECTOR_EMIT");
let mut native_parser = native_store.is_some()
    && (
        registry::EMITTING_VECTOR_CLI_PARSERS.contains(&parser_name)
        || (legacy_emit_enabled && legacy_emit_registered(parser_name))
    );
```

`legacy_emit_registered()` check capability table (set ở phase-02). Dart fix R12
bắt buộc trước khi enable legacy_emit (verified ở phase-02 gate).

### Patch 2 — Re-launch short-circuit (D2)

Tại `orchestrator.rs` embedding pass loop (~line 2203), sau khi build `cmd` cho child:

```rust
// Skip child launch nếu incremental + artifact còn fresh
let skip_child = !args.full_scan
    && !args.reconcile
    && let Some(artifact) = &embedding_artifact
    && artifact.exists()
    && artifact_mtime_fresh(artifact, primary_pass_started_at);

if !skip_child {
    let child_result = run_child(&command_vec, &run_cwd, args.verbose, &embedding_env);
    // ... existing handler
} else {
    println!("[embedding] {parser_name}: skipping child launch (primary-pass artifact fresh)");
    // Treat as success; finish_native_embedding_pass sẽ đọc artifact
}
```

### Patch 3 — vector_count semantics (D4)

Tại `orchestrator.rs:2176-2183` + downstream:

```rust
// Default behavior cho legacy carve-out (non-emitter parsers)
vector_info.insert(
    "vector_status".into(),
    json!(if config.writes_vectors {
        if native_parser { "pending" }
        else { "disabled-no-emitter" }  // explicit carve-out
    } else { "disabled" }),
);
vector_info.insert(
    "vector_count".into(),
    json!(if native_parser { Value::Null } else { json!(0) }),
);
```

Sau `finish_native_embedding_pass` (line 2295-2308): nếu `count == 0` và
status="success" → vector_count giữ 0 (không phải null); vector_status="success"
(giữ nguyên).

### Patch 4 — Flag opt-in (D8)

`env.rs` hoặc inline trong orchestrator.rs:
```rust
fn env_flag_truthy(key: &str) -> bool {
    matches!(
        std::env::var(key).unwrap_or_default().to_lowercase().as_str(),
        "1" | "true" | "yes" | "on" | "rust"
    )
}
```

Phase-03 default off. Phase-04 sẽ flip default ON.

### Patch 5 — Cross-update các plan liên quan

Trong `plans/260916-1154-native-vector-ingest-local/plan.md`: thêm 1 dòng
vào §"Phát hiện quyết định" — "F-extension: legacy 17 vector emit
(plan `260916-1432-legacy-17-vector-emit`) extends coverage từ shared-7
sang full 24 parsers; xem phase-03 widening + phase-04 flip."

## Deliverables

- Code changes: `orchestrator.rs` + (potentially) `env.rs`
- Tests:
  - Unit test re-launch short-circuit (artifact fresh → skip; artifact stale → re-launch)
  - Unit test vector_status semantics (legacy carve-out = "disabled-no-emitter")
  - Integration test: procsample `dev sync code` → `vectors_upserted > 0` + Qdrant có
    collections (`procsample_*_functions` cho cplus/java/sql)
- Report: `reports/phase03-parity.md` ghi số vectors_upserted trước/sau, parity
  matrix xanh 2 lần liên tiếp

## Gate

- [ ] Patch 1-4 compile xanh
- [ ] Per-patch unit test PASS
- [ ] `dev sync code --full-scan` ở procsample → `vectors_upserted > 0`
    - Verify bằng `curl http://localhost:6333/collections | jq` thấy collections mới
    - Verify `vector_count` trong `incremental_sync_summaries/*.json` ≠ null
  - [ ] Parity matrix (`scripts/rust_mcp/local_parity_matrix.py`) xanh 2 lần liên tiếp
  - [ ] Rollback leg: `CORTEX_LEGACY_VECTOR_EMIT=python` (unset) → vector_status
    "disabled-no-emitter", vectors_upserted=0 byte-identical pre-plan
  - [ ] Cross-plan update complete (1 commit update 2 plan.md files)

## Rollback

- `CORTEX_LEGACY_VECTOR_EMIT=python` (unset) → opt-in off, legacy behavior restored
- Revert 1 commit (orchestrator patches + cross-plan updates) → byte-identical pre-plan
- Capability table constant revert nếu cần (`SHARED_VECTOR_CLI_PARSERS` alias kept ở phase-01)