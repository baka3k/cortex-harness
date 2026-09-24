# Phase 01 — Port module YAKE rule generation vào doc-tiny

> plan-id: 260924-1642-yake-dynamic-rules-doc-sync · phase: 01 · đỏ-team rev: C2/C3/m1/m2

## Mục tiêu

Module tự chứa `doc-tiny/yake_rules.py`: text → YAKE keywords → EntityRuler JSON (`{"patterns":[{"label","pattern"}]}`), port từ prototype `/Users/hieplq1.aip/test/Yake/src/yake_vi/` (`extractor.py`, `ruler_export.py`, `text.py`, CLI `__main__.py`). Không import graphrag_ingest_langextract (tránh kéo heavy chain sentence-transformers/gliner).

## Thay đổi

### 1. `doc-tiny/yake_rules.py` (mới)

- `clean_text(text)` — NFC normalize, strip control chars, collapse whitespace (port `text.py`).
- `YakeRuleExtractor` dataclass: `language="en"`, `max_ngram=3`, `top=150`, `dedup=False`; wrap `yake.KeywordExtractor(lan=..., n=..., top=..., dedupLim=False)`; `extract(text) -> list[(phrase, score)]` sort score ascending (port `extractor.py`; default `en` theo D6, **dedupLim=False đúng prototype** — sửa red-team m2).
- `infer_entity_label(phrase, default="KEYWORD")` — giữ nguyên bảng heuristics (port `ruler_export.py:10-37`).
- `keywords_to_ruler_patterns(keywords, *, default_label="KEYWORD", use_heuristics=True, min_length=2)` — dedupe casefold, bỏ phrase ngắn (port `ruler_export.py:40-62`).
- `build_ruler_json(keywords, ...)` → `{"patterns":[...]}`.
- `load_text(path)` — chỉ cho CLI/manual: PDF qua `pypdf`, `.txt/.md` utf-8, `.docx`, `.pptx` (lazy import); `.xlsx` → `""`. **Đường hot của ingestor không dùng hàm này** (D1).
- `safe_source_key(source_id)` — cùng quy tắc sanitize như ingestor `_safe_source_id` (ingestor:644-646, relpath-based) — ingestor tự tính source_id rồi truyền vào, hàm này chỉ sanitize cho file name (sửa C3).
- `rule_file_for(rules_dir, source_id)` → `rules_dir / f"ruler.from-yake.{safe_source_key(source_id)}.json"`.
- `ensure_rule_file(rules_dir, source_id, text, *, language, top, max_ngram) -> Path | None` — API chính cho ingestor: extract từ **text có sẵn** → patterns; **patterns rỗng → KHÔNG ghi + xóa file cũ của source nếu tồn tại, trả None** (sửa C2); ngược lại ghi file, trả path.
- `prune_rule_files(rules_dir, keep_source_ids)` — xóa mọi `ruler.from-yake.*.json` trong `rules_dir` mà source không còn (folder/full sync).
- `prune_sources(rules_dir, source_ids)` — xóa rule file của các source đã bị delete (dùng bởi dev.py, D8).
- `merge_rule_files(paths) -> dict` — gộp patterns, dedupe `(label, pattern.casefold())` (test/debug).
- CLI `main()` mirror `yake-vi`: `--file/-f`, `--language/-l`, `--top/-t`, `--n`, `--format text|ruler`, `-o/--output`, `--ruler-label`, `--no-ruler-heuristics`; thêm `--rules-dir` (kèm `--prune`), `--prune-sources ID [ID...]`.

### 2. `doc-tiny/requirements.txt`

Thêm `yake>=0.7` (resolve 0.7.3 + jellyfish compiled + segtok + tabulate — không pure-python, red-team m1; pin những gì test).

### 3. Cài vào venv hiệu dụng

`uv pip install --python .venv/bin/python "yake>=0.7"` (venv hiệu dụng = repo root `.venv`, doc-tiny không có venv riêng — research F3). Verify: `.venv/bin/python -c "import yake"` và import được `yake_rules` qua path doc-tiny.

### 4. Dọn prototype artifact

Xóa `doc-tiny/rules/ruler.from-yake.json` (untracked, sinh thủ công — scheme per-project thay thế). Không đụng 3 file ruler tĩnh đã commit.

## Tests — `doc-tiny/tests/test_yake_rules.py` (mới, unittest + plan-id docstring)

Importlib-load module từ path như `test_ingest_input_ignore.py:21-33` (doc-tiny/tests không có conftest — red-team m5):

1. `test_extract_returns_score_sorted_keywords` — text Anh có "Digital Key Framework", "NFC", "ISO 18013-5" → keywords không rỗng, ascending.
2. `test_infer_entity_label_heuristics` — "RFC 5280"→STANDARD, "NFC"→TRANSPORT, "AES-256"→CRYPTO, "Technical Specification"→DOCUMENT, "random thing"→KEYWORD.
3. `test_keywords_to_ruler_patterns_dedupe_and_min_length`.
4. `test_ensure_rule_file_skips_empty_and_deletes_stale` — text rỗng/không keyword → không tạo file; file cũ của source bị xóa (C2).
5. `test_ensure_rule_file_naming_relpath_source` — source_id `"docs/a spec.pdf"`-style sau sanitize → file name duy nhất, khác `"docs/b spec.pdf"` (C3 collision).
6. `test_prune_rule_files_and_prune_sources` — keep-list prune + delete-list prune.
7. `test_cli_ruler_output` — `main([...])` ghi JSON hợp lệ (txt tạm).
8. `test_load_text_skips_xlsx`.

## Definition of Done

- `pytest doc-tiny/tests/test_yake_rules.py` pass; không cần spacy/gliner/model.
- `python doc-tiny/yake_rules.py --file doc-tiny/testdata/CCC-TS-101-Digital-Key-R3-1.2.1-APPROVED.pdf -l en --top 150 --format ruler -o /tmp/r.json` → ~100–150 patterns (parity xấp xỉ artifact prototype — yake 0.7.3 vs 0.7.1 của prototype có thể lệch nhẹ, chấp nhận).
- `yake` import được trong repo `.venv`.
