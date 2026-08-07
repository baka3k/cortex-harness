"""CLI helpers for graph provider selection in scan scripts."""

from __future__ import annotations

import os
from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import Optional

from tools.graph.core.base import GraphDriver, GraphProvider
from tools.graph.core.factory import GraphDriverFactory


def normalize_graph_provider(value: Optional[str]) -> GraphProvider:
    provider = (value or "falkordb").strip().lower()
    if provider in {"neo4j", "neo"}:
        return GraphProvider.NEO4J
    if provider in {"falkor", "falkordb"}:
        return GraphProvider.FALKORDB
    raise ValueError(f"Unsupported graph provider: {value}")


def env_graph_provider(default: str = "falkordb") -> str:
    return (
        os.getenv("CODE_GRAPH_PROVIDER")
        or os.getenv("GRAPH_PROVIDER")
        or default
    )


def _has_option(parser: ArgumentParser, option: str) -> bool:
    return any(option in action.option_strings for action in parser._actions)


def add_graph_provider_args(parser: ArgumentParser) -> None:
    """Add provider-neutral graph options while preserving existing Neo4j flags."""

    if not _has_option(parser, "--graph-provider"):
        parser.add_argument(
            "--graph-provider",
            choices=["neo4j", "falkordb"],
            default=env_graph_provider(),
            help="Graph database provider used for graph writes.",
        )
    if not _has_option(parser, "--falkordb-path"):
        parser.add_argument(
            "--falkordb-path",
            default=os.getenv("FALKORDB_PATH"),
            help="Owner-specific FalkorDBLite .rdb path (derived when omitted).",
        )
    if not _has_option(parser, "--falkordb-graph"):
        parser.add_argument(
            "--falkordb-graph",
            default=os.getenv("FALKORDB_GRAPH") or os.getenv("FALKORDB_DATABASE"),
            help=(
                "FalkorDB graph name. When --project-id is set, the "
                "ProjectRegistry resolves this default unless an explicit "
                "value is provided."
            ),
        )


def apply_project_registry_defaults(args: Namespace) -> Namespace:
    """Resolve --falkordb-graph and --qdrant-collection defaults via the
    ProjectRegistry when --project-id is set and no explicit override was
    passed on the command line.

    Returns the same ``Namespace`` for fluent use. Mutates in place.
    """
    project_id = getattr(args, "project_id", None)
    if not project_id:
        return args
    # Lazy import — project_registry pulls project_id_lookup_key, which is
    # safe to import anywhere; this lazy import keeps the CLI module
    # independent of the registry's optional config-loading paths.
    from tools.common.project_registry import resolve_project_targets

    try:
        targets = resolve_project_targets(project_id)
    except Exception:
        # Unknown project + no env fallback: leave args untouched so the
        # downstream code surfaces the registry's own error.
        return args

    # Only fill an absent value. Any non-empty graph name is a valid explicit
    # target, including the literal name ``neo4j`` under FalkorDB. Argparse
    # namespaces do not preserve whether a non-empty value came from CLI or
    # environment, so replacing one here would violate explicit precedence.
    falkordb_graph = getattr(args, "falkordb_graph", None)
    if falkordb_graph in (None, "") and targets.code_graph:
        args.falkordb_graph = targets.code_graph

    qdrant_collection = getattr(args, "qdrant_collection", None)
    if not qdrant_collection and targets.code_qdrant_collection:
        args.qdrant_collection = targets.code_qdrant_collection

    return args


def prepare_graph_args(args: Namespace) -> bool:
    """Normalize selected provider into the legacy args consumed by scan scripts."""

    if os.environ.get("CORTEX_DISABLE_GRAPH", "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return False

    # Apply registry defaults before any other preparation. This lets
    # ``--project-id`` alone determine the target graph and collection
    # without requiring the caller to spell them out.
    apply_project_registry_defaults(args)

    provider = normalize_graph_provider(getattr(args, "graph_provider", None))
    if provider == GraphProvider.NEO4J:
        return bool(
            getattr(args, "neo4j_uri", None)
            and getattr(args, "neo4j_user", None)
            and getattr(args, "neo4j_password", None)
        )

    path = getattr(args, "falkordb_path", None)
    if not path:
        from cortex_harness.storage import resolve_storage
        path = str(resolve_storage(Path.cwd()).falkordb_code_path)
        setattr(args, "falkordb_path", path)
    setattr(args, "neo4j_uri", None)
    setattr(args, "neo4j_user", getattr(args, "falkordb_user", None) or "")
    setattr(args, "neo4j_password", getattr(args, "falkordb_password", None) or "")
    resolved_graph = (
        getattr(args, "falkordb_graph", None)
        or getattr(args, "project_id", None)
        or "neo4j"
    )
    setattr(args, "neo4j_db", resolved_graph)
    if not getattr(args, "falkordb_graph", None):
        args.falkordb_graph = resolved_graph
    return True


async def create_graph_driver_from_args(args: Namespace) -> Optional[GraphDriver]:
    """Create the selected graph driver, returning None when graph writes are disabled."""

    # Callers may use this helper directly rather than calling
    # ``prepare_graph_args`` first. Keep the boundary self-contained so the
    # default FalkorDB provider always derives an embedded local path.
    prepare_graph_args(args)
    provider = normalize_graph_provider(getattr(args, "graph_provider", None))
    if provider == GraphProvider.NEO4J:
        uri = getattr(args, "neo4j_uri", None)
        user = getattr(args, "neo4j_user", None)
        password = getattr(args, "neo4j_password", None)
        if not (uri and user and password):
            return None
        return await GraphDriverFactory.create_driver(
            provider=GraphProvider.NEO4J,
            uri=uri,
            user=user,
            password=password,
            database=getattr(args, "neo4j_db", None),
        )

    graph_name = (
        getattr(args, "falkordb_graph", None)
        or getattr(args, "neo4j_db", None)
        or getattr(args, "project_id", None)
        or "neo4j"
    )
    setattr(args, "neo4j_db", graph_name)
    return await GraphDriverFactory.create_driver(
        GraphProvider.FALKORDB,
        {
            "graph": graph_name,
            "database": graph_name,
            "path": getattr(args, "falkordb_path", None),
            "instance_id": os.getenv("CORTEX_STORAGE_INSTANCE", "default"),
            "owner_id": os.getenv("CORTEX_STORAGE_OWNER", "code"),
        },
    )
