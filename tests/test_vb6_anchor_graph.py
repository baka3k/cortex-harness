"""Plan 260924 phase-01 gates: anchor graph schema, writer plane, builders.

Covers the phase-01 exit criteria:

- schema/manifest: ``Control`` node spec + id-index, HAS_CONTROL/WIRED_TO/
  INSTANTIATES rel specs, the USES (Function, Control) pair, Variable.is_static
  column — and DDL that compiles (compile_rel_ddl raises on unspecced rels)
- write path: a Control node + HAS_CONTROL (Type→Control) + WIRED_TO
  (Function→Control) + USES (Function→Control) ride the REAL writer against a
  capturing driver without raising preflight
- builders: control_rows / has_control_rows / wiring_rows /
  instantiation_rows over fake payloads, target-in-batch guarded
- publication: asdict_function derives exported/is_public_api from is_private
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

from tools.graph.schema import ladybug_schema  # noqa: E402
from tools.graph.schema.manifest import CODE_GRAPH_SCHEMA  # noqa: E402
from tools.graph.writer.language_writer import LanguageCodeWriter  # noqa: E402
from tools.vb.vb_analyzer_base import (  # noqa: E402
    control_rows,
    has_control_rows,
    instantiation_rows,
    vb6_control_index,
    wiring_rows,
)
from tools.vb.vb_common import (  # noqa: E402
    FunctionDef,
    VariableDef,
    asdict_function,
    asdict_variable,
)


class CapturingDriver:
    def __init__(self):
        self.calls = []

    async def execute_query(self, query, parameters=None, database=None):
        self.calls.append((query, parameters or {}, database))
        rows = (parameters or {}).get("rows", [])
        return ([{"count": len(rows)}], [], None)

    async def create_indexes(self, specs, database=None):
        return None


def _fn(module, name, *, arity=0, private=False, rel=None, event="", control_type=""):
    rel = rel or f"{module}.frm"
    qualified = f"{module}.{name}"
    fn = FunctionDef(
        symbol_id=f"{qualified}/{arity}@{rel}",
        qualified_name=qualified,
        name=name,
        kind="sub",
        class_name=None,
        namespace_name=None,
        file_path=rel,
        start_line=10,
        end_line=20,
        arity=arity,
        code="",
        module_name=module,
        is_private=private,
    )
    if event:
        fn.vb6_event = event
        fn.vb6_control_type = control_type
    return fn


def _payload(rel, module, *, functions=(), controls=(), classes=(), instantiations=()):
    return {
        "file_def": type("FD", (), {"file_path": rel})(),
        "parse_meta": {"module_name": module},
        "functions": list(functions),
        "controls": list(controls),
        "classes": list(classes),
        "instantiations": list(instantiations),
    }


SCOPE = dict(
    project_id="p1",
    project_name="p1",
    language="vb6",
    repo="/repo",
    build_system="",
)


class AnchorSchemaTest(unittest.TestCase):
    def test_control_node_spec_and_id_index(self) -> None:
        pk, columns = ladybug_schema.node_spec("Control")
        self.assertEqual(pk, "id")
        for column in ("qualified_name", "kind", "scope_name", "class_name",
                       "line_number", "package_name"):
            self.assertIn(column, columns)
        self.assertTrue(CODE_GRAPH_SCHEMA.has_identity_index("Control", "id"))

    def test_new_rel_specs(self) -> None:
        pairs, _cols = ladybug_schema.rel_spec("HAS_CONTROL")
        self.assertIn(("Type", "Control"), pairs)
        pairs, _cols = ladybug_schema.rel_spec("WIRED_TO")
        self.assertIn(("Function", "Control"), pairs)
        self.assertIn(("Function", "Type"), pairs)
        pairs, _cols = ladybug_schema.rel_spec("INSTANTIATES")
        self.assertIn(("Function", "Type"), pairs)

    def test_uses_gains_function_control_pair(self) -> None:
        pairs, _cols = ladybug_schema.rel_spec("USES")
        self.assertIn(("Function", "Control"), pairs)
        # AD-01: forms ride the types lane, so (Function, Class) must NOT exist
        self.assertNotIn(("Function", "Class"), pairs)

    def test_variable_gains_is_static_bool_column(self) -> None:
        _pk, columns = ladybug_schema.node_spec("Variable")
        self.assertIn("is_static", columns)
        self.assertEqual(ladybug_schema.column_type("is_static"), "BOOL")

    def test_ddl_compiles_for_new_rels(self) -> None:
        for rel_type, endpoints in (
            ("HAS_CONTROL", [("Type", "Control")]),
            ("WIRED_TO", [("Function", "Control"), ("Function", "Type")]),
            ("INSTANTIATES", [("Function", "Type")]),
        ):
            ddl = ladybug_schema.compile_rel_ddl(rel_type, endpoints)
            self.assertIn(f"CREATE REL TABLE IF NOT EXISTS `{rel_type}`", ddl)
        self.assertIn("CREATE NODE TABLE IF NOT EXISTS `Control`",
                      ladybug_schema.compile_node_ddl("Control"))


class AnchorWritePathSmokeTest(unittest.TestCase):
    """1 Control node + HAS_CONTROL + WIRED_TO + USES through the real writer."""

    CONTROL_ROW = {
        "id": "frmLogin.cmdSubmit@control",
        "name": "cmdSubmit",
        "qualified_name": "frmLogin.cmdSubmit",
        "kind": "VB.CommandButton",
        "scope_name": "frmLogin",
        "class_name": "frmLogin",
        "package_name": "",
        "file_path": "frmLogin.frm",
        "line_number": 5,
        "code": "",
        "comment": "",
        "summary": "",
        "note": "",
        "project_id": "p1",
        "project_id_normalized": "p1",
        "project_name": "p1",
        "language": "vb6",
        "repo": "/repo",
        "build_system": "",
    }

    RELATIONS = [
        {
            "source_id": "frmLogin@frmLogin.frm",
            "source_label": "Type",
            "target_id": "frmLogin.cmdSubmit@control",
            "target_label": "Control",
            "rel_type": "HAS_CONTROL",
            "properties": {},
        },
        {
            "source_id": "frmLogin.cmdSubmit_Click/0@frmLogin.frm",
            "source_label": "Function",
            "target_id": "frmLogin.cmdSubmit@control",
            "target_label": "Control",
            "rel_type": "WIRED_TO",
            "properties": {"event": "cmdSubmit.Click"},
        },
        {
            "source_id": "frmLogin.cmdSubmit_Click/0@frmLogin.frm",
            "source_label": "Function",
            "target_id": "frmLogin.cmdSubmit@control",
            "target_label": "Control",
            "rel_type": "USES",
            "properties": {"member": "Text", "access": "read"},
        },
    ]

    def test_control_node_and_three_edges_through_write_all(self) -> None:
        driver = CapturingDriver()
        writer = LanguageCodeWriter(driver, database="p1smoke", batch_size=10)
        counts = asyncio.run(writer.write_all(
            projects=[{"id": "p1", "name": "p1", "language": "vb6",
                       "repo": "/repo", "root": "/repo", "build_system": ""}],
            controls=[self.CONTROL_ROW],
            relations=list(self.RELATIONS),
            use_full_writers=True,
        ))
        self.assertEqual(counts.get("controls"), 1)
        self.assertEqual(counts.get("relations"), 3)

        control_queries = [
            query for query, _params, _db in driver.calls if "MERGE (c:Control" in query
        ]
        self.assertTrue(control_queries, "Control node plane never written")
        for rel_type in ("HAS_CONTROL", "WIRED_TO", "USES"):
            merges = [
                query for query, _params, _db in driver.calls
                if f"MERGE (a)-[r:{rel_type}]" in query
            ]
            self.assertTrue(merges, f"{rel_type} never written")


class AnchorBuilderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.handler = _fn("frmLogin", "cmdSubmit_Click", event="cmdSubmit.Click",
                           control_type="VB.CommandButton")
        self.lifecycle = _fn("frmLogin", "Form_Load", event="Form.Load",
                             control_type="Form")
        self.plain = _fn("frmLogin", "Helper")
        self.classes = [type("CD", (), {"symbol_id": "frmLogin@frmLogin.frm"})()]
        self.controls = [
            {"name": "frmLogin", "type": "VB.Form", "line": 1},
            {"name": "cmdSubmit", "type": "VB.CommandButton", "line": 5},
            {"name": "txtUsername", "type": "VB.TextBox", "line": 12},
        ]
        self.payload = _payload(
            "frmLogin.frm", "frmLogin",
            functions=[self.handler, self.lifecycle, self.plain],
            controls=self.controls,
            classes=self.classes,
        )
        self.payloads = [self.payload]

    def test_control_rows_skip_pseudo_controls(self) -> None:
        rows = control_rows(self.payloads, **SCOPE)
        ids = {row["id"] for row in rows}
        self.assertEqual(ids, {"frmLogin.cmdSubmit@control", "frmLogin.txtUsername@control"})
        row = rows[0]
        self.assertEqual(row["qualified_name"], "frmLogin.cmdSubmit")
        self.assertEqual(row["kind"], "VB.CommandButton")
        self.assertEqual(row["class_name"], "frmLogin")

    def test_has_control_rows_source_on_type(self) -> None:
        rows = has_control_rows(self.payloads)
        sources = {(row["source_label"], row["target_label"], row["rel_type"]) for row in rows}
        self.assertEqual(sources, {("Type", "Control", "HAS_CONTROL")})
        self.assertEqual(rows[0]["source_id"], "frmLogin@frmLogin.frm")

    def test_has_control_rows_guarded_when_controls_absent_from_batch(self) -> None:
        # target-in-batch guard: an empty control-node batch emits no rows
        self.assertEqual(has_control_rows(self.payloads, control_ids=set()), [])

    def test_wiring_rows_control_and_pseudo_targets(self) -> None:
        rows = wiring_rows(self.payloads)
        by_event = {row["properties"]["event"]: row for row in rows}
        self.assertIn("cmdSubmit.Click", by_event)
        self.assertEqual(by_event["cmdSubmit.Click"]["target_label"], "Control")
        self.assertEqual(
            by_event["cmdSubmit.Click"]["target_id"], "frmLogin.cmdSubmit@control"
        )
        # pseudo-control lifecycle wires to the form's Type node
        self.assertIn("Form.Load", by_event)
        self.assertEqual(by_event["Form.Load"]["target_label"], "Type")
        self.assertEqual(by_event["Form.Load"]["target_id"], "frmLogin@frmLogin.frm")
        self.assertEqual(by_event["Form.Load"]["rel_type"], "WIRED_TO")

    def test_wiring_rows_never_fire_without_annotation(self) -> None:
        self.assertEqual(wiring_rows([_payload("frmLogin.frm", "frmLogin",
                                               functions=[self.plain])]), [])

    def test_control_index_keys(self) -> None:
        index = vb6_control_index(self.payloads)
        self.assertEqual(index[("frmlogin", "cmdsubmit")], "frmLogin.cmdSubmit@control")
        self.assertNotIn(("frmlogin", "frmlogin"), index)

    def test_instantiation_project_class_resolves_to_type(self) -> None:
        order_cls = type("CD", (), {"symbol_id": "clsOrder@clsOrder.cls"})()
        payloads = [
            self.payload,
            _payload("clsOrder.cls", "clsOrder", classes=[order_cls],
                     functions=[_fn("clsOrder", "Make", rel="clsOrder.cls")],
                     instantiations=[{"proc": "Make", "name": "clsOrder", "line": 3}]),
        ]
        type_nodes, rels = instantiation_rows(payloads, **SCOPE)
        self.assertEqual(type_nodes, [])
        self.assertEqual(len(rels), 1)
        row = rels[0]
        self.assertEqual(row["rel_type"], "INSTANTIATES")
        self.assertEqual(row["source_id"], "clsOrder.Make/0@clsOrder.cls")
        self.assertEqual(row["target_id"], "clsOrder@clsOrder.cls")
        self.assertEqual(row["target_label"], "Type")

    def test_instantiation_external_class_becomes_com_type(self) -> None:
        payloads = [
            _payload("modData.bas", "modData",
                     functions=[_fn("modData", "Load", rel="modData.bas")],
                     instantiations=[{"proc": "Load", "name": "ADODB.Recordset",
                                      "line": 4}]),
        ]
        type_nodes, rels = instantiation_rows(payloads, **SCOPE)
        self.assertEqual(len(type_nodes), 1)
        self.assertEqual(type_nodes[0]["id"], "external::vb6/adodb.recordset")
        self.assertEqual(type_nodes[0]["kind"], "com")
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0]["rel_type"], "USES_TYPE")
        self.assertEqual(rels[0]["target_id"], "external::vb6/adodb.recordset")

    def test_instantiation_guarded_when_proc_unattributable(self) -> None:
        payloads = [
            _payload("modData.bas", "modData",
                     functions=[],
                     instantiations=[{"proc": "Ghost", "name": "clsOrder", "line": 4}]),
        ]
        type_nodes, rels = instantiation_rows(payloads, **SCOPE)
        self.assertEqual(type_nodes, [])
        self.assertEqual(rels, [])


class PseudoControlGateTest(unittest.TestCase):
    """Review M2: Form/MDIForm/UserControl stay designer-only; only Class
    remains matchable when a module has no designer controls."""

    def test_form_load_in_bas_never_annotated(self) -> None:
        from tools.vb.vb_analyzer_base import match_event_handlers

        fn = _fn("modPlain", "Form_Load", rel="modPlain.bas")
        wired = match_event_handlers([], [fn])
        self.assertEqual(wired, [])
        self.assertEqual(getattr(fn, "vb6_event", ""), "")

    def test_class_initialize_in_cls_annotated(self) -> None:
        from tools.vb.vb_analyzer_base import match_event_handlers

        fn = _fn("clsSink", "Class_Initialize", rel="clsSink.cls")
        wired = match_event_handlers([], [fn])
        self.assertEqual(len(wired), 1)
        self.assertEqual(fn.vb6_event, "Class.Initialize")

    def test_form_load_in_designer_file_still_annotated(self) -> None:
        from tools.vb.vb_analyzer_base import match_event_handlers

        fn = _fn("frmAnchor", "Form_Load")
        controls = [{"name": "frmAnchor", "type": "VB.Form", "line": 1}]
        wired = match_event_handlers(controls, [fn])
        self.assertEqual(len(wired), 1)
        self.assertEqual(fn.vb6_event, "Form.Load")


class ExportedPublicationTest(unittest.TestCase):
    def test_public_friend_exported_private_not(self) -> None:
        for private, expected in ((False, True), (True, False)):
            row = asdict_function(_fn("modMain", "Go", private=private), "p", "p", "vb6", "r", "")
            self.assertEqual(row["exported"], expected, f"is_private={private}")
            self.assertEqual(row["is_public_api"], expected, f"is_private={private}")


class VariableStaticRowTest(unittest.TestCase):
    def test_asdict_variable_carries_is_static(self) -> None:
        var = VariableDef(
            symbol_id="modUtil.Counter@modUtil.bas",
            qualified_name="modUtil.Counter",
            name="Counter",
            type_name="Long",
            is_global=False,
            is_shared=False,
            class_name=None,
            namespace_name=None,
            file_path="modUtil.bas",
            line_number=3,
            code="Static Counter As Long",
            is_static=True,
        )
        row = asdict_variable(var, "p", "p", "vb6", "r", "")
        self.assertTrue(row["is_static"])


JAVA_AVAILABLE = os.path.exists("/usr/bin/java") or os.environ.get("JAVA_HOME") is not None


@unittest.skipUnless(JAVA_AVAILABLE, "java not available")
class HydrationBridgeTest(unittest.TestCase):
    """P2 DoD: anchor planes survive _hydrate_payload (adapter→analyzer bridge).

    Red-team H3 — hydration drops unknown TOP-LEVEL keys silently; this test
    fails if a whitelist entry is forgotten, even when the worker emits the
    plane correctly.
    """

    FIXTURE_DIR = ROOT / "tests" / "fixtures" / "vb6-application"

    @classmethod
    def setUpClass(cls) -> None:
        import tools.vb.vb_analyzer_base as base

        files = [
            str(path) for path in sorted(cls.FIXTURE_DIR.iterdir())
            if path.suffix.lower() in {".bas", ".cls", ".frm"}
        ]
        cls.payloads = asyncio.run(base._parse_vb6_with_antlr_batch(
            parse_files=files,
            all_source_files=files,
            root=str(cls.FIXTURE_DIR),
            parse_fn=base._PARSER_FACTORY["vb6"],
            cache_dir=None,
            parse_cache=False,
            vb6_parser_engine="antlr",
            vb6_antlr_timeout_sec=300.0,
            vb6_antlr_workspace_timeout_ms=120000,
            verbose=False,
        ))

    def test_anchor_planes_survive_hydration(self) -> None:
        by_rel = {p["file_def"].file_path: p for p in self.payloads}
        anchor = by_rel["frmAnchor.frm"]
        for plane in ("instantiations", "with_targets", "ui_access", "redim"):
            self.assertIn(plane, anchor, f"{plane} dropped at hydration")
        # data-bearing planes on this fixture
        for plane in ("with_targets", "ui_access", "redim"):
            self.assertTrue(anchor[plane], f"{plane} empty after hydration")
        # instantiation data lives on frmMain / clsSink
        self.assertTrue(by_rel["frmMain.frm"]["instantiations"])
        self.assertTrue(by_rel["clsSink.cls"]["instantiations"])
        self.assertTrue(anchor["controls"], "controls[] dropped at hydration")

    def test_flags_and_signatures_survive_hydration(self) -> None:
        by_rel = {p["file_def"].file_path: p for p in self.payloads}
        sink_vars = {v.name: v for v in by_rel["clsSink.cls"]["variables"]}
        self.assertTrue(sink_vars["mSource"].with_events)
        self.assertTrue(sink_vars["buffer"].is_static)
        state_vars = {v.name: v for v in by_rel["modState.bas"]["variables"]}
        self.assertTrue(state_vars["AppStatus"].is_global)
        calc = [f for f in by_rel["modUtil.bas"]["functions"] if f.name == "CalcTotal"][0]
        self.assertEqual(calc.param_types, ["Double", "Double"])
        self.assertEqual(calc.return_type, "Double")


if __name__ == "__main__":
    unittest.main()
