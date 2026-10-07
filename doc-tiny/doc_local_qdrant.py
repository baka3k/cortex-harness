"""Document-owner access to the shared embedded Qdrant store."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Optional
from urllib.parse import urlparse


_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from cortex_harness.storage import (  # noqa: E402
    LocalQdrantStore,
    QdrantStore,
    QdrantStorageRole,
    create_storage,
    resolve_storage,
)


class RemoteQdrantUnsupportedError(ValueError):
    """Raised when a remote locator reaches the local-only document runtime.

    Kept for backward compatibility for callers that explicitly opt into
    the legacy ``locator=...`` shape. New code should pass
    ``project_id=...`` and let :func:`get_document_qdrant_store` route
    through :class:`StorageFactory`.
    """


def get_document_qdrant_store(
    locator: Optional[str] = None,
    *,
    project_root: Optional[Path] = None,
    project_id: Optional[str] = None,
) -> QdrantStore:
    """Open the cached document-owner store.

    Resolution order:

    1. ``project_id`` supplied → :class:`StorageFactory` chooses
       :class:`LocalQdrantStore` or :class:`RemoteQdrantStore` based on the
       project's ``storage_backend``.
    2. ``locator`` URL-shaped → raises :class:`RemoteQdrantUnsupportedError`
       to preserve legacy failure semantics.
    3. Falls back to local ``QDRANT_DOC_PATH`` resolution.
    """

    if project_id:
        try:
            from tools.common.project_registry import (
                ProjectNotRegisteredError as _RegistryNotRegistered,
                resolve_project_targets,
            )
        except ImportError:
            # Standalone doc-tiny runtime without the code registry on
            # sys.path: no registry can know this id — treat it as
            # unregistered under the doc-side contract class.
            from project_contract import ProjectNotRegisteredError as _DocNotRegistered

            raise _DocNotRegistered(project_id, []) from None

        try:
            targets = resolve_project_targets(project_id)
        except _RegistryNotRegistered as exc:
            # The registry error is a same-named sibling of the doc-side
            # class, not a subclass — re-export under the doc-side contract
            # class so callers see one error shape across the boundary.
            from project_contract import ProjectNotRegisteredError as _DocNotRegistered

            raise _DocNotRegistered(exc.project_id, exc.known) from exc

        factory = create_storage(targets, project_root=project_root)
        return factory.get_qdrant_store(QdrantStorageRole.DOCUMENT)

    value = str(locator or "").strip()
    if value:
        parsed = urlparse(value)
        if not Path(value).expanduser().is_absolute() and (parsed.scheme or parsed.netloc):
            raise RemoteQdrantUnsupportedError(
                "Remote Qdrant endpoints are unsupported. Export or re-ingest "
                "the data, then use QDRANT_DOC_PATH or CORTEX_DATA_HOME."
            )
    root = Path(project_root or os.getcwd()).resolve()
    path = value or os.environ.get("QDRANT_DOC_PATH")
    resolved = resolve_storage(root, qdrant_doc_path=path)
    return LocalQdrantStore(resolved, QdrantStorageRole.DOCUMENT)
