"""Phase 02 contract test: vb6-antlr-worker protocol over the fixture corpus.

Builds the worker if needed, runs it through the adapter (which materializes
.frm into padded temp .cls), and asserts the JSON contract: schema shape,
parse success, .frm payloads, malformed isolation, and the cross-module
CalcTotal edge carrying a callee_id from the ASG.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.vb.vb6_antlr_adapter import (  # noqa: E402
    ensure_worker_built,
    parse_vb6_files_with_antlr,
)

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "vb6-application"
PARSE_CACHE_VERSION = "vb-family-v2026-09-17-1"

JAVA_AVAILABLE = Path("/usr/bin/java").exists() or os.environ.get("JAVA_HOME") is not None


def _fixture_files() -> list:
    files = []
    for name in sorted(FIXTURE_DIR.iterdir()):
        if name.suffix.lower() in {".bas", ".cls", ".frm", ".ctl", ".pag"}:
            files.append(str(name))
    return files


def run_worker_on_fixture():
    return parse_vb6_files_with_antlr(
        root=str(FIXTURE_DIR),
        files=_fixture_files(),
        timeout_sec=300.0,
        workspace_timeout_ms=120000,
        parse_cache_version=PARSE_CACHE_VERSION,
        verbose=False,
    )


@unittest.skipUnless(JAVA_AVAILABLE, "java not available")
class Vb6AntlrWorkerContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.jar = ensure_worker_built()
        cls.payloads, cls.errors, cls.meta = run_worker_on_fixture()

    def test_payload_shape_matches_contract(self) -> None:
        required = ("functions", "calls", "classes", "file_def", "parse_meta")
        for rel, payload in self.payloads.items():
            for key in required:
                self.assertIn(key, payload, f"{rel} missing payload key {key}")
            meta = payload["parse_meta"]
            self.assertEqual(meta["parser_engine"], "antlr")
            self.assertEqual(meta["parser_language"], "vb6_antlr")
            self.assertEqual(meta["resolution_source"], "asg")
            self.assertEqual(payload["parse_cache_version"], PARSE_CACHE_VERSION)
            for fn in payload["functions"]:
                self.assertIn("symbol_id", fn)
                self.assertIn("module_name", fn)
            for call in payload["calls"]:
                for key in ("caller_id", "callee_name", "call_line", "call_type",
                            "resolution_status"):
                    self.assertIn(key, call)

    def test_parse_success_gate_excluding_malformed(self) -> None:
        # M3: >=95% ok on fixture, with malformed.bas the sanctioned failure
        # (excluded from the denominator per phase-02 exit criteria)
        ok_files = set(self.payloads)
        self.assertIn("malformed.bas", self.errors, "malformed.bas must report ok=false")
        self.assertNotIn("malformed.bas", ok_files)
        denominator = len((ok_files | set(self.errors)) - {"malformed.bas"})
        rate = len(ok_files) / max(1, denominator)
        self.assertGreaterEqual(rate, 0.95, f"parse success {rate:.2f} below 95%")

    def test_malformed_does_not_sink_batch(self) -> None:
        # red-team F3: every other file still has a payload
        for expected in ("modMain.bas", "modUtil.bas", "frmMain.frm", "clsOrder.cls"):
            self.assertIn(expected, self.payloads)
        self.assertTrue(self.payloads["modMain.bas"]["functions"])

    def test_frm_materialized_with_payload(self) -> None:
        for form in ("frmMain.frm", "frmAbout.frm"):
            self.assertIn(form, self.payloads, f"{form} must parse via temp .cls")
            payload = self.payloads[form]
            self.assertTrue(payload["functions"], f"{form} has no functions")
            form_load = [f for f in payload["functions"] if f["name"] == "Form_Load"]
            if form == "frmMain.frm":
                self.assertTrue(form_load, "frmMain.Form_Load missing")
                # designer-block padding keeps original line numbers
                self.assertEqual(form_load[0]["start_line"], 31)

    def test_cross_module_calc_total_has_callee_id(self) -> None:
        calls = self.payloads["modMain.bas"]["calls"]
        hits = [
            call for call in calls
            if "DoWork" in call["caller_id"]
            and call["callee_name"].endswith("CalcTotal")
            and call.get("callee_id")
        ]
        self.assertTrue(hits, "cross-module CalcTotal must carry an ASG callee_id")
        for call in hits:
            self.assertEqual(call["callee_id"], "modUtil.CalcTotal/2@modUtil.bas")

    def test_module_name_from_attribute(self) -> None:
        # modMain.bas declares Attribute VB_Name = "modMain"; the registry keys
        # functions under the declared module name, not the file stem.
        for fn in self.payloads["modMain.bas"]["functions"]:
            self.assertTrue(fn["symbol_id"].startswith("modMain."))

    def test_implements_map_and_interface_emission(self) -> None:
        self.assertEqual(
            self.meta.get("implements_map", {}).get("clsOrder"), ["IShip"]
        )
        interfaces = self.payloads["IShip.cls"]["interfaces"]
        self.assertTrue(
            any(i["symbol_id"] == "interface::IShip@IShip.cls" for i in interfaces),
            "IShip must be emitted as an Interface target (AD-10)",
        )

    def test_late_bound_and_with_block_survive(self) -> None:
        late = [
            call for call in self.payloads["latebas.bas"]["calls"]
            if call["callee_name"].endswith("LateBound")
        ]
        self.assertTrue(late, "late-bound member call must not be dropped")
        self.assertEqual(late[0]["resolution_status"], "undefined")

        form_load_calls = [
            call for call in self.payloads["frmMain.frm"]["calls"]
            if "Form_Load" in call["caller_id"]
        ]
        with_calls = [c for c in form_load_calls if c["callee_name"] == ".ProcessOrder"]
        self.assertTrue(with_calls, "With-block member call must be captured")
        self.assertEqual(
            with_calls[0].get("callee_id"), "clsOrder.ProcessOrder/1@clsOrder.cls"
        )


if __name__ == "__main__":
    unittest.main()
