# Phase 02: Rewrite cplus_analyzer.py integration

## Context

code-tiny's `cplus_analyzer.py` (5110 lines) currently integrates the basic `proc_sql.py` at 8 code locations. The patch's diffs were written against a pre-Pro*C file, so they cannot apply directly. This phase manually replaces all basic integration points with the comprehensive `proc_analyzer.py` API.

## Current integration points to replace

| Line | Current (basic) | Target (comprehensive) |
| --- | --- | --- |
| 54 | `from tools.cplus.proc_sql import extract_exec_sql_statements` | `from tools.cplus.proc_analyzer import analyze_proc_file, prepare_proc_path` |
| 81 | `_PARSE_CACHE_VERSION = "cplus-v2026-07-31-proc1"` | `"cplus-v2026-08-04-proc1"` |
| ~608 | `_parse_file`: inline EXEC SQL masking | Call `prepare_proc_path(path)` → `parser.parse(prepared.masked_bytes)`, return `prepared.source_bytes` |
| ~2745 | `payload["proc_sql_statements"] = []` | `payload["proc_nodes"] = []` + `payload["proc_diagnostics"] = []` |
| ~2889-2939 | Build `CplusSqlStatement` rows + `DEFINES` edges | Call `analyze_proc_file(path, file_path=..., project_id=..., functions=[...])` → merge nodes + relations |
| ~3488 | `buf_proc_sql_statements` buffer | `buf_proc_nodes: Dict[str, List[dict]]` keyed by label |
| ~3580 | `_PROC_SQL_CYPHER` (single MERGE) | `_PROC_NODE_CYPHER` per-label MERGE dict |
| ~3602-3617 | Flush `buf_proc_sql_statements` | Flush `buf_proc_nodes[label]` per label |
| ~3656-3665 | `allowed_rel_types` includes `"DEFINES"` | Replace with 9 Pro*C relationship types, remove `DEFINES` |
| ~4116-4123 | Write `proc_sql_statements` rows | Write `proc_nodes` rows from payload |

## Additional changes from patch

- `_parse_file` signature gains `.pc` branch returning `(tree, source_bytes)`.
- `_is_cpp_file`: `.pc` defaults to C, compile_db_index can override to C++.
- `_scan_c_family_files`: `.pc` already present — **no change needed**.
- `_load_or_parse_payload`: add `project_id` parameter to cache signature; skip clang fallback for `.pc`; call `normalize_cached_payload`.
- `_collect_include_graph`: `.pc` uses `prepare_proc_path(path).source_bytes.decode("utf-8")`.
- `build_call_graph`: `buf_proc_nodes` dict; `_PROC_NODE_LABELS` set; `_PROC_NODE_CYPHER` dict; Qdrant points include proc nodes.
- Relation properties: `setdefault("project_id", project_id)`.
- Log messages: "C/C++/Pro*C/resource files".

## Implementation approach

**Do NOT `git apply`** — the patch hunks will fail on every changed region. Instead, manually edit each location using the patch as a semantic reference.

1. Change import (line 54).
2. Bump cache version (line 81).
3. Rewrite `_parse_file` `.pc` branch.
4. Rewrite `parse_c_family_file` `.pc` proc analysis section.
5. Rewrite `_load_or_parse_payload` cache + payload normalization.
6. Rewrite `build_call_graph` buffers, flush, allowed_rel_types, Qdrant points.
7. Update `_collect_include_graph` `.pc` handling.
8. Update `_is_cpp_file` `.pc` branch.

## Success criteria

- `python -m py_compile code-tiny/tools/cplus/cplus_analyzer.py` passes.
- No remaining reference to `proc_sql`, `CplusSqlStatement`, `DEFINES`, or `buf_proc_sql_statements`.
- `grep -c "proc_analyzer\|proc_nodes\|SqlStatement\|DECLARES_STATEMENT" code-tiny/tools/cplus/cplus_analyzer.py` returns > 10.
