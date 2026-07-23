"""Shared, incremental-safe Qdrant sync for primary code analyzers.

The transport stays independent of parser models.  Analyzers map their
canonical rows to :class:`VectorDocument` values and this module owns the
bounded text, embedding, upsert, retry, and stale-point cleanup contract.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from tools.common.project_scope import (
    PROJECT_ID_NORMALIZED_FIELD,
    project_id_lookup_key,
)
from urllib.parse import urlparse
import uuid


_COLLECTION_RE = re.compile(r"[A-Za-z0-9_.-]+")
_QUOTED_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?im)([\"']?(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret|token)[\"']?"
    r"\s*[:=]\s*)(?P<quote>[\"'])(.*?)(?P=quote)",
    re.DOTALL,
)
_UNQUOTED_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?im)([\"']?(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|secret|token)[\"']?"
    r"\s*[:=]\s*)([^\s,;]+)",
)
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.DOTALL,
)
_POINT_NAMESPACE = uuid.UUID("6694a056-5f64-5e1a-b5fc-b1ef4ec630db")
MAX_VECTOR_TEXT_CHARS = 16_000


@dataclass(frozen=True)
class VectorDocument:
    """A parser-independent semantic document ready for embedding."""

    id: str
    text: str
    payload: Dict[str, Any]


def vector_configured(url: Optional[str]) -> bool:
    """Return whether vector persistence was explicitly configured."""

    return bool((url or "").strip())


def validate_target(url: str, collection: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Qdrant URL must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("Qdrant URL must not embed credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("Qdrant URL must not contain a query string or fragment")
    if not collection or not _COLLECTION_RE.fullmatch(collection):
        raise ValueError("Qdrant collection must contain only letters, digits, '_', '-', or '.'")


def redact_text(value: Any) -> str:
    """Remove common credential material before embedding or persistence."""

    text = str(value or "")
    text = _PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", text)
    text = _QUOTED_SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group('quote')}[REDACTED]{match.group('quote')}",
        text,
    )
    return _UNQUOTED_SECRET_ASSIGNMENT_RE.sub(r"\1[REDACTED]", text)


def deterministic_point_id(parser: str, project_id: str, symbol_id: str, root_scope: str) -> str:
    """Return a stable UUID based only on semantic identity and scope."""

    identity = "\0".join(
        (parser.strip().lower(), project_id.strip(), root_scope.strip(), symbol_id.strip())
    )
    if not all((parser.strip(), project_id.strip(), root_scope.strip(), symbol_id.strip())):
        raise ValueError("parser, project_id, root_scope, and symbol_id are required for vector identity")
    return str(uuid.uuid5(_POINT_NAMESPACE, identity))


def _bounded_text(parts: Iterable[Any], max_chars: int) -> str:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    text = redact_text("\n".join(str(part) for part in parts if part not in (None, "")))
    return text[:min(max_chars, MAX_VECTOR_TEXT_CHARS)]


def documents_from_payloads(
    payloads: Iterable[Mapping[str, Any]],
    *,
    parser: str,
    root_scope: str,
    max_chars: int,
) -> list[VectorDocument]:
    """Map canonical analyzer payloads to the shared vector contract."""

    documents: list[VectorDocument] = []
    for source in payloads:
        symbol_id = str(source.get("symbol_id") or source.get("id") or "").strip()
        project_id = str(source.get("project_id") or "").strip()
        if not symbol_id or not project_id:
            raise ValueError("vector payloads require symbol_id/id and project_id")
        file_path = str(source.get("file_path") or source.get("path") or "").replace("\\", "/")
        name = str(source.get("name") or file_path or symbol_id)
        qualified_name = str(source.get("qualified_name") or name)
        node_type = str(source.get("node_type") or source.get("kind") or "symbol")
        text = _bounded_text(
            (
                f"{node_type}: {qualified_name}",
                f"file: {file_path}" if file_path else "",
                source.get("summary"),
                source.get("comment"),
                source.get("note"),
                source.get("code"),
            ),
            max_chars,
        )
        payload: Dict[str, Any] = {
            "node_type": node_type,
            "symbol_id": symbol_id,
            "project_id": project_id,
            PROJECT_ID_NORMALIZED_FIELD: project_id_lookup_key(project_id),
            "project_name": str(source.get("project_name") or project_id),
            "language": str(source.get("language") or parser),
            "repo": str(source.get("repo") or ""),
            "file_path": file_path,
            "name": name,
            "qualified_name": qualified_name,
            "parser": parser,
            "root_scope": root_scope,
            "text": text,
        }
        for key in ("start_line", "end_line"):
            value = source.get(key)
            if value is not None:
                payload[key] = value
        documents.append(
            VectorDocument(
                id=deterministic_point_id(parser, project_id, symbol_id, root_scope),
                text=text,
                payload=payload,
            )
        )
    return documents


def documents_from_rows(
    rows: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    parser: str,
    root_scope: str,
    max_chars: int,
) -> list[VectorDocument]:
    """Map canonical language-writer rows without transport branching."""

    payloads: list[Mapping[str, Any]] = []
    for category, values in rows.items():
        if category in {"relations", "calls"}:
            continue
        default_type = category[:-1] if category.endswith("s") else category
        for value in values:
            item = dict(value)
            item.setdefault("symbol_id", item.get("id"))
            item.setdefault("node_type", item.get("kind") or default_type)
            payloads.append(item)
    return documents_from_payloads(
        payloads,
        parser=parser,
        root_scope=root_scope,
        max_chars=max_chars,
    )


def _request_with_retry(
    requests_module: Any,
    method: str,
    url: str,
    *,
    timeout: float,
    retries: int,
    retry_sleep: float,
    allowed_statuses: Sequence[int] = (),
    **kwargs: Any,
) -> Any:
    request_exception = getattr(requests_module, "RequestException", Exception)
    for attempt in range(max(0, retries) + 1):
        try:
            response = requests_module.request(method, url, timeout=timeout, **kwargs)
            if response.status_code in allowed_statuses:
                return response
            response.raise_for_status()
            return response
        except request_exception:
            if attempt >= max(0, retries):
                raise
            time.sleep(max(0.0, retry_sleep))
    raise RuntimeError("unreachable Qdrant retry state")


def _extract_vector_size(response: Any) -> Optional[int]:
    try:
        vectors = response.json()["result"]["config"]["params"]["vectors"]
    except (KeyError, TypeError, ValueError):
        return None
    if isinstance(vectors, Mapping) and isinstance(vectors.get("size"), int):
        return int(vectors["size"])
    return None


def _ensure_collection(
    requests_module: Any,
    *,
    url: str,
    collection: str,
    vector_size: int,
    timeout: float,
    retries: int,
    retry_sleep: float,
) -> None:
    endpoint = f"{url.rstrip('/')}/collections/{collection}"
    response = _request_with_retry(
        requests_module,
        "GET",
        endpoint,
        timeout=timeout,
        retries=retries,
        retry_sleep=retry_sleep,
        allowed_statuses=(404,),
    )
    if response.status_code == 200:
        existing_size = _extract_vector_size(response)
        if existing_size is not None and existing_size != vector_size:
            raise ValueError(
                f"Qdrant collection {collection!r} has vector size {existing_size}, "
                f"but the configured embedder produces {vector_size}"
            )
        return
    if response.status_code != 404:
        response.raise_for_status()
    _request_with_retry(
        requests_module,
        "PUT",
        endpoint,
        json={"vectors": {"size": vector_size, "distance": "Cosine"}},
        timeout=timeout,
        retries=retries,
        retry_sleep=retry_sleep,
    )


def _ensure_project_scope_index(
    requests_module: Any,
    *,
    url: str,
    collection: str,
    timeout: float,
    retries: int,
    retry_sleep: float,
) -> None:
    _request_with_retry(
        requests_module,
        "PUT",
        f"{url.rstrip('/')}/collections/{collection}/index?wait=true",
        json={
            "field_name": PROJECT_ID_NORMALIZED_FIELD,
            "field_schema": "keyword",
        },
        timeout=timeout,
        retries=retries,
        retry_sleep=retry_sleep,
    )


def _delete_stale(
    requests_module: Any,
    *,
    url: str,
    collection: str,
    parser: str,
    project_id: str,
    root_scope: str,
    keep_ids: Sequence[str],
    cleanup_paths: Iterable[str],
    full_replace: bool,
    timeout: float,
    retries: int,
    retry_sleep: float,
) -> None:
    paths = sorted({str(path).replace("\\", "/") for path in cleanup_paths if path})
    if not full_replace and not paths:
        return
    must: list[dict[str, Any]] = [
        {
            "key": PROJECT_ID_NORMALIZED_FIELD,
            "match": {"value": project_id_lookup_key(project_id)},
        },
        {"key": "parser", "match": {"value": parser}},
        {"key": "root_scope", "match": {"value": root_scope}},
    ]
    if not full_replace:
        must.append({"key": "file_path", "match": {"any": paths}})
    point_filter: dict[str, Any] = {"must": must}
    if keep_ids:
        point_filter["must_not"] = [{"has_id": list(keep_ids)}]
    endpoint = f"{url.rstrip('/')}/collections/{collection}/points/delete?wait=true"
    try:
        _request_with_retry(
            requests_module,
            "POST",
            endpoint,
            json={"filter": point_filter},
            timeout=timeout,
            retries=retries,
            retry_sleep=retry_sleep,
        )
    except getattr(requests_module, "RequestException", Exception) as exc:
        response = getattr(exc, "response", None)
        if getattr(response, "status_code", None) == 404:
            return
        raise


def sync_vector_documents(
    documents: Sequence[VectorDocument],
    *,
    url: str,
    collection: str,
    model_name: str,
    device: str,
    embed_batch_size: int,
    qdrant_batch_size: int,
    parser: str,
    project_id: str,
    root_scope: str,
    cleanup_paths: Iterable[str] = (),
    full_replace: bool = False,
    timeout: float = 300.0,
    retries: int = 3,
    retry_sleep: float = 2.0,
    verbose: bool = False,
    requests_module: Any = None,
    embedder_factory: Any = None,
) -> int:
    """Embed, upsert, then delete stale points inside the requested scope."""

    validate_target(url, collection)
    if requests_module is None:
        try:
            import requests as requests_module  # type: ignore[no-redef]
        except ImportError as exc:  # pragma: no cover - dependency environment
            raise RuntimeError("Qdrant indexing requires requests") from exc

    for document in documents:
        payload = document.payload
        if (
            str(payload.get("project_id") or "") != project_id
            or str(payload.get("parser") or "") != parser
            or str(payload.get("root_scope") or "") != root_scope
        ):
            raise ValueError("Vector document payload scope does not match the requested sync scope")

    if documents:
        if not model_name.strip():
            raise ValueError("An embedding model is required when Qdrant is configured")
        if embedder_factory is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:  # pragma: no cover - dependency environment
                raise RuntimeError("Qdrant indexing requires sentence-transformers") from exc
            embedder_factory = SentenceTransformer
        model = embedder_factory(
            model_name,
            device=device,
            trust_remote_code="jina" in model_name.lower(),
        )
        vectors = model.encode(
            [document.text for document in documents],
            batch_size=max(1, embed_batch_size),
            show_progress_bar=verbose,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        if len(vectors) != len(documents) or not len(vectors):
            raise RuntimeError("Embedding output count does not match vector documents")
        vector_size = len(vectors[0])
        if vector_size <= 0:
            raise RuntimeError("Embedding model returned empty vectors")
        _ensure_collection(
            requests_module,
            url=url,
            collection=collection,
            vector_size=vector_size,
            timeout=timeout,
            retries=retries,
            retry_sleep=retry_sleep,
        )
        _ensure_project_scope_index(
            requests_module,
            url=url,
            collection=collection,
            timeout=timeout,
            retries=retries,
            retry_sleep=retry_sleep,
        )
        points = []
        for document, vector in zip(documents, vectors):
            values = vector.tolist() if hasattr(vector, "tolist") else list(vector)
            points.append({"id": document.id, "vector": values, "payload": document.payload})
        size = max(1, qdrant_batch_size)
        for start in range(0, len(points), size):
            _request_with_retry(
                requests_module,
                "PUT",
                f"{url.rstrip('/')}/collections/{collection}/points?wait=true",
                json={"points": points[start:start + size]},
                timeout=timeout,
                retries=retries,
                retry_sleep=retry_sleep,
            )

    _delete_stale(
        requests_module,
        url=url,
        collection=collection,
        parser=parser,
        project_id=project_id,
        root_scope=root_scope,
        keep_ids=[document.id for document in documents],
        cleanup_paths=cleanup_paths,
        full_replace=full_replace,
        timeout=timeout,
        retries=retries,
        retry_sleep=retry_sleep,
    )
    return len(documents)
