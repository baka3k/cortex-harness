# Phase 03: Shell Script Analyzer

## Context

`.sh` files are entirely unhandled today (no `shell` key in `ANALYZERS`, no `tools/shell` package). Sample scripts (e.g. `BBSEAB01.sh`) define shell functions, source/invoke other `.sh` scripts, and read parameters out of `.ini` files via `grep 'KEY' path.ini | awk -F: '{print $2}'`. These are the two dependency edges migration engineers actually need (script→script call graph, script→config reference), not full POSIX semantics.

## Requirements

- New package `code-tiny/tools/shell/` following the `perl_analyzer.py` skeleton: CLI (`--root`, `--project-id`, `--project-name`, incremental manifests, Neo4j/Qdrant args, `--dry-run`), a `pipeline.py` producing a fact result, `build_graph_rows()` for `LanguageCodeWriter`, `safe_cache_root()` for incremental caching.
- Read files through `read_legacy_text` (Phase 01).
- Parse shell function definitions: `name() { ... }` and `function name { ... }` forms → `ShellFunction` nodes owned by a `ShellScript` file node.
- Parse invocation edges: `. other.sh`, `source other.sh`, `sh other.sh`, `./other.sh`, and bare `${VAR}.sh` when `VAR` was assigned a literal earlier in the same file → `CALLS` edge from the invoking `ShellScript`/`ShellFunction` to the target `ShellScript` (resolve target path relative to the invoking file's directory first, then project root; if unresolved, still emit the edge with `resolved: false` and the raw literal).
- Parse config references: `grep '<KEY>' "<path-expr>"` where `<path-expr>` ends in `.ini` (directly or via simple `${VAR}` substitution from variables assigned earlier in the same file) → `REFERENCES` edge from the `ShellScript`/`ShellFunction` to the target `.ini` path (or an `unresolved` diagnostic with the raw expression when substitution isn't possible, e.g. cross-file variable dependencies like the `WK_MK_MNG_KEY` example from `FC_GET_INI`).
- Do not implement a general shell interpreter (no control-flow, no arithmetic, no full variable scoping) — pattern-based extraction only, explicitly scoped in the plan's Risks section.
- Register `shell` across the 7-file checklist: `incremental_sync.py` (`ANALYZERS`, `_SOURCE_EXTENSIONS` add `.sh`, `_select_parser_for_path`), `owner_manifest.py` (`SUPPORTED_PARSERS`, detection function), `cortex_harness/dev.py` (`LANG_ANALYZERS`, `LANG_EXTENSIONS`), `framework_registry.py` (`CAPABILITIES["shell"]`), `project_topology/registry.py` (only if a build/identity special-file marker makes sense — likely skip, shell projects don't have a canonical manifest file), `tests/test_mcp_acceptance_matrix.py` (`ACCEPTANCE_MATRIX["shell"]`, `PRIMARY_TO_PROFILE["shell"]`), `tests/test_common_analyzer_registry.py` (`EXPECTED_PRIMARY` add `"shell"`).

## Architecture

```text
code-tiny/tools/shell/
├── __init__.py
├── shell_analyzer.py       # CLI entry point
├── models.py                # ShellScript, ShellFunction, edge fact dataclasses
├── parser.py                 # regex/line-based extraction (functions, invocations, grep-ini refs)
├── pipeline.py                # full/incremental orchestration, calls parser.py per changed file
└── README.md
```

Keep extraction line-based/regex, not a full shell grammar/tree-sitter grammar — there is no mature, widely-used tree-sitter-bash coverage need here beyond what regex handles for this fixed set of patterns, and pulling in a shell grammar dependency would be disproportionate to the two edge types required.

## Related Files

Create:
- `code-tiny/tools/shell/__init__.py`
- `code-tiny/tools/shell/shell_analyzer.py`
- `code-tiny/tools/shell/models.py`
- `code-tiny/tools/shell/parser.py`
- `code-tiny/tools/shell/pipeline.py`
- `code-tiny/tools/shell/README.md`
- `tests/fixtures/shell-application/BBSEAB01.sh` (synthetic, mirrors function calls + grep-ini pattern)
- `tests/fixtures/shell-application/other_target.sh`
- `tests/test_shell_parser.py`
- `tests/test_shell_analyzer_pipeline.py`

Modify:
- `code-tiny/tools/sync/incremental_sync.py`
- `code-tiny/tools/sync/owner_manifest.py`
- `cortex_harness/dev.py`
- `code-tiny/mcp/framework_registry.py`
- `tests/test_mcp_acceptance_matrix.py`
- `tests/test_common_analyzer_registry.py`

Reference:
- `code-tiny/tools/perl/perl_analyzer.py` (analyzer skeleton to mirror)
- `code-tiny/tools/common/analyzer_cache.py::safe_cache_root`
- `code-tiny/tools/graph/writer/language_writer.py::LanguageCodeWriter`
