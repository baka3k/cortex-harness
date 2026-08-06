import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch


def _load_module(filename: str):
    path = Path(__file__).resolve().parents[1] / filename
    spec = importlib.util.spec_from_file_location(f"test_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class LegacyNeo4jIsolationTests(unittest.TestCase):
    def test_loader_has_no_import_time_driver_and_requires_opt_in(self):
        module = _load_module("neo4j_loader.py")
        self.assertFalse(hasattr(module, "neo4j_driver"))
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(module.LEGACY_OPT_IN_ENV, None)
            with self.assertRaisesRegex(RuntimeError, "legacy Neo4j-only utility"):
                module.create_legacy_clients()

    def test_openai_helper_has_no_graph_driver_dependency(self):
        module = _load_module("open_ai_exec.py")
        self.assertFalse(hasattr(module, "GraphDatabase"))
        self.assertFalse(hasattr(module, "QdrantNeo4jRetriever"))


if __name__ == "__main__":
    unittest.main()
