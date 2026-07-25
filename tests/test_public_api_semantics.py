import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.android.android_kotlin_analyzer import (  # noqa: E402
    _kotlin_api_visibility as android_kotlin_visibility,
)
from tools.cplus.cplus_analyzer import _cplus_api_visibility  # noqa: E402
from tools.java.java_analyzer import parse_java_file  # noqa: E402
from tools.kotlin.kotlin_analyzer import (  # noqa: E402
    _kotlin_api_visibility,
    parse_kotlin_file,
)


def test_java_public_and_interface_implicit_visibility(tmp_path):
    source = tmp_path / "Api.java"
    source.write_text(
        """
package example;
public class Api {
  public void visible() {}
  protected void inheritedOnly() {}
  void packageOnly() {}
  private void hidden() {}
}
interface Contract {
  void implicitPublic();
  private void helper() {}
}
""",
        encoding="utf-8",
    )

    functions, _, classes, *_ = parse_java_file(str(source), str(tmp_path))
    by_name = {item.name: item for item in functions}
    by_class = {item.name: item for item in classes}

    assert by_class["Api"].is_public_api
    assert not by_class["Contract"].is_public_api
    assert by_name["visible"].is_public_api
    assert by_name["implicitPublic"].is_public_api
    assert by_name["inheritedOnly"].visibility == "protected"
    assert not by_name["packageOnly"].is_public_api
    assert not by_name["hidden"].is_public_api


def test_kotlin_default_public_and_internal_private_exclusion(tmp_path):
    source = tmp_path / "Api.kt"
    source.write_text(
        """
package example
class Api {
  fun visible() = Unit
  internal fun moduleOnly() = Unit
  private fun hidden() = Unit
}
private class Hidden
""",
        encoding="utf-8",
    )

    functions, _, classes, *_ = parse_kotlin_file(str(source), str(tmp_path))
    by_name = {item.name: item for item in functions}
    by_class = {item.name: item for item in classes}

    assert by_class["Api"].is_public_api
    assert not by_class["Hidden"].is_public_api
    assert by_name["visible"].is_public_api
    assert by_name["moduleOnly"].visibility == "internal"
    assert not by_name["hidden"].is_public_api
    assert _kotlin_api_visibility("fun visible() = Unit") == (
        "public",
        True,
        "default public by Kotlin language rule",
    )
    assert android_kotlin_visibility("internal fun f()") == (
        "internal",
        False,
        "explicit internal",
    )


def test_cplus_header_heuristics_remain_opt_in():
    assert _cplus_api_visibility(
        '__declspec(dllexport) int exported();', "src/api.cpp"
    )[:2] == ("exported", True)
    assert _cplus_api_visibility("int inferred();", "include/api.hpp")[:2] == (
        "inferred",
        False,
    )
    assert _cplus_api_visibility("static int hidden();", "src/api.cpp")[:2] == (
        "unknown",
        False,
    )
