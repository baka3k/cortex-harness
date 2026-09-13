#!/usr/bin/env python3
"""Sinh golden fixtures phase 03 (signal_normalizer + intent + query_understanding).

Import TRỰC TIẾP implementation Python tham chiếu. Chạy từ repo root:

    uv run --no-project python scripts/rust_parity/gen_phase03_fixtures.py

(freshness ISO branch được monkeypatch `datetime.datetime.now` về mốc cố định
để fixture deterministic; `now_epoch` được emit cho Rust dùng cùng mốc.)
Output: 3 file JSON trong rust/crates/cortex-retrieval/tests/fixtures/.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code-tiny"))

from tools.common import signal_normalizer as sn  # noqa: E402
from tools.common.query_intent_classifier import (  # noqa: E402
    classify_query,
    classify_query_explain,
    get_weight_profile,
)
from tools.common.query_understanding import QueryUnderstanding  # noqa: E402

FIXTURES = REPO / "rust" / "crates" / "cortex-retrieval" / "tests" / "fixtures"


def write(name: str, payload: dict) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    path = FIXTURES / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(REPO)}")


def gen_signal() -> None:
    payload = {
        "generator": "scripts/rust_parity/gen_phase03_fixtures.py",
        "clamp": [
            # NaN case không đưa vào fixture (JSON không biểu diễn được) —
            # unit test Rust che path NaN → 0.0.
            {"value": 1.5, "lo": 0.0, "hi": 1.0, "expected": sn.clamp(1.5, 0.0, 1.0)},
            {"value": -0.5, "lo": 0.0, "hi": 1.0, "expected": sn.clamp(-0.5, 0.0, 1.0)},
            {"value": 0.42, "lo": 0.0, "hi": 1.0, "expected": sn.clamp(0.42, 0.0, 1.0)},
            {"value": 7.0, "lo": 5.0, "hi": 5.0, "expected": sn.clamp(7.0, 5.0, 5.0)},
        ],
        "min_max": [
            {"values": [], "expected": sn.min_max_normalize([])},
            {"values": [7.0], "expected": sn.min_max_normalize([7.0])},
            {"values": [3.0, 3.0, 3.0], "expected": sn.min_max_normalize([3.0, 3.0, 3.0])},
            {"values": [0.1, 0.5, 0.9, 1.2], "expected": sn.min_max_normalize([0.1, 0.5, 0.9, 1.2])},
            {"values": [-4.0, 0.0, 4.0], "expected": sn.min_max_normalize([-4.0, 0.0, 4.0])},
        ],
        "batch": [
            {"values": [None, None, 0.0, 5.0, 12.5], "lo": None, "hi": None,
             "expected": [c["s"] for c in sn.batch_normalize_signal(
                 [{}, {"s": None}, {"s": 0.0}, {"s": 5.0}, {"s": 12.5}], "s")]},
            {"values": [1.0, 7.5, 30.0], "lo": 0.0, "hi": 25.0,
             "expected": [c["k"] for c in sn.batch_normalize_signal(
                 [{"k": 1.0}, {"k": 7.5}, {"k": 30.0}], "k", lo=0.0, hi=25.0)]},
            {"values": [10.0, 20.0, 30.0], "lo": 20.0, "hi": 20.0,
             "expected": [c["k"] for c in sn.batch_normalize_signal(
                 [{"k": 10.0}, {"k": 20.0}, {"k": 30.0}], "k", lo=20.0, hi=20.0)]},
            {"values": [2.0, 4.0], "lo": 0.0, "hi": 10.0,
             "expected": [c["n"] for c in sn.batch_normalize_signal(
                 [{"s": 2.0}, {"s": 4.0}], "s", out_key="n", lo=0.0, hi=10.0)]},
        ],
        "normalize_signals": [
            {"raw": {"semantic": 0.83, "keyword": 14.2, "graph": 3, "freshness": 0.6, "confidence": 0.71, "usage": 0.42},
             "bounds": {"keyword": [0.0, 20.0], "graph": [0.0, 10.0]},
             "expected": sn.normalize_signals(
                 {"semantic": 0.83, "keyword": 14.2, "graph": 3, "freshness": 0.6,
                  "confidence": 0.71, "usage": 0.42},
                 signal_bounds={"keyword": (0.0, 20.0), "graph": (0.0, 10.0)})},
            {"raw": {"semantic": 1.7, "keyword": -3.0, "mystery": 9.9},
             "bounds": None,
             "expected": sn.normalize_signals({"semantic": 1.7, "keyword": -3.0, "mystery": 9.9})},
            {"raw": {"graph": 0.5}, "bounds": {"graph": [0.5, 0.5]},
             "expected": sn.normalize_signals({"graph": 0.5}, signal_bounds={"graph": (0.5, 0.5)})},
        ],
        "freshness_elapsed": [
            {"elapsed": 0.0, "half_life_days": 30.0, "expected": sn.freshness_from_elapsed(0.0, 30.0)},
            {"elapsed": -100.0, "half_life_days": 30.0, "expected": sn.freshness_from_elapsed(-100.0, 30.0)},
            {"elapsed": 30 * 86400, "half_life_days": 30.0, "expected": sn.freshness_from_elapsed(30 * 86400, 30.0)},
            {"elapsed": 86400, "half_life_days": 30.0, "expected": sn.freshness_from_elapsed(86400, 30.0)},
            {"elapsed": 365 * 86400, "half_life_days": 7.0, "expected": sn.freshness_from_elapsed(365 * 86400, 7.0)},
        ],
    }

    # freshness_from_dirty: monkeypatch datetime.datetime.now về mốc cố định.
    import datetime as dt

    fixed = dt.datetime(2026, 9, 13, 12, 0, 0, tzinfo=dt.timezone.utc)
    fixed_epoch = fixed.timestamp()

    class _FixedDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz else fixed.replace(tzinfo=None)

    original = dt.datetime
    dt.datetime = _FixedDatetime
    try:
        payload["freshness_dirty"] = {
            "now_epoch": fixed_epoch,
            "cases": [
                {"is_dirty": True, "iso": "", "expected": sn.freshness_from_dirty(True, "")},
                {"is_dirty": False, "iso": "", "expected": sn.freshness_from_dirty(False, "")},
                {"is_dirty": False, "iso": "bogus", "expected": sn.freshness_from_dirty(False, "bogus")},
                {"is_dirty": False, "iso": "2026-09-13T11:00:00+00:00",
                 "expected": sn.freshness_from_dirty(False, "2026-09-13T11:00:00+00:00")},
                {"is_dirty": False, "iso": "2026-08-14T12:00:00+00:00",
                 "expected": sn.freshness_from_dirty(False, "2026-08-14T12:00:00+00:00")},
                {"is_dirty": False, "iso": "2026-09-13T18:59:59+07:00",
                 "expected": sn.freshness_from_dirty(False, "2026-09-13T18:59:59+07:00")},
            ],
        }
    finally:
        dt.datetime = original

    write("signal_golden.json", payload)


CLASSIFY_QUERIES = [
    "who calls the validateToken function",
    "show me the callers of processPayment",
    "find code similar to payment flow",
    "recently changed files",
    "unlikely helper",
    "explain the auth flow",
    "dirty nodes",
    "who uses UserService",
    "Callers of X",
    "caller of validate",
    "where is validateToken used",
    "this week changes",
    "how does checkout work",
    "path from A to B",
    "latest changes in repo",
    "",
    "   ",
    "random gibberish query",
    "imports of module",
    "dependencies of PaymentService",
    "uncommitted work in checkout",
]


def gen_intent() -> None:
    payload = {
        "generator": "scripts/rust_parity/gen_phase03_fixtures.py",
        "classify": [{"query": q, "expected": classify_query(q)} for q in CLASSIFY_QUERIES],
        "explain": [
            {"query": q, "expected": classify_query_explain(q)} for q in CLASSIFY_QUERIES
        ],
        "weights": [
            {"intent": intent, "expected": get_weight_profile(intent)}
            for intent in ["semantic", "structural", "temporal", "default", "unknown-intent"]
        ],
    }
    write("intent_golden.json", payload)


TEXT_CASES = [
    "function xử lý thanh toán bị lỗi khi user chưa login",
    "who calls UserService.validate_token in payment module",
    "Truy vấn đơn hàng bị lỗi khi thanh toán",
    "explain the authentication flow with JWT token refresh",
    "\"validateToken\" calls UserService and handle_payment",
    "recent changes in checkout component",
    "",
    "   ",
    "get user by id from database repository",
    "Hệ thống gửi thông báo email khi đơn hàng hoàn tất",
    "tạo mới hóa đơn và cập nhật trạng thái",
    "xử lý đơn hàng: validate_order kiểm tra PaymentController mỗi khi order có lỗi 500",
]

PARAGRAPH_CASES = [
    {
        "text": "who calls validateToken\nsecond line about payment gateway\nthird line",
        "max_chars": 2000,
    },
    {
        "text": "Tìm các component UI liên quan đến thanh toán.\nDòng thứ hai mô tả thêm về session và token.",
        "max_chars": 2000,
    },
    {
        "text": "explain how the payment refund works with the order database and user notification email flow in detail",
        "max_chars": 60,
    },
    {"text": "", "max_chars": 100},
    {"text": "   \n  ", "max_chars": 100},
]


def gen_qu() -> None:
    payload = {
        "generator": "scripts/rust_parity/gen_phase03_fixtures.py",
        "from_text": [
            {"text": t, "expected": QueryUnderstanding.from_text(t).to_dict()} for t in TEXT_CASES
        ],
        "from_paragraph": [
            {
                "text": c["text"],
                "max_chars": c["max_chars"],
                "expected": QueryUnderstanding.from_paragraph(c["text"], c["max_chars"]).to_dict(),
            }
            for c in PARAGRAPH_CASES
        ],
    }
    write("qu_golden.json", payload)


if __name__ == "__main__":
    gen_signal()
    gen_intent()
    gen_qu()
