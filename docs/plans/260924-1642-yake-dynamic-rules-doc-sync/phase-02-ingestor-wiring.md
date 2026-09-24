# Phase 02 — Ingestor: YAKE pre-pass + GLiNER×EntityRuler merge

> plan-id: 260924-1642-yake-dynamic-rules-doc-sync · phase: 02 · red-team rev: C1/C2/M2/M3/M4/m4/m7

## Mục tiêu

Trong `doc-tiny/graphrag_ingest_langextract.py` + `doc-tiny/entity_extractors.py`: (a) sinh rule động per-source **từ text đã load**, trước khi extraction chạy; (b) provider `gliner` merge EntityRuler matches (rule tĩnh + động, union) qua **ruler-only sidecar `spacy.blank("en")`** (không cần `en_core_web_sm` — model không có trong venv, red-team C1); (c) graceful degradation.

## Thay đổi

### 1. `entity_extractors.py` — thêm (không sửa hàm hiện có trừ chỗ ghi chú)

```python
def build_ruler_pipeline(ruler_sources: list[str]):
    # spacy.blank("en") + entity_ruler(overwrite_ents không áp dụng — blank không có NER)
    # nạp TẤT CẢ nguồn: mỗi entry là file JSON hoặc dir (glob *.json top-level)
    # loader: skip file 0 patterns với warn (KHÔNG raise — sửa C2; build_spacy_pipeline
    #         cho provider spacy giữ nguyên semantics raise cũ để không đổi hành vi)
    # trả None nếu tổng patterns = 0

def ruler_match(ruler_nlp, text) -> List[Dict[str, Any]]:
    # ruler_nlp(text).ents → {"name","type","start_char","end_char","confidence":1.0}
    # blank pipeline ⇒ ents chứa THUẦN ruler matches, không có NER (sửa M2)

def merge_ruler_gliner(ruler_entities, gliner_entities) -> List[Dict[str, Any]]:
    # ruler trước; bỏ gliner entity trùng (name.casefold(), type); giữ gliner riêng
```

Ghi chú (m3): dedupe này chỉ đúng ở tầng merge; tầng `build_graph_components_from_entities` merge tiếp theo normalize-key riêng và giữ surface name dài nhất — precedence không phải guarantee toàn đường ống, chấp nhận.

### 2. Args mới (argparse ~L1117)

```
--yake-rules / --no-yake-rules   (default: env YAKE_ENABLED qua _env_flag — entity_extractors.py:79-88 đã có)
--yake-language    (default: env YAKE_LANGUAGE hoặc "en")
--yake-top         (int, default: env YAKE_TOP hoặc 150)
--yake-max-ngram   (int, default: env YAKE_MAX_NGRAM hoặc 3)
--yake-rules-dir   (default: <script_dir>/rules/from-yake/<safe(project_id or "default")>)
--ruler-json       ĐỔI thành action="append"  (back-compat: dùng 1 lần vẫn OK — sửa M4)
```

### 3. Pre-pass `ensure_yake_rules(text, source_id, args)` — gọi tại chỗ text đã load (sửa M3)

- **Folder mode** (main ~L1266-1298): đầu loop, sau khi có file list từ `_iter_input_files` (ingestor:659-679 — KHÔNG phải `_find_doc_files` của dev.py, m7) và trước loop process: `prune_rule_files(yake_rules_dir, keep={safe_source_id(f)})`. Trong loop, ngay sau khi load text của từng file (~L1286) và tính `source_id` (~L1282-1285): gọi `ensure_yake_rules(raw_text, source_id, args)` → log `yake rules: <n> patterns -> <path>` hoặc `yake rules: 0 patterns (skipped)`.
- **Single-file branches** (L1300-1423, mỗi format một nhánh load raw_text): sau khi load, gọi tương tự với source_id của nhánh đó. `--raw-text`/`--xlsx`: không pre-pass (by design).
- Toàn bộ wrap try/except (ImportError + Exception) → warn 1 dòng rõ ràng ("dynamic yake rules skipped: ..."), KHÔNG fail sync (D4).
- Ordering guarantee: rule của source X được ghi trước khi `process_text(X)` chạy — cùng tiến trình, tuần tự (red-team m10 xác nhận không đường nào extraction chạy trước).

### 4. Ruler hiệu dụng cho provider gliner — UNION (sửa M4)

```python
ruler_sources = list(args.ruler_json or [])            # append — user files/dirs
if yake_enabled and yake_rules_dir exists:
    ruler_sources.append(str(yake_rules_dir))           # per-project dynamic dir
```

- Chỉ khi `args.entity_provider == "gliner"` và `ruler_sources` không rỗng: `ruler_nlp = build_ruler_pipeline(ruler_sources)` (lazy — build một lần trước extraction đầu, guarded → warn + None khi fail).
- Truyền `ruler_nlp` xuống `process_text(..., ruler_nlp=None)`.

### 5. Gắn vào 2 đường extraction trong `process_text` (L695-853)

- **Single path — ƯU TIÊN LÀM/TRẢI NGHIỆM TRƯỚC** (flow thật: dev.py luôn `--no-batch`, dev.py:1220 — m4): `build_graph_components` thêm param `ruler_nlp=None`; nhánh `provider == "gliner"`: sau `extract_entities_gliner(...)`, nếu `ruler_nlp`: `entities = merge_ruler_gliner(ruler_match(ruler_nlp, text), entities)`.
- **Batch path** (L776-818): sau `extract_entities_gliner_batch(...)`, mỗi paragraph merge tương tự trước `build_graph_components_from_entities`.
- Provider `spacy`/`gemini`/`langextract`: KHÔNG đổi (spacy vẫn dùng `--ruler-json` qua `build_spacy_pipeline` như cũ). `process_xlsx_structured`: không đổi.

## Tests

### `doc-tiny/tests/test_gliner_ruler_merge.py` (mới)

1. `test_build_ruler_pipeline_blank_no_model` — 2 file patterns (1 file rỗng bị skip + warn) trong tmp dir → pipeline trả về, `nlp.pipe_names` chỉ có `entity_ruler` (không `ner`) — chạy được mà KHÔNG cần `en_core_web_sm` (C1/M2 regression guard).
2. `test_merge_ruler_gliner_prefers_ruler` — ruler `[("NFC","TRANSPORT")]` + gliner `[("nfc","TRANSPORT",0.4), ("Alice","PERSON",0.9)]` → 2 entities, NFC confidence 1.0.
3. `test_merge_ruler_gliner_diff_type_kept` — `("aes","CRYPTO")` vs `("AES","TECH")` → giữ cả 2.
4. `test_ruler_match_spans` — real blank pipeline + 2 patterns mini → spans/name đúng.

### `doc-tiny/tests/test_yake_prepass.py` (mới, importlib-load ingestor)

5. `test_ensure_yake_rules_uses_loaded_text` — gọi với text có sẵn (không file I/O PDF) → rule file sinh đúng tên `<rules_dir>/ruler.from-yake.<safe_source_id>.json`.
6. `test_ensure_yake_rules_empty_text_no_file` — text rỗng → không file, không raise (C2).
7. `test_folder_prepass_prunes_before_processing` — tmp dir có 2 rule file cũ, folder input 1 file → pre-pass prune còn 1 (verify bằng gọi trực tiếp hàm pre-pass tách riêng).
8. `test_prepass_degrades_gracefully` — monkeypatch `yake_rules.ensure_rule_file` raise → warn, không raise.
9. `test_single_path_gliner_merges_ruler` — `build_graph_components(text, "gliner", gliner_model=FakeModel, ruler_nlp=real_blank_ruler)` → nodes chứa cả entity GLiNER-fake lẫn ruler match, dedupe đúng (single path — m4).

## Definition of Done

- Các test trên pass; `pytest doc-tiny/tests/` toàn bộ không regression (đặc biệt spacy provider tests giữ nguyên).
- KHÔNG cần `en_core_web_sm` cho bất kỳ test/DOM nào của phase này.
