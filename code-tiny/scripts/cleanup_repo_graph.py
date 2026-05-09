"""cleanup_repo_graph.py

Delete graph data for one repository scope before overwrite scan.

Scope key: (project_id, repo_name)

Usage:
    python cleanup_repo_graph.py \
        --project-id <id> \
        --repo-name <project/repo> \
        [--neo4j-uri bolt://localhost:7687] \
        [--neo4j-user neo4j] \
        [--neo4j-password <pw>] \
        [--neo4j-db neo4j]
"""

from __future__ import annotations

import argparse
import base64
import os
import random
import re
import sys
import time

from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, TransientError

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
        print(f"[cleanup_repo_graph] warning: could not decrypt NEO4J_PASS ({exc})", file=sys.stderr)
        return password

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


def _run_with_retry(session, query: str, **params):
    for attempt in range(_MAX_RETRIES):
        try:
            return session.run(query, **params)
        except (TransientError, ServiceUnavailable) as exc:
            if attempt >= _MAX_RETRIES - 1:
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
) -> tuple[int, int]:
    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, _maybe_decrypt_password(neo4j_password)))
    try:
        with driver.session(database=neo4j_db) as session:
            deleted_nodes_result = _run_with_retry(
                session,
                _DELETE_REPO_SCOPED_QUERY,
                project_id=project_id,
                repo_name=repo_name,
            )
            deleted_nodes_record = deleted_nodes_result.single()
            deleted_nodes = int((deleted_nodes_record or {}).get("deleted_nodes", 0))

            deleted_unknown_result = _run_with_retry(
                session,
                _DELETE_ORPHAN_UNKNOWN_FUNCTIONS,
            )
            deleted_unknown_record = deleted_unknown_result.single()
            deleted_unknown = int((deleted_unknown_record or {}).get("deleted_unknown", 0))

            return deleted_nodes, deleted_unknown
    finally:
        driver.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cleanup Neo4j graph data for one repository scope (project_id + repo_name)."
    )
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--repo-name", required=True)
    parser.add_argument("--neo4j-uri", default=os.getenv("NEO4J_URI", "bolt://localhost:7687"))
    parser.add_argument("--neo4j-user", default=os.getenv("NEO4J_USER", "neo4j"))
    parser.add_argument("--neo4j-password", default=os.getenv("NEO4J_PASS", ""))
    parser.add_argument("--neo4j-db", default=os.getenv("NEO4J_DB", "neo4j"))
    args = parser.parse_args()

    try:
        deleted_nodes, deleted_unknown = cleanup_repo_graph(
            project_id=args.project_id,
            repo_name=args.repo_name,
            neo4j_uri=args.neo4j_uri,
            neo4j_user=args.neo4j_user,
            neo4j_password=args.neo4j_password,
            neo4j_db=args.neo4j_db,
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
