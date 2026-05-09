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
             "description": "Parser type: cplus/cpp/c++/c/clang/java/kotlin/jvm or android/android-kotlin"},
            {"name": "database_name", "type": "str", "required": False,
             "description": "Neo4j database name (e.g., 'neo4j', 'sample_database', 'minat')"},
        ],
        "output": "Dict with parser_type, database_name, activated status",
        "example": "activate_project(parser_type='cplus', database_name='sample_database')",
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
             "description": "Source file path patterns (e.g., ['sample_database01', 'SampleDatabase'])"},
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
        "example": "find_path_between_module(source_modules=['sample_database01'], target_modules=['minat01'], direction='both')",
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
        "example": "listup_symbols_matching_file_path(modules=['sample_database01.c'], node_types=['Function'])",
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
        "example": "trace_flow_between_module(source_modules=['sample_database'], target_modules=['minat'], rel_types=['CALLS'])",
    },
    # --- Semantic / vector -------------------------------------------------------
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
        "description": "Query IPC (Inter-Process Communication) messages by sender/receiver components from ipc_messages.json.",
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
        "name": "list_mcp_functions",
        "description": "List all available MCP tools with descriptions, parameters, and use cases. Call this FIRST to discover what tools are available before making other calls.",
        "use_cases": ["Tool discovery", "Understand available capabilities",
                      "Get parameter reference before calling a tool"],
        "inputs": [],
        "output": "Dict with total_count and functions list (name, description, use_cases, inputs, output, example)",
        "example": "list_mcp_functions()",
    },
]

# Keyed lookup for O(1) access
_CATALOG_BY_NAME: Dict[str, Dict[str, Any]] = {t["name"]: t for t in _FULL_CATALOG}

# ---------------------------------------------------------------------------
# Backend-specific overrides
# ---------------------------------------------------------------------------

# Android: trace_flow uses Android-specific edge types
ANDROID_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "trace_flow": {
        "description": (
            "Trace a call/interaction flow across Android graph edges "
            "(UI resources, routes, intents, events, broadcasts, handler messages, etc.)."
        ),
        "use_cases": [
            "Trace Android Intent flows",
            "Follow broadcast sender → receiver chains",
            "Trace NavRoute → Fragment navigation",
            "Follow handler message dispatch",
        ],
        "example": "trace_flow(start_id='activityA', end_id='serviceB', rel_types=['STARTS_INTENT', 'CALLS'])",
    },
    "trace_flow_between_module": {
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
    "activate_project": {
        "inputs": [
            {"name": "parser_type", "type": "str", "required": False,
             "description": "Parser type: android / android-kotlin / kotlin-android"},
            {"name": "database_name", "type": "str", "required": False,
             "description": "Neo4j database name for the Android project"},
        ],
        "example": "activate_project(parser_type='android', database_name='my_android_db')",
    },
}


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
