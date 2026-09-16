# Cutover Runbook — Python → Rust (phase 14, rust-full-migration)

> **Phase-06 cập nhật (plans/260914-2259-dev-make-python-cutover):** từ cutover này
> **dev/make layer chạy binary-only** (`cortex-dev`) — mọi entrypoint (dev.sh/bat/ps1,
> dev-global.cmd, wrapper.bat, inno shortcuts, scoop shim, Makefile `DEV`/`LIFECYCLE`,
> `ort-ensure`) resolve binary theo D1: `CORTEX_DEV_BIN` → `~/.local/bin/cortex-dev`
> → `rust/target/release/`. Không có python rollback ở tầng entrypoint:
> **flag `CORTEX_DEV_BACKEND` không tồn tại**, entrypoint không còn trỏ `dev.py`.
>
> **Downgrade path (2 bước)** khi binary release có lỗi trên máy user:
> 1. `CORTEX_DEV_BIN=/path/to/previous-release/cortex-dev` (trỏ release cũ), hoặc
> 2. cài lại release binary trước đó: thay `~/.local/bin/cortex-dev` (và
>    `rust/target/release/cortex-dev`) bằng binary của release trước.
> Installer giữ previous release binary cho mục đích này. `dev.py` còn trong repo
> chỉ là **parity reference** (phục vụ `scripts/rust_parity/dev_cli_parity.py`),
> không entrypoint nào gọi nó.

> Áp dụng cho repo `cortex-harness`. Mục tiêu: chuyển toàn bộ runtime vận hành
> (analyzer sync + MCP server + storage graph) sang binary Rust, giữ Python làm
> rollback tối thiểu 1 release, sau đó archive code Python theo khối.
>
> Cờ môi trường điều khiển backend (định nghĩa ở mục [4](#4-flip-step) và
> [5](#5-rollback-steps)):

| Env | Giá trị | Ý nghĩa |
|---|---|---|
| `CORTEX_RUST_ANALYZER` | *(unset)* | **auto-flip**: dùng analyzer Rust cho parser đã port khi binary tồn tại; thiếu → Python |
| | `python` | **rollback flag**: ép analyzer Python |
| | `rust` | ép analyzer Rust (lỗi im lặng → Python nếu thiếu binary) |
| | *(khác)* | bỏ qua — hành vi cũ |
| `CORTEX_MCP_BACKEND` | *(unset)* | **auto-flip**: `dev mcp start` dùng `cortex-mcp` binary (`--server unified` cho code, `--server mind` cho doc) khi binary tồn tại; thiếu → Python |
| | `python` | **rollback flag**: ép MCP server Python |
| | `rust` | ép Rust; binary vắng → fallback Python kèm cảnh báo |
| `CORTEX_MIGRATE_BIN` | path | override vị trí binary `cortex-migrate` cho `dev migrate` |
| `CORTEX_MCP_BIN` | path | override vị trí binary `cortex-mcp` cho `dev mcp start` |
| `CORTEX_EMBED_BACKEND` | *(unset)* hoặc `onnx` | **mặc định từ re-baseline vector-lane phase-02** (`plans/260915-2027-vector-lane-rust-port`): embedder Rust native (`cortex-embed`: ort + tokenizers, CPU). Cần ONNX artifacts trong `.cache/embed/` (`make embed-artifacts`) hoặc `ORT_DYLIB_PATH` resolve được |
| | `python` | **rollback flag**: embedding qua Python sidecar (`embed_worker.py` persistent cho mind MCP, one-shot cho `cortex-doc embed`) |
| `ORT_DYLIB_PATH` | path | override `libonnxruntime`; mặc định tìm `.cache/ort/<ver>/` rồi `.venv/.../onnxruntime/capi/` |
| `CORTEX_EMBED_ORT_THREADS` | int | intra-op threads của ORT session (mặc định `min(cpus, 8)`) |

**Trạng thái `CORTEX_EMBED_BACKEND`** (spike `plans/260914-1706-onnx-embedding-spike`
+ re-baseline `plans/260915-2027-vector-lane-rust-port` phase-02): parity số học đạt
(cosine worst **0.9999994** trên 840 vector; GLiNER contract **848/848** khớp). Hai
lý do giữ `python` mặc định trước đây đã xử lý xong: (1) drift chữ số thứ 7 được
đóng băng bằng tolerance score tuyệt đối **1e-6** trong comparator (tolerance
re-baseline, fixtures ghi từ Python giữ nguyên làm hợp đồng cấu trúc); (2) latency
+25–31ms là root-cause HTTP stall — đã fix bằng TTL cache
`collection_names_cached` (`plans/260914-1706-.../reports/phase02b-latency-rootcause.md`:
onnx p95 26.1ms — nhanh nhất). Rollback: `CORTEX_EMBED_BACKEND=python`.

---

## 0b. Vector lane (plans/260915-2027-vector-lane-rust-port, 2026-09-15)

Backend vector search trong `cortex-mcp` (cả unified lẫn mind):

| Backend | Điều kiện chọn | Đường chạy |
|---|---|---|
| Remote | project đăng ký `storage_backend: "remote"` + `qdrant_url` | REST `/points/search` native (ureq) |
| Local | mọi trường hợp còn lại | sidecar `scripts/rust_mcp/vector_worker.py` (NDJSON stdio, chỉ `qdrant-client`, không torch) |

Query embedding: `cortex-embed` ONNX (jina-v3 cho code lane, bge-m3 cho doc lane) —
`CORTEX_EMBED_BACKEND=onnx` là mặc định từ re-baseline phase-02.

Cờ liên quan: `CORTEX_MCP_VECTOR_WORKER` (override path worker),
`CORTEX_MCP_PYTHON` (python binary cho sidecar), `CORTEX_EMBED_BACKEND=python`
(rollback embedder).

Phụ thuộc hạ tầng cục bộ (máy dev): qdrant + falkordb chạy docker trong colima
(`cortex-qdrant` :6333, `cortex-falkordb` :6379). Colima dừng → mind remote
connection refused; `colima start` để khôi phục.

**Cutover 2026-09-15 (live)**: `dev start` giờ tự chọn backend qua chính
`<svc>/mcp.sh` (mục mới: `CORTEX_MCP_BACKEND=python` giữ server python; unset/auto
exec `cortex-mcp`, doc-tiny chạy `--server mind`). Verify live: cả 2 server chạy
binary `rust/target/release/cortex-mcp`; smoke toàn bộ **44/44 tool** phản hồi hợp lệ
(39 graph + 5 mind).

**Ladybug runtime đã port (2026-09-15, tonight)**: `graph/ladybug.rs` — mở store
file `<owner>.lbug/<graph>` in-process qua crate `lbug` (cùng engine/format với
PyPI `ladybug` của Python); schema introspection dùng `CALL show_tables()` (REL →
relationship types uppercase, NODE → labels); `GraphRuntime` + `DocGraphStore`
đều route được ladybug/falkordb. Verify live: mind `list_source_ids` /
`get_paragraph_text` OK trên instance ladybug thật; smoke **44/44 tool**.

Hạn chế còn lại: `explore_graph` fusion numerics với dữ liệu đa tín hiệu thật
(signal normalize/weights/query-understanding) chưa byte-parity với Python
(4/23 golden case; `semantic_search` + `expand_graph` đã PASS toàn bộ). Track
riêng. Rollback tức thì per-service: `CORTEX_MCP_BACKEND=python dev start`.

---

## 0c. Vector local lane — native JSON engine (plans/260916-1154-native-vector-ingest-local, 2026-09-16)

Từ phase-05 của plan này, **code lane local chạy native hoàn toàn**: writer
ghi JSON engine `LocalQdrantStore` (`<qdrant-code-root>/cortex-local-store.json`,
ghi atomic-rename), reader MCP đọc qua `LocalQdrantReader` **không giữ flock**
(writer giữ exclusive lease như cũ; reader revalidate snapshot theo mtime+size).
Mind/doc lane KHÔNG đổi — sidecar `vector_worker.py` vẫn sống cho doc store.

### Cờ hatch

| `CORTEX_VECTOR_BACKEND` | Writer (embedding pass) | Reader (code lane) |
|---|---|---|
| *(unset)* — **mặc định sau flip** | native local JSON engine | native reader (không lock) |
| `rust` | native (opt-in, như unset) | native reader |
| `python` | **FROZEN — zero writes** (python children không embed từ phase-08; không có đường delegate) | sidecar đọc pickle legacy |

Remote lane không chịu ảnh hưởng của flag này. Flag liên quan khác giữ nguyên:
`CORTEX_EMBED_BACKEND` (embedder), `CORTEX_EMBED_ORCHESTRATOR` (kill-switch
toàn pass), `CORTEX_MCP_BACKEND` (whole-server rollback).

### Re-index (bắt buộc 1 lần cho instance cũ)

Dữ liệu pickle legacy **đóng băng từ phase-08** (không ai ghi) — sau flip cần
re-index. Trình tự cho 1 instance (guard trong `LocalQdrantStore::open` +
`LocalQdrantReader::open` sẽ bắt buộc đúng thứ tự này):

```bash
# 1. Quarantine subtree pickle (giữ lại làm backup — KHÔNG xoá):
mv <qdrant-code-root>/collection <qdrant-code-root>/collection.legacy-pickle.bak

# 2. Re-index (full scan → full_replace scope delete + upsert lại):
dev sync code --full-scan
#    embedding-only: dev sync code --sync-mode embedding --full-scan

# 3. Verify JSON store sống:
ls <qdrant-code-root>/cortex-local-store.json
```

Lần đầu `dev sync code` trên root còn nguyên pickle → lỗi trung thực
"legacy qdrant-client pickle store detected …" kèm recipe trên — đó là guard
chủ động, không phải hỏng hóc.

### Rollback

- `CORTEX_VECTOR_BACKEND=python` — writer frozen, reader code-lane quay sidecar.
- **Sau quarantine**: python leg **từ chối LOUDLY** (guard trong
  `vector_sidecar`: JSON store present + `collection/` absent → refuse — không
  bao giờ phục empty pickle). Recovery = bỏ flag (quay native) hoặc restore
  `collection.legacy-pickle.bak` → `collection` (mất re-index, chấp nhận stale).
- Flip là 1 commit — `git revert <flip-commit>` trả về opt-in-only.
- JSON store hỏng → xoá + `dev sync code --full-scan` re-embed (embedding là
  derived data).

### Verify gates

- Parity matrix ×2: `.venv/bin/python scripts/rust_mcp/local_parity_matrix.py`
  (thêm `--rss` để chạy RSS gate — store 118MB, peak reader < 300MB).
- Rollback drill: `.venv/bin/python scripts/rust_mcp/vector_local_drill.py`
  (3 legs: frozen + refusal + convergence).
- Chi tiết: `plans/260916-1154-native-vector-ingest-local/reports/`.

---

## 0. Build một lần

```bash
cd <repo>/rust
cargo build --release -p cortex-migrate -p cortex-mcp -p cortex-dev
# Analyzer binaries (mọi parser đã port phase 04–13):
cargo build --release --workspace
```

Sau bước này `rust/target/release/` phải có: `cortex-migrate`, `cortex-mcp`,
`cortex-dev`, `analyzer-<lang>` cho từng parser.

## 1. Migration dữ liệu local instance (chạy TRƯỚC khi flip)

Legacy embedded FalkorDBLite `.rdb` → Ladybug. Tool **không bao giờ ghi/xoá
nguồn** (boot read-only, `SHUTDOWN NOSAVE`) và ghi vào store file MỚI
(`<instance>/ladybug/<owner>/<owner>.lbug/<graph>`); file tồn tại sẵn → từ chối
trừ khi `--overwrite`.

```bash
# 1a. Xem trước — liệt kê graph + counts, không ghi gì:
dev migrate --dry-run
#    hoặc trực tiếp: rust/target/release/cortex-migrate --instance <id> --dry-run

# 1b. Migrate thật (mặc định cả code+doc lane của instance đang active):
dev migrate
#    Instance khác / bó lane:
rust/target/release/cortex-migrate --instance bakatrans --owner code
#    Chỉ một graph:
rust/target/release/cortex-migrate --instance default --graph hyper_graph
#    Chạy lại từ đầu (xoá store đích đã tạo dở):
rust/target/release/cortex-migrate --instance default --overwrite
```

Báo cáo cuối chạy in bảng so sánh node/rel count **nguồn ↔ đích** cho từng
graph (kèm từng label / rel-type). Tool exit 0 chỉ khi mọi count khớp; bất kỳ
dòng `COUNT MISMATCH`/`ERROR` → điều tra trước khi đi tiếp.

## 2. Dogfood 1 tuần (toàn-Rust trên stock)

Mỗi ngày làm đúng chuỗi sau và ghi kết quả vào nhật ký dogfood:

```bash
# a) Sync code bằng analyzer Rust (mặc định đã auto-flip — KHÔNG set env gì):
dev sync code --project-dir <stock-project> all
#    Bắt buộc thấy trong log các analyzer chạy dạng binary
#    rust/target/release/analyzer-<lang> (không phải python analyzers/*.py).
#    Muốn ép Rust rõ ràng: CORTEX_RUST_ANALYZER=rust dev sync code ...

# b) MCP server Rust + doctor:
dev mcp start                # dòng đầu phải là: [backend] auto (... cortex-mcp ...)
curl -s http://127.0.0.1:8788/health   # {"status":"healthy",...}
curl -s http://127.0.0.1:8789/health   # doc/mind server
dev doctor

# c) Sync doc (doc-tiny pipeline vẫn Python ở giai đoạn này — so sánh GraphRAG):
dev sync doc --project-dir <stock-project>
```

**So sánh hằng ngày** (dual-run đối chiếu):

1. Đếm node/rel graph code trước vs sau sync (`GRAPH.RO_QUERY` count hoặc
   `dev storage-layout`); drift bất thường → dừng, rollback, ghi nhận.
2. Chạy cùng 3 câu query GraphRAG qua server Rust (`:8788`) và server Python
   (`CORTEX_MCP_BACKEND=python dev mcp start --force-restart`) — kết quả
   tool-result phải tương đương (cùng tool catalog, shape JSON giống nhau).
3. Journal sync: `dev journal status --journal-path <path in summary>` —
   không được có run `failed` treo.
4. Ghi chú thời gian sync (Rust nhanh hơn đáng kể là kỳ vọng; regression →
   ghi nhận).

Ngưỡng qua cửa: **7 ngày liên tiếp** không có lỗi blocker (crash, count drift,
tool-result sai shape).

## 3. Flip step (kết thúc dual-run)

Khi dogfood đạt:

1. Đảm bảo migration đã chạy cho mọi instance thật (mục 1).
2. Flip mặc định trong vận hành: bỏ mọi `CORTEX_RUST_ANALYZER` /
   `CORTEX_MCP_BACKEND` ra khỏi `.env`, launcher, CI — **mặc định unset chính
   là backend Rust** từ phase 14 (auto-flip đã merge).
3. Khởi động lại MCP: `dev mcp start --force-restart` → xác nhận
   `[backend] auto (...)` trỏ vào binary Rust cho cả code + doc.
4. Cập nhật ReadMe/INSTALLER_GUIDE/CLAUDE.md: runtime mặc định là Rust; env
   flags chỉ còn là rollback.

## 4. Rollback steps (mỗi lúc cần)

```bash
# Analyzer về Python (mọi chỗ set env hoặc 1 lần chạy):
export CORTEX_RUST_ANALYZER=python
dev sync code all            # chạy lại bằng analyzers/*.py như cũ

# MCP server về Python:
export CORTEX_MCP_BACKEND=python
dev mcp stop || dev stop     # dừng server Rust (dev stop dọn cả 2 backend)
dev mcp start                # [backend] CORTEX_MCP_BACKEND=python (rollback flag)

# Dữ liệu graph: .rdb nguồn KHÔNG BỊ XOÁ bởi migration — set lại provider
# falkordb trong .cortext-harness/config/<active>.json là chạy lại trên dữ liệu cũ.
```

Rollback xong phải xác nhận: sync chạy được, `/health` OK trên 2 port, doctor
xanh.

## 5.1. Phase-07 update — composition gate đã đóng (260915-analyzer-layer-rust-cutover)

> Áp dụng từ phase-07 (2026-09-15). Mục tiêu: mirror đầy đủ flip matrix
> sang delegation target (`incremental_sync.py`), đóng composition gate trên
> synthetic multi-language corpus (24 parsers + 12 overlays + topology +
> dart/flutter/csharp + message endpoints), CI workflows update **trước**
> delete commit.

### 5.1.1. Flip matrix mirror (incremental_sync.py ↔ cortex-sync registry)

`_RUST_ANALYZER_BINARIES` + `_RUST_FRAMEWORK_BINARIES` map **đầy đủ 24
primary + 12 overlays + project_topology + dart/csharp** — mirror 1:1 với
`cortex-sync` registry `rust_analyzer_binaries()` + `framework_rust_binaries()`.
`_resolve_rust_binary_name()` lookup theo parser/framework key (cùng logic
`mapped_binary_name()` của Rust). `_rust_binary_path()` probe bare name
trước, `.exe` sau (Windows delegated path — red-team A8/F2).

Grep gate (red-team S2): tham chiếu `_analyzer.py` chỉ tồn tại trong 2 file:
- `code-tiny/tools/sync/incremental_sync.py` (`ANALYZERS`/`FRAMEWORK_ANALYZERS`
  `script_path` field — rollback path)
- `rust/crates/cortex-sync/src/registry.rs` (`AnalyzerConfig`/`FrameworkAnalyzerConfig`
  `script_path` field — registry reference)

Script `phase07_composition_parity.py` enforce gate này. Khi phase-08 xoá
Python entries, các `script_path` field trở thành "rollback only" — không
còn code live gọi.

### 5.1.2. Composition parity legs (phase-07)

Script `scripts/rust_parity/phase07_composition_parity.py` aggregate 8 legs:

1. **graph-diff baseline** — FalkorDB reachable; per-parser scripts own full diff
2. **message-scan parity** — re-run `analyzer_parity_message_scan_graph.py`
3. **overlay-binary proof** (red-team F4) — spring overlay scheduled, status != crashed
4. **qdrant counts + cosine** — phase-06 fallback nếu `QDRANT_URL` unset
5. **detector_evidence declaration** (red-team A4) — masked trong
   `sync_orchestrator_parity.py::MASKED_SUMMARY_KEYS` (struts-evidence
   order divergence; documented)
6. **delegation smoke (static)** — `_RUST_ANALYZER_BINARIES` +
   `_RUST_FRAMEWORK_BINARIES` + `.exe` probe present
7. **per-parser delegation** — dart/flutter/csharp/topology scripts referenced
8. **grep gate** — no `_analyzer.py` references ngoài rollback script_path

Exit 0 = all legs PASS; 1 = any FAIL.

### 5.1.3. Auto-flip (đúng cho cả cortex-sync + delegation target)

Từ phase-08 (1 commit cuối, gated trên dogfood 7 ngày + rollback drill):

| `CORTEX_RUST_ANALYZER` | semantics MỚI (cả cortex-sync + incremental_sync delegation) |
|---|---|
| unset | **Rust-if-binary** (auto-flip phase-14) |
| `rust` | Rust binary; missing → hard error + hint build |
| `python` hoặc khác | **Loud "retired" error**: "Python analyzer plane retired at <commit>; rollback = git revert <tag>" — KHÔNG silent fallback |

`--version` build-commit handshake: orchestrator check binary version khớp
expected; lệch → hard error (red-team F5 stale binary). Embedding/message
artifacts thiếu/invalid từ child → hard-error, không bao giờ im lặng.

### 5.1.4. Windows prerequisites

- Cài `tree-sitter-cli` nếu chưa có (cargo build cho analyzer binaries trên
  Windows cần native toolchain).
- `cargo build --release` mặc định sinh `.exe` suffix — `_rust_binary_path()`
  đã probe đúng (red-team A8/F2).
- Ladybug path (Windows default) — delegation smoke bắt buộc pass trước
  delete. Test bằng `PATH` chứa Rust binary + Windows shell.

### 5.1.5. C# (csharp) prerequisites

- `dotnet` SDK phải có trên máy (Roslyn worker auto-build từ
  `analyzer-csharp` Rust binary lúc sync; thiếu dotnet → bootstrap fail).
- Cài: `brew install dotnet` (macOS) hoặc tải từ dotnet.microsoft.com (Windows).
- Fallback tree-sitter không port (per red-team A6/C5/F6); fallback-usage gate
  đo fallback có được dùng thật trên corpus.

### 5.1.6. detector_evidence policy

Trường `framework_overlays[].detector_evidence` có order divergence giữa
Python (`_group_paths_by_framework` sort cho struts) và Rust (`frameworks.rs`
insertion-order). Hiện **masked trong `sync_orchestrator_parity.py`**
với documented reason. Long-term: sort both sides identically (deferred
parity-script change). Khi fix, gate sẽ lift mask và verify byte-parity.

### 5.1.7. Stale claim (§5.3 stale)

Nếu user báo "binary có nhưng không chạy":
1. `cargo build --release --workspace` (rebuild all 32 binaries).
2. Check `rust/target/release/analyzer-<x>` (Unix) hoặc `.exe` (Windows) tồn tại.
3. `analyzer-<x> --version` (handshake — phase-08 gate).
4. Nếu version lệch → rebuild; nếu exit non-zero → `cortex-sync` sẽ in
   "Python analyzer plane retired at <commit>" thay vì silent fallback.

## 5.2. Archive Python theo khối

Sau khi Rust ổn định **2 release** liên tiếp cho một khối:

1. Khối order gợi ý (ổn định trước, archive trước):
   `analyzer-<lang>` (phase 04–06) → `graph writer` → `MCP code server`
   (phase 11–13) → `dev CLI` (phase 10) → `doc pipeline` → `qdrant/retrieval`.
2. Mỗi khối: xoá code Python + entry trong launcher, giữ test parity fixture
   sinh từ Python như chứng cứ; thêm note vào `docs/` + CHANGELOG.
3. Giữ lại vĩnh viễn: `code-tiny/tools/sync/incremental_sync.py` (orchestrator
   sync, vẫn là entry `dev sync code`), doc-tiny pipeline tới khi port xong,
   và bộ rollback env parsing (rẻ, vô hại).
4. Sau mỗi lần archive: chạy lại bộ gates (`cargo test --workspace`, parity
   suites trong CI) để chắc không có phụ thuộc chết.
