import json
import tempfile
import unittest
from pathlib import Path

from click.testing import CliRunner

from cortex_harness.dev import cli


class DevInitGraphProviderTests(unittest.TestCase):
    def test_init_defaults_to_falkordb_graph_provider(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            result = runner.invoke(cli, ["init", str(project_path)], input="\n" * 60)

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertNotIn("Code - Neo4j", result.output)
            self.assertNotIn("Code — Neo4j", result.output)
            self.assertIn("Code — Graph + Qdrant + Embedding", result.output)
            self.assertIn("FALKORDB_HOST", result.output)

            config_path = project_path / ".cortext-harness" / "config" / "dev.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))

            code_env = config["code"]["env"]
            doc_env = config["doc"]["env"]
            self.assertEqual(code_env["GRAPH_PROVIDER"], "falkordb")
            self.assertEqual(code_env["CODE_GRAPH_PROVIDER"], "falkordb")
            self.assertEqual(code_env["FALKORDB_HOST"], "localhost")
            self.assertEqual(code_env["FALKORDB_GRAPH"], "my_project")
            self.assertEqual(doc_env["GRAPH_PROVIDER"], "falkordb")
            self.assertEqual(doc_env["DOC_GRAPH_PROVIDER"], "falkordb")


if __name__ == "__main__":
    unittest.main()
