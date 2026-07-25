import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
FIXTURE = ROOT / "tests" / "fixtures" / "project-topology"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.project_topology.topology_analyzer import main as topology_main  # noqa: E402
from tools.sync.incremental_sync import (  # noqa: E402
    PROJECT_TOPOLOGY_ANALYZER,
    _selected_parsers,
    _walk_all_source_files,
)


def test_auto_sync_registers_non_vector_topology_overlay():
    selected, automatic = _selected_parsers("auto")
    assert automatic is True
    assert "project_topology" in selected
    assert PROJECT_TOPOLOGY_ANALYZER.writes_vectors is False
    assert Path(PROJECT_TOPOLOGY_ANALYZER.script_path).is_file()


def test_sync_inventory_includes_extensionless_and_proto_descriptors():
    inventory = _walk_all_source_files(str(FIXTURE))
    assert "native/Makefile" in inventory
    assert "api/service.proto" in inventory
    assert "settings.gradle.kts" in inventory


def test_topology_cli_incremental_manifest_recomputes_complete_state(
    tmp_path, capsys
):
    changed = tmp_path / "changed.json"
    deleted = tmp_path / "deleted.json"
    changed.write_text(
        json.dumps({"files": ["app/build.gradle.kts"]}), encoding="utf-8"
    )
    deleted.write_text(json.dumps({"files": ["old/pom.xml"]}), encoding="utf-8")

    exit_code = topology_main(
        [
            "--root",
            str(FIXTURE),
            "--project-id",
            "Fixture",
            "--incremental",
            "--changed-files-manifest",
            str(changed),
            "--deleted-files-manifest",
            str(deleted),
            "--dry-run",
        ]
    )
    payload = json.loads(
        capsys.readouterr().out.split("[project_topology] ", 1)[1]
    )
    assert exit_code == 0
    assert payload["changed_descriptors"] == ["app/build.gradle.kts"]
    assert payload["deleted_descriptors"] == ["old/pom.xml"]
    assert payload["modules"] >= 6
    assert payload["coverage"]["build_execution"] is False
