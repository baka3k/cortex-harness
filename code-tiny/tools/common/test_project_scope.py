"""Unit tests for project_scope helpers.

Run from the repo root with::

    PYTHONPATH=code-tiny python -m unittest \
        code-tiny.tools.common.test_project_scope
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


_PROJECT_SCOPE_PATH = (
    Path(__file__).resolve().parents[0] / "project_scope.py"
)


def _load_project_scope():
    spec = importlib.util.spec_from_file_location(
        "project_scope_under_test", _PROJECT_SCOPE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["project_scope_under_test"] = module
    spec.loader.exec_module(module)
    return module


project_scope = _load_project_scope()


class QdrantFilterTests(unittest.TestCase):
    """``qdrant_project_filter`` scopes by project_id when set, returns
    ``None`` (full search) when the id is empty."""

    def test_filters_by_project(self) -> None:
        filt = project_scope.qdrant_project_filter("cortext")
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
        # Missing project_id is the implicit full-search path — no filter.
        self.assertIsNone(project_scope.qdrant_project_filter(None))
        self.assertIsNone(project_scope.qdrant_project_filter(""))
        self.assertIsNone(project_scope.qdrant_project_filter("   "))


class MatchesProjectScopeTests(unittest.TestCase):
    """``matches_project_scope`` returns True for every candidate when
    ``project_id`` is missing."""

    def test_matching_project(self) -> None:
        candidate = {"project_id_normalized": "projA", "name": "x"}
        self.assertTrue(project_scope.matches_project_scope(candidate, "ProjA"))

    def test_non_matching_project(self) -> None:
        candidate = {"project_id_normalized": "projB", "name": "x"}
        self.assertFalse(project_scope.matches_project_scope(candidate, "ProjA"))

    def test_no_project_id_always_matches(self) -> None:
        # Missing project_id is the implicit full-search path — every
        # candidate passes.
        candidate = {"project_id_normalized": "projB", "name": "x"}
        self.assertTrue(project_scope.matches_project_scope(candidate, None))
        self.assertTrue(project_scope.matches_project_scope(candidate, ""))


class PrepareParametersTests(unittest.TestCase):
    """``prepare_project_scope_parameters`` adds ``*_normalized`` keys but
    no longer sets a ``search_full`` flag."""

    def test_adds_normalized_key(self) -> None:
        params = project_scope.prepare_project_scope_parameters(
            "MATCH (n)", {"project_id": "cortext"}
        )
        self.assertEqual(params["project_id_normalized"], "cortext")

    def test_does_not_set_search_full(self) -> None:
        params = project_scope.prepare_project_scope_parameters(
            "MATCH (n)", {"project_id": "cortext"}
        )
        self.assertNotIn("search_full", params)

    def test_empty_params(self) -> None:
        params = project_scope.prepare_project_scope_parameters("MATCH (n)", None)
        self.assertNotIn("search_full", params)
        self.assertNotIn("project_id_normalized", params)


if __name__ == "__main__":
    unittest.main()