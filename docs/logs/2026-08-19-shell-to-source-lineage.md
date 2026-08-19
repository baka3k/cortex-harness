# Shell-to-source lineage through an external ledger — 2026-08-19

## Context

The shell analyzer described script-to-script and configuration dependencies,
but batch command invocations did not connect to the already-materialized native
source files that implement those commands. The extension follows the
[shell analyzer phase](../../plans/260731-1500-legacy-migration-parser-coverage/phase-03-shell-script-analyzer.md)
without embedding deployment-specific program names or routing rules.

## Change

- Shell models now represent command invocations and evidence-backed program
  mappings independently from scripts and functions
  (`code-tiny/tools/shell/models.py:27`, `code-tiny/tools/shell/models.py:40`).
- The parser performs function discovery before scanning commands, preserves
  internal function calls, extracts generic external invocations with shell
  tokenization, and marks dynamic commands explicitly
  (`code-tiny/tools/shell/parser.py:126`,
  `code-tiny/tools/shell/parser.py:157`,
  `code-tiny/tools/shell/parser.py:245`).
- A JSON ledger loader accepts configurable identifier, source-path, and evidence
  fields; it canonicalizes root-relative source paths, rejects root escapes and
  conflicting mappings, and hashes the ledger when row evidence is absent
  (`code-tiny/tools/shell/mapping.py:20`,
  `code-tiny/tools/shell/mapping.py:39`,
  `code-tiny/tools/shell/mapping.py:52`).
- Graph output adds invocation and batch-program nodes plus
  `HAS_INVOCATION`, `RESOLVES_TO`, and `IMPLEMENTED_BY` edges. Resolution edges
  are emitted only after the target file is confirmed in the same project scope
  (`code-tiny/tools/shell/shell_analyzer.py:35`,
  `code-tiny/tools/shell/shell_analyzer.py:152`,
  `code-tiny/tools/shell/shell_analyzer.py:179`).
- The analyzer CLI and incremental sync accept the external ledger without
  adding project-specific mappings to the repository
  (`code-tiny/tools/shell/shell_analyzer.py:346`,
  `code-tiny/tools/sync/incremental_sync.py:1191`).

## Impact

**Risk level: medium.** Batch shell jobs can now be traced through an exact
invocation node to a logical program and then to a materialized native source
file. Missing source endpoints remain visible as unresolved status and do not
produce false implementation edges.

An isolated full run materialized 23,669 scripts, 276,817 invocations, and 3,381
mapped programs. A representative batch script produced four verified invocation
chains to native source files. Unit coverage includes generic and dynamic
commands, configurable ledger fields, canonical paths, conflict handling, and
the missing-source fail-closed case (`tests/test_shell_parser.py:47`,
`tests/test_shell_program_mapping.py:28`,
`tests/test_shell_program_mapping.py:81`,
`tests/test_shell_program_mapping.py:127`).

## Decision

Program-to-source knowledge stays in an external, hash-bound ledger because it
is deployment data, not parser logic. The shell parser records syntax evidence;
the ledger supplies routing evidence; and the graph writer accepts a resolved
lineage only when the target file is physically present in the scoped graph.
This separation avoids heuristic filename guesses and keeps dynamic commands
truthfully unresolved.

## References

- Plan: [Shell script analyzer](../../plans/260731-1500-legacy-migration-parser-coverage/phase-03-shell-script-analyzer.md)
- Mapping loader: `code-tiny/tools/shell/mapping.py:39`
- Scoped lineage gate: `code-tiny/tools/shell/shell_analyzer.py:179`
- Parser tests: `tests/test_shell_parser.py:47`
- Lineage tests: `tests/test_shell_program_mapping.py:81`
