# Phase 1: Pro*C (`.pc`) Support in `cplus_analyzer`

## Goal

Make `.pc` files (Oracle Pro*C precompiler sources) discoverable and
parseable by the existing C++ pipeline without breaking the tree-sitter/clang
walk on `EXEC SQL` / `EXEC ORACLE` embedded directives, and decode Shift-JIS
(CP932) comments correctly.

## Why not a separate tool

Pro*C is C with embedded precompiler directives — after preprocessing those
directives out, the rest is ordinary C. Reusing `cplus_analyzer.py` avoids
duplicating the entire C symbol/call/type extraction logic (confirmed via
user decision: extend existing analyzer).

## Steps

### 1.1 — Extension registration (discovery)

Add `.pc` everywhere `.c/.cpp/...` is currently checked:

- `code-tiny/tools/cplus/cplus_analyzer.py`
  - directory-walk filter (`name.lower().endswith((".c", ".h", ...))`, ~L2495)
  - `_is_cpp_file()` (~L2502) — treat `.pc` like `.c` (C-family, not C++-family)
  - anywhere `.c` triggers "has_cpp_sibling"/companion-header heuristics
- `code-tiny/tools/common/message_scan.py::_PARSER_EXTENSIONS["cplus"]` — append `.pc`
- `code-tiny/mcp/framework_registry.py` — add `"pro*c"`, `"proc"` to the
  `cplus` alias set (`_generic_profile("cplus", {"cplus", "cpp", "c++", "c", "clang", ...})`)
  so query/routing by language name still resolves to the `cplus` backend.

### 1.2 — CP932/Shift-JIS decode fallback

Reuse the exact fallback chain already proven in
`code-tiny/tools/cplus/rc_parser.py` (BOM-based UTF-16 check, then
`utf-8` → `cp932`) as a shared helper (extract to `code-tiny/tools/common/`
if not already shared, e.g. `text_encoding.py::decode_source_bytes()`), and
call it from `cplus_analyzer.py`'s file-read path used for:
- comment/doc extraction (currently raw `decode("utf-8", errors="ignore")`)
- `_looks_like_cpp_header()` sniffing

Tree-sitter/clang byte-offset parsing itself is unaffected (operates on raw
bytes), so this only fixes **displayed/stored text** (comments, summaries),
not parse correctness.

### 1.3 — EXEC SQL / EXEC ORACLE preprocessing

Add `_preprocess_proc_directives(source_bytes) -> (patched_bytes, sql_statements)`
in `cplus_analyzer.py` (or a new `code-tiny/tools/cplus/proc_preprocessor.py`
imported by it), run **only when the file extension is `.pc`**, before handing
bytes to the parser:

- Match `EXEC SQL ... ;` and `EXEC ORACLE ... ;` (case-insensitive, statement
  ends at first unquoted `;`, may span multiple lines).
- Replace each match **in place, preserving byte length and newline count**
  (pad with spaces / keep embedded `\n`) with a syntactically valid C
  statement stand-in, e.g. `__exec_sql__("<kind>");` where `<kind>` is the
  first token after `EXEC SQL` (`CONNECT`, `COMMIT`, `ROLLBACK`, `WHENEVER`,
  `DECLARE`, `SELECT`, `UPDATE`, `INSERT`, `FETCH`, ...). Preserving byte
  offsets is required so existing line/column-based symbol locations stay
  correct for the rest of the file.
- Collect each raw statement (kind, raw text, line range, enclosing function
  name resolved after parse) into a list returned alongside the patched bytes.
- Host variables (`:varname`) referenced inside `EXEC SQL` bodies should be
  captured as a simple regex extraction (`r":([A-Za-z_][A-Za-z0-9_]*)"`) and
  attached to the statement record — do not attempt full Pro*C grammar
  parsing (explicitly out of scope, see plan.md Non-Goals).

### 1.4 — Graph representation

For each captured statement, emit (mirroring the `cobol` `EXEC SQL` handling
in `code-tiny/tools/cobol/parser.py` for consistency of shape, adapted to
cplus's payload schema):
- A lightweight `EmbeddedSqlStatement` record with `kind`, `raw_text`,
  `host_vars`, `enclosing_function_id`, `start_line`, `end_line`.
- A relation `EXEC_SQL` from the enclosing function symbol to the statement
  (or directly to a referenced table name when `kind` is `SELECT/INSERT/
  UPDATE/DELETE` and a table name can be regex-extracted — best-effort only).

### 1.5 — Fixtures & tests

- Add `tests/fixtures/*/BZZAAB02.pc`-style minimal fixture (2–3 functions,
  `EXEC SQL CONNECT`, `COMMIT`, `WHENEVER ... GOTO`) under the cplus test
  fixtures directory used by existing cplus analyzer tests.
- Assert: file is discovered, functions `Kora_logon`/`Kcommit` extracted with
  correct line numbers (proves byte-length-preserving preprocessing), and
  `EXEC_SQL` relations exist with expected `kind` values.

## Files Touched

- `code-tiny/tools/cplus/cplus_analyzer.py`
- `code-tiny/tools/cplus/proc_preprocessor.py` (new)
- `code-tiny/tools/common/text_encoding.py` (new, or extend existing shared util)
- `code-tiny/tools/common/message_scan.py`
- `code-tiny/mcp/framework_registry.py`
- test fixtures under `tests/fixtures/` (cplus)

## Validation

- Run the cplus analyzer against a `.pc` fixture and against the real sample
  (`BZZAAB02.pc`) in dry-run mode; confirm no parse exceptions, function count
  matches manual count, and `EXEC_SQL` relation count matches the number of
  `EXEC SQL`/`EXEC ORACLE` statements in the file.
- Confirm `.c` (`BZZAAB01.c`) still parses unaffected (regression check).
