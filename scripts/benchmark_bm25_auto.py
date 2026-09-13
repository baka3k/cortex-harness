"""A/B ranking report cho auto-BM25 corpus trong IntelligentRetrievalEngine.

So sánh 2 chế độ của engine trên cùng candidate set (retrieval stub, engine
Python thật — giống pattern test phase-04):

  - OFF  ``auto_bm25=False``  (rollback path, ``CORTEX_BM25_AUTO=0``)
  - ON   ``auto_bm25=True``   (auto build corpus BM25 từ seed candidates)

Candidate set ~40 nodes sinh từ dữ liệu giống realworld (codebase
payment/auth/order TS + PY), 12 queries chia 3 intent (4 structural,
4 semantic, 4 temporal, gồm cả tiếng Việt). In ra markdown: ranking diff
top-10 + latency. Kết quả feed decision record phase A2:
``plans/1309-2104-parallel-bm25-journal-cutover/reports/ab-bm25-auto.md``.

Tiêu chí flip (plan A2): bm25 chỉ được đảo thứ tự các candidate có semantic
score gần nhau (chênh < 0.1) hoặc thêm hit mới có lý do keyword rõ.

Usage::

    PYTHONPATH=code-tiny .venv/bin/python scripts/benchmark_bm25_auto.py
"""

from __future__ import annotations

import hashlib
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parents[1]
_CODE_TINY = _ROOT / "code-tiny"
if str(_CODE_TINY) not in sys.path:
    sys.path.insert(0, str(_CODE_TINY))

from tools.common.intelligent_retrieval import IntelligentRetrievalEngine
from tools.common.query_intent_classifier import classify_query

TOP_K = 10
# Ngưỡng "semantic gần nhau" của tiêu chí flip trong plan A2.
CLOSE_SEMANTIC_DELTA = 0.1


# ─────────────────────────────────────────────────────────────
# Candidate set ~40 nodes — dữ liệu giống realworld (identifier fields)
# ─────────────────────────────────────────────────────────────

# (file_path, name, kind) — node_id = "<file_path>:<name>"
_DOC_SPECS: List[Tuple[str, str, str]] = [
    ("services/payment/payment.controller.ts", "processPayment", "method"),
    ("services/payment/payment.controller.ts", "refundPayment", "method"),
    ("services/payment/payment.controller.ts", "captureCharge", "method"),
    ("services/payment/payment.controller.ts", "PaymentController", "class"),
    ("services/payment/payment.gateway.ts", "chargeGateway", "class"),
    ("services/payment/payment.schema.ts", "paymentSchema", "const"),
    ("services/payment/refund.worker.ts", "refundWorker", "function"),
    ("services/auth/auth.service.ts", "validateToken", "method"),
    ("services/auth/auth.service.ts", "refreshToken", "method"),
    ("services/auth/auth.service.ts", "hashPassword", "function"),
    ("services/auth/auth.service.ts", "AuthService", "class"),
    ("services/auth/session.store.ts", "loadSession", "function"),
    ("services/auth/jwt.utils.ts", "signJwt", "function"),
    ("services/order/order.service.ts", "createOrder", "method"),
    ("services/order/order.service.ts", "cancelOrder", "method"),
    ("services/order/order.service.ts", "OrderService", "class"),
    ("services/order/order.mapper.ts", "mapOrderRow", "function"),
    ("services/invoice/invoice.service.ts", "generateInvoice", "method"),
    ("services/invoice/invoice.service.ts", "calculateInvoiceTotal", "method"),
    ("services/invoice/invoice.pdf.ts", "renderInvoicePdf", "function"),
    ("services/shipping/shipping.service.ts", "estimateShipping", "method"),
    ("services/shipping/shipping.service.ts", "trackShipment", "method"),
    ("services/notification/notification.queue.ts", "sendNotification", "method"),
    ("services/notification/notification.queue.ts", "NotificationQueue", "class"),
    ("services/user/user.service.ts", "createUser", "method"),
    ("services/user/user.service.ts", "updateUserProfile", "method"),
    ("services/user/user.repository.ts", "findUserByEmail", "function"),
    ("services/cart/cart.service.ts", "addToCart", "method"),
    ("services/cart/cart.service.ts", "mergeCarts", "function"),
    ("services/report/report.builder.ts", "buildDailyReport", "function"),
    ("services/cache/cache.helpers.ts", "cacheGet", "function"),
    ("services/cache/cache.helpers.ts", "cacheSet", "function"),
    ("services/audit/audit.log.ts", "auditLog", "function"),
    ("workers/refund_py/refund_worker.py", "retry_refund", "function"),
    ("workers/invoice_py/sync_invoice.py", "sync_invoice", "function"),
    ("workers/order_py/order_events.py", "emit_order_event", "function"),
    ("tests/payment/payment.fixture.ts", "processPaymentFixture", "function"),
    ("tests/auth/auth.fixture.ts", "validateTokenFixture", "function"),
    ("tests/order/order.fixture.ts", "createOrderFixture", "function"),
    ("lib/retry/retry.policy.ts", "withRetry", "function"),
]


def _build_documents() -> List[Dict[str, Any]]:
    """40 candidate dicts đúng shape ``_qdrant_hit_to_candidate`` output."""
    docs: List[Dict[str, Any]] = []
    for file_path, name, kind in _DOC_SPECS:
        node_id = f"{file_path}:{name}"
        docs.append({
            "node_id":        node_id,
            "name":           name,
            "qualified_name": node_id,
            "kind":           kind,
            "file_path":      file_path,
            "semantic":       0.0,  # gán theo query ở _semantic_for
            "keyword":        0.0,
            "graph":          0.0,
            "freshness":      0.0,
            "confidence":     0.0,
            "usage":          0.0,
        })
    return docs


# ─────────────────────────────────────────────────────────────
# Query set: 4 structural + 4 semantic + 4 temporal (gồm tiếng Việt)
# ─────────────────────────────────────────────────────────────

# query → danh sách node_id "vector neighbor" theo thứ tự relevance; đây là
# cái mà Qdrant semantic search thực tế sẽ trả (kèm semantic score giảm dần).
QUERY_AFFINITY: Dict[str, List[str]] = {
    # structural
    "who calls processPayment": [
        "services/payment/payment.controller.ts:processPayment",
        "services/payment/payment.controller.ts:PaymentController",
        "tests/payment/payment.fixture.ts:processPaymentFixture",
        "workers/refund_py/refund_worker.py:retry_refund",
    ],
    "callers of validateToken": [
        "services/auth/auth.service.ts:validateToken",
        "services/auth/auth.service.ts:AuthService",
        "tests/auth/auth.fixture.ts:validateTokenFixture",
        "services/auth/session.store.ts:loadSession",
    ],
    "dependencies of OrderService": [
        "services/order/order.service.ts:OrderService",
        "services/order/order.service.ts:createOrder",
        "services/invoice/invoice.service.ts:generateInvoice",
        "workers/order_py/order_events.py:emit_order_event",
    ],
    "where is sendNotification used": [
        "services/notification/notification.queue.ts:sendNotification",
        "services/notification/notification.queue.ts:NotificationQueue",
        "services/order/order.service.ts:createOrder",
    ],
    # semantic
    "find code similar to refund processing": [
        "services/payment/payment.controller.ts:refundPayment",
        "workers/refund_py/refund_worker.py:retry_refund",
        "services/payment/payment.controller.ts:captureCharge",
        "services/payment/payment.gateway.ts:chargeGateway",
    ],
    "explain the authentication flow with JWT": [
        "services/auth/auth.service.ts:validateToken",
        "services/auth/jwt.utils.ts:signJwt",
        "services/auth/auth.service.ts:refreshToken",
        "services/auth/session.store.ts:loadSession",
    ],
    "hàm xử lý thanh toán bị lỗi": [
        "services/payment/payment.controller.ts:processPayment",
        "services/payment/payment.controller.ts:captureCharge",
        "services/payment/payment.gateway.ts:chargeGateway",
        "workers/refund_py/refund_worker.py:retry_refund",
    ],
    "code similar to invoice total calculation": [
        "services/invoice/invoice.service.ts:calculateInvoiceTotal",
        "services/invoice/invoice.service.ts:generateInvoice",
        "workers/invoice_py/sync_invoice.py:sync_invoice",
    ],
    # temporal
    "recently changed payment files": [
        "services/payment/payment.controller.ts:processPayment",
        "services/payment/payment.controller.ts:refundPayment",
        "services/payment/payment.schema.ts:paymentSchema",
        "services/payment/payment.gateway.ts:chargeGateway",
    ],
    "last modified auth session code": [
        "services/auth/session.store.ts:loadSession",
        "services/auth/auth.service.ts:refreshToken",
        "services/auth/auth.service.ts:validateToken",
    ],
    "what changed this week in order service": [
        "services/order/order.service.ts:createOrder",
        "services/order/order.service.ts:cancelOrder",
        "services/order/order.mapper.ts:mapOrderRow",
        "workers/order_py/order_events.py:emit_order_event",
    ],
    "hàm nào bị sửa gần đây": [
        "services/cart/cart.service.ts:mergeCarts",
        "services/cache/cache.helpers.ts:cacheSet",
        "services/report/report.builder.ts:buildDailyReport",
    ],
}

QUERIES: List[str] = list(QUERY_AFFINITY.keys())


def _jitter(seed: str) -> float:
    """Jitter deterministic [0, 1) — md5, không phụ thuộc PYTHONHASHSEED."""
    digest = hashlib.md5(seed.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) / 0xFFFFFFFF


def _semantic_for(query: str, docs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Gán semantic score cho 1 query: affinity giảm dần từ 0.92, phần còn
    lại 0.35 + jitter (đúng pattern "vector neighbors cluster lại với nhau").
    """
    affinity = QUERY_AFFINITY[query]
    out: List[Dict[str, Any]] = []
    for doc in docs:
        d = dict(doc)
        if doc["node_id"] in affinity:
            rank = affinity.index(doc["node_id"])
            d["semantic"] = 0.92 - 0.03 * rank
        else:
            d["semantic"] = 0.35 + 0.12 * _jitter(f"{query}|{doc['node_id']}")
        out.append(d)
    return out


# ─────────────────────────────────────────────────────────────
# Engine stub + chạy A/B
# ─────────────────────────────────────────────────────────────


def _make_engine(auto_bm25: bool, docs: List[Dict[str, Any]], query: str) -> IntelligentRetrievalEngine:
    """Engine thật, stub retrieve trả candidate set với semantic theo query."""
    engine = IntelligentRetrievalEngine(auto_bm25=auto_bm25)
    scoped = _semantic_for(query, docs)
    engine._retrieve_qdrant = (
        lambda q, collection, top_k, project_id=None: [dict(d) for d in scoped]
    )
    engine._retrieve_keyword = lambda q, top_k, **kw: []
    return engine


def _run_mode(auto_bm25: bool, docs: List[Dict[str, Any]]) -> Tuple[Dict[str, List[Tuple[str, float]]], Dict[str, float]]:
    """Chạy cả query set 1 chế độ → {query: [(node_id, score)…]} + latency ms."""
    rankings: Dict[str, List[Tuple[str, float]]] = {}
    latencies: Dict[str, float] = {}
    for query in QUERIES:
        engine = _make_engine(auto_bm25, docs, query)
        t0 = time.perf_counter()
        results = engine.search(query, top_k=TOP_K)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        rankings[query] = [(r.node_id, r.score) for r in results]
        latencies[query] = elapsed_ms
    return rankings, latencies


def _short(node_id: str) -> str:
    # "services/payment/payment.controller.ts:processPayment" → "payment.controller.ts:processPayment"
    return node_id.rsplit("/", 1)[-1]


# ─────────────────────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────────────────────


def _diff_query(query: str, off: List[Tuple[str, float]], on: List[Tuple[str, float]], sem_by_id: Dict[str, float]) -> Tuple[str, int, int, float]:
    """Markdown table diff 1 query + (số swap, số swap gần nhau, Δsemantic max)."""
    off_ids = [nid for nid, _ in off]
    on_ids = [nid for nid, _ in on]
    swaps = 0
    close_swaps = 0
    max_delta = 0.0
    lines = [
        f"#### `{query}`",
        "",
        f"Intent: `{classify_query(query)}`",
        "",
        "| # | OFF (auto_bm25=0) | ON (auto_bm25=1) | Ghi chú |",
        "|---|---|---|---|",
    ]
    for i in range(max(len(off_ids), len(on_ids))):
        off_entry = off[i] if i < len(off) else None
        on_entry = on[i] if i < len(on) else None
        off_cell = (
            f"`{_short(off_entry[0])}` {off_entry[1]:.4f}" if off_entry else "—"
        )
        on_cell = f"`{_short(on_entry[0])}` {on_entry[1]:.4f}" if on_entry else "—"
        note = ""
        if off_entry and on_entry and off_entry[0] != on_entry[0]:
            moved = on_entry[0]
            old_rank = off_ids.index(moved) if moved in off_ids else -1
            if old_rank == -1:
                note = "ON thêm hit mới"
            else:
                swaps += 1
                # Δsemantic giữa candidate bị đổi chỗ tại rank này (2 chiều)
                delta = abs(
                    sem_by_id.get(off_entry[0], 0.0)
                    - sem_by_id.get(on_entry[0], 0.0)
                )
                max_delta = max(max_delta, delta)
                if delta < CLOSE_SEMANTIC_DELTA:
                    close_swaps += 1
                    note = f"đảo chỗ, Δsemantic {delta:.3f} (< 0.1)"
                else:
                    note = f"đảo chỗ, Δsemantic {delta:.3f} (>= 0.1)"
        lines.append(f"| {i + 1} | {off_cell} | {on_cell} | {note} |")
    return "\n".join(lines), swaps, close_swaps, max_delta


def main() -> None:
    docs = _build_documents()
    print(f"Candidate set: {len(docs)} nodes · Query set: {len(QUERIES)} "
          f"(4 structural / 4 semantic / 4 temporal) · top_k={TOP_K}")
    print()

    print("Đang chạy OFF (auto_bm25=False)…")
    off_rankings, off_latency = _run_mode(False, docs)
    print("Đang chạy ON  (auto_bm25=True)…")
    on_rankings, on_latency = _run_mode(True, docs)
    print()

    # Latency table
    print("## Latency", "\n")
    print("| Query | OFF (ms) | ON (ms) | Δ (ms) |")
    print("|---|---|---|---|")
    for query in QUERIES:
        print(f"| `{query}` "
              f"| {off_latency[query]:.2f} | {on_latency[query]:.2f} "
              f"| {on_latency[query] - off_latency[query]:+.2f} |")
    off_mean = statistics.mean(off_latency.values())
    on_mean = statistics.mean(on_latency.values())
    print(f"| **mean** | **{off_mean:.2f}** | **{on_mean:.2f}** "
          f"| **{on_mean - off_mean:+.2f}** |")
    print()

    # Ranking diff per query
    total_swaps = total_close = 0
    global_max_delta = 0.0
    print("## Ranking diff (top-%d)" % TOP_K, "\n")
    for query in QUERIES:
        sem_by_id = {
            d["node_id"]: d["semantic"] for d in _semantic_for(query, docs)
        }
        block, swaps, close_swaps, max_delta = _diff_query(
            query, off_rankings[query], on_rankings[query], sem_by_id
        )
        print(block)
        print()
        total_swaps += swaps
        total_close += close_swaps
        global_max_delta = max(global_max_delta, max_delta)

    print("## Tổng kết tiêu chí flip", "\n")
    print(f"- Tổng số vị trí bị đảo thứ tự (trong {len(QUERIES)} × top-{TOP_K}): "
          f"{total_swaps}")
    print(f"- Số đảo chỗ giữa candidate có Δsemantic < {CLOSE_SEMANTIC_DELTA}: "
          f"{total_close}/{total_swaps}")
    print(f"- Δsemantic lớn nhất trong các cặp bị đảo chỗ: {global_max_delta:.3f}")
    new_hits = sum(
        1 for q in QUERIES
        for nid, _ in on_rankings[q]
        if nid not in {n for n, _ in off_rankings[q]}
    )
    print(f"- Số hit mới xuất hiện trong top-{TOP_K} nhờ bm25 (auto corpus "
          f"build trên seeds nên chỉ là re-entry): {new_hits}")


if __name__ == "__main__":
    main()
