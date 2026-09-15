"""Analyzer provider-wiring regression check.

Phase-08 (analyzer-layer-rust-cutover): the Python analyzer entry points
referenced by ``ANALYZERS`` below were retired at the cutover commit. The
check is preserved as a documented skip so the test report still calls
out the regression explicitly — per red-team F7 / phase-08 audit
disposition, skips MUST be loud (no silent ``importorskip``).

Once the migration is revisited, replace the skip with a parity check
that targets the Rust analyzer binaries (e.g. assert the corresponding
``analyzer-<lang>`` [[bin]] in ``rust/crates/`` exists and wires the
shared graph-driver factory).
"""

from __future__ import annotations

import unittest


_RETIRED_AT_PHASE_08 = (
    "Phase-08 (analyzer-layer-rust-cutover): Python analyzer scripts were "
    "deleted at this commit. The provider-wiring check below is preserved "
    "as a documented loud-skip rather than removed — operators should see "
    "this in the pytest report, not a silent omission."
)


class AnalyzerProviderWiringTests(unittest.TestCase):
    @unittest.skip(_RETIRED_AT_PHASE_08)
    def test_migrated_entrypoints_use_shared_driver_factory_helper(self) -> None:
        return None

    @unittest.skip(_RETIRED_AT_PHASE_08)
    def test_sync_entrypoints_use_shared_driver_factory_helper(self) -> None:
        return None

    def test_driver_helper_derives_local_path_for_direct_falkor_callers(self) -> None:
        # Kept live: targets ``tools.graph.cli`` which is not retired and
        # remains part of the kept harness plane.
        import argparse
        import asyncio
        import tempfile
        from unittest import mock

        from tools.graph.cli import create_graph_driver_from_args

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

    def test_explicit_falkor_graph_wins_over_registry_default(self) -> None:
        import argparse
        from unittest import mock

        from tools.graph.cli import apply_project_registry_defaults

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