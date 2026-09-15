# Phase 06 — Embedding orchestrator-level qua cortex-embed (component-gated overturn của NO-GO)

> **status: DONE (shared-7 lineage) — 2026-09-15.** 8/8 component gates PASS với
> số đo thật (fixture + live): point-id uuid5, redaction 3-tầng, `_hash_vector`,
> delete-by-filter/vectors_config, embedding cosine (live worst 0.999999999992,
> 0 under-gate), wall-time native 1.95× Python (không regression), provenance pin
> fail-closed (+ negative tests), artifact 0600. spike NO-GO overturned.
> Corrections khi implement (chi tiết trong report): G3 `_hash_vector` là
> message-lane FALLBACK vectorizer chứ không phải change-detection; 5 latent bug
> production path được các gate bắt live (upsert POST→PUT, delete filter body,
> pick_graph không tìm thấy export script-named, pin sha256 fail-open ở
> ensure_ort). **Carve-out tường minh (không im lặng)**: legacy
> `CodeEmbedder`/`QdrantWriter` lineage (~17 parser, point-id scheme + arithmetic
> khác) và local embedded-store lane VẪN ở Python children → phase-08 delete list
> thu hẹp còn shared-7 lineage. Message-vector lane swap (bullet "nếu phase-05
> pin sidecar") defer sang phase-05. Toàn bộ evidence:
> `reports/phase06-vector-component-gates.md`.

## Bối cảnh đã sửa (red-team Critical #1)

Rev 1 citation sai: "cosine 0.9999994/840 vectors" là **bge-m3 doc-plane** (`runbook:42-44`), không phải jina int8. Thực trạng: jina fp32 parity = 0.9999995/520 texts (`spike/reports/phase01-jina-parity.md:12`); **int8 gate CHƯA chạy** (:18); spike có report **NO-GO đo đạc** cho port sync ingest (`spike/reports/phase05-sync-decision.md:8`) vì 3 hazard cosine-gates "có thể không bắt được": deterministic point-id (uuid5 join order), redaction regex 3 tầng, `_hash_vector`. Phase này phải **overturn NO-GO bằng component evidence**, không citation.

## Component gates (mỗi cái một parity gate riêng — thứ tự implement theo thứ tự này)

| # | Component | Gate |
|---|---|---|
| G1 | **Point-id parity**: tái tạo deterministic uuid5 point id từ payload fields, byte-match join order của `primary_vector_sync.py:43-89` | Fixture: N payloads → id list Python vs Rust exact |
| G2 | **Redaction parity**: port 3-tier redaction regex | Corpus edge-cases (secret-shaped strings) → redacted payloads diff 0 |
| G3 | **`_hash_vector` parity**: port hàm hash-vector (change-detection cho vector skip) | Fixture inputs → hash exact |
| G4 | **Stale cleanup parity**: `delete_by_filter` fields + vectors_config phải byte-match (red-team F8) | Gate chạy trên **collection Python-written snapshot** (không phải fresh collection) — stale points bị xoá đúng, config không recreate |
| G5 | **Embedding quality**: cortex-embed jina (fp32 trước, int8 nếu artifact có) vs Python children embed trên corpus thật | Count per collection + cosine per point ≥ ngưỡng (fp32: 0.999; int8: ngưỡng spike) |
| G6 | **Wall-time baseline** (red-team A5): sync-time embed full corpus Python-children vs orchestrator ONNX, cùng máy | Không regression > 20%; spike đo ort ≤1.00× ở batch 8, chậm hơn ở batch lớn → chọn batch size có measured evidence |
| G7 | **Provenance pins** (red-team S5): HF snapshot_download pin revision hash + `trust_remote_code` review; exported graph sha256 ghi in-repo (`cortex-embed/src/model.rs` verify hash khi load, không chỉ existence); `ensure_ort.rs:171` **fail closed** khi PyPI digest thiếu | Build/repro test + negative test digest thiếu |
| G8 | **Artifact security**: embedding-input artifact (chunks + payloads = source code plaintext) ghi **0600** tại vị trí quy định phase-01 contract (red-team S6) | Permission test |

## Scope code

- `cortex-sync` orchestrator: embedding pass mới đọc artifact do children emit (contract frozen phase-01) → embed qua `cortex-embed` → upsert qdrant qua `cortex-storage` (collection/layout provisioning GHI RÕ — red-team A7 nửa qdrant layout).
- `cortex-analyzer-framework`: children emit embedding-input artifact; `--qdrant-*`/`--embed-*` flags với rust children → no-op có log "moved to orchestrator" (contract cũ giữ cho Python children delegation path).
- Message-vector lane (nếu phase-05 pin sidecar): chuyển sang cortex-embed.
- `EMBED_DEVICE`: ort device detection ưu tiên; torch Python probe chỉ còn cho delegation path.

## Thất bại → fallback end-state (đã ghi plan.md)

Bất kỳ gate nào fail không fix được → plan dừng trước phase-07/08: giữ 7 parser `SHARED_VECTOR_CLI_PARSERS` trên Python children làm vector plane, flip 15 parser còn lại. Đổi lại: phase-08 delete list thu hẹp tương ứng. **Không xoá khi vector plane chưa native hoặc chưa carve-out tường minh.**

## Cross-plan

- Kết quả gates + wall-time → update frontmatter `260914-1706-onnx-embedding-spike` (blockedBy relationship 2 chiều) + note cho MCP golden re-baseline (thuộc spike, không chặn xoá).

## Exit criteria

- 8/8 component gates PASS, report `phase06-vector-component-gates.md` (mỗi gate một section, evidence kèm số đo).
- 1 sync smoke end-to-end qdrant bật: vectors xuất hiện, query sanity pass.
- Hoặc: fallback end-state kích hoạt + plan re-scope (delete list thu hẹp) ghi vào plan.md.
