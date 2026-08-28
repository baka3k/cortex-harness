import argparse
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CODE_TINY = os.path.join(REPO_ROOT, "code-tiny")
if CODE_TINY not in sys.path:
    sys.path.insert(0, CODE_TINY)

from tools.graph.core.base import GraphProvider
from tools.graph.core.factory import GraphDriverFactory, GraphWritesDisabledError
from tools.graph.cli import prepare_graph_args
from tools.common.harness_config import load_harness_config
from tools.sync import incremental_sync


class FakeGraphDriver:
    def __init__(self, *, fail_schema=False):
        self.indexes = []
        self.queries = []
        self.closed = False
        self.fail_schema = fail_schema

    async def create_indexes(self, indexes, database=None):
        if self.fail_schema:
            raise RuntimeError("schema rejected")
        self.indexes.append((indexes, database))

    async def inspect_indexes(self, database=None):
        indexes = [index for invocation, _database in self.indexes for index in invocation]
        return [
            {
                "label": index["label"],
                "properties": (
                    index["property"]
                    if isinstance(index["property"], list)
                    else [index["property"]]
                ),
                "index_type": index.get("type", "range"),
                "entity_type": "node",
                "status": "OPERATIONAL",
            }
            for index in indexes
        ]

    async def execute_query(self, query, parameters=None, database=None):
        self.queries.append((query, parameters, database))
        return [], [], None

    def close(self):
        self.closed = True


class IncrementalSyncGraphSetupTests(unittest.IsolatedAsyncioTestCase):
    def test_explicit_local_target_overrides_remote_project_environment(self):
        with patch.dict(
            os.environ,
            {"FALKORDB_URI": "localhost:6379"},
            clear=False,
        ):
            args = incremental_sync.parse_args(
                [
                    "--root",
                    ".",
                    "--project-id",
                    "demo",
                    "--falkordb-path",
                    "/tmp/demo.rdb",
                    "--falkordb-graph",
                    "demo-canary",
                ]
            )
            self.assertTrue(prepare_graph_args(args))
            child_env = incremental_sync._build_analyzer_env(args)

        self.assertIsNone(args.falkordb_uri)
        self.assertEqual(args.falkordb_path, "/tmp/demo.rdb")
        self.assertNotIn("FALKORDB_URI", child_env)
        self.assertEqual(child_env["FALKORDB_PATH"], "/tmp/demo.rdb")
        self.assertEqual(
            incremental_sync._graph_target_cli_args(args),
            [
                "--graph-provider",
                "falkordb",
                "--falkordb-path",
                "/tmp/demo.rdb",
                "--falkordb-graph",
                "demo-canary",
            ],
        )

    def test_explicit_local_and_remote_targets_are_rejected(self):
        with self.assertRaises(SystemExit):
            incremental_sync.parse_args(
                [
                    "--root",
                    ".",
                    "--falkordb-path",
                    "/tmp/demo.rdb",
                    "--falkordb-uri",
                    "localhost:6379",
                ]
            )

    def test_remote_uri_credentials_stay_out_of_child_argv(self):
        uri = "redis://alice:secret@graph.internal:6379"
        args = incremental_sync.parse_args(
            [
                "--root",
                ".",
                "--project-id",
                "demo",
                "--falkordb-uri",
                uri,
                "--falkordb-graph",
                "demo-canary",
            ]
        )
        self.assertTrue(prepare_graph_args(args))

        child_env = incremental_sync._build_analyzer_env(args)
        child_argv = incremental_sync._graph_target_cli_args(args)

        self.assertEqual(child_env["FALKORDB_URI"], uri)
        self.assertNotIn("--falkordb-uri", child_argv)
        self.assertFalse(any("secret" in value for value in child_argv))
        self.assertEqual(
            child_argv,
            [
                "--graph-provider",
                "falkordb",
                "--falkordb-graph",
                "demo-canary",
            ],
        )

    def test_no_graph_sanitizes_analyzer_environment(self):
        args = incremental_sync.parse_args(
            ["--root", ".", "--project-id", "demo", "--no-graph"]
        )
        with patch.dict(
            os.environ,
            {
                "CODE_GRAPH_PROVIDER": "falkordb",
                "GRAPH_PROVIDER": "falkordb",
                "FALKORDB_PATH": "/user/store.rdb",
                "FALKORDB_GRAPH": "user-graph",
                "NEO4J_URI": "bolt://user-db",
            },
            clear=False,
        ):
            env = incremental_sync._build_analyzer_env(args)

        self.assertEqual(env["CODE_GRAPH_PROVIDER"], "neo4j")
        self.assertEqual(env["GRAPH_PROVIDER"], "neo4j")
        self.assertEqual(env["CORTEX_DISABLE_GRAPH"], "1")
        self.assertNotIn("FALKORDB_PATH", env)
        self.assertNotIn("FALKORDB_GRAPH", env)
        self.assertNotIn("NEO4J_URI", env)

    def test_no_graph_survives_child_project_config_loading(self):
        args = incremental_sync.parse_args(
            ["--root", ".", "--project-id", "demo", "--no-graph"]
        )
        child_env = incremental_sync._build_analyzer_env(args)
        with tempfile.TemporaryDirectory() as root:
            config = Path(root) / ".cortext-harness" / "config" / "dev.json"
            config.parent.mkdir(parents=True)
            config.write_text(
                json.dumps(
                    {
                        "code": {
                            "env": {
                                "NEO4J_URI": "bolt://configured-db",
                                "NEO4J_USER": "neo4j",
                                "NEO4J_PASS": "secret",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, child_env, clear=True):
                load_harness_config(str(config))
                analyzer_args = argparse.Namespace(
                    graph_provider="neo4j",
                    neo4j_uri=os.environ.get("NEO4J_URI"),
                    neo4j_user=os.environ.get("NEO4J_USER"),
                    neo4j_password=os.environ.get("NEO4J_PASS"),
                    project_id=None,
                )
                self.assertFalse(prepare_graph_args(analyzer_args))

    async def test_no_graph_blocks_helper_and_direct_factory_construction(self):
        args = argparse.Namespace(
            graph_provider="neo4j",
            neo4j_uri="bolt://configured-db",
            neo4j_user="neo4j",
            neo4j_password="secret",
            neo4j_db="code",
            project_id=None,
        )
        with patch.dict(os.environ, {"CORTEX_DISABLE_GRAPH": "1"}, clear=False), patch.object(
            GraphDriverFactory, "create_driver", new_callable=AsyncMock
        ) as create_driver:
            self.assertIsNone(await incremental_sync.create_graph_driver_from_args(args))
            create_driver.assert_not_awaited()

        with patch.dict(os.environ, {"CORTEX_DISABLE_GRAPH": "1"}, clear=False):
            with self.assertRaises(GraphWritesDisabledError):
                await GraphDriverFactory.create_driver(
                    GraphProvider.NEO4J,
                    {
                        "uri": "bolt://configured-db",
                        "user": "neo4j",
                        "password": "secret",
                    },
                )

    async def test_falkordb_setup_uses_graph_driver_not_neo4j_subprocess(self):
        driver = FakeGraphDriver()
        with tempfile.TemporaryDirectory() as root:
            local_path = os.path.join(root, "code", "data.rdb")
            args = argparse.Namespace(
                graph_provider="falkordb",
                falkordb_path=local_path,
                falkordb_graph="cortext",
                neo4j_db="cortext",
            )

            async def fake_create_driver(provider, config):
                self.assertEqual(provider, GraphProvider.FALKORDB)
                self.assertEqual(config["path"], local_path)
                self.assertNotIn("uri", config)
                self.assertEqual(config["graph"], "cortext")
                return driver

            with patch.dict(
                os.environ,
                {"CORTEX_GRAPH_JOURNAL_MODE": "shared-shadow"},
                clear=False,
            ), patch.object(
                incremental_sync.GraphDriverFactory,
                "create_driver",
                side_effect=fake_create_driver,
            ), patch.object(
                incremental_sync.subprocess,
                "run",
                side_effect=AssertionError("Neo4j setup subprocess should not run"),
            ):
                await incremental_sync._ensure_project_repository_graph(
                    args=args,
                    root=root,
                    project_id="cortext",
                    project_name="Cortext Project",
                )

        self.assertTrue(driver.closed)
        ensured = [index for invocation, _database in driver.indexes for index in invocation]
        self.assertIn(
            {"label": "Project", "property": "project_id", "type": "range"},
            ensured,
        )
        self.assertIn(
            {"label": "Function", "property": "id", "type": "range"},
            ensured,
        )
        self.assertIn(
            {"label": "SqlStatement", "property": "id", "type": "range"},
            ensured,
        )
        query, params, database = driver.queries[0]
        self.assertIn("MERGE (p:Project", query)
        self.assertEqual(params["project_id"], "cortext")
        self.assertEqual(params["project_id_normalized"], "cortext")
        self.assertEqual(params["project_slug"], "cortext-project")
        self.assertEqual(params["repo_name"], f"Cortext Project/{os.path.basename(root)}")
        self.assertEqual(
            set(re.findall(r"\$([A-Za-z_]\w*)", query)), set(params)
        )
        self.assertEqual(database, "cortext")

    async def test_required_preflight_defers_project_mutation_to_analyzer_journal(self):
        driver = FakeGraphDriver()
        with tempfile.TemporaryDirectory() as root:
            args = argparse.Namespace(
                graph_provider="falkordb",
                falkordb_path=os.path.join(root, "code", "data.rdb"),
                falkordb_graph="cortext",
                neo4j_db="cortext",
            )

            async def fake_create_driver(provider, config):
                self.assertEqual(provider, GraphProvider.FALKORDB)
                return driver

            with patch.dict(
                os.environ,
                {"CORTEX_GRAPH_JOURNAL_MODE": "shared-required"},
                clear=True,
            ), patch.object(
                incremental_sync.GraphDriverFactory,
                "create_driver",
                side_effect=fake_create_driver,
            ):
                await incremental_sync._ensure_project_repository_graph(
                    args=args,
                    root=root,
                    project_id="cortext",
                    project_name="Cortext Project",
                )

        self.assertTrue(driver.closed)
        self.assertFalse(hasattr(driver, "journal_config"))
        self.assertEqual(driver.queries, [])

    async def test_neo4j_uses_the_same_preflight_before_project_mutation(self):
        driver = FakeGraphDriver()
        with tempfile.TemporaryDirectory() as root:
            args = argparse.Namespace(
                graph_provider="neo4j",
                neo4j_uri="bolt://localhost:7687",
                neo4j_user="neo4j",
                neo4j_password="secret",
                neo4j_db="cortext",
                falkordb_graph="ignored",
            )
            with patch.object(
                incremental_sync,
                "create_graph_driver_from_args",
                return_value=driver,
            ), patch.object(
                incremental_sync.subprocess,
                "run",
                side_effect=AssertionError("schema setup must not shell out"),
            ):
                await incremental_sync._ensure_project_repository_graph(
                    args=args,
                    root=root,
                    project_id="cortext",
                    project_name="Cortext Project",
                )

        self.assertTrue(driver.closed)
        self.assertTrue(driver.indexes)
        self.assertEqual(len(driver.queries), 4)
        self.assertIn("duplicates > 1", driver.queries[0][0])
        self.assertIn("REQUIRE p.project_id IS UNIQUE", driver.queries[1][0])
        self.assertIn("REQUIRE r.name IS UNIQUE", driver.queries[2][0])
        self.assertIn("MERGE (p:Project", driver.queries[3][0])

    async def test_schema_failure_executes_no_project_mutation(self):
        driver = FakeGraphDriver(fail_schema=True)
        with tempfile.TemporaryDirectory() as root:
            args = argparse.Namespace(
                graph_provider="falkordb",
                falkordb_graph="cortext",
                neo4j_db="cortext",
            )
            with patch.object(
                incremental_sync,
                "create_graph_driver_from_args",
                return_value=driver,
            ):
                with self.assertRaisesRegex(RuntimeError, "creation failed"):
                    await incremental_sync._ensure_project_repository_graph(
                        args=args,
                        root=root,
                        project_id="cortext",
                        project_name="Cortext Project",
                    )

        self.assertTrue(driver.closed)
        self.assertEqual(driver.queries, [])

    async def test_neo4j_duplicate_identity_audit_blocks_constraints_and_mutation(self):
        class DuplicateDriver(FakeGraphDriver):
            async def execute_query(self, query, parameters=None, database=None):
                self.queries.append((query, parameters, database))
                if "duplicates > 1" in query:
                    return (
                        [
                            {
                                "label": "Project",
                                "property": "project_id",
                                "identity": "cortext",
                                "duplicates": 2,
                            }
                        ],
                        [],
                        None,
                    )
                return [], [], None

        driver = DuplicateDriver()
        with tempfile.TemporaryDirectory() as root:
            args = argparse.Namespace(
                graph_provider="neo4j",
                neo4j_uri="bolt://localhost:7687",
                neo4j_user="neo4j",
                neo4j_password="secret",
                neo4j_db="cortext",
                falkordb_graph="ignored",
            )
            with patch.object(
                incremental_sync,
                "create_graph_driver_from_args",
                return_value=driver,
            ):
                with self.assertRaisesRegex(RuntimeError, "duplicate graph identities"):
                    await incremental_sync._ensure_project_repository_graph(
                        args=args,
                        root=root,
                        project_id="cortext",
                        project_name="Cortext Project",
                    )

        self.assertTrue(driver.closed)
        self.assertEqual(len(driver.queries), 1)
        self.assertEqual(driver.indexes, [])


if __name__ == "__main__":
    unittest.main()
