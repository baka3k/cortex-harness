"""setup_graph_project.py

Creates Project -> HAS_REPOSITORY -> Repository hierarchy through the selected
Neo4j or FalkorDB provider.

Concurrency guarantee
---------------------
Unique constraints on Project.project_id and Repository.name make Neo4j MERGE
truly atomic: the DB acquires an exclusive index lock before deciding to CREATE,
so concurrent scan jobs that race on the same project_id serialise at the
database level — no application-level mutex or distributed lock is required.

ensure_constraints() is called once per invocation (idempotent) before any
MERGE, guaranteeing the schema is in place.

All three MERGEs (Project, Repository, and the HAS_REPOSITORY relationship) run
in a single write transaction so the structure is never partially created.

Retries with exponential back-off on TransientError cover the residual lock
timeouts that can still occur on relationship-level contention.

Usage:
    python setup_graph_project.py \\
        --project-id  <id>    \\
        --project-name <name> \\
        --source-path  <path> \\
        [--repo-name   <project/repo>]  \\
        [--neo4j-uri   bolt://localhost:7687] \\
        [--neo4j-user  neo4j] \\
        [--neo4j-password <pw>] \\
        [--neo4j-db    neo4j]
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import os
import random
import re
import sys
import time
from pathlib import Path

_CODE_ROOT = Path(__file__).resolve().parents[1]
if str(_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(_CODE_ROOT))

from scripts.setup_constraints import ensure_falkordb_unique_constraint
from tools.graph.cli import (
    add_graph_provider_args,
    create_graph_driver_from_args,
    normalize_graph_provider,
    prepare_graph_args,
)
from tools.graph.core.base import GraphProvider

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False

try:
    from neo4j import GraphDatabase
    from neo4j.exceptions import TransientError, ClientError
except ImportError:  # type: ignore[import]
    GraphDatabase = None  # type: ignore[assignment]
    TransientError = Exception  # type: ignore[assignment,misc]
    ClientError = Exception  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Password decryption helper
# ---------------------------------------------------------------------------

_FERNET_PREFIX_RE = re.compile(r'^gAAAAA')


def _maybe_decrypt_password(password: str) -> str:
    """If *password* looks like a Fernet token and HYPER_PACK_ENCRYPTION_PASSWORD
    is set, attempt decryption and return the plain-text password.
    Falls back to the original value if crypto is unavailable or decryption fails.
    """
    if not _CRYPTO_AVAILABLE or not _FERNET_PREFIX_RE.match(password):
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
        decrypted = Fernet(key).decrypt(password.encode("utf-8")).decode("utf-8")
        return decrypted
    except Exception as exc:
        print(
            f"[setup_graph_project] warning: could not decrypt NEO4J_PASS "
            f"({exc}); using value as-is (likely plain-text or wrong key)"
        )
        return password


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_RETRIES = 5
_BASE_BACKOFF = 0.15  # seconds

# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------


def _normalize_slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return cleaned or "project"


def _derive_repo_node_name(project_name: str, source_path: str) -> str:
    folder = (source_path or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or "repo"
    return f"{_normalize_slug(project_name)}/{_normalize_slug(folder)}"


# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------


def _run_with_retry(session, query: str, params: dict) -> None:
    for attempt in range(_MAX_RETRIES):
        try:
            session.run(query, **params)
            return
        except TransientError as exc:
            if attempt >= _MAX_RETRIES - 1:
                raise
            backoff = _BASE_BACKOFF * (2 ** attempt) + random.uniform(0, _BASE_BACKOFF)
            print(
                f"[setup_graph_project] TransientError "
                f"(attempt {attempt + 1}/{_MAX_RETRIES}), "
                f"retrying in {backoff:.2f}s — {exc}"
            )
            time.sleep(backoff)


# ---------------------------------------------------------------------------
# Schema constraints  (idempotent, safe to run on every invocation)
# ---------------------------------------------------------------------------

_CONSTRAINT_STMTS = [
    (
        "unique_project_id",
        (
            "CREATE CONSTRAINT unique_project_id IF NOT EXISTS "
            "FOR (p:Project) REQUIRE p.project_id IS UNIQUE"
        ),
    ),
    (
        "unique_repository_name",
        (
            "CREATE CONSTRAINT unique_repository_name IF NOT EXISTS "
            "FOR (r:Repository) REQUIRE r.name IS UNIQUE"
        ),
    ),
]


def ensure_constraints(driver, neo4j_db: str) -> None:
    """Create uniqueness constraints if they do not already exist.

    With these constraints in place, Neo4j MERGE becomes truly atomic under
    concurrency: the engine acquires an exclusive constraint-index lock before
    deciding to CREATE, eliminating the classic check-then-create race condition.
    Concurrent MERGE calls on the same project_id will queue at the lock layer
    and all converge on the single node rather than creating duplicates.
    """
    with driver.session(database=neo4j_db) as session:
        for name, stmt in _CONSTRAINT_STMTS:
            try:
                session.run(stmt)
                print(f"[setup_graph_project] constraint {name!r} ensured")
            except ClientError as exc:
                # Already exists, or syntax varies across Neo4j versions — not fatal.
                print(f"[setup_graph_project] constraint {name!r} skipped: {exc}")


_UPSERT_PROJECT_QUERY = """
MERGE (p:Project {project_id: $project_id})
ON CREATE SET
    p.name       = $project_name,
    p.slug       = $project_slug,
    p.created_at = timestamp()
ON MATCH SET
    p.name       = $project_name,
    p.slug       = $project_slug
WITH p
MERGE (r:Repository {name: $repo_name})
ON CREATE SET
    r.id          = $repo_name,
    r.project_id  = $project_id,
    r.created_at  = timestamp()
ON MATCH SET
    r.id          = $repo_name
WITH p, r
MERGE (p)-[:HAS_REPOSITORY]->(r)
"""


def setup_project_graph_with_driver(
    driver,
    *,
    provider: GraphProvider,
    project_id: str,
    project_name: str,
    repo_name: str,
    database: str,
    constraint_timeout: float = 30.0,
    poll_interval: float = 0.25,
) -> None:
    """Apply provider-native uniqueness schema and atomically upsert topology."""
    if provider == GraphProvider.FALKORDB:
        ensure_falkordb_unique_constraint(
            driver.graph,
            "Project",
            ("project_id",),
            timeout=constraint_timeout,
            interval=poll_interval,
        )
        ensure_falkordb_unique_constraint(
            driver.graph,
            "Repository",
            ("name",),
            timeout=constraint_timeout,
            interval=poll_interval,
        )
    else:
        ensure_constraints(driver, database)

    driver.execute_query_sync(
        _UPSERT_PROJECT_QUERY,
        {
            "project_id": project_id,
            "project_name": project_name,
            "project_slug": _normalize_slug(project_name),
            "repo_name": repo_name,
        },
        database,
    )
    print(
        f"[setup_graph_project] OK provider={provider.value} "
        f"project={project_id!r} repo={repo_name!r} db={database!r}"
    )


# ---------------------------------------------------------------------------
# Core operation
# ---------------------------------------------------------------------------


def setup_project_graph(
    project_id: str,
    project_name: str,
    repo_name: str,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    neo4j_db: str = "neo4j",
) -> None:
    """Atomically create (or retrieve) the Project -> Repository hierarchy.

    Concurrency safety
    ------------------
    Unique constraints on Project.project_id and Repository.name ensure that
    concurrent MERGE operations on the same key serialise at the DB index lock
    layer.  N parallel scan jobs for the same project will each wait their turn
    and all end up sharing exactly ONE Project node.

    Steps
    -----
    1. ensure_constraints — guarantee uniqueness schema is in place (idempotent).
    2. Single write transaction:
       a. MERGE Project      (unique constraint -> atomic under concurrency)
       b. MERGE Repository   (unique constraint -> atomic under concurrency)
       c. MERGE HAS_REPOSITORY relationship
    """
    if GraphDatabase is None:
        raise RuntimeError("neo4j Python driver is not installed (pip install neo4j)")

    project_slug = _normalize_slug(project_name)
    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
    try:
        # Step 1 — ensure constraints (idempotent; cheap on subsequent runs)
        ensure_constraints(driver, neo4j_db)

        # Step 2 — upsert hierarchy in a single transaction.
        # The unique constraints serialise concurrent MERGEs at the index lock
        # layer so N jobs racing on the same project_id all converge on ONE node.
        with driver.session(database=neo4j_db) as session:
            _run_with_retry(
                session,
                _UPSERT_PROJECT_QUERY,
                dict(
                    project_id=project_id,
                    project_name=project_name,
                    project_slug=project_slug,
                    repo_name=repo_name,
                ),
            )

        print(
            f"[setup_graph_project] OK  "
            f"project={project_id!r}  repo={repo_name!r}  db={neo4j_db!r}"
        )
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Establish a provider-neutral Project -> Repository hierarchy."
    )
    add_graph_provider_args(parser)
    parser.add_argument("--project-id", required=True, help="Unique project identifier.")
    parser.add_argument("--project-name", required=True, help="Human-readable project name.")
    parser.add_argument(
        "--source-path",
        default="",
        help="Source directory — used to derive repo node name.",
    )
    parser.add_argument(
        "--repo-name",
        default="",
        help=(
            "Explicit repo node name (project/repo). "
            "Derived from project-name + source-path folder if omitted."
        ),
    )
    parser.add_argument(
        "--neo4j-uri",
        default=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
    )
    parser.add_argument("--neo4j-user", default=os.getenv("NEO4J_USER", "neo4j"))
    parser.add_argument("--neo4j-password", default=os.getenv("NEO4J_PASS", ""))
    parser.add_argument("--neo4j-db", default=os.getenv("NEO4J_DB", "neo4j"))
    parser.add_argument(
        "--constraint-timeout",
        type=float,
        default=float(os.getenv("FALKORDB_CONSTRAINT_TIMEOUT", "30")),
    )
    parser.add_argument(
        "--constraint-poll-interval",
        type=float,
        default=float(os.getenv("FALKORDB_CONSTRAINT_POLL_INTERVAL", "0.25")),
    )

    args = parser.parse_args()
    repo_name = args.repo_name or _derive_repo_node_name(args.project_name, args.source_path)
    neo4j_password = _maybe_decrypt_password(args.neo4j_password)

    provider = normalize_graph_provider(args.graph_provider)
    if provider == GraphProvider.NEO4J:
        setup_project_graph(
            project_id=args.project_id,
            project_name=args.project_name,
            repo_name=repo_name,
            neo4j_uri=args.neo4j_uri,
            neo4j_user=args.neo4j_user,
            neo4j_password=neo4j_password,
            neo4j_db=args.neo4j_db,
        )
        return

    prepare_graph_args(args)

    async def _run_falkordb() -> None:
        driver = await create_graph_driver_from_args(args)
        if driver is None:
            raise RuntimeError("FalkorDB provider could not be configured")
        try:
            setup_project_graph_with_driver(
                driver,
                provider=provider,
                project_id=args.project_id,
                project_name=args.project_name,
                repo_name=repo_name,
                database=args.neo4j_db,
                constraint_timeout=args.constraint_timeout,
                poll_interval=args.constraint_poll_interval,
            )
        finally:
            driver.close()

    asyncio.run(_run_falkordb())


if __name__ == "__main__":
    main()
