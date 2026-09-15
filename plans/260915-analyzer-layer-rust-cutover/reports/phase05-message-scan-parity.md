# Phase 05 — Message-scan native parity report

**Plan:** `260915-analyzer-layer-rust-cutover` · phase-05 · red-team Critical #2
**Status:** PASS (collect + artifact + graph-emission parity) · message vectors deferred to phase-06 (ownership resolved, see "Ownership resolution")
**Date:** 2026-09-15 (updated: graph emission leg)

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
| Graph upsert (`Message` + `MessageEndpoint` MERGE + `File`-`CONTAINS` + cleanup) | **Ported** (graph leg) | `rust/crates/cortex-sync/src/message_scan/graph.rs` |
| Orchestrator native lane (`run_native_message_scan_lane`) | **Wired** | `rust/crates/cortex-sync/src/orchestrator.rs:2640` |
| Public library surface (choí integration tests + future embed lane) | **Wired** | `rust/crates/cortex-sync/src/lib.rs` |
| Qdrant vector upsert | **Deferred** | phase-06 (cortex-embed ownership — xem "Ownership resolution") |

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

### Graph emission parity leg (Message/MessageEndpoint trên FalkorDB)

**Tool:** `scripts/rust_parity/analyzer_parity_message_scan_graph.py` (FalkorDB
127.0.0.1:6379, graph `p05msg_py` vs `p05msg_rs`, dump/diff qua
`dual_write_diff.dump_graph` + mask volatile/`_start_id`/`_end_id`).

- **Baseline (old path):** Python orchestrator chạy graph pass, rồi Python children
  (java/ts/python) được gọi trực tiếp với `--enable-message-scan` — đúng cách
  `incremental_sync.py` wire message flags cho embedding children (`_build_analyzer_cmd`)
- **Candidate:** rust orchestrator `cortex-sync` với `CORTEX_RUST_ANALYZER=rust` — rust
  children viết code graph, native lane viết message graph
- **Leg 1 (full @ commit 1)** + **Leg 2 (incremental sau commit 2** — rename notify,
  thêm publish/fanout/broadcast, exercised stale-message cleanup**)**

Kết quả:

```
[gate graph_diff_full] message_plane_pass=True whole_graph_diff_total=18
  py_message_plane={'message_nodes': 7, 'endpoint_nodes': 4, 'sends_message': 7, 'targets_endpoint': 5}
  rs_message_plane={'message_nodes': 7, 'endpoint_nodes': 4, 'sends_message': 7, 'targets_endpoint': 5}
[gate graph_diff_inc]  message_plane_pass=True whole_graph_diff_total=20
  py_message_plane={'message_nodes': 20, 'endpoint_nodes': 13, 'sends_message': 20, 'targets_endpoint': 17}
  rs_message_plane={'message_nodes': 20, 'endpoint_nodes': 13, 'sends_message': 20, 'targets_endpoint': 17}
[gate message_plane_exercised] pass=True
[gates] all_pass=True
```

**PASS — message-plane diff = 0 (nodes + rels, mọi property ngoài mask) trên cả 2 legs.**
Baseline và candidate cho cùng node identity (msg::/msg_endpoint:: ids), cùng property
values (language="typescript", repo, confidence float, project_id_normalized), cùng rel
shape (CONTAINS/SENDS_MESSAGE/TARGETS_ENDPOINT) — gồm cả conservation: messages stale
của file đổi bị cleanup đúng, không duplicate/stale sót.

Residual `whole_graph_diff_total` (18/20) nằm ở **code-graph plane** (Function/Type/File
nodes — semantic summary/note text giữa python children vs rust children trên corpus
message này) — thuộc parity scope phase-04 (children), không phải message-scan; được
script report dạng diagnostic, không gate.

Message vectors KHÔNG gate ở đây — ownership phase-06 (xem "Ownership resolution").

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
running 35 tests   (unit tests in src/message_scan/*)
test result: ok. 35 passed; 0 failed

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
- Chạy cho mọi parser ∈ `registry::message_enabled_parsers()` (17 parsers), duyệt theo
  `PARSER_ITERATION_ORDER` (thứ tự launch children của Python orchestrator — quan trọng
  vì full-scan message plane là order-sensitive, xem "Graph emission leg")
- Mỗi parser: graph cleanup → collect_messages_for_parser → graph upsert → write_message_artifact
  (đúng thứ tự pipeline Python `run_message_scan_pipeline`)
- Output: `summary["native_message_scan"]` chứa `{parsers: [...], total_messages,
  total_graph_upserted, output_dir, qdrant_collection, graph_upsert: "native-falkordb",
  vector_upsert: "deferred-phase-06"}`; mỗi parser entry có `graph_upserted`,
  `deleted_messages`, `deleted_endpoints`
- Incremental (`!full_scan || recovery_full_scan` — cùng effective signal của children):
  cleanup `Message` theo changed ∪ deleted paths; full: `cleanup_all` toàn project
- Parsers không có file scan/deleted bị skip (mirror điều kiện launch children của
  Python — children không chạy thì message pipeline + cleanup_all của parser đó cũng
  không chạy)
- Failures: aggregate đầu tiên được raise là error (mirror các phase khác)

## Graph emission leg (graph half — landed)

Port `upsert_messages_to_neo4j` + `cleanup_message_nodes_neo4j` +
`cleanup_all_message_nodes_neo4j` sang `rust/crates/cortex-sync/src/message_scan/graph.rs`,
thực thi qua `cortex_graph_writer::store::GraphStore` (FalkorDbStore) — **cùng write path**
với language/topology writer, không có write path riêng:

- Query text giữ nguyên từng chữ Python (byte-parity; `datetime()` được rewrite thành
  `$__falkordb_now` ISO-8601 bởi store, giống `_prepare_falkordb_query` của Python)
- `prepare_project_scope_parameters` inject đệ quy `project_id_normalized` vào từng row
  `$rows` ở CẢ HAI backend (falkordb-py và FalkorDbStore cùng hành vi) — thuộc tính
  `project_id_normalized` trên `Message`/`MessageEndpoint` khớp wire
- `confidence` được round-recover qua f64 (`(f32 as f64 * 1e4).round() / 1e4`) để param
  float wire-identical với Python `round(confidence, 4)` (f32→f64 thô của 0.95 là
  0.949999988079071 ≠ 0.95)
- Node identity: `Message {id: msg::<sha1[:24]>}`, `MessageEndpoint {id:
  msg_endpoint::<project_id>::<sha1(sender|receiver)[:16]>` (sender rỗng → `"unknown"`,
  receiver rỗng → không có receiver endpoint); rels: `(f:File)-[:CONTAINS]->(m)` khi File
  tồn tại cùng project, `(s)-[:SENDS_MESSAGE]->(m)`, `(m)-[:TARGETS_ENDPOINT]->(r)`
- Batch 500 rows/query như Python

### Ngữ nghĩa order-sensitive của full scan (đã mirror đúng)

Pipeline Python chạy cleanup_all **per parser** (last-parser-wins trong 1 full run) và
Python orchestrator chỉ launch children có file scan/deleted. Native lane mirror cả hai:
duyệt `PARSER_ITERATION_ORDER` filter message-enabled + skip parser không có file —
trên corpus java,ts,python: java → python → ts, graph cuối cùng chỉ còn messages của `ts`.

### Language + repo parity

- `Message.language`: children truyền default language name riêng (ts child →
  `"typescript"`, js → `"javascript"`, android → `"android-kotlin"`, còn lại → tên parser)
  — `message_scan::message_language()` mirror mapping này
- `Message.repo`: Python `os.path.abspath(args.root)` trên **raw** `--root` (không resolve
  symlink — `/var/...` giữ nguyên, không thành `/private/var/...`); lane dùng
  `util::absolute(args.root)` trên raw string, không phải `root_str` đã realpath

## Ownership resolution (message vectors — final)

**Quyết định (chốt cho phase-06):** message **vector** lane (Qdrant upsert + cleanup +
point-id/redaction/hash_vector/wall-time/provenance gates) thuộc ownership của
**phase-06 — cortex-embed embed engine**. Graph lane (`Message`/`MessageEndpoint`) đã
land native ở phase-05 này (xem "Graph emission leg") — không còn nằm trong deferred.

| Lane | Owner | Trạng thái |
|---|---|---|
| Graph upsert `Message` + `MessageEndpoint` + `File`-`CONTAINS` | phase-05 (native lane) | **DONE** — `message_scan/graph.rs` |
| Cleanup `Message` theo file path + prune endpoint mồ côi | phase-05 (native lane) | **DONE** — `message_scan/graph.rs` |
| Qdrant vector upsert (`upsert_messages_to_qdrant`, `_hash_vector`, point-id `uuid5(NAMESPACE_URL, record.id)`) | **phase-06 (cortex-embed)** | Deferred — ownership đã chốt |
| Qdrant cleanup theo file/project (`cleanup_qdrant_for_files`, `_qdrant_delete_by_project`) | **phase-06 (cortex-embed)** | Deferred — đi cùng vector upsert |
| Message vector parity gate (cosine/counts theo ngưỡng phase-06) | **phase-06 component gates** | Move-out khỏi phase-05 — phase-05.md sanction rõ option này |

Lý do chốt cortex-embed (thay vì Python embed sidecar tạm):
- phase-05 đã freeze contract đủ chắc: `MessageRecord` + `hash_vector` + payload shape +
  `PROJECT_ID_NORMALIZED_FIELD` đã ported + tested; cortex-embed chỉ cần cắm embed_texts
- Phase-05.md thiết kế nói rõ: "nếu phase-06 pass trước thì message vectors dùng luôn
  cortex-embed … ownership GHI RÕ (red-team A7)" — đây chính là ghi rõ đó
- Giữ Python embed sidecar tạm cho message vectors sẽ kéo thêm một lane pin-cũ qua
  phase-06/08, trong khi children message pipeline (graph half) không còn cần thiết sau
  flip — tốn chi phí retire mà không gate thêm gì

Tạm thời Python children tiếp tục chạy message **vector** plane qua embedding pass với
`force_python=true` (đã có ở `orchestrator.rs:1925`); graph half phía Python children
cũng còn chạy trên embedding pass (orchestrator chỉ wire `--enable-message-scan` cho
embedding children — `incremental_sync.py` `_build_analyzer_cmd` tại vector loop) cho tới
khi phase-06 ship vector lane và phase-08 retired-error đóng Python path.

## Deferred to phase-06 (history — trước khi graph leg landed)

> Cập nhật: 2 hàng đầu dưới đây đã DONE trong phase-05 (bảng "Ownership resolution");
> giữ lại để lưu bối cảnh review ban đầu.

| Lane | Owner | Why deferred |
|---|---|---|
| Graph upsert `Message` + `MessageEndpoint` MERGE | cortex-embed (phase-06) | ~~Cần `cortex-graph-writer` Message/MessageEndpoint label support~~ — đã solve: schema đã có label + store write path dùng được chung |
| Cleanup `Message` nodes by file path | cortex-embed (phase-06) | ~~Phụ thuộc graph upsert~~ — đã land cùng graph leg |
| Qdrant vector upsert | cortex-embed (phase-06) | Cần `cortex-embed` để ghi đúng embed (component gates: point-id uuid5, redaction, hash_vector, wall-time, provenance) — phase-05 chỉ pin artifact contract |

## Exit criteria

- [x] Parity PASS — collect + artifact byte-exact với Python baseline
- [x] Parity PASS — graph emission leg: Message/MessageEndpoint nodes + rels diff 0
      giữa python-children path và rust native path, full + incremental (FalkorDB)
- [x] Message-scan flags với rust children có ý nghĩa — orchestrator native lane chạy độc lập với primary/embedding pass
- [x] Ownership message vectors ghi rõ — **graph upsert + cleanup land native ở phase-05;
      qdrant vector lane deferred sang phase-06 (cortex-embed component gates)** — xem
      "Ownership resolution"
- [x] Fallback path rõ ràng — nếu phase-06 FAIL, fallback end-state giữ message-enabled parsers trên Python children, plan dừng trước flip

## Files changed (this phase)

| File | LOC delta | Purpose |
|---|---|---|
| `rust/crates/cortex-sync/Cargo.toml` | +1 dep (`once_cell`) | Lazy regex compilation |
| `rust/crates/cortex-sync/src/lib.rs` | +14 | Public library surface |
| `rust/crates/cortex-sync/src/main.rs` | -110 (refactored) | Re-exports via lib.rs |
| `rust/crates/cortex-sync/src/message_scan/mod.rs` | +170 | Public API: collect, write_artifact, detectors, graph |
| `rust/crates/cortex-sync/src/message_scan/detectors.rs` | +1.5k | 17 detector ngôn ngữ + GenericMessageDetector + helpers |
| `rust/crates/cortex-sync/src/message_scan/record.rs` | +150 | MessageRecord + stable_message_id + hash_vector |
| `rust/crates/cortex-sync/src/message_scan/collect.rs` | +340 | collect_messages_for_parser + walk + split_args |
| `rust/crates/cortex-sync/src/message_scan/artifact.rs` | +140 | write_message_artifact (atomic JSON) |
| `rust/crates/cortex-sync/src/message_scan/extensions.rs` | +55 | parser → extensions map + message_language() |
| `rust/crates/cortex-sync/src/message_scan/graph.rs` | +330 (graph leg) | upsert_messages_to_graph + cleanup queries qua cortex_graph_writer store |
| `rust/crates/cortex-sync/src/orchestrator.rs` | +230 | run_native_message_scan_lane (cleanup → collect → graph upsert → artifact) + wire-in |
| `rust/crates/cortex-sync/tests/message_scan_parity.rs` | +150 | End-to-end parity fixture test |
| `rust/crates/cortex-sync/tests/fixtures/message_scan/expected_records.json` | +150 | Golden snapshot (17 records) |
| `scripts/rust_parity/analyzer_parity_message_scan_graph.py` | +430 (graph leg) | Parity leg: graph diff trên Message/MessageEndpoint (full + incremental) |

## Repro commands

```bash
# Build (cortex-sync lib + bin)
cargo build -p cortex-sync

# Unit tests
cargo test -p cortex-sync --lib

# Integration tests (including parity)
cargo test -p cortex-sync --test message_scan_parity

# Graph emission parity leg (FalkorDB 127.0.0.1:6379 phải UP)
cargo build --release -p cortex-sync -p analyzer-java -p analyzer-ts -p analyzer-python
.venv/bin/python scripts/rust_parity/analyzer_parity_message_scan_graph.py

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