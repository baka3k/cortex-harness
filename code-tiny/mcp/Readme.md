# Graph MCP Usage Guide

`graph_mcp` is a read-and-analysis interface for an indexed codebase. It lets an
AI client ask questions such as:

- "Where is login implemented?"
- "Which functions call this function?"
- "How does this screen reach the backend and database?"
- "Which workflows may break if I edit this symbol?"
- "In what order should these modules be migrated?"

The default local runtime reads code-graph data from FalkorDB and semantic
embeddings from Qdrant. Neo4j remains available as a compatibility provider.
Most tools are read-only. `annotate_node` is the notable write operation
because it adds or updates graph annotations.

The normal entry point is `unified_mcp.py` in this directory. It exposes one
MCP server named `graph_mcp` and routes each call to the appropriate backend.
The current unified server exposes **39 tools**: 15 registered directly on the
unified server, 19 proxied to the parser-routed graph backends, and 5
dependency-planning tools proxied to the planning backend.

Core input contract (this supersedes older guidance):

- **Every call passes `parser_type` and `project_id` explicitly.** There is no
  `activate_project` tool and no session-level parser/database state.
- **`project_id` is the only scope key.** It resolves, through the Project
  Registry, to the FalkorDB graph shard and the Qdrant collection for that
  project. There is no separate `database_name`/`db` public parameter (`db` is
  accepted only as a legacy alias that is merged into `project_id`).
- **Omitting `project_id` means "query every registered project"** (unscoped
  fan-out). Omitting `parser_type` on a search tool fans the query out across
  all query engines and merges the results.

See `docs/PROJECT_REGISTRY.md` and `docs/UNIFIED_INGEST_QUERY_CONTRACT.md` at
the repository root for the authoritative contract documents.

## How To Use This Guide

Use the shortest route that matches what you already know:

| You need                                               | Go to                                           |
| ------------------------------------------------------ | ----------------------------------------------- |
| Start the server and make a first call                 | [Quick Start](#quick-start)                     |
| Pick the correct tool for a question                   | [Choose A Tool By Task](#choose-a-tool-by-task) |
| Look up a tool by name                                 | [Complete Tool Index](#complete-tool-index)     |
| Understand `project_id`, scope, and fan-out            | [The project_id Contract](#the-project_id-contract) |
| Understand common response shapes                      | [Output Guide](#output-guide)                   |
| See every field for one tool                           | [Tool Reference](#tool-reference)               |
| Follow copy-ready multi-step examples                  | [Practical Recipes](#practical-recipes)         |
| Diagnose empty results or connection errors            | [Troubleshooting](#troubleshooting)             |

## Choose A Tool By Task

Start from the question you want to answer. The "next step" column shows the
most common follow-up call.

| Question or task                                                      | Start with                                                                                              | Next step                               |
| --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- | --------------------------------------- |
| I only know the behavior or business concept                          | [`semantic_search`](#semantic_search) or [`explore_graph`](#explore_graph) | `get_symbol` or `query_subgraph`        |
| I want code that is semantically similar to a description             | [`semantic_search`](#semantic_search)                                                                   | `get_symbol`                            |
| I know part of a function, class, or type name                        | [`search_functions`](#search_functions)                                                                 | `get_symbol`                            |
| I know a literal, SQL fragment, API call, or log message              | [`search_by_code`](#search_by_code)                                                                     | `get_symbol`                            |
| I know the file path but not its symbols                              | [`listup_symbols_matching_file_path`](#listup_symbols_matching_file_path)                               | `get_symbol`                            |
| I know a class and want all of its methods                            | [`listup_class_matching_path`](#listup_class_matching_path)                                             | `get_symbol`                            |
| I need full source and metadata for one symbol ID                     | [`get_symbol`](#get_symbol)                                                                             | `query_subgraph`                        |
| I need details for several symbol IDs                                 | [`get_node_details`](#get_node_details)                                                                 | Review returned nodes                   |
| I need callers and callees around one function                        | [`query_subgraph`](#query_subgraph)                                                                     | `get_node_details`                      |
| I need proof that function A reaches function B                       | [`find_paths`](#find_paths)                                                                             | `reconstruct_flow`                      |
| I need a path between two modules                                     | [`find_path_between_module`](#find_path_between_module)                                                 | `trace_flow_between_module`             |
| I need to identify a module's externally called entry points          | [`list_up_entrypoint`](#list_up_entrypoint)                                                             | `query_subgraph`                        |
| I need callbacks, virtual calls, or function-pointer candidates       | [`list_possible_calls`](#list_possible_calls)                                                           | `get_node_details`                      |
| I need all downstream or upstream flows from one node                 | [`trace_flow`](#trace_flow)                                                                             | `reconstruct_flow`                      |
| I need screen-to-screen navigation paths                              | [`find_screen_workflows`](#find_screen_workflows)                                                       | `analyze_workflow_impact`               |
| I need to know which workflows contain a function                     | [`find_workflows_containing`](#find_workflows_containing)                                               | `analyze_workflow_impact`               |
| I need a risk score before changing a function                        | [`analyze_workflow_impact`](#analyze_workflow_impact)                                                   | Use recommendation to define tests      |
| I start from an API endpoint and need frontend callers                | [`find_callers_of_endpoint`](#find_callers_of_endpoint)                                                 | `get_api_call_chain`                    |
| I need screen-to-database fullstack trace                             | [`get_api_call_chain`](#get_api_call_chain)                                                             | Inspect returned symbols                |
| I need sender/receiver IPC relationships                              | [`get_ipc_message`](#get_ipc_message)                                                                   | `trace_flow`                            |
| I need dependency cycles in supplied nodes/edges                      | [`compute_scc`](#compute_scc)                                                                           | `topological_sort`                      |
| I need a linear order or parallel waves for supplied nodes/edges      | [`topological_sort`](#topological_sort)                                                                 | Execute returned waves                  |
| I need module/file/function order directly from indexed `CALLS` edges | [`plan_dependency_order`](#plan_dependency_order)                                                       | File/function planner                   |
| I need canonical modules, public APIs, or endpoints                   | [`get_project_modules`](#get_project_modules)                                                           | `get_public_apis` / `get_endpoints`     |
| I need an aggregate architecture summary or special files             | [`get_module_architecture_summary`](#get_module_architecture_summary)                                   | `get_project_special_files`             |

## Complete Tool Index

The unified server advertises exactly 39 tools. `list_mcp_functions` returns
the live catalog; this index mirrors it.

### Session And Discovery

| Tool | Main purpose | Minimum useful input |
| --- | --- | --- |
| [`list_mcp_functions`](#list_mcp_functions) | Return the live catalog of tools and schemas | `{}` |
| [`list_parsers`](#list_parsers) | Show parser aliases and capability profiles | `{}` |
| [`inspect_parser_capabilities`](#inspect_parser_capabilities) | Compare advertised vs observed graph schema support | optional `parser_type` |
| [`list_databases`](#list_databases) | List graph shards visible to the backend | optional `parser_type` |
| [`list_qdrant_collections`](#list_qdrant_collections) | List semantic-search collections | optional `parser_type` |

### Search And Discovery

| Tool | Main purpose | Minimum useful input |
| --- | --- | --- |
| [`explore_graph`](#explore_graph) | Natural-language hybrid search plus graph context | `query` |
| [`semantic_search`](#semantic_search) | Vector similarity search with optional graph expansion | `query` |
| [`search_functions`](#search_functions) | Search symbol names and qualified names | `query` |
| [`search_by_code`](#search_by_code) | Search implementation text and literals | `query` |

### Symbols And Call Graph

| Tool | Main purpose | Minimum useful input |
| --- | --- | --- |
| [`get_symbol`](#get_symbol) | Fetch one symbol by node ID | `node_id` |
| [`get_node_details`](#get_node_details) | Fetch several nodes in one call | `node_ids` |
| [`query_subgraph`](#query_subgraph) | Get callers/callees around a function | `function_id` |
| [`find_paths`](#find_paths) | Find paths between two functions | `start_function_id`, `end_function_id` |
| [`find_path_between_module`](#find_path_between_module) | Find paths between module/file tokens | `source_module`, `target_module` |
| [`listup_symbols_matching_file_path`](#listup_symbols_matching_file_path) | Inventory symbols by path token | `file_path` or `modules` |
| [`listup_class_matching_path`](#listup_class_matching_path) | List methods for a class/type name | `class_name` |
| [`list_up_entrypoint`](#list_up_entrypoint) | Find functions called from outside a module | `modules` |
| [`list_possible_calls`](#list_possible_calls) | Find indirect/dynamic call candidates | optional `function_id` |
| [`annotate_node`](#annotate_node) | Write notes and tags to a graph node | `node_id` |

### Flows And Dependency Planning

| Tool | Main purpose | Minimum useful input |
| --- | --- | --- |
| [`trace_flow`](#trace_flow) | Trace inbound/outbound flow from one node | `start_id` |
| [`trace_flow_between_module`](#trace_flow_between_module) | Trace flow between module tokens | `source_module`, `target_module` |
| [`reconstruct_flow`](#reconstruct_flow) | Turn raw paths into ordered explainable flows | `entry_context_json`, `paths_json` |
| [`compute_scc`](#compute_scc) | Detect strongly connected components/cycles | `nodes`, `edges` |
| [`topological_sort`](#topological_sort) | Produce dependency order and execution waves | `nodes`, `edges` |
| [`plan_dependency_order`](#plan_dependency_order) | Plan module order from indexed calls | `modules` |
| [`plan_file_dependency_order`](#plan_file_dependency_order) | Plan file order inside modules | `modules` |
| [`plan_function_dependency_order`](#plan_function_dependency_order) | Plan function order inside modules | `modules` |

### Workflows, IPC, And Fullstack

| Tool | Main purpose | Minimum useful input |
| --- | --- | --- |
| [`find_screen_workflows`](#find_screen_workflows) | Discover screen navigation workflows | `project_id`, `node_a` |
| [`find_workflows_containing`](#find_workflows_containing) | Find workflows that contain a function | `function_id` |
| [`analyze_workflow_impact`](#analyze_workflow_impact) | Score workflow impact of a function change | `function_id` |
| [`get_ipc_message`](#get_ipc_message) | Query sender/receiver IPC messages | `sender` or `receiver` |
| [`find_callers_of_endpoint`](#find_callers_of_endpoint) | Find frontend callers of a backend endpoint | `endpoint_path` |
| [`get_api_call_chain`](#get_api_call_chain) | Trace component/endpoint through backend layers | `component_name` or `endpoint_path` |

### Project Context

| Tool | Main purpose | Minimum useful input |
| --- | --- | --- |
| [`get_project_modules`](#get_project_modules) | Canonical modules, descriptors, and internal/external dependencies | `project_id` |
| [`get_public_apis`](#get_public_apis) | Strict source-level public/exported declarations | `project_id` |
| [`get_endpoints`](#get_endpoints) | Normalized HTTP, route, service, page, and gRPC endpoint inventory | `project_id` |
| [`get_module_architecture_summary`](#get_module_architecture_summary) | Counts and bounded samples from the indexed graph; no filesystem rescan | `project_id` plus `module_id` or `all_modules=true` |
| [`get_project_special_files`](#get_project_special_files) | Descriptor roles, parse depth, diagnostics, and redaction-safe summaries | `project_id` |
| [`get_framework_context`](#get_framework_context) | Framework instances and independently reported context dimensions | `project_id` |

All six project-context tools use deterministic ordering and limits and return
`capability_diagnostics` when the active provider schema lacks required labels
or relationships. They fan out per project graph directly, so an omitted
`parser_type` already searches every parser's data for them.

### Removed Tools

The following tools existed in earlier releases and are **no longer exposed**:
`activate_project` (session state was removed; pass `parser_type` +
`project_id` on every call) and the entire `livingdoc_*` family
(`livingdoc_get_links_by_anchor`, `livingdoc_get_links_for_symbol`,
`livingdoc_get_links_for_document`, `livingdoc_list_documents`,
`livingdoc_list_ingested_documents`, `livingdoc_get_link_stats`,
`livingdoc_trace_path`, `livingdoc_derive_anchors_for_file`,
`livingdoc_validate_links`).

## Source Files

| File | Role |
| --- | --- |
| `unified_mcp.py` | Main MCP server (`graph_mcp`). Registers 15 tools directly, proxies the other 24, implements fan-out dispatch/merge, and routes each tool call to the correct backend. |
| `tool_metadata.py` | Shared catalog behind `list_mcp_functions`: descriptions, use cases, input fields, outputs, and examples. |
| `framework_registry.py` | Canonical capability registry for 27 parser profiles: aliases, backend assignment, support level, labels, relationships, and feature flags. |
| `fastmcp_server.py` | Generic graph backend plus the five dependency-planning tools. Also hosts backend-only workflow tools (`list_workflows`, `get_workflow_steps`, `search_workflows`) that are not exposed through the unified server. |
| `cplus/cplus_mcp.py` | Graph backend used by C/C++, JVM, Python, JS/TS, PHP, C#, SQL, COBOL, framework profiles (Spring, MyBatis, Struts, Servlet/JSP, Flutter, ASP.NET), and other compatible capability profiles. Implements `find_screen_workflows` and `analyze_proc_data_impact` (the latter is backend-only). |
| `android/android_mcp.py` | Android-oriented backend, selected when `parser_type` is `android`, `android-kotlin`, or `kotlin-android`. |
| `java/java_mcp.py` | Standalone Java backend implementation. Present in this directory but **not** registered in the unified router's `BACKENDS`. |
| `falkordb_discovery.py` | Discovers sibling FalkorDB instances' `data.rdb` files under `CORTEX_DATA_HOME` so unscoped queries can read every registered project across instances. |
| `semantic_graph_expansion.py` | Graph-expands semantic search hits: multi-hop traversal returning `hop_distance`, `graph_proximity`, seeds, and edges in a `graph_expansion` block. |
| `services/*` | Shared services: `explore_service.py` (explore_graph orchestration), `project_context_service.py` (project-context aggregates), `workflow_service.py` (find_screen_workflows), `flow_reconstructor.py` (reconstruct_flow), `graph_service.py`, `impact_service.py`, `symbol_service.py` (legacy FastAPI-era paths). |
| `../tools/common/embed_runtime.py` | Process-wide embedder/vector cache shared by all backends (`MCP_QUERY_EMBED_CACHE`, device auto-detect). |
| `../tools/common/project_registry.py` | The Project Registry: `resolve_project_targets(project_id)` mapping each project to its graph shard and Qdrant collections. |

## Quick Start

### 1. Start FalkorDB And Qdrant

The default service endpoints are:

| Service  | Default URL                           | Used by                                                  |
| -------- | ------------------------------------- | -------------------------------------------------------- |
| FalkorDB | `127.0.0.1:6380`                      | Symbol, call graph, workflow, API, and project-context tools |
| Qdrant   | `http://localhost:6333`               | `semantic_search` and semantic parts of `explore_graph`  |

Each project's graph shard is named after its `project_id` (naming rule; see
[The project_id Contract](#the-project_id-contract)). Qdrant is only required
for semantic/vector queries. Neo4j can be selected explicitly for legacy
compatibility; see [Graph Provider Configuration](#graph-provider-configuration).

### 2. Start `graph_mcp`

Preferred: use the harness launcher, which sets provider, storage, and
embedding environment for you:

```bash
cd /Users/hieplq1.aip/AI/cortex-harness
python cortex_harness/dev.py code-tiny   # serves http://127.0.0.1:8788/mcp
```

Or start the server directly from the repository root:

```bash
cd /Users/hieplq1.aip/AI/cortex-harness
python code-tiny/mcp/unified_mcp.py --transport streamable-http \
  --host 127.0.0.1 --port 8788 --path /mcp
```

The client endpoint is `http://127.0.0.1:8788/mcp`. The doc-side sibling
server (`mind_mcp`) runs on port 8789 and is configured in
`cortex_harness/dev.py`.

### 3. Confirm The Live Tool Catalog

Select tool `list_mcp_functions` and send:

```json
{}
```

Expected top-level output shape:

```json
{
  "total_count": 39,
  "functions": [
    {
      "name": "search_functions",
      "description": "...",
      "use_cases": ["..."],
      "inputs": [{ "name": "query", "type": "str", "required": true }],
      "output": "...",
      "example": "..."
    }
  ]
}
```

The return value is a JSON string, so some clients display it as text that
contains JSON. Parse the text once if your client does not do that
automatically.

### 4. Find A Symbol

Select tool `search_functions` and send. Note that `parser_type` and
`project_id` are passed on every call — there is no session activation step:

```json
{
  "query": "AuthManager|validateToken",
  "parser_type": "cplus",
  "project_id": "cortext",
  "limit": 20
}
```

Copy the returned `id`, `node_id`, or `symbol_id`. Exact field names can vary
by backend, but that value is the graph ID needed by the next call.

Representative result:

```json
{
  "results": [
    {
      "id": "validateToken/1@src/auth/AuthManager.java",
      "name": "validateToken",
      "qualified_name": "AuthManager.validateToken",
      "file_path": "src/auth/AuthManager.java"
    }
  ],
  "backend": "cplus"
}
```

### 5. Inspect Callers And Callees

Select tool `query_subgraph` and replace `function_id` with the ID returned in
step 4:

```json
{
  "function_id": "validateToken/1@src/auth/AuthManager.java",
  "parser_type": "cplus",
  "project_id": "cortext",
  "direction": "all",
  "max_depth": 2
}
```

Representative result:

```json
{
  "nodes": [
    {
      "id": "validateToken/1@src/auth/AuthManager.java",
      "name": "validateToken"
    },
    { "id": "login/2@src/auth/LoginService.java", "name": "login" }
  ],
  "edges": [
    {
      "source": "login/2@src/auth/LoginService.java",
      "target": "validateToken/1@src/auth/AuthManager.java",
      "type": "CALLS"
    }
  ]
}
```

Response fields are backend-dependent. Treat the samples in this guide as
shape examples, not fixed snapshots of your data.

## Prerequisites

Install dependencies from the repository root:

```bash
cd /Users/hieplq1.aip/AI/cortex-harness
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Required services:

| Service     | Default                                            |
| ----------- | -------------------------------------------------- |
| FalkorDB    | Host `127.0.0.1`, port `6380`                      |
| Qdrant HTTP | `http://localhost:6333`                            |

Useful environment variables:

```bash
export GRAPH_PROVIDER=falkordb
export FALKORDB_HOST=127.0.0.1
export FALKORDB_PORT=6380
export QDRANT_URL=http://localhost:6333
export CODE_EMBEDDING_MODEL=jinaai/jina-embeddings-v3
export EMBED_DEVICE=auto
```

`EMBED_DEVICE` accepts `auto`, `mps`, `cuda`, or `cpu`. `auto` resolves at
startup to MPS on macOS, then CUDA, then CPU, with automatic CPU fallback when
the accelerator fails (`EMBED_FALLBACK_TO_CPU=1` by default). The shared
embedder cache is controlled by `MCP_QUERY_EMBED_CACHE` (LRU size, default
`512`; `0`/`false`/`off` disables it).

Optional FalkorDB authentication:

```bash
export FALKORDB_USERNAME=your_username
export FALKORDB_PASSWORD=your_password
```

## The project_id Contract

`project_id` is the single scope key for every project-scoped operation. Both
`graph_mcp` (this server) and `mind_mcp` resolve it through the shared Project
Registry (`code-tiny/tools/common/project_registry.py`).

### Resolution And Naming Rule

| Concept | Rule |
| --- | --- |
| `project_id` raw | Preserved as identity/display; canonicalised to the registered form when a config entry matches. |
| `project_id_normalized` | `str(value).strip().casefold()` — the comparison key. Lookup is case-insensitive and whitespace-trimmed. |
| Code graph | `== project_id` |
| Code Qdrant collection | `== project_id` |
| Doc graph | `== f"{project_id}_doc"` (owned by `mind_mcp`; disjoint label space) |
| Doc Qdrant collection | `== f"{project_id}_doc"` |

Resolution precedence (lowest to highest): naming rule, env vars, config-file
values, per-call overrides. Env vars only contribute when no config file
describes any project.

### Registry Input

Every `*.json` file under `.cortext-harness/config/` is a registry entry,
reusing the `dev.json` shape:

```json
{
  "active": true,
  "project": {"code": "cortext", "name": "cortext"},
  "code": {"env": {"FALKORDB_GRAPH": "cortext", "QDRANT_COLLECTION": "cortext"}},
  "doc":  {"env": {"FALKORDB_GRAPH": "cortext_doc", "QDRANT_COLLECTION": "cortext_doc"}}
}
```

`project.code` is the registry key and becomes the canonical raw `project_id`.
Omitted graph/collection fields fall back to the naming rule.

### Scope Semantics Per Tool

- **`project_id` provided** — the query is pinned to that project's graph
  shard and Qdrant payload filter. Case-insensitive.
- **`project_id` omitted** — the query fans out across every registered
  project's graph (plus sibling FalkorDB instances discovered by
  `falkordb_discovery.py`), merging results. Use this only on trusted
  single-tenant setups.
- **`fe_project_id` / `be_project_id`** (fullstack tools) — independent
  frontend/backend project scopes. On `find_callers_of_endpoint` and
  `get_api_call_chain`, `project_id` is optional when either of these is set.
- **`db`** — accepted only as a legacy alias; the router merges it into
  `project_id`. New clients must not send it.

## Graph Provider Configuration

### FalkorDB: Current Default

The harness launchers set these defaults:

```bash
export GRAPH_PROVIDER=${GRAPH_PROVIDER:-falkordb}
export FALKORDB_HOST=${FALKORDB_HOST:-127.0.0.1}
export FALKORDB_PORT=${FALKORDB_PORT:-6380}
```

Set these variables **before** the Python process starts; the backend reads
the provider during module import.

Unified MCP graph tools use a shared, provider-neutral graph-driver
abstraction (`CypherGraphDriver`). Neo4j remains an explicit compatibility
provider rather than a hidden fallback. All tool families in this guide —
search, symbol, path, trace, IPC, annotation, planners, workflow, and
fullstack bridge tools — work on both providers; several tools check the
active graph's labels/relationships first and return
`capability_diagnostics` or a structured `unsupported_capability` /
`capability_unavailable` error instead of an unexplained empty result when the
schema lacks required topology.

### Neo4j: Compatibility Mode

Use Neo4j only when an external/legacy deployment requires it:

```bash
export GRAPH_PROVIDER=neo4j
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASS=your_password
export NEO4J_DB=neo4j
```

Then pass the matching `project_id` on every call (the registry maps the
project to its graph/DB per `code.env.GRAPH_PROVIDER`).

## Start The Unified MCP Server

```bash
cd /Users/hieplq1.aip/AI/cortex-harness
python code-tiny/mcp/unified_mcp.py --transport streamable-http \
  --host 127.0.0.1 --port 8788 --path /mcp
```

The default endpoint is:

```text
http://127.0.0.1:8788/mcp
```

Client config example:

```json
{
  "graph_mcp": {
    "url": "http://127.0.0.1:8788/mcp",
    "type": "http",
    "allowWriteAccess": true
  }
}
```

The server also supports:

| Argument      | Default           | Meaning                                      |
| ------------- | ----------------- | -------------------------------------------- |
| `--transport` | `streamable-http` | One of `stdio`, `sse`, or `streamable-http`. |
| `--host`      | `127.0.0.1`       | Host for HTTP transports.                    |
| `--port`      | `8788`            | Port for HTTP transports.                    |
| `--path`      | `/mcp`            | Streamable HTTP path.                        |

Environment alternatives:

```bash
export FASTMCP_TRANSPORT=streamable-http
export FASTMCP_HOST=127.0.0.1
export FASTMCP_PORT=8788
export FASTMCP_STREAMABLE_HTTP_PATH=/mcp
export MCP_SERVER_NAME=graph_mcp
```

The server name defaults to `graph_mcp` (override with `MCP_SERVER_NAME`).

### Health And Readiness Endpoints

Beyond the MCP endpoint, the HTTP transports expose two operational routes:

| Route | Meaning |
| --- | --- |
| `GET /health` | Liveness probe. `200` with `{"status": "healthy", "liveness": true}` while the process is up. |
| `GET /ready` | Readiness probe. `200 {"status": "ready"}` when storage gateways can serve reads; `503 {"status": "not_ready", "storage_state": ...}` while gateways are warming/draining or no committed generation is active. |

### Generation-Isolated Storage

When gateway mode is enabled (`CORTEX_STORE_GATEWAY_ENABLED`, or a gateway is
registered via `CORTEX_STORAGE_INSTANCE` / `CORTEX_DATA_HOME`), the MCP server
stays connected and serves the **last committed generation** while ingestion
builds the next generation in isolated staging storage. One owner holds the
embedded-store lease; same-target writes are queued and serialized through a
single writer lane; queries are admitted through a bounded reader policy.
Readiness during a swap is reported by `/ready` (`warming`, `draining`,
`rollback_ready` states appear in `storage_state`).

## Input Guide

### The Most Important Input Types

| Input kind             | Example                                       | Meaning                                          | How to obtain it                                                 |
| ---------------------- | --------------------------------------------- | ------------------------------------------------ | ---------------------------------------------------------------- |
| Natural-language query | `"payment fails after login"`                 | Behavior or concept to find                      | Write it yourself; use with `explore_graph` or `semantic_search` |
| Name query             | `"AuthManager\|validateToken"`                | Symbol name or alternatives (`\|` separates)     | Write known name fragments; use with `search_functions`          |
| Node/function ID       | `"validateToken/1@src/auth/AuthManager.java"` | Stable graph identifier, not just a display name | Copy from search, path, or subgraph results                      |
| Module/path token      | `"src/auth"`                                  | Substring matched against indexed file paths     | Use a repository directory or filename fragment                  |
| `project_id`           | `"cortext"`                                   | Project scope; resolves graph shard + Qdrant collection via the registry | Copy from ingestion config/result or `list_databases` |
| Qdrant `collection`    | `"cortext"`                                   | Vector collection to search (naming rule: `== project_id`) | Use `list_qdrant_collections`                    |
| `parser_type`          | `"cplus"`                                     | Selects the query profile/backend                | Use `list_parsers` and the routing table below                   |

Do not confuse a symbol name with a symbol ID. `search_functions` accepts a
name fragment; `get_symbol`, `query_subgraph`, `find_paths`, and impact tools
normally require the returned graph ID.

### Top-Level Parameters

Use typed top-level MCP parameters with the unified server.

Good:

```json
{
  "query": "AuthManager",
  "parser_type": "python",
  "project_id": "cortext",
  "limit": 20
}
```

Do not wrap unified calls in a backend-style `payload` object. The unified
server builds backend payloads for you.

Wrong for the unified server:

```json
{
  "payload": {
    "query": "AuthManager"
  }
}
```

### Common Fields

| Rule | Details |
| --- | --- |
| Empty strings | Treated as "not provided". Most parameters default to `""`. |
| No session state | `parser_type` and `project_id` are not sticky; pass them on **every** call. `list_parsers` reports `active_parser_type: null`. |
| `project_id` | Case-insensitive project scope. Omit to query across all registered projects (fan-out). |
| `parser_type` | Canonical parser or alias. Omitting it on the 13 fan-out search tools dispatches across all query engines; see [Fan-Out](#fan-out-across-parsers-and-projects). |
| Numbers | Public schemas use JSON numbers (`limit: 20`, `max_depth: 4`, `min_score: 0.7`). Several fields also coerce numeric strings. |
| Lists | Prefer real JSON arrays for `List[...]` fields (`modules`, `kinds`, `node_types`, `graph_rel_types`). |
| Legacy aliases | The router normalizes `module` → `modules`, `source_module` → `source_modules`, `target_module` → `target_modules`, `class_name` → `class_names`, `db` → `project_id`. |
| `node_type` | Common filters are `code` and `doc`. Use `doc` when searching document nodes rather than code symbols. |
| `content_mode` | Backend catalog supports `auto`, `summary`, `comment`, `code`, and `name`; not every wrapper exposes it directly. |

### Required Scope Fields

Pass `project_id` whenever more than one project shares the same FalkorDB /
Qdrant services. Fullstack tools use separate frontend/backend scopes:

```json
{
  "endpoint_path": "/api/users/:id",
  "http_method": "GET",
  "fe_project_id": "web-client",
  "be_project_id": "user-service"
}
```

If a query unexpectedly returns another repository's symbols, missing or wrong
project scope is the first thing to check.

### Fan-Out Across Parsers And Projects

13 search tools support parser fan-out: `search_functions`, `search_by_code`,
`get_symbol`, `get_node_details`, `query_subgraph`, `find_paths`,
`find_path_between_module`, `listup_symbols_matching_file_path`,
`listup_class_matching_path`, `list_up_entrypoint`, `trace_flow`,
`trace_flow_between_module`, `list_possible_calls`.

Behavior when `parser_type` is omitted:

- Each physical query engine (backends: `cplus`, `android`) is dispatched in
  parallel with the same arguments.
- List results are concatenated and each item is tagged with its source
  `parser_type`; duplicates are removed (nodes by `id`, edges by
  `(start, type, end)`); a `dedup_removed` count is reported.
- The merged response adds `ok`, `parsers_searched`, `parsers_failed`,
  `parser_errors`, `query_engine: "graph_fanout"`, and a `parser_results` map
  from engine name to that engine's full raw result (including per-engine
  diagnostics such as `capability_diagnostics`).
- `ok` is `false` only when **every** engine failed.
- Project-context tools are excluded from parser fan-out because their Cypher
  already reads every project's graph shard directly.

## Output Guide

There is no single response schema for all 39 tools. The unified wrapper
normalizes a few common empty states, while each backend preserves useful
domain-specific fields.

### Search Responses

Search tools usually return `results`, `functions`, or `matched_nodes`. Each
item commonly includes an ID, name, qualified name, file path, and optional
score/content.

```json
{
  "results": [
    {
      "id": "processPayment/2@src/payment/service.py",
      "name": "processPayment",
      "qualified_name": "PaymentService.processPayment",
      "file_path": "src/payment/service.py",
      "score": 0.86
    }
  ]
}
```

`semantic_search` results carry 400-character text previews (full payload
fields are narrowed for latency); set `show_snippet` / `show_comment` /
`include_raw_fields` for more. With `expand_graph: true`, the response gains a
`graph_expansion` block containing `seed_ids`, `depth`, `direction`,
`results` (neighbors with `hop_distance` and `graph_proximity`), and `edges`.

### Graph Responses

Graph tools usually return `nodes` plus `edges`, or a list of ordered `paths`.

```json
{
  "nodes": [
    { "id": "a", "name": "login" },
    { "id": "b", "name": "validateToken" }
  ],
  "edges": [{ "source": "a", "target": "b", "type": "CALLS" }],
  "paths": [{ "nodes": ["a", "b"], "relationships": ["CALLS"] }]
}
```

### Fan-Out Responses

When a fan-out tool runs across engines (see above), the top-level result is
the merged view plus per-engine raw results:

```json
{
  "ok": true,
  "query_engine": "graph_fanout",
  "parsers_searched": ["cplus", "android"],
  "parsers_failed": [],
  "dedup_removed": 3,
  "results": ["...merged, each item tagged with parser_type..."],
  "parser_results": {
    "cplus": { "...full raw cplus result..." },
    "android": { "...full raw android result..." }
  }
}
```

### Empty Results Are Usually Valid

The wrapper normalizes several no-match cases so clients can handle them
without treating them as server failures:

```json
{
  "paths": [],
  "nodes": [],
  "edges": [],
  "reason": "no_path"
}
```

Other common empty shapes are `{"symbol": null}`, `{"entrypoints": []}`, and
`{"symbols": []}`. Verify scope and identifiers before concluding that data was
not ingested.

### Errors And Validation Responses

Some tools return a structured validation response instead of raising an MCP
protocol error. For example, `list_up_entrypoint` can return:

```json
{
  "ok": false,
  "entrypoints": [],
  "missing_fields": ["modules"],
  "message": "Missing required parameter(s): modules. Please provide all missing fields and retry."
}
```

Schema-gated tools return `unsupported_capability` /
`capability_unavailable` errors with the missing labels/relationships listed.
Connection failures and invalid credentials may still be raised as MCP errors.

## Parser Routing

`parser_type` selects a capability profile, not a separate MCP server. There
are 27 canonical profiles; two physical backends. Call `list_parsers`
(`detail_level`: `"summary"` or `"full"`) for the live table, and
`inspect_parser_capabilities` to compare a profile's advertised support with
what the active graph actually contains.

| Canonical `parser_type` | Aliases | Backend | Support level |
| --- | --- | --- | --- |
| `android` | `android`, `android-kotlin`, `kotlin-android` | android | full |
| `cplus` | `cplus`, `cpp`, `c++`, `c`, `clang`, `proc`, `pro*c`, `pro-c` | cplus | full |
| `python` | `python`, `py`, `fastapi`, `django`, `flask` | cplus | partial |
| `javascript` | `javascript`, `js`, `node`, `nodejs`, `express`, `express.js` | cplus | partial |
| `typescript` | `typescript`, `ts`, `tsx`, `nestjs`, `nest.js` | cplus | full |
| `php` | `php`, `laravel`, `symfony` | cplus | partial |
| `csharp` | `csharp`, `c#`, `cs`, `dotnet`, `.net` | cplus | full |
| `sql` | `sql` | cplus | full (database) |
| `plsql` | `plsql`, `pl/sql`, `oracle-plsql` | cplus | full (database) |
| `jvm` | `jvm`, `java`, `kotlin` | cplus | generic |
| `go` | `go` | cplus | generic |
| `perl` | `perl` | cplus | generic |
| `shell` | `shell`, `sh`, `bash` | cplus | generic |
| `jp1` | `jp1`, `ajs`, `jobnet` | cplus | generic |
| `rust` | `rust` | cplus | generic |
| `swift` | `swift` | cplus | generic |
| `delphi` | `delphi`, `pascal` | cplus | generic |
| `vbnet` | `vbnet` | cplus | generic |
| `visual_basic` | `visual_basic`, `vb6`, `vba`, `vbscript` | cplus | generic |
| `cobol` | `cobol`, `cobol85`, `ibm-cobol`, `gnucobol` | cplus | full |
| `spring` | `spring`, `spring-boot`, `spring_boot` | cplus | full (+endpoint queries) |
| `servlet_jsp` | `servlet_jsp`, `servlet-jsp`, `servlet`, `jsp` | cplus | full (generation-scoped) |
| `mybatis` | `mybatis`, `my-batis` | cplus | full (+persistence queries) |
| `struts` | `struts`, `struts2`, `apache-struts`, `apache_struts` | cplus | full (+endpoint queries) |
| `flutter` | `dart`, `flutter`, `flutter-dart`, `flutter_dart` | cplus | full |
| `aspnet_framework` | `aspnet_framework`, `aspnet-framework`, `asp.net-framework`, `aspnetframework` | cplus | full (generation-scoped) |
| `aspnet_core` | `aspnet_core`, `aspnet-core`, `asp.net-core`, `aspnetcore` | cplus | full (generation-scoped) |

Notes:

- Support levels: `full` (all core dimensions), `partial` (some dimensions
  such as call-graph or endpoints are limited — e.g. Python/JS/PHP report
  partial calls/endpoints), `generic` (shared generic label/relationship
  vocabulary). Per-dimension detail is in `list_parsers` output.
- "generation-scoped" profiles (Servlet/JSP, ASP.NET) tag facts with the
  ingestion generation so stale facts are filtered automatically.
- The `cplus` profile additionally exposes `strict`, `conservative`, and
  `proc_data_impact` query modes for Pro*C data-impact analysis.
- Empty or unknown `parser_type` on non-fan-out tools falls back to
  `MCP_UNIFIED_DEFAULT_BACKEND` (default `cplus`).

## Recommended Query Workflow

1. Call `list_mcp_functions` to confirm the live tool catalog.
2. Call `list_parsers` and `list_databases` if you are unsure about routing or
   available graph shards.
3. Pass `parser_type` and `project_id` explicitly on every call. To search
   everywhere at once, omit `parser_type` (and/or `project_id`) on a fan-out
   search tool and read the merged `parser_results`.
4. Use `semantic_search` or `explore_graph` for vague natural-language
   discovery; inspect graph-expansion diagnostics when partial.
5. Use `search_functions`, `search_by_code`, or
   `listup_symbols_matching_file_path` to get stable node IDs.
6. Use `get_symbol`, `get_node_details`, `query_subgraph`, `find_paths`, or
   impact tools for exact analysis.

## Tool Reference

### Discovery And Session Tools

#### `list_mcp_functions`

Purpose: Lists every available MCP tool with description, use cases, inputs,
outputs, and examples. This is the first call clients should make because the
live server can differ from stale documentation.

Input: none.

Output: JSON string containing `total_count` (39) and `functions`. Each
function entry includes `name`, `description`, `use_cases`, `inputs`,
`output`, and `example`.

Example:

```json
{}
```

#### `list_parsers`

Purpose: Lists all canonical parser profiles with aliases, query engine,
support level, and per-dimension support.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `detail_level` | No | `str` | `summary` (default) or `full`. |

Output: Dict with `parsers` (sorted alias list), `capabilities`,
`detail_level`, `capability_contract_version`, `default_query_engine`, and
`active_parser_type` (always `null` — session state was removed).

Example:

```json
{}
```

#### `inspect_parser_capabilities`

Purpose: Compares a parser profile's advertised support with node labels and
relationship types observed in the active graph provider.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `parser_type` | No | `str` | Parser profile; omit on fan-out tools to dispatch across query engines. |
| `project_id` | No | `str` | Project scope. |

Output: Advertised/effective support for `symbols`, `calls`, `endpoints`, and
`database`; schema status/fingerprint; missing contract evidence; and a
recommended action such as `run_incremental_sync`.

Use this before specialized endpoint or database queries, and after changing
parser/overlay configuration.

#### `list_databases`

Purpose: Lists graph shards visible to the selected backend. In FalkorDB mode
this returns the registered project graphs (each named after its
`project_id`); in Neo4j mode it lists Neo4j databases.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `parser_type` | No | `str` | Backend alias to use for the lookup. |

Output: Dict with graph/database names, or a provider connection error.

Example:

```json
{ "parser_type": "cplus" }
```

#### `list_qdrant_collections`

Purpose: Lists Qdrant vector collections used by semantic search.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `parser_type` | No | `str` | Backend alias. |
| `qdrant_url` | No | `str` | Qdrant HTTP URL, defaults to `QDRANT_URL` or `http://localhost:6333`. |
| `include_vectors` | No | `bool` | Include vector metadata when supported. |

Output: Dict with Qdrant collection names and optional metadata. Under the
naming rule the code collection equals the `project_id`.

Example:

```json
{ "qdrant_url": "http://localhost:6333", "include_vectors": false }
```

### Semantic And Broad Search Tools

#### `explore_graph`

Purpose: Intent-aware graph search for natural language questions, bug
descriptions, requirement paragraphs, or vague concepts. It fuses semantic
vector search, BM25 keyword signals, and call-graph expansion, returning
explainable ranked nodes with per-node reasons.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `query` | Yes | `str` | Natural language query. English and Vietnamese are supported. |
| `mode` | No | `str` | `semantic`, `hybrid`, or `graph_expanded`. Default: `hybrid`. |
| `top_k` | No | `int` | Max matched nodes. Default: `10`. |
| `collection` | No | `str` | Qdrant collection override. |
| `debug` | No | `bool` | Include per-signal scoring details. |
| `parser_type` | No | `str` | Canonical parser or alias. |
| `project_id` | No | `str` | Project scope for every retrieval and expansion stage. |

Output: Dict with `matched_nodes`, `entry_points`, `related_paths`,
`explanation`, `confidence`, `query_analysis`, and `mode`. Retrieval
diagnostics identify the selected provider/graph and whether graph retrieval
degraded, so an empty match set is distinguishable from unavailable graph
expansion.

Provider note: `semantic` mode is primarily Qdrant-backed. `hybrid` and
`graph_expanded` add graph expansion through the shared provider abstraction.
If graph expansion times out, the response can still contain semantic
candidates plus diagnostics. When `project_id` is supplied, vector search,
keyword matching, BM25 boosting, expansion, and packaging all remain inside
that project.

Example:

```json
{
  "query": "function xu ly thanh toan bi loi khi user chua login",
  "mode": "semantic",
  "top_k": 15,
  "parser_type": "python",
  "project_id": "cortext"
}
```

#### `semantic_search`

Purpose: Searches Qdrant embeddings for code or comments similar in meaning to
the query, with optional multi-hop graph expansion.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `query` | Yes | `str` | Natural language query or code snippet. |
| `mode` | No | `str` | Backend search mode. Default: `combined`. |
| `top_k` | No | `int` | Number of results. Default: `10`. |
| `collection` | No | `str` | Qdrant collection override (defaults to the project's collection). |
| `content_mode` | No | `str` | `auto`, `summary`, `comment`, `code`, or `name`. |
| `include_raw_fields` | No | `bool` | Include full raw payload fields. |
| `show_snippet` / `show_comment` | No | `bool` | Include code snippet / comment text in results. |
| `expand_graph` | No | `bool` | Graph-expand the vector hits. Default: `false`. |
| `graph_depth` | No | `int` | Expansion hops (capped at 5). Default: `2`. |
| `graph_direction` | No | `str` | `out`, `in`, or `both`. Default: `both`. |
| `graph_rel_types` | No | `List[str]` | Relationship types; default `CALLS`, `USES_TYPE`, `REFERENCES`, `INHERITS`. |
| `graph_limit` | No | `int` | Max expansion results. Default: `50`. |
| `parser_type` | No | `str` | Backend alias. |
| `project_id` | No | `str` | Case-insensitive project scope; applied as a server-side Qdrant payload filter and to graph expansion. |

Output: Dict with `results` (score, node metadata, file path, symbol name,
400-char content preview) and, when `expand_graph: true`, a `graph_expansion`
block with exact `hop_distance`, `seed_ids` provenance, `graph_proximity`,
and `graph_expansion.edges`.

Use when:

- Exact string search is too brittle.
- You want similar implementations.
- You are looking for code by behavior rather than name.
- You need a bridge from requirements text to candidate symbols.

Example:

```json
{
  "query": "allocate memory safely",
  "top_k": 5,
  "parser_type": "cplus",
  "project_id": "cortext",
  "expand_graph": true,
  "graph_depth": 2
}
```

#### `search_functions`

Purpose: Searches code/doc nodes by name or qualified name. This is the fastest
tool when you know part of a function, class, type, or symbol name.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `query` | Yes | `str` | Search terms. `termA|termB` matches alternatives. |
| `limit` | No | `int` | Max results. Default: `50`. |
| `top_k` | No | `int` | Optional alternative result cap. |
| `content_mode` | No | `str` | Result content verbosity. |
| `include_raw_fields` | No | `bool` | Include raw backend fields. |
| `framework` | No | `str` | Framework filter when supported. |
| `kinds` | No | `List[str]` | Symbol-kind filter (e.g. `["Function", "Class"]`). |
| `node_type` | No | `str` | `code` or `doc`. |
| `parser_type` | No | `str` | Backend alias; omit for fan-out. |
| `project_id` | No | `str` | Project scope. |

Output: Dict with `results`/`functions` and node IDs for follow-up calls.

Example:

```json
{
  "query": "handleLogin|AuthManager",
  "limit": 20,
  "parser_type": "cplus",
  "project_id": "cortext"
}
```

#### `search_by_code`

Purpose: Searches function bodies or implementation text for exact code
snippets, string literals, API names, or regex-like fragments.

Input: same shape as `search_functions` (`query` is the code text; backend
search is generally case-sensitive).

Output: Dict with `results` or backend-specific matching nodes.

Use when:

- You know an exact API call, SQL fragment, log message, or literal.
- You need to find all places using a legacy pattern.
- Semantic search returns too many broad candidates.

Example:

```json
{
  "query": "DataNormal|Authen|Login|SignIn|Account",
  "parser_type": "cplus",
  "project_id": "cortext",
  "limit": 500
}
```

### Symbol And Code Graph Tools

#### `get_symbol`

Purpose: Fetches full metadata for a single node by ID.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `node_id` | Yes | `str` | Node ID from search results. |
| `node_type` | No | `str` | Optional domain filter: `code` or `doc`. |
| `parser_type` | No | `str` | Backend alias; omit for fan-out. |
| `project_id` | No | `str` | Project scope. |

Output: Dict with symbol metadata such as `name`, `qualified_name`,
`file_path`, `signature`, `code`, `comment`, line numbers, and raw backend
fields. If not found, returns `symbol: null` with a message.

Example:

```json
{
  "node_id": "validateToken/1@src/auth/AuthManager.java",
  "parser_type": "cplus",
  "project_id": "cortext"
}
```

#### `get_node_details`

Purpose: Batch version of `get_symbol`; fetches multiple node records in one
call.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `node_ids` | Yes | `str` / `List[str]` | One ID, a list of IDs, or a comma/semicolon-separated string. |
| `node_type` | No | `str` | Optional domain filter. |
| `parser_type` | No | `str` | Backend alias; omit for fan-out. |
| `project_id` | No | `str` | Project scope. |

Output: Dict with `nodes` or backend-specific node-detail list.

Example:

```json
{ "node_ids": "func_1,func_2,func_3", "parser_type": "cplus", "project_id": "cortext" }
```

#### `query_subgraph`

Purpose: Returns call-graph context around one function: callers, callees, or
both, depending on direction.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `function_id` | Yes | `str` | Starting function/symbol node ID. |
| `direction` | No | `str` | `all` (default), `in`, `out`, or `both`. |
| `max_depth` | No | `int` | Traversal depth. Default: `2`. |
| `limit` | No | `int` | Optional backend result cap. |
| `node_type` | No | `str` | Optional domain filter. |
| `parser_type` | No | `str` | Backend alias; omit for fan-out. |
| `project_id` | No | `str` | Project scope. |

Output: Dict with `nodes` and `edges`; empty result is normalized to
`reason: no_subgraph`.

Example:

```json
{
  "function_id": "func_main",
  "direction": "out",
  "max_depth": 3,
  "parser_type": "cplus",
  "project_id": "cortext"
}
```

#### `find_paths`

Purpose: Finds call paths between two specific function nodes.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `start_function_id` | Yes | `str` | Starting function ID. |
| `end_function_id` | Yes | `str` | Target function ID. |
| `limit` | No | `int` | Optional max result count. |
| `node_type` | No | `str` | Optional domain filter. |
| `parser_type` | No | `str` | Backend alias; omit for fan-out. |
| `project_id` | No | `str` | Project scope. |

Output: Dict with `paths`, and often `nodes` and `edges`. No-path runtime
errors are normalized to `paths: []`, `nodes: []`, `edges: []`,
`reason: no_path`.

Example:

```json
{
  "start_function_id": "main",
  "end_function_id": "malloc",
  "parser_type": "cplus",
  "project_id": "cortext",
  "limit": 10
}
```

#### `find_path_between_module`

Purpose: Finds call paths between files/modules using file path tokens.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `source_module` | Yes | `str` | Source file/module token. |
| `target_module` | Yes | `str` | Target file/module token. |
| `limit` | No | `int` | Max paths. |
| `parser_type` | No | `str` | Backend alias; omit for fan-out. |
| `project_id` | No | `str` | Project scope. |

Output: Dict with `paths` or backend-specific module graph data.

Example:

```json
{
  "source_module": "src/auth",
  "target_module": "src/payment",
  "parser_type": "cplus",
  "project_id": "cortext",
  "limit": 20
}
```

#### `listup_symbols_matching_file_path`

Purpose: Lists functions/classes/types in files whose path contains one or
more tokens.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `file_path` | No | `str` | Convenience single path token. |
| `modules` | No | `List[str]` | Explicit list of path tokens. |
| `node_types` | No | `List[str]` | Optional filters such as `Function`, `Class`, or `Type`. |
| `parser_type` | No | `str` | Backend alias; omit for fan-out. |
| `project_id` | No | `str` | Project scope. |

At least one of `file_path` or `modules` is required.

Output: Dict with `symbols` or backend-specific symbol inventory.

Example:

```json
{
  "file_path": "src/auth/router.ts",
  "node_types": ["Function"],
  "parser_type": "typescript",
  "project_id": "cortext"
}
```

#### `listup_class_matching_path`

Purpose: Lists functions/methods declared in classes/types whose names match a
pattern.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `class_name` | Yes | `str` | Class/type name pattern. Routed to backend as `class_names`. |
| `parser_type` | No | `str` | Backend alias; omit for fan-out. |
| `project_id` | No | `str` | Project scope. |

Output: Dict with `functions` or backend-specific class/method structure.

Example:

```json
{ "class_name": "AuthManager", "parser_type": "cplus", "project_id": "cortext" }
```

#### `list_up_entrypoint`

Purpose: Finds entry point functions in target modules: functions inside the
module that are called from outside the module.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `modules` | Yes | `List[str]` | Module/file path tokens (`module` accepted as a single-module alias). |
| `parser_type` | No | `str` | Backend alias; omit for fan-out. |
| `project_id` | No | `str` | Project scope. |

Output: Dict with `entrypoints`. If required fields are missing, returns
`ok: false`, `missing_fields`, accepted formats, and an example.

Example:

```json
{ "modules": ["src/api"], "parser_type": "cplus", "project_id": "cortext" }
```

#### `list_possible_calls`

Purpose: Lists `POSSIBLE_CALLS` relationships such as function pointer calls,
virtual calls, and callback registrations.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `function_id` | No | `str` | Optional function to scope the indirect-call lookup. |
| `limit` | No | `int` | Max results. |
| `parser_type` | No | `str` | Backend alias; omit for fan-out. |
| `project_id` | No | `str` | Project scope. |

Output: Dict with `calls` or backend-specific possible-call edge records.

Example:

```json
{ "function_id": "func_123", "limit": 50, "parser_type": "cplus", "project_id": "cortext" }
```

#### `annotate_node`

Purpose: Adds or updates review annotations on a graph node. This is the
server's main write operation.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `node_id` | Yes | `str` | Target node ID. |
| `note` | No | `str` | Free-form note. |
| `tags` | No | `str` | Comma-separated tags. |
| `parser_type` | No | `str` | Backend alias. |
| `project_id` | No | `str` | Project scope. |

Output: Dict with updated node/annotation data.

Example:

```json
{ "node_id": "func_123", "note": "Buffer overflow risk", "tags": "security,review" }
```

### Flow And Module Tracing Tools

#### `trace_flow`

Purpose: Traces flow paths from a starting node using backend-specific default
relationships.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `start_id` | Yes | `str` | Start function/symbol ID. |
| `direction` | No | `str` | Direction filter, backend-specific (e.g. `downstream`, `upstream`). |
| `limit` | No | `int` | Max results. |
| `parser_type` | No | `str` | Backend alias; omit for fan-out. |
| `project_id` | No | `str` | Project scope. |

Output: Dict with `flows`, `nodes`, `edges`, or backend-specific traced paths.
No-path runtime errors are normalized to `nodes: []`, `edges: []`,
`reason: no_path`.

Example:

```json
{ "start_id": "func_123", "direction": "downstream", "limit": 25, "parser_type": "cplus", "project_id": "cortext" }
```

#### `trace_flow_between_module`

Purpose: Traces flow paths between modules using backend-specific relationship
logic.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `source_module` | Yes | `str` | Source module/file token. |
| `target_module` | Yes | `str` | Target module/file token. |
| `limit` | No | `int` | Max results. |
| `parser_type` | No | `str` | Backend alias; omit for fan-out. |
| `project_id` | No | `str` | Project scope. |

Output: Dict with `flows`, `nodes`, `edges`, or backend-specific module paths.

Example:

```json
{
  "source_module": "ui/",
  "target_module": "service/",
  "limit": 20,
  "parser_type": "android",
  "project_id": "digital_key_main"
}
```

#### `reconstruct_flow`

Purpose: Converts candidate graph paths into grounded, ordered execution-flow
objects that are easier for agents and reviewers to reason about.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `entry_context_json` | Yes | `str` | JSON object string with `type`, `entry_point`, `entry_node_id`, `screen`, and `trigger`. |
| `paths_json` | Yes | `str` | JSON array string of path objects with `nodes` and `edges`. |

Output: Dict with `flows` and `uncertainties`. Each flow contains `flow_id`,
`title`, `type`, `confidence`, `entry_node_id`, `paths_used`,
`discarded_paths`, and ordered `steps`.

Example:

```json
{
  "entry_context_json": "{\"type\":\"backend\",\"entry_point\":\"main\",\"entry_node_id\":\"n1\",\"screen\":null,\"trigger\":null}",
  "paths_json": "[{\"path_id\":\"path_1\",\"nodes\":[{\"node_id\":\"n1\",\"name\":\"main\",\"mapped_type\":\"function\",\"location\":{\"file\":\"main.c\",\"line\":10}}],\"edges\":[]}]"
}
```

### Dependency Planning Tools

The five planner tools are proxied to the planning backend
(`fastmcp_server.py`); they accept the same provider-neutral `project_id`
scope as the rest of the server.

#### `compute_scc`

Purpose: Computes strongly connected components in a directed dependency graph.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `nodes` | No | `str` | Node IDs/names as a comma or semicolon-separated string. |
| `edges` | No | `List[Dict]` | Edge records with source/target style fields. |
| `edge_semantics` | No | `str` | `depends_on` or `calls`. Default: `depends_on`. |
| `include_singletons` | No | `bool` | Include one-node SCCs. Default: `true`. |

Output: Dict with `components`, `node_to_scc`, and `cycle_summary`.

Example:

```json
{ "nodes": "A,B", "edges": [{ "from": "A", "to": "B" }], "edge_semantics": "depends_on" }
```

#### `topological_sort`

Purpose: Sorts a dependency graph into a linear order and/or parallel waves.
It can auto-condense SCCs when cycles exist.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `nodes` | No | `str` | Node IDs/names as a comma or semicolon-separated string. |
| `edges` | No | `List[Dict]` | Dependency edges. |
| `edge_semantics` | No | `str` | `depends_on` or `calls`. Default: `depends_on`. |
| `output_mode` | No | `str` | `linear`, `waves`, or `both`. Default: `both`. |
| `on_cycle` | No | `str` | `auto_condense_scc` or `error`. Default: `auto_condense_scc`. |

Output: Dict with `is_dag`, `linear_order`, `waves`, cycle diagnostics, and
optional condensed SCC data.

Example:

```json
{
  "nodes": "A,B,C",
  "edges": [{ "from": "A", "to": "B" }, { "from": "B", "to": "C" }],
  "output_mode": "both"
}
```

#### `plan_dependency_order`

Purpose: Builds module-level dependency waves from graph `CALLS` edges.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `modules` | Yes | `str` | Comma/semicolon-separated module tokens. |
| `edge_semantics` | No | `str` | Default: `depends_on`. |
| `on_cycle` | No | `str` | Default: `auto_condense_scc`. |
| `parser_type` | No | `str` | Backend alias. |
| `project_id` | No | `str` | Project scope. |

Output: Dict with `waves`, `module_order`, `depends_on_map`,
`module_dependencies`, cycle diagnostics, and SCC mapping.

Example:

```json
{ "modules": "auth,payment", "parser_type": "cplus", "project_id": "cortext" }
```

#### `plan_file_dependency_order`

Purpose: Builds file-level dependency waves inside one or more modules from
graph `CALLS` edges.

Input: as `plan_dependency_order` plus:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `include_cross_module` | No | `bool` | Include cross-module edges. |
| `max_files_per_module` | No | `int` | Default: `2000`. |

Output: Dict with `cross_module_edges` and `modules[]`. Each module contains
`waves`, `file_order`, `depends_on_map`, cycle diagnostics, SCC mapping, and
`file_dependencies`.

Example:

```json
{ "modules": "auth,payment", "include_cross_module": true, "parser_type": "cplus", "project_id": "cortext" }
```

#### `plan_function_dependency_order`

Purpose: Builds function-level dependency waves inside one or more modules
from graph `CALLS` edges.

Input: as `plan_dependency_order` plus:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `include_cross_module` | No | `bool` | Include cross-module edges. |
| `include_lambdas` | No | `bool` | Include lambda/function-literal nodes when available. |
| `max_functions_per_module` | No | `int` | Default: `5000`. |

Output: Dict with `cross_module_edges` and `modules[]`. Each module contains
function waves, ordered IDs, detailed function metadata, dependency maps,
cycle diagnostics, and SCC mapping.

Example:

```json
{
  "modules": "auth,payment",
  "include_cross_module": true,
  "include_lambdas": false,
  "parser_type": "cplus",
  "project_id": "cortext"
}
```

### Workflow Tools

#### `find_screen_workflows`

Purpose: Finds ranked screen-to-screen navigation workflows using `NAVIGATE`
edges. It can search between two screens or around one screen. Implemented in
the cplus backend via `services/workflow_service.py`.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `project_id` | Yes | `str` | Project scope. |
| `node_a` | Yes | `str` | Source/anchor screen name or symbol ID. |
| `node_b` | No | `str` | Target screen name for pair mode. |
| `direction` | No | `str` | `inbound`, `outbound`, or `bidirectional`. Default: `bidirectional`. |
| `max_hops` | No | `int` | Max NAVIGATE hops. Default: `8`. |
| `max_paths` | No | `int` | Max workflows. Default: `100`. |
| `parser_type` | No | `str` | Backend alias. |

Output: Dict with `mode`, `direction`, `project_id`, `resolved`,
`workflows`, `uncertainties`, and `truncated`.

Example:

```json
{
  "project_id": "my-app",
  "node_a": "RewardHome",
  "node_b": "GoldTransfer",
  "parser_type": "typescript",
  "max_hops": 8
}
```

#### `analyze_workflow_impact`

Purpose: Combines call-graph expansion and workflow-level scoring to estimate
the blast radius of changing a function/screen.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `function_id` | Yes | `str` | Symbol ID or function/screen ID. |
| `project_id` | No | `str` | Project scope; omit to query across all projects. |
| `direction` | No | `str` | `downstream` or `upstream`. Default: `downstream`. |
| `max_depth` | No | `int` | CALLS traversal depth, capped at `4`. Default: `4`. |
| `parser_type` | No | `str` | Backend alias. |

Output: Dict with `risk_score`, counts, `impacted_nodes`, and a
`workflow_impact` block containing direct/indirect affected workflows,
cascade workflows, navigator impacts, shared-screen conflict flag, score, and
recommendation.

Provider note: both the call-graph expansion and the workflow-scoring layer
(`tools/common/workflow_impact_scorer.py`) run through the shared
provider-neutral graph driver. When the active graph lacks workflow-shaped
relationships (`HAS_STEP`), the response contains base call-graph risk plus a
`workflow_impact` diagnostic instead of a score.

Example:

```json
{
  "function_id": "func_123",
  "project_id": "cortext",
  "direction": "downstream",
  "max_depth": 4
}
```

#### `find_workflows_containing`

Purpose: Lists workflows that contain a function directly via `HAS_STEP` or
indirectly through a `CALLS` chain.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `function_id` | Yes | `str` | Symbol ID or file path anchor. |
| `project_id` | No | `str` | Project scope. |
| `include_indirect` | No | `bool` | Include CALLS-chain derived workflows. Default: `true`. |
| `max_depth` | No | `int` | Indirect traversal cap, capped at `4`. Default: `4`. |
| `parser_type` | No | `str` | Backend alias. |

Output: Dict with `function_id`, `direct_workflows`,
`indirect_workflows`, and `total`.

Example:

```json
{ "function_id": "func_123", "project_id": "cortext", "include_indirect": true, "max_depth": 4 }
```

### IPC And Fullstack API Bridge Tools

#### `get_ipc_message`

Purpose: Queries IPC/message records by sender and/or receiver. It checks
graph `Message` nodes first and can fall back to JSON data in backend logic.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `sender` | No | `str` | Sender component pattern. |
| `receiver` | No | `str` | Receiver component pattern. |
| `parser_type` | No | `str` | Backend alias; omit for fan-out. |
| `project_id` | No | `str` | Project scope. |

Output: Dict with `messages`, sender/receiver lists, or backend-specific IPC
details.

Example:

```json
{ "sender": "Activity", "receiver": "Service", "parser_type": "android", "project_id": "digital_key_main" }
```

#### `find_callers_of_endpoint`

Purpose: Returns frontend functions/screens that call a backend API endpoint.
It traverses `Function -> CALLS_API -> ApiCall -> MATCHES -> ApiEndpoint`.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `endpoint_path` | Yes | `str` | Backend endpoint path, for example `/api/users/:id`. |
| `http_method` | No | `str` | `GET`, `POST`, `PUT`, `DELETE`, or empty for any. Default: `GET`. |
| `be_project_id` | No | `str` | Case-insensitive backend project scope. |
| `fe_project_id` | No | `str` | Case-insensitive frontend project scope. |
| `project_id` | No | `str` | Project scope; optional when `be_project_id`/`fe_project_id` is set. |
| `parser_type` | No | `str` | Backend alias. |

Output: Dict with `endpoint_path`, `callers`, and `total`. Each caller
contains function name, qualified name, React role, file path, line number,
project ID, URL pattern, and match confidence.

Provider note: this tool uses the shared graph driver and checks required
`ApiEndpoint`, `ApiCall`, `CALLS_API`, and `MATCHES` schema before querying.

Example:

```json
{
  "endpoint_path": "/api/users/:id",
  "http_method": "GET",
  "fe_project_id": "web-client",
  "be_project_id": "user-service"
}
```

#### `get_api_call_chain`

Purpose: Returns an end-to-end chain from a frontend component or endpoint to
backend controller, service, repository, and database nodes.

Input:

| Field | Required | Type | Meaning |
| --- | --- | --- | --- |
| `component_name` | No | `str` | Frontend component/screen name. |
| `endpoint_path` | No | `str` | Backend endpoint path. Required when `component_name` is absent. |
| `fe_project_id` | No | `str` | Case-insensitive frontend project scope. |
| `be_project_id` | No | `str` | Case-insensitive backend project scope. |
| `project_id` | No | `str` | Project scope; optional when either fe/be scope is set. |
| `max_depth` | No | `str`/`int` | Max frontend `CALLS` hops. Default: `5`. |
| `parser_type` | No | `str` | Backend alias. |

Output: Dict with `chains` and `total`. Each chain can include frontend
component/caller, API call, backend endpoint, controller, service,
repository, database, and match confidence.

Example:

```json
{
  "component_name": "UserProfileScreen",
  "fe_project_id": "web-client",
  "be_project_id": "user-service",
  "max_depth": 5
}
```

### Project Context Tools

All six tools share this common input shape (`project_id` plus optional
`parser_type`, `offset`/`limit` paging) and return
`capability_diagnostics` when the active graph lacks the required schema.

#### `get_project_modules`

Purpose: Canonical modules, descriptors, and internal/external dependencies.

Input: `project_id`, `module_id`, `module_path`,
`include_dependencies` (default `true`), `offset`, `limit` (default `50`),
`parser_type`.

Output: Module records with descriptors and dependency lists. Requires
`ProjectModule`/`BuildDescriptor` labels and `HAS_DESCRIPTOR`.

#### `get_public_apis`

Purpose: Strict source-level public/exported declarations.

Input: `project_id`, `module_id`, `symbol_kinds` (list), `language`,
`include_inferred` (default `false` — inferred C/C++ headers are opt-in),
`offset`, `limit`, `parser_type`.

Output: Public API records. Requires `ProjectModule` + `EXPOSES_API`.

#### `get_endpoints`

Purpose: Normalized HTTP, route, service, page, and gRPC endpoint inventory.

Input: `project_id`, `module_id`, `protocol`, `framework`, `http_method`,
`query`, `offset`, `limit`, `parser_type`.

Output: Endpoint records. Requires `ProjectModule` + `EXPOSES_ENDPOINT`.

#### `get_module_architecture_summary`

Purpose: Counts and bounded samples from the indexed graph; no filesystem
rescan.

Input: `project_id`, `module_id` **or** `all_modules=true`, `detail_level`
(default `standard`), `item_limit` (default `10`), `parser_type`.

Output: Aggregate counts plus bounded per-section samples.

#### `get_project_special_files`

Purpose: Descriptor roles, parse depth, diagnostics, freshness, and
redaction-safe summaries for special files (build, config, entry points).

Input: `project_id`, `module_id`, `role`, `parser`, `framework`,
`parse_depth`, `status`, `include_generated` (default `true`), `offset`,
`limit`, `parser_type`.

Output: Special-file descriptor records.

#### `get_framework_context`

Purpose: Framework instances and independently reported context dimensions.

Input: `project_id`, `module_id`, `framework`, `dimensions` (list),
`offset`, `limit`, `parser_type`.

Output: Framework instance/context records. Requires
`ProjectModule`/`FrameworkInstance` labels and `USES_FRAMEWORK`.

### Backend-Only Tools (Not On The Unified Server)

These exist in backend modules and in the `list_mcp_functions` catalog source
but are **not** exposed through `graph_mcp` today:

| Tool | Location | Inputs |
| --- | --- | --- |
| `list_workflows` | `fastmcp_server.py` | `project`, `language`, `domain`, `limit` (default 50), `project_id` |
| `get_workflow_steps` | `fastmcp_server.py` | `workflow_id`, `project_id` |
| `search_workflows` | `fastmcp_server.py` | `query`, `limit` (default 20), `project_id` |
| `analyze_proc_data_impact` | `cplus/cplus_mcp.py` | Pro*C data-impact analysis |

To expose one through the unified server, add its name to
`_UNIFIED_TOOL_NAMES`/`_PROXIED_TOOL_NAMES` in `unified_mcp.py`.

## Practical Recipes

Each step below names the tool and shows only the input object sent to that
tool. Replace sample IDs and project scopes with values from your own results.

### Recipe 1: Find Code From A Vague Bug Report

Situation: you know the behavior but not the function name. This path works in
the default FalkorDB runtime because discovery starts in Qdrant.

1. Confirm the collection with `list_qdrant_collections`:

   ```json
   {}
   ```

2. Search behavior with `semantic_search`:

   ```json
   {
     "query": "user login succeeds but payment still reports unauthenticated",
     "parser_type": "python",
     "project_id": "cortext",
     "top_k": 10
   }
   ```

3. Copy the best result ID into `get_symbol`:

   ```json
   {
     "node_id": "processPayment/2@src/payment/service.py",
     "parser_type": "python",
     "project_id": "cortext"
   }
   ```

4. Inspect local impact with `query_subgraph`:

   ```json
   {
     "function_id": "processPayment/2@src/payment/service.py",
     "direction": "all",
     "max_depth": 2,
     "parser_type": "python",
     "project_id": "cortext"
   }
   ```

Read the result as follows: incoming `CALLS` edges are callers that may need
regression tests; outgoing edges are dependencies whose behavior the function
relies on.

### Recipe 2: Find A Known Function And Its Callers

Situation: you know `validateToken` but do not know its graph ID.

1. Use `search_functions`:

   ```json
   { "query": "validateToken|AuthManager", "limit": 20, "parser_type": "cplus", "project_id": "cortext" }
   ```

2. Confirm the exact candidate with `get_symbol`:

   ```json
   { "node_id": "validateToken/1@src/auth/AuthManager.java", "parser_type": "cplus", "project_id": "cortext" }
   ```

3. Find callers only with `query_subgraph`:

   ```json
   {
     "function_id": "validateToken/1@src/auth/AuthManager.java",
     "direction": "in",
     "max_depth": 3,
     "parser_type": "cplus",
     "project_id": "cortext"
   }
   ```

4. If dynamic dispatch is possible, add `list_possible_calls`:

   ```json
   {
     "function_id": "validateToken/1@src/auth/AuthManager.java",
     "limit": 100,
     "parser_type": "cplus",
     "project_id": "cortext"
   }
   ```

### Recipe 3: Assess Change Risk

1. `query_subgraph` for the local dependency neighborhood:

   ```json
   {
     "function_id": "processPayment/2@src/payment/service.py",
     "direction": "all",
     "max_depth": 3,
     "parser_type": "python",
     "project_id": "cortext"
   }
   ```

2. `trace_flow` for broader downstream behavior:

   ```json
   {
     "start_id": "processPayment/2@src/payment/service.py",
     "direction": "downstream",
     "limit": 50,
     "parser_type": "python",
     "project_id": "cortext"
   }
   ```

3. `find_workflows_containing` then `analyze_workflow_impact` for
   workflow-aware scope (both run on the shared provider-neutral driver):

   ```json
   { "function_id": "processPayment/2@src/payment/service.py", "project_id": "cortext", "include_indirect": true, "max_depth": 4 }
   ```

   ```json
   { "function_id": "processPayment/2@src/payment/service.py", "project_id": "cortext", "direction": "downstream", "max_depth": 4 }
   ```

Use `workflow_impact.recommendation`, `risk_score`, and the affected workflow
lists to decide review depth and regression-test scope.

### Recipe 4: Trace A Frontend Endpoint To Backend Data

These fullstack bridge tools use the active graph provider.

Starting from an endpoint, call `find_callers_of_endpoint`:

```json
{
  "endpoint_path": "/api/users/:id",
  "http_method": "GET",
  "fe_project_id": "web-client",
  "be_project_id": "user-service"
}
```

Starting from a screen, call `get_api_call_chain`:

```json
{
  "component_name": "UserProfileScreen",
  "fe_project_id": "web-client",
  "be_project_id": "user-service",
  "max_depth": 5
}
```

Look for `fe_function`, `api_call`, `be_endpoint`, `be_controller`,
`be_service`, `be_repository`, and `be_database` in each returned chain.

### Recipe 5: Plan A Migration In Dependency Order

For indexed modules, use `plan_dependency_order`:

```json
{
  "modules": "src/auth,src/payment,src/orders",
  "parser_type": "cplus",
  "project_id": "cortext",
  "edge_semantics": "depends_on",
  "on_cycle": "auto_condense_scc"
}
```

Read `waves` from first to last. Items in the same wave can usually be worked
on in parallel. If the result reports cycles, inspect the SCC mapping before
assigning separate tasks. Drill down with `plan_file_dependency_order` and
`plan_function_dependency_order` for file- and function-level waves.

For a manually supplied dependency graph, call `compute_scc` first, then
`topological_sort` with the same nodes and edges:

```json
{
  "nodes": "auth,payment,orders",
  "edges": [
    { "from": "payment", "to": "auth" },
    { "from": "orders", "to": "payment" }
  ],
  "edge_semantics": "depends_on",
  "include_singletons": true
}
```

```json
{
  "nodes": "auth,payment,orders",
  "edges": [
    { "from": "payment", "to": "auth" },
    { "from": "orders", "to": "payment" }
  ],
  "edge_semantics": "depends_on",
  "output_mode": "both",
  "on_cycle": "auto_condense_scc"
}
```

### Recipe 6: Search Everything At Once (Fan-Out)

Omit `parser_type` and `project_id` on a fan-out search tool to sweep every
query engine and every registered project in one call:

```json
{ "query": "validateToken", "limit": 50 }
```

The merged response tags each hit with its source `parser_type` and keeps the
per-engine raw results under `parser_results`. Use targeted
`parser_type`/`project_id` calls once you have narrowed the candidate set.

### Recipe 7: Debug Empty Results

1. Call `list_databases` and verify the expected project graph shard is
   listed.
2. Call `list_qdrant_collections` and copy the exact collection name instead
   of guessing it.
3. Run `search_functions` with a broad known name and explicit
   `parser_type` + `project_id`.
4. Inspect `capability_diagnostics` (or `parser_results` on fan-out calls) to
   distinguish "no data" from "schema lacks required labels/relationships".
5. Call `inspect_parser_capabilities` to see advertised vs observed schema
   support and the recommended action (e.g. `run_incremental_sync`).
6. Call `list_mcp_functions` to verify that your client is using the live
   input schema rather than a cached schema.
7. Treat an empty array as a valid no-match result only when
   `capability_diagnostics` does not report omitted or unsupported
   relationships; treat connection and schema-validation errors as
   configuration/client failures.

## Testing Tools Manually

The repository includes an interactive MCP tester:

```bash
cd /Users/hieplq1.aip/AI/cortex-harness
source .venv/bin/activate
python code-tiny/testtool/mcp_tester.py --endpoint http://127.0.0.1:8788/mcp
```

Jump directly to a tool:

```bash
python code-tiny/testtool/mcp_tester.py --tool search_functions --project-id cortext
```

`code-tiny/testtool/` also contains `mcp_client.py` (scriptable client),
`mcp_batch_report.py` (batch reporting), and `tool_defaults.py`.

## Troubleshooting

| Symptom | Likely cause | What to check |
| --- | --- | --- |
| FalkorDB connection refused | FalkorDB is not running or the runtime points to the wrong host/port. | `GRAPH_PROVIDER=falkordb`, `FALKORDB_HOST`, `FALKORDB_PORT`. |
| Tool unexpectedly connects to Neo4j | The process inherited Neo4j provider variables or is running an old cached MCP process. | Check `GRAPH_PROVIDER` / `CODE_GRAPH_PROVIDER`, restart MCP, then inspect response provider diagnostics. |
| Neo4j connection refused in compatibility mode | Neo4j is not running or credentials point to the wrong host/port. | `GRAPH_PROVIDER=neo4j`, `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASS`, `NEO4J_DB`. |
| `activate_project` not found | The tool was removed; there is no session state. | Pass `parser_type` + `project_id` explicitly on every call. |
| Semantic search returns no results | Qdrant collection missing or wrong collection name. | `list_qdrant_collections`, `QDRANT_URL`; under the naming rule the collection equals `project_id`. |
| Tool routes to unexpected backend | Missing or wrong `parser_type`. | `list_parsers`, parser routing table above. |
| Results include another project | Shared services without `project_id` filter (unscoped fan-out is the default when omitted). | Pass `project_id`, `fe_project_id`, or `be_project_id` where relevant. |
| `list_up_entrypoint` returns `missing_fields` | Required `modules` input was empty after normalization. | Pass `modules: ["src/api"]` or `module: "src/api"`. |
| `get_symbol` returns `symbol: null` | Wrong ID, wrong project scope, or wrong `node_type`. | Re-run `search_functions` and verify `project_id`. |
| `/ready` returns 503 | Storage gateway is warming/draining or no committed generation is active. | Check `storage_state` in the response; wait for ingestion publish or review gateway logs (`CORTEX_STORE_GATEWAY_ENABLED`). |
| Fan-out response missing an engine's data | That engine failed; `ok` is false only when all engines fail. | Inspect `parsers_failed` / `parser_errors` / `parser_results`. |

## Maintenance Notes

- Update `tool_metadata.py` whenever adding/removing tools or changing
  documented inputs/outputs; keep `FANOUT_SEARCH_TOOL_NAMES` there in sync
  with `_FANOUT_SEARCH_TOOLS` in `unified_mcp.py`.
- Update `unified_mcp.py` when the public MCP wrapper signature changes, and
  keep `_UNIFIED_TOOL_NAMES` consistent with the catalog.
- Update `framework_registry.py` (parser profiles) and the parser routing
  table in this README together.
- Keep `fastmcp` pinned consistently with related MCP services, as noted in
  `requirements.txt`.
- If `java/java_mcp.py` should become active through the unified server, add
  it to `BACKENDS` in `unified_mcp.py` and register a profile in
  `framework_registry.py` (its `_ALLOWED_BACKENDS` currently admits only
  `android` and `cplus`).

## Framework Profiles

Framework profiles (Spring, MyBatis, Struts, Servlet/JSP, Flutter, ASP.NET)
are parser-aware capability profiles routed through the shared cplus backend.
Facts are framework-, project-, module-, and (for Servlet/JSP and ASP.NET)
generation-scoped; generation-scoped profiles automatically filter stale facts
from earlier ingestion generations.

The unified server exposes two ASP.NET parser-aware profiles through the
shared C++ backend:

- `aspnet_core`, `aspnet-core`, `asp.net-core`, and `aspnetcore`
- `aspnet_framework`, `aspnet-framework`, `asp.net-framework`, and
  `aspnetframework`

Both profiles search the shared migration labels (`HttpEndpoint`, `Route`,
`Middleware`, `Controller`, `Action`, `RazorPage`, `WebFormPage`, `Service`,
`ConfigurationKey`, and related labels) and traverse the normalized ASP.NET
relationship vocabulary. Canonical C# nodes are linked with `SEMANTIC_OF` and
remain owned by the `csharp` analyzer.
