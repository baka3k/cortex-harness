---
type: validation
date: 2026-07-25
---

# Validation: Project Topology and Context Tools Plan

## Summary

**Result: PASS for implementation planning, including the all-parser/framework
expansion.**

The plan covers every requested capability, maps it to confirmed repository
extension points, states compatibility and security boundaries, defines six
ordered phases, and includes measurable acceptance criteria. It is ready for
implementation after approval, with provider parity explicitly gated by the
active Neo4j-to-FalkorDB migration.

The additive expansion defines four further phases, a complete special-file
matrix for 22 primary analyzers and 12 overlays, and two additional MCP tools.
The plan now contains ten ordered phases and six aggregate context tools.

## Critical Questions

### 1. Does the plan extend existing behavior instead of rebuilding it?

Yes. It reuses:

- Android manifest/resource/Gradle facts;
- framework overlays and existing MyBatis semantic parsing;
- normalized incremental manifests;
- provider-neutral graph drivers and recording-writer conventions;
- Unified MCP capability profiles, schema inspection, and shared metadata.

### 2. Is module topology clearly different from Git repository topology?

Yes. Existing incremental topology models scan/repository scopes. The new
`ProjectModule` contract models semantic build modules within those normalized
scopes and reuses, rather than replaces, their path behavior.

### 3. Are ambiguous requirements resolved?

Yes:

- public API means source-level public/exported declarations;
- C/C++ inference is opt-in;
- gRPC first-release coverage is static protobuf service/RPC extraction;
- additional ecosystem manifests are detection-only unless fixture-backed;
- architecture summaries aggregate indexed graph state and do not rescan disk;
- build files are statically analyzed and never executed.

### 4. Are the four MCP tools specified enough to implement and test?

Yes. Inputs, filters, scoping, pagination, result fields, capability behavior,
and bounded summary behavior are defined in `plan.md` and Phase 05.

### 5. Are graph compatibility and ownership safe?

Conditionally yes. The plan requires:

- compatibility mapping before writes;
- one canonical module identity with additive labels;
- topology-owned cleanup only;
- project-scoped stable IDs;
- recording-driver exact-row/edge tests;
- live provider parity before completion.

These are implementation gates, not optional recommendations.

### 6. Is the parser scope feasible?

Yes, because it is staged and honest about static-analysis limits. Requested
families have explicit extraction depth. Dynamic Gradle/Make/CMake/Maven
constructs become unresolved diagnostics rather than speculative facts.

### 7. Are acceptance criteria explicit?

Yes. The plan requires deterministic mixed-fixture topology, Android semantic
coverage, language-correct public APIs, endpoint/gRPC normalization, four
discoverable MCP tools, incremental equivalence, scoped deletion, provider
evidence, security bounds, and regression coverage.

### 8. Does the expanded matrix cover the live registry rather than only the
user-provided subset?

Yes. It includes all 22 primaries and all 12 overlays confirmed by
`tests/test_common_analyzer_registry.py`, including FastAPI/Django, Express,
Laravel, database SQL, and database PL/SQL.

### 9. Does “special file” overstate mandatory files?

No. The matrix classifies identity/topology/dependency/configuration/framework/
interface/resource/deployment/generated/secret-bearing roles and explicitly
states that languages such as C/C++, Python, COBOL, and SQL have no universal
project manifest.

### 10. Are the additional MCP queries bounded and safe?

Yes. `get_project_special_files` and `get_framework_context` inherit mandatory
scope, pagination, limits, capability diagnostics, provenance, freshness, and
redaction requirements.

## Plan Integrity Checks

- One H1 per plan, phase, and research document: passed.
- All phase links from `plan.md` resolve: passed.
- `git diff --check` for plan artifacts and dependency update: passed.
- Existing `docs/development-rules.md`: absent; root `AGENTS.md`, repository
  memories, existing plan conventions, and the requested planning skill were
  used instead.
- User-owned dirty files were observed and not modified:
  `.cortext-harness/config/dev.json`, `.gitignore`, and `.cortex/`.

## Required Pre-Implementation Gates

1. Approve the Phase 01 graph compatibility and public API contract.
2. Confirm the active provider migration interface before schema/MCP live work.
3. Establish baseline focused regression commands and environment exclusions.
4. Keep deep support claims aligned with fixture-backed handlers.

## Recommendations

- Start with Phase 01; do not parallelize writer and public tool work before the
  identity/ownership contract is frozen.
- Phases 02, 03, and 04 may then proceed in parallel only where shared models and
  stable IDs are already finalized.
- Run the red-team adversarial scenarios as acceptance cases, not merely review
  notes.

## Unresolved Questions

No question blocks plan creation. Compiled ABI analysis and deep parsing for
additional ecosystems remain deliberate follow-ups.
