#!/usr/bin/env python3
"""Upgrade a dense-only collection to hybrid (dense + named ``sparse``).

Local Qdrant mode cannot add a sparse vector to an existing collection
(``update_collection`` raises ValueError), so the only upgrade path is
recreate: scroll every point's payload, drop + recreate with the sparse
config, re-encode dense + sparse from the payload text, upsert back, and
re-stamp the sentinel with ``hybrid=true``.

Dense vectors are re-encoded from the same model (low-bit MPS/CPU drift
only) — quality comparison is top-k overlap on sample queries (see the
"Hybrid retrieval enablement" runbook), never vector hashing.

Usage:
  python code-tiny/scripts/backfill_hybrid_sparse.py --project-id X --dry-run
  python code-tiny/scripts/backfill_hybrid_sparse.py --project-id X --yes
  python code-tiny/scripts/backfill_hybrid_sparse.py --collection C --yes
  python code-tiny/scripts/backfill_hybrid_sparse.py --project-id X --doc-mode --yes
  # resume an interrupted re-upsert (backup + checkpoint live in the cache dir):
  python code-tiny/scripts/backfill_hybrid_sparse.py --project-id X --yes --resume
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CODE_TINY = Path(__file__).resolve().parents[1]
REPO_ROOT = CODE_TINY.parent
DOC_TINY = REPO_ROOT / "doc-tiny"
for entry in (CODE_TINY, DOC_TINY, REPO_ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from qdrant_client.http import models as qmodels  # noqa: E402

from tools.common import embed_runtime, embedding_marker  # noqa: E402
from tools.common.local_qdrant import (  # noqa: E402
    collection_has_sparse,
    collection_supports_sparse,
    discover_code_collections,
    hybrid_document_text,
)


def _resolve_store(args):
    if args.doc_mode:
        from doc_local_qdrant import get_document_qdrant_store

        return get_document_qdrant_store(getattr(args, "qdrant_path", None))
    from tools.common.local_qdrant import get_code_qdrant_store

    return get_code_qdrant_store(None)


def _resolve_collection(args, store=None) -> str:
    if args.collection:
        return args.collection
    project_id = (args.project_id or "").strip()
    if not project_id:
        raise SystemExit("Either --project-id or --collection is required.")
    if args.doc_mode:
        return f"{project_id}_doc"
    if store is None:
        store = _resolve_store(args)
    # The bare project id is a logical registry name — physical code
    # collections are ``{project}_{hash}__{parser}_functions``. A backfill
    # drops and recreates its target, so ambiguity is never resolved
    # silently: exactly one match proceeds, several demand --collection.
    found = discover_code_collections(store, project_id)
    if not found:
        raise SystemExit(
            f"No code collections found for project {project_id!r} "
            f"(looked for {project_id}_*__*_functions in the resolved store). "
            f"Sync first with 'dev sync code', or pass --collection explicitly."
        )
    if len(found) > 1:
        raise SystemExit(
            f"Project {project_id!r} has {len(found)} code collections; "
            f"backfill them one at a time with --collection:\n  "
            + "\n  ".join(found)
        )
    return found[0]


def _resolve_model(args) -> str:
    if args.doc_mode:
        return (
            os.environ.get("DOC_EMBEDDING_MODEL")
            or os.environ.get("EMBEDDING_MODEL")
            or __import__("embedding_utils").DEFAULT_DOC_EMBEDDING_MODEL
        )
    from tools.common.model_defaults import DEFAULT_CODE_EMBEDDING_MODEL

    return os.environ.get("CODE_EMBEDDING_MODEL") or DEFAULT_CODE_EMBEDDING_MODEL


def _checkpoint_dir() -> Path:
    root = os.environ.get("QDRANT_CACHE_DIR")
    base = Path(root) if root else Path(tempfile.gettempdir()) / "cortex-hybrid-backfill"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _backup_path(collection: str) -> Path:
    import re

    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", collection)
    return _checkpoint_dir() / f"backfill_{safe}_payloads.jsonl"


def _state_path(collection: str) -> Path:
    import re

    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", collection)
    return _checkpoint_dir() / f"backfill_{safe}_state.json"


def scroll_payloads(store, collection: str) -> List[Dict[str, Any]]:
    """Every non-sentinel point as ``{"id", "payload"}`` (vectors excluded)."""
    rows: List[Dict[str, Any]] = []
    offset: Any = None
    while True:
        points, offset = store.scroll(
            collection,
            scroll_filter=None,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points or []:
            pid = point.get("id") if isinstance(point, dict) else getattr(point, "id", None)
            payload = point.get("payload") if isinstance(point, dict) else getattr(point, "payload", None)
            if embedding_marker.is_meta_payload(payload):
                continue
            rows.append({"id": str(pid), "payload": dict(payload or {})})
        if offset is None:
            break
    return rows


def recreate_with_sparse(store, collection: str, vector_size: int, model_identity: str,
                         project_id: Optional[str], reset_hint: str) -> None:
    store.recreate_collection(
        collection,
        vectors_config=qmodels.VectorParams(
            size=int(vector_size), distance=qmodels.Distance.COSINE
        ),
        sparse_vectors_config={"sparse": qmodels.SparseVectorParams()},
    )
    embedding_marker.stamp(
        store,
        collection,
        embedding_model=model_identity,
        vector_size=vector_size,
        project_id=project_id,
        hybrid=True,
    )


def backfill(
    store,
    collection: str,
    *,
    model_identity: str,
    device: str,
    batch_size: int,
    resume: bool,
    verbose: bool,
) -> Tuple[int, int]:
    """Encode + upsert from the payload backup; returns (upserted, total)."""
    backup = _backup_path(collection)
    state = _state_path(collection)
    start = 0
    if resume and backup.is_file():
        if state.is_file():
            start = int(json.loads(state.read_text()).get("upserted", 0))
        print(f"[backfill] resuming at {start} (checkpoint {state.name})")
    else:
        backup.parent.mkdir(parents=True, exist_ok=True)

    with backup.open("r", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    total = len(rows)
    if start >= total:
        return total, total

    model = embed_runtime.get_sentence_transformer(
        model_identity,
        device=device,
        trust_remote_code=embed_runtime.model_policy(model_identity)["trust_remote_code"],
    )
    vector_size = int(model.get_sentence_embedding_dimension())

    # Batch loop: dense + sparse from the payload text, upsert, checkpoint.
    index = start
    while index < total:
        chunk = rows[index:index + batch_size]
        texts = [hybrid_document_text(row["payload"]) or " " for row in chunk]
        vectors = model.encode(
            texts, batch_size=max(1, batch_size), show_progress_bar=False,
            convert_to_numpy=True, normalize_embeddings=True,
        )
        sparse_pairs = embed_runtime.encode_sparse(
            model_identity, texts, device=device, model=model,
        )
        points = []
        for row, vector, (indices, values) in zip(chunk, vectors, sparse_pairs):
            points.append({
                "id": row["id"],
                "vector": {
                    "": vector.tolist(),
                    "sparse": qmodels.SparseVector(
                        indices=list(indices), values=list(values)
                    ),
                },
                "payload": row["payload"],
            })
        store.upsert(collection, points, wait=True)
        index += len(chunk)
        state.write_text(json.dumps({"upserted": index, "total": total}))
        if verbose:
            print(f"[backfill] {index}/{total}")

    # Clean checkpoint only on full completion.
    state.unlink(missing_ok=True)
    return total, total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project-id", default=None)
    parser.add_argument("--collection", default=None)
    parser.add_argument("--doc-mode", action="store_true",
                        help="Target the doc store + doc collection naming.")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true",
                        help="Confirm the destructive drop (required to run).")
    parser.add_argument("--force", action="store_true",
                        help="Re-run even when the collection already has sparse.")
    parser.add_argument("--resume", action="store_true",
                        help="Continue an interrupted re-upsert from checkpoint.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    store = _resolve_store(args)
    collection = _resolve_collection(args, store)
    model_identity = embed_runtime.effective_model_identity(_resolve_model(args))
    device = os.environ.get("EMBED_DEVICE") or "auto"

    exists = store.collection_exists(collection)
    if not exists:
        raise SystemExit(f"Collection not found: {collection!r}")

    info = store.get_collection_info(collection)
    already_sparse = collection_has_sparse(info)
    backup = _backup_path(collection)
    total_points = int(getattr(store.count(collection, count_filter=None, exact=True), "count", 0))

    print(f"[backfill] collection={collection!r} model={model_identity!r} "
          f"points={total_points} sparse_already={already_sparse} "
          f"backup={backup.name}{' (present)' if backup.is_file() else ' (missing)'}")

    if args.dry_run:
        if not backup.is_file():
            rows = scroll_payloads(store, collection)
            textable = sum(1 for row in rows if hybrid_document_text(row["payload"]))
            print(f"[dry-run] would export {len(rows)} payloads "
                  f"({textable} with extractable text), drop + recreate with "
                  f"'sparse', re-encode, and stamp hybrid=true.")
        else:
            rows = [json.loads(line) for line in backup.open(encoding="utf-8") if line.strip()]
            print(f"[dry-run] existing backup has {len(rows)} payloads; "
                  f"--resume would continue its re-upsert.")
        return 0

    if not args.yes:
        raise SystemExit(
            "Refusing to drop the collection without --yes (destructive: "
            "vectors are re-encoded from payload text, originals are not "
            "recoverable). Preview with --dry-run first."
        )
    if already_sparse and not args.force and not (args.resume and backup.is_file()):
        raise SystemExit(
            f"Collection {collection!r} already declares the sparse vector. "
            f"Re-running needs --force (full recreate)."
        )

    # Step 1 — export payloads (the only recoverable copy goes here).
    if not (args.resume and backup.is_file()):
        rows = scroll_payloads(store, collection)
        backup.write_text(
            "\n".join(json.dumps(row, ensure_ascii=True) for row in rows) + "\n",
            encoding="utf-8",
        )
        print(f"[backfill] exported {len(rows)} payloads → {backup}")
    elif not backup.is_file():
        raise SystemExit(
            f"--resume requested but no backup exists at {backup}; "
            f"run once without --resume to export first."
        )

    # Step 2/3 — drop + recreate with sparse, re-encode, upsert.
    started = time.perf_counter()
    vector_size = 1024
    if not already_sparse or not args.resume:
        try:
            probe_model = embed_runtime.get_sentence_transformer(
                model_identity, device=device,
                trust_remote_code=embed_runtime.model_policy(model_identity)["trust_remote_code"],
            )
            vector_size = int(probe_model.get_sentence_embedding_dimension())
        except Exception as exc:
            raise SystemExit(f"Embedding model load failed: {exc}") from exc
        recreate_with_sparse(
            store, collection, vector_size, model_identity,
            project_id=(args.project_id or None),
            reset_hint="run this script again or reset + re-ingest",
        )
        print(f"[backfill] recreated {collection!r} (dim={vector_size}, sparse='sparse')")
    else:
        print("[backfill] --resume: collection already carries sparse config; keeping it")

    upserted, total = backfill(
        store, collection,
        model_identity=model_identity, device=device,
        batch_size=max(1, args.batch_size), resume=args.resume,
        verbose=args.verbose,
    )
    elapsed = time.perf_counter() - started
    print(f"[backfill] done: {upserted}/{total} points re-encoded and upserted "
          f"in {elapsed:.1f}s. Sentinel stamped hybrid=true.")
    print("[backfill] verify quality with the A/B harness (runbook: "
          "docs/UNIFIED_INGEST_QUERY_CONTRACT.md → Hybrid retrieval enablement).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
