"""Unit tests for doc-tiny/project_contract.py.

Run from the repo root::

    PYTHONPATH=doc-tiny python -m unittest \
        doc-tiny.tests.test_project_contract
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


_PROJECT_CONTRACT_PATH = (
    Path(__file__).resolve().parents[1] / "project_contract.py"
)


def _load_module(name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(name, file_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


project_contract = _load_module("project_contract_under_test", _PROJECT_CONTRACT_PATH)


@contextmanager
def _scrubbed_env():
    """Scrub the harness env vars so test fixtures do not leak."""
    leaked = (
        "FALKORDB_GRAPH",
        "FALKORDB_GRAPH_DOC",
        "NEO4J_DB",
        "QDRANT_COLLECTION",
        "QDRANT_COLLECTION_DOC",
    )
    originals = {key: os.environ.get(key) for key in leaked}
    for key in leaked:
        os.environ[key] = ""
    try:
        yield
    finally:
        for key, value in originals.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _write_config(directory: Path, project_id: str, **overrides):
    payload = {
        "active": True,
        "project": {"code": project_id, "name": project_id},
        "code": {"env": overrides.pop("code_env", {})},
        "doc": {"env": overrides.pop("doc_env", {})},
    }
    file_path = directory / f"{project_id}.json"
    file_path.write_text(json.dumps(payload), encoding="utf-8")
    return file_path


class _BaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self._scrub = _scrubbed_env()
        self._scrub.__enter__()
        self.addCleanup(self._scrub.__exit__, None, None, None)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_dir = Path(self._tmp.name)

    def write_config(self, project_id: str, **overrides):
        return _write_config(self.config_dir, project_id, **overrides)


class CaseInsensitiveLookupTests(_BaseTest):
    def test_case_variants_return_identical_targets(self) -> None:
        self.write_config("cortext")
        lower = project_contract.resolve_project_targets(
            "cortext", config_dir=self.config_dir
        )
        upper = project_contract.resolve_project_targets(
            "CORTEXT", config_dir=self.config_dir
        )
        self.assertEqual(lower, upper)
        self.assertEqual(lower.doc_graph, "cortext_doc")
        self.assertEqual(lower.doc_qdrant_collection, "cortext_doc")
        self.assertEqual(lower.project_id_normalized, "cortext")


class DefaultNamingTests(_BaseTest):
    def test_default_naming_when_no_doc_env(self) -> None:
        self.write_config("alpha")
        targets = project_contract.resolve_project_targets(
            "alpha", config_dir=self.config_dir
        )
        self.assertEqual(targets.doc_graph, "alpha_doc")
        self.assertEqual(targets.doc_qdrant_collection, "alpha_doc")

    def test_doc_env_overrides_defaults(self) -> None:
        self.write_config(
            "beta",
            doc_env={
                "FALKORDB_GRAPH": "beta_doc_graph",
                "QDRANT_COLLECTION": "beta_doc_q",
            },
        )
        targets = project_contract.resolve_project_targets(
            "beta", config_dir=self.config_dir
        )
        self.assertEqual(targets.doc_graph, "beta_doc_graph")
        self.assertEqual(targets.doc_qdrant_collection, "beta_doc_q")


class UnknownProjectTests(_BaseTest):
    def test_unknown_raises_with_known_list(self) -> None:
        self.write_config("alpha")
        with self.assertRaises(project_contract.ProjectNotRegisteredError) as ctx:
            project_contract.resolve_project_targets(
                "gamma", config_dir=self.config_dir
            )
        self.assertIn("alpha", ctx.exception.known)


class QdrantFilterTests(unittest.TestCase):
    def test_default_filters_by_normalized_project(self) -> None:
        filt = project_contract.qdrant_project_filter("cortext")
        self.assertEqual(
            filt,
            {
                "must": [
                    {
                        "key": "project_id_normalized",
                        "match": {"value": "cortext"},
                    }
                ]
            },
        )

    def test_no_project_id_returns_none(self) -> None:
        # Simplified contract: omit project_id to span every project —
        # the Qdrant filter collapses to ``None``.
        self.assertIsNone(project_contract.qdrant_project_filter(None))
        self.assertIsNone(project_contract.qdrant_project_filter(""))
        # Default mode still scopes by project.
        self.assertIsNotNone(
            project_contract.qdrant_project_filter("cortext")
        )


class ResolveHelperTests(_BaseTest):
    def test_two_projects_have_disjoint_doc_targets(self) -> None:
        self.write_config("alpha")
        self.write_config("beta")
        a = project_contract.resolve_project_targets(
            "alpha", config_dir=self.config_dir
        )
        b = project_contract.resolve_project_targets(
            "beta", config_dir=self.config_dir
        )
        self.assertNotEqual(a.doc_graph, b.doc_graph)
        self.assertNotEqual(a.doc_qdrant_collection, b.doc_qdrant_collection)

    def test_list_registered_projects_returns_sorted_raw_ids(self) -> None:
        self.write_config("alpha")
        self.write_config("beta")
        self.assertEqual(
            sorted(
                project_contract.list_registered_projects(
                    config_dir=self.config_dir
                )
            ),
            ["alpha", "beta"],
        )


if __name__ == "__main__":
    unittest.main()
