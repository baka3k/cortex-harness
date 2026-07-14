import json
import tempfile
import unittest
from pathlib import Path

from click.testing import CliRunner

from cortex_harness.dev import cli, _mcp_env_from_config


class DevInitGraphProviderTests(unittest.TestCase):
    def test_mcp_env_without_config_is_empty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual(_mcp_env_from_config(Path(temp_dir), "code-tiny"), {})

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
            self.assertEqual(code_env["QDRANT_COLLECTION"], "my_project")
            self.assertEqual(doc_env["GRAPH_PROVIDER"], "falkordb")
            self.assertEqual(doc_env["DOC_GRAPH_PROVIDER"], "falkordb")

            mcp_env = _mcp_env_from_config(project_path, "code-tiny")
            self.assertEqual(mcp_env["QDRANT_COLLECTION"], "my_project")
            self.assertEqual(mcp_env["QDRANT_COLLECTION_CODE"], "my_project")
            self.assertEqual(mcp_env["QDRANT_URL"], "http://localhost:6333")
            self.assertEqual(mcp_env["EMBED_MODEL"], code_env["EMBEDDING_MODEL"])

    def test_init_accepts_custom_qdrant_collection(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            input_lines = ["SHOP", ""] + [""] * 10 + ["shop_vectors"] + [""] * 60
            result = runner.invoke(cli, ["init", str(project_path)], input="\n".join(input_lines))

            self.assertEqual(result.exit_code, 0, result.output)

            config_path = project_path / ".cortext-harness" / "config" / "dev.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            code_env = config["code"]["env"]

            self.assertEqual(config["project"]["code"], "SHOP")
            self.assertEqual(code_env["QDRANT_COLLECTION"], "shop_vectors")
            self.assertEqual(_mcp_env_from_config(project_path, "code-tiny")["QDRANT_COLLECTION"], "shop_vectors")

    def test_init_defaults_code_qdrant_and_graph_db_to_project_code(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            result = runner.invoke(
                cli,
                ["init", str(project_path)],
                input="\n".join(["SHOP", "Shop Project", "neo4j"] + [""] * 60),
            )

            self.assertEqual(result.exit_code, 0, result.output)

            config_path = project_path / ".cortext-harness" / "config" / "dev.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            code_env = config["code"]["env"]

            self.assertEqual(config["project"]["code"], "SHOP")
            self.assertEqual(config["project"]["name"], "Shop Project")
            self.assertEqual(code_env["QDRANT_COLLECTION"], "SHOP")
            self.assertEqual(code_env["NEO4J_DB"], "SHOP")
            self.assertEqual(_mcp_env_from_config(project_path, "code-tiny")["QDRANT_COLLECTION"], "SHOP")

    def test_init_dot_defaults_code_source_to_resolved_current_directory(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            with runner.isolated_filesystem(temp_dir):
                project_path = Path.cwd()
                result = runner.invoke(cli, ["init", "."], input="\n" * 60)

                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn(f"[{project_path}]", result.output)

                config_path = project_path / ".cortext-harness" / "config" / "dev.json"
                config = json.loads(config_path.read_text(encoding="utf-8"))
                code_projects = config["code"]["source"]["projects"]

                self.assertEqual(code_projects[0]["git"], "")
                self.assertEqual(code_projects[0]["folder"], [str(project_path)])


if __name__ == "__main__":
    unittest.main()
