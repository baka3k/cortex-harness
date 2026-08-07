from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cobol.models import (  # noqa: E402
    AnalysisSummary,
    AnalysisResult,
    SemanticEdge,
    SemanticNode,
    SourceEvidence,
)
from tools.cobol.pipeline import graph_rows, select_incremental_result  # noqa: E402


def test_incremental_selection_retains_cross_file_endpoint_labels() -> None:
    changed_evidence = SourceEvidence("changed.cbl", 1)
    stable_evidence = SourceEvidence("stable.cpy", 1)
    source = SemanticNode("program", "CobolProgram", "PROGRAM", "changed.cbl", changed_evidence)
    target = SemanticNode("copybook", "CobolCopybook", "COPY", "stable.cpy", stable_evidence)
    edge = SemanticEdge(
        "edge",
        source.id,
        target.id,
        "INCLUDES",
        changed_evidence,
    )
    result = AnalysisResult(
        "demo",
        "/repo",
        (source, target),
        (edge,),
        (),
        AnalysisSummary(2, 2, 1, 0, 0),
    )

    selected = select_incremental_result(result, ["changed.cbl"])
    node_rows, relation_rows = graph_rows(selected)

    assert {node.id for node in selected.nodes} == {"program", "copybook"}
    assert set(node_rows) == {"CobolProgram", "CobolCopybook"}
    assert relation_rows[0]["source_label"] == "CobolProgram"
    assert relation_rows[0]["target_label"] == "CobolCopybook"
