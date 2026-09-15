#!/usr/bin/env python3
"""Parity check PyO3 bindings vs implementation Python tham chiếu.

Chạy: bash scripts/rust_parity/build_pyo3.sh  (build + test 1 lệnh)
"""

# === Phase-08 archive notice (2026-09-15) ===
# PY side archived at phase-08 cutover, fixtures = golden.
# Python analyzer entry points were retired at the phase-08 cutover;
# fixtures under tests/fixtures/ are now the golden reference.
# Do not attempt to re-run the Python side — tools/<lang>/<lang>_analyzer.py
# no longer exists. See plans/260915-analyzer-layer-rust-cutover/reports/phase08-cutover.md.
# === end archive notice ===


from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code-tiny"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cortex_retrieval_py as rust  # noqa: E402

from tools.common.bm25_ranker import BM25Ranker  # noqa: E402
from tools.common.query_intent_classifier import classify_query as py_classify  # noqa: E402
from tools.common.query_intent_classifier import classify_query_explain as py_explain  # noqa: E402
from tools.common.query_intent_classifier import get_weight_profile as py_weights  # noqa: E402
from tools.common.query_understanding import QueryUnderstanding  # noqa: E402

QUERIES = [
    "who calls the validateToken function",
    "find code similar to payment flow",
    "recently changed files",
    "unlikely helper",
    "random gibberish query",
    "",
    "explain the authentication flow with JWT",
]

DOCS = [
    {"symbol_id": "fn_a", "text": "payment retry policy for failed transactions"},
    {"symbol_id": "fn_b", "text": "refund payment gateway timeout"},
    {"symbol_id": "fn_c", "text": "database session savepoint helper"},
]


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "OK " if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        raise SystemExit(1)


def main() -> None:
    # 1. intent classification
    for query in QUERIES:
        check(f"classify_query({query!r})", rust.classify_query(query) == py_classify(query))
        explain_py = py_explain(query)
        explain_rust = dict(zip(("intent", "matched"), rust.classify_query_explain(query)))
        check(
            f"classify_query_explain({query!r})",
            explain_rust == {"intent": explain_py["intent"], "matched": explain_py["matched"]},
        )

    # 2. weight profiles
    for intent in ("semantic", "structural", "temporal", "default", "unknown"):
        check(
            f"get_weight_profile({intent})",
            dict(rust.get_weight_profile(intent)) == py_weights(intent),
        )

    # 3. bm25 scores
    ranker = BM25Ranker()
    ranker.build_index(DOCS, text_field="text", id_field="symbol_id")
    for query in ("payment", "payment gateway", "session", "nonexistent", ""):
        expected = ranker.score(query)
        actual = json.loads(rust.bm25_score(json.dumps(DOCS), query, text_field="text", id_field="symbol_id"))
        check(
            f"bm25_score({query!r})",
            set(actual) == set(expected)
            and all(abs(actual[k] - expected[k]) < 1e-9 for k in expected),
        )

    # 4. query understanding
    texts = [
        "function xử lý thanh toán bị lỗi khi user chưa login",
        "who calls UserService.validate_token in payment module",
        "",
    ]
    for text in texts:
        expected = QueryUnderstanding.from_text(text).to_dict()
        actual = json.loads(rust.query_understanding(text))
        check(f"query_understanding({text[:40]!r}...)", actual == expected)

    print("PyO3 parity: tất cả pass")


if __name__ == "__main__":
    main()
