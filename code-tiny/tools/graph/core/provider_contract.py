"""Provider-neutral graph selection and error classification contracts.

This module must stay safe to import in graph-less and FalkorDB-only
processes.  In particular, it must not import any provider implementation or
optional database client package.
"""

from __future__ import annotations

from typing import Any, MutableMapping

from tools.graph.core.base import GraphProvider


_PROVIDER_ALIASES = {
    "falkor": GraphProvider.FALKORDB,
    "falkordb": GraphProvider.FALKORDB,
    "falkor-db": GraphProvider.FALKORDB,
    "neo": GraphProvider.NEO4J,
    "neo4j": GraphProvider.NEO4J,
}

_DIRECTION_ALIASES = {
    "in": "in",
    "incoming": "in",
    "callers": "in",
    "upstream": "in",
    "out": "out",
    "outgoing": "out",
    "callees": "out",
    "downstream": "out",
    "both": "both",
    "all": "both",
    "any": "both",
    "undirected": "both",
}

_DATABASE_NOT_FOUND_MARKERS = (
    "database does not exist",
    "database not found",
    "unknown database",
    "graph does not exist",
    "unknown graph",
    "graph reference",
)


def normalize_graph_provider_name(
    value: GraphProvider | str | None,
    *,
    default: GraphProvider | str = GraphProvider.FALKORDB,
) -> str:
    """Return the canonical active-provider name or fail closed.

    ``None`` means the caller did not provide a value and uses the explicit
    default.  An empty or unknown value is configuration, not absence, and is
    rejected instead of silently selecting another provider.
    """

    candidate: GraphProvider | str = default if value is None else value
    if isinstance(candidate, GraphProvider):
        provider = candidate
    elif isinstance(candidate, str):
        normalized = candidate.strip().casefold()
        if not normalized:
            raise ValueError("Graph provider must not be empty")
        provider = _PROVIDER_ALIASES.get(normalized)
        if provider is None:
            raise ValueError(f"Unsupported graph provider: {candidate}")
    else:
        raise TypeError(
            "Graph provider must be a GraphProvider, string, or None; "
            f"got {type(candidate).__name__}"
        )

    if provider not in {GraphProvider.FALKORDB, GraphProvider.NEO4J}:
        raise ValueError(f"Unsupported graph provider: {provider.value}")
    return provider.value


def normalize_graph_provider(
    value: GraphProvider | str | None,
    *,
    default: GraphProvider | str = GraphProvider.FALKORDB,
) -> GraphProvider:
    """Return the canonical active :class:`GraphProvider`."""

    return GraphProvider(normalize_graph_provider_name(value, default=default))


def isolate_graph_provider_environment(
    environment: MutableMapping[str, str],
    value: GraphProvider | str | None,
    *,
    scoped_key: str | None = None,
) -> str:
    """Remove inherited settings for the inactive graph provider.

    Removing keys matters in addition to ignoring them: service launchers may
    inherit Neo4j settings from a shell or service-local ``.env`` even when
    the active project selects FalkorDB.
    """

    provider = normalize_graph_provider_name(value)
    environment["GRAPH_PROVIDER"] = provider
    if scoped_key:
        environment[scoped_key] = provider
    if "MCP_GRAPH_PROVIDER" in environment:
        environment["MCP_GRAPH_PROVIDER"] = provider

    for key in tuple(environment):
        if provider == "falkordb" and key.startswith("NEO4J_"):
            environment.pop(key, None)
        elif provider == "neo4j" and (
            key.startswith("FALKORDB_") or key == "DOC_FALKORDB_GRAPH"
        ):
            environment.pop(key, None)
    return provider


def normalize_graph_direction(direction: Any) -> str:
    """Normalize traversal direction to ``in``/``out``/``both``.

    Unknown values are rejected rather than broadening a query to an
    undirected traversal.
    """

    normalized = str(direction or "both").strip().casefold()
    mapped = _DIRECTION_ALIASES.get(normalized)
    if mapped is None:
        valid = ", ".join(sorted(_DIRECTION_ALIASES))
        raise ValueError(
            f"Invalid direction {direction!r}. Valid values (or aliases): {valid}."
        )
    return mapped


def is_database_not_found_error(exc: BaseException) -> bool:
    """Classify missing logical databases/graphs without provider imports."""

    code = str(getattr(exc, "code", "") or "").casefold()
    if "databasenotfound" in code or "database_not_found" in code:
        return True
    message = str(exc).casefold()
    return any(marker in message for marker in _DATABASE_NOT_FOUND_MARKERS)


__all__ = [
    "isolate_graph_provider_environment",
    "is_database_not_found_error",
    "normalize_graph_direction",
    "normalize_graph_provider",
    "normalize_graph_provider_name",
]
