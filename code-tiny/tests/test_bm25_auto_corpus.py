"""Tests cho auto-corpus BM25 trong IntelligentRetrievalEngine (phase A1/A2).

Engine tự build corpus BM25 từ seed candidates (corpus text = identifier
fields: qualified_name, name, kind, file_path) khi caller không inject
``bm25_ranker``. Flag điều khiển: constructor ``auto_bm25`` override env
``CORTEX_BM25_AUTO``; unset → default ON (phase A2 flip, rollback =
``CORTEX_BM25_AUTO=0``).

Engine dùng ở đây là Python thật — chỉ stub 2 method retrieve
(``_retrieve_qdrant`` / ``_retrieve_keyword``) theo pattern phase-04,
không cần Qdrant/graph thật.

Corpus test dùng ≥ 6 documents: với corpus 2-doc, idf của token xuất hiện
ở 1 doc = 0 nên BM25 score = 0 — không đủ để phân biệt bug "mất signal".
"""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from tools.common import rust_bridge
from tools.common.intelligent_retrieval import (
    ENV_BM25_AUTO,
    IntelligentRetrievalEngine,
    _bm25_corpus_text,
)

_EXTENSION_AVAILABLE = rust_bridge.available()


@contextmanager
def bridge_mode(value):
    """Pin ``CORTEX_RETRIEVAL_RUST`` = *value* trong block (None = unset)."""
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


@contextmanager
def bm25_auto_env(value: Optional[str]):
    """Pin ``CORTEX_BM25_AUTO`` = *value* trong block (None = unset)."""
    old = os.environ.get(ENV_BM25_AUTO)
    if value is None:
        os.environ.pop(ENV_BM25_AUTO, None)
    else:
        os.environ[ENV_BM25_AUTO] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(ENV_BM25_AUTO, None)
        else:
            os.environ[ENV_BM25_AUTO] = old


def _seed_documents() -> List[Dict[str, Any]]:
    """6 candidate dicts dạng ``_qdrant_hit_to_candidate`` output (tối giản).

    Token đích ``processpayment`` xuất hiện đúng 1 lần (n_pay); corpus đủ
    lớn để idf > 0.
    """
    docs: List[Dict[str, Any]] = []
    for i in range(6):
        docs.append({
            "node_id":        f"n{i}",
            "name":           f"helper{i}",
            "qualified_name": f"src/mod{i}.ts:helper{i}",
            "kind":           "function",
            "file_path":      f"src/mod{i}.ts",
            "semantic":       0.5 + i / 100,
            "keyword":        0.0,
        })
    docs[2]["name"] = "processPayment"
    docs[2]["qualified_name"] = "src/payment.ts:processPayment"
    docs[2]["file_path"] = "src/payment.ts"
    return docs


def _make_engine(
    docs: List[Dict[str, Any]],
    auto_bm25: Optional[bool] = None,
    keyword_docs: Optional[List[Dict[str, Any]]] = None,
    **kwargs: Any,
) -> IntelligentRetrievalEngine:
    """Engine thật với 2 method retrieve stub — không cần Qdrant/graph."""
    engine = IntelligentRetrievalEngine(auto_bm25=auto_bm25, **kwargs)
    engine._retrieve_qdrant = (
        lambda query, collection, top_k, project_id=None: [dict(d) for d in docs]
    )
    engine._retrieve_keyword = (
        lambda query, top_k, **kw: [dict(d) for d in (keyword_docs or [])]
    )
    return engine


class CorpusTextTests(unittest.TestCase):
    """Golden corpus text: identifier fields → chuỗi chính xác từng char."""

    def test_golden_corpus_text(self):
        candidate = {
            "qualified_name": "src/payment.ts:PaymentController.refund",
            "name": "refund",
            "kind": "method",
            "file_path": "src/payment.ts",
        }
        self.assertEqual(
            _bm25_corpus_text(candidate),
            "src/payment.ts:PaymentController.refund refund method src/payment.ts",
        )

    def test_falsy_fields_are_skipped(self):
        candidate = {
            "qualified_name": "",
            "name": "foo",
            "kind": None,
            "file_path": "",
        }
        self.assertEqual(_bm25_corpus_text(candidate), "foo")

    def test_all_fields_empty_gives_empty_text(self):
        self.assertEqual(_bm25_corpus_text({}), "")


class AutoBm25FlagTests(unittest.TestCase):
    """Resolve cờ: constructor param override env ``CORTEX_BM25_AUTO``."""

    def test_param_overrides_env_on(self):
        with bm25_auto_env("0"):
            self.assertTrue(IntelligentRetrievalEngine(auto_bm25=True)._auto_bm25)

    def test_param_overrides_env_off(self):
        with bm25_auto_env("1"):
            self.assertFalse(IntelligentRetrievalEngine(auto_bm25=False)._auto_bm25)

    def test_env_on(self):
        with bm25_auto_env("1"):
            self.assertTrue(IntelligentRetrievalEngine()._auto_bm25)

    def test_env_off(self):
        # Rollback path: CORTEX_BM25_AUTO=0 tắt auto dù default đã flip ON.
        with bm25_auto_env("0"):
            self.assertFalse(IntelligentRetrievalEngine()._auto_bm25)

    def test_env_unset_defaults_on(self):
        # Phase A2: unset → ON (default flipped sau A/B report).
        with bm25_auto_env(None):
            self.assertTrue(IntelligentRetrievalEngine()._auto_bm25)

    def test_env_truthy_words(self):
        for raw in ("true", "on", "yes"):
            with bm25_auto_env(raw):
                self.assertTrue(IntelligentRetrievalEngine()._auto_bm25)


class AutoBm25SearchTests(unittest.TestCase):
    """Hành vi search: OFF không có signal; ON có signal + weight bm25."""

    def test_auto_off_no_bm25_signal(self):
        with bm25_auto_env("0"):
            engine = _make_engine(_seed_documents(), auto_bm25=None)
            results = engine.search("processPayment", top_k=10, debug=True)
        self.assertTrue(results)
        for r in results:
            self.assertEqual(float(r.node.get("bm25") or 0.0), 0.0)
            self.assertNotIn("_bm25_text", r.node)  # corpus không được build
            # Không có weight bm25 → scorer không cộng signal bm25 (fix a3d5f2d)
            self.assertNotIn("bm25", r.explanation)
            self.assertNotIn("bm25", r.explanation["weighted_contributions"])

    def test_auto_on_injects_signal_and_weight(self):
        engine = _make_engine(_seed_documents(), auto_bm25=True)
        results = engine.search("processPayment", top_k=10, debug=True)
        self.assertTrue(results)
        by_id = {r.node_id: r for r in results}
        target = by_id["n2"]
        # Signal bm25 > 0 trên candidate khớp token đích
        self.assertGreater(float(target.node.get("bm25") or 0.0), 0.0)
        # Corpus text được ghi vào candidate dict đúng như thiết kế
        self.assertEqual(
            target.node["_bm25_text"],
            "src/payment.ts:processPayment processPayment function src/payment.ts",
        )
        # Weight bm25 được inject khi có signal: kiểm chứng qua explanation
        # của ``score_all`` (đúng chỗ — ``weights_used`` là intent profile
        # trước khi engine inject bm25 nên không phản ánh scorer weights).
        self.assertIn("bm25", target.explanation["weighted_contributions"])
        self.assertGreater(
            target.explanation["weighted_contributions"]["bm25"], 0.0
        )

    def test_auto_on_candidates_without_match_have_no_score(self):
        engine = _make_engine(_seed_documents(), auto_bm25=True)
        results = engine.search("processPayment", top_k=10, debug=True)
        others = [r for r in results if r.node_id != "n2"]
        self.assertTrue(others)
        for r in others:
            self.assertEqual(float(r.node.get("bm25") or 0.0), 0.0)

    def test_injected_ranker_takes_precedence_over_auto(self):
        # Inject-ready semantics giữ nguyên: ranker truyền vào → auto corpus
        # KHÔNG build (không có ``_bm25_text``), signal đến từ ranker stub.
        class _StubRanker:
            def score(self, query: str) -> Dict[str, float]:
                return {"n0": 0.75}

        with bm25_auto_env(None):
            engine = _make_engine(
                _seed_documents(),
                auto_bm25=True,
                bm25_ranker=_StubRanker(),
            )
            results = engine.search("processPayment", top_k=10, debug=True)
        by_id = {r.node_id: r for r in results}
        self.assertEqual(float(by_id["n0"].node.get("bm25") or 0.0), 0.75)
        self.assertNotIn("_bm25_text", by_id["n0"].node)

    def test_auto_on_with_no_candidates_is_noop(self):
        engine = _make_engine([], auto_bm25=True)
        # Không candidates → không build corpus, search trả rỗng không lỗi.
        self.assertEqual(engine.search("anything", top_k=5), [])


@unittest.skipUnless(_EXTENSION_AVAILABLE, "cortex_retrieval_py not built (make rust-pyo3)")
class BridgeModeParityTests(unittest.TestCase):
    """Cùng engine + corpus qua 2 bridge mode (python / rust) → cùng score."""

    def _run_search(self) -> Dict[str, tuple]:
        engine = _make_engine(_seed_documents(), auto_bm25=True)
        results = engine.search("processPayment", top_k=10, debug=True)
        return {
            r.node_id: (r.score, round(float(r.node.get("bm25") or 0.0), 9))
            for r in results
        }

    def test_python_and_rust_same_scores(self):
        with bridge_mode("0"):
            python_scores = self._run_search()
        with bridge_mode(None):
            rust_scores = self._run_search()
        self.assertEqual(python_scores, rust_scores)
        # Signal phải fire thật trên cả 2 đường (không phải đều 0)
        self.assertGreater(python_scores["n2"][1], 0.0)


if __name__ == "__main__":
    unittest.main()
