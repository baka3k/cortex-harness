# Verification report — 260924-1642-yake-dynamic-rules-doc-sync

Date: 2026-09-24 · Environment: repo `.venv` (Python 3.12.14, spacy 3.8.16, gliner, yake 0.7.3;
`en_core_web_sm` NOT installed — by design, the ruler sidecar uses `spacy.blank("en")`).

## Unit / integration tests

| Suite | Result |
|---|---|
| `pytest doc-tiny/tests/test_yake_rules.py` | **14 passed** (extraction order, label heuristics, pattern dedupe/min-length, C2 empty guard, C3 naming, prune×2, merge, load_text xlsx skip, CLI×2) |
| `pytest doc-tiny/tests/test_gliner_ruler_merge.py` | **11 passed** (blank sidecar w/o NER model + pipe_names guard, C2 empty-file skip, file+dir mix, ruler spans/confidence 1.0, merge precedence, diff-type kept, unions) |
| `pytest doc-tiny/tests/test_yake_prepass.py` | **5 passed** (pre-pass on loaded text, empty text no file, disabled noop, degradation monkeypatch, folder prune scoping, single-path gliner×ruler merge w/ FakeModel, M4 union sources) |
| `pytest doc-tiny/tests/` (full) | **51 passed, 12 errors** — errors are the pre-existing mcp 2.x/`fastmcp` collection failures in `test_mcp_project_id_optional.py`, identical to the pre-change baseline (verified before any edit) |
| `pytest tests/test_dev_sync_doc_yake.py` | **7 passed** (default-on flags, env disable, CLI override ×2 in M5 click form, env params forwarded, `all` subcommand, prune + dry-run no-prune) |
| `pytest tests/test_dev_sync_reliability.py` | **9 passed** — no regression (subset `assertIn` style, as red-team m5 predicted) |
| `pytest tests/test_unified_contract_doc_paths.py` | pre-existing collection error (mcp 2.x `fastmcp` import), unchanged from baseline — not touched by this plan |

## Phase DoD spot checks

- **P01 CLI parity**: `python doc-tiny/yake_rules.py --file doc-tiny/testdata/CCC-TS-101-Digital-Key-R3-1.2.1-APPROVED.pdf -l en --top 150 --format ruler -o /tmp/r.json` → **150 patterns** (`KEYWORD` 142, `DOCUMENT` 4, `TRANSPORT` 4). Prototype artifact `doc-tiny/rules/ruler.from-yake.json` deleted; `yake>=0.7` pinned in both `doc-tiny/requirements.txt` and root `requirements.txt`; installed into repo `.venv` (yake 0.7.3 + jellyfish/segtok/tabulate).
- **P03 dry-run (real CLI)**: `yake   : on (lang=en, top=150, dir=rules/from-yake/yakeab)` echo + `--yake-rules --yake-language en --yake-top 150 --yake-rules-dir …` in the printed cmd.

## E2E A/B (manual, real GLiNER `urchade/gliner_large-v2.1`, real bge-m3, local FalkorDB+Qdrant)

**Fixture deviation (documented):** the full CCC PDF yields **2371** extractable paragraphs →
GLiNER-large on CPU ≈ hours per run. The E2E therefore used an **8-page excerpt of the same
CCC PDF** (28,221 chars, 68 paragraphs; YAKE on the excerpt still yields 150 patterns incl.
6 DOCUMENT + 7 TRANSPORT). Full-PDF rule generation parity is separately proven by the P01
CLI DoD above; merge mechanics are covered by unit tests with a fake GLiNER model.

| Step | Command (group options before subcommand) | Result |
|---|---|---|
| 0 dry-run default | `dev sync doc --project-dir <tmp> --dry-run all` | ✅ echo `yake : on (…)`; cmd contains `--yake-rules --yake-rules-dir …/rules/from-yake/yakeab` |
| 1 Baseline A | `dev sync doc --project-dir <tmp> --no-yake all` (fresh storage) | ✅ echo `yake : off`; cmd `--no-yake-rules`; **0 files** under `doc-tiny/rules/from-yake/`; exit 0 in 49.9s |
| 2 Run B (feature) | wipe storage+state → `dev sync doc --project-dir <tmp> all` | ✅ echo `yake : on`; `yake rules: 150 patterns -> …/ruler.from-yake.CCC-TS-101-excerpt.pdf.json` printed **before** that file's `Source:/Entity provider:/Chunked into` lines (log lines 52–57); `ruler merge: 1 source(s) [gliner]`; exit 0 |
| 3 Dry-run `--no-yake` | `dev sync doc --project-dir <tmp> --no-yake --dry-run all` | ✅ cmd contains `--no-yake-rules`; rule-file mtime unchanged |
| 4 Deletion-only incremental | delete PDF → `printf '\n' \| dev sync doc --project-dir <tmp>` | ✅ `changed: 0 deleted: 1` (zero ingestor subprocesses), `[yake] pruned rule files for 1 deleted doc(s)`; rule file gone (D8) |
| 5 Git hygiene | `git status` / `git check-ignore` | ✅ `doc-tiny/rules/from-yake/` matched by `.gitignore:173`; no dynamic artifact tracked |

### A/B entity evidence (m8)

Entities read directly from the local Qdrant store (`yakeab_doc`, `entity_mentions` payloads):

| Run | Entities | Label distribution |
|---|---|---|
| A (`--no-yake`, GLiNER only) | 105 | ORG 9, PERSON 10, STANDARD 8, TECH 49, CRYPTO 11, DATE 7, PRODUCT 11 — **no TRANSPORT/DOCUMENT** (they are not in the GLiNER label set) |
| B (default, yake on) | 198 | A's labels + **TRANSPORT 6**, KEYWORD 87 |

**93 entities exist in B but not in A.** All 6 TRANSPORT entities are new-in-B and ruler-sourced:

```
Console NFC Reader::TRANSPORT   Door NFC Reader::TRANSPORT   NFC::TRANSPORT
NFC Reader::TRANSPORT           Vehicle NFC Readers::TRANSPORT
Vehicle UWB Module::TRANSPORT
```

Since `TRANSPORT`/`DOCUMENT` cannot be produced by the GLiNER label set, their presence in B
proves the dynamic rules were generated **and consumed** by the merge. DOCUMENT-labeled rules
exist in the file (6 patterns) but their occurrences fell in short paragraphs (<150 chars)
that skip extraction — TRANSPORT is the load-bearing evidence.

KEYWORD +82 net entities is the documented noise trade-off (plan risk table; tune via `YAKE_TOP`).

## Degradation evidence (D4)

- Live: guarded `import yake_rules` at ingestor top (`except Exception` → warn line per source
  via `ensure_yake_rules`), sync continues GLiNER-only.
- Unit: `test_prepass_degrades_gracefully` (monkeypatched raise → warn, no raise) and
  `test_build_ruler_pipeline_returns_none_without_patterns` (empty dir / missing file → None,
  no raise). Full venv-without-yake run not executed live (would require a second venv);
  the import guard + unit tests cover the path.

## Files changed

| File | Change |
|---|---|
| `doc-tiny/yake_rules.py` | **new** — ported yake_vi (extractor, label heuristics, ruler export, per-source files, prune, CLI mirror + prune modes) |
| `doc-tiny/entity_extractors.py` | + `build_ruler_pipeline` (blank sidecar, C2 skip-not-raise), `ruler_match`, `merge_ruler_gliner`; no existing function changed |
| `doc-tiny/graphrag_ingest_langextract.py` | guarded yake import; `--ruler-json` append (M4); `--yake-*` args; `ensure_yake_rules` pre-pass on loaded text (folder + 5 single-file branches); folder prune; lazy/refreshable ruler sidecar; merge on single + batch paths; xlsx/spacy ruler consumers keep first-entry semantics |
| `cortex_harness/dev.py` | sync-doc region only: `--yake/--no-yake` group option (M5), base_cmd yake flags + per-project `--yake-rules-dir` (C3), `yake :` echo, `_prune_yake_rule_files` after incremental success (D8) |
| `doc-tiny/requirements.txt`, `requirements.txt` | + `yake>=0.7` |
| `.gitignore` | + `doc-tiny/rules/from-yake/` |
| `doc-tiny/Readme.md` | Entity-extraction flags (repeatable ruler-json, yake flags), compact-table rows, new "Dynamic rules (YAKE)" section |
| `ReadMe.md`, `docs/HARNESS_WORKFLOW.md` | 1-line capability/pipeline mentions |
| `doc-tiny/rules/ruler.from-yake.json` | deleted (prototype artifact) |
| tests | `doc-tiny/tests/test_yake_rules.py`, `test_gliner_ruler_merge.py`, `test_yake_prepass.py`, `tests/test_dev_sync_doc_yake.py` |

## Review (hi-craft full mode — code lens, fix cycle 1)

Adversarial reviewer verdict: **6 findings, all Medium, 0 Critical/High, score 8/10**. All six
fixed and regression-tested (51 → 55 doc-tiny + 8 dev-sync tests green):

1. **Prune stem-spelling collision** — `_prune_yake_rule_files` also pruned the stem-keyed
   rule file of a *surviving* doc when a deleted nested doc shared its stem. Fixed: stem
   spelling pruned only when no kept file has that stem (`kept_stems` from `_find_doc_files`).
   Test: `test_prune_keeps_stem_of_surviving_file`.
2. **O(N²) folder rebuild** — ruler sidecar fully rebuilt per file. Fixed: `_refresh_ruler_for_yake_change`
   adds just the written patterns via `entity_ruler.add_patterns`; full rebuild only on
   removal / not-yet-built / failure. Tests: `RulerRefreshTests` (incremental add, removal →
   rebuild, pre-build deferral).
3. **`YAKE_TOP=abc` crashed argparse construction** — now `_env_int` warns + falls back
   (150/3). Test: `EnvIntTests`.
4. **Vacuous `assertIn("--yake-rules")`** (matched `--yake-rules-dir`) — assertions now
   space-delimited (`" --yake-rules "`), off-case asserts `NotIn`.
5. **CLI `-o` could write `{"patterns": []}`** — empty ruler output is never written/emitted
   now (C2 applies to every output path).
6. **Folder-mode `.xlsx` went through the YAKE pre-pass** (plan scope: yake skips xlsx) —
   folder call site now skips `.xlsx` (matching the standalone `--xlsx` branch).

## Verdict

All four phase DoDs ✅. The two "mắt xích" from the plan overview are closed end-to-end:
(1) sync generates rules per document before extraction, from the already-loaded text;
(2) the default gliner provider consumes static + dynamic rules through the blank-pipeline
ruler sidecar — demonstrated by label families GLiNER cannot emit. Default-on confirmed via
E2E step 0/2 (`--no-yake` and `YAKE_ENABLED=0` both disable cleanly).
