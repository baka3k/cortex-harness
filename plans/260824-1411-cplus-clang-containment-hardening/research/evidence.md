# Research evidence: C/C++ Clang containment hardening

## Research question

Why does Clang currently produce fewer FalkorDB nodes/entities/rows than
Tree-sitter, what remains useful when the project does not build, and which
changes are required before semantic publication is safe?

## Conclusion

The current difference mixes valid plane differences with real adapter loss.
Clang itself is not expected to duplicate Tree-sitter's tolerant structural
model, but neither existing Clang path is safe to replace the entire structural
payload. Disable every cross-backend whole-payload winner; retain Tree-sitter
structure and use Clang protocol 2 only as faithful-context semantic evidence.

## Exact implementation evidence

| Area | Evidence | Finding |
| --- | --- | --- |
| Legacy fallback | `cplus_analyzer.py:3336-3392`, `:3743` | `off` uniquely enables replacement when Clang diagnostics are lower than Tree-sitter `ERROR` nodes |
| Cache bypass | `cplus_analyzer.py:3216-3240` | A recovered LIBCLANG cache may be returned before a fresh Tree-sitter baseline |
| Bounded repair | `parse_recovery.py:628-805` | `repair` still whole-replaces the payload and can use `free-mode` context |
| Candidate comparison | `parse_quality.py:306-327` | Cross-provider diagnostics are no longer compared, but structural replacement remains permitted |
| Mutable extent cache | `clang_parser.py:203-246`, `:341`, `:443`, `:471-476` | Cache key ignores list mutation; calls in later functions can disappear |
| Declaration filter | `clang_parser.py:407` | Definitions-only extraction drops prototypes and pure-virtual declarations |
| Identity | `clang_parser.py:420-443` | Name + arity + file and deduplication collapse same-arity overloads |
| Structural identity | `cplus_analyzer.py:433`, `:1562`, `:1751`, `:1850`, `:1966` | Tree-sitter `Function` IDs also use scope/name/arity/file, so USR-only observations cannot safely join current endpoints |
| Semantic context | `semantic_context.py:45-54`, `:112-117` | Faithful eligibility exists but is not wired into normal analyzer/sync runtime |
| Worker completeness | `semantic_worker.py:164-219`, `:553-569` | Request lacks fidelity state; zero diagnostics can report complete coverage |
| Strong edge | `call_evidence.py:121-132`, `guarded_publication.py:213-254` | Non-empty fingerprint is checked, faithful context is not |
| Query coverage | `cplus_mcp.py:493-555` | Coverage is project-aggregate, not exact requested/visited frontier |
| Impact service | `services/impact_service.py:51-110` | Missing proxy coverage safely falls to unknown, but the end-to-end contract is not wired |
| Runtime owner | `storage/gateway.py:51`, `storage/generation.py:18`, `guarded_publication.py:982-983` | Existing gateway/generation seams should own admitted publication; a second semantic publisher would split authority |

## Seven-fixture differential

The checked-in C/C++ semantic corpus (`direct.c`, `fp.c`, `macro_static.c`,
`overload.cpp`, `template.cpp`, `virtual.cpp`, `pilot_header.hpp`) produced:

| Measure | Tree-sitter | Legacy Clang adapter |
| --- | ---: | ---: |
| Calls | 13 | 9 |
| Structural relations | 34 | 0 |

Confirmed adapter losses include `fp.c::run -> apply` and both calls in
`macro_static.c::entry`, prototypes, pure-virtual declarations, overload
identity, and declaration/inheritance/type-use relations. Expected plane
differences include Tree-sitter primitive `Type` facts and lexical treatment of
constructs such as `static_cast`. Therefore:

- wrong gate: total Clang rows must equal or exceed Tree-sitter rows;
- correct gate: Tree-sitter structural identities/relations are invariant and
  every Clang evidence delta is additive, typed, and accounted for.

The invariance check must use a canonical structural projection rather than
serialized byte equality: labels, versioned stable IDs, normalized spans,
relation types/endpoints, and stable properties, excluding ordering, timing,
timestamps, and run-local IDs.

## Buildability decision

A complete project build is not a prerequisite for Clang semantic value. One TU
can be eligible when its exact flags, defines, target, include paths, headers,
generated inputs, source, and dependency fingerprints are available even if
linking, tests, packaging, or unrelated modules fail. A partial AST from missing
or guessed context is not authoritative; synthetic, inherited, missing,
rejected, failed, or truncated outcomes stay Tree-sitter-only with visible
noncoverage.

Compile-database loading must preserve multiple commands for one TU. The
current first-command behavior cannot represent conditional calls across build
variants, and a worker's self-reported fingerprint cannot attest trusted origin
or freshness. Context fidelity, admission, and execution coverage are separate
facts; only faithful + accepted + complete with parent-verified provenance is
eligible for strict evidence.

## Completeness and publication finding

Completeness cannot be inferred from whatever rows a traversal happens to
visit. Before work starts, the runtime needs an immutable expected scope
manifest keyed by project, generation, revision, policy, TU, and configuration.
An authoritative negative requires exact expected/actual key equality and one
matching complete record per key. Non-empty answers under incomplete coverage
remain useful but must be labeled `partial`.

Likewise, count-equivalent provider readback is insufficient. Every semantic,
coverage, graph, and vector row must be project/generation scoped; validation
must compare canonical sets/full digests against the exact inactive target
inside the owner's publication boundary. On context loss, the current view must
be a newly validated containment generation; the previous semantic snapshot is
historical only.

## Runtime wiring gap

Repository search finds protocol-2 worker, context registry/cache/scheduler,
evidence merge, guarded publication, and provider writers in tests, benchmark,
shadow tooling, and isolated modules, but not as one complete normal
`cplus_analyzer`/incremental-sync path. The plan must integrate existing
components rather than claim that Phase 06 unit coverage is production wiring.

## Active-plan contradictions

1. `260821-1144-cplus-semantic-call-graph` correctly declares Tree-sitter
   structure plus Clang semantics, while runtime still contains two structural
   replacement paths.
2. `260807-1329-parser-quality-recovery` explicitly permits a whole-file Clang
   winner; this decision is superseded by the dual-plane invariant.
3. The semantic plan is pending, Phases 01-06 are checked, and Phase 07 records
   containment. Its Phase 06 report also notes real generation orchestration is
   deferred despite stronger completion wording elsewhere.
4. Phase 07 measured 6/7 priority faithful contexts (85.7143%), below its 90%
   gate, and has no live FalkorDB/Neo4j publication/rollback canary.

## Test/environment baseline

The research pass ran:

```bash
.venv/bin/python -m pytest -q \
  tests/test_cplus_parse_recovery.py \
  tests/test_cplus_clang_worker.py \
  tests/test_cplus_semantic_worker.py \
  tests/test_cplus_semantic_context.py \
  tests/test_cplus_call_evidence.py \
  tests/test_cplus_evidence_merge.py \
  tests/test_cplus_guarded_publication.py \
  tests/test_cplus_pilot_rollout.py
```

Result: `6 failed, 186 passed, 10 subtests passed`. Four failures are missing
`neo4j` import prerequisites; two are pilot-manifest failures because revision
`0eede02...` is absent from the current Git object database. Phase 5 must
reproduce, classify, and repair these conditions before treating the suite as
promotion evidence.

## Deferred intentionally

- Full Clang structural/AST parity and equal raw row counts.
- Executing build systems or repository compile commands.
- Automatic generated-header reconstruction.
- LibTooling migration and per-node AST merge.
- Million-LOC, production Pro*C, and live provider gates already owned by the
  semantic rollout plan; this plan consumes rather than weakens them.
