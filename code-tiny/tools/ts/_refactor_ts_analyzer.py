"""One-shot surgery: replace all inline definitions in ts_analyzer.py with imports.

Run from the hyper-graph root:
    python tools/ts/_refactor_ts_analyzer.py
"""
from __future__ import annotations
import os
import sys

TARGET = os.path.join(os.path.dirname(__file__), "ts_analyzer.py")

# ── Import block that replaces the inline definitions ─────────────────────────
IMPORT_BLOCK = '''\
# ── Sub-package imports (replaces former inline definitions) ──────────────────
from tools.ts.types.ast_types import (
    FunctionDef,
    ApiCallDef,
    RenderEdge,
    NavigateEdge,
    RouteConfigEntry,
    NavigatorDef,
    ParamListDef,
)
from tools.ts.types.graph_types import (
    FileDef,
    NamespaceDef,
    TypeDef,
    RelationEdge,
    CallEdge,
)
from tools.ts.utils.id_utils import (
    _symbol_id,
    _qualified_name,
    _type_id,
    _namespace_id,
    _anonymous_name,
    _stable_point_id,
)
from tools.ts.utils.file_utils import (
    _PARSE_CACHE_VERSION,
    _TS_SOURCE_EXTENSIONS,
    _SCAN_SKIP_DIRS,
    _SCREEN_DIR_SEGMENTS,
    _SERVICE_DIR_SEGMENTS,
    _INDEX_BASENAMES,
    _index_module_name,
    _is_screen_file,
    _is_service_file,
    _scan_ts_files,
    _file_path_to_route,
)
from tools.ts.utils.regex_patterns import (
    _SCREEN_NAME_SUFFIXES,
    _WRAPPER_NAME_SUFFIXES,
    _RE_HOC_FACTORY_NAME,
    _RE_WRAPS_CHILDREN,
    _NAV_CHROME_SUFFIXES,
    _NAVIGATOR_NAME_SUFFIXES,
    _RE_NAVIGATOR_FACTORY_NAME,
    _RE_SCREEN_HOOKS,
    _RE_SCREEN_NAV_CALL,
    _RE_SCREEN_PROP_NAMES,
    _RE_MIDDLEWARE_API,
    _RE_MIDDLEWARE_QUERY,
    _RE_MIDDLEWARE_REDUX,
    _RE_SERVICE_LAYER,
    _RE_FETCH_CALL,
    _RE_FETCH_METHOD,
    _RE_AXIOS_SHORTHAND,
    _RE_AXIOS_CONFIG,
    _RE_HTTP_CLIENT,
    _RE_NAMED_CLIENT,
    _RE_AXIOS_CREATE,
    _RE_ENV_VAR,
    _RE_ASSIGN_USE_NAVIGATION,
    _RE_ASSIGN_USE_NAVIGATION_DESTRUCT,
    _RE_ASSIGN_USE_ROUTER,
    _RE_ASSIGN_USE_NAVIGATE,
    _RE_ASSIGN_USE_HISTORY,
    _RE_NAV_PROP_CALL,
    _RE_NAV_PROP_OBJ,
    _RE_ROUTER_CALL,
    _RE_ROUTER_OBJ,
    _RE_NAV_REF_CALL,
    _RE_JSX_LINK,
    _RE_JSX_NAVIGATE_EL,
    _nav_obj_method_re,
    _nav_fn_call_re,
    _RE_USER_TRIGGER,
    _RE_SYSTEM_TRIGGER,
    _RE_ASYNC_TRIGGER,
    _RE_AUTH_GUARD,
    _RE_PERM_GUARD,
    _RE_SCREEN_ELEM_START,
    _RE_SCREEN_NAME_ATTR,
    _RE_SCREEN_COMP_ATTR,
    _RE_NAVIGATOR_FACTORY,
    _FACTORY_TO_NAV_TYPE,
    _CALL_EXPR_KIND_MAP,
)
from tools.ts.agents.parser_agent import (
    _JSX_NODE_TYPES,
    _get_ts_parser,
    _parse_file,
    _node_text,
    _line_from_byte,
    _node_snippet,
    _find_nodes_by_type,
    _is_benign_jsx_entity_error,
    _tree_error_stats,
    _first_identifier,
    _extract_name_field,
    _extract_leading_comment,
    _extract_file_comment,
    _build_note,
    _normalize_ws,
    _normalize_call_name,
    _extract_scope_stack,
    _count_parameters,
    _extract_return_type,
    _extract_param_types,
    _count_arguments,
    _iter_calls,
    _extract_call_name,
    _collect_imports,
    _collect_exports,
    _jsx_name,
    _collect_jsx_tags,
)
from tools.ts.agents.dependency_agent import (
    _collect_ts_import_graph,
    _expand_impacted_files_by_imports,
)
from tools.ts.agents.symbol_agent import (
    _detect_middleware_kind,
    _detect_react_role,
    _clean_url_expr,
    _extract_file_base_url,
    _extract_api_calls,
    _collect_navigate_calls,
    _classify_nav_context,
    _detect_nav_guard,
    _collect_route_configs,
    _extract_navigator_declarations,
    _extract_param_lists,
    _has_jsx_in_subtree,
    _collect_rendered_components,
)
from tools.ts.agents.traversal_agent import (
    _NAMESPACE_NODE_TYPES,
    _TYPE_NODE_KINDS,
    _FUNCTION_NODE_KINDS,
    _INNER_FUNCTION_TYPES,
    _find_inner_function_arg,
    _extract_root_factory_name,
    _record_function,
    _walk_tree,
)
# ─────────────────────────────────────────────────────────────────────────────

'''

# ── Markers ────────────────────────────────────────────────────────────────────
# Start: support both the old and current marker forms so the script remains
# usable across refactor iterations.
START_MARKERS = [
    "# ── Modular sub-package imports ───────────────────────────────────────────────",
    "# ── Sub-package imports (replaces former inline definitions) ──────────────────",
    '_PARSE_CACHE_VERSION = "ts-v2026-04-06-6"',
]

# End: the final line of `_walk_tree` is the `for child …` loop — `parse_ts_file`
# immediately follows.  Use the signature line as an anchor so we stop BEFORE it.
END_MARKER = "\ndef parse_ts_file("


def main() -> None:
    with open(TARGET, "r", encoding="utf-8") as fh:
        source = fh.read()

    # ── Locate start ──────────────────────────────────────────────────────────
    start_idx = -1
    for marker in START_MARKERS:
        start_idx = source.find(marker)
        if start_idx != -1:
            break
    if start_idx == -1:
        # If modular imports already exist with a slightly different marker,
        # treat this run as a safe no-op instead of failing hard.
        if (
            "from tools.ts.types.ast_types import (" in source
            and "\ndef parse_ts_file(" in source
        ):
            print("[refactor] ts_analyzer.py appears already modularized; nothing to do.")
            return
        print("ERROR: Could not locate start marker in ts_analyzer.py", file=sys.stderr)
        sys.exit(1)

    # ── Locate end ────────────────────────────────────────────────────────────
    end_idx = source.find(END_MARKER, start_idx)
    if end_idx == -1:
        print("ERROR: Could not locate end marker 'def parse_ts_file(' in ts_analyzer.py", file=sys.stderr)
        sys.exit(1)
    # Include the leading newline so we leave exactly one blank line before parse_ts_file
    end_idx += 1  # skip the leading '\n' — the import block already ends with '\n\n'

    removed_lines = source[start_idx:end_idx].count("\n")
    new_source = source[:start_idx] + IMPORT_BLOCK + source[end_idx:]
    if new_source == source:
        print("[refactor] No changes needed (already up to date).")
        return

    # ── Write back ────────────────────────────────────────────────────────────
    backup_path = TARGET + ".bak"
    with open(backup_path, "w", encoding="utf-8") as fh:
        fh.write(source)
    print(f"[refactor] Backup saved: {backup_path}")

    with open(TARGET, "w", encoding="utf-8") as fh:
        fh.write(new_source)

    new_lines = new_source.count("\n")
    print(
        f"[refactor] Done.  Removed {removed_lines} lines of inline definitions, "
        f"replaced with import block.  New file: {new_lines} lines."
    )


if __name__ == "__main__":
    main()
