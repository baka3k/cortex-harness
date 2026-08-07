import asyncio
import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


incremental_sync = _load_module(
    "incremental_sync_parse_quality_test",
    CODE_TINY / "tools" / "sync" / "incremental_sync.py",
)
from tools.cplus import cplus_analyzer  # noqa: E402


class IncrementalSyncParseQualityTests(unittest.TestCase):
    def test_only_cplus_receives_quality_flags_and_bootstrap_is_disabled(self):
        common = dict(
            python_bin=sys.executable,
            root="/tmp/project",
            project_id="demo",
            project_name="Demo",
            before_sha="before",
            after_sha="after",
            changed_manifest=None,
            deleted_manifest=None,
            qdrant_collection=None,
            message_scan_enabled=False,
            message_output_dir=None,
            message_qdrant_collection=None,
            incremental=False,
            verbose=False,
            parse_quality="report",
            parse_quality_report="/tmp/report.json",
        )
        cplus_cmd = incremental_sync._build_analyzer_cmd(
            analyzer=incremental_sync.ANALYZERS["cplus"],
            **common,
        )
        self.assertIn("--parse-quality", cplus_cmd)
        self.assertIn("--parse-quality-report", cplus_cmd)
        self.assertIn("--disable-compile-db-bootstrap", cplus_cmd)

        shell_cmd = incremental_sync._build_analyzer_cmd(
            analyzer=incremental_sync.ANALYZERS["shell"],
            **common,
        )
        self.assertNotIn("--parse-quality", shell_cmd)
        self.assertNotIn("--disable-compile-db-bootstrap", shell_cmd)

    def test_off_policy_preserves_compatibility_without_forcing_bootstrap_flag(self):
        cmd = incremental_sync._build_analyzer_cmd(
            python_bin=sys.executable,
            analyzer=incremental_sync.ANALYZERS["cplus"],
            root="/tmp/project",
            project_id="demo",
            project_name="Demo",
            before_sha="before",
            after_sha="after",
            changed_manifest=None,
            deleted_manifest=None,
            qdrant_collection=None,
            message_scan_enabled=False,
            message_output_dir=None,
            message_qdrant_collection=None,
            incremental=False,
            verbose=False,
            parse_quality="off",
        )
        self.assertNotIn("--disable-compile-db-bootstrap", cmd)

    def test_dry_run_does_not_write_artifact(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "sample.c").write_text("int sample(void) { return 0; }\n", encoding="utf-8")
            report_path = Path(root, "artifacts", "quality.json")
            with mock.patch.object(cplus_analyzer, "prepare_graph_args", return_value=False):
                rc = asyncio.run(
                    cplus_analyzer.main(
                        [
                            "--root",
                            root,
                            "--dry-run",
                            "--parse-quality",
                            "report",
                            "--parse-quality-report",
                            str(report_path),
                            "--disable-message-scan",
                        ]
                    )
                )
            self.assertEqual(rc, 0)
            self.assertFalse(report_path.exists())

    def test_report_is_atomic_private_and_reconciled(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "sample.c").write_text("int sample(void) { return 0; }\n", encoding="utf-8")
            report_path = Path(root, "artifacts", "quality.json")
            asyncio.run(
                cplus_analyzer.build_call_graph(
                    root=root,
                    code_writer=None,
                    qdrant_writer=None,
                    embedder=None,
                    batch_size=8,
                    qdrant_batch_size=8,
                    cache_dir=str(Path(root, ".cache")),
                    keep_cache=False,
                    parse_cache=False,
                    neo4j_batch_size=8,
                    neo4j_calls_batch_size=8,
                    neo4j_state_path=None,
                    project_id="demo",
                    project_name="Demo",
                    language="cplus",
                    repo="demo",
                    build_system="",
                    event_map_path=None,
                    call_stats_path=None,
                    possible_calls_path=None,
                    unresolved_calls_path=None,
                    parse_errors_path=str(report_path),
                    parse_run_id="test-run",
                    commit_sha="deadbeef",
                    verbose=False,
                )
            )
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["aggregates"]["file_count"], 1)
            self.assertEqual(payload["detail_record_count"], 1)
            self.assertNotIn(root, json.dumps(payload))
            self.assertEqual(stat.S_IMODE(os.stat(report_path).st_mode), 0o600)

    def test_repair_policy_uses_bounded_parser_local_queue(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "broken.cpp").write_text(
                "class Broken { public: void run( {\n",
                encoding="utf-8",
            )
            report_path = Path(root, "artifacts", "quality.json")
            metrics = {
                "queued": 1,
                "attempted": 1,
                "improved": 0,
                "non_improved": 1,
                "failed": 0,
                "stop_reason": "queue_empty",
            }
            with mock.patch.object(
                cplus_analyzer,
                "recover_payload_candidates",
                return_value=({}, metrics),
            ) as recovery_mock:
                asyncio.run(
                    cplus_analyzer.build_call_graph(
                        root=root,
                        code_writer=None,
                        qdrant_writer=None,
                        embedder=None,
                        batch_size=8,
                        qdrant_batch_size=8,
                        cache_dir=str(Path(root, ".cache")),
                        keep_cache=False,
                        parse_cache=False,
                        neo4j_batch_size=8,
                        neo4j_calls_batch_size=8,
                        neo4j_state_path=None,
                        project_id="demo",
                        project_name="Demo",
                        language="cplus",
                        repo="demo",
                        build_system="",
                        event_map_path=None,
                        call_stats_path=None,
                        possible_calls_path=None,
                        unresolved_calls_path=None,
                        parse_errors_path=str(report_path),
                        parse_run_id="test-repair",
                        commit_sha="deadbeef",
                        verbose=False,
                        parse_quality_policy="repair",
                        parse_quality_max_files=7,
                        parse_quality_wall_seconds=11,
                        parse_quality_workers=2,
                    )
                )
            self.assertEqual(recovery_mock.call_count, 1)
            budgets = recovery_mock.call_args.kwargs["budgets"]
            self.assertEqual((budgets.max_files, budgets.wall_seconds, budgets.workers), (7, 11, 2))
            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["recovery"], metrics)


if __name__ == "__main__":
    unittest.main()
