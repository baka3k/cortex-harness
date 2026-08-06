from __future__ import annotations

from pathlib import Path
import sys

import pytest


CODE_TINY = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = CODE_TINY.parent
for entry in (str(CODE_TINY), str(REPOSITORY_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from cortex_harness.storage import reset_clients
from tools.common.local_qdrant import (
    LocalQdrantWriter,
    RemoteQdrantUnsupportedError,
    collections_payload,
    get_code_qdrant_store,
    query_points,
)
from tools.common.primary_vector_sync import vector_configured


@pytest.fixture(autouse=True)
def _reset_local_clients() -> None:
    reset_clients()
    yield
    reset_clients()


def test_code_owner_path_writer_and_query_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code_path = tmp_path / "instance" / "qdrant" / "code"
    monkeypatch.setenv("QDRANT_CODE_PATH", str(code_path))

    writer = LocalQdrantWriter(None, "symbols", 2)
    writer.ensure_collection()
    writer.upsert([
        {
            "id": 1,
            "vector": [1.0, 0.0],
            "payload": {"project_id_normalized": "project-a"},
        }
    ])

    store = get_code_qdrant_store()
    hits = query_points(
        store,
        "symbols",
        [1.0, 0.0],
        limit=5,
        query_filter={
            "must": [{
                "key": "project_id_normalized",
                "match": {"value": "project-a"},
            }]
        },
    )

    assert writer.url == str(code_path)
    assert collections_payload(store)["collections"] == ["symbols"]
    assert hits[0]["payload"]["project_id_normalized"] == "project-a"


def test_remote_locator_is_rejected_before_store_open() -> None:
    with pytest.raises(RemoteQdrantUnsupportedError, match="Remote Qdrant endpoints"):
        get_code_qdrant_store("https://example.invalid:6333")


def test_vector_persistence_requires_an_explicit_owner_path() -> None:
    assert vector_configured(None) is False
    assert vector_configured("   ") is False
    assert vector_configured("/tmp/code-owner") is True


def test_windows_drive_path_is_not_misclassified_as_remote(tmp_path: Path) -> None:
    from tools.common.local_qdrant import _local_path

    resolved = _local_path(r"C:\cortex\qdrant", project_root=tmp_path)
    assert str(resolved).endswith(r"C:\cortex\qdrant")


def test_active_code_tiny_python_does_not_assemble_rest_storage_paths() -> None:
    forbidden = ("/" + "collections", "/" + "points")
    offenders: list[str] = []
    for path in CODE_TINY.rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in forbidden):
            offenders.append(str(path.relative_to(CODE_TINY)))
    assert offenders == []
