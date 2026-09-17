# Phase 01: Add proc_analyzer.py module, remove proc_sql.py

## Context

The patch adds a self-contained 884-line `proc_analyzer.py` module. It is almost entirely new code with no external code-tiny conflicts. The old `proc_sql.py` (46 lines) becomes dead code once Phase 02 rewires `cplus_analyzer.py`.

## Requirements

- Copy `proc_analyzer.py` content from patch into `code-tiny/tools/cplus/proc_analyzer.py` verbatim.
- Verify the lazy import `from tools.sql.sql_analyzer import _get_sql_parser` resolves at runtime.
- Delete `code-tiny/tools/cplus/proc_sql.py`.
- Verify no remaining import of `proc_sql` anywhere in `code-tiny/`.

## Implementation steps

1. Create `code-tiny/tools/cplus/proc_analyzer.py` with the full 884-line content from the patch's `tools/cplus/proc_analyzer.py` hunk.
2. Run `python -m py_compile code-tiny/tools/cplus/proc_analyzer.py` to verify syntax.
3. Delete `code-tiny/tools/cplus/proc_sql.py`.
4. `grep -r "proc_sql" code-tiny/` to find all remaining references (will be in `cplus_analyzer.py` — handled in Phase 02, and in tests — handled in Phase 04).

## Key module API (from patch)

```
PROC_EXTENSIONS = (".pc",)
prepare_proc_bytes(raw: bytes) -> PreparedProcSource
prepare_proc_path(path: str | Path) -> PreparedProcSource
analyze_proc_file(path, *, file_path, project_id="", functions=()) -> dict
summarize_proc_root(root, *, max_files=0) -> dict
```

## Data model

```
PreparedProcSource(source_bytes, masked_bytes, encoding, regions, diagnostics)
ProcRegion(start_byte, end_byte, start_line, end_line, code, sql, operation, ...)
ProcDiagnostic(code, message, start_line)
```

## Success criteria

- `python -m py_compile code-tiny/tools/cplus/proc_analyzer.py` passes.
- `prepare_proc_bytes()` unit-tested inline (smoke test from patch test file).
- `proc_sql.py` removed from disk.
