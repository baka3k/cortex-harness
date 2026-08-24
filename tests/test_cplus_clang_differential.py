import sys
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
