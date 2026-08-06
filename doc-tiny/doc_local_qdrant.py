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
    QdrantStorageRole,
    resolve_storage,
)


class RemoteQdrantUnsupportedError(ValueError):
    """Raised when a remote locator reaches the local-only document runtime."""


def get_document_qdrant_store(
    locator: Optional[str] = None,
    *,
    project_root: Optional[Path] = None,
) -> LocalQdrantStore:
    """Open the cached document-owner store selected by local path config."""

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
