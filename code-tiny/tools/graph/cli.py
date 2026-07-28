"""CLI helpers for graph provider selection in scan scripts."""

from __future__ import annotations

import os
from argparse import ArgumentParser, Namespace
from typing import Optional

from tools.graph.core.base import GraphDriver, GraphProvider
from tools.graph.core.factory import GraphDriverFactory


def normalize_graph_provider(value: Optional[str]) -> GraphProvider:
    provider = (value or "neo4j").strip().lower()
    if provider in {"neo4j", "neo"}:
        return GraphProvider.NEO4J
    if provider in {"falkor", "falkordb"}:
        return GraphProvider.FALKORDB
    raise ValueError(f"Unsupported graph provider: {value}")


def env_graph_provider(default: str = "neo4j") -> str:
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
    if not _has_option(parser, "--falkordb-uri"):
        parser.add_argument(
            "--falkordb-uri",
            default=os.getenv("FALKORDB_URI") or os.getenv("FALKORDB_URL"),
        )
    if not _has_option(parser, "--falkordb-host"):
        parser.add_argument("--falkordb-host", default=os.getenv("FALKORDB_HOST", "localhost"))
    if not _has_option(parser, "--falkordb-port"):
        parser.add_argument(
            "--falkordb-port",
            type=int,
            default=int(os.getenv("FALKORDB_PORT", "6379")),
        )
    if not _has_option(parser, "--falkordb-user"):
        parser.add_argument(
            "--falkordb-user",
            default=os.getenv("FALKORDB_USER") or os.getenv("FALKORDB_USERNAME"),
        )
    if not _has_option(parser, "--falkordb-password"):
        parser.add_argument(
            "--falkordb-password",
            default=os.getenv("FALKORDB_PASSWORD", ""),
        )
    if not _has_option(parser, "--falkordb-graph"):
        parser.add_argument(
            "--falkordb-graph",
            default=os.getenv("FALKORDB_GRAPH") or os.getenv("FALKORDB_DATABASE", "neo4j"),
            help=(
                "FalkorDB graph name. When --project-id is set, the "
                "ProjectRegistry resolves this default unless an explicit "
                "value is provided."
            ),
        )
    if not _has_option(parser, "--falkordb-ssl"):
        parser.add_argument(
            "--falkordb-ssl",
            action="store_true",
            default=os.getenv("FALKORDB_SSL", "").lower() in {"1", "true", "yes", "on"},
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

    # Only override when the user did not pass an explicit value. We
    # distinguish "explicit value passed" from "default from env" by
    # checking the original ``sys.argv`` (argparse drops the distinction
    # after parsing). For simplicity we override unconditionally when
    # the registry has a non-default value and the CLI default was used.
    falkordb_graph = getattr(args, "falkordb_graph", None)
    if (
        falkordb_graph in (None, "", "neo4j")
        or os.getenv("FALKORDB_GRAPH") in (None, "", "neo4j")
    ) and targets.code_graph:
        args.falkordb_graph = targets.code_graph

    qdrant_collection = getattr(args, "qdrant_collection", None)
    if not qdrant_collection and targets.code_qdrant_collection:
        args.qdrant_collection = targets.code_qdrant_collection

    return args


def prepare_graph_args(args: Namespace) -> bool:
    """Normalize selected provider into the legacy args consumed by scan scripts."""

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

    uri = getattr(args, "falkordb_uri", None)
    if not uri:
        host = getattr(args, "falkordb_host", None) or "localhost"
        port = getattr(args, "falkordb_port", None) or 6379
        scheme = "rediss" if getattr(args, "falkordb_ssl", False) else "redis"
        uri = f"{scheme}://{host}:{port}"

    setattr(args, "neo4j_uri", uri)
    setattr(args, "neo4j_user", getattr(args, "falkordb_user", None) or "")
    setattr(args, "neo4j_password", getattr(args, "falkordb_password", None) or "")
    setattr(args, "neo4j_db", getattr(args, "falkordb_graph", None) or "neo4j")
    return True


async def create_graph_driver_from_args(args: Namespace) -> Optional[GraphDriver]:
    """Create the selected graph driver, returning None when graph writes are disabled."""

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

    graph_name = getattr(args, "falkordb_graph", None) or "neo4j"
    setattr(args, "neo4j_db", graph_name)
    return await GraphDriverFactory.create_driver(
        GraphProvider.FALKORDB,
        {
            "uri": getattr(args, "falkordb_uri", None),
            "host": getattr(args, "falkordb_host", None),
            "port": getattr(args, "falkordb_port", None),
            "user": getattr(args, "falkordb_user", None),
            "password": getattr(args, "falkordb_password", None),
            "graph": graph_name,
            "database": graph_name,
            "ssl": bool(getattr(args, "falkordb_ssl", False)),
        },
    )
