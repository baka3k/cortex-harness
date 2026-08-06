"""Registry preflight for the unified ingest/query contract.

This deterministic check verifies that two registered projects resolve to
disjoint code/document graph and collection targets. Embedded-storage and
ingest/query/reset behavior is covered by the Phase 07 fixture test suite.

Usage::

    python scripts/smoke_unified_contract.py [--project-a NAME] [--project-b NAME]

Exit code is 0 when every check passes and 1 when registration or isolation
validation fails.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List, Tuple


DEFAULT_FIXTURE_CONFIG = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "fixtures"
    / "unified_contract"
    / "config"
)


def _resolve_targets(project_id: str, config_dir: Path) -> dict:
    """Resolve the registry targets for ``project_id``."""
    # Lazy import so the script can run without code-tiny on sys.path.
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "code-tiny"))
    try:
        from tools.common.project_registry import resolve_project_targets

        return resolve_project_targets(project_id, config_dir=config_dir).__dict__
    finally:
        # No path cleanup — running multiple times is harmless.
        pass


def _check_pair(
    name: str, project_a: str, project_b: str, config_dir: Path
) -> Tuple[str, str]:
    try:
        a = _resolve_targets(project_a, config_dir)
        b = _resolve_targets(project_b, config_dir)
    except Exception as exc:
        return name, f"FAIL (registry: {exc})"

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
    parser.add_argument("--project-a", default="proj_alpha")
    parser.add_argument("--project-b", default="proj_beta")
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=DEFAULT_FIXTURE_CONFIG,
        help="Project-registry directory (defaults to the two-project fixture).",
    )
    args = parser.parse_args(argv)

    print("=" * 60)
    print("Unified Ingest/Query Contract — registry preflight")
    print("=" * 60)
    print(f"Project A: {args.project_a}")
    print(f"Project B: {args.project_b}")

    print()
    print("--- Two-project isolation ---")
    failed = False
    for name, project_a, project_b in (
        ("default", args.project_a, args.project_b),
    ):
        check_name, status = _check_pair(
            name, project_a, project_b, args.config_dir
        )
        print(f"{check_name:>16}  {status}")
        failed = failed or status.startswith("FAIL")

    print()
    print("Done.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
