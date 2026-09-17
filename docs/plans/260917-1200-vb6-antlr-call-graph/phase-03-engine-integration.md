# Phase 03 — Adapter + Engine Dispatch + Fallback Cascade

> Gate ra: `--vb6-parser-engine auto|antlr|regex` hoạt động; M1 ở mức payload (golden tests chạy qua engine antlr); M6 (sync log in engine thật + resolution rate mặc định).

## Tasks

### 3.1 `code-tiny/tools/vb/vb6_antlr_adapter.py` (hoàn thiện từ bản spike)

Mirror `vb_roslyn_adapter.py` cấu trúc:

- `ensure_worker_built()`: thread-lock (module-level lock như roslyn), check stamp file (jar mtime + vendor hash) → `mvn -q -p vendor/proleap-vb6-parser,vb6-antlr-worker package -DskipTests`; build lỗi → RuntimeError với output tail.
- `parse_vb6_files_with_antlr(root, files, *, timeout_sec, file_timeout_ms, parse_cache_version, verbose) -> (payloads_by_rel, errors_by_rel, worker_meta)`:
  - Đọc `.vbp` gần root nhất (hoặc tất cả .vbp nếu nhiều — ghi worker_meta), materialize `.frm/.ctl` → temp `.cls` (Q5), manifest, subprocess, parse stdout JSON.
  - Trả payload đúng shape `_is_valid_payload_shape` + `parse_meta` hoàn chỉnh: `parser_language="vb6_antlr"`, `parser_engine="antlr"`, `requested_engine`, `workspace_kind="vbp"`, `resolution_source="asg"`.
- Timeout tổng (subprocess) theo pattern roslyn (`vbnet_roslyn_timeout_sec` tương đương → `vb6_antlr_timeout_sec`).

### 3.2 Engine dispatch trong `vb_analyzer_base.py`

- Thêm args: `--vb6-parser-engine {auto,antlr,regex}` (default `auto`), `--vb6-antlr-timeout-sec`, `--vb6-antlr-file-timeout-ms` (mặc định theo env `VB6_ANTLR_*` như vbnet).
- `_parse_vb6_with_antlr_batch()` mirror `_parse_vbnet_with_roslyn_batch()` (`vb_analyzer_base.py:368-508`):
  - Cache-miss check per-file NHƯNG worker vẫn nhận toàn bộ files (AD-02) — chú thích rõ tại chỗ để reviewer không "tối ưu" thành cache-miss-only.
  - Per-file: payload worker không hợp lệ/error → `parse_vb_file(abs_path, root, parse_fn, "vb6", fallback_reason=...)` (regex cascade) + `_ensure_parse_meta_defaults(parser_engine="antlr_fallback_regex", ...)`.
- Dispatch trong `build_call_graph` (`vb_analyzer_base.py:584-599` vùng `dialect == "vbnet"`): thêm nhánh `dialect == "vb6" and vb6_parser_engine != "regex"`.
- `auto`: thử build worker (nhanh nếu đã built); java/maven thiếu → **WARNING rõ ràng một dòng** (`[vb6][engine] antlr unavailable (reason), falling back to regex`) + toàn bộ chạy regex. Kết quả engine thật ghi vào `parse_meta.parser_engine` per-file.

### 3.3 Regex fallback không regress

- `parse_vb_file` đường regex giữ nguyên hành vi cho vba/vbscript (chúng vẫn gọi nó) — chỉ sửa những gì phase 04 yêu cầu ở tầng resolver, không đụng extraction regex.
- Bảng test: `tests/test_vb6_engine_dispatch.py` — auto-fallback khi jar thiếu (fake path), engine flag override, parse_meta.engine phản ánh đúng.

### 3.4 M6 — sync log mặc định

Ở `build_call_graph`, sau `resolve_calls`/resolver (phase 04): dòng log KHÔNG điều kiện verbose:

```text
[vb6][summary] engine=antlr files=N ok=M fallback=K callsites=C resolved=R possible=P rate=xx.x%
```

- Engine lấy từ đa số parse_meta; hiển thị cả mix nếu có (`engine=antlr+regex_fallback(3)`).

## Định nghĩa xong

- [ ] `--vb6-parser-engine antlr` chạy corpus fixture qua worker, payload đúng shape, hydrate cache không vỡ (dùng lại `_hydrate_payload`)
- [ ] Fallback per-file cascade khi worker lỗi 1 file (test giả lập)
- [ ] `auto` fallback + WARNING khi Java thiếu
- [ ] Golden tests phase-01 chạy với engine=antlr đạt M1 mức payload
- [ ] **`PARSE_CACHE_VERSION` bump trong PR của phase này** (AD-08 sửa theo F6 — phase-03 là merge đầu đổi payload: parse_meta engine/fallback fields)
- [ ] Dòng summary mặc định xuất hiện (M6)
