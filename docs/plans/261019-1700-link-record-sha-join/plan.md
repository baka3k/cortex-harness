---
title: "link-tiny: engine precision fixes — sha-join + full symbol universe (resultset truncation, Field label)"
status: pending
created: 2026-10-19
mode: hi-plan (fast)
scope: "link-tiny/link_engine.py precision defects (doc/code sha join, paginated symbol reads, Field label), link-tiny/tests fixtures + unit suite + verifier, docs/LINK_CONTRACT.md + link-tiny/README.md notes, one live reconcile backfill on the live project; zero changes to dev.py sync internals / code-tiny / doc-tiny / record schema / MCP tool surface"
blockedBy: []
blocks: []
relatedPlans:
  - 261024-1305-link-doc-code-v2-redesign (follow-up v2 redesign: fixes extensionless doc_sha misses, endpoint projection drops, and replaces 8.4 GB JSONL with indexed SQLite store)
  - 261019-1126-link-tiny-doc-code-traceability (parent feature — this repairs its Phase-3 engine delta/match steps; link store, CLI, and MCP phases are untouched)
  - 260718-2159-incremental-scan-reliability (owns the sync-state / inventory baseline formats this plan reads)
prediction: after one reconcile — symbol_universe rises from 28992 to the full per-label server counts (~100k+ on the live project), active links grow well past 2002 (unit-col family pairings alone add ≥8), ≥95% of records carry non-null source_sha on both sides
---

# link-tiny: engine precision fixes — sha-join + full symbol universe

## Symptoms (live deployment, built 2026-10-19T09:15Z)

**A. Every record carries `"source_sha": null`** on both sides, while
`state.json` holds 68 `doc_shas` + 2251 `code_shas`. `_sha_changed` can
never fire (both sides `null`), so **content edits never
stale-propagate** — only deletions/renames are detected. The incremental
no-op skip still works (`None == None`), which hides the bug.

**B. Symbol coverage is silently crippled (~22%).** `find_doc_for_code`
for `grid_unit_col` returns 0 although the doc paragraph names it
and the code graph holds the node. The reported `symbol_universe` of
28992 is an artifact: FalkorDB's server-wide `resultset_size` default
(10000) **silently truncates every label read** — Function (~50.4k),
Variable (~38.6k), Field (~27.9k) each yield exactly 10000 rows. The
2002 existing links matched only against that capped index; and the
`Field` label — designer controls, the symbols screen-design documents
actually name — was never read at all.

## Root causes (verified on live data)

### A. Two namespace mismatches between graph identity and baseline keys

| Side | Graph identity | Baseline keys | Join attempt today |
| --- | --- | --- | --- |
| doc | `Paragraph.source_id` = **flattened** rel path: `specs__module_a(v1)__batch_job_01_data_flow.xlsx` (dev.py builds it as `posix_rel.replace("/", "__").replace("\\", "__")`, see `_prune_yake_rule_files` line ~1244; single-file mode uses the stem) | POSIX rel paths: `specs/module_a(v1)/batch_job_01_data_flow.xlsx` (68 keys in `.cortext-harness/sync-state/{md5}.json`) | `_doc_sha_for` tries direct rel lookup, then a **stem** fallback — neither matches the `__`-flattened spelling |
| code | `file_path` (symbols) / `source_file` (endpoints) = **folder-relative**: `src/COMMON/…/ADODB.cs` | **project-relative** re-expression inside `load_code_inventory_hashes`: `<code-folder>/s1/src/COMMON/…/ADODB.cs` (2251 keys) | `code_shas.get(symbol.path)` — plain dict miss |

Decisive constraint from probing the live graph and inventories: naive
**suffix matching is forbidden** — 152 s1 paths are suffix-shadowed by
s3 paths and 1 exact folder-relative collision exists between the two
scan folders. But graph nodes carry a **`repo`** property holding the
absolute sync-folder path (`…/<code-folder>/s1`), which makes an
unambiguous `(repo, file_path)` join possible.

> Parser caveat: `repo` semantics vary (e.g. the android parser stamps
> the project root, not the scan folder). The fallback rule must handle
> null/foreign `repo` values; Phase 01 pins the per-parser truth.

### B. Silent resultset truncation + missing Field label

- `GRAPH.CONFIG GET resultset_size` → `10000`; the engine runs one
  unpaginated query per label, so large labels are cut at exactly 10000
  rows with no warning. Concrete miss: token `grid_unit_col`
  (paragraph 21 of `specs__order_summary_business_rule_spec.md`)
  vs the `Variable` node in `OrderSummaryForm.Designer.vb` (+ the
  `Field` twin in `.Designer.cs`) — every pipeline step passes, the node
  sits beyond the cut → 0 links.
- `Field` is absent from `CODE_SYMBOL_LABELS`, so designer controls are
  unreachable even below the cap.

## Fix strategy

1. **Doc side** — build a flattened index `{flatten(rel) → sha}` from
   `doc_shas` using the *same* rule dev.py uses to build `source_id`
   (`/` and `\` → `__`). Lookups: direct rel → flattened → stem
   (single-file mode). Never un-flatten a `doc_id` (file names may
   legitimately contain `__`).
2. **Code side sha join** — read `n.repo` alongside the identity columns;
   `EngineSymbol` gains a `repo` field (record schema unchanged — it is
   a join input, not part of `identity`). Baselines become
   repo-aware: `{norm_repo → {folder_rel → sha}}`. Join order:
   `(repo, path)` exact → unique cross-folder fallback whose
   project-relative form ends with the graph path → `None`.
   Ambiguity never produces a wrong SHA.
3. **Full symbol universe** — `_read_labeled_symbols` reads in
   deterministic keyset batches (`ORDER BY id(n) LIMIT/SKIP`) until a
   short page; a per-label shortfall vs the server count is a loud
   warning, never a silent cut. No server config writes (the engine
   stays read-only).
4. **`Field` label** — added to `CODE_SYMBOL_LABELS`
   (`target_type="symbol"`, `file_path` identity column); contract and
   tests updated.
5. **Telemetry** — `BuildReport.sha_join` +
   `state.json["sha_join"]`: `{doc_matched, doc_missing, code_matched,
   code_missing, code_ambiguous}`; `symbol_universe` becomes
   per-label-accounted so a future cap regression is visible in the
   sync report and `trace_status`.
6. **Backfill** — one `dev sync link --reconcile` after A+B land
   re-stamps SHAs and re-matches over the full universe; append-only
   contract respected.

## Phases

| Phase | Delivers | Test budget |
| --- | --- | --- |
| [phase-01](phase-01-sha-namespace-probe-and-contract.md) | Namespace probe (live graphs) + sha-join contract in LINK_CONTRACT.md + failing fixtures (TDD red) | S |
| [phase-02](phase-02-engine-sha-join.md) | Engine sha-join implementation + telemetry + green unit suite (incl. edit-drift now firing) | M |
| [phase-03](phase-03-symbol-read-pagination-and-field-label.md) | Paginated symbol reads (resultset_size-proof) + Field label + cap regression test | M |
| [phase-04](phase-04-backfill-acceptance-docs.md) | One live reconcile on the live project over the full universe + acceptance gates + docs/log | M |

Execution order: 01 → 02 → 03 → 04 (04 runs last so the reconcile
happens exactly once, over both fixes).

## Acceptance

- After the Phase 04 reconcile on the live project: ≥95% of active records carry
  non-null `doc.source_sha` and `code.source_sha`; spot-check N records
  against the sync-state/inventory SHAs byte-for-byte.
- `symbol_universe` equals the sum of per-label server counts (no
  round-10000 signature); `find_doc_for_code(name="grid_unit_col")`
  returns ≥1 active record.
- Unit proof: editing a file's SHA in the baseline + re-running
  `build_links` appends stale copies for its links (previously impossible).
- `sha_join` telemetry appears in the sync report and `state.json`;
  ambiguity/missing counters are non-alarming but visible.
- Full `link-tiny/tests` + `tests/test_make_lifecycle.py` stay green;
  record schema (LINK_CONTRACT.md) unchanged.

## Risks

| Risk | Mitigation |
| --- | --- |
| `repo` semantics differ per parser (project root vs scan folder) | Phase 01 probe pins per-parser mapping on live graphs; fallback path covers null/foreign values; ambiguity → `None`, never a wrong SHA |
| Doc rel keys on Windows carry `\` separators | Flatten rule handles both separators (mirrors dev.py exactly) |
| Reconcile re-appends the whole active set | Append-only contract already defines supersede semantics; document expected file growth in Phase 04 |
| Field label adds many low-value links (generic designer names like `label1`) | Exact-name + existing stoplist/minimum-length rules still gate doc tokens; pair growth measured in Phase 04 before/after |
| Pagination changes row order vs today's index | Deterministic `ORDER BY id(n)` batching; dedup across batch boundaries covered by tests |
| Fixture drift from real graph shapes | Phase 01 fixtures are copied from the live sample graphs, not invented |
