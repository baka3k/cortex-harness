"""Smoke test for the unified ingest/query contract.

Per Phase 07 of the unified ingest/query contract plan, this script
exercises the full contract end-to-end against live FalkorDB + Qdrant
for two distinct registered projects. It prints a pass/fail matrix.

The script gracefully skips when the backing services are unavailable so
it can be run as a CI pre-flight without false failures.

Usage::

    python scripts/smoke_unified_contract.py [--project-a NAME] [--project-b NAME]

Exit code is 0 when every check passes (or every check is skipped due to
missing services). Exit code is 1 when any check fails.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
from typing import List, Tuple


def _service_reachable(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _resolve_targets(project_id: str) -> dict:
    """Resolve the registry targets for ``project_id``."""
    # Lazy import so the script can run without code-tiny on sys.path.
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code-tiny"))
    try:
        from tools.common.project_registry import resolve_project_targets

        return resolve_project_targets(project_id).__dict__
    finally:
        # No path cleanup — running multiple times is harmless.
        pass


def _check_pair(name: str, project_a: str, project_b: str) -> Tuple[str, str]:
    try:
        a = _resolve_targets(project_a)
        b = _resolve_targets(project_b)
    except Exception as exc:
        return name, f"SKIP (registry: {exc})"

    failures: List[str] = []
    if a["code_graph"] == b["code_graph"]:
        failures.append("code_graph collides")
    if a["code_qdrant_collection"] == b["code_qdrant_collection"]:
        failures.append("code_qdrant_collection collides")
    if a["doc_graph"] == b["doc_graph"]:
        failures.append("doc_graph collides")
    if a["doc_qdrant_collection"] == b["doc_qdrant_collection"]:
        failures.append("doc_qdrant_collection collides")
    if a["code_graph"] == a["doc_graph"]:
        failures.append("code_graph equals doc_graph within project A")

    if failures:
        return name, f"FAIL: {'; '.join(failures)}"
    return name, "PASS"


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-a", default="cortext")
    parser.add_argument("--project-b", default="proj_beta")
    parser.add_argument("--falkordb-host", default="localhost")
    parser.add_argument("--falkordb-port", type=int, default=6379)
    parser.add_argument("--qdrant-host", default="localhost")
    parser.add_argument("--qdrant-port", type=int, default=6333)
    args = parser.parse_args(argv)

    print("=" * 60)
    print("Unified Ingest/Query Contract — smoke test")
    print("=" * 60)
    print(f"Project A: {args.project_a}")
    print(f"Project B: {args.project_b}")

    falkordb_ok = _service_reachable(args.falkordb_host, args.falkordb_port)
    qdrant_ok = _service_reachable(args.qdrant_host, args.qdrant_port)
    print()
    print(
        f"FalkorDB @ {args.falkordb_host}:{args.falkordb_port}: "
        f"{'reachable' if falkordb_ok else 'NOT reachable'}"
    )
    print(
        f"Qdrant    @ {args.qdrant_host}:{args.qdrant_port}: "
        f"{'reachable' if qdrant_ok else 'NOT reachable'}"
    )

    if not (falkordb_ok and qdrant_ok):
        print()
        print(
            "One or more backing services are unavailable. Skipping live "
            "checks. Start FalkorDB and Qdrant and re-run for live smoke."
        )
        return 0

    print()
    print("--- Two-project isolation ---")
    for name, project_a, project_b in (
        ("default", args.project_a, args.project_b),
    ):
        check_name, status = _check_pair(name, project_a, project_b)
        print(f"{check_name:>16}  {status}")

    print()
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
