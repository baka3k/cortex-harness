from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from cortex_harness.mcp_contract import result_envelope_json
from testtool.mcp_batch_report import (
    BatchRun,
    ServerConfig,
    SuiteConfig,
    ToolCase,
    classify_response,
    render_markdown,
    validate_contract,
)


def _response(*, ok: bool, details=None, data=None):
    error = None
    if not ok:
        error = {
            "code": "capability_unavailable",
            "message": "NAVIGATE unavailable",
            "retryable": False,
            "details": details or {"missing_relationships": ["NAVIGATE"]},
        }
    if data is None and ok:
        data = {"results": [{"id": "n1"}]}
    envelope_text = result_envelope_json(
        "find_screen_workflows",
        ok=ok,
        data=data,
        error=error,
        message="Success: 1 result." if ok else error["message"],
    )
    return {
        "jsonrpc": "2.0",
        "id": 3,
        "result": {
            "_meta": {
                "contract": "cortex.mcp.tool-result",
                "contractVersion": "2.0",
                "tool": "find_screen_workflows",
            },
            "content": [{"type": "text", "text": envelope_text}],
            "isError": not ok,
        },
    }


def test_contract_validator_accepts_standard_response_and_rejects_internal_catalogs():
    assert validate_contract(_response(ok=True), "find_screen_workflows") == []

    errors = validate_contract(
        _response(
            ok=False,
            details={
                "missing_relationships": ["NAVIGATE"],
                "available_relationships": ["CALLS", "NAVIGATE"],
            },
        ),
        "find_screen_workflows",
    )

    assert any("available_relationships" in item for item in errors)


def test_contract_validator_rejects_v1_structured_content_channel():
    response = _response(ok=True)
    response["result"]["structuredContent"] = {"ok": True, "data": {}, "error": None}
    response["result"]["content"].append({"type": "text", "text": "extra"})

    errors = validate_contract(response, "find_screen_workflows")

    assert any("structuredContent" in item for item in errors)
    assert any("exactly one text block" in item for item in errors)


def test_response_classifier_distinguishes_data_empty_and_tool_error():
    assert classify_response(_response(ok=True)) == ("SUCCESS_DATA", None)

    assert classify_response(
        _response(ok=True, data={"results": []})
    ) == ("SUCCESS_EMPTY", None)
    assert classify_response(_response(ok=False)) == (
        "TOOL_ERROR",
        "capability_unavailable",
    )


def test_suite_rejects_duplicate_tool_cases_per_server():
    case = ToolCase(tool="list_parsers", arguments={})
    with pytest.raises(ValueError, match="duplicate tool case"):
        SuiteConfig(
            name="duplicate",
            project="demo",
            parser="proc",
            servers=(
                ServerConfig(
                    name="graph_mcp",
                    endpoint="http://127.0.0.1:8788/mcp",
                    cases=(case, case),
                ),
            ),
        )


def test_markdown_report_contains_schema_input_raw_output_and_contract_result():
    case = ToolCase(tool="find_screen_workflows", arguments={"project_id": "demo"})
    suite = SuiteConfig(
        name="demo-suite",
        project="demo",
        parser="proc",
        servers=(
            ServerConfig(
                name="graph_mcp",
                endpoint="http://127.0.0.1:8788/mcp",
                cases=(case,),
            ),
        ),
    )
    run = BatchRun(
        server="graph_mcp",
        endpoint="http://127.0.0.1:8788/mcp",
        case=case,
        description="Find workflows.",
        input_schema={"type": "object"},
        output_schema=None,
        response=_response(ok=False),
        status="TOOL_ERROR",
        error_code="capability_unavailable",
        duration_ms=3.2,
        contract_errors=(),
        expectation_errors=(),
    )

    report = render_markdown(
        suite,
        [run],
        inventories={"graph_mcp": ["find_screen_workflows"]},
        inventory_errors=(),
        started_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
        finished_at=datetime(2026, 8, 28, 0, 0, 1, tzinfo=timezone.utc),
    )

    assert "`graph_mcp.find_screen_workflows`" in report
    assert "### Input executed" in report
    assert '"project_id": "demo"' in report
    assert "### Raw parsed JSON-RPC output" in report
    assert "capability_unavailable" in report
    assert "Contract: **PASS**" in report
