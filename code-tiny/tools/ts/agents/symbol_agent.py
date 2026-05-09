"""SymbolAgent — React role classification, API extraction, and navigation detection.

Responsibilities:
- Detect middleware kind from function name / code / file path.
- Classify a component function as screen / component / hook / middleware.
- Extract outgoing HTTP API calls (fetch, axios, HttpClient, named clients).
- Collect navigate() / push() / Link targets from function bodies.
- Extract route configs from Navigator JSX declarations.
- Extract navigator declarations (createStackNavigator, etc.).
- Extract ParamList type aliases via tree-sitter.
- Collect rendered JSX component names from an AST subtree.
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from tools.ts.types.ast_types import (
    ApiCallDef, NavigateEdge, NavigatorDef, ParamListDef, RenderEdge,
)
from tools.ts.utils.file_utils import (
    _index_module_name, _is_screen_file, _is_service_file, _file_path_to_route,
)
from tools.ts.utils.regex_patterns import (
    # React role classification
    _RE_HOC_FACTORY_NAME, _RE_WRAPS_CHILDREN,
    _WRAPPER_NAME_SUFFIXES, _NAV_CHROME_SUFFIXES, _NAVIGATOR_NAME_SUFFIXES,
    _RE_NAVIGATOR_FACTORY_NAME, _SCREEN_NAME_SUFFIXES,
    _RE_SCREEN_HOOKS, _RE_SCREEN_NAV_CALL, _RE_SCREEN_PROP_NAMES,
    # Middleware detection
    _RE_MIDDLEWARE_API, _RE_MIDDLEWARE_QUERY, _RE_MIDDLEWARE_REDUX, _RE_SERVICE_LAYER,
    # API extraction
    _RE_FETCH_CALL, _RE_FETCH_METHOD,
    _RE_AXIOS_SHORTHAND, _RE_AXIOS_CONFIG, _RE_HTTP_CLIENT, _RE_NAMED_CLIENT,
    _RE_AXIOS_CREATE, _RE_ENV_VAR,
    # Navigation detection
    _RE_ASSIGN_USE_NAVIGATION, _RE_ASSIGN_USE_NAVIGATION_DESTRUCT,
    _RE_ASSIGN_USE_ROUTER, _RE_ASSIGN_USE_NAVIGATE, _RE_ASSIGN_USE_HISTORY,
    _RE_NAV_PROP_CALL, _RE_NAV_PROP_OBJ,
    _RE_ROUTER_CALL, _RE_ROUTER_OBJ, _RE_NAV_REF_CALL,
    _RE_NAV_SERVICE_CALL, _RE_NAV_SERVICE_OBJ,
    _RE_JSX_LINK, _RE_JSX_NAVIGATE_EL,
    _nav_obj_method_re, _nav_fn_call_re,
    # Navigation V2.0
    _RE_USER_TRIGGER, _RE_SYSTEM_TRIGGER, _RE_ASYNC_TRIGGER,
    _RE_AUTH_GUARD, _RE_PERM_GUARD,
    _RE_SCREEN_ELEM_START, _RE_SCREEN_NAME_ATTR, _RE_SCREEN_COMP_ATTR,
    # Navigator factory
    _RE_NAVIGATOR_FACTORY, _FACTORY_TO_NAV_TYPE,
)
from tools.ts.agents.parser_agent import (
    _find_nodes_by_type, _node_text, _first_identifier,
)


# ─── URL helpers ─────────────────────────────────────────────────────────────

def normalize_url_pattern(url: str) -> str:
    """Normalize a URL string into a stable route pattern."""
    if not url:
        return ""
    url = url.strip()
    # Template literals: replace ${...} with :param
    url = re.sub(r'\$\{[^}]+\}', ':param', url)
    # Strip trailing slash (unless root)
    if url != "/" and url.endswith("/"):
        url = url.rstrip("/")
    return url


def normalize_http_method(method: str) -> str:
    return method.upper()


def merge_base_url(base: Optional[str], path: str) -> str:
    if not base:
        return normalize_url_pattern(path)
    base = base.rstrip("/")
    path = path if path.startswith("/") else "/" + path
    return normalize_url_pattern(base + path)


# ─── Middleware detection ─────────────────────────────────────────────────────

def _detect_middleware_kind(name: str, code: str, file_path: str) -> str:
    """Return the middleware sub-kind or empty string if not a middleware."""
    if _RE_MIDDLEWARE_API.search(code):
        return "api"
    if _RE_MIDDLEWARE_QUERY.search(code):
        return "query"
    if _RE_MIDDLEWARE_REDUX.search(code):
        return "redux"
    if _RE_SERVICE_LAYER.search(code):
        return "service"
    if _is_service_file(file_path):
        return "service"
    return ""


# ─── React role classification ────────────────────────────────────────────────

def _detect_react_role(
    name: str,
    file_path: str,
    has_jsx: bool,
    middleware_kind: str,
    code: str = "",
) -> str:
    """Classify the React role of a function symbol.

    Priority order:
      middleware > hook > screen > component > "" (plain function)

    Screen detection uses semantic signals in priority order:
      1. (Strongest) Navigation hooks in body
      2. Imperative navigation calls
      3. Receives React-Navigation props by name
      4. File lives in a routing directory
      5. (Weakest) Name or index-module folder ends with Screen/Page/View/Tab/Scene/Activity
    """
    if middleware_kind:
        return "middleware"
    if name.startswith("use") and len(name) > 3 and name[3].isupper():
        return "hook"
    if has_jsx and name and name[0].isupper():
        # HOC / layout-wrapper demotion
        _is_hoc_name = (
            bool(_RE_HOC_FACTORY_NAME.match(name))
            or name.endswith(_WRAPPER_NAME_SUFFIXES)
        )
        if _is_hoc_name or bool(_RE_WRAPS_CHILDREN.search(code)):
            return "component"
        # Navigation chrome demotion
        if name.endswith(_NAV_CHROME_SUFFIXES):
            return "component"
        # Navigator / router demotion
        if (
            name.endswith(_NAVIGATOR_NAME_SUFFIXES)
            or bool(_RE_NAVIGATOR_FACTORY_NAME.match(name))
        ):
            return "component"
        # Corroborating signals
        folder_name = _index_module_name(file_path) or ""
        _has_screen_name = (
            name.endswith(_SCREEN_NAME_SUFFIXES)
            or folder_name.endswith(_SCREEN_NAME_SUFFIXES)
        )
        _in_screen_dir = _is_screen_file(file_path)
        # 1. Navigation hooks
        if _RE_SCREEN_HOOKS.search(code):
            if _has_screen_name or _in_screen_dir:
                return "screen"
        # 2. Imperative nav calls
        if _RE_SCREEN_NAV_CALL.search(code):
            if _has_screen_name or _in_screen_dir:
                return "screen"
        # 3. Receives React-Navigation props
        if _RE_SCREEN_PROP_NAMES.search(code):
            if _has_screen_name or _in_screen_dir:
                return "screen"
        # 4. File in screen/routing directory
        if _in_screen_dir:
            return "screen"
        # 5. Name suffix fallback
        if _has_screen_name:
            return "screen"
        return "component"
    return ""


# ─── API URL extraction ───────────────────────────────────────────────────────

def _clean_url_expr(raw: str) -> str:
    s = raw.strip().strip('`\'"')
    s = _RE_ENV_VAR.sub('', s)
    s = s.strip().lstrip('+').strip().strip('"\'`').strip()
    return s


def _extract_file_base_url(code: str) -> str:
    m = _RE_AXIOS_CREATE.search(code)
    if not m:
        return ""
    raw = m.group("base").strip().strip('`\'"')
    raw = _RE_ENV_VAR.sub('', raw).strip().lstrip('+').strip().strip('`\'"')
    if not raw or 'process' in raw or 'env' in raw.lower():
        return ""
    norm = normalize_url_pattern(raw)
    return norm or ""


def _extract_api_calls(
    code: str,
    function_id: str,
    rel_path: str,
    start_line: int,
    file_base_url: str = "",
) -> List[ApiCallDef]:
    """Extract outgoing HTTP request definitions from a function body."""
    results: List[ApiCallDef] = []

    def _make_call(raw_url: str, method: str, base_url: str = "") -> Optional[ApiCallDef]:
        cleaned = _clean_url_expr(raw_url)
        if not cleaned:
            return None
        if base_url or file_base_url:
            resolved = merge_base_url(base_url or file_base_url or None, cleaned)
        else:
            resolved = normalize_url_pattern(cleaned)
        if not resolved:
            return None
        norm_method = normalize_http_method(method)
        sid = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"ApiCall::{function_id}::{norm_method}::{resolved}",
        ))
        return ApiCallDef(
            symbol_id=sid,
            caller_function_id=function_id,
            url_pattern=resolved,
            raw_url=raw_url.strip(),
            http_method=norm_method,
            base_url_ref=base_url or file_base_url,
            file_path=rel_path,
            start_line=start_line,
            confidence=0.85,
        )

    for m in _RE_FETCH_CALL.finditer(code):
        raw = m.group("url")
        vicinity = code[m.start(): m.start() + 300]
        mm = _RE_FETCH_METHOD.search(vicinity)
        method = mm.group("method") if mm else "GET"
        call = _make_call(raw, method)
        if call:
            results.append(call)

    for m in _RE_AXIOS_SHORTHAND.finditer(code):
        call = _make_call(m.group("url"), m.group("method"))
        if call:
            results.append(call)

    for m in _RE_AXIOS_CONFIG.finditer(code):
        method = m.group("method") or "GET"
        call = _make_call(m.group("url"), method)
        if call:
            results.append(call)

    for m in _RE_HTTP_CLIENT.finditer(code):
        call = _make_call(m.group("url"), m.group("method"))
        if call:
            results.append(call)

    for m in _RE_NAMED_CLIENT.finditer(code):
        call = _make_call(m.group("url"), m.group("method"))
        if call:
            results.append(call)

    seen: Set[str] = set()
    deduped: List[ApiCallDef] = []
    for c in results:
        key = f"{c.http_method}:{c.url_pattern}"
        if key not in seen:
            seen.add(key)
            deduped.append(c)
    return deduped


# ─── Navigation call extraction ───────────────────────────────────────────────

def _collect_navigate_calls(code: str) -> List[Tuple[str, str]]:
    """Return deduplicated ``(target, method)`` pairs for navigation calls."""
    seen: dict = {}
    nav_obj_vars: Set[str] = {"navigation", "navigator"}
    nav_fn_vars: Set[str] = set()
    hist_vars: Set[str] = set()

    for m in _RE_ASSIGN_USE_NAVIGATION.finditer(code):
        nav_obj_vars.add(m.group("var"))
    for m in _RE_ASSIGN_USE_ROUTER.finditer(code):
        nav_obj_vars.add(m.group("var"))
    for m in _RE_ASSIGN_USE_HISTORY.finditer(code):
        hist_vars.add(m.group("var"))
    if _RE_ASSIGN_USE_NAVIGATION_DESTRUCT.search(code):
        nav_fn_vars.add("navigate")
    for m in _RE_ASSIGN_USE_NAVIGATE.finditer(code):
        nav_fn_vars.add(m.group("var"))

    _has_use_router = bool(re.search(r'\buseRouter\s*\(', code))
    _has_use_history = bool(re.search(r'\buseHistory\s*\(', code))

    for m in _RE_NAV_PROP_CALL.finditer(code):
        t = m.group("target")
        if t:
            seen[(t, m.group("method"))] = None
    for m in _RE_NAV_PROP_OBJ.finditer(code):
        t = m.group("target")
        if t:
            seen[(t, "navigate")] = None

    for var in nav_obj_vars - {"navigation", "navigator"}:
        for mm in _nav_obj_method_re(var).finditer(code):
            t = mm.group("target")
            if t:
                seen[(t, mm.group("method"))] = None

    if _has_use_router:
        for m in _RE_ROUTER_CALL.finditer(code):
            t = m.group("target")
            if t:
                seen[(t, m.group("method"))] = None
        for m in _RE_ROUTER_OBJ.finditer(code):
            t = m.group("target")
            if t:
                seen[(t, m.group("method"))] = None
    if _has_use_history:
        for var in (hist_vars or {"history"}):
            for mm in _nav_obj_method_re(var).finditer(code):
                t = mm.group("target")
                if t:
                    seen[(t, mm.group("method"))] = None

    for var in nav_fn_vars:
        for mm in _nav_fn_call_re(var).finditer(code):
            t = mm.group("target")
            if t:
                seen[(t, "navigate")] = None

    for m in _RE_NAV_REF_CALL.finditer(code):
        t = m.group("target")
        if t:
            seen[(t, m.group("method"))] = None

    # Generic navigation-service wrappers: ``navigationServices.navigate('X')``,
    # ``navService.push('X')``, ``appNav.replace('X')``, etc.  Captures any
    # identifier containing ``navig`` and must come AFTER the specific
    # ``navigation``/``navigator`` patterns so the dedup dict keeps the
    # first-seen entry; duplicates are harmless either way.
    for m in _RE_NAV_SERVICE_CALL.finditer(code):
        t = m.group("target")
        if t:
            seen[(t, m.group("method"))] = None
    for m in _RE_NAV_SERVICE_OBJ.finditer(code):
        t = m.group("target")
        if t:
            seen[(t, "navigate")] = None

    for m in _RE_JSX_LINK.finditer(code):
        t = m.group("route") or m.group("route2")
        if t:
            seen[(t.strip(), "link")] = None

    for m in _RE_JSX_NAVIGATE_EL.finditer(code):
        t = m.group("route") or m.group("route2")
        if t:
            seen[(t.strip(), "navigate")] = None

    return list(seen.keys())


# ─── Navigation V2.0: context + guard ────────────────────────────────────────

def _classify_nav_context(code: str) -> str:
    if _RE_USER_TRIGGER.search(code):
        return "user"
    if _RE_ASYNC_TRIGGER.search(code):
        return "async"
    if _RE_SYSTEM_TRIGGER.search(code):
        return "system"
    return "user"


def _detect_nav_guard(code: str) -> Optional[str]:
    if _RE_AUTH_GUARD.search(code):
        return "auth"
    if _RE_PERM_GUARD.search(code):
        return "permission"
    return None


# ─── Route config extraction ─────────────────────────────────────────────────

def _collect_route_configs(code: str) -> List[Tuple[str, str]]:
    """Extract (route_name, component_name) pairs from JSX navigator declarations."""
    seen: dict = {}
    for m in _RE_SCREEN_ELEM_START.finditer(code):
        window = code[m.start(): m.start() + 1000]
        name_m = _RE_SCREEN_NAME_ATTR.search(window)
        comp_m = _RE_SCREEN_COMP_ATTR.search(window)
        if name_m and comp_m:
            name = name_m.group("name")
            comp = comp_m.group("comp")
            if name and comp:
                seen[name] = comp
    return list(seen.items())


def _extract_navigator_declarations(code: str, rel_path: str) -> List[NavigatorDef]:
    """Extract React Navigation factory declarations from module-level code."""
    routes_by_file = _collect_route_configs(code)
    results: List[NavigatorDef] = []
    for m in _RE_NAVIGATOR_FACTORY.finditer(code):
        var_name = m.group("var_name")
        factory = m.group("factory")
        generic = (m.group("generic") or "").strip()
        param_list_ref = generic.split(",")[0].strip() if generic else ""
        nav_type = _FACTORY_TO_NAV_TYPE.get(factory, "unknown")
        start_line = code[: m.start()].count("\n") + 1
        symbol_id = f"Navigator::{var_name}::{rel_path}"
        results.append(NavigatorDef(
            symbol_id=symbol_id,
            var_name=var_name,
            factory=factory,
            nav_type=nav_type,
            param_list_ref=param_list_ref,
            file_path=rel_path,
            start_line=start_line,
            routes=list(routes_by_file),
        ))
    return results


def _extract_param_lists(
    root_node: Any,
    source_bytes: bytes,
    rel_path: str,
) -> List[ParamListDef]:
    """Extract TypeScript ParamList type aliases via tree-sitter."""
    results: List[ParamListDef] = []
    for node in _find_nodes_by_type(root_node, "type_alias_declaration"):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        type_name = _node_text(name_node, source_bytes)
        if not type_name.endswith("ParamList"):
            continue
        routes: Dict[str, str] = {}
        value_node = node.child_by_field_name("value")
        if value_node is not None:
            for prop in _find_nodes_by_type(value_node, "property_signature"):
                key_node = prop.child_by_field_name("name")
                type_ann = prop.child_by_field_name("type")
                if key_node is None:
                    continue
                key = _node_text(key_node, source_bytes).strip('"\'')
                type_str = (
                    re.sub(r"\s+", " ", _node_text(type_ann, source_bytes).lstrip(":").strip())
                    if type_ann is not None
                    else "undefined"
                )
                routes[key] = type_str
        start_line = source_bytes[: node.start_byte].count(b"\n") + 1
        symbol_id = f"ParamList::{type_name}::{rel_path}"
        results.append(ParamListDef(
            symbol_id=symbol_id,
            name=type_name,
            file_path=rel_path,
            routes=routes,
        ))
    return results


# ─── JSX rendering helpers ────────────────────────────────────────────────────

def _has_jsx_in_subtree(node: Any) -> bool:
    if node.type in {
        "jsx_element", "jsx_self_closing_element", "jsx_fragment", "jsx_opening_element",
    }:
        return True
    for child in node.children:
        if _has_jsx_in_subtree(child):
            return True
    return False


def _jsx_name(node: Any, source_bytes: bytes) -> Optional[str]:
    """Return the JSX element name for an opening or self-closing JSX node."""
    name_node = node.child_by_field_name("name")
    if name_node is None:
        for child in node.children:
            if child.type in {"jsx_identifier", "jsx_member_expression", "jsx_namespaced_name"}:
                name_node = child
                break
    if name_node is None:
        return None
    return _node_text(name_node, source_bytes)


def _collect_rendered_components(node: Any, source_bytes: bytes) -> List[str]:
    """Return PascalCase component names rendered as JSX within the subtree."""
    names: Dict[str, None] = {}
    for jsx_node in _find_nodes_by_type(node, "jsx_opening_element"):
        n = _jsx_name(jsx_node, source_bytes)
        if n and n[0].isupper():
            names[n] = None
    for jsx_node in _find_nodes_by_type(node, "jsx_self_closing_element"):
        n = _jsx_name(jsx_node, source_bytes)
        if n and n[0].isupper():
            names[n] = None
    return list(names)


# ─── SymbolAgent class facade ─────────────────────────────────────────────────

class SymbolAgent:
    """Object-oriented facade over the module-level symbol analysis functions."""

    def detect_middleware_kind(self, name: str, code: str, file_path: str) -> str:
        return _detect_middleware_kind(name, code, file_path)

    def detect_react_role(
        self,
        name: str,
        file_path: str,
        has_jsx: bool,
        middleware_kind: str,
        code: str = "",
    ) -> str:
        return _detect_react_role(name, file_path, has_jsx, middleware_kind, code)

    def extract_api_calls(
        self,
        code: str,
        function_id: str,
        rel_path: str,
        start_line: int,
        file_base_url: str = "",
    ) -> List[ApiCallDef]:
        return _extract_api_calls(code, function_id, rel_path, start_line, file_base_url)

    def collect_navigate_calls(self, code: str) -> List[Tuple[str, str]]:
        return _collect_navigate_calls(code)

    def collect_route_configs(self, code: str) -> List[Tuple[str, str]]:
        return _collect_route_configs(code)

    def extract_navigator_declarations(
        self, code: str, rel_path: str
    ) -> List[NavigatorDef]:
        return _extract_navigator_declarations(code, rel_path)

    def extract_param_lists(
        self, root_node: Any, source_bytes: bytes, rel_path: str
    ) -> List[ParamListDef]:
        return _extract_param_lists(root_node, source_bytes, rel_path)
