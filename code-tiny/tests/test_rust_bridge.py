"""A/B parity tests: Rust PyO3 path vs Python reference path.

Bridge dispatch được điều khiển qua env ``CORTEX_RETRIEVAL_RUST``:
  - ``0``   → buộc đường Python (reference).
  - unset   → auto (Rust nếu có).

Các test A/B tự skip khi extension chưa build (``make rust-pyo3``) để
môi trường không có Rust toolchain vẫn xanh.
"""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager

from tools.common import rust_bridge
from tools.common.bm25_ranker import BM25Ranker
from tools.common.query_intent_classifier import classify_query, get_weight_profile
from tools.common.query_understanding import QueryUnderstanding

_EXTENSION_AVAILABLE = rust_bridge.available()

QUERIES = [
    "who calls the validateToken function",
    "find code similar to payment flow",
    "recently changed files",
    "explain the authentication flow with JWT",
    "hàm xử lý thanh toán bị lỗi",
    "",
]

DOCS = [
    {"key": "fn_a", "body": "payment retry policy for failed transactions"},
    {"key": "fn_b", "body": "refund payment gateway timeout"},
    {"key": "fn_c", "body": "database session savepoint helper"},
]


@contextmanager
def bridge_mode(value):
    """Run the block with ``CORTEX_RETRIEVAL_RUST`` pinned to *value*."""
    old = os.environ.get(rust_bridge.ENV_OVERRIDE)
    if value is None:
        os.environ.pop(rust_bridge.ENV_OVERRIDE, None)
    else:
        os.environ[rust_bridge.ENV_OVERRIDE] = value
    rust_bridge.reset_cache()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(rust_bridge.ENV_OVERRIDE, None)
        else:
            os.environ[rust_bridge.ENV_OVERRIDE] = old
        rust_bridge.reset_cache()


def python_result(callable_):
    with bridge_mode("0"):
        return callable_()


def rust_result(callable_):
    with bridge_mode(None):
        return callable_()


class BridgeModeTests(unittest.TestCase):
    """Env override hoạt động kể cả khi không có extension."""

    def test_off_mode_disables_bridge(self):
        with bridge_mode("0"):
            self.assertIsNone(rust_bridge.load_extension())
            self.assertFalse(rust_bridge.available())
            # classify_query vẫn trả kết quả Python
            self.assertEqual(classify_query("who calls X"), "structural")

    def test_cache_invalidation_on_reset(self):
        with bridge_mode("0"):
            self.assertIsNone(rust_bridge.load_extension())
        if _EXTENSION_AVAILABLE:
            with bridge_mode(None):
                self.assertTrue(rust_bridge.available())


@unittest.skipUnless(_EXTENSION_AVAILABLE, "cortex_retrieval_py not built (make rust-pyo3)")
class RustPythonParityTests(unittest.TestCase):
    """Cùng input qua 2 đường phải cho cùng output."""

    def test_classify_query_parity(self):
        for query in QUERIES:
            with self.subTest(query=query):
                self.assertEqual(
                    rust_result(lambda: classify_query(query)),
                    python_result(lambda: classify_query(query)),
                )

    def test_get_weight_profile_parity(self):
        for intent in ("semantic", "structural", "temporal", "default", "unknown-intent"):
            with self.subTest(intent=intent):
                self.assertEqual(
                    rust_result(lambda: get_weight_profile(intent)),
                    python_result(lambda: get_weight_profile(intent)),
                )

    def test_query_understanding_parity(self):
        for query in QUERIES:
            if not query.strip():
                continue  # empty fast-path chạy trước bridge ở cả 2 đường
            with self.subTest(query=query):
                r = rust_result(lambda: QueryUnderstanding.from_text(query).to_dict())
                p = python_result(lambda: QueryUnderstanding.from_text(query).to_dict())
                self.assertEqual(r, p)

    def test_bm25_ranker_parity(self):
        for query in ("payment", "payment gateway", "session", "nonexistent", ""):
            with self.subTest(query=query):

                def scored():
                    ranker = BM25Ranker()
                    ranker.build_index(DOCS, text_field="body", id_field="key")
                    return ranker.score(query)

                r = rust_result(scored)
                p = python_result(scored)
                self.assertEqual(set(r), set(p))
                for key, value in p.items():
                    self.assertAlmostEqual(r[key], value, places=9)

    def test_bm25_ranker_available_with_rust_without_rank_bm25(self):
        # Điểm của rust path: BM25 hoạt động cả khi rank-bm25 vắng mặt.
        with bridge_mode(None):
            ranker = BM25Ranker()
            self.assertTrue(ranker.available)
            ranker.build_index(DOCS, text_field="body", id_field="key")
            self.assertIn("fn_a", ranker.score("payment"))


if __name__ == "__main__":
    unittest.main()
