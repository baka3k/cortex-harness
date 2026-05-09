"""Frontend ↔ Backend API Contract Bridge — V2.0

Upgrades from V1 (path + method only) to a **5-signal ensemble matcher**.
See ``tools/common/api_match_engine.py`` for the full scoring design.

Key changes vs V1
─────────────────
* Uses ``MultiSignalMatcher`` from api_match_engine — 5 weighted signals.
* Enriched Neo4j queries: joins Function metadata (name, intent, file_path)
  onto ApiCall nodes, and handler/controller metadata onto ApiEndpoint nodes.
* MATCHES relationship now carries per-signal breakdown for explainability.
* PathIndex: O(k) candidate retrieval instead of O(n×m) brute-force.
* New ``--explain`` CLI flag to print full signal breakdown per match.

Usage (CLI)
-----------
    python -m tools.ts.ts_api_bridge \\
        --fe-project my-frontend \\
        --be-project my-backend \\
        [--min-confidence 0.5] \\
        [--explain] \\
        [--dry-run] \\
        [--verbose]

Relationship written
--------------------
    (ac:ApiCall)-[:MATCHES {
        confidence,  match_type,  fe_project,  be_project,
        sig_path,    sig_method,  sig_name,    sig_module,  sig_pathvar
    }]->(ep:ApiEndpoint)

Match types (confidence tiers):
    "exact"      — raw ≥ 0.90
    "strong"     — raw ∈ [0.72, 0.90)
    "structural" — raw ∈ [0.52, 0.72)
    "weak"       — raw ∈ [min_conf, 0.52)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any, Dict, List

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.common.url_normalizer import normalize_http_method
from tools.common.api_match_engine import (
    ApiCallEnriched,
    ApiEndpointEnriched,
    MatchResult,
    MultiSignalMatcher,
)
from tools.graph import GraphDriverFactory, GraphProvider


# ─────────────────────────────────────────────────────────────────────────────
# Enriched Cypher queries (V2)
# ─────────────────────────────────────────────────────────────────────────────

# Load ApiCall nodes enriched with one representative caller Function.
_LOAD_API_CALLS_ENRICHED = """
MATCH (ac:ApiCall {project_id: $project_id})
WHERE ac.url_pattern IS NOT NULL AND ac.url_pattern <> ''
OPTIONAL MATCH (f:Function)-[:CALLS_API]->(ac)
WITH ac, collect(f)[0] AS f
RETURN ac.symbol_id      AS symbol_id,
       ac.url_pattern    AS url_pattern,
       ac.http_method    AS http_method,
       ac.project_id     AS project_id,
       ac.file_path      AS file_path,
       coalesce(f.name, '')       AS function_name,
       coalesce(f.intent, '')     AS function_intent,
       coalesce(f.react_role, '') AS react_role
"""

# Load ApiEndpoint nodes with handler/controller metadata.
_LOAD_API_ENDPOINTS_ENRICHED = """
MATCH (ep:ApiEndpoint {project_id: $project_id})
WHERE ep.path IS NOT NULL AND ep.path <> ''
RETURN ep.symbol_id                          AS symbol_id,
       ep.path                               AS path,
       ep.http_method                        AS http_method,
       ep.project_id                         AS project_id,
       coalesce(ep.file_path, '')            AS file_path,
       coalesce(ep.handler_names, [])        AS handler_names,
       coalesce(ep.controller_class, '')     AS controller_class,
       coalesce(ep.framework, '')            AS framework
"""

_DELETE_OLD_MATCHES = """
MATCH (ac:ApiCall {project_id: $fe_project_id})
      -[r:MATCHES]->
      (ep:ApiEndpoint {project_id: $be_project_id})
DELETE r
"""

# Upsert MATCHES with full signal breakdown stored on the relationship.
_UPSERT_MATCHES_V2 = """
UNWIND $rows AS row
MATCH (ac:ApiCall     {symbol_id: row.ac_id})
MATCH (ep:ApiEndpoint {symbol_id: row.ep_id})
MERGE (ac)-[r:MATCHES]->(ep)
SET r.confidence  = row.confidence,
    r.match_type  = row.match_type,
    r.fe_project  = row.fe_project,
    r.be_project  = row.be_project,
    r.sig_path    = row.sig_path,
    r.sig_method  = row.sig_method,
    r.sig_name    = row.sig_name,
    r.sig_module  = row.sig_module,
    r.sig_pathvar = row.sig_pathvar
"""


# ─────────────────────────────────────────────────────────────────────────────
# Data loading helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_api_calls(records: List[Any]) -> List[ApiCallEnriched]:
    calls: List[ApiCallEnriched] = []
    for r in records:
        sid = r.get("symbol_id")
        url = r.get("url_pattern") or ""
        if not sid or not url:
            continue
        calls.append(ApiCallEnriched(
            symbol_id=sid,
            url_pattern=url,
            http_method=normalize_http_method(r.get("http_method")),
            project_id=r.get("project_id") or "",
            file_path=r.get("file_path") or "",
            function_name=r.get("function_name") or "",
            function_intent=r.get("function_intent") or "",
            react_role=r.get("react_role") or "",
        ))
    return calls


def _build_api_endpoints(records: List[Any]) -> List[ApiEndpointEnriched]:
    eps: List[ApiEndpointEnriched] = []
    for r in records:
        sid = r.get("symbol_id")
        path = r.get("path") or ""
        if not sid or not path:
            continue
        raw_handlers = r.get("handler_names") or []
        handlers = raw_handlers if isinstance(raw_handlers, list) else [raw_handlers]
        eps.append(ApiEndpointEnriched(
            symbol_id=sid,
            path=path,
            http_method=normalize_http_method(r.get("http_method")),
            project_id=r.get("project_id") or "",
            file_path=r.get("file_path") or "",
            handler_names=handlers,
            controller_class=r.get("controller_class") or "",
            framework=r.get("framework") or "",
        ))
    return eps





# ─────────────────────────────────────────────────────────────────────────────
# Main async linker  (V2)
# ─────────────────────────────────────────────────────────────────────────────

async def link_api_calls_to_endpoints(
    *,
    fe_project_id: str,
    be_project_id: str,
    driver: Any,
    database: str,
    min_confidence: float = 0.50,
    batch_size: int = 200,
    dry_run: bool = False,
    verbose: bool = False,
    explain: bool = False,
) -> Dict[str, Any]:
    """Connect ``ApiCall`` nodes to ``ApiEndpoint`` nodes using the V2 multi-signal matcher.

    Returns a stats dict::

        {
            "api_calls":     int,
            "api_endpoints": int,
            "matches":       int,
            "by_tier": {"exact": int, "strong": int, "structural": int, "weak": int},
        }
    """
    # --- Load enriched ApiCall nodes (FE) ------------------------------------
    records_calls, _, _ = await driver.execute_query(
        _LOAD_API_CALLS_ENRICHED, {"project_id": fe_project_id}, database
    )
    api_calls = _build_api_calls(records_calls)

    # --- Load enriched ApiEndpoint nodes (BE) --------------------------------
    records_eps, _, _ = await driver.execute_query(
        _LOAD_API_ENDPOINTS_ENRICHED, {"project_id": be_project_id}, database
    )
    api_endpoints = _build_api_endpoints(records_eps)

    if verbose:
        print(
            f"[api-bridge] Loaded {len(api_calls)} ApiCall nodes (FE: {fe_project_id}), "
            f"{len(api_endpoints)} ApiEndpoint nodes (BE: {be_project_id})"
        )

    # --- Multi-signal matching ------------------------------------------------
    matcher = MultiSignalMatcher(api_endpoints, min_confidence=min_confidence)
    matches: List[MatchResult] = matcher.match_all(api_calls, fe_project_id, be_project_id)

    # --- Tier breakdown -------------------------------------------------------
    by_tier: Dict[str, int] = {"exact": 0, "strong": 0, "structural": 0, "weak": 0}
    for m in matches:
        by_tier[m.match_type] = by_tier.get(m.match_type, 0) + 1

    if verbose or explain:
        print(
            f"[api-bridge] {len(matches)} matches "
            f"(exact={by_tier['exact']} strong={by_tier['strong']} "
            f"structural={by_tier['structural']} weak={by_tier['weak']}) "
            f"confidence >= {min_confidence}"
        )

    if explain:
        _HDR = (
            f"{'TIER':<12} {'CONF':>6}  "
            f"{'SIG_PATH':>8} {'SIG_MTH':>7} {'SIG_NAME':>8} {'SIG_MOD':>7} {'SIG_PV':>6}  "
            f"FE-CALL → BE-ENDPOINT"
        )
        print(_HDR)
        print("─" * len(_HDR))
        for m in sorted(matches, key=lambda x: -x.confidence)[:50]:
            s = m.signals
            print(
                f"{m.match_type:<12} {m.confidence:>6.3f}  "
                f"{s.path:>8.3f} {s.method:>7.3f} {s.name:>8.3f} {s.module:>7.3f} {s.pathvar:>6.3f}  "
                f"{m.ac_id[:28]}… → {m.ep_id[:28]}…"
            )
        if len(matches) > 50:
            print(f"  … and {len(matches) - 50} more (use --verbose to see all)")

    if dry_run:
        return {
            "api_calls": len(api_calls),
            "api_endpoints": len(api_endpoints),
            "matches": len(matches),
            "by_tier": by_tier,
        }

    # --- Delete stale MATCHES ------------------------------------------------
    await driver.execute_query(
        _DELETE_OLD_MATCHES,
        {"fe_project_id": fe_project_id, "be_project_id": be_project_id},
        database,
    )

    # --- Upsert new MATCHES with signal breakdown ----------------------------
    rows = [
        {
            "ac_id":       m.ac_id,
            "ep_id":       m.ep_id,
            "confidence":  m.confidence,
            "match_type":  m.match_type,
            "fe_project":  m.fe_project,
            "be_project":  m.be_project,
            "sig_path":    m.signals.path,
            "sig_method":  m.signals.method,
            "sig_name":    m.signals.name,
            "sig_module":  m.signals.module,
            "sig_pathvar": m.signals.pathvar,
        }
        for m in matches
    ]
    for i in range(0, len(rows), batch_size):
        chunk = rows[i: i + batch_size]
        try:
            await driver.execute_query(_UPSERT_MATCHES_V2, {"rows": chunk}, database)
        except Exception as exc:
            if verbose:
                print(f"[api-bridge] MATCHES write error (batch {i}): {exc}")

    if verbose:
        print(f"[api-bridge] MATCHES written: {len(matches)}")

    return {
        "api_calls":     len(api_calls),
        "api_endpoints": len(api_endpoints),
        "matches":       len(matches),
        "by_tier":       by_tier,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

async def _main() -> None:
    p = argparse.ArgumentParser(
        description=(
            "V2 — Link frontend ApiCall nodes to backend ApiEndpoint nodes "
            "via MATCHES using the multi-signal ensemble engine."
        )
    )
    p.add_argument("--fe-project",     required=True,  help="Frontend project_id")
    p.add_argument("--be-project",     required=True,  help="Backend project_id")
    p.add_argument("--min-confidence", type=float, default=0.50,
                   help="Minimum confidence to create a MATCHES edge (default: 0.50)")
    p.add_argument("--neo4j-uri",      default=os.environ.get("NEO4J_URI"))
    p.add_argument("--neo4j-user",     default=os.environ.get("NEO4J_USER"))
    p.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASS"))
    p.add_argument("--neo4j-db",       default=os.environ.get("NEO4J_DB", "neo4j"))
    p.add_argument("--dry-run",  action="store_true",
                   help="Score matches but do not write to Neo4j")
    p.add_argument("--verbose",  action="store_true",
                   help="Print summary statistics")
    p.add_argument("--explain",  action="store_true",
                   help="Print per-signal score breakdown for every match")
    args = p.parse_args()

    if not args.neo4j_uri:
        print("[api-bridge] ERROR: --neo4j-uri or NEO4J_URI required", file=sys.stderr)
        sys.exit(1)

    driver_factory = GraphDriverFactory(
        uri=args.neo4j_uri,
        user=args.neo4j_user or "",
        password=args.neo4j_password or "",
        provider=GraphProvider.NEO4J,
    )
    driver = await driver_factory.create()
    try:
        stats = await link_api_calls_to_endpoints(
            fe_project_id=args.fe_project,
            be_project_id=args.be_project,
            driver=driver,
            database=args.neo4j_db,
            min_confidence=args.min_confidence,
            dry_run=args.dry_run,
            verbose=args.verbose or args.explain,
            explain=args.explain,
        )
        tier = stats["by_tier"]
        print(
            f"[api-bridge] Done. api_calls={stats['api_calls']} "
            f"api_endpoints={stats['api_endpoints']} matches={stats['matches']} "
            f"(exact={tier['exact']} strong={tier['strong']} "
            f"structural={tier['structural']} weak={tier['weak']})"
        )
    finally:
        await driver.close()


if __name__ == "__main__":
    asyncio.run(_main())
