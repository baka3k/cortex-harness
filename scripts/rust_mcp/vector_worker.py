#!/usr/bin/env python3
"""Persistent local-Qdrant vector sidecar for the Rust MCP (vector-lane plan
phase-04; plan 260915-2027-vector-lane-rust-port).

Why a sidecar: the python `qdrant-client` local mode persists collections as
pickled `PointStruct` rows inside per-collection SQLite files — an internal,
version-coupled format that Rust must not parse. The sync/ingest writer stays
Python (spike `260914-1706` phase-05 NO-GO), so this worker reads exactly the
store the writer produces: zero format risk, zero drift.

Deliberately imports ONLY `qdrant_client` (+ stdlib). No torch, no
transformers, no sentence-transformers — query embedding is done Rust-side by
`cortex-embed` ONNX; this process is a thin local-search plane. The harness
asserts the import budget (phase-04 gate: import < 2s, RSS < 300MB).

Protocol: NDJSON on stdio (one request line → one response line), banner on
stderr only. Spawned by `cortex-mcp` (`graph/vector_lane.rs`) with:

    python vector_worker.py --store <qdrant-local-store-dir>

Requests:
    {"op": "meta",    "collection": "<name>"}
    {"op": "search",  "collection": "<name>", "vector": [...], "limit": N,
     "filter": {...} | null}

Responses:
    {"ok": true, "sizes": {"default": 1024}}              # meta (or "named")
    {"ok": true, "hits": [{"id", "version", "score", "payload"}]}
    {"ok": false, "error": "<message>"}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client import models as qmodels

PROTOCOL_VERSION = 1


def log(message: str) -> None:
    print(f"[vector-worker] {message}", file=sys.stderr, flush=True)


class LocalStore:
    """One QdrantClient(path=…) per process — mirrors local_qdrant.py shape."""

    def __init__(self, store_path: str) -> None:
        self.path = Path(store_path)
        if not self.path.is_dir():
            raise NotADirectoryError(f"qdrant local store not found: {self.path}")
        self.client = QdrantClient(path=str(self.path))

    def collection_names(self) -> list[str]:
        return [item.name for item in self.client.get_collections().collections]

    def vector_sizes(self, collection: str) -> dict[str, int]:
        """Mirror `local_qdrant.vector_sizes`: default vs named vector sizes."""
        info = self.client.get_collection(collection_name=collection)
        vectors = info.config.params.vectors
        if isinstance(vectors, qmodels.VectorParams):
            return {"default": int(vectors.size)}
        sizes: dict[str, int] = {}
        for name, params in (vectors or {}).items():
            sizes[name] = int(getattr(params, "size", 0) or 0)
        return sizes

    def search(
        self,
        collection: str,
        vector: list[float],
        limit: int,
        query_filter: dict | None,
        using: str | None = None,
    ) -> list[dict]:
        """Mirror `qdrant_query_support.search_collection` (without the
        `_collection` provenance tag — the Rust merge layer owns that)."""
        query_filter_model = (
            qmodels.Filter.model_validate(query_filter) if query_filter else None
        )
        kwargs: dict = {}
        if using:
            kwargs["using"] = using
        response = self.client.query_points(
            collection,
            query=vector,
            limit=int(limit),
            query_filter=query_filter_model,
            with_payload=qmodels.PayloadSelectorExclude(exclude=["text"]),
            with_vectors=False,
            **kwargs,
        )
        points = getattr(response, "points", response)
        hits: list[dict] = []
        for point in points:
            hits.append(
                {
                    "id": str(point.id),
                    "version": point.version,
                    "score": float(point.score),
                    "payload": point.payload or {},
                }
            )
        return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True, help="qdrant local store dir")
    args = parser.parse_args()

    store = LocalStore(args.store)
    log(f"ready store={store.path} protocol={PROTOCOL_VERSION}")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            op = request.get("op")
            if op == "list":
                response = {"ok": True, "collections": store.collection_names()}
            elif op == "meta":
                sizes = store.vector_sizes(request["collection"])
                response = {"ok": True, "sizes": sizes}
            elif op == "search":
                hits = store.search(
                    request["collection"],
                    request["vector"],
                    int(request.get("limit", 10)),
                    request.get("filter"),
                    using=request.get("using"),
                )
                response = {"ok": True, "hits": hits}
            else:
                response = {"ok": False, "error": f"unknown op {op!r}"}
        except Exception as exc:  # noqa: BLE001 — errors travel as responses
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
