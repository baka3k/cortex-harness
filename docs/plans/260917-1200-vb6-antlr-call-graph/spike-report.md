# Spike Report — Phase 02: Vendor ProLeap + Java Worker

Date: 2026-09-17. Fixture: `tests/fixtures/vb6-application/` (11 files).

## Vendor & build outcome (task 2.1)

- Pinned upstream: `uwol/proleap-vb6-parser` @ `53e7b5c5754d108225be5a925026d3608c6abe75`
  (branch `main`; pom version `3.0.0`). **No `v3.0.0` git tag exists** — tags stop
  at v2.2.0; the pin is the main-branch commit whose pom declares 3.0.0.
- Vendored `pom.xml` + `LICENSE` + `src/main` only (tests / .github dropped,
  ~2.2 MB saved). See `vendor/proleap-vb6-parser/VENDOR.md`.
- **JDK 26 build: PASS.** Upstream declares `maven.compiler.source/target=17`;
  the worker module adds `maven.compiler.release=17`. No toolchain pin needed.
  Maven 3.9.16, OpenJDK 26.0.2. Shaded jar: `worker/target/vb6-antlr-worker.jar`
  (~15.6 MB, includes ANTLR 4.7.2 runtime + ProLeap + Gson).
- Build layout: aggregator pom (packaging `pom`) + modules `vendor/proleap-vb6-parser`
  and `worker`. Rebuild: `mvn -q -f code-tiny/tools/vb/antlr_worker/pom.xml -DskipTests package`.

## Worker protocol (task 2.2) — verified by `tests/test_vb6_antlr_worker_contract.py`

- argv `--manifest <path> --workspace-timeout-ms <n> --parse-cache-version <v>`;
  manifest `{"root", "project", "files": [{"file_path", "parse_path"?}]}`.
- Whole-program `VbParserRunnerImpl.analyzeFiles` with `ignoreSyntaxErrors=true`
  (workspace-level timeout; per-file timeout is impossible inside one Program —
  red-team F3 acknowledged).
- Per-file isolation: syntax-error pre-validation per file; if the batch throws,
  the batch is retried without the error files and those report `ok=false`
  (`excluded after batch failure`). A module that registers zero procedures
  with syntax errors also reports `ok=false`.
- Call rows carry `{caller_id, caller_scope, callee_name, callee_id?,
  callee_arity?, call_line, site_column, call_type, resolution_status,
  callee_member}`; `resolution_status ∈ {asg_resolved, external, undefined}`.
- Calls are collected by walking each procedure's parse tree and resolving
  contexts through the ASG registry (UNDEFINED calls have no target-side holder
  in the ASG, so scope-element traversal alone cannot see them).
- Noise filters applied: CallDelegate/MembersCall wrappers, MODULE/ME/DICTIONARY
  call types, bare receiver rows (`Debug` next to `Debug.Print`), bare
  module/class references (`New clsOrder`), argument lists stripped from names.

## M3 — parse success (gate ≥95%, excluding sanctioned malformed.bas)

**10/10 well-formed files ok = 100%.** `malformed.bas` reports `ok=false`
("no procedures after parse (1 syntax errors)") and does not sink the batch
(all other files still produce payloads) — red-team F3 verified.

## .frm materialization (task 2.3 / AD-03) — PROVEN

- Adapter strips everything before the first `Attribute VB_Name` and replaces
  it with **blank lines** so worker line numbers match the ORIGINAL .frm
  (verified: `frmMain.Form_Load` start_line == 31).
- Both forms produce full payloads (functions + calls); `frmAbout`/`frmMain`
  emitted as Class nodes with `kind=form`.
- Module name comes from `Attribute VB_Name` (upstream prefers the declared
  name); temp-file stem never leaks into symbol ids.

## Cross-module resolution (ASG)

- `CalcTotal 1, 2` (unqualified, cross-module) and `modUtil.CalcTotal(3,4)`
  both → `modUtil.CalcTotal/2@modUtil.bas` with `callee_id` ✓.
- With-block `.ProcessOrder` → `clsOrder.ProcessOrder/1@clsOrder.cls` ✓.
- Property `ord.Total = 5` / `amount = ord.Total` → `clsOrder.Total/1` (let) /
  `clsOrder.Total/0` (get) ✓. Self-property `Total = 42` inside clsOrder ✓.
- Interface dispatch `ship.Ship_Order` (As IShip) → `IShip.Ship_Order/1@IShip.cls`.
- Known limitation (expected): unqualified `TestSameName` (two forms) resolves
  ARBITRARILY to `frmAbout.TestSameName` in the ASG. The phase-04 python
  resolver must re-derive unqualified calls and mark this case `ambiguous`
  instead of trusting the worker's pick.
- `Implements` statements have no ASG metamodel (grammar-only): the worker
  line-scans module lines for `^\s*Implements\s+(\w+)` and reports
  `implements_map` + emits `interface::<name>@<rel>` Interface nodes (AD-10).

## M4-spike — throughput (gate: <2x regex per-file; JVM startup ≤5s)

Corpus: 132 files (12 fixture replicas, unique module names, .frm materialized).

| Metric | Value |
|---|---|
| regex (parse_vb_file, in-process) | 0.11 ms/file |
| antlr worker (batch, amortized) | **10.15–10.66 ms/file** |
| ratio | **~96x — RELATIVE GATE FAIL** |
| JVM startup (1-file run, incl. process spawn) | **0.14 s — PASS (≤5s)** |
| absolute budget | ~10.2 s per 1000 files, single batch |

- `--workspace-timeout-ms` governs the whole batch; per-file timeout remains
  impossible inside one Program (design accepted, plan F3).
- JIT tuning (`-XX:TieredStopAtLevel=1`) measured: no improvement (12.5 ms/file);
  the cost is ProLeap's 6 ASG passes (type defs, declarations, expressions,
  4x type assignment), not JIT warmup.

### Gate deviation & recommendation

The `<2x regex` relative gate is unachievable for ANY parser that builds a
cross-module ASG (regex does zero resolution; the whole point of this plan is
that regex cannot produce the call graph). Absolute cost is ~10 ms/file —
roughly 11 s for a 1000-file project per sync, negligible next to embedding +
Neo4j/Qdrant writes. **Recommendation: keep engine=antlr as the default
(AD-07) and re-express M4 as an absolute budget** (e.g. amortized ≤50 ms/file
on ≥100 files, JVM startup ≤5 s). Flagged for owner review; if rejected,
`--vb6-parser-engine regex` preserves today's behavior exactly.

## Issues encountered (upstream)

- No v3.0.0 tag (pin by commit); Maven Central 404 (issue #18) — vendored.
- `.frm`/`.ctl` get no ASG module (issue #20) — materialization workaround proven.
- `Implements` unsupported in ASG metamodel — worker line-scan (documented above).
- ProLeap resolves ambiguous unqualified calls arbitrarily — python resolver
  re-derives (phase 04).

## Verdict

Spike gates M3 (100%) and .frm proof: **PASS**. Protocol contract test: 8/8
green. M4 relative gate: **FAIL (documented deviation, absolute budget sound)**.
Proceeding to phase 03 wiring per AD-02/AD-07 defaults with the M4 gate
reframing proposed above.
