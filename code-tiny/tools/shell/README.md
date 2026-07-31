# tools/shell

Regex/line-based structural analyzer for POSIX shell (`.sh`) batch scripts,
targeting the JP1/AJS-driven batch chains (`shell -> Pro*C -> Oracle`) style
of scripts: sequential, flat control flow, no deep nesting.

## Modules

- `models.py` — dataclasses (`ShellScriptFile`, `ShellVariable`,
  `ShellFunctionDef`, `ShellConfigRead`, `ShellCallEdge`, `RelationEdge`).
- `shell_parser.py` — `parse_shell_file(path, root)` extracts variables,
  function bodies, config reads (`grep 'KEY' file.ini | awk ...`), and call
  edges (`sh ./other.sh`, `${DIR}/other.sh`); `build_relations(script)`
  converts config-read/call-edge facts into generic graph relations
  (`READS_CONFIG`, `CALLS`).
- `shell_analyzer.py` — CLI entry point (`--root`, `--dry-run`, standard
  graph-provider/vector-sync args), mirrors `tools/go/go_analyzer.py`.

## Usage

```bash
python -m tools.shell.shell_analyzer --root /path/to/project --dry-run
```

## Registration

- `tools/common/message_scan.py::_PARSER_EXTENSIONS["shell"] = (".sh",)`
- `tools/sync/incremental_sync.py::ANALYZERS["shell"]` and
  `_select_parser_for_path`/`_SOURCE_EXTENSIONS`
- `tools/project_topology/registry.py::PRIMARY_SPECIAL_FILE_COVERAGE["shell"]`
- `mcp/framework_registry.py` — `"shell"` profile with aliases
  `{"shell", "sh", "bash", "posix-shell"}`, routed to `graph_generic`.
