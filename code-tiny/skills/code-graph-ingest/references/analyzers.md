# Analyzer CLI reference

## Common flags (all analyzers)
- `--root` (required)
- `--neo4j-uri`, `--neo4j-user`, `--neo4j-password`, `--neo4j-db`
- `--qdrant-url`, `--qdrant-collection`
- `--embed-model`, `--max-embed-chars`, `--chunk-embed`
- `--device`
- `--batch-size`
- `--neo4j-batch-size`
- `--neo4j-state`, `--disable-neo4j-resume`
- `--qdrant-batch-size`, `--qdrant-timeout`, `--qdrant-retries`, `--qdrant-retry-sleep`
- `--cache-dir`, `--keep-cache`, `--disable-parse-cache`
- `--project-id`, `--project-name`, `--language`, `--repo`, `--build-system`
- `--dry-run`, `--verbose`

## Kotlin (`tools/kotlin/kotlin_analyzer.py`)
- Default collection: `kotlin_functions`
- Default device: `auto` (resolves to `cuda`, `mps`, or `cpu`)
- Default `--batch-size`: 4
- Default `--qdrant-batch-size`: 128
- Vector size derived from the embed model

## Java (`tools/java/java_analyzer.py`)
- Default collection: `java_functions`
- Default device: `cpu`
- Default `--batch-size`: 4
- Default `--qdrant-batch-size`: 128
- Vector size is fixed at 768 in code

## TypeScript (`tools/ts/ts_analyzer.py`)
- Default collection: `typescript_functions`
- Default device: `auto` (resolves to `cuda`, `mps`, or `cpu`)
- Default `--batch-size`: 4
- Default `--qdrant-batch-size`: 128
- Vector size derived from the embed model

## JavaScript (`tools/js/js_analyzer.py`)
- Default collection: `javascript_functions`
- Default device: `auto` (resolves to `cuda`, `mps`, or `cpu`)
- Default `--batch-size`: 4
- Default `--qdrant-batch-size`: 128
- Vector size derived from the embed model

## PHP (`tools/php/php_analyzer.py`)
- Default collection: `php_functions`
- Default device: `auto` (resolves to `cuda`, `mps`, or `cpu`)
- Default `--batch-size`: 4
- Default `--qdrant-batch-size`: 128
- Vector size derived from the embed model

## SQL (`tools/sql/sql_analyzer.py`)
- Default collection: `sql_functions`
- Default device: `auto` (resolves to `cuda`, `mps`, or `cpu`)
- Default `--batch-size`: 4
- Default `--qdrant-batch-size`: 128
- Vector size derived from the embed model

## PL/SQL (`tools/plsql/plsql_analyzer.py`)
- Default collection: `plsql_functions`
- Default device: `auto` (resolves to `cuda`, `mps`, or `cpu`)
- Default `--batch-size`: 4
- Default `--qdrant-batch-size`: 128
- Vector size derived from the embed model
- Parser: regex-based heuristics (no tree-sitter dependency)

## C# (`tools/csharp/csharp_analyzer.py`)
- Default collection: `csharp_functions`
- Default device: `cpu`
- Default `--batch-size`: 4
- Default `--qdrant-batch-size`: 128
- Vector size is fixed at 768 in code

## C/C++ (`tools/cplus/cplus_analyzer.py`)
- Default collection: `cplus_functions`
- Default device: `cpu`
- Default `--batch-size`: 8
- Default `--qdrant-batch-size`: 512
- Vector size is fixed at 768 in code
- Extra export flags:
  - `--event-map` (JSON mapping file for cross-project events/IDL)
  - `--call-stats-path` (write call resolution stats JSON)
  - `--possible-calls-path` (write POSSIBLE_CALLS edges JSON)
  - `--unresolved-calls-path` (write unresolved calls as JSONL)
