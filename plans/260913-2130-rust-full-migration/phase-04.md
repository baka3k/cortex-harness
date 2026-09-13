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
  `updated_at`, content_mode, note/summary format "Performs X operation (takes N parameters)"
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

- [x] Graph diff rỗng (ngoài mask) trên stock + ≥1 testdata dir, chế độ FULL.
- [x] Incremental: sửa/thêm/xoá file thật trên bản copy stock → changed-manifest path cho
      kết quả giống Python (cleanup counts khớp).
- [x] Summary JSON cùng schema (`summary-path`).
- [x] clippy `-D warnings`; harness nằm trong CI.

**Trạng thái 2026-09-14:** PASS toàn bộ gate.

- Crate `rust/crates/cortex-analyzer-framework`: `cli` (contract orchestrator
  `_build_analyzer_cmd` — gồm các flag lane vector/message/cache nhận-và-bỏ-qua, contract
  test replay dòng lệnh), `scan` (COMMON_SCAN_EXCLUDE + `_should_ignore_directory` +
  `CORTEX_EXTRA_IGNORE_DIRS` fnmatch normcase-darwin), `manifest` (`load_manifest_paths`
  JSON dict/array/TXT), `ts` (decode `errors="ignore"`, cursor walk, snippet leading
  comments), `semantic` (port `semantic_inference.py` + `call_graph_builder.py` +
  `confidence_scorer.py` — naming/type/body/usage signals, `_build_semantic_note`,
  usage-regex memoized, float parity cho `doc_confidence`), `traits` (`Analyzer` +
  `AnalyzerContext`, store `Option` cho `CORTEX_DISABLE_GRAPH`), `summary` (JSON schema +
  `[SCAN_RESULT]` line byte-identical).
- Crate `rust/crates/analyzer-python`: parse tree-sitter-python 0.25 (walk + docstring +
  signature + base classes + self-fields + entrypoint decorators + imports), semantic enrich
  (mutation như Python), call resolution (arity → self-field type → caller scope), IMPORTS,
  OVERRIDES, write_all `use_full_writers=True files_variant=with_imports`, incremental
  cleanup (DETACH DELETE + UnknownFunction prune), exemplar consumer của `Analyzer` trait.
- `rust/grammar-versions.toml`: pin `tree-sitter-python` 0.25.0 cả 2 bên — phát hiện
  `tree-sitter-languages` 1.10.2 wheels build trên ABI cũ, **raise TypeError** với
  tree-sitter ≥0.25 nên `_get_python_parser` fallback sang `tree_sitter_python` crate riêng;
  pin theo grammar crate, không theo tsl.
- **Sửa latent bug phase-03**: `prepare_project_scope_parameters` Rust không đệ quy vào
  `$rows` (array of row-map) → node viết từ Rust thiếu `project_id_normalized`
  (`File` nodes) trong khi Python inject qua `enrich_project_scope` đệ quy. Fixture phase-03
  không bắt được vì rows fixture đã mang sẵn `project_id_normalized` ("stock" normalize
  no-op). Đã fix `project_scope.rs` (enrich đệ quy list + sibling `*_normalized` kể cả
  None) — đúng cho cả 2 store.
- **python_analyzer.py** (tham chiếu): call rows giờ mang `project_id` tường minh — cùng giá
  trị journal metadata project_id trong orchestrator run, giữ journal-less run hợp lệ
  (`_require_call_project_scope` fail-closed). Rust mirror.
- Khác biệt scope có chủ đích (ghi trong lib.rs/main.rs): embedding/Qdrant không port (key
  decision #3); message scan + `--enable-flows` + `--enable-llm-summary` là plane Python;
  `--config` nhận-và-bỏ-quá (Rust orchestrator truyền explicit args).

**Parity kết quả** (`reports/phase04-analyzer-parity.md`, falkordb local 127.0.0.1:6379):
- FULL testdata (`tests/fixtures/python-analyzer/`, 2 files khó: decorators/nested
  class/unicode/self-field resolution): nodes 26/26, edges 38/38, diff=0
- FULL stock thật (115 .py): nodes 1149/1149, edges 2990/2990, diff=0
- Incremental trên copy 30 files stock (sửa/thêm/xoá + manifests): nodes 517/517,
  edges 1382/1382, diff=0, cleanup deleted_nodes py=3 rust=3
- `[SCAN_RESULT]` byte-identical cả 2 corpus; summary JSON schema đủ 15 trường
- Thời gian: stock python 4.7s vs rust 1.5s (không đặt gate tuyệt đối)
