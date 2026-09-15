# Phase 02 — Retire Python query-plane (MCP code lane + doc lane query)

## Mục tiêu

Xoá Python MCP runtime đã được Rust thay thế hoàn toàn, và chuyển mọi đường rollback `CORTEX_MCP_BACKEND=python` thành retired-error — mirror đúng semantics phase-08 analyzer.

## Tiền đề (blockedBy)

- `260915-2027-vector-lane-rust-port` **phase-05 done**: Rust MCP đủ vector lane (remote + local sidecar), `dev mcp start` auto-flip cả 2 server, golden fixtures re-baselined.
- Phase-01 disposition: danh sách xoá chính xác (dự kiến `code-tiny/mcp/**` — unified_mcp, fastmcp_server, language sub-servers, services; `doc-tiny/mcp_graph_rag.py`, `doc-tiny/graphrag_query_langextract.py`; import-only helpers của chúng).

## Việc

1. Xoá per disposition list — grep audit per-dir trước commit: chỉ xoá file KHÔNG được import bởi keep-list (sidecars, parity scripts có thể import vài helper — nếu có, tách helper sang keep hoặc inline).
2. **Flip semantics** `CORTEX_MCP_BACKEND` (cả cortex-dev + docs + wrapper scripts):
   - `python` / giá trị khác → loud error: `"Python MCP plane retired at <commit>; rollback = git revert <tag>"`.
   - missing-binary khi unset → hard error + hint build (không fallback Python).
3. Cập nhật `docs/cutover-runbook.md` (bảng env mục 4/5), `ReadMe.md`, CI workflows còn tham chiếu Python MCP.
4. Commit riêng + tag `python-cleanup-query-plane`.

## Gate / Verification

1. `cargo build --release` + `cargo clippy -D warnings` toàn workspace.
2. MCP smoke: `dev mcp start` (unset) chạy 2 server Rust; `CORTEX_MCP_BACKEND=python` → retired-error đúng message; query smoke unified + mind khớp golden fixtures.
3. Sync smoke không regress (parity suite còn sống chạy xanh).
4. pytest còn lại: passes + skips report rõ (loud-skip policy).

## Rollback

`git revert` tag `python-cleanup-query-plane` — khôi phục toàn bộ cụm.
