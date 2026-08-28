"""CLI helpers for graph provider selection in scan scripts."""

from __future__ import annotations

import logging
import os
from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import Optional

from tools.graph.core.base import GraphDriver, GraphProvider
from tools.graph.core.factory import GraphDriverFactory
from tools.graph.core.provider_contract import normalize_graph_provider


logger = logging.getLogger(__name__)


def env_graph_provider(default: str = "falkordb") -> str:
    return (
        os.getenv("CODE_GRAPH_PROVIDER")
        or os.getenv("GRAPH_PROVIDER")
        or default
    )


def graph_writes_disabled() -> bool:
    """Return whether this process was explicitly launched graphless."""

    return os.environ.get("CORTEX_DISABLE_GRAPH", "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


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
    if not _has_option(parser, "--falkordb-uri"):
        parser.add_argument(
            "--falkordb-uri",
            default=os.getenv("FALKORDB_URI"),
            help=(
                "Remote FalkorDB server URI (scheme://host:port or host:port). "
                "When set, --falkordb-path and the embedded backend are ignored."
            ),
        )
    if not _has_option(parser, "--falkordb-password"):
        parser.add_argument(
            "--falkordb-password",
            default=os.getenv("FALKORDB_PASSWORD"),
            help="Password for the remote FalkorDB server (optional).",
        )
    if not _has_option(parser, "--falkordb-ssl"):
        parser.add_argument(
            "--falkordb-ssl",
            action="store_true",
            default=os.getenv("FALKORDB_SSL", "").strip().lower()
            not in ("", "0", "false", "no"),
            help="Use TLS for the remote FalkorDB server.",
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
    from tools.common.project_registry import (
        ProjectNotRegisteredError,
        resolve_project_targets,
    )

    try:
        targets = resolve_project_targets(project_id, config_dir=_resolve_config_dir(args))
    except ProjectNotRegisteredError as exc:
        # The registry could not find this project. Surface the warning so
        # users running from a sibling repo (or from the wrong CWD) learn
        # that their config directory does not describe the requested
        # project. Downstream code still falls back to ``args.project_id``,
        # but the explicit warning prevents silent misconfiguration.
        logger.warning(
            "[graph-cli] project_id %r not registered in discovered config; "
            "falling back to args.project_id for falkordb-graph. %s",
            project_id,
            exc,
        )
        return args
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning(
            "[graph-cli] registry lookup for %r failed unexpectedly (%s); "
            "falling back to args.project_id.",
            project_id,
            exc,
        )
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


def _resolve_config_dir(args: Namespace) -> Optional[Path]:
    """Pick the registry's config directory using --root when available.

    Walks up from --root so a scan of ``/Users/hieplq1.aip/HyperDev/hyper-pack``
    finds ``<root>/.cortext-harness/config/*.json`` even when the process
    CWD lives somewhere unrelated (e.g. ``/Users/hieplq1.aip/AI/cortex-harness``).
    Falls back to ``None`` so the registry uses its default CWD-based
    discovery when --root is not supplied.
    """
    raw_root = getattr(args, "root", None)
    if not raw_root:
        return None
    root = Path(str(raw_root)).expanduser().resolve()
    if not root.is_dir():
        return None
    for candidate in (root, *root.parents):
        config_dir = candidate / ".cortext-harness" / "config"
        if config_dir.is_dir():
            return config_dir
    return None


def prepare_graph_args(args: Namespace) -> bool:
    """Normalize selected provider into the legacy args consumed by scan scripts."""

    if graph_writes_disabled():
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

    falkordb_uri = getattr(args, "falkordb_uri", None) or os.getenv("FALKORDB_URI")
    if falkordb_uri:
        # Remote FalkorDB project: never synthesize an embedded local path —
        # FalkorDBLite may not even be installable on this platform (win32).
        args.falkordb_uri = falkordb_uri
        args.falkordb_path = None
    else:
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
        or "hyper_graph"
    )
    setattr(args, "neo4j_db", resolved_graph)
    if not getattr(args, "falkordb_graph", None):
        args.falkordb_graph = resolved_graph
    return True


async def create_graph_driver_from_args(
    args: Namespace,
    *,
    attach_journal: bool = True,
) -> Optional[GraphDriver]:
    """Create the selected graph driver, returning None when graph writes are disabled.

    ``attach_journal=False`` is reserved for orchestrator preflight writes that
    happen before parser-scoped journal metadata exists.
    """

    # Callers may use this helper directly rather than calling
    # ``prepare_graph_args`` first. Keep the boundary self-contained so the
    # default FalkorDB provider always derives an embedded local path.
    if not prepare_graph_args(args):
        return None
    provider = normalize_graph_provider(getattr(args, "graph_provider", None))
    if provider == GraphProvider.NEO4J:
        uri = getattr(args, "neo4j_uri", None)
        user = getattr(args, "neo4j_user", None)
        password = getattr(args, "neo4j_password", None)
        if not (uri and user and password):
            return None
        driver = await GraphDriverFactory.create_driver(
            provider=GraphProvider.NEO4J,
            uri=uri,
            user=user,
            password=password,
            database=getattr(args, "neo4j_db", None),
        )
        if attach_journal:
            from tools.graph.journal.config import attach_journal_config
            from tools.graph.journal.consumer import resume_journal

            journal_config = attach_journal_config(driver, args)
            if journal_config is not None and journal_config.required:
                await resume_journal(journal_config, driver)
        return driver

    graph_name = (
        getattr(args, "falkordb_graph", None)
        or getattr(args, "project_id", None)
        or "hyper_graph"
    )
    setattr(args, "neo4j_db", graph_name)
    falkordb_uri = getattr(args, "falkordb_uri", None) or os.getenv("FALKORDB_URI")
    if falkordb_uri:
        driver = await GraphDriverFactory.create_driver(
            GraphProvider.FALKORDB,
            {
                "graph": graph_name,
                "database": graph_name,
                "uri": falkordb_uri,
                "password": getattr(args, "falkordb_password", None)
                or os.getenv("FALKORDB_PASSWORD"),
                "ssl": bool(
                    getattr(args, "falkordb_ssl", False)
                    or os.getenv("FALKORDB_SSL", "").strip().lower()
                    not in ("", "0", "false", "no")
                ),
                "_suppress_deprecation": True,
            },
        )
    else:
        driver = await GraphDriverFactory.create_driver(
            GraphProvider.FALKORDB,
            {
                "graph": graph_name,
                "database": graph_name,
                "path": getattr(args, "falkordb_path", None),
                "instance_id": os.getenv("CORTEX_STORAGE_INSTANCE", "default"),
                "owner_id": os.getenv("CORTEX_STORAGE_OWNER", "code"),
            },
        )
    if attach_journal:
        from tools.graph.journal.config import attach_journal_config
        from tools.graph.journal.consumer import resume_journal

        journal_config = attach_journal_config(driver, args)
        if journal_config is not None and journal_config.required:
            await resume_journal(journal_config, driver)
    return driver
