# YAKE dynamic entity rules for doc sync — 2026-09-24

## Context

`dev sync doc` ran the doc-tiny ingestor with `--entity-provider gliner` (default), but the
hand-written `doc-tiny/rules/*.json` EntityRuler patterns were **only** consumed by the spacy
provider — the default path never read them, and rules were static regardless of corpus.
Request: the system should run YAKE on each incoming document itself, generate rules
dynamically, and only then run GLiNER — with GLiNER actually consuming them.
Executed via `hi-craft --full` on plan `260924-1642-yake-dynamic-rules-doc-sync`
(research + red-team already attached to the plan dir).

## Change

- **`doc-tiny/yake_rules.py` (new)** — self-contained port of the `yake_vi` prototype
  (`YakeRuleExtractor` with prototype defaults `dedupLim=False`, label heuristics
  STANDARD/TRANSPORT/CRYPTO/KDF_HASH/CERT/DOCUMENT/ALGORITHM/KEYWORD, per-source rule files
  `ruler.from-yake.<safe_source_id>.json`, prune helpers, CLI mirroring `yake-vi` plus
  `--prune`/`--prune-sources`). Never writes an empty `{"patterns": []}` file — removes the
  stale file instead (red-team C2).
- **`doc-tiny/entity_extractors.py`** — added `build_ruler_pipeline` (ruler-only sidecar
  `spacy.blank("en")` + `entity_ruler`, no `en_core_web_sm`; skips empty/malformed rule files
  with a warn instead of raising), `ruler_match`, `merge_ruler_gliner` (ruler wins
  `(name.casefold(), type)` collisions, confidence 1.0). Existing functions untouched.
- **`doc-tiny/graphrag_ingest_langextract.py`** — guarded `yake_rules` import (missing `yake`
  → one-line warn, sync continues); `--ruler-json` now `action="append"` and merged as a
  **union** with the dynamic dir on the gliner path (red-team M4; spacy/xlsx consumers keep
  first-entry semantics via `_first_ruler_json`); `--yake-*` args (env-backed via
  `_env_flag`/`_env_int`); `ensure_yake_rules` pre-pass on the already-loaded text in the
  folder loop (`.xlsx` excluded) + 5 single-file branches; folder prune to the current file
  list; lazy ruler sidecar with `_refresh_ruler_for_yake_change` — incremental
  `entity_ruler.add_patterns` for newly written rules, full rebuild only on removal; merge on
  both single and batch extraction paths.
- **`cortex_harness/dev.py` (sync-doc region only)** — `--yake/--no-yake` group option
  (must precede the subcommand), `YAKE_*` doc.env → base_cmd flags, per-project
  `--yake-rules-dir` (`doc-tiny/rules/from-yake/<project>/`, red-team C3 cross-project
  isolation), `yake :` echo, and `_prune_yake_rule_files` after incremental success so
  **deletion-only runs** (zero ingestor subprocesses, red-team M1) still prune — stem-keyed
  pruning guarded by surviving-file stems.
- **Deps/hygiene** — `yake>=0.7` in both requirements files (installed: 0.7.3),
  `.gitignore` += `doc-tiny/rules/from-yake/`, prototype artifact
  `doc-tiny/rules/ruler.from-yake.json` deleted, docs (doc-tiny/Readme "Dynamic rules (YAKE)"
  section, root ReadMe + HARNESS_WORKFLOW one-liners).
- **Tests** — `doc-tiny/tests/test_yake_rules.py` (14), `test_gliner_ruler_merge.py` (11),
  `test_yake_prepass.py` (10), `tests/test_dev_sync_doc_yake.py` (8). All green;
  `tests/test_dev_sync_reliability.py` no regression. Pre-existing failures untouched:
  mcp-2.x `fastmcp` collection errors in `doc-tiny/tests/test_mcp_project_id_optional.py`
  and `tests/test_unified_contract_doc_paths.py` (identical before/after).

## Impact

Default `dev sync doc` behavior changes: dynamic rules are generated and merged **on** by
default (disable per-invocation `--no-yake` or via `doc.env YAKE_ENABLED=0`). Entity graphs
gain ruler-sourced nodes (E2E: +93 entities on an 8-page CCC excerpt, incl. 6 TRANSPORT
nodes GLiNER's label set cannot produce) at the cost of KEYWORD-label noise (tunable
`YAKE_TOP`). Risk: **medium** (default-on behavior change, new dependency `yake`), mitigated
by graceful degradation and A/B-verified off-switch.

## Decision

- Rules generated **inside the ingestor from the already-loaded text** (no double PDF parse),
  not as a dev.py pre-step (plan D1 after red-team M3).
- GLiNER consumes rules via a **blank-pipeline sidecar**, not `en_core_web_sm` (absent from
  the effective venv; also keeps statistical NER out of the merge — C1/M2).
- Per-project rules subdir + prune scoped to it (C3); dev.py owns deletion pruning (D8).
- Reviewer fix cycle applied (6 Medium findings): prune stem-collision guard, O(1) per-source
  ruler refresh, `_env_int` coercion, non-vacuous CLI test assertions, CLI empty-output
  guard, xlsx pre-pass skip.

## References

- plan: ./docs/plans/260924-1642-yake-dynamic-rules-doc-sync/plan.md
- verification: ./docs/plans/260924-1642-yake-dynamic-rules-doc-sync/verification-report.md
- commit: 6f541e2
- prototype: /Users/hieplq1.aip/test/Yake (src/yake_vi)
