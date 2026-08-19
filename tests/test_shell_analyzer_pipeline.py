from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.shell.pipeline import run_shell_analysis
from tools.shell.shell_analyzer import build_graph_rows


def test_shell_pipeline_builds_resolved_and_unresolved_rows() -> None:
    fixture_root = ROOT / "tests" / "fixtures" / "shell-application"

    result = run_shell_analysis(str(fixture_root), project_id="shell-fixture")
    rows = build_graph_rows(result, project_name="Shell fixture", repo="fixtures/shell")

    assert {row["id"] for row in rows["scripts"]} == {"batch_entry.sh", "other_target.sh"}
    assert [row["name"] for row in rows["functions"]] == ["run_batch"]
    calls = [row for row in rows["relations"] if row["rel_type"] == "CALLS"]
    assert {row["properties"]["resolved"] for row in calls} == {True, False}
    assert {row["id"] for row in rows["files"]} == {"settings.ini"}
    ini_reference = next(
        row for row in rows["relations"] if row["rel_type"] == "REFERENCES"
    )
    assert ini_reference["target_id"] in {row["id"] for row in rows["files"]}
