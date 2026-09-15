# Phase 05: Cutover vận hành — flip `dev mcp start`, docs, housekeeping

## Mục tiêu

Đưa vector-lane Rust vào mặc định vận hành một cách an toàn, có rollback, kèm xử lý
2 bug chặn đã phát hiện 2026-09-15.

## Scope

1. **Housekeeping chặn cutover** (bắt buộc trước khi flip):
   - Fix `mind_mcp.list_source_ids` — Binder exception
     `Cannot change parameter expression data type from BOOL to STRING` khi
     `project_id=None`. Pattern `$param IS NULL OR … STARTS WITH $param` lặp ở
     `doc-tiny/mcp_graph_rag.py` (dòng 390, 432, 600, 696) và mirror Rust
     `cortex-mcp/src/mind/graphstore.rs` (dòng 257, 296, 400, 424). Fix: tách nhánh
     query (không truyền param NULL) — **áp dụng đồng bộ 2 phía**, thêm fixture case
     `project_id=None` vào compare_mind.
   - Fix `tests/test_mcp_acceptance_matrix.py` (24 fail): assert script Python đã xoá
     sau analyzer cutover → đổi contract sang `_RUST_ANALYZER_BINARIES`; row `csharp`
     cập nhật `endpoints/database = partial` theo registry hiện tại.
2. **E2E thật trên snapshot instance `cortex`**: boot `cortex-mcp --server unified`
   + `--server mind` (backend auto: remote/offline local tuỳ env của snapshot), chạy
   bộ case thật: semantic_search combined trên lane code (điểm ≠ 0, hit trùng kết quả
   Python), explore_graph hybrid, mind local + remote. So trực tiếp với server Python
   chạy cùng snapshot (dual-run như realworld-stock-test).
3. **Flip vận hành**:
   - `dev mcp start` auto-flip đã có (binary tồn tại → Rust) — xác nhận lại flow sau khi
     cả 2 server đủ tính năng; cập nhật `docs/cutover-runbook.md`: mục "vector lane",
     ma trận env (`CORTEX_MCP_BACKEND`, `CORTEX_EMBED_BACKEND`,
     `CORTEX_MCP_VECTOR_WORKER`), cảnh báo local-sidecar là python process.
   - `dev start` (legacy) giữ nguyên hành vi Python (đúng decision phase-14) — ghi rõ
     trong runbook để operator không nhầm.
   - Installer/release: `cortex-mcp` + `vector_worker.py` + ONNX artifacts vào gói
     (kiểm tra INSTALLER_GUIDE + install-windows).
4. **Docs & memory**: cập nhật ReadMe (mục Graph providers / runtime), wiki nếu có;
   ghi report tổng hợp cutover vào `reports/phase05-*.md`.

## Gate

- [ ] Dual-run snapshot: 0 sai khác cấu trúc, score ≤ 1e-6 trên bộ case thật (kể cả
      explore_graph hybrid với seeds vector ≠ rỗng).
- [ ] 2 bug chặn đã fix + mirror 2 phía + test xanh (pytest MCP suite ≥ trạng thái hiện tại
      — mục tiêu 24 fail ở acceptance matrix về 0).
- [ ] Flip default hoạt động: `dev mcp start` (env unset) boot Rust, tool vector trả kết quả
      thật; `CORTEX_MCP_BACKEND=python` rollback 1 lệnh; 2 flag test tồn tại trong repo.
- [ ] Runbook + INSTALLER_GUIDE cập nhật; report cutover ghi số liệu latency p50/p95
      unified + mind (remote và local).

## Ghi chú

- Sau flip, khối Python vector-lane (`cplus_mcp.py` embed/qdrant path,
  `mcp_graph_rag.py` search path) giữ lại làm parity reference ≥ 1 release trước khi
  cân nhắc archive — cùng chu kỳ rollback như phase-14 đã làm với analyzer.

**Trạng thái 2026-09-15 — DONE (housekeeping + bugfix chặn); flip mặc định chờ graph-track xử lý 6 case explore/expand.** Report: `reports/phase05-cutover-bugfix.md`.
