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
| `CORTEX_EMBED_BACKEND` | *(unset)* hoặc `python` | **mặc định hiện tại**: embedding qua Python sidecar (`embed_worker.py` persistent cho mind MCP, one-shot cho `cortex-doc embed`) |
| | `onnx` | embedder Rust native (`cortex-embed`: ort + tokenizers, CPU). **Chưa bật mặc định** — xem ghi chú dưới bảng |
| `ORT_DYLIB_PATH` | path | override `libonnxruntime`; mặc định tìm `.cache/ort/<ver>/` rồi `.venv/.../onnxruntime/capi/` |
| `CORTEX_EMBED_ORT_THREADS` | int | intra-op threads của ORT session (mặc định `min(cpus, 8)`) |

**Trạng thái `CORTEX_EMBED_BACKEND`** (spike `plans/260914-1706-onnx-embedding-spike`):
parity số học đã đạt (cosine worst **0.9999994** trên 840 vector; GLiNER contract
**848/848** khớp), nhưng mặc định vẫn là `python` vì hai lý do đo bằng số trong
`plans/260914-1706-onnx-embedding-spike/reports/phase02-bgem3-parity.md`:
(1) điểm tool-result dịch ở **chữ số thứ 7** → phải re-baseline golden fixture
phase-12/13 (phase-04 của spike, đợi dogfood xong); (2) latency end-to-end của MCP
server khi bật onnx **chậm hơn ~25–31ms/tool-call** ở steady state, chưa giải thích
được. Bật `onnx` trước khi xử lý 2 việc đó là làm vỡ hợp đồng đã pass.

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
