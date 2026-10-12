"""Phase 02 — the doc-side ``project_contract`` facade agrees with the registry.

One parser behavior for ``.cortext-harness/config/*.json``: the doc-side
facade delegates name derivation to ``tools.common.project_registry`` and
both trees resolve identical ``ProjectTargets``. Also pins the dual-key doc
collection precedence (``QDRANT_COLLECTION_DOC`` first, legacy
``QDRANT_COLLECTION`` second) in both the delegated and the standalone
fallback path.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
DOC_TINY = ROOT / "doc-tiny"
for path in (str(DOC_TINY), str(ROOT), str(ROOT / "code-tiny")):
    if path not in sys.path:
        sys.path.insert(0, path)

import project_contract  # the same module instance mcp_graph_rag imports
from tools.common import project_registry


_PROJECT_CONTRACT_PATH = DOC_TINY / "project_contract.py"


@contextmanager
def _standalone_project_contract():
    """Load a fresh ``project_contract`` with the code registry unimportable.

    Exercises the standalone fallback parser (used when doc-tiny runs
    without ``code-tiny`` on sys.path).
    """
    with patch.dict(
        sys.modules,
        {
            "tools": None,
            "tools.common": None,
            "tools.common.project_registry": None,
        },
    ):
        spec = importlib.util.spec_from_file_location(
            "project_contract_standalone_under_test", _PROJECT_CONTRACT_PATH
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        yield module
    sys.modules.pop("project_contract_standalone_under_test", None)


class _ConfigFixture(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_dir = Path(self._tmp.name)

    def write_config(self, name: str, payload: dict) -> Path:
        path = self.config_dir / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def write_project(self, project_id: str, doc_env: dict) -> Path:
        return self.write_config(
            project_id,
            {
                "active": True,
                "project": {"code": project_id, "name": project_id},
                "code": {"env": {}},
                "doc": {"env": doc_env},
            },
        )


class DualKeyPrecedenceTests(_ConfigFixture):
    """AC1..AC3 — the doc-collection env key is read with explicit precedence."""

    def test_new_key_resolved_by_both_trees(self):
        self.write_project("alpha", {"QDRANT_COLLECTION_DOC": "custom_coll"})
        for resolver in (
            project_registry.resolve_project_targets,
            project_contract.resolve_project_targets,
        ):
            targets = resolver("alpha", config_dir=self.config_dir)
            self.assertEqual(targets.doc_qdrant_collection, "custom_coll")

    def test_legacy_key_still_resolved(self):
        self.write_project("beta", {"QDRANT_COLLECTION": "legacy_coll"})
        for resolver in (
            project_registry.resolve_project_targets,
            project_contract.resolve_project_targets,
        ):
            targets = resolver("beta", config_dir=self.config_dir)
            self.assertEqual(targets.doc_qdrant_collection, "legacy_coll")

    def test_new_key_wins_over_legacy(self):
        self.write_project(
            "gamma",
            {"QDRANT_COLLECTION_DOC": "new_coll", "QDRANT_COLLECTION": "old_coll"},
        )
        for resolver in (
            project_registry.resolve_project_targets,
            project_contract.resolve_project_targets,
        ):
            targets = resolver("gamma", config_dir=self.config_dir)
            self.assertEqual(targets.doc_qdrant_collection, "new_coll")

    def test_standalone_fallback_reads_both_keys(self):
        self.write_project("delta", {"QDRANT_COLLECTION_DOC": "doc_key"})
        self.write_project("epsilon", {"QDRANT_COLLECTION": "legacy_key"})
        with _standalone_project_contract() as standalone:
            self.assertEqual(
                standalone.resolve_project_targets(
                    "delta", config_dir=self.config_dir
                ).doc_qdrant_collection,
                "doc_key",
            )
            self.assertEqual(
                standalone.resolve_project_targets(
                    "epsilon", config_dir=self.config_dir
                ).doc_qdrant_collection,
                "legacy_key",
            )


class FacadeAgreementTests(_ConfigFixture):
    """AC4 — both trees return equal targets for every registered id."""

    def test_targets_agree_across_fixture_corpus(self):
        self.write_project("zeta", {"FALKORDB_GRAPH": "zeta_docs"})
        self.write_project("eta", {})
        self.write_project("Theta", {"QDRANT_COLLECTION_DOC": "theta_custom"})
        # A malformed config file must be skipped identically by both sides.
        (self.config_dir / "broken.json").write_text("{not json", encoding="utf-8")

        registered = project_contract.list_registered_projects(
            config_dir=self.config_dir
        )
        self.assertEqual(
            registered,
            project_registry.list_registered_projects(config_dir=self.config_dir),
        )
        self.assertEqual(sorted(registered), ["Theta", "eta", "zeta"])
        for project_id in registered:
            doc_targets = project_contract.resolve_project_targets(
                project_id, config_dir=self.config_dir
            )
            code_targets = project_registry.resolve_project_targets(
                project_id, config_dir=self.config_dir
            )
            self.assertEqual(doc_targets, code_targets)

    def test_unregistered_error_agrees(self):
        self.write_project("iota", {})
        for resolver in (
            project_registry.resolve_project_targets,
            project_contract.resolve_project_targets,
        ):
            with self.assertRaises(KeyError):
                resolver("missing", config_dir=self.config_dir)


if __name__ == "__main__":
    unittest.main()
