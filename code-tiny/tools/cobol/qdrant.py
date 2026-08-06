"""Optional project-scoped Qdrant indexing for COBOL semantic nodes."""

from __future__ import annotations

import re
import uuid
from typing import Any, Iterable

from tools.common.local_qdrant import delete_by_filter, ensure_collection, get_code_qdrant_store

from tools.common.project_scope import (
    PROJECT_ID_NORMALIZED_FIELD,
    project_id_lookup_key,
)

from .models import AnalysisResult


SEARCHABLE_LABELS = frozenset({
    "CobolProgram",
    "CobolSection",
    "CobolParagraph",
    "CobolDataItem",
    "CobolCopybook",
    "CobolFile",
    "CobolSqlStatement",
    "CobolCicsCommand",
})
_COLLECTION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,255}$")


def _validate_target(url: str, collection: str) -> None:
    if not _COLLECTION_RE.fullmatch(collection):
        raise ValueError("Qdrant collection must contain only letters, digits, '_', '-', or '.'")
    get_code_qdrant_store(url)


def point_id(node_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, node_id))


def semantic_documents(result: AnalysisResult, *, max_chars: int = 800) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for node in result.nodes:
        if node.label not in SEARCHABLE_LABELS:
            continue
        detail = str(node.properties.get("raw_text") or node.properties.get("code") or "")
        text = "\n".join(
            value for value in (
                f"{node.label}: {node.name}",
                str(node.properties.get("qualified_name") or ""),
                f"file: {node.file_path}",
                detail,
            )
            if value
        )
        if max_chars > 0:
            text = text[:max_chars]
        documents.append({
            "id": point_id(node.id),
            "node_id": node.id,
            "text": text,
            "payload": {
                "symbol_id": node.id,
                "name": node.name,
                "qualified_name": node.properties.get("qualified_name", node.name),
                "kind": node.label,
                "file_path": node.file_path,
                "start_line": node.evidence.start_line,
                "end_line": node.evidence.end_line,
                "project_id": result.project_id,
                PROJECT_ID_NORMALIZED_FIELD: project_id_lookup_key(result.project_id),
                "language": "cobol",
                "code": detail[:max_chars] if max_chars > 0 else detail,
            },
        })
    return documents


def _delete_stale(
    store,
    *,
    url: str,
    collection: str,
    project_id: str,
    keep_ids: list[str],
    cleanup_paths: Iterable[str],
    full_replace: bool,
) -> None:
    must: list[dict[str, Any]] = [{
        "key": PROJECT_ID_NORMALIZED_FIELD,
        "match": {"value": project_id_lookup_key(project_id)},
    }]
    paths = sorted(set(cleanup_paths))
    if not full_replace:
        if not paths:
            return
        must.append({"key": "file_path", "match": {"any": paths}})
    point_filter: dict[str, Any] = {"must": must}
    if keep_ids:
        point_filter["must_not"] = [{"has_id": keep_ids}]
    del url
    delete_by_filter(store, collection, point_filter)


def sync_qdrant(
    result: AnalysisResult,
    *,
    url: str,
    collection: str,
    model_name: str,
    device: str = "cpu",
    batch_size: int = 8,
    max_chars: int = 800,
    cleanup_paths: Iterable[str] = (),
    full_replace: bool = False,
    verbose: bool = False,
) -> int:
    """Embed first, then upsert and remove only stale points in the affected scope."""
    _validate_target(url, collection)
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError("Qdrant indexing requires sentence-transformers") from exc

    documents = semantic_documents(result, max_chars=max_chars)
    if not documents:
        return 0
    trust_remote_code = "jina" in model_name.lower()
    model = SentenceTransformer(model_name, device=device, trust_remote_code=trust_remote_code)
    vectors = model.encode(
        [item["text"] for item in documents],
        batch_size=max(1, batch_size),
        show_progress_bar=verbose,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )
    vector_size = int(vectors.shape[1])
    store = get_code_qdrant_store(url)
    ensure_collection(store, collection, vector_size)
    points = [
        {"id": document["id"], "vector": vector.tolist(), "payload": document["payload"]}
        for document, vector in zip(documents, vectors)
    ]
    for start in range(0, len(points), max(1, batch_size)):
        store.upsert(collection, points[start:start + max(1, batch_size)], wait=True)
    _delete_stale(
        store,
        url=url,
        collection=collection,
        project_id=result.project_id,
        keep_ids=[item["id"] for item in documents],
        cleanup_paths=cleanup_paths,
        full_replace=full_replace,
    )
    return len(points)
