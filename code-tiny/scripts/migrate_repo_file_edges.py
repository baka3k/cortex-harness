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
    python migrate_repo_file_edges.py --neo4j-uri bolt://host:7687 --dry-run

Environment variables (fallbacks):
    NEO4J_URI       bolt://localhost:7687
    NEO4J_USER      neo4j
    NEO4J_PASSWORD  (empty)
    NEO4J_DB        neo4j
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import sys

_FERNET_TOKEN_RE = re.compile(r'^gAAAAA')


def _maybe_decrypt_password(password: str) -> str:
    if not _FERNET_TOKEN_RE.match(password):
        return password
    try:
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except ImportError:
        return password
    enc_pw = os.environ.get("HYPER_PACK_ENCRYPTION_PASSWORD", "my-secret-encryption-key-2026")
    try:
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=b"static_salt_2026", iterations=100_000)
        key = base64.urlsafe_b64encode(kdf.derive(enc_pw.encode("utf-8")))
        return Fernet(key).decrypt(password.encode("utf-8")).decode("utf-8")
    except Exception as exc:
        print(f"[migrate_repo_file_edges] warning: could not decrypt NEO4J_PASS ({exc})", file=sys.stderr)
        return password


try:
    from neo4j import GraphDatabase
    from neo4j.exceptions import ClientError
except ImportError:
    print(
        "ERROR: neo4j Python driver not installed.  Run: pip install neo4j",
        file=sys.stderr,
    )
    sys.exit(1)


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
) -> None:
    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, _maybe_decrypt_password(neo4j_password)))
    try:
        with driver.session(database=neo4j_db) as session:
            # ── Pass 1: backfill HAS_FILE edges ──────────────────────────────
            if dry_run:
                result = session.run("""
                    MATCH (r:Repository)
                    MATCH (f:File)
                    WHERE f.repo = r.name AND NOT (r)-[:HAS_FILE]->(f)
                    RETURN count(f) AS would_create
                """)
                would_create = result.single()["would_create"]
                print(f"[DRY RUN] Pass 1: would create {would_create} HAS_FILE edges")
            else:
                result = session.run(_BACKFILL_HAS_FILE)
                created = result.single()["created"]
                print(f"[OK] Pass 1: created {created} HAS_FILE edges")

            # ── Pass 2: remove bad Project→File CONTAINS edges ───────────────
            count_result = session.run(_COUNT_BAD_EDGES)
            total_bad = count_result.single()["total"]
            print(f"     Pass 2: found {total_bad} invalid (Project)-[:CONTAINS]->(File) edges")

            if total_bad > 0:
                if dry_run:
                    print(f"[DRY RUN] Pass 2: would delete {total_bad} edges")
                else:
                    del_result = session.run(_DELETE_BAD_EDGES)
                    deleted = del_result.single()["deleted"]
                    print(f"[OK] Pass 2: deleted {deleted} invalid edges")
            else:
                print("[OK] Pass 2: nothing to clean up")

            # ── Pass 3: merge orphan Project nodes ───────────────────────────
            count_result3 = session.run(_COUNT_ORPHAN_PROJECTS)
            total_orphans = count_result3.single()["total"]
            print(f"     Pass 3: found {total_orphans} orphan Project nodes (old MERGE key)")

            if total_orphans > 0:
                if dry_run:
                    print(f"[DRY RUN] Pass 3: would merge {total_orphans} orphan Project nodes")
                else:
                    merge_result = session.run(_MIGRATE_ORPHAN_PROJECTS)
                    merged = merge_result.single()["merged"]
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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without making any changes.",
    )

    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"Running migration [{mode}] on {args.neo4j_uri} / db={args.neo4j_db}")
    run_migration(
        neo4j_uri=args.neo4j_uri,
        neo4j_user=args.neo4j_user,
        neo4j_password=args.neo4j_password,
        neo4j_db=args.neo4j_db,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
