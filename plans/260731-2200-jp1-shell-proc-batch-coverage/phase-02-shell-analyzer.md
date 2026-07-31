# Phase 2: New `tools/shell` Analyzer for `.sh` Batch Scripts

## Goal

Discover and parse POSIX shell batch scripts (`01.SHELL/00.sh/**/*.sh` style)
so they become graph nodes with extracted call/read/config edges, following
the same module shape as the existing `perl` analyzer (closest precedent:
interpreted scripting language, `IDENTITY`-depth parse, no compiler).

## Module Shape (mirrors `code-tiny/tools/perl/`)

Create `code-tiny/tools/shell/`:
- `shell_analyzer.py` — CLI entry (`--root`, `--dry-run`, matches other
  analyzers' argparse conventions, e.g. `tools/vb/vbnet_analyzer.py`)
- `shell_parser.py` — line/regex-based extraction (no full shell grammar;
  `bash`/`sh` scripts here are simple, sequential, `KEY=value` + command
  invocation style, not deeply nested control flow)
- `models.py` — dataclasses: `ShellScriptFile`, `ShellVariable`,
  `ShellCallEdge`, `ShellConfigRead`
- `README.md` — usage doc, matching sibling tools

## Extraction Rules

From the sample (`BBSEAB01.sh`):
- **Identity**: script id = filename stem (`BBSEAB01`), decode with the same
  CP932 fallback as Phase 1 (`text_encoding.decode_source_bytes`).
- **Variable assignments**: `NAME="value"` / `` NAME=`cmd` `` (backtick or
  `$(...)` command substitution) → `ShellVariable(name, raw_expr, line)`.
- **Config reads**: pattern
  `` grep 'KEY' "${SOME_PATH}/${...}.ini" | awk -F: '{ print $2 }' ``
  → `ShellConfigRead(config_key="KEY", ini_path_expr="${SOME_PATH}/...")`.
  This is the edge Phase 4's ini coverage links against.
- **Call edges**: any invocation of another script/binary as a bare command,
  `sh ./other.sh`, `${SOMEDIR}/other.sh`, or a compiled program name that
  matches a known `.pc`/`.c` module stem (best-effort name match against the
  cplus symbol/file index, analogous to how `call_graph_builder.py` resolves
  cross-file calls by name) → `ShellCallEdge(callee_ref, line)`.
- **Function definitions** inside the script (`FC_GET_INI(){ ... }` style,
  seen in the sample) → treated as symbols (`kind=function`) so intra-script
  structure is visible, same idea as cplus functions.

## Registration (wire into existing infra)

- `code-tiny/tools/common/message_scan.py::_PARSER_EXTENSIONS` → add
  `"shell": (".sh",)`
- `code-tiny/tools/sync/incremental_sync.py` → add
  `"shell": AnalyzerConfig("shell", os.path.join(_ROOT_DIR, "tools", "shell", "shell_analyzer.py"), True)`
  following the existing `cplus`/`cobol`/`dart` entries.
- `code-tiny/tools/project_topology/registry.py::PRIMARY_SPECIAL_FILE_COVERAGE`
  → add a `"shell"` `CoverageEntry` (glob `*.sh`, role `DescriptorRole.INTERFACE`,
  `ParseDepth.IDENTITY`, doc key `"parser-special-files/shell"`).
- `code-tiny/mcp/framework_registry.py` → add a `"shell"` entry to
  `PUBLIC_QUERY_ENGINES` (route to `graph_generic`) and a generic profile
  entry with aliases `{"shell", "sh", "bash", "posix-shell"}`.

## Files Touched

- `code-tiny/tools/shell/` (new: `shell_analyzer.py`, `shell_parser.py`,
  `models.py`, `README.md`)
- `code-tiny/tools/common/message_scan.py`
- `code-tiny/tools/sync/incremental_sync.py`
- `code-tiny/tools/project_topology/registry.py`
- `code-tiny/mcp/framework_registry.py`

## Validation

- Run against `BBSEAB01.sh` (and 2–3 sibling scripts) in dry-run mode; confirm
  variable/config-read/call-edge counts are non-zero and match manual review.
- Confirm CP932-encoded comment headers decode without mojibake in the
  extracted `summary`/`comment` fields.
