from __future__ import annotations

from cortex_harness.mcp_contract import (
    MCP_CONTRACT_NAME,
    MCP_CONTRACT_VERSION,
    normalize_error,
    normalize_success,
    result_meta,
    result_summary,
)


def test_success_envelope_preserves_business_payload_without_flattening():
    payload = {
        "db": "procsample",
        "results": [{"id": "function-1"}],
        "ids": ["function-1"],
    }

    result = normalize_success(payload)

    assert result == {"ok": True, "data": payload, "error": None}
    assert result["data"] is payload


def test_error_envelope_converts_legacy_type_to_stable_code_and_details():
    legacy = {
        "ok": False,
        "query_engine": "graph_generic",
        "error": {
            "type": "capability_unavailable",
            "message": "NAVIGATE is unavailable",
            "retryable": False,
            "missing_relationships": ["NAVIGATE"],
        },
    }

    result = normalize_error(legacy)

    assert result["ok"] is False
    assert result["data"] is None
    assert result["error"] == {
        "code": "capability_unavailable",
        "message": "NAVIGATE is unavailable",
        "retryable": False,
        "details": {
            "missing_relationships": ["NAVIGATE"],
            "context": {"query_engine": "graph_generic"},
        },
    }


def test_result_metadata_uses_mcp_meta_instead_of_polluting_business_data():
    assert result_meta("search_functions") == {
        "contract": MCP_CONTRACT_NAME,
        "contractVersion": MCP_CONTRACT_VERSION,
        "tool": "search_functions",
    }


def test_result_summary_is_concise_and_never_embeds_raw_payload():
    summary = result_summary(
        {"results": [{"note": "Code:\n" + "x" * 10_000}]},
        ok=True,
    )

    assert summary == "Success: 1 result. Read structuredContent.data."
    assert len(summary) < 100


def test_known_exceptions_map_to_stable_api_error_codes():
    project_error = type("ProjectNotRegisteredError", (Exception,), {})

    assert normalize_error(ValueError("bad limit"))["error"]["code"] == (
        "invalid_parameters"
    )
    assert normalize_error(LookupError("collection missing"))["error"]["code"] == (
        "collection_unavailable"
    )
    assert normalize_error(project_error("missing project"))["error"]["code"] == (
        "project_not_registered"
    )


def test_legacy_error_code_aliases_are_canonicalized_at_the_wire_boundary():
    assert normalize_error(
        {
            "ok": False,
            "error": {
                "type": "unsupported_capability",
                "message": "NAVIGATE unavailable",
            },
        }
    )["error"]["code"] == "capability_unavailable"
    assert normalize_error(
        {"ok": False, "error": {"code": "OVERLOADED", "message": "busy"}}
    )["error"]["code"] == "overloaded"
