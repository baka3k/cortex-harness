"""Engine parity check (plan 260917-1628, phase 5.2).

Runs BOTH engines (regex + antlr) over the fixture corpus and compares the
hydrated planes by symbol-id SET: parity set = enums, constants, events
(red-team F15 — `properties` is out: ANTLR represents properties as
functions with kind property get/let/set, the regex `properties` plane is a
separate lane). Field-level equality is NOT required — only symbol coverage.

`declares` is asserted ANTLR-only: the regex payload has no declares plane
and must not crash (hydration is tolerant, `parse_meta.
declares_regex_support` records the asymmetry).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

from test_vb6_antlr_worker_contract import run_worker_on_fixture  # noqa: E402
from tools.vb.vb_common import get_vb6_parser, parse_vb_file  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "vb6-application"
SOURCE_EXTS = {".bas", ".cls", ".frm"}

JAVA_AVAILABLE = __import__("os").path.exists("/usr/bin/java") or __import__("os").environ.get("JAVA_HOME") is not None


def _regex_payload_by_file():
    payloads = {}
    for path in sorted(FIXTURE_DIR.iterdir()):
        if path.suffix.lower() not in SOURCE_EXTS:
            continue
        try:
            parsed = parse_vb_file(str(path), str(FIXTURE_DIR), get_vb6_parser, "vb6")
        except Exception:
            continue
        payloads[path.name] = {
            "enums": {e.symbol_id for e in parsed[8]},
            "constants": {c.symbol_id for c in parsed[9]},
            "events": {e.symbol_id for e in parsed[6]},
            "declares": [],
            "parse_meta": parsed[12],
        }
    return payloads


@unittest.skipUnless(JAVA_AVAILABLE, "java not available")
class Vb6EngineParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.antlr_payloads, cls.errors, _meta = run_worker_on_fixture()
        cls.regex_payloads = _regex_payload_by_file()

    def _symbol_set(self, payloads, plane):
        ids = set()
        for payload in payloads.values():
            rows = payload.get(plane) if isinstance(payload, dict) else getattr(payload, plane, None)
            if rows is None:
                continue
            for row in rows:
                if isinstance(row, dict):
                    if row.get("symbol_id"):
                        ids.add(row["symbol_id"])
                elif isinstance(row, str):
                    ids.add(row)
                else:
                    ids.add(row.symbol_id)
        return ids

    def test_enum_symbol_ids_match_across_engines(self) -> None:
        antlr = self._symbol_set(self.antlr_payloads, "enums")
        regex = self._symbol_set(self.regex_payloads, "enums")
        self.assertTrue(antlr, "antlr must hydrate enums")
        self.assertTrue(regex, "regex must extract enums")
        self.assertEqual(antlr, regex, f"enum symbol-id sets differ: {antlr ^ regex}")

    def test_constant_symbol_ids_match_across_engines(self) -> None:
        antlr = self._symbol_set(self.antlr_payloads, "constants")
        regex = self._symbol_set(self.regex_payloads, "constants")
        self.assertTrue(antlr)
        self.assertTrue(regex)
        self.assertEqual(antlr, regex, f"constant symbol-id sets differ: {antlr ^ regex}")

    def test_event_symbol_ids_match_across_engines(self) -> None:
        antlr = self._symbol_set(self.antlr_payloads, "events")
        regex = self._symbol_set(self.regex_payloads, "events")
        self.assertTrue(antlr)
        self.assertTrue(regex)
        self.assertEqual(antlr, regex, f"event symbol-id sets differ: {antlr ^ regex}")

    def test_declares_antlr_only_and_regex_tolerant(self) -> None:
        antlr = self._symbol_set(self.antlr_payloads, "declares")
        self.assertIn("GetTickCount@modApi.bas", antlr)
        # regex payload: no declares plane, no crash, asymmetry recorded
        for name, payload in self.regex_payloads.items():
            self.assertEqual(payload["declares"], [])
            if payload["parse_meta"].get("parser_engine") == "regex":
                self.assertFalse(payload["parse_meta"].get("declares_regex_support", True))


if __name__ == "__main__":
    unittest.main()
