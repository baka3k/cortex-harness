# Phase 05 — Cache Bump + Observability + Re-sync Runbook

> Gate ra: AD-08 hoàn tất (cache bump cùng PR payload-change); M6 hoàn chỉnh; runbook dry-run được; README drift sửa.

## Tasks

### 5.1 Cache policy cho engine ANTLR (AD-02)

- `PARSE_CACHE_VERSION` **đã bump ở phase-03** (AD-08/F6 — merge đầu đổi payload). Phase-05 chỉ VERIFY: không còn đường hydration payload cũ; nếu phase-04 đổi thêm shape CallEdge so với phase-03 thì bump tiếp trong PR phase-04.
- Cache vẫn ghi per-file payload (worker output lọc theo file) — dùng cho: skip embed + skip graph write khi incremental (đối chiếu `changed_set`), KHÔNG dùng để thu nhỏ input của worker. Comment tại `_parse_vb6_with_antlr_batch` chốt điều này (review checklist item).
- `parse_cache_version` worker-side: worker ghi nhận vào payload; mismatch → coi như miss.

### 5.2 Observability hoàn chỉnh

- Sync summary mặc định (M6) — đã dựng khung phase 03; bổ trợ:
  - `parse_meta` per-file: `engine`, `fallback_reason`, `module_name`, `resolution_status_counts` (dict đếm theo loại — tính ở resolver).
  - WARNING engine degrade phải 1 dòng, có `reason` (java missing / build failed / worker timeout), không im lặng — sửa luôn pattern `except Exception: pass` tại `vb_common.py:473-474`: bắt hẹp (ImportError/RuntimeError) và ghi `parse_meta.parser_engine="regex_unavailable_ts"` + stderr warning 1 lần per run (không spam per-file).
- `benchmark_vb6_parse_quality.py` hoàn thiện gate M4 (nếu phase-01 chỉ skeleton).

### 5.3 Doctor + docs

- Kiểm tra khái niệm doctor hiện có (`cortex_harness/dev.py`, plans 260820-doctor-*): thêm check `vb6-antlr`: java có? vendor built? jar stamp? → output trạng thái (chỉ báo cáo, không tự build).
- `code-tiny/tools/vb/README.md` sửa drift: bỏ claim "requirements.txt đã bao gồm grammar VB6 tree-sitter"; mô tả engine mới: yêu cầu JDK 17+ + Maven (chỉ khi engine antlr; auto fallback regex), cách build worker, biến môi trường `VB6_ANTLR_*`.
- `requirements.txt` (F10): hiện KHÔNG có bất kỳ package tree-sitter vb nào (kể cả `tree-sitter-vb-dot-net` cho vbnet) → **thêm `tree-sitter-vb-dot-net`** (tồn tại PyPI, đã verify) để error-stats path vbnet hoạt động; KHÔNG thêm tree_sitter_vb6/vba/vbscript (không tồn tại). Xóa import dead `tree_sitter_vb6`/`tree_sitter_vba`/`tree_sitter_vbscript` khỏi `vb_common.py` parser factories? — **Giữ factories** (vba/vbscript dùng) nhưng biến message lỗi rõ hơn ("not installable from PyPI; see README"). `get_vb6_parser` xóa hẳn hoặc no-op raise — quyết định: xóa khỏi `_PARSER_FACTORY` map cho vb6 (engine antlr/regex không cần), giữ hàm kèm docstring chết để tránh import lỗi từ bên ngoài (grep trước khi xóa).

### 5.4 Re-sync runbook

`docs/plans/260917-1200-vb6-antlr-call-graph/resync-runbook.md`:

1. Merge PR cuối → bump version đã vào.
2. Với mỗi project VB6 đã ingest: chạy sync full (không incremental) lần đầu — cache tự miss vì version.
3. Verify mỗi project: dòng `[vb6][summary]` (rate ≥80%?), spot-check `find_callers` 2-3 hàm, đếm POSSIBLE_CALLS (Cypher mẫu kèm sẵn).
4. Rollback: nếu sai dữ liệu → re-run với `--vb6-parser-engine regex` (kết quả = hiện tại) trong khi điều tra.

### 5.5 message_scan compatibility

- Chạy `run_message_scan_pipeline` trên fixture: contract không đổi (payload shape giữ) — chỉ verify, không sửa trừ khi test chỉ ra vỡ.

## Định nghĩa xong

- [ ] Verify cache bump hiệu lực (không hydration cũ) + AD-09 edge re-publication có trong đường incremental
- [ ] WARNING degrade 1 dòng có reason; `except: pass` thu hẹp
- [ ] README/docs/doctor note cập nhật
- [ ] Runbook dry-run trên dev graph của chính repo fixture
- [ ] message_scan fixture xanh
