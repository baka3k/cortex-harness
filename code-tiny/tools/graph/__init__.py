"""
Graph Database Abstraction Layer

This package provides database-agnostic interfaces for graph operations.
Currently supports Neo4j with extensibility for future databases (Kuzu, FalkorDB, etc.)
"""

from tools.graph.core.base import GraphDriver, QueryExecutor
from tools.graph.core.require_neo4j import (
    add_require_neo4j_argument,
    check_creds_or_exit,
    resolve_require_neo4j,
)
from tools.graph.writer.language_writer import LanguageCodeWriter


def __getattr__(name):
    """Load provider implementations only when a caller actually needs them.

    Parser and writer unit tests only need the abstract graph contract.  Eagerly
    importing the factory made those paths depend on optional Neo4j/FalkorDB
    client packages even when no database connection was requested.
    """
    if name in {"GraphDriverFactory", "GraphProvider"}:
        from tools.graph.core.factory import GraphDriverFactory, GraphProvider

        return {"GraphDriverFactory": GraphDriverFactory, "GraphProvider": GraphProvider}[name]
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
