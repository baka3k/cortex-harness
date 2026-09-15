# Phase 01 — Registry wiring + bug fix (KHÔNG đổi default)

> **Status 2026-09-15: DONE — PASS toàn bộ gate.** Unit 11/11 + clippy sạch + workspace check +
> 6 smoke end-to-end trên falkordb/qdrant thật (chi tiết mục Smoke đã chạy cuối file).
> Deviation 1: framework map ở phase này có **11/12 entry** — `flutter` không khai báo trước vì
> `analyzer-dart` chưa tồn tại; khai báo sớm sẽ kích hoạt hard-error `=rust` cho dart repo.
> flutter map + binary đổ cùng lúc ở phase-02.

## Mục tiêu

`cortex-sync` có map ĐỦ (22 primary + 12 overlays + topology) và flip matrix phase-14 đầy đủ — nhưng **default không đổi**: unset vẫn → Python. Chỉ opt-in `CORTEX_RUST_ANALYZER=rust` kích hoạt binary. Không có cửa sổ mất vector/message cho default path (red-team finding 3).

## Scope code

| File | Thay đổi |
|---|---|
| `rust/crates/cortex-sync/src/registry.rs` | `rust_analyzer_binaries()`: 7 → 22 entries (tên [[bin]] byte-match `incremental_sync.py:1360-1383`); `framework_rust_binaries() -> BTreeMap<&str, (&str bin, &[&str] extra_args)>` (12 entries; underscore→dash cho servlet_jsp; shared analyzer-database-schema + `--dialect`); topology policy hook |
| `rust/crates/cortex-sync/src/registry.rs` | **.exe probing** (red-team finding 4): resolve binary thêm `.exe` trên Windows (`bin_dir.join(name).with_extension("exe")` pattern) — cả `is_file()` check |
| `rust/crates/cortex-sync/src/orchestrator.rs` | Overlay spawn (`:1669`): giữ `extra_args` từ `framework_config` (sửa bug `with_script` drop); overlay đi qua cùng flip resolution như primary; embedding pass + message-scan flags **pin Python children explicit** khi opt-in rust (chỉ đến phase-05/06 thay thế) |
| `rust/crates/cortex-sync/src/cli.rs` | `--python-bin` giữ nguyên = rollback seam |
| Contract freeze (thiết kế, chưa code) | Embedding-input artifact: format, vị trí cache, **permission 0600** (red-team S6), version field — dùng chung qua `cortex-analyzer-framework`; phase-02→06 build theo contract này |

## Flip matrix (trong giai đoạn 01–07; phase-08 đổi cột default)

| `CORTEX_RUST_ANALYZER` | binary có | binary thiếu |
|---|---|---|
| unset | **Python script** (default giữ nguyên) | Python script |
| `rust` | Rust binary | **hard error** + hint build |
| `python` | Python script (rollback) | Python script |
| giá trị khác | Python script | Python script |

(Post-phase-08: unset → Rust-if-binary; mọi row non-Rust + missing-binary → loud "retired" error — xem phase-08.)

## Tasks

- [ ] Extend map 22 + framework map 12 + topology hook; unit test mỗi entry trỏ đúng [[bin]]
- [ ] .exe probing + unit test (`#[cfg(windows)]`-aware, test được trên macOS qua abstraction)
- [ ] Flip matrix + unit test đủ 8 ô (`CORTEX_RUST_ANALYZER_BIN_DIR` override gồm)
- [ ] Overlay extra_args fix + unit test: cmd line chứa `--framework <val>` / `--dialect <val>` cho 5 overlays từng crash
- [ ] Embedding pass + message lane pin Python-children khi `=rust` — **unit test chứng minh pin thắng flip resolution** (red-team S1)
- [ ] `cargo test -p cortex-sync` + clippy `-D warnings`

## Exit criteria

- Opt-in smoke (`=rust`) trên falkordb: graph parsers là binary; embedding pass + message lane vẫn Python children; **qdrant-enabled assertion: `vector_status=success` + `vector_count > 0`** (red-team S1 gate).
- Default smoke (unset): hành vi KHỐNG đổi — toàn bộ children Python như trước phase.
- Overlay cmd test pass (không exit(2)).

## Smoke đã chạy (2026-09-15, corpus /tmp/cortex-sync-smoke: hello.py, hello.js, Main.java, api.py=FastAPI)

| Run | Env | Kết quả |
|---|---|---|
| A graph pass, unset | default | java child = `python` ✓ default không đổi |
| B graph pass, `=rust` | opt-in | java child = `analyzer-java` ✓ |
| C `=rust` + BIN_DIR rỗng | hard-error cell | **exit 2** + `[registry] ... build it with: cargo build --release -p analyzer-java` ✓ (non-retryable theo retry loop cortex-dev) |
| D/E overlay `=rust` | crash-fix | `fastapi_django` overlay spawn `analyzer-fastapi-django` **kèm `--framework fastapi_django`**, status success ✓ |
| F overlay unset | python path | overlay spawn python **kèm `--framework`** ✓ (fix cả 2 backend) |
| G `=rust` + qdrant | pin gate | graph child `analyzer-python` ✓; embedding child `python` ✓; `vector_status=success`; **qdrant collection `smoke-g_...__python_functions` = 6 points, green** ✓ |

Tests: `cargo test -p cortex-sync` 11/11 (flip matrix 8 ô, .exe probe bare/exe/both/empty-err, map parity 22+11, force_python pin, overlay extra_args cả 2 backend). `cargo clippy -p cortex-sync --all-targets` sạch. `cargo check --workspace` pass.

Ghi chú implementation: `unsafe_code = "deny"` chuyển từ `[lints]` Cargo.toml về source-level attr trong `main.rs` (Cargo render lints ra command line nên override mọi in-code attr; test builds cần lift để set env var).
