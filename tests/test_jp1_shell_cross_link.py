from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.jp1.pipeline import run_jp1_analysis
from tools.jp1.jp1_analyzer import build_graph_rows


def test_jp1_pipeline_links_shell_and_preserves_unresolved_target() -> None:
    fixture_root = ROOT / "tests" / "fixtures" / "jp1-application"

    result = run_jp1_analysis(str(fixture_root), project_id="jp1-fixture")
    rows = build_graph_rows(result, project_name="JP1 fixture", repo="fixtures/jp1")
    calls = [row for row in rows["relations"] if row["rel_type"] == "CALLS"]

    assert {row["target_id"] for row in calls} == {"run.sh", "@BOSAPDIR@/missing.sh"}
    assert {row["properties"]["resolved"] for row in calls} == {True, False}