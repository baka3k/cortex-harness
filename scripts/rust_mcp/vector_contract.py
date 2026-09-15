#!/usr/bin/env python3
"""Shared contract for the vector-lane golden capture (phase-01 of
plans/260915-2027-vector-lane-rust-port).

Covers BOTH MCP planes against real stores:

* ``unified`` (code lane) — `semantic_search` + `explore_graph` on a *snapshot*
  of instance ``cortex`` (local Qdrant ``qdrant/code`` 132MB + Ladybug graph),
  embedder ON (jina-v3, ``EMBED_DEVICE=mps``) exactly like production.
* ``mind`` (doc lane) — `semantic_search` + `query_graph_rag_langextract` on the
  phase-13 fixture store (project ``mindfix`` → remote qdrant 127.0.0.1:6333,
  isolated local store; bge-m3 embedder).

Snapshot policy: the instance copy lives under ``.cache/vector_golden/`` (never
committed, never the live instance — the live servers hold file locks). The
``CORTEX_EFFECTIVE_*`` fingerprint vars from the recorded active-env file are
DROPPED: they embed absolute paths into the live instance and would defeat the
snapshot isolation.
"""

from __future__ import annotations

import os
import shlex
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CODE_TINY = REPO_ROOT / "code-tiny"
DOC_TINY = REPO_ROOT / "doc-tiny"
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
FIXTURE_PATH = FIXTURE_DIR / "vector_fixtures.json"
ACTIVE_ENV = REPO_ROOT / ".cache" / "mcp" / "cortext-code-tiny.active.env"
CACHE_ROOT = REPO_ROOT / ".cache" / "vector_golden"
# Mirror the real storage layout `<home>/v1/instances/<id>` — the storage
# config resolves absolute paths from the manifest/env, so the snapshot must
# be drop-in at the same relative depth.
SNAPSHOT_HOME = CACHE_ROOT / "data_home"
SNAPSHOT_INSTANCE = SNAPSHOT_HOME / "v1" / "instances" / "cortex"

DROPPED_ENV_PREFIXES = ("CORTEX_EFFECTIVE_", "FASTMCP_", "CORTEX_MCP_")
LIVE_HOME = Path(os.path.expanduser("~/.cortext-harness"))

# Same tolerance contract as the phase-02 re-baseline (mind_contract.py):
# ort-vs-torch score drift ~1e-7 is a measured property, not a port bug.
TOLERANCE_KEYS = {
    "score",
    "rerank_score",
    "confidence",
    "graph_proximity",
    "semantic",
    "keyword",
    "graph",
    "bm25",
    "freshness",
    "usage",
    "final",
}
TOLERANCE = 1e-6

DEFAULT_CODE_QUERY = "parse configuration file and load settings"
DEFAULT_DOC_QUERY = "hướng dẫn cài đặt và cấu hình hệ thống"


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse the lifecycle `export K=V` env files (single-line values)."""
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = shlex.split(value)[0] if value[0] == "'" else value[1:-1]
        env[key.strip()] = value
    return env


def ensure_snapshot(force: bool = False) -> Path:
    """Copy instance `cortex` into the cache area (qdrant + ladybug + manifest)."""
    import sys

    live = Path(
        os.path.expanduser("~/.cortext-harness/v1/instances/cortex")
    )
    if not live.is_dir():
        sys.exit(f"live instance not found: {live}")
    if SNAPSHOT_INSTANCE.is_dir() and not force:
        return SNAPSHOT_INSTANCE
    if SNAPSHOT_INSTANCE.is_dir():
        shutil.rmtree(SNAPSHOT_INSTANCE)
    SNAPSHOT_INSTANCE.mkdir(parents=True)
    # Only the planes the tools touch — keeps the copy ~330MB instead of the
    # whole instance tree (falkordb legacy .rdb files are not needed).
    for entry in sorted(live.iterdir()):
        name = entry.name
        if name in {"qdrant", "ladybug"} and entry.is_dir():
            shutil.copytree(entry, SNAPSHOT_INSTANCE / name)
        elif entry.is_file():
            shutil.copy2(entry, SNAPSHOT_INSTANCE / name)
    _strip_lease_artifacts(SNAPSHOT_INSTANCE)
    _rewrite_manifest_paths(SNAPSHOT_INSTANCE / "manifest.json")
    return SNAPSHOT_INSTANCE


def _strip_lease_artifacts(root: Path) -> None:
    """Lease/lock files carry live-pid ownership records — never copy them."""
    for path in root.rglob("*"):
        if path.is_file() and (path.name == ".lock" or ".lock" in path.name):
            path.unlink(missing_ok=True)


def _rewrite_manifest_paths(manifest_path: Path) -> None:
    """Manifest records absolute live paths — repoint them at the snapshot."""
    if not manifest_path.exists():
        return
    text = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(
        text.replace(str(LIVE_HOME), str(SNAPSHOT_HOME)), encoding="utf-8"
    )


def unified_server_env(port: int) -> dict[str, str]:
    """Production-shaped env for `code-tiny/mcp/unified_mcp.py` on the snapshot."""
    if not ACTIVE_ENV.exists():
        raise FileNotFoundError(
            f"{ACTIVE_ENV} missing — run `dev start` once for project cortext"
        )
    env = {
        key: value
        for key, value in parse_env_file(ACTIVE_ENV).items()
        if not key.startswith(DROPPED_ENV_PREFIXES)
    }
    # The active env pins absolute live-store paths (LADYBUG_*_PATH,
    # QDRANT_*_PATH, …) — repoint every one of them at the snapshot so the
    # capture server never contends with the live servers' leases.
    live_prefix = str(LIVE_HOME)
    snapshot_prefix = str(SNAPSHOT_HOME)
    env = {
        key: value.replace(live_prefix, snapshot_prefix)
        for key, value in env.items()
    }
    # The Rust graph runtime picks the graph NAME from LADYBUG_GRAPH; the
    # lifecycle env carries the project id ("cortext") while the actual store
    # graph is the path basename ("hyper_graph"). Python ignores this var, so
    # align it with the store for both sides.
    ladybug_path = env.get("LADYBUG_CODE_PATH") or env.get("LADYBUG_PATH")
    if ladybug_path:
        env["LADYBUG_GRAPH"] = Path(ladybug_path).name
    env.update(
        {
            "CORTEX_DATA_HOME": str(SNAPSHOT_HOME),
            "CORTEX_STORAGE_INSTANCE": "cortex",
            "FASTMCP_PORT": str(port),
            "PYTHONHASHSEED": "0",
            # 1 process per case is the isolation unit; the embedder preload
            # (default ON) keeps every case on a warm model like production.
            "MCP_PRELOAD_EMBEDDER": "1",
        }
    )
    return env


def mind_server_env(port: int) -> dict[str, str]:
    """Phase-13 fixture env (mindfix → remote qdrant :6333, isolated local)."""
    import mind_contract  # noqa: PLC0415 — sys.path is set by callers

    env = mind_contract.server_env(port)
    env["PYTHONHASHSEED"] = "0"
    return env


# ---------------------------------------------------------------------------
# Case matrix — phase-01 gate: >= 40 local + >= 20 remote structural cases.
# ---------------------------------------------------------------------------


def unified_cases() -> list[dict]:
    """semantic_search + explore_graph on the cortex snapshot (local qdrant)."""
    cases: list[dict] = []
    search_variants = [
        # (suffix, extra arguments)
        ("basic", {}),
        ("mode_comment", {"mode": "comment"}),
        ("mode_code", {"mode": "code"}),
        ("topk3", {"top_k": 3}),
        ("topk25", {"top_k": 25}),
        ("content_mode_code", {"content_mode": "code"}),
        ("include_raw_fields", {"include_raw_fields": True}),
        ("explicit_collection", {"collection": "cortext_mess"}),
        ("expand_graph", {"expand_graph": True, "graph_depth": 1}),
        ("scoped_project", {"project_id": "cortext"}),
        ("scope_prefix", {"project_id": "cortext_4ee207813f"}),
        ("snippet_flags", {"show_snippet": True, "show_comment": True}),
    ]
    for suffix, extra in search_variants:
        cases.append(
            {
                "id": f"unified.semantic_search.{suffix}",
                "flavor": "unified",
                "tool": "semantic_search",
                "arguments": {"query": DEFAULT_CODE_QUERY, **extra},
            }
        )
    # Second query — same variants on the "hot" path keep the golden honest
    # about collection merge/dedupe ordering across queries.
    for suffix in ("basic", "topk25", "expand_graph", "scoped_project"):
        extra = {"expand_graph": True, "graph_depth": 1} if suffix == "expand_graph" else {}
        if suffix == "scoped_project":
            extra = {"project_id": "cortext"}
        cases.append(
            {
                "id": f"unified.semantic_search.v2_{suffix}",
                "flavor": "unified",
                "tool": "semantic_search",
                "arguments": {"query": "authenticate user request token", **extra},
            }
        )
    # Error/edge paths (error envelopes are part of the contract).
    cases.extend(
        [
            {
                "id": "unified.semantic_search.missing_query",
                "flavor": "unified",
                "tool": "semantic_search",
                "arguments": {},
            },
            {
                "id": "unified.semantic_search.unregistered_project",
                "flavor": "unified",
                "tool": "semantic_search",
                "arguments": {"query": DEFAULT_CODE_QUERY, "project_id": "no_such_project"},
            },
            {
                "id": "unified.semantic_search.missing_collection",
                "flavor": "unified",
                "tool": "semantic_search",
                "arguments": {"query": DEFAULT_CODE_QUERY, "collection": "no_such_collection"},
            },
        ]
    )
    for mode in ("semantic", "hybrid", "graph_expanded"):
        cases.append(
            {
                "id": f"unified.explore_graph.{mode}",
                "flavor": "unified",
                "tool": "explore_graph",
                "arguments": {"query": DEFAULT_CODE_QUERY, "mode": mode, "top_k": 5},
            }
        )
    cases.append(
        {
            "id": "unified.explore_graph.debug_scoped",
            "flavor": "unified",
            "tool": "explore_graph",
            "arguments": {
                "query": DEFAULT_CODE_QUERY,
                "mode": "hybrid",
                "debug": True,
                "project_id": "cortext",
            },
        }
    )
    return cases


def mind_cases() -> list[dict]:
    """mind vector tools on the phase-13 fixture store (remote qdrant)."""
    return [
        {
            "id": "mind.semantic_search.basic",
            "flavor": "mind",
            "tool": "semantic_search",
            "arguments": {"query": DEFAULT_DOC_QUERY, "project_id": "mindfix"},
        },
        {
            "id": "mind.semantic_search.topk2",
            "flavor": "mind",
            "tool": "semantic_search",
            "arguments": {"query": DEFAULT_DOC_QUERY, "top_k": 2, "project_id": "mindfix"},
        },
        {
            "id": "mind.semantic_search.unscoped",
            "flavor": "mind",
            "tool": "semantic_search",
            "arguments": {"query": DEFAULT_DOC_QUERY},
        },
        {
            "id": "mind.semantic_search.max_passage_chars",
            "flavor": "mind",
            "tool": "semantic_search",
            "arguments": {
                "query": DEFAULT_DOC_QUERY,
                "max_passage_chars": 200,
                "project_id": "mindfix",
            },
        },
        {
            "id": "mind.semantic_search.entity_mentions",
            "flavor": "mind",
            "tool": "semantic_search",
            "arguments": {
                "query": DEFAULT_DOC_QUERY,
                "include_entity_mentions": True,
                "project_id": "mindfix",
            },
        },
        {
            "id": "mind.semantic_search.missing_query",
            "flavor": "mind",
            "tool": "semantic_search",
            "arguments": {},
        },
        {
            "id": "mind.query_graph_rag.basic",
            "flavor": "mind",
            "tool": "query_graph_rag_langextract",
            "arguments": {"query": DEFAULT_DOC_QUERY, "project_id": "mindfix"},
        },
        {
            "id": "mind.query_graph_rag.no_expand",
            "flavor": "mind",
            "tool": "query_graph_rag_langextract",
            "arguments": {
                "query": DEFAULT_DOC_QUERY,
                "expand_related": False,
                "project_id": "mindfix",
            },
        },
        {
            "id": "mind.query_graph_rag.rerank",
            "flavor": "mind",
            "tool": "query_graph_rag_langextract",
            "arguments": {
                "query": DEFAULT_DOC_QUERY,
                "rerank": True,
                "project_id": "mindfix",
            },
        },
    ]


def all_cases() -> list[dict]:
    return unified_cases() + mind_cases()
