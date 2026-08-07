---
type: HI Predict report
date: 2026-08-07
depth: quick
verdict: CAUTION
---

# HI Predict Report: Tree-sitter Parse Error Recovery

## Context

The observed C/C++ parser run reported:

- 20,186 scanned files;
- 6,673 files with a Tree-sitter root error flag or explicit `ERROR` node (33.06%);
- 2,650 explicit `ERROR` nodes;
- legacy CP932 samples containing `MISSING` nodes without explicit `ERROR` nodes;
- approximately 3,281 compile-command entries (16.25% of scanned files);
- five headers for which the existing C/C++ alternate-grammar retry selected the other parser.

The two headline counts do not measure the same thing. Since 2,650 explicit
`ERROR` nodes can affect at most 2,650 files, at least 4,023 of the 6,673 flagged
files (at least 60.29%) have no explicit `ERROR` node. They are likely dominated
by `MISSING`-node recovery or other root `has_error` conditions. AST traversal
continues, so this is a quality signal rather than a run-level parser failure.

Context retrieval note: `mind_mcp` contained no matching project passages and
`graph_mcp` was unavailable. The analysis therefore used Serena-backed source
inspection, the repository's exact-run evidence, and a focused test execution.
Code-derived confidence is high for current control flow and medium for the
distribution of root causes until the detailed report is enabled for a run.

## Executive Summary

Continue ingestion, but separate the fast recovered parse from a bounded repair
pipeline. Do not lower the libclang threshold globally or retry all 6,673 files.
First make per-file parse quality observable, then prioritize retries using
structural damage and semantic yield, run expensive fallbacks in isolated
workers under budgets, and label or quarantine low-confidence graph output.

The verdict is **CAUTION** because all identified risks have concrete
mitigations, but scaling fallback before adding resource isolation, compile-flag
sanitization, cache-context fingerprints, and provenance-aware publication could
increase latency and pollute the graph.

## Evidence From the Current Implementation

| Evidence | Current behavior | Consequence |
| --- | --- | --- |
| `cplus_analyzer._tree_error_stats` | Separately counts root `has_error`, explicit `ERROR`, and `MISSING` nodes | The printed file count cannot be compared directly with explicit `ERROR` count |
| `parse_c_family_file` | Continues extraction from the recovered Tree-sitter AST | Partial results remain available, but their confidence is not enforced downstream |
| `--parse-errors-path` | Can write per-file parser language, encoding, retry, `ERROR`, and `MISSING` metadata | The capability exists but is not wired through the root `dev.py` analyzer launcher |
| Header retry | Tries C and C++ grammars for ambiguous headers | Useful, but covers only one error cohort |
| libclang fallback | Triggers at 50, 100, or 200 explicit errors depending on file size | It cannot reach most low-error or `MISSING`-only files |
| Fallback selection | Compares Tree-sitter error count with clang diagnostic count | The numbers have different semantics and are not a reliable quality comparison |
| Parse cache | Avoids repeated work | It does not fully fingerprint compile context, parser versions, or recovery policy |
| Existing test | `python -m pytest -q tests/test_cplus_graph_runtime.py` passed 4 tests | Current metadata behavior is verified; remediation behavior still needs dedicated tests |

## Consensus Agreements

1. Continue parsing and publishing useful partial evidence; a Tree-sitter warning
   alone must not fail the full run.
2. Separate normal ingestion from repair. Expensive retries must not block graph
   availability for the entire corpus.
3. Turn detailed diagnostics on before changing fallback policy. Root causes must
   be grouped by extension, encoding, parser language, compile context, and error
   signature.
4. Use a bounded, prioritized recovery ladder rather than applying libclang to
   every flagged file.
5. Propagate parser backend, quality tier, and provenance so recovered output is
   not silently treated as authoritative.
6. Include parser, grammar, compile-command, encoding, and recovery-policy
   fingerprints in cache identity.

## Conflicts and Resolutions

| Topic | Architect | Security | Performance | UX | Devil's Advocate | Resolution |
| --- | --- | --- | --- | --- | --- | --- |
| When to repair | Quality assessment before extraction; staged queue | Isolated fallback only | Asynchronous, budgeted second pass | Immediate status and actionable progress | Start with the smallest diagnostic change | Finish the recovered first pass, emit quality metadata immediately, and process prioritized repairs in a resumable second pass |
| Fallback threshold | Replace raw counts with a composite score | Require containment and resource limits | Never lower globally | Show deterministic reason and outcome | Pilot before building a broad policy engine | Keep the current threshold until a 100-file pilot; then use structural-damage/semantic-yield scoring under a run budget |
| Report detail | Add locations and bounded snippets | Avoid source/path disclosure | Aggregate for cheap triage | Provide ranked causes and next actions | Do not build a dashboard first | Default to repository-relative paths, locations, normalized signatures, and aggregates; make sanitized snippets opt-in |
| Graph publication | Keep low-confidence evidence searchable but non-authoritative | Quarantine unsafe/low-confidence contributions | Publish baseline promptly | Never hide partial-quality status | Zero-error publication is unnecessary | Publish file/symbol evidence with quality markers; suppress strong call/inheritance edges only for critically damaged files |

## Risk Summary

| Risk | Severity | Persona | Mitigation |
| --- | --- | --- | --- |
| Recovered AST silently creates false symbols or relations | High | Architect, Security | Add quality/provenance fields; quarantine critically damaged strong edges; validate semantic yield |
| Unbounded fallback adds hours or exhausts memory | High | Performance, Security | Isolated worker processes, per-file timeout/RSS limits, bounded concurrency, run-wide file/time budget |
| Untrusted compile commands expose dangerous clang flags or external paths | High; conditionally critical for hostile repositories | Security | Never execute command strings; strict flag allowlist; canonical path containment; restricted filesystem/network |
| Existing threshold misses most flagged files | High | Architect, Performance | Classify `MISSING`, error-span, encoding, grammar, and extraction-yield cohorts; target retries by damage/value |
| Stale cache hides parser/context improvements | Medium-High | Architect, Performance | Fingerprint grammar, parser/backend versions, compile flags, encoding, and recovery-policy version |
| Detailed diagnostics leak host paths or source | Medium | Security, UX | Repository-relative normalized paths, restrictive permissions/retention, sanitized opt-in snippets |
| Operators misread 6,673 files as 6,673 hard failures | Medium | UX, Devil's Advocate | Split clean, explicit-`ERROR`, `MISSING`-only, lossy-decode, retry-success, and quarantined metrics |

## Persona Details

### Architect

**Concerns:** The current metric is not a quality measure; recovered graph data
lacks an enforced confidence boundary; fallback happens after Tree-sitter
extraction; cache identity omits parts of parse context; compile-command coverage
must be evaluated for translation units rather than all headers.

**Recommendations:** Introduce a stable `ParseQuality` contract between parsing
and extraction; route retries by source cohort; select a whole-file winner using
structural damage plus semantic yield; propagate backend/quality provenance;
fingerprint actual parse context. **Confidence:** high for architecture, medium
for cause distribution.

### Security

**Threats:** Malformed source can cause parser denial of service; in-process
libclang failures can stall or crash ingestion; unsafe compile arguments and
paths can widen filesystem/native-code exposure; poisoned partial ASTs can
damage graph integrity; reports/caches can disclose proprietary paths or source.

**Mitigations:** Run fallback in disposable, resource-limited subprocesses; use a
strict compile-flag allowlist without executing commands; canonicalize and
contain all paths; cap files, AST traversal, diagnostics, and reports; use
relative paths and restrictive retention; keep recovery non-destructive.
**Severity:** high, conditionally critical for attacker-controlled repositories.

### Performance

**Bottlenecks:** With only 2,650 explicit errors and a minimum threshold of 50,
at most 53 files could currently qualify for fallback (and likely fewer). A
synchronous retry of 6,673 files would add `6,673 × fallback latency`; at an
illustrative 0.5-2 seconds per file this is about 56 minutes to 3.7 hours.

**Alternatives:** Keep recovered Tree-sitter as the fast baseline; persist a
resumable repair queue; prioritize damaged/high-value files; borrow compile flags
from representative translation units for headers; cache both successful and
terminal non-improvements. Start with a maximum of 500 files or 15 minutes per
run, one alternate Tree-sitter parse and one libclang attempt per file, then tune
from measurements. **Confidence:** high for reachability/cost bounds, medium for
absolute latency.

### UX

**Issues:** The single summary line mixes file-level and node-level semantics;
only ten sample paths are shown; the root CLI does not expose the detailed
artifact; downstream users cannot tell whether a result is clean, recovered,
repaired, or quarantined.

**Edge cases:** `MISSING`-only files, CP932/lossy decoding, generated/vendor code,
ambiguous headers, Pro*C, Windows resources, absent compile context, and cached
old results need distinct statuses. CLI output must remain useful in plain text
and machine-readable without relying on color. **Accessibility concern:** long
raw-path logs are difficult to navigate; stable summaries and a report path are
preferable to thousands of emitted lines.

### Devil's Advocate

**Assumptions challenged:** A flagged file is not necessarily unusable; zero
Tree-sitter errors is not the product goal; a lower clang diagnostic count is not
proof of a better AST; every header does not need its own compile-command entry.

**Simpler alternative:** First wire the existing JSON report into normal runs,
correct the metric labels, and build a 100-file stratified gold corpus. Do not
build a universal multi-parser merge engine before evidence shows which cohorts
damage graph results. **Worst case:** retrying all flagged files increases runtime
by hours, consumes memory, persists stale cache entries, and replaces useful
recovered ASTs with context-poor fallback output.

## Recommendations

1. **Expose a run-scoped parser-quality artifact from `dev.py`.** Reuse
   `--parse-errors-path` for C/C++ and define a small common diagnostic schema for
   other analyzers. This is the minimum change that makes the problem measurable.
2. **Correct the summary semantics.** Report file-level `has_error`, explicit
   `ERROR` nodes/files, `MISSING` nodes/files, lossy decoding, retry attempts,
   retry improvements, and quality tiers separately.
3. **Cluster before retrying.** Group by source kind, extension, encoding,
   grammar choice, compile-context availability, generated/vendor status, error
   location/signature, and extraction yield.
4. **Add per-file quality and provenance.** At minimum use `clean`,
   `recovered-low-damage`, `retry-required`, and `quarantined`; attach backend,
   language, compile-context fingerprint, and policy version.
5. **Use a bounded recovery ladder.** Try encoding validation, C/C++ header
   grammar retry, dialect-specific masking/preprocessing, context-aware libclang,
   then minimal fallback/quarantine. Permit at most one attempt per stage.
6. **Make expensive fallback asynchronous and isolated.** Apply file/time/memory
   budgets, a small worker pool, cohort circuit breakers, and resumable results.
7. **Select improved results by semantic yield, not diagnostic count alone.** Use
   structural error-span coverage, top-level declaration/function/type recovery,
   scope consistency, and stable symbol/call counts.
8. **Fix cache identity before scaling repair.** Include source hash, encoding,
   parser/grammar versions, compile-context fingerprint, fallback backend, and
   recovery-policy version.
9. **Validate with a stratified 100-file pilot.** Include clean C/C++, ambiguous
   headers, macro-heavy/generated files, CP932, Pro*C, resource files, with and
   without compile context. Track p50/p95 latency, RSS, timeout rate, quality-tier
   changes, semantic yield, and graph cardinality/correctness.

## Next Steps

Because the verdict is **CAUTION**, address these mitigations before broad
fallback rollout:

1. Wire and enrich the diagnostics artifact and relabel current metrics.
2. Build the stratified pilot corpus and establish current semantic-yield and
   performance baselines.
3. Define the `ParseQuality`/provenance contract and quarantine policy.
4. Design isolated, budgeted recovery workers and compile-argument filtering.
5. Only then tune recovery routing and fallback thresholds from observed cohort
   results.

## Success Criteria

- Normal ingestion completes even when individual files contain recovered syntax.
- Every extracted entity is traceable to parser backend and quality tier.
- Parser-quality and graph-write health are reported separately.
- Expensive recovery remains within explicit wall-time, memory, and file budgets.
- Cache entries are invalidated when parse context or recovery policy changes.
- Gold fixtures show improved symbol/call correctness without unacceptable p95
  runtime or peak-RSS regression.

## Unresolved Questions

1. Which source root and exact file cohorts produced the 6,673 flags?
2. How many flagged files contain only `MISSING` nodes, and where are those nodes
   located relative to declarations and function boundaries?
3. What percentage of translation units—not all scanned files—have usable compile
   commands, and which headers can inherit their context?
4. How much do current parse warnings affect expected function, type, and call
   extraction on a reviewed gold sample?
