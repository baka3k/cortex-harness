import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from mcp_runtime_config import (  # noqa: E402
    load_active_config,
    runtime_environment,
)


class McpRuntimeConfigTests(unittest.TestCase):
    def _write_config(self, root: Path, name: str, payload: dict) -> Path:
        path = root / ".cortext-harness" / "config" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_load_active_config_prefers_explicit_active_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_config(root, "dev", {"active": False, "project": {"code": "old"}})
            expected = self._write_config(
                root,
                "prod",
                {"active": True, "project": {"code": "active-project"}},
            )

            config, path = load_active_config(root)

        self.assertEqual(path, expected)
        self.assertEqual(config["project"]["code"], "active-project")

    def test_code_runtime_environment_uses_active_falkor_and_qdrant_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = self._write_config(
                root,
                "dev",
                {
                    "active": True,
                    "project": {"code": "sample", "name": "Sample"},
                    "code": {
                        "env": {
                            "GRAPH_PROVIDER": "falkordb",
                            "CODE_GRAPH_PROVIDER": "falkordb",
                            "FALKORDB_URI": "redis://graph.internal:6380",
                            "FALKORDB_HOST": "graph.internal",
                            "FALKORDB_PORT": "6380",
                            "FALKORDB_GRAPH": "sample-graph",
                            "NEO4J_URI": "redis://legacy-value:6379",
                            "NEO4J_DB": "legacy-db",
                            "QDRANT_HOST": "qdrant.internal",
                            "QDRANT_PORT": "7333",
                        }
                    },
                },
            )

            env = runtime_environment(root, "code-tiny")

        self.assertEqual(env["GRAPH_PROVIDER"], "falkordb")
        self.assertEqual(env["CODE_GRAPH_PROVIDER"], "falkordb")
        self.assertEqual(env["FALKORDB_GRAPH"], "sample-graph")
        self.assertEqual(env["NEO4J_DB"], "sample-graph")
        self.assertNotIn("NEO4J_URI", env)
        self.assertNotIn("QDRANT_URL", env)
        self.assertNotIn("FALKORDB_URI", env)
        self.assertEqual(env["FALKORDB_PATH"], env["FALKORDB_CODE_PATH"])
        self.assertTrue(env["QDRANT_CODE_PATH"].endswith("/qdrant/code"))
        self.assertEqual(env["PROJECT_ID"], "sample")
        self.assertEqual(env["PROJECT_NAME"], "Sample")
        self.assertEqual(env["CORTEX_HARNESS_CONFIG_PATH"], str(config_path))

    def test_doc_runtime_environment_uses_doc_section_and_scoped_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write_config(
                root,
                "dev",
                {
                    "active": True,
                    "project": {"code": "docs", "name": "Docs"},
                    "doc": {
                        "env": {
                            "GRAPH_PROVIDER": "falkordb",
                            "DOC_GRAPH_PROVIDER": "falkordb",
                            "FALKORDB_GRAPH": "docs-graph",
                            "FALKORDB_HOST": "localhost",
                            "FALKORDB_PORT": "6379",
                            "QDRANT_URL": "https://qdrant.example.test",
                            "QDRANT_COLLECTION": "bespoke-doc-vectors",
                        }
                    },
                },
            )

            env = runtime_environment(root, "doc-tiny")

        self.assertEqual(env["DOC_GRAPH_PROVIDER"], "falkordb")
        self.assertNotIn("CODE_GRAPH_PROVIDER", env)
        self.assertEqual(env["FALKORDB_GRAPH"], "docs-graph")
        self.assertEqual(env["NEO4J_DB"], "docs-graph")
        self.assertNotIn("QDRANT_URL", env)
        self.assertNotIn("FALKORDB_HOST", env)
        self.assertEqual(env["FALKORDB_PATH"], env["FALKORDB_DOC_PATH"])
        self.assertTrue(env["QDRANT_DOC_PATH"].endswith("/qdrant/doc"))
        self.assertEqual(env["QDRANT_COLLECTION_DOC"], "bespoke-doc-vectors")

    def test_runtime_environment_is_empty_when_project_has_no_harness_config(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(runtime_environment(Path(directory), "code-tiny"), {})


if __name__ == "__main__":
    unittest.main()
