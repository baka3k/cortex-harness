# Phase 04: Port tests, update docs, verify

## Context

The patch includes a 501-line test file (`test_proc_analyzer.py`) and documentation updates. code-tiny has a minimal test directory and no `tests/cplus/`. Existing tests reference the old `proc_sql` / `CplusSqlStatement` API.

## Test porting

1. Create `tests/cplus/` directory (at repo root, matching existing test convention).
2. Port `test_proc_analyzer.py` from patch to `tests/cplus/test_proc_analyzer.py`.
3. Verify test imports resolve:
   - `from tools.cplus import cplus_analyzer` — needs `code-tiny` on `sys.path`
   - `from tools.cplus import proc_analyzer`
   - `from tools.common.incremental_cleanup import cleanup_neo4j_for_files`
4. Update existing `tests/test_cplus_proc_sql.py` — either rename to reference `proc_analyzer` or delete if superseded by the comprehensive test file.
5. Update `tests/test_common_analyzer_registry.py` and `tests/test_unified_mcp_parser_routing.py` per patch (add `.pc` routing tests, `proc`/`pro*c` alias tests).

## Documentation updates

Per patch:
- `code-tiny/README.md`: "C/C++ and Pro*C (`.pc`)" in analyzer list.
- `code-tiny/docs/cplus_analyzer_guide.md`: Pro*C section, new labels/relationships table.
- `code-tiny/mcp/Readme.md`: alias list update.

## Verification steps

1. `python -m py_compile` all changed Python files.
2. Run focused scanner tests (prepare_proc_bytes, encoding, masking).
3. Run semantic tests (analyze_proc_file, table extraction, cursor lifecycle).
4. Run registry tests (`.pc` routing, alias canonicalization).
5. Run cache round-trip test (`_load_or_parse_payload` with `.pc` fixture).
6. Run build pipeline test (`build_call_graph` with FakeCodeWriter).
7. Full C/C++ regression: existing tests still pass with non-`.pc` files.

## Success criteria

- All focused tests pass.
- No reference to `proc_sql` or `CplusSqlStatement` in code-tiny or tests.
- `python -m py_compile` clean for every changed file.
- Documentation lists 5 Pro*C labels and 9 relationship types.
