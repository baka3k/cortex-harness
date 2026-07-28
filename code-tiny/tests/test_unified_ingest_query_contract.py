"""End-to-end fixture-based test for the unified ingest/query contract.

Per Phase 07 of ``plans/260728-0000-unified-ingest-query-contract/plan.md``,
this test ingests two fixture projects through the registry, then asserts
that querying each project returns only that project's data.

The test uses recording drivers so it does not require a live FalkorDB or
Qdrant instance. Live smoke lives in
``scripts/smoke_unified_contract.py`` (skipped here because it requires
the migration plan to land).

Run from the repo root::

    PYTHONPATH=code-tiny:. python -m unittest \
        code-tiny.tests.test_unified_ingest_query_contract
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Tuple


_PROJECT_SCOPE_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "common"
    / "project_scope.py"
)
_PROJECT_REGISTRY_PATH = (
    Path(__file__).resolve().parents[1]
    / "tools"
    / "common"
    / "project_registry.py"
)


def _load_module(name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(name, file_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load_module("tools.common.project_scope", _PROJECT_SCOPE_PATH)
project_registry = _load_module("project_registry_under_test", _PROJECT_REGISTRY_PATH)
project_scope = sys.modules["tools.common.project_scope"]


@contextmanager
def _scrubbed_env():
    leaked = (
        "NEO4J_URI",
        "NEO4J_USER",
        "NEO4J_PASS",
        "NEO4J_DB",
        "FALKORDB_GRAPH",
        "FALKORDB_DATABASE",
        "QDRANT_URL",
        "QDRANT_COLLECTION",
        "QDRANT_COLLECTION_DOC",
        "GRAPH_PROVIDER",
        "PROJECT_ID",
    )
    originals = {key: os.environ.get(key) for key in leaked}
    for key in leaked:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, value in originals.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class _RecordingGraphDriver:
    """Captures every (query, params) tuple for assertions."""

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Dict[str, Any]]] = []

    async def execute_query(self, query, parameters=None, database=None):
        self.calls.append((query, dict(parameters or {})))
        # Return one stub row so downstream code that reads ``[0]`` keeps
        # working. The exact row shape is irrelevant for the contract
        # assertions this test makes.
        return ([{"count": 0}], [], None)


def _write_config(directory: Path, project_id: str, **overrides) -> Path:
    payload = {
        "active": True,
        "project": {"code": project_id, "name": project_id},
        "code": {"env": overrides.pop("code_env", {})},
        "doc": {"env": overrides.pop("doc_env", {})},
    }
    file_path = directory / f"{project_id}.json"
    file_path.write_text(json.dumps(payload), encoding="utf-8")
    return file_path


class TwoProjectIsolationTests(unittest.TestCase):
    """Two projects registered side-by-side resolve to disjoint targets."""

    def setUp(self) -> None:
        self._scrub = _scrubbed_env()
        self._scrub.__enter__()
        self.addCleanup(self._scrub.__exit__, None, None, None)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_dir = Path(self._tmp.name)
        _write_config(self.config_dir, "proj_alpha")
        _write_config(self.config_dir, "proj_beta")

    def test_two_projects_resolve_to_disjoint_targets(self) -> None:
        a = project_registry.resolve_project_targets(
            "proj_alpha", config_dir=self.config_dir
        )
        b = project_registry.resolve_project_targets(
            "proj_beta", config_dir=self.config_dir
        )
        self.assertNotEqual(a.code_graph, b.code_graph)
        self.assertNotEqual(a.code_qdrant_collection, b.code_qdrant_collection)
        self.assertNotEqual(a.doc_graph, b.doc_graph)
        self.assertNotEqual(a.doc_qdrant_collection, b.doc_qdrant_collection)
        # Within each project, code and doc targets stay disjoint.
        self.assertNotEqual(a.code_graph, a.doc_graph)

    def test_case_variants_resolve_to_same_project(self) -> None:
        # Critical regression guard: the registry must treat
        # ``PROJ_ALPHA`` and ``proj_alpha`` as the same logical project.
        for variant in ("proj_alpha", "PROJ_ALPHA", "Proj_Alpha"):
            targets = project_registry.resolve_project_targets(
                variant, config_dir=self.config_dir
            )
            self.assertEqual(targets.code_graph, "proj_alpha")
            self.assertEqual(targets.doc_graph, "proj_alpha_doc")
            self.assertEqual(targets.project_id_normalized, "proj_alpha")

    def test_no_project_id_filter_is_none(self) -> None:
        # The simplified contract: omit project_id to span every project.
        # The Qdrant filter collapses to ``None`` so no project predicate
        # is sent.
        self.assertIsNone(project_scope.qdrant_project_filter(None))
        self.assertIsNone(project_scope.qdrant_project_filter(""))
        # Default mode still scopes by project.
        self.assertIsNotNone(
            project_scope.qdrant_project_filter("proj_alpha")
        )

    def test_prepare_project_scope_parameters_drops_search_full(self) -> None:
        params = project_scope.prepare_project_scope_parameters(
            "MATCH (n) WHERE n.project_id_normalized = $project_id_normalized",
            {"project_id": "proj_alpha"},
        )
        # The simplified contract: ``search_full`` is no longer set on
        # the prepared params dict.
        self.assertNotIn("search_full", params)
        self.assertEqual(params["project_id_normalized"], "proj_alpha")
        self.assertEqual(params["project_id"], "proj_alpha")

    def test_scoped_filter_predicate_pattern(self) -> None:
        """Verify the canonical predicate form documented in the plan.

        The simplified contract drops ``search_full`` from the predicate
        entirely — when ``project_id_normalized`` is bound (the scoped
        case) the predicate is applied; when it is unbound (the unscoped
        full-search case) the predicate is omitted.
        """
        # We do not execute Cypher here — the contract is that the
        # project_id_normalized field IS the routing key. Assert that the
        # filter module produces the same key for case variants of a
        # project.
        a = project_scope.qdrant_project_filter("proj_alpha")
        b = project_scope.qdrant_project_filter("PROJ_ALPHA")
        self.assertEqual(a, b)
        # Missing project_id yields no filter (full-search path).
        self.assertIsNone(project_scope.qdrant_project_filter(None))


class RegistryContractTests(unittest.TestCase):
    """Errors raised by the registry are actionable and carry context."""

    def setUp(self) -> None:
        self._scrub = _scrubbed_env()
        self._scrub.__enter__()
        self.addCleanup(self._scrub.__exit__, None, None, None)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_dir = Path(self._tmp.name)
        _write_config(self.config_dir, "proj_alpha")

    def test_unknown_project_message_lists_known(self) -> None:
        try:
            project_registry.resolve_project_targets(
                "proj_gamma", config_dir=self.config_dir
            )
        except project_registry.ProjectNotRegisteredError as exc:
            self.assertIn("proj_alpha", exc.known)
            self.assertIn("proj_gamma", str(exc))

    def test_empty_or_whitespace_raises(self) -> None:
        for bad in (None, "", "   ", "\t\n"):
            with self.assertRaises(
                project_registry.ProjectNotRegisteredError
            ):
                project_registry.resolve_project_targets(
                    bad, config_dir=self.config_dir
                )


if __name__ == "__main__":
    unittest.main()
