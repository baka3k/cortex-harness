# Phase 07 Report: Stratified pilot and rollout decision

Date: 2026-08-21 · Branch: `feature/local-db` · Decision: **remain in containment**

## Outcome

Phase 07 now has a reproducible fail-closed pilot contract, executable three-mode
benchmark, immutable manifest, independent Pro*C scorecard, run-scoped report
bundle, and operator/developer runbook. The reviewed developer canary passed
all available local correctness and safety suites and achieved 100% direct-call
precision and recall on eligible reviewed facts.

Comprehensive semantic publication is **not promoted**. This checkout contains
only synthetic fixtures, not a real production corpus; one priority Pro*C TU
lacks a reviewed generated artifact/context, most required stratified Pro*C
production cohorts are unavailable, the million-LOC resource run and queue/
header-fan-out measurements are absent, and no live Neo4j or FalkorDB staging
canary was authorized/configured. Defaults remain unchanged
and semantic publication stays off/containment.

## Delivered components

### Pilot decision contract

`code-tiny/tools/cplus/pilot_rollout.py` provides:

- workspace-contained SHA-256 manifest validation and credential rejection;
- compile-context census for faithful, inherited, synthetic, missing,
  rejected, and failed cohorts, including priority ratios and stable reasons;
- reviewed direct-call precision/recall separated from virtual, indirect,
  dependent, unresolved, and unreviewed observations;
- impact-answer correctness plus an independent unsafe-negative check;
- a one-revision/configuration/query horizon check plus required cold, warm,
  and physically changed-TU conditions across all three modes;
- all five Pro*C labels and nine relationships as a separate non-aggregate
  scorecard with mapping, joins, filtering, invalidation, parity, security,
  resource, failure, publication, and rollback gates;
- non-waivable decision rules and deterministic manifest/evidence/report
  fingerprints;
- immutable-revision membership checks for every corpus file, protected
  benchmark-owned evidence, and deterministic JSON report-bundle writing.

### Executable pilot benchmark

`tests/benchmark_cplus_semantic_calls.py` measures:

- Tree-sitter containment cold/warm behavior;
- sparse and comprehensive Clang worker behavior with actual semantic-cache
  cold misses, warm hits, and physically changed-TU invalidation;
- latency, per-TU CPU, RSS, semantic-cache storage, failures, coverage, and
  edge-class counts; queue and header fan-out remain explicitly unmeasured and
  therefore fail their promotion gate;
- reviewed worker observations and Pro*C decode/mask/SQL facts;
- typed missing evidence for real scale and provider canaries.

The benchmark accepts reviewed external gate evidence but never promotes an
absent gate. Report output is under this plan's `reports/developer-canary/`.

### Immutable developer corpus

`pilot-manifest.json` binds the baseline revision and every corpus file by
SHA-256. It covers C, C++, a header, macro/template-heavy code, two build
configurations, generated Pro*C output, and original Pro*C. The new header
fixture ensures inherited-header coverage is visible rather than counted as a
standalone faithful TU.

The manifest is explicitly `synthetic_developer_canary`; the pilot enforces
that this class can never satisfy the real-workload gate. Its Pro*C cohort
census enumerates every required extension, language mode, encoding,
SQL/data-flow, map-quality, wrapper, configuration, and invalidation dimension.
Unavailable developer cohorts carry stable reasons and fail the independent
Pro*C gate.

### Test-runtime reliability

The journal crash/resume suite was previously uncollectable because the
repository did not declare `pytest-asyncio`. `pyproject.toml` now has an
explicit uv development dependency group for pytest and pytest-asyncio, and
`uv.lock` records it. The previously blocked async suite now passes 21/21.

### Operations

`docs/CPLUS_SEMANTIC_PILOT.md` documents manifest preparation, execution,
evidence supply, context states, strict/conservative semantics, diagnosis,
decision interpretation, and the existing rollback switch.

## Developer canary results

| Measure | Result |
| --- | ---: |
| Reviewed eligible facts | 13 |
| Reviewed direct calls | 9 |
| Direct precision | 100% |
| Direct recall | 100% |
| Unsafe negative answers | 0 |
| Priority faithful contexts | 6 / 7 (85.7143%) |
| Weak evidence promoted to `CALLS` | 0 |
| Focused gate suite | 303 passed + 10 subtests |
| Phase 07 test module | 22 passed |
| Semantic worker | pinned libclang 18.1.1 ready |

All measured developer-corpus per-TU latency, CPU, and RSS limits passed. The
resource gate remains failed because the required million-LOC run was not
performed. The independent Pro*C scorecard also fails the incomplete
stratified-cohort gate; unit-tested behavior is not reported as pilot coverage.

## Terminal decision

`remain_in_containment`

Failed hard gates:

1. real stratified workload;
2. priority faithful compile-context coverage (85.7143% < 90%);
3. complete Pro*C component gates (stratified cohort and production resource
   results absent);
4. overall million-LOC resource budget;
5. measured queue/cache/header-fan-out/storage operational evidence;
6. live Neo4j and FalkorDB provider canaries;
7. live deterministic publication;
8. live rollback with last-valid-generation retention.

Safe action: keep semantic publication off, retain Tree-sitter containment and
the last valid semantic generation, and prohibit repository-complete or unsafe
negative impact claims.

## Verification

- Phase 01–07 and owner-contract gate: **303 passed, 10 subtests passed**.
- Journal runtime after dependency fix: **21 passed**.
- Independent code review: **9.6/10, approved, 0 critical findings**.
- Repository regression suite (`tests/`): **996 passed, 207 subtests passed,
  37 unrelated pre-existing failures**. No Phase 07 test failed. The remaining
  failures are in ASP.NET/dotnet availability and fixtures, COBOL/Dart/Flutter
  fixtures/runtime, lifecycle environment expectations, and unrelated MCP
  backend expectations.

## Promotion remediation

Before reconvening, provide a SHA-bound real C/C++/Pro*C corpus, raise faithful
priority context coverage to at least 90%, complete the million-LOC cold/warm/
changed resource run including the Pro*C lane, and attach successful Neo4j and
FalkorDB deterministic publication/crash-resume/rollback canaries. Rerun the
same manifest/evidence/report workflow; do not change defaults independently of
an all-green report.
