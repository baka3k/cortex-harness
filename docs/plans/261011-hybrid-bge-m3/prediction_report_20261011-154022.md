# Prediction Report — Hybrid Retrieval cho BGE-M3 (dense + sparse, ColBERT defer)

- **Date:** 2026-10-11
- **Depth:** deep
- **Verdict:** ⚠️ **CAUTION** (0 Critical, 3 High — đều có mitigation cụ thể)
- **Proposal:** Khai thác 3 đầu ra gốc của BGE-M3 mà harness đang bỏ phí. Hiện tại cả 2 pipeline chỉ dùng **dense** (1024-dim, COSINE, unnamed `default` vector). Đề xuất: (P1) ingest thêm **sparse** (learned lexical weights) cạnh dense, query fuse server-side bằng Qdrant Query API (`prefetch` dense+sparse → `Fusion.RRF`); (P2) backfill collection cũ; (P3) ColBERT multi-vector chỉ xem xét ở pha sau dạng rerank top-k.

## Executive Summary

BGE-M3 sinh cả 3 biểu diễn từ **cùng một forward pass** — chi phí compute thêm để lấy sparse là không đáng kể (2 linear projection + max-pool), trong khi Qdrant hỗ trợ native sparse index + RRF fusion (qdrant-client 1.18.0 đã đủ). Với một code-intelligence harness — nơi truy vấn theo **identifier chính xác** (tên hàm, mã lỗi, class name) là use-case cốt lõi và là điểm yếu cấu trúc của dense-only — dense+sparse là đề xuất có benefit/cost tốt. Ba rủi ro cần kiểm soát: schema migration cho collection đang sống, độ trung thực của đầu ra sparse nếu không dùng FlagEmbedding, và nguy cơ nửa vời (một số collection hybrid, một số dense-only) nếu thiếu capability detection.

## Agreements (4+ personas cùng hướng)

1. Dense+sparse (RRF) là pha 1 đúng đắn; ColBERT phải defer (storage ~200×/point, latency MaxSim) — Architect + Performance + Devil's Advocate.
2. Kiến trúc hiện tại thuận lợi: 4 backend MCP đã delegate về chung `qdrant_query_support.search_collection/merge_hits`; ingest có chokepoint `CodeEmbedder`/`primary_vector_sync`/`local_qdrant.ensure_collection` + marker sentinel — đúng chỗ cắm hybrid — Architect.
3. Feature flag env (mặc định off → bật sau A/B) và A/B gate theo đúng runbook swap model (EN/VI/JA/identifiers) — mọi persona.
4. Không đụng model swap: cùng model, cùng forward pass → không phải re-stamp embedding_model; chỉ thêm capability flag.

## Conflicts

| Topic | Architect | Security | Performance | UX | Devil's Advocate | Resolution |
|---|---|---|---|---|---|---|
| Sparse có redundant với keyword search Neo4j sẵn có trong `intelligent_retrieval`? | Thích cắm vào hot path server-side | — | Sparse index nhanh, ít tốn hơn round-trip graph | Consumer không phải đổi gì | **Có** — keyword signal đã fuse sẵn | Hot path `semantic_search` (đa số query) hiện **dense-only**; Neo4j keyword chỉ có trong engine intelligent_retrieval và là keyword thô, không phải learned weights trên subword. Sparse nâng hot path, compose được → **làm, nhưng A/B gate** |
| FlagEmbedding hay hand-roll sparse/colbert heads? | Ưa ít dependency | Lo supply-chain → pin | FlagEmbedding nhẹ (torch+transformers đã có) | — | Hand-roll dễ lệch reference math | **FlagEmbedding** hoặc port chính xác `sparse_linear.pt` + golden-vector test. Lưu ý: snapshot local **chưa có** 2 file head này → phải tải (qua audit path sẵn có) |
| ColBERT làm ngay? | Chỉ nếu có use-case chứng minh | — | **Từ chối** cho pha 1 | — | Storage bùng nổ vô ích | **Defer** — chỉ rerank top-50 nếu sau này có bằng chứng |
| Collection cũ xử lý thế nào? | Cần backfill hoặc fallback per-collection | Marker phải phản ánh capability | Backfill tái dùng payload text rẻ hơn re-ingest | Fallback phải trong suốt, không error | Nửa vời là worst case | Query-side **capability detection qua collection info** (mở rộng `qdrant_layout_cache`), dense-only collection → query dense thuần; backfill script riêng |

## Risk Summary

| Risk | Severity | Persona | Mitigation |
|---|---|---|---|
| Nửa vời: collection hybrid trộn dense-only, ranking không nhất quán, marker không phản ánh | High | Architect / DA | `_embed_meta` thêm `hybrid` field; query đọc capability từ collection info (cached); test ma trận cả 2 trạng thái |
| Đầu ra sparse lệch reference (tự triển khai sai ReLU/max-pool/normalization) → quality âm thầm giảm | High | Architect | Dùng `BGEM3FlagModel` hoặc golden test so vector với output tham chiếu trên mẫu cố định |
| Snapshot offline thiếu `sparse_linear.pt`/`colbert_linear.pt` → lần chạy đầu cần mạng, vi phạm cam kết offline-first | High | Security / Architect | Prefetch 2 head qua `embedding_runtime` audit path; `_missing_files()` mở rộng kiểm tra head khi hybrid bật |
| Merge cross-collection: RRF score khác ngữ nghĩa cosine hiện tại trong `merge_hits` | Medium | Performance | RRF là rank-normalized → vẫn so được; ghi rõ trong `merge_hits` + test |
| Storage tăng (sparse index RAM + sparse vectors on disk) | Low | Performance | ~vài KB/point với chunk hiện tại; đo trước/sau trên 1 collection pilot |
| Dependency mới (FlagEmbedding) | Low | Security | Pin version + hash; không kéo thêm heavy dep |

## Per-persona detail

### Architect — confidence: high
- **Concerns:** collection đang sống chỉ có unnamed `default` vector; thêm named sparse cần `update_collection` + backfill; doc-side là flat-tree riêng (twin pattern `embedding_utils`) nên thay đổi phải nhân đôi có kỷ luật; 4 backend byte-parallel đã delegate về `qdrant_query_support` nên fusion chỉ cần sửa 1 chỗ.
- **Recommendations:** named vectors `dense` (giữ default hoặc đặt tên mới khi re-index) + `sparse`; fusion tại `search_collection` qua Query API; capability flag trong sentinel; flag env `EMBED_HYBRID`.

### Security — severity: low
- **Threats:** fetch thêm head files từ HF (supply-chain, supply surface +2 file); FlagEmbedding pip dep mới; không có auth/data-exposure mới (sparse là dữ liệu dẫn xuất của text đã lưu).
- **Mitigations:** tải qua audit session sẵn có; pin + hash trong requirements; HEAD-gate `_missing_files` khi `EMBED_HYBRID=1`.

### Performance — metrics_impact: query +1 prefetch/collection (~vài chục ms local); ingest +2 projection (không đáng kể); storage +vài KB/point (sparse), ColBERT +~200× (loại)
- **Bottlenecks:** fan-out nhiều collection × fusion; sparse index RAM.
- **Alternatives:** DBSF thay RRF (cần calibration); giữ dense cho collection nhỏ.

### UX
- **Issues:** không đổi response shape (`results`/`errors[]`) — consumer không vỡ gì; chất lượng tăng rõ ở query identifier/serial/mã lỗi.
- **Edge cases:** collection dense-only phải fallback êm (không nhét lỗi vào `errors[]` như mismatch); A/B cần cách tắt nhanh per-run (env flag).

### Devil's Advocate
- **Assumptions challenged:** (1) "dense đang kém với identifier" — chưa có A/B số liệu trong repo chứng minh; (2) "sparse không trùng keyword Neo4j" — đúng cho hot path nhưng trùng một phần với intelligent_retrieval; (3) "chi phí migration đáng để trả" — không phải bugfix mà là enhancement.
- **Simpler alternatives:** khuếch đại keyword signal sẵn có; **tăng `MAX_EMBED_CHARS` (dev.json đang 500 chars trong khi BGE-M3 hỗ trợ 8192 tokens)** — đây có thể là quality lever lớn hơn sparse cho phía doc, và là cải tiến riêng rẻ hơn.
- **Worst case:** trạng thái nửa vời + sparse math sai → ranking xấu hơn âm thầm. → Risk #1/#2 là điều kiện tiên quyết.

## Numbered recommendations

1. **P1 — dense+sparse RRF sau flag:** ingest ghi thêm named sparse vector (cùng forward pass); query `prefetch=[dense, sparse], query=Fusion.RRF` tại `search_collection`; `EMBED_HYBRID` mặc định `0`.
2. **Capability-aware query:** mở rộng `qdrant_layout_cache` đọc `sparse_vectors` từ collection info; collection không có sparse → query dense thuần, **không** báo lỗi.
3. **Sparse fidelity:** `BGEM3FlagModel` (hoặc port `sparse_linear.pt` chính xác) + golden-vector test.
4. **Backfill:** `update_collection(sparse_vectors_config=...)` + script backfill tái dùng `text` trong payload (rẻ hơn re-ingest: không re-parse, chỉ re-encode sparse).
5. **Doc side song song:** twin helper trong `embedding_utils` theo đúng pattern hiện có.
6. **Marker:** `_embed_meta` thêm trường `hybrid`; writer chokepoint kiểm tra khi `EMBED_HYBRID=1` thiếu head files → fail fast.
7. **A/B gate** theo runbook swap (EN/VI/JA + identifier query sets) trước khi bật default.
8. **Defer ColBERT** đến khi có use-case rerank chứng minh được; nếu làm thì chỉ rerank top-50.
9. **Improvement song song rẻ hơn:** xem lại `MAX_EMBED_CHARS=500` (BGE-M3 đạt 8192 tokens) — kế hoạch riêng.

## Next steps (theo verdict CAUTION)

Xử lý 3 mitigation High (capability flag + marker, golden test sparse, prefetch head offline) → đưa đề xuất qua `hi-plan` cho pha 1 (dense+sparse RRF). Không start code trước khi có plan.
