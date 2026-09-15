# Phase 05 — Final sweep: archive parity infra, chốt forced-Python inventory, bookkeeping

## Mục tiêu

Chốt trạng thái cuối: mọi Python còn lại thuộc inventory có chủ đích, docs nhất quán, các plan liên quan cập nhật.

## Tiền đề

- Phase-02/03/04 đã commit; **dogfood sign-off 7 ngày của umbrella** (`260913-2130-rust-full-migration` + analyzer runbook) PASS — parity/golden infra chỉ archive sau mốc này.

## Việc

1. **Archive parity infra** (tiền lệ phase-08: header + giữ fixtures):
   - `scripts/rust_parity/**`: thêm header "PY side archived at <commit>; fixtures JSON = golden contract" vào script còn chạy được; script import module đã xoá → chuyển `archived/` (đã có `scripts/archived/`).
   - `scripts/rust_mcp/` record_*/compare_*/contract: giữ workers (embed_worker, gliner_sidecar, vector_worker — runtime live) + fixtures; record/compare đánh giá: còn import module sống → giữ; không → archived/.
   - `cortex_harness/dev.py`: nếu parity suite cuối không còn import → xoá; còn → giữ + header parity-reference.
2. **Forced-Python inventory** — bảng cuối ghi vào `docs/cutover-runbook.md` (mục mới) + `reports/final-sweep.md`:
   | File/glob | Lý do giữ | Trigger/owner |
   Đảm bảo: sidecars (embed/gliner/vector), clang plane, csharp roslyn worker liên quan, harness scripts, journal consumer (nếu giữ), installers py (nếu giữ), parity infra.
3. **Docs nhất quán**: ReadMe, INSTALLER_GUIDE, docs/cutover-runbook — rollback story = git revert; xóa mọi hướng dẫn chạy Python runtime đã xoá; env table chỉ còn flag sống.
4. **Venv/uv final**: `make` bootstrap chỉ cài dependency keep-list; smoke `dev doctor` trên môi trường sạch (xóa `.venv`, bootstrap lại).
5. **Bookkeeping 3 plan**:
   - Umbrella `260913-2130-rust-full-migration`: status note "Python legacy cleanup closed by 260915-2230-python-legacy-cleanup; remaining Python = forced inventory".
   - `260915-2027-vector-lane-rust-port`: note phase-04 vector_worker.py thuộc forced inventory (không phải cleanup target).
   - `260915-analyzer-layer-rust-cutover`: note tiếp nối retired-semantics sang MCP/sync plane.
6. Tag cuối `python-cleanup-final`; commit `reports/final-sweep.md` (LOC xoá thực tế per phase, inventory count, smoke evidence).

## Gate / Verification

1. `find . -name "*.py"` (trừ `.venv`, `tests/fixtures` fixture corpus) — 100% dòng trả về xuất hiện trong inventory.
2. Đầy đủ gates kỹ thuật của phase trước vẫn xanh (build, clippy, smokes, parity còn sống).
3. Docs grep: không còn hướng dẫn gọi file .py đã xoá.
4. Plan status → done sau khi dogfood sign-off + sweep report commit.

## Rollback

Chỉ docs/archive/tag — revert git nếu cần.
