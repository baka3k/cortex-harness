"""AST-level data types produced during TypeScript/TSX parsing.

These dataclasses represent the raw structural output of the tree-sitter
traversal: functions, call edges, render edges, navigation edges, and
navigator declarations.  They are intentionally free of graph-DB concerns.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class FunctionDef:
    symbol_id: str
    qualified_name: str
    name: str
    kind: str
    scope_name: Optional[str]
    file_path: str
    start_line: int
    end_line: int
    arity: int
    code: str
    comment: str = ""
    summary: str = ""
    note: str = ""
    exported: bool = False
    # Semantic fields populated by SemanticInferenceEngine
    return_type: str = ""
    param_types: List[str] = field(default_factory=list)
    intent: str = ""
    inferred_doc: bool = False
    doc_confidence: float = 0.0
    signals: Dict[str, float] = field(default_factory=dict)
    side_effect: bool = False
    # React/React-Native classification
    react_role: str = ""          # ""|"screen"|"component"|"hook"|"middleware"
    middleware_kind: str = ""     # ""|"api_call"|"query_client"|"redux"|"service"


@dataclass
class ApiCallDef:
    """An outgoing HTTP request detected inside a frontend function body."""
    symbol_id: str
    caller_function_id: str       # FunctionDef.symbol_id that contains this call
    url_pattern: str              # normalised, e.g. "/api/users/:id"
    raw_url: str                  # original expression, e.g. "/api/users/${id}"
    http_method: str              # GET | POST | PUT | PATCH | DELETE | ALL
    base_url_ref: str             # axios instance name / baseURL fragment, or ""
    file_path: str
    start_line: int
    confidence: float             # 0.5–1.0


@dataclass
class RenderEdge:
    renderer_id: str
    rendered_name: str   # PascalCase JSX component/screen name used inside renderer
    rendered_id: Optional[str] = None


@dataclass
class NavigateEdge:
    source_id: str          # function/screen that triggers navigation
    target_name: str        # raw target string ("HomeScreen", "/home")
    nav_method: str         # "navigate"|"push"|"replace"|"link"|"__route_config__"
    target_id: Optional[str] = None
    via: str = "direct"             # "direct"|"component"|"hook"|"service"|"wrapped"
    trigger_type: str = "user"      # "user"|"system"|"async"
    guard: Optional[str] = None     # "auth"|"permission"|None
    call_depth: int = 0             # hops from owning screen to this navigate call
    source_trace: List[str] = field(default_factory=list)  # [func_ids] call chain
    confidence: float = 1.0         # target_resolution * attribution * call_path


@dataclass
class RouteConfigEntry:
    route_name: str        # "Home", "Profile" — navigator screen name parameter
    component_name: str    # "HomeScreen" — the component registered for that route
    file_path: str         # file where this navigator declaration lives


@dataclass
class NavigatorDef:
    """A React Navigation factory declaration, e.g. `const RootStack = createStackNavigator<T>()`."""
    symbol_id: str          # "Navigator::<var_name>::<file>"
    var_name: str           # "RootStack", "Tab", "Drawer"
    factory: str            # "createStackNavigator" | "createBottomTabNavigator" | ...
    nav_type: str           # "stack" | "tab" | "drawer" | "native_stack" | "material_top"
    param_list_ref: str     # "RootStackParamList" | "" if no generic
    file_path: str
    start_line: int
    routes: List[Tuple[str, str]]  # [(route_name, component_name), ...]


@dataclass
class ParamListDef:
    """A TypeScript type alias whose name ends with 'ParamList', e.g. type RootStackParamList = {...}."""
    symbol_id: str          # "ParamList::<name>::<file>"
    name: str               # "RootStackParamList"
    file_path: str
    routes: Dict[str, str]  # {"Home": "undefined", "Detail": "{id:string}"}
