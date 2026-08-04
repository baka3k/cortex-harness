from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cplus.cplus_analyzer import _load_or_parse_payload
from tools.jp1.pipeline import run_jp1_analysis
from tools.project_topology.detector import parse_descriptor_file
from tools.shell.pipeline import run_shell_analysis


def test_legacy_jobnet_shell_ini_and_proc_chain(tmp_path: Path) -> None:
    fixture_root = ROOT / "tests" / "fixtures" / "legacy-migration-e2e"

    jp1 = run_jp1_analysis(str(fixture_root), project_id="legacy-e2e")
    shell = run_shell_analysis(str(fixture_root), project_id="legacy-e2e")
    ini = parse_descriptor_file(root=fixture_root, project_id="legacy-e2e", path="settings.ini")
    proc = _load_or_parse_payload(str(fixture_root / "program.pc"), str(fixture_root), str(tmp_path), False)

    jp1_calls = [relation for relation in jp1.files[0].relations if relation.rel_type == "CALLS"]
    shell_refs = [relation for file in shell.files for relation in file.relations if relation.rel_type == "REFERENCES"]
    assert {relation.resolved for relation in jp1_calls} == {True, False}
    assert shell_refs[0].target_id == "settings.ini"
    assert shell_refs[0].resolved is True
    assert ini.descriptor.properties["entries"][0]["key"] == "MODE"
    assert proc["proc_nodes"][0]["name"] in {"SELECT", "UNKNOWN"}