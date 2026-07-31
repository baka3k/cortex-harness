# Phase 04: JP1 Jobnet Analyzer

## Context

Hitachi JP1/AJS job-network definitions (e.g. `JBCWV013_ALL.txt`) describe a job DAG using a proprietary brace-delimited DSL: `unit=<name>,,<host>,;` blocks contain `ty=n` (jobnet/group) or `ty=j` (job) type markers, `cm="<comment>"`, `el=` (element layout, cosmetic), `ar=(f=<from>,t=<to>,seq)` (execution-order edges), and `te="<path/to/script>.sh"` (the actual executable for a job unit). These files use the extension `.txt`, which is also used for arbitrary unrelated text elsewhere in a project, so ownership must be decided by content, not extension.

## Requirements

- New package `code-tiny/tools/jp1/`.
- Detection: a `.txt` file is claimed by the `jp1` parser only if content sniffing finds a line matching `^\s*unit=` within the first 5 non-blank lines **and** a following `{` block containing at least one `ty=` line. `_select_parser_for_path` in `incremental_sync.py` must special-case `.txt` to run this sniff instead of unconditionally mapping the extension (this is the one entry in the 7-file checklist that differs from a normal "map extension to parser" registration).
- Parse nested `unit=name,,host,;` blocks (they nest — a jobnet `ty=n` unit contains child `unit=` blocks) into `Jp1Unit` nodes with properties `type` (from `ty=`), `comment` (from `cm=`), `exec_target` (raw string from `te=`, if present).
- Parse `ar=(f=A,t=B,seq)` lines into `NEXT` edges between sibling `Jp1Unit` nodes (`A -> B`) within the same parent block.
- Parse `INCLUDES` edges from a parent jobnet `Jp1Unit` to each child `Jp1Unit`.
- For units with a `te="...sh"` target, resolve the referenced path against the project root (JP1 files reference scripts like `@BOSAPDIR@/sh/ALL/JBCWV013_ALL-010.sh` — treat `@VAR@` placeholders as literal path segments unless a project-level substitution table is configured; when the resolved path exists as a scanned `ShellScript` file, emit a `CALLS` edge from the `Jp1Unit` to that `ShellScript`; otherwise record an `unresolved` diagnostic with the raw `te=` value) — this is the cross-parser link that makes the jobnet → shell → ini chain traceable end-to-end.
- Read files through `read_legacy_text` (Phase 01); JP1 files carry Shift-JIS `cm=` comments (Japanese text).
- Register `jp1` across the 7-file checklist with the content-sniff caveat above: `incremental_sync.py` (`ANALYZERS`, `_select_parser_for_path` sniff branch — do **not** add `.txt` to `_SOURCE_EXTENSIONS` unconditionally; instead special-case it so plain `.txt` files aren't force-claimed), `owner_manifest.py`, `cortex_harness/dev.py` (`LANG_ANALYZERS`; `LANG_EXTENSIONS["jp1"]` can stay empty/informational since detection is content-based), `framework_registry.py` (`CAPABILITIES["jp1"]`), `project_topology/registry.py` (skip — no canonical manifest file), `tests/test_mcp_acceptance_matrix.py`, `tests/test_common_analyzer_registry.py`.

## Architecture

```text
code-tiny/tools/jp1/
├── __init__.py
├── jp1_analyzer.py     # CLI entry point
├── models.py             # Jp1Unit, edge fact dataclasses
├── sniff.py               # content-sniff detector used by incremental_sync._select_parser_for_path
├── parser.py              # brace-block parser (unit=/ty=/cm=/ar=/te=)
├── pipeline.py            # full/incremental orchestration + cross-link to shell ShellScript nodes
└── README.md
```

`sniff.py` is imported directly by `code-tiny/tools/sync/incremental_sync.py::_select_parser_for_path` — this is the one place a new parser needs logic beyond a static extension-set lookup, so keep the sniff function small, pure, and independently unit-tested.

## Related Files

Create:
- `code-tiny/tools/jp1/__init__.py`
- `code-tiny/tools/jp1/jp1_analyzer.py`
- `code-tiny/tools/jp1/models.py`
- `code-tiny/tools/jp1/sniff.py`
- `code-tiny/tools/jp1/parser.py`
- `code-tiny/tools/jp1/pipeline.py`
- `code-tiny/tools/jp1/README.md`
- `tests/fixtures/jp1-application/JBCWV013_ALL.txt` (synthetic, mirrors nested unit=/ar=/te= structure)
- `tests/test_jp1_sniff.py`
- `tests/test_jp1_parser.py`
- `tests/test_jp1_shell_cross_link.py` (verifies `Jp1Unit --CALLS--> ShellScript` when both analyzers run over the same fixture project)

Modify:
- `code-tiny/tools/sync/incremental_sync.py` (`_select_parser_for_path` sniff branch, `ANALYZERS`)
- `code-tiny/tools/sync/owner_manifest.py`
- `cortex_harness/dev.py`
- `code-tiny/mcp/framework_registry.py`
- `tests/test_mcp_acceptance_matrix.py`
- `tests/test_common_analyzer_registry.py`

Reference:
- `code-tiny/tools/shell/models.py` / `parser.py` (Phase 03 — target of the `CALLS` cross-link)
- `code-tiny/tools/sync/incremental_sync.py:495-550` (`_select_parser_for_path`)
