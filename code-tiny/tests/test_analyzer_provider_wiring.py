import argparse
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.graph.cli import (
    apply_project_registry_defaults,
    create_graph_driver_from_args,
)


ANALYZERS = (
    "android/android_java_analyzer.py",
    "android/android_kotlin_analyzer.py",
    "cobol/cobol_analyzer.py",
    "cplus/cplus_analyzer.py",
    "csharp/csharp_analyzer.py",
    "delphi/delphi_analyzer.py",
    "flutter/flutter_analyzer.py",
    "java/java_analyzer.py",
    "js/js_analyzer.py",
    "kotlin/kotlin_analyzer.py",
    "php/php_analyzer.py",
    "plsql/plsql_analyzer.py",
    "python/python_analyzer.py",
    "sql/sql_analyzer.py",
    "spring/spring_analyzer.py",
    "struts/struts_analyzer.py",
    "mybatis/mybatis_analyzer.py",
    "servlet_jsp/servlet_jsp_analyzer.py",
    "ts/ts_analyzer.py",
    "vb/vb_analyzer_base.py",
)


class AnalyzerProviderWiringTests(unittest.TestCase):
    def test_migrated_entrypoints_use_shared_driver_factory_helper(self):
        tools_root = Path(__file__).resolve().parents[1] / "tools"
        for relative_path in ANALYZERS:
            with self.subTest(analyzer=relative_path):
                source = (tools_root / relative_path).read_text(encoding="utf-8")
                self.assertIn("create_graph_driver_from_args(args)", source)
                self.assertNotIn("provider=GraphProvider.NEO4J", source)

    def test_sync_entrypoints_use_shared_driver_factory_helper(self):
        tools_root = Path(__file__).resolve().parents[1] / "tools" / "sync"
        for filename in ("message_scan.py", "dead_code_report.py"):
            with self.subTest(entrypoint=filename):
                source = (tools_root / filename).read_text(encoding="utf-8")
                self.assertIn("create_graph_driver_from_args(args)", source)
                self.assertNotIn(
                    "GraphDriverFactory.create_driver(\n            GraphProvider.NEO4J",
                    source,
                )

    def test_driver_helper_derives_local_path_for_direct_falkor_callers(self):
        args = argparse.Namespace(
            graph_provider="falkordb",
            falkordb_path=None,
            falkordb_graph="project-a",
            project_id=None,
            neo4j_uri=None,
            neo4j_user=None,
            neo4j_password=None,
            neo4j_db=None,
        )
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
            "os.environ", {"CORTEX_DATA_HOME": directory}, clear=False
        ), mock.patch(
            "tools.graph.cli.GraphDriverFactory.create_driver",
            new=mock.AsyncMock(return_value=object()),
        ) as factory:
            asyncio.run(create_graph_driver_from_args(args))
        config = factory.await_args.args[1]
        self.assertTrue(config["path"])
        self.assertIn(directory, config["path"])

    def test_explicit_falkor_graph_wins_over_registry_default(self):
        targets = argparse.Namespace(
            code_graph="registry-graph",
            code_qdrant_collection="registry-vectors",
        )
        for explicit_graph in ("explicit-graph", "neo4j"):
            with self.subTest(graph=explicit_graph):
                args = argparse.Namespace(
                    project_id="registered",
                    falkordb_graph=explicit_graph,
                    qdrant_collection=None,
                )
                with mock.patch(
                    "tools.common.project_registry.resolve_project_targets",
                    return_value=targets,
                ):
                    apply_project_registry_defaults(args)
                self.assertEqual(args.falkordb_graph, explicit_graph)


if __name__ == "__main__":
    unittest.main()
