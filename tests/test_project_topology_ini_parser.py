from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.project_topology.detector import parse_descriptor_file
from tools.project_topology.registry import PRIMARY_SPECIAL_FILE_COVERAGE, descriptor_spec_for_path


def test_cp932_flat_ini_is_parsed_through_descriptor_detector(tmp_path: Path) -> None:
    source = "MODE:BATCH\nCOMMENT:\u65e5\u672c\u8a9e\n# ignored\n"
    (tmp_path / "settings.ini").write_bytes(source.encode("cp932"))

    result = parse_descriptor_file(
        root=tmp_path,
        project_id="ini-fixture",
        path="settings.ini",
    )

    assert result.descriptor.parser == "ini"
    assert result.descriptor.properties["entries"] == [
        {"key": "MODE", "value": "BATCH", "line": 1},
        {"key": "COMMENT", "value": "\u65e5\u672c\u8a9e", "line": 2},
    ]
    assert descriptor_spec_for_path("settings.ini") is not None


def test_dat_and_ini_coverage_entries_are_registered() -> None:
    assert PRIMARY_SPECIAL_FILE_COVERAGE["ini"].patterns == ("*.ini",)
    assert PRIMARY_SPECIAL_FILE_COVERAGE["dat"].roles[0].value == "resource"