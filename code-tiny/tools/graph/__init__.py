"""
Graph Database Abstraction Layer

This package provides database-agnostic interfaces for graph operations.
FalkorDB is the primary provider; Neo4j is an optional compatibility extra.
"""

from tools.graph.core.base import GraphDriver, GraphProvider, QueryExecutor
from tools.graph.writer.language_writer import LanguageCodeWriter


def __getattr__(name):
    """Load provider implementations only when a caller actually needs them.

    Parser and writer unit tests only need the abstract graph contract.  Eagerly
    importing the factory made those paths depend on optional Neo4j/FalkorDB
    client packages even when no database connection was requested.
    """
    if name == "GraphDriverFactory":
        from tools.graph.core.factory import GraphDriverFactory

        return GraphDriverFactory
    if name in {
        "add_require_neo4j_argument",
        "check_creds_or_exit",
        "resolve_require_neo4j",
    }:
        from tools.graph.core import require_neo4j

        return getattr(require_neo4j, name)
    raise AttributeError(name)

__all__ = [
    'GraphDriver',
    'QueryExecutor',
    'GraphDriverFactory', 
    'GraphProvider',
    'LanguageCodeWriter',
    'add_require_neo4j_argument',
    'check_creds_or_exit',
    'resolve_require_neo4j',
]
