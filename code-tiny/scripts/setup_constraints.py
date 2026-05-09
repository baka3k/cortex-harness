"""setup_constraints.py

One-time (idempotent) migration script that adds uniqueness constraints to
Neo4j so that concurrent MERGE operations are safe under parallelism.

WHY THIS IS NECESSARY
---------------------
Without a uniqueness constraint, Neo4j MERGE is NOT atomic under concurrency:

    Job A: check Project {project_id: "x"} exists → NO → CREATE
    Job B: check Project {project_id: "x"} exists → NO → CREATE
    Result: two Project nodes with the same project_id!

With a UNIQUE constraint, Neo4j acquires an exclusive index lock before the
MERGE decides to CREATE.  Concurrent jobs queue at the lock and all converge
on the SAME node — no application-level mutex or distributed lock required.

USAGE
-----
Run once after initial setup or when adding a new database:

    python setup_constraints.py
    python setup_constraints.py --neo4j-uri bolt://host:7687 --neo4j-db mydb

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
        print(f"[setup_constraints] warning: could not decrypt NEO4J_PASS ({exc})", file=sys.stderr)
        return password


try:
    from neo4j import GraphDatabase
    from neo4j.exceptions import ClientError
except ImportError:
    print("ERROR: neo4j Python driver not installed.  Run: pip install neo4j", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------

# Constraints to DROP before (re-)creating — handles renaming of the MERGE key.
# Uses DROP … IF EXISTS so re-running is safe.
DROP_CONSTRAINTS: list[tuple[str, str]] = [
    # Old/incorrect variants may have used p.id; we standardize on p.project_id.
    # Drop by name first so the CREATE below can enforce the canonical property.
    (
        "unique_project_id",
        "DROP CONSTRAINT unique_project_id IF EXISTS",
    ),
]

# Each tuple: (constraint_name, cypher_statement)
# All statements use IF NOT EXISTS so re-running is safe.
CONSTRAINTS: list[tuple[str, str]] = [
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
    (
        "unique_navigator_id",
        (
            "CREATE CONSTRAINT unique_navigator_id IF NOT EXISTS "
            "FOR (n:Navigator) REQUIRE n.id IS UNIQUE"
        ),
    ),
    (
        "unique_route_param_id",
        (
            "CREATE CONSTRAINT unique_route_param_id IF NOT EXISTS "
            "FOR (p:RouteParam) REQUIRE p.id IS UNIQUE"
        ),
    ),
]

INDEXES: list[tuple[str, str]] = [
    (
        "type_id_lookup",
        (
            "CREATE INDEX type_id_lookup IF NOT EXISTS "
            "FOR (t:Type) ON (t.id)"
        ),
    ),
    (
        "package_id_lookup",
        (
            "CREATE INDEX package_id_lookup IF NOT EXISTS "
            "FOR (p:Package) ON (p.id)"
        ),
    ),
    (
        "field_id_lookup",
        (
            "CREATE INDEX field_id_lookup IF NOT EXISTS "
            "FOR (f:Field) ON (f.id)"
        ),
    ),
    (
        "alias_id_lookup",
        (
            "CREATE INDEX alias_id_lookup IF NOT EXISTS "
            "FOR (a:Alias) ON (a.id)"
        ),
    ),
    (
        "template_id_lookup",
        (
            "CREATE INDEX template_id_lookup IF NOT EXISTS "
            "FOR (t:Template) ON (t.id)"
        ),
    ),
    (
        "function_type_id_lookup",
        (
            "CREATE INDEX function_type_id_lookup IF NOT EXISTS "
            "FOR (ft:FunctionType) ON (ft.id)"
        ),
    ),
    (
        "message_id_lookup",
        (
            "CREATE INDEX message_id_lookup IF NOT EXISTS "
            "FOR (m:Message) ON (m.id)"
        ),
    ),
    (
        "message_endpoint_id_lookup",
        (
            "CREATE INDEX message_endpoint_id_lookup IF NOT EXISTS "
            "FOR (m:MessageEndpoint) ON (m.id)"
        ),
    ),
    (
        "infra_node_id_lookup",
        (
            "CREATE INDEX infra_node_id_lookup IF NOT EXISTS "
            "FOR (i:InfraNode) ON (i.id)"
        ),
    ),
    (
        "project_id_lookup",
        (
            "CREATE INDEX project_id_lookup IF NOT EXISTS "
            "FOR (p:Project) ON (p.id)"
        ),
    ),
    (
        "repository_id_lookup",
        (
            "CREATE INDEX repository_id_lookup IF NOT EXISTS "
            "FOR (r:Repository) ON (r.id)"
        ),
    ),
    (
        "paragraph_id_lookup",
        (
            "CREATE INDEX paragraph_id_lookup IF NOT EXISTS "
            "FOR (p:Paragraph) ON (p.id)"
        ),
    ),
    (
        "chunk_id_lookup",
        (
            "CREATE INDEX chunk_id_lookup IF NOT EXISTS "
            "FOR (c:Chunk) ON (c.id)"
        ),
    ),
    (
        "slide_id_lookup",
        (
            "CREATE INDEX slide_id_lookup IF NOT EXISTS "
            "FOR (s:Slide) ON (s.id)"
        ),
    ),
    (
        "android_manifest_id_lookup",
        (
            "CREATE INDEX android_manifest_id_lookup IF NOT EXISTS "
            "FOR (a:AndroidManifest) ON (a.id)"
        ),
    ),
    (
        "android_component_id_lookup",
        (
            "CREATE INDEX android_component_id_lookup IF NOT EXISTS "
            "FOR (a:AndroidComponent) ON (a.id)"
        ),
    ),
    (
        "android_resource_id_lookup",
        (
            "CREATE INDEX android_resource_id_lookup IF NOT EXISTS "
            "FOR (a:AndroidResource) ON (a.id)"
        ),
    ),
    (
        "gradle_module_id_lookup",
        (
            "CREATE INDEX gradle_module_id_lookup IF NOT EXISTS "
            "FOR (g:GradleModule) ON (g.id)"
        ),
    ),
    (
        "android_intent_action_id_lookup",
        (
            "CREATE INDEX android_intent_action_id_lookup IF NOT EXISTS "
            "FOR (a:AndroidIntentAction) ON (a.id)"
        ),
    ),
    (
        "android_annotation_id_lookup",
        (
            "CREATE INDEX android_annotation_id_lookup IF NOT EXISTS "
            "FOR (a:AndroidAnnotation) ON (a.id)"
        ),
    ),
    (
        "api_endpoint_path_method_project_idx",
        (
            "CREATE INDEX api_endpoint_path_method_project_idx IF NOT EXISTS "
            "FOR (ep:ApiEndpoint) ON (ep.path, ep.http_method, ep.project_id)"
        ),
    ),
    (
        "api_endpoint_symbol_id_idx",
        (
            "CREATE INDEX api_endpoint_symbol_id_idx IF NOT EXISTS "
            "FOR (ep:ApiEndpoint) ON (ep.symbol_id)"
        ),
    ),
    (
        "api_call_symbol_id_idx",
        (
            "CREATE INDEX api_call_symbol_id_idx IF NOT EXISTS "
            "FOR (ac:ApiCall) ON (ac.symbol_id)"
        ),
    ),
    (
        "api_call_project_id_idx",
        (
            "CREATE INDEX api_call_project_id_idx IF NOT EXISTS "
            "FOR (ac:ApiCall) ON (ac.project_id)"
        ),
    ),
    (
        "controller_symbol_id_idx",
        (
            "CREATE INDEX controller_symbol_id_idx IF NOT EXISTS "
            "FOR (c:Controller) ON (c.symbol_id)"
        ),
    ),
    (
        "service_symbol_id_idx",
        (
            "CREATE INDEX service_symbol_id_idx IF NOT EXISTS "
            "FOR (s:Service) ON (s.symbol_id)"
        ),
    ),
    (
        "database_symbol_id_idx",
        (
            "CREATE INDEX database_symbol_id_idx IF NOT EXISTS "
            "FOR (d:Database) ON (d.symbol_id)"
        ),
    ),
    (
        "data_repository_symbol_id_idx",
        (
            "CREATE INDEX data_repository_symbol_id_idx IF NOT EXISTS "
            "FOR (r:DataRepository) ON (r.symbol_id)"
        ),
    ),
    (
        "message_project_id_idx",
        (
            "CREATE INDEX message_project_id_idx IF NOT EXISTS "
            "FOR (m:Message) ON (m.project_id)"
        ),
    ),
]

FULLTEXT_INDEXES: list[tuple[str, str]] = [
    (
        "mcp_symbol_text_ft",
        (
            "CREATE FULLTEXT INDEX mcp_symbol_text_ft IF NOT EXISTS "
            "FOR (n:Function|Class|Type|Namespace|Package|File|Field|Alias|Template|FunctionType|Event|Project|"
            "Property|Interface|Enum|Constant|Variable|UnknownFunction|Message|MessageEndpoint|"
            "AndroidManifest|AndroidComponent|AndroidResource|GradleModule|GradleDependency|AndroidAnnotation|"
            "AndroidNavRoute|AndroidIntentAction|AndroidHandlerMessage|ApiEndpoint|ApiCall|Controller|Service|"
            "Database|DataRepository|Middleware) "
            "ON EACH [n.name, n.qualified_name, n.file_path, n.path, n.package_name, n.class_name, n.module_path, "
            "n.namespace, n.application_id, n.coordinate, n.group, n.artifact, n.version, n.res_type, "
            "n.component_type, n.route, n.action, n.token, n.http_method, n.url_pattern]"
        ),
    ),
    (
        "mcp_symbol_code_ft",
        (
            "CREATE FULLTEXT INDEX mcp_symbol_code_ft IF NOT EXISTS "
            "FOR (n:Function|Class|Type|Namespace|Package|File|Field|Alias|Template|FunctionType|Event|Project|"
            "Property|Interface|Enum|Constant|Variable|UnknownFunction|Message|"
            "AndroidManifest|AndroidComponent|AndroidResource|GradleModule|GradleDependency|AndroidAnnotation|"
            "AndroidNavRoute|AndroidIntentAction|AndroidHandlerMessage|ApiEndpoint|ApiCall|Controller|Service|"
            "Database|DataRepository|Middleware) "
            "ON EACH [n.code, n.comment, n.summary, n.note, n.payload, n.response, n.explanation]"
        ),
    ),
]

BACKFILL_PROJECT_ID = (
    "MATCH (p:Project) "
    "WHERE p.project_id IS NULL AND p.id IS NOT NULL "
    "SET p.project_id = p.id "
    "RETURN count(p) AS count"
)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def apply_constraints(
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    neo4j_db: str,
) -> None:
    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, _maybe_decrypt_password(neo4j_password)))
    ok = 0
    skipped = 0
    backfilled = 0
    index_ok = 0
    index_skipped = 0
    fulltext_ok = 0
    fulltext_skipped = 0

    try:
        with driver.session(database=neo4j_db) as session:
            for name, stmt in DROP_CONSTRAINTS:
                try:
                    session.run(stmt)
                    print(f"  [DROPPED] {name}")
                except ClientError as exc:
                    print(f"  [SKIP-DROP] {name}  ({exc.code})")

            try:
                backfilled = int(session.run(BACKFILL_PROJECT_ID).single()["count"])
                print(f"  [OK]      backfill_project_id ({backfilled} node(s))")
            except ClientError as exc:
                print(f"  [SKIPPED] backfill_project_id  ({exc.code})")

            for name, stmt in CONSTRAINTS:
                try:
                    session.run(stmt)
                    print(f"  [OK]      {name}")
                    ok += 1
                except ClientError as exc:
                    # Constraint already exists, or Neo4j version does not support
                    # IF NOT EXISTS syntax — not fatal, schema is already correct.
                    print(f"  [SKIPPED] {name}  ({exc.code})")
                    skipped += 1

            for name, stmt in INDEXES:
                try:
                    session.run(stmt)
                    print(f"  [OK]      {name}")
                    index_ok += 1
                except ClientError as exc:
                    print(f"  [SKIPPED] {name}  ({exc.code})")
                    index_skipped += 1

            for name, stmt in FULLTEXT_INDEXES:
                try:
                    session.run(stmt)
                    print(f"  [OK]      {name}")
                    fulltext_ok += 1
                except ClientError as exc:
                    print(f"  [SKIPPED] {name}  ({exc.code})")
                    fulltext_skipped += 1
    finally:
        driver.close()

    print(
        "\nDone: "
        f"constraints {ok} applied/{skipped} skipped, "
        f"indexes {index_ok} applied/{index_skipped} skipped, "
        f"fulltext {fulltext_ok} applied/{fulltext_skipped} skipped, "
        f"{backfilled} backfilled."
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply Neo4j uniqueness constraints required for safe concurrent scanning."
    )
    parser.add_argument(
        "--neo4j-uri",
        default=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        help="Neo4j Bolt URI (default: bolt://localhost:7687).",
    )
    parser.add_argument(
        "--neo4j-user",
        default=os.getenv("NEO4J_USER", "neo4j"),
        help="Neo4j username.",
    )
    parser.add_argument(
        "--neo4j-password",
        default=os.getenv("NEO4J_PASSWORD", ""),
        help="Neo4j password.",
    )
    parser.add_argument(
        "--neo4j-db",
        default=os.getenv("NEO4J_DB", "neo4j"),
        help="Neo4j database name.",
    )

    args = parser.parse_args()

    print(f"Applying constraints to {args.neo4j_uri} / db={args.neo4j_db}")
    apply_constraints(
        neo4j_uri=args.neo4j_uri,
        neo4j_user=args.neo4j_user,
        neo4j_password=args.neo4j_password,
        neo4j_db=args.neo4j_db,
    )


if __name__ == "__main__":
    main()
