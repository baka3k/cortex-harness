# Analyzer CLI reference (Rust binaries — phase-08 cutover)

The Python analyzer entry points (`tools/<lang>/<lang>_analyzer.py`) were
retired at the phase-08 cutover. This reference now describes the
`analyzer-<lang>` Rust binaries that replace them; the CLI contract is
byte-for-byte compatible for the flags that survived the port.

## Common flags (all analyzer binaries)
- `--root` (required)
- `--falkordb-uri`, `--falkordb-graph`, `--ladybug-path`, `--ladybug-graph`,
  `--graph-provider`
- `--qdrant-url`, `--qdrant-collection`
- `--embed-model`, `--max-embed-chars`, `--device`, `--batch-size`
- `--incremental`, `--changed-files-manifest`, `--deleted-files-manifest`
- `--enable-message-scan`, `--disable-message-scan`, `--message-output-dir`,
  `--message-qdrant-collection`
- `--cache-dir`, `--keep-cache`, `--disable-parse-cache`
- `--project-id`, `--project-name`, `--language`, `--repo`, `--build-system`
- `--dry-run`, `--verbose`

## Kotlin (`analyzer-kotlin`)
- Default device: `auto` (resolves to `cuda`, `mps`, or `cpu`)
- Default `--batch-size`: 4

## Java (`analyzer-java`)
- Vector lane (`cortex-embed` orchestrator-level) — see
  `reports/phase08-cutover.md` for the embedding disposition.

## TypeScript (`analyzer-ts`)
- `--mode` selects the analyzer pipeline.

## JavaScript (`analyzer-js`)

## PHP (`analyzer-php`)

## SQL (`analyzer-sql`)

## PL/SQL (`analyzer-plsql`)

## C# (`analyzer-csharp`)
- Spawns the Roslyn worker (`tools/csharp/roslyn_worker/`) + bootstrap build.
- Requires the .NET SDK; see `docs/cutover-runbook.md` §5.1.5.

## C/C++ (`analyzer-cplus`)
- Spawns the Python clang plane (`tools/cplus/clang_parser.py` +
  `tools/cplus/semantic_worker.py` + `tools/cplus/proc_analyzer.py`).
- Requires the cplus clang plane + parse-quality flags.

## COBOL (`analyzer-cobol`)

## Dart (`analyzer-dart`)
- `--mode dart|flutter` (no `all`; orchestrator chooses per invocation).

## Project topology (`analyzer-topology`)
- Runs at end of sync; reads graph → computes topology → writes via
  `cortex-graph-writer::topology`.