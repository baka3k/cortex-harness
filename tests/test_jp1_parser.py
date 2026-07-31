from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.jp1.parser import parse_jp1_text


def test_parses_nested_units_arcs_and_shell_target(tmp_path: Path) -> None:
    (tmp_path / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    source = """unit=ROOT,,host,;
{
ty=n;
unit=FIRST,,host,;
{
ty=j;
te=\"run.sh\";
}
unit=SECOND,,host,;
{
ty=j;
}
ar=(f=FIRST,t=SECOND,seq);
}
"""

    parsed = parse_jp1_text(source, file_path="jobnet.txt", project_root=str(tmp_path))

    assert [unit.name for unit in parsed.units] == ["ROOT", "FIRST", "SECOND"]
    assert {relation.rel_type for relation in parsed.relations} == {"INCLUDES", "NEXT", "CALLS"}
    call = next(relation for relation in parsed.relations if relation.rel_type == "CALLS")
    assert call.target_id == "run.sh"
    assert call.resolved is True


def test_exec_target_cannot_escape_project_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.sh"
    outside.write_text("#!/bin/sh\n", encoding="utf-8")
    source = """unit=RUN,,host,;
{
ty=j;
te=\"../outside.sh\";
}
"""

    parsed = parse_jp1_text(source, file_path="jobnet.txt", project_root=str(tmp_path))

    call = next(relation for relation in parsed.relations if relation.rel_type == "CALLS")
    assert call.resolved is False