# Phase-04 — parity matrix + golden (local ingest + search) — 2026-09-16

Plan: `plans/260916-1154-native-vector-ingest-local/plan.md` rev2, phase-04.
Harness: `scripts/rust_mcp/local_twin_parity.py` (twin-capture, dựng từ
phase-03) + `scripts/rust_mcp/local_parity_matrix.py` (full matrix, `--rss`
chạy thêm RSS gate). Probes: `cortex-storage/examples/local_vector_probe.rs`,
`cortex-sync/examples/vector_writer_probe.rs`.

## Kết quả — XANH 2 LẦN LIÊN TIẾP

Hai lần chạy `local_parity_matrix.py` liên tiếp: **8/8 legs green** cả hai lần
(và lần `--rss`: **9/9**).

| Leg | Nội dung | Kết quả |
|---|---|---|
| twin/native-vs-pickle | JSON engine vs pickle store (real MCP sidecar worker) — list/meta/search parity | PASS, **max score-diff đo được: 2.220e-16** (tolerance 1e-6) |
| stale-rename/full-replace | full_replace upsert qua seam thật (`sync_vector_documents`) | PASS — ids `id-1,id-2,id-3`, count 3 |
| stale-rename/incremental | file1.go rename → chỉ điểm stale chết | PASS — `id-2,id-3` sống (must_not/has_id phase-01 là điều kiện cứng) |
| drift/message-contract | drift guard message trên local | PASS — byte-khớp `Qdrant collection 'code' has vector size default=8, but the configured embedder produces 12` |
| scope/prefix-empty-unregistered | prefix-expansion / không filter / unregistered key | PASS — prefix 6/6, unscoped 8/8, ghost 0 |
| explore-seeds/merge-dedupe | 2 collections, seeds, merge_hits semantics | PASS — dedupe by str(id), 5/5 unique sau merge |
| tune-env/inert | `QDRANT_HNSW_M/EF_CONSTRUCT/EF/SCALAR_QUANT` set | PASS — sync OK, sizes nguyên, 4 payload indexes |
| guard/missing-and-legacy | reader từ chối missing dir + legacy-only root | PASS — cả hai loud, không serve empty |
| rss/reader-peak (--rss) | store 118MB (12000×512), reader burst 20 searches | PASS — **peak RSS 71MB < 300MB** (red-team M8), completed check bật |

## Max score-diff đo được

**2.220e-16** — f64-ratio (LocalClient) vs float32-normalize (QdrantLocal) trên
fixture 8 điểm × 8 dims × query [0.5]×8. Chênh lệch ở mức machine epsilon,
cách xa tolerance 1e-6 (F8 đóng lại; **không nới tolerance**).

## Review-fix cập nhật (2026-09-16, sau code-review rev1 của execution)

1. **Twin meta leg giờ THẬT**: `local_vector_probe search` emit `sizes` đọc từ
   JSON engine qua `LocalQdrantReader::get_collection_info` — không còn
   hardcode `{"default": DIM}` (review finding 3).
2. **Explore-seeds leg chạy `merge_hits` THẬT**: probe mới
   `cortex-mcp/examples/vector_lane_probe.rs` drive `vector_lane::
   search_collection` + `merge_hits` thực (kèm `_collection` provenance tag,
   desc-order assert) — không còn Python re-implementation (finding 4).
3. **Phạm vi RSS gate (finding 5, relabel)**: gate đo peak RSS của **reader
   process probe** (`local_vector_probe read-big` — chính là code
   `LocalQdrantReader` mà lane MCP dùng), KHÔNG phải toàn bộ MCP process
   (không gồm baseline MCP + ONNX embedder). Plan phase-03 gate ghi "ΔRSS MCP
   process" — số đo 71MB/118MB là lower-bound của khoản đó (reader là phần
   dominan theo dung lượng; embedder/axum thêm cố định). Ghi nhận trung thực,
   không claim vượt phạm vi.
4. **Euclid sort direction (finding 6)**: engine sửa ascending (nearest-first)
   + unit test pin.
5. **Empty-ids footgun (finding 7)**: `delete`/`apply_payload` từ chối
   `Some(&[])` + filter None.
6. **Failed-pass deferred state (finding 2)**: `sync_vector_documents` wrapper
   gọi `VectorWriteOps::discard` trên lỗi → local `reload_from_disk` (swap
   client trong handle + process cache, giữ lease) — pass lỗi không thể bị
   flush lén bởi op khác.
7. **Reader root derivation (finding 1)**: `vector_lane::local_store_root` giờ
   đi qua `cortex_storage::resolve_storage` (writer/reader cùng một resolver —
   config-file + relative-data-home + instance normalization); env-chain cũ
   chỉ còn là fallback khi resolution fail.

### Re-review verdict (fix cycle 1, commit 6c47af1)

**9.5/10 — auto-approve** (0 Critical/High; reviewer tự re-run pin tests xanh).
Hai Low đã fix tiếp (commit sau): `discard` log loudly khi reload lỗi (không nuốt
error của chính cơ chế correctness), matrix precheck đủ 3 probe binaries.
Low residue chấp nhận (reviewer xác nhận không reachable trong wiring production,
ghi nhận để không mất dấu):

- Sibling `LocalQdrantStore` handle mở TRƯỚC reload giữ Arc cũ — production
  single-handle (orchestrator mở 1 lần), test/exotic-only.
- In-flight swap race (thread đang `with_collection` trên Arc cũ lúc discard) —
  sync pass tuần tự, single-writer.
- `local_store_root` fallback chain không normalize instance — chỉ chạy khi
  shared resolver fail (writer cùng fail loud, không có divergence scenario).
- Twin probe `sizes` chỉ đọc shape anonymous config — fixture là anonymous;
  named-vector fixture sẽ cần mở rộng probe.

## Danh sách divergence được chấp nhận (đóng windows risk #3)

1. **`version` field**: native hit không có `version`; sidecar hit có. Inert
   cho consumers (`tools_semantic.rs` dùng id/score/payload/_collection).
   Không fabricate giá trị giả — comparator chuẩn hoá trước khi so.
2. **`vector: null` key**: native trả `vector: null` khi `with_vectors=false`;
   lane strip key này (`lane_hit_shape`) → khớp sidecar shape.
3. **`QDRANT_HNSW_EF`** (và HNSW tuning nói chung): inert trên local engine —
   sidecar cũng đã ignore từ trước; documented divergence, không hành động.
4. **Tie-break order**: native sort theo id sort-key khi score bằng nhau;
   QdrantLocal dùng np.argsort (unstable). Trên embedding thực (không tie) —
   không quan sát được khác biệt trên fixture; ghi nhận ở mức engine.
5. **Payload `text` exclusion**: sidecar exclude server-side; native post-strip
   trong lane (`lane_hit_shape`) — parity chứng minh ở leg twin (payload equal
   sau chuẩn hoá).

## Ghi chú vận hành harness

- Probes là tool nội bộ (không phải user tool), build:
  `cargo build -p cortex-storage --example local_vector_probe -p cortex-sync
  --example vector_writer_probe -p cortex-mcp --example vector_lane_probe`
  (thêm `-p cortex-mcp --example sidecar_guard_probe` cho drill). Sau khi sửa
  example nhớ rebuild — cargo đã có lần không relink example khi chỉ lib đổi.
- RSS leg dùng `/usr/bin/time -l` (macOS: byte count đứng ĐẦU dòng
  "maximum resident set size"; GNU time -v thì số đứng cuối, đơn vị KiB —
  script xử lý cả hai).
- Scratch JSON store quota note: gen-big tạo store ~119MB trong tempdir —
  dataset lớn nên chạy trên máy có ≥1GB free; fixture nhỏ (twin/matrix) dùng
  KB-level.
