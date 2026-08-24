"""Dual-plane component contract while admitted runtime wiring remains gated."""

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.common.call_evidence import is_strong_call_evidence  # noqa: E402
from tools.cplus.cplus_analyzer import parse_c_family_file  # noqa: E402
from tools.cplus.semantic_worker import extract_semantic_callsite_evidence  # noqa: E402


FIXTURES = ROOT / "tests" / "fixtures" / "cplus_semantic_calls"


def _projection(name: str):
    parsed = parse_c_family_file(
        str(FIXTURES / name), str(FIXTURES), name.endswith(".cpp")
    )
    functions, relations = parsed[0], parsed[4]
    return {
        "functions": tuple(
            sorted(
                (
                    function.symbol_id,
                    function.signature,
                    function.start_byte,
                    function.end_byte,
                )
                for function in functions
            )
        ),
        "relations": tuple(
            sorted(
                (
                    relation.source_label,
                    relation.source_id,
                    relation.rel_type,
                    relation.target_label,
                    relation.target_id,
                )
                for relation in relations
            )
        ),
    }


def test_build_free_faithful_tu_adds_observations_without_structural_drift():
    before = _projection("direct.c")
    extraction = extract_semantic_callsite_evidence(
        str(FIXTURES / "direct.c"),
        str(FIXTURES),
        ["-std=c11"],
        config_fingerprint="cfg-faithful",
    )
    after = _projection("direct.c")

    assert extraction.status == "ok"
    assert extraction.callsites
    assert before == after
    assert all(site["caller_symbol_id"].startswith("cplus-function-v2::") for site in extraction.callsites)


def test_worker_observation_requires_parent_context_attestation_to_be_strong():
    extraction = extract_semantic_callsite_evidence(
        str(FIXTURES / "direct.c"),
        str(FIXTURES),
        ["-std=c11"],
        config_fingerprint="cfg-faithful",
    )
    direct = next(site for site in extraction.callsites if site["resolution_class"] == "direct_resolved")
    direct.update(
        {
            "project_id": "p1",
            "generation_id": "g1",
            "context_fidelity": "faithful",
            "context_admission": "accepted",
            "execution_coverage": "complete",
            "context_attestation": "",
            "manifest_key": "p1:g1:r1:policy:direct.c:cfg-faithful",
        }
    )

    assert not is_strong_call_evidence(direct)
    direct["context_attestation"] = "parent-verified-attestation"
    assert is_strong_call_evidence(direct)
