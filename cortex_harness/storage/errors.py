"""Shared storage error types.

Centralizes exceptions raised by the backend adapters so callers (MCP tools,
ingest scripts, the ``make doctor`` check) see a single set of typed errors
regardless of which backend is in use. Inheriting from :class:`RuntimeError`
keeps these distinct from ``OSError``/``ConnectionError`` flow paths that
scripts may already catch differently.
"""

from __future__ import annotations


class BackendConnectionError(RuntimeError):
    """A remote storage backend is unreachable.

    Raised when a ``RemoteQdrantStore`` cannot reach a Qdrant server or when
    a remote FalkorDB driver cannot reach its Redis-compatible server. The
    constructor produces an actionable message that points the operator at
    the URL and the underlying cause.
    """

    def __init__(self, backend: str, url: str, cause: BaseException | None = None) -> None:
        suffix = f": {cause}" if cause is not None else ""
        super().__init__(
            f"{backend} server at {url} is unreachable{suffix}. "
            "Check that the server is running and the URL is correct."
        )
        self.backend = backend
        self.url = url
        self.cause = cause
