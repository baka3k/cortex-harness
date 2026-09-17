"""Phase 04: incremental edge re-publication (AD-09, red-team F1 Critical).

When a file changes, incremental cleanup DETACH DELETEs its nodes, destroying
INCOMING edges from unchanged callers. The vb6 path must re-publish every edge
whose source OR target file is in the changed set — not only edges originating
from the changed file.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "vb6-application"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

import tools.vb.vb_analyzer_base as base  # noqa: E402
from tests.test_vb6_graph_contract import CapturingDriver  # noqa: E402
from tools.graph.writer.language_writer import LanguageCodeWriter  # noqa: E402

JAVA_AVAILABLE = os.path.exists("/usr/bin/java") or os.environ.get("JAVA_HOME") is not None


@unittest.skipUnless(JAVA_AVAILABLE, "java not available")
class Vb6IncrementalEdgeRepublicationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="vb6_incr_")
        self.root = Path(self._tmp.name) / "app"
        shutil.copytree(FIXTURE_DIR, self.root)
        self.driver = CapturingDriver()
        self.writer = LanguageCodeWriter(self.driver, database="incr", batch_size=100)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, changed_files):
        asyncio.run(base.build_call_graph(
            str(self.root),
            dialect="vb6",
            code_writer=self.writer,
            qdrant_writer=None,
            embedder=None,
            project_id="incr-test",
            project_name="incr-test",
            language="vb6",
            repo=str(self.root),
            build_system="",
            cache_dir=None,
            parse_cache=False,
            incremental=bool(changed_files),
            changed_files=changed_files,
            deleted_files=[],
            verbose=False,
            embed_batch_size=1,
            qdrant_batch_size=1,
            vb6_parser_engine="antlr",
        ))

    def _call_rows(self):
        rows = []
        for query, params, _db in self.driver.calls:
            if ":CALLS" in query and "POSSIBLE" not in query:
                rows.extend(params.get("rows", []))
        return rows

    def _possible_rows(self):
        rows = []
        for query, params, _db in self.driver.calls:
            if "POSSIBLE_CALLS" in query and "{site_id" in query:
                rows.extend(params.get("rows", []))
        return rows

    def _file_rows(self):
        rows = []
        for query, params, _db in self.driver.calls:
            if "MERGE (f:File" in query:
                rows.extend(params.get("rows", []))
        return rows

    def test_incoming_edges_from_unchanged_caller_are_republished(self) -> None:
        # touch modUtil.bas (callee side) — callers live in unchanged files
        target = self.root / "modUtil.bas"
        text = target.read_text(encoding="utf-8")
        target.write_text(text + "\n' touched for incremental sync\n", encoding="utf-8")
        self._run(changed_files=["modUtil.bas"])

        # node writes restricted to the changed file
        file_rows = self._file_rows()
        written = {row.get("path") or row.get("file_path") for row in file_rows}
        self.assertEqual(written, {"modUtil.bas"})

        # AD-09: the edge from the UNCHANGED modMain.DoWork into the CHANGED
        # modUtil.CalcTotal must be re-published
        call_rows = self._call_rows()
        incoming = [
            row for row in call_rows
            if row.get("caller_id") == "modMain.DoWork/0@modMain.bas"
            and row.get("callee_id") == "modUtil.CalcTotal/2@modUtil.bas"
        ]
        self.assertTrue(
            incoming,
            "incoming CALLS edge from unchanged modMain.DoWork into changed "
            "modUtil.CalcTotal was not re-published (AD-09/F1)",
        )

        # edges entirely between unchanged files must NOT be re-published
        unrelated = [
            row for row in call_rows
            if row.get("caller_id") == "frmMain.cmdGo_Click/0@frmMain.frm"
            and row.get("callee_id") == "frmMain.Form_Load/0@frmMain.frm"
        ]
        self.assertFalse(unrelated, "unrelated edge should not be re-published")

    def test_possible_calls_follow_same_rule(self) -> None:
        # touching modMain.bas must re-publish its outgoing weak edges
        target = self.root / "modMain.bas"
        text = target.read_text(encoding="utf-8")
        target.write_text(text + "\n' touched\n", encoding="utf-8")
        self._run(changed_files=["modMain.bas"])
        possible = self._possible_rows()
        self.assertTrue(possible)
        # ambiguous TestSameName rows (caller = changed file) survive
        callers = {row.get("caller_id") for row in possible}
        self.assertIn("modMain.DoWork/0@modMain.bas", callers)
        # weak edges whose caller is an UNCHANGED file are filtered out
        for row in possible:
            caller_file = (row.get("caller_id") or "").rsplit("@", 1)[-1]
            callee_file = (row.get("callee_id") or "").rsplit("@", 1)[-1]
            self.assertTrue(
                caller_file == "modMain.bas" or callee_file == "modMain.bas",
                f"edge {row.get('caller_id')} -> {row.get('callee_id')} outside changed set",
            )


if __name__ == "__main__":
    unittest.main()
