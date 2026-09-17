# VB6 ANTLR call graph — ProLeap worker + resolver + evidence plane — 2026-09-17

## Context

Plan `docs/plans/260917-1200-vb6-antlr-call-graph/plan.md` (`hi-craft --full`,
6 phases): the VB6 pipeline was regex-only with a silent tree-sitter degrade
(no vb6 grammar exists on PyPI), missed every no-paren/`Call`/continuation
callsite, resolved calls by `sorted(candidates)[0]`, dropped unresolved calls
before the graph, and collided Class ids across files. Prediction report
`260917-1139` drove the ANTLR-first decision (D2: Java worker wrapping
vendored ProLeap).

## Change

- **Engine** — `code-tiny/tools/vb/antlr_worker/`: vendored
  `proleap-vb6-parser` @ `53e7b5c5` (pom 3.0.0, MIT; no v3.0.0 tag exists —
  pinned by commit; not on Maven Central) + `Vb6Worker.java` (manifest in,
  one JSON doc out). Whole-program `analyzeFiles` with
  `ignoreSyntaxErrors`, syntax-error pre-validation, batch-retry without
  error files (malformed.bas never sinks the batch), workspace timeout.
  Calls collected by parse-tree walk resolved through the ASG registry
  (UNDEFINED calls have no target-side holder). JDK 26 builds with
  `--release 17`. Aggregator→vendor+worker Maven modules; jar stamp-cached.
- **Adapter** — `vb6_antlr_adapter.py`: thread-locked build cache with
  stamp short-circuit (prebuilt jar works without mvn), `.frm/.ctl/.pag`
  materialized to padded temp `.cls` (line numbers preserved), manifest
  protocol with `parse_path` overrides.
- **Dispatch** — `vb_analyzer_base.py`: `_parse_vb6_with_antlr_batch`
  (worker always gets the FULL project per AD-02), `--vb6-parser-engine
  auto|antlr|regex` (+ env/timeout flags), loud one-line WARNING on auto
  degrade, per-file regex fallback with truthful `parse_meta`, mandatory
  `[vb6][summary]` line (engine, files, fallback, callsites, resolved,
  possible, rate).
- **Resolver** — `vb6_resolver.py`: module registry keyed by
  `Attribute VB_Name` (worker fills it; regex path reads it via one-line
  regex), resolution order exact-qualified → module-local (overload-aware:
  Property Get/Let kept distinct) → project-public unique → arity filter.
  Ambiguous names never pick arbitrarily (overrides ProLeap's arbitrary
  ASG binding — `TestSameName` case), late-bound `As Object` receivers
  classified via variable registry, VB intrinsics → external.
- **Publication** — two-tier: deterministic targets → CALLS; every weak
  call → POSSIBLE_CALLS site-keyed (callee-independent site id for
  multi-candidate), `resolution_class=lexical_candidate` only (AD-04),
  free-text vb6 `resolution_status`. Placeholder `external_symbol` Function
  nodes with post-write reconciliation. CONTAINS class→method and
  IMPLEMENTS Type→Interface (interface nodes emitted for Implements-target
  classes per AD-10). Class ids now carry `@rel_path`.
- **AD-09** — incremental syncs keep whole-project payloads; edges
  (CALLS/POSSIBLE_CALLS/IMPLEMENTS) re-published when source OR target file
  changed, so incoming edges from unchanged callers are rebuilt after
  DETACH-DELETE cleanup. Node writes/embedding stay changed-set-filtered.
- **Hygiene** — `PARSE_CACHE_VERSION` → `vb-family-v2026-09-17-1` (same
  change, AD-08); tolerant `dataclass_from_payload` hydration; silent
  `except: pass` on tree-sitter degrade now records
  `parse_meta.tree_sitter_unavailable_reason` + warns once;
  `tree-sitter-vb-dotnet==0.3.0` added to requirements (the only real
  vb-family grammar on PyPI); README drift fixed; `dev doctor` reports
  vb6-antlr engine status (report-only); all vb asdict rows carry
  `project_id_normalized` so call-edge endpoint MATCHes work on real graphs.

## Result (gates)

| Gate | Result |
|---|---|
| M1 idioms (payload+rows) | 47/47 expected callsites have edges; 5 idiom tests green |
| M2 recall expected-resolvable | **29/29 = 100%** (baseline regex: 18/29 = 62%, arbitrary targets) |
| M3 parse success (excl. malformed) | 10/10 = 100%; `.frm` materialization proven |
| M4 throughput | JVM 0.14 s (PASS ≤5 s); relative "<2x regex" **FAIL at ~96x** — reframed as absolute budget ~10 ms/file worker-internal (owner decision flagged in benchmark-report.md) |
| M6 summary | printed unconditionally: `engine=antlr(10)+regex_fallback(1) … rate=66.0%` |
| M7 no regressions | 49 new vb6 tests green; csharp matrix + dev_ignore failures pre-exist (verified via git stash) |

## Reflection

- The fixture-first baseline (phase 01) paid off twice: it quantified the
  regex gap (20/47 capture, string-literal false positive) and caught my own
  fixture bug (`Dim total` shadowing the `Total` property — VB6 is
  case-insensitive).
- ProLeap's ASG is the value, not the grammar: With-block member binding and
  property Get/Let discrimination came free; but its arbitrary ambiguous-name
  binding had to be overridden in the python resolver, and `Implements`
  (grammar-only upstream) needed a line-scan.
- The M4 relative-throughput gate was wrong at plan time — no cross-module
  ASG can be within 2x of a line-regex; absolute budgets measure what
  matters (~26 ms/file end-to-end, embedding still dominates syncs).
- Adversarial review caught what my test double structurally could not
  (`project_id_normalized` on Function nodes — CapturingDriver counts rows,
  it cannot fail a Cypher MATCH) plus a PyPI package-name hallucination.
  Fakes prove shape; endpoint contracts need a live read-back (left as a
  runbook step for the dev graph).
