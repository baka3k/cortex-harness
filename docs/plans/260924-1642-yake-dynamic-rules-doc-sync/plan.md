---
title: "YAKE Dynamic Rules cho doc sync — sinh rule động theo tài liệu trước GLINER + GLiNER×EntityRuler merge"
status: complete
created: 2026-09-24
completed: 2026-09-24
mode: hi-plan --full
scope: "Pipeline ingest tài liệu doc-tiny: (1) port logic sinh keyword→EntityRuler JSON từ prototype /Users/hieplq1.aip/test/Yake (yake_vi) thành module doc-tiny/yake_rules.py tự chứa; (2) graphrag_ingest_langextract.py thêm yake pre-pass (sinh ruler.from-yake.<source>.json vào doc-tiny/rules/from-yake/<project>/ trước khi extraction chạy, dùng text đã load) + merge EntityRuler×GLiNER trên đường provider gliner (mặc định) qua ruler-only sidecar spacy.blank; (3) cortex_harness/dev.py chỉ wiring vùng sync doc: base_cmd flags từ env YAKE_* + option --yake/--no-yake + prune rule khi doc bị xóa; (4) docs + tests. KHÔNG đụng: graph schema/writer code-tiny, xlsx structured pipeline, MCP query layer, Qdrant/Neo4j write path, sync code region của dev.py"
blockedBy: []
blocks: []
relatedPlans:
  - 260821-2115-dev-sync-code-windows
sources:
  - docs/plans/260924-1642-yake-dynamic-rules-doc-sync/research-digest.md
  - docs/plans/260924-1642-yake-dynamic-rules-doc-sync/red-team.md
  - /Users/hieplq1.aip/test/Yake (prototype yake_vi: src/yake_vi/{__main__,extractor,ruler_export,documents,text}.py)
  - doc-tiny/graphrag_ingest_langextract.py
  - doc-tiny/entity_extractors.py
  - doc-tiny/rules/ (ruler.combined.json, ruler.crypto.json, ruler.sample.json, ruler.from-yake.json — prototype artifact)
  - cortex_harness/dev.py (vùng sync doc: _sync_doc_folder L1175-1330, sync_doc L3616, sync_doc_all L3686)
  - doc-tiny/Readme.md; ReadMe.md; .gitignore
---

# YAKE Dynamic Rules cho doc sync — sinh rule động trước GLINER

## Overview

Hiện trạng (research-digest.md F1–F8, red-team m10 verify chéo): `dev sync doc` chạy doc-tiny ingestor với `--entity-provider gliner` (mặc định) và `--no-batch` (dev.py:1220 → đường single `build_graph_components` là flow thật). Các rule thực thể đặt tại `doc-tiny/rules/` (ruler.combined/crypto/sample.json — commit af57bb0) là **tĩnh** và **đường GLINER không bao giờ đọc chúng** — `--ruler-json` chỉ dùng khi `entity-provider=spacy` (`entity_extractors.py:135-174`, gọi tại ingestor:715/1277). File `doc-tiny/rules/ruler.from-yake.json` (untracked) là artifact chạy tay prototype `yake-vi` — không nằm trong workflow.

Nhu cầu: khi `dev sync doc` đẩy tài liệu vào, hệ thống **tự chạy YAKE trên tài liệu truyền vào → sinh rule động → đặt vào `doc-tiny/rules/` → rồi mới chạy GLINER**, và GLINER phải thực sự tiêu thụ các rule đó (cả rule tĩnh lẫn động).

Prototype `/Users/hieplq1.aip/test/Yake` (package `yake_vi`) đã chứng minh logic: `yake.KeywordExtractor` → top keywords → `{"patterns":[{"label","pattern"}]}` với label heuristics (STANDARD/TRANSPORT/CRYPTO/KDF_HASH/CERT/DOCUMENT/ALGORITHM/KEYWORD) — format khớp validator của `build_spacy_pipeline` (research F7).

### Bài toán then chốt (2 mắt xích)

| # | Mắt xích | Hiện trạng | Plan |
|---|---|---|---|
| 1 | Sinh rule động theo tài liệu, TRƯỚC khi GLINER chạy | ❌ không tồn tại trong workflow (chỉ chạy tay bằng yake-vi) | P1+P2: yake pre-pass in-process dùng text đã load |
| 2 | GLINER tiêu thụ rules (tĩnh + động) | ❌ `--ruler-json` bị bỏ qua trên provider gliner | P2: ruler-only sidecar (`spacy.blank("en")`) + merge với GLiNER |

## Scope Challenge (3 câu)

1. **Giải quyết gì?** Biến `doc-tiny/rules/` từ kho rule tĩnh chết thành **rule động sinh theo corpus đang sync** + lấp gap đường ống: rule (tĩnh + động) phải ảnh hưởng kết quả extraction trên provider mặc định (gliner). Không làm lại extraction engine.
2. **In/Out?** IN: `doc-tiny/yake_rules.py` (mới), `doc-tiny/graphrag_ingest_langextract.py` (pre-pass + merge, vùng process_text/main), `doc-tiny/entity_extractors.py` (thêm `build_ruler_pipeline` + `ruler_match` + `merge_ruler_gliner`), `doc-tiny/requirements.txt` (+`yake`), `cortex_harness/dev.py` (chỉ vùng sync doc: `base_cmd`, options `sync doc`, prune khi xóa doc), `.gitignore`, `doc-tiny/tests/`, `tests/`, Readme×2. OUT: graph schema/writer code-tiny, xlsx structured pipeline (yake bỏ qua `.xlsx`), provider spacy/gemini/langextract (không đổi hành vi), MCP query layer, storage backends, sync code của dev.py (plan 260821-2115 active — chỉ chung file, khác region).
3. **Biết xong khi nào?** E2E A/B trên fixture CCC PDF: (a) sync mặc định sinh `doc-tiny/rules/from-yake/<project>/ruler.from-yake.<source>.json`; (b) entity nodes có match từ rule động (label DOCUMENT/TRANSPORT...) **và** chạy `--no-yake` cùng tài liệu thì các node đó vắng mặt/bớt (A/B baseline — red-team m8); (c) `--dry-run` không ghi rule file; (d) `--no-yake` tắt pre-pass; (e) venv thiếu `yake` → sync vẫn chạy, chỉ warn.

## Design Decisions (đã sửa theo red-team C1–C3, M1–M5)

- **D1 — YAKE pre-pass in-process trong ingestor, dùng CHÍNH text đã load.** Trong `main()`, ngay sau `_read_input_text(file)` (folder loop, ~L1286) / sau khi load `raw_text` (single-file branches L1300-1423), gọi `ensure_yake_rules(text, path, args)` sinh rule per-source TRƯỚC `process_text` — không parse PDF lần 2 (sửa M3: bỏ hẳn `load_text` khỏi đường hot; `generate_for_file` chỉ giữ cho CLI/manual). dev.py chỉ truyền flag/env (giống `GLINER_*`).
- **D2 — Rule files per-project, keyed theo source_id kiểu relpath.** Layout: `doc-tiny/rules/from-yake/<project_id>/ruler.from-yake.<safe_source_id>.json`, với `safe_source_id` đúng quy tắc `_safe_source_id` (ingestor:644-646, relpath-based — tránh va chạm `docs/a/spec.pdf` vs `docs/b/spec.pdf`, sửa C3-stem-collision). Prune chỉ quét trong subdir project → không bao giờ đụng rule của project khác (sửa C3-cross-project). dev.py truyền `--yake-rules-dir` = subdir project. Static rules top-level + yake dir cùng được nạp khi merge (union — D7).
- **D3 — GLiNER×EntityRuler merge qua ruler-ONLY sidecar.** Hàm mới `build_ruler_pipeline(paths)`: `spacy.blank("en")` + `entity_ruler` (KHÔNG cần `en_core_web_sm` — model này KHÔNG có trong venv hiệu dụng, red-team C1; blank pipeline cũng loại trừ NER statistical xâm phạm merge, sửa M2). Mỗi paragraph: ruler matches ∪ GLiNER predictions qua `merge_ruler_gliner` — dedupe `(name.casefold(), type)`, ruler match mang `confidence: 1.0`. Lưu ý (m3): precedence chỉ đúng ở tầng merge này; tầng dưới `build_graph_components_from_entities` merge tiếp theo normalize-key và giữ surface name dài nhất — end-state ổn nhưng "ruler thắng tuyệt đối" không phải guarantee toàn đường ống. Áp dụng cho **single path trước** (flow thật vì dev.py luôn `--no-batch`, m4), batch path wired sau.
- **D4 — Graceful degradation, sync không bao giờ vỡ vì yake.** `import yake` fail → warn 1 dòng rõ ràng (nói rõ rule động bị bỏ qua), chạy tiếp. `build_ruler_pipeline` fail → warn + chỉ GLiNER thuần. **Không bao giờ ghi rule file rỗng**: patterns = [] → skip ghi + xóa file cũ của source đó nếu có (sửa C2 — file rỗng từng làm `build_spacy_pipeline` raise và giết folder spacy sync; loader mới cũng skip file rỗng với warn thay vì raise).
- **D5 — Mặc định BẬT** (khi yake khả dụng), tắt bằng `--no-yake` (CLI dev) hoặc `YAKE_ENABLED=0` (config doc.env). Env `doc.env` pass-through verbatim qua `_doc_env_for_process` (F1, m10 verify).
- **D6 — Tham số mặc định theo prototype**: `YAKE_LANGUAGE=en`, `YAKE_TOP=150`, `YAKE_MAX_NGRAM=3`, `dedupLim=False` (đúng default prototype `dedup_lower=False` — sửa m2), min pattern length 2, label heuristics bảng của `ruler_export.infer_entity_label`.
- **D7 — `--ruler-json` thành repeatable + merge là UNION.** `action="append"` (back-compat: 1 lần dùng vẫn OK). Ruler hiệu dụng cho gliner = `[*args.ruler_json] + [yake_rules_dir]` nếu yake bật (sửa M4 — trước đây `or` làm mất yake dir khi user truyền ruler-json). Provider spacy giữ nguyên hành vi cũ (chỉ dùng `--ruler-json` như framework pipeline đầy đủ) — mở rộng cho spacy nằm ở Follow-ups.
- **D8 — Prune khi xóa doc nằm ở dev.py.** Incremental deletion-only run sinh 0 subprocess ingestor (dev.py:1285-1295, M1) nên ingestor không bao giờ có cơ hội prune. Sau incremental sync thành công, dev.py tự xóa rule file của các `deleted_rel` bằng cách gọi `python yake_rules.py --prune-sources <safe_source_ids...> --rules-dir <dir>` (1 subprocess nhẹ, hoặc inline helper nếu nông). Folder/full sync prune trong ingestor pre-pass (keep = file list hiện tại của subdir project).

### Bề mặt config/CLI (mới)

| Layer | Khóa/Flag | Mặc định | Ghi chú |
|---|---|---|---|
| doc.env (config JSON) | `YAKE_ENABLED` | `"1"` | `"0"/"false"/"no"/"off"` → tắt |
| doc.env | `YAKE_LANGUAGE` | `"en"` | YAKE stopword lan (`en`/`vi`) |
| doc.env | `YAKE_TOP` | `"150"` | max keywords/file |
| doc.env | `YAKE_MAX_NGRAM` | `"3"` | |
| dev sync doc | `--yake/--no-yake` | từ env | group option — đặt TRƯỚC subcommand `all` (M5) |
| ingestor | `--yake-rules` / `--no-yake-rules` | theo env `YAKE_ENABLED` | |
| ingestor | `--yake-language/--yake-top/--yake-max-ngram` | theo env | |
| ingestor | `--yake-rules-dir` | `doc-tiny/rules/from-yake/<project_id>` | dev.py truyền tường minh |
| ingestor | `--ruler-json` (append) | `None` | giờ cũng ảnh hưởng provider gliner (union) |

## Phases

| Phase | File | Nội dung |
|---|---|---|
| 01 | [phase-01-yake-module.md](phase-01-yake-module.md) | Port `yake_vi` → `doc-tiny/yake_rules.py` (extractor + ruler export + label heuristics + CLI mirror `yake-vi` + prune CLI), `requirements.txt` pin `yake>=0.7` + install venv, unit tests |
| 02 | [phase-02-ingestor-wiring.md](phase-02-ingestor-wiring.md) | Ingestor: args mới, `ensure_yake_rules` pre-pass dùng text đã load + per-source write + prune (folder), `build_ruler_pipeline` sidecar blank, `merge_ruler_gliner` pure fn, gắn single + batch path, degradation, tests |
| 03 | [phase-03-dev-sync-wiring.md](phase-03-dev-sync-wiring.md) | `dev.py` sync doc: `--yake/--no-yake`, base_cmd flags + `--yake-rules-dir` per-project, echo, prune khi xóa doc (D8), `.gitignore`, tests CliRunner dry-run |
| 04 | [phase-04-docs-e2e.md](phase-04-docs-e2e.md) | Readme×2, E2E A/B trên CCC PDF (cmd form đúng click), verification-report |

## Risks & Mitigations

| Risk | Mức | Mitigation |
|---|---|---|
| Đổi hành vi mặc định sync (yake on) | T | `--no-yake` + `YAKE_ENABLED=0`; degradation khi thiếu package (warn rõ, không silent) |
| `yake` chưa có trong venv hiệu dụng (repo `.venv`) | C | P1 cài + pin `yake>=0.7` (kéo jellyfish/segtok/tabulate — không phải pure-python, red-team m1); D4 warn-continue |
| Label `KEYWORD` tạo node nhiễu | M | Cap `YAKE_TOP`; heuristics map thuật ngữ kỹ thuật; tune bằng config (follow-up) |
| spaCy blank ruler sidecar hiệu năng | L | Pattern matching thuần, không NER — nhanh hơn pipeline đầy đủ nhiều |
| Nhiều project dùng chung `doc-tiny/rules/` | M | D2 subdir per-project + prune scoped |
| Trùng vùng dev.py với plan 260821-2115 (sync code windows) | L | Khác region (sync doc L1175-1330); rebase bình thường |
| File rule rỗng/ PDF scan kém | M | D4: không ghi file rỗng + loader skip rỗng; YAKE ranking tự lọc |
| GLINER model không khả dụng lúc E2E | M | E2E chính dùng gliner theo env máy; nếu không có model: chứng minh merge bằng unit test (fake model) + ghi giới hạn trong report |

## Verification (tổng)

1. `pytest doc-tiny/tests/test_yake_rules.py` — sinh pattern, heuristics label, per-source write/prune/merge, không ghi rỗng.
2. `pytest doc-tiny/tests/` (ingestor wiring) — merge pure fn; pre-pass chạy trước extraction trên text đã load; single path gliner nhận ruler entities (fake gliner model + real blank ruler); degradation path; provider spacy không đổi.
3. `pytest tests/` (dev sync) — dry-run cmd flags theo env + `--no-yake` (assertIn subset style — red-team m5 xác nhận không break); prune gọi khi có deleted files.
4. E2E A/B (Phase 04) trên CCC PDF: rule file sinh đúng subdir project; node DOCUMENT/TRANSPORT xuất hiện ở run mặc định và vắng ở `--no-yake`; `--dry-run` không ghi file.

## Validation — critical questions (chế độ autonomous: tự trả lời + ghi rõ assumption)

| # | Câu hỏi | Quyết định | Trạng thái |
|---|---|---|---|
| 1 | Yake bật mặc định hay opt-in? | **Bật mặc định** (D5) — theo yêu cầu "hệ thống tự chạy yake"; tắt bằng `--no-yake`/env. | assumption — user xác nhận qua E2E P4 |
| 2 | Sinh rule ở dev.py (pre-step subprocess) hay trong ingestor? | **Trong ingestor, dùng text đã load** (D1) — tránh double-parse, hoạt động cả khi gọi ingestor trực tiếp. | đã chốt sau red-team M3 |
| 3 | Rule dùng chung 1 file hay per-project/per-source? | **`rules/from-yake/<project>/<source>.json`** (D2) — sau red-team C3 (cross-project prune destruction + stem collision). | đã chốt sau red-team |
| 4 | GLINER cần `en_core_web_sm` cho ruler merge? | **Không** — sidecar `spacy.blank("en")` + entity_ruler (D3) — sau red-team C1/M2. | đã chốt sau red-team |
| 5 | Ngôn ngữ YAKE mặc định? | `en` theo ví dụ user (CCC spec); tài liệu VN đặt `YAKE_LANGUAGE=vi`. | assumption |

## Delegation receipts

```
[delegate] role=researcher mode=spawn scope="doc-sync/yake integration research digest" status=done → research-digest.md
[delegate] role=red-team mode=spawn scope="adversarial review plan v1 (fix-first, C1-C3/M1-M5 đã hồi)" status=done → red-team.md
```

## Follow-ups (ngoài scope)

- Dùng YAKE keywords mở rộng **GLiNER label set** (hiện chỉ là ruler pattern).
- Cho provider spacy cũng nạp yake rules dir (union như gliner).
- Áp yake rules cho xlsx structured pipeline + query-time boosting ở MCP layer.
- Tự phát hiện `YAKE_LANGUAGE` theo tài liệu.
