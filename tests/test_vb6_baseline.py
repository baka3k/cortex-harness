"""Phase 01 baseline for the vb6-application fixture corpus (plan 260917-1200).

Runs the CURRENT regex engine (parse_vb_file + resolve_calls) over the golden
corpus and records the reference-zero numbers into baseline.md.  The
assertions here only guard against rot (corpus shape, engine runs); quality
gates (M1/M2) are asserted by the engine-specific tests in later phases using
the same matching helpers exported from this module.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.vb.vb_common import (  # noqa: E402
    CallEdge,
    FunctionDef,
    get_vb6_parser,
    parse_vb_file,
    resolve_calls,
)

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "vb6-application"
EXPECTED_PATH = FIXTURE_DIR / "expected.json"
BASELINE_PATH = ROOT / "docs" / "plans" / "260917-1200-vb6-antlr-call-graph" / "baseline.md"

WELL_FORMED_FILES = (
    "modMain.bas",
    "modUtil.bas",
    "modA.bas",
    "modB.bas",
    "latebas.bas",
    "clsOrder.cls",
    "clsShip.cls",
    "IShip.cls",
    "frmMain.frm",
    "frmAbout.frm",
)


def load_expected() -> Dict[str, Any]:
    return json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))


def expected_callsites(expected: Dict[str, Any]) -> List[Dict[str, Any]]:
    sites: List[Dict[str, Any]] = []
    for file_name, spec in expected.get("files", {}).items():
        for site in spec.get("callsites", []):
            item = dict(site)
            item["file"] = file_name
            sites.append(item)
    return sites


def _norm(name: str) -> str:
    return (name or "").strip().lstrip(".").lower()


def _last_segment(name: str) -> str:
    return _norm(name).rsplit(".", 1)[-1]


def callsite_matches(edge: Dict[str, Any], site: Dict[str, Any]) -> bool:
    """Name-based match between an observed call edge and an expected site."""

    observed = _norm(str(edge.get("callee_name") or ""))
    if not observed:
        return False
    wanted = _norm(str(site.get("callee_name") or ""))
    if not wanted:
        return False
    if observed == wanted:
        return True
    observed_simple = observed.rsplit(".", 1)[-1]
    wanted_simple = wanted.rsplit(".", 1)[-1]
    return observed_simple == wanted_simple and bool(observed_simple)


def match_callsite(
    site: Dict[str, Any],
    edges_by_file: Dict[str, List[Dict[str, Any]]],
    *,
    line_tolerance: int = 1,
) -> Optional[Dict[str, Any]]:
    """Return the best matching observed edge for an expected site, if any.

    Prefers name+line matches (within tolerance), then name-only matches so
    the report can distinguish "captured but wrong line" from "missed".
    """

    file_edges = edges_by_file.get(site["file"], [])
    by_name = [edge for edge in file_edges if callsite_matches(edge, site)]
    if not by_name:
        return None
    wanted_line = int(site.get("line") or 0)
    near = [
        edge
        for edge in by_name
        if abs(int(edge.get("call_line") or 0) - wanted_line) <= line_tolerance
    ]
    return (near or by_name)[0]


def run_regex_baseline() -> Dict[str, Any]:
    """Parse the corpus with the current regex path and measure capture/resolution."""

    functions: List[FunctionDef] = []
    calls: List[CallEdge] = []
    edges_by_file: Dict[str, List[Dict[str, Any]]] = {}
    files_ok = 0
    for name in sorted(FIXTURE_DIR.iterdir()):
        if name.suffix.lower() not in {".bas", ".cls", ".frm", ".ctl", ".pag"}:
            continue
        try:
            payload = parse_vb_file(
                str(name), str(FIXTURE_DIR), get_vb6_parser, "vb6"
            )
        except Exception:
            continue
        file_functions, file_calls = payload[0], payload[1]
        functions.extend(file_functions)
        calls.extend(file_calls)
        rel = name.name
        edges_by_file[rel] = [call.__dict__ for call in file_calls]
        if file_functions:
            files_ok += 1

    resolved_before = sum(1 for call in calls if call.callee_id)
    resolve_calls(functions, calls)
    resolved_after = sum(1 for call in calls if call.callee_id)

    expected = load_expected()
    sites = expected_callsites(expected)
    denominator = [site for site in sites if site["expect_resolution"] == "resolved"]
    captured = sum(1 for site in sites if match_callsite(site, edges_by_file))
    captured_resolvable = sum(
        1 for site in denominator if match_callsite(site, edges_by_file)
    )
    false_positives: List[str] = []
    no_call_sites = [site for site in sites if site["expect_resolution"] == "no_call"]
    for site in no_call_sites:
        hit = match_callsite(site, edges_by_file)
        if hit is not None:
            false_positives.append(f"{site['file']}:{site['line']}:{site['callee_name']}")

    return {
        "functions": len(functions),
        "calls": len(calls),
        "files_ok": files_ok,
        "resolved_before": resolved_before,
        "resolved_after": resolved_after,
        "expected_sites": len(sites),
        "expected_resolvable": len(denominator),
        "captured": captured,
        "captured_resolvable": captured_resolvable,
        "false_positives": false_positives,
        "edges_by_file": edges_by_file,
    }


class Vb6BaselineTest(unittest.TestCase):
    def test_corpus_and_expected_contract_shape(self) -> None:
        expected = load_expected()
        self.assertGreaterEqual(len(expected.get("case_matrix", [])), 15)
        sites = expected_callsites(expected)
        self.assertGreaterEqual(len(sites), 40)
        resolvable = [site for site in sites if site["expect_resolution"] == "resolved"]
        self.assertGreaterEqual(len(resolvable), 20)
        for file_name in WELL_FORMED_FILES:
            self.assertIn(file_name, expected["files"], f"missing fixture spec: {file_name}")
        self.assertTrue((FIXTURE_DIR / "Sample.vbp").exists())
        self.assertTrue((FIXTURE_DIR / "malformed.bas").exists())

    def test_regex_baseline_runs_and_records_reference_zero(self) -> None:
        stats = run_regex_baseline()
        self.assertGreaterEqual(stats["files_ok"], len(WELL_FORMED_FILES) - 1)
        self.assertGreater(stats["functions"], 0)
        self.assertGreaterEqual(stats["calls"], 1)

        lines = [
            "# VB6 Fixture Baseline (regex engine) — reference zero",
            "",
            "Recorded by tests/test_vb6_baseline.py (phase 01). All later",
            "improvement claims (M1/M2/M4) are measured against these numbers.",
            "",
            f"- date: 2026-09-17",
            f"- engine: regex (parse_vb_file + resolve_calls)",
            f"- files parsed with >=1 function: {stats['files_ok']}",
            f"- functions extracted: {stats['functions']}",
            f"- raw call edges: {stats['calls']}",
            f"- resolved after resolve_calls: {stats['resolved_after']} / {stats['calls']}",
            "",
            "## Capture against expected.json",
            "",
            f"- expected callsites: {stats['expected_sites']}",
            f"- expected-resolvable (M2 denominator): {stats['expected_resolvable']}",
            f"- captured (name match): {stats['captured']} / {stats['expected_sites']}",
            f"- captured of expected-resolvable: {stats['captured_resolvable']} / {stats['expected_resolvable']}",
            f"- string-literal trap false positives: {len(stats['false_positives'])}"
            + (f" ({', '.join(stats['false_positives'])})" if stats["false_positives"] else ""),
            "",
            "Known regex limitations demonstrated by this baseline: no-paren Sub",
            "calls, `Call x`, line continuations and unqualified cross-module",
            "calls are invisible to _CALL_RE (requires '('); string literals can",
            "produce false calls; resolve_calls picks sorted(candidates)[0] with",
            "no arity/project model.",
            "",
        ]
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
