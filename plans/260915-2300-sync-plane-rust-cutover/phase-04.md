# Phase-04 — Parity harness embedded lanes — rev2

Mục tiêu: gate cơ học trước flip — Rust-native vs golden phase-01, đủ lanes, có `.lbug` diff chính thức.

## Changes

1. **`graph-state` bin** (`cortex-graph-driver/src/bin/`, cạnh replay_journal.rs):
   - `graph-state dump <store.lbug> --graph <name> --format canonical`: per label {count, canonical
     key-set + property-hash per node sorted}; edge per type; **thêm field `schema_fingerprint` +
     index set** (L3 — index/schema drift phải hiện trong dump, không chỉ ở preflight phase-03).
     `--graph` bắt buộc: named graph absent/empty trên full-sync leg → FAIL (H1 gate).
   - `graph-state diff <a> <b>`: exit 0 nếu canonical equal; unified diff các key lệch.
   - `lbug` read-only; store đóng sạch/checkpoint trước dump; handle `.wal` (spike.rs:63-79).
2. **Harness** `scripts/rust_parity/sync_plane_native_parity.py` (group B infra):
   - Legs: ladybug full-sync, ladybug incremental (touch 1 file), `CORTEX_SYNC_BACKEND=python` (đối
     chứng hatch), journal-required, embedded-falkordb leg theo nhánh Q2 phase-02 (PASS hoặc expect-fail).
   - Gates per leg: summary masked-equal golden (mask list phase-01.7 — **graph-name KHÔNG mask**;
     assert `summary.backend` đúng leg type); changed-manifest equal; graph-state diff = 0 (full-sync);
     **assert resolved graph name = project_id-derived** (H1); journal conservation; stdout không chứa
     `python-plane delegation` (trừ hatch leg).
   - Mask list mirror sync_orchestrator_parity.py:13-37.
3. **Fixtures**: canonical golden dumps → `tests/fixtures/sync-plane-golden/ladybug/`; README tái-create.

## Gates

- Harness xanh **2 lần liên tiếp** (anti-flake); verdict + log → `reports/phase04-parity.md`.
- Hatch leg: `backend == "python"` + hành vi Python đúng (đối chứng ngược).
- Schema-index drift: golden dump chứa `schema_fingerprint` + index set; harness so sánh (L3).
- Không đụng instance user; scratch xoá cuối phase.

## Out

- Không revive Python parity leg của sync_orchestrator_parity.py (archived phase-08) — so Rust-vs-golden.
