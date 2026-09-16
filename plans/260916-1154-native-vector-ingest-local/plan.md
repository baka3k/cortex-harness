---
title: "Native vector-ingest local lane — Rust đẩy embedding vào qdrant local qua LocalQdrantStore JSON engine + flip atomic reader code-lane khỏi Python sidecar (rev2 hấp thụ red-team-rev1: 1 Critical hatch-semantics, 4 High)"
status: executed 2026-09-16 (rev2 — phase-01..05 xong; flip ON, dogfood PENDING chờ user, xem reports/phase05-drill.md)
created: 2026-09-16
revised: 2026-09-16 (rev2)
executed: 2026-09-16
target: "rust/crates/cortex-storage/src/qdrant.rs (point_matches, get_collection_info, persist-mode, payload-index, read-only client), rust/crates/cortex-sync/src/vector_store.rs + vector_sync.rs + orchestrator.rs (:2080-2110 vùng store resolution), rust/crates/cortex-mcp/src/graph/vector_lane.rs (Local arms), scripts/rust_mcp/ (comparator + fixtures), docs/cutover-runbook.md, plans/260915-2027-vector-lane-rust-port (cross-update), plans/260915-2230-python-legacy-cleanup (cross-update)"
blockedBy: []
blocks:
  - "260915-2230-python-legacy-cleanup"  # sau flip: children vector code-lane (primary_vector_sync.py, local_qdrant.py nhánh code, sidecar-for-code) chuyển retirable — disposition của cleanup phải đợi kết quả plan này
relatedPlans:
  - "260915-2300-sync-plane-rust-cutover"    # tiền nhiệm trực tiếp — phase-06 tạo native embedding driver (remote-only, local fail-closed); plan này đóng nốt local lane; DONE
  - "260915-2027-vector-lane-rust-port"      # đã execute — D2 (local = sidecar) bị revise CHO RIÊN code lane; sidecar sống tiếp cho mind/doc lane
  - "260913-2130-rust-full-migration"        # umbrella — wave vector cuối
  - "260916-0936-presence-gating-parser-retirement"  # planned, đụng orchestrator.rs vùng gate :1487/:3041 — SEQUENCE, không interleave với phase-02 của plan này (vùng :2080-2110)
  - "260914-1706-onnx-embedding-spike"       # cortex-embed ONNX parity 0.999 — embedder đã native, không đụng
research: "plans/260916-1154-native-vector-ingest-local/research/repository-findings.md"
redTeam: "plans/260916-1154-native-vector-ingest-local/reports/red-team-rev1.md"
---

# Native vector-ingest local lane — Rust đẩy embedding vào qdrant (remote + local)

## Overview — hiện trạng đo 2026-09-16 (research §repository-findings.md)

Native embedding driver của sync-plane phase-06 chỉ phục vụ **remote HTTP**; mọi nhánh
local fail-closed (`vector_store.rs:28-32` — enum `NativeStore` không có variant Local;
`orchestrator.rs:2093-2099` chỉ log + `native_store=None`). Phát hiện quyết định:

| # | Fact | Bằng chứng |
|---|---|---|
| F1 | **Local code-vector lane đang nhận 0 write**: Rust analyzer children hardcode `vectors=0` từ phase-08 — pre-flip "hành vi Python" ở local là ZERO-WRITES, không phải "python children ghi" | `goanalyzer.rs:288-292` (red-team Critical #1 dùng fact này sửa hatch semantics) |
| F2 | `LocalQdrantStore` (JSON engine, `cortex-local-store.json`) **đã có gần đủ surface** cho ingest: upsert/delete/create_payload_index/get_collection_info/create_collection | `cortex-storage/src/qdrant.rs:450-931` |
| F3 | **2 lỗi parity thật phải fix trước khi wire**: (a) `point_matches` chỉ xử lý `must` — bỏ qua `must_not`/`has_id` mà `stale_filter` LUÔN emit → local delete sẽ xoá SẠCH scope thay vì chỉ stale; (b) `get_collection_info` không mang vector config → drift guard (`vector_sizes`) vô hiệu | `qdrant.rs:280-339, 779-785`, `vector_sync.rs:425-451`, `qdrant.rs:462-478` |
| F4 | **Lease xung đột**: `LocalQdrantStore::open` giữ exclusive flock (`LOCK_EX|LOCK_NB`, cache theo process lifetime) — reader MCP lâu dài giữ lock sẽ làm MỌI `dev sync code` fail. Durability thật của store là `write_atomic` (temp + fsync + rename + dir fsync) → reader KHÔNG cần lock để không torn-read | `qdrant.rs:358-393`, `lease.rs:116`, `util.rs:297-312` |
| F5 | Perf: O(n²) serialize nằm Ở GIỮA CÁC OPS — ~70 upsert calls/parser pass × re-serialize toàn store mỗi call (mỗi call đã flush đúng 1 lần) | `qdrant.rs:520-551 → 442-444`, research A3 |
| F6 | Legacy layout `<qdrant-code-root>/collection/<name>/storage.sqlite` (pickle; layout cũ hơn là single-file-at-root — chưa verify) — Rust JSON engine ghi `cortex-local-store.json` CÙNG root → không collision; không ai ngoài qdrant-client đọc sqlite | research §C8 + red-team #9 |
| F7 | Reader code-lane (sau vector-lane-rust-port): `vector_lane.rs` `VectorStore::Local` arms route list/meta/search sang sidecar `vector_worker.py`; flip = thay nội dung các arm này | `cortex-mcp/src/graph/vector_lane.rs:140-207` |
| F8 | Score chênh nguồn gốc f64-ratio (LocalClient) vs float32-normalize (QdrantLocal) → kỳ vọng ≲1e-6, đóng băng bằng tolerance 1e-6 (convention `compare_vector.py`) | research §B6 |
| F9 | Payload exclusion `text` + field `version` trên hit: local engine CHƯA hỗ trợ → post-strip + shape-audit (phase-01) | research §B6 |
| F10 | Lệnh re-index đã tồn tại: `dev sync code --full-scan` (hoặc `--sync-mode embedding --full-scan`) → `full_replace` scope delete | research §C8 |

## Scope decisions (user 2026-09-16 qua AskUserQuestion; rev2 hấp thụ red-team)

| Câu hỏi | Quyết định |
|---|---|
| Q1 Engine sở hữu format local | **Native JSON store** (`LocalQdrantStore`) — Rust là chủ sở hữu format, bỏ dependency qdrant-client cho code lane. (Loại: qdrant server local — thêm runtime dep, mất zero-infra; Qdrant Edge beta — không đọc được file cũ, chưa production) |
| Q2 Phạm vi flip | **Atomic — gộp cả writer + reader (code lane)**. Reader `VectorStore::Local` của cortex-mcp chuyển sang `LocalQdrantStore`, bỏ sidecar cho code lane. **Mind/doc lane KHÔNG đụng** — writer doc vẫn Python (`docsync.rs:58`) nên sidecar + `vector_worker.py` sống tiếp cho mind lane (D6) |
| Q3 Dữ liệu legacy (pickle sqlite) | **Re-index per instance** — không viết tool migrate. `dev sync code --full-scan` sau flip; quarantine = rename **subtree pickle** `<root>/collection` → `<root>/collection.legacy-pickle.bak` (GIỮ nguyên `cortex-local-store.json` tại root — red-team #5) sau khi re-index thành công |
| D4 Lease/concurrency (rev2, thay lock-flock reader) | **Reader KHÔNG giữ lock**: client read-only mới (`open_readonly`, không flock) đọc qua đảm bảo `write_atomic` của writer (rename atomic → reader luôn thấy store cũ-hoặc-mới nguyên vẹn) + revalidate mtime mỗi burst. Writer giữ nguyên `LOCK_EX|LOCK_NB` hiện tại ( uncontended vì reader không giữ lock; fail-fast same-instance sync giữ nguyên) — red-team H3 |
| D5 Hatch & xoá Python (rev2 sửa semantics) | **Không xoá Python trong plan này**. Hatch `CORTEX_VECTOR_BACKEND` phạm vi **local lane only**: `=rust` (opt-in từ phase-02, default từ phase-05) bật native local; `=python` hoặc unset (trước flip) → **native local pass TẮT, local data FROZEN (zero-writes)** — KHÔNG có "delegate python children" (không tồn tại đường này từ phase-08, red-team C1). Remote lane không chịu ảnh hưởng flag. Deletion thuộc `260915-2230-python-legacy-cleanup` |
| D6 Biên lane (mặc định) | Chỉ code lane (`sync_vector_documents` + `vector_lane.rs`). Mind/doc ingest ngoài biên (mọi plan trước đã chốt doc-plane out of scope) |

## Phase map (5 phases — rev2)

| Phase | Tên | Deliverable chính | Gate chốt |
|---|---|---|---|
| 01 | Engine hardening (cortex-storage) | Fix `point_matches` (must_not + has_id) khớp shape `stale_filter`; `get_collection_info` mang vector config (shape REST-like để `vector_sizes` dùng nguyên văn); **persist-mode bulk** (`persist=false` trong pass + 1 flush cuối — diệt O(n²) GIỮA các ops, F5); `create_payload_index` idempotent; `open_readonly` (không lock, mtime revalidate); **shape-audit**: field `version` trên hit + QdrantLocal có áp `QDRANT_HNSW_EF` không | Filter-parity fixture với golden filters capture từ `stale_filter`; concurrent test writer(sync)+reader(search) không torn-read không deadlock; **benchmark wall-time + total-bytes-serialized assertion** (không phải flush counter — red-team H4); audit output trong reports/phase01-engine.md |
| 02 | Writer wire (cortex-sync) | `NativeStore::Local(LocalQdrantStore)`; trait seam 2 impl trong `sync_vector_documents`; nhánh local trả Local **CHỈ khi `CORTEX_VECTOR_BACKEND=rust` (opt-in; unset → giữ Unsupported fail-closed — red-team H2: không có flip-without-escape)**; legacy-guard compound key (`collection/` present OR root `storage.sqlite` present) AND `cortex-local-store.json` absent → lỗi trung thực ở `LocalQdrantStore::open` (red-team #9); scratch JSON store quota note | Scratch local sync Rust-only với flag bật: JSON store đúng collection/points/payload, rename-file → stale points bị xoá đúng (không xoá sạch); drift guard message khớp; flag unset → hành vi hôm nay byte-identical |
| 03 | Reader wire (cortex-mcp code lane) | `vector_lane.rs` Local arms → read-only client (mtime revalidate); post-strip `text`; hit shape theo audit phase-01; sidecar chỉ còn mind lane. **Prerequisite mới (red-team M6): twin-capture mini-harness (python pickle store + native JSON twin trên fixture nhỏ) dựng ở phase này** | Twin-fixture parity cấu trúc + score tolerance 1e-6; **RSS assertion định lượng** (ΔRSS MCP process < 300MB trên store 132MB — red-team M8); `CORTEX_MCP_VECTOR_WORKER` rỗng vẫn fail-closed cho mind lane |
| 04 | Parity harness + golden (ingest + search, local) | Mở rộng twin-capture thành full matrix (ma trận vector-lane phase-01): stale-rename, drift, prefix/rỗng/không-registered, explore seeds, tune-env inert, read-only write-op error | Xanh 2 lần liên tiếp; reports/phase04-parity.md ghi max score-diff đo được + danh sách divergence được chấp nhận (đóng windows risk #3) |
| 05 | Flip + re-index + dogfood + rollback drill | Flip default `CORTEX_VECTOR_BACKEND=rust` (unset → native) 1 commit; legacy notice + `dev sync code --full-scan` re-index; quarantine `<root>/collection` → `.legacy-pickle.bak` (giữ JSON store); **rollback guard: `cortex-local-store.json` present + `collection/` absent → python leg TỪ CHỐI loudly (không phục empty)** — red-team H5; runbook + cross-plan closure | Drill legs: (1) python-hatch trên instance CHƯA quarantine → sidecar đọc pickle legacy stale + writer frozen assert; (2) rollback SAU quarantine → loud refusal assert (data an toàn trong JSON); (3) native re-run `--full-scan` → parity fixture + counts converge; dogfood ≥1 kỳ hoặc waiver user ghi rõ |

## Rủi ro chính (rev2)

1. **Delete ăn nhầm điểm** (F3a) — `point_matches` fix là điều kiện cứng phase-02; unit test filter-parity chặn. Red-team cleared: chưa có production caller của LocalQdrantStore ngoài factory/parity tests → không có đường pre-fix chạm data thật.
2. **Hatch semantics trung thực** (red-team C1) — `=python` nghĩa là FROZEN (zero-writes) + sidecar đọc pickle legacy (stale); KHÔNG quảng cáo "delegate python children". Drill leg 2 chứng minh refusal là loud, không fail-open empty.
3. **Score parity 1e-6** (F8) — kỳ vọng đã đo, chứng minh bằng fixture thật phase-04; nếu vượt tolerance → điều tra f64-vs-f32, không nới tolerance.
4. **Perf JSON** (F5) — write: persist-mode bulk phase-01 (benchmark assertion); read: RSS assertion phase-03 (132MB JSON → parsed f64 vectors ~70MB + payload, gate < 300MB Δ).
5. **Phủ đủ 2 reader surfaces** (F7) — `semantic_search` + `explore_graph` seeds đều trong comparator matrix phase-04.
6. **Legacy false-negative** (red-team #9) — compound key + guard trong `open` (cả writer lẫn reader); layout single-file-at-root cũ → verify ở phase-02 probe, nếu có → thêm vào key.

## Rollback

- `CORTEX_VECTOR_BACKEND=python` (hoặc unset nếu chưa flip) — local lane TẮT native: writer frozen (zero-writes, đúng hành vi pre-phase-08-đến-nay), reader code-lane quay sidecar đọc pickle legacy. **Sau quarantine**: python leg từ chối loudly (JSON store là dữ liệu sống) — recovery = flip lại native (revert 1 commit), KHÔNG bao giờ tự tạo pickle rỗng im lặng.
- Flip là 1 commit — revert 1 commit trả về opt-in-only (phase-02..04 state).
- Legacy quarantine là rename subtree `.bak` — phục hồi bằng đổi tên lại.
- JSON store hỏng → xoá + `dev sync code --full-scan` re-embed (embedding là derived data).

## Out of scope

- Mind/doc lane (writer `docsync.rs`/`graphrag_ingest_langextract.py`, reader mind sidecar) — giữ nguyên.
- Xoá Python children vector code-lane — `260915-2230-python-legacy-cleanup` (sau flip + dogfood).
- Qdrant Edge / qdrant server local — đã loại ở Q1.
- Migration tool pickle→JSON — đã loại ở Q3.
- Xây lại đường "python children ghi local" (đã chết từ phase-08) — hatch = frozen, không resurrect (red-team C1).
- `CORTEX_MCP_BACKEND` / `CORTEX_EMBED_BACKEND` — không đụng, đã ổn ở vector-lane plan.
