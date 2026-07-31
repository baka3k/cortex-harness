from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.shell.parser import parse_shell_text


def test_extracts_functions_calls_and_ini_references(tmp_path: Path) -> None:
    (tmp_path / "other.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (tmp_path / "settings.ini").write_text("MODE:BATCH\n", encoding="utf-8")
    source = """CONFIG=settings.ini
run_batch() {
  . other.sh
  grep 'MODE' "${CONFIG}" | awk -F: '{print $2}'
}
source missing.sh
"""

    parsed = parse_shell_text(source, file_path="main.sh", project_root=str(tmp_path))

    assert [function.name for function in parsed.functions] == ["run_batch"]
    assert [relation.rel_type for relation in parsed.relations] == ["CALLS", "REFERENCES", "CALLS"]
    assert parsed.relations[0].resolved is True
    assert parsed.relations[1].target_id == "settings.ini"
    assert parsed.relations[2].resolved is False
    assert parsed.diagnostics[0].code == "shell-call-unresolved"


def test_script_target_cannot_escape_project_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.sh"
    outside.write_text("#!/bin/sh\n", encoding="utf-8")

    parsed = parse_shell_text(
        ". ../outside.sh\n",
        file_path="main.sh",
        project_root=str(tmp_path),
    )

    assert parsed.relations[0].resolved is False