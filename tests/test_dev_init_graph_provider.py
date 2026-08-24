import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from click.testing import CliRunner

from cortex_harness.dev import (
    _code_env_for_process,
    _doc_env_for_process,
    _env_to_neo4j_args,
    _mcp_env_from_config,
    _mcp_start_one,
    _neo4j_args_code,
    _run_with_retry,
    cli,
)
from cortex_harness.storage.config import LegacyRemoteConfigurationError


class DevInitGraphProviderTests(unittest.TestCase):
    def test_code_process_environment_normalizes_embedding_aliases(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cfg = {
                "project": {"code": "SHOP"},
                "code": {
                    "env": {
                        "CORTEX_DATA_HOME": temp_dir,
                        "EMBEDDING_MODEL": "fixture-model",
                        "device": "mps",
                        "BATCH_SIZE": "6",
                        "MAX_EMBED_CHARS": "700",
                        "CACHE_DIR": "/tmp/code-cache",
                    }
                },
            }
            env = _code_env_for_process(cfg, Path(temp_dir))
        self.assertEqual(env["CODE_EMBEDDING_MODEL"], "fixture-model")
        self.assertEqual(env["EMBED_DEVICE"], "mps")
        self.assertEqual(env["EMBED_BATCH_SIZE"], "6")
        self.assertEqual(env["MAX_EMBED_CHARS"], "700")
        self.assertEqual(env["QDRANT_CACHE_DIR"], "/tmp/code-cache")
        self.assertEqual(env["CORTEX_STORAGE_PROJECT_ID"], "SHOP")
        self.assertEqual(env["FALKORDB_PATH"], env["FALKORDB_CODE_PATH"])
        self.assertTrue(env["QDRANT_CODE_PATH"].endswith("/qdrant/code"))

    def test_code_process_environment_always_resolves_local_qdrant(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cfg = {
                "project": {"code": "SHOP"},
                "code": {"env": {"GRAPH_PROVIDER": "falkordb", "CORTEX_DATA_HOME": temp_dir}},
            }

            env = _code_env_for_process(cfg, Path(temp_dir))

        self.assertNotIn("QDRANT_URL", env)
        self.assertIn("QDRANT_CODE_PATH", env)

    def test_code_process_environment_rejects_legacy_remote_storage(self):
        cfg = {
            "project": {"code": "SHOP"},
            "code": {"env": {"GRAPH_PROVIDER": "falkordb", "QDRANT_HOST": "localhost"}},
        }

        with self.assertRaises(LegacyRemoteConfigurationError):
            _code_env_for_process(cfg)

    def test_retry_runner_passes_supplied_environment(self):
        with patch("cortex_harness.dev.subprocess.run", return_value=SimpleNamespace(returncode=0)) as run:
            result = _run_with_retry(["python", "worker.py"], env={"EMBED_DEVICE": "cpu"})
        self.assertEqual(result, 0)
        self.assertEqual(run.call_args.kwargs["env"]["EMBED_DEVICE"], "cpu")

    def test_mcp_env_without_config_is_empty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual(_mcp_env_from_config(Path(temp_dir), "code-tiny"), {})

    def test_mcp_start_scrubs_inherited_remote_endpoints_for_local_falkordb(self):
        svc_dir = Path(__file__).resolve().parents[1] / "code-tiny"
        svc = {
            "dir": svc_dir,
            "cmd": ["mcp/unified_mcp.py"],
            "url": "http://127.0.0.1:8788/mcp",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(
                "os.environ",
                {
                    "FALKORDB_URI": "redis://localhost:6379",
                    "FALKORDB_HOST": "localhost",
                    "FALKORDB_PORT": "6379",
                    "QDRANT_URL": "http://localhost:6333",
                    "NEO4J_URI": "bolt://stale.example:7687",
                    "NEO4J_USER": "stale-user",
                    "NEO4J_PASS": "stale-pass",
                    "NEO4J_DB": "stale-graph",
                },
                clear=False,
            ), patch("cortex_harness.dev._venv_python", return_value="/fake/python"), patch(
                "cortex_harness.dev._load_dotenv", return_value={}
            ), patch("cortex_harness.dev.subprocess.Popen") as popen, patch(
                "cortex_harness.dev.MCP_LOG_DIR", Path(temp_dir)
            ):
                popen.return_value = SimpleNamespace(pid=123)
                _mcp_start_one(
                    "code-tiny",
                    svc,
                    extra_env={"GRAPH_PROVIDER": "falkordb", "FALKORDB_PATH": "/tmp/code.rdb"},
                )

        child_env = popen.call_args.kwargs["env"]
        self.assertNotIn("FALKORDB_URI", child_env)
        self.assertNotIn("FALKORDB_HOST", child_env)
        self.assertNotIn("FALKORDB_PORT", child_env)
        self.assertNotIn("QDRANT_URL", child_env)
        self.assertFalse(any(key.startswith("NEO4J_") for key in child_env))

    def test_graph_args_fallback_to_falkordb_hyper_graph(self):
        self.assertIn("--graph-provider", _neo4j_args_code({}))
        self.assertIn("falkordb", _neo4j_args_code({}))
        self.assertIn("--falkordb-graph", _neo4j_args_code({}))
        self.assertIn("hyper_graph", _neo4j_args_code({}))

        self.assertIn("--graph-provider", _env_to_neo4j_args({}))
        self.assertIn("falkordb", _env_to_neo4j_args({}))
        self.assertIn("--falkordb-graph", _env_to_neo4j_args({}))
        self.assertIn("hyper_graph", _env_to_neo4j_args({}))

    def test_graph_args_use_local_path_without_remote_falkordb_flags(self):
        env = {
            "GRAPH_PROVIDER": "falkordb",
            "FALKORDB_PATH": "/tmp/falkor/code.rdb",
            "FALKORDB_GRAPH": "shop",
            "NEO4J_URI": "bolt://stale.example:7687",
            "NEO4J_DB": "stale",
        }
        for args in (_neo4j_args_code(env), _env_to_neo4j_args(env)):
            self.assertIn("--falkordb-path", args)
            self.assertNotIn("--falkordb-uri", args)
            self.assertNotIn("--falkordb-host", args)
            self.assertNotIn("--neo4j-uri", args)

    def test_invalid_graph_provider_fails_closed_instead_of_selecting_neo4j(self):
        with self.assertRaisesRegex(ValueError, "GRAPH_PROVIDER.*falkordb.*neo4j"):
            _neo4j_args_code({"GRAPH_PROVIDER": "falkord"})

    def test_falkordb_process_environment_drops_all_neo4j_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cfg = {
                "project": {"code": "SHOP"},
                "code": {
                    "env": {
                        "GRAPH_PROVIDER": "falkordb",
                        "CODE_GRAPH_PROVIDER": "falkordb",
                        "CORTEX_DATA_HOME": temp_dir,
                        "NEO4J_URI": "bolt://stale.example:7687",
                        "NEO4J_USER": "stale-user",
                        "NEO4J_PASS": "stale-pass",
                        "NEO4J_DB": "stale-graph",
                    }
                },
            }

            env = _code_env_for_process(cfg, Path(temp_dir))

        self.assertEqual(env["FALKORDB_GRAPH"], "SHOP")
        self.assertFalse(any(key.startswith("NEO4J_") for key in env))

    def test_graph_args_keep_neo4j_only_when_explicitly_selected(self):
        env = {
            "GRAPH_PROVIDER": "neo4j",
            "NEO4J_URI": "bolt://graph.example:7687",
            "NEO4J_USER": "neo4j",
            "NEO4J_PASS": "secret",
            "NEO4J_DB": "shop",
        }
        args = _neo4j_args_code(env)
        self.assertIn("--neo4j-uri", args)
        self.assertNotIn("--falkordb-path", args)

    def test_doc_process_environment_preserves_explicit_neo4j_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cfg = {
                "project": {"code": "SHOP"},
                "doc": {"env": {
                    "GRAPH_PROVIDER": "neo4j",
                    "DOC_GRAPH_PROVIDER": "neo4j",
                    "NEO4J_URI": "bolt://graph.example:7687",
                    "NEO4J_DB": "docs_archive",
                    "NEO4J_USER": "neo4j",
                    "NEO4J_PASS": "secret",
                    "FALKORDB_GRAPH": "stale-doc-graph",
                    "FALKORDB_PATH": "/tmp/stale-doc.rdb",
                    "CORTEX_DATA_HOME": temp_dir,
                }},
            }

            env = _doc_env_for_process(cfg, Path(temp_dir))

        self.assertEqual(env["NEO4J_URI"], "bolt://graph.example:7687")
        self.assertEqual(env["NEO4J_DB"], "docs_archive")
        self.assertFalse(any(key.startswith("FALKORDB_") for key in env))
        self.assertIn("--neo4j-uri", _env_to_neo4j_args(env))
        self.assertNotIn("--falkordb-path", _env_to_neo4j_args(env))

    def test_init_defaults_to_falkordb_graph_provider(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            result = runner.invoke(cli, ["init", str(project_path)], input="\n" * 60)

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertNotIn("Code - Neo4j", result.output)
            self.assertNotIn("Code — Neo4j", result.output)
            self.assertIn("Code — Graph + Qdrant + Embedding", result.output)
            self.assertIn("Local storage", result.output)
            self.assertNotIn("FALKORDB_HOST", result.output)
            self.assertNotIn("QDRANT_HOST", result.output)

            config_path = project_path / ".cortext-harness" / "config" / "dev.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))

            code_env = config["code"]["env"]
            doc_env = config["doc"]["env"]
            self.assertEqual(config["storage_backend"], "local")
            self.assertNotIn("remote", config)
            self.assertEqual(code_env["GRAPH_PROVIDER"], "falkordb")
            self.assertEqual(code_env["CODE_GRAPH_PROVIDER"], "falkordb")
            self.assertEqual(code_env["FALKORDB_GRAPH"], "my_project")
            self.assertEqual(code_env["CORTEX_STORAGE_INSTANCE"], "default")
            self.assertEqual(code_env["QDRANT_COLLECTION"], "my_project")
            self.assertEqual(doc_env["GRAPH_PROVIDER"], "falkordb")
            self.assertEqual(doc_env["DOC_GRAPH_PROVIDER"], "falkordb")
            self.assertEqual(doc_env["FALKORDB_GRAPH"], "my_project_doc")
            self.assertFalse(set(code_env) & {
                "QDRANT_URL", "QDRANT_HOST", "QDRANT_PORT",
                "FALKORDB_URI", "FALKORDB_HOST", "FALKORDB_PORT",
            })

            mcp_env = _mcp_env_from_config(project_path, "code-tiny")
            self.assertEqual(mcp_env["QDRANT_COLLECTION"], "my_project")
            self.assertEqual(mcp_env["QDRANT_COLLECTION_CODE"], "my_project")
            self.assertEqual(mcp_env["FALKORDB_PATH"], mcp_env["FALKORDB_CODE_PATH"])
            self.assertIn("QDRANT_CODE_PATH", mcp_env)
            self.assertNotIn("QDRANT_URL", mcp_env)
            self.assertEqual(mcp_env["EMBED_MODEL"], code_env["EMBEDDING_MODEL"])

    def test_init_accepts_custom_qdrant_collection(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            # prompt order: project code, project name, storage backend,
            # CORTEX_STORAGE_INSTANCE, CORTEX_DATA_HOME, code GRAPH_PROVIDER,
            # code FALKORDB_GRAPH, code QDRANT_COLLECTION, …
            input_lines = ["SHOP", "", "", "", "", "", "", "shop_vectors"] + [""] * 60
            result = runner.invoke(cli, ["init", str(project_path)], input="\n".join(input_lines))

            self.assertEqual(result.exit_code, 0, result.output)

            config_path = project_path / ".cortext-harness" / "config" / "dev.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            code_env = config["code"]["env"]

            self.assertEqual(config["project"]["code"], "SHOP")
            self.assertEqual(config["storage_backend"], "local")
            self.assertNotIn("remote", config)
            self.assertEqual(code_env["QDRANT_COLLECTION"], "shop_vectors")
            self.assertEqual(_mcp_env_from_config(project_path, "code-tiny")["QDRANT_COLLECTION"], "shop_vectors")

    def test_init_serializes_instance_and_optional_data_root_only(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            data_home = project_path / "storage"
            # prompt order: project code, project name, storage backend,
            # CORTEX_STORAGE_INSTANCE, CORTEX_DATA_HOME, …
            input_lines = ["", "", "", "team-a", str(data_home)] + [""] * 60
            result = runner.invoke(cli, ["init", str(project_path)], input="\n".join(input_lines))

            self.assertEqual(result.exit_code, 0, result.output)
            config_path = project_path / ".cortext-harness" / "config" / "dev.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(config["storage_backend"], "local")
        for section in ("code", "doc"):
            env = config[section]["env"]
            self.assertEqual(env["CORTEX_STORAGE_INSTANCE"], "team-a")
            self.assertEqual(env["CORTEX_DATA_HOME"], str(data_home))
            self.assertNotIn("QDRANT_PATH", env)
            self.assertNotIn("FALKORDB_PATH", env)

    def test_init_defaults_code_qdrant_and_graph_db_to_project_code(self):
        runner = CliRunner()

        with tempfile.TemporaryDirectory() as temp_dir:
            project_path = Path(temp_dir)
            # prompt order: project code, project name, storage backend,
            # CORTEX_STORAGE_INSTANCE, CORTEX_DATA_HOME, code GRAPH_PROVIDER,
            # code NEO4J_URI/DB/USER/PASS (4), code QDRANT_COLLECTION, …
            input_lines = ["SHOP", "Shop Project", "", "", "", "neo4j"] + [""] * 60
            result = runner.invoke(
                cli,
                ["init", str(project_path)],
                input="\n".join(input_lines),
            )

            self.assertEqual(result.exit_code, 0, result.output)

            config_path = project_path / ".cortext-harness" / "config" / "dev.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            code_env = config["code"]["env"]

            self.assertEqual(config["project"]["code"], "SHOP")
            self.assertEqual(config["project"]["name"], "Shop Project")
            self.assertEqual(config["storage_backend"], "local")
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
