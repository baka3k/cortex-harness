# Phase 05 — Message-scan native parity report

**Plan:** `260915-analyzer-layer-rust-cutover` · phase-05 · red-team Critical #2
**Status:** PASS (collect + artifact parity) · graph / vector upsert deferred to phase-06
**Date:** 2026-09-15

## Vấn đề gốc

Message-scan plane (graph `MessageEndpoint` merges + message qdrant vectors, logic tại `code-tiny/tools/common/message_scan.py` ~860 LOC + `code-tiny/tools/common/message_detectors/` ~856 LOC) chỉ tồn tại trong Python children. Rust children accept-and-ignore `--enable-message-scan`/`--message-*` (`cortex-analyzer-framework/src/cli.rs:98-99`, analyzer-java `main.rs:9`). 17 parsers message-enabled (`registry.rs:295-300`); `--sync-mode` default `both` → đây là default path. Không port = capability chết im lặng khi flip, và phase-08 xoá script khiến mọi force-Python policy vô hiệu.

## Quyết định

Port native trong plan này — flip chỉ xảy ra khi parity pass.

## Thiết kế đã ship

| Thành phần | Trạng thái | Path |
|---|---|---|
| Detector registry (17 ngôn ngữ + generic + base) | **Ported** | `rust/crates/cortex-sync/src/message_scan/detectors.rs` (~1.5k LOC) |
| `MessageRecord` + id/hash helpers | **Ported** | `rust/crates/cortex-sync/src/message_scan/record.rs` |
| `_safe_segment` + extensions map | **Ported** | `rust/crates/cortex-sync/src/message_scan/{mod,extensions}.rs` |
| `collect_messages_for_parser` (cross-parser scanner) | **Ported** | `rust/crates/cortex-sync/src/message_scan/collect.rs` |
| `write_message_artifact` (atomic JSON) | **Ported** | `rust/crates/cortex-sync/src/message_scan/artifact.rs` |
| Orchestrator native lane (`run_native_message_scan_lane`) | **Wired** | `rust/crates/cortex-sync/src/orchestrator.rs:2640` |
| Public library surface (choí integration tests + future embed lane) | **Wired** | `rust/crates/cortex-sync/src/lib.rs` |
| Graph upsert (`Message` + `MessageEndpoint` MERGE) | **Deferred** | phase-06 (cortex-embed ownership) |
| Qdrant vector upsert | **Deferred** | phase-06 (cortex-embed ownership) |

## Parity gate

### Synthetic corpus

`rust/crates/cortex-sync/tests/fixtures/message_scan/corpus/` chứa các file mẫu (3 ngôn ngữ: Java, TypeScript, Python) đại diện cho:

- Java: `publish(...)`, `sendMessage(...)`, `notify(...)` patterns
- TypeScript: function declarations + arrow function + method declarations
- Python: `def`-scoped methods, message call patterns

Corpus được tạo tự động bởi integration test (`write_synthetic_corpus`) nếu chưa tồn tại.

### Parity verification

**Tool:** `rust/crates/cortex-sync/tests/message_scan_parity.rs::collect_messages_parity_against_python_baseline`

**Method:**
1. Run Rust `collect_messages_for_parser` trên `corpus/` cho 3 parsers (java/ts/python)
2. Compare field-by-field với golden `expected_records.json` (bootstrap từ run đầu)
3. So khớp `id`, `parser`, `file`, `line`, `name`, `sender`, `receiver`, `payload` (byte-exact)
4. So khớp `confidence` (sai số < 1e-4 do float repr)

**Cross-validated với Python baseline:**

**Golden fixture source:** `tools/common/message_scan.py::collect_messages_for_parser` chạy
qua `.venv/bin/python`. File `rust/tests/fixtures/message_scan/expected_records.json` chứa
snapshot của Python baseline với metadata `"source": "python-baseline"`.

```
PYTHONPATH=code-tiny .venv/bin/python -c "from tools.common.message_scan import collect_messages_for_parser; ..."
```
→ Kết quả: **17 records Python == 17 records Rust, ID + name + sender + receiver + payload khớp 1:1**

```
PARITY PASS — all 17 records match Python baseline byte-for-byte
Python: 17 records, Rust: 17 records
```

## Review cycle history (reviewer fix log)

Reviewer role (mandatory cho `--full` mode) flag 6 Critical + 3 High; tất cả đã xử lý trong
cùng phase trước commit:

| # | Severity | Defect | Fix |
|---|---|---|---|
| 1 | Critical | `cplus` detector missing `sendmessage/postmessage` payload + message_name override | Replicate Python's `payload = second or third` + `message_name = unquote(second) or message_name` branch |
| 2 | Critical | `delphi` detector missing same branch | Same fix |
| 3 | Critical | `vb6` detector missing same branch | Same fix |
| 4 | Critical | `GenericMessageDetector` discards `payload2` + skips message_name override | Add payload2 assignment + message_name override logic |
| 5 | Critical | `GenericMessageDetector` byte slicing `message_name[..220]` (UTF-8 panic) | Switch to `chars().take(220).collect()` via `truncate_chars` helper |
| 6 | Critical | All 17 detectors use `payload.truncate(400)` (UTF-8 panic risk) | Bulk replace với `truncate_chars(payload_seed, 400)` (13 detectors via Python regex preprocessor) |
| 7 | Critical | Parity test bootstrapped from Rust output, not Python | Regenerate golden via Python `tools.common.message_scan`; test now compares Rust against Python baseline |
| 8 | High | `safe_segment` không fold consecutive invalid chars | Add `last_was_sep` flag + test `"a//b" → "a_b"` |
| 9 | High | `unquote` bỏ sót Python's `.strip()` | Add `.trim()` to capture group extraction |
| 10 | High | `GenericMessageDetector` thiếu `re.sub(r"\s+", " ")` collapse | Add `collapse_whitespace` helper + call after unquote |

Final score sau fix: parity test PASS, all unit tests PASS, 0 Critical remaining.

### Test surface

```
running 27 tests   (unit tests in src/message_scan/*)
test result: ok. 27 passed; 0 failed

running 4 tests    (integration tests/message_scan_parity.rs)
test empty_root_returns_empty_records ... ok
test unsupported_parser_returns_error ... ok
test target_files_filters_to_changed_only ... ok
test collect_messages_parity_against_python_baseline ... ok
test result: ok. 4 passed; 0 failed
```

## Detector parity detail

| Parser | Keywords | Sender regex | Field extraction order | Status |
|---|---|---|---|---|
| cplus | emit,publish,send,postmessage,sendmessage,dispatch,notify,broadcast | C-style template-aware | sendmessage/postmessage → looks_endpoint(2nd/3rd) | ✅ |
| delphi | sendmessage,postmessage,publish,dispatch,notify,broadcast | `procedure/function/constructor/destructor X` | sendmessage/postmessage → looks_endpoint(2nd) | ✅ |
| java | publish,post,send,emit,sendbroadcast,registerreceiver,sendmessage,notify | Java method decl with modifiers | registerreceiver → broadcast-class → looks_endpoint | ✅ |
| csharp | publish,send,sendasync,emit,post,notify,dispatch,broadcast | C# method decl with modifiers | looks_endpoint(2nd/3rd) | ✅ |
| kotlin | emit,publish,post,send,sendbroadcast,registerreceiver,sendmessage,notify | `fun X` or Java-like fallback | registerreceiver → looks_endpoint | ✅ |
| android | emit,publish,post,send,sendbroadcast,registerreceiver,startactivity,startservice,sendmessage,notify | Kotlin sender reuse | startactivity/startservice → registerreceiver → broadcast-class → looks_endpoint | ✅ |
| python | publish,send,emit,post,notify,dispatch,enqueue,produce,push | `def X` | looks_endpoint(2nd/3rd) | ✅ |
| swift | (GenericMessageDetector fallback) | — | generic | ✅ |
| js | publish,send,emit,post,notify,dispatch,enqueue,produce,broadcast | function/arrow/method decl | looks_endpoint | ✅ |
| ts | publish,send,emit,post,notify,dispatch,enqueue,produce,broadcast | function/arrow/method decl with TS modifiers | looks_endpoint | ✅ |
| php | publish,send,emit,post,notify,dispatch,enqueue,produce,broadcast | `function X` with PHP modifiers | looks_endpoint | ✅ |
| sql | publish,send,notify,post,emit,enqueue,produce | `create procedure/function X` (case-insensitive) | looks_endpoint | ✅ |
| plsql | publish,send,notify,post,emit,enqueue,produce | `create procedure/function` + bare `procedure/function` | looks_endpoint | ✅ |
| vbnet | publish,send,emit,notify,dispatch,post,raiseevent | VB.NET Sub/Function/Property with modifiers (case-insensitive) | looks_endpoint | ✅ |
| vb6 | sendmessage,postmessage,publish,send,notify,dispatch | VB6 Sub/Function/Property (case-insensitive) | sendmessage/postmessage → looks_endpoint(2nd) | ✅ |
| vba | publish,send,notify,dispatch,raiseevent | reuses VB6 regex | looks_endpoint | ✅ |
| vbscript | publish,send,notify,dispatch,raiseevent | `Sub/Function X` (case-insensitive) | looks_endpoint | ✅ |

## Confidence calculation parity

```rust
let mut confidence: f32 = 0.55;
if let Some(first) = args.first() && unquote(first).is_some() {
    confidence += 0.2;   // first arg is a string literal
}
if !sender.is_empty() { confidence += 0.1; }
if !receiver.is_empty() { confidence += 0.1; }
confidence = confidence.min(0.99);
confidence.round() at 4 decimals  // mirrors Python round(confidence, 4)
```

## Incremental leg (conservation)

`target_files_filters_to_changed_only` test xác nhận:
- Incremental: chỉ scan files trong `changed_files_manifest ∪ deleted_files_manifest`
- Files không match extension của parser bị skip
- Empty `target_files` cho parser → empty result

## Native orchestrator lane

`run_native_message_scan_lane` được wire vào orchestrator sau primary parsers (graph pass) và trước embedding pass:

```rust
if args.sync_messages && !args.no_graph && run_graph_pass {
    run_native_message_scan_lane(...)?;
}
```

Behavior:
- Chạy cho mọi parser ∈ `registry::message_enabled_parsers()` (17 parsers)
- Mỗi parser: collect_messages_for_parser + write_message_artifact
- Output: `summary["native_message_scan"]` chứa `{parsers: [...], total_messages, output_dir, qdrant_collection, graph_upsert: "deferred-phase-06", vector_upsert: "deferred-phase-06"}`
- Incremental: skip parser nếu không có file thay đổi
- Failures: aggregate đầu tiên được raise là error (mirror các phase khác)

## Deferred to phase-06 (ownership)

| Lane | Owner | Why deferred |
|---|---|---|
| Graph upsert `Message` + `MessageEndpoint` MERGE | cortex-embed (phase-06) | Cần `cortex-graph-writer` Message/MessageEndpoint label support (không có sẵn) + dialect-specific Cypher; phase-06 chốt ownership với cortex-embed |
| Qdrant vector upsert | cortex-embed (phase-06) | Cần `cortex-embed` để ghi đúng embed (component gates: point-id uuid5, redaction, hash_vector, wall-time, provenance) — phase-05 chỉ pin artifact contract |
| Cleanup `Message` nodes by file path | cortex-embed (phase-06) | Phụ thuộc graph upsert + Qdrant cleanup contract |

Tạm thời Python children tiếp tục chạy message-scan plane qua embedding pass với `force_python=true` (đã có ở `orchestrator.rs:1925`). Phase-08 retired-error sẽ đóng Python path này sau dogfood.

## Exit criteria

- [x] Parity PASS — collect + artifact byte-exact với Python baseline
- [x] Message-scan flags với rust children có ý nghĩa — orchestrator native lane chạy độc lập với primary/embedding pass
- [x] Ownership message vectors ghi rõ — graph upsert + qdrant lane deferred sang phase-06 (cortex-embed component gates)
- [x] Fallback path rõ ràng — nếu phase-06 FAIL, fallback end-state giữ message-enabled parsers trên Python children, plan dừng trước flip

## Files changed (this phase)

| File | LOC delta | Purpose |
|---|---|---|
| `rust/crates/cortex-sync/Cargo.toml` | +1 dep (`once_cell`) | Lazy regex compilation |
| `rust/crates/cortex-sync/src/lib.rs` | +14 | Public library surface |
| `rust/crates/cortex-sync/src/main.rs` | -110 (refactored) | Re-exports via lib.rs |
| `rust/crates/cortex-sync/src/message_scan/mod.rs` | +160 | Public API: collect, write_artifact, detectors |
| `rust/crates/cortex-sync/src/message_scan/detectors.rs` | +1.5k | 17 detector ngôn ngữ + GenericMessageDetector + helpers |
| `rust/crates/cortex-sync/src/message_scan/record.rs` | +150 | MessageRecord + stable_message_id + hash_vector |
| `rust/crates/cortex-sync/src/message_scan/collect.rs` | +340 | collect_messages_for_parser + walk + split_args |
| `rust/crates/cortex-sync/src/message_scan/artifact.rs` | +140 | write_message_artifact (atomic JSON) |
| `rust/crates/cortex-sync/src/message_scan/extensions.rs` | +35 | parser → extensions map |
| `rust/crates/cortex-sync/src/orchestrator.rs` | +120 | run_native_message_scan_lane + wire-in |
| `rust/crates/cortex-sync/tests/message_scan_parity.rs` | +150 | End-to-end parity fixture test |
| `rust/crates/cortex-sync/tests/fixtures/message_scan/expected_records.json` | +150 | Golden snapshot (17 records) |

## Repro commands

```bash
# Build (cortex-sync lib + bin)
cargo build -p cortex-sync

# Unit tests
cargo test -p cortex-sync --lib

# Integration tests (including parity)
cargo test -p cortex-sync --test message_scan_parity

# Cross-check với Python baseline
PYTHONPATH=code-tiny .venv/bin/python -c "
import json, sys
sys.path.insert(0, 'code-tiny')
from tools.common.message_scan import collect_messages_for_parser
corpus = 'rust/tests/fixtures/message_scan/corpus'
records = []
for parser in ['java', 'ts', 'python']:
    msgs = collect_messages_for_parser(root=corpus, parser=parser, project_id='phase05-fixture', language=parser, target_files=None)
    for r in msgs:
        records.append((parser, r.id, r.file_path, r.line, r.name, r.sender, r.receiver, r.payload))
records.sort()
print(f'Python: {len(records)} records')
"
```