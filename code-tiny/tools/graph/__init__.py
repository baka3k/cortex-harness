"""
Graph Database Abstraction Layer

This package provides database-agnostic interfaces for graph operations.
Currently supports Neo4j with extensibility for future databases (Kuzu, FalkorDB, etc.)
"""

from tools.graph.core.base import GraphDriver, QueryExecutor
from tools.graph.core.factory import GraphDriverFactory, GraphProvider
from tools.graph.core.require_neo4j import (
    add_require_neo4j_argument,
    check_creds_or_exit,
    resolve_require_neo4j,
)
from tools.graph.writer.language_writer import LanguageCodeWriter

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
