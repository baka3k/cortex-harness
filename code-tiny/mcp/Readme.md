# Graph MCP Usage Guide

`graph_mcp` is a read-and-analysis interface for an indexed codebase. It lets an
AI client ask questions such as:

- "Where is login implemented?"
- "Which functions call this function?"
- "How does this screen reach the backend and database?"
- "Which workflows may break if I edit this symbol?"
- "Which specification sections are linked to this code?"
- "In what order should these modules be migrated?"

The default local runtime reads code-graph and Living Docs data from FalkorDB
and semantic embeddings from Qdrant. Neo4j remains available as compatibility
mode. Most tools are read-only. `annotate_node` is the notable write operation
because it adds or updates graph annotations.

The normal entry point is `unified_mcp.py`. It exposes one MCP server named
`graph_mcp` and routes each call to the appropriate backend. The current unified
server exposes **42 tools**.

## How To Use This Guide

Use the shortest route that matches what you already know:

| You need                                               | Go to                                           |
| ------------------------------------------------------ | ----------------------------------------------- |
| Start the server and make a first call                 | [Quick Start](#quick-start)                     |
| Pick the correct tool for a question                   | [Choose A Tool By Task](#choose-a-tool-by-task) |
| Look up a tool by name                                 | [Complete Tool Index](#complete-tool-index)     |
| Understand IDs, project scope, lists, and number types | [Input Guide](#input-guide)                     |
| Understand common response shapes                      | [Output Guide](#output-guide)                   |
| See every field for one tool                           | [Tool Reference](#tool-reference)               |
| Follow copy-ready multi-step examples                  | [Practical Recipes](#practical-recipes)         |
| Diagnose empty results or connection errors            | [Troubleshooting](#troubleshooting)             |

## Choose A Tool By Task

Start from the question you want to answer. The "next step" column shows the
most common follow-up call.

| Question or task                                                      | Start with                                                                                              | Next step                               |
| --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- | --------------------------------------- |
| I only know the behavior or business concept                          | [`semantic_search`](#semantic_search) in FalkorDB mode; [`explore_graph`](#explore_graph) in Neo4j mode | `get_symbol` or `query_subgraph`        |
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
| I need to find dependency cycles in supplied nodes/edges              | [`compute_scc`](#compute_scc)                                                                           | `topological_sort`                      |
| I need a linear order or parallel waves for supplied nodes/edges      | [`topological_sort`](#topological_sort)                                                                 | Execute returned waves                  |
| I need module/file/function order directly from indexed `CALLS` edges | [`plan_dependency_order`](#plan_dependency_order)                                                       | File/function planner                   |
| I need code linked to a specification document                        | [`livingdoc_get_links_for_document`](#livingdoc_get_links_for_document)                                 | `get_symbol`                            |
| I need documents linked to one code symbol                            | [`livingdoc_get_links_for_symbol`](#livingdoc_get_links_for_symbol)                                     | Open returned document anchors          |
| I need to know whether documents were ingested but not linked         | [`livingdoc_list_ingested_documents`](#livingdoc_list_ingested_documents)                               | Compare with `livingdoc_list_documents` |
| I need Living Docs health and orphan statistics                       | [`livingdoc_get_link_stats`](#livingdoc_get_link_stats)                                                 | `livingdoc_validate_links`              |

## Complete Tool Index

### Session And Discovery

| Tool                                                  | Main purpose                                  | Minimum useful input             |
| ----------------------------------------------------- | --------------------------------------------- | -------------------------------- |
| [`list_mcp_functions`](#list_mcp_functions)           | Return the live catalog of tools and schemas  | `{}`                             |
| [`list_parsers`](#list_parsers)                       | Show parser aliases and active parser context | `{}`                             |
| [`activate_project`](#activate_project)               | Set session defaults for parser and database  | `parser_type` or `database_name` |
| [`list_databases`](#list_databases)                   | List available graph names/databases          | `{}`                             |
| [`list_qdrant_collections`](#list_qdrant_collections) | List semantic-search collections              | `{}`                             |

### Search And Discovery

| Tool                                    | Main purpose                                      | Minimum useful input |
| --------------------------------------- | ------------------------------------------------- | -------------------- |
| [`explore_graph`](#explore_graph)       | Natural-language hybrid search plus graph context | `query`              |
| [`semantic_search`](#semantic_search)   | Vector similarity search                          | `query`              |
| [`search_functions`](#search_functions) | Search symbol names and qualified names           | `query`              |
| [`search_by_code`](#search_by_code)     | Search implementation text and literals           | `query`              |

### Symbols And Call Graph

| Tool                                                                      | Main purpose                                | Minimum useful input                   |
| ------------------------------------------------------------------------- | ------------------------------------------- | -------------------------------------- |
| [`get_symbol`](#get_symbol)                                               | Fetch one symbol by node ID                 | `node_id`                              |
| [`get_node_details`](#get_node_details)                                   | Fetch several nodes in one call             | `node_ids`                             |
| [`query_subgraph`](#query_subgraph)                                       | Get callers/callees around a function       | `function_id`                          |
| [`find_paths`](#find_paths)                                               | Find paths between two functions            | `start_function_id`, `end_function_id` |
| [`find_path_between_module`](#find_path_between_module)                   | Find paths between module/file tokens       | `source_module`, `target_module`       |
| [`listup_symbols_matching_file_path`](#listup_symbols_matching_file_path) | Inventory symbols by path token             | `file_path` or `modules`               |
| [`listup_class_matching_path`](#listup_class_matching_path)               | List methods for a class/type name          | `class_name`                           |
| [`list_up_entrypoint`](#list_up_entrypoint)                               | Find functions called from outside a module | `modules` or `module`                  |
| [`list_possible_calls`](#list_possible_calls)                             | Find indirect/dynamic call candidates       | Optional `function_id`                 |
| [`annotate_node`](#annotate_node)                                         | Write notes and tags to a graph node        | `node_id`                              |

### Flows And Dependency Planning

| Tool                                                                | Main purpose                                  | Minimum useful input               |
| ------------------------------------------------------------------- | --------------------------------------------- | ---------------------------------- |
| [`trace_flow`](#trace_flow)                                         | Trace inbound/outbound flow from one node     | `start_id`                         |
| [`trace_flow_between_module`](#trace_flow_between_module)           | Trace flow between module tokens              | `source_module`, `target_module`   |
| [`reconstruct_flow`](#reconstruct_flow)                             | Turn raw paths into ordered explainable flows | `entry_context_json`, `paths_json` |
| [`compute_scc`](#compute_scc)                                       | Detect strongly connected components/cycles   | `nodes`, `edges`                   |
| [`topological_sort`](#topological_sort)                             | Produce dependency order and execution waves  | `nodes`, `edges`                   |
| [`plan_dependency_order`](#plan_dependency_order)                   | Plan module order from indexed calls          | `modules`                          |
| [`plan_file_dependency_order`](#plan_file_dependency_order)         | Plan file order inside modules                | `modules`                          |
| [`plan_function_dependency_order`](#plan_function_dependency_order) | Plan function order inside modules            | `modules`                          |

### Workflows, IPC, And Fullstack

| Tool                                                      | Main purpose                                    | Minimum useful input                |
| --------------------------------------------------------- | ----------------------------------------------- | ----------------------------------- |
| [`find_screen_workflows`](#find_screen_workflows)         | Discover React/TS navigation workflows          | `project_id`, `node_a`              |
| [`find_workflows_containing`](#find_workflows_containing) | Find workflows that contain a function          | `function_id`                       |
| [`analyze_workflow_impact`](#analyze_workflow_impact)     | Score workflow impact of a function change      | `function_id`                       |
| [`get_ipc_message`](#get_ipc_message)                     | Query sender/receiver IPC messages              | `sender` or `receiver`              |
| [`find_callers_of_endpoint`](#find_callers_of_endpoint)   | Find frontend callers of a backend endpoint     | `endpoint_path`                     |
| [`get_api_call_chain`](#get_api_call_chain)               | Trace component/endpoint through backend layers | `component_name` or `endpoint_path` |

### Living Docs V2

| Tool                                                                      | Main purpose                                      | Minimum useful input          |
| ------------------------------------------------------------------------- | ------------------------------------------------- | ----------------------------- |
| [`livingdoc_get_links_by_anchor`](#livingdoc_get_links_by_anchor)         | Get all links touching one anchor                 | `anchor_id`                   |
| [`livingdoc_get_links_for_symbol`](#livingdoc_get_links_for_symbol)       | Find docs linked to a code symbol                 | `node_id` or `qualified_name` |
| [`livingdoc_get_links_for_document`](#livingdoc_get_links_for_document)   | Find code linked to a document                    | `source_file`                 |
| [`livingdoc_list_documents`](#livingdoc_list_documents)                   | List documents that already have code links       | `{}`                          |
| [`livingdoc_list_ingested_documents`](#livingdoc_list_ingested_documents) | List persisted documents, including unlinked ones | `{}`                          |
| [`livingdoc_get_link_stats`](#livingdoc_get_link_stats)                   | Summarize link health, status, and orphans        | `{}`                          |
| [`livingdoc_trace_path`](#livingdoc_trace_path)                           | Walk code-document links across multiple hops     | `start_node_id`               |
| [`livingdoc_derive_anchors_for_file`](#livingdoc_derive_anchors_for_file) | Rebuild/debug anchors for one file                | `source_file`                 |
| [`livingdoc_validate_links`](#livingdoc_validate_links)                   | Revalidate a sample of existing links             | `{}`                          |

## Source Files

| File                     | Role                                                                                                                                      |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `unified_mcp.py`         | Main MCP server. Use this for normal operation. It exposes a single endpoint and routes each tool call to the correct backend.            |
| `tool_metadata.py`       | Shared catalog for `list_mcp_functions`: descriptions, use cases, input fields, outputs, and examples.                                    |
| `fastmcp_server.py`      | Generic graph backend plus dependency-planning, workflow, and Living Docs tools.                                                          |
| `android/android_mcp.py` | Android-oriented backend. Used when `parser_type` is `android`, `android-kotlin`, or `kotlin-android`.                                    |
| `cplus/cplus_mcp.py`     | C/C++-family backend. In the unified server this also receives `java`, `kotlin`, `jvm`, `delphi`, `pascal`, and VB-family parser aliases. |
| `java/java_mcp.py`       | Standalone Java backend implementation. It is present in this directory, but the current unified router does not add it to `BACKENDS`.    |
| `services/*`             | Shared services for symbol lookup, graph exploration, flow reconstruction, workflow discovery, and impact analysis.                       |

## Quick Start

This walkthrough starts the server, confirms the live catalog, selects a
backend, finds a symbol, and inspects its local call graph.

### 1. Start FalkorDB And Qdrant

The default service endpoints are:

| Service  | Default URL                           | Used by                                                  |
| -------- | ------------------------------------- | -------------------------------------------------------- |
| FalkorDB | `127.0.0.1:6380`, graph `hyper_graph` | Symbol, call graph, workflow, API, and Living Docs tools |
| Qdrant   | `http://localhost:6333`               | `semantic_search` and semantic parts of `explore_graph`  |

FalkorDB is the default graph provider in the current local runtime. Qdrant is
only required for semantic/vector queries. Neo4j can be selected explicitly for
legacy compatibility; see [Graph Provider Configuration](#graph-provider-configuration).

### 2. Start `graph_mcp`

```bash
cd /Users/hieplq1.rpm/Hyper-Dev/hyper-pack/hyper-dev/hyper-graph
source .venv/bin/activate
GRAPH_PROVIDER=falkordb \
FALKORDB_HOST=127.0.0.1 \
FALKORDB_PORT=6380 \
FALKORDB_GRAPH=hyper_graph \
python mcp/unified_mcp.py --transport streamable-http --host 127.0.0.1 --port 8788 --path /mcp
```

The client endpoint is `http://127.0.0.1:8788/mcp`.

### 3. Confirm The Live Tool Catalog

Select tool `list_mcp_functions` and send:

```json
{}
```

Expected top-level output shape:

```json
{
  "total_count": 42,
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

The return value is currently a JSON string, so some clients display it as text
that contains JSON. Parse the text once if your client does not do that
automatically.

### 4. Set Session Defaults

Select tool `activate_project` and send:

```json
{
  "parser_type": "cplus",
  "database_name": "hyper_graph"
}
```

Use `android` for the Android backend. Java, Kotlin, C/C++, Delphi, Pascal, and
VB-family aliases currently route through the `cplus` backend.

### 5. Find A Symbol

Select tool `search_functions` and send:

```json
{
  "query": "AuthManager|validateToken",
  "db": "hyper_graph",
  "limit": "20",
  "project_id": "hypergraph"
}
```

Copy the returned `id`, `node_id`, or `symbol_id`. Exact field names can vary by
backend, but that value is the graph ID needed by the next call.

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

### 6. Inspect Callers And Callees

Select tool `query_subgraph` and replace `function_id` with the ID returned in
step 5:

```json
{
  "function_id": "validateToken/1@src/auth/AuthManager.java",
  "db": "hyper_graph",
  "direction": "all",
  "max_depth": 2,
  "project_id": "hypergraph"
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

Install dependencies from the Hyper Graph root:

```bash
cd /Users/hieplq1.rpm/Hyper-Dev/hyper-pack/hyper-dev/hyper-graph
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Required services:

| Service     | Default                                            |
| ----------- | -------------------------------------------------- |
| FalkorDB    | Host `127.0.0.1`, port `6380`, graph `hyper_graph` |
| Qdrant HTTP | `http://localhost:6333`                            |

Useful environment variables:

```bash
export GRAPH_PROVIDER=falkordb
export FALKORDB_HOST=127.0.0.1
export FALKORDB_PORT=6380
export FALKORDB_GRAPH=hyper_graph
export QDRANT_URL=http://localhost:6333
export CODE_EMBEDDING_MODEL=jinaai/jina-embeddings-v3
export EMBED_DEVICE=cpu
```

Optional FalkorDB authentication:

```bash
export FALKORDB_USERNAME=your_username
export FALKORDB_PASSWORD=your_password
```

## Graph Provider Configuration

### FalkorDB: Current Default

The Hyper Pack runtime launchers set these defaults:

```bash
export GRAPH_PROVIDER=${GRAPH_PROVIDER:-falkordb}
export FALKORDB_HOST=${FALKORDB_HOST:-127.0.0.1}
export FALKORDB_PORT=${FALKORDB_PORT:-6380}
export FALKORDB_GRAPH=${FALKORDB_GRAPH:-hyper_graph}
```

Set these variables **before** the Python process starts. The backend reads the
provider during module import. A bare `python mcp/unified_mcp.py ...` command
without `GRAPH_PROVIDER` still follows the source-level legacy fallback to
Neo4j, while the Hyper Pack service launcher explicitly supplies FalkorDB as
the runtime default.

For tools with a `db` field, use the FalkorDB graph name:

```json
{
  "db": "hyper_graph"
}
```

Several unified wrapper signatures still show the legacy literal
`db="neo4j"`. That literal is not the default provider contract. In FalkorDB
mode, pass `db: "hyper_graph"` explicitly or call `activate_project` with
`database_name: "hyper_graph"`. Explicit `db` values are safest for clients
that cache tool schemas or always send optional defaults.

The `cplus`/generic and planner/Living Docs paths are provider-aware. The
current `android` backend remains Neo4j-specific, so Android graph queries still
require the Neo4j compatibility configuration below.

Provider support is currently mixed because several specialized tools still
open a raw Neo4j driver instead of using the shared graph-driver abstraction:

| Tool family                                                                      | FalkorDB default runtime                                                                             | Neo4j compatibility mode |
| -------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- | ------------------------ |
| `cplus`/generic search, symbol, path, trace, IPC, and annotation tools           | Supported                                                                                            | Supported                |
| Dependency planners and Living Docs tools dispatched through `fastmcp_server.py` | Supported                                                                                            | Supported                |
| `semantic_search` and `list_qdrant_collections`                                  | Qdrant-backed; graph expansion depends on selected backend                                           | Supported                |
| `explore_graph`                                                                  | Semantic/Qdrant behavior can work, but keyword and graph-expanded modes still use a raw Neo4j driver | Fully supported          |
| `find_callers_of_endpoint`, `get_api_call_chain`                                 | Not currently provider-aware                                                                         | Supported                |
| `find_workflows_containing` and workflow layer of `analyze_workflow_impact`      | Not currently provider-aware                                                                         | Supported                |
| `android` backend                                                                | Not currently provider-aware                                                                         | Supported                |

This guide marks Neo4j-only tools in their individual sections. Do not point a
Neo4j-only tool at `db: "hyper_graph"`; it will still try the Neo4j Bolt driver.

### Neo4j: Compatibility Mode

Use Neo4j only when an older flow or the Android backend requires it:

```bash
export GRAPH_PROVIDER=neo4j
export NEO4J_URI=bolt://localhost:7687
export NEO4J_USER=neo4j
export NEO4J_PASS=your_password
export NEO4J_DB=neo4j
```

Then use `database_name: "neo4j"` or `db: "neo4j"` in calls.

## Start The Unified MCP Server

```bash
cd /Users/hieplq1.rpm/Hyper-Dev/hyper-pack/hyper-dev/hyper-graph
source .venv/bin/activate
GRAPH_PROVIDER=falkordb \
FALKORDB_HOST=127.0.0.1 \
FALKORDB_PORT=6380 \
FALKORDB_GRAPH=hyper_graph \
python mcp/unified_mcp.py --transport streamable-http --host 127.0.0.1 --port 8788 --path /mcp
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
```

## Input Guide

### The Most Important Input Types

| Input kind             | Example                                       | Meaning                                          | How to obtain it                                                 |
| ---------------------- | --------------------------------------------- | ------------------------------------------------ | ---------------------------------------------------------------- | ------------------------------------------------------- |
| Natural-language query | `"payment fails after login"`                 | Behavior or concept to find                      | Write it yourself; use with `explore_graph` or `semantic_search` |
| Name query             | `"AuthManager                                 | validateToken"`                                  | Symbol name or alternatives                                      | Write known name fragments; use with `search_functions` |
| Node/function ID       | `"validateToken/1@src/auth/AuthManager.java"` | Stable graph identifier, not just a display name | Copy from search, path, or subgraph results                      |
| Module/path token      | `"src/auth"`                                  | Substring matched against indexed file paths     | Use a repository directory or filename fragment                  |
| `project_id`           | `"hypergraph"`                                | Ingestion scope inside a shared graph            | Copy from ingestion config/result or an existing node payload    |
| `db`                   | `"hyper_graph"`                               | FalkorDB graph name or Neo4j database name       | Use `list_databases`; current default runtime uses `hyper_graph` |
| Qdrant `collection`    | `"hypergraph_73eddc5fcc__python_functions"`   | Vector collection to search                      | Use `list_qdrant_collections`                                    |
| `parser_type`          | `"cplus"`                                     | Selects the routed backend                       | Use `list_parsers` and the routing table below                   |

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
  "db": "hyper_graph",
  "limit": "20"
}
```

Do not wrap unified calls in a backend-style `payload` object unless you are
calling the backend module directly. The unified server builds backend payloads
for you.

Wrong for the unified server:

```json
{
  "payload": {
    "query": "AuthManager"
  }
}
```

### Common Fields

Important normalization rules:

| Rule                        | Details                                                                                                                                                                                     |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Empty strings               | Treated as "not provided".                                                                                                                                                                  |
| `activate_project` defaults | Stores `parser_type` and `database_name` for later calls in the same server session.                                                                                                        |
| `db` selection              | Current runtime uses FalkorDB graph `hyper_graph`. Some wrappers still expose legacy `neo4j` defaults, so pass `db: "hyper_graph"` explicitly in FalkorDB mode.                             |
| Numeric strings             | Several public wrappers accept numbers as strings, for example `limit: "50"` or `top_k: "10"`.                                                                                              |
| List aliases                | The router normalizes aliases such as `module` -> `modules`, `source_module` -> `source_modules`, `target_module` -> `target_modules`, and `class_name` -> `class_names` for backend calls. |
| `project_id`                | Use it when multiple projects are indexed into the same graph/Qdrant services.                                                                                                              |
| `node_type`                 | Common filters are `code` and `doc`. Use `doc` when searching document nodes rather than code symbols.                                                                                      |
| `content_mode`              | Backend catalog supports `auto`, `summary`, `comment`, `code`, and `name`; not every public wrapper exposes this field directly.                                                            |

### Number Types

The unified wrapper has two historical number styles. Match the public tool
schema exactly:

| Style          | Fields                                                                      | Correct sample                       |
| -------------- | --------------------------------------------------------------------------- | ------------------------------------ |
| Numeric string | Many search/trace wrappers use `limit`, `top_k`, and `max_depth` as strings | `"limit": "20"`                      |
| JSON number    | Workflow, planner, and Living Docs wrappers use integers/floats             | `"max_depth": 4`, `"min_score": 0.7` |

When uncertain, call `list_mcp_functions` and use the live `inputs[].type`.

### Lists And Embedded JSON

Use real JSON arrays when a field is declared as `List[...]`:

```json
{
  "modules": ["src/auth", "src/payment"],
  "node_types": ["Function", "Class"]
}
```

`get_node_details.node_ids` is a string in the public wrapper. Separate IDs by
comma or semicolon:

```json
{
  "node_ids": "func_1,func_2,func_3"
}
```

`reconstruct_flow` is intentionally different: its two inputs are JSON strings,
not nested objects. Escape the inner JSON:

```json
{
  "entry_context_json": "{\"type\":\"backend\",\"entry_point\":\"main\",\"entry_node_id\":\"n1\"}",
  "paths_json": "[{\"path_id\":\"p1\",\"nodes\":[{\"node_id\":\"n1\",\"name\":\"main\"}],\"edges\":[]}]"
}
```

### Required Scope Fields

Pass `project_id` whenever more than one project shares the same database.
Fullstack tools use separate scopes:

```json
{
  "endpoint_path": "/api/users/:id",
  "http_method": "GET",
  "fe_project_id": "web-client",
  "be_project_id": "user-service",
  "db": "hyper_graph"
}
```

If a query unexpectedly returns another repository's symbols, missing or wrong
project scope is the first thing to check.

## Output Guide

There is no single response schema for all 42 tools. The unified wrapper
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

Connection failures, invalid database credentials, and unavailable backend
tools may still be raised as MCP errors.

## Parser Routing

Call `activate_project` once at the beginning of a session if you will run
multiple related queries.

```json
{
  "parser_type": "cplus",
  "database_name": "hyper_graph"
}
```

Current unified routing:

| `parser_type` values                                                                                                      | Backend                                                |
| ------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| `android`, `android-kotlin`, `kotlin-android`                                                                             | `android/android_mcp.py`                               |
| `cplus`, `cpp`, `c++`, `c`, `clang`, `java`, `kotlin`, `jvm`, `go`, `perl`, `rust`, `swift`, `delphi`, `pascal`, `vbnet`, `vb6`, `vba`, `vbscript`, `st` | `cplus/cplus_mcp.py` |
| Empty or unknown                                                                                                          | `MCP_UNIFIED_DEFAULT_BACKEND`, falling back to `cplus` |

## Recommended Query Workflow

1. Call `list_mcp_functions` to confirm the live tool catalog.
2. Call `list_parsers` and `list_databases` if you are unsure about routing.
3. Call `activate_project` to set default parser/database context.
4. Use `semantic_search` for vague natural-language discovery in FalkorDB mode. Use `explore_graph` hybrid/expanded modes only in Neo4j mode.
5. Use `search_functions`, `search_by_code`, or `listup_symbols_matching_file_path` to get stable node IDs.
6. Use `get_symbol`, `get_node_details`, `query_subgraph`, `find_paths`, or impact tools for exact analysis.

## Tool Reference

### Discovery And Session Tools

#### `list_mcp_functions`

Purpose: Lists every available MCP tool with description, use cases, inputs,
outputs, and examples. This is the first call clients should make because the
live server can differ from stale documentation.

Input: none.

Output: JSON string containing `total_count` and `functions`. Each function
entry includes `name`, `description`, `use_cases`, `inputs`, `output`, and
`example`.

Use when:

- Starting a new MCP session.
- Building or debugging an MCP client.
- Checking whether a function is available after code changes.

Example:

```json
{}
```

#### `list_parsers`

Purpose: Lists parser aliases supported by the unified router and reports the
default/active parser context.

Input: none.

Output: Dict with `parsers`, `default_backend`, and `active_parser_type`.

Use when:

- You do not know which `parser_type` to pass.
- You need to confirm whether the current server supports a language alias.
- You are debugging unexpected backend routing.

Example:

```json
{}
```

#### `activate_project`

Purpose: Sets default `parser_type` and graph name/database for subsequent calls
in the same server process.

Input:

| Field           | Required | Type  | Meaning                                                                             |
| --------------- | -------- | ----- | ----------------------------------------------------------------------------------- |
| `parser_type`   | No       | `str` | Parser/backend alias, for example `cplus`, `android`, `java`, `kotlin`, or `perl`.  |
| `database_name` | No       | `str` | FalkorDB graph name or Neo4j database name. Current runtime default: `hyper_graph`. |

Output: Dict with `parser_type`, `database_name`, `activated` or backend
status fields, plus `backend`.

Use when:

- Running several related calls against the same project/database.
- Avoiding repeated `parser_type` and `db` parameters.
- Switching between Android and C/C++-family backends.

Example:

```json
{
  "parser_type": "perl",
  "database_name": "hyper_graph"
}
```

#### `list_databases`

Purpose: Lists graph names/databases visible to the selected backend. In
FalkorDB mode this normally returns the configured graph, such as
`hyper_graph`; in Neo4j mode it lists Neo4j databases.

Input:

| Field         | Required | Type  | Meaning                              |
| ------------- | -------- | ----- | ------------------------------------ |
| `parser_type` | No       | `str` | Backend alias to use for the lookup. |

Output: Dict with graph/database names, or a provider connection error.

Use when:

- You do not know the correct `db` value.
- You need to verify graph-provider connectivity.
- You are switching between indexed environments.

Example:

```json
{
  "parser_type": "cplus"
}
```

#### `list_qdrant_collections`

Purpose: Lists Qdrant vector collections used by semantic search.

Input:

| Field             | Required | Type   | Meaning                                                                |
| ----------------- | -------- | ------ | ---------------------------------------------------------------------- |
| `parser_type`     | No       | `str`  | Backend alias.                                                         |
| `db`              | No       | `str`  | Graph name/database context. Use `hyper_graph` in the current runtime. |
| `qdrant_url`      | No       | `str`  | Qdrant HTTP URL, defaults to `QDRANT_URL` or `http://localhost:6333`.  |
| `include_vectors` | No       | `bool` | Include vector metadata when supported.                                |

Output: Dict with Qdrant collection names and optional metadata.

Use when:

- Choosing the `collection` argument for `semantic_search` or `explore_graph`.
- Checking whether embeddings were generated for a project.
- Debugging empty semantic search results.

Example:

```json
{
  "qdrant_url": "http://localhost:6333",
  "include_vectors": false
}
```

### Semantic And Broad Search Tools

#### `explore_graph`

Purpose: Intent-aware graph search for natural language questions, bug
descriptions, requirement paragraphs, or vague concepts. It combines semantic
vector search, keyword signals, and graph expansion.

Input:

| Field        | Required | Type   | Meaning                                                       |
| ------------ | -------- | ------ | ------------------------------------------------------------- |
| `query`      | Yes      | `str`  | Natural language query. English and Vietnamese are supported. |
| `mode`       | No       | `str`  | `semantic`, `hybrid`, or `graph_expanded`. Default: `hybrid`. |
| `top_k`      | No       | `str`  | Max matched nodes. Default: `10`.                             |
| `db`         | No       | `str`  | Database override used by graph-assisted modes.               |
| `collection` | No       | `str`  | Qdrant collection override.                                   |
| `debug`      | No       | `bool` | Include per-signal scoring details.                           |

Output: Dict with `matched_nodes`, `entry_points`, `related_paths`,
`explanation`, `confidence`, `query_analysis`, and `mode`.

Use when:

- You only know the business concept, not a function name.
- You are triaging a bug from a natural-language report.
- You want both candidate symbols and graph-neighbor context in one call.
- You need a first-pass map before exact symbol lookup.

Provider note: `semantic` mode is primarily Qdrant-backed. The current
`hybrid` keyword path and `graph_expanded` path still open a raw Neo4j driver,
so use those modes only in Neo4j compatibility mode until `explore_graph` is
migrated to the shared provider abstraction.

Example:

```json
{
  "query": "function xu ly thanh toan bi loi khi user chua login",
  "mode": "semantic",
  "top_k": "15",
  "collection": "my_project_python_functions"
}
```

#### `semantic_search`

Purpose: Searches Qdrant embeddings for code or comments similar in meaning to
the query.

Input:

| Field         | Required | Type  | Meaning                                                                |
| ------------- | -------- | ----- | ---------------------------------------------------------------------- |
| `query`       | Yes      | `str` | Natural language query or code snippet.                                |
| `parser_type` | No       | `str` | Backend alias.                                                         |
| `db`          | No       | `str` | Graph name/database context. Use `hyper_graph` in the current runtime. |
| `top_k`       | No       | `str` | Number of results.                                                     |
| `collection`  | No       | `str` | Qdrant collection or project collection prefix.                        |
| `project_id`  | No       | `str` | Project scope for indexed data.                                        |

Output: Dict with `results`, or backend-specific semantic result fields. Each
result normally includes score, node metadata, file path, symbol name, and code
or summary content.

Use when:

- Exact string search is too brittle.
- You want similar implementations.
- You are looking for code by behavior rather than name.
- You need a bridge from requirements text to candidate symbols.

Example:

```json
{
  "query": "allocate memory safely",
  "top_k": "5",
  "collection": "my_project_cplus_functions"
}
```

#### `search_functions`

Purpose: Searches code/doc nodes by name or qualified name. This is the fastest
tool when you know part of a function, class, type, or symbol name.

Input:

| Field           | Required | Type   | Meaning                                                    |
| --------------- | -------- | ------ | ---------------------------------------------------------- | ------------------------ |
| `query`         | Yes      | `str`  | Search terms. Backend catalog supports `termA              | termB` for alternatives. |
| `parser_type`   | No       | `str`  | Backend alias.                                             |
| `db`            | No       | `str`  | Graph name/database. Current runtime graph: `hyper_graph`. |
| `limit`         | No       | `str`  | Max results.                                               |
| `node_type`     | No       | `str`  | `code` or `doc`.                                           |
| `expand_search` | No       | `bool` | Include compact cross-domain context when supported.       |
| `project_id`    | No       | `str`  | Project scope.                                             |

Output: Dict with `functions` or backend fields such as `results`, `ids`, and
`db`. Results include node IDs for follow-up calls.

Use when:

- You need a stable `node_id` or `symbol_id`.
- You know part of a method/class/function name.
- You want a quick inventory of matching symbols before graph traversal.
- You are about to call `get_symbol`, `query_subgraph`, or `find_paths`.

Example:

```json
{
  "query": "handleLogin|AuthManager",
  "limit": "20",
  "parser_type": "cplus",
  "db": "hyper_graph"
}
```

#### `search_by_code`

Purpose: Searches function bodies or implementation text for exact code
snippets, string literals, API names, or regex-like fragments.

Input:

| Field           | Required | Type   | Meaning                                                         |
| --------------- | -------- | ------ | --------------------------------------------------------------- |
| `query`         | Yes      | `str`  | Code text to match. Backend search is generally case-sensitive. |
| `parser_type`   | No       | `str`  | Backend alias.                                                  |
| `db`            | No       | `str`  | Graph name/database. Current runtime graph: `hyper_graph`.      |
| `limit`         | No       | `str`  | Max results.                                                    |
| `node_type`     | No       | `str`  | `code` or `doc`.                                                |
| `expand_search` | No       | `bool` | Include compact cross-domain context when supported.            |
| `project_id`    | No       | `str`  | Project scope.                                                  |

Output: Dict with `results` or backend-specific matching nodes.

Use when:

- You know an exact API call, SQL fragment, log message, or literal.
- You need to find all places using a legacy pattern.
- Semantic search returns too many broad candidates.
- You are validating whether a specific code idiom exists.

Example:

```json
{
  "query": "DataNormal|Authen|Login|SignIn|Account",
  "db": "hyper_graph",
  "limit": "500"
}
```

### Symbol And Code Graph Tools

#### `get_symbol`

Purpose: Fetches full metadata for a single node by ID.

Input:

| Field         | Required | Type  | Meaning                                                    |
| ------------- | -------- | ----- | ---------------------------------------------------------- |
| `node_id`     | Yes      | `str` | Node ID from search results.                               |
| `parser_type` | No       | `str` | Backend alias.                                             |
| `db`          | No       | `str` | Graph name/database. Current runtime graph: `hyper_graph`. |
| `node_type`   | No       | `str` | Optional domain filter: `code` or `doc`.                   |
| `project_id`  | No       | `str` | Project scope.                                             |

Output: Dict with symbol metadata such as `name`, `qualified_name`,
`file_path`, `signature`, `code`, `comment`, line numbers, and raw backend
fields. If not found, returns `symbol: null` with a message.

Use when:

- You need the exact implementation for a node.
- You want to inspect metadata before modifying a function.
- You are confirming a search result before impact analysis.

Example:

```json
{
  "node_id": "func_12345",
  "db": "hyper_graph"
}
```

#### `get_node_details`

Purpose: Batch version of `get_symbol`; fetches multiple node records in one
call.

Input:

| Field         | Required | Type  | Meaning                                                                                              |
| ------------- | -------- | ----- | ---------------------------------------------------------------------------------------------------- |
| `node_ids`    | Yes      | `str` | One ID or a comma/semicolon-separated string of IDs. Backend also accepts lists after normalization. |
| `parser_type` | No       | `str` | Backend alias.                                                                                       |
| `db`          | No       | `str` | Graph name/database. Current runtime graph: `hyper_graph`.                                           |
| `node_type`   | No       | `str` | Optional domain filter.                                                                              |
| `project_id`  | No       | `str` | Project scope.                                                                                       |

Output: Dict with `nodes` or backend-specific node-detail list.

Use when:

- You have IDs from `find_paths` or `query_subgraph`.
- You need to render or inspect several related symbols.
- You want fewer MCP round trips than repeated `get_symbol` calls.

Example:

```json
{
  "node_ids": "func_1,func_2,func_3",
  "db": "hyper_graph"
}
```

#### `query_subgraph`

Purpose: Returns call-graph context around one function: callers, callees, or
both, depending on direction and backend support.

Input:

| Field           | Required | Type   | Meaning                                                                                  |
| --------------- | -------- | ------ | ---------------------------------------------------------------------------------------- |
| `function_id`   | Yes      | `str`  | Starting function/symbol node ID.                                                        |
| `parser_type`   | No       | `str`  | Backend alias.                                                                           |
| `db`            | No       | `str`  | Graph name/database. Current runtime graph: `hyper_graph`.                               |
| `limit`         | No       | `str`  | Optional backend result cap.                                                             |
| `node_type`     | No       | `str`  | Optional domain filter.                                                                  |
| `expand_search` | No       | `bool` | Include compact cross-domain bridge context when supported.                              |
| `project_id`    | No       | `str`  | Project scope.                                                                           |
| `direction`     | No       | `str`  | Public wrapper default is `all`; backend catalog also describes `out`, `in`, and `both`. |
| `max_depth`     | No       | `int`  | Traversal depth. Default: `2`.                                                           |

Output: Dict with `nodes` and `edges`; empty result is normalized to
`reason: no_subgraph`.

Use when:

- You are doing impact analysis around one function.
- You need direct callers/callees before a refactor.
- You need test scope from graph neighbors.
- You want to understand local dependencies without computing full paths.

Example:

```json
{
  "function_id": "func_main",
  "direction": "out",
  "max_depth": 3,
  "db": "hyper_graph"
}
```

#### `find_paths`

Purpose: Finds call paths between two specific function nodes.

Input:

| Field               | Required | Type   | Meaning                                                    |
| ------------------- | -------- | ------ | ---------------------------------------------------------- |
| `start_function_id` | Yes      | `str`  | Starting function ID.                                      |
| `end_function_id`   | Yes      | `str`  | Target function ID.                                        |
| `parser_type`       | No       | `str`  | Backend alias.                                             |
| `db`                | No       | `str`  | Graph name/database. Current runtime graph: `hyper_graph`. |
| `limit`             | No       | `str`  | Optional max result count.                                 |
| `node_type`         | No       | `str`  | Optional domain filter.                                    |
| `expand_search`     | No       | `bool` | Include bridge context when supported.                     |
| `project_id`        | No       | `str`  | Project scope.                                             |

Output: Dict with `paths`, and often `nodes` and `edges`. No-path runtime
errors are normalized to `paths: []`, `nodes: []`, `edges: []`,
`reason: no_path`.

Use when:

- You need to prove how one function reaches another.
- You are debugging unexpected side effects.
- You want an execution chain for documentation or code review.
- You need candidate path data for `reconstruct_flow`.

Example:

```json
{
  "start_function_id": "main",
  "end_function_id": "malloc",
  "limit": "10"
}
```

#### `find_path_between_module`

Purpose: Finds call paths between files/modules using file path tokens.

Input:

| Field           | Required | Type  | Meaning                                                    |
| --------------- | -------- | ----- | ---------------------------------------------------------- |
| `source_module` | Yes      | `str` | Source file/module token.                                  |
| `target_module` | Yes      | `str` | Target file/module token.                                  |
| `parser_type`   | No       | `str` | Backend alias.                                             |
| `db`            | No       | `str` | Graph name/database. Current runtime graph: `hyper_graph`. |
| `limit`         | No       | `str` | Max paths.                                                 |
| `project_id`    | No       | `str` | Project scope.                                             |

Output: Dict with `paths` or backend-specific module graph data.

Use when:

- You care about module coupling rather than individual functions.
- You are planning a refactor across directories.
- You need to verify whether one subsystem can reach another.
- You want architectural evidence for dependency cleanup.

Example:

```json
{
  "source_module": "src/auth",
  "target_module": "src/payment",
  "limit": "20"
}
```

#### `listup_symbols_matching_file_path`

Purpose: Lists functions/classes/types in files whose path contains one or
more tokens.

Input:

| Field         | Required | Type        | Meaning                                                    |
| ------------- | -------- | ----------- | ---------------------------------------------------------- |
| `file_path`   | No       | `str`       | Convenience single path token.                             |
| `modules`     | No       | `List[str]` | Explicit list of path tokens.                              |
| `node_types`  | No       | `List[str]` | Optional filters such as `Function`, `Class`, or `Type`.   |
| `parser_type` | No       | `str`       | Backend alias.                                             |
| `db`          | No       | `str`       | Graph name/database. Current runtime graph: `hyper_graph`. |
| `project_id`  | No       | `str`       | Project scope.                                             |

At least one of `file_path` or `modules` is required.

Output: Dict with `symbols` or backend-specific symbol inventory.

Use when:

- You know the file but not the function names.
- You want the API surface of a file/directory.
- You need a complete symbol inventory before touching a module.
- You are mapping a source file to graph IDs.

Example:

```json
{
  "file_path": "src/auth/router.ts",
  "node_types": ["Function"]
}
```

#### `listup_class_matching_path`

Purpose: Lists functions/methods declared in classes/types whose names match a
pattern.

Input:

| Field         | Required | Type  | Meaning                                                      |
| ------------- | -------- | ----- | ------------------------------------------------------------ |
| `class_name`  | Yes      | `str` | Class/type name pattern. Routed to backend as `class_names`. |
| `parser_type` | No       | `str` | Backend alias.                                               |
| `db`          | No       | `str` | Graph name/database. Current runtime graph: `hyper_graph`.   |
| `project_id`  | No       | `str` | Project scope.                                               |

Output: Dict with `functions` or backend-specific class/method structure.

Use when:

- You need all methods on a class.
- You are reviewing class-level API behavior.
- You know a type name but not its method IDs.

Example:

```json
{
  "class_name": "AuthManager",
  "db": "hyper_graph"
}
```

#### `list_up_entrypoint`

Purpose: Finds entry point functions in target modules: functions inside the
module that are called from outside the module.

Input:

| Field         | Required | Type        | Meaning                                                                                          |
| ------------- | -------- | ----------- | ------------------------------------------------------------------------------------------------ |
| `modules`     | Yes      | `List[str]` | Module/file path tokens.                                                                         |
| `module`      | No       | `str`       | Single-module alias for clients that cannot pass lists.                                          |
| `parser_type` | No       | `str`       | Backend alias.                                                                                   |
| `db`          | Yes      | `str`       | Graph name/database. The wrapper advertises legacy `neo4j`; pass `hyper_graph` in FalkorDB mode. |
| `project_id`  | No       | `str`       | Project scope.                                                                                   |

Output: Dict with `entrypoints`. If required fields are missing, returns
`ok: false`, `missing_fields`, accepted formats, and an example.

Use when:

- You want to identify a module's public API surface.
- You are planning where to start reading a subsystem.
- You need change-impact entry points for tests or review.
- You are checking module boundary leaks.

Example:

```json
{
  "modules": ["src/api"],
  "db": "hyper_graph"
}
```

#### `list_possible_calls`

Purpose: Lists `POSSIBLE_CALLS` relationships such as function pointer calls,
virtual calls, and callback registrations.

Input:

| Field         | Required | Type  | Meaning                                                    |
| ------------- | -------- | ----- | ---------------------------------------------------------- |
| `function_id` | No       | `str` | Optional function to scope the indirect-call lookup.       |
| `parser_type` | No       | `str` | Backend alias.                                             |
| `db`          | No       | `str` | Graph name/database. Current runtime graph: `hyper_graph`. |
| `limit`       | No       | `str` | Max results.                                               |
| `project_id`  | No       | `str` | Project scope.                                             |

Output: Dict with `calls` or backend-specific possible-call edge records.

Use when:

- Static `CALLS` edges are incomplete because of callbacks or dynamic dispatch.
- You are analyzing C/C++ function pointers.
- You are tracing Android callback-style behavior.
- You want a conservative impact set.

Example:

```json
{
  "function_id": "func_123",
  "limit": "50"
}
```

#### `annotate_node`

Purpose: Adds or updates review annotations on a graph node.

Input:

| Field         | Required | Type  | Meaning                                                    |
| ------------- | -------- | ----- | ---------------------------------------------------------- |
| `node_id`     | Yes      | `str` | Target node ID.                                            |
| `note`        | No       | `str` | Free-form note.                                            |
| `tags`        | No       | `str` | Comma-separated tags.                                      |
| `parser_type` | No       | `str` | Backend alias.                                             |
| `db`          | No       | `str` | Graph name/database. Current runtime graph: `hyper_graph`. |
| `project_id`  | No       | `str` | Project scope.                                             |

Output: Dict with updated node/annotation data.

Use when:

- Marking a function for review.
- Tagging security, migration, or tech-debt concerns.
- Recording analysis findings directly in the graph.

Example:

```json
{
  "node_id": "func_123",
  "note": "Buffer overflow risk",
  "tags": "security,review"
}
```

### Flow And Module Tracing Tools

#### `trace_flow`

Purpose: Traces flow paths from a starting node using backend-specific default
relationships.

Input:

| Field         | Required | Type  | Meaning                                                    |
| ------------- | -------- | ----- | ---------------------------------------------------------- |
| `start_id`    | Yes      | `str` | Start function/symbol ID.                                  |
| `direction`   | No       | `str` | Direction filter, backend-specific.                        |
| `parser_type` | No       | `str` | Backend alias.                                             |
| `db`          | No       | `str` | Graph name/database. Current runtime graph: `hyper_graph`. |
| `limit`       | No       | `str` | Max results.                                               |
| `project_id`  | No       | `str` | Project scope.                                             |

Output: Dict with `flows`, `nodes`, `edges`, or backend-specific traced paths.
No-path runtime errors are normalized to `nodes: []`, `edges: []`,
`reason: no_path`.

Use when:

- You need broader flow tracing than a single `find_paths` pair.
- You are exploring downstream or upstream effects from one node.
- You want backend-specific traversal behavior.

Example:

```json
{
  "start_id": "func_123",
  "direction": "downstream",
  "limit": "25"
}
```

#### `trace_flow_between_module`

Purpose: Traces flow paths between modules using backend-specific relationship
logic.

Input:

| Field           | Required | Type  | Meaning                                                    |
| --------------- | -------- | ----- | ---------------------------------------------------------- |
| `source_module` | Yes      | `str` | Source module/file token.                                  |
| `target_module` | Yes      | `str` | Target module/file token.                                  |
| `parser_type`   | No       | `str` | Backend alias.                                             |
| `db`            | No       | `str` | Graph name/database. Current runtime graph: `hyper_graph`. |
| `limit`         | No       | `str` | Max results.                                               |
| `project_id`    | No       | `str` | Project scope.                                             |

Output: Dict with `flows`, `nodes`, `edges`, or backend-specific module paths.
No-path runtime errors are normalized to `nodes: []`, `edges: []`,
`reason: no_path`.

Use when:

- You need cross-module flow evidence.
- You are analyzing architecture or subsystem coupling.
- You need Android route/intent/event style relationships where supported.

Example:

```json
{
  "source_module": "ui/",
  "target_module": "service/",
  "limit": "20",
  "parser_type": "android"
}
```

#### `reconstruct_flow`

Purpose: Converts candidate graph paths into grounded, ordered execution-flow
objects that are easier for agents and reviewers to reason about.

Input:

| Field                | Required | Type  | Meaning                                                                                  |
| -------------------- | -------- | ----- | ---------------------------------------------------------------------------------------- |
| `entry_context_json` | Yes      | `str` | JSON object string with `type`, `entry_point`, `entry_node_id`, `screen`, and `trigger`. |
| `paths_json`         | Yes      | `str` | JSON array string of path objects with `nodes` and `edges`.                              |

Output: Dict with `flows` and `uncertainties`. Each flow contains `flow_id`,
`title`, `type`, `confidence`, `entry_node_id`, `paths_used`,
`discarded_paths`, and ordered `steps`.

Use when:

- You already have path data from `find_paths` or `query_subgraph`.
- You want an explainable execution narrative.
- You need to distinguish direct edges from inferred/shared-state steps.
- You are preparing impact-analysis notes for an agent or reviewer.

Example:

```json
{
  "entry_context_json": "{\"type\":\"backend\",\"entry_point\":\"main\",\"entry_node_id\":\"n1\",\"screen\":null,\"trigger\":null}",
  "paths_json": "[{\"path_id\":\"path_1\",\"nodes\":[{\"node_id\":\"n1\",\"name\":\"main\",\"mapped_type\":\"function\",\"location\":{\"file\":\"main.c\",\"line\":10}}],\"edges\":[]}]"
}
```

### Dependency Planning Tools

#### `compute_scc`

Purpose: Computes strongly connected components in a directed dependency graph.

Input:

| Field                | Required | Type                   | Meaning                                                  |
| -------------------- | -------- | ---------------------- | -------------------------------------------------------- |
| `nodes`              | No       | `str`                  | Node IDs/names as a comma or semicolon-separated string. |
| `edges`              | No       | `List[Dict[str, Any]]` | Edge records with source/target style fields.            |
| `edge_semantics`     | No       | `str`                  | `depends_on` or `calls`. Default: `depends_on`.          |
| `include_singletons` | No       | `bool`                 | Include one-node SCCs. Default: `true`.                  |

Output: Dict with `components`, `node_to_scc`, and `cycle_summary`.

Use when:

- You need to detect dependency cycles.
- You are preparing a migration or extraction plan.
- You want to condense cyclic groups before sorting.

Example:

```json
{
  "nodes": "A,B",
  "edges": [{ "from": "A", "to": "B" }],
  "edge_semantics": "depends_on"
}
```

#### `topological_sort`

Purpose: Sorts a dependency graph into a linear order and/or parallel waves.
It can auto-condense SCCs when cycles exist.

Input:

| Field            | Required | Type                   | Meaning                                                       |
| ---------------- | -------- | ---------------------- | ------------------------------------------------------------- |
| `nodes`          | No       | `str`                  | Node IDs/names as a comma or semicolon-separated string.      |
| `edges`          | No       | `List[Dict[str, Any]]` | Dependency edges.                                             |
| `edge_semantics` | No       | `str`                  | `depends_on` or `calls`. Default: `depends_on`.               |
| `output_mode`    | No       | `str`                  | `linear`, `waves`, or `both`. Default: `both`.                |
| `on_cycle`       | No       | `str`                  | `auto_condense_scc` or `error`. Default: `auto_condense_scc`. |

Output: Dict with `is_dag`, `linear_order`, `waves`, cycle diagnostics, and
optional condensed SCC data.

Use when:

- You need migration order.
- You want parallelizable work waves.
- You need cycle diagnostics before refactoring.

Example:

```json
{
  "nodes": "A,B,C",
  "edges": [
    { "from": "A", "to": "B" },
    { "from": "B", "to": "C" }
  ],
  "output_mode": "both"
}
```

#### `plan_dependency_order`

Purpose: Builds module-level dependency waves from graph `CALLS` edges.

Input:

| Field            | Required | Type  | Meaning                                  |
| ---------------- | -------- | ----- | ---------------------------------------- |
| `modules`        | Yes      | `str` | Comma/semicolon-separated module tokens. |
| `parser_type`    | No       | `str` | Backend alias.                           |
| `db`             | No       | `str` | Database.                                |
| `edge_semantics` | No       | `str` | Default: `depends_on`.                   |
| `on_cycle`       | No       | `str` | Default: `auto_condense_scc`.            |

Output: Dict with `waves`, `module_order`, `depends_on_map`,
`module_dependencies`, cycle diagnostics, and SCC mapping.

Use when:

- You need a module migration sequence.
- You want to split module work into parallel waves.
- You are planning a refactor across several subsystems.

Example:

```json
{
  "modules": "auth,payment",
  "db": "hyper_graph"
}
```

#### `plan_file_dependency_order`

Purpose: Builds file-level dependency waves inside one or more modules from
graph `CALLS` edges.

Input:

| Field                  | Required | Type   | Meaning                                  |
| ---------------------- | -------- | ------ | ---------------------------------------- |
| `modules`              | Yes      | `str`  | Comma/semicolon-separated module tokens. |
| `parser_type`          | No       | `str`  | Backend alias.                           |
| `db`                   | No       | `str`  | Database.                                |
| `edge_semantics`       | No       | `str`  | Default: `depends_on`.                   |
| `on_cycle`             | No       | `str`  | Default: `auto_condense_scc`.            |
| `include_cross_module` | No       | `bool` | Include cross-module edges.              |
| `max_files_per_module` | No       | `int`  | Default: `2000`.                         |

Output: Dict with `cross_module_edges` and `modules[]`. Each module contains
`waves`, `file_order`, `depends_on_map`, cycle diagnostics, SCC mapping, and
`file_dependencies`.

Use when:

- Module-level planning is too coarse.
- You need file-by-file migration order.
- You want cross-module visibility before moving files.

Example:

```json
{
  "modules": "auth,payment",
  "db": "hyper_graph",
  "include_cross_module": true
}
```

#### `plan_function_dependency_order`

Purpose: Builds function-level dependency waves inside one or more modules from
graph `CALLS` edges.

Input:

| Field                      | Required | Type   | Meaning                                               |
| -------------------------- | -------- | ------ | ----------------------------------------------------- |
| `modules`                  | Yes      | `str`  | Comma/semicolon-separated module tokens.              |
| `parser_type`              | No       | `str`  | Backend alias.                                        |
| `db`                       | No       | `str`  | Database.                                             |
| `edge_semantics`           | No       | `str`  | Default: `depends_on`.                                |
| `on_cycle`                 | No       | `str`  | Default: `auto_condense_scc`.                         |
| `include_cross_module`     | No       | `bool` | Include cross-module edges.                           |
| `include_lambdas`          | No       | `bool` | Include lambda/function-literal nodes when available. |
| `max_functions_per_module` | No       | `int`  | Default: `5000`.                                      |

Output: Dict with `cross_module_edges` and `modules[]`. Each module contains
function waves, ordered IDs, detailed function metadata, dependency maps, cycle
diagnostics, and SCC mapping.

Use when:

- You need the most granular migration or rewrite order.
- You are splitting implementation tasks across functions.
- You need to see cycles at function granularity.

Example:

```json
{
  "modules": "auth,payment",
  "db": "hyper_graph",
  "include_cross_module": true,
  "include_lambdas": false
}
```

### Workflow Tools

#### `find_screen_workflows`

Purpose: Finds ranked screen-to-screen workflows for React/TypeScript projects
using `NAVIGATE` edges. It can search between two screens or around one screen.

Input:

| Field                    | Required | Type   | Meaning                                                              |
| ------------------------ | -------- | ------ | -------------------------------------------------------------------- |
| `project_id`             | Yes      | `str`  | Project scope.                                                       |
| `node_a`                 | Yes      | `str`  | Source/anchor screen name or symbol ID.                              |
| `node_b`                 | No       | `str`  | Target screen name for pair mode.                                    |
| `direction`              | No       | `str`  | `inbound`, `outbound`, or `bidirectional`. Default: `bidirectional`. |
| `max_hops`               | No       | `int`  | Max NAVIGATE hops. Default: `8`.                                     |
| `max_paths`              | No       | `int`  | Max workflows. Default: `100`.                                       |
| `include_entry_function` | No       | `bool` | Reserved/optional entry metadata.                                    |
| `include_api_calls`      | No       | `bool` | Reserved/optional API metadata.                                      |
| `db`                     | No       | `str`  | Database.                                                            |
| `parser_type`            | No       | `str`  | Backend alias.                                                       |

Output: Dict with `mode`, `direction`, `project_id`, `resolved`,
`workflows`, `uncertainties`, and `truncated`.

Use when:

- You need all business flows between two screens.
- You are changing a screen and want inbound/outbound workflows.
- You are validating nested navigator paths.
- You are planning UI regression tests.

Example:

```json
{
  "project_id": "my-app",
  "node_a": "RewardHome",
  "node_b": "GoldTransfer",
  "db": "hyper_graph",
  "max_hops": 8
}
```

#### `analyze_workflow_impact`

Purpose: Combines call-graph expansion and workflow-level scoring to estimate
the blast radius of changing a function/screen.

Input:

| Field         | Required | Type  | Meaning                                                              |
| ------------- | -------- | ----- | -------------------------------------------------------------------- |
| `function_id` | Yes      | `str` | Symbol ID or function/screen ID.                                     |
| `db`          | No       | `str` | Neo4j database used by the workflow-scoring layer. Default: `neo4j`. |
| `direction`   | No       | `str` | `downstream` or `upstream`. Default: `downstream`.                   |
| `max_depth`   | No       | `int` | CALLS traversal depth, capped at `4`.                                |

Output: Dict with `risk_score`, counts, `impacted_nodes`, and optional
`workflow_impact` containing direct/indirect affected workflows, cascade
workflows, navigator impacts, shared-screen conflict flag, score, and
recommendation.

Use when:

- You need risk before modifying a shared function.
- You want workflow-aware regression scope.
- You are refactoring screen navigation or shared UI logic.
- You need an objective score for review priority.

Provider note: call-graph expansion is dispatched through the provider-aware
backend, but the workflow-scoring layer still opens a raw Neo4j driver. In the
FalkorDB runtime, the response may contain base call-graph risk plus
`workflow_impact.error`. Use Neo4j compatibility mode for the full workflow
score.

Example:

```json
{
  "function_id": "func_123",
  "db": "neo4j",
  "direction": "downstream",
  "max_depth": 4
}
```

#### `find_workflows_containing`

Purpose: Lists workflows that contain a function directly via `HAS_STEP` or
indirectly through a `CALLS` chain.

Input:

| Field              | Required | Type   | Meaning                                                 |
| ------------------ | -------- | ------ | ------------------------------------------------------- |
| `function_id`      | Yes      | `str`  | Symbol ID or file path anchor.                          |
| `db`               | No       | `str`  | Neo4j database. Default: `neo4j`.                       |
| `include_indirect` | No       | `bool` | Include CALLS-chain derived workflows. Default: `true`. |
| `max_depth`        | No       | `int`  | Indirect traversal cap, capped at `4`.                  |

Output: Dict with `function_id`, `direct_workflows`,
`indirect_workflows`, and `total`.

Use when:

- You need to know which workflows a function participates in.
- You are building a regression checklist.
- You want workflow ownership for a code path.

Provider note: this tool currently opens a raw Neo4j driver and is not
FalkorDB-aware.

Example:

```json
{
  "function_id": "func_123",
  "db": "neo4j",
  "include_indirect": true,
  "max_depth": 4
}
```

### IPC And Fullstack API Bridge Tools

#### `get_ipc_message`

Purpose: Queries IPC/message records by sender and/or receiver. It checks graph
`Message` nodes first and can fall back to JSON data in backend logic.

Input:

| Field         | Required | Type  | Meaning                                                    |
| ------------- | -------- | ----- | ---------------------------------------------------------- |
| `sender`      | No       | `str` | Sender component pattern.                                  |
| `receiver`    | No       | `str` | Receiver component pattern.                                |
| `parser_type` | No       | `str` | Backend alias.                                             |
| `db`          | No       | `str` | Graph name/database. Current runtime graph: `hyper_graph`. |
| `project_id`  | No       | `str` | Project scope.                                             |

Output: Dict with `messages`, sender/receiver lists, or backend-specific IPC
details.

Use when:

- An Android component communicates through intents/services/events.
- You need to trace message passing between components.
- You only know the sender or receiver and need the counterpart list.

Example:

```json
{
  "sender": "Activity",
  "receiver": "Service",
  "db": "hyper_graph",
  "project_id": "digital_key_main"
}
```

#### `find_callers_of_endpoint`

Purpose: Returns frontend functions/screens that call a backend API endpoint.
It traverses `Function -> CALLS_API -> ApiCall -> MATCHES -> ApiEndpoint`.

Input:

| Field           | Required | Type  | Meaning                                                                  |
| --------------- | -------- | ----- | ------------------------------------------------------------------------ |
| `endpoint_path` | Yes      | `str` | Backend endpoint path, for example `/api/users/:id`.                     |
| `http_method`   | No       | `str` | `GET`, `POST`, `PUT`, `DELETE`, `ALL`, or empty for any. Default: `GET`. |
| `be_project_id` | No       | `str` | Backend project scope.                                                   |
| `fe_project_id` | No       | `str` | Frontend project scope.                                                  |
| `db`            | No       | `str` | Neo4j database. Default: `neo4j`.                                        |

Output: Dict with `endpoint_path`, `callers`, and `total`. Each caller
contains function name, qualified name, React role, file path, line number,
project ID, URL pattern, and match confidence.

Use when:

- You are changing a backend API contract.
- You need to find affected frontend screens.
- You want FE impact before removing or renaming an endpoint.

Provider note: this tool currently opens a raw Neo4j driver and is not
FalkorDB-aware.

Example:

```json
{
  "endpoint_path": "/api/users/:id",
  "http_method": "GET",
  "be_project_id": "backend",
  "fe_project_id": "frontend",
  "db": "hyper_graph"
}
```

#### `get_api_call_chain`

Purpose: Returns an end-to-end chain from a frontend component or endpoint to
backend controller, service, repository, and database nodes.

Input:

| Field            | Required | Type  | Meaning                                                          |
| ---------------- | -------- | ----- | ---------------------------------------------------------------- |
| `component_name` | No       | `str` | Frontend component/screen name.                                  |
| `endpoint_path`  | No       | `str` | Backend endpoint path. Required when `component_name` is absent. |
| `fe_project_id`  | No       | `str` | Frontend project scope.                                          |
| `be_project_id`  | No       | `str` | Backend project scope.                                           |
| `max_depth`      | No       | `str` | Max frontend `CALLS` hops. Default: `5`.                         |
| `db`             | No       | `str` | hyper_graph database. Default: `neo4j`.                          |

Output: Dict with `chains` and `total`. Each chain can include frontend
component/caller, API call, backend endpoint, controller, service, repository,
database, and match confidence.

Use when:

- You need to understand what database a screen ultimately touches.
- You are auditing data access paths.
- You are debugging a fullstack behavior from UI to backend.
- You need backend dependencies of a frontend component.

Provider note: this tool currently opens a raw Neo4j driver and is not
FalkorDB-aware.

Example:

```json
{
  "component_name": "UserProfileScreen",
  "fe_project_id": "frontend",
  "be_project_id": "backend",
  "max_depth": "5",
  "db": "neo4j"
}
```

### Living Docs V2 Tools

Living Docs tools use anchor-based code-to-document traceability. They query
`LINKS_TO` and `LINKED_FROM` relationships between code nodes and document
anchors. Pass `project_id` when several projects share the same graph.

#### `livingdoc_get_links_by_anchor`

Purpose: Lists every link touching a known anchor ID.

Input:

| Field                  | Required | Type    | Meaning                                                 |
| ---------------------- | -------- | ------- | ------------------------------------------------------- |
| `anchor_id`            | Yes      | `str`   | Code or document anchor ID.                             |
| `db`                   | No       | `str`   | FalkorDB graph name or Neo4j database name.             |
| `direction`            | No       | `str`   | `out`, `in`, or `both`. Default: `both`.                |
| `include_node_details` | No       | `bool`  | Include linked node details.                            |
| `min_score`            | No       | `float` | Minimum link score. `0.0` uses no score filter.         |
| `status_filter`        | No       | `str`   | Filter by link status, such as accepted pipeline state. |
| `project_id`           | No       | `str`   | Project scope.                                          |

Output: Dict with `links`; each link follows the standard link payload shape
with anchor IDs, endpoints, score, status, and optional node details.

Use when:

- You already know an anchor ID.
- You need all incoming/outgoing doc-code links for one anchor.
- You are investigating low-score or rejected links.

Example:

```json
{
  "anchor_id": "src/auth.py:AuthManager.validateToken",
  "direction": "both",
  "min_score": 0.7
}
```

#### `livingdoc_get_links_for_symbol`

Purpose: Given a code symbol, returns document sections linked to it.

Input:

| Field            | Required | Type    | Meaning                                                 |
| ---------------- | -------- | ------- | ------------------------------------------------------- |
| `node_id`        | No       | `str`   | Code node ID.                                           |
| `qualified_name` | No       | `str`   | Symbol qualified name. Used when `node_id` is unknown.  |
| `db`             | No       | `str`   | FalkorDB graph name or Neo4j database name.             |
| `min_score`      | No       | `float` | Minimum link score.                                     |
| `status_filter`  | No       | `str`   | Link status filter.                                     |
| `limit`          | No       | `int`   | Max links. `0` means backend default/no explicit limit. |
| `project_id`     | No       | `str`   | Project scope.                                          |

At least one of `node_id` or `qualified_name` should be provided.

Output: Dict with `links` pointing from the symbol to document anchors.

Use when:

- Reviewing which spec sections a function implements.
- Checking whether a code path is documented.
- Preparing impact notes before changing a symbol.

Example:

```json
{
  "qualified_name": "AuthManager.validateToken",
  "min_score": 0.7
}
```

#### `livingdoc_get_links_for_document`

Purpose: Given a document path, lists code symbols linked to its sections.

Input:

| Field                  | Required | Type    | Meaning                                            |
| ---------------------- | -------- | ------- | -------------------------------------------------- |
| `source_file`          | Yes      | `str`   | Document source path, for example `docs/spec.pdf`. |
| `db`                   | No       | `str`   | FalkorDB graph name or Neo4j database name.        |
| `min_score`            | No       | `float` | Minimum link score.                                |
| `status_filter`        | No       | `str`   | Link status filter.                                |
| `include_node_details` | No       | `bool`  | Include code/document node details.                |
| `limit`                | No       | `int`   | Max links.                                         |
| `project_id`           | No       | `str`   | Project scope.                                     |

Output: Dict with `links` keyed around the document's anchors.

Use when:

- Starting from a spec/PDF/PPTX and asking "what code implements this?"
- Checking document coverage.
- Drilling into a document found by `livingdoc_list_documents`.

Example:

```json
{
  "source_file": "docs/spec.pdf",
  "include_node_details": true
}
```

#### `livingdoc_list_documents`

Purpose: Lists documents that have at least one code link, including link count
and score statistics. This tool reports **linked** documents, not every document
that was ingested.

Input:

| Field        | Required | Type  | Meaning                                     |
| ------------ | -------- | ----- | ------------------------------------------- |
| `db`         | No       | `str` | FalkorDB graph name or Neo4j database name. |
| `min_links`  | No       | `int` | Minimum links required. Default: `1`.       |
| `limit`      | No       | `int` | Max documents.                              |
| `project_id` | No       | `str` | Project scope.                              |

Output: Dict with `documents`; each document includes `source_file`,
`link_count`, and score stats.

Use when:

- Discovering which documents are connected to the codebase.
- Prioritizing high-coverage specs.
- Selecting a document before calling `livingdoc_get_links_for_document`.

Example:

```json
{
  "min_links": 5,
  "limit": 20
}
```

#### `livingdoc_list_ingested_documents`

Purpose: Lists document nodes actually persisted in the active graph, including
documents that do not have any Living Docs code links yet.

Input:

| Field        | Required | Type  | Meaning                                                                     |
| ------------ | -------- | ----- | --------------------------------------------------------------------------- |
| `db`         | No       | `str` | FalkorDB graph name or Neo4j database name. Uses active context when empty. |
| `limit`      | No       | `int` | Maximum documents. `0` means no explicit limit.                             |
| `project_id` | No       | `str` | Restrict results to one project.                                            |

Output: Dict with `documents`. Each item contains at least `source_file` and
`document_node_count`; backend implementations may include additional document
metadata.

Use when:

- Confirming that a PDF, PPTX, DOCX, or Markdown file reached the graph.
- Separating an ingestion failure from a Living Docs link-generation failure.
- Finding documents that exist in the active graph but do not appear in
  `livingdoc_list_documents`.
- Auditing document persistence before running or debugging the link pipeline.

Do not use it to measure code-document coverage. A document returned here may
have zero `LINKS_TO` edges. Use `livingdoc_list_documents` for linked documents
and `livingdoc_get_link_stats` for link health.

Example:

```json
{
  "db": "hyper_graph",
  "limit": 100,
  "project_id": "hypergraph"
}
```

Representative output:

```json
{
  "documents": [
    {
      "source_file": "docs/authentication-spec.pdf",
      "document_node_count": 48
    }
  ]
}
```

#### `livingdoc_get_link_stats`

Purpose: Returns aggregate health statistics for the Living Docs link graph.

Input:

| Field             | Required | Type   | Meaning                                     |
| ----------------- | -------- | ------ | ------------------------------------------- |
| `db`              | No       | `str`  | FalkorDB graph name or Neo4j database name. |
| `include_orphans` | No       | `bool` | Count orphaned edges. Default: `true`.      |
| `project_id`      | No       | `str`  | Project scope.                              |

Output: Dict with `total_links`, `by_status`, `by_pipeline_version`,
`orphan_count`, and `score_histogram`.

Use when:

- Confirming the Living Docs pipeline has run.
- Checking traceability health before relying on links.
- Monitoring pipeline quality after re-ingest.

Example:

```json
{
  "include_orphans": true
}
```

#### `livingdoc_trace_path`

Purpose: Walks `LINKS_TO` and `LINKED_FROM` relationships from a starting node
for up to five hops.

Input:

| Field           | Required | Type    | Meaning                                     |
| --------------- | -------- | ------- | ------------------------------------------- |
| `start_node_id` | Yes      | `str`   | Starting node ID.                           |
| `max_hops`      | No       | `int`   | Hop count, usually 1 to 5. Default: `3`.    |
| `direction`     | No       | `str`   | `out`, `in`, or `both`. Default: `both`.    |
| `db`            | No       | `str`   | FalkorDB graph name or Neo4j database name. |
| `limit`         | No       | `int`   | Max paths. Default: `50`.                   |
| `min_score`     | No       | `float` | Minimum link score.                         |
| `project_id`    | No       | `str`   | Project scope.                              |

Output: Dict with `paths`, each path being an ordered list of node and edge
records.

Use when:

- Direct lookup is too narrow.
- You need code -> spec -> related code traversal.
- You are tracing documentation-driven impact across anchors.

Example:

```json
{
  "start_node_id": "c_AuthManager_validateToken",
  "max_hops": 4,
  "min_score": 0.7
}
```

#### `livingdoc_derive_anchors_for_file`

Purpose: Re-derives anchor IDs for one source file from the active graph. This
is useful for checking anchor stability before or after refactors/re-ingest.

Input:

| Field             | Required | Type        | Meaning                                                                          |
| ----------------- | -------- | ----------- | -------------------------------------------------------------------------------- |
| `source_file`     | Yes      | `str`       | Source code or document file path.                                               |
| `node_types`      | No       | `List[str]` | Restrict to node types, for example `p` or `h` for document paragraphs/headings. |
| `db`              | No       | `str`       | FalkorDB graph name or Neo4j database name.                                      |
| `include_anchors` | No       | `bool`      | Include full anchor records. Default: `true`.                                    |
| `limit`           | No       | `int`       | Max anchors.                                                                     |
| `project_id`      | No       | `str`       | Project scope.                                                                   |

Output: Dict with `anchors` and per-anchor metadata.

Use when:

- A link disappeared after re-ingest.
- You need to verify stable anchors across file edits.
- You are debugging anchor generation for one file.

Example:

```json
{
  "source_file": "docs/spec.pdf",
  "node_types": ["p", "h"],
  "include_anchors": true
}
```

#### `livingdoc_validate_links`

Purpose: Samples existing Living Docs links and re-validates them against the
pipeline validation rules: self-link prevention, anchor existence, anchor
format, score threshold, and node existence.

Input:

| Field                     | Required | Type    | Meaning                                                               |
| ------------------------- | -------- | ------- | --------------------------------------------------------------------- |
| `sample_size`             | No       | `int`   | Number of links to validate. Default: `100`.                          |
| `accept_threshold`        | No       | `float` | Minimum accepted score. `0.0` lets backend use env/default threshold. |
| `db`                      | No       | `str`   | FalkorDB graph name or Neo4j database name.                           |
| `check_nodes`             | No       | `bool`  | Check node existence. Default: `true`.                                |
| `include_failure_details` | No       | `bool`  | Include detailed failures. Default: `true`.                           |
| `project_id`              | No       | `str`   | Project scope.                                                        |

Output: Dict with `sample_size`, `passed`, `failed`, and failure details per
validation rule.

Use when:

- Links were created by an older or looser pipeline.
- You suspect orphaned endpoints after code/doc deletion.
- You need a periodic traceability sanity check.

Example:

```json
{
  "sample_size": 200,
  "accept_threshold": 0.7,
  "include_failure_details": true
}
```

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
     "collection": "hypergraph_73eddc5fcc__python_functions",
     "top_k": "10",
     "project_id": "hypergraph",
     "db": "hyper_graph"
   }
   ```

3. Copy the best result ID into `get_symbol`:

   ```json
   {
     "node_id": "processPayment/2@src/payment/service.py",
     "project_id": "hypergraph",
     "db": "hyper_graph"
   }
   ```

4. Inspect local impact with `query_subgraph`:

   ```json
   {
     "function_id": "processPayment/2@src/payment/service.py",
     "direction": "all",
     "max_depth": 2,
     "project_id": "hypergraph",
     "db": "hyper_graph"
   }
   ```

Read the result as follows: incoming `CALLS` edges are callers that may need
regression tests; outgoing edges are dependencies whose behavior the function
relies on.

### Recipe 2: Find A Known Function And Its Callers

Situation: you know `validateToken` but do not know its graph ID.

1. Use `search_functions`:

   ```json
   {
     "query": "validateToken|AuthManager",
     "limit": "20",
     "project_id": "hypergraph",
     "db": "hyper_graph"
   }
   ```

2. Confirm the exact candidate with `get_symbol`:

   ```json
   {
     "node_id": "validateToken/1@src/auth/AuthManager.java",
     "project_id": "hypergraph",
     "db": "hyper_graph"
   }
   ```

3. Find callers only with `query_subgraph`:

   ```json
   {
     "function_id": "validateToken/1@src/auth/AuthManager.java",
     "direction": "in",
     "max_depth": 3,
     "project_id": "hypergraph",
     "db": "hyper_graph"
   }
   ```

4. If dynamic dispatch is possible, add `list_possible_calls`:

   ```json
   {
     "function_id": "validateToken/1@src/auth/AuthManager.java",
     "limit": "100",
     "project_id": "hypergraph",
     "db": "hyper_graph"
   }
   ```

### Recipe 3: Assess Change Risk

#### FalkorDB Default Path

Use provider-aware graph tools for a concrete impact set:

1. `query_subgraph` for the local dependency neighborhood:

   ```json
   {
     "function_id": "processPayment/2@src/payment/service.py",
     "direction": "all",
     "max_depth": 3,
     "project_id": "hypergraph",
     "db": "hyper_graph"
   }
   ```

2. `trace_flow` for broader downstream behavior:

   ```json
   {
     "start_id": "processPayment/2@src/payment/service.py",
     "direction": "downstream",
     "limit": "50",
     "project_id": "hypergraph",
     "db": "hyper_graph"
   }
   ```

3. `get_node_details` for the nodes selected for review:

   ```json
   {
     "node_ids": "caller_1,caller_2,dependency_1",
     "project_id": "hypergraph",
     "db": "hyper_graph"
   }
   ```

#### Neo4j Workflow-Aware Path

Use this only in Neo4j compatibility mode when workflow nodes and edges exist:

1. `find_workflows_containing`:

   ```json
   {
     "function_id": "processPayment/2@src/payment/service.py",
     "db": "neo4j",
     "include_indirect": true,
     "max_depth": 4
   }
   ```

2. `analyze_workflow_impact`:

   ```json
   {
     "function_id": "processPayment/2@src/payment/service.py",
     "db": "neo4j",
     "direction": "downstream",
     "max_depth": 4
   }
   ```

Use `workflow_impact.recommendation`, `overall_risk_score`, and the affected
workflow lists to decide review depth and regression-test scope.

### Recipe 4: Trace A Frontend Endpoint To Backend Data

These fullstack bridge tools currently require Neo4j compatibility mode.

Starting from an endpoint, call `find_callers_of_endpoint`:

```json
{
  "endpoint_path": "/api/users/:id",
  "http_method": "GET",
  "fe_project_id": "web-client",
  "be_project_id": "user-service",
  "db": "neo4j"
}
```

Starting from a screen, call `get_api_call_chain`:

```json
{
  "component_name": "UserProfileScreen",
  "fe_project_id": "web-client",
  "be_project_id": "user-service",
  "max_depth": "5",
  "db": "neo4j"
}
```

Look for `fe_function`, `api_call`, `be_endpoint`, `be_controller`,
`be_service`, `be_repository`, and `be_database` in each returned chain.

### Recipe 5: Distinguish Document Ingestion From Linking

Situation: a specification does not appear in code-link results. First determine
whether the document was persisted at all.

1. List every ingested document with `livingdoc_list_ingested_documents`:

   ```json
   {
     "project_id": "hypergraph",
     "db": "hyper_graph",
     "limit": 200
   }
   ```

2. List only documents with code links using `livingdoc_list_documents`:

   ```json
   {
     "project_id": "hypergraph",
     "db": "hyper_graph",
     "min_links": 1,
     "limit": 200
   }
   ```

3. Interpret the difference:
   - Present in both: ingestion and linking both succeeded.
   - Present only in `livingdoc_list_ingested_documents`: ingestion succeeded,
     but link generation produced no accepted links.
   - Present in neither: verify ingestion scope, source path, and graph name.

4. For a linked document, call `livingdoc_get_links_for_document`:

   ```json
   {
     "source_file": "docs/authentication-spec.pdf",
     "project_id": "hypergraph",
     "db": "hyper_graph",
     "min_score": 0.65,
     "include_node_details": true
   }
   ```

5. For pipeline diagnostics, call `livingdoc_get_link_stats` and
   `livingdoc_validate_links`:

   ```json
   {
     "project_id": "hypergraph",
     "db": "hyper_graph",
     "include_orphans": true
   }
   ```

   ```json
   {
     "project_id": "hypergraph",
     "db": "hyper_graph",
     "sample_size": 100,
     "accept_threshold": 0.65,
     "check_nodes": true,
     "include_failure_details": true
   }
   ```

### Recipe 6: Plan A Migration In Dependency Order

For indexed modules, use `plan_dependency_order`:

```json
{
  "modules": "src/auth,src/payment,src/orders",
  "db": "hyper_graph",
  "edge_semantics": "depends_on",
  "on_cycle": "auto_condense_scc"
}
```

Read `waves` from first to last. Items in the same wave can usually be worked on
in parallel. If the result reports cycles, inspect the SCC mapping before
assigning separate tasks.

For a manually supplied dependency graph, call `compute_scc` first:

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

Then call `topological_sort` with the same nodes and edges:

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

### Recipe 7: Debug Empty Results

1. Call `list_databases` with `parser_type: "cplus"`. In the default runtime,
   verify that `hyper_graph` is returned.
2. Call `list_qdrant_collections` and copy the exact collection name instead of
   guessing it.
3. Run `search_functions` with a broad known name and explicit
   `db: "hyper_graph"` plus the correct `project_id`.
4. Remove `project_id` temporarily only if the graph is trusted and you are
   diagnosing a scope mismatch.
5. Check whether the tool is marked Neo4j-only in this guide.
6. Call `list_mcp_functions` to verify that your client is using the live input
   schema rather than a cached schema.
7. Treat an empty array as a valid no-match result; treat connection errors and
   schema-validation errors as configuration/client failures.

## Testing Tools Manually

The repository includes an interactive MCP tester:

```bash
cd /Users/hieplq1.rpm/Hyper-Dev/hyper-pack/hyper-dev/hyper-graph
source .venv/bin/activate
python testtool/mcp_tester.py --endpoint http://127.0.0.1:8788/mcp
```

Jump directly to a tool:

```bash
python testtool/mcp_tester.py --tool search_functions --project-id my_project
```

## Troubleshooting

| Symptom                                        | Likely cause                                                                        | What to check                                                                                      |
| ---------------------------------------------- | ----------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| FalkorDB connection refused                    | FalkorDB is not running or the runtime points to the wrong host/port.               | `GRAPH_PROVIDER=falkordb`, `FALKORDB_HOST`, `FALKORDB_PORT`, `FALKORDB_GRAPH`.                     |
| Tool unexpectedly connects to Neo4j            | The tool is Neo4j-only, or the process did not inherit FalkorDB provider variables. | Check the provider support table, `GRAPH_PROVIDER`, `MCP_GRAPH_PROVIDER`, and tool-specific notes. |
| Neo4j connection refused in compatibility mode | Neo4j is not running or credentials point to the wrong host/port.                   | `GRAPH_PROVIDER=neo4j`, `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASS`, `NEO4J_DB`.                       |
| Semantic search returns no results             | Qdrant collection missing or wrong collection name.                                 | `list_qdrant_collections`, `QDRANT_URL`, analyzer ingest logs.                                     |
| Tool routes to unexpected backend              | Missing or wrong `parser_type`.                                                     | `activate_project`, `list_parsers`, parser routing table above.                                    |
| Results include another project                | Shared graph without `project_id` filter.                                           | Pass `project_id`, `fe_project_id`, or `be_project_id` where relevant.                             |
| `list_up_entrypoint` returns `missing_fields`  | Required module/db input was empty after normalization.                             | Pass `modules: ["src/api"]` or `module: "src/api"`.                                                |
| `get_symbol` returns `symbol: null`            | Wrong ID, wrong project scope, or wrong `node_type`.                                | Re-run `search_functions` and verify `db`/`project_id`.                                            |

## Maintenance Notes

- Update `tool_metadata.py` whenever adding/removing tools or changing
  documented inputs/outputs.
- Update `unified_mcp.py` when the public MCP wrapper signature changes.
- Keep `fastmcp==3.0.0` pinned consistently with related Hyper Dev MCP
  services, as noted in `requirements.txt`.
- If `java/java_mcp.py` should become active through the unified server, add it
  to `BACKENDS` and update the parser routing table in this README.

## ASP.NET Framework Profiles

The unified server exposes two parser-aware profiles through the shared C++
backend:

- `aspnet_core`, `aspnet-core`, `asp.net-core`, and `aspnetcore`
- `aspnet_framework`, `aspnet-framework`, `asp.net-framework`, and
  `aspnetframework`

Both profiles search the shared migration labels (`HttpEndpoint`, `Route`,
`Middleware`, `Controller`, `Action`, `RazorPage`, `WebFormPage`, `Service`,
`ConfigurationKey`, and related labels) and traverse the normalized ASP.NET
relationship vocabulary. Facts are framework-, project-, module-, and
generation-scoped; canonical C# nodes are linked with `SEMANTIC_OF` and remain
owned by the `csharp` analyzer.
