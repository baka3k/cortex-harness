# Phase 4: `KEY:VALUE` INI Coverage + `.dat` Resource Registration

## Goal

Parse the project's pseudo-INI config files (`KEY:VALUE` per line, not
`configparser` `[section]key=value`) so Phase 2's shell `ShellConfigRead`
edges resolve to real config-key nodes, and register `.dat` delivery/output
folders as path/resource nodes without content parsing.

## 4.1 — INI (`KEY:VALUE`) parser

The sample (`BBSEAB01_06_01.ini`) is:
```
WK_MK_MNG_KEY:BBSEAB06
WK_MK_MNG_PTN_KEY:01
```
plus a CP932-encoded Japanese comment header (`#`-prefixed, same decode
fallback as Phases 1–3).

Create `code-tiny/tools/batchconfig/` (or extend an existing lightweight
config tool if one already parses simple key-delimiter files — search first;
none found in current registry):
- `ini_analyzer.py` — CLI entry
- `ini_parser.py` — line parser: skip `#` comments/blank lines, split each
  line on the **first** `:`, trim, produce `ConfigEntry(key, value, line)`.
  Do not use `configparser` (wrong grammar for this format).
- Node: one `ConfigFile` node per `.ini`, one `ConfigEntry` child per line.
- Relation: `DEFINES_CONFIG` from file to entry.

## 4.2 — Link shell `grep` reads to config entries

In Phase 2's `ShellConfigRead(config_key="WK_MK_MNG_KEY", ini_path_expr=...)`,
resolve `ini_path_expr` (which may itself be templated,
e.g. `${BZZ_BT_ENVPATH}/${WK_MK_MNG_KEY}-${WK_MK_MNG_PTN_KEY}.ini`) via
best-effort matching:
- If the expression is a static literal path, resolve directly to the
  `ConfigFile` node.
- If templated (contains `${...}`), emit a `READS_CONFIG` edge to the
  **directory** (all `.ini` files under the resolved static path prefix) with
  a `note="templated path, resolved to directory scope"` flag rather than
  guessing a single file — avoid false-precision.

## 4.3 — `.dat` folders

`04.DAT/**` in the sample contains only delivery/output subfolders (e.g.
`送付フォルダ/`), no parseable content. Register as `PathNode`/resource
entries only (path + role=`DescriptorRole.RESOURCE`), matching how the
project already treats non-code asset directories — no new parser needed,
just ensure `.dat` directories aren't silently excluded from
`project_topology`'s special-file scan.

## Registration

- `code-tiny/tools/common/message_scan.py::_PARSER_EXTENSIONS` → add
  `"batchconfig": (".ini",)`
- `code-tiny/tools/sync/incremental_sync.py` → add `AnalyzerConfig("batchconfig", ...ini_analyzer.py, True)`
- `code-tiny/tools/project_topology/registry.py` → add `"batchconfig"`
  `CoverageEntry` (`DescriptorRole.CONFIGURATION`, `ParseDepth.IDENTITY`) and
  ensure `.dat` paths are covered by a `RESOURCE`-role entry (no parser
  reference, path registration only).
- `code-tiny/mcp/framework_registry.py` → add `"batchconfig"` profile,
  aliases `{"ini", "batchconfig", "config"}`.

## Files Touched

- `code-tiny/tools/batchconfig/` (new: `ini_analyzer.py`, `ini_parser.py`, `models.py`, `README.md`)
- Phase 2's `shell_parser.py` (resolve `ShellConfigRead` → `ConfigEntry`)
- `code-tiny/tools/common/message_scan.py`
- `code-tiny/tools/sync/incremental_sync.py`
- `code-tiny/tools/project_topology/registry.py`
- `code-tiny/mcp/framework_registry.py`

## Validation

- Parse `BBSEAB01_06_01.ini`; confirm 2 `ConfigEntry` nodes
  (`WK_MK_MNG_KEY=BBSEAB06`, `WK_MK_MNG_PTN_KEY=01`) with correct decoded
  comment header.
- Confirm `BBSEAB01.sh`'s `WK_MK_MNG_KEY`/`WK_MK_MNG_PTN_KEY` grep reads
  produce `READS_CONFIG` edges (templated-path case) to the `03.INI/env/ALL/`
  directory scope.
