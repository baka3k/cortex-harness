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
        [--neo4j-uri   bolt://...]     \\
        [--neo4j-user  neo4j]          \\
        [--neo4j-password ...]         \\
        [--neo4j-db    neo4j]
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import sys
import time

from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, TransientError

# ---------------------------------------------------------------------------
# Password decryption helper (shared pattern with setup_graph_project.py)
# ---------------------------------------------------------------------------

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
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"static_salt_2026",
            iterations=100_000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(enc_pw.encode("utf-8")))
        return Fernet(key).decrypt(password.encode("utf-8")).decode("utf-8")
    except Exception as exc:
        print(
            f"[link_project_repos] warning: could not decrypt NEO4J_PASS ({exc}); "
            "using value as-is",
            file=sys.stderr,
        )
        return password

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
) -> int:
    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, _maybe_decrypt_password(neo4j_pass)))
    try:
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                with driver.session(database=neo4j_db) as session:
                    result = session.run(
                        _LINK_REPOS_QUERY,
                        project_id=project_id,
                        project_name=project_name,
                        project_slug=project_slug,
                    )
                    linked = result.single()["linked"]
                    print(
                        f"[link_project_repos] OK  "
                        f"project={project_id!r}  repos_linked={linked}"
                    )
                    return linked
            except TransientError as exc:
                if attempt < _MAX_RETRIES:
                    print(
                        f"[link_project_repos] TransientError (attempt {attempt}/{_MAX_RETRIES}): "
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
        default=os.getenv("NEO4J_PASS", "abcd1234"),
    )
    parser.add_argument(
        "--neo4j-db",
        default=os.getenv("NEO4J_DB", "neo4j"),
    )
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
    )


if __name__ == "__main__":
    main()
