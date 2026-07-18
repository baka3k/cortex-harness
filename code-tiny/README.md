# Cortex Harness Code Intelligence (`code-tiny`)

`code-tiny` contains the source analyzers and unified MCP server used by Cortex Harness for code intelligence. It can:

- parse supported source languages into deterministic symbols and relationships;
- persist code structure to FalkorDB or Neo4j;
- persist semantic vectors for primary-language symbols to Qdrant;
- enrich primary-language graphs with framework-specific facts; and
- expose search, traversal, impact, dependency-planning, and workflow tools through one MCP endpoint.

Run the examples below from the `code-tiny/` directory unless stated otherwise.

## Supported analyzers

### Primary language analyzers

Primary analyzers own source files and can write graph facts and Qdrant vectors.

| Parser | Script/module | Typical sources |
| --- | --- | --- |
| `android` | `tools/android/android_kotlin_analyzer.py` | Android Kotlin/Java/XML projects |
| `cobol` | `tools/cobol/cobol_analyzer.py` | COBOL copybooks and programs |
| `cplus` | `tools/cplus/cplus_analyzer.py` | C, C++, headers, Windows resources |
| `csharp` | `tools/csharp/csharp_analyzer.py` | C# source |
| `dart` | `tools/flutter/flutter_analyzer.py --mode dart` | Dart packages and Flutter source |
| `delphi` | `tools/delphi/delphi_analyzer.py` | Delphi/Object Pascal |
| `go` | `tools/go/go_analyzer.py` | Go source |
| `java` | `tools/java/java_analyzer.py` | Java source |
| `js` | `tools/js/js_analyzer.py` | JavaScript/JSX |
| `kotlin` | `tools/kotlin/kotlin_analyzer.py` | Kotlin source |
| `perl` | `tools/perl/perl_analyzer.py` | Perl 5 `.pl`, `.pm`, and `.t` |
| `php` | `tools/php/php_analyzer.py` | PHP source |
| `plsql` | `tools/plsql/plsql_analyzer.py` | PL/SQL source |
| `python` | `tools/python/python_analyzer.py` | Python source |
| `rust` | `tools/rust/rust_analyzer.py` | Rust source |
| `sql` | `tools/sql/sql_analyzer.py` | SQL source |
| `swift` | `tools/swift/swift_analyzer.py` | Swift source |
| `ts` | `tools/ts/ts_analyzer.py` | TypeScript/TSX |
| `vbnet` | `tools/vb/vbnet_analyzer.py` | VB.NET |
| `vb6` | `tools/vb/vb6_analyzer.py` | Visual Basic 6 |
| `vba` | `tools/vb/vba_analyzer.py` | VBA |
| `vbscript` | `tools/vb/vbscript_analyzer.py` | VBScript and Classic ASP |

### Framework overlays

Framework overlays run after their prerequisite primary parser. They write graph facts only; semantic retrieval starts from the primary-language vector collection and expands through graph relationships. Struts XML-only facts also use the framework-filtered graph search fallback.

| Overlay | Module | Primary seed parser(s) |
| --- | --- | --- |
| `spring` | `tools.spring.spring_analyzer` | Java, Kotlin |
| `servlet_jsp` | `tools.servlet_jsp.servlet_jsp_analyzer` | Java |
| `mybatis` | `tools.mybatis.mybatis_analyzer` | Java, Kotlin |
| `struts` | `tools.struts.struts_analyzer` | Java / graph fallback |
| `flutter` | `tools.flutter.flutter_analyzer --mode flutter` | Dart |
| `aspnet_core` | `tools.aspnet_core.aspnet_core_analyzer` | C# |
| `aspnet_framework` | `tools.aspnet_framework.aspnet_framework_analyzer` | C# |

## Persistence model

```text
source files
  -> primary analyzer
     -> FalkorDB or Neo4j graph
     -> Qdrant primary-language vectors
  -> detected framework overlays
     -> graph-only framework facts
  -> unified MCP
     -> semantic seeds + bounded graph expansion
```

- FalkorDB is the default and recommended graph provider for Cortex Harness. Neo4j remains available as a compatibility provider.
- Qdrant is optional. When it is omitted, analyzers can run graph-only.
- Primary collections should normally be separated by parser, for example `digital_key_rust` and `digital_key_go`.
- A collection's vector dimension must match the configured embedding model.
- Rust, Go, Swift, Perl, and primary Dart use the shared incremental-safe vector adapter. Their IDs are deterministic and scoped by parser, project, repository/root scope, and symbol.
- Use a stable logical `--repo` value such as `digital_key`, not an absolute checkout path, to keep vector IDs portable between machines.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

FalkorDB/Neo4j and Qdrant are external services. Start the services required by your chosen persistence mode before running a non-dry scan.

## Preferred workflow: incremental orchestration

For a configured Cortex Harness project, prefer the root CLI. It detects primary languages and framework overlays, derives scoped collection names, propagates embedding settings, and manages incremental state.

`dev init` configures FalkorDB by default. The direct examples below also pass `--graph-provider falkordb` explicitly so they do not depend on legacy analyzer-local provider defaults.

```powershell
dev init C:\projects\digital_key
dev sync code --project-dir C:\projects\digital_key
dev sync code --project-dir C:\projects\digital_key all
dev sync code --project-dir C:\projects\digital_key --full-scan
dev sync code --project-dir C:\projects\digital_key --change-detection hash --reconcile
```

The underlying orchestrator can also be called directly:

```powershell
python tools/sync/incremental_sync.py `
  --root "C:\projects\digital_key" `
  --project-id digital_key `
  --project-name "digital_key" `
  --graph-provider falkordb `
  --falkordb-host localhost `
  --falkordb-port 6379 `
  --falkordb-graph digital_key `
  --qdrant-url http://localhost:6333 `
  --embed-model jinaai/jina-embeddings-v3 `
  --embed-device cuda `
  --embed-batch-size 1 `
  --max-embed-chars 800 `
  --verbose
```

Use `--full-scan` for a scoped full replacement. Without it, the default `hybrid` detector combines committed, staged, unstaged, and untracked Git candidates with a versioned SHA-256 inventory. An initialized submodule has its own Git baseline and is mapped back into the configured source root. A non-Git root falls back to content-hash comparison instead of being initialized or skipped.

The sync control state lives under `<source-root>/.cache` unless `--cache-dir`/`QDRANT_CACHE_DIR` is set. Lock files contain diagnostics only; ownership is enforced by an OS lock. Useful controls are `--change-detection {hybrid,committed,hash}`, `--reconcile`, `--submodules {recursive,ignore}`, and `--lock-timeout-seconds`. `committed` mode requires a clean worktree.

## Direct analyzer configuration

All registered primary analyzers expose these portable arguments:

| Concern | Arguments |
| --- | --- |
| Input and identity | `--root`, `--project-id`, `--project-name`, `--language`, `--repo` |
| Graph selection | `--graph-provider {falkordb,neo4j}` |
| FalkorDB | `--falkordb-uri` or `--falkordb-host`, `--falkordb-port`, `--falkordb-graph` |
| Neo4j | `--neo4j-uri`, `--neo4j-user`, password via `NEO4J_PASS`, optional `--neo4j-db` |
| Qdrant | `--qdrant-url`, `--qdrant-collection`, `--qdrant-batch-size` |
| Embedding | `--embed-model`, `--device`, `--batch-size`, `--max-embed-chars` |
| Operation | `--incremental`, manifest arguments, `--dry-run`, `--verbose` |

The Neo4j password flag differs between some legacy and newer analyzers (`--neo4j-pass` versus `--neo4j-password`). `NEO4J_PASS` works across the primary analyzer set and is preferred for direct Neo4j commands. For a framework overlay that does not inherit these variables, pass its explicit `--neo4j-password` option.

Common environment aliases used by orchestration include:

- `GRAPH_PROVIDER` / `CODE_GRAPH_PROVIDER`
- `FALKORDB_URI`, `FALKORDB_HOST`, `FALKORDB_PORT`, `FALKORDB_GRAPH`
- `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASS`, `NEO4J_DB`
- `QDRANT_URL`, `QDRANT_COLLECTION`, `QDRANT_COLLECTION_CODE`
- `CODE_EMBEDDING_MODEL` / `EMBED_MODEL`
- `EMBED_DEVICE`, `EMBED_BATCH_SIZE`, `MAX_EMBED_CHARS`
- `QDRANT_BATCH_SIZE`, `QDRANT_TIMEOUT`, `QDRANT_RETRIES`, `QDRANT_RETRY_SLEEP`

## Full direct example: FalkorDB + Qdrant

Rust example on Windows PowerShell:

```powershell
python -m tools.rust.rust_analyzer `
  --qdrant-url http://localhost:6333 `
  --qdrant-collection digital_key_rust `
  --embed-model jinaai/jina-embeddings-v3 `
  --device cuda `
  --root "C:\projects\digital_key" `
  --repo digital_key `
  --project-id digital_key `
  --project-name "digital_key" `
  --language rust `
  --batch-size 1 `
  --max-embed-chars 800 `
  --graph-provider falkordb `
  --falkordb-host localhost `
  --falkordb-port 6379 `
  --falkordb-graph digital_key `
  --verbose
```

Equivalent Bash example:

```bash
python -m tools.rust.rust_analyzer \
  --root /projects/digital_key \
  --repo digital_key \
  --project-id digital_key \
  --project-name digital_key \
  --language rust \
  --graph-provider falkordb \
  --falkordb-host localhost \
  --falkordb-port 6379 \
  --falkordb-graph digital_key \
  --qdrant-url http://localhost:6333 \
  --qdrant-collection digital_key_rust \
  --embed-model jinaai/jina-embeddings-v3 \
  --device cpu \
  --batch-size 1 \
  --max-embed-chars 800 \
  --verbose
```

Omit the graph arguments for a Qdrant-only run. Omit the Qdrant arguments for a graph-only run.

## Primary analyzer sample catalog

The following PowerShell variables keep the per-analyzer examples short while remaining executable:

```powershell
$graph = @(
  "--graph-provider", "falkordb",
  "--falkordb-host", "localhost",
  "--falkordb-port", "6379",
  "--falkordb-graph", "digital_key"
)

$vector = @(
  "--qdrant-url", "http://localhost:6333",
  "--embed-model", "jinaai/jina-embeddings-v3",
  "--device", "cuda",
  "--batch-size", "1",
  "--max-embed-chars", "800"
)
```

Use a collection name that identifies the parser:

```powershell
# Android
python -m tools.android.android_kotlin_analyzer @graph @vector --root "C:\projects\digital_key" --project-id digital_key --project-name digital_key --repo digital_key --language android --qdrant-collection digital_key_android --verbose

# COBOL
python -m tools.cobol.cobol_analyzer @graph @vector --root "C:\projects\mainframe" --project-id mainframe --project-name mainframe --repo mainframe --language cobol --qdrant-collection mainframe_cobol --verbose

# C/C++
python -m tools.cplus.cplus_analyzer @graph @vector --root "C:\projects\native_app" --project-id native_app --project-name native_app --repo native_app --language cplus --qdrant-collection native_app_cplus --verbose

# C#
python -m tools.csharp.csharp_analyzer @graph @vector --root "C:\projects\dotnet_app" --project-id dotnet_app --project-name dotnet_app --repo dotnet_app --language csharp --qdrant-collection dotnet_app_csharp --verbose

# Dart primary mode
python -m tools.flutter.flutter_analyzer @graph @vector --mode dart --root "C:\projects\flutter_app" --project-id flutter_app --project-name flutter_app --repo flutter_app --language dart --qdrant-collection flutter_app_dart --verbose

# Delphi / Object Pascal
python -m tools.delphi.delphi_analyzer @graph @vector --root "C:\projects\delphi_app" --project-id delphi_app --project-name delphi_app --repo delphi_app --language delphi --qdrant-collection delphi_app_delphi --verbose

# Go
python -m tools.go.go_analyzer @graph @vector --root "C:\projects\go_service" --project-id go_service --project-name go_service --repo go_service --language go --qdrant-collection go_service_go --verbose

# Java
python -m tools.java.java_analyzer @graph @vector --root "C:\projects\java_app" --project-id java_app --project-name java_app --repo java_app --language java --qdrant-collection java_app_java --verbose

# JavaScript
python -m tools.js.js_analyzer @graph @vector --root "C:\projects\web_app" --project-id web_app --project-name web_app --repo web_app --language js --qdrant-collection web_app_js --verbose

# Kotlin
python -m tools.kotlin.kotlin_analyzer @graph @vector --root "C:\projects\kotlin_app" --project-id kotlin_app --project-name kotlin_app --repo kotlin_app --language kotlin --qdrant-collection kotlin_app_kotlin --verbose

# Perl
python -m tools.perl.perl_analyzer @graph @vector --root "C:\projects\perl_app" --project-id perl_app --project-name perl_app --repo perl_app --language perl --qdrant-collection perl_app_perl --verbose

# PHP
python -m tools.php.php_analyzer @graph @vector --root "C:\projects\php_app" --project-id php_app --project-name php_app --repo php_app --language php --qdrant-collection php_app_php --verbose

# PL/SQL
python -m tools.plsql.plsql_analyzer @graph @vector --root "C:\projects\oracle_db" --project-id oracle_db --project-name oracle_db --repo oracle_db --language plsql --qdrant-collection oracle_db_plsql --verbose

# Python
python -m tools.python.python_analyzer @graph @vector --root "C:\projects\python_app" --project-id python_app --project-name python_app --repo python_app --language python --qdrant-collection python_app_python --verbose

# Rust
python -m tools.rust.rust_analyzer @graph @vector --root "C:\projects\rust_app" --project-id rust_app --project-name rust_app --repo rust_app --language rust --qdrant-collection rust_app_rust --verbose

# SQL
python -m tools.sql.sql_analyzer @graph @vector --root "C:\projects\sql_repo" --project-id sql_repo --project-name sql_repo --repo sql_repo --language sql --qdrant-collection sql_repo_sql --verbose

# Swift
python -m tools.swift.swift_analyzer @graph @vector --root "C:\projects\swift_app" --project-id swift_app --project-name swift_app --repo swift_app --language swift --qdrant-collection swift_app_swift --verbose

# TypeScript / TSX
python -m tools.ts.ts_analyzer @graph @vector --root "C:\projects\typescript_app" --project-id typescript_app --project-name typescript_app --repo typescript_app --language ts --qdrant-collection typescript_app_ts --verbose

# VB.NET
python -m tools.vb.vbnet_analyzer @graph @vector --root "C:\projects\vbnet_app" --project-id vbnet_app --project-name vbnet_app --repo vbnet_app --language vbnet --qdrant-collection vbnet_app_vbnet --verbose

# VB6
python -m tools.vb.vb6_analyzer @graph @vector --root "C:\projects\vb6_app" --project-id vb6_app --project-name vb6_app --repo vb6_app --language vb6 --qdrant-collection vb6_app_vb6 --verbose

# VBA
python -m tools.vb.vba_analyzer @graph @vector --root "C:\projects\vba_app" --project-id vba_app --project-name vba_app --repo vba_app --language vba --qdrant-collection vba_app_vba --verbose

# VBScript / Classic ASP
python -m tools.vb.vbscript_analyzer @graph @vector --root "C:\projects\classic_asp" --project-id classic_asp --project-name classic_asp --repo classic_asp --language vbscript --qdrant-collection classic_asp_vbscript --verbose
```

For CPU execution, replace `"cuda"` with `"cpu"` in `$vector`.

## Framework overlay samples

Overlays intentionally omit Qdrant and embedding arguments. Run their primary parser first, or use `dev sync code` to schedule both automatically.

```powershell
# Spring
python -m tools.spring.spring_analyzer @graph --root "C:\projects\java_app" --project-id java_app --project-name java_app --verbose

# Servlet/JSP
python -m tools.servlet_jsp.servlet_jsp_analyzer @graph --root "C:\projects\java_web" --project-id java_web --project-name java_web --verbose

# MyBatis
python -m tools.mybatis.mybatis_analyzer @graph --root "C:\projects\java_app" --project-id java_app --project-name java_app --verbose

# Apache Struts 2
python -m tools.struts.struts_analyzer @graph --root "C:\projects\struts_app" --project-id struts_app --project-name struts_app --verbose

# Flutter overlay
python -m tools.flutter.flutter_analyzer @graph --mode flutter --root "C:\projects\flutter_app" --project-id flutter_app --project-name flutter_app --verbose

# ASP.NET Core
python -m tools.aspnet_core.aspnet_core_analyzer @graph --root "C:\projects\aspnet_core" --project-id aspnet_core --project-name aspnet_core --verbose

# ASP.NET Framework
python -m tools.aspnet_framework.aspnet_framework_analyzer @graph --root "C:\projects\aspnet_framework" --project-id aspnet_framework --project-name aspnet_framework --verbose
```

## Optional Neo4j compatibility

Set Neo4j environment variables and replace `@graph` with `--graph-provider neo4j`:

```powershell
$env:NEO4J_URI = "bolt://localhost:7687"
$env:NEO4J_USER = "neo4j"
$env:NEO4J_PASS = "your-password"
$env:NEO4J_DB = "neo4j"

python -m tools.rust.rust_analyzer @vector `
  --graph-provider neo4j `
  --root "C:\projects\rust_app" `
  --project-id rust_app `
  --project-name rust_app `
  --repo rust_app `
  --language rust `
  --qdrant-collection rust_app_rust `
  --verbose
```

## Dry runs and graph-only/vector-only runs

Dry-run support varies in how much analysis is performed, but it never writes graph or vector state:

```powershell
python -m tools.go.go_analyzer --root "C:\projects\go_service" --dry-run --verbose
```

Graph-only:

```powershell
python -m tools.go.go_analyzer @graph `
  --root "C:\projects\go_service" `
  --project-id go_service `
  --project-name go_service `
  --repo go_service `
  --language go `
  --verbose
```

Qdrant-only:

```powershell
python -m tools.go.go_analyzer @vector `
  --root "C:\projects\go_service" `
  --project-id go_service `
  --project-name go_service `
  --repo go_service `
  --language go `
  --qdrant-collection go_service_go `
  --verbose
```

## C/C++ unresolved-call diagnostics

After a verbose C/C++ run, the analyzer reports call resolution coverage:

```text
[calls] resolved 63294 / 105170 (60.2%), unresolved 41876
[calls] top unresolved files:
  - Blut01/Blut01App.cpp: 2671 unresolved / 4670 total
```

An unresolved call is a source call expression for which no matching project symbol could be found after the analyzer's resolution passes. Common causes are external/system libraries, complex macros, indirect calls, and virtual dispatch.

Optional diagnostic files:

| Flag | Format | Content |
| --- | --- | --- |
| `--call-stats-path <file>` | JSON | Resolution totals and per-file statistics |
| `--unresolved-calls-path <file>` | JSONL | One record per unresolved call |
| `--possible-calls-path <file>` | JSON | Inheritance-derived `POSSIBLE_CALLS` edges |

```bash
python -m tools.cplus.cplus_analyzer \
  --root /projects/native_app \
  --call-stats-path /tmp/call_stats.json \
  --unresolved-calls-path /tmp/unresolved.jsonl \
  --possible-calls-path /tmp/possible_calls.json \
  --verbose
```

## Unified MCP server

The normal entrypoint is `mcp/unified_mcp.py`. It routes parser profiles to the appropriate backend behind one endpoint.

```bash
python mcp/unified_mcp.py \
  --transport streamable-http \
  --host 127.0.0.1 \
  --port 8788 \
  --path /mcp
```

MCP client configuration:

```json
{
  "graph_mcp": {
    "url": "http://127.0.0.1:8788/mcp",
    "type": "http",
    "allowWriteAccess": true
  }
}
```

Start with `list_parsers`, then activate a default parser/database or pass `parser_type` per request:

```json
{
  "tool": "activate_project",
  "parser_type": "rust",
  "database_name": "digital_key"
}
```

Semantic search with graph expansion:

```json
{
  "tool": "semantic_search",
  "parser_type": "rust",
  "project_id": "digital_key",
  "collection": "digital_key_rust",
  "query": "authentication token validation",
  "top_k": 10,
  "expand_graph": true,
  "graph_depth": 2
}
```

Use `list_qdrant_collections` to inspect available collections. For detailed MCP tools, routing, transport, and response contracts, see [`mcp/README.md`](mcp/README.md).

## CUDA check

`--device cuda` requires a CUDA-enabled PyTorch build and a compatible GPU/driver:

```powershell
python -c "import torch; print('torch', torch.__version__); print('cuda', torch.version.cuda); print('cuda_available', torch.cuda.is_available()); print('device_count', torch.cuda.device_count())"
```

If `cuda_available` is `False`, use `--device cpu` or install the PyTorch build appropriate for your CUDA runtime from the official PyTorch installation guide.

## Additional notes

- Some analyzers require language-specific Tree-sitter wheels or external semantic workers. Install the complete `requirements.txt` and check the analyzer-specific README under `tools/<parser>/` when present.
- Qdrant dependencies are loaded lazily for the shared primary vector adapter, so graph-only runs do not require vector services.
- Configured persistence failures return non-zero; do not treat a failed analyzer as a clean incremental baseline.
- Caches and incremental state live under analyzer/project cache directories. Use `--ignore-cache` when validating a clean rescan.
