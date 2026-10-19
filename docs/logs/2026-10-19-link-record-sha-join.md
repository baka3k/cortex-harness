# link-tiny sha-join + full symbol universe — 2026-10-19

[inline:log-writer]

## Context

Plan `docs/plans/261019-1700-link-record-sha-join/plan.md` (hi-plan fast;
repairs the Phase-3 engine of the parent feature
`261019-1126-link-tiny-doc-code-traceability`). Two live defects on
the live project (built 09:15Z):

1. **Every record carried `source_sha: null` on both sides** (2002/2002
   actives) while `state.json` held 68 doc + 2251 code baseline SHAs —
   namespace mismatches: doc graph `source_id` is the `__`-flattened rel
   path vs POSIX baseline keys; code graph `file_path` is folder-relative
   vs project-relative inventory keys. `_sha_changed` could never fire,
   so content edits never stale-propagated.
2. **`symbol_universe` 28992 was an artifact.** FalkorDB's server-wide
   `resultset_size` 10000 silently truncated every label read (Function
   50437, Variable 38619, Field 27941 → exactly 10000 each) and `Field`
   was not even in `CODE_SYMBOL_LABELS` — designer controls were
   unreachable. Concrete miss: doc token `grid_unit_col` (para 21
   of the order-summary business-rule spec) → 0 links.

Evidence pinned by `link-tiny/tests/probe_sha_namespaces.py` (live
markdown report): flatten rule hit-rate 67/67; `repo` = absolute
scan-folder on every symbol label, **null on endpoint labels**
(HttpEndpoint/Route); s1+s3 carry **1 exact folder-relative collision**
(`src/shared/_shared_widget.vb`) and **152 cross-folder suffix
shadows** — naive suffix matching is forbidden by data.

## Change

- **`link_engine.py`**: `flatten_doc_rel` + `build_doc_sha_index`
  (direct → flattened → *unique* stem; a flattened id/stem shared by
  keys with different SHAs resolves to null, never a guess);
  `normalize_repo_key` + `load_code_folder_inventories`
  (`{resolved folder → {folder_rel → sha}}`, shared chain reader with
  the flat loader); `CodeShaIndex.resolve` — `(repo, path)` exact →
  unique cross-folder fallback → flat direct/unique-suffix →
  `missing`, with `>1 candidate → ambiguous → null`;
  `EngineSymbol.repo` (join input only — **identity and record schema
  unchanged**); paginated label reads `ORDER BY id(n) SKIP/LIMIT`
  (batch 5000, `id(n)` dedup, per-label `{rows_read, expected, kept}`
  accounting, shortfall = loud stderr warning); `Field` added to
  `CODE_SYMBOL_LABELS`; `BuildReport.sha_join` +
  `symbol_universe_by_label`; `state.json` gains `sha_join` +
  `symbol_universe_by_label` (record schema untouched).
- **`link_worker.py`**: passes folder inventories into the engine;
  summary echoes `sha_join` + `symbol_universe_by_label`.
- **`mcp_server.py`**: `trace_status` passes `sha_join` +
  `symbol_universe_by_label` through.
- **Tests**: `test_sha_join.py` (contract fixtures from live shapes:
  flatten/lookup order, literal-`__` filenames, ambiguous stems,
  repo-exact vs shadow, null/foreign repo fallback, ambiguity → null,
  edit-drift firing, no-op skip, deletion staling);
  `test_link_engine.py` (+pagination: cap-proof reads, batch-boundary
  dedup, loud shortfall, Field link, universe == Σ server counts,
  symbol-beyond-cap regression); `test_mcp_tools.py` (sha_join
  passthrough); `test_normalizers.py` (designer-token noise guard).
- **Verifiers**: `probe_sha_namespaces.py` (standalone probe) and
  `verify_record_shas.py` (read-only byte-for-byte re-verification with
  exit-code gate; restores `repo` from the live graph because records
  deliberately don't carry it).
- **Docs**: `LINK_CONTRACT.md` §1.5 (SHA join) + §1.6 (full symbol
  universe); README stale-behavior + coverage notes.

## Impact (live reconcile, 2026-10-19 14:01Z)

| metric | before | after |
| --- | --- | --- |
| active links | 2002 | **13069** |
| docs with active links | 16 | 19 |
| symbols referenced | 590 | 3518 |
| symbol_universe | 28992 (truncated) | **125989** = Σ per-label counts |
| records with non-null SHAs | 0% both sides | **100% both sides** |
| `grid_unit_col` | 0 links | 2 (Designer.vb Variable + Designer.cs Field) |
| `UNIT_COL_NO` / `UNIT_COL_NAME` | 0 | 8 / 10 (multi-file fanout) |

`links.jsonl` grew 0.9 MB → ~7.3 MB (append-only: 2002 stale copies +
13069 fresh records; supersede semantics per contract). `sha_join`
telemetry: doc 13069/13069 matched, code 13069 matched / 0 missing /
**0 ambiguous**. Byte-for-byte gate: 25 sampled + all 13069 actives
re-verified against the baselines — 0 mismatches, 0 unexplained nulls.
MCP gate: `trace_status` reports sha_join + universe; `find_code_for_doc`
/ `find_doc_for_code` carry non-null `source_sha`.

## Decision

- **Stale-drift live proof = the unit-level proof** (Phase 02 test
  `test_build_links_edit_drift_fires`): producing a live code edit would
  require a re-sync cycle and pruning inventory entries would violate the
  engine's read-only rule. The unit test drives the exact same baseline
  delta the real sync produces.
- **Ambiguity never guesses.** The one colliding file
  (`_shared_widget.vb`) resolves via its `repo` pin at build time; the
  verifier initially flagged it because records don't store `repo` — the
  verifier now restores the pin from the live graph instead of weakening
  the join.
- **No server config writes**: pagination instead of raising
  `resultset_size` — the engine stays read-only on the graph server.
- Pair growth 2002 → 13069 accepted: exact-name + stoplist/min-length
  rules unchanged; growth came from previously invisible symbols
  (truncation) and designer `Field` controls that documents name
  literally.

## Review (adversarial, code lens — 6 findings, all fixed)

An isolated review pass over the diff found two High and four Medium
defects; every fix ships with a regression test, and the post-fix live
dry-run over the reconciled store is a strict no-op (0 new / 13069
unchanged — the oscillation class is dead on real data):

1. **High — write/drift paths could resolve duplicate identities
   differently** (last-wins vs first-wins repo across scan folders) →
   identical builds oscillated and grew the log. Fix: one join per
   identity, cached first-wins and shared by both paths.
2. **High — a known-folder `repo` whose map lacked the path borrowed
   another folder's SHA as "matched"** (the collision shape under
   inventory lag), and the verifier inherited the blindness. Fix: a repo
   pin is authoritative — known-folder miss → `missing`, never borrowed;
   contract §1.5 updated.
3. **Medium — §1.6's universe invariant didn't match the code.** Fix:
   `symbol_universe` = Σ kept (matched index), new
   `symbol_universe_expected` = Σ server counts (completeness gate,
   state.json + report + trace_status); unreadable labels stay visible
   with `error: 1` instead of vanishing.
4. **Medium — doc-side reads were still unpaginated**; a silent
   paragraph cut would stale its own links. Fix: the same keyset
   pagination + count + loud shortfall warning on `Paragraph` and
   `HAS_ENTITY` reads.
5. **Medium — ladybug pagination claim unverified.** Fix: honest comment
   (query shape verified on FalkorDB; per-label degradation + a new
   destructive-read guard surface a collapse loudly). The guard aborts a
   build whose symbol read collapses to 0 against a non-empty prior
   state instead of stale-marking everything (`--reconcile` bypasses;
   doc wipes still stale — only code reads swallow failures).
6. **Medium — same-SHA stem/flattened collisions were treated as
   ambiguous** (stricter than contract, silently disabling edit
   detection for those docs). Fix: agreeing SHAs resolve; only differing
   SHAs are ambiguous; also fixed a latent revival bug where a later
   same-SHA key un-marked an established ambiguity.

Post-fix live verification: verifier PASS (0 mismatches, 0 unexplained
nulls, 100% non-null); the Σ expected − Σ kept gap of 40 rows is
explained per label (live project's HttpEndpoint/Route nodes carry no
`source_file`, so they cannot form a stable identity — reads complete,
kept 0). `symbol_universe_expected` lands in `state.json` on the next
natural build (the one planned reconcile was not repeated to avoid
re-appending 13k records).

- **Stale-drift live proof = the unit-level proof** (Phase 02 test
  `test_build_links_edit_drift_fires`): producing a live code edit would
  require a re-sync cycle and pruning inventory entries would violate the
  engine's read-only rule. The unit test drives the exact same baseline
  delta the real sync produces.
- **Ambiguity never guesses.** The one colliding file
  (`_shared_widget.vb`) resolves via its `repo` pin at build time; the
  verifier initially flagged it because records don't store `repo` — the
  verifier now restores the pin from the live graph instead of weakening
  the join.
- **No server config writes**: pagination instead of raising
  `resultset_size` — the engine stays read-only on the graph server.
- Pair growth 2002 → 13069 accepted: exact-name + stoplist/min-length
  rules unchanged; growth came from previously invisible symbols
  (truncation) and designer `Field` controls that documents name
  literally.

## References

- Plan: `docs/plans/261019-1700-link-record-sha-join/` (phases 01–04)
- Contract: `docs/LINK_CONTRACT.md` §1.5 (SHA join), §1.6 (full symbol
  universe)
- Parent feature log: `docs/logs/2026-10-19-link-tiny-doc-code-traceability.md`
- Probe/verifier: `link-tiny/tests/probe_sha_namespaces.py`,
  `link-tiny/tests/verify_record_shas.py`
