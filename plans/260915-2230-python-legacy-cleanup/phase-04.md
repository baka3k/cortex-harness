# Phase 04 — Cleanup phụ trợ: tests chết, scripts obsolete, cortex_harness, installers, requirements

## Mục tiêu

Dọn lớp ngoài runtime: test suites của Python đã chết, scripts tiện ích không còn entrypoint, module harness chỉ còn làm parity reference, prune dependency và build targets.

## Việc (theo disposition list phase-01)

1. **Test suites chết**: `tests/*.py` suite import runtime đã xoá (giữ `tests/fixtures/**` — fixture corpus dùng bởi parity Rust), `code-tiny/tests/**`, `doc-tiny/tests/**`, `code-tiny/testtool/**` phần test Python runtime. Test tiêu thụ fixture JSON → giữ.
2. **Scripts obsolete**: `scripts/mcp-lifecycle.py` (+ `.ps1`) đã port vào cortex-dev (plan dev-make) → xoá; `benchmark_*.py`, `audit_graph_ingest.py`, `check_project_isolation.py`, `smoke_unified_contract.py`, `validate_retrieval.py`, `export_lbug_view.py`, `generate_graph_ingest_scale_fixture.py`, `mcp_runtime_config.py`, `code-tiny/list_db.py` — từng file: còn ai gọi (docs/skills/user workflow) → giữ + inventory; không → xoá.
3. **cortex_harness/**: `dev.py` giữ làm parity reference (tiền lệ phase-08) đến phase-05; `sync_processes.py`, `db_transfer.py`, `storage/**`, `mcp_contract.py`, `project_config.py`, `_phase08_skip.py` — xoá nếu pyexec bridge + parity scripts không còn import (audit quyết định).
4. **installers/**: `config_manager.py`, `registry_manager.py` — kiểm tra Inno Setup / scoop shim / Rust installer có gọi không; không → xoá + port logic còn thiếu (nếu có) sang installer script hiện hành — port nhỏ này thuộc cleanup, không phải runtime port.
5. **code-tiny/{livingdoc,skills,scripts} + run_mcp.sh/mcp.sh/sub.md + doc-tiny residuals**: disposition từng mục (livingdoc = pipeline standalone — hỏi user hoặc giữ theo docs; skills → cập nhật hoặc xoá kèm).
6. **Prune**: `requirements*.txt` theo keep-list; Makefile targets (`parity-fixtures` giữ — nó chạy rust_parity), venv bootstrap chỉ cài dependency của keep-list; CI workflows bỏ job test Python chết.

## Gate / Verification

1. `scripts/audit_python_disposition.sh` exit 0 (không file nào ngoài keep-list).
2. `make build` + `dev doctor` + `make parity-fixtures` xanh trên venv prune.
3. Toàn bộ CI workflows green (self-check nhanh local).
4. Commit riêng + tag `python-cleanup-aux`.

## Rollback

`git revert` tag `python-cleanup-aux`.
