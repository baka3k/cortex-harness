"""Round-trip tests for the local db-transfer export/import pipeline."""

from __future__ import annotations

import json
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cortex_harness import db_transfer  # noqa: E402
from cortex_harness.db_transfer import (  # noqa: E402
    BUNDLE_SCHEMA,
    BUNDLE_SUFFIX,
    DbTransferError,
    export_project,
    import_project,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write_dev_json(project_dir: Path, *, storage_backend: str = "local", project_id: str = "demo") -> None:
    cfg_dir = project_dir / ".cortext-harness" / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "active": True,
        "project": {"code": project_id, "name": project_id},
        "storage_backend": storage_backend,
        "code": {
            "env": {
                "GRAPH_PROVIDER": "falkordb",
                "CODE_GRAPH_PROVIDER": "falkordb",
                "FALKORDB_GRAPH": project_id,
                "QDRANT_COLLECTION": project_id,
            },
            "source": {"projects": [{"git": "", "folder": []}]},
        },
        "doc": {
            "env": {
                "GRAPH_PROVIDER": "falkordb",
                "DOC_GRAPH_PROVIDER": "falkordb",
                "FALKORDB_GRAPH": f"{project_id}_doc",
                "QDRANT_COLLECTION_DOC": f"{project_id}_doc",
            },
            "source": {"projects": [{"git": "", "folder": []}]},
        },
    }
    (cfg_dir / "dev.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _seed_local_instance(data_home: Path, *, project_id: str, instance: str = "default") -> dict:
    """Populate ``data_home`` with fake Qdrant + FalkorDB files for ``project_id``."""
    root = data_home / "v1" / "instances" / instance
    q_code = root / "qdrant" / "code" / project_id
    q_doc = root / "qdrant" / "doc" / f"{project_id}_doc"
    q_code.mkdir(parents=True, exist_ok=True)
    q_doc.mkdir(parents=True, exist_ok=True)
    (q_code / "segments.bin").write_bytes(b"\x00CODE" * 4)
    (q_code / "meta.json").write_text(
        json.dumps({"collections": {project_id: {"vectors_count": 4}}}),
        encoding="utf-8",
    )
    (q_doc / "segments.bin").write_bytes(b"\x00DOC" * 4)
    f_code = root / "falkordb" / "code" / "data.rdb"
    f_doc = root / "falkordb" / "doc" / "data.rdb"
    f_code.parent.mkdir(parents=True, exist_ok=True)
    f_doc.parent.mkdir(parents=True, exist_ok=True)
    f_code.write_bytes(b"FALKCODE" * 8)
    f_doc.write_bytes(b"FALKDOC" * 8)
    (root / "manifest.json").write_text(
        json.dumps({"schema_version": "v1", "instance_id": instance, "owners": {}}, indent=2),
        encoding="utf-8",
    )
    return {
        "qdrant_code": q_code,
        "qdrant_doc": q_doc,
        "falkordb_code": f_code,
        "falkordb_doc": f_doc,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_export_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_id = "demo"
    project_root = tmp_path / "project"
    project_root.mkdir()
    data_home = tmp_path / "data-home"

    monkeypatch.setattr(
        "cortex_harness.storage.config.default_data_home", lambda: data_home
    )
    monkeypatch.setattr(db_transfer, "tempfile_dir", lambda _root: tmp_path / "staging")

    _write_dev_json(project_root, project_id=project_id)
    seeded = _seed_local_instance(data_home, project_id=project_id)

    out_dir = tmp_path / "exports"
    result = export_project(project_root, output=out_dir / f"{project_id}{BUNDLE_SUFFIX}")

    assert result.archive_path.is_file()
    assert result.archive_path.suffix == BUNDLE_SUFFIX
    assert result.project_id == project_id

    with tarfile.open(result.archive_path, "r:gz") as tar:
        names = tar.getnames()
    assert any(n.endswith("manifest.json") for n in names)
    assert any(f"qdrant/code/{project_id}" in n for n in names)
    assert any("falkordb/code.rdb" in n for n in names)

    recipient_root = tmp_path / "recipient"
    recipient_data = tmp_path / "recipient-data"
    (recipient_root / ".cortext-harness" / "config").mkdir(parents=True, exist_ok=True)
    _write_dev_json(recipient_root, project_id=project_id)
    monkeypatch.setattr(
        "cortex_harness.storage.config.default_data_home", lambda: recipient_data
    )

    imported = import_project(recipient_root, result.archive_path)
    assert imported.project_id == project_id

    restored_code = recipient_data / "v1" / "instances" / "default" / "qdrant" / "code" / project_id
    restored_falk = recipient_data / "v1" / "instances" / "default" / "falkordb" / "code" / "data.rdb"
    assert restored_code.is_dir()
    assert (restored_code / "segments.bin").read_bytes() == seeded["qdrant_code"].joinpath("segments.bin").read_bytes()
    assert restored_falk.read_bytes() == seeded["falkordb_code"].read_bytes()


def test_export_rejects_remote_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    _write_dev_json(project_root, storage_backend="remote")

    monkeypatch.setattr(
        "cortex_harness.storage.config.default_data_home",
        lambda: tmp_path / "data-home",
    )

    with pytest.raises(DbTransferError, match="storage_backend='local'"):
        export_project(project_root, output=tmp_path / "out.cortexdb")


def test_import_refuses_overwrite_without_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_id = "demo"
    project_root = tmp_path / "project"
    project_root.mkdir()
    data_home = tmp_path / "data-home"

    monkeypatch.setattr(
        "cortex_harness.storage.config.default_data_home", lambda: data_home
    )
    monkeypatch.setattr(db_transfer, "tempfile_dir", lambda _root: tmp_path / "staging")

    _write_dev_json(project_root, project_id=project_id)
    _seed_local_instance(data_home, project_id=project_id)

    archive = export_project(
        project_root, output=tmp_path / "bundle.cortexdb"
    ).archive_path

    with pytest.raises(DbTransferError, match="OVERWRITE=1"):
        import_project(project_root, archive, overwrite=False)

    imported = import_project(project_root, archive, overwrite=True)
    assert imported.backup_path is not None and imported.backup_path.exists()


def test_export_missing_storage_reports_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    data_home = tmp_path / "data-home"

    monkeypatch.setattr(
        "cortex_harness.storage.config.default_data_home", lambda: data_home
    )
    monkeypatch.setattr(db_transfer, "tempfile_dir", lambda _root: tmp_path / "staging")

    _write_dev_json(project_root, project_id="demo")
    # No seeded storage — expect a clear error instead of an empty archive.

    with pytest.raises(DbTransferError, match="No local storage files"):
        export_project(project_root, output=tmp_path / "out.cortexdb")


def test_bundle_schema_embedded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    data_home = tmp_path / "data-home"

    monkeypatch.setattr(
        "cortex_harness.storage.config.default_data_home", lambda: data_home
    )
    monkeypatch.setattr(db_transfer, "tempfile_dir", lambda _root: tmp_path / "staging")

    _write_dev_json(project_root, project_id="demo")
    _seed_local_instance(data_home, project_id="demo")

    archive = export_project(
        project_root, output=tmp_path / "demo.cortexdb"
    ).archive_path

    with tarfile.open(archive, "r:gz") as tar:
        manifest_member = next(m for m in tar.getmembers() if m.name.endswith("manifest.json"))
        reader = tar.extractfile(manifest_member)
        assert reader is not None
        manifest = json.loads(reader.read().decode("utf-8"))

    assert manifest["schema"] == BUNDLE_SCHEMA
    assert manifest["project_id"] == "demo"
    assert "code" in manifest["entries"]["qdrant"]
    assert "code" in manifest["entries"]["falkordb"]


# ---------------------------------------------------------------------------
# CLI alias tests (``dev export`` / ``dev import``)
# ---------------------------------------------------------------------------

def test_cli_export_import_aliases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from click.testing import CliRunner

    from cortex_harness.dev import cli

    project_id = "demo"
    project_root = tmp_path / "project"
    project_root.mkdir()
    data_home = tmp_path / "data-home"

    monkeypatch.setattr(
        "cortex_harness.storage.config.default_data_home", lambda: data_home
    )
    monkeypatch.setattr(db_transfer, "tempfile_dir", lambda _root: tmp_path / "staging")
    monkeypatch.chdir(project_root)

    _write_dev_json(project_root, project_id=project_id)
    _seed_local_instance(data_home, project_id=project_id)

    out_path = tmp_path / "alias-out.cortexdb"
    runner = CliRunner()
    export_result = runner.invoke(
        cli,
        ["export", project_id, "--output", str(out_path), "--project-dir", str(project_root)],
    )
    assert export_result.exit_code == 0, export_result.output
    assert out_path.is_file()

    import_result = runner.invoke(
        cli,
        ["import", str(out_path), "--overwrite", "--project-dir", str(project_root)],
    )
    assert import_result.exit_code == 0, import_result.output
    assert (data_home / "v1" / "instances" / "default" / "falkordb" / "code" / "data.rdb").is_file()
