# Phase 10 — WAVE E2: dev CLI (clap) + storage layer

## Scope A — `dev` CLI (5.1k, 21 commands)

`rust/crates/cortex-dev/` với clap; giữ **command names + options y nguyên** (scripts,
Makefile, installers, tài liệu và thói quen người dùng phụ thuộc): build, install,
doctor, status, init, storage-init/layout/migrate-layout/backup/stop, infra-up/down,
export/export-db/import/import-db, sync (code|doc), journal, mcp, mcp-gates, start/stop,
ignore, harness, help.

- Config resolution: `.cortext-harness/config/*.json`, active-project semantics,
  `--project-dir` walk-up, `CORTEX_HARNESS_CONFIG_PATH` env — replicate `dev.py` logic.
- `dev init` flow (scaffold + active flip deactivating others).
- Output format: giữ nguyên text shape của status/doctor/storage-layout (scripts có thể parse).

## Scope B — storage layer (5.4k) — phần concurrency là trọng tâm

| Module | LOC | Nội dung port |
|---|---|---|
| `gateway.py` | 954 | **Generation-pinned store access**: bounded lanes (`BoundedLane`, `LaneLimits`), admission, owner lifecycle, ingestion job state machine |
| `generation.py` | 452 | generation pinning — store swap an toàn giữa 2 generation |
| `admission.py` + `lease.py` | ~270 | StorageLease (portalocker → fs2/fd-lock + flock semantics đã học ở ladybug), BoundedLane |
| `config.py` + `targets.py` + `factory.py` | ~1.7k | env/instance resolution, EffectiveStorageTarget, provider selection (local/remote, falkordb/ladybug/neo4j), active flip |
| `layout.py` + `migration.py` | ~370 | instance layout `~/.cortext-harness/v1/instances/...`, ladybug store file naming (đã thấy `ladybug_store_file_name` dùng ở driver) |
| `qdrant.py` + `qdrant_remote.py` + `remote_probe.py` | ~1.1k | local embedded + remote REST clients, health probes |
| `contracts.py` + `errors.py` | ~320 | dataclasses/error codes |

## Stress test (bắt buộc — rủi ro cao nhất của phase)

- Multi-process: N process đua lease trên cùng instance → đúng 1 winner (so Python behavior).
- Generation swap khi có reader đang pinned generation cũ → reader không thấy store mới.
- BoundedLane saturation + timeout → admission rejection đúng code.
- Kill -9 giữa lease → recover_expired_leases khớp.

## Gate

- [x] `dev --help` surface khớp (21 commands); status/doctor/storage-layout output text khớp.
- [x] Stress suite 4 kịch bản trên pass; so hành vi với Python trên cùng kịch bản.
- [x] `dev doctor` tất cả check ok với storage Rust.
- [ ] `dev sync code` (orchestrator Phase 09) chạy qua CLI Rust end-to-end trên stock —
      qua Python orchestrator bridge hiện tại OK (cortex-sync swap test riêng ở phase 09);
      gate cuối gắn cutover 1-tuần dogfood.

**Trạng thái 2026-09-14:** Scope A `cortex-dev` 67/67 parity checks (help surface 55/55,
status/doctor/storage-layout text, init scaffold + active-flip byte-equal, ignore) —
reports/phase10-devcli-parity.md. Scope B `cortex-storage` 17 modules + stress 4/4 khớp
Python 16/16 điểm (lease race 8 process 1 winner, generation swap pinned reader,
BoundedLane saturation + timeout codes, kill -9 recover_expired_leases) —
reports/phase10-storage-parity.md.
