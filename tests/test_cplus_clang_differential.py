import sys
import json
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cplus import clang_parser  # noqa: E402
from tools.cplus.cplus_analyzer import parse_c_family_file  # noqa: E402
from tools.cplus.function_identity import (  # noqa: E402
    FUNCTION_IDENTITY_SCHEMA,
    build_function_identity,
)
from tools.cplus import semantic_shadow  # noqa: E402
from tools.cplus.semantic_shadow import (  # noqa: E402
    build_differential_artifact,
    build_file_differential_artifact,
)


FIXTURES = ROOT / "tests" / "fixtures" / "cplus_semantic_calls"
FILES = ("direct.c", "fp.c", "macro_static.c", "overload.cpp", "template.cpp", "virtual.cpp")


def _tree_sitter_functions(name: str):
    parsed = parse_c_family_file(
        str(FIXTURES / name), str(FIXTURES), name.endswith(".cpp")
    )
    return parsed[0], parsed[4]


def test_signature_v2_joins_clang_inventory_to_exact_tree_sitter_functions():
    assert clang_parser.is_available(), "pinned libclang runtime is required"

    for name in FILES:
        tree_functions, _relations = _tree_sitter_functions(name)
        clang_payload = clang_parser.parse_and_extract(
            str(FIXTURES / name), str(FIXTURES), ""
        )
        assert clang_payload is not None
        tree_ids = {function.symbol_id for function in tree_functions}
        clang_ids = {function["symbol_id"] for function in clang_payload["functions"]}
        assert tree_ids == clang_ids, name
        assert all(
            identity.startswith(f"{FUNCTION_IDENTITY_SCHEMA}::") for identity in tree_ids
        )


def test_same_arity_overloads_are_distinct_and_clang_cannot_mutate_structure():
    before_functions, before_relations = _tree_sitter_functions("overload.cpp")
    before = (
        tuple(sorted(function.symbol_id for function in before_functions)),
        tuple(
            sorted(
                (relation.source_label, relation.source_id, relation.rel_type,
                 relation.target_label, relation.target_id)
                for relation in before_relations
            )
        ),
    )
    clang_parser.parse_and_extract(
        str(FIXTURES / "overload.cpp"), str(FIXTURES), ""
    )
    after_functions, after_relations = _tree_sitter_functions("overload.cpp")
    after = (
        tuple(sorted(function.symbol_id for function in after_functions)),
        tuple(
            sorted(
                (relation.source_label, relation.source_id, relation.rel_type,
                 relation.target_label, relation.target_id)
                for relation in after_relations
            )
        ),
    )

    pick_ids = {function.symbol_id for function in before_functions if function.name == "pick"}
    assert len(pick_ids) == 2
    assert before == after


def test_internal_linkage_uses_file_discriminator_but_external_identity_does_not():
    internal_a = build_function_identity(
        qualified_name="helper", parameter_types=("int",), linkage="internal", rel_path="a.c"
    )
    internal_b = build_function_identity(
        qualified_name="helper", parameter_types=("int",), linkage="internal", rel_path="b.c"
    )
    external_a = build_function_identity(
        qualified_name="api", parameter_types=("int",), linkage="external", rel_path="a.h"
    )
    external_b = build_function_identity(
        qualified_name="api", parameter_types=("int",), linkage="external", rel_path="b.cpp"
    )

    assert internal_a.logical_id != internal_b.logical_id
    assert external_a.logical_id == external_b.logical_id


def test_four_stage_differential_artifact_is_deterministic_and_plane_typed():
    functions, relations = _tree_sitter_functions("overload.cpp")
    structural = {
        "functions": [asdict(function) for function in functions],
        "relations": [asdict(relation) for relation in relations],
    }
    clang_payload = clang_parser.parse_and_extract(
        str(FIXTURES / "overload.cpp"), str(FIXTURES), ""
    )
    assert clang_payload is not None
    first = build_differential_artifact(
        raw_tree_sitter=structural,
        raw_clang=clang_payload,
        validated_tree_sitter=structural,
        persisted_tree_sitter=structural,
    )
    second = build_differential_artifact(
        raw_tree_sitter=structural,
        raw_clang=clang_payload,
        validated_tree_sitter=structural,
        persisted_tree_sitter=structural,
    )

    assert first["tree_sitter_invariant"] is True
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["blocking_delta_count"] == 0
    assert {delta["classification"] for delta in first["deltas"]} <= {
        "expected_plane_difference"
    }


def test_differential_fails_closed_on_type_kind_and_duplicate_identity_drift():
    baseline = {
        "function_types": [
            {
                "symbol_id": "fn-type::int(int)",
                "type_signature": "int(int)",
                "file_path": "sample.cpp",
                "start_line": 1,
                "end_line": 1,
                "code": "int(int)",
            }
        ],
        "functions": [
            {
                "symbol_id": "cplus-function-v2::sample(int)",
                "qualified_name": "sample",
                "kind": "function",
                "signature": "sample(int)",
                "file_path": "sample.cpp",
                "start_line": 1,
                "end_line": 3,
            }
        ],
    }
    changed = json.loads(json.dumps(baseline))
    changed["function_types"] = []
    changed["functions"][0]["kind"] = "declaration"
    changed["functions"].append(dict(changed["functions"][0]))

    artifact = build_differential_artifact(
        raw_tree_sitter=baseline,
        raw_clang=baseline,
        validated_tree_sitter=changed,
        persisted_tree_sitter=changed,
    )

    assert artifact["tree_sitter_invariant"] is False
    assert artifact["passed"] is False
    classifications = {delta["classification"] for delta in artifact["deltas"]}
    assert "identity_collision" in classifications
    assert "validation_rejection" in classifications
    assert "unexpected_persistence" in classifications


def test_missing_raw_clang_function_is_adapter_loss():
    structural = {
        "functions": [
            {
                "symbol_id": "cplus-function-v2::lost()",
                "qualified_name": "lost",
                "kind": "function",
                "signature": "lost()",
                "file_path": "lost.cpp",
            }
        ]
    }
    artifact = build_differential_artifact(
        raw_tree_sitter=structural,
        raw_clang={},
        validated_tree_sitter=structural,
        persisted_tree_sitter=structural,
    )

    assert artifact["tree_sitter_invariant"] is True
    assert artifact["passed"] is False
    assert any(delta["classification"] == "adapter_loss" for delta in artifact["deltas"])


def test_relation_property_drift_is_not_erased():
    baseline = {
        "relations": [
            {
                "source_label": "Type",
                "source_id": "Base",
                "rel_type": "INHERITS",
                "target_label": "Type",
                "target_id": "Derived",
                "properties": {"visibility": "public", "virtual": False},
            }
        ]
    }
    changed = json.loads(json.dumps(baseline))
    changed["relations"][0]["properties"]["visibility"] = "private"
    artifact = build_differential_artifact(
        raw_tree_sitter=baseline,
        raw_clang=baseline,
        validated_tree_sitter=changed,
        persisted_tree_sitter=changed,
    )

    assert artifact["tree_sitter_invariant"] is False
    assert artifact["blocking_delta_count"] > 0


def test_source_code_drift_is_not_erased():
    baseline = {
        "file_def": {"file_path": "source.c", "code": "int entry(void) { return 0; }"},
        "functions": [
            {
                "symbol_id": "cplus-function-v2::entry()",
                "qualified_name": "entry",
                "kind": "function",
                "signature": "entry()",
                "file_path": "source.c",
                "code": "int entry(void) { return 0; }",
            }
        ],
    }
    changed = json.loads(json.dumps(baseline))
    changed["file_def"]["code"] = "int entry(void) { return 1; }"
    changed["functions"][0]["code"] = "int entry(void) { return 1; }"
    artifact = build_differential_artifact(
        raw_tree_sitter=baseline,
        raw_clang=baseline,
        validated_tree_sitter=changed,
        persisted_tree_sitter=changed,
    )

    assert artifact["tree_sitter_invariant"] is False
    assert artifact["passed"] is False


def test_weak_callsite_removal_and_mutation_are_not_erased():
    baseline = {
        "calls": [
            {
                "id": "provider-row-1",
                "logical_callsite_id": "cplus-callsite-v2::entry::12",
                "caller_id": "cplus-function-v2::entry()",
                "caller_file": "source.c",
                "call_start_byte": 12,
                "call_type": "call_expression",
                "call_arity": 1,
                "callee_name": "target",
                "callee_id": None,
            }
        ]
    }
    removed = {"calls": []}
    mutated = json.loads(json.dumps(baseline))
    mutated["calls"][0]["logical_callsite_id"] = "cplus-callsite-v2::entry::13"
    mutated["calls"][0]["resolution_class"] = "lexical_candidate"
    mutated["calls"][0]["semantic_provider"] = "tree_sitter"
    lexically_mutated = json.loads(json.dumps(baseline))
    lexically_mutated["calls"][0]["callee_name"] = "other_target"

    removed_artifact = build_differential_artifact(
        raw_tree_sitter=baseline,
        raw_clang=baseline,
        validated_tree_sitter=removed,
        persisted_tree_sitter=removed,
    )
    mutated_artifact = build_differential_artifact(
        raw_tree_sitter=baseline,
        raw_clang=baseline,
        validated_tree_sitter=mutated,
        persisted_tree_sitter=mutated,
    )
    lexical_artifact = build_differential_artifact(
        raw_tree_sitter=baseline,
        raw_clang=baseline,
        validated_tree_sitter=lexically_mutated,
        persisted_tree_sitter=lexically_mutated,
    )

    assert removed_artifact["tree_sitter_invariant"] is False
    assert removed_artifact["passed"] is False
    assert mutated_artifact["tree_sitter_invariant"] is False
    assert mutated_artifact["passed"] is False
    assert lexical_artifact["tree_sitter_invariant"] is False
    assert lexical_artifact["passed"] is False

    site_id_payload = json.loads(json.dumps(baseline))
    del site_id_payload["calls"][0]["logical_callsite_id"]
    site_id_payload["calls"][0]["site_id"] = "persisted-site-12"
    callsite_rows = [
        row
        for row in semantic_shadow.canonical_structural_projection(site_id_payload)
        if row["label"] == "Callsite"
    ]
    assert callsite_rows[0]["identity"] == "persisted-site-12"


def test_shadow_report_embeds_and_persists_differential(monkeypatch, tmp_path):
    monkeypatch.setattr(
        semantic_shadow,
        "run_semantic_worker",
        lambda **_kwargs: {"status": "ok", "callsites": []},
    )
    structural = {
        "functions": [
            {
                "symbol_id": "cplus-function-v2::entry()",
                "qualified_name": "entry",
                "kind": "function",
                "signature": "entry()",
                "file_path": "entry.cpp",
            }
        ]
    }
    output_path = tmp_path / "shadow.json"
    report = semantic_shadow.run_shadow_comparison(
        root=str(tmp_path),
        files=["entry.cpp"],
        output_path=str(output_path),
        worker_path="unused-worker.py",
        differential_inputs={
            "entry.cpp": {
                "raw_tree_sitter": structural,
                "raw_clang": structural,
                "validated_tree_sitter": structural,
                "persisted_tree_sitter": structural,
            }
        },
    )

    assert report["files"]["entry.cpp"]["differential"]["passed"] is True
    saved = json.loads(output_path.read_text(encoding="utf-8"))
    assert saved["files"]["entry.cpp"]["differential"] == report["files"]["entry.cpp"]["differential"]


def test_all_reviewed_full_payload_fixtures_pass_per_file_differential():
    files = FILES + ("pilot_header.hpp",)

    for name in files:
        artifact = build_file_differential_artifact(root=str(FIXTURES), rel_file=name)
        assert artifact["passed"] is True, (name, artifact["deltas"])
