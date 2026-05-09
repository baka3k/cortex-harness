# Project Call Graph MCP (Tiny)

Small repository containing an MCP server (FastMCP) that proxies to the Project Call Graph backend, plus Kotlin/Java/C#/C++/TypeScript/JavaScript/PHP/SQL/PL/SQL analyzers.
The backend API/scan service is not in this repo and must run elsewhere.

Supported languages:

- C#
- C/C++
- Java
- JavaScript
- Kotlin
- PHP
- PL/SQL
- SQL
- TypeScript

## Main components

- MCP server: `mcp/fastmcp_server.py` (FastMCP proxy to backend).
- MCP support services: `mcp/services/graph_service.py`, `mcp/services/impact_service.py`, `mcp/services/symbol_service.py`.
- Analyzers (Tree-sitter + Neo4j + Qdrant):
  - Kotlin: `tools/kotlin/kotlin_analyzer.py`
  - Java: `tools/java/java_analyzer.py`
  - TypeScript: `tools/ts/ts_analyzer.py`
  - JavaScript: `tools/js/js_analyzer.py`
  - PHP: `tools/php/php_analyzer.py`
  - SQL: `tools/sql/sql_analyzer.py`
  - PL/SQL: `tools/plsql/plsql_analyzer.py`
  - C/C++: `tools/cplus/cplus_analyzer.py`
  - C#: `tools/csharp/csharp_analyzer.py`

## Setup

```bash
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
```

## Run MCP server

Default backend URL: `http://127.0.0.1:8000` (env `MCP_PROXY_BASE_URL`).

```bash
python mcp/fastmcp_server.py --backend-url http://127.0.0.1:8000 --host 127.0.0.1 --port 8788 --path /mcp
```

Common environment variables:

- `MCP_PROXY_BASE_URL`, `MCP_BACKEND_TIMEOUT`
- `MCP_FASTMCP_HOST`, `MCP_FASTMCP_PORT`, `MCP_FASTMCP_PATH`
- `MCP_FASTMCP_TRANSPORT` (default: `streamable-http`)

MCP client config:

```json
"graph_mcp": {
  "url": "http://127.0.0.1:8788/mcp",
  "type": "http",
  "allowWriteAccess": true
}
```

## activate_project

`activate_project` only stores defaults for:

- `parser_type` (optional)
- `database_name` (optional; accepts a DB name or path, will normalize + validate)

At least one of the two must be provided.

## MCP response content fields

Most MCP tools accept:

- `content_mode`: choose what goes into `properties.content`.
  - `auto` (default): summary -> comment -> name fallback
  - `summary`, `comment`, `code`, `name`
- `include_raw_fields`: when `true`, keep `summary/comment/code` fields in the response payload. Default `false`.

When `include_raw_fields=false`, the response will only include `properties.content` (plus metadata) to reduce payload size.

Example tool calls:

```json
// Default: content auto fallback (summary -> comment -> name)
{ "tool": "get_symbol", "node_id": "pkg.Class/method/1@src/File.java" }
```

```json
// Force code in content, but keep raw fields too
{
  "tool": "query_subgraph",
  "db": "neo4j",
  "function_id": "pkg.Class/method/1@src/File.java",
  "content_mode": "code",
  "include_raw_fields": true
}
```

Android MCP payload format (payload supported, top-level args also accepted):

```json
{
  "tool": "query_subgraph",
  "payload": {
    "db": "neo4j",
    "function_id": "pkg.Class/method/1@src/File.java",
    "content_mode": "code",
    "include_raw_fields": true
  }
}
```

Android MCP tools accept `payload` or flat arguments; when both are provided, `payload` takes precedence.

```json
// Semantic search with content from summary only
{
  "tool": "semantic_search",
  "query": "authentication token",
  "content_mode": "summary"
}
```

```json
// Semantic search with explicit collections
{
  "tool": "semantic_search",
  "query": "authentication token",
  "collection": ["kotlin_functions", "java_functions"],
  "content_mode": "summary"
}
```

Note: If `collection` is not provided, `semantic_search` will query Qdrant for all collections and search across them. Use `list_qdrant_collections` first to see available collections. Pass `include_vectors=true` to also return vector sizes so you can match your embedding model. You can pass multiple collections via `collection` (comma-separated or list) and the tool will merge results.

```json
// List entrypoints into a module from external callers
{
  "tool": "list_up_entrypoint",
  "modules": ["src/app", "src/service"],
  "content_mode": "summary",
  "include_raw_fields": true
}
```

## Kotlin analysis

Script: `tools/kotlin/kotlin_analyzer.py`

Environment variables:

- `NEO4J_URI`: Neo4j Bolt URI (e.g., `bolt://localhost:7687`).
- `NEO4J_USER`: Neo4j username.
- `NEO4J_PASSWORD`: Neo4j password.
- `NEO4J_DB`: Neo4j database name (optional).
- `QDRANT_URL`: Qdrant base URL (e.g., `http://localhost:6333`).
- `QDRANT_COLLECTION`: Qdrant collection name (default: `kotlin_functions`).
- `EMBED_MODEL`: embedding model id or local path (default: `jinaai/jina-embeddings-v3`).
- `JINA_MODEL_PATH`: optional local path used when `EMBED_MODEL` is unset.
- `EMBED_DEVICE`: embedding device (default: `cpu`; e.g., `cuda`).
  Note: Qdrant collection vector size is inferred from the embed model; if the collection exists, its vector size must match or recreate the collection.

Example:

```bash
python tools/kotlin/kotlin_analyzer.py \
  --root C:\\path\\to\\kotlin \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password password \
  --qdrant-url http://localhost:6333 \
  --qdrant-collection kotlin_functions \
  --embed-model jinaai/jina-embeddings-v3 \
  --device cpu \
  --qdrant-timeout 300 \
  --qdrant-retries 3 \
  --qdrant-retry-sleep 2 \
  --batch-size 1 \
  --verbose
```

Dry run (count Kotlin files only):

```bash
python tools/kotlin/kotlin_analyzer.py --root C:\\path\\to\\kotlin --dry-run
```

## Java analysis

Script: `tools/java/java_analyzer.py`

Environment variables:

- `NEO4J_URI`: Neo4j Bolt URI (e.g., `bolt://localhost:7687`).
- `NEO4J_USER`: Neo4j username.
- `NEO4J_PASSWORD`: Neo4j password.
- `NEO4J_DB`: Neo4j database name (optional).
- `QDRANT_URL`: Qdrant base URL (e.g., `http://localhost:6333`).
- `QDRANT_COLLECTION`: Qdrant collection name (default: `java_functions`).
- `EMBED_MODEL`: embedding model id or local path (default: `jinaai/jina-embeddings-v3`).
- `JINA_MODEL_PATH`: optional local path used when `EMBED_MODEL` is unset.
- `EMBED_DEVICE`: embedding device (default: `cpu`; e.g., `cuda`).
  Note: Qdrant collection vector size is inferred from the embed model; if the collection exists, its vector size must match or recreate the collection.

Example:

```bash
python tools/java/java_analyzer.py \
  --root C:\\path\\to\\java \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password password \
  --qdrant-url http://localhost:6333 \
  --qdrant-collection java_functions \
  --embed-model jinaai/jina-embeddings-v3 \
  --device cpu \
  --qdrant-timeout 300 \
  --qdrant-retries 3 \
  --qdrant-retry-sleep 2 \
  --batch-size 1 \
  --verbose
```

Dry run (count Java files only):

```bash
python tools/java/java_analyzer.py --root C:\\path\\to\\java --dry-run
```

## TypeScript analysis

Script: `tools/ts/ts_analyzer.py`

Environment variables:

- `NEO4J_URI`: Neo4j Bolt URI (e.g., `bolt://localhost:7687`).
- `NEO4J_USER`: Neo4j username.
- `NEO4J_PASSWORD`: Neo4j password.
- `NEO4J_DB`: Neo4j database name (optional).
- `QDRANT_URL`: Qdrant base URL (e.g., `http://localhost:6333`).
- `QDRANT_COLLECTION`: Qdrant collection name (default: `typescript_functions`).
- `EMBED_MODEL`: embedding model id or local path (default: `jinaai/jina-embeddings-v3`).
- `JINA_MODEL_PATH`: optional local path used when `EMBED_MODEL` is unset.
- `EMBED_DEVICE`: embedding device (default: `auto`; e.g., `cuda`).
  Note: Qdrant collection vector size is inferred from the embed model; if the collection exists, its vector size must match or recreate the collection.

Note: install Tree-sitter parser for TypeScript/TSX (`tree-sitter-typescript`) or use `tree-sitter-languages`.

Example:

```bash
python tools/ts/ts_analyzer.py \
  --root C:\\path\\to\\typescript \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password password \
  --qdrant-url http://localhost:6333 \
  --qdrant-collection typescript_functions \
  --embed-model jinaai/jina-embeddings-v3 \
  --device auto \
  --qdrant-timeout 300 \
  --qdrant-retries 3 \
  --qdrant-retry-sleep 2 \
  --batch-size 1 \
  --verbose
```

Dry run (count TypeScript files only):

```bash
python tools/ts/ts_analyzer.py --root C:\\path\\to\\typescript --dry-run
```

## JavaScript analysis

Script: `tools/js/js_analyzer.py`

Environment variables:

- `NEO4J_URI`: Neo4j Bolt URI (e.g., `bolt://localhost:7687`).
- `NEO4J_USER`: Neo4j username.
- `NEO4J_PASSWORD`: Neo4j password.
- `NEO4J_DB`: Neo4j database name (optional).
- `QDRANT_URL`: Qdrant base URL (e.g., `http://localhost:6333`).
- `QDRANT_COLLECTION`: Qdrant collection name (default: `javascript_functions`).
- `EMBED_MODEL`: embedding model id or local path (default: `jinaai/jina-embeddings-v3`).
- `JINA_MODEL_PATH`: optional local path used when `EMBED_MODEL` is unset.
- `EMBED_DEVICE`: embedding device (default: `auto`; e.g., `cuda`).
  Note: Qdrant collection vector size is inferred from the embed model; if the collection exists, its vector size must match or recreate the collection.

Note: install Tree-sitter parser for JavaScript/JSX (`tree-sitter-javascript`) or use `tree-sitter-languages`.

Example:

```bash
python tools/js/js_analyzer.py \
  --root C:\\path\\to\\javascript \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password password \
  --qdrant-url http://localhost:6333 \
  --qdrant-collection javascript_functions \
  --embed-model jinaai/jina-embeddings-v3 \
  --device auto \
  --qdrant-timeout 300 \
  --qdrant-retries 3 \
  --qdrant-retry-sleep 2 \
  --batch-size 1 \
  --verbose
```

Dry run (count JavaScript files only):

```bash
python tools/js/js_analyzer.py --root C:\\path\\to\\javascript --dry-run
```

## PHP analysis

Script: `tools/php/php_analyzer.py`

Environment variables:

- `NEO4J_URI`: Neo4j Bolt URI (e.g., `bolt://localhost:7687`).
- `NEO4J_USER`: Neo4j username.
- `NEO4J_PASSWORD`: Neo4j password.
- `NEO4J_DB`: Neo4j database name (optional).
- `QDRANT_URL`: Qdrant base URL (e.g., `http://localhost:6333`).
- `QDRANT_COLLECTION`: Qdrant collection name (default: `php_functions`).
- `EMBED_MODEL`: embedding model id or local path (default: `jinaai/jina-embeddings-v3`).
- `JINA_MODEL_PATH`: optional local path used when `EMBED_MODEL` is unset.
- `EMBED_DEVICE`: embedding device (default: `auto`; e.g., `cuda`).
  Note: Qdrant collection vector size is inferred from the embed model; if the collection exists, its vector size must match or recreate the collection.

Note: install Tree-sitter parser for PHP (`tree-sitter-php`) or use `tree-sitter-languages`.

Example:

```bash
python tools/php/php_analyzer.py \
  --root C:\\path\\to\\php \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password password \
  --qdrant-url http://localhost:6333 \
  --qdrant-collection php_functions \
  --embed-model jinaai/jina-embeddings-v3 \
  --device auto \
  --qdrant-timeout 300 \
  --qdrant-retries 3 \
  --qdrant-retry-sleep 2 \
  --batch-size 1 \
  --verbose
```

Dry run (count PHP files only):

```bash
python tools/php/php_analyzer.py --root C:\\path\\to\\php --dry-run
```

## SQL analysis

Script: `tools/sql/sql_analyzer.py`

Environment variables:

- `NEO4J_URI`: Neo4j Bolt URI (e.g., `bolt://localhost:7687`).
- `NEO4J_USER`: Neo4j username.
- `NEO4J_PASSWORD`: Neo4j password.
- `NEO4J_DB`: Neo4j database name (optional).
- `QDRANT_URL`: Qdrant base URL (e.g., `http://localhost:6333`).
- `QDRANT_COLLECTION`: Qdrant collection name (default: `sql_functions`).
- `EMBED_MODEL`: embedding model id or local path (default: `jinaai/jina-embeddings-v3`).
- `JINA_MODEL_PATH`: optional local path used when `EMBED_MODEL` is unset.
- `EMBED_DEVICE`: embedding device (default: `auto`; e.g., `cuda`).
  Note: Qdrant collection vector size is inferred from the embed model; if the collection exists, its vector size must match or recreate the collection.

Note: install Tree-sitter parser for SQL (`tree-sitter-sql`) or use `tree-sitter-languages`.

Example:

```bash
python tools/sql/sql_analyzer.py \
  --root C:\\path\\to\\sql \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password password \
  --qdrant-url http://localhost:6333 \
  --qdrant-collection sql_functions \
  --embed-model jinaai/jina-embeddings-v3 \
  --device auto \
  --qdrant-timeout 300 \
  --qdrant-retries 3 \
  --qdrant-retry-sleep 2 \
  --batch-size 1 \
  --verbose
```

Dry run (count SQL files only):

```bash
python tools/sql/sql_analyzer.py --root C:\\path\\to\\sql --dry-run
```

## PL/SQL analysis

Script: `tools/plsql/plsql_analyzer.py`

Environment variables:

- `NEO4J_URI`: Neo4j Bolt URI (e.g., `bolt://localhost:7687`).
- `NEO4J_USER`: Neo4j username.
- `NEO4J_PASSWORD`: Neo4j password.
- `NEO4J_DB`: Neo4j database name (optional).
- `QDRANT_URL`: Qdrant base URL (e.g., `http://localhost:6333`).
- `QDRANT_COLLECTION`: Qdrant collection name (default: `plsql_functions`).
- `EMBED_MODEL`: embedding model id or local path (default: `jinaai/jina-embeddings-v3`).
- `JINA_MODEL_PATH`: optional local path used when `EMBED_MODEL` is unset.
- `EMBED_DEVICE`: embedding device (default: `auto`; e.g., `cuda`).
  Note: Qdrant collection vector size is inferred from the embed model; if the collection exists, its vector size must match or recreate the collection.

Note: PL/SQL analyzer uses regex heuristics (no tree-sitter dependency).

Example:

```bash
python tools/plsql/plsql_analyzer.py \
  --root C:\\path\\to\\plsql \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password password \
  --qdrant-url http://localhost:6333 \
  --qdrant-collection plsql_functions \
  --embed-model jinaai/jina-embeddings-v3 \
  --device auto \
  --qdrant-timeout 300 \
  --qdrant-retries 3 \
  --qdrant-retry-sleep 2 \
  --batch-size 1 \
  --verbose
```

Dry run (count PL/SQL files only):

```bash
python tools/plsql/plsql_analyzer.py --root C:\\path\\to\\plsql --dry-run
```

## C/C++ analysis

Script: `tools/cplus/cplus_analyzer.py`

Environment variables:

- `NEO4J_URI`: Neo4j Bolt URI (e.g., `bolt://localhost:7687`).
- `NEO4J_USER`: Neo4j username.
- `NEO4J_PASSWORD`: Neo4j password.
- `NEO4J_DB`: Neo4j database name (optional).
- `QDRANT_URL`: Qdrant base URL (e.g., `http://localhost:6333`).
- `QDRANT_COLLECTION`: Qdrant collection name (default: `cplus_functions`).
- `EMBED_MODEL`: embedding model id or local path (default: `jinaai/jina-embeddings-v3`).
- `JINA_MODEL_PATH`: optional local path used when `EMBED_MODEL` is unset.
- `EMBED_DEVICE`: embedding device (default: `cpu`; e.g., `cuda`).
  Note: Qdrant collection vector size is inferred from the embed model; if the collection exists, its vector size must match or recreate the collection.

Note: install Tree-sitter parsers for C/C++ (`tree-sitter-c`, `tree-sitter-cpp`) or use `tree-sitter-languages`.

Example:

```bash
python tools/cplus/cplus_analyzer.py \
  --root C:\\path\\to\\cplus \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password password \
  --qdrant-url http://localhost:6333 \
  --qdrant-collection cplus_functions \
  --embed-model jinaai/jina-embeddings-v3 \
  --device cpu \
  --qdrant-timeout 300 \
  --qdrant-retries 3 \
  --qdrant-retry-sleep 2 \
  --batch-size 1 \
  --verbose
```

Dry run (count C/C++ files only):

```bash
python tools/cplus/cplus_analyzer.py --root C:\\path\\to\\cplus --dry-run
```

### Unresolved calls

After a run with `--verbose`, the analyzer prints a call resolution report:

```
[calls] resolved 63294 / 105170 (60.2%), unresolved 41876
[calls] top unresolved files:
  - Blut01/Blut01App.cpp: 2671 unresolved / 4670 total
```

**What "unresolved" means:** a call expression in the source code for which no matching `symbol_id` could be found after an 8-pass resolution strategy (qualified name + arity → qualified name → file-local scope → `using namespace` expansion → scope chain → global name fallback). Common causes:

- Calls into external / system libraries (e.g., Win32 API, MFC, STL internals, third-party SDKs) whose source is not under `--root`.
- Complex macros that expand to names not present in the index.
- Indirect / virtual dispatch resolved at runtime.

**Default behavior:** unresolved statistics are printed to stdout only; nothing is written to disk.

**Optional output flags:**

| Flag                             | Output | Description                                                                                                                                                |
| -------------------------------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--call-stats-path <file>`       | JSON   | Per-file and totals: `total_calls`, `resolved_calls`, `unresolved_calls`, `resolved_ratio`, `macro_resolved_calls`, `possible_calls_written`, `by_file[]`. |
| `--unresolved-calls-path <file>` | JSONL  | One JSON object per unresolved call: `caller_id`, `file_path`, `line`, `callee_name`, `macro_expansion`.                                                   |
| `--possible-calls-path <file>`   | JSON   | `POSSIBLE_CALLS` edges inferred from inheritance (base method → overriding method).                                                                        |

Example – save all diagnostic output:

```bash
python tools/cplus/cplus_analyzer.py \
  --root /path/to/project \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password password \
  --call-stats-path /tmp/call_stats.json \
  --unresolved-calls-path /tmp/unresolved.jsonl \
  --possible-calls-path /tmp/possible_calls.json \
  --verbose
```

Quick inspection after the run:

```bash
# How many unresolved calls?
python3 -c "import json; d=json.load(open('/tmp/call_stats.json')); print(d['unresolved_calls'], '/', d['total_calls'])"

# Top 10 unresolved callee names
python3 -c "
import sys
from collections import Counter
c = Counter(json.loads(l)['callee_name'] for l in open('/tmp/unresolved.jsonl'))
for name, n in c.most_common(10): print(n, name)
" 2>/dev/null || python3 -c "
import json
from collections import Counter
c = Counter(json.loads(l)['callee_name'] for l in open('/tmp/unresolved.jsonl'))
for name, n in c.most_common(10): print(n, name)
"
```

## C# analysis

Script: `tools/csharp/csharp_analyzer.py`

Environment variables:

- `NEO4J_URI`: Neo4j Bolt URI (e.g., `bolt://localhost:7687`).
- `NEO4J_USER`: Neo4j username.
- `NEO4J_PASSWORD`: Neo4j password.
- `NEO4J_DB`: Neo4j database name (optional).
- `QDRANT_URL`: Qdrant base URL (e.g., `http://localhost:6333`).
- `QDRANT_COLLECTION`: Qdrant collection name (default: `csharp_functions`).
- `EMBED_MODEL`: embedding model id or local path (default: `jinaai/jina-embeddings-v3`).
- `JINA_MODEL_PATH`: optional local path used when `EMBED_MODEL` is unset.
- `EMBED_DEVICE`: embedding device (default: `cpu`; e.g., `cuda`).
  Note: Qdrant collection vector size is inferred from the embed model; if the collection exists, its vector size must match or recreate the collection.

Note: install Tree-sitter parser for C# (`tree-sitter-c-sharp`) or use `tree-sitter-languages`.

Example:

```bash
python tools/csharp/csharp_analyzer.py \
  --root C:\\path\\to\\csharp \
  --neo4j-uri bolt://localhost:7687 \
  --neo4j-user neo4j \
  --neo4j-password password \
  --qdrant-url http://localhost:6333 \
  --qdrant-collection csharp_functions \
  --embed-model jinaai/jina-embeddings-v3 \
  --device cpu \
  --qdrant-timeout 300 \
  --qdrant-retries 3 \
  --qdrant-retry-sleep 2 \
  --batch-size 1 \
  --verbose
```

Dry run (count C# files only):

```bash
python tools/csharp/csharp_analyzer.py --root C:\\path\\to\\csharp --dry-run
```

## Caching and resume

All analyzers support caching and resume to handle large codebases:

- Parse cache (per-file): enabled by default; disable with `--disable-parse-cache`.
- Neo4j batch + resume: uses state file (default under `.cache/<analyzer>/neo4j_state.json`); disable with `--disable-neo4j-resume`.
- Qdrant resume: caches embeddings + upsert state; keep cache with `--keep-cache`.

Common tuning flags:

- `--cache-dir` (base cache folder)
- `--batch-size` (embedding batch)
- `--embed-model` (embedding model id or local path)
- `--max-embed-chars` (max chars per text before embedding; default 4000)
- `--chunk-embed` (split long texts into chunks and mean-pool vectors)
- `--device` (embedding device, e.g., `cpu` or `cuda`)
- `--qdrant-collection` (collection name to upsert points into)
- `--qdrant-url` (Qdrant base URL, e.g., `http://localhost:6333`)
- `--qdrant-batch-size` (points per upsert)
- `--qdrant-timeout` (seconds per request)
- `--qdrant-retries` (retry count)
- `--qdrant-retry-sleep` (seconds between retries)
- `--neo4j-uri` (Neo4j Bolt URI, e.g., `bolt://localhost:7687`)
- `--neo4j-user` (Neo4j username)
- `--neo4j-password` (Neo4j password)
- `--neo4j-db` (Neo4j database name)
- `--neo4j-batch-size`

## CLOC stats (optional)

If `cloc` is available and Neo4j is configured, analyzers run a pre-process step to collect code statistics and store them in Neo4j.
The node label is `CodebaseStats` (keyed by `project_id`) with totals (`total_files`, `total_blank`, `total_comment`, `total_code`),
plus `languages_json` for per-language breakdown, and metadata such as `cloc_version` and `generated_at`.
If `cloc` is missing or fails, this step is skipped.

## Notes

- The MCP server requires the Project Call Graph backend running to query call graphs.
- `semantic_search` in the MCP server requires `tools/cplus/qdrant_semantic_search.py` and Qdrant (script not included in this repo).

## NOTICE

Check GPU support CUDA CUDA

```
 python -c "import torch; print('torch', torch.__version__); print('cuda', torch.version.cuda); print('cuda_available', torch.cuda.is_available()); print('device_count', torch.cuda.device_count())"
```

If you see

```
cuda 12.8
cuda_available True
```

Install

```
python.exe -m pip install --upgrade pip
pip uninstall -y torch torchvision torchaudio
pip install --index-url https://download.pytorch.org/whl/cu124 torch torchvision torchaudio
```

How to fix Fooocus for RTX 5000 blackwell (windows)
https://github.com/lllyasviel/Fooocus/discussions/4002#discussion-8353109
