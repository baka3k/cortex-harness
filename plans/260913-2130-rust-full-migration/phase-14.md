# Phase 14 — WAVE G+H: doc-tiny + cutover vận hành

## Scope A — doc-tiny (6.3k)

- `graphrag_ingest_langextract.py` + `graphrag_query_langextract.py`: langextract = **LLM
  API call** (Gemini/OpenAI) → port reqwest; embedding qua bge-m3 (ONNX chính thức có sẵn —
  parity gate cosine như jina; chỉ lấy dense head); neo4j_loader (neo4rs hoặc giữ — doc
  pipeline dùng neo4j optional).
- `entity_extractors.py`: provider seam đã có (`gliner/langextract/spacy`) — gliner giữ
  sidecar Python, 2 provider còn lại port trực tiếp.
- `mcp_graph_rag.py` (doc MCP tools) — đã nằm trong Phase 13 surface; phần ingest CLI port ở đây.
- `project_contract.py` + extractor/excel pipeline (nếu còn dùng) — kiểm tra usage trước khi port.

## Scope B — Cutover vận hành

1. **Migration dữ liệu local instance**: falkordblite `.rdb` cũ → ladybug (ladybug plan đã có
   hướng); journal v3 Rust đọc được ✓; script `cortex migrate` cho instance người dùng cuối.
2. **Installers**: install-windows.ps1/bat + context menu installers → sinh lại cho binary Rust
   (single file + sidecar .so nếu còn); `make install` path.
3. **CI cutover**: parity suites vào required checks; nightly dual-run so kết quả stock +
   procsample; báo cáo drift tự động.
4. **Kết thúc dual-run**: flip defaults (`CORTEX_RUST_ANALYZER=rust`,
   `CORTEX_MCP_BACKEND=rust`), Python giữ rollback flag 1 release; sau đó archive code
   Python theo từng khối (bắt đầu từ những khối đã ổn định 2 release).
5. **Docs**: ReadMe, INSTALLER_GUIDE, CLAUDE.md/AGENTS.md cập nhật runtime mới.

## Gate

- [x] doc-tiny ingest + query parity trên stock_doc thật (stages deterministic;
      LLM disable đối xứng — reports/phase14-doc-tiny-parity.md).
- [x] Migration script chạy trên local instance thật: `cortex-migrate` — dry-run
      default (15 graphs) + cortex (3) + bakatrans (97,559 nodes / 268,548 rels,
      754MB ladybug store, 37/37 breakdowns src==dst); synthetic round-trip test.
- [ ] 1 kỳ dogfood đầy đủ (sync + MCP + doctor) toàn-Rust trên stock không lỗi trong 1 tuần —
      **vận hành, chờ chạy** (runbook: docs/cutover-runbook.md).
- [x] Rollback flag được test (flip về Python vẫn chạy) — tests/test_phase14_rust_analyzer_flip.py.

**Trạng thái 2026-09-14:** Code cutover hoàn tất — `cortex-migrate` (falkor_boot +
ladybug_writer, dry-run/overwrite, verify counts), flip defaults: `CORTEX_RUST_ANALYZER`
unset → auto-Rust (binary present) / `=python` rollback; `CORTEX_MCP_BACKEND` tương tự
qua `dev mcp start` (`--server unified` :8788 / `--server mind` :8789).
Runbook vận hành: docs/cutover-runbook.md. Report:
reports/phase14-cutover-parity.md.
