import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
MCP_DIR = CODE_TINY / "mcp"
for path in (str(CODE_TINY), str(MCP_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from tool_metadata import _FULL_CATALOG  # noqa: E402
from tools.project_topology.registry import (  # noqa: E402
    FRAMEWORK_CONTEXT_COVERAGE,
    PRIMARY_SPECIAL_FILE_COVERAGE,
)
from tools.sync.incremental_sync import ANALYZERS, FRAMEWORK_ANALYZERS  # noqa: E402


MATRIX_PATH = ROOT / "docs" / "PROJECT_TOPOLOGY_ACCEPTANCE_MATRIX.json"


def _matrix():
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def test_matrix_covers_every_registered_primary_and_framework():
    matrix = _matrix()
    primary = {row["name"]: row for row in matrix["primary_analyzers"]}
    frameworks = {row["name"]: row for row in matrix["framework_overlays"]}

    assert set(primary) == set(ANALYZERS) == set(PRIMARY_SPECIAL_FILE_COVERAGE)
    assert (
        set(frameworks)
        == set(FRAMEWORK_ANALYZERS)
        == set(FRAMEWORK_CONTEXT_COVERAGE)
    )
    for row in [*primary.values(), *frameworks.values()]:
        assert (ROOT / row["evidence"]).is_file(), row
        assert row["parse_depth"] in {
            "identity",
            "topology",
            "dependency",
            "semantic",
            "unsupported",
        }
    for name, row in primary.items():
        assert row["parse_depth"] == PRIMARY_SPECIAL_FILE_COVERAGE[
            name
        ].parse_depth.value
    for name, row in frameworks.items():
        assert row["parse_depth"] == FRAMEWORK_CONTEXT_COVERAGE[
            name
        ].parse_depth.value


def test_matrix_context_tools_match_catalog_and_unified_registration():
    matrix = _matrix()
    catalog = {item["name"] for item in _FULL_CATALOG}
    unified_source = (MCP_DIR / "unified_mcp.py").read_text(encoding="utf-8")
    for tool in matrix["context_tools"]:
        assert tool in catalog
        assert f'name="{tool}"' in unified_source
