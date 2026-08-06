"""Provider-neutral graph runtime shared by the LivingDoc command-line tools.

FalkorDBLite is the supported default and always opens the code owner's
``FALKORDB_PATH``.  Neo4j remains available only when explicitly selected as
the rollback provider; importing this module never imports the Neo4j package
directly.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

from cortex_harness.storage import resolve_storage
from tools.graph.core.base import GraphDriver, GraphProvider
from tools.graph.core.factory import GraphDriverFactory


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    return os.getenv(name, default)


def default_falkordb_path() -> str:
    return str(resolve_storage(Path.cwd()).falkordb_code_path)


def add_graph_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the common local graph contract and isolated Neo4j rollback flags."""
    parser.add_argument(
        "--graph-provider",
        choices=("falkordb", "neo4j"),
        default=_env("CODE_GRAPH_PROVIDER") or _env("GRAPH_PROVIDER", "falkordb"),
        help="Graph provider. The supported default is local FalkorDBLite.",
    )
    parser.add_argument(
        "--falkordb-path",
        default=_env("FALKORDB_PATH"),
        help="Owner-scoped FalkorDBLite .rdb path; derived when omitted.",
    )
    parser.add_argument(
        "--falkordb-graph",
        default=_env("FALKORDB_GRAPH") or _env("FALKORDB_DATABASE"),
        help="Logical graph name inside the local FalkorDBLite store.",
    )

    # Explicit rollback only. These values are neither required nor consulted
    # while the default FalkorDB provider is selected.
    parser.add_argument("--neo4j-uri", default=_env("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=_env("NEO4J_USER"))
    parser.add_argument(
        "--neo4j-pass",
        "--neo4j-password",
        dest="neo4j_password",
        default=_env("NEO4J_PASS") or _env("NEO4J_PASSWORD"),
    )
    parser.add_argument("--neo4j-db", default=_env("NEO4J_DB"))


def prepare_graph_arguments(args: argparse.Namespace) -> argparse.Namespace:
    provider = str(getattr(args, "graph_provider", "falkordb") or "falkordb").lower()
    if provider == "falkordb":
        args.falkordb_path = str(
            Path(getattr(args, "falkordb_path", None) or default_falkordb_path())
            .expanduser()
            .resolve()
        )
        args.falkordb_graph = (
            getattr(args, "falkordb_graph", None)
            or getattr(args, "project_id", None)
            or "hyper_graph"
        )
        return args

    missing = [
        flag
        for flag, value in (
            ("NEO4J_URI/--neo4j-uri", getattr(args, "neo4j_uri", None)),
            ("NEO4J_USER/--neo4j-user", getattr(args, "neo4j_user", None)),
            ("NEO4J_PASS/--neo4j-pass", getattr(args, "neo4j_password", None)),
        )
        if not value
    ]
    if missing:
        raise ValueError("Explicit Neo4j rollback requires: " + ", ".join(missing))
    return args


def create_graph_driver(args: argparse.Namespace) -> GraphDriver:
    prepare_graph_arguments(args)
    if args.graph_provider == "neo4j":
        return asyncio.run(
            GraphDriverFactory.create_driver(
                GraphProvider.NEO4J,
                uri=args.neo4j_uri,
                user=args.neo4j_user,
                password=args.neo4j_password,
                database=args.neo4j_db,
            )
        )

    return asyncio.run(
        GraphDriverFactory.create_driver(
            GraphProvider.FALKORDB,
            {
                "path": args.falkordb_path,
                "graph": args.falkordb_graph,
                "database": args.falkordb_graph,
                "instance_id": _env("CORTEX_STORAGE_INSTANCE", "default"),
                "owner_id": _env("CORTEX_CODE_STORAGE_OWNER", "code"),
            },
        )
    )


class Record(dict):
    """Small compatibility record used by the legacy LivingDoc helpers."""

    def data(self) -> dict[str, Any]:
        return dict(self)


class QueryResult(list[Record]):
    def single(self) -> Optional[Record]:
        return self[0] if self else None


class QuerySession:
    """Neo4j-session-shaped facade backed only by the GraphDriver contract."""

    def __init__(self, driver: GraphDriver, database: Optional[str] = None):
        self.driver = driver
        self.database = database

    @property
    def provider(self) -> GraphProvider:
        return self.driver.provider

    def run(
        self,
        query: str,
        parameters: Optional[Mapping[str, Any]] = None,
    ) -> QueryResult:
        records, _, _ = self.driver.execute_query_sync(
            query,
            dict(parameters or {}),
            database=self.database,
        )
        return QueryResult(Record(record) for record in records)


@contextmanager
def open_graph_session(args: argparse.Namespace) -> Iterator[QuerySession]:
    driver = create_graph_driver(args)
    database = args.neo4j_db if args.graph_provider == "neo4j" else args.falkordb_graph
    try:
        yield QuerySession(driver, database=database)
    finally:
        driver.close()
