"""MCP retrieval failures must remain distinguishable from zero-hit success."""

from __future__ import annotations

import asyncio
import json
import threading
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "code-tiny", ROOT / "code-tiny" / "mcp"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from cortex_harness.storage import GatewayErrorCode, StoreGatewayError  # noqa: E402
from services import explore_service as explore_module  # noqa: E402
from services.explore_service import ExploreService  # noqa: E402


@pytest.mark.asyncio
async def test_explore_retrieval_uses_named_lane() -> None:
    understanding = SimpleNamespace(embedding_text="query", raw_query="query")
    service = ExploreService()

    with patch(
        "tools.common.intelligent_retrieval.IntelligentRetrievalEngine.search",
        return_value=[],
    ) as search:
        await service._run_retrieval(
            understanding,
            None,
            None,
            "graph",
            "collection",
            10,
            "semantic",
            False,
            None,
            None,
            None,
            "demo",
        )

    assert search.call_count == 1
    # The mock records the caller thread before returning.
    assert any(thread.name.startswith("cortex-retrieval") for thread in threading.enumerate())
    service.close(wait=True)


@pytest.mark.asyncio
async def test_explore_rejects_excessive_top_k_before_store_work() -> None:
    service = ExploreService()

    with pytest.raises(StoreGatewayError) as raised:
        await service.explore("find callers", top_k=101)

    assert raised.value.code is GatewayErrorCode.REQUEST_TOO_LARGE
    assert raised.value.details["accepted_limit"] == 100
    service.close(wait=False)


@pytest.mark.asyncio
async def test_embedder_initialization_is_single_flight_on_named_lane() -> None:
    service = ExploreService(model_name="fixture-model")
    calls: list[str] = []

    def build(model_name: str):
        calls.append(threading.current_thread().name)
        return lambda _text: [float(len(model_name))]

    with patch.object(explore_module, "_make_embedder", side_effect=build):
        first, second = await asyncio.gather(
            service._get_embedder(), service._get_embedder()
        )

    assert first is second
    assert len(calls) == 1
    assert calls[0].startswith("cortex-retrieval")
    service.close(wait=True)


@pytest.mark.asyncio
async def test_retrieval_drain_rejects_new_work() -> None:
    service = ExploreService()
    service.begin_drain()

    with pytest.raises(StoreGatewayError) as raised:
        await service._run_retrieval(
            SimpleNamespace(embedding_text="query", raw_query="query"),
            None,
            None,
            "graph",
            "collection",
            10,
            "semantic",
            False,
            None,
            None,
            None,
            "demo",
        )

    assert raised.value.code is GatewayErrorCode.STORE_MAINTENANCE
    service.close(wait=False)


@pytest.mark.asyncio
async def test_explore_retrieval_does_not_turn_storage_failure_into_empty_success() -> None:
    understanding = SimpleNamespace(embedding_text="query", raw_query="query")
    service = ExploreService()

    with patch(
        "tools.common.intelligent_retrieval.IntelligentRetrievalEngine.search",
        side_effect=RuntimeError("vector store unavailable"),
    ):
        with pytest.raises(RuntimeError, match="vector store unavailable"):
            await service._run_retrieval(
                understanding,
                None,
                None,
                "graph",
                "collection",
                10,
                "semantic",
                False,
                None,
                None,
                None,
                "demo",
            )
    service.close(wait=True)


def test_unified_mcp_preserves_structured_gateway_error() -> None:
    from unified_mcp import _build_tool_error

    error = StoreGatewayError(
        GatewayErrorCode.OVERLOADED,
        "graph-read admission queue is full",
        retryable=True,
        retry_after_ms=100,
        details={"capacity": 32, "correlation_id": "request-1"},
    )

    payload = _build_tool_error("search_functions", {"query": "find"}, error)

    assert payload["ok"] is False
    assert payload["error"]["type"] == "OVERLOADED"
    assert payload["error"]["retryable"] is True
    assert payload["error"]["retry_after_ms"] == 100
    assert payload["error"]["capacity"] == 32
    assert payload["error"]["correlation_id"] == "request-1"


@pytest.mark.asyncio
async def test_unified_health_keeps_liveness_separate_from_readiness() -> None:
    from unified_mcp import health_check, readiness_check

    storage = {
        "mode": "generation_gateway",
        "feature_requested": True,
        "liveness": True,
        "readiness": False,
        "state": "owner_missing",
        "gateway_count": 0,
        "gateways": [],
    }
    with patch("unified_mcp.storage_runtime_status", return_value=storage):
        live_response = await health_check(None)
        ready_response = await readiness_check(None)

    live_payload = json.loads(live_response.body)
    ready_payload = json.loads(ready_response.body)
    assert live_response.status_code == 200
    assert live_payload["status"] == "healthy"
    assert live_payload["readiness"] is False
    assert ready_response.status_code == 503
    assert ready_payload["status"] == "not_ready"
