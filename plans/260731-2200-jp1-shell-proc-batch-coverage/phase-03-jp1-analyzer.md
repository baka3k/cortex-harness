# Phase 3: New `tools/jp1` Analyzer for JP1/AJS Unit-Definition Files

## Goal

Parse Hitachi JP1/AJS job-net **unit definition export** files (the
`unit=...;{ ... }` DSL seen in `JBCWV013_ALL.txt`) into a job-net hierarchy
graph, with the `te="...sh"` field producing the cross-link into Phase 2's
shell script nodes.

## Detection (content-sniffed, not extension-based)

These files have no reliable extension (`.txt` here; JP1 exports are commonly
extensionless or `.def` in other projects). Detect by content: first
non-whitespace line matches `^unit=[A-Za-z0-9_]+,`. Register this sniff in
whatever central file-classification step feeds the analyzer dispatch (check
`code-tiny/tools/common/source_inventory.py` for where content-sniff already
happens, e.g. `_looks_like_cpp_header`-style heuristics) so JP1 files aren't
silently skipped as "unknown/text".

## Module Shape

Create `code-tiny/tools/jp1/`:
- `jp1_analyzer.py` — CLI entry, same conventions as `perl_analyzer.py`/`shell_analyzer.py`
- `jp1_parser.py` — recursive-descent parser for the brace-nested `unit=...{ ... }` grammar:
  - Top-level: `unit=<id>,<comment-or-blank>,<parent-service>,;`
  - Block attributes (`;`-terminated `key=value` pairs inside `{ }`):
    `ty` (unit type: `n`=jobnet/group, `j`=job, `g`=?), `cm` (comment/display
    name, quoted, may contain Japanese — CP932 decode), `sz`, `el` (element
    position), `ar=(f=...,t=...,seq)` (sequence/flow edge between sibling
    units), `te` (execution command/script path, quoted), `tho`/`wth`/`eu`
    (timeout/wait/exec-user — capture as opaque attributes, not modeled
    further per Non-Goals), `sd` (start delay/schedule flag).
  - Nested `unit=...{ ... }` blocks recurse (job-net containing sub-units).
- `models.py` — `Jp1Unit(unit_id, unit_type, comment, parent_id, sequence_edges, exec_command)`

## Graph Representation

- One node per `unit=` block: `kind="jobnet"` if `ty=n`, `kind="job"` if `ty=j`.
- `CONTAINS` relation from parent unit to nested unit (job-net → its jobs).
- `PRECEDES` relation from `ar=(f=X,t=Y,...)` (X runs before Y).
- `EXECUTES` relation from a `ty=j` unit to the shell script referenced in
  `te="@BOSAPDIR@/sh/ALL/XXX.sh"` — resolve the path template
  (`@BOSAPDIR@` is an env placeholder) to the Phase 2 shell script node by
  matching the trailing path segment (`sh/ALL/XXX.sh`) against indexed shell
  script paths, same best-effort name/path matching approach as Phase 2's
  call-edge resolution.

## Registration

- Add a content-sniff based dispatch entry (not extension-keyed) — if
  `incremental_sync.py`'s `AnalyzerConfig` dispatch is strictly
  extension-driven, add a pre-pass that reclassifies `.txt` files under
  `*JP1定義*`/`*ジョブネット*` path patterns (or content-sniffed anywhere) to
  the `jp1` analyzer before the default `.txt`/unknown handling applies.
- `code-tiny/tools/project_topology/registry.py` → add `"jp1"` `CoverageEntry`
  with `DescriptorRole.TOPOLOGY` (it describes execution topology, not a
  dependency manifest), `ParseDepth.SEMANTIC` (need the `ar=`/`te=` graph, not
  just identity).
- `code-tiny/mcp/framework_registry.py` → add `"jp1"` profile
  (`aliases={"jp1", "ajs", "jobnet"}`), route to `graph_generic`.

## Files Touched

- `code-tiny/tools/jp1/` (new: `jp1_analyzer.py`, `jp1_parser.py`, `models.py`, `README.md`)
- `code-tiny/tools/common/source_inventory.py` (content-sniff hook)
- `code-tiny/tools/sync/incremental_sync.py`
- `code-tiny/tools/project_topology/registry.py`
- `code-tiny/mcp/framework_registry.py`

## Validation

- Parse `JBCWV013_ALL.txt`; confirm the 8 sub-units (`-010` … `-080`) are
  extracted with correct `ty`, `cm` (Japanese decoded), and that the 8
  `ar=(f=...,t=...)` lines produce exactly 8 `PRECEDES` edges matching the DAG
  in the sample (010→020→{030,040}→050→060→070→080, 040→080 direct).
- Confirm each `-010`..`-080` unit's `te="...sh"` resolves to an `EXECUTES`
  edge once Phase 2 shell nodes exist for those paths.
