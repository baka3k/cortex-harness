---
title: "Tier 3 Extraction Layer — Delphi, PL/SQL"
status: pending
created: 2026-07-31
mode: hi-plan --full
parent: 260731-1700-multi-language-rust-extraction
parentPhase: 4
scope: Port the two regex/hybrid analyzers to Rust
priority: 4-5 (Delphi: 68.8% parse+extract; PLSQL: no corpus, deferred)
blockedBy: []
---

# Tier 3 Extraction Layer — Delphi, PL/SQL

## Overview

Port the two regex-dependent analyzers. These are the hardest ports because they involve **regex engines** (not just tree-sitter AST walks).

- **Delphi:** tree-sitter **+ regex fallback** — dual code path
- **PL/SQL:** **pure regex, NO tree-sitter** — entirely pattern-based

## Per-Language Survey

### Delphi (`delphi_analyzer.py`) — 2769 lines

**⚠️ CRITICAL FINDING:** tree-sitter is NOT used for extraction. Even on the `delphi_tree_sitter` path, tree-sitter is used ONLY to find `interface`/`implementation` section line ranges. **Extraction is ALWAYS regex-based.** The `range_guided_by_tree` flag in `parse_meta` controls which line ranges the regex extractors scan.

**Dual-path architecture:**
```
_get_delphi_parser() returns parser → delphi_tree_sitter path (line-range guided regex)
_get_delphi_parser() returns None  → regex_fallback path (regex on full file)
Both paths use the SAME regex extractors — only line ranges differ.
```

**Returns 9-tuple:**
`(functions, calls, types, [namespace_def], fields, relations, file_def, uses_units, parse_meta)`

**Dataclasses (DIFFERENT from SQL/plsql):**
`FunctionDef` (no `exported`), `FileDef` (no `imports`/`exports`), `NamespaceDef`, `TypeDef` (with `kind`), `FieldDef` (unique: scope_name, type_signature), `RelationEdge` (`Dict[str,Any]`), `CallEdge` (has `caller_file`, `call_arity`, `callee_raw` — no `callee_qualified`/`callee_simple`)

**Regex patterns (the real extraction engine):**
- `_strip_comments_and_strings`: `'([^']|'')*'|\{[^}]*\}|\(\*.*?\*\)|//.*?$` (length-preserving mask)
- `_find_matching_end_block`: `\b(begin|end)\b` depth counter
- `_extract_unit_name`: `unit NAME;` / `program NAME;` / `library NAME;`
- `_extract_uses_units`: `(?is)\buses\b\s*([^;]+);`
- `_find_type_declaration_end`: `(?is)=\s*(?:packed\s+)?(class|record|interface)\b|\bend\s*;`
- `type_decl_re`: `(?im)^\s*(NAME)\s*=\s*(class|record|interface)\b(?:\s*\(([^)]*)\))?`
- `method_re`: `(?im)^\s*(?:(class)\s+)?(procedure|function|constructor|destructor)\s+(NAME)\s*(\([^;\n]*\))?(?::\s*([^;\n]+))?;`
- `field_re`: `(?im)^\s*(NAMES)\s*:\s*([^;=\n]+);`
- `signature_re`: same as method_re but for top-level routines
- `call_re`: `\b(NAME)\s*\(` (inside method bodies)
- `_register_type_usage`: `:\s*([^;\)\n=]+)` → type extraction
- `_normalize_type_name`: strips `<>`, `const/var/out/array of/class of/packed/reference to/specialize/generic`, `^`

**Unique extraction:**
- Type/field extraction with base-type inheritance (`(ParentClass)`)
- `USES_TYPE` + `POINTER_TO` (`^` detection) relations
- Routine body detection: pairs `begin`/`end` via depth counter; forward declarations vs full bodies
- `_resolve_calls` scoring resolver (file match +15, uses-closure +7, same scope +10)
- Delphi params are `;`-separated (not commas)

### SQL (`sql_analyzer.py`) — 2363 lines

**⚠️ CRITICAL FINDING:** tree-sitter code (`_walk_tree`, `_CLASS_NODE_TYPES`, `_FUNCTION_NODE_KINDS`) is **DEAD CODE — never executed.** `parse_sql_file` uses **pure regex** for everything. The tree-sitter scaffolding can be dropped from the port.

**Returns 6-tuple → payload dict:**
`functions, calls, classes, namespaces, relations, file_def`

**Dataclasses:**
`FunctionDef` (has `exported`), `FileDef` (has `imports`/`exports`), `NamespaceDef`, `ClassDef` (has `exported`), `RelationEdge` (`Dict[str,str]`), `CallEdge` (`caller_id, caller_scope, callee_name, callee_id, callee_arity, callee_raw, callee_qualified, callee_simple, call_line`)

**Regex patterns (ALL active):**
- `_SQL_IDENTIFIER = r"[A-Za-z_][\w$#]*"`, `_SQL_QUALIFIED_IDENTIFIER = r"(?:IDENT\.)*IDENT"`
- `_SQL_CREATE_RE`: `create\s+(?:or\s+replace\s+)?(?P<kind>procedure|proc|function)\s+(?P<name>QUAL_ID)`
- `_SQL_CALL_RE`: `call\s+(?P<name>QUAL_ID)`
- `_SQL_EXEC_RE`: `exec(?:ute)?\s+(?P<name>QUAL_ID)`
- `_SQL_GENERIC_CALL_RE`: `(?P<name>QUAL_ID)\s*\(`
- `_SQL_BARE_CALL_RE`: `^(?P<name>QUAL_ID)\s*;\s*$` (MULTILINE)
- `_SQL_BODY_START_RE`: `(as|is|begin)`
- `_mask_sql_comments`: `/* */` (DOTALL), `--`, `//`
- `_find_routine_end`: `\bend\b\s*(?P<label>...)?\s*;`
- Arity: hand-written paren/string/depth-aware comma counter (`_count_params_segment`)

**Sets:** `_SQL_CALL_KEYWORDS` (~44 keywords), `_SQL_TYPE_KEYWORDS` (~26 types), `_SQL_BUILTIN_PREFIXES` (`pg_catalog., information_schema., sys., dbms_, utl_`)

### PL/SQL (`plsql_analyzer.py`) — 2634 lines — pure regex, NO tree-sitter

**Returns 6-tuple** (identical structure to SQL): `functions, calls, classes, namespaces, relations, file_def`

**Dataclasses:** Mirror of SQL set.

**ALL `_PLSQL_*_RE` regex patterns:**
| Pattern | Matches |
|---|---|
| `_PLSQL_CREATE_RE` | CREATE [OR REPLACE] PROCEDURE/FUNCTION |
| `_PLSQL_PACKAGE_BODY_RE` | CREATE PACKAGE BODY (→ Namespace) |
| `_PLSQL_PACKAGE_RE` | CREATE PACKAGE (spec) |
| `_PLSQL_TRIGGER_RE` | CREATE TRIGGER |
| `_PLSQL_PROC_RE` | `procedure NAME` (nested inside packages) |
| `_PLSQL_FUNC_RE` | `function NAME` (nested inside packages) |
| `_PLSQL_CALL_RE` | `CALL proc` |
| `_PLSQL_EXEC_RE` | `EXEC[UTE] proc` |
| `_PLSQL_GENERIC_CALL_RE` | `name(` calls |
| `_PLSQL_BARE_CALL_RE` | `name;` (no-paren calls) |
| `_PLSQL_BODY_START_RE` | `(as|is|begin)` |
| `_PLSQL_JOB_CALL_RE` | `dbms_scheduler.create_job(` |
| `_PLSQL_JOB_NAME_ARG_RE` | `job_name => '...'` |
| `_PLSQL_JOB_ACTION_ARG_RE` | `job_action => '...'` |

**Unique:** Package handling (→ NamespaceDef), triggers (kind="trigger"), DBMS_SCHEDULER job blocks (recursive call extraction), dedup helpers.

## Phases

**⚠️ REVISED:** All three languages are regex-based (not tree-sitter). Delphi uses tree-sitter ONLY for line-range detection. SQL/PLSQL are pure regex. This plan is a **regex engine port**, not an AST walker port.

### Phase D — Delphi regex extraction (with optional tree-sitter line ranges)

**Goal:** Port Delphi's regex extraction engine. Optionally use tree-sitter for section line-range detection.

**Deliverables:**
1. Add `tree-sitter-delphi`/`tree-sitter-pascal` to Cargo.toml IF available (only for line ranges)
2. Write `delphi.rs`:
   - Port `_strip_comments_and_strings` (length-preserving comment mask)
   - Port all regex patterns: `type_decl_re`, `method_re`, `field_re`, `signature_re`, `call_re`
   - Port `_find_matching_end_block` (begin/end depth counter)
   - Port `_resolve_calls` scoring resolver
   - Port `_normalize_type_name`, `_register_type_usage`
   - `DelphiParseOutput` with `uses_units` field
3. `build_delphi_payload`
4. Wire `lib.rs`
5. Optional: if tree-sitter-delphi available, port `_extract_section_line_ranges_from_tree`

**Validation:** Differential test on BLM master corpus (9 files).

### Phase S — SQL regex extraction

**Goal:** Port SQL's pure-regex extraction engine.

**Deliverables:**
1. Write `sql_lang.rs`:
   - Port `_mask_sql_comments` (comment masking)
   - Port all regex patterns: `_SQL_CREATE_RE`, `_SQL_CALL_RE`, `_SQL_EXEC_RE`, etc.
   - Port `_count_params_segment` (paren/depth-aware comma counter)
   - Port `_find_routine_end`
   - `SqlParseOutput` with `classes` (not `types`)
2. `build_sql_payload`
3. Wire `lib.rs`
4. Fixtures + differential test

**⚠️ Note:** Drop dead tree-sitter code from the port.

### Phase P — PL/SQL regex extraction (DEFERRED)

**⚠️ DEFERRED — no corpus, unconfirmed ROI.**

**If unblocked:**
1. Port all `_PLSQL_*_RE` patterns
2. Port package handling, trigger extraction, DBMS_SCHEDULER job blocks
3. `build_plsql_payload`
4. Requires real PL/SQL corpus for differential testing

## Validation Criteria

Delphi:
- [ ] `cargo test --release delphi::` passes (both paths)
- [ ] Differential test: Rust == Python on BLM master corpus
- [ ] Regex fallback produces same output as tree-sitter path
- [ ] `uses_units` field matches (not `using_namespaces`)

PL/SQL:
- [ ] DEFERRED until corpus available

## Risk Register

| Risk | Severity | Mitigation |
|------|----------|------------|
| `tree-sitter-delphi` crate unavailable or broken | **High** | Use regex fallback exclusively if tree-sitter grammar doesn't exist |
| Regex port — Rust regex syntax differs from Python | Medium | Test each pattern individually |
| PL/SQL deferred — may never be needed | Low | Monitor for corpus appearance |

## Estimated Effort

- **Delphi (both paths):** High (~800 lines — dual code path, regex patterns)
- **PL/SQL:** Deferred (estimated ~500 lines if ported — pure regex)
