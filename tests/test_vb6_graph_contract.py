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


@unittest.skipUnless(JAVA_AVAILABLE, "java not available")
class Vb6GoldenUiTraceTest(unittest.TestCase):
    """Plan 260924 M5: golden UI trace login→menu, driver-level multi-hop.

    Fixture story (frmAnchor.frm + modState.bas + frmMain.frm): the
    cmdSubmit_Click handler is wired to its control, reads control state
    (txtUsername/txtPassword .Text), writes module state (AppStatus),
    navigates through `With frmMain ... .Show` (→ frmMain Type node), and
    calls modState.ResetStatus, which reads back the same state. No eye of
    the trace may be broken.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.driver = CapturingDriver()
        cls.writer = LanguageCodeWriter(cls.driver, database="vb6trace", batch_size=100)
        asyncio.run(base.build_call_graph(
            str(FIXTURE_DIR),
            dialect="vb6",
            code_writer=cls.writer,
            qdrant_writer=None,
            embedder=None,
            project_id="vb6-ui-trace",
            project_name="vb6-ui-trace",
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
        cls.rel_rows = [
            row for query, params, _db in cls.driver.calls
            if "MERGE (a)-[r:" in query
            for row in params.get("rows", [])
        ]
        cls.control_ids = {
            row["id"] for query, params, _db in cls.driver.calls
            if "MERGE (c:Control" in query for row in params.get("rows", [])
        }

    # -- helpers -------------------------------------------------------

    def _rels(self, rel_type: str, **match):
        rows = [row for row in self.rel_rows if row.get("rel_type") == rel_type]
        for key, value in match.items():
            rows = [row for row in rows if row.get(key) == value]
        return rows

    def _calls_rows_by_caller(self, caller_id: str):
        rows = []
        for query, params, _db in self.driver.calls:
            # write_calls merges with `(caller)-[r:CALLS]->(callee)`
            if ":CALLS]" in query and "MERGE" in query:
                for row in params.get("rows", []):
                    if row.get("caller_id") == caller_id:
                        rows.append(row)
        return rows

    # -- node plane: controls + HAS_CONTROL ----------------------------

    def test_control_nodes_and_has_control(self) -> None:
        for name in ("frmAnchor.cmdSubmit@control", "frmAnchor.txtUsername@control",
                     "frmAnchor.txtPassword@control", "frmAnchor.cboType@control"):
            self.assertIn(name, self.control_ids)
        # pseudo-control never gets a node
        self.assertNotIn("frmAnchor.frmAnchor@control", self.control_ids)
        has = self._rels("HAS_CONTROL", source_id="frmAnchor@frmAnchor.frm")
        targets = {row["target_id"] for row in has}
        self.assertIn("frmAnchor.cmdSubmit@control", targets)
        self.assertTrue(all(row["source_label"] == "Type" for row in has))

    # -- eye 1: control → handler (WIRED_TO) ---------------------------

    def test_eye1_handler_wired_to_control(self) -> None:
        wired = self._rels(
            "WIRED_TO", source_id="frmAnchor.cmdSubmit_Click/0@frmAnchor.frm",
        )
        self.assertIn(
            ("frmAnchor.cmdSubmit@control", "Control"),
            {(row["target_id"], row["target_label"]) for row in wired},
        )
        # pseudo-control lifecycle → the form's Type node
        load = self._rels("WIRED_TO", source_id="frmAnchor.Form_Load/0@frmAnchor.frm")
        self.assertIn(
            ("frmAnchor@frmAnchor.frm", "Type"),
            {(row["target_id"], row["target_label"]) for row in load},
        )
        # class lifecycle (plan 260924: Class pseudo-control → Type node)
        init = self._rels("WIRED_TO", source_id="clsSink.Class_Initialize/0@clsSink.cls")
        self.assertIn(
            ("clsSink@clsSink.cls", "Type"),
            {(row["target_id"], row["target_label"]) for row in init},
        )

    # -- eye 2: handler → control state (USES with member/access) ------

    def test_eye2_handler_reads_control_state(self) -> None:
        uses = self._rels(
            "USES", source_id="frmAnchor.cmdSubmit_Click/0@frmAnchor.frm",
        )
        username = [
            row for row in uses if row["target_id"] == "frmAnchor.txtUsername@control"
        ]
        self.assertTrue(username, "control state read eye broken")
        self.assertEqual(username[0]["properties"]["member"], "Text")
        self.assertEqual(username[0]["properties"]["access"], "read")
        password = [
            row for row in uses
            if row["target_id"] == "frmAnchor.txtPassword@control"
            and row["properties"].get("member") == "Text"
        ]
        self.assertTrue(password)

    # -- eye 3: handler → module state write ---------------------------

    def test_eye3_handler_writes_module_state(self) -> None:
        uses = self._rels(
            "USES", source_id="frmAnchor.cmdSubmit_Click/0@frmAnchor.frm",
        )
        app_rows = [
            row for row in uses
            if row["target_id"] == "modState.AppStatus@modState.bas"
        ]
        self.assertTrue(app_rows, "state write eye broken")
        self.assertEqual(app_rows[0]["target_label"], "Variable")
        self.assertEqual(app_rows[0]["properties"]["access"], "write")

    # -- eye 4: With <form> ... .Show navigation → Type node -----------

    def test_eye4_form_nav_through_with_target(self) -> None:
        uses = self._rels(
            "USES", source_id="frmAnchor.cmdSubmit_Click/0@frmAnchor.frm",
        )
        nav = [row for row in uses if row["target_id"] == "frmMain@frmMain.frm"]
        self.assertTrue(nav, "With <form> .Show navigation eye broken")
        self.assertEqual(nav[0]["target_label"], "Type")
        self.assertEqual(nav[0]["properties"].get("member"), "Show")
        # and no strict CALLS edge was published at all in this fixture run
        self.assertFalse(self._rels("CALLS"))

    # -- eye 5: cross-module call reads the state back -----------------

    def test_eye5_cross_module_call_reads_state_back(self) -> None:
        calls = self._calls_rows_by_caller("frmAnchor.cmdSubmit_Click/0@frmAnchor.frm")
        self.assertIn(
            "modState.ResetStatus/0@modState.bas",
            {row["callee_id"] for row in calls},
        )
        reset_uses = self._rels(
            "USES", source_id="modState.ResetStatus/0@modState.bas",
        )
        app_rows = [
            row for row in reset_uses
            if row["target_id"] == "modState.AppStatus@modState.bas"
        ]
        self.assertTrue(any(row["properties"]["access"] == "read" for row in app_rows))
        self.assertTrue(any(row["properties"]["access"] == "write" for row in app_rows))

    # -- type anchors: instantiations + COM receivers ------------------

    def test_instantiation_and_com_type_uses(self) -> None:
        inst = self._rels(
            "INSTANTIATES", source_id="frmMain.Form_Load/0@frmMain.frm",
        )
        self.assertIn("clsOrder@clsOrder.cls", {row["target_id"] for row in inst})
        com = [
            row for row in self.rel_rows
            if row.get("rel_type") == "USES_TYPE"
            and row.get("target_id") == "external::vb6/adodb.recordset"
        ]
        self.assertTrue(com, "ADODB.Recordset USES_TYPE eye broken")
        self.assertEqual(com[0]["properties"]["com_type"], "ADODB.Recordset")

    def test_redim_mutation_rows(self) -> None:
        rows = [
            row for row in self.rel_rows
            if row.get("rel_type") == "USES"
            and row["properties"].get("mutation") == "redim_preserve"
            and row["source_id"] == "frmAnchor.cmdSubmit_Click/0@frmAnchor.frm"
        ]
        self.assertTrue(rows, "ReDim Preserve mutation row missing")
        self.assertEqual(rows[0]["target_label"], "Variable")

    # -- multi-hop traversal: control → impacted procs/state -----------

    def test_multi_hop_trace_from_control(self) -> None:
        def wired_handlers(control_id):
            return [
                row["source_id"] for row in self.rel_rows
                if row["rel_type"] == "WIRED_TO" and row["target_id"] == control_id
            ]

        def outgoing(symbol_id):
            edges = [row for row in self.rel_rows if row["source_id"] == symbol_id]
            edges += [
                {"rel_type": "CALLS", "target_id": row["callee_id"]}
                for row in self._calls_rows_by_caller(symbol_id)
            ]
            return edges

        handlers = wired_handlers("frmAnchor.cmdSubmit@control")
        self.assertIn("frmAnchor.cmdSubmit_Click/0@frmAnchor.frm", handlers)
        seen = set()
        for handler in handlers:
            for edge in outgoing(handler):
                seen.add((edge["rel_type"], edge["target_id"]))
        # every eye of the golden story reachable in ONE traversal
        self.assertIn(("USES", "frmAnchor.txtUsername@control"), seen)
        self.assertIn(("USES", "modState.AppStatus@modState.bas"), seen)
        self.assertIn(("CALLS", "modState.ResetStatus/0@modState.bas"), seen)
        self.assertIn(("USES", "frmMain@frmMain.frm"), seen)

    def test_external_placeholders_not_exported(self) -> None:
        # review M3: placeholder external stubs must not pollute the
        # public-surface (exported/is_public_api) queries
        placeholders = [
            row for query, params, _db in self.driver.calls
            if "MERGE (f:Function" in query
            for row in params.get("rows", [])
            if row.get("kind") == "external_symbol"
        ]
        self.assertTrue(placeholders, "no external placeholders in this run")
        for row in placeholders:
            self.assertFalse(row.get("exported", True), row["id"])
            self.assertFalse(row.get("is_public_api", True), row["id"])


@unittest.skipUnless(JAVA_AVAILABLE, "java not available")
class Vb6ComReceiverWithoutNewTest(unittest.TestCase):
    """Review C1 regression: `Dim cn As ADODB.Connection` + member access with
    NO `New` anywhere must NOT abort the relations batch (endpoint preflight
    is fail-closed) — the unguarded USES_TYPE row is dropped instead."""

    @classmethod
    def setUpClass(cls) -> None:
        import tempfile

        cls.tmp = tempfile.TemporaryDirectory(prefix="vb6_com_nodb_")
        source = (
            'Attribute VB_Name = "modCom"\n'
            "Option Explicit\n\n"
            "Public Sub QueryAll()\n"
            "    Dim cn As ADODB.Connection\n"
            "    cn.Open \"DSN=x\"\n"
            "    cn.Close\n"
            "End Sub\n"
        )
        (Path(cls.tmp.name) / "modCom.bas").write_text(source, encoding="utf-8")
        cls.driver = CapturingDriver()
        cls.writer = LanguageCodeWriter(cls.driver, database="vb6comnodb", batch_size=100)
        cls.error = None
        try:
            asyncio.run(base.build_call_graph(
                cls.tmp.name,
                dialect="vb6",
                code_writer=cls.writer,
                qdrant_writer=None,
                embedder=None,
                project_id="vb6-com-nodb",
                project_name="vb6-com-nodb",
                language="vb6",
                repo=cls.tmp.name,
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
        except Exception as exc:  # noqa: BLE001
            cls.error = exc

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_write_completes_without_preflight_abort(self) -> None:
        self.assertIsNone(self.error, f"relations batch aborted: {self.error}")

    def test_no_uses_type_to_missing_node(self) -> None:
        # the com receiver stays annotated in payload, but no edge is
        # fabricated to an external Type node that no New ever created
        uses_type = [
            row for query, params, _db in self.driver.calls
            if "r:USES_TYPE" in query
            for row in params.get("rows", [])
            if str(row.get("target_id", "")).startswith("external::")
        ]
        self.assertEqual(uses_type, [])


if __name__ == "__main__":
    unittest.main()
