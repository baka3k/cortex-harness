import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cplus import clang_parser  # noqa: E402


FIXTURES = ROOT / "tests" / "fixtures" / "cplus_semantic_calls"


def test_extent_index_is_parse_local_immutable_and_nested_safe():
    mutable = [(0, 100, "outer")]
    first_index = clang_parser.FunctionExtentIndex.build(mutable)
    mutable.append((20, 40, "inner"))

    assert clang_parser._find_enclosing_func(30, first_index) == "outer"
    second_index = clang_parser.FunctionExtentIndex.build(mutable)
    assert clang_parser._find_enclosing_func(30, second_index) == "inner"
    assert not hasattr(clang_parser._find_enclosing_func, "_cache")


def test_real_libclang_retains_calls_from_later_functions():
    assert clang_parser.is_available(), "pinned libclang runtime is required"

    fp = clang_parser.parse_and_extract(str(FIXTURES / "fp.c"), str(FIXTURES), "")
    macro = clang_parser.parse_and_extract(
        str(FIXTURES / "macro_static.c"), str(FIXTURES), ""
    )

    assert fp is not None
    assert macro is not None
    fp_names = {function["symbol_id"]: function["name"] for function in fp["functions"]}
    macro_names = {
        function["symbol_id"]: function["name"] for function in macro["functions"]
    }
    assert ("run", "apply") in {
        (fp_names[call["caller_id"]], call["callee_name"])
        for call in fp["calls"]
    }
    assert {
        call["callee_name"]
        for call in macro["calls"]
        if macro_names[call["caller_id"]] == "entry"
    } == {
        "internal",
        "other_file_helper",
    }
