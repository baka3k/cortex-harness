"""
Graph-store adapter for doc-tiny scripts.

Neo4j remains the default provider. FalkorDB can be selected with
``--graph-provider falkordb`` or ``DOC_GRAPH_PROVIDER=falkordb``.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, Optional

from neo4j import GraphDatabase


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
    def __init__(self, driver: FalkorDBDriver):
        self._driver = driver

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
        records, _, _ = self._driver.execute_query_sync(query, params)
        return FalkorDBResult(records)


class FalkorDBGraphStore:
    provider = "falkordb"

    def __init__(self, driver: FalkorDBDriver):
        self._driver = driver

    def session(self) -> FalkorDBSession:
        return FalkorDBSession(self._driver)

    def close(self) -> None:
        self._driver.close()

    def setup_indexes(self) -> None:
        graph = self._driver.graph
        for index in DOC_INDEXES:
            label = index["label"]
            prop = index["property"]
            props = prop if isinstance(prop, list) else [prop]
            try:
                graph.create_node_range_index(label, *props)
                print(f"Applied FalkorDB range index: {label}({', '.join(props)})")
            except Exception as exc:
                print(f"Skipped FalkorDB range index {label}({', '.join(props)}): {exc}")


class Neo4jGraphStore:
    provider = "neo4j"

    def __init__(self, uri: str, user: str, password: str):
        self._driver = GraphDatabase.driver(uri, auth=(user, password))

    def session(self):
        return self._driver.session()

    def close(self) -> None:
        self._driver.close()

    def setup_indexes(self) -> None:
        with self.session() as session:
            for statement in NEO4J_INDEX_STATEMENTS:
                session.run(statement)
                print(f"Applied: {statement}")


def normalize_provider(value: Optional[str]) -> str:
    provider = (value or "neo4j").strip().lower()
    if provider in {"falkor", "falkordb"}:
        return "falkordb"
    if provider == "neo4j":
        return provider
    raise ValueError(f"Unsupported graph provider: {value}")


def env_graph_provider() -> str:
    return normalize_provider(os.getenv("DOC_GRAPH_PROVIDER") or os.getenv("GRAPH_PROVIDER"))


def add_graph_store_args(parser) -> None:
    parser.add_argument(
        "--graph-provider",
        choices=["neo4j", "falkordb"],
        default=env_graph_provider(),
        help="Graph database provider for doc-tiny graph operations.",
    )
    parser.add_argument("--falkordb-uri", default=os.getenv("FALKORDB_URI") or os.getenv("FALKORDB_URL"))
    parser.add_argument("--falkordb-host", default=os.getenv("FALKORDB_HOST", "localhost"))
    parser.add_argument("--falkordb-port", type=int, default=int(os.getenv("FALKORDB_PORT", "6379")))
    parser.add_argument("--falkordb-user", default=os.getenv("FALKORDB_USER") or os.getenv("FALKORDB_USERNAME"))
    parser.add_argument("--falkordb-pass", default=os.getenv("FALKORDB_PASSWORD", ""))
    parser.add_argument(
        "--falkordb-graph",
        default=os.getenv("FALKORDB_GRAPH") or os.getenv("FALKORDB_DATABASE", "neo4j"),
    )
    parser.add_argument(
        "--falkordb-ssl",
        action="store_true",
        default=os.getenv("FALKORDB_SSL", "").lower() in {"1", "true", "yes", "on"},
    )


def create_graph_store_from_args(args):
    provider = normalize_provider(getattr(args, "graph_provider", None))
    if provider == "neo4j":
        return Neo4jGraphStore(args.neo4j_uri, args.neo4j_user, args.neo4j_pass)
    return FalkorDBGraphStore(
        FalkorDBDriver(
            uri=getattr(args, "falkordb_uri", None),
            user=getattr(args, "falkordb_user", None),
            password=getattr(args, "falkordb_pass", None),
            graph=getattr(args, "falkordb_graph", None),
            host=getattr(args, "falkordb_host", None),
            port=getattr(args, "falkordb_port", None),
            ssl=bool(getattr(args, "falkordb_ssl", False)),
        )
    )


def create_graph_store_from_env():
    provider = env_graph_provider()
    if provider == "neo4j":
        return Neo4jGraphStore(
            os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            os.getenv("NEO4J_USER") or os.getenv("NEO4J_USERNAME", "neo4j"),
            os.getenv("NEO4J_PASS", "password"),
        )
    return FalkorDBGraphStore(
        FalkorDBDriver(
            uri=os.getenv("FALKORDB_URI") or os.getenv("FALKORDB_URL"),
            user=os.getenv("FALKORDB_USER") or os.getenv("FALKORDB_USERNAME"),
            password=os.getenv("FALKORDB_PASSWORD", ""),
            graph=os.getenv("FALKORDB_GRAPH") or os.getenv("FALKORDB_DATABASE", "neo4j"),
            host=os.getenv("FALKORDB_HOST", "localhost"),
            port=int(os.getenv("FALKORDB_PORT", "6379")),
            ssl=os.getenv("FALKORDB_SSL", "").lower() in {"1", "true", "yes", "on"},
        )
    )
