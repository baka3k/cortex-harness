from __future__ import annotations

import json
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.shell.mapping import load_program_mappings
from tools.shell.models import ShellAnalysisResult
from tools.shell.parser import parse_shell_text
from tools.shell.shell_analyzer import _verified_lineage_rows, build_graph_rows


def _analysis(tmp_path: Path) -> ShellAnalysisResult:
    parsed = parse_shell_text(
        "BIN_DIR=/opt/batch\n${BIN_DIR}/sample_program --mode fast\n",
        file_path="batch_entry.sh",
        project_root=str(tmp_path),
    )
    return ShellAnalysisResult(project_id="client-alpha", files=(parsed,))


def test_external_mapping_uses_canonical_root_relative_source_path(tmp_path: Path) -> None:
    source = tmp_path / "native-src" / "sample_program.c"
    source.parent.mkdir()
    source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    ledger = tmp_path / "mapping.json"
    ledger.write_text(
        json.dumps(
            [
                {
                    "program_id": "sample_program",
                    "source_path": f"{tmp_path.name}/native-src/sample_program.c",
                    "evidence_hash": "sha256:fixture",
                }
            ]
        ),
        encoding="utf-8",
    )

    mappings = load_program_mappings(str(ledger), root=str(tmp_path))

    assert mappings[0].source_path == "native-src/sample_program.c"


def test_external_mapping_supports_configured_field_names_and_ledger_hash(tmp_path: Path) -> None:
    source = tmp_path / "native-src" / "sample_program.c"
    source.parent.mkdir()
    source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    ledger = tmp_path / "mapping.json"
    ledger.write_text(
        json.dumps(
            {
                "mappings": [
                    {
                        "logical_name": "sample_program",
                        "implementation": "native-src/sample_program.c",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    mappings = load_program_mappings(
        str(ledger),
        root=str(tmp_path),
        program_id_field="logical_name",
        source_path_field="implementation",
    )

    assert mappings[0].program_id == "sample_program"
    assert mappings[0].evidence_hash.startswith("sha256:")


def test_lineage_is_emitted_only_for_materialized_source_endpoint(tmp_path: Path) -> None:
    source = tmp_path / "native-src" / "sample_program.c"
    source.parent.mkdir()
    source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    ledger = tmp_path / "mapping.json"
    ledger.write_text(
        json.dumps(
            {
                "mappings": [
                    {
                        "program_id": "sample_program",
                        "source_path": "native-src/sample_program.c",
                        "evidence_hash": "sha256:fixture",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    mappings = load_program_mappings(str(ledger), root=str(tmp_path))
    rows = build_graph_rows(
        _analysis(tmp_path),
        project_name="Client Alpha",
        repo="fixtures/shell",
        program_mappings=mappings,
    )

    class Driver:
        async def execute_query(self, query, parameters=None, database=None):
            return ([{"id": "native-src/sample_program.c"}], [], None)

    verified, skipped = asyncio.run(
        _verified_lineage_rows(
            Driver(), rows, database="code", project_id="client-alpha"
        )
    )

    assert skipped == 0
    assert verified["invocations"][0]["resolution_status"] == "verified"
    assert {row["rel_type"] for row in verified["relations"]} >= {
        "HAS_INVOCATION",
        "RESOLVES_TO",
        "IMPLEMENTED_BY",
    }


def test_missing_source_endpoint_keeps_invocation_without_false_target(tmp_path: Path) -> None:
    ledger = tmp_path / "mapping.json"
    ledger.write_text(
        json.dumps(
            [
                {
                    "program_id": "sample_program",
                    "source_path": "native-src/missing.c",
                    "evidence_hash": "sha256:fixture",
                }
            ]
        ),
        encoding="utf-8",
    )
    mappings = load_program_mappings(str(ledger), root=str(tmp_path))
    rows = build_graph_rows(
        _analysis(tmp_path),
        project_name="Client Alpha",
        repo="fixtures/shell",
        program_mappings=mappings,
    )

    class Driver:
        async def execute_query(self, query, parameters=None, database=None):
            return ([], [], None)

    verified, skipped = asyncio.run(
        _verified_lineage_rows(
            Driver(), rows, database="code", project_id="client-alpha"
        )
    )

    assert skipped == 2
    assert verified["programs"] == []
    assert verified["invocations"][0]["resolution_status"] == "mapped_source_missing"
    assert {row["rel_type"] for row in verified["relations"]} == {"HAS_INVOCATION"}
