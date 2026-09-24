"""Phase 03 tests: vb6 engine dispatch, fallback cascade, parse_meta truth.

Covers AD-05/AD-07: `--vb6-parser-engine auto|antlr|regex`, the loud (never
silent) degrade to regex when java/worker are unavailable, per-file fallback
when the worker fails one file, and parse_meta.engine reflecting reality.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "vb6-application"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

import tools.vb.vb_analyzer_base as base  # noqa: E402
from tools.vb.vb_common import PARSE_CACHE_VERSION  # noqa: E402


def _fixture_files():
    return [
        str(path) for path in sorted(FIXTURE_DIR.iterdir())
        if path.suffix.lower() in {".bas", ".cls", ".frm"}
    ]


class Vb6EngineDispatchTest(unittest.TestCase):
    def test_parse_cache_version_bumped(self) -> None:
        # AD-05 (plan 260917-1628): bumped per payload-shape change — phase-01
        # hydrated planes, phase-02 controls/keep-designer, phase-04 comments;
        # 09-18-1 dropped Const from _VAR_DECL_RE; 260924-1 added the anchor
        # planes (plan 260924 phase 02). Was red BEFORE plan 260924 (pinned
        # 09-17-4 vs actual 09-18-1) — fixed as part of this bump.
        self.assertEqual(PARSE_CACHE_VERSION, "vb-family-v2026-09-24-1")
        self.assertNotEqual(PARSE_CACHE_VERSION, "vb-family-v2026-09-17-1")
        self.assertNotEqual(PARSE_CACHE_VERSION, "vb-family-v2026-04-03-2")

    def _run_build_call_graph(self, engine: str):
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            asyncio.run(base.build_call_graph(
                str(FIXTURE_DIR),
                dialect="vb6",
                code_writer=None,
                qdrant_writer=None,
                embedder=None,
                project_id="dispatch-test",
                project_name="dispatch-test",
                language="vb6",
                repo=str(FIXTURE_DIR),
                build_system="",
                cache_dir=None,
                parse_cache=False,
                incremental=False,
                changed_files=[],
                deleted_files=[],
                verbose=False,
                embed_batch_size=1,
                qdrant_batch_size=1,
                parallel_workers=2,
                vb6_parser_engine=engine,
            ))
        return buffer.getvalue()

    def test_forced_regex_engine_skips_worker(self) -> None:
        with mock.patch.object(base, "_parse_vb6_with_antlr_batch") as batch:
            output = self._run_build_call_graph("regex")
        batch.assert_not_called()
        self.assertIn("[vb6][summary]", output)
        self.assertIn("engine=regex", output)

    def test_auto_degrades_loudly_when_worker_unavailable(self) -> None:
        def unavailable(*_args, **_kwargs):
            raise RuntimeError("java missing")
        with mock.patch.object(base, "ensure_vb6_antlr_worker_built",
                               side_effect=unavailable), \
                mock.patch.object(base, "_parse_vb6_with_antlr_batch") as batch:
            output = self._run_build_call_graph("auto")
        batch.assert_not_called()
        self.assertIn("[vb6][engine] antlr unavailable (java missing), falling back to regex", output)
        self.assertIn("engine=regex", output)

    def test_worker_batch_error_falls_back_per_file(self) -> None:
        files = _fixture_files()[:3]
        def boom(**_kwargs):
            raise RuntimeError("simulated worker explosion")
        with mock.patch.object(base, "parse_vb6_files_with_antlr", side_effect=boom):
            payloads = asyncio.run(base._parse_vb6_with_antlr_batch(
                parse_files=files,
                all_source_files=files,
                root=str(FIXTURE_DIR),
                parse_fn=base._PARSER_FACTORY["vb6"],
                cache_dir=None,
                parse_cache=False,
                vb6_parser_engine="antlr",
                vb6_antlr_timeout_sec=30.0,
                vb6_antlr_workspace_timeout_ms=30000,
                verbose=False,
            ))
        self.assertEqual(len(payloads), len(files))
        for payload in payloads:
            meta = payload["parse_meta"]
            self.assertEqual(meta.get("parser_engine"), "regex")
            self.assertIn("simulated worker explosion", meta.get("fallback_reason", ""))

    def test_per_file_worker_error_falls_back(self) -> None:
        files = _fixture_files()
        rel_files = [os.path.relpath(path, FIXTURE_DIR).replace("\\", "/") for path in files]

        def fake_worker(*, root, files, **_kwargs):
            payloads = {}
            errors = {}
            for rel in rel_files:
                if rel == "modUtil.bas":
                    errors[rel] = "timeout after 1ms"
                else:
                    payloads[rel] = {
                        "functions": [], "calls": [], "classes": [],
                        "namespaces": [], "relations": [], "properties": [],
                        "events": [], "interfaces": [], "enums": [],
                        "constants": [], "variables": [],
                        "file_def": {
                            "file_path": rel, "start_line": 1, "end_line": 1,
                            "code": "", "comment": "", "summary": "", "note": "",
                            "imports": [], "exports": [],
                        },
                        "parse_meta": {"parser_engine": "antlr"},
                    }
            return payloads, errors, {"workspace_kind": "vbp"}

        with mock.patch.object(base, "parse_vb6_files_with_antlr", side_effect=fake_worker):
            payloads = asyncio.run(base._parse_vb6_with_antlr_batch(
                parse_files=files,
                all_source_files=files,
                root=str(FIXTURE_DIR),
                parse_fn=base._PARSER_FACTORY["vb6"],
                cache_dir=None,
                parse_cache=False,
                vb6_parser_engine="antlr",
                vb6_antlr_timeout_sec=30.0,
                vb6_antlr_workspace_timeout_ms=30000,
                verbose=False,
            ))
        by_rel = {p["file_def"].file_path: p for p in payloads}
        self.assertEqual(
            by_rel["modUtil.bas"]["parse_meta"].get("parser_engine"), "regex"
        )
        self.assertEqual(
            by_rel["modUtil.bas"]["parse_meta"].get("fallback_reason"), "timeout after 1ms"
        )
        self.assertEqual(
            by_rel["modMain.bas"]["parse_meta"].get("parser_engine"), "antlr"
        )

    def test_cli_accepts_vb6_engine_flags(self) -> None:
        args = base.parse_args([
            "--dialect", "vb6", "--root", str(FIXTURE_DIR),
            "--vb6-parser-engine", "antlr",
            "--vb6-antlr-timeout-sec", "42",
            "--vb6-antlr-workspace-timeout-ms", "12345",
        ])
        self.assertEqual(args.vb6_parser_engine, "antlr")
        self.assertEqual(args.vb6_antlr_timeout_sec, 42.0)
        self.assertEqual(args.vb6_antlr_workspace_timeout_ms, 12345)

    def test_auto_engine_defaults(self) -> None:
        args = base.parse_args(["--dialect", "vb6", "--root", str(FIXTURE_DIR)])
        self.assertEqual(args.vb6_parser_engine, "auto")


if __name__ == "__main__":
    unittest.main()
