import argparse
import logging
import os
import signal
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP
from qdrant_client.http import models as qmodels
from sentence_transformers import SentenceTransformer

from embedding_utils import resolve_embedding_device, resolve_embedding_model
from graph_store import (
    create_graph_store_for_project,
    create_graph_store_from_env,
    env_graph_provider,
)
from doc_local_qdrant import get_document_qdrant_store
from project_contract import (
    list_registered_projects,
    qdrant_project_filter as _pc_qdrant_project_filter,
    resolve_project_targets,
)


MCP_NAME = os.getenv("MCP_SERVER_NAME", "graph_rag")

# Load .env if present (for NEO4J_*/QDRANT_*/TEXT_EMBEDDING_MODEL).
try:
    from dotenv import load_dotenv

    env_path = Path(__file__).with_name(".env")
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
except Exception:
    pass

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER") or os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASS = os.getenv("NEO4J_PASS") or os.getenv("NEO4J_PASS", "password")

QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION_DOC", "documents")

DEFAULT_TEXT_EMBEDDING_MODEL = "BAAI/bge-m3"

DEFAULT_ENTITY_TYPES = [
    "ORG",
    "PRODUCT",
    "STANDARD",
    "TECH",
    "CRYPTO",
    "SECURITY",
    "PROTOCOL",
    "VEHICLE",
    "DEVICE",
    "SERVER",
    "APP",
    "CERTIFICATE",
    "KEY",
]


def _parse_entity_types(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


ENTITY_TYPES_DEFAULT = _parse_entity_types(
    os.getenv("DEFAULT_ENTITY_TYPES") or os.getenv("ENTITY_TYPES_DEFAULT")
) or DEFAULT_ENTITY_TYPES


_qdrant_client = None
_neo4j_driver = None
_embedder: Optional[SentenceTransformer] = None
logger = logging.getLogger("graph_rag.mcp")


def get_qdrant():
    global _qdrant_client
    if _qdrant_client is None:
        _qdrant_client = get_document_qdrant_store()
    return _qdrant_client


def get_embedder() -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        model_name, local_files_only = resolve_embedding_model(None, DEFAULT_TEXT_EMBEDDING_MODEL)
        device = resolve_embedding_device(None)
        _embedder = SentenceTransformer(
            model_name, local_files_only=local_files_only, device=device
        )
    return _embedder


def get_neo4j():
    global _neo4j_driver
    if _neo4j_driver is None:
        _neo4j_driver = create_graph_store_from_env()
    return _neo4j_driver


def _acquire_graph_store(project_id: Optional[str]):
    if project_id and env_graph_provider() == "neo4j":
        # Preserve the legacy Neo4j request-scoped database session behavior.
        return create_graph_store_for_project(project_id), True
    base = get_neo4j()
    if not project_id:
        return base, False
    if getattr(base, "provider", None) == "falkordb":
        targets = resolve_project_targets(project_id)
        return base.for_graph(targets.doc_graph), False
    return base, False


def _graph_store_candidates(project_id: Optional[str]):
    """Return deterministic graph stores for scoped or full-search queries."""
    if project_id:
        return [_acquire_graph_store(project_id)]
    base = get_neo4j()
    if getattr(base, "provider", None) != "falkordb":
        return [(base, False)]

    graph_names: List[str] = []
    for registered_project in list_registered_projects():
        graph_name = resolve_project_targets(registered_project).doc_graph
        if graph_name and graph_name not in graph_names:
            graph_names.append(graph_name)
    if not graph_names:
        return [(base, False)]
    return [(base.for_graph(graph_name), False) for graph_name in graph_names]


def _resolve_doc_collection(
    project_id: Optional[str], collection: Optional[str] = None
) -> str:
    if collection:
        return collection
    if project_id:
        return resolve_project_targets(project_id).doc_qdrant_collection
    return QDRANT_COLLECTION


def _resolve_doc_collections(
    project_id: Optional[str], collection: Optional[str] = None
) -> List[str]:
    if collection:
        return [collection]
    if project_id:
        return [resolve_project_targets(project_id).doc_qdrant_collection]

    collections: List[str] = []
    for registered_project in list_registered_projects():
        name = resolve_project_targets(registered_project).doc_qdrant_collection
        if name and name not in collections:
            collections.append(name)
    return collections or [QDRANT_COLLECTION]


def qdrant_search_entity_payload(
    query_vector: List[float],
    top_k: int,
    source_id: Optional[str],
    collection: Optional[str] = None,
    project_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    qdrant = get_qdrant()
    collection_names = _resolve_doc_collections(project_id, collection)

    # Build the Qdrant filter. The project filter combines with the
    # ``source_id`` filter via AND. A missing ``project_id`` produces no
    # project predicate, so the search spans every project.
    must_conditions: List[qmodels.FieldCondition] = []
    if source_id:
        must_conditions.append(
            qmodels.FieldCondition(
                key="source_id",
                match=qmodels.MatchValue(value=source_id),
            )
        )
    project_filter = _pc_qdrant_project_filter(project_id)
    if project_filter and "must" in project_filter:
        for cond in project_filter["must"]:
            key = cond.get("key")
            match_value = cond.get("match", {}).get("value")
            if key and match_value is not None:
                must_conditions.append(
                    qmodels.FieldCondition(
                        key=key,
                        match=qmodels.MatchValue(value=match_value),
                    )
                )
    qdrant_filter = qmodels.Filter(must=must_conditions) if must_conditions else None

    available = None
    if hasattr(qdrant, "list_collection_names"):
        available = set(qdrant.list_collection_names())

    payloads_by_key: Dict[Any, Dict[str, Any]] = {}
    for collection_name in collection_names:
        if available is not None and collection_name not in available:
            continue
        try:
            if hasattr(qdrant, "search"):
                hits = qdrant.search(
                    collection_name=collection_name,
                    query_vector=query_vector,
                    limit=top_k,
                    query_filter=qdrant_filter,
                )
            else:
                hits = qdrant.query_points(
                    collection_name=collection_name,
                    query=query_vector,
                    limit=top_k,
                    query_filter=qdrant_filter,
                ).points
        except Exception as exc:
            if len(collection_names) == 1:
                raise
            logger.warning("Skipping unavailable doc collection %s: %s", collection_name, exc)
            continue

        for hit in hits:
            if not hit.payload:
                continue
            key = (
                hit.payload.get("project_id_normalized"),
                hit.payload.get("source_id"),
                hit.payload.get("paragraph_id"),
                hit.payload.get("text"),
            )
            row = {
                "score": getattr(hit, "score", None),
                "text": hit.payload.get("text"),
                "source_id": hit.payload.get("source_id"),
                "paragraph_id": hit.payload.get("paragraph_id"),
                "project_id": hit.payload.get("project_id"),
                "project_id_normalized": hit.payload.get("project_id_normalized"),
                "entity_ids": hit.payload.get("entity_ids") or [],
                "entity_mentions": hit.payload.get("entity_mentions") or [],
                "collection": collection_name,
            }
            existing = payloads_by_key.get(key)
            if existing is None or float(row.get("score") or 0.0) > float(
                existing.get("score") or 0.0
            ):
                payloads_by_key[key] = row
    payloads = list(payloads_by_key.values())
    payloads.sort(key=lambda row: float(row.get("score") or 0.0), reverse=True)
    return payloads[:top_k]


def fetch_entities_by_ids(
    entity_ids: List[str], project_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    if not entity_ids:
        return []
    entities: List[Dict[str, Any]] = []
    seen = set()
    for store, owned in _graph_store_candidates(project_id):
        try:
            with store.session() as session:
                result = session.run(
                    """
                    MATCH (e:Entity)
                    WHERE e.id IN $ids
                      AND ($project_id_normalized IS NULL OR
                           e.project_id_normalized = $project_id_normalized)
                    RETURN e.id AS id, e.name AS name, e.type AS type
                    """,
                    ids=entity_ids,
                    project_id_normalized=(project_id.strip().casefold() if project_id else None),
                )
                for record in result:
                    row = dict(record)
                    key = row.get("id") or (row.get("name"), row.get("type"))
                    if key in seen:
                        continue
                    seen.add(key)
                    entities.append(row)
        finally:
            if owned:
                store.close()
    return entities[: len(entity_ids)]


def fetch_relations_by_entity_ids(
    entity_ids: List[str],
    entity_types: Optional[List[str]],
    related_k: int,
    project_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not entity_ids or related_k <= 0:
        return []
    entity_types = entity_types or []
    relations: List[Dict[str, Any]] = []
    seen = set()
    for store, owned in _graph_store_candidates(project_id):
        remaining = related_k - len(relations)
        if remaining <= 0:
            break
        try:
            with store.session() as session:
                result = session.run(
                    """
                    UNWIND $ids AS id
                    MATCH (e:Entity {id: id})-[r:RELATED]-(e2:Entity)
                    WHERE ($types = [] OR e.type IN $types OR e2.type IN $types)
                      AND ($project_id_normalized IS NULL OR
                           (e.project_id_normalized = $project_id_normalized AND
                            e2.project_id_normalized = $project_id_normalized))
                    RETURN e.id AS source_id, e.name AS source, e.type AS source_type,
                           r.type AS relation,
                           e2.id AS target_id, e2.name AS target, e2.type AS target_type
                    LIMIT $limit
                    """,
                    ids=entity_ids,
                    types=entity_types,
                    limit=remaining,
                    project_id_normalized=(project_id.strip().casefold() if project_id else None),
                )
                for record in result:
                    row = dict(record)
                    key = (
                        row.get("source_id"),
                        row.get("relation"),
                        row.get("target_id"),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    relations.append(row)
                    if len(relations) >= related_k:
                        break
        finally:
            if owned:
                store.close()
    return relations


def _filter_entity_ids_for_expansion(
    entity_ids: List[str],
    payloads: List[Dict[str, Any]],
    min_score_to_expand: Optional[float],
    min_entity_occurrences: Optional[int],
) -> List[str]:
    if not entity_ids:
        return []
    if min_score_to_expand is not None:
        scores = [row.get("score") for row in payloads if row.get("score") is not None]
        if scores and max(scores) < min_score_to_expand:
            return []
    if min_entity_occurrences and min_entity_occurrences > 1:
        counts: Dict[str, int] = {}
        for row in payloads:
            for entity_id in row.get("entity_ids") or []:
                counts[entity_id] = counts.get(entity_id, 0) + 1
        return [eid for eid in entity_ids if counts.get(eid, 0) >= min_entity_occurrences]
    return entity_ids


def fetch_relations_with_depth(
    entity_ids: List[str],
    entity_types: Optional[List[str]],
    related_k: int,
    depth: int,
    project_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not entity_ids or related_k <= 0 or depth <= 0:
        return []
    relations: List[Dict[str, Any]] = []
    seen_relations = set()
    seen_entities = set(entity_ids)
    frontier = list(entity_ids)
    remaining = related_k

    for _ in range(depth):
        if not frontier or remaining <= 0:
            break
        step_relations = fetch_relations_by_entity_ids(
            frontier, entity_types, remaining, project_id=project_id
        )
        new_frontier = set()
        for rel in step_relations:
            rel_key = (
                rel.get("source_id"),
                rel.get("relation"),
                rel.get("target_id"),
            )
            if rel_key in seen_relations:
                continue
            seen_relations.add(rel_key)
            relations.append(rel)
            source_id = rel.get("source_id")
            target_id = rel.get("target_id")
            if source_id and source_id not in seen_entities:
                new_frontier.add(source_id)
            if target_id and target_id not in seen_entities:
                new_frontier.add(target_id)

        remaining = related_k - len(relations)
        if not new_frontier:
            break
        seen_entities.update(new_frontier)
        frontier = list(new_frontier)

    return relations


def _compute_heuristic_rerank_score(
    passage: Dict[str, Any],
    entity_types: List[str],
    entity_weight: float,
    type_weight: float,
    confidence_weight: float,
    length_penalty: float,
) -> float:
    base_score = passage.get("score") or 0.0
    text = passage.get("text") or ""
    mentions = passage.get("_entity_mentions") or []
    unique_entities = set()
    type_hits = 0
    confidences: List[float] = []

    for item in mentions:
        entity_id = item.get("id") or item.get("name")
        if entity_id:
            unique_entities.add(entity_id)
        if item.get("type") in entity_types:
            type_hits += 1
        conf = item.get("confidence")
        if conf is not None:
            confidences.append(float(conf))

    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
    penalty = length_penalty * len(text) if length_penalty else 0.0
    return (
        base_score
        + entity_weight * len(unique_entities)
        + type_weight * type_hits
        + confidence_weight * avg_conf
        - penalty
    )


def _apply_heuristic_rerank(
    passages: List[Dict[str, Any]],
    entity_types: List[str],
    entity_weight: float,
    type_weight: float,
    confidence_weight: float,
    length_penalty: float,
) -> List[Dict[str, Any]]:
    for passage in passages:
        passage["rerank_score"] = _compute_heuristic_rerank_score(
            passage,
            entity_types,
            entity_weight,
            type_weight,
            confidence_weight,
            length_penalty,
        )
    return sorted(passages, key=lambda row: row.get("rerank_score", 0.0), reverse=True)


def fetch_paragraph_by_source(
    source_id: str,
    paragraph_id: int,
    project_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    for store, owned in _graph_store_candidates(project_id):
        try:
            with store.session() as session:
                result = session.run(
                    """
                    MATCH (p:Paragraph {source_id: $source_id, paragraph_id: $paragraph_id})
                    WHERE $project_id_normalized IS NULL OR
                          p.project_id_normalized = $project_id_normalized
                    RETURN p.text AS text,
                           p.short AS short,
                           p.source_id AS source_id,
                           p.paragraph_id AS paragraph_id
                    """,
                    source_id=source_id,
                    paragraph_id=paragraph_id,
                    project_id_normalized=(project_id.strip().casefold() if project_id else None),
                )
                record = result.single()
                if record:
                    return dict(record)
        finally:
            if owned:
                store.close()
    return None




def register_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    def list_source_ids(limit=50, project_id=None) -> List[str]:
        """List available source_id values from Neo4j (Paragraph nodes)."""
        limit_val = max(0, int(limit))
        source_ids: List[str] = []
        seen = set()
        for store, owned in _graph_store_candidates(project_id):
            if len(source_ids) >= limit_val:
                break
            try:
                with store.session() as session:
                    result = session.run(
                        """
                        MATCH (p:Paragraph)
                        WHERE p.source_id IS NOT NULL
                          AND ($project_id_normalized IS NULL OR
                               p.project_id_normalized = $project_id_normalized)
                        RETURN DISTINCT p.source_id AS source_id
                        ORDER BY source_id
                        LIMIT $limit
                        """,
                        limit=limit_val - len(source_ids),
                        project_id_normalized=(project_id.strip().casefold() if project_id else None),
                    )
                    for record in result:
                        source_id = record["source_id"]
                        if source_id not in seen:
                            seen.add(source_id)
                            source_ids.append(source_id)
            finally:
                if owned:
                    store.close()
        return source_ids[:limit_val]

    @mcp.tool()
    def list_qdrant_collections(project_id=None) -> List[str]:
        """List Qdrant collections."""
        qdrant = get_qdrant()
        names = qdrant.list_collection_names()
        if not project_id:
            return names
        expected = _resolve_doc_collection(project_id)
        return [name for name in names if name == expected]

    @mcp.tool()
    def semantic_search(
        query,
        top_k=5,
        source_id=None,
        collection=None,
        project_id=None,
        max_passage_chars=None,
        include_entity_ids=True,
        include_entity_mentions=False,
    ):
        """Vector-only search in Qdrant. Returns passages without graph expansion.

        Per Phase 05 of the unified ingest/query contract plan, ``project_id``
        scopes the query to one project's shard; omit it to search across all
        projects. The Qdrant collection is resolved through the registry when
        ``project_id`` is given; the explicit ``collection`` arg still wins as
        an escape hatch.
        """
        # Type coercion to handle n8n passing strings
        query = str(query) if query else ""
        top_k = int(top_k) if top_k is not None else 5
        max_passage_chars = int(max_passage_chars) if max_passage_chars is not None else None
        include_entity_ids = bool(include_entity_ids) if include_entity_ids is not None else True
        include_entity_mentions = (
            bool(include_entity_mentions) if include_entity_mentions is not None else False
        )

        embedder = get_embedder()
        q_vec = embedder.encode([query])[0].tolist()

        payloads = qdrant_search_entity_payload(
            q_vec,
            top_k=top_k,
            source_id=source_id,
            collection=collection,
            project_id=project_id,
        )

        passages = []
        for row in payloads:
            text = row.get("text") or ""
            if max_passage_chars:
                text = text[:max_passage_chars]
            passage = {
                "text": text,
                "score": row.get("score"),
                "source_id": row.get("source_id"),
                "paragraph_id": row.get("paragraph_id"),
            }
            if include_entity_ids:
                passage["entity_ids"] = row.get("entity_ids") or []
            if include_entity_mentions:
                passage["entity_mentions"] = row.get("entity_mentions") or []
            passages.append(passage)

        return {
            "query": query,
            "top_k": top_k,
            "source_id": source_id,
            "collection": _resolve_doc_collection(project_id, collection),
            "collections_searched": _resolve_doc_collections(project_id, collection),
            "passages": passages,
        }

    @mcp.tool()
    def query_graph_rag_langextract(
        query,
        top_k=5,
        source_id=None,
        collection=None,
        include_entities=True,
        include_relations=True,
        expand_related=True,
        related_k=50,
        graph_depth=1,
        entity_types=None,
        max_passage_chars=None,
        min_score_to_expand=None,
        min_entity_occurrences=None,
        rerank=False,
        rerank_entity_weight=0.05,
        rerank_type_weight=0.1,
        rerank_confidence_weight=0.3,
        rerank_length_penalty=0.0002,
        project_id=None,
    ):
        """
        Query Qdrant for top-k passages with entity_ids payload, then fetch related
        entity context from Neo4j. Returns context only (no LLM generation).
        """
        # Type coercion to handle n8n passing strings
        query = str(query) if query else ""
        top_k = int(top_k) if top_k is not None else 5
        related_k = int(related_k) if related_k is not None else 50
        graph_depth = int(graph_depth) if graph_depth is not None else 1
        include_entities = bool(include_entities) if include_entities is not None else True
        include_relations = bool(include_relations) if include_relations is not None else True
        expand_related = bool(expand_related) if expand_related is not None else True
        rerank = bool(rerank) if rerank is not None else False
        rerank_entity_weight = float(rerank_entity_weight) if rerank_entity_weight is not None else 0.05
        rerank_type_weight = float(rerank_type_weight) if rerank_type_weight is not None else 0.1
        rerank_confidence_weight = float(rerank_confidence_weight) if rerank_confidence_weight is not None else 0.3
        rerank_length_penalty = float(rerank_length_penalty) if rerank_length_penalty is not None else 0.0002
        max_passage_chars = int(max_passage_chars) if max_passage_chars is not None else None
        min_score_to_expand = float(min_score_to_expand) if min_score_to_expand is not None else None
        min_entity_occurrences = int(min_entity_occurrences) if min_entity_occurrences is not None else None

        embedder = get_embedder()
        q_vec = embedder.encode([query])[0].tolist()

        if entity_types is None:
            entity_types = list(ENTITY_TYPES_DEFAULT)
        elif isinstance(entity_types, str):
            entity_types = [t.strip() for t in entity_types.split(",") if t.strip()]

        payloads = qdrant_search_entity_payload(
            q_vec,
            top_k=top_k,
            source_id=source_id,
            collection=collection,
            project_id=project_id,
        )

        passages = []
        entity_ids: List[str] = []
        for row in payloads:
            text = row.get("text") or ""
            if max_passage_chars:
                text = text[:max_passage_chars]
            passages.append(
                {
                    "text": text,
                    "score": row.get("score"),
                    "source_id": row.get("source_id"),
                    "paragraph_id": row.get("paragraph_id"),
                    "_entity_mentions": row.get("entity_mentions") or [],
                }
            )
            entity_ids.extend(row.get("entity_ids") or [])

        entity_ids = list(dict.fromkeys(entity_ids))
        entity_ids = _filter_entity_ids_for_expansion(
            entity_ids,
            payloads,
            min_score_to_expand=min_score_to_expand,
            min_entity_occurrences=min_entity_occurrences,
        )

        entities = (
            fetch_entities_by_ids(entity_ids, project_id=project_id)
            if include_entities else []
        )
        relations = []
        if include_relations and expand_related:
            relations = fetch_relations_with_depth(
                entity_ids, entity_types, related_k, graph_depth,
                project_id=project_id,
            )

        if rerank:
            passages = _apply_heuristic_rerank(
                passages,
                entity_types,
                rerank_entity_weight,
                rerank_type_weight,
                rerank_confidence_weight,
                rerank_length_penalty,
            )
            for passage in passages:
                passage.pop("_entity_mentions", None)
        else:
            for passage in passages:
                passage.pop("_entity_mentions", None)

        return {
            "query": query,
            "top_k": top_k,
            "source_id": source_id,
            "collection": _resolve_doc_collection(project_id, collection),
            "collections_searched": _resolve_doc_collections(project_id, collection),
            "graph_depth": graph_depth,
            "min_score_to_expand": min_score_to_expand,
            "min_entity_occurrences": min_entity_occurrences,
            "rerank_applied": rerank,
            "rerank_strategy": "heuristic" if rerank else None,
            "passages": passages,
            "entities": entities,
            "relations": relations,
        }

    @mcp.tool()
    def get_paragraph_text(
        source_id,
        paragraph_id,
        project_id=None,
    ):
        """Fetch a paragraph's text by source_id + paragraph_id from Neo4j."""
        # Type coercion to handle n8n passing strings
        source_id = str(source_id) if source_id else None
        paragraph_id = int(paragraph_id) if paragraph_id is not None else 0
        
        if not source_id:
            return {"warning": "source_id is required."}
        record = fetch_paragraph_by_source(
            source_id, paragraph_id, project_id=project_id
        )
        if not record:
            return {
                "source_id": source_id,
                "paragraph_id": paragraph_id,
                "text": None,
                "warning": "Paragraph not found.",
            }
        return record


if __name__ == "__main__":
    force_quit = {"armed": False}

    def _handle_sigint(signum, _frame) -> None:
        if force_quit["armed"]:
            print("Force quitting now.")
            os._exit(0)
        force_quit["armed"] = True
        if signum == signal.SIGTERM:
            print("Received SIGTERM. Send again to force quit.")
        else:
            print("Received SIGINT. Press Ctrl+C again to force quit.")

    signal.signal(signal.SIGINT, _handle_sigint)
    signal.signal(signal.SIGTERM, _handle_sigint)
    if hasattr(signal, "SIGQUIT"):
        signal.signal(signal.SIGQUIT, _handle_sigint)

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="streamable-http",
    )
    parser.add_argument("--host", default=os.getenv("FASTMCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("FASTMCP_PORT", "8789")))
    parser.add_argument(
        "--path",
        dest="stream_path",
        default=os.getenv("FASTMCP_STREAMABLE_HTTP_PATH", "/mcp"),
        help="Streamable HTTP path",
    )
    parser.add_argument(
        "--stream-path",
        dest="stream_path",
        default=os.getenv("FASTMCP_STREAMABLE_HTTP_PATH", "/mcp"),
        help="Streamable HTTP path (deprecated, use --path)",
    )
    args = parser.parse_args()

    mcp = FastMCP(
        MCP_NAME,
        host=args.host,
        port=args.port,
        streamable_http_path=args.stream_path,
        stateless_http=True,
        json_response=True,
    )
    register_tools(mcp)
    transport = args.transport
    endpoint = f"http://{args.host}:{args.port}{args.stream_path}"
    print(f"Starting MCP server: {MCP_NAME}")
    print(f"Transport: {transport}")
    if transport == "streamable-http":
        print(f"Endpoint: {endpoint}")
    else:
        print("Endpoint: (stdio)")
    mcp.run(transport=args.transport)
