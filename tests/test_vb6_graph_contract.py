"""Phase 04 graph contract: two-tier edges + integrity relations (M5 prep).

Runs build_call_graph end-to-end over the fixture with a CapturingDriver
(same pattern as test_cobol_graph_contract) and asserts the published rows:

- CALLS rows for deterministic targets (no ambiguous ever)
- POSSIBLE_CALLS rows site-keyed with standard resolution_class vocabulary
  (AD-04) and free-text vb6 resolution_status
- CONTAINS class->method and IMPLEMENTS Class->Interface relations
- placeholder external_symbol Function rows for weak callees
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "vb6-application"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.graph.writer.language_writer import LanguageCodeWriter  # noqa: E402
import tools.vb.vb_analyzer_base as base  # noqa: E402

JAVA_AVAILABLE = os.path.exists("/usr/bin/java") or os.environ.get("JAVA_HOME") is not None


class CapturingDriver:
    def __init__(self):
        self.calls = []

    async def execute_query(self, query, parameters=None, database=None):
        self.calls.append((query, parameters or {}, database))
        rows = (parameters or {}).get("rows", [])
        return ([{"count": len(rows)}], [], None)

    async def create_indexes(self, specs, database=None):
        return None


@unittest.skipUnless(JAVA_AVAILABLE, "java not available")
class Vb6GraphContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.driver = CapturingDriver()
        cls.writer = LanguageCodeWriter(cls.driver, database="vb6fixture", batch_size=100)
        asyncio.run(base.build_call_graph(
            str(FIXTURE_DIR),
            dialect="vb6",
            code_writer=cls.writer,
            qdrant_writer=None,
            embedder=None,
            project_id="vb6-fixture",
            project_name="vb6-fixture",
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
            vb6_parser_engine="antlr",
        ))

    # -- helpers ------------------------------------------------------

    def _rows(self, needle: str):
        found = []
        for query, params, _db in self.driver.calls:
            if needle in query:
                for row in params.get("rows", []):
                    found.append(row)
        return found

    def _possible_rows(self):
        return self._rows("POSSIBLE_CALLS")

    def _calls_rows(self):
        rows = []
        for query, params, _db in self.driver.calls:
            if ":CALLS" in query and "POSSIBLE" not in query:
                for row in params.get("rows", []):
                    rows.append(row)
        return rows

    def _relation_rows(self, rel_type: str):
        rows = []
        for query, params, _db in self.driver.calls:
            if f":{rel_type}" in query:
                for row in params.get("rows", []):
                    rows.append(row)
        return rows

    def _function_rows(self):
        return self._rows(":Function")

    # -- assertions ---------------------------------------------------

    def test_calls_edge_modmain_dowork_to_modutil_calctotal(self) -> None:
        rows = self._calls_rows()
        hit = [
            row for row in rows
            if row.get("caller_id") == "modMain.DoWork/0@modMain.bas"
            and row.get("callee_id") == "modUtil.CalcTotal/2@modUtil.bas"
        ]
        self.assertTrue(hit, "expected CALLS edge DoWork -> CalcTotal missing")

    def test_no_ambiguous_in_calls(self) -> None:
        for row in self._calls_rows():
            self.assertNotEqual(row.get("resolution_status"), "ambiguous")

    def test_possible_calls_use_standard_vocabulary_and_site_ids(self) -> None:
        rows = self._possible_rows()
        self.assertTrue(rows, "no POSSIBLE_CALLS published")
        for row in rows:
            props = row.get("props") or {}
            self.assertEqual(props.get("resolution_class"), "lexical_candidate")
            self.assertIn(
                props.get("resolution_status"),
                {"ambiguous", "late_bound", "external", "unresolved", "asg_resolved",
                 "name_resolved", ""},
            )
            self.assertTrue(row.get("site_id"))
            self.assertIn("semantic_provider", props)

    def test_possible_calls_cover_special_cases(self) -> None:
        rows = self._possible_rows()
        statuses = {
            (row.get("props") or {}).get("resolution_status") for row in rows
        }
        self.assertIn("late_bound", statuses)
        self.assertIn("ambiguous", statuses)
        self.assertIn("external", statuses)
        # ambiguous multi-target: two rows share one site (both candidates)
        ambiguous = [row for row in rows if (row.get("props") or {}).get("resolution_status") == "ambiguous"]
        sites = {}
        for row in ambiguous:
            sites.setdefault(row["site_id"], []).append(row["callee_id"])
        multi = {site: ids for site, ids in sites.items() if len(ids) > 1}
        self.assertTrue(multi, "ambiguous TestSameName must publish one row per candidate")

    def test_dictionary_call_survives_as_possible_with_default_member(self) -> None:
        # M2 (plan 260917-1628): the rs!FieldName dictionary row must reach
        # POSSIBLE_CALLS (never dropped, never strict CALLS) with the
        # default_member prop and a standard-vocabulary status
        rows = [
            row for row in self._possible_rows()
            if (row.get("props") or {}).get("call_type") == "dictionary_call"
        ]
        self.assertTrue(rows, "dictionary call dropped from POSSIBLE_CALLS")
        for row in rows:
            props = row.get("props") or {}
            self.assertTrue(props.get("default_member"))
            self.assertEqual(props.get("callee_name"), "rs!FieldName")
            self.assertIn(
                props.get("resolution_status"),
                {"ambiguous", "late_bound", "external", "unresolved", "asg_resolved",
                 "name_resolved", ""},
            )
            self.assertEqual(props.get("resolution_class"), "lexical_candidate")
        # and no dictionary row may appear in the strict CALLS tier
        for row in self._calls_rows():
            self.assertNotEqual(row.get("call_type"), "dictionary_call")

    def test_external_symbol_placeholders_written(self) -> None:
        rows = self._function_rows()
        placeholders = {row.get("id") for row in rows if row.get("kind") == "external_symbol"}
        self.assertTrue(placeholders)
        possible_targets = {row.get("callee_id") for row in self._possible_rows()}
        self.assertTrue(placeholders & possible_targets)

    def test_contains_class_to_method(self) -> None:
        rows = self._relation_rows("CONTAINS")
        hit = [
            row for row in rows
            if row.get("source_id") == "clsOrder@clsOrder.cls"
            and row.get("target_id") == "clsOrder.ProcessOrder/1@clsOrder.cls"
        ]
        self.assertTrue(hit, "CONTAINS clsOrder -> ProcessOrder missing")

    def test_implements_cls_to_interface(self) -> None:
        rows = self._relation_rows("IMPLEMENTS")
        pairs = {(row.get("source_id"), row.get("target_id")) for row in rows}
        self.assertIn(("clsShip@clsShip.cls", "interface::IShip@IShip.cls"), pairs)
        self.assertIn(("clsOrder@clsOrder.cls", "interface::IShip@IShip.cls"), pairs)

    def test_class_ids_carry_rel_path(self) -> None:
        # classes are written through the types lane (label :Type), matching
        # the existing vbnet writer convention
        class_rows = self._rows(":Type")
        ids = {row.get("id") for row in class_rows}
        self.assertIn("clsOrder@clsOrder.cls", ids)
        self.assertIn("frmMain@frmMain.frm", ids)

    def test_function_rows_carry_normalized_project_scope(self) -> None:
        # reviewer F3: CALLS/POSSIBLE_CALLS endpoint MATCHes use
        # (id, project_id_normalized); Function nodes must carry the property
        rows = self._function_rows()
        self.assertTrue(rows)
        for row in rows:
            self.assertTrue(
                str(row.get("project_id_normalized") or "").strip(),
                f"function row {row.get('id')} lacks project_id_normalized",
            )

    def test_interface_nodes_written(self) -> None:
        iface_rows = [
            row for query, params, _db in self.driver.calls
            if ":Interface" in query for row in params.get("rows", [])
        ]
        ids = {row.get("id") for row in iface_rows}
        self.assertIn("interface::IShip@IShip.cls", ids)


if __name__ == "__main__":
    unittest.main()
