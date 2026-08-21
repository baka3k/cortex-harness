# C/C++/Pro*C semantic pilot operations

The Phase 07 pilot is a fail-closed rollout tool. It measures containment,
sparse semantic analysis, and comprehensive eligible-TU semantic analysis on
one immutable manifest, but it does not enable semantic publication. Only a
report whose every hard gate passes permits a default change.

The checked-in developer canary is intentionally classified as
`synthetic_developer_canary`. Its terminal decision is
`remain_in_containment`; it cannot stand in for a production corpus or a live
Neo4j/FalkorDB canary.

## Run the developer canary

From the repository root:

```bash
uv run python tests/benchmark_cplus_semantic_calls.py \
  --gate-evidence plans/260821-1144-cplus-semantic-call-graph/pilot-gate-evidence.json \
  --output-dir plans/260821-1144-cplus-semantic-call-graph/reports/developer-canary
```

The output directory contains four versioned artifacts:

- `pilot-manifest.json`: the exact immutable input horizon;
- `pilot-evidence.json`: observations, toolchain fingerprints, resource
  measurements, suite results, provider state, and fault evidence;
- `proc-scorecard.json`: the independent Pro*C component/gate table;
- `rollout-decision.json`: metrics, hard gates, one terminal decision, the
  safe action, and deterministic fingerprints.

Do not hand-edit generated report files. Change the source manifest or gate
evidence, rerun the benchmark, and review the resulting fingerprint change.

## Prepare a real pilot manifest

Copy the checked-in manifest and replace the corpus with repository-relative,
SHA-256-bound files from an immutable revision. Set `workload_class` to `real`
only when the corpus is a genuine canary workload. The corpus must include C,
C++, headers, macro/template-heavy files, multiple configurations, generated
code, and both C-mode and C++-mode Pro*C cohorts from the component map.

Every TU/configuration record must be one of:

- `faithful`: exact reviewed compile context;
- `inherited`: a header context inherited from a named including TU;
- `synthetic`: safe fallback, never promotion eligible;
- `missing`: no usable context;
- `rejected`: unsafe or contract-invalid context;
- `failed`: a bounded worker failure.

All non-faithful records require a stable reason. Absolute/external corpus
paths, path traversal, credential-bearing flags, and corpus hash drift fail
manifest loading before analysis. Every corpus file must also exist at the
manifest's Git revision; a worktree-only fixture is rejected.

The `proc_cohort_census` must enumerate the complete Phase 07 Pro*C matrix.
Missing developer cohorts remain `unavailable` with stable reasons. They must
not be relabeled as covered merely because an isolated unit test exists.

## Supply live gate evidence

Use `--gate-evidence` for reviewed results that cannot be derived from the
worker benchmark, especially staging providers and publication boundaries.
The evidence must preserve:

- exact Neo4j and FalkorDB graph/provider fingerprints;
- deterministic rerun, crash/resume, atomic publication, and rollback results;
- cold, warm, changed-TU, and million-LOC resource measurements;
- strict/conservative consumer outcomes and incomplete-negative handling;
- the separate Pro*C map, SQL, join, filtering, parity, invalidation,
  security, resource, publication, and rollback results.

External gate evidence may only populate the documented suite, impact,
provider, scale, operational, publication, rollback, security, and Pro*C
fields. It cannot replace benchmark-owned observations, contexts, modes,
worker readiness, or measured resources. Fingerprints are mandatory for
reviewed impact replays and live canary evidence.

An absent value remains `not_run` or false. Unit-test success is not converted
into a live provider pass. Never include raw precompiler commands, credentials,
connect descriptors, environment secrets, or external absolute paths.

## Interpret coverage and decisions

`strict` means accepted `direct_resolved` evidence only. `conservative` may
include virtual, indirect, dependent, unresolved, and lexical evidence without
relabeling it. A negative result is authoritative only when the visited
semantic frontier is complete; otherwise the outcome must be `incomplete`.

Possible terminal decisions are:

- `promote_comprehensive`: every hard gate passed; the recorded configuration
  is eligible for a separately reviewed default change;
- `remain_in_containment`: a correctness, safety, Pro*C, publication,
  rollback, or critical gate failed;
- `revise_and_reconvene`: measurements are safe but incomplete; sparse mode
  may continue shadow-only without repository-complete or negative claims.

The report's `safe_action` is authoritative. `defaults_may_change=false`
prohibits enabling comprehensive semantic publication.

## Roll back and diagnose

Set the existing publication switch to containment/rollback mode; no reparse is
required to serve the last valid generation:

```bash
export CORTEX_SEMANTIC_PUBLICATION_MODE=rollback
```

Verify that the operator status reports semantic publication disabled, weak
Tree-sitter evidence retained, and either the last valid semantic generation or
explicit containment when no ledger entry exists.

For a failed run, inspect in this order:

1. manifest and evidence fingerprints/revision;
2. compile-context census and stable noncoverage reasons;
3. direct precision/recall by class, never aggregate edge count alone;
4. unsafe negative-answer count;
5. the separate Pro*C scorecard;
6. cold/warm/changed and million-LOC budgets;
7. provider, publication, rollback, failure-isolation, and security gates.

Do not waive a failed hard gate. Preserve the report bundle and remediate the
specific failed gate before reconvening.
