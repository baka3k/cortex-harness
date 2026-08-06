"""link_project_repos.py

Post-scan linking step.

After all individual repo scans have finished for a project, this script
creates exactly ONE Project node and connects it to EVERY Repository node
that carries matching project_id.

The Project node is only connected to Repository nodes (HAS_REPOSITORY).
It does NOT get direct edges to File, Function, or any other child nodes
— that hierarchy is handled by Repository → HAS_FILE → File.

Usage
-----
    python link_project_repos.py \\
        --project-id   <uuid>          \\
        --project-name <display name>  \\
        [--project-slug <slug>]        \\
        [--graph-provider falkordb]    \\
        [--falkordb-path <data.rdb>]   \\
        [--neo4j-uri   bolt://...]     \\
        [--neo4j-user  neo4j]          \\
        [--neo4j-password ...]         \\
        [--neo4j-db    neo4j]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from argparse import Namespace

from tools.graph.cli import (
    add_graph_provider_args,
    create_graph_driver_from_args,
    env_graph_provider,
)

# ---------------------------------------------------------------------------
# Cypher
# ---------------------------------------------------------------------------

# Upsert the Project node then link every Repository that belongs to it.
_LINK_REPOS_QUERY = """
MERGE (p:Project {project_id: $project_id})
ON CREATE SET
    p.name       = $project_name,
    p.slug       = $project_slug,
    p.created_at = timestamp()
ON MATCH SET
    p.name       = $project_name,
    p.slug       = $project_slug
WITH p
MATCH (r:Repository)
WHERE r.project_id = $project_id
MERGE (p)-[:HAS_REPOSITORY]->(r)
RETURN count(r) AS linked
"""

# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_RETRY_DELAY = 2.0  # seconds


def run(
    *,
    project_id: str,
    project_name: str,
    project_slug: str,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_pass: str,
    neo4j_db: str,
    graph_provider: str | None = None,
    falkordb_path: str | None = None,
    falkordb_graph: str | None = None,
) -> int:
    args = Namespace(
        project_id=project_id,
        graph_provider=graph_provider or env_graph_provider(),
        falkordb_path=falkordb_path,
        falkordb_graph=falkordb_graph,
        neo4j_uri=neo4j_uri,
        neo4j_user=neo4j_user,
        neo4j_password=neo4j_pass,
        neo4j_db=neo4j_db,
    )
    driver = asyncio.run(create_graph_driver_from_args(args))
    if driver is None:
        raise RuntimeError(
            "Neo4j rollback mode requires URI, user, and password credentials."
        )
    try:
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                records, _, _ = driver.execute_query_sync(
                    _LINK_REPOS_QUERY,
                    {
                        "project_id": project_id,
                        "project_name": project_name,
                        "project_slug": project_slug,
                    },
                    args.neo4j_db,
                )
                linked = int((records[0] if records else {}).get("linked", 0))
                print(
                    f"[link_project_repos] OK  "
                    f"project={project_id!r}  repos_linked={linked}"
                )
                return linked
            except Exception as exc:
                transient = exc.__class__.__name__ in {"TransientError", "ServiceUnavailable"}
                if transient and attempt < _MAX_RETRIES:
                    print(
                        f"[link_project_repos] transient error (attempt {attempt}/{_MAX_RETRIES}): "
                        f"{exc} — retrying in {_RETRY_DELAY}s"
                    )
                    time.sleep(_RETRY_DELAY)
                else:
                    raise
    finally:
        driver.close()
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Create (Project)-[:HAS_REPOSITORY]->(Repository) edges "
            "for all repos that share the given project_id."
        )
    )
    parser.add_argument("--project-id", required=True, help="UUID of the project")
    parser.add_argument("--project-name", required=True, help="Display name of the project")
    parser.add_argument("--project-slug", default="", help="URL-safe slug (derived from name if omitted)")
    parser.add_argument(
        "--neo4j-uri",
        default=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
    )
    parser.add_argument(
        "--neo4j-user",
        default=os.getenv("NEO4J_USER", "neo4j"),
    )
    parser.add_argument(
        "--neo4j-password",
        default=os.getenv("NEO4J_PASS", ""),
    )
    parser.add_argument(
        "--neo4j-db",
        default=os.getenv("NEO4J_DB", "neo4j"),
    )
    add_graph_provider_args(parser)
    args = parser.parse_args()

    slug = args.project_slug or args.project_name.lower().replace(" ", "-")

    run(
        project_id=args.project_id,
        project_name=args.project_name,
        project_slug=slug,
        neo4j_uri=args.neo4j_uri,
        neo4j_user=args.neo4j_user,
        neo4j_pass=args.neo4j_password,
        neo4j_db=args.neo4j_db,
        graph_provider=args.graph_provider,
        falkordb_path=args.falkordb_path,
        falkordb_graph=args.falkordb_graph,
    )


if __name__ == "__main__":
    main()
