"""
Shared MCP tool catalog — single source of truth for all backend list_mcp_functions.

Usage in each backend:

    from mcp.tool_metadata import build_catalog

    # inside tool_list_mcp_functions:
    return {
        "total_count": ...,
        "functions": build_catalog(TOOL_NAMES, overrides=OVERRIDES),
        ...
    }

TOOL_NAMES  — frozenset of tool-name strings the backend actually registers.
OVERRIDES   — optional dict {tool_name: {field: value, ...}} for backend-specific
              description/use_cases/inputs patches.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# Full catalog (union of all backends)
# ---------------------------------------------------------------------------

_FULL_CATALOG: List[Dict[str, Any]] = [
    {
        "name": "activate_project",
        "description": "Set default parser_type and database for all subsequent tool calls in this session.",
        "use_cases": ["Start of conversation", "Switch between projects", "Set context for multiple queries"],
        "inputs": [
            {"name": "parser_type", "type": "str", "required": False,
             "description": "Parser type: cplus/cpp/c++/c/clang/delphi/pascal/java/kotlin/jvm/vbnet/vb6/vba/vbscript or android/android-kotlin"},
            {"name": "database_name", "type": "str", "required": False,
             "description": "Neo4j database name (e.g., 'neo4j', 'vtgm', 'boze')"},
        ],
        "output": "Dict with parser_type, database_name, activated status",
        "example": "activate_project(parser_type='cplus', database_name='vtgm')",
    },
    {
        "name": "search_functions",
        "description": "Search for functions/classes/types by name or qualified name. Returns BOTH node details AND IDs.",
        "use_cases": ["Find function by name", "Search for class", "Get symbol ID for further queries",
                      "Fuzzy search across codebase"],
        "inputs": [
            {"name": "query", "type": "str", "required": True,
             "description": "Search terms separated by | (e.g., 'MyClass|MyFunc'). Case-insensitive substring match."},
            {"name": "limit", "type": "int", "required": False, "description": "Max results (default: 50)"},
            {"name": "db", "type": "str", "required": False,
             "description": "Database name (uses activate_project default if not set)"},
            {"name": "content_mode", "type": "str", "required": False,
             "description": "Output format: 'auto', 'summary', 'comment', 'code', 'name'"},
            {"name": "include_raw_fields", "type": "bool", "required": False,
             "description": "Include raw Neo4j properties (default: false)"},
              {"name": "node_type", "type": "str", "required": False,
               "description": "Node domain filter: 'code' or 'doc' (default: code)."},
              {"name": "expand_search", "type": "bool", "required": False,
               "description": "When true, allow cross-domain traversal and keep non-requested nodes compact."},
        ],
        "output": "Dict with 'results' (node list), 'ids' (ID list), 'db' (database used)",
        "example": "search_functions(query='handleClick|onClick', limit=10)",
    },
    {
        "name": "search_by_code",
        "description": "Search for code snippets by matching text in function bodies/implementations.",
        "use_cases": ["Find functions containing specific code", "Search for API usage",
                      "Locate string literals", "Find regex patterns in code"],
        "inputs": [
            {"name": "query", "type": "str", "required": True,
             "description": "Code text to search for (case-sensitive)"},
            {"name": "limit", "type": "int", "required": False, "description": "Max results (default: 50)"},
            {"name": "db", "type": "str", "required": False},
            {"name": "content_mode", "type": "str", "required": False,
             "description": "Output format: 'auto', 'summary', 'comment', 'code', 'name'"},
            {"name": "include_raw_fields", "type": "bool", "required": False,
             "description": "Include raw Neo4j properties (default: false)"},
            {"name": "node_type", "type": "str", "required": False,
             "description": "Node domain filter: 'code' or 'doc' (default: code)."},
            {"name": "expand_search", "type": "bool", "required": False,
             "description": "When true, allow cross-domain traversal and keep non-requested nodes compact."},
        ],
        "output": "Dict with matching nodes containing the code snippet",
        "example": "search_by_code(query='malloc|calloc', limit=20)",
    },
    {
        "name": "get_symbol",
        "description": "Fetch detailed metadata for a specific node by its ID.",
        "use_cases": ["Get full details of a function", "Inspect symbol properties",
                      "View documentation/comments", "Get source code"],
        "inputs": [
            {"name": "node_id", "type": "str", "required": True, "description": "Node ID from search results"},
            {"name": "db", "type": "str", "required": False},
            {"name": "content_mode", "type": "str", "required": False},
            {"name": "include_raw_fields", "type": "bool", "required": False},
            {"name": "node_type", "type": "str", "required": False,
             "description": "Node domain filter: 'code' or 'doc' (default: code). Returns node_type_mismatch when filtered out."},
        ],
        "output": "Dict with node metadata (name, qualified_name, file_path, signature, code, comment, etc.)",
        "example": "get_symbol(node_id='func_12345')",
    },
    {
        "name": "get_node_details",
        "description": "Batch fetch metadata for multiple nodes by their IDs (more efficient than repeated get_symbol).",
        "use_cases": ["Get details for multiple functions at once", "Batch lookup after search",
                      "Process search results"],
        "inputs": [
            {"name": "node_ids", "type": "List[str]", "required": True, "description": "List of node IDs"},
            {"name": "db", "type": "str", "required": False},
            {"name": "content_mode", "type": "str", "required": False},
            {"name": "include_raw_fields", "type": "bool", "required": False,
             "description": "Include raw Neo4j properties (default: false)"},
            {"name": "node_type", "type": "str", "required": False,
             "description": "Node domain filter: 'code' or 'doc' (default: code)."},
        ],
        "output": "Dict with list of node metadata",
        "example": "get_node_details(node_ids=['func_1', 'func_2', 'func_3'])",
    },
    {
        "name": "query_subgraph",
        "description": "Get call graph context around a function: who calls it (callers) and what it calls (callees).",
        "use_cases": ["Understand function dependencies", "Find all callers of a function",
                      "Trace function call tree", "Impact analysis"],
        "inputs": [
            {"name": "function_id", "type": "str", "required": True, "description": "Starting function node ID"},
            {"name": "max_depth", "type": "int", "required": False, "description": "Graph traversal depth (default: 2)"},
            {"name": "relationship_types", "type": "List[str]", "required": False,
             "description": "Filter by rel types (default: CALLS)"},
            {"name": "direction", "type": "str", "required": False,
             "description": "'out' (callees), 'in' (callers), 'both' (default)"},
            {"name": "db", "type": "str", "required": False},
            {"name": "content_mode", "type": "str", "required": False,
             "description": "Output format: 'auto', 'summary', 'comment', 'code', 'name'"},
            {"name": "include_raw_fields", "type": "bool", "required": False,
             "description": "Include raw Neo4j properties (default: false)"},
            {"name": "node_type", "type": "str", "required": False,
             "description": "Node domain filter: 'code' or 'doc' (default: code)."},
            {"name": "expand_search", "type": "bool", "required": False,
             "description": "When true, include bridge nodes from opposite domain with compact payload."},
        ],
        "output": "Dict with 'nodes' and 'edges' forming the subgraph",
        "example": "query_subgraph(function_id='func_main', max_depth=3, direction='out')",
    },
    {
        "name": "find_paths",
        "description": "Find all call paths between two specific functions.",
        "use_cases": ["Trace how function A reaches function B", "Find execution paths",
                      "Understand call chains", "Debug control flow"],
        "inputs": [
            {"name": "start_function_id", "type": "str", "required": True, "description": "Starting function ID"},
            {"name": "end_function_id", "type": "str", "required": True, "description": "Target function ID"},
            {"name": "max_depth", "type": "int", "required": False, "description": "Max path length (default: 5)"},
            {"name": "relationship_types", "type": "List[str]", "required": False},
            {"name": "db", "type": "str", "required": False},
            {"name": "content_mode", "type": "str", "required": False,
             "description": "Output format: 'auto', 'summary', 'comment', 'code', 'name'"},
            {"name": "include_raw_fields", "type": "bool", "required": False,
             "description": "Include raw Neo4j properties (default: false)"},
            {"name": "node_type", "type": "str", "required": False,
             "description": "Node domain filter: 'code' or 'doc' (default: code)."},
            {"name": "expand_search", "type": "bool", "required": False,
             "description": "When true, include bridge nodes from opposite domain with compact payload."},
        ],
        "output": "Dict with list of paths, each path containing nodes and edges",
        "example": "find_paths(start_function_id='main', end_function_id='malloc')",
    },
    {
        "name": "find_path_between_module",
        "description": "Find call paths between modules/files (by file path pattern). Supports bidirectional search.",
        "use_cases": ["Find how module A uses module B", "Trace cross-module dependencies",
                      "Architectural analysis", "Module coupling analysis"],
        "inputs": [
            {"name": "source_modules", "type": "List[str]", "required": True,
             "description": "Source file path patterns (e.g., ['vtgm01', 'Vtgm'])"},
            {"name": "target_modules", "type": "List[str]", "required": True,
             "description": "Target file path patterns"},
            {"name": "max_depth", "type": "int", "required": False, "description": "Max path length (default: 6)"},
            {"name": "direction", "type": "str", "required": False,
             "description": "'out', 'in', 'both' (default: 'out', auto-retries with 'both')"},
            {"name": "include_possible", "type": "bool", "required": False,
             "description": "Include POSSIBLE_CALLS edges"},
            {"name": "include_fp", "type": "bool", "required": False,
             "description": "Include function pointer calls"},
            {"name": "limit", "type": "int", "required": False, "description": "Max paths to return (default: 10)"},
            {"name": "db", "type": "str", "required": False},
        ],
        "output": "Dict with paths between modules, graph visualization data",
        "example": "find_path_between_module(source_modules=['vtgm01'], target_modules=['Boze01'], direction='both')",
    },
    {
        "name": "listup_symbols_matching_file_path",
        "description": "List all symbols (functions/classes/types) in files matching path pattern. Supports filtering by node type.",
        "use_cases": ["List all functions in a file", "Get classes in a module",
                      "Inventory symbols in directory", "Extract API surface"],
        "inputs": [
            {"name": "modules", "type": "List[str]", "required": True,
             "description": "File path patterns to match"},
            {"name": "node_types", "type": "List[str]", "required": False,
             "description": "Filter by types: ['Function'], ['Class', 'Type'], etc. (default: all symbols)"},
            {"name": "db", "type": "str", "required": False},
            {"name": "content_mode", "type": "str", "required": False},
        ],
        "output": "Dict with list of symbols matching path and type filters",
        "example": "listup_symbols_matching_file_path(modules=['vtgm01.c'], node_types=['Function'])",
    },
    {
        "name": "listup_class_matching_path",
        "description": "List all functions/methods declared in classes matching name pattern.",
        "use_cases": ["Get all methods of a class", "Class API inventory", "Find member functions"],
        "inputs": [
            {"name": "class_names", "type": "List[str]", "required": True,
             "description": "Class name patterns"},
            {"name": "db", "type": "str", "required": False},
        ],
        "output": "Dict with classes and their functions",
        "example": "listup_class_matching_path(class_names=['MyClass', 'Handler'])",
    },
    {
        "name": "list_up_entrypoint",
        "description": "Find entry point functions: functions in target modules that are called from OUTSIDE those modules.",
        "use_cases": ["Find public API of a module", "Identify module boundaries",
                      "Locate exported functions", "API surface analysis"],
        "inputs": [
            {"name": "modules", "type": "List[str]", "required": True,
             "description": "Module/file path patterns"},
            {"name": "limit", "type": "int", "required": False, "description": "Max results (default: 200)"},
            {"name": "db", "type": "str", "required": False},
        ],
        "output": "Dict with entry point functions",
        "example": "list_up_entrypoint(modules=['src/api/'])",
    },
    # --- C++ / generic trace_flow ------------------------------------------------
    {
        "name": "trace_flow",
        "description": "Advanced flow tracing with custom relationship types (CALLS, POSSIBLE_CALLS, function pointers, etc.).",
        "use_cases": ["Custom relationship traversal", "Trace with specific edge types",
                      "Advanced graph queries"],
        "inputs": [
            {"name": "start_id", "type": "str", "required": True},
            {"name": "end_id", "type": "str", "required": True},
            {"name": "rel_types", "type": "List[str]", "required": False,
             "description": "Custom relationship types to traverse"},
            {"name": "max_depth", "type": "int", "required": False},
            {"name": "direction", "type": "str", "required": False},
            {"name": "db", "type": "str", "required": False},
        ],
        "output": "Dict with traced paths using specified relationships",
        "example": "trace_flow(start_id='func1', end_id='func2', rel_types=['CALLS', 'POSSIBLE_CALLS'])",
    },
    {
        "name": "trace_flow_between_module",
        "description": "Advanced module-to-module flow tracing with custom relationships.",
        "use_cases": ["Custom module dependency analysis",
                      "Trace specific edge types between modules"],
        "inputs": [
            {"name": "source_modules", "type": "List[str]", "required": True},
            {"name": "target_modules", "type": "List[str]", "required": True},
            {"name": "rel_types", "type": "List[str]", "required": False},
            {"name": "max_depth", "type": "int", "required": False},
            {"name": "direction", "type": "str", "required": False},
            {"name": "limit", "type": "int", "required": False},
            {"name": "db", "type": "str", "required": False},
        ],
        "output": "Dict with module flow paths",
        "example": "trace_flow_between_module(source_modules=['vtgm'], target_modules=['boze'], rel_types=['CALLS'])",
    },
    # --- Planning / dependency ordering -----------------------------------------
    {
        "name": "compute_scc",
        "description": "Compute strongly connected components (SCC) from a directed dependency graph.",
        "use_cases": [
            "Detect dependency cycles before migration",
            "Map nodes to SCC groups",
            "Prepare condensation before topological planning",
        ],
        "inputs": [
            {"name": "nodes", "type": "List[str]", "required": False},
            {"name": "edges", "type": "List[Dict[str, Any]]", "required": False,
             "description": "Edge records containing source/target-style fields"},
            {"name": "edge_semantics", "type": "str", "required": False,
             "description": "depends_on (default) or calls"},
            {"name": "include_singletons", "type": "bool", "required": False},
        ],
        "output": (
            "Dict with components[{scc_id,nodes,size,is_cycle}], node_to_scc, "
            "and cycle_summary{total_scc,reported_scc,cyclic_scc,self_loops}"
        ),
        "example": "compute_scc(nodes=['A','B'], edges=[{'from':'A','to':'B'}])",
    },
    {
        "name": "topological_sort",
        "description": (
            "Topologically sort dependency graph and return linear order and/or parallel waves. "
            "Supports SCC auto-condensation when cycles exist."
        ),
        "use_cases": [
            "Get migration execution order",
            "Split work into parallel waves",
            "Handle cyclic graphs with SCC fallback",
        ],
        "inputs": [
            {"name": "nodes", "type": "List[str]", "required": False},
            {"name": "edges", "type": "List[Dict[str, Any]]", "required": False},
            {"name": "edge_semantics", "type": "str", "required": False,
             "description": "depends_on (default) or calls"},
            {"name": "output_mode", "type": "str", "required": False,
             "description": "linear | waves | both (default)"},
            {"name": "on_cycle", "type": "str", "required": False,
             "description": "auto_condense_scc (default) or error"},
        ],
        "output": (
            "Dict with is_dag, unresolved_nodes, unresolved_cycles, diagnostics, "
            "optional condensed{node_to_scc,...}, and linear_order/waves depending on output_mode"
        ),
        "example": "topological_sort(nodes=['A','B'], edges=[{'from':'A','to':'B'}], output_mode='both')",
    },
    {
        "name": "plan_dependency_order",
        "description": "Plan module-level dependency order from CALLS edges.",
        "use_cases": [
            "Module migration sequencing",
            "Wave-based execution planning by module",
            "Cycle diagnostics at module level",
        ],
        "inputs": [
            {"name": "modules", "type": "List[str]", "required": True,
             "description": "Module tokens matched against file_path"},
            {"name": "db", "type": "str", "required": False},
            {"name": "edge_semantics", "type": "str", "required": False},
            {"name": "on_cycle", "type": "str", "required": False},
        ],
        "output": (
            "Dict with waves[{wave,modules}], module_order, depends_on_map, "
            "module_dependencies, unresolved_cycles, unresolved_nodes, node_to_scc"
        ),
        "example": "plan_dependency_order(modules=['auth','payment'])",
    },
    {
        "name": "plan_file_dependency_order",
        "description": "Plan file-level dependency order per module from CALLS edges.",
        "use_cases": [
            "Detailed file migration order",
            "Parallel file waves per module",
            "Cross-module dependency visibility",
        ],
        "inputs": [
            {"name": "modules", "type": "List[str]", "required": True},
            {"name": "db", "type": "str", "required": False},
            {"name": "edge_semantics", "type": "str", "required": False},
            {"name": "on_cycle", "type": "str", "required": False},
            {"name": "include_cross_module", "type": "bool", "required": False},
            {"name": "max_files_per_module", "type": "int", "required": False},
        ],
        "output": (
            "Dict with cross_module_edges and modules[]. Each module includes "
            "waves[{wave,files}], file_order, depends_on_map, unresolved_cycles, node_to_scc, file_dependencies"
        ),
        "example": "plan_file_dependency_order(modules=['auth','payment'], include_cross_module=true)",
    },
    {
        "name": "plan_function_dependency_order",
        "description": "Plan function-level dependency order per module from CALLS edges.",
        "use_cases": [
            "Function migration sequencing with metadata",
            "Wave execution planning by function",
            "Cycle/SCC diagnostics at function granularity",
        ],
        "inputs": [
            {"name": "modules", "type": "List[str]", "required": True},
            {"name": "db", "type": "str", "required": False},
            {"name": "edge_semantics", "type": "str", "required": False},
            {"name": "on_cycle", "type": "str", "required": False},
            {"name": "include_cross_module", "type": "bool", "required": False},
            {"name": "include_lambdas", "type": "bool", "required": False},
            {"name": "max_functions_per_module", "type": "int", "required": False},
        ],
        "output": (
            "Dict with cross_module_edges and modules[]. For each module: "
            "waves[{wave,function_ids,functions}], function_order_ids, function_order "
            "(with id,name,qualified_name,file_path), depends_on_map, unresolved_cycles, "
            "unresolved_nodes, node_to_scc, function_dependencies"
        ),
        "example": "plan_function_dependency_order(modules=['auth','payment'], include_cross_module=true)",
    },
    {
        "name": "find_screen_workflows",
        "description": (
            "Discover ranked screen-only NAVIGATE workflows for a React/TS project. "
            "Input either a pair (node_a + node_b) or a single node_a with a direction. "
            "Paths contain only nodes with react_role='screen', are simple (no repeats), "
            "and are ranked by aggregate edge confidence DESC, total call_depth ASC, length ASC."
        ),
        "use_cases": [
            "List all business flows between two screens (e.g. RewardHome -> GoldTransfer)",
            "List all workflows that start from, end at, or touch a single screen",
            "Discover nested-navigator paths where an outer screen reaches an inner screen via components",
        ],
        "inputs": [
            {"name": "project_id", "type": "str", "required": True,
             "description": "Project scope. All nodes in the returned paths share this project_id."},
            {"name": "node_a", "type": "str", "required": True,
             "description": "Screen name (case-insensitive) or symbol_id. Source in pair mode; anchor in single mode."},
            {"name": "node_b", "type": "str", "required": False,
             "description": "Second screen. When provided, pair mode is used; otherwise single mode."},
            {"name": "direction", "type": "str", "required": False,
             "description": "single-mode only: 'inbound' | 'outbound' | 'bidirectional' (default)"},
            {"name": "max_hops", "type": "int", "required": False,
             "description": "Max NAVIGATE hops on a path (default 8, capped at 20)"},
            {"name": "max_paths", "type": "int", "required": False,
             "description": "Max workflows returned after dedup/rank (default 100, cap 1000)"},
            {"name": "include_entry_function", "type": "bool", "required": False,
             "description": "Reserved: attach entry function metadata to each workflow"},
            {"name": "include_api_calls", "type": "bool", "required": False,
             "description": "Reserved: attach API calls reachable from each workflow"},
            {"name": "db", "type": "str", "required": False,
             "description": "Neo4j database name (default 'neo4j')"},
        ],
        "output": (
            "Dict with keys: mode, direction, project_id, resolved (name->candidates), "
            "workflows (ranked list with path, edges, length, aggregate_confidence, total_call_depth, direction), "
            "uncertainties, truncated"
        ),
        "example": "find_screen_workflows(project_id='my-app', node_a='RewardHome', node_b='GoldTransfer')",
    },
    # --- Semantic / vector -------------------------------------------------------
    {
        "name": "explore_graph",
        "description": (
            "Intent-aware, multi-strategy Graph Explorer search. "
            "Accepts natural language, paragraphs, or vague descriptions. "
            "Combines semantic vector search, BM25 keyword search, and call-graph expansion "
            "with automatic query understanding (entity/domain signal extraction). "
            "Returns explainable, ranked results with per-node WHY reasons. "
            "Supports English and Vietnamese input."
        ),
        "use_cases": [
            "Search by describing a bug in plain language",
            "Find all nodes related to a domain concept (auth, payment, order…)",
            "Paste a paragraph of requirements and find relevant code",
            "Discover entry points + call-graph neighbors in one query",
            "Multi-language natural language search (EN + VI)",
        ],
        "inputs": [
            {"name": "query", "type": "str", "required": True,
             "description": "Natural language text: keyword, sentence, or multi-line paragraph"},
            {"name": "mode", "type": "str", "required": False,
             "description": "'semantic' | 'hybrid' (default) | 'graph_expanded'"},
            {"name": "top_k", "type": "int", "required": False,
             "description": "Max matched nodes to return (default: 10)"},
            {"name": "db", "type": "str", "required": False,
             "description": "Neo4j database name"},
            {"name": "collection", "type": "str", "required": False,
             "description": "Qdrant collection name"},
            {"name": "debug", "type": "bool", "required": False,
             "description": "Include per-signal score breakdown in results"},
        ],
        "output": (
            "Dict with: matched_nodes (list of nodes with reason/score/signals), "
            "entry_points (subset of high-importance nodes), "
            "related_paths (graph-expanded neighbors), "
            "explanation (human-readable summary), "
            "confidence (0.0–1.0), "
            "query_analysis (extracted intent/entities/domain_signals), "
            "mode"
        ),
        "example": (
            "explore_graph(query='function xử lý thanh toán bị lỗi khi user chưa login', "
            "mode='graph_expanded', top_k=15)"
        ),
    },
    {
        "name": "semantic_search",
        "description": "Semantic code search using Qdrant vector embeddings. Find similar code/comments by meaning.",
        "use_cases": ["Find semantically similar code", "Search by natural language",
                      "Locate similar implementations", "Documentation search"],
        "inputs": [
            {"name": "query", "type": "str", "required": True,
             "description": "Natural language query or code snippet"},
            {"name": "mode", "type": "str", "required": False,
             "description": "'code', 'comment', 'hybrid'"},
            {"name": "top_k", "type": "int", "required": False,
             "description": "Number of results (default: 10)"},
            {"name": "collection", "type": "str", "required": False,
             "description": "Qdrant collection name"},
            {"name": "qdrant_url", "type": "str", "required": False},
        ],
        "output": "Dict with semantically similar code snippets ranked by relevance",
        "example": "semantic_search(query='allocate memory safely', mode='code', top_k=5)",
    },
    # --- IPC / Android -----------------------------------------------------------
    {
        "name": "get_ipc_message",
        "description": "Query IPC/message records by sender/receiver (Neo4j Message nodes first, JSON fallback).",
        "use_cases": ["Find IPC between components", "Trace message passing",
                      "Android Intent flows", "Event communication"],
        "inputs": [
            {"name": "sender", "type": "str", "required": False,
             "description": "Sender component pattern. If only sender provided, returns list of receivers."},
            {"name": "receiver", "type": "str", "required": False,
             "description": "Receiver component pattern. If only receiver provided, returns list of senders."},
            {"name": "db", "type": "str", "required": False},
        ],
        "output": "Dict with IPC message details or sender/receiver lists",
        "example": "get_ipc_message(sender='Activity', receiver='Service')",
    },
    # --- Graph utilities ---------------------------------------------------------
    {
        "name": "list_possible_calls",
        "description": "List POSSIBLE_CALLS relationships (function pointer calls, virtual calls, callback registrations).",
        "use_cases": ["Find indirect calls", "Trace callback chains",
                      "Virtual function analysis", "Function pointer usage"],
        "inputs": [
            {"name": "limit", "type": "int", "required": False,
             "description": "Max results (default: 100)"},
            {"name": "db", "type": "str", "required": False},
        ],
        "output": "Dict with POSSIBLE_CALLS edges",
        "example": "list_possible_calls(limit=50)",
    },
    {
        "name": "annotate_node",
        "description": "Add or update annotations (notes/tags/severity) on a node for documentation/review purposes.",
        "use_cases": ["Mark functions for review", "Tag security issues",
                      "Add documentation notes", "Flag technical debt"],
        "inputs": [
            {"name": "node_id", "type": "str", "required": True},
            {"name": "note", "type": "str", "required": False, "description": "Text note"},
            {"name": "tags", "type": "str", "required": False,
             "description": "Comma-separated tags"},
            {"name": "severity", "type": "str", "required": False,
             "description": "Severity level (e.g., 'high', 'medium', 'low')"},
            {"name": "db", "type": "str", "required": False},
        ],
        "output": "Dict with updated node",
        "example": "annotate_node(node_id='func_123', note='Buffer overflow risk', severity='high')",
    },
    # --- Infrastructure ----------------------------------------------------------
    {
        "name": "list_databases",
        "description": "List all available Neo4j databases in the system.",
        "use_cases": ["Discover available projects", "Switch between databases",
                      "Check database names"],
        "inputs": [],
        "output": "Dict with list of database names",
        "example": "list_databases()",
    },
    {
        "name": "list_qdrant_collections",
        "description": "List all Qdrant vector collections for semantic search.",
        "use_cases": ["Discover available collections", "Check embeddings status"],
        "inputs": [
            {"name": "qdrant_url", "type": "str", "required": False},
            {"name": "include_vectors", "type": "bool", "required": False},
        ],
        "output": "Dict with Qdrant collections and metadata",
        "example": "list_qdrant_collections()",
    },
    {
        "name": "list_parsers",
        "description": "List supported code parser types (languages/frameworks).",
        "use_cases": ["Check supported languages", "Discover parser options"],
        "inputs": [],
        "output": "Dict with available parsers (e.g., cplus, java, kotlin, android, etc.)",
        "example": "list_parsers()",
    },
    {
        "name": "list_workflows",
        "description": "List workflow definitions with optional filters.",
        "use_cases": ["Discover available workflows", "Filter workflows by project/language/domain"],
        "inputs": [
            {"name": "project", "type": "str", "required": False},
            {"name": "language", "type": "str", "required": False},
            {"name": "domain", "type": "str", "required": False},
            {"name": "limit", "type": "int", "required": False},
            {"name": "db", "type": "str", "required": False},
        ],
        "output": "Dict with workflows[] and total",
        "example": "list_workflows(project='my-app', language='kotlin')",
    },
    {
        "name": "get_workflow_steps",
        "description": "Get ordered function steps of a workflow by workflow_id.",
        "use_cases": ["Inspect workflow execution steps", "Trace workflow implementation path"],
        "inputs": [
            {"name": "workflow_id", "type": "str", "required": True},
            {"name": "db", "type": "str", "required": False},
        ],
        "output": "Dict with workflow metadata and steps[] ordered by step_order",
        "example": "get_workflow_steps(workflow_id='wf_checkout')",
    },
    {
        "name": "search_workflows",
        "description": "Search workflows by keyword on name, description, and domain.",
        "use_cases": ["Find business flows by keyword", "Locate domain-specific workflows"],
        "inputs": [
            {"name": "query", "type": "str", "required": True},
            {"name": "limit", "type": "int", "required": False},
            {"name": "db", "type": "str", "required": False},
        ],
        "output": "Dict with workflows[] and total",
        "example": "search_workflows(query='payment', limit=20)",
    },
    {
        "name": "list_mcp_functions",
        "description": "List all available MCP tools with descriptions, parameters, and use cases. Call this FIRST to discover what tools are available before making other calls.",
        "use_cases": ["Tool discovery", "Understand available capabilities",
                      "Get parameter reference before calling a tool"],
        "inputs": [],
        "output": "Dict with total_count and functions list (name, description, use_cases, inputs, output, example)",
        "example": "list_mcp_functions()",
    },
    {
        "name": "reconstruct_flow",
        "description": (
            "Reconstruct POSSIBLE execution flows from candidate graph paths. "
            "Produces a grounded, traceable flow representation mapping to real node_ids "
            "consumable by AI agents for reasoning, explanation, and impact analysis."
        ),
        "use_cases": [
            "Reconstruct backend call flows from path data",
            "Reconstruct frontend event → handler → API → navigation flows",
            "Build hybrid UI-to-backend flows",
            "Explain how a trigger reaches a target function",
            "Impact analysis with ordered execution context",
        ],
        "inputs": [
            {
                "name": "entry_context_json",
                "type": "str",
                "required": True,
                "description": (
                    'JSON string: {"type": "backend|frontend|hybrid", '
                    '"entry_point": str, "entry_node_id": str, '
                    '"screen": str|null, "trigger": str|null}'
                ),
            },
            {
                "name": "paths_json",
                "type": "str",
                "required": True,
                "description": (
                    "JSON string: array of path objects. Each path: "
                    '{"path_id": str, "nodes": [{node_id, name, mapped_type, location}], '
                    '"edges": [{from, to, type}]}'
                ),
            },
        ],
        "output": (
            '{"flows": [...], "uncertainties": [...]}. '
            "Each flow: flow_id, title, type, confidence (high/medium/low), "
            "entry_node_id, paths_used, discarded_paths, steps[]. "
            "Each step: step_id, node_id, name, mapped_type, path_ids, "
            "relation (direct_edge/same_path_sequence/inferred_bridge/shared_state/unknown), "
            "reason_text, uncertainty (low/medium/high)."
        ),
        "example": (
            "reconstruct_flow("
            "entry_context_json='{\"type\":\"backend\",\"entry_point\":\"main\","
            "\"entry_node_id\":\"n1\",\"screen\":null,\"trigger\":null}', "
            "paths_json='[{\"path_id\":\"path_1\",\"nodes\":[{\"node_id\":\"n1\","
            "\"name\":\"main\",\"mapped_type\":\"function\","
            "\"location\":{\"file\":\"main.c\",\"line\":10}}],"
            "\"edges\":[]}]')"
        ),
    },
    {
        "name": "find_callers_of_endpoint",
        "description": (
            "Return frontend functions/screens that call a specific backend API endpoint "
            "via Function -> CALLS_API -> ApiCall -> MATCHES -> ApiEndpoint."
        ),
        "use_cases": [
            "Find all screens calling a backend endpoint",
            "Trace endpoint usage from frontend",
            "Impact analysis before backend API changes",
        ],
        "inputs": [
            {"name": "endpoint_path", "type": "str", "required": True,
             "description": "Backend endpoint path (e.g. '/api/users/:id')"},
            {"name": "http_method", "type": "str", "required": False,
             "description": "HTTP method filter (GET/POST/PUT/DELETE/ALL)"},
            {"name": "be_project_id", "type": "str", "required": False,
             "description": "Backend project_id filter"},
            {"name": "fe_project_id", "type": "str", "required": False,
             "description": "Frontend project_id filter"},
            {"name": "db", "type": "str", "required": False,
             "description": "Neo4j database name"},
        ],
        "output": "Dict with endpoint_path, callers (frontend symbols), and total",
        "example": "find_callers_of_endpoint(endpoint_path='/api/users/:id', http_method='GET')",
    },
    {
        "name": "get_api_call_chain",
        "description": (
            "Return fullstack call chain from frontend component or endpoint to backend "
            "layers (ApiEndpoint, Controller, Service, Repository, Database)."
        ),
        "use_cases": [
            "Trace end-to-end FE -> BE -> DB execution chain",
            "Understand backend dependencies of a screen",
            "Audit data access path for an API",
        ],
        "inputs": [
            {"name": "component_name", "type": "str", "required": False,
             "description": "Frontend component/screen name"},
            {"name": "endpoint_path", "type": "str", "required": False,
             "description": "Backend endpoint path (used when component_name is not provided)"},
            {"name": "fe_project_id", "type": "str", "required": False,
             "description": "Frontend project_id filter"},
            {"name": "be_project_id", "type": "str", "required": False,
             "description": "Backend project_id filter"},
            {"name": "max_depth", "type": "int", "required": False,
             "description": "Max frontend CALLS hops (default: 5)"},
            {"name": "db", "type": "str", "required": False,
             "description": "Neo4j database name"},
        ],
        "output": "Dict with chains (fe/api/be/database segments) and total",
        "example": "get_api_call_chain(component_name='UserProfileScreen', max_depth=5)",
    },
    {
        "name": "analyze_workflow_impact",
        "description": (
            "Analyze change impact for a function/screen at call-graph and workflow levels, "
            "including risk scoring and recommendation."
        ),
        "use_cases": [
            "Estimate blast radius before refactoring",
            "Detect workflow-level regression risk",
            "Prioritize test scope by impact severity",
        ],
        "inputs": [
            {"name": "function_id", "type": "str", "required": True,
             "description": "Function/screen symbol_id to analyze"},
            {"name": "db", "type": "str", "required": False,
             "description": "Neo4j database name"},
            {"name": "direction", "type": "str", "required": False,
             "description": "Traversal direction: downstream or upstream"},
            {"name": "max_depth", "type": "int", "required": False,
             "description": "Traversal depth cap (default: 4, max: 4)"},
        ],
        "output": "Dict with risk_score, impacted_nodes, and workflow_impact details",
        "example": "analyze_workflow_impact(function_id='func_123', direction='downstream')",
    },
    {
        "name": "find_workflows_containing",
        "description": (
            "Find workflows containing a function directly (HAS_STEP) or indirectly "
            "(reachable through CALLS chain)."
        ),
        "use_cases": [
            "List workflows affected by a function change",
            "Validate workflow ownership of a code path",
            "Support regression planning by workflow coverage",
        ],
        "inputs": [
            {"name": "function_id", "type": "str", "required": True,
             "description": "Function symbol_id or file_path anchor"},
            {"name": "db", "type": "str", "required": False,
             "description": "Neo4j database name"},
            {"name": "include_indirect", "type": "bool", "required": False,
             "description": "Include CALLS-chain derived workflows (default: true)"},
            {"name": "max_depth", "type": "int", "required": False,
             "description": "Indirect traversal depth cap (default: 4, max: 4)"},
        ],
        "output": "Dict with direct_workflows, indirect_workflows, and total",
        "example": "find_workflows_containing(function_id='func_123', include_indirect=True)",
    },
    {
        "name": "trace_flow_between_module",
        "description": (
            "Trace flow paths between functions in two modules using Android interaction edges "
            "(CALLS, routes, intents, events, etc.)."
        ),
        "use_cases": [
            "Trace cross-module Android IPC",
            "Find inter-module event flows",
        ],
        "example": "trace_flow_between_module(source_modules=['ui/'], target_modules=['service/'])",
    },
    {
        "name": "activate_project",
        "inputs": [
            {"name": "parser_type", "type": "str", "required": False,
             "description": "Parser type: android / android-kotlin / kotlin-android"},
            {"name": "database_name", "type": "str", "required": False,
             "description": "Neo4j database name for the Android project"},
        ],
        "example": "activate_project(parser_type='android', database_name='my_android_db')",
    },
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_catalog(
    tool_names: Set[str],
    overrides: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Return the filtered, optionally-overridden list of tool metadata entries.

    Args:
        tool_names: Set of tool name strings the backend actually registers.
        overrides:  Optional dict mapping tool_name -> partial field overrides
                    (e.g., to change description or use_cases for a specific backend).

    Returns:
        List of tool metadata dicts, preserving the canonical ordering.
    """
    overrides = overrides or {}
    result: List[Dict[str, Any]] = []
    for entry in _FULL_CATALOG:
        name = entry["name"]
        if name not in tool_names:
            continue
        item = deepcopy(entry)
        if name in overrides:
            item.update(overrides[name])
        result.append(item)
    return result
