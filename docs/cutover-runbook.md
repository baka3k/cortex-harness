# Cutover Runbook — Python → Rust (phase 14, rust-full-migration)

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

## 5. Archive Python theo khối

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
