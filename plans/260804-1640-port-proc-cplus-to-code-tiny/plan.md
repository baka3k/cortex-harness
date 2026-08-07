---
title: "Port comprehensive Pro*C analyzer to code-tiny (replace basic proc_sql)"
status: in_progress
created: 2026-08-04
target: code-tiny/tools/cplus
blockedBy: []
blocks:
  - "260807-1329-parser-quality-recovery"
phaseBlockedBy:
  "03": [260807-1202-graph-ingest-write-path-hardening]
  "04": [260807-1202-graph-ingest-write-path-hardening]
relatedPlans:
  - "260731-1500-legacy-migration-parser-coverage"
  - "260804-1426-proc-cplus-analyzer"
  - "260807-1202-graph-ingest-write-path-hardening"
  - "260807-1329-parser-quality-recovery"
---

# Port comprehensive Pro*C analyzer to code-tiny (replace basic proc_sql)

## Overview

A production-grade Pro*C analyzer patch (`proc_analyzer.py`, 884 lines) was generated against code-tiny's pre-Pro*C state. Since then, commit `65facb8` (Jul 31) landed a **basic** Pro*C implementation (`proc_sql.py`, 46 lines, regex-based). This plan ports the comprehensive patch to code-tiny's **current** state by **replacing** the basic implementation.

The patch was generated from inside `code-tiny/` (paths like `tools/cplus/...`), not from repo root. All target files live under `code-tiny/`.

## What exists vs. what the patch adds

| Aspect | code-tiny NOW (basic, 65facb8) | Patch (comprehensive) |
| --- | --- | --- |
| Module | `proc_sql.py` (46 lines, single regex) | `proc_analyzer.py` (884 lines, lexical state machine) |
| Scanner | `\bEXEC\s+SQL\b.*?;` regex | Stateful lexer: comments, strings, char literals, raw strings, PL/SQL blocks |
| Encoding | None (assumes UTF-8) | UTF-8 → CP932 fallback → replacement diagnostic |
| Masking | Byte-range mask in `_parse_file` | Length-preserving mask with newline alignment assertions |
| Labels | `CplusSqlStatement` (1 label) | `SqlStatement`, `SqlDirective`, `SqlCursor`, `SqlHostVariable`, `DatabaseTable` (5 labels) |
| Relationships | `DEFINES` (1 type) | `DECLARES_STATEMENT`, `DECLARES_DIRECTIVE`, `BINDS_PARAMETER`, `DECLARES_CURSOR`, `REFERENCES_CURSOR`, `REFERENCES_STATEMENT`, `READS_FROM`, `WRITES_TO`, `REFERENCES_TABLE` (9 types) |
| SQL semantics | Operation + targets + host vars (regex) | SQL grammar parser integration, cursor lifecycle, dynamic SQL, indicator variables, CTE filtering |
| MCP aliases | None added | `proc`, `pro*c`, `pro-c` → canonicalize to `cplus` |
| DB constraints | None for Pro*C labels | Uniqueness + index per label |
| Cache version | `cplus-v2026-07-31-proc1` | `cplus-v2026-08-04-proc1` |

## Dependencies verified

- `code-tiny/tools/sql/sql_analyzer.py` — exists, exports `_get_sql_parser()` ✓
- `code-tiny/tools/common/incremental_cleanup.py` — exists ✓
- `code-tiny/tools/cplus/clang_parser.py`, `rc_parser.py` — exist ✓

## Active-plan coordination

- `260807-1202-graph-ingest-write-path-hardening` owns the canonical schema
  manifest, pre-stream index readiness, label-qualified relationship writes,
  integrity accounting, and graph-run recovery. This plan continues to own
  Pro*C extraction semantics, labels, relationships, aliases, and parser tests.
- Phase 03 must register Pro*C labels/identities in the shared manifest rather
  than adding another analyzer-local or standalone constraint list. Phase 04's
  graph acceptance must use the hardening plan's readiness, explain-plan,
  integrity, and idempotency gates.
- Phases 01-02 parser implementation can proceed independently. The phase
  blockers prevent graph integration from declaring completion on the current
  post-stream index path.

## Phases

| Phase | Outcome | Depends on |
| --- | --- | --- |
| [01](phase-01.md) | Add `proc_analyzer.py`, remove `proc_sql.py` | Existing SQL runtime |
| [02](phase-02.md) | Rewrite `cplus_analyzer.py` integration | 01 |
| [03](phase-03.md) | Update MCP, constraints, driver, routing | 02 |
| [04](phase-04.md) | Port tests, update docs, verify | 01-03 |

## Key risks

| Risk | Mitigation |
| --- | --- |
| Existing graphs have `CplusSqlStatement`/`DEFINES` nodes | Old nodes cleaned by generic file-scoped cleanup; no migration script needed if re-scan is acceptable |
| `proc_analyzer.py` imports `tools.sql.sql_analyzer` at runtime | Lazy import inside `_sql_parse_status()` with fallback diagnostic — already handled |
| code-tiny `framework_registry.py` has different structure than patch assumes | Adapt to `CAPABILITIES` dict + `_generic_profile()` pattern, not `GENERIC_PRIMARY_ALIASES` |
| Test file references root-level test infrastructure | Port to `tests/cplus/` under code-tiny or root `tests/` |

## Explicit non-goals

- Applying the patch to the root project (root has no `tools/` directory)
- Keeping `proc_sql.py` for backward compatibility (full replacement)
- Graph migration from `CplusSqlStatement` → `SqlStatement` (re-scan instead)
