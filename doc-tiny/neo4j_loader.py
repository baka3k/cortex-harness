"""Explicitly isolated Neo4j-only compatibility loader.

The supported doc-tiny runtime uses :mod:`graph_store` and can select
FalkorDB. This module is retained only for operators who still depend on the
``neo4j-graphrag`` retriever during the dual-provider rollback window. It has
no import-time connections and requires an explicit opt-in environment flag.
"""

from __future__ import annotations

import os
from typing import Any, Tuple


LEGACY_OPT_IN_ENV = "DOC_ENABLE_LEGACY_NEO4J"


def create_legacy_clients() -> Tuple[Any, Any]:
    """Create Neo4j and Qdrant clients only after explicit legacy opt-in."""
    if os.getenv(LEGACY_OPT_IN_ENV, "").casefold() not in {"1", "true", "yes", "on"}:
        raise RuntimeError(
            f"neo4j_loader.py is a legacy Neo4j-only utility; set {LEGACY_OPT_IN_ENV}=1 "
            "to use it, or use graph_store.py for the supported provider-neutral runtime"
        )

    from neo4j import GraphDatabase
    from doc_local_qdrant import get_document_qdrant_store

    from enviroment_loader import (
        neo4j_password,
        neo4j_uri,
        neo4j_username,
    )

    neo4j_driver = GraphDatabase.driver(
        neo4j_uri,
        auth=(neo4j_username, neo4j_password),
    )
    # Even the Neo4j rollback utility uses the supported local document-owner
    # vector store; the rollback scope applies only to the graph provider.
    qdrant_client = get_document_qdrant_store().client
    return neo4j_driver, qdrant_client


__all__ = ["LEGACY_OPT_IN_ENV", "create_legacy_clients"]
