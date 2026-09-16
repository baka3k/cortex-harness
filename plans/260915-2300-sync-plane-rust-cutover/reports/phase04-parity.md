# Phase-04 report — Parity harness embedded lanes (2026-09-16)

## Changes

1. **`graph-state` bin** (`rust/crates/cortex-graph-driver/src/bin/graph_state.rs`):
   - `dump <store.lbug> --graph <name>`: canonical payload
     `cortex-graph-state/canonical-v1` — per label {count, key-set, sorted per-node
     property-hash}, per rel table count, **schema_fingerprint + index set** (L3),
     store/graph identity. Store mở `read_only(true)`.
   - `--graph` bắt buộc; named graph không có node tables → **exit 3 FAIL** (H1 gate).
   - `diff <a> <b>`: deep canonical diff, exit 0 khi equal, in từng key lệch.
2. **Harness** `scripts/rust_parity/sync_plane_native_parity.py` — 5 legs trên scratch
   (instance `p4-parity`, data home riêng, project_id `sp1golden`, corpus
   `fastapi_django` + git baseline):
   - leg1 full-sync native: exit 0 / no delegation / `backend=rust-native` / masked-equal
     golden (`summary-full-rust-native-healthy.json`) / graph-state dump diff self-consistent /
     **resolved graph name == project_id-derived (H1, non-masked)** / `native_message_scan.
     total_graph_upserted > 0` (M1) / fingerprint + index set present (L3).
   - leg2 incremental (touch `main.py` + commit): exit 0, no delegation, not full-scan.
   - leg3 hatch `CORTEX_SYNC_BACKEND=python`: delegation fires, `backend=python`, healthy.
   - leg4 `CORTEX_GRAPH_JOURNAL_MODE=required`: native replay lane — `journal.backend
     = "rust-native"`, `mode=required`.
   - leg5 embedded-falkordb expect-fail (`--graph-provider falkordb`, no URI): exit ≠ 0,
     no delegation, honest two-option message + rebuild caveat.
   - Mask list: mirror `sync_orchestrator_parity.py` MASKED_SUMMARY_KEYS + per-run tokens
     (backend, command, lock, snapshot_id, manifest paths, git shas, project-scoped
     collection tokens, tmp roots). **Graph name KHÔNG mask**; `project_id` KHÔNG mask.
   - `parsers` so theo projection (parser, role, status, changed, deleted; bỏ entry
     role=embedding) — python leg có entry embedding-pass thêm (plane ownership khác,
     phase-06 vector pipeline); `vector_embeddings` loại khỏi structural diff cùng lý do.
3. **Parity fix lộ qua leg2**: impact-expansion queries dùng `type(r)` — ladybug 0.20.4
   không có hàm TYPE (repro PyPI ladybug: `LABEL(r)` OK) → `query_impacted_files` chọn
   `LABEL`/`type` theo `store.provider()`.

## Gates — kết quả

- ✅ Harness xanh **2 lần liên tiếp** (anti-flake): `PARITY GATE: PASS (all legs)` ×2.
- ✅ Hatch leg: `backend == "python"` + python plane healthy (đối chứng ngược).
- ✅ Golden dump chứa `schema_fingerprint` + index set (L3);
  `tests/fixtures/sync-plane-golden/ladybug/canonical-dump-sp1golden.json`
  (157 labels, 14 rel tables, 171 indexes, File=6).
- ✅ Không đụng instance user (`cortex`/`default`/`bakatrans`/`phase14-synth` untouched —
  mọi leg dùng instance `p4-parity` + data home trong scratch); scratch xoá cuối phase
  (finalizer `shutil.rmtree`, `--keep` để debug).

## Leg5 note (Q2 FAIL branch)

Embedded falkordb fail-closed message được assert nguyên văn 2 lựa chọn
(`FALKORDB_URI` giữ data / `GRAPH_PROVIDER=ladybug` rebuild) + caveat "no data
migration / rebuilt by a full re-sync" — khớp `EMBEDDED_FALKORDB_UNAVAILABLE`
(graphops.rs) từ phase-02.
