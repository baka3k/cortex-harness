import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

from cortex_harness.dev import (
    _code_env_for_process,
    _configured_qdrant_url,
    _env_to_neo4j_args,
    _mcp_env_from_config,
    _neo4j_args_code,
    _run_with_retry,
    cli,
)


class DevInitGraphProviderTests(unittest.TestCase):
    def test_code_process_environment_normalizes_embedding_aliases(self):
        cfg = {
            "project": {"code": "SHOP"},
            "code": {
                "env": {
                    "QDRANT_HOST": "localhost",
                    "QDRANT_PORT": "6333",
                    "EMBEDDING_MODEL": "fixture-model",
                    "device": "mps",
                    "BATCH_SIZE": "6",
                    "MAX_EMBED_CHARS": "700",
                    "CACHE_DIR": "/tmp/code-cache",
                }
            },
        }
        env = _code_env_for_process(cfg)
        self.assertEqual(env["CODE_EMBEDDING_MODEL"], "fixture-model")
        self.assertEqual(env["EMBED_DEVICE"], "mps")
        self.assertEqual(env["EMBED_BATCH_SIZE"], "6")
        self.assertEqual(env["MAX_EMBED_CHARS"], "700")
        self.assertEqual(env["QDRANT_CACHE_DIR"], "/tmp/code-cache")

    def test_code_process_environment_does_not_enable_unconfigured_qdrant(self):
        cfg = {"project": {"code": "SHOP"}, "code": {"env": {"GRAPH_PROVIDER": "falkordb"}}}

        env = _code_env_for_process(cfg)

        self.assertNotIn("QDRANT_URL", env)
        self.assertEqual(_configured_qdrant_url(cfg["code"]["env"]), "")

    def test_retry_runner_passes_supplied_environment(self):
        with patch("cortex_harness.dev.subprocess.run", return_value=SimpleNamespace(returncode=0)) as run:
            result = _run_with_retry(["python", "worker.py"], env={"EMBED_DEVICE": "cpu"})
        self.assertEqual(result, 0)
        self.assertEqual(run.call_args.kwargs["env"]["EMBED_DEVICE"], "cpu")

    def test_mcp_env_without_config_is_empty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual(_mcp_env_from_config(Path(temp_dir), "code-tiny"), {})

    def test_graph_args_fallback_to_falkordb_hyper_graph(self):
        self.assertIn("--graph-provider", _neo4j_args_code({}))
        self.assertIn("falkordb", _neo4j_args_code({}))
        self.assertIn("--falkordb-graph", _neo4j_args_code({}))
        self.assertIn("hyper_graph", _neo4j_args_code({}))

        self.assertIn("--graph-provider", _env_to_neo4j_args({}))
        self.assertIn("falkordb", _env_to_neo4j_args({}))
        self.assertIn("--falkordb-graph", _env_to_neo4j_args({}))
        self.assertIn("hyper_graph", _env_to_neo4j_args({}))

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
