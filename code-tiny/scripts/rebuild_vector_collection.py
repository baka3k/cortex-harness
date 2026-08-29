#!/usr/bin/env python3
"""Rebuild a Qdrant collection with HNSW/quantization tuning — copy vectors, never re-embed.

Intended for **remote** Qdrant stores: local mode cannot apply HNSW or
quantization settings (the client no-ops them), so rebuilding a local
collection is possible but pointless — the script warns and asks for
confirmation in that case.

Flow (destructive steps require ``--yes``):
  1. Scroll the source collection ``with_vectors=True`` in batches.
  2. Create ``<collection>_rebuild_tmp`` with the tuning config from env
     (``QDRANT_HNSW_M`` / ``QDRANT_HNSW_EF_CONSTRUCT`` /
     ``QDRANT_SCALAR_QUANT``).
  3. Upload into temp; validate ``count(exact)`` matches the source.
  4. ``--yes`` only: drop the source, recreate it with the same tuning,
     re-upload from temp, **re-validate the count on the target**, then
     drop temp. Without ``--yes`` the script stops after step 3 and just
     prints the plan.
  5. Invalidate the collection-layout cache.

Back up first (``db_transfer export``) — there are no collection aliases
in this runtime, so the swap is delete-then-recreate.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List

CODE_TINY = Path(__file__).resolve().parents[1]
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from qdrant_client.http import models as qmodels  # noqa: E402

from tools.common import qdrant_layout_cache  # noqa: E402
from tools.common.local_qdrant import (  # noqa: E402
    LocalQdrantStore,
    get_code_qdrant_store,
    model_to_dict,
    _tuning_kwargs,
)


def _count_exact(store: Any, name: str) -> int:
    result = store.count(name, exact=True)
    return int(getattr(result, "count", result))


def _vectors_config(store: Any, name: str) -> Dict[str, Any]:
    """Copy the source layout (default or named) as a client-compatible dict."""
    info = model_to_dict(store.get_collection_info(name))
    vectors = ((info.get("config") or {}).get("params") or {}).get("vectors")
    if not vectors:
        raise RuntimeError(
            f"Cannot read vector config for {name!r}; refusing to rebuild blindly."
        )
    return vectors


def _iter_batches(store: Any, name: str, batch_size: int) -> Iterator[List[Dict[str, Any]]]:
    offset: Any = None
    while True:
        points, next_offset = store.scroll(
            name,
            limit=batch_size,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        if points:
            yield [point if isinstance(point, dict) else model_to_dict(point) for point in points]
        if next_offset is None:
            return
        offset = next_offset


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("collection", help="Collection to rebuild.")
    parser.add_argument(
        "--url",
        default="local-code-store",
        help="Store locator (compat token or remote project storage resolution).",
    )
    parser.add_argument("--batch-size", type=int, default=256, help="Scroll/upsert batch size.")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Execute the destructive swap (default: dry-run through count validation).",
    )
    args = parser.parse_args(argv)

    store = get_code_qdrant_store(args.url)
    source = args.collection
    if not store.collection_exists(source):
        print(f"[rebuild] collection {source!r} does not exist.")
        return 1
    tuning = _tuning_kwargs(store)
    if isinstance(store, LocalQdrantStore):
        print(
            "[rebuild] WARNING: this is a local-mode store — HNSW/quantization "
            "tuning is a no-op locally, so the rebuild adds no value."
        )
    if not tuning:
        print(
            "[rebuild] no tuning env set (QDRANT_HNSW_M / QDRANT_HNSW_EF_CONSTRUCT / "
            "QDRANT_SCALAR_QUANT): rebuild would produce an identical collection."
        )

    temp = f"{source}_rebuild_tmp"
    vectors_config = _vectors_config(store, source)
    source_count = _count_exact(store, source)
    print(f"[rebuild] source {source!r}: {source_count} points; layout={vectors_config}")
    if store.collection_exists(temp):
        if not args.yes:
            print(
                f"[rebuild] ABORT: stale temp collection {temp!r} exists from a previous "
                f"run (it may be the only complete copy if the last --yes run failed "
                f"mid-swap). Remove it manually or re-run with --yes to drop it."
            )
            return 1
        print(f"[rebuild] dropping stale temp collection {temp!r} from a previous run.")
        store.delete_collection(temp)

    # 1-2. copy vectors into the tuned temp collection
    store.create_collection(temp, vectors_config=vectors_config, **tuning)
    copied = 0
    for batch in _iter_batches(store, source, args.batch_size):
        uploads = [
            {
                "id": point.get("id"),
                "vector": point.get("vector"),
                "payload": point.get("payload"),
            }
            for point in batch
        ]
        store.upsert(temp, uploads, wait=True)
        copied += len(uploads)
        print(f"[rebuild] copied {copied}/{source_count}", end="\r", flush=True)
    print()

    # 3. count validation on temp
    temp_count = _count_exact(store, temp)
    if temp_count != source_count:
        print(f"[rebuild] ABORT: temp count {temp_count} != source count {source_count}.")
        store.delete_collection(temp)
        return 1
    print(f"[rebuild] temp validation ok ({temp_count} points).")

    if not args.yes:
        print(
            f"[rebuild] DRY-RUN plan (--yes to execute):\n"
            f"  1. delete {source!r}\n"
            f"  2. recreate {source!r} with tuning {tuning or {}}\n"
            f"  3. re-upload {temp_count} points from {temp!r}\n"
            f"  4. count-assert {source!r} == {temp_count}\n"
            f"  5. drop {temp!r}\n"
            f"Back up first (db_transfer export)."
        )
        return 0

    # 4. destructive swap
    store.delete_collection(source)
    store.create_collection(source, vectors_config=vectors_config, **tuning)
    reuploaded = 0
    for batch in _iter_batches(store, temp, args.batch_size):
        uploads = [
            {
                "id": point.get("id"),
                "vector": point.get("vector"),
                "payload": point.get("payload"),
            }
            for point in batch
        ]
        store.upsert(source, uploads, wait=True)
        reuploaded += len(uploads)
    target_count = _count_exact(store, source)
    if target_count != temp_count:
        print(
            f"[rebuild] FAILED: target count {target_count} != expected {temp_count}. "
            f"Recover points from {temp!r} (kept for inspection)."
        )
        return 1
    store.delete_collection(temp)
    qdrant_layout_cache.invalidate(args.url)
    print(f"[rebuild] done: {source!r} rebuilt with {target_count} points; temp dropped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
