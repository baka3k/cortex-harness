"""Storage factory — single backend-resolution entry point.

The :class:`StorageFactory` accepts a :class:`ProjectTargets` (already
parsed by ``tools.common.project_registry``) and a :class:`ResolvedStorage`
(``resolve_storage()`` result) and exposes two methods:

* :meth:`StorageFactory.get_qdrant_store` returns either
  :class:`LocalQdrantStore` or :class:`RemoteQdrantStore` based on the
  project's ``storage_backend`` choice.
* :meth:`StorageFactory.get_falkordb_driver` returns a ``FalkorDBDriver``
  opened against the matching local ``.rdb`` path or remote URI.

Mixed backend is supported: a project may choose ``storage_backend: remote``
yet still leave ``remote.falkordb_uri`` unset (and vice versa). In that case
the missing component falls back to the local default rather than raising,
because the operator's intent is usually to migrate one component at a
time.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Iterable,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    runtime_checkable,
)

from .config import (
    BackendMode,
    QdrantStorageRole,
    RemoteStorageConfig,
    ResolvedStorage,
    StorageRole,
    validate_backend_config,
)
from .qdrant import LocalQdrantStore
from .qdrant_remote import RemoteQdrantStore
from .targets import (
    EffectiveStorageTarget,
    EffectiveStorageTopology,
    local_graph_target,
    local_vector_target,
    remote_graph_target,
    remote_vector_target,
)


if TYPE_CHECKING:  # pragma: no cover - circular import guard
    from tools.common.project_registry import ProjectTargets
    from tools.graph.driver.falkordb_driver import FalkorDBDriver


@runtime_checkable
class QdrantStore(Protocol):
    """Structural contract shared by local-file and remote Qdrant adapters."""

    @property
    def client(self) -> Any: ...

    @property
    def role(self) -> QdrantStorageRole: ...

    def list_collection_names(self) -> list[str]: ...

    def collection_exists(self, name: str) -> bool: ...

    def get_collection_info(self, name: str) -> Any: ...

    def create_collection(
        self,
        name: str,
        *,
        vectors_config: Any,
        **kwargs: Any,
    ) -> Any: ...

    def recreate_collection(
        self,
        name: str,
        *,
        vectors_config: Any,
        **kwargs: Any,
    ) -> Any: ...

    def delete_collection(self, name: str) -> Any: ...

    def upsert(
        self,
        collection_name: str,
        points: Sequence[Any],
        **kwargs: Any,
    ) -> Any: ...

    def upload_points(
        self,
        collection_name: str,
        points: Iterable[Any],
        **kwargs: Any,
    ) -> Any: ...

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
    ) -> list[Any]: ...

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
    ) -> Any: ...

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
    ) -> Any: ...

    def retrieve(
        self,
        collection_name: str,
        ids: Sequence[Any],
        *,
        with_payload: Any = True,
        with_vectors: Any = False,
        **kwargs: Any,
    ) -> list[Any]: ...

    def count(
        self,
        collection_name: str,
        *,
        count_filter: Any = None,
        exact: bool = True,
        **kwargs: Any,
    ) -> Any: ...

    def delete(
        self,
        collection_name: str,
        *,
        points_selector: Any = None,
        points_selector_ids: Any = None,
        filter_selector: Any = None,
        **kwargs: Any,
    ) -> Any: ...

    def set_payload(
        self,
        collection_name: str,
        payload: Mapping[str, Any],
        *,
        points: Any = None,
        filter: Any = None,
        **kwargs: Any,
    ) -> Any: ...

    def overwrite_payload(
        self,
        collection_name: str,
        payload: Mapping[str, Any],
        *,
        points: Any = None,
        filter: Any = None,
        **kwargs: Any,
    ) -> Any: ...

    def create_payload_index(
        self,
        collection_name: str,
        field_name: str,
        *,
        field_schema: Any = None,
        field_type: Any = None,
        **kwargs: Any,
    ) -> Any: ...

    def close(self) -> None: ...


# Emergency rollback: force every project onto the local backend even when
# ``storage_backend`` says otherwise. Operators flip this on to recover from
# a remote-server outage without editing project configs.
ENV_FORCE_LOCAL = "CORTEX_STORAGE_BACKEND_FORCE_LOCAL"


class StorageFactory:
    """Resolve the correct backend instances for one project.

    Construct directly or use :meth:`from_targets` to bind a parsed
    :class:`ProjectTargets` plus a :class:`ResolvedStorage`. The factory is
    cheap to construct; backend clients themselves are cached by the
    adapter classes (``LocalQdrantStore`` per role, ``RemoteQdrantStore``
    per ``(url, api_key)``).
    """

    def __init__(
        self,
        *,
        backend_mode: BackendMode,
        resolved: ResolvedStorage,
        remote: Optional[RemoteStorageConfig] = None,
        project_scope: str = "unbound",
        code_graph: Optional[str] = None,
        doc_graph: Optional[str] = None,
        code_collection: Optional[str] = None,
        doc_collection: Optional[str] = None,
    ) -> None:
        # Emergency rollback: force local even when remote is requested.
        self._requested_mode = backend_mode
        self._forced_local = bool(os.getenv(ENV_FORCE_LOCAL))
        if self._forced_local:
            self._mode = BackendMode.LOCAL
            self._remote: Optional[RemoteStorageConfig] = None
        else:
            self._mode = backend_mode
            self._remote = remote if backend_mode == BackendMode.REMOTE else None
        self._resolved = resolved
        self._project_scope = str(project_scope or "unbound")
        self._code_graph = code_graph or resolved.code_graph
        self._doc_graph = doc_graph or resolved.doc_graph
        self._code_collection = code_collection or resolved.code_collection
        self._doc_collection = doc_collection or resolved.doc_collection

    @classmethod
    def from_targets(
        cls,
        targets: "ProjectTargets",
        resolved: ResolvedStorage,
    ) -> "StorageFactory":
        """Build a factory from a :class:`ProjectTargets` plus resolved paths.

        ``resolved`` carries the local paths even when ``storage_backend`` is
        ``remote`` — :meth:`get_falkordb_driver` falls back to the local
        path when ``remote.falkordb_uri`` is not configured, and
        :meth:`get_qdrant_store` likewise.
        """
        try:
            mode = BackendMode(targets.storage_backend)
        except ValueError as exc:
            # ``storage_backend`` should already be validated at registry
            # level; surface a clear error if it isn't.
            raise ValueError(
                f"unknown storage_backend {targets.storage_backend!r} on "
                f"project {targets.project_id!r}"
            ) from exc
        remote: Optional[RemoteStorageConfig] = None
        if mode == BackendMode.REMOTE:
            _, remote = validate_backend_config("remote", targets.remote_config)
        return cls(
            backend_mode=mode,
            resolved=resolved,
            remote=remote,
            project_scope=targets.project_id,
            code_graph=getattr(targets, "code_graph", None),
            doc_graph=getattr(targets, "doc_graph", None),
            code_collection=getattr(targets, "code_qdrant_collection", None),
            doc_collection=getattr(targets, "doc_qdrant_collection", None),
        )

    @property
    def backend_mode(self) -> BackendMode:
        return self._mode

    @property
    def resolved(self) -> ResolvedStorage:
        return self._resolved

    @property
    def requested_backend_mode(self) -> BackendMode:
        return self._requested_mode

    @property
    def forced_local(self) -> bool:
        return self._forced_local

    def is_remote(self) -> bool:
        return self._mode == BackendMode.REMOTE

    # ── Effective target identity ───────────────────────────────────────────

    @staticmethod
    def _role_value(role: StorageRole | QdrantStorageRole | str) -> str:
        value = role.value if hasattr(role, "value") else str(role)
        return "doc" if value == "document" else value

    def effective_graph_target(
        self,
        graph_name: Optional[str] = None,
        *,
        role: StorageRole | QdrantStorageRole | str = StorageRole.CODE,
    ) -> EffectiveStorageTarget:
        """Describe the graph target selected before any connection attempt.

        A missing remote URI resolves to the explicit file-backed component at
        this boundary.  Later connection/auth failures never call this method
        again and therefore cannot change the target.
        """

        role_value = self._role_value(role)
        namespace = graph_name or (
            self._doc_graph if role_value == StorageRole.DOCUMENT.value else self._code_graph
        )
        if not namespace:
            raise ValueError("effective graph target requires a graph name")
        if self._mode == BackendMode.REMOTE and self._remote and self._remote.falkordb_uri:
            return remote_graph_target(
                self._remote.falkordb_uri,
                graph=namespace,
                role=role_value,
                password=self._remote.falkordb_password,
                ssl=self._remote.falkordb_ssl,
            )
        return local_graph_target(
            self._resolved.falkordb_path_for_role(role_value),
            graph=namespace,
            role=role_value,
        )

    def effective_vector_target(
        self,
        collection_name: Optional[str] = None,
        *,
        role: StorageRole | QdrantStorageRole | str = StorageRole.CODE,
    ) -> EffectiveStorageTarget:
        """Describe the effective Qdrant server or local directory."""

        role_value = self._role_value(role)
        namespace = collection_name or (
            self._doc_collection if role_value == StorageRole.DOCUMENT.value else self._code_collection
        )
        if not namespace:
            raise ValueError("effective vector target requires a collection name")
        if self._mode == BackendMode.REMOTE and self._remote and self._remote.qdrant_url:
            return remote_vector_target(
                self._remote.qdrant_url,
                collection=namespace,
                role=role_value,
                api_key=self._remote.qdrant_api_key,
            )
        return local_vector_target(
            self._resolved.path_for_role(role_value),
            collection=namespace,
            role=role_value,
        )

    def effective_topology(
        self,
        *,
        graph_name: Optional[str] = None,
        collection_name: Optional[str] = None,
        role: StorageRole | QdrantStorageRole | str = StorageRole.CODE,
        generation_id: str = "unbound",
        project_scope: Optional[str] = None,
    ) -> EffectiveStorageTopology:
        """Return the canonical graph/vector topology used for compatibility."""

        return EffectiveStorageTopology(
            project_scope=str(project_scope or self._project_scope),
            requested_backend=self._requested_mode.value,
            forced_local=self._forced_local,
            generation_id=str(generation_id or "unbound"),
            graph=self.effective_graph_target(graph_name, role=role),
            vector=self.effective_vector_target(collection_name, role=role),
        )

    # ── Qdrant ──────────────────────────────────────────────────────────────

    def get_qdrant_store(self, role: QdrantStorageRole) -> QdrantStore:
        """Return a Qdrant store for ``role`` using the project's backend.

        Falls back to local when ``storage_backend == remote`` but
        ``remote.qdrant_url`` is unset — that way a project that is still
        ingesting into local Qdrant while migrating only the graph backend
        does not break.
        """
        if (
            self._mode == BackendMode.REMOTE
            and self._remote is not None
            and self._remote.qdrant_url
        ):
            return RemoteQdrantStore(
                url=self._remote.qdrant_url,
                api_key=self._remote.qdrant_api_key,
                role=role,
            )
        return LocalQdrantStore(self._resolved, role)

    # ── FalkorDB ────────────────────────────────────────────────────────────

    def get_falkordb_driver(
        self,
        graph_name: str,
        role: StorageRole = StorageRole.CODE,
    ) -> "FalkorDBDriver":
        """Return a FalkorDB driver for ``graph_name`` using the project's backend.

        Falls back to local when ``storage_backend == remote`` but
        ``remote.falkordb_uri`` is unset. The local driver receives the
        resolved ``owner_id``/``instance_id`` so the embedded lease identity
        matches what other call sites compute.
        """
        # Imported here to avoid a ``cortex_harness`` → ``code-tiny`` import
        # cycle at module load time.
        from tools.graph.driver.falkordb_driver import FalkorDBDriver

        if (
            self._mode == BackendMode.REMOTE
            and self._remote is not None
            and self._remote.falkordb_uri
        ):
            return FalkorDBDriver(
                uri=self._remote.falkordb_uri,
                password=self._remote.falkordb_password,
                ssl=self._remote.falkordb_ssl,
                graph=graph_name,
                _suppress_deprecation=True,
            )
        return FalkorDBDriver(
            path=str(self._resolved.falkordb_path_for_role(role)),
            graph=graph_name,
            owner_id=(
                self._resolved.doc_owner_id
                if role == StorageRole.DOCUMENT
                else self._resolved.code_owner_id
            ),
            instance_id=self._resolved.instance_id,
        )


def create_storage(
    targets: "ProjectTargets",
    *,
    project_root: Optional[Path] = None,
    resolved: Optional[ResolvedStorage] = None,
) -> StorageFactory:
    """One-call factory used by ingest scripts and MCP tools.

    Resolves local paths (when not already supplied) and binds them with the
    project's ``storage_backend`` choice into a :class:`StorageFactory`.
    Optional-root wrappers use the current working directory at call time.
    """
    if resolved is None:
        root = Path.cwd() if project_root is None else Path(project_root)
        resolved = resolve_storage(root)
    return StorageFactory.from_targets(targets, resolved)


def resolve_storage(project_root: Path) -> ResolvedStorage:
    """Local re-export to keep ``create_storage`` importable standalone."""
    from .config import resolve_storage as _resolve_storage

    return _resolve_storage(project_root)
