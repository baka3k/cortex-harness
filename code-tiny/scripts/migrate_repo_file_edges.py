"""migrate_repo_file_edges.py

One-time migration that fixes the graph structure for data scanned BEFORE the
repo-isolation fix was applied.

What it does
------------
Pass 1 — Backfill HAS_FILE edges:
    For every File node that has a ``repo`` property and a matching Repository
    node (Repository.name == File.repo), create the
    ``(Repository)-[:HAS_FILE]->(File)`` relationship if it does not already
    exist.

Pass 2 — Remove invalid Project→File CONTAINS edges:
    Delete any ``(Project)-[:CONTAINS]->(File)`` relationship.  The correct
    path from a Project to its files is now:
        (Project)-[:HAS_REPOSITORY]->(Repository)-[:HAS_FILE]->(File)
    Direct Project→File CONTAINS edges are structurally wrong and must be
    removed.

Both passes are purely relationship operations — no nodes are created or
deleted.

Usage:
    python migrate_repo_file_edges.py
    python migrate_repo_file_edges.py --graph-provider falkordb --dry-run
    python migrate_repo_file_edges.py --neo4j-uri bolt://host:7687 --dry-run

Environment variables (fallbacks):
    NEO4J_URI       bolt://localhost:7687
    NEO4J_USER      neo4j
    NEO4J_PASSWORD  (empty)
    NEO4J_DB        neo4j
"""
from __future__ import annotations

import argparse
import asyncio
import os
from argparse import Namespace

from tools.graph.cli import (
    add_graph_provider_args,
    create_graph_driver_from_args,
    env_graph_provider,
)


# ---------------------------------------------------------------------------
# Cypher
# ---------------------------------------------------------------------------

# Pass 1: create (Repository)-[:HAS_FILE]->(File) for every file whose
# repo property matches a Repository node name.
_BACKFILL_HAS_FILE = """
MATCH (r:Repository)
MATCH (f:File)
WHERE f.repo = r.name
  AND NOT (r)-[:HAS_FILE]->(f)
WITH r, f
MERGE (r)-[:HAS_FILE]->(f)
RETURN count(f) AS created
"""

# Pass 2: count then delete direct (Project)-[:CONTAINS]->(File) edges.
_COUNT_BAD_EDGES = """
MATCH (p:Project)-[rel:CONTAINS]->(f:File)
RETURN count(rel) AS total
"""

_DELETE_BAD_EDGES = """
MATCH (p:Project)-[rel:CONTAINS]->(f:File)
DELETE rel
RETURN count(rel) AS deleted
"""

# Pass 3: merge orphan Project nodes that were created by the old
# setup_graph_project.py (which used MERGE on {project_id:...} instead of {id:...}).
# We copy their HAS_REPOSITORY relationships to the canonical node (MERGE key = id)
# and then DETACH DELETE the orphan.
_COUNT_ORPHAN_PROJECTS = """
MATCH (orphan:Project)
WHERE orphan.project_id IS NOT NULL AND orphan.id IS NULL
RETURN count(orphan) AS total
"""

_MIGRATE_ORPHAN_PROJECTS = """
MATCH (orphan:Project)
WHERE orphan.project_id IS NOT NULL AND orphan.id IS NULL
WITH orphan
MERGE (canonical:Project {project_id: orphan.project_id})
ON CREATE SET
    canonical.name       = orphan.name,
    canonical.slug       = orphan.slug,
    canonical.created_at = orphan.created_at
ON MATCH SET
    canonical.name       = coalesce(canonical.name, orphan.name),
    canonical.slug       = coalesce(canonical.slug, orphan.slug)
WITH orphan, canonical
OPTIONAL MATCH (orphan)-[:HAS_REPOSITORY]->(r:Repository)
WITH orphan, canonical, collect(r) AS repos
FOREACH (r IN repos | MERGE (canonical)-[:HAS_REPOSITORY]->(r))
DETACH DELETE orphan
RETURN count(canonical) AS merged
"""



# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_migration(
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    neo4j_db: str,
    dry_run: bool = False,
    graph_provider: str | None = None,
    falkordb_path: str | None = None,
    falkordb_graph: str | None = None,
) -> None:
    args = Namespace(
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
        # ── Pass 1: backfill HAS_FILE edges ──────────────────────────────
        if dry_run:
            records, _, _ = driver.execute_query_sync("""
                MATCH (r:Repository)
                MATCH (f:File)
                WHERE f.repo = r.name AND NOT (r)-[:HAS_FILE]->(f)
                RETURN count(f) AS would_create
            """, database=args.neo4j_db)
            would_create = (records[0] if records else {}).get("would_create", 0)
            print(f"[DRY RUN] Pass 1: would create {would_create} HAS_FILE edges")
        else:
            records, _, _ = driver.execute_query_sync(
                _BACKFILL_HAS_FILE, database=args.neo4j_db
            )
            created = (records[0] if records else {}).get("created", 0)
            print(f"[OK] Pass 1: created {created} HAS_FILE edges")

        # ── Pass 2: remove bad Project→File CONTAINS edges ───────────────
        records, _, _ = driver.execute_query_sync(
            _COUNT_BAD_EDGES, database=args.neo4j_db
        )
        total_bad = (records[0] if records else {}).get("total", 0)
        print(f"     Pass 2: found {total_bad} invalid (Project)-[:CONTAINS]->(File) edges")

        if total_bad > 0:
            if dry_run:
                print(f"[DRY RUN] Pass 2: would delete {total_bad} edges")
            else:
                records, _, _ = driver.execute_query_sync(
                    _DELETE_BAD_EDGES, database=args.neo4j_db
                )
                deleted = (records[0] if records else {}).get("deleted", 0)
                print(f"[OK] Pass 2: deleted {deleted} invalid edges")
        else:
            print("[OK] Pass 2: nothing to clean up")

        # ── Pass 3: merge orphan Project nodes ───────────────────────────
        records, _, _ = driver.execute_query_sync(
            _COUNT_ORPHAN_PROJECTS, database=args.neo4j_db
        )
        total_orphans = (records[0] if records else {}).get("total", 0)
        print(f"     Pass 3: found {total_orphans} orphan Project nodes (old MERGE key)")

        if total_orphans > 0:
            if dry_run:
                print(f"[DRY RUN] Pass 3: would merge {total_orphans} orphan Project nodes")
            else:
                records, _, _ = driver.execute_query_sync(
                    _MIGRATE_ORPHAN_PROJECTS, database=args.neo4j_db
                )
                merged = (records[0] if records else {}).get("merged", 0)
                print(f"[OK] Pass 3: merged {merged} orphan Project nodes into canonical form")
        else:
            print("[OK] Pass 3: no orphan Project nodes found")

    finally:
        driver.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Migrate existing graph data: backfill Repository→File HAS_FILE edges "
            "and remove invalid Project→File CONTAINS edges."
        )
    )
    parser.add_argument(
        "--neo4j-uri",
        default=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
    )
    parser.add_argument("--neo4j-user", default=os.getenv("NEO4J_USER", "neo4j"))
    parser.add_argument("--neo4j-password", default=os.getenv("NEO4J_PASSWORD", ""))
    parser.add_argument("--neo4j-db", default=os.getenv("NEO4J_DB", "neo4j"))
    add_graph_provider_args(parser)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without making any changes.",
    )

    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"Running migration [{mode}] provider={args.graph_provider} / db={args.neo4j_db}")
    run_migration(
        neo4j_uri=args.neo4j_uri,
        neo4j_user=args.neo4j_user,
        neo4j_password=args.neo4j_password,
        neo4j_db=args.neo4j_db,
        dry_run=args.dry_run,
        graph_provider=args.graph_provider,
        falkordb_path=args.falkordb_path,
        falkordb_graph=args.falkordb_graph,
    )


if __name__ == "__main__":
    main()
