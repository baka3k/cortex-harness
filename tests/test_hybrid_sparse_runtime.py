"""Phase 01 — hybrid sparse encode runtime (BGE-M3 learned lexical weights).

Covers: the golden fidelity gate against the reference FlagEmbedding output
(fixture committed once from a networked machine), the offline fail-fast
head contract, flag×family no-op behavior, dense byte-parity under the
flag, and the doc-tiny twin parity (embedding_utils).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
DOC_TINY = ROOT / "doc-tiny"
for entry in (CODE_TINY, DOC_TINY):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from tools.common import embed_runtime  # noqa: E402

BGE = "BAAI/bge-m3"
QWEN3 = "Qwen/Qwen3-Embedding-0.6B"
GOLDEN_FIXTURE = ROOT / "tests" / "fixtures" / "bge_m3_sparse_golden.json"

os.environ.setdefault("MCP_PRELOAD_EMBEDDER", "0")


def _bge_snapshot_cached() -> bool:
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(BGE, local_files_only=True)
        return embed_runtime.hybrid_head_available(BGE)
    except Exception:
        return False


class HybridFlagTableTests(unittest.TestCase):
    """AC3 — the flag×family decision table (code side)."""

    def tearDown(self):
        os.environ.pop("EMBED_HYBRID", None)
        embed_runtime._HYBRID_FAMILY_WARNED.clear()

    def test_flag_table(self):
        import embedding_utils as doc_utils

        cases = [
            # (env value, model, expected)
            (None, BGE, False),
            ("0", BGE, False),
            ("1", BGE, True),
            ("true", BGE, True),
            ("yes", BGE, True),
            ("on", BGE, True),
            ("TRUE", BGE, True),
            ("1", "/models/bge-m3-local-snapshot", True),
            # Other families are a no-op even with the flag on.
            ("1", QWEN3, False),
            ("1", "jinaai/jina-embeddings-v3", False),
            ("1", "some-unknown-model", False),
        ]
        for env_value, model, expected in cases:
            with self.subTest(env=env_value, model=model):
                if env_value is None:
                    os.environ.pop("EMBED_HYBRID", None)
                else:
                    os.environ["EMBED_HYBRID"] = env_value
                self.assertEqual(embed_runtime.hybrid_enabled(model), expected)
                # Doc twin decides identically on the same inputs.
                self.assertEqual(doc_utils.hybrid_enabled(model), expected)

    def test_non_bge_family_warns_once(self):
        os.environ["EMBED_HYBRID"] = "1"
        with self.assertLogs("tools.common.embed_runtime", level="WARNING") as captured:
            self.assertFalse(embed_runtime.hybrid_enabled(QWEN3))
            self.assertFalse(embed_runtime.hybrid_enabled(QWEN3))
        warnings = [line for line in captured.output if "EMBED_HYBRID" in line]
        self.assertEqual(len(warnings), 1)


class SparseHeadGateTests(unittest.TestCase):
    """AC2 — missing head fails fast, actionable, zero network."""

    def setUp(self):
        embed_runtime.reset_caches()
        embed_runtime.reset_sparse_head_cache()

    def tearDown(self):
        os.environ.pop("EMBED_HYBRID", None)
        embed_runtime.reset_caches()
        embed_runtime.reset_sparse_head_cache()

    def _fake_st_model(self, hidden_size: int = 4):
        """A minimal stand-in with the ST Transformer-module layout."""
        import torch

        class _Encoder(torch.nn.Module):
            def forward(self, **features):
                ids = features["input_ids"]
                batch, seq = ids.shape
                hidden = torch.zeros(batch, seq, hidden_size)
                hidden[:, :, 0] = 1.0
                return type("Out", (), {"last_hidden_state": hidden})()

        class _First:
            tokenizer = _FakeTokenizer()
            auto_model = _Encoder()

        class _Model:
            def __getitem__(self, index):
                return _First()

        return _Model()

    def test_missing_head_raises_actionable_error(self):
        os.environ["EMBED_HYBRID"] = "1"
        with tempfile.TemporaryDirectory() as empty_dir:
            with patch.object(
                embed_runtime, "get_sentence_transformer",
                return_value=self._fake_st_model(),
            ), patch.object(
                embed_runtime, "_st_local_files_only", return_value=True
            ), patch.object(
                embed_runtime, "_hybrid_head_path", return_value=None
            ):
                with self.assertRaises(RuntimeError) as caught:
                    embed_runtime.encode_sparse(BGE, ["worker init"], device="cpu")
        message = str(caught.exception)
        self.assertIn("sparse_linear.pt", message)
        self.assertIn("prefetch_hybrid_heads.py", message)
        self.assertIn("EMBED_HYBRID", message)

    def test_local_dir_model_encodes_with_local_head(self):
        """End-to-end sparse math on a stub encoder with a real head file."""
        import torch

        os.environ["EMBED_HYBRID"] = "1"
        hidden_size = 4
        with tempfile.TemporaryDirectory() as model_dir:
            torch.manual_seed(7)
            head = {
                "weight": torch.randn(1, hidden_size).half(),
                "bias": torch.randn(1).half(),
            }
            torch.save(head, Path(model_dir) / "sparse_linear.pt")
            with patch.object(
                embed_runtime, "get_sentence_transformer",
                return_value=self._fake_st_model(hidden_size),
            ), patch.object(
                embed_runtime, "_st_local_files_only", return_value=True
            ):
                results = embed_runtime.encode_sparse(
                    model_dir, ["ab ab c"], device="cpu"
                )
        self.assertEqual(len(results), 1)
        indices, values = results[0]
        # Fake tokenizer: ids 1..n per token, specials are 0/1/2/3; token id 4
        # repeats ("ab ab") and must keep its max weight, sorted ascending.
        self.assertEqual(indices, sorted(indices))
        self.assertTrue(all(i > 3 for i in indices), indices)
        self.assertTrue(all(v > 0 for v in values))
        self.assertNotIn(3, indices)  # unk

    def test_encode_sparse_empty_input(self):
        self.assertEqual(embed_runtime.encode_sparse(BGE, [], device="cpu"), [])


class _FakeTokenizer:
    """Tokenizes by word: specials 0-3, words get stable ids from letter sum."""

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


class GoldenFidelityTests(unittest.TestCase):
    """AC1 — encode_sparse must reproduce the reference FlagEmbedding output.

    The fixture (tests/fixtures/bge_m3_sparse_golden.json) was generated once
    from BGEM3FlagModel.encode(return_sparse=True) on a networked machine.
    Skipped when the snapshot or the head is not cached locally.
    """

    @unittest.skipUnless(
        GOLDEN_FIXTURE.is_file() and _bge_snapshot_cached(),
        "golden fixture or bge-m3 snapshot/head not cached on this machine",
    )
    def test_encode_sparse_matches_reference(self):
        fixture = json.loads(GOLDEN_FIXTURE.read_text(encoding="utf-8"))
        texts = [entry["text"] for entry in fixture["texts"]]
        results = embed_runtime.encode_sparse(BGE, texts, device="cpu")
        self.assertEqual(len(results), len(texts))
        for entry, (indices, values) in zip(fixture["texts"], results):
            with self.subTest(text=entry["text"][:40]):
                expected = {int(k): v for k, v in entry["lexical_weights"].items()}
                self.assertEqual(set(indices), set(expected))
                for index, value in zip(indices, values):
                    self.assertAlmostEqual(value, expected[index], places=4)

    def test_dense_path_byte_parallel_under_flag(self):
        """AC1 — hybrid never touches the dense encode path."""
        stub = _DenseRecordingST([[0.5, 0.25]])
        try:
            for flag in (None, "1"):
                os.environ.pop("EMBED_HYBRID", None)
                if flag:
                    os.environ["EMBED_HYBRID"] = flag
                embed_runtime.reset_caches()
                with patch.object(embed_runtime, "get_sentence_transformer",
                                  return_value=stub):
                    self.assertEqual(embed_runtime.embed_query("q", BGE), [0.5, 0.25])
                self.assertEqual(stub.encode_calls, [(["q"], {
                    "normalize_embeddings": True,
                })])
                stub.encode_calls.clear()
        finally:
            os.environ.pop("EMBED_HYBRID", None)
            embed_runtime.reset_caches()


class _DenseRecordingST:
    """ST stub that serves both ``encode`` (dense) and the ``[0]`` sparse
    layout, so one object can back the whole query pair."""

    def __init__(self, vectors, hidden_size: int = 4):
        self.vectors = vectors
        self.encode_calls = []
        import torch

        class _Encoder(torch.nn.Module):
            def forward(self, **features):
                ids = features["input_ids"]
                hidden = torch.zeros(ids.shape[0], ids.shape[1], hidden_size)
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

        return torch.tensor(self.vectors)


class OfflineNoNetworkTests(unittest.TestCase):
    """AC2 — a complete snapshot + head serves a hybrid encode fully offline."""

    def test_encode_sparse_zero_network_on_complete_cache(self):
        if not _bge_snapshot_cached():
            self.skipTest("bge-m3 snapshot or sparse head not cached on this machine")
        code = (
            "import sys; sys.path.insert(0, {code_tiny!r}); "
            "from tools.common import embed_runtime; "
            "out = embed_runtime.encode_sparse({bge!r}, ['incremental sync worker'], "
            "device='cpu'); "
            "assert out and out[0][0], 'empty sparse output'; "
            "print('sparse-ok', len(out[0][0]))"
        ).format(code_tiny=str(CODE_TINY), bge=BGE)
        subprocess.run(
            [sys.executable, "-c", code],
            env=dict(os.environ, HF_ENDPOINT="http://127.0.0.1:9",
                     EMBED_HYBRID="1", EMBED_DEVICE="cpu"),
            check=True,
            timeout=600,
        )


class DocTwinParityTests(unittest.TestCase):
    """AC4 — the doc-side twin mirrors the code-side sparse contract."""

    def test_sparse_vector_conversion_contract(self):
        import embedding_utils as doc_utils

        # A real lexical_weights dict cannot carry duplicate ids, so the
        # contract under test is: drop w<=0, sort ids, keep positive max.
        indices, values = doc_utils.sparse_lexical_weights_to_vector(
            {"5": 0.4, 2: 0.9, "7": 0.0, "3": -0.5, 11: 1.2}
        )
        self.assertEqual(indices, [2, 5, 11])
        self.assertAlmostEqual(values[0], 0.9)
        self.assertAlmostEqual(values[1], 0.4)
        self.assertAlmostEqual(values[2], 1.2)

    def test_marker_and_head_constants_twinned(self):
        import embedding_utils as doc_utils

        self.assertEqual(doc_utils.BGE_M3_EMBEDDING_MARKER,
                         embed_runtime.BGE_M3_EMBEDDING_MARKER)
        self.assertEqual(doc_utils.HYBRID_SPARSE_HEAD_FILE,
                         embed_runtime.HYBRID_SPARSE_HEAD_FILE)
        self.assertEqual(doc_utils.HYBRID_SPARSE_VECTOR_NAME,
                         embed_runtime.HYBRID_SPARSE_VECTOR_NAME)


class EmbedQueryPairTests(unittest.TestCase):
    """embed_query_pair returns the dense path untouched + a cached sparse."""

    def tearDown(self):
        os.environ.pop("EMBED_HYBRID", None)
        embed_runtime.reset_caches()
        embed_runtime.reset_sparse_head_cache()

    def test_pair_uses_dense_lru_and_own_sparse_cache(self):
        stub = _DenseRecordingST([[0.5, 0.25]])
        # The dir name must carry the bge-m3 family marker so model_policy
        # resolves the same ST query path the production default uses.
        with tempfile.TemporaryDirectory(prefix="bge-m3-") as model_dir:
            import torch

            torch.save({
                "weight": torch.ones(1, 4).half(),
                "bias": torch.zeros(1).half(),
            }, Path(model_dir) / "sparse_linear.pt")
            with patch.object(embed_runtime, "get_sentence_transformer",
                              return_value=stub), \
                 patch.object(embed_runtime, "_st_local_files_only",
                              return_value=True):
                dense_a, sparse_a = embed_runtime.embed_query_pair(
                    "sync worker", model_dir, device_name="cpu")
                dense_b, sparse_b = embed_runtime.embed_query_pair(
                    "sync worker", model_dir, device_name="cpu")
        self.assertEqual(dense_a, dense_b)
        self.assertEqual(sparse_a, sparse_b)
        # Dense encode ran exactly once (query LRU); sparse served from its
        # own cache on the second call.
        self.assertEqual(stub.encode_calls, [(["sync worker"], {
            "normalize_embeddings": True,
        })])


if __name__ == "__main__":
    unittest.main()
