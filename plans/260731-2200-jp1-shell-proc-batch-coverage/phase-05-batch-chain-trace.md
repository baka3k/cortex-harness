# Phase 5: End-to-End Batch-Chain Trace + Graph/MCP Wiring

## Goal

Make the JP1 → shell → Pro*C → DB chain queryable through the existing
`mcp_project` graph tools, analogous to how `get_api_call_chain` /
`find_callers_of_endpoint` already bridge frontend↔backend for web frameworks.

## 5.1 — Cross-language edge assembly

By the end of Phases 1–4, these edges exist per-analyzer:
- JP1: `CONTAINS`, `PRECEDES`, `EXECUTES` (unit → shell script)
- Shell: function symbols, `ShellCallEdge` (script → other script/binary),
  `READS_CONFIG` (script → ini entry/dir)
- Pro*C (cplus): functions, `EXEC_SQL` (function → statement/table)
- Existing `sql`/`plsql` analyzers: table/procedure nodes

Phase 5 adds the **missing link**: Shell `ShellCallEdge` targets that name-match
a `.pc`/`.c` module stem (e.g. shell invokes `BZZAAB02` and `BZZAAB02.pc`
exists) must resolve to a `CALLS` edge from the shell script to the cplus
file/entry symbol. Implement this resolution in
`code-tiny/tools/common/call_graph_builder.py` (or a new
`batch_chain_linker.py` alongside it) as a **post-pass** that runs after all
per-language analyzers have written their nodes, matching the existing
pattern of cross-file call resolution by name index.

## 5.2 — Query/trace support

- Extend `code-tiny/mcp/framework_registry.py::CPLUS_RELATIONSHIPS` (and
  equivalent for the new `shell`/`jp1`/`batchconfig` profiles) to include the
  new relation kinds (`EXEC_SQL`, `EXECUTES`, `PRECEDES`, `READS_CONFIG`,
  `CALLS` cross-language).
- Add a `trace_batch_chain` capability (mirrors `get_api_call_chain`) in the
  unified MCP tool set: given a JP1 unit id, DB table name, or `.pc` file,
  walk `EXECUTES`/`CALLS`/`EXEC_SQL` edges in both directions to answer
  "what job-nets/scripts touch this DB table" and "what DB objects does this
  job-net eventually hit" — the primary Java-migration impact-analysis use
  case (per user's stated goal).

## 5.3 — Encoding consistency check

Run a repo-wide check (dry-run over the full `JavaMigration/REDACTED` sample,
read-only, outside the workspace, not committed) confirming CP932 fallback
decode (Phase 1's shared `text_encoding.decode_source_bytes`) is applied
consistently across `.pc`, `.sh`, `.ini`, and JP1 `.txt` — these all showed
Shift-JIS Japanese comments in the samples.

## Files Touched

- `code-tiny/tools/common/call_graph_builder.py` or new
  `code-tiny/tools/common/batch_chain_linker.py`
- `code-tiny/mcp/framework_registry.py`
- `code-tiny/mcp/fastmcp_server.py` / `unified_mcp.py` (new trace tool wiring,
  exact entry point TBD during implementation — confirm current tool
  registration pattern before adding)

## Validation

- End-to-end dry run over the sample subtree: from `JBCWV013_ALL.txt`
  (JP1 job-net), trace to `JBCWV013_ALL-010.sh` (shell), to whichever
  `.pc`/`.c` program it invokes, to any table referenced via `EXEC_SQL` —
  confirm the full chain is walkable via the new trace tool.
- Confirm no regression in existing `get_api_call_chain`/`find_callers_of_endpoint`
  behavior for already-supported frameworks (aspnet, spring, etc.) since
  `framework_registry.py` is shared/edited in this phase.

## Follow-up note (not a phase here)

When `260731-1030-rust-extraction-layer` / `260731-1700-multi-language-rust-extraction`
eventually port `cplus` extraction fully to Rust, the Pro*C preprocessing step
from Phase 1 (`_preprocess_proc_directives`) must be ported alongside it (or
kept as a Python pre-pass feeding the Rust extractor) so `.pc` support is not
lost. Flag this explicitly in that plan's task list when it reaches the cplus
Rust cutover.
