# Phase 02: Pro*C (.pc) EXEC SQL Coverage

## Context

Oracle Pro*C files (`.pc`, sometimes `.pcc`) are C source with embedded `EXEC SQL ...;` precompiler directives (`EXEC SQL CONNECT`, `EXEC SQL COMMIT`, `EXEC SQL WHENEVER ... GOTO ...`, host-variable binds via `:varname`). `cplus_analyzer._scan_c_family_files` does not match `.pc` today, so these files are invisible to ingestion. COBOL already solved the identical extraction problem (`cobol/parser.py:191-195` + `cobol/semantics.py:231-244`); this phase mirrors that shape inside `cplus_analyzer` rather than inventing a new approach.

## Requirements

- Add `.pc` (and `.pcc`) to `_scan_c_family_files` (`code-tiny/tools/cplus/cplus_analyzer.py:2489-2499`).
- Route all source reads for `.pc` files (and ideally all cplus reads, to fix latent Shift-JIS mojibake risk) through `read_legacy_text` from Phase 01.
- Extract `EXEC SQL <OP> ...;` statements: capture `operation` (`CONNECT`, `COMMIT`, `ROLLBACK`, `SELECT`, `UPDATE`, `INSERT`, `DELETE`, `WHENEVER`, etc. via `re.search(r"EXEC\s+SQL\s+([A-Z]+)", upper)` same as COBOL), `targets` (table names after `FROM|INTO|UPDATE|JOIN|TABLE`), and `host_variables` (`:name` tokens).
- Emit a `CplusSqlStatement` node per statement and a `DEFINES` edge from the enclosing C function (same relation name COBOL uses from paragraph → statement, for consistency across the graph schema) to the statement node.
- `EXEC SQL INCLUDE sqlca;` / `sqlda`/`sqlcpr` style includes should still flow through the existing `#include` handling already present for `.c`/`.h` files — don't special-case them beyond making sure the tree-sitter C parse tolerates the `EXEC SQL` lines (Pro*C is not valid C grammar as-is; if the existing tree-sitter C grammar errors on `EXEC SQL` lines, strip/mask them before the AST pass the same way error-recovery is already handled for `.h` files at `cplus_analyzer.py:1986-1987`, then run the regex-based EXEC SQL extractor over the original un-masked text for the statement facts).
- Register `.pc`/`.pcc` under the existing `cplus` parser key across the 7-file checklist (this is an extension addition to an existing parser, not a new parser name):
  - `code-tiny/tools/sync/incremental_sync.py`: `_SOURCE_EXTENSIONS`, `_select_parser_for_path`.
  - `code-tiny/tools/sync/owner_manifest.py`: extension→parser detection function (cplus branch).
  - `cortex_harness/dev.py`: `LANG_EXTENSIONS["cplus"]`.
  - `code-tiny/mcp/framework_registry.py`: confirm `cplus` CAPABILITIES entry already covers the new node label (`CplusSqlStatement`) or add it to the searchable-label set.
  - `code-tiny/tools/project_topology/registry.py`: no change expected (cplus CoverageEntry is build-file based, not extension based) — verify and note if untouched.
  - `tests/test_mcp_acceptance_matrix.py`, `tests/test_common_analyzer_registry.py`: no new primary key needed since `cplus` already exists — add a regression assertion instead (see tests below).

## Architecture

Extraction lives in a new small module `code-tiny/tools/cplus/proc_sql.py` (mirrors `cobol/parser.py`'s EXEC SQL branch) with:

```python
def extract_exec_sql_statements(source_text: str) -> List[ProcSqlStatement]: ...
```

`cplus_analyzer.py` calls this once per `.pc`/`.pcc` file after (or in place of) the normal AST pass, and `semantics`-equivalent code (wherever `cplus_analyzer.py` currently builds `functions`/`calls` rows) adds a `CplusSqlStatement` row plus a `DEFINES` relation row keyed to the enclosing function's id.

## Related Files

Create:
- `code-tiny/tools/cplus/proc_sql.py`
- `tests/fixtures/procc-application/BZZAAB02.pc` (small synthetic Pro*C fixture with `EXEC SQL CONNECT`/`COMMIT`/`SELECT`, CP932-encoded)
- `tests/test_cplus_proc_sql.py`

Modify:
- `code-tiny/tools/cplus/cplus_analyzer.py` (`_scan_c_family_files`, file-read call sites, graph-row building for the new statement/edge)
- `code-tiny/tools/sync/incremental_sync.py`
- `code-tiny/tools/sync/owner_manifest.py`
- `cortex_harness/dev.py`
- `code-tiny/mcp/framework_registry.py` (verify/extend only if needed)
- `tests/test_mcp_acceptance_matrix.py` (add `.pc` extension assertion under the existing `cplus` row)
- `tests/test_common_analyzer_registry.py` (add `.pc` to whatever extension-coverage assertion exists for `cplus`)

Reference:
- `code-tiny/tools/cobol/parser.py:191-195`
- `code-tiny/tools/cobol/semantics.py:231-244`
- `code-tiny/tools/cplus/cplus_analyzer.py:1986-1987` (existing error-node tolerance precedent for `.h`)
