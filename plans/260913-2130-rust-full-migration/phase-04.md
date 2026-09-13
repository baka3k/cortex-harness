# Phase 04 — WAVE D TEMPLATE: Analyzer framework + python analyzer

**Phase quan trọng nhất của Wave D**: dựng *khung analyzer Rust* + port `python_analyzer.py`
(2.2k) làm template; mọi analyzer khác (Phase 05–08) chỉ là điền nội dung vào khung này.

## 1. Analyzer framework (`rust/crates/cortex-analyzer-framework/`)

CLI contract **giữ nguyên từng chữ** với bản Python (orchestrator `incremental_sync.py`
gọi được cả 2 backend bằng đúng lệnh — đã thấy trong log sync thật):

```
--root --project-id --project-name --commit-sha-before --commit-sha-after
--graph-provider --falkordb-graph [--falkordb-uri]
--incremental --changed-files-manifest <path> --deleted-files-manifest <path>
--disable-message-scan --verbose [--embed-model] [--sync-mode]
```

Thành phần khung:
- `Analyzer` trait: `detect(root) -> bool`, `run(ctx) -> AnalyzerResult` (files scanned,
  nodes written, edges written, duration), `language()`.
- `AnalyzerContext`: GraphStore (Phase 03), journal (Phase 02), manifests reader
  (changed/deleted files JSON), project scope resolver, quality policy config.
- Tree-sitter native: workspace deps `tree-sitter` + grammar crates; **grammar version pin
  file** (`grammar-versions.toml`) đối chiếu với `tree-sitter-languages` wheels Python dùng.
- Symbol extraction helpers (dùng chung: qualified_name builder, scope stack, line ranges).
- Structured JSON summary output giống `--summary-path` của orchestrator.

## 2. Python analyzer port (`rust/crates/analyzer-python/`)

Port `tools/python/python_analyzer.py` (2.2k) + phần dùng chung của nó:
- Parse `.py` bằng tree-sitter (grammar `tree-sitter-python`), extract: Module/File, Class,
  Function (kèm arity), imports, CALLS edges, decorators → labels/schema của CODE_GRAPH_SCHEMA.
- Node properties giữ nguyên tên field (`project_id`, `project_id_normalized`, `repo`,
  `updated_at`, `content_mode`, note/summary format "Performs X operation (takes N parameters)"
  — **format chuỗi này phải byte-identical** vì nó được embed vào vector sau này).
- Incremental: đọc changed/deleted manifests, cleanup graph cho deleted files
  (`deleted_nodes=740` semantics như log sync thật), `repo_file_edges` batched writes.
- FULL parity với `--disable-message-scan` trước; message scan sau (flag riêng).

## 3. Parity harness (mẫu chuẩn Wave D)

`scripts/rust_parity/analyzer_parity.py`:
- Chạy analyzer Python và Rust trên **repo stock thật** (116 files) + testdata, 2 graph riêng.
- Dump cả 2 graph: nodes + rels + properties (mask `updated_at`, `_graph_id`).
- So exact → báo cáo diff theo label/property.
- Tích hợp CI: `pytest` wrapper gọi 2 binary.

## Gate

- [ ] Graph diff rỗng (ngoài mask) trên stock + ≥1 testdata dir, chế độ FULL.
- [ ] Incremental: sửa/thêm/xoá file thật trên bản copy stock → changed-manifest path cho
      kết quả giống Python (cleanup counts khớp).
- [ ] Summary JSON cùng schema (`summary-path`).
- [ ] clippy `-D warnings`; harness nằm trong CI.
