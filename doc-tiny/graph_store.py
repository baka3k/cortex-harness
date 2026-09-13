"""
Graph-store adapter for doc-tiny scripts.

Providers: ``neo4j`` (remote server), ``falkordb`` (embedded FalkorDBLite or
remote server), and ``ladybug`` (embedded LadybugDB, local-only). Select with
``--graph-provider`` or ``DOC_GRAPH_PROVIDER``/``GRAPH_PROVIDER``.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional


_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_CODE_TINY = os.path.join(_REPO_ROOT, "code-tiny")
if _CODE_TINY not in sys.path:
    sys.path.insert(0, _CODE_TINY)

from tools.graph.driver.falkordb_driver import FalkorDBDriver


DOC_INDEXES = [
    {"label": "Chunk", "property": ["doc_id", "id"]},
    {"label": "Entity", "property": "name"},
    {"label": "Entity", "property": "type"},
    {"label": "Document", "property": "id"},
]

NEO4J_INDEX_STATEMENTS = [
    "CREATE INDEX chunk_doc_idx IF NOT EXISTS FOR (c:Chunk) ON (c.doc_id, c.id)",
    "CREATE INDEX entity_name_idx IF NOT EXISTS FOR (e:Entity) ON (e.name)",
    "CREATE INDEX entity_type_idx IF NOT EXISTS FOR (e:Entity) ON (e.type)",
    "CREATE INDEX document_id_idx IF NOT EXISTS FOR (d:Document) ON (d.id)",
]


class FalkorDBResult(list):
    def single(self) -> Optional[Dict[str, Any]]:
        return self[0] if self else None


class FalkorDBSession:
    def __init__(
        self, driver: FalkorDBDriver, database: Optional[str] = None
    ):
        self._driver = driver
        self._database = database or getattr(driver, "database", "neo4j")

    def __enter__(self) -> "FalkorDBSession":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def run(
        self,
        query: str,
        parameters: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> FalkorDBResult:
        params = dict(parameters or {})
        params.update(kwargs)
        records, _, _ = self._driver.execute_query_sync(
            query, params, self._database
        )
        return FalkorDBResult(records)


class FalkorDBGraphStore:
    provider = "falkordb"

    def __init__(
        self,
        driver: FalkorDBDriver,
        database: Optional[str] = None,
        *,
        owns_driver: bool = True,
    ):
        self._driver = driver
        self._database = database or getattr(driver, "database", "neo4j")
        self._owns_driver = owns_driver

    def session(self) -> FalkorDBSession:
        return FalkorDBSession(self._driver, self._database)

    def for_graph(self, database: str) -> "FalkorDBGraphStore":
        """Return a lightweight graph view over this store's shared driver."""
        return FalkorDBGraphStore(
            self._driver, database, owns_driver=False
        )

    def close(self) -> None:
        if self._owns_driver:
            self._driver.close()

    def setup_indexes(self) -> None:
        # Route through the driver's provider-neutral index API: Ladybug has
        # no ``select_graph`` and FalkorDB index creation stays inside its
        # driver as well.
        asyncio.run(self._driver.create_indexes(DOC_INDEXES))
        for index in DOC_INDEXES:
            label = index["label"]
            prop = index["property"]
            props = prop if isinstance(prop, list) else [prop]
            print(f"Applied {self.provider} index: {label}({', '.join(props)})")


class Neo4jGraphStore:
    provider = "neo4j"

    def __init__(
        self, uri: str, user: str, password: str, database: Optional[str] = None
    ):
        from neo4j import GraphDatabase

        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._database = database

    def session(self):
        if self._database:
            return self._driver.session(database=self._database)
        return self._driver.session()

    def close(self) -> None:
        self._driver.close()

    def setup_indexes(self) -> None:
        with self.session() as session:
            for statement in NEO4J_INDEX_STATEMENTS:
                session.run(statement)
                print(f"Applied: {statement}")


def normalize_provider(value: Optional[str]) -> str:
    provider = (value or "falkordb").strip().lower()
    if provider in {"falkor", "falkordb"}:
        return "falkordb"
    if provider in {"ladybug", "lbug", "lady-bug", "kuzu"}:
        # "kuzu" predates the LadybugDB fork and resolves onto it.
        return "ladybug"
    if provider == "neo4j":
        return provider
    raise ValueError(f"Unsupported graph provider: {value}")


def env_graph_provider() -> str:
    return normalize_provider(os.getenv("DOC_GRAPH_PROVIDER") or os.getenv("GRAPH_PROVIDER") or "falkordb")


def add_graph_store_args(parser) -> None:
    parser.add_argument(
        "--graph-provider",
        choices=["neo4j", "falkordb", "ladybug"],
        default=env_graph_provider(),
        help="Graph database provider for doc-tiny graph operations.",
    )
    parser.add_argument("--falkordb-path", default=os.getenv("FALKORDB_PATH"))
    parser.add_argument(
        "--falkordb-uri",
        default=os.getenv("FALKORDB_URI"),
        help=(
            "Remote FalkorDB server URI (scheme://host:port or host:port). "
            "When set, --falkordb-path and the embedded backend are ignored."
        ),
    )
    parser.add_argument(
        "--falkordb-password",
        default=os.getenv("FALKORDB_PASSWORD"),
        help="Password for the remote FalkorDB server (optional).",
    )
    parser.add_argument(
        "--falkordb-ssl",
        action="store_true",
        default=os.getenv("FALKORDB_SSL", "").strip().lower()
        not in ("", "0", "false", "no"),
        help="Use TLS for the remote FalkorDB server.",
    )
    parser.add_argument(
        "--falkordb-graph",
        default=os.getenv("FALKORDB_GRAPH") or os.getenv("FALKORDB_DATABASE", "neo4j"),
    )
    parser.add_argument("--ladybug-path", default=os.getenv("LADYBUG_PATH"))
    parser.add_argument(
        "--ladybug-graph",
        default=os.getenv("LADYBUG_GRAPH") or "hyper_graph",
    )


def _resolve_default_graph_path(role: str) -> str:
    from cortex_harness.storage import resolve_storage

    resolved = resolve_storage(Path.cwd())
    if role == "doc":
        return str(resolved.falkordb_doc_path)
    return str(resolved.falkordb_code_path)


def _resolve_default_ladybug_path(role: str) -> str:
    from cortex_harness.storage import resolve_storage

    resolved = resolve_storage(Path.cwd())
    if role == "doc":
        return str(resolved.ladybug_doc_path)
    return str(resolved.ladybug_code_path)


def _open_ladybug_store(graph: Optional[str], path: Optional[str], role: str = "doc"):
    from tools.graph.driver.ladybug_driver import LadybugDriver

    if not path:
        path = _resolve_default_ladybug_path(role)
    return FalkorDBGraphStore(
        LadybugDriver(
            path=path,
            graph=graph or "hyper_graph",
            owner_id=os.getenv("CORTEX_STORAGE_OWNER", "doc"),
            instance_id=os.getenv("CORTEX_STORAGE_INSTANCE", "default"),
        )
    )


def create_graph_store_from_args(args):
    provider = normalize_provider(getattr(args, "graph_provider", None))
    if provider == "neo4j":
        return Neo4jGraphStore(
            args.neo4j_uri,
            args.neo4j_user,
            args.neo4j_pass,
            getattr(args, "neo4j_db", None) or os.getenv("NEO4J_DB"),
        )
    if provider == "ladybug":
        return _open_ladybug_store(
            getattr(args, "ladybug_graph", None),
            getattr(args, "ladybug_path", None),
        )
    falkordb_uri = getattr(args, "falkordb_uri", None)
    if falkordb_uri:
        return FalkorDBGraphStore(
            FalkorDBDriver(
                uri=falkordb_uri,
                password=getattr(args, "falkordb_password", None),
                ssl=bool(getattr(args, "falkordb_ssl", False)),
                graph=getattr(args, "falkordb_graph", None),
                _suppress_deprecation=True,
            )
        )
    path = getattr(args, "falkordb_path", None)
    if not path:
        path = _resolve_default_graph_path("doc")
    return FalkorDBGraphStore(
        FalkorDBDriver(
            path=path,
            graph=getattr(args, "falkordb_graph", None),
            owner_id=os.getenv("CORTEX_STORAGE_OWNER", "doc"),
            instance_id=os.getenv("CORTEX_STORAGE_INSTANCE", "default"),
        )
    )


def create_graph_store_from_env():
    provider = env_graph_provider()
    if provider == "neo4j":
        return Neo4jGraphStore(
            os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            os.getenv("NEO4J_USER") or os.getenv("NEO4J_USERNAME", "neo4j"),
            os.getenv("NEO4J_PASS", "password"),
            os.getenv("NEO4J_DB"),
        )
    if provider == "ladybug":
        return _open_ladybug_store(
            os.getenv("LADYBUG_GRAPH") or "hyper_graph",
            os.getenv("LADYBUG_PATH"),
        )
    falkordb_uri = (os.getenv("FALKORDB_URI") or "").strip()
    if falkordb_uri:
        return FalkorDBGraphStore(
            FalkorDBDriver(
                uri=falkordb_uri,
                password=os.getenv("FALKORDB_PASSWORD"),
                ssl=os.getenv("FALKORDB_SSL", "").strip().lower()
                not in ("", "0", "false", "no"),
                graph=os.getenv("FALKORDB_GRAPH")
                or os.getenv("FALKORDB_DATABASE", "neo4j"),
                _suppress_deprecation=True,
            )
        )
    path = os.getenv("FALKORDB_PATH")
    if not path:
        path = _resolve_default_graph_path("doc")
    return FalkorDBGraphStore(
        FalkorDBDriver(
            path=path,
            graph=os.getenv("FALKORDB_GRAPH") or os.getenv("FALKORDB_DATABASE", "neo4j"),
            owner_id=os.getenv("CORTEX_STORAGE_OWNER", "doc"),
            instance_id=os.getenv("CORTEX_STORAGE_INSTANCE", "default"),
        )
    )


def create_graph_store_for_project(project_id: str):
    """Create a request-scoped store for the registry-resolved doc graph."""
    from project_contract import resolve_project_targets

    targets = resolve_project_targets(project_id)
    provider = env_graph_provider()
    if provider == "neo4j":
        return Neo4jGraphStore(
            os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            os.getenv("NEO4J_USER") or os.getenv("NEO4J_USERNAME", "neo4j"),
            os.getenv("NEO4J_PASS", "password"),
            targets.doc_graph,
        )
    if provider == "ladybug":
        return _open_ladybug_store(targets.doc_graph, os.getenv("LADYBUG_PATH"))
    falkordb_uri = (os.getenv("FALKORDB_URI") or "").strip()
    if falkordb_uri:
        return FalkorDBGraphStore(
            FalkorDBDriver(
                uri=falkordb_uri,
                password=os.getenv("FALKORDB_PASSWORD"),
                ssl=os.getenv("FALKORDB_SSL", "").strip().lower()
                not in ("", "0", "false", "no"),
                graph=targets.doc_graph,
                _suppress_deprecation=True,
            )
        )
    path = os.getenv("FALKORDB_PATH")
    if not path:
        path = _resolve_default_graph_path("doc")
    return FalkorDBGraphStore(
        FalkorDBDriver(
            path=path,
            graph=targets.doc_graph,
            owner_id=os.getenv("CORTEX_STORAGE_OWNER", "doc"),
            instance_id=os.getenv("CORTEX_STORAGE_INSTANCE", "default"),
        )
    )
