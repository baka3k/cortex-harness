#!/usr/bin/env python3
"""Sinh golden fixtures cho fusion engine (phase 04).

Dựng `IntelligentRetrievalEngine` bằng `__new__` (bỏ qua __init__ có network),
stub `_retrieve_qdrant`/`_retrieve_keyword` trả seeds được convert bằng REAL
`_qdrant_hit_to_candidate`/`_graph_keyword_node_to_candidate`, rồi gọi `search()`
thật — mọi bước merge/BM25/freshness/normalize/score/rank chạy code Python gốc.

Chạy từ repo root:
    uv run --no-project --with rank_bm25==0.2.2 python scripts/rust_parity/gen_fusion_fixtures.py

`datetime.datetime.now` được monkeypatch về mốc cố định (freshness deterministic);
`now_epoch` emit cho Rust truyền vào `search(...)`.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code-tiny"))

from tools.common.bm25_ranker import BM25Ranker  # noqa: E402
from tools.common.intelligent_retrieval import (  # noqa: E402
    IntelligentRetrievalEngine,
    _graph_keyword_node_to_candidate,
    _qdrant_hit_to_candidate,
)

FIXTURES = REPO / "rust" / "crates" / "cortex-retrieval" / "tests" / "fixtures"

# ── Fixed clock ──────────────────────────────────────────────────────────────
FIXED = dt.datetime(2026, 9, 13, 12, 0, 0, tzinfo=dt.timezone.utc)


class _FixedDatetime(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED if tz else FIXED.replace(tzinfo=None)


# ── Seed data (hits/nodes thô — convert bằng code Python thật) ──────────────
QDRANT_HITS_A = [
    {"id": "q-1", "score": 0.91,
     "payload": {"symbol_id": "fn_validate_token", "name": "validate_token",
                 "qualified_name": "auth.fn_validate_token", "kind": "function",
                 "file_path": "src/auth.py", "doc_confidence": 0.7,
                 "signals": {"usage": 0.4}, "exported": True,
                 "project_id": "demo", "language": "python"}},
    {"id": "q-2", "score": 0.72,
     "payload": {"symbol_id": "fn_login", "name": "login", "kind": "function",
                 "file_path": "src/auth.py", "doc_confidence": 0.5}},
    {"id": "q-3", "score": 0.55,
     "payload": {"symbol_id": "cls_session", "name": "Session", "kind": "class",
                 "file_path": "src/session.py"}},
]

KEYWORD_NODES_A = [
    {"id": "fn_validate_token", "name": "validate_token",
     "qualified_name": "auth.fn_validate_token", "kind": "function",
     "file_path": "src/auth.py", "doc_confidence": 0.7, "exported": True},
    {"id": "fn_check_permission", "name": "check_permission",
     "kind": "function", "file_path": "src/auth.py"},
]

QDRANT_HITS_B = [
    {"id": "q-10", "score": 0.88,
     "payload": {"symbol_id": "fn_refund_payment", "name": "refund_payment",
                 "kind": "function", "file_path": "src/payment.py",
                 "doc_confidence": 0.9, "signals": {"usage": 0.2}}},
    {"id": "q-11", "score": 0.41,
     "payload": {"symbol_id": "fn_charge", "name": "charge", "kind": "function",
                 "file_path": "src/payment.py"}},
]

KEYWORD_NODES_C = [
    {"id": "fn_sync_state", "name": "sync_state", "kind": "function",
     "file_path": "src/state.py"},
]

BM25_DOCS = [
    {"symbol_id": "fn_validate_token", "note": "validate token credential session"},
    {"symbol_id": "fn_login", "note": "login session authentication"},
    {"symbol_id": "fn_check_permission", "note": "permission access control check"},
    {"symbol_id": "fn_refund_payment", "note": "refund payment transaction"},
    {"symbol_id": "fn_sync_state", "note": "sync state checkpoint"},
]

CASES = [
    {
        "name": "structural_with_bm25",
        "query": "who calls validateToken",
        "top_k": 10,
        "debug": True,
        "weight_override": None,
        "bm25": True,
        "freshness_map": {"fn_validate_token": "2026-09-13T11:00:00+00:00",
                          "fn_login": "2026-08-14T12:00:00+00:00"},
        "dirty": ["fn_check_permission"],
        "qdrant_hits": QDRANT_HITS_A,
        "keyword_nodes": KEYWORD_NODES_A,
    },
    {
        "name": "semantic_override_topk3_no_bm25",
        "query": "explain payment refund flow",
        "top_k": 3,
        "debug": True,
        "weight_override": {"semantic": 0.6, "graph": 0.1},
        "bm25": False,
        "freshness_map": {},
        "dirty": [],
        "qdrant_hits": QDRANT_HITS_B,
        "keyword_nodes": [],
    },
    {
        "name": "temporal_bm25_adds_minimal_candidate",
        "query": "what changed in sync state checkpoint",
        "top_k": 10,
        "debug": True,
        "weight_override": None,
        "bm25": True,
        "freshness_map": {},
        "dirty": [],
        "qdrant_hits": [],
        "keyword_nodes": KEYWORD_NODES_C,
    },
    {
        "name": "single_candidate_semantic_norm_zero",
        "query": "random gibberish query",
        "top_k": 10,
        "debug": True,
        "weight_override": None,
        "bm25": False,
        "freshness_map": {"fn_charge": "2026-09-13T18:59:59+07:00"},
        "dirty": [],
        "qdrant_hits": QDRANT_HITS_B[1:],
        "keyword_nodes": [],
    },
    {
        "name": "empty_query",
        "query": "   ",
        "top_k": 5,
        "debug": True,
        "weight_override": None,
        "bm25": True,
        "freshness_map": {},
        "dirty": [],
        "qdrant_hits": QDRANT_HITS_A,
        "keyword_nodes": KEYWORD_NODES_A,
    },
]


def build_engine(case: dict) -> IntelligentRetrievalEngine:
    engine = IntelligentRetrievalEngine.__new__(IntelligentRetrievalEngine)
    engine._qdrant_url = ""
    engine._collection = ""
    engine._embedder = None
    engine._graph = None
    engine._database = ""
    engine._freshness = case["freshness_map"]
    engine._dirty = set(case["dirty"])
    engine._seed_k = 20
    engine._expander = None
    engine._expand_depth = 2
    engine._expand_limit = 50
    engine._bm25_weight = 0.15
    if case["bm25"]:
        ranker = BM25Ranker()
        ranker.build_index(BM25_DOCS, text_field="note", id_field="symbol_id")
        engine._bm25_ranker = ranker
    else:
        engine._bm25_ranker = None

    qdrant_seeds = [_qdrant_hit_to_candidate(h, h["score"]) for h in case["qdrant_hits"]]
    keyword_seeds = [_graph_keyword_node_to_candidate(n) for n in case["keyword_nodes"]]

    def _stub_qdrant(query, collection, top_k, project_id=None):
        return qdrant_seeds

    def _stub_keyword(query, top_k, labels=None, properties=None, project_id=None):
        return keyword_seeds

    engine._retrieve_qdrant = _stub_qdrant
    engine._retrieve_keyword = _stub_keyword
    return engine


def main() -> None:
    original = dt.datetime
    dt.datetime = _FixedDatetime
    try:
        cases = []
        for case in CASES:
            engine = build_engine(case)
            results = engine.search(
                case["query"], top_k=case["top_k"], debug=case["debug"],
                weight_override=case["weight_override"],
            )
            cases.append({
                "name": case["name"],
                "query": case["query"],
                "top_k": case["top_k"],
                "debug": case["debug"],
                "weight_override": case["weight_override"],
                "now_epoch": FIXED.timestamp(),
                "bm25_docs": BM25_DOCS if case["bm25"] else None,
                "freshness_map": case["freshness_map"],
                "dirty": case["dirty"],
                "qdrant_hits": case["qdrant_hits"],
                "keyword_nodes": case["keyword_nodes"],
                "expected": [r.to_dict() for r in results],
            })
    finally:
        dt.datetime = original

    FIXTURES.mkdir(parents=True, exist_ok=True)
    path = FIXTURES / "fusion_golden.json"
    path.write_text(
        json.dumps({"generator": __file__, "cases": cases}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {path.relative_to(REPO)} ({len(cases)} cases)")


if __name__ == "__main__":
    main()
