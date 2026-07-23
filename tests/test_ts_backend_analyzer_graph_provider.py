import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.ts import ts_backend_analyzer  # pyright: ignore[reportMissingImports]  # noqa: E402
from tools.graph.core.base import GraphProvider  # pyright: ignore[reportMissingImports]  # noqa: E402


class _StopAfterCleanup(RuntimeError):
    pass


class _FakeDriver:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakeWriter:
    def __init__(self, driver, database):
        self.driver = driver
        self.database = database


class TypeScriptBackendAnalyzerGraphProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_incremental_cleanup_uses_project_scoped_keyword_arguments(self):
        driver = _FakeDriver()
        writer = _FakeWriter(driver, "project_graph")
        cleanup_calls = []

        async def fake_cleanup(
            *,
            driver,
            database,
            project_id,
            file_paths,
            verbose=False,
        ):
            cleanup_calls.append(
                {
                    "driver": driver,
                    "database": database,
                    "project_id": project_id,
                    "file_paths": file_paths,
                    "verbose": verbose,
                }
            )

        with tempfile.TemporaryDirectory() as root:
            changed_file = Path(root, "src", "changed.ts")
            changed_file.parent.mkdir(parents=True)
            changed_file.write_text("export const changed = true;\n", encoding="utf-8")
            with (
                patch.object(
                    ts_backend_analyzer,
                    "cleanup_neo4j_for_files",
                    new=fake_cleanup,
                ),
                patch.object(
                    ts_backend_analyzer,
                    "_collect_base_write_data",
                    side_effect=_StopAfterCleanup,
                ),
            ):
                with self.assertRaises(_StopAfterCleanup):
                    await ts_backend_analyzer.build_backend_graph(
                        root=root,
                        code_writer=writer,
                        batch_size=4,
                        cache_dir=None,
                        parse_cache=False,
                        neo4j_batch_size=100,
                        project_id="project-id",
                        project_name="Project",
                        language="typescript",
                        repo=root,
                        build_system="npm",
                        verbose=True,
                        incremental=True,
                        changed_files=["src/changed.ts"],
                        deleted_files=["src/deleted.ts"],
                    )

        self.assertEqual(
            cleanup_calls,
            [
                {
                    "driver": driver,
                    "database": "project_graph",
                    "project_id": "project-id",
                    "file_paths": ["src/changed.ts", "src/deleted.ts"],
                    "verbose": True,
                }
            ],
        )

    async def test_main_uses_provider_aware_driver_creation_for_falkordb(self):
        driver = _FakeDriver()

        with tempfile.TemporaryDirectory() as root:
            with (
                patch(
                    "tools.graph.cli.GraphDriverFactory.create_driver",
                    new=AsyncMock(return_value=driver),
                ) as create_driver,
                patch.object(
                    ts_backend_analyzer,
                    "build_backend_graph",
                    new=AsyncMock(),
                ) as build_graph,
            ):
                result = await ts_backend_analyzer.main(
                    [
                        "--root",
                        root,
                        "--graph-provider",
                        "falkordb",
                        "--falkordb-graph",
                        "project_graph",
                    ]
                )

        self.assertEqual(result, 0)
        create_driver_args = create_driver.await_args
        self.assertIsNotNone(create_driver_args)
        assert create_driver_args is not None
        provider, config = create_driver_args.args
        self.assertEqual(provider, GraphProvider.FALKORDB)
        self.assertEqual(config["graph"], "project_graph")
        self.assertEqual(config["database"], "project_graph")
        await_args = build_graph.await_args
        self.assertIsNotNone(await_args)
        assert await_args is not None
        self.assertIs(await_args.kwargs["code_writer"].driver, driver)
        self.assertEqual(
            await_args.kwargs["code_writer"].database,
            "project_graph",
        )
        self.assertTrue(driver.closed)

    async def test_dry_run_does_not_open_a_graph_connection(self):
        create_driver = AsyncMock(return_value=_FakeDriver())
        with tempfile.TemporaryDirectory() as root:
            with patch.object(
                ts_backend_analyzer,
                "create_graph_driver_from_args",
                new=create_driver,
            ):
                result = await ts_backend_analyzer.main(
                    [
                        "--root",
                        root,
                        "--graph-provider",
                        "falkordb",
                        "--falkordb-graph",
                        "project_graph",
                        "--dry-run",
                    ]
                )

        self.assertEqual(result, 0)
        create_driver.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
