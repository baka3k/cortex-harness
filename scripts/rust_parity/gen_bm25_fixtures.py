#!/usr/bin/env python3
"""Sinh golden fixtures cho Rust BM25 port từ implementation Python tham chiếu.

Import TRỰC TIẾP `tools.common.bm25_ranker` (không copy code) để mọi thay đổi
hành vi Python đều bắn ra qua fixture diff. Chạy từ repo root:

    uv run --no-project --with rank_bm25==0.2.2 python scripts/rust_parity/gen_bm25_fixtures.py

Output: rust/crates/cortex-retrieval/tests/fixtures/bm25_golden.json (commit vào repo).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code-tiny"))

from tools.common.bm25_ranker import BM25Ranker  # noqa: E402

# Mỗi set: (tên, text_field, id_field, documents).
# Set `code_nodes`: ghi đè terms phổ biến để kích hoạt negative-IDF/epsilon path.
DOC_SETS = [
    (
        "code_nodes",
        "note",
        "symbol_id",
        [
            {"symbol_id": "fn_process_payment", "note": "Process payment retry policy for failed transactions"},
            {"symbol_id": "fn_refund_payment", "note": "Refund payment flow with payment gateway timeout handling"},
            {"symbol_id": "cls_payment_gateway", "note": "Payment gateway client for payment service integration"},
            {"symbol_id": "fn_validate_order", "note": "Validate order payload before order is submitted"},
            {"symbol_id": "fn_submit_order", "note": "Submit order to order service and await confirmation"},
            {"symbol_id": "cls_db_session", "note": "Database session helper with savepoint support"},
            {"symbol_id": "fn_batch_checkpoint", "note": "Persist batch checkpoint for batch restart"},
            {"symbol_id": "xu_ly_thanh_toan", "note": "Hàm xử lý thanh toán bị lỗi timeout khi gọi cổng thanh toán"},
        ],
    ),
    (
        "edge_cases",
        "summary",
        "node_id",
        [
            {"node_id": "empty_text", "summary": ""},
            {"node_id": "none_text", "summary": None},
            {"node_id": "dup_a", "summary": "same tokens same tokens"},
            {"node_id": "dup_b", "summary": "same tokens different order tokens"},
            {"node_id": "UPPER_Mix", "summary": "CamelCase and snake_case and UPPERCASE"},
            {"node_id": "vietnamese", "summary": "Truy vấn dữ liệu đơn hàng theo mã đơn"},
        ],
    ),
]

QUERY_CASES = [
    "payment",
    "payment order",
    "payment payment retry",
    "nonexistentterm",
    "payment nonexistentterm",
    "same tokens",
    "tokens same same",
    "CamelCase",
    "đơn hàng",
    "timeout",
    "  ",
    "",
    "xu_ly_thanh_toan xử lý",
]


def main() -> None:
    fixture = {
        "rank_bm25_version": "0.2.2",
        "generator": "scripts/rust_parity/gen_bm25_fixtures.py",
        "sets": [],
    }
    for name, text_field, id_field, documents in DOC_SETS:
        ranker = BM25Ranker()
        ranker.build_index(documents, text_field=text_field, id_field=id_field)
        if not ranker.available:
            raise SystemExit("rank_bm25 chưa cài — chạy lại bằng uv run --with rank_bm25==0.2.2")
        cases = []
        for query in QUERY_CASES:
            cases.append({"query": query, "expected": ranker.score(query)})
        fixture["sets"].append(
            {
                "name": name,
                "text_field": text_field,
                "id_field": id_field,
                "documents": [
                    {"id": doc.get(id_field, "") or "", "text": doc.get(text_field, "") or ""}
                    for doc in documents
                ],
                "cases": cases,
            }
        )

    out_path = (
        REPO / "rust" / "crates" / "cortex-retrieval" / "tests" / "fixtures" / "bm25_golden.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_path.relative_to(REPO)} ({len(fixture['sets'])} sets)")


if __name__ == "__main__":
    main()
