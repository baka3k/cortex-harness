"""Phase 03 — query-side fusion: all-or-none dispatch, RRF args, merge.

The fan-out never mixes score scales: fused RRF only when EVERY selected
collection declares the sparse vector; any dense-only member (or a ``_mess``
hash-vector collection) downgrades the whole fan-out to dense-only. Flag
OFF keeps byte-parallel behavior. Response shape is unchanged either way.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
DOC_TINY = ROOT / "doc-tiny"
for entry in (CODE_TINY, DOC_TINY):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from qdrant_client.http import models as qmodels  # noqa: E402

from tools.common import (  # noqa: E402
    embed_runtime,
    qdrant_layout_cache,
    qdrant_query_support,
)

BGE = "BAAI/bge-m3"
SPARSE_PAIR = ([4, 9], [0.5, 0.25])


class _QueryRecordingStore:
    """Store double: records query_points kwargs; capability is settable."""

    def __init__(self, sparse_collections=("proj",)):
        self.sparse_collections = set(sparse_collections)
        self.query_calls: list = []

    def get_collection_info(self, name):
        sparse = (
            {"sparse": object()} if name in self.sparse_collections else None
        )
        return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(
            vectors=SimpleNamespace(size=1024), sparse_vectors=sparse,
        )))

    def query_points(self, collection, **kwargs):
        self.query_calls.append((collection, kwargs))
        return SimpleNamespace(points=[
            {"id": f"{collection}-1", "score": 0.9, "payload": {"summary": "s"}},
        ])

    def retrieve(self, collection, ids, **kwargs):
        return []


class FusionDispatchTests(unittest.TestCase):
    """AC4 — the all-or-none dispatch matrix."""

    def setUp(self):
        qdrant_layout_cache.reset_cache()
        os.environ.pop("EMBED_HYBRID", None)
        os.environ.pop("EMBED_FUSE_K", None)

    def tearDown(self):
        qdrant_layout_cache.reset_cache()
        os.environ.pop("EMBED_HYBRID", None)
        os.environ.pop("EMBED_FUSE_K", None)

    def test_dispatch_matrix(self):
        # (env flag, store sparse collections, fan-out, expected fused?)
        cases = [
            ("1", {"a", "b"}, ["a", "b"], True),
            ("1", {"a", "b"}, ["a"], True),
            ("1", {"a"}, ["a", "b"], False),          # one dense-only member
            ("0", {"a", "b"}, ["a", "b"], False),      # flag off
            (None, {"a", "b"}, ["a", "b"], False),     # flag unset
            ("1", set(), ["a"], False),                # nothing hybrid
            ("1", {"proj_mess", "a"}, ["proj_mess", "a"], False),  # F6 exempt
            ("1", {"a"}, [], False),                   # empty fan-out
        ]
        store = _QueryRecordingStore()
        for flag, sparse_cols, fanout, expected in cases:
            with self.subTest(flag=flag, fanout=fanout, sparse=sorted(sparse_cols)):
                store.sparse_collections = set(sparse_cols)
                qdrant_layout_cache.reset_cache()
                if flag is None:
                    os.environ.pop("EMBED_HYBRID", None)
                else:
                    os.environ["EMBED_HYBRID"] = flag
                decided = qdrant_query_support.fanout_supports_sparse(
                    store, [(col, None) for col in fanout], qdrant_url="u",
                )
                self.assertEqual(decided, expected)

    def test_flag_on_all_hybrid_fanout_fuses(self):
        store = _QueryRecordingStore(sparse_collections={"a", "b"})
        os.environ["EMBED_HYBRID"] = "1"
        hits, errors = qdrant_query_support.merge_collections(
            store, [("a", None), ("b", None)], [0.1] * 1024, 5,
            qdrant_url="u", sparse_vector=SPARSE_PAIR,
        )
        self.assertEqual(len(store.query_calls), 2)
        for collection, kwargs in store.query_calls:
            self.assertIn("prefetch", kwargs)
            self.assertIsInstance(kwargs["query"], qmodels.FusionQuery)
        self.assertEqual(errors, [])
        self.assertEqual(len(hits), 2)

    def test_flag_on_mixed_fanout_all_dense(self):
        store = _QueryRecordingStore(sparse_collections={"a"})  # b dense-only
        os.environ["EMBED_HYBRID"] = "1"
        qdrant_query_support.merge_collections(
            store, [("a", None), ("b", None)], [0.1] * 1024, 5,
            qdrant_url="u", sparse_vector=SPARSE_PAIR,
        )
        for collection, kwargs in store.query_calls:
            self.assertNotIn("prefetch", kwargs)
            self.assertEqual(kwargs["query"], [0.1] * 1024)

    def test_flag_off_ignores_sparse(self):
        store = _QueryRecordingStore(sparse_collections={"a"})
        qdrant_query_support.merge_collections(
            store, [("a", None)], [0.1] * 1024, 5,
            qdrant_url="u", sparse_vector=SPARSE_PAIR,
        )
        _collection, kwargs = store.query_calls[0]
        self.assertNotIn("prefetch", kwargs)

    def test_response_shape_unchanged(self):
        store = _QueryRecordingStore(sparse_collections={"a"})
        os.environ["EMBED_HYBRID"] = "1"
        hits = qdrant_query_support.search_collection(
            store, "a", [0.1] * 1024, None, 5,
            sparse_vector=SPARSE_PAIR, fusion=True,
        )
        self.assertEqual(len(hits), 1)
        hit = hits[0]
        self.assertEqual(set(hit) >= {"id", "score", "payload", "_collection"}, True)
        self.assertEqual(hit["_collection"], "a")


class FusionQueryArgsTests(unittest.TestCase):
    """AC5 — the fused query carries prefetch/using/FusionQuery correctly."""

    def setUp(self):
        qdrant_layout_cache.reset_cache()

    def tearDown(self):
        qdrant_layout_cache.reset_cache()
        os.environ.pop("EMBED_FUSE_K", None)

    def test_fusion_query_structure(self):
        os.environ["EMBED_FUSE_K"] = "32"
        query_filter = qmodels.Filter(must=[qmodels.FieldCondition(
            key="project_id_normalized", match=qmodels.MatchValue(value="proj"),
        )])
        # Production callers pre-apply the sentinel exclusion (search_collection).
        query_filter = qdrant_query_support._with_sentinel_exclusion(query_filter)
        kwargs = qdrant_query_support.hybrid_fusion_query(
            [0.1] * 1024, SPARSE_PAIR, vector_name=None, top_k=5,
            query_filter=query_filter,
        )
        prefetch = kwargs["prefetch"]
        self.assertEqual(len(prefetch), 2)
        dense, sparse = prefetch
        self.assertEqual(dense.using, "")
        self.assertEqual(dense.limit, 32)
        self.assertEqual(dense.query, [0.1] * 1024)
        self.assertEqual(sparse.using, "sparse")
        self.assertEqual(sparse.limit, 32)
        self.assertIsInstance(sparse.query, qmodels.SparseVector)
        self.assertEqual(list(sparse.query.indices), [4, 9])
        self.assertEqual(list(sparse.query.values), [0.5, 0.25])
        # Sentinel exclusion rides both channels via the caller's filter.
        for channel in (dense, sparse):
            must_not = list(channel.filter.must_not or [])
            self.assertTrue(any(
                getattr(c, "key", None) == "_embed_meta" for c in must_not
            ))
        self.assertIsInstance(kwargs["query"], qmodels.FusionQuery)
        self.assertEqual(kwargs["query"].fusion, qmodels.Fusion.RRF)
        self.assertEqual(kwargs["limit"], 5)

    def test_fuse_k_default(self):
        self.assertEqual(qdrant_query_support.fuse_k_from_env(10), 64)
        self.assertEqual(qdrant_query_support.fuse_k_from_env(40), 160)

    def test_named_dense_vector_keeps_using(self):
        kwargs = qdrant_query_support.hybrid_fusion_query(
            [0.1] * 1024, SPARSE_PAIR, vector_name="legacy_dense", top_k=3,
            query_filter=None,
        )
        self.assertEqual(kwargs["prefetch"][0].using, "legacy_dense")

    def test_merge_rrf_scores_dedupe(self):
        merged = qdrant_query_support.merge_hits(
            [
                [{"id": "p1", "score": 0.6, "payload": {}, "_collection": "a"},
                 {"id": "p2", "score": 0.4, "payload": {}, "_collection": "a"}],
                [{"id": "p1", "score": 0.8, "payload": {}, "_collection": "b"}],
            ],
            5,
        )
        self.assertEqual([h["id"] for h in merged], ["p1", "p2"])
        self.assertEqual(merged[0]["score"], 0.8)
        self.assertEqual(merged[0]["_collection"], "b")


class EncodeOnceTests(unittest.TestCase):
    """AC4b — backends fetch the pair via the shared helper, encode once."""

    def setUp(self):
        os.environ["EMBED_HYBRID"] = "1"

    def tearDown(self):
        os.environ.pop("EMBED_HYBRID", None)
        embed_runtime.reset_caches()
        embed_runtime.reset_sparse_head_cache()

    def test_query_vector_pair_encode_once(self):
        stub = _PairRecordingST()
        with tempfile_prefix() as model_dir:
            import torch

            torch.save({
                "weight": torch.ones(1, 4).half(),
                "bias": torch.zeros(1).half(),
            }, Path(model_dir) / "sparse_linear.pt")
            with patch.object(embed_runtime, "get_sentence_transformer",
                              return_value=stub), \
                 patch.object(embed_runtime, "_st_local_files_only",
                              return_value=True):
                dense_a, sparse_a = embed_runtime.query_vector_pair(
                    "sync worker", model_dir, device_name="cpu")
                dense_b, sparse_b = embed_runtime.query_vector_pair(
                    "sync worker", model_dir, device_name="cpu")
        self.assertEqual(dense_a, dense_b)
        self.assertEqual(sparse_a, sparse_b)
        # One dense encode (LRU), one sparse encode (sparse cache).
        self.assertEqual(len(stub.encode_calls), 1)
        self.assertEqual(len(stub.forward_count), 1)

    def test_pair_off_flag_returns_none_sparse(self):
        os.environ.pop("EMBED_HYBRID", None)
        stub = _PairRecordingST()
        with patch.object(embed_runtime, "get_sentence_transformer",
                          return_value=stub):
            dense, sparse = embed_runtime.query_vector_pair("q", BGE, device_name="cpu")
        self.assertEqual(sparse, None)
        self.assertEqual(dense, [0.5, 0.25])

    def test_query_sparse_only_degrades_on_missing_head(self):
        """Reader-soft: a missing head must never fail a query (review fix)."""
        os.environ["EMBED_HYBRID"] = "1"
        embed_runtime.reset_caches()
        embed_runtime.reset_sparse_head_cache()
        with patch.object(embed_runtime, "_hybrid_head_path", return_value=None), \
             patch.object(embed_runtime, "get_sentence_transformer",
                          return_value=_PairRecordingST()), \
             patch.object(embed_runtime, "_st_local_files_only", return_value=True):
            with self.assertLogs("tools.common.embed_runtime", level="WARNING"):
                sparse = embed_runtime.query_sparse_only("q", BGE, device_name="cpu")
        self.assertEqual(sparse, None)

    def test_search_collection_auto_fusion_none_branch(self):
        """fusion=None auto-fuses when THIS collection declares sparse."""
        store = _QueryRecordingStore(sparse_collections={"a"})
        os.environ["EMBED_HYBRID"] = "1"
        qdrant_query_support.search_collection(
            store, "a", [0.1] * 1024, None, 5, sparse_vector=SPARSE_PAIR,
        )
        self.assertIn("prefetch", store.query_calls[0][1])
        store.query_calls.clear()
        qdrant_query_support.search_collection(
            store, "b", [0.1] * 1024, None, 5, sparse_vector=SPARSE_PAIR,
        )
        self.assertNotIn("prefetch", store.query_calls[0][1])


class _FakeTokenizer:
    """Word→stable-id tokenizer for stub encoders (specials 0-3)."""

    special_tokens_map = {
        "cls_token": "<s>", "eos_token": "</s>",
        "pad_token": "<pad>", "unk_token": "<unk>",
    }

    def convert_tokens_to_ids(self, token):
        return {"<s>": 0, "</s>": 2, "<pad>": 1, "<unk>": 3}.get(token, 4)

    def __call__(self, texts, padding=False, truncation=False, max_length=512,
                 return_tensors=None):
        import torch

        rows = []
        for text in texts:
            ids = [0]
            for word in str(text).split():
                ids.append(4 + (sum(ord(ch) for ch in word) % 29))
            ids.append(2)
            rows.append(ids[:max_length])
        width = max(len(row) for row in rows)
        input_ids = [row + [1] * (width - len(row)) for row in rows]
        attention = [[1] * len(row) + [0] * (width - len(row)) for row in rows]
        return {
            "input_ids": torch.tensor(input_ids),
            "attention_mask": torch.tensor(attention),
        }


class _PairRecordingST:
    def __init__(self):
        self.encode_calls = []
        self.forward_count = []
        import torch

        holder = self

        class _Encoder(torch.nn.Module):
            def forward(self, **features):
                holder.forward_count.append(1)
                ids = features["input_ids"]
                hidden = torch.zeros(ids.shape[0], ids.shape[1], 4)
                hidden[:, :, 0] = 1.0
                return type("Out", (), {"last_hidden_state": hidden})()

        class _First:
            tokenizer = _FakeTokenizer()
            auto_model = _Encoder()

        self._first = _First()

    def __getitem__(self, index):
        return self._first

    def encode(self, sentences, **kwargs):
        self.encode_calls.append((list(sentences), kwargs))
        import torch

        return torch.tensor([[0.5, 0.25]])


def tempfile_prefix():
    import tempfile

    return tempfile.TemporaryDirectory(prefix="bge-m3-")


if __name__ == "__main__":
    unittest.main()
