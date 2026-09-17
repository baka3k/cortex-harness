"""Golden tests for the VB6 ANTLR pipeline (plan 260917-1200, M1/M2).

Runs the real engine=antlr batch (worker subprocess) over the fixture corpus,
resolves through the VB6 project-model resolver, and measures the payload
against expected.json:

- M2: >=80% of expected-resolvable callsites resolve to the expected callee
- zero-drop: every expected callsite appears in CALLS or POSSIBLE_CALLS tier
- no_call trap: the string literal must not produce an edge
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from test_vb6_baseline import expected_callsites, load_expected  # noqa: E402
from tools.vb.vb6_resolver import resolve_vb6_calls  # noqa: E402
from tools.vb.vb_analyzer_base import (  # noqa: E402
    _PARSER_FACTORY,
    _parse_vb6_with_antlr_batch,
)

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "vb6-application"
JAVA_AVAILABLE = os.path.exists("/usr/bin/java") or os.environ.get("JAVA_HOME") is not None


def run_antlr_pipeline():
    files = [
        str(path) for path in sorted(FIXTURE_DIR.iterdir())
        if path.suffix.lower() in {".bas", ".cls", ".frm", ".ctl", ".pag"}
    ]
    payloads = asyncio.run(_parse_vb6_with_antlr_batch(
        parse_files=files,
        all_source_files=files,
        root=str(FIXTURE_DIR),
        parse_fn=_PARSER_FACTORY["vb6"],
        cache_dir=None,
        parse_cache=False,
        vb6_parser_engine="antlr",
        vb6_antlr_timeout_sec=300.0,
        vb6_antlr_workspace_timeout_ms=120000,
        verbose=False,
    ))
    functions = [fn for payload in payloads for fn in payload["functions"]]
    calls = [call for payload in payloads for call in payload["calls"]]
    resolve_vb6_calls(functions, calls, payloads=payloads)
    edges_by_file = {
        payload["file_def"].file_path: [call.__dict__ for call in payload["calls"]]
        for payload in payloads
    }
    return payloads, edges_by_file


def _matches(edge: dict, site: dict, *, line_tolerance: int = 1) -> bool:
    observed = (edge.get("callee_name") or "").lower().lstrip(".")
    wanted = (site["callee_name"] or "").lower().lstrip(".")
    if not (observed == wanted or observed.rsplit(".", 1)[-1] == wanted.rsplit(".", 1)[-1]):
        return False
    if not wanted:
        return False
    return abs(int(edge.get("call_line") or 0) - int(site.get("line") or 0)) <= line_tolerance


@unittest.skipUnless(JAVA_AVAILABLE, "java not available")
class Vb6GoldenAntlrTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payloads, cls.edges_by_file = run_antlr_pipeline()
        cls.sites = expected_callsites(load_expected())

    def _denominator(self):
        return [site for site in self.sites if site["expect_resolution"] == "resolved"]

    @staticmethod
    def _target_ok(edge: dict, site: dict) -> bool:
        """Strict target check: callee id prefix AND property kind (Get=0
        params vs Let=1) when expected — a plain-name match once masked a
        Let-call rebound to the Get symbol (reviewer F7)."""
        if not edge.get("callee_id"):
            return False
        wanted = (site.get("expect_callee") or "").lower()
        got = (edge.get("callee_id") or "").lower()
        if not wanted or not (wanted in got or got.startswith(wanted)):
            return False
        expected_kind = site.get("expect_kind") or ""
        if expected_kind in {"property get", "property let"}:
            arity = int(got.split("/")[1].split("@")[0]) if "/" in got else -1
            want_arity = 0 if expected_kind == "property get" else 1
            if arity != want_arity:
                return False
        return True

    def test_m2_recall_on_expected_resolvable(self) -> None:
        denominator = self._denominator()
        hits = 0
        misses = []
        for site in denominator:
            ok = any(
                _matches(edge, site) and self._target_ok(edge, site)
                for edge in self.edges_by_file.get(site["file"], [])
            )
            if ok:
                hits += 1
            else:
                misses.append(f"{site['file']}:{site['line']}:{site['callee_name']}")
        rate = 100.0 * hits / len(denominator)
        self.assertGreaterEqual(
            rate, 80.0, f"M2 recall {rate:.1f}% below 80%; missed: {misses}"
        )

    def test_zero_drop_every_site_in_some_tier(self) -> None:
        absent = []
        for site in self.sites:
            if site["expect_resolution"] == "no_call":
                continue
            if not any(
                _matches(edge, site)
                for edge in self.edges_by_file.get(site["file"], [])
            ):
                absent.append(f"{site['file']}:{site['line']}:{site['callee_name']}")
        self.assertFalse(absent, f"dropped callsites: {absent}")

    def test_string_literal_trap_has_no_edge(self) -> None:
        for site in self.sites:
            if site["expect_resolution"] != "no_call":
                continue
            bad = [
                edge for edge in self.edges_by_file.get(site["file"], [])
                if _matches(edge, site)
            ]
            self.assertFalse(bad, f"string literal produced edges: {bad}")

    def test_special_statuses_classified(self) -> None:
        by = {}
        for file_name, needle in (
            ("latebas.bas", "x.LateBound"),
            ("modMain.bas", "TestSameName"),
            ("modMain.bas", "MsgBox"),
        ):
            for edge in self.edges_by_file.get(file_name, []):
                # exact callee_name: the fixture also carries a resolved
                # frmMain.TestSameName receiver call (UseGlobalForm) that
                # endswith-matching would conflate with the ambiguous site
                if (edge.get("callee_name") or "") == needle:
                    by[needle] = edge.get("resolution_status")
        self.assertEqual(by.get("x.LateBound"), "late_bound")
        self.assertEqual(by.get("TestSameName"), "ambiguous")
        self.assertEqual(by.get("MsgBox"), "external")

    def test_m1_idioms_payload_level(self) -> None:
        # the five M1 idioms: no-paren sub call, Call x, line continuation,
        # cross-module unqualified, MsgBox external — all present with edges
        must_exist = (
            ("modMain.bas", "DoSomething", "modMain.DoSomething"),
            ("modMain.bas", "InitData", "modMain.InitData"),
            ("modMain.bas", "LogMessage", "modUtil.LogMessage"),
            ("modMain.bas", "CalcTotal", "modUtil.CalcTotal"),
            ("frmMain.frm", "modMain.DoWork", "modMain.DoWork"),
        )
        for file_name, member, expected_target in must_exist:
            found = [
                edge for edge in self.edges_by_file.get(file_name, [])
                if (edge.get("callee_name") or "").endswith(member)
                and expected_target in (edge.get("callee_id") or "")
            ]
            self.assertTrue(found, f"{file_name}: no resolved edge to {expected_target}")


if __name__ == "__main__":
    unittest.main()
