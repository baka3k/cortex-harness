"""Qdrant point-payload contract for the VB6 depth upgrade (plan 260917-1628).

Runs build_call_graph over the fixture with a capturing Qdrant writer +
deterministic embedder (same fake-driver pattern as test_vb6_graph_contract)
and asserts the M3/M5 gates:

- function points carry vb6_event / vb6_control_type for wired handlers and
  an enriched note (M3)
- function/enum/constant/declare comments survive into point payloads (M5)
- designer class points carry the compact controls summary
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "vb6-application"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

import tools.vb.vb_analyzer_base as base  # noqa: E402

JAVA_AVAILABLE = os.path.exists("/usr/bin/java") or os.environ.get("JAVA_HOME") is not None


class CapturingQdrantWriter:
    def __init__(self):
        self.points = []

    def ensure_collection(self):
        return None

    def upsert(self, points):
        self.points.extend(points)


class FakeEmbedder:
    vector_size = 4

    def embed(self, texts, batch_size=8, verbose=False):
        return [[0.0, 0.0, 0.0, float(len(text) % 97)] for text in texts]


@unittest.skipUnless(JAVA_AVAILABLE, "java not available")
class Vb6QdrantPayloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.writer = CapturingQdrantWriter()
        asyncio.run(base.build_call_graph(
            str(FIXTURE_DIR),
            dialect="vb6",
            code_writer=None,
            qdrant_writer=cls.writer,
            embedder=FakeEmbedder(),
            project_id="vb6-qdrant-test",
            project_name="vb6-qdrant-test",
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
            qdrant_batch_size=8,
            vb6_parser_engine="antlr",
        ))
        cls.points = {point["payload"]["symbol_id"]: point["payload"]
                      for point in cls.writer.points}

    def _by_name(self, node_type: str, name: str):
        return [
            payload for payload in self.points.values()
            if payload.get("node_type") == node_type and payload.get("name") == name
        ]

    def test_m3_event_wiring_in_point_payload(self) -> None:
        handlers = self._by_name("function", "cmdGo_Click")
        self.assertTrue(handlers, "cmdGo_Click point missing")
        self.assertEqual(handlers[0]["vb6_event"], "cmdGo.Click")
        self.assertEqual(handlers[0]["vb6_control_type"], "VB.CommandButton")
        self.assertTrue(handlers[0]["note"], "handler note must be enriched")

        nested = self._by_name("function", "txtName_Change")
        self.assertTrue(nested)
        self.assertEqual(nested[0]["vb6_event"], "txtName.Change")
        self.assertEqual(nested[0]["vb6_control_type"], "VB.TextBox")

        form_load = self._by_name("function", "Form_Load")
        self.assertTrue(form_load)
        self.assertEqual(form_load[0]["vb6_event"], "Form.Load")

    def test_m3_no_false_positive_wiring(self) -> None:
        decoy = self._by_name("function", "Helper_Click_Validate")
        self.assertTrue(decoy, "decoy sub point missing")
        self.assertEqual(decoy[0]["vb6_event"], "")
        self.assertEqual(decoy[0]["vb6_control_type"], "")

    def test_m3_class_point_carries_controls_summary(self) -> None:
        frm_main = self._by_name("class", "frmMain")
        self.assertTrue(frm_main, "frmMain class point missing")
        controls = frm_main[0].get("controls") or []
        names = {c["name"] for c in controls}
        self.assertIn("fraData", names)
        self.assertIn("txtName", names)
        for entry in controls:
            self.assertEqual(set(entry.keys()), {"name", "type"})

    def test_m5_comments_in_point_payloads(self) -> None:
        elapsed = self._by_name("function", "ElapsedMs")
        self.assertTrue(elapsed)
        self.assertIn("Reads the process uptime", elapsed[0]["comment"])
        self.assertEqual(elapsed[0]["comment"], elapsed[0]["summary"])

        enum = self._by_name("enum", "AppColor")
        self.assertTrue(enum, "enum point missing")
        self.assertEqual(enum[0]["comment"], "Color codes for the fixture UI")

        constant = self._by_name("constant", "MAX_RETRY")
        self.assertTrue(constant)
        self.assertEqual(constant[0]["comment"], "Retry budget for the fixture sync loop")

        declare = self._by_name("function", "GetTickCount")
        self.assertTrue(declare, "declare point missing (functions lane, kind=declare)")
        self.assertEqual(declare[0]["kind"], "declare")
        self.assertEqual(declare[0]["comment"], "kernel32 uptime probe used by ElapsedMs")
        self.assertEqual(declare[0]["lib"], "kernel32")

        event = self._by_name("event", "BeforeSave")
        self.assertTrue(event, "event point missing")
        self.assertEqual(event[0]["parameters"], "Cancel As Boolean")

    def test_m5_comment_attachment_rate(self) -> None:
        """M5 gate: >=80% of planted adjacent comments reach point payloads."""

        planted = [
            ("function", "ElapsedMs"),
            ("function", "ReadField"),
            ("function", "cmdGo_Click"),
            ("function", "txtName_Change"),
            ("enum", "AppColor"),
            ("constant", "MAX_RETRY"),
            ("function", "GetTickCount"),
            ("event", "BeforeSave"),
        ]
        hits = 0
        for node_type, name in planted:
            found = self._by_name(node_type, name)
            if found and (found[0].get("comment") or ""):
                hits += 1
        rate = 100.0 * hits / len(planted)
        self.assertGreaterEqual(rate, 80.0, f"M5 comment rate {rate:.0f}% below 80%")


if __name__ == "__main__":
    unittest.main()
