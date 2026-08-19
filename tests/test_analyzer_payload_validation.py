from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.common.payload_validation import (  # noqa: E402
    QuarantineReason,
    accounting_for_payload,
    identity_merge_fingerprint,
    validate_cplus_payload,
)


def _function(identity: str, name: str, **changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "symbol_id": identity,
        "name": name,
        "qualified_name": name,
        "kind": "function",
        "scope_name": None,
        "file_path": "src/main.cpp",
        "start_byte": 0,
        "end_byte": 12,
        "start_line": 1,
        "end_line": 1,
        "arity": 0,
        "code": f"void {name}() {{}}",
        "comment": "",
        "summary": "",
        "note": "",
    }
    row.update(changes)
    return row


def test_malformed_symbol_and_dependent_effects_are_quarantined_before_writes():
    good = _function("fn:good", "演算子operator+")
    bad = _function("fn:bad\n#define X", "#define X")
    payload = {
        "file_def": {"file_path": "src/main.cpp"},
        "functions": [good, bad],
        "relations": [
            {
                "source_label": "File",
                "target_label": "Function",
                "rel_type": "CONTAINS",
                "source_id": "src/main.cpp",
                "target_id": "fn:good",
            },
            {
                "source_label": "File",
                "target_label": "Function",
                "rel_type": "CONTAINS",
                "source_id": "src/main.cpp",
                "target_id": "fn:bad\n#define X",
            },
        ],
        "calls": [
            {"caller_id": "fn:good", "callee_name": "ok"},
            {"caller_id": "fn:bad\n#define X", "callee_name": "bad"},
        ],
    }

    validated, quarantine = validate_cplus_payload(payload, project_id="demo")

    assert [row["symbol_id"] for row in validated["functions"]] == ["fn:good"]
    assert len(validated["relations"]) == 1
    assert len(validated["calls"]) == 1
    assert {record.reason for record in quarantine} >= {
        QuarantineReason.MALFORMED_DECLARATOR,
        QuarantineReason.UNRESOLVED_REFERENCE,
    }
    accounting = accounting_for_payload(validated, quarantine)
    assert accounting.discovered == accounting.accepted + accounting.quarantined


def test_whitespace_padded_symbol_identity_is_quarantined_before_indexed_writes():
    padded = _function("  false-capture/1@src/main.cpp", "  false-capture  ")
    payload = {
        "file_def": {"file_path": "src/main.cpp"},
        "functions": [padded],
        "relations": [
            {
                "source_label": "File",
                "target_label": "Function",
                "rel_type": "CONTAINS",
                "source_id": "src/main.cpp",
                "target_id": padded["symbol_id"],
            }
        ],
        "calls": [{"caller_id": padded["symbol_id"], "callee_name": "target"}],
    }

    validated, quarantine = validate_cplus_payload(payload, project_id="demo")

    assert validated["functions"] == []
    assert validated["relations"] == []
    assert validated["calls"] == []
    assert {record.reason for record in quarantine} >= {
        QuarantineReason.MALFORMED_DECLARATOR,
        QuarantineReason.UNRESOLVED_REFERENCE,
    }


def test_quarantined_file_quality_suppresses_nodes_relations_calls_and_vectors():
    payload = {
        "file_def": {
            "file_path": "src/recovered.pc",
            "parse_quality": {"tier": "quarantined"},
        },
        "evidence_policy": {"strong_relations_allowed": False},
        "functions": [_function("fn:unsafe", "unsafe", file_path="src/recovered.pc")],
        "relations": [
            {
                "source_label": "File",
                "target_label": "Function",
                "source_id": "src/recovered.pc",
                "target_id": "fn:unsafe",
                "rel_type": "CONTAINS",
            }
        ],
        "calls": [{"caller_id": "fn:unsafe", "callee_name": "target"}],
    }

    validated, quarantine = validate_cplus_payload(payload, project_id="demo")

    assert validated["functions"] == []
    assert validated["relations"] == []
    assert validated["calls"] == []
    assert all(
        record.reason is QuarantineReason.QUARANTINED_FILE_QUALITY
        for record in quarantine
    )


def test_conflicting_duplicate_identity_quarantines_all_candidates_deterministically():
    payload = {
        "file_def": {"file_path": "src/main.cpp"},
        "functions": [
            _function("fn:same", "first"),
            _function("fn:same", "second"),
        ],
    }

    validated, quarantine = validate_cplus_payload(payload, project_id="demo")

    assert validated["functions"] == []
    assert len(quarantine) == 2
    assert {record.reason for record in quarantine} == {
        QuarantineReason.CONFLICTING_DUPLICATE
    }


def test_identical_duplicate_is_deterministically_normalized_and_accounted_as_rejected():
    duplicate = _function("fn:same", "same")
    validated, quarantine = validate_cplus_payload(
        {
            "file_def": {"file_path": "src/main.cpp"},
            "functions": [duplicate, dict(duplicate)],
        },
        project_id="demo",
    )

    accounting = accounting_for_payload(validated, quarantine)
    assert len(validated["functions"]) == 1
    assert accounting.accepted == 1
    assert accounting.rejected == 1
    assert accounting.discovered == 2


def test_function_definition_wins_over_matching_declaration():
    declaration = _function(
        "fn:shared",
        "shared",
        kind="declaration",
        start_line=4,
        end_line=4,
        code="void shared();",
    )
    definition = _function(
        "fn:shared",
        "shared",
        kind="function",
        start_line=40,
        end_line=44,
        code="void shared() { work(); }",
    )

    validated, quarantine = validate_cplus_payload(
        {
            "file_def": {"file_path": "src/main.cpp"},
            "functions": [declaration, definition],
        },
        project_id="demo",
    )

    assert validated["functions"] == [definition]
    assert quarantine == ()
    accounting = accounting_for_payload(validated, quarantine)
    assert accounting.accepted == 1
    assert accounting.rejected == 1
    assert accounting.discovered == 2


def test_function_declaration_and_definition_share_scan_wide_fingerprint():
    declaration = _function("fn:shared", "shared", kind="declaration")
    definition = _function("fn:shared", "shared", kind="function")

    assert identity_merge_fingerprint(
        "Function", declaration
    ) == identity_merge_fingerprint("Function", definition)


def test_unsafe_source_path_quarantines_the_entire_payload():
    validated, quarantine = validate_cplus_payload(
        {
            "file_def": {"file_path": "../escape.cpp"},
            "functions": [_function("fn:unsafe", "unsafe")],
        },
        project_id="demo",
    )

    assert validated["_quarantine_entire_payload"] is True
    assert validated["functions"] == []
    assert {record.reason for record in quarantine} >= {
        QuarantineReason.INVALID_PATH,
        QuarantineReason.MISSING_OWNER,
    }


def test_unsafe_source_path_handles_non_mapping_records_without_crashing():
    validated, quarantine = validate_cplus_payload(
        {"file_def": {"file_path": "../escape.cpp"}, "functions": [None]},
        project_id="demo",
    )

    assert validated["functions"] == []
    assert {record.reason for record in quarantine} >= {
        QuarantineReason.INVALID_PATH,
        QuarantineReason.INVALID_RECORD,
    }


def test_missing_downstream_required_function_field_is_quarantined():
    malformed = _function("fn:missing-kind", "broken")
    malformed.pop("kind")

    validated, quarantine = validate_cplus_payload(
        {"file_def": {"file_path": "src/main.cpp"}, "functions": [malformed]},
        project_id="demo",
    )

    assert validated["functions"] == []
    assert [record.reason for record in quarantine] == [QuarantineReason.INVALID_RECORD]


def test_invalid_downstream_required_function_types_are_quarantined():
    malformed = _function(
        "fn:invalid-types",
        "broken",
        arity="2",
        qualified_name=None,
        code=None,
    )

    validated, quarantine = validate_cplus_payload(
        {"file_def": {"file_path": "src/main.cpp"}, "functions": [malformed]},
        project_id="demo",
    )

    assert validated["functions"] == []
    assert [record.reason for record in quarantine] == [QuarantineReason.INVALID_RECORD]


def test_declaration_merge_fingerprint_ignores_path_span_code_and_provenance():
    header = _function("type:shared", "Shared", kind="class", file_path="include/shared.h")
    source = _function(
        "type:shared",
        "Shared",
        kind="class",
        file_path="src/shared.cpp",
        start_line=50,
        end_line=70,
        code="class Shared { int value; };",
        quality_provenance={"tier": "recovered"},
    )

    assert identity_merge_fingerprint("Type", header) == identity_merge_fingerprint(
        "Type", source
    )


def test_scan_wide_conflicting_identity_is_blocked_in_every_payload():
    payload = {
        "file_def": {"file_path": "src/main.cpp"},
        "functions": [_function("fn:shared", "one")],
    }

    validated, quarantine = validate_cplus_payload(
        payload,
        project_id="demo",
        known_identities={("Project", "demo"), ("File", "src/main.cpp")},
        blocked_identities={("Function", "fn:shared")},
    )

    assert validated["functions"] == []
    assert [record.reason for record in quarantine] == [
        QuarantineReason.CONFLICTING_DUPLICATE
    ]


def test_scan_wide_registry_allows_cross_file_edges_and_rejects_unknown_endpoints():
    payload = {
        "file_def": {"file_path": "src/derived.cpp"},
        "types": [
            _function(
                "type:derived",
                "Derived",
                file_path="src/derived.cpp",
            )
        ],
        "relations": [
            {
                "source_label": "Type",
                "target_label": "Type",
                "source_id": "type:derived",
                "target_id": "type:base",
                "rel_type": "INHERITS",
            },
            {
                "source_label": "Type",
                "target_label": "Type",
                "source_id": "type:derived",
                "target_id": "type:missing",
                "rel_type": "INHERITS",
            },
        ],
    }
    known = {
        ("Project", "demo"),
        ("File", "src/derived.cpp"),
        ("Type", "type:derived"),
        ("Type", "type:base"),
    }

    validated, quarantine = validate_cplus_payload(
        payload,
        project_id="demo",
        known_identities=known,
    )

    assert [relation["target_id"] for relation in validated["relations"]] == [
        "type:base"
    ]
    assert len(quarantine) == 1
    assert quarantine[0].reason is QuarantineReason.UNRESOLVED_REFERENCE
