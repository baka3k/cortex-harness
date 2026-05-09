from __future__ import annotations

"""TypeScript Backend Semantic Analyzer — Express / NestJS V1.0

Extracts a structured, flow-aware **Backend Execution Graph**::

    API_ENDPOINT → MIDDLEWARE → CONTROLLER → SERVICE → REPOSITORY → DATABASE

Supported frameworks
────────────────────
* **Express.js** — ``app.get(path, ...handlers)``, ``router.post(...)``,
  inline arrow handlers, middleware ``(req, res, next)`` signatures.
* **NestJS**     — ``@Controller``, ``@Get/@Post/…``, ``@Injectable``,
  ``@UseGuards``, ``@UseInterceptors``, ``@Module`` DI registration.

Graph node types
────────────────
API_ENDPOINT, CONTROLLER, SERVICE, REPOSITORY, DATABASE,
MIDDLEWARE, GUARD, INTERCEPTOR

Graph relationship types
────────────────────────
HANDLES  (API_ENDPOINT → CONTROLLER)
USES     (ENDPOINT/CONTROLLER → MIDDLEWARE / GUARD / INTERCEPTOR)
CALLS    (CONTROLLER → SERVICE, SERVICE → SERVICE/REPOSITORY)
QUERIES  (REPOSITORY → DATABASE)
RETURNS  (CONTROLLER → response shape)

Each relationship carries metadata:
    source_file, call_depth, is_async, confidence
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.common.analyzer_cache import (
    file_signature,
    load_parse_cache,
    safe_cache_root,
    write_parse_cache,
)
from tools.common.git_diff import load_manifest_paths
from tools.common.incremental_cleanup import cleanup_neo4j_for_files
from tools.common.semantic_inference import SemanticInferenceEngine
from tools.graph import GraphDriverFactory, GraphProvider
from tools.graph.writer.language_writer import LanguageCodeWriter

try:
    from tree_sitter_languages import get_parser as ts_get_parser
except Exception:
    ts_get_parser = None

# ─── shared parse infra (re-use from ts_analyzer) ────────────────────────────
from tools.ts.ts_analyzer import (  # type: ignore[import]
    _get_ts_parser,
    _parse_file as _parse_ts_file,
    _node_text,
    _find_nodes_by_type,
    _line_from_byte,
    _node_snippet,
    _extract_leading_comment,
    _first_identifier,
    _extract_name_field,
    _normalize_ws,
    _collect_imports,
    _collect_exports,
    _collect_ts_import_graph,
    _expand_impacted_files_by_imports,
    _load_or_parse_payload as _ts_load_or_parse_payload,
    QdrantWriter,
    CodeEmbedder,
    _func_qdrant_payload,
    _stable_point_id,
    FileDef,
    _SCAN_SKIP_DIRS,
    _PARSE_CACHE_VERSION,
)
from tools.js.js_analyzer import (  # type: ignore[import]
    _parse_file as _parse_js_file,
    _load_or_parse_payload as _js_load_or_parse_payload,
    _collect_js_import_graph,
)

# JS extensions that must be parsed with the JavaScript grammar
_JS_EXTENSIONS = (".js", ".jsx", ".mjs", ".cjs")


def _parse_file(path: str):
    """Route to JS or TS grammar based on file extension."""
    if path.endswith(_JS_EXTENSIONS):
        return _parse_js_file(path)
    return _parse_ts_file(path)

# Backend analyzer scans both TypeScript AND plain JavaScript sources so that
# Express / NestJS projects written in JS (or compiled to JS) are not missed.
_BACKEND_SOURCE_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts", ".js", ".mjs", ".cjs")


def _scan_backend_files(root: str) -> List[str]:
    """Walk *root* and collect all TS/JS source files, skipping common noise dirs."""
    files: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SCAN_SKIP_DIRS]
        for name in filenames:
            if name.endswith(_BACKEND_SOURCE_EXTENSIONS):
                files.append(os.path.join(dirpath, name))
    return sorted(files)

_semantic_engine = SemanticInferenceEngine()

# ─────────────────────────────────────────────────────────────────────────────
# Backend-specific dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ApiEndpointDef:
    """A single HTTP route registered in Express or NestJS."""
    symbol_id: str
    path: str                        # normalised, e.g. "/users/:id"
    http_method: str                 # GET | POST | PUT | PATCH | DELETE | ALL | …
    framework: str                   # "express" | "nestjs"
    file_path: str
    start_line: int
    end_line: int
    handler_names: List[str]         # ["authMiddleware", "getUser"]
    middleware_names: List[str]      # inline middlewares before the handler
    controller_class: Optional[str]  # NestJS: parent class name
    code: str
    comment: str = ""
    is_async: bool = False


@dataclass
class ControllerDef:
    """A controller method (NestJS) or route handler function (Express)."""
    symbol_id: str
    qualified_name: str
    name: str
    kind: str                        # "method" | "function"
    file_path: str
    start_line: int
    end_line: int
    arity: int
    code: str
    comment: str = ""
    is_async: bool = False
    framework: str = ""
    # NestJS specific
    nestjs_decorators: List[str] = field(default_factory=list)
    parent_class: Optional[str] = None
    # Resolved edges
    service_calls: List[str] = field(default_factory=list)  # callee names
    guard_names: List[str] = field(default_factory=list)
    interceptor_names: List[str] = field(default_factory=list)


@dataclass
class ServiceDef:
    """A service class or injectable function."""
    symbol_id: str
    qualified_name: str
    name: str
    kind: str                       # "class" | "function"
    file_path: str
    start_line: int
    end_line: int
    code: str
    comment: str = ""
    is_async: bool = False
    framework: str = ""
    # Resolved edges
    repository_calls: List[str] = field(default_factory=list)
    service_calls: List[str] = field(default_factory=list)


@dataclass
class RepositoryDef:
    """A data-access class or function (ORM, raw query, DAO)."""
    symbol_id: str
    qualified_name: str
    name: str
    kind: str
    file_path: str
    start_line: int
    end_line: int
    code: str
    comment: str = ""
    orm_kind: str = ""               # "prisma"|"typeorm"|"sequelize"|"mongoose"|"raw"
    database_targets: List[str] = field(default_factory=list)


@dataclass
class MiddlewareDef:
    """Express middleware | NestJS Guard | NestJS Interceptor."""
    symbol_id: str
    qualified_name: str
    name: str
    kind: str                        # "middleware"|"guard"|"interceptor"
    file_path: str
    start_line: int
    end_line: int
    code: str
    comment: str = ""
    framework: str = ""


@dataclass
class BackendEdge:
    """A directed edge in the backend execution graph."""
    source_id: str
    source_label: str                # API_ENDPOINT | CONTROLLER | SERVICE | REPOSITORY
    target_id: str
    target_label: str
    rel_type: str                    # HANDLES | USES | CALLS | QUERIES | RETURNS
    source_file: str
    call_depth: int = 0
    is_async: bool = False
    confidence: float = 1.0
    properties: Dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Regex patterns — NestJS decorators
# ─────────────────────────────────────────────────────────────────────────────

_RE_NESTJS_CONTROLLER = re.compile(
    r'@Controller\s*\(\s*[\'"`]?(?P<prefix>[A-Za-z0-9_/.-]*)[\'"`]?\s*\)',
    re.MULTILINE,
)
_RE_NESTJS_HTTP_METHOD = re.compile(
    r'@(?P<method>Get|Post|Put|Patch|Delete|Head|Options|All|HttpCode)\s*\('
    r'\s*[\'"`]?(?P<path>[A-Za-z0-9_/:.-]*)[\'"`]?\s*\)',
    re.MULTILINE,
)
_RE_NESTJS_INJECTABLE = re.compile(r'@Injectable\s*\(', re.MULTILINE)
_RE_NESTJS_USE_GUARDS = re.compile(
    r'@UseGuards\s*\(\s*(?P<guards>[^)]+)\)',
    re.MULTILINE,
)
_RE_NESTJS_USE_INTERCEPTORS = re.compile(
    r'@UseInterceptors\s*\(\s*(?P<interceptors>[^)]+)\)',
    re.MULTILINE,
)
_RE_NESTJS_PIPE = re.compile(r'@UsePipes\s*\(\s*(?P<pipes>[^)]+)\)', re.MULTILINE)
_RE_NESTJS_MODULE = re.compile(r'@Module\s*\(', re.MULTILINE)
_RE_NESTJS_GUARD_CLASS = re.compile(r'implements\s+(?:CanActivate|AuthGuard)', re.MULTILINE)
_RE_NESTJS_INTERCEPTOR_CLASS = re.compile(r'implements\s+NestInterceptor', re.MULTILINE)

# ─────────────────────────────────────────────────────────────────────────────
# Regex patterns — Express routes
# ─────────────────────────────────────────────────────────────────────────────

# Matches: app.get("/path", h1, h2)  router.post("/path", h)  Route.put(...)
_RE_EXPRESS_ROUTE = re.compile(
    r'\b(?P<obj>app|router|Router|Route|server|api|v\d|express)\s*'
    r'\.\s*(?P<method>get|post|put|patch|delete|head|options|all|use)\s*\('
    r'\s*[\'"`](?P<path>[A-Za-z0-9_/:*.-]+)[\'"`]\s*'
    r'(?P<rest>[^;]*)',
    re.MULTILINE,
)
# Express middleware signature: (req, res, next) => ...  or  function(req, res, next)
_RE_EXPRESS_MIDDLEWARE_SIG = re.compile(
    r'(?:function\s*\w*\s*\(|(?:\(|\b)\s*)'
    r'(?:req|request)\s*,\s*(?:res|response)\s*,\s*(?:next)\s*[),]',
    re.MULTILINE,
)
# Dynamic routing: router[method](path, handler)
_RE_EXPRESS_DYNAMIC_ROUTE = re.compile(
    r'\b(?:app|router)\s*\[\s*(?P<method_var>\w+)\s*\]\s*\('
    r'\s*[\'"`](?P<path>[A-Za-z0-9_/:*.-]+)[\'"`]',
    re.MULTILINE,
)

# ─────────────────────────────────────────────────────────────────────────────
# Regex patterns — ORM / data access detection
# ─────────────────────────────────────────────────────────────────────────────

_RE_PRISMA = re.compile(
    r'\b(?:prisma|PrismaClient)\s*[.\[]|\bprisma\.\w+\.(findMany|findFirst|findUnique|'
    r'create|update|delete|upsert|aggregate|count|groupBy)\b',
    re.IGNORECASE,
)
_RE_TYPEORM = re.compile(
    r'\b(?:Repository|getRepository|DataSource|EntityManager|createQueryBuilder)\b|'
    r'\.(?:find|findOne|findBy|save|insert|update|delete|remove|query|createQueryBuilder)\s*\(',
    re.IGNORECASE,
)
_RE_SEQUELIZE = re.compile(
    r'\b(?:Model|Sequelize|sequelize)\b.*\.(?:findAll|findOne|create|update|destroy|'
    r'bulkCreate|upsert|findOrCreate)\s*\(',
    re.IGNORECASE,
)
_RE_MONGOOSE = re.compile(
    r'\b(?:mongoose|Schema|Model)\b|'
    r'\.(?:find|findById|findOne|save|create|updateOne|deleteOne|aggregate)\s*\(',
    re.IGNORECASE,
)
_RE_RAW_SQL = re.compile(
    r'\b(?:query|execute|raw|runSql)\s*\(\s*[\'"`]?\s*(?:SELECT|INSERT|UPDATE|DELETE|CREATE)',
    re.IGNORECASE,
)

# Known DB package names → canonical DB label
_DB_PACKAGE_MAP: Dict[str, str] = {
    "prisma": "PostgreSQL/Prisma",
    "@prisma/client": "PostgreSQL/Prisma",
    "typeorm": "TypeORM",
    "sequelize": "Sequelize",
    "mongoose": "MongoDB/Mongoose",
    "mongodb": "MongoDB",
    "pg": "PostgreSQL",
    "mysql2": "MySQL",
    "mysql": "MySQL",
    "sqlite3": "SQLite",
    "better-sqlite3": "SQLite",
    "redis": "Redis",
    "ioredis": "Redis",
    "@neondatabase/serverless": "Neon/PostgreSQL",
    "knex": "Knex",
    "drizzle-orm": "Drizzle",
    "kysely": "Kysely",
    "firebase-admin": "Firebase",
    "supabase-js": "@supabase/supabase-js",
    "@supabase/supabase-js": "Supabase",
    "neo4j-driver": "Neo4j",
}

# ─────────────────────────────────────────────────────────────────────────────
# Helper: extract handler names from the rest of an Express route declaration
# ─────────────────────────────────────────────────────────────────────────────

_RE_HANDLER_IDENT = re.compile(r'\b([A-Za-z_$][A-Za-z0-9_$]*)\b')
# Matches qualified access like  userController.getUsers  as a single token
_RE_HANDLER_QUALIFIED = re.compile(
    r'([A-Za-z_$][A-Za-z0-9_$]*)\s*\.\s*([A-Za-z_$][A-Za-z0-9_$]*)'
)
_HANDLER_SKIP_WORDS: Set[str] = {
    "function", "async", "req", "res", "next", "err",
    "request", "response", "new", "return", "throw",
}


def _extract_express_handlers(rest: str) -> Tuple[List[str], List[str]]:
    """Parse handler list from the *rest* of an Express route call.

    Treats ``obj.method`` as a single token so that ``userCtrl.getUser``
    is not split into ``userCtrl`` (middleware) + ``getUser`` (handler).

    Returns ``(middleware_names, handler_names)`` where the *last* token
    is considered the primary handler.
    """
    rest = rest.split(")")[0].strip().rstrip(",")

    # Build an ordered (position, token) list — qualified names first
    tokens: List[Tuple[int, str]] = []
    covered: Set[int] = set()

    for m in _RE_HANDLER_QUALIFIED.finditer(rest):
        obj, method = m.group(1), m.group(2)
        if obj not in _HANDLER_SKIP_WORDS and method not in _HANDLER_SKIP_WORDS:
            tokens.append((m.start(), f"{obj}.{method}"))
            covered.update(range(m.start(), m.end()))

    for m in _RE_HANDLER_IDENT.finditer(rest):
        if m.start() not in covered and m.group(1) not in _HANDLER_SKIP_WORDS:
            tokens.append((m.start(), m.group(1)))

    if not tokens:
        return [], []
    tokens.sort()
    names = [name for _, name in tokens]
    return names[:-1], [names[-1]]


# ─────────────────────────────────────────────────────────────────────────────
# Helper: detect ORM type from code snippet
# ─────────────────────────────────────────────────────────────────────────────

def _detect_orm_kind(code: str) -> str:
    if _RE_PRISMA.search(code):
        return "prisma"
    if _RE_TYPEORM.search(code):
        return "typeorm"
    if _RE_SEQUELIZE.search(code):
        return "sequelize"
    if _RE_MONGOOSE.search(code):
        return "mongoose"
    if _RE_RAW_SQL.search(code):
        return "raw"
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Helper: detect DB targets from package.json dependencies
# ─────────────────────────────────────────────────────────────────────────────

def _db_targets_from_packages(root: str) -> List[str]:
    pkg_path = os.path.join(root, "package.json")
    if not os.path.isfile(pkg_path):
        return []
    try:
        with open(pkg_path, "r", encoding="utf-8") as fh:
            pkg = json.load(fh)
    except Exception:
        return []
    deps: Dict[str, str] = {}
    deps.update(pkg.get("dependencies") or {})
    deps.update(pkg.get("devDependencies") or {})
    found: List[str] = []
    for pkg_name, db_label in _DB_PACKAGE_MAP.items():
        if pkg_name in deps and db_label not in found:
            found.append(db_label)
    return found


# ─────────────────────────────────────────────────────────────────────────────
# NestJS: extract decorators preceding a class/method node
# ─────────────────────────────────────────────────────────────────────────────

def _collect_decorators_before(node, source_bytes: bytes) -> List[str]:
    """Return decorator texts (e.g. '@Get("/path")') immediately before *node*."""
    result: List[str] = []
    prev = node.prev_sibling
    while prev is not None and prev.type in {"decorator", "comment"}:
        if prev.type == "decorator":
            result.append(_node_text(prev, source_bytes).strip())
        prev = prev.prev_sibling
    return list(reversed(result))


def _parse_decorator_name(dec_text: str) -> str:
    """Extract just the decorator name: '@Get("/x")' → 'Get'."""
    m = re.match(r'@(\w+)', dec_text)
    return m.group(1) if m else ""


def _parse_decorator_arg(dec_text: str) -> str:
    """Extract first string argument from decorator: '@Get("/x")' → '/x'."""
    m = re.search(r'[\'"`]([^\'"`]*)[\'"`]', dec_text)
    return m.group(1) if m else ""


# ─────────────────────────────────────────────────────────────────────────────
# NestJS: constructor injection Analysis
# ─────────────────────────────────────────────────────────────────────────────

_RE_CTOR_INJECT = re.compile(
    r'constructor\s*\([^)]{0,2000}\)',
    re.DOTALL,
)
_RE_INJECT_PARAM = re.compile(
    r'(?:private|protected|public|readonly)\s+(?P<field>\w+)\s*:\s*(?P<type>\w+)',
)


def _extract_constructor_injections(class_code: str) -> Dict[str, str]:
    """Return {fieldName: TypeName} for constructor-injected dependencies."""
    m = _RE_CTOR_INJECT.search(class_code)
    if not m:
        return {}
    return {
        mm.group("field"): mm.group("type")
        for mm in _RE_INJECT_PARAM.finditer(m.group(0))
    }


# ─────────────────────────────────────────────────────────────────────────────
# Symbol id helpers (mirroring ts_analyzer convention)
# ─────────────────────────────────────────────────────────────────────────────

def _symbol_id(label: str, name: str, rel_path: str, extra: str = "") -> str:
    key = f"{label}::{name}@{rel_path}"
    if extra:
        key += f"[{extra}]"
    return key


def _stable_id(raw: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, raw))


# ─────────────────────────────────────────────────────────────────────────────
# Core file parser — returns all backend artefacts from a single .ts/.tsx file
# ─────────────────────────────────────────────────────────────────────────────

def parse_backend_file(
    path: str,
    root: str,
) -> Tuple[
    List[ApiEndpointDef],
    List[ControllerDef],
    List[ServiceDef],
    List[RepositoryDef],
    List[MiddlewareDef],
    List[BackendEdge],
    FileDef,
    Dict[str, Any],
]:
    """Parse a single TypeScript/TSX file and return backend graph artefacts."""
    rel_path = os.path.relpath(path, root)
    tree, source_bytes = _parse_file(path)
    source_text = source_bytes.decode("utf-8", errors="ignore")

    # ── file-level metadata ───────────────────────────────────────────────────
    imports = _collect_imports(tree, source_bytes)
    exports = _collect_exports(tree, source_bytes)
    file_def = FileDef(
        file_path=rel_path,
        start_line=1,
        end_line=source_text.count("\n") + 1,
        code=source_text,
        comment="",
        summary="",
        note="",
        imports=imports,
        exports=exports,
        jsx_tags=[],
        jsx_components=[],
    )

    # ── framework detection for this file ────────────────────────────────────
    _is_nestjs = bool(
        _RE_NESTJS_CONTROLLER.search(source_text)
        or _RE_NESTJS_INJECTABLE.search(source_text)
        or _RE_NESTJS_MODULE.search(source_text)
    )
    _is_express = (not _is_nestjs) and bool(_RE_EXPRESS_ROUTE.search(source_text))

    endpoints: List[ApiEndpointDef] = []
    controllers: List[ControllerDef] = []
    services: List[ServiceDef] = []
    repositories: List[RepositoryDef] = []
    middlewares: List[MiddlewareDef] = []
    edges: List[BackendEdge] = []

    parse_meta: Dict[str, Any] = {
        "parser_language": "typescript_backend",
        "is_nestjs": _is_nestjs,
        "is_express": _is_express,
    }

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 1 — Express route extraction (regex-based, confirmed by AST)
    # ─────────────────────────────────────────────────────────────────────────
    _seen_ctrl_ids: Set[str] = set()  # dedup ControllerDef stubs in this file
    if _is_express or not _is_nestjs:
        for m in _RE_EXPRESS_ROUTE.finditer(source_text):
            http_method = m.group("method").upper()
            path_str = m.group("path")
            rest = m.group("rest") or ""
            middleware_names, handler_names = _extract_express_handlers(rest)
            start_line = source_text[: m.start()].count("\n") + 1
            end_line = source_text[: m.end()].count("\n") + 1
            _is_async_route = bool(re.search(r'\basync\b', rest))
            ep_id = _symbol_id("API_ENDPOINT", f"{http_method}:{path_str}", rel_path)
            ep = ApiEndpointDef(
                symbol_id=ep_id,
                path=path_str,
                http_method=http_method,
                framework="express",
                file_path=rel_path,
                start_line=start_line,
                end_line=end_line,
                handler_names=handler_names,
                middleware_names=middleware_names,
                controller_class=None,
                code=_normalize_ws(m.group(0)),
                is_async=_is_async_route,
            )
            endpoints.append(ep)

            # USES edges for middleware
            for mw_name in middleware_names:
                mw_id = _symbol_id("MIDDLEWARE", mw_name, rel_path)
                edges.append(BackendEdge(
                    source_id=ep_id,
                    source_label="API_ENDPOINT",
                    target_id=mw_id,
                    target_label="MIDDLEWARE",
                    rel_type="USES",
                    source_file=rel_path,
                    confidence=0.8,
                ))

            # HANDLES edges + ControllerDef stubs for route handlers
            for h_name in handler_names:
                ctrl_id = _symbol_id("CONTROLLER", h_name, rel_path)
                if ctrl_id not in _seen_ctrl_ids:
                    _seen_ctrl_ids.add(ctrl_id)
                    controllers.append(ControllerDef(
                        symbol_id=ctrl_id,
                        qualified_name=h_name,
                        name=h_name.split(".")[-1],
                        kind="function",
                        file_path=rel_path,
                        start_line=start_line,
                        end_line=end_line,
                        arity=0,
                        code="",  # filled in by Phase 3 if function found in AST
                        is_async=_is_async_route,
                        framework="express",
                    ))
                edges.append(BackendEdge(
                    source_id=ep_id,
                    source_label="API_ENDPOINT",
                    target_id=ctrl_id,
                    target_label="CONTROLLER",
                    rel_type="HANDLES",
                    source_file=rel_path,
                    confidence=0.9,
                ))

        # Dynamic routing: router[method](path, handler)
        for m in _RE_EXPRESS_DYNAMIC_ROUTE.finditer(source_text):
            path_str = m.group("path")
            method_var = m.group("method_var")
            start_line = source_text[: m.start()].count("\n") + 1
            ep_id = _symbol_id("API_ENDPOINT", f"DYNAMIC:{path_str}", rel_path)
            endpoints.append(ApiEndpointDef(
                symbol_id=ep_id,
                path=path_str,
                http_method=f"${method_var}",
                framework="express",
                file_path=rel_path,
                start_line=start_line,
                end_line=start_line,
                handler_names=[],
                middleware_names=[],
                controller_class=None,
                code=_normalize_ws(m.group(0)),
            ))

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 2 — NestJS AST-based extraction
    # ─────────────────────────────────────────────────────────────────────────
    if _is_nestjs:
        # Walk class_declaration nodes
        for class_node in _find_nodes_by_type(tree.root_node, "class_declaration"):
            class_name_node = class_node.child_by_field_name("name")
            if class_name_node is None:
                continue
            class_name = _node_text(class_name_node, source_bytes)
            class_snippet, cls_start, cls_end = _node_snippet(class_node, source_bytes)
            class_decorators = _collect_decorators_before(class_node, source_bytes)
            dec_names = [_parse_decorator_name(d) for d in class_decorators]

            # ── @Controller ──────────────────────────────────────────────────
            if "Controller" in dec_names:
                ctrl_dec = next(
                    (d for d in class_decorators if _parse_decorator_name(d) == "Controller"),
                    "",
                )
                base_prefix = _parse_decorator_arg(ctrl_dec) or ""

                # Class-level @UseGuards
                class_guard_dec = next(
                    (d for d in class_decorators if _parse_decorator_name(d) == "UseGuards"),
                    None,
                )
                class_guards = _extract_name_list(class_guard_dec or "")

                # Class-level @UseInterceptors
                class_int_dec = next(
                    (d for d in class_decorators if _parse_decorator_name(d) == "UseInterceptors"),
                    None,
                )
                class_interceptors = _extract_name_list(class_int_dec or "")

                # DI injections
                injections = _extract_constructor_injections(class_snippet)

                # Walk method_definition nodes inside the class body
                body_node = class_node.child_by_field_name("body")
                for method_node in _find_nodes_by_type(
                    body_node or class_node, "method_definition"
                ):
                    method_name_node = method_node.child_by_field_name("name")
                    if method_name_node is None:
                        continue
                    method_name = _node_text(method_name_node, source_bytes)
                    if method_name == "constructor":
                        continue

                    method_decs = _collect_decorators_before(method_node, source_bytes)
                    method_dec_names = [_parse_decorator_name(d) for d in method_decs]

                    _HTTP_METHODS = {
                        "Get", "Post", "Put", "Patch", "Delete",
                        "Head", "Options", "All",
                    }
                    http_dec = next(
                        (d for d in method_decs if _parse_decorator_name(d) in _HTTP_METHODS),
                        None,
                    )
                    if http_dec is None:
                        continue  # not an HTTP handler

                    http_method = _parse_decorator_name(http_dec).upper()
                    route_suffix = _parse_decorator_arg(http_dec)
                    full_path = f"/{base_prefix}/{route_suffix}".replace("//", "/")

                    method_snippet, m_start, m_end = _node_snippet(method_node, source_bytes)
                    is_async = bool(re.search(r'\basync\b', method_snippet[:50]))

                    # Method-level guards / interceptors
                    m_guard_dec = next(
                        (d for d in method_decs if _parse_decorator_name(d) == "UseGuards"),
                        None,
                    )
                    m_interceptor_dec = next(
                        (d for d in method_decs if _parse_decorator_name(d) == "UseInterceptors"),
                        None,
                    )
                    guard_names = class_guards + _extract_name_list(m_guard_dec or "")
                    interceptor_names = class_interceptors + _extract_name_list(m_interceptor_dec or "")

                    # Build API_ENDPOINT node
                    ep_id = _symbol_id("API_ENDPOINT", f"{http_method}:{full_path}", rel_path,
                                       extra=class_name)
                    ep = ApiEndpointDef(
                        symbol_id=ep_id,
                        path=full_path,
                        http_method=http_method,
                        framework="nestjs",
                        file_path=rel_path,
                        start_line=m_start,
                        end_line=m_end,
                        handler_names=[f"{class_name}.{method_name}"],
                        middleware_names=[],
                        controller_class=class_name,
                        code=_normalize_ws(method_snippet[:300]),
                        is_async=is_async,
                    )
                    endpoints.append(ep)

                    # Build CONTROLLER node
                    ctrl_id = _symbol_id("CONTROLLER", f"{class_name}.{method_name}", rel_path)
                    comment = _extract_leading_comment(method_node, source_bytes)
                    ctrl = ControllerDef(
                        symbol_id=ctrl_id,
                        qualified_name=f"{class_name}.{method_name}",
                        name=method_name,
                        kind="method",
                        file_path=rel_path,
                        start_line=m_start,
                        end_line=m_end,
                        arity=0,
                        code=method_snippet,
                        comment=comment,
                        is_async=is_async,
                        framework="nestjs",
                        nestjs_decorators=method_dec_names,
                        parent_class=class_name,
                        guard_names=guard_names,
                        interceptor_names=interceptor_names,
                    )
                    # Detect service calls via DI field access: this.userService.getById(...)
                    ctrl.service_calls = _extract_di_service_calls(method_snippet, injections)
                    controllers.append(ctrl)

                    # ── HANDLES edge: endpoint → controller ──
                    edges.append(BackendEdge(
                        source_id=ep_id,
                        source_label="API_ENDPOINT",
                        target_id=ctrl_id,
                        target_label="CONTROLLER",
                        rel_type="HANDLES",
                        source_file=rel_path,
                        is_async=is_async,
                        confidence=1.0,
                    ))

                    # ── USES edges: controller → guards / interceptors ──
                    for g_name in guard_names:
                        g_id = _symbol_id("GUARD", g_name, rel_path)
                        edges.append(BackendEdge(
                            source_id=ctrl_id,
                            source_label="CONTROLLER",
                            target_id=g_id,
                            target_label="GUARD",
                            rel_type="USES",
                            source_file=rel_path,
                            confidence=0.9,
                        ))
                    for i_name in interceptor_names:
                        i_id = _symbol_id("INTERCEPTOR", i_name, rel_path)
                        edges.append(BackendEdge(
                            source_id=ctrl_id,
                            source_label="CONTROLLER",
                            target_id=i_id,
                            target_label="INTERCEPTOR",
                            rel_type="USES",
                            source_file=rel_path,
                            confidence=0.9,
                        ))

                    # ── CALLS edges: controller → services ──
                    for svc_call in ctrl.service_calls:
                        svc_id = _symbol_id("SERVICE", svc_call, rel_path)
                        edges.append(BackendEdge(
                            source_id=ctrl_id,
                            source_label="CONTROLLER",
                            target_id=svc_id,
                            target_label="SERVICE",
                            rel_type="CALLS",
                            source_file=rel_path,
                            is_async=is_async,
                            confidence=0.85,
                        ))

            # ── @Injectable → detect SERVICE or REPOSITORY ────────────────────
            elif "Injectable" in dec_names:
                orm_kind = _detect_orm_kind(class_snippet)
                is_repo = bool(
                    orm_kind
                    or re.search(r'Repository|Dao|Store\b', class_name, re.IGNORECASE)
                    or "InjectRepository" in class_snippet
                )
                if is_repo:
                    repo_id = _symbol_id("REPOSITORY", class_name, rel_path)
                    repos = RepositoryDef(
                        symbol_id=repo_id,
                        qualified_name=class_name,
                        name=class_name,
                        kind="class",
                        file_path=rel_path,
                        start_line=cls_start,
                        end_line=cls_end,
                        code=class_snippet,
                        orm_kind=orm_kind,
                        database_targets=[],
                    )
                    repositories.append(repos)
                    # QUERIES edge (resolved later with DB targets)
                else:
                    injections = _extract_constructor_injections(class_snippet)
                    svc_id = _symbol_id("SERVICE", class_name, rel_path)
                    svc = ServiceDef(
                        symbol_id=svc_id,
                        qualified_name=class_name,
                        name=class_name,
                        kind="class",
                        file_path=rel_path,
                        start_line=cls_start,
                        end_line=cls_end,
                        code=class_snippet,
                        framework="nestjs",
                    )
                    # Collect repository and service calls from method bodies
                    svc.repository_calls = _extract_di_service_calls(
                        class_snippet, injections, repo_types=True
                    )
                    svc.service_calls = _extract_di_service_calls(
                        class_snippet, injections, repo_types=False
                    )
                    services.append(svc)

                    for repo_call in svc.repository_calls:
                        repo_id = _symbol_id("REPOSITORY", repo_call, rel_path)
                        edges.append(BackendEdge(
                            source_id=svc_id,
                            source_label="SERVICE",
                            target_id=repo_id,
                            target_label="REPOSITORY",
                            rel_type="CALLS",
                            source_file=rel_path,
                            confidence=0.8,
                        ))
                    for svc_call in svc.service_calls:
                        dep_svc_id = _symbol_id("SERVICE", svc_call, rel_path)
                        edges.append(BackendEdge(
                            source_id=svc_id,
                            source_label="SERVICE",
                            target_id=dep_svc_id,
                            target_label="SERVICE",
                            rel_type="CALLS",
                            source_file=rel_path,
                            confidence=0.75,
                        ))

            # ── Guard / Interceptor classes ───────────────────────────────────
            elif bool(_RE_NESTJS_GUARD_CLASS.search(class_snippet)):
                gd_id = _symbol_id("GUARD", class_name, rel_path)
                middlewares.append(MiddlewareDef(
                    symbol_id=gd_id,
                    qualified_name=class_name,
                    name=class_name,
                    kind="guard",
                    file_path=rel_path,
                    start_line=cls_start,
                    end_line=cls_end,
                    code=class_snippet,
                    framework="nestjs",
                ))
            elif bool(_RE_NESTJS_INTERCEPTOR_CLASS.search(class_snippet)):
                int_id = _symbol_id("INTERCEPTOR", class_name, rel_path)
                middlewares.append(MiddlewareDef(
                    symbol_id=int_id,
                    qualified_name=class_name,
                    name=class_name,
                    kind="interceptor",
                    file_path=rel_path,
                    start_line=cls_start,
                    end_line=cls_end,
                    code=class_snippet,
                    framework="nestjs",
                ))

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 3 — Express middleware + handler functions (non-NestJS)
    # Walk function_declaration nodes.  Classify by signature:
    #   (req, res, next) → MiddlewareDef
    #   (req, res)       → ControllerDef (may upgrade a stub from Phase 1)
    # Also walk variable declarations for arrow-function handlers.
    # ─────────────────────────────────────────────────────────────────────────
    if _is_express or not _is_nestjs:
        _phase3_seen: Set[str] = set()  # avoid double-registration

        def _register_fn_node(fn_node, fn_name: str) -> None:
            """Classify and register a function node found in the AST."""
            fn_snippet, fn_start, fn_end = _node_snippet(fn_node, source_bytes)
            if _RE_EXPRESS_MIDDLEWARE_SIG.search(fn_snippet):
                # (req, res, next) — middleware
                mw_id = _symbol_id("MIDDLEWARE", fn_name, rel_path)
                if mw_id in _phase3_seen:
                    return
                _phase3_seen.add(mw_id)
                middlewares.append(MiddlewareDef(
                    symbol_id=mw_id,
                    qualified_name=fn_name,
                    name=fn_name,
                    kind="middleware",
                    file_path=rel_path,
                    start_line=fn_start,
                    end_line=fn_end,
                    code=fn_snippet,
                    framework="express",
                ))
            elif re.search(
                r'(?:req|request)\s*,\s*(?:res|response)',
                fn_snippet[:120],
            ):
                # (req, res) — route handler → ControllerDef
                ctrl_id = _symbol_id("CONTROLLER", fn_name, rel_path)
                if ctrl_id in _phase3_seen:
                    return
                _phase3_seen.add(ctrl_id)
                # Upgrade stub created in Phase 1, or create fresh
                for ctrl in controllers:
                    if ctrl.symbol_id == ctrl_id:
                        ctrl.code = fn_snippet
                        ctrl.start_line = fn_start
                        ctrl.end_line = fn_end
                        ctrl.is_async = fn_snippet[:60].count("async") > 0
                        return
                if ctrl_id not in _seen_ctrl_ids:
                    _seen_ctrl_ids.add(ctrl_id)
                    controllers.append(ControllerDef(
                        symbol_id=ctrl_id,
                        qualified_name=fn_name,
                        name=fn_name,
                        kind="function",
                        file_path=rel_path,
                        start_line=fn_start,
                        end_line=fn_end,
                        arity=0,
                        code=fn_snippet,
                        comment=_extract_leading_comment(fn_node, source_bytes),
                        is_async=fn_snippet[:60].count("async") > 0,
                        framework="express",
                    ))

        # Named function declarations
        for fn_node in _find_nodes_by_type(tree.root_node, "function_declaration"):
            fn_name = _extract_name_field(fn_node, source_bytes) or ""
            if fn_name:
                _register_fn_node(fn_node, fn_name)

        # Arrow functions / function expressions in variable declarations
        # e.g.  const getUser = async (req, res) => { ... }
        for decl_node in _find_nodes_by_type(tree.root_node, "variable_declaration"):
            for decl in _find_nodes_by_type(decl_node, "variable_declarator"):
                name_node = decl.child_by_field_name("name")
                value_node = decl.child_by_field_name("value")
                if name_node is None or value_node is None:
                    continue
                if value_node.type not in {
                    "arrow_function", "function", "async_function_expression",
                    "function_expression",
                }:
                    continue
                fn_name = _node_text(name_node, source_bytes)
                if fn_name:
                    _register_fn_node(value_node, fn_name)

        # module.exports = { getUser, createUser } — export object shorthand
        # Detect and generate CALLS edges for Express handlers found in file
        # by scanning call_expressions in each registered controller function.
        _ctrl_by_name: Dict[str, ControllerDef] = {
            c.name: c for c in controllers if c.framework == "express"
        }
        for ctrl in list(_ctrl_by_name.values()):
            if not ctrl.code:
                continue
            # Walk call_expressions inside handler code text (lightweight regex)
            for call_m in re.finditer(
                r'\b([A-Za-z_$][A-Za-z0-9_$]*)(?:\s*\.\s*([A-Za-z_$][A-Za-z0-9_$]*))?\s*\(',
                ctrl.code,
            ):
                callee_obj = call_m.group(1)
                callee_method = call_m.group(2)
                callee_name = f"{callee_obj}.{callee_method}" if callee_method else callee_obj
                if callee_obj in _HANDLER_SKIP_WORDS:
                    continue
                # Avoid self-referential edges
                if callee_name == ctrl.name or callee_obj == ctrl.name:
                    continue
                # Emit a CALLS edge; target will be resolved cross-file later
                target_id = _symbol_id("SERVICE", callee_name, rel_path)
                edges.append(BackendEdge(
                    source_id=ctrl.symbol_id,
                    source_label="CONTROLLER",
                    target_id=target_id,
                    target_label="SERVICE",
                    rel_type="CALLS",
                    source_file=rel_path,
                    confidence=0.6,
                ))

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 4 — Repository / data-access detection (file-level, both frameworks)
    # ─────────────────────────────────────────────────────────────────────────
    orm_kind = _detect_orm_kind(source_text)
    if orm_kind and not any(r.file_path == rel_path for r in repositories):
        # No @Injectable repo found, but file clearly accesses DB
        repo_id = _symbol_id("REPOSITORY", os.path.basename(rel_path), rel_path)
        repositories.append(RepositoryDef(
            symbol_id=repo_id,
            qualified_name=os.path.splitext(os.path.basename(rel_path))[0],
            name=os.path.splitext(os.path.basename(rel_path))[0],
            kind="module",
            file_path=rel_path,
            start_line=1,
            end_line=file_def.end_line,
            code=source_text[:500],
            orm_kind=orm_kind,
        ))

    return (
        endpoints,
        controllers,
        services,
        repositories,
        middlewares,
        edges,
        file_def,
        parse_meta,
    )


# ─────────────────────────────────────────────────────────────────────────────
# DI call extraction helpers
# ─────────────────────────────────────────────────────────────────────────────

_RE_THIS_DOT = re.compile(r'\bthis\.(?P<field>\w+)\.\w+\s*\(')


def _extract_di_service_calls(
    code: str,
    injections: Dict[str, str],
    *,
    repo_types: bool = False,
) -> List[str]:
    """Return type names of injected services (or repositories) that are called.

    When *repo_types* is True, only return types whose names contain 'Repository',
    'Dao', or 'Store'. Otherwise return non-repo service types.
    """
    called_fields: Set[str] = set()
    for m in _RE_THIS_DOT.finditer(code):
        called_fields.add(m.group("field"))

    result: List[str] = []
    for field, type_name in injections.items():
        if field not in called_fields:
            continue
        is_repo = bool(re.search(r'Repository|Dao|Store\b', type_name, re.IGNORECASE))
        if repo_types and is_repo:
            result.append(type_name)
        elif not repo_types and not is_repo:
            result.append(type_name)
    return result


def _extract_name_list(text: str) -> List[str]:
    """Extract bare identifiers from a decorator argument string."""
    return [
        m.group(1) for m in re.finditer(r'\b([A-Z][A-Za-z0-9_]+)\b', text)
        if m.group(1) not in {"new", "true", "false"}
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Project type detector
# ─────────────────────────────────────────────────────────────────────────────

# package.json deps that strongly indicate a backend project
_BACKEND_PACKAGES: Set[str] = {
    "express", "fastify", "koa", "hapi", "@hapi/hapi",
    "@nestjs/core", "@nestjs/common", "@nestjs/platform-express",
    "restify", "polka", "connect",
    "prisma", "@prisma/client", "typeorm", "sequelize", "mongoose",
    "drizzle-orm", "knex", "kysely",
    "graphql", "apollo-server", "@apollo/server",
    "trpc", "@trpc/server",
    "pg", "mysql2", "mongodb", "redis", "ioredis",
}

# package.json deps that strongly indicate a frontend project
_FRONTEND_PACKAGES: Set[str] = {
    "react", "react-dom",
    "vue", "@vue/core",
    "svelte", "@sveltejs/kit",
    "solid-js",
    "angular", "@angular/core",
    "next", "nuxt", "@remix-run/react",
    "gatsby",
    "vite", "@vitejs/plugin-react",
    "webpack",
    "react-native", "expo",
    "@react-navigation/native",
}

# Directory name segments that indicate backend source
_BACKEND_DIR_SEGMENTS: Set[str] = {
    "controllers", "controller",
    "services", "service",
    "repositories", "repository",
    "middleware", "middlewares",
    "guards", "guard",
    "interceptors", "interceptor",
    "routes", "routers",
    "dto", "dtos",
    "entities", "entity",
    "migrations", "migration",
    "modules", "module",
}

# Directory name segments that indicate frontend source
_FRONTEND_DIR_SEGMENTS: Set[str] = {
    "components", "component",
    "pages", "page",
    "screens", "screen",
    "views", "view",
    "hooks",
    "assets", "styles", "css",
    "layouts", "layout",
    "store",
}


def detect_project_type(root: str) -> str:
    """Return ``"frontend"``, ``"backend"``, or ``"fullstack"`` / ``"unknown"``.

    Detection pipeline (highest confidence first):
    1. ``package.json`` dependency scoring.
    2. Source directory structure scoring.
    3. Entry-point file content signals.
    """
    backend_score = 0
    frontend_score = 0

    # ── 1. package.json ────────────────────────────────────────────────────
    pkg_path = os.path.join(root, "package.json")
    if os.path.isfile(pkg_path):
        try:
            with open(pkg_path, "r", encoding="utf-8") as fh:
                pkg = json.load(fh)
            all_deps: Set[str] = set()
            all_deps.update((pkg.get("dependencies") or {}).keys())
            all_deps.update((pkg.get("devDependencies") or {}).keys())
            backend_score += len(all_deps & _BACKEND_PACKAGES) * 3
            frontend_score += len(all_deps & _FRONTEND_PACKAGES) * 3
            # "main"/"server" as start script → strong backend signal
            scripts: Dict[str, str] = pkg.get("scripts") or {}
            for script_val in scripts.values():
                if re.search(r'\b(nest|express)\b', script_val, re.IGNORECASE):
                    backend_score += 2
                if re.search(r'\b(vite|react-scripts|next dev)\b', script_val, re.IGNORECASE):
                    frontend_score += 2
        except Exception:
            pass

    # ── 2. Directory structure ─────────────────────────────────────────────
    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SCAN_SKIP_DIRS]
        for d in dirnames:
            dl = d.lower()
            if dl in _BACKEND_DIR_SEGMENTS:
                backend_score += 1
            if dl in _FRONTEND_DIR_SEGMENTS:
                frontend_score += 1

    # ── 3. Entry-point file signals ────────────────────────────────────────
    _entry_candidates = ["main.ts", "index.ts", "server.ts", "app.ts",
                         "src/main.ts", "src/index.ts", "src/server.ts"]
    for candidate in _entry_candidates:
        ep = os.path.join(root, candidate)
        if os.path.isfile(ep):
            try:
                with open(ep, "r", encoding="utf-8") as fh:
                    content = fh.read(4096)
                if re.search(r'NestFactory|createNestApplication|express\(\)', content):
                    backend_score += 5
                if re.search(r'ReactDOM\.render|createRoot|render\(<App', content):
                    frontend_score += 5
            except Exception:
                pass

    # ── Decision ──────────────────────────────────────────────────────────
    if backend_score == 0 and frontend_score == 0:
        return "unknown"
    if backend_score > 0 and frontend_score > 0:
        # Both — label as fullstack only if backend clearly outweighs
        if backend_score >= frontend_score * 1.5:
            return "backend"
        if frontend_score >= backend_score * 1.5:
            return "frontend"
        return "fullstack"
    return "backend" if backend_score > frontend_score else "frontend"


# ─────────────────────────────────────────────────────────────────────────────
# Cache-aware file loader
# ─────────────────────────────────────────────────────────────────────────────

_BE_PARSE_CACHE_VERSION = "ts-be-v2026-04-03-1"


def _load_or_parse_backend_payload(
    file_path: str,
    root: str,
    parse_cache_root: str,
    parse_cache: bool,
) -> Dict[str, Any]:
    rel_path = os.path.relpath(file_path, root)

    cached_payload = None
    signature = None
    if parse_cache:
        signature = file_signature(file_path)
        cached_payload = load_parse_cache(parse_cache_root, rel_path, signature)
        if cached_payload and cached_payload.get("_cache_version") == _BE_PARSE_CACHE_VERSION:
            return cached_payload.get("payload", cached_payload)
        cached_payload = None

    (
        be_endpoints,
        be_controllers,
        be_services,
        be_repositories,
        be_middlewares,
        be_edges,
        file_def,
        parse_meta,
    ) = parse_backend_file(file_path, root)

    payload = {
        "_cache_version": _BE_PARSE_CACHE_VERSION,
        "endpoints": [asdict(item) for item in be_endpoints],
        "controllers": [asdict(item) for item in be_controllers],
        "services": [asdict(item) for item in be_services],
        "repositories": [asdict(item) for item in be_repositories],
        "middlewares": [asdict(item) for item in be_middlewares],
        "edges": [asdict(item) for item in be_edges],
        "file_def": asdict(file_def),
        "parse_meta": parse_meta,
    }

    if parse_cache and signature is not None:
        write_parse_cache(parse_cache_root, rel_path, signature,
                          {"_cache_version": _BE_PARSE_CACHE_VERSION, "payload": payload})
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# Cross-file edge resolution
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_cross_file_edges(
    all_payloads: List[Dict[str, Any]],
    db_targets: List[str],
    base_payloads: Optional[List[Dict[str, Any]]] = None,
) -> List[BackendEdge]:
    """Resolve symbolic references and auto-generate edges from the call graph.

    Passes:
      1. HANDLES  — endpoint handler_name → controller symbol_id
      2. CALLS    — controller/service service_calls → service symbol_id
      3. CALLS    — service repository_calls → repository symbol_id
      4. QUERIES  — repository orm_kind → database node
      5. CALLS    — automatic cross-file CALLS using the base function call graph
                    (works for both Express and NestJS without pattern restrictions)

    Returns a list of *additional* cross-file edges to supplement per-file edges.
    """
    extra_edges: List[BackendEdge] = []

    # ── Build lookup indexes ──────────────────────────────────────────────────
    # service name → symbol_id
    svc_index: Dict[str, str] = {}
    repo_index: Dict[str, str] = {}
    ctrl_index: Dict[str, str] = {}
    mw_index: Dict[str, str] = {}
    ep_handler_index: Dict[str, str] = {}  # handler_name → endpoint_id

    for payload in all_payloads:
        for svc in payload.get("services") or []:
            svc_index[svc["name"]] = svc["symbol_id"]
        for repo in payload.get("repositories") or []:
            repo_index[repo["name"]] = repo["symbol_id"]
        for ctrl in payload.get("controllers") or []:
            ctrl_index[ctrl["qualified_name"]] = ctrl["symbol_id"]
            ctrl_index[ctrl["name"]] = ctrl["symbol_id"]
        for mw in payload.get("middlewares") or []:
            mw_index[mw["name"]] = mw["symbol_id"]
        for ep in payload.get("endpoints") or []:
            for h_name in ep.get("handler_names") or []:
                ep_handler_index[h_name] = ep["symbol_id"]

    # ── Resolve HANDLES edges (express: handler_name → controller) ────────────
    for payload in all_payloads:
        rel_path = (payload.get("file_def") or {}).get("file_path", "")
        for ep in payload.get("endpoints") or []:
            for h_name in ep.get("handler_names") or []:
                if h_name in ctrl_index:
                    extra_edges.append(BackendEdge(
                        source_id=ep["symbol_id"],
                        source_label="API_ENDPOINT",
                        target_id=ctrl_index[h_name],
                        target_label="CONTROLLER",
                        rel_type="HANDLES",
                        source_file=rel_path,
                        confidence=0.9,
                    ))

    # ── Resolve CALLS edges (controller/service → service/repository) ─────────
    for payload in all_payloads:
        rel_path = (payload.get("file_def") or {}).get("file_path", "")
        for ctrl in payload.get("controllers") or []:
            for svc_call in ctrl.get("service_calls") or []:
                if svc_call in svc_index:
                    extra_edges.append(BackendEdge(
                        source_id=ctrl["symbol_id"],
                        source_label="CONTROLLER",
                        target_id=svc_index[svc_call],
                        target_label="SERVICE",
                        rel_type="CALLS",
                        source_file=rel_path,
                        is_async=ctrl.get("is_async", False),
                        confidence=0.9,
                    ))
        for svc in payload.get("services") or []:
            for repo_call in svc.get("repository_calls") or []:
                if repo_call in repo_index:
                    extra_edges.append(BackendEdge(
                        source_id=svc["symbol_id"],
                        source_label="SERVICE",
                        target_id=repo_index[repo_call],
                        target_label="REPOSITORY",
                        rel_type="CALLS",
                        source_file=rel_path,
                        confidence=0.9,
                    ))
            for svc_call in svc.get("service_calls") or []:
                if svc_call in svc_index and svc_index[svc_call] != svc["symbol_id"]:
                    extra_edges.append(BackendEdge(
                        source_id=svc["symbol_id"],
                        source_label="SERVICE",
                        target_id=svc_index[svc_call],
                        target_label="SERVICE",
                        rel_type="CALLS",
                        source_file=rel_path,
                        confidence=0.85,
                    ))

    # ── Resolve QUERIES edges (repository → database) ────────────────────────
    for payload in all_payloads:
        rel_path = (payload.get("file_def") or {}).get("file_path", "")
        for repo in payload.get("repositories") or []:
            if not repo.get("orm_kind"):
                continue
            targets = repo.get("database_targets") or db_targets or ["Database"]
            for db_target in targets:
                db_id = f"DATABASE::{db_target}"
                extra_edges.append(BackendEdge(
                    source_id=repo["symbol_id"],
                    source_label="REPOSITORY",
                    target_id=db_id,
                    target_label="DATABASE",
                    rel_type="QUERIES",
                    source_file=rel_path,
                    confidence=0.85,
                    properties={"orm_kind": repo.get("orm_kind", "")},
                ))

    # ── Pass 5: Auto-CALLS from base function call graph ─────────────────────
    # Works for any framework: if a function in file A calls a function in file B
    # and those files host different backend semantic nodes, emit a CALLS edge.
    if base_payloads:
        # Map file_path → [(label, symbol_id), ...] for all backend semantic nodes
        file_to_semantic: Dict[str, List[Tuple[str, str]]] = {}
        for payload in all_payloads:
            fp = (payload.get("file_def") or {}).get("file_path", "")
            if not fp:
                continue
            for ep in payload.get("endpoints") or []:
                file_to_semantic.setdefault(fp, []).append(("API_ENDPOINT", ep["symbol_id"]))
            for ctrl in payload.get("controllers") or []:
                file_to_semantic.setdefault(fp, []).append(("CONTROLLER", ctrl["symbol_id"]))
            for svc in payload.get("services") or []:
                file_to_semantic.setdefault(fp, []).append(("SERVICE", svc["symbol_id"]))
            for repo in payload.get("repositories") or []:
                file_to_semantic.setdefault(fp, []).append(("REPOSITORY", repo["symbol_id"]))
            for mw in payload.get("middlewares") or []:
                file_to_semantic.setdefault(fp, []).append(("MIDDLEWARE", mw["symbol_id"]))

        # Map function symbol_id → file_path (from base parser output)
        func_id_to_file: Dict[str, str] = {}
        for payload in base_payloads:
            fp = (payload.get("file_def") or {}).get("file_path", "")
            for func in payload.get("functions") or []:
                func_id_to_file[func["symbol_id"]] = fp

        # Dedup against edges already in extra_edges
        existing_keys: Set[Tuple[str, str, str]] = {
            (e.source_id, e.target_id, e.rel_type) for e in extra_edges
        }

        for payload in base_payloads:
            caller_fp = (payload.get("file_def") or {}).get("file_path", "")
            caller_semantics = file_to_semantic.get(caller_fp)
            if not caller_semantics:
                continue
            for call in payload.get("calls") or []:
                callee_id = call.get("callee_id")
                if not callee_id:
                    continue
                callee_fp = func_id_to_file.get(callee_id)
                if not callee_fp or callee_fp == caller_fp:
                    continue
                callee_semantics = file_to_semantic.get(callee_fp)
                if not callee_semantics:
                    continue
                for src_label, src_id in caller_semantics:
                    for tgt_label, tgt_id in callee_semantics:
                        key = (src_id, tgt_id, "CALLS")
                        if key in existing_keys:
                            continue
                        existing_keys.add(key)
                        extra_edges.append(BackendEdge(
                            source_id=src_id,
                            source_label=src_label,
                            target_id=tgt_id,
                            target_label=tgt_label,
                            rel_type="CALLS",
                            source_file=caller_fp,
                            confidence=0.75,
                        ))

    return extra_edges


# ─────────────────────────────────────────────────────────────────────────────
# Base graph data collector (mirrors js_analyzer write_all schema)
# ─────────────────────────────────────────────────────────────────────────────

def _collect_base_write_data(
    base_payloads: List[Dict[str, Any]],
    project_id: str,
    project_name: str,
    language: str,
    repo: str,
    build_system: str,
    root: str = "",
) -> Tuple[List, List, List, List, List, List]:
    """Build write batches for File / Function / Type nodes.

    Returns (projects, files, types, functions, relations, calls) in the
    schema expected by ``LanguageCodeWriter.write_all``.
    """
    all_projects = [{
        "id": project_id,
        "name": project_name,
        "language": language,
        "repo": repo,
        "root": root,
        "build_system": build_system,
    }]
    all_files: List[Dict[str, Any]] = []
    all_types: List[Dict[str, Any]] = []
    all_functions: List[Dict[str, Any]] = []
    all_relations: List[Dict[str, Any]] = []
    all_calls: List[Dict[str, Any]] = []

    for payload in base_payloads:
        file_def = payload.get("file_def") or {}
        file_id = file_def.get("file_path", "")
        if not file_id:
            continue
        all_files.append({
            "id": file_id,
            "path": file_id,
            "start_line": file_def.get("start_line", 1),
            "end_line": file_def.get("end_line", 1),
            "code": file_def.get("code", ""),
            "comment": file_def.get("comment", ""),
            "summary": file_def.get("summary", ""),
            "note": file_def.get("note", ""),
            "imports": file_def.get("imports") or [],
            "exports": file_def.get("exports") or [],
            "jsx_tags": file_def.get("jsx_tags") or [],
            "jsx_components": file_def.get("jsx_components") or [],
            "project_id": project_id,
            "project_name": project_name,
            "language": language,
            "repo": repo,
            "build_system": build_system,
        })
        all_relations.append({
            "source_id": project_id,
            "target_id": file_id,
            "rel_type": "CONTAINS",
            "properties": {},
        })
        for type_def in payload.get("types") or []:
            all_types.append({
                "id": type_def["symbol_id"],
                "name": type_def["name"],
                "qualified_name": type_def["qualified_name"],
                "kind": type_def["kind"],
                "file_path": type_def["file_path"],
                "start_line": type_def["start_line"],
                "end_line": type_def["end_line"],
                "code": type_def.get("code", ""),
                "comment": type_def.get("comment", ""),
                "summary": type_def.get("summary", ""),
                "note": type_def.get("note", ""),
                "exported": type_def.get("exported", False),
                "project_id": project_id,
                "project_name": project_name,
                "language": language,
                "repo": repo,
                "build_system": build_system,
            })
            all_relations.append({
                "source_id": file_id,
                "target_id": type_def["symbol_id"],
                "rel_type": "CONTAINS",
                "properties": {},
            })
        for func in payload.get("functions") or []:
            all_functions.append({
                "id": func["symbol_id"],
                "name": func["name"],
                "qualified_name": func["qualified_name"],
                "kind": func["kind"],
                "scope_name": func.get("scope_name"),
                "class_name": None,
                "package_name": None,
                "file_path": func["file_path"],
                "start_line": func["start_line"],
                "end_line": func["end_line"],
                "arity": func["arity"],
                "code": func["code"],
                "comment": func.get("comment", ""),
                "summary": func.get("summary", ""),
                "note": func.get("note", ""),
                "exported": func.get("exported", False),
                "project_id": project_id,
                "project_name": project_name,
                "language": language,
                "repo": repo,
                "build_system": build_system,
            })
            all_relations.append({
                "source_id": file_id,
                "target_id": func["symbol_id"],
                "rel_type": "CONTAINS",
                "properties": {},
            })
        for rel in payload.get("relations") or []:
            all_relations.append({
                "source_id": rel["source_id"],
                "target_id": rel["target_id"],
                "rel_type": rel["rel_type"],
                "properties": rel.get("properties") or {},
            })
        for call in payload.get("calls") or []:
            if call.get("callee_id"):
                all_calls.append({
                    "caller_id": call["caller_id"],
                    "callee_id": call["callee_id"],
                })

    return all_projects, all_files, all_types, all_functions, all_relations, all_calls


# ─────────────────────────────────────────────────────────────────────────────
# Main async build function
# ─────────────────────────────────────────────────────────────────────────────

async def build_backend_graph(
    root: str,
    code_writer: Optional[LanguageCodeWriter],
    batch_size: int,
    cache_dir: Optional[str],
    parse_cache: bool,
    neo4j_batch_size: int,
    project_id: str,
    project_name: str,
    language: str,
    repo: str,
    build_system: str,
    verbose: bool,
    incremental: bool = False,
    changed_files: Optional[Iterable[str]] = None,
    deleted_files: Optional[Iterable[str]] = None,
) -> None:
    start_time = time.time()
    cache_root = safe_cache_root(cache_dir, "ts_backend_analyzer", project_root=root)
    parse_cache_root = os.path.join(cache_root, "parse")
    os.makedirs(parse_cache_root, exist_ok=True)

    all_scanned_files = _scan_backend_files(root)
    db_targets = _db_targets_from_packages(root)

    project_type = detect_project_type(root)
    if verbose:
        print(f"[backend-analyzer] project_type={project_type}")

    # Separate JS and TS files for grammar-correct import graph
    _js_ext_set = set(_JS_EXTENSIONS)
    js_files = [f for f in all_scanned_files if os.path.splitext(f)[1].lower() in _js_ext_set]
    ts_files = [f for f in all_scanned_files if os.path.splitext(f)[1].lower() not in _js_ext_set]

    changed_set = {f.replace("\\", "/") for f in (changed_files or []) if f}
    deleted_set = {f.replace("\\", "/") for f in (deleted_files or []) if f}

    if incremental and changed_set:
        # Build a combined import graph: TS import grammar for .ts files,
        # JS import grammar for .js files
        deps_by_file = _collect_ts_import_graph(ts_files, root)
        deps_by_file.update(_collect_js_import_graph(js_files, root))
        impacted = _expand_impacted_files_by_imports(
            {os.path.relpath(f, root).replace("\\", "/") for f in all_scanned_files
             if os.path.relpath(f, root).replace("\\", "/") in changed_set},
            deps_by_file,
        )
        selected_files = [
            f for f in all_scanned_files
            if os.path.relpath(f, root).replace("\\", "/") in impacted
        ]
    else:
        selected_files = all_scanned_files

    if verbose:
        print(f"[backend-analyzer] Scanning {len(selected_files)} files under {root}")

    # Incremental cleanup
    if incremental and (changed_set | deleted_set) and code_writer:
        cleanup_targets = sorted(
            (changed_set | deleted_set) - {
                os.path.relpath(f, root).replace("\\", "/") for f in all_scanned_files
            }
        )
        if cleanup_targets:
            await cleanup_neo4j_for_files(
                code_writer.driver,
                cleanup_targets,
                database=code_writer.database,
            )

    # ── Dual-pipeline parse ───────────────────────────────────────────────────
    # Each file is parsed twice:
    #   1. Base parser  (js_analyzer / ts_analyzer) → Function, Type, File nodes
    #   2. Backend parser (this module)             → ApiEndpoint, Controller, …
    all_base_payloads: List[Dict[str, Any]] = []
    all_backend_payloads: List[Dict[str, Any]] = []
    parse_error_count = 0

    for file_path in selected_files:
        ext = os.path.splitext(file_path)[1].lower()

        # 1. Base parse — full function/type graph exactly as js_analyzer does
        try:
            if ext in _js_ext_set:
                base_payload = _js_load_or_parse_payload(
                    file_path, root, parse_cache_root, parse_cache
                )
            else:
                base_payload = _ts_load_or_parse_payload(
                    file_path, root, parse_cache_root, parse_cache
                )
            all_base_payloads.append(base_payload)
        except Exception as exc:
            parse_error_count += 1
            if verbose:
                print(f"[backend-analyzer] base-parse ERROR {file_path}: {exc}")

        # 2. Backend parse — endpoint / controller / service patterns
        try:
            be_payload = _load_or_parse_backend_payload(
                file_path, root, parse_cache_root, parse_cache
            )
            all_backend_payloads.append(be_payload)
        except Exception as exc:
            if verbose:
                print(f"[backend-analyzer] backend-parse ERROR {file_path}: {exc}")

    # ── Cross-file edge resolution ────────────────────────────────────────────
    extra_edges = _resolve_cross_file_edges(all_backend_payloads, db_targets, all_base_payloads)

    # ── Stats ─────────────────────────────────────────────────────────────────
    total_files = len(all_base_payloads)
    total_fns = sum(len(p.get("functions") or []) for p in all_base_payloads)
    total_endpoints = sum(len(p.get("endpoints") or []) for p in all_backend_payloads)
    total_controllers = sum(len(p.get("controllers") or []) for p in all_backend_payloads)
    total_services = sum(len(p.get("services") or []) for p in all_backend_payloads)
    total_repos = sum(len(p.get("repositories") or []) for p in all_backend_payloads)
    total_middlewares = sum(len(p.get("middlewares") or []) for p in all_backend_payloads)
    total_edges = (
        sum(len(p.get("edges") or []) for p in all_backend_payloads) + len(extra_edges)
    )

    if verbose:
        elapsed = time.time() - start_time
        print(
            f"[backend-analyzer] Parse complete in {elapsed:.1f}s | "
            f"files={total_files} functions={total_fns} "
            f"endpoints={total_endpoints} controllers={total_controllers} "
            f"services={total_services} repos={total_repos} "
            f"middlewares={total_middlewares} edges={total_edges}"
            + (f" parse_errors={parse_error_count}" if parse_error_count else "")
        )

    # ── Write to graph DB ─────────────────────────────────────────────────────
    if code_writer:
        # 1. Write base function graph (File/Function/Type) via write_all
        #    This gives the same graph density as running js_analyzer.
        _projects, _files, _types, _functions, _relations, _calls = _collect_base_write_data(
            all_base_payloads, project_id, project_name, language, repo, build_system, root=root
        )
        await code_writer.write_all(
            projects=_projects,
            files=_files or None,
            types=_types or None,
            functions=_functions or None,
            relations=_relations or None,
            calls=_calls or None,
            use_full_writers=True,
            files_variant="with_jsx",
        )

        # 2. Write backend-specific nodes (ApiEndpoint, Controller, Service, …)
        await _write_backend_graph(
            code_writer,
            all_backend_payloads,
            extra_edges,
            project_id=project_id,
            project_name=project_name,
            language=language,
            repo=repo,
            build_system=build_system,
            batch_size=neo4j_batch_size,
            verbose=verbose,
        )

    print(
        f"[SCAN_RESULT] parser=typescript_backend files={total_files} "
        f"functions={total_fns} endpoints={total_endpoints} services={total_services}",
        flush=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Graph DB write helpers — UNWIND batch Cypher
# ─────────────────────────────────────────────────────────────────────────────

_UPSERT_API_ENDPOINT_UNWIND = """
UNWIND $rows AS row
MERGE (n:ApiEndpoint {symbol_id: row.symbol_id})
SET n.path             = row.path,
    n.http_method      = row.http_method,
    n.framework        = row.framework,
    n.file_path        = row.file_path,
    n.start_line       = row.start_line,
    n.end_line         = row.end_line,
    n.handler_names    = row.handler_names,
    n.middleware_names = row.middleware_names,
    n.controller_class = row.controller_class,
    n.code             = row.code,
    n.comment          = row.comment,
    n.is_async         = row.is_async,
    n.project_id       = row.project_id,
    n.project_name     = row.project_name,
    n.language         = row.language,
    n.repo             = row.repo
RETURN count(n) AS count
"""

_UPSERT_CONTROLLER_UNWIND = """
UNWIND $rows AS row
MERGE (n:Controller {symbol_id: row.symbol_id})
SET n.qualified_name = row.qualified_name,
    n.name           = row.name,
    n.kind           = row.kind,
    n.file_path      = row.file_path,
    n.start_line     = row.start_line,
    n.end_line       = row.end_line,
    n.code           = row.code,
    n.comment        = row.comment,
    n.is_async       = row.is_async,
    n.framework      = row.framework,
    n.parent_class   = row.parent_class,
    n.project_id     = row.project_id,
    n.project_name   = row.project_name,
    n.language       = row.language,
    n.repo           = row.repo
RETURN count(n) AS count
"""

_UPSERT_SERVICE_UNWIND = """
UNWIND $rows AS row
MERGE (n:Service {symbol_id: row.symbol_id})
SET n.qualified_name = row.qualified_name,
    n.name           = row.name,
    n.kind           = row.kind,
    n.file_path      = row.file_path,
    n.start_line     = row.start_line,
    n.end_line       = row.end_line,
    n.code           = row.code,
    n.comment        = row.comment,
    n.is_async       = row.is_async,
    n.framework      = row.framework,
    n.project_id     = row.project_id,
    n.project_name   = row.project_name,
    n.language       = row.language,
    n.repo           = row.repo
RETURN count(n) AS count
"""

_UPSERT_REPOSITORY_UNWIND = """
UNWIND $rows AS row
MERGE (n:DataRepository {symbol_id: row.symbol_id})
SET n.qualified_name = row.qualified_name,
    n.name           = row.name,
    n.kind           = row.kind,
    n.file_path      = row.file_path,
    n.start_line     = row.start_line,
    n.end_line       = row.end_line,
    n.code           = row.code,
    n.comment        = row.comment,
    n.orm_kind       = row.orm_kind,
    n.project_id     = row.project_id,
    n.project_name   = row.project_name,
    n.language       = row.language,
    n.repo           = row.repo
RETURN count(n) AS count
"""

_UPSERT_MIDDLEWARE_UNWIND = """
UNWIND $rows AS row
MERGE (n:Middleware {symbol_id: row.symbol_id})
SET n.qualified_name = row.qualified_name,
    n.name           = row.name,
    n.kind           = row.kind,
    n.file_path      = row.file_path,
    n.start_line     = row.start_line,
    n.end_line       = row.end_line,
    n.code           = row.code,
    n.comment        = row.comment,
    n.framework      = row.framework,
    n.project_id     = row.project_id,
    n.project_name   = row.project_name,
    n.language       = row.language,
    n.repo           = row.repo
RETURN count(n) AS count
"""

_UPSERT_DATABASE_UNWIND = """
UNWIND $rows AS row
MERGE (n:Database {symbol_id: row.symbol_id})
SET n.name       = row.name,
    n.project_id = row.project_id
RETURN count(n) AS count
"""

# {rel_type} is replaced at runtime via str.replace — NOT a Python format string
_UPSERT_BE_EDGE_UNWIND = """\
UNWIND $rows AS row
MATCH (src {symbol_id: row.source_id})
MATCH (tgt {symbol_id: row.target_id})
MERGE (src)-[r:{rel_type}]->(tgt)
SET r.source_file = row.source_file,
    r.call_depth  = row.call_depth,
    r.is_async    = row.is_async,
    r.confidence  = row.confidence
RETURN count(r) AS count
"""

_UPSERT_FILE_CONTAINS_BACKEND_UNWIND = """
UNWIND $rows AS row
MATCH (f:File {id: row.file_id})
MATCH (n {symbol_id: row.node_id})
MERGE (f)-[:CONTAINS]->(n)
RETURN count(*) AS count
"""


async def _write_backend_graph(
    writer: LanguageCodeWriter,
    all_payloads: List[Dict[str, Any]],
    extra_edges: List[BackendEdge],
    *,
    project_id: str,
    project_name: str,
    language: str,
    repo: str,
    build_system: str,
    batch_size: int,
    verbose: bool,
) -> None:
    _common = dict(
        project_id=project_id,
        project_name=project_name,
        language=language,
        repo=repo,
    )
    db = writer.database

    ep_batch: List[Dict[str, Any]] = []
    ctrl_batch: List[Dict[str, Any]] = []
    svc_batch: List[Dict[str, Any]] = []
    repo_batch: List[Dict[str, Any]] = []
    mw_batch: List[Dict[str, Any]] = []
    db_batch: List[Dict[str, Any]] = []
    edge_batch: List[Dict[str, Any]] = []
    file_contains_batch: List[Dict[str, Any]] = []

    db_symbols_seen: Set[str] = set()

    for payload in all_payloads:
        file_id = (payload.get("file_def") or {}).get("file_path", "")
        for ep in payload.get("endpoints") or []:
            row = {**ep, **_common,
                   "handler_names": ep.get("handler_names") or [],
                   "middleware_names": ep.get("middleware_names") or [],
                   "controller_class": ep.get("controller_class") or ""}
            ep_batch.append(row)
            if file_id:
                file_contains_batch.append({"file_id": file_id, "node_id": ep["symbol_id"]})
        for ctrl in payload.get("controllers") or []:
            ctrl_batch.append({**ctrl, **_common, "parent_class": ctrl.get("parent_class") or ""})
            if file_id:
                file_contains_batch.append({"file_id": file_id, "node_id": ctrl["symbol_id"]})
        for svc in payload.get("services") or []:
            svc_batch.append({**svc, **_common})
            if file_id:
                file_contains_batch.append({"file_id": file_id, "node_id": svc["symbol_id"]})
        for r in payload.get("repositories") or []:
            repo_batch.append({**r, **_common, "orm_kind": r.get("orm_kind") or ""})
            if file_id:
                file_contains_batch.append({"file_id": file_id, "node_id": r["symbol_id"]})
        for mw in payload.get("middlewares") or []:
            mw_batch.append({**mw, **_common})
            if file_id:
                file_contains_batch.append({"file_id": file_id, "node_id": mw["symbol_id"]})
        for edge in payload.get("edges") or []:
            edge_batch.append(edge)

    for edge in extra_edges:
        edge_batch.append(asdict(edge))

    for edge in edge_batch:
        if edge.get("target_label") == "DATABASE":
            db_sym = edge["target_id"]
            if db_sym not in db_symbols_seen:
                db_symbols_seen.add(db_sym)
                db_batch.append({
                    "symbol_id": db_sym,
                    "name": db_sym.replace("DATABASE::", ""),
                    "project_id": project_id,
                })

    async def _run_unwind(query: str, rows: List[Dict[str, Any]], label: str) -> None:
        if not rows:
            return
        for i in range(0, len(rows), batch_size):
            chunk = rows[i: i + batch_size]
            try:
                await writer.driver.execute_query(query, {"rows": chunk}, db)
            except Exception as exc:
                if verbose:
                    print(f"[backend-writer] {label} write error: {exc}")
        if verbose:
            print(f"[backend-writer] {label}: {len(rows)}")

    await _run_unwind(_UPSERT_API_ENDPOINT_UNWIND, ep_batch, "ApiEndpoint")
    await _run_unwind(_UPSERT_CONTROLLER_UNWIND, ctrl_batch, "Controller")
    await _run_unwind(_UPSERT_SERVICE_UNWIND, svc_batch, "Service")
    await _run_unwind(_UPSERT_REPOSITORY_UNWIND, repo_batch, "Repository")
    await _run_unwind(_UPSERT_MIDDLEWARE_UNWIND, mw_batch, "Middleware")
    await _run_unwind(_UPSERT_DATABASE_UNWIND, db_batch, "Database")
    # File → CONTAINS → backend node edges (written after node upserts)
    await _run_unwind(_UPSERT_FILE_CONTAINS_BACKEND_UNWIND, file_contains_batch, "FileContains")

    # Group backend edges by rel_type and write each group with UNWIND
    from collections import defaultdict
    edges_by_type: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for e in edge_batch:
        edges_by_type[e.get("rel_type", "CALLS")].append(e)
    for rel_type, edges in edges_by_type.items():
        q = _UPSERT_BE_EDGE_UNWIND.replace("{rel_type}", rel_type)
        await _run_unwind(q, edges, f"Edge({rel_type})")

    if verbose:
        print(
            f"[backend-writer] Wrote {len(ep_batch)} endpoints, {len(ctrl_batch)} controllers, "
            f"{len(svc_batch)} services, {len(repo_batch)} repos, "
            f"{len(mw_batch)} middlewares, {len(edge_batch)} edges"
        )


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="TypeScript Backend Semantic Analyzer (Express/NestJS)")
    p.add_argument("--root", required=True, help="Root folder containing TypeScript sources")
    p.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI"))
    p.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER"))
    p.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASS"))
    p.add_argument("--neo4j-db", default=os.environ.get("NEO4J_DB"))
    p.add_argument("--batch-size", type=int, default=4)
    p.add_argument("--neo4j-batch-size", type=int, default=500)
    p.add_argument("--cache-dir", default=os.environ.get("QDRANT_CACHE_DIR"))
    p.add_argument("--disable-parse-cache", action="store_true")
    p.add_argument("--ignore-cache", action="store_true",
                   help="Ignore local caches for this run.")
    p.add_argument("--project-id", dest="project_id", default=os.environ.get("PROJECT_ID"))
    p.add_argument("--project_id", dest="project_id")
    p.add_argument("--project-name", dest="project_name", default=os.environ.get("PROJECT_NAME"))
    p.add_argument("--project_name", dest="project_name")
    p.add_argument("--language", default=os.environ.get("PROJECT_LANGUAGE", "typescript"))
    p.add_argument("--repo", default=os.environ.get("PROJECT_REPO"))
    p.add_argument("--build-system", dest="build_system", default=os.environ.get("PROJECT_BUILD_SYSTEM", ""))
    p.add_argument("--build_system", dest="build_system")
    # Embedding / Qdrant args — accepted for CLI compatibility with the shared
    # command builder; the backend analyzer does not embed functions by default
    # but may use these in a future Qdrant enrichment pass.
    p.add_argument("--device", default=os.environ.get("EMBED_DEVICE", "auto"),
                   help="(reserved) Embedding device — not used by backend analyzer.")
    p.add_argument("--embed-model", default=os.environ.get("CODE_EMBEDDING_MODEL", ""),
                   help="(reserved) Embedding model name — not used by backend analyzer.")
    p.add_argument("--max-embed-chars", type=int, default=4000,
                   help="(reserved) Max chars per embedding — not used by backend analyzer.")
    p.add_argument("--qdrant-url", default=os.environ.get("QDRANT_URL", ""),
                   help="(reserved) Qdrant URL — not used by backend analyzer.")
    p.add_argument("--qdrant-collection", default=os.environ.get("QDRANT_COLLECTION", ""),
                   help="(reserved) Qdrant collection — not used by backend analyzer.")
    p.add_argument("--incremental", action="store_true")
    p.add_argument(
        "--changed-files-manifest",
        help="JSON/TXT manifest of changed+impacted file paths (relative to --root)",
    )
    p.add_argument(
        "--deleted-files-manifest",
        help="JSON/TXT manifest of deleted file paths (relative to --root)",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(enable_message_scan=True)
    p.add_argument("--enable-message-scan", dest="enable_message_scan", action="store_true", help="Enable message scan and sync (default)")
    p.add_argument("--disable-message-scan", dest="enable_message_scan", action="store_false", help="Disable message scan and sync")
    return p.parse_args(argv)


async def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    if not os.path.isdir(args.root):
        print(f"ERROR: --root '{args.root}' is not a directory", file=sys.stderr)
        return 1

    project_type = detect_project_type(args.root)
    print(f"[backend-analyzer] Detected project type: {project_type}")

    if project_type == "frontend":
        print(
            "[backend-analyzer] WARNING: project appears to be frontend — "
            "consider using ts_analyzer.py instead.",
            file=sys.stderr,
        )

    code_writer = None
    driver = None
    if args.neo4j_uri and args.neo4j_user and args.neo4j_password:
        try:
            driver = await GraphDriverFactory.create_driver(
                provider=GraphProvider.NEO4J,
                uri=args.neo4j_uri,
                user=args.neo4j_user,
                password=args.neo4j_password,
            )
            code_writer = LanguageCodeWriter(
                driver=driver,
                database=args.neo4j_db,
                batch_size=args.neo4j_batch_size,
                verbose=args.verbose,
            )
            if args.verbose:
                print(f"[backend-analyzer] Connected to Neo4j at {args.neo4j_uri}")
        except Exception as exc:
            print(f"[backend-analyzer] Neo4j connection failed: {exc}", file=sys.stderr)

    parse_cache = not args.disable_parse_cache
    if args.ignore_cache:
        parse_cache = False
    project_id = args.project_id or os.path.basename(os.path.abspath(args.root))
    project_name = args.project_name or project_id
    language = args.language or "typescript"
    repo = args.repo or os.path.abspath(args.root)

    changed_files: List[str] = []
    deleted_files: List[str] = []
    if args.incremental:
        if args.changed_files_manifest:
            changed_files = list(load_manifest_paths(args.changed_files_manifest, args.root))
        if args.deleted_files_manifest:
            deleted_files = list(load_manifest_paths(args.deleted_files_manifest, args.root))

    if args.dry_run:
        files = _scan_backend_files(args.root)
        print(f"Dry run: {len(files)} TypeScript/JavaScript files found under {args.root}")
        print(f"  Project type: {project_type}")
        return 0

    try:
        await build_backend_graph(
            root=args.root,
            code_writer=code_writer,
            batch_size=args.batch_size,
            cache_dir=args.cache_dir,
            parse_cache=parse_cache,
            neo4j_batch_size=args.neo4j_batch_size,
            project_id=project_id,
            project_name=project_name,
            language=language,
            repo=repo,
            build_system=args.build_system,
            verbose=args.verbose,
            incremental=args.incremental,
            changed_files=changed_files,
            deleted_files=deleted_files,
        )
    finally:
        if driver:
            driver.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
