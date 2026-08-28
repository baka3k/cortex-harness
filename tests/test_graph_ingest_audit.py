from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_graph_ingest", ROOT / "scripts" / "audit_graph_ingest.py"
)
assert SPEC and SPEC.loader
audit_graph_ingest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_graph_ingest)


def _journal(**overrides):
    value = {
        "status": "drained",
        "endpoint_audit": "sealed",
        "conservation": {
            "conserved": True,
            "node": {"conflict": 0, "rejected": 0},
            "edge": {"conflict": 0, "rejected": 0},
            "producers": {"open": 0},
        },
    }
    value.update(overrides)
    return value


def test_journal_requires_seal_and_conservation() -> None:
    assert audit_graph_ingest._journal_is_valid(_journal())
    assert not audit_graph_ingest._journal_is_valid(
        _journal(endpoint_audit=None)
    )
    assert not audit_graph_ingest._journal_is_valid(
        _journal(conservation={"conserved": False})
    )


def test_journal_rejects_conflicts_and_open_producers() -> None:
    conservation = {
        "conserved": True,
        "node": {"conflict": 1, "rejected": 0},
        "edge": {"conflict": 0, "rejected": 0},
        "producers": {"open": 0},
    }
    assert not audit_graph_ingest._journal_is_valid(
        _journal(conservation=conservation)
    )
    conservation["node"]["conflict"] = 0
    conservation["producers"]["open"] = 1
    assert not audit_graph_ingest._journal_is_valid(
        _journal(conservation=conservation)
    )
