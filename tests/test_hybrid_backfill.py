"""Phase 04 — hybrid backfill script: recreate round-trip on real local mode.

The script must turn a dense-only collection into a hybrid one WITHOUT
re-ingest: export payloads → drop + recreate with sparse → re-encode from
payload text → upsert → stamp hybrid=true. Idempotency guard refuses a
second full run; --dry-run never mutates.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
for entry in (CODE_TINY, CODE_TINY / "scripts", ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from qdrant_client import QdrantClient  # noqa: E402
from qdrant_client.http import models as qmodels  # noqa: E402

from tools.common import embed_runtime, embedding_marker  # noqa: E402
from tools.common.local_qdrant import (  # noqa: E402
    collection_has_sparse,
    collection_supports_sparse,
    model_to_dict,
)


class RawLocalStoreAdapter:
    """The store surface the backfill script uses, over a real local client."""

    def __init__(self, path):
        self._client = QdrantClient(path=str(path))

    def collection_exists(self, name):
        return self._client.collection_exists(collection_name=name)

    def get_collection_info(self, name):
        return self._client.get_collection(collection_name=name)

    def count(self, collection_name, count_filter=None, exact=True):
        return self._client.count(
            collection_name=collection_name, count_filter=count_filter, exact=exact,
        )

    def create_collection(self, name, vectors_config=None, **kwargs):
        self._client.create_collection(
            collection_name=name, vectors_config=vectors_config, **kwargs,
        )

    def recreate_collection(self, name, vectors_config=None, **kwargs):
        self._client.recreate_collection(
            collection_name=name, vectors_config=vectors_config, **kwargs,
        )

    def upsert(self, collection, points, wait=True):
        normalized = [
            qmodels.PointStruct(**point) if isinstance(point, dict) else point
            for point in points
        ]
        return self._client.upsert(
            collection_name=collection, points=normalized, wait=wait,
        )

    def scroll(self, collection, *, scroll_filter=None, limit=100, offset=None,
               with_payload=True, with_vectors=False):
        points, offset = self._client.scroll(
            collection_name=collection, scroll_filter=scroll_filter,
            limit=limit, offset=offset, with_payload=with_payload,
            with_vectors=with_vectors,
        )
        return [model_to_dict(point) for point in points], offset

    def retrieve(self, collection, ids, with_payload=True, with_vectors=False):
        points = self._client.retrieve(
            collection_name=collection, ids=ids, with_payload=with_payload,
            with_vectors=with_vectors,
        )
        return [model_to_dict(point) for point in points]


def _load_script():
    path = CODE_TINY / "scripts" / "backfill_hybrid_sparse.py"
    spec = importlib.util.spec_from_file_location("backfill_hybrid_sparse", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BGE = "BAAI/bge-m3"


class _FakeST:
    """Deterministic 4-dim stand-in for the SentenceTransformer."""

    def get_sentence_embedding_dimension(self):
        return 4

    def encode(self, texts, **kwargs):
        import numpy as np

        return np.array([
            [0.1 * ((len(t) % 7) + 1), 0.2, 0.3, 0.4] for t in texts
        ])

    @property
    def device(self):
        import torch

        return torch.device("cpu")


def _fake_encode_sparse(model_source, texts, device=None, model=None, **kwargs):
    return [([4, 9], [0.5, 0.25]) for _ in texts]


class BackfillRoundTripTests(unittest.TestCase):
    """AC6 — recreate-based upgrade keeps payloads, adds sparse, stamps."""

    def setUp(self):
        import uuid

        os.environ["EMBED_HYBRID"] = "1"
        self.module = _load_script()
        self._tmp = tempfile.TemporaryDirectory(prefix="bge-m3-backfill-")
        self.store = RawLocalStoreAdapter(self._tmp.name)
        self.ids = {
            pid: str(uuid.uuid5(uuid.NAMESPACE_URL, f"backfill-test::{pid}"))
            for pid in ("p1", "p2", "p3")
        }
        self.store.create_collection(
            "proj",
            vectors_config=qmodels.VectorParams(size=4, distance=qmodels.Distance.COSINE),
        )
        self.store.upsert("proj", [
            {"id": self.ids["p1"], "vector": [0.1] * 4, "payload": {"text": "worker run", "project_id": "proj"}},
            {"id": self.ids["p2"], "vector": [0.2] * 4, "payload": {"code": "def parse(): pass"}},
            {"id": self.ids["p3"], "vector": [0.3] * 4, "payload": {"summary": "s", "comment": "k"}},
        ], wait=True)
        embedding_marker.stamp(self.store, "proj", embedding_model=BGE,
                               vector_size=4, hybrid=False)
        qdrant_layout_reset()

    def tearDown(self):
        os.environ.pop("EMBED_HYBRID", None)
        self._tmp.cleanup()
        embed_runtime.reset_caches()
        embed_runtime.reset_sparse_head_cache()
        embedding_marker.reset_sentinel_cache()
        qdrant_layout_reset()

    def _run(self, *extra):
        argv = ["backfill_hybrid_sparse.py", *extra]
        with patch.object(self.module, "_resolve_store", return_value=self.store), \
             patch.object(sys, "argv", argv):
            return self.module.main()

    def test_round_trip_upgrade(self):
        with patch.object(embed_runtime, "get_sentence_transformer",
                          return_value=_FakeST()), \
             patch.object(embed_runtime, "encode_sparse",
                          side_effect=_fake_encode_sparse):
            self._run("--collection", "proj", "--yes", "--batch-size", "2")

        self.assertTrue(collection_supports_sparse(self.store, "proj"))
        meta = embedding_marker.check(self.store, "proj")
        self.assertIs(meta["hybrid"], True)
        # All three payloads re-upserted with dense + sparse halves.
        points, _ = self.store.scroll(
            "proj", scroll_filter=None, limit=10, offset=None,
            with_payload=True, with_vectors=True,
        )
        by_id = {str(p["id"] if isinstance(p, dict) else p.id): p for p in points}
        for pid in ("p1", "p2", "p3"):
            point = by_id[self.ids[pid]]
            payload = point.get("payload") if isinstance(point, dict) else point.payload
            vector = point.get("vector") if isinstance(point, dict) else point.vector
            self.assertIsInstance(vector, dict, pid)
            self.assertIn("", vector)
            sparse = vector.get("sparse")
            # Scrolled points come back serialized: dict or SparseVector.
            indices = sparse["indices"] if isinstance(sparse, dict) else sparse.indices
            self.assertEqual(list(indices), [4, 9], pid)
            self.assertIsNotNone(payload.get("text") or payload.get("code") or payload.get("summary"), pid)
        # Payload copy is verbatim (hash-ok per AC6).
        p1 = by_id[self.ids["p1"]]
        payload1 = p1.get("payload") if isinstance(p1, dict) else p1.payload
        self.assertEqual(payload1.get("project_id"), "proj")

    def test_second_run_refuses_without_force(self):
        with patch.object(embed_runtime, "get_sentence_transformer",
                          return_value=_FakeST()), \
             patch.object(embed_runtime, "encode_sparse",
                          side_effect=_fake_encode_sparse):
            self._run("--collection", "proj", "--yes")
            with self.assertRaises(SystemExit) as caught:
                self._run("--collection", "proj", "--yes")
        self.assertIn("already declares the sparse vector", str(caught.exception))

    def test_dry_run_never_mutates(self):
        before = int(self.store.count("proj", count_filter=None, exact=True).count)
        self._run("--collection", "proj", "--dry-run")
        self.assertFalse(collection_supports_sparse(self.store, "proj"))
        after = int(self.store.count("proj", count_filter=None, exact=True).count)
        self.assertEqual(before, after)

    def test_refuses_without_yes(self):
        with self.assertRaises(SystemExit) as caught:
            self._run("--collection", "proj")
        self.assertIn("--yes", str(caught.exception))


def qdrant_layout_reset():
    try:
        from tools.common import qdrant_layout_cache

        qdrant_layout_cache.reset_cache()
    except Exception:
        pass


if __name__ == "__main__":
    unittest.main()
