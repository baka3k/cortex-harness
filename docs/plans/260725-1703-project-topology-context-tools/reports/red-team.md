---
type: red-team
date: 2026-07-25
---

# Red Team: Project Topology and Context Tools Plan

## Summary

**Verdict: GO with the documented gates.**

The plan is broad but decomposed along real repository boundaries. The most
serious failure modes are duplicate module identities, pretending executable
build files are fully resolved, unsafe incremental cleanup, and provider-specific
aggregate queries. The draft already addresses these risks; implementation must
not weaken the stated confidence, ownership, and provider gates.

## Findings

| Severity | Challenge | Failure mode | Required mitigation in plan |
| --- | --- | --- | --- |
| High | Existing `GradleModule` vs new `ProjectModule` | Duplicate nodes and conflicting dependencies make summaries unreliable | Phase 01 compatibility map and Phase 02 one-node/additive-label rule must be completed before writer code |
| High | Gradle/Make/CMake are executable/dynamic languages | Regex/static parsing is presented as build truth | Every parser returns evidence, confidence, and unresolved diagnostics; no build command execution; dynamic constructs remain unresolved |
| High | Topology overlay owns facts that connect to canonical/framework nodes | Descriptor deletion accidentally removes language or MyBatis/Spring facts | Separate ownership, relationship-specific cleanup, prior topology state, and recording-driver delete tests in Phases 02 and 06 |
| High | Unified MCP direct queries touch active provider migration | FalkorDB works while Neo4j silently diverges, or vice versa | Keep live parity blocked by `neo4j-to-falkordb-migration`; use provider-neutral driver results and recording tests first |
| High | Public API meaning differs by language | Tool returns private/internal or merely top-level symbols as public | Extract normalized visibility at parse time; strict default; C/C++ header heuristic opt-in; return evidence/confidence |
| Medium | Endpoint labels overlap across frameworks | Duplicate endpoint rows and inaccurate counts | Normalize while retaining original node identity; deduplicate by node/protocol key; fixture mixed-label cases |
| Medium | Architecture summary can become an unbounded mega-query | High latency, memory pressure, oversized MCP responses | Counts plus bounded samples by default, validated limits, deterministic pagination, no N+1 symbol queries |
| Medium | Android manifest merging/flavors | Source manifests are misrepresented as final merged application state | Retain source-set/provenance; derive effective values only with sufficient target/plugin evidence; emit ambiguity diagnostics |
| Medium | XML/config content can contain secrets or hostile structure | Credential leakage, entity access, parser resource exhaustion | Redact values, whitelist summaries, prevent external entity access, and enforce size/depth/node-count/path limits |
| Medium | Parent descriptor changes affect child modules | Incremental results differ from full scan | Dependency-aware invalidation for settings/POM/projectDir/module changes and full-vs-incremental equivalence tests |
| Low | Detection-only manifests are advertised as deep support | Capability catalog becomes misleading | Report handler depth and require fixture-backed capability before dependency/topology claims |

## Adversarial Scenarios

1. A Gradle Kotlin DSL computes module includes from an environment variable.
   Expected: root descriptor fact plus unresolved include diagnostic; no invented
   module.
2. A module contains both `pom.xml` and `build.gradle.kts`.
   Expected: one module, both descriptors, merged technology evidence, conflict
   diagnostic only if identities disagree.
3. Deleting `settings.gradle` leaves child source directories.
   Expected: recompute affected subtree, retain modules supported by other
   descriptors, tombstone only unsupported topology facts.
4. Java and Kotlin Android analyzers both see the same manifest/resources.
   Expected: stable IDs and idempotent writes, not duplicate nodes.
5. A Kotlin `internal` class is called by another module.
   Expected: it may be an entry point in call analysis but is excluded from
   strict public API inventory.
6. A C++ header under `include/` lacks export/linkage evidence.
   Expected: excluded by default; returned as inferred only on explicit request.
7. One endpoint has `ApiEndpoint`, `HttpEndpoint`, and framework labels.
   Expected: one normalized result retaining original labels/evidence.
8. A strings XML includes an API-key-like value.
   Expected: resource identity/type is stored; sensitive raw value is redacted.
9. A project has 50,000 public symbols.
   Expected: bounded page, stable continuation metadata, accurate total or
   explicit count semantics.
10. Provider schema lacks `ProjectModule`.
    Expected: `capability_unavailable`, not an empty module list.

## Recommendations

- Treat the Phase 01 compatibility map as a hard gate.
- Keep descriptor parser functions pure and independently fuzzable.
- Prefer recording-driver tests for writes/queries before live services.
- Add one full-vs-incremental final-graph equivalence test for the mixed fixture.
- Do not mark the plan complete while Neo4j parity is merely assumed.

## Expansion Review: All Registered Analyzers and Overlays

The later expansion to 22 primary analyzers, 12 overlays, and two additional MCP
tools remains **GO with gates**:

| Severity | Challenge | Required mitigation |
| --- | --- | --- |
| High | Scope expands into dozens of project formats | Machine-readable coverage registry; staged parse depth; unsupported entries remain explicit |
| High | A filename is mistaken for proof of a framework | Require content/module evidence and confidence; filename-only matches never imply semantic support |
| High | `.env`, connection, signing, and deployment files expose secrets | Key/schema metadata only, centralized redaction, negative leak tests |
| High | Registry/MCP/docs drift across 34 analyzers/overlays | Phase 10 executable acceptance matrix and consistency tests |
| Medium | Binary/complex formats such as Office VBA, Xcode, MSBuild, and vendor COBOL explode scope | Start with safe metadata adapters and diagnostics; do not execute native tooling |
| Medium | Tooling files overwhelm architecture summaries | Role-based filtering; tooling context excluded from default summaries unless architecture-relevant |
| Medium | Generated lock/resolution files override canonical descriptors | Explicit canonical/generated precedence and provenance |

The matrix correctly distinguishes universally required descriptors from
high-value conventional files. Implementation must preserve that distinction in
MCP responses and documentation.

## Unresolved Questions

- Whether compiled ABI reports are desired remains outside this plan.
- Deep support for detection-only ecosystems should be separate follow-up plans.
- Live Neo4j acceptance timing depends on the active migration plan.
