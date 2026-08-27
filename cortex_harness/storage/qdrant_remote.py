"""Qdrant remote-mode adapter for Cortex Harness.

Mirrors the public API of :class:`LocalQdrantStore` (same method names,
same parameter conventions, same dataclass-style return types) so callers
can swap a local file backend for a remote server backend without rewriting
queries.

Key differences from :class:`LocalQdrantStore`:

* The constructor takes a URL (and optional API key) instead of a resolved
  filesystem path.
* No ``StorageLease`` is acquired — the server side owns concurrency.
* Clients are cached per ``(url, api_key)`` tuple so projects sharing the
  same backend (but with different credentials) do not collide, while
  repeated lookups for the same URL reuse a single TCP connection pool.
* Health-check failures raise :class:`BackendConnectionError` so MCP tools
  can return a structured error to the caller.
"""

from __future__ import annotations

import atexit
import threading
from typing import Any, Iterable, Mapping, Optional, Sequence

from .config import QdrantStorageRole
from .errors import BackendConnectionError


try:  # pragma: no cover - exercised only when qdrant-client is installed
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qmodels
except ImportError as exc:  # pragma: no cover - import guard
    raise ImportError(
        "Remote Qdrant storage requires the 'qdrant-client' package. "
        "Install dependencies from requirements.txt or pyproject.toml."
    ) from exc


# Per-process client cache. Keyed by ``(url, api_key)`` so two projects
# pointing at the same URL with different credentials do not share a client.
_remote_client_lock = threading.Lock()
_remote_clients: dict[tuple[str, Optional[str]], "QdrantClient"] = {}


def get_remote_client(url: str, *, api_key: Optional[str] = None) -> "QdrantClient":
    """Return a cached :class:`QdrantClient` for ``url``.

    Two callers asking for the same ``(url, api_key)`` pair share one client;
    callers asking for the same URL with different keys get distinct clients.
    """
    cache_key = (url, api_key)
    with _remote_client_lock:
        existing = _remote_clients.get(cache_key)
        if existing is not None:
            return existing
        # Avoid constructor-time version probes: backend selection and target
        # fingerprinting must complete before the first network operation.
        # Explicit health checks and real store operations surface connection
        # or compatibility errors at their typed boundaries.
        client = QdrantClient(
            url=url,
            api_key=api_key,
            check_compatibility=False,
        )
        _remote_clients[cache_key] = client
        return client


def reset_remote_clients() -> None:
    """Close every cached remote client (test-only convenience)."""
    with _remote_client_lock:
        for client in _remote_clients.values():
            try:
                client.close()
            except Exception:  # pragma: no cover - best-effort close
                pass
        _remote_clients.clear()


# Ensure cached clients are closed before interpreter shutdown so the
# underlying HTTP connection pool does not log noise during teardown.
atexit.register(reset_remote_clients)


class RemoteQdrantStore:
    """Single owner of vector operations for one remote Qdrant backend.

    Mirrors :class:`cortex_harness.storage.LocalQdrantStore`. Every method
    forwards to a cached :class:`QdrantClient`; no caller-side code needs to
    know whether the backing service is local or remote.
    """

    def __init__(
        self,
        url: str,
        *,
        api_key: Optional[str] = None,
        timeout: Optional[float] = None,
        role: Optional[QdrantStorageRole] = None,
    ) -> None:
        self._url = url
        self._api_key = api_key
        self._role = role or QdrantStorageRole.CODE
        self._owns_client = False
        # Use ``get_collections``-time configuration when timeout=None so we
        # honor the qdrant_client default. Override only when explicitly
        # requested by the caller.
        client = get_remote_client(url, api_key=api_key)
        if timeout is not None:
            # Some qdrant-client versions expose ``timeout`` on the client
            # itself; we set it best-effort.
            try:
                client.timeout = timeout
            except AttributeError:
                pass
        self._client = client

    @property
    def client(self) -> "QdrantClient":
        return self._client

    @property
    def role(self) -> QdrantStorageRole:
        return self._role

    @property
    def url(self) -> str:
        return self._url

    def __repr__(self) -> str:
        return f"RemoteQdrantStore(url={self._url!r}, api_key=***)"

    # ── collections ─────────────────────────────────────────────────────────

    def list_collection_names(self) -> list[str]:
        return [c.name for c in self._client.get_collections().collections]

    def collection_exists(self, name: str) -> bool:
        return self._client.collection_exists(collection_name=name)

    def get_collection_info(self, name: str) -> Any:
        return self._client.get_collection(collection_name=name)

    def create_collection(
        self,
        name: str,
        *,
        vectors_config: Any,
        **kwargs: Any,
    ) -> Any:
        return self._client.create_collection(
            collection_name=name, vectors_config=vectors_config, **kwargs,
        )

    def recreate_collection(
        self,
        name: str,
        *,
        vectors_config: Any,
        **kwargs: Any,
    ) -> Any:
        return self._client.recreate_collection(
            collection_name=name, vectors_config=vectors_config, **kwargs,
        )

    def delete_collection(self, name: str) -> Any:
        return self._client.delete_collection(collection_name=name)

    # ── points ──────────────────────────────────────────────────────────────

    def upsert(
        self,
        collection_name: str,
        points: Sequence[Any],
        **kwargs: Any,
    ) -> Any:
        normalized = [
            qmodels.PointStruct(**point) if isinstance(point, Mapping) else point
            for point in points
        ]
        return self._client.upsert(
            collection_name=collection_name, points=normalized, **kwargs,
        )

    def upload_points(
        self,
        collection_name: str,
        points: Iterable[Any],
        **kwargs: Any,
    ) -> Any:
        normalized = (
            qmodels.PointStruct(**point) if isinstance(point, Mapping) else point
            for point in points
        )
        return self._client.upload_points(
            collection_name=collection_name, points=normalized, **kwargs,
        )

    def search(
        self,
        collection_name: str,
        query_vector: Sequence[float],
        *,
        limit: int = 10,
        query_filter: Any = None,
        with_payload: Any = True,
        with_vectors: Any = False,
        **kwargs: Any,
    ) -> list[Any]:
        legacy_search = getattr(self._client, "search", None)
        if callable(legacy_search):
            return legacy_search(
                collection_name=collection_name, query_vector=query_vector,
                limit=limit, query_filter=query_filter, with_payload=with_payload,
                with_vectors=with_vectors, **kwargs,
            )
        response = self._client.query_points(
            collection_name=collection_name, query=query_vector, limit=limit,
            query_filter=query_filter, with_payload=with_payload,
            with_vectors=with_vectors, **kwargs,
        )
        return list(response.points)

    def query_points(
        self,
        collection_name: str,
        *,
        query: Any = None,
        limit: int = 10,
        query_filter: Any = None,
        with_payload: Any = True,
        with_vectors: Any = False,
        **kwargs: Any,
    ) -> Any:
        return self._client.query_points(
            collection_name=collection_name,
            query=query, limit=limit, query_filter=query_filter,
            with_payload=with_payload, with_vectors=with_vectors, **kwargs,
        )

    def scroll(
        self,
        collection_name: str,
        *,
        scroll_filter: Any = None,
        limit: int = 100,
        with_payload: Any = True,
        with_vectors: Any = False,
        offset: Any = None,
        **kwargs: Any,
    ) -> Any:
        return self._client.scroll(
            collection_name=collection_name,
            scroll_filter=scroll_filter, limit=limit,
            with_payload=with_payload, with_vectors=with_vectors,
            offset=offset, **kwargs,
        )

    def retrieve(
        self,
        collection_name: str,
        ids: Sequence[Any],
        *,
        with_payload: Any = True,
        with_vectors: Any = False,
        **kwargs: Any,
    ) -> list[Any]:
        return self._client.retrieve(
            collection_name=collection_name, ids=ids,
            with_payload=with_payload, with_vectors=with_vectors, **kwargs,
        )

    def count(
        self,
        collection_name: str,
        *,
        count_filter: Any = None,
        exact: bool = True,
        **kwargs: Any,
    ) -> Any:
        return self._client.count(
            collection_name=collection_name,
            count_filter=count_filter, exact=exact, **kwargs,
        )

    def delete(
        self,
        collection_name: str,
        *,
        points_selector: Any = None,
        points_selector_ids: Any = None,
        filter_selector: Any = None,
        **kwargs: Any,
    ) -> Any:
        selector = points_selector
        if selector is None and points_selector_ids is not None:
            selector = qmodels.PointIdsList(points=list(points_selector_ids))
        if selector is None and filter_selector is not None:
            selector = filter_selector
        if selector is None:
            raise ValueError("delete requires point IDs or a filter selector")
        return self._client.delete(
            collection_name=collection_name, points_selector=selector, **kwargs,
        )

    def set_payload(
        self,
        collection_name: str,
        payload: Mapping[str, Any],
        *,
        points: Any = None,
        filter: Any = None,
        **kwargs: Any,
    ) -> Any:
        selector = points if points is not None else filter
        if selector is None:
            raise ValueError("set_payload requires point IDs or a filter")
        return self._client.set_payload(
            collection_name=collection_name, payload=payload,
            points=selector, **kwargs,
        )

    def overwrite_payload(
        self,
        collection_name: str,
        payload: Mapping[str, Any],
        *,
        points: Any = None,
        filter: Any = None,
        **kwargs: Any,
    ) -> Any:
        selector = points if points is not None else filter
        if selector is None:
            raise ValueError("overwrite_payload requires point IDs or a filter")
        return self._client.overwrite_payload(
            collection_name=collection_name, payload=payload,
            points=selector, **kwargs,
        )

    def create_payload_index(
        self,
        collection_name: str,
        field_name: str,
        *,
        field_schema: Any = None,
        field_type: Any = None,
        **kwargs: Any,
    ) -> Any:
        if field_schema is None and field_type is None:
            field_schema = qmodels.PayloadSchemaType.KEYWORD
        return self._client.create_payload_index(
            collection_name=collection_name, field_name=field_name,
            field_schema=field_schema, field_type=field_type, **kwargs,
        )

    # ── lifecycle ───────────────────────────────────────────────────────────

    def check_connection(self) -> bool:
        """Return ``True`` when the remote server is reachable.

        Never raises — failures simply mean the server is unreachable. Use
        :class:`BackendConnectionError` (which callers should handle) for
        operations that demand an explicit failure signal.
        """
        try:
            self._client.get_collections()
            return True
        except Exception:
            return False

    def ensure_reachable(self) -> None:
        """Raise :class:`BackendConnectionError` if the server is not reachable.

        Convenience for callers that prefer exceptions over booleans. MCP
        tools catch this and translate to a structured error.
        """
        try:
            self._client.get_collections()
        except Exception as exc:
            raise BackendConnectionError("Qdrant", self._url, exc) from exc

    def close(self) -> None:
        """Drop *this* store's client from the cache.

        Distinct callers using the same URL still observe the cached
        connection unless they too invoke ``close``. ``reset_remote_clients``
        closes everything at once (test helper + interpreter exit).
        """
        with _remote_client_lock:
            existing = _remote_clients.pop((self._url, self._api_key), None)
        if existing is not None:
            try:
                existing.close()
            except Exception:  # pragma: no cover - best-effort close
                pass
