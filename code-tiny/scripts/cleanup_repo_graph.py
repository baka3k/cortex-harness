"""cleanup_repo_graph.py

Delete graph data for one repository scope before overwrite scan.

Scope key: (project_id, repo_name)

Usage:
    python cleanup_repo_graph.py \
        --project-id <id> \
        --repo-name <project/repo> \
        [--graph-provider falkordb] \
        [--falkordb-path ~/.cortext-harness/.../data.rdb] \
        [--neo4j-uri bolt://localhost:7687] \
        [--neo4j-user neo4j] \
        [--neo4j-password <pw>] \
        [--neo4j-db neo4j]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
import time
from argparse import Namespace

from tools.graph.cli import (
    add_graph_provider_args,
    create_graph_driver_from_args,
    env_graph_provider,
)

_MAX_RETRIES = 5
_BASE_BACKOFF = 0.2

_DELETE_REPO_SCOPED_QUERY = """
MATCH (n)
WHERE n.project_id = $project_id
  AND n.repo = $repo_name
WITH collect(DISTINCT n) AS nodes
UNWIND nodes AS n
DETACH DELETE n
RETURN count(n) AS deleted_nodes
"""

_DELETE_ORPHAN_UNKNOWN_FUNCTIONS = """
MATCH (u:UnknownFunction)
WHERE NOT ()-[:UNKNOWN_CALL]->(u)
WITH collect(u) AS nodes
UNWIND nodes AS u
DETACH DELETE u
RETURN count(u) AS deleted_unknown
"""


def _run_with_retry(driver, query: str, database: str, **params):
    for attempt in range(_MAX_RETRIES):
        try:
            records, _, _ = driver.execute_query_sync(query, params, database)
            return records
        except Exception as exc:
            transient = exc.__class__.__name__ in {"TransientError", "ServiceUnavailable"}
            if not transient or attempt >= _MAX_RETRIES - 1:
                raise
            delay = _BASE_BACKOFF * (2 ** attempt) + random.uniform(0, _BASE_BACKOFF)
            print(
                "[cleanup_repo_graph] transient failure "
                f"(attempt {attempt + 1}/{_MAX_RETRIES}), retry in {delay:.2f}s: {exc}"
            )
            time.sleep(delay)


def cleanup_repo_graph(
    *,
    project_id: str,
    repo_name: str,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    neo4j_db: str,
    graph_provider: str | None = None,
    falkordb_path: str | None = None,
    falkordb_graph: str | None = None,
) -> tuple[int, int]:
    args = Namespace(
        project_id=project_id,
        graph_provider=graph_provider or env_graph_provider(),
        falkordb_path=falkordb_path,
        falkordb_graph=falkordb_graph,
        neo4j_uri=neo4j_uri,
        neo4j_user=neo4j_user,
        neo4j_password=neo4j_password,
        neo4j_db=neo4j_db,
    )
    driver = asyncio.run(create_graph_driver_from_args(args))
    if driver is None:
        raise RuntimeError(
            "Neo4j rollback mode requires URI, user, and password credentials."
        )
    try:
        deleted_nodes_result = _run_with_retry(
            driver,
            _DELETE_REPO_SCOPED_QUERY,
            args.neo4j_db,
            project_id=project_id,
            repo_name=repo_name,
        )
        deleted_nodes_record = deleted_nodes_result[0] if deleted_nodes_result else {}
        deleted_nodes = int(deleted_nodes_record.get("deleted_nodes", 0))

        deleted_unknown_result = _run_with_retry(
            driver,
            _DELETE_ORPHAN_UNKNOWN_FUNCTIONS,
            args.neo4j_db,
        )
        deleted_unknown_record = deleted_unknown_result[0] if deleted_unknown_result else {}
        deleted_unknown = int(deleted_unknown_record.get("deleted_unknown", 0))

        return deleted_nodes, deleted_unknown
    finally:
        driver.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cleanup graph data for one repository scope (project_id + repo_name)."
    )
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--repo-name", required=True)
    parser.add_argument("--neo4j-uri", default=os.getenv("NEO4J_URI", "bolt://localhost:7687"))
    parser.add_argument("--neo4j-user", default=os.getenv("NEO4J_USER", "neo4j"))
    parser.add_argument("--neo4j-password", default=os.getenv("NEO4J_PASS", ""))
    parser.add_argument("--neo4j-db", default=os.getenv("NEO4J_DB", "neo4j"))
    add_graph_provider_args(parser)
    args = parser.parse_args()

    try:
        deleted_nodes, deleted_unknown = cleanup_repo_graph(
            project_id=args.project_id,
            repo_name=args.repo_name,
            neo4j_uri=args.neo4j_uri,
            neo4j_user=args.neo4j_user,
            neo4j_password=args.neo4j_password,
            neo4j_db=args.neo4j_db,
            graph_provider=args.graph_provider,
            falkordb_path=args.falkordb_path,
            falkordb_graph=args.falkordb_graph,
        )
        print(
            "[cleanup_repo_graph] OK "
            f"project={args.project_id!r} repo={args.repo_name!r} "
            f"deleted_nodes={deleted_nodes} deleted_unknown={deleted_unknown}"
        )
        return 0
    except Exception as exc:
        print(f"[cleanup_repo_graph] FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
