"""Phase 05 — the doctor scope-drift report (warn-only, report-only).

One command answers "registry vs stores: what drifted?". Every finding is
``required=False`` (``[doctor][warn]``, never fails CI) and the doctor never
issues a write/create/delete against any store.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
LIFECYCLE_PATH = ROOT / "scripts" / "mcp-lifecycle.py"


def _load_lifecycle_module():
    spec = importlib.util.spec_from_file_location(
        "mcp_lifecycle_doctor_drift_test", LIFECYCLE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LIFECYCLE = _load_lifecycle_module()


class FakeQdrantStore:
    def __init__(self, collections, points=None):
        self._collections = list(collections)
        self._points = dict(points or {})
        self.calls = []

    def list_collection_names(self):
        self.calls.append("list_collection_names")
        return list(self._collections)

    def get_collection_info(self, name):
        self.calls.append(f"get_collection_info:{name}")

        class _Info:
            pass

        info = _Info()
        info.points_count = self._points.get(name)
        return info

    # Write surface must never be touched — presence here + the not-called
    # asserts below is the AC3 guard.
    create_collection = mock.Mock()
    delete_collection = mock.Mock()
    upsert = mock.Mock()


class FakeGraphDriver:
    def __init__(self, graphs, counts=None):
        self._graphs = list(graphs)
        self._counts = dict(counts or {})
        self.calls = []

    def list_graphs(self):
        self.calls.append("list_graphs")
        return list(self._graphs)

    def execute_query_sync(self, query, params, database):
        self.calls.append(f"count:{database}")
        if database not in self._counts:
            raise RuntimeError("count failed")
        return [{"count": self._counts[database]}], ["count"], None


@pytest.fixture
def clean_registry(tmp_path, monkeypatch):
    """No projects anywhere; ROOT is a fresh tree."""
    config_dir = tmp_path / ".cortext-harness" / "config"
    config_dir.mkdir(parents=True)
    monkeypatch.setattr(LIFECYCLE, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    return config_dir


def _write_config(config_dir: Path, name: str, payload: dict) -> Path:
    path = config_dir / f"{name}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run_checks(qdrant_stores=(), graph_drivers=()):
    with mock.patch.object(
        LIFECYCLE, "_drift_qdrant_stores", lambda: list(qdrant_stores)
    ), mock.patch.object(
        LIFECYCLE, "_drift_graph_drivers", lambda: list(graph_drivers)
    ), mock.patch.object(
        LIFECYCLE, "doctor_check", return_value=0
    ) as check:
        LIFECYCLE.doctor_scope_drift_checks()
    return [
        (c.args[0], c.args[1], c.args[2], c.kwargs.get("required", True))
        for c in check.call_args_list
    ]


class TestScopeDriftChecks:
    def test_ac1_unregistered_absent_empty_and_malformed_all_warn(self, clean_registry):
        _write_config(
            clean_registry,
            "registered",
            {
                "project": {"code": "registered"},
                "storage_backend": "local",
                "doc": {"env": {"QDRANT_COLLECTION_DOC": "registered_doc"}},
            },
        )
        (clean_registry / "broken.json").write_text("{not json", encoding="utf-8")

        qdrant = FakeQdrantStore(
            ["orphan_doc", "registered_doc", "empty_doc"],
            {"orphan_doc": 7, "empty_doc": 0, "registered_doc": 3},
        )
        checks = _run_checks(qdrant_stores=[("q", qdrant)])
        warns = [c for c in checks if c[1] is False]
        messages = " | ".join(c[2] for c in warns)

        assert any("orphan_doc" in m and "7 points" in m for m in messages.split(" | "))
        assert any("empty_doc" in m and "empty" in m and "safe to drop" in m
                   for m in messages.split(" | "))
        # registered_doc is declared — no orphan warn for it.
        assert "registered_doc holds" not in messages
        assert "broken.json" in messages and "invisible to the registry" in messages
        # AC1: warn-only semantics — no required failures.
        assert all(c[3] is False for c in warns)

    def test_ac1_declared_but_absent_warns_on_own_backend(self, clean_registry):
        _write_config(
            clean_registry,
            "ghost",
            {
                "project": {"code": "ghost"},
                "storage_backend": "remote",
                "remote": {"qdrant_url": "q"},
                "doc": {"env": {"QDRANT_COLLECTION_DOC": "ghost_doc"}},
            },
        )
        qdrant = FakeQdrantStore([])  # the project's own store holds nothing
        checks = _run_checks(qdrant_stores=[("q", qdrant)])
        messages = " | ".join(c[2] for c in checks)
        assert any(
            "ghost_doc" in m and "does not exist" in m for m in messages.split(" | ")
        )

    def test_local_project_not_flagged_absent_on_remote_store(self, clean_registry):
        """A local project's shard is not expected on someone else's remote."""
        _write_config(
            clean_registry,
            "localp",
            {
                "project": {"code": "localp"},
                "storage_backend": "local",
                "doc": {"env": {"QDRANT_COLLECTION_DOC": "localp_doc"}},
            },
        )
        qdrant = FakeQdrantStore(["other_doc"])  # remote store, unrelated
        checks = _run_checks(qdrant_stores=[("http://remote:6333", qdrant)])
        messages = " | ".join(c[2] for c in checks)
        assert "localp_doc" not in messages

    def test_ac2_clean_state_reports_single_ok_group(self, clean_registry):
        qdrant = FakeQdrantStore(["registered_doc"])
        graph = FakeGraphDriver(["registered_doc"], {"registered_doc": 5})
        _write_config(
            clean_registry,
            "registered",
            {
                "project": {"code": "registered"},
                "storage_backend": "local",
                "doc": {"env": {
                    "QDRANT_COLLECTION_DOC": "registered_doc",
                    "FALKORDB_GRAPH": "registered_doc",
                }},
            },
        )
        checks = _run_checks(
            qdrant_stores=[("q", qdrant)],
            graph_drivers=[("f", graph)],
        )
        oks = [c for c in checks if c[0] == "scope drift" and c[1] is True]
        warns = [c for c in checks if c[1] is False]
        assert len(oks) == 1
        assert "agree" in oks[0][2]
        assert warns == []

    def test_ac3_doctor_never_writes_to_stores(self, clean_registry):
        qdrant = FakeQdrantStore(["junk_doc"], {"junk_doc": 0})
        graph = FakeGraphDriver(["junk_doc"], {"junk_doc": 0})
        _run_checks(
            qdrant_stores=[("q", qdrant)],
            graph_drivers=[("f", graph)],
        )
        qdrant.create_collection.assert_not_called()
        qdrant.delete_collection.assert_not_called()
        qdrant.upsert.assert_not_called()
        # Only reads: listing + point counts (and a count on the existing graph).
        assert all(
            call in {"list_collection_names", "get_collection_info:junk_doc"}
            or call.startswith("get_collection_info:")
            for call in qdrant.calls
        )
        assert all(
            call in {"list_graphs", "count:junk_doc"} for call in graph.calls
        )

    def test_graph_count_failure_falls_back_to_name_level(self, clean_registry):
        graph = FakeGraphDriver(["orphan_doc"])  # count raises -> no entry
        checks = _run_checks(graph_drivers=[("f", graph)])
        messages = " | ".join(c[2] for c in checks)
        assert any("orphan_doc" in m and "no registered" in m
                   for m in messages.split(" | "))
        assert "holds" not in messages

    def test_force_local_bypass(self, clean_registry, monkeypatch):
        monkeypatch.setenv("CORTEX_STORAGE_BACKEND_FORCE_LOCAL", "1")
        with mock.patch.object(
            LIFECYCLE, "doctor_check", return_value=0
        ) as check, mock.patch.object(
            LIFECYCLE, "_scan_project_backends"
        ) as scan:
            LIFECYCLE.doctor_scope_drift_checks()
        scan.assert_not_called()
        assert check.call_args.args[0] == "scope drift"
        assert "bypassed" in check.call_args.args[2]

    def test_legacy_key_notice_is_informational(self, clean_registry):
        _write_config(
            clean_registry,
            "legacy",
            {
                "project": {"code": "legacy"},
                "storage_backend": "local",
                "doc": {"env": {"QDRANT_COLLECTION": "legacy_doc"}},
            },
        )
        checks = _run_checks()
        notices = [c for c in checks if c[0] == "scope drift legacy key"]
        assert len(notices) == 1
        assert notices[0][1] is True  # informational, not a warn
        assert "QDRANT_COLLECTION_DOC" in notices[0][2]

    def test_dual_key_registered_entry_needs_no_notice(self, clean_registry):
        _write_config(
            clean_registry,
            "modern",
            {
                "project": {"code": "modern"},
                "storage_backend": "local",
                "doc": {"env": {"QDRANT_COLLECTION_DOC": "modern_doc"}},
            },
        )
        checks = _run_checks()
        assert not [c for c in checks if c[0] == "scope drift legacy key"]
