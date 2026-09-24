# Research digest — dynamic YAKE rule generation for `dev sync doc`

Created: 2026-09-24 (researcher worker; evidence-backed, refs are `path:line` in `/Users/hieplq1.aip/AI/cortex-harness` unless absolute).

## Findings

### 1. Env flow: config → `_doc_env_for_process` → subprocess env / CLI args

1. Config store is **JSON only** (no toml/yaml): `HARNESS_CONFIG_DIR = ".cortext-harness/config"` (`cortex_harness/dev.py:64`); `_load_active_config` (`cortex_harness/dev.py:289-310`) globs `*.json` and picks the file with `"active": true`. Working example of `doc.env` as a flat string map: `tests/test_dev_sync_reliability.py:22-49`; minimal fixture: `tests/fixtures/unified_contract/config/proj_alpha.json:8` (`"doc": {"env": {}}`).
2. `_doc_env_for_process` (`cortex_harness/dev.py:753-805`) starts from `cfg["doc"]["env"]` (line 759) and copies **every** key except `_REMOTE_STORAGE_KEYS` (760-763), then layers storage/`PROJECT_ID`/`QDRANT_COLLECTION_DOC` defaults. Consequence: a new `YAKE_*` key added to `doc.env` in the config JSON reaches the subprocess env **with zero code change** — only the `env.get(...)` → CLI flag wiring is new work.
3. `sync doc` click group: `cortex_harness/dev.py:3616-3634` (`--entity-provider` default `gliner`, `--preview`, `--dry-run`); interactive body 3639-3683 and `sync doc all` 3686-3737 both call `_venv_python(DOC_TINY)` then loop `_sync_doc_folder(...)`.
4. `_sync_doc_folder` (`cortex_harness/dev.py:1175-1314`) builds `base_cmd` at **1205-1221**: `python` + `DOC_INGESTOR` (= `doc-tiny/graphrag_ingest_langextract.py`, `dev.py:145`) + `_env_to_neo4j_args(env)` (`dev.py:511-535`) + explicit flags with inline defaults, e.g. `--gliner-labels env.get("GLINER_LABELS", "PERSON,ORG,...")` (`dev.py:1216`). This is exactly where `--yake-*` flags would be appended. Full mode = one subprocess with `--folder` (1237-1241); incremental mode = **one subprocess per changed file** via `DOC_EXT_FLAGS` (`dev.py:157-165`, loop 1285-1295) — a per-document YAKE step must work in both shapes. `_run_with_retry` (`dev.py:1419-1436`) passes `env` through to the child, so `YAKE_*` are also readable via `os.getenv` in the ingestor.
5. Defaults are **not** declared in `doc-tiny/enviroment_loader.py` — that file only `load_dotenv()`s and exposes legacy neo4j/openai values (`doc-tiny/enviroment_loader.py:1-17`). Doc-env defaults live inline in `dev.py` `base_cmd` and in argparse defaults of the ingestor (pattern: `default=os.getenv("GLINER_MODEL_NAME", ...)` at `doc-tiny/graphrag_ingest_langextract.py:1120,1125,1161-1162` — a `YAKE_*` env default would follow this pattern).
6. Actual interpreter: `_venv_python` (`cortex_harness/dev.py:1403-1416`) prefers `doc-tiny/.venv/bin/python` then **repo-root** `.venv`. On this machine `doc-tiny/.venv` does not exist, so the venv in play is `/Users/hieplq1.aip/AI/cortex-harness/.venv/bin/python`.

### 2. doc-tiny ingest pipeline: loading, hook points, ruler usage

7. PDF library is **pypdf** (`doc-tiny/graphrag_ingest_langextract.py:10` → `read_pdf_text` 68-73). Other readers: `read_text_file` 76-77, `read_docx_text` 80-83, `read_pptx_text` 86-96, `read_xlsx_text` 99-114; dispatcher `_read_input_text` **682-692**; folder walker `_iter_input_files` 662-679 (ext allowlist `_DOC_INPUT_EXTENSIONS` 659: `.pdf/.txt/.md/.docx/.pptx/.xlsx`).
8. `main()` folder flow (`doc-tiny/graphrag_ingest_langextract.py:1266-1298`): builds `shared_nlp`/`shared_gliner` once (1274-1279), then per file `_read_input_text(file_path)` at **1286** → `process_text(...)` 1287-1297. Single-file paths: `--pdf` 1300-1315, `--text-file` 1317-1332, `--md` 1334-1349, `--docx` 1351-1366, `--pptx` 1368-1383, `--xlsx` 1385-1411, `--raw-text` 1413-1423. **The natural pre-extraction hook is immediately after each text read (line 1286 in the folder loop; around line 1304/1321/... for single files) or centrally at the top of `process_text` (695-727).**
9. `--ruler-json` arg is at `doc-tiny/graphrag_ingest_langextract.py:1117`. Its **only** consumers are `build_spacy_pipeline` calls: lazy build in `process_text` (**715**), shared build for `--folder` (**1277**), and `spacy_ruler=args.ruler_json` for structured xlsx (**950**). `build_spacy_pipeline` (`doc-tiny/entity_extractors.py:135-174`) accepts a **single file or a directory** — a directory merges every `*.json` inside it (163-169) and validates the `{"patterns":[{"label","pattern"}]}` shape (146-161). Dropping `ruler.from-yake.json` into `doc-tiny/rules/` and passing the directory therefore auto-merges it with the hand-written rules.
10. **Confirmed: the GLINER path reads no ruler/pattern file.** `extract_entities_gliner` (`doc-tiny/entity_extractors.py:255-275`) and `extract_entities_gliner_batch` (295-311) only call `model.predict_entities(text, labels, threshold)`; `build_gliner_model` (213-235) reads only `GLINER_MODEL_PATH`/offline env. Since `gliner` is the default provider everywhere (`dev.py:3619`, `dev.py:1211`, argparse default `doc-tiny/graphrag_ingest_langextract.py:1104-1109`), YAKE-generated rules currently have **no effect on the default path** — wiring them into GLINER (e.g. post-pass regex normalization or label seeding) is new design surface. spaCy string patterns are also case-sensitive literals; `ruler.combined.json` mixes literal and `LOWER` token patterns (see `git show af57bb0 -- doc-tiny/rules/`), so the exporter should consider token patterns for robustness.

### 3. Yake prototype (port source, `/Users/hieplq1.aip/test/Yake`)

11. `src/yake_vi/extractor.py:15-50` — `VietnameseKeywordExtractor` wraps `yake.KeywordExtractor(lan, n=max_ngram, top, dedupLim)`; `extract()` returns `(keyword, score)` sorted ascending (lower score = better); default `language="vi"`, `max_ngram=3`, `top=10`.
12. `src/yake_vi/ruler_export.py` — `infer_entity_label` regex heuristics → STANDARD/TRANSPORT/CRYPTO/KDF_HASH/CERT/DOCUMENT/ALGORITHM (10-37); `keywords_to_ruler_patterns` dedup casefold + `min_length=2` (40-62); `build_ruler_json`/`write_ruler_json` emit `{"patterns":[{"label","pattern"}]}` (65-102) — shape exactly matches what `build_spacy_pipeline` validates.
13. `src/yake_vi/text.py:8-15` — `clean_text` NFC + control-char + whitespace normalization. `src/yake_vi/documents.py:10-34` — uses **pymupdf** for PDF (not available in the harness venv; the port must reuse doc-tiny's pypdf `read_pdf_text` instead). `src/yake_vi/__main__.py:18-114` — CLI flags `--file/--top/--n/--language/--format ruler/-o/--ruler-label/--no-ruler-heuristics`. `pyproject.toml` deps: `yake>=0.4.8` only (pdf extra = pymupdf).

### 4. Venv / dependency status

14. Import check in the **actual venv** (`/Users/hieplq1.aip/AI/cortex-harness/.venv/bin/python`): `yake=False`, `pymupdf=False`, `fitz=False`; `pypdf=True`, `spacy=True`, `gliner=True`. → `yake` must be added to `doc-tiny/requirements.txt` (currently lists pypdf/python-docx/python-pptx/openpyxl/sentence-transformers/spacy/gliner/etc.) **and** the root `requirements.txt` (the root venv is what actually resolves, per finding 6), then installed.
15. doc-tiny already depends on pypdf — no PDF lib addition needed if the hook reuses `_read_input_text`.

### 5. `doc-tiny/rules/` state and consumption

16. Contents: `ruler.combined.json` (459 lines), `ruler.crypto.json` (11), `ruler.sample.json` (16), and **`ruler.from-yake.json` (604 lines, currently UNTRACKED** — `git status --short doc-tiny/rules/` shows `?? doc-tiny/rules/ruler.from-yake.json`; it was produced by a prototype run, labels KEYWORD/DOCUMENT/... visible in the file head).
17. Git history: commit `af57bb0` (2026-05-09, "Add secure coding guidelines and end-to-end pipeline recommendations") introduced `doc-tiny/rules/` with `ruler.combined.json` + `ruler.crypto.json` + `ruler.sample.json` (`git show --stat af57bb0 -- doc-tiny/rules/`). No later commits touched the dir.
18. Consumption docs: only `doc-tiny/Readme.md:177,222,260` document `--ruler-json` (as a manual flag, default `None`). Nothing in `docs/HARNESS_WORKFLOW.md` or `ReadMe.md` mentions `ruler.combined.json`/`ruler.crypto.json` or auto-loading `rules/` — today the rules are opt-in, manually passed, and only meaningful for `--entity-provider spacy`.

### 6. Test conventions

19. doc-tiny tests are unittest-style, load the ingestor via `importlib.util.spec_from_file_location` (`doc-tiny/tests/test_ingest_input_ignore.py:21-33`), reference the owning plan in the module docstring (line 1: "plan 260909-0854 phase 04"), and use `tempfile.TemporaryDirectory` + `unittest.mock.patch.dict("os.environ", ...)` (66, 93). Same importlib pattern at `tests/test_graphrag_ingest_langextract.py:8-18`.
20. Harness-side tests are pytest/unittest mixed: `tests/test_unified_contract_doc_paths.py:14-26` inserts doc-tiny+code-tiny on `sys.path` and imports the ingest module directly, with fakes like `_RecordingQdrant` (52-60) and `inspect.getsource(...)` assertions (92-100). `tests/test_dev_sync_reliability.py:20-112` is the model for CLI/env tests: `click.testing.CliRunner`, writes `.cortext-harness/config/dev.json` with `active:true` + `doc.env` keys into a temp root, invokes `sync ... --dry-run` and asserts on the echoed command line (dry-run prints the cmd without executing; `_run_with_retry` `dev.py:1429-1431`). `tests/test_dev_init_graph_provider.py:174-184` shows the fake-venv pattern: `patch("cortex_harness.dev._venv_python", return_value="/fake/python")` + patched `subprocess.Popen` to capture the child env.
21. Prototype tests use `pytest.importorskip("yake")` / `pytest.importorskip("pymupdf")` (`/Users/hieplq1.aip/test/Yake/tests/test_extractor.py:1-4`, `test_documents.py:1-4`) — the right guard if YAKE stays an optional dep.

### 7. `docs/development-rules.md`

22. **Does not exist.** Prior plans record this explicitly: `docs/plans/260713-1638-framework-parser-integration/plan.md:151` ("...was requested by the planning skill but is not present in this repository; existing repository conventions and the supplied root AGENTS.md govern this plan"); also `docs/plans/260714-1603-flutter-analyzer-parser/plan.md:44`. Governing conventions are therefore repository habits: `tests/test_*.py` (pytest-collected), plan-dir docstrings citing plan id + phase, evidence citations as `path:line`, phase-gated plan dirs under `docs/plans/`.

### 8. Plan directory conventions

23. Layout `docs/plans/<YYMMDD[-HHMM]>-<slug>/`. Recent exemplar `docs/plans/260924-1434-vb6-anchor-graph-coverage/`: `plan.md` with **YAML front-matter** (lines 1-27): `title`, `status` (observed values repo-wide: active/complete/completed/in_progress/pending/superseded/implemented), `created`, `completed`, `mode` (e.g. `hi-plan --full`), `scope` (one dense paragraph incl. explicit OUT list), `blockedBy`, `blocks`, `relatedPlans`, `sources` (includes sibling `research-digest.md`/`red-team.md`). Body: Overview, evidence table with `path:line`, "Scope Challenge (3 câu)", Architecture Decisions table (AD-xx), pipeline diagram, acceptance milestones M1-M7.
24. Phase files: newer plans use descriptive names — `phase-01-schema-publication.md`, `phase-02-worker-planes.md`, `phase-03-resolver.md`, `phase-04-integration-golden.md` plus `research-digest.md`, `red-team.md`, `verification-report.md` (`docs/plans/260924-1434-vb6-anchor-graph-coverage/`). Older style (`docs/plans/260916-ladybugdb-graph-provider/`) uses `phase-00-spike-report.md`, `phase-01.md`…`phase-08.md` and a blockquote status header (`plan.md:1-5`). Prefer the 260924 style.

## Conventions to follow

- New env keys go in config JSON under `doc.env` (flat string map); harness reads them with `env.get("KEY", "<inline default>")` in `_sync_doc_folder`'s `base_cmd`; ingestor argparse mirrors with `default=os.getenv("YAKE_...")` (pattern `doc-tiny/graphrag_ingest_langextract.py:1120`).
- CLI tests: `CliRunner` + temp `.cortext-harness/config/dev.json` + `--dry-run` asserting the echoed command (`tests/test_dev_sync_reliability.py:94-112`); fake venv via `patch("cortex_harness.dev._venv_python", return_value="/fake/python")` (`tests/test_dev_init_graph_provider.py:174`).
- Ingestor unit tests: importlib-load `graphrag_ingest_langextract.py`, docstring citing plan id + phase, `tempfile.TemporaryDirectory` (`doc-tiny/tests/test_ingest_input_ignore.py:1,21-33`).
- Plan artifacts: `research-digest.md` (this file), `plan.md` YAML front-matter per finding 23, descriptive `phase-NN-*.md` files, evidence always `path:line`.
- Optional deps guarded with `pytest.importorskip` (prototype test pattern).

## Risks / open questions

- **[verified gap]** GLINER — the default provider — never reads ruler/pattern files (finding 10). Generating YAKE rules only pays off for `--entity-provider spacy` unless new plumbing feeds GLINER (post-pass or label seeding). Product decision needed: force spacy-ruler sidecar, extend GLINER path, or accept spacy-only scope.
- **[verified]** `yake` is not installed in the root venv (the effective doc venv, finding 6/14) — dependency + install step is mandatory; `pymupdf` is also absent so the port must reuse pypdf readers.
- **[open]** Commit policy for generated `ruler.from-yake.json`: currently untracked (604 lines) while sibling hand-written rules are committed since af57bb0. Generated-per-run artifacts vs committed static rules needs a ruling (and per-folder/per-file naming to avoid incremental-sync collisions, since incremental spawns one subprocess per file — finding 4).
- **[open, inferred]** Language default: prototype defaults `lan="vi"`; corpus is mixed EN/VI — should be configurable (`YAKE_LANGUAGE`) rather than hardcoded.
- **[inferred]** Where the step runs: task says "inside its own workflow/venv" — an ingestor-side hook (doc-tiny process) avoids re-parsing PDFs and gets `raw_text` for free; a dev.py-side pre-pass would need its own PDF parsing. Inferred from code layout, not from any spec.
- **[note]** spaCy ruler string patterns are case-sensitive; YAKE output casing may mismatch doc text — consider `LOWER` token patterns like `ruler.combined.json` uses.
- **[minor]** `sync doc --entity-provider` help lists "gliner / langextract / spacy" while ingestor also accepts `gemini` (`dev.py:3619-3620` vs `graphrag_ingest_langextract.py:1104-1109`).

## Recommended approaches

1. **Ingestor-side hook (recommended):** add `--yake-rules-dir` + `YAKE_*` argparse/env options to `graphrag_ingest_langextract.py`; run YAKE right after `_read_input_text` (folder loop line 1286, plus single-file paths) and write `rules/<source>.yake.json`; refresh the shared spaCy EntityRuler incrementally (`ruler.add_patterns`) or rebuild per doc; add `yake` to both requirements files.
2. **Harness-side pre-pass:** `dev.py _sync_doc_folder` runs a small yake-rules CLI in the doc venv before `base_cmd` and appends `--ruler-json <rules dir>` — simplest contract, but re-parses PDFs and only affects provider=spacy.
3. **Hybrid + GLINER bridge:** generate rules ingestor-side AND extend `entity_extractors.py` so the GLINER path also consumes ruler patterns as entity hints — the only option that changes default-provider behavior.
