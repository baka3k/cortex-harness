"""Unit tests for harness_config.py — Phase 06 contract.

Run from the repo root::

    PYTHONPATH=code-tiny python -m unittest \
        code-tiny.tools.common.test_harness_config
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
from unittest import mock


_PROJECT_SCOPE_PATH = (
    Path(__file__).resolve().parents[0] / "project_scope.py"
)
_PROJECT_REGISTRY_PATH = (
    Path(__file__).resolve().parents[0] / "project_registry.py"
)
_HARNESS_CONFIG_PATH = (
    Path(__file__).resolve().parents[0] / "harness_config.py"
)


def _load_module(name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(name, file_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load_module("tools.common.project_scope", _PROJECT_SCOPE_PATH)
_load_module("tools.common.project_registry", _PROJECT_REGISTRY_PATH)
harness_config = _load_module("harness_config_under_test", _HARNESS_CONFIG_PATH)


@contextmanager
def _scrubbed_env():
    """Scrub env vars that ``load_harness_config`` may set so tests are isolated."""
    leaked = (
        "CORTEX_DATA_HOME",
        "CORTEX_STORAGE_INSTANCE",
        "CORTEX_CODE_STORAGE_OWNER",
        "CORTEX_DOC_STORAGE_OWNER",
        "CORTEX_STORAGE_OWNER",
        "QDRANT_PATH",
        "QDRANT_CODE_PATH",
        "QDRANT_DOC_PATH",
        "FALKORDB_PATH",
        "FALKORDB_CODE_PATH",
        "FALKORDB_DOC_PATH",
        "NEO4J_URI",
        "NEO4J_USER",
        "NEO4J_PASS",
        "NEO4J_DB",
        "FALKORDB_GRAPH",
        "FALKORDB_DATABASE",
        "QDRANT_URL",
        "QDRANT_COLLECTION",
        "QDRANT_COLLECTION_DOC",
        "QDRANT_COLLECTION_CODE",
        "CODE_EMBEDDING_MODEL",
        "EMBED_MODEL",
        "EMBED_DEVICE",
        "EMBED_BATCH_SIZE",
        "MAX_EMBED_CHARS",
        "GRAPH_PROVIDER",
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


class _BaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self._scrub = _scrubbed_env()
        self._scrub.__enter__()
        self.addCleanup(self._scrub.__exit__, None, None, None)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_dir = Path(self._tmp.name)

    def write_dev(self, project_id: str, **overrides) -> Path:
        payload = {
            "active": True,
            "project": {"code": project_id, "name": project_id},
            "code": {"env": overrides.pop("code_env", {})},
            "doc": {"env": overrides.pop("doc_env", {})},
        }
        path = self.config_dir / "dev.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path


class LoadHarnessConfigTests(_BaseTest):
    def test_loads_code_and_doc_env_vars(self) -> None:
        path = self.write_dev(
            "cortext",
            code_env={
                "FALKORDB_GRAPH": "cortext",
                "QDRANT_COLLECTION": "cortext",
                "NEO4J_USER": "neo4j",
                "GRAPH_PROVIDER": "falkordb",
            },
            doc_env={
                "FALKORDB_GRAPH": "cortext_doc",
                "QDRANT_COLLECTION": "cortext_doc",
            },
        )
        harness_config.load_harness_config(str(path))

        self.assertEqual(os.environ["FALKORDB_GRAPH"], "cortext")
        self.assertEqual(os.environ["QDRANT_COLLECTION"], "cortext")
        self.assertEqual(os.environ["QDRANT_COLLECTION_DOC"], "cortext_doc")
        self.assertEqual(os.environ["NEO4J_USER"], "neo4j")
        self.assertEqual(os.environ["GRAPH_PROVIDER"], "falkordb")

    def test_doc_collection_defaults_to_project_doc_naming(self) -> None:
        # No doc.env override — the loader should derive
        # ``{project_id}_doc`` per the unified contract naming rule.
        path = self.write_dev("alpha")
        harness_config.load_harness_config(str(path))
        self.assertEqual(os.environ["QDRANT_COLLECTION_DOC"], "alpha_doc")

    def test_existing_env_wins(self) -> None:
        path = self.write_dev(
            "alpha", code_env={"FALKORDB_GRAPH": "from_config"}
        )
        os.environ["FALKORDB_GRAPH"] = "from_shell"
        try:
            harness_config.load_harness_config(str(path))
            self.assertEqual(os.environ["FALKORDB_GRAPH"], "from_shell")
        finally:
            os.environ.pop("FALKORDB_GRAPH", None)


class LoadHarnessTargetsTests(_BaseTest):
    def test_returns_registry_derived_targets(self) -> None:
        path = self.write_dev("cortext")
        # The harness_config loader consults the registry; the registry
        # reads ``.cortext-harness/config/*.json`` from CWD. Copy our test
        # dev.json into the registry's expected location so it is found.
        registry_dir = self.config_dir / ".cortext-harness" / "config"
        registry_dir.mkdir(parents=True)
        (registry_dir / "cortext.json").write_text(
            json.dumps(
                {
                    "project": {"code": "cortext", "name": "cortext"},
                    "code": {"env": {"FALKORDB_GRAPH": "cortext"}},
                    "doc": {"env": {"FALKORDB_GRAPH": "cortext_doc"}},
                }
            ),
            encoding="utf-8",
        )

        # Run from the test config_dir so the registry discovers it.
        old_cwd = os.getcwd()
        os.chdir(self.config_dir)
        try:
            targets = harness_config.load_harness_targets(str(path))
        finally:
            os.chdir(old_cwd)

        self.assertIsNotNone(targets)
        self.assertEqual(targets["code_graph"], "cortext")
        self.assertEqual(targets["doc_graph"], "cortext_doc")
        self.assertEqual(targets["doc_qdrant_collection"], "cortext_doc")


if __name__ == "__main__":
    unittest.main()
