from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "generate_graph_ingest_scale_fixture",
    ROOT / "scripts" / "generate_graph_ingest_scale_fixture.py",
)
assert SPEC and SPEC.loader
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)


def test_manifest_binds_compile_database_without_binding_output_path(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(fixture, "TOTAL_FILES", 7)
    monkeypatch.setattr(fixture, "COMPILED_FILES", 3)
    monkeypatch.setattr(fixture, "FANOUT_HEADERS", 2)
    monkeypatch.setattr(fixture, "PROC_FILES", 2)

    first = fixture.generate(tmp_path / "first")
    second = fixture.generate(tmp_path / "second")

    assert first["content_manifest_sha256"] == second["content_manifest_sha256"]
    assert first["compile_commands_sha256"] != second["compile_commands_sha256"]
    assert first["compile_commands_sha256"] == fixture.hashlib.sha256(
        (tmp_path / "first" / "compile_commands.json").read_bytes()
    ).hexdigest()
