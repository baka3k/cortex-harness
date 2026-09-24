"""Phase 04 unit tests: VB6 project-model resolver (vb6_resolver.py)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.vb.vb6_resolver import VB6ModuleRegistry, resolve_vb6_calls  # noqa: E402
from tools.vb.vb_common import CallEdge, FunctionDef  # noqa: E402


def _fn(module, name, *, arity=0, private=False, kind="sub", rel=None):
    rel = rel or f"{module}.bas"
    qualified = f"{module}.{name}"
    return FunctionDef(
        symbol_id=f"{qualified}/{arity}@{rel}",
        qualified_name=qualified,
        name=name,
        kind=kind,
        class_name=None,
        namespace_name=None,
        file_path=rel,
        start_line=1,
        end_line=2,
        arity=arity,
        code="",
        module_name=module,
        is_private=private,
    )


def _call(caller_module, caller_proc, callee_name, *, member="", arity=None, callee_id=None):
    return CallEdge(
        caller_id=f"{caller_module}.{caller_proc}/0@{caller_module}.bas",
        caller_scope=caller_module,
        callee_name=callee_name,
        callee_id=callee_id,
        callee_arity=arity,
        call_line=1,
        callee_member=member,
    )


class VB6ResolverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.functions = [
            _fn("modMain", "DoWork"),
            _fn("modUtil", "CalcTotal", arity=2, kind="function"),
            _fn("modUtil", "HelperSub", private=True),
            _fn("modA", "A_Run"),
            _fn("modA", "CalcTotal", arity=1, kind="function"),
            _fn("clsOrder", "ProcessOrder", arity=1, rel="clsOrder.cls"),
            _fn("frmMain", "TestSameName", rel="frmMain.frm"),
            _fn("frmAbout", "TestSameName", rel="frmAbout.frm"),
        ]
        self.payloads = [
            {"file_def": {"file_path": "modMain.bas"},
             "functions": [self.functions[0]], "variables": [],
             "parse_meta": {"module_name": "modMain"}},
            {"file_def": {"file_path": "modUtil.bas"},
             "functions": [self.functions[1], self.functions[2]], "variables": [],
             "parse_meta": {"module_name": "modUtil"}},
            {"file_def": {"file_path": "modA.bas"},
             "functions": [self.functions[3], self.functions[4]], "variables": [],
             "parse_meta": {"module_name": "modA"}},
            {"file_def": {"file_path": "clsOrder.cls"},
             "functions": [self.functions[5]], "variables": [],
             "parse_meta": {"module_name": "clsOrder"}},
            {"file_def": {"file_path": "frmMain.frm"},
             "functions": [self.functions[6]], "variables": [],
             "parse_meta": {"module_name": "frmMain"}},
            {"file_def": {"file_path": "frmAbout.frm"},
             "functions": [self.functions[7]], "variables": [],
             "parse_meta": {"module_name": "frmAbout"}},
        ]

    def _resolve(self, calls):
        counts = resolve_vb6_calls(self.functions, calls, payloads=self.payloads)
        return calls, counts

    def test_module_local_wins(self) -> None:
        calls = [_call("modUtil", "RunHelper", "HelperSub", member="HelperSub")]
        self._resolve(calls)
        self.assertEqual(calls[0].callee_id, "modUtil.HelperSub/0@modUtil.bas")
        self.assertEqual(calls[0].resolution_status, "name_resolved")

    def test_private_unreachable_from_other_module(self) -> None:
        calls = [_call("modMain", "DoWork", "HelperSub", member="HelperSub")]
        self._resolve(calls)
        self.assertIsNone(calls[0].callee_id)
        self.assertEqual(calls[0].resolution_status, "unresolved")

    def test_cross_module_public_unique(self) -> None:
        calls = [_call("modMain", "DoWork", "A_Run", member="A_Run")]
        self._resolve(calls)
        self.assertEqual(calls[0].callee_id, "modA.A_Run/0@modA.bas")
        self.assertEqual(calls[0].resolution_status, "name_resolved")

    def test_arity_filters_candidates(self) -> None:
        # two public CalcTotal (modUtil/2 and modA/1): arity 2 picks modUtil
        calls = [_call("modMain", "DoWork", "CalcTotal", member="CalcTotal", arity=2)]
        self._resolve(calls)
        self.assertEqual(calls[0].callee_id, "modUtil.CalcTotal/2@modUtil.bas")

    def test_ambiguous_multi_target_not_arbitrary(self) -> None:
        calls = [_call("modMain", "DoWork", "TestSameName", member="TestSameName")]
        # simulate the worker's arbitrary ASG pick — resolver must drop it
        calls[0].callee_id = "frmAbout.TestSameName/0@frmAbout.frm"
        self._resolve(calls)
        self.assertEqual(calls[0].resolution_status, "ambiguous")
        self.assertIsNone(calls[0].callee_id)
        self.assertEqual(len(calls[0].candidate_ids), 2)
        for candidate in ("frmMain.TestSameName/0@frmMain.frm", "frmAbout.TestSameName/0@frmAbout.frm"):
            self.assertIn(candidate, calls[0].candidate_ids)

    def test_builtin_external(self) -> None:
        calls = [_call("modMain", "DoWork", "MsgBox", member="MsgBox")]
        self._resolve(calls)
        self.assertEqual(calls[0].resolution_status, "external")

    def test_qualified_module_receiver(self) -> None:
        calls = [_call("modMain", "DoWork", "modUtil.CalcTotal", member="CalcTotal", arity=2)]
        self._resolve(calls)
        self.assertEqual(calls[0].callee_id, "modUtil.CalcTotal/2@modUtil.bas")
        self.assertEqual(calls[0].resolution_status, "asg_resolved")

    def test_form_intrinsic_external(self) -> None:
        calls = [_call("modMain", "DoWork", "frmAbout.Show", member="Show")]
        self._resolve(calls)
        self.assertEqual(calls[0].resolution_status, "external")

    def test_me_receiver_external_when_missing(self) -> None:
        calls = [_call("frmMain", "Form_Load", "Me.Refresh", member="Refresh")]
        self._resolve(calls)
        self.assertEqual(calls[0].resolution_status, "external")

    def test_me_receiver_resolves_own_module(self) -> None:
        calls = [_call("modMain", "DoWork", "Me.DoWork", member="DoWork")]
        self._resolve(calls)
        self.assertEqual(calls[0].callee_id, "modMain.DoWork/0@modMain.bas")

    def test_late_bound_object_receiver(self) -> None:
        payloads = self.payloads + [{
            "file_def": {"file_path": "late.bas"},
            "functions": [],
            "variables": [{
                "name": "x", "type_name": "Object",
                "module_name": "late", "procedure_name": "RunLate",
            }],
            "parse_meta": {"module_name": "late"},
        }]
        calls = [_call("late", "RunLate", "x.LateBound", member="LateBound")]
        resolve_vb6_calls([], calls, payloads=payloads)
        self.assertEqual(calls[0].resolution_status, "late_bound")
        self.assertIsNone(calls[0].callee_id)

    def test_typed_receiver_keeps_asg_binding(self) -> None:
        calls = [_call("frmMain", "Form_Load", "ord.Total", member="Total",
                       callee_id="clsOrder.Total/1@clsOrder.cls")]
        self._resolve(calls)
        self.assertEqual(calls[0].resolution_status, "asg_resolved")
        self.assertEqual(calls[0].callee_id, "clsOrder.Total/1@clsOrder.cls")

    def test_with_block_member_keeps_asg_binding(self) -> None:
        calls = [_call("frmMain", "Form_Load", ".ProcessOrder", member="ProcessOrder",
                       callee_id="clsOrder.ProcessOrder/1@clsOrder.cls")]
        self._resolve(calls)
        self.assertEqual(calls[0].resolution_status, "asg_resolved")

    def test_debug_intrinsic_object_external(self) -> None:
        calls = [_call("modMain", "DoWork", "Debug.Print", member="Print")]
        self._resolve(calls)
        self.assertEqual(calls[0].resolution_status, "external")

    def test_registry_from_payloads_uses_module_name(self) -> None:
        registry = VB6ModuleRegistry.from_payloads(self.payloads)
        self.assertIn("modutil", registry.modules)
        self.assertEqual(registry.modules["modutil"].kind, "bas")
        self.assertEqual(registry.modules["clsorder"].kind, "cls")
        self.assertEqual(registry.modules["frmmain"].kind, "frm")


@unittest.skipUnless(True, "always on")
class VB6ResolverDepthUpgradeTest(unittest.TestCase):
    """Plan 260917-1628: interface dispatch, arity range, predeclared-id,
    dictionary calls (phase-03 unit level over synthetic payloads)."""

    def _payloads(self):
        fn_ship_order_order = _fn("clsOrder", "Ship_Order", arity=1, rel="clsOrder.cls")
        fn_ship_order_ship = _fn("clsShip", "Ship_Order", arity=1, rel="clsShip.cls")
        fn_iface = _fn("IShip", "Ship_Order", arity=1, rel="IShip.cls")
        fn_flexible = FunctionDef(
            symbol_id="modApi.Flexible/3@modApi.bas",
            qualified_name="modApi.Flexible",
            name="Flexible",
            kind="sub",
            class_name=None,
            namespace_name=None,
            file_path="modApi.bas",
            start_line=26,
            end_line=30,
            arity=3,
            code="",
            module_name="modApi",
            min_arity=1,
            has_optional_args=True,
            has_paramarray=True,
        )
        return (
            [
                {"file_def": {"file_path": "IShip.cls"},
                 "functions": [fn_iface], "variables": [],
                 "parse_meta": {"module_name": "IShip"}},
                {"file_def": {"file_path": "clsOrder.cls"},
                 "functions": [fn_ship_order_order], "variables": [],
                 "parse_meta": {"module_name": "clsOrder", "implements": ["IShip"]}},
                {"file_def": {"file_path": "clsShip.cls"},
                 "functions": [fn_ship_order_ship], "variables": [],
                 "parse_meta": {"module_name": "clsShip", "implements": ["IShip"]}},
                {"file_def": {"file_path": "modApi.bas"},
                 "functions": [fn_flexible], "variables": [],
                 "parse_meta": {"module_name": "modApi"}},
                {"file_def": {"file_path": "modMain.bas"},
                 "functions": [], "variables": [],
                 "parse_meta": {"module_name": "modMain"}},
            ],
            fn_flexible,
        )

    def test_interface_dispatch_ambiguous_across_implementers(self) -> None:
        payloads, _ = self._payloads()
        ship_payload = {
            "file_def": {"file_path": "frmMain.frm"},
            "functions": [],
            "variables": [{
                "name": "ship", "type_name": "IShip",
                "module_name": "frmMain", "procedure_name": "Form_Load",
            }],
            "parse_meta": {"module_name": "frmMain"},
        }
        calls = [_call("frmMain", "Form_Load", "ship.Ship_Order",
                       member="Ship_Order", arity=1,
                       callee_id="IShip.Ship_Order/1@IShip.cls")]
        resolve_vb6_calls([], calls, payloads=payloads + [ship_payload])
        call = calls[0]
        # the arbitrary ASG pick (the interface module itself) is dropped: the
        # implementer set is genuinely ambiguous -> POSSIBLE_CALLS candidates
        self.assertEqual(call.resolution_status, "ambiguous")
        self.assertIsNone(call.callee_id)
        self.assertEqual(
            sorted(call.candidate_ids),
            ["clsOrder.Ship_Order/1@clsOrder.cls", "clsShip.Ship_Order/1@clsShip.cls"],
        )

    def test_interface_dispatch_unique_implementer_resolves(self) -> None:
        payloads, _ = self._payloads()
        # drop clsShip: single implementer -> name_resolved
        payloads = [p for p in payloads if p["file_def"]["file_path"] != "clsShip.cls"]
        ship_payload = {
            "file_def": {"file_path": "frmMain.frm"},
            "functions": [],
            "variables": [{
                "name": "ship", "type_name": "IShip",
                "module_name": "frmMain", "procedure_name": "Form_Load",
            }],
            "parse_meta": {"module_name": "frmMain"},
        }
        calls = [_call("frmMain", "Form_Load", "ship.Ship_Order",
                       member="Ship_Order", arity=1)]
        resolve_vb6_calls([], calls, payloads=payloads + [ship_payload])
        self.assertEqual(calls[0].resolution_status, "name_resolved")
        self.assertEqual(calls[0].callee_id, "clsOrder.Ship_Order/1@clsOrder.cls")

    def test_arity_range_accepts_optional_and_paramarray(self) -> None:
        payloads, _ = self._payloads()
        calls = [
            _call("modMain", "Run", "Flexible", member="Flexible", arity=1,
                  callee_id="modApi.Flexible/3@modApi.bas"),
            _call("modMain", "Run", "Flexible", member="Flexible", arity=3,
                  callee_id="modApi.Flexible/3@modApi.bas"),
        ]
        resolve_vb6_calls([], calls, payloads=payloads)
        for call in calls:
            self.assertEqual(call.resolution_status, "name_resolved")
            self.assertEqual(call.callee_id, "modApi.Flexible/3@modApi.bas")

    def test_arity_range_narrows_multi_candidates(self) -> None:
        payloads, _ = self._payloads()
        # a second, signature-incompatible Flexible (regex-shaped: exact 0)
        exact_zero = _fn("modOther", "Flexible")
        payloads.append({
            "file_def": {"file_path": "modOther.bas"},
            "functions": [exact_zero], "variables": [],
            "parse_meta": {"module_name": "modOther"},
        })
        # bound arity 5: only the ParamArray signature accepts it
        calls = [_call("modMain", "Run", "Flexible", member="Flexible", arity=5,
                       callee_id="modApi.Flexible/3@modApi.bas")]
        resolve_vb6_calls([], calls, payloads=payloads)
        self.assertEqual(calls[0].resolution_status, "name_resolved")
        self.assertEqual(calls[0].callee_id, "modApi.Flexible/3@modApi.bas")
        # bound arity 0: the ParamArray signature (min 1) is excluded instead
        calls = [_call("modMain", "Run", "Flexible", member="Flexible", arity=0,
                       callee_id="modOther.Flexible/0@modOther.bas")]
        resolve_vb6_calls([], calls, payloads=payloads)
        self.assertEqual(calls[0].resolution_status, "name_resolved")
        self.assertEqual(calls[0].callee_id, "modOther.Flexible/0@modOther.bas")

    def test_predeclared_class_intrinsic_external_missing_unresolved(self) -> None:
        payloads, _ = self._payloads()
        payloads.append({
            "file_def": {"file_path": "clsGlobal.cls"},
            "functions": [],
            "variables": [],
            "parse_meta": {"module_name": "clsGlobal",
                           "module_attributes": {"vb_predeclaredid": "True"}},
        })
        # intrinsic member of a predeclared instance stays external
        calls = [_call("modMain", "Run", "clsGlobal.Show", member="Show")]
        resolve_vb6_calls([], calls, payloads=payloads)
        self.assertEqual(calls[0].resolution_status, "external")
        # a missing NON-intrinsic member is a typo, not an intrinsic —
        # unresolved (review fix F4: no vocabulary dilution)
        calls = [_call("modMain", "Run", "clsGlobal.Typo", member="Typo")]
        resolve_vb6_calls([], calls, payloads=payloads)
        self.assertEqual(calls[0].resolution_status, "unresolved")

    def test_predeclared_form_member_resolves_cross_module(self) -> None:
        payloads, _ = self._payloads()
        payloads.append({
            "file_def": {"file_path": "frmGlobal.frm"},
            "functions": [_fn("frmGlobal", "Setup", rel="frmGlobal.frm")],
            "variables": [],
            "parse_meta": {"module_name": "frmGlobal",
                           "module_attributes": {"vb_predeclaredid": "True"}},
        })
        calls = [_call("modMain", "Run", "frmGlobal.Setup", member="Setup")]
        resolve_vb6_calls([], calls, payloads=payloads)
        self.assertEqual(calls[0].resolution_status, "asg_resolved")
        self.assertEqual(calls[0].callee_id, "frmGlobal.Setup/0@frmGlobal.frm")

    def test_dictionary_late_bound_receiver(self) -> None:
        payloads, _ = self._payloads()
        payloads.append({
            "file_def": {"file_path": "modApi.bas"},
            "functions": [],
            "variables": [{
                "name": "rs", "type_name": "Object",
                "module_name": "modApi", "procedure_name": "ReadField",
            }],
            "parse_meta": {"module_name": "modApi"},
        })
        calls = [_call("modApi", "ReadField", "rs!FieldName", member="FieldName")]
        resolve_vb6_calls([], calls, payloads=payloads)
        self.assertEqual(calls[0].resolution_status, "late_bound")
        self.assertIsNone(calls[0].callee_id)

    def test_dictionary_typed_receiver_external(self) -> None:
        payloads, _ = self._payloads()
        payloads.append({
            "file_def": {"file_path": "modApi.bas"},
            "functions": [],
            "variables": [{
                "name": "rs", "type_name": "Recordset",
                "module_name": "modApi", "procedure_name": "ReadField",
            }],
            "parse_meta": {"module_name": "modApi"},
        })
        calls = [_call("modApi", "ReadField", "rs!FieldName", member="FieldName")]
        resolve_vb6_calls([], calls, payloads=payloads)
        self.assertEqual(calls[0].resolution_status, "external")
        self.assertIsNone(calls[0].callee_id)

    def test_declared_api_call_external(self) -> None:
        # review fix F1: unqualified calls to a declared API (declares plane)
        # classify external, never unresolved
        payloads, _ = self._payloads()
        payloads[3]["declares"] = [{
            "symbol_id": "GetTickCount@modApi.bas", "name": "GetTickCount",
            "proc_kind": "function", "lib": "kernel32",
        }]
        calls = [_call("modApi", "ElapsedMs", "GetTickCount", member="GetTickCount")]
        resolve_vb6_calls([], calls, payloads=payloads)
        self.assertEqual(calls[0].resolution_status, "external")
        self.assertIsNone(calls[0].callee_id)

    def test_exact_arity_beats_range_match(self) -> None:
        # review fix F2: exact-signature hits take precedence over range hits
        payloads, _ = self._payloads()
        payloads.append({
            "file_def": {"file_path": "modOther.bas"},
            "functions": [_fn("modOther", "Flexible", arity=1, kind="sub")],
            "variables": [],
            "parse_meta": {"module_name": "modOther"},
        })
        # bound arity 1: modOther.Flexible is an EXACT match and must win over
        # the range-accepting modApi.Flexible (optional + ParamArray)
        calls = [_call("modMain", "Run", "Flexible", member="Flexible", arity=1,
                       callee_id="modOther.Flexible/1@modOther.bas")]
        resolve_vb6_calls([], calls, payloads=payloads)
        self.assertEqual(calls[0].resolution_status, "name_resolved")
        self.assertEqual(calls[0].callee_id, "modOther.Flexible/1@modOther.bas")

    def test_split_callee_bang_receiver(self) -> None:
        from tools.vb.vb6_resolver import _split_callee
        call = _call("modApi", "ReadField", "rs!FieldName", member="FieldName")
        receiver, member = _split_callee(call)
        self.assertEqual((receiver, member), ("rs", "FieldName"))
        call = _call("modMain", "Run", "modUtil.CalcTotal", member="CalcTotal")
        self.assertEqual(_split_callee(call), ("modUtil", "CalcTotal"))


# ---------------------------------------------------------------------------
# plan 260924 phase 03: anchor-plane resolution
# ---------------------------------------------------------------------------

from tools.vb.vb6_resolver import (  # noqa: E402
    external_com_type_id,
    is_com_type_name,
    resolve_anchor_planes,
)


def _anchor_payload(rel, module, *, controls=(), constants=(), variables=(),
                    functions=(), ui_access=(), with_targets=(), instantiations=()):
    return {
        "file_def": {"file_path": rel},
        "parse_meta": {"module_name": module},
        "controls": list(controls),
        "constants": list(constants),
        "variables": list(variables),
        "functions": list(functions),
        "ui_access": list(ui_access),
        "with_targets": list(with_targets),
        "instantiations": list(instantiations),
    }


class AnchorResolutionTest(unittest.TestCase):
    def _registry_payloads(self):
        return [
            _anchor_payload(
                "frmLogin.frm", "frmLogin",
                controls=[
                    {"name": "frmLogin", "type": "VB.Form", "line": 1},
                    {"name": "cmdSubmit", "type": "VB.CommandButton", "line": 5},
                    {"name": "txtUsername", "type": "VB.TextBox", "line": 6},
                ],
                variables=[
                    {"symbol_id": "frmLogin.txtUsername@frmLogin.frm",
                     "name": "txtUsername", "type_name": "", "module_name": "frmLogin"},
                ],
                ui_access=[
                    {"proc": "cmdSubmit_Click", "receiver_raw": "cmdSubmit",
                     "member": "", "access": "write", "via_with": False, "line": 10},
                    {"proc": "cmdSubmit_Click", "receiver_raw": "txtUsername",
                     "member": "Text", "access": "read", "via_with": False, "line": 11},
                ],
                with_targets=[
                    {"proc": "cmdSubmit_Click", "expr_raw": "txtUsername",
                     "line": 12, "block_end_line": 14},
                    {"proc": "cmdSubmit_Click", "expr_raw": "frmMain",
                     "line": 15, "block_end_line": 17},
                ],
            ),
            _anchor_payload(
                "modState.bas", "modState",
                constants=[
                    {"symbol_id": "MAX_TRIES@modState.bas", "name": "MAX_TRIES"},
                ],
                variables=[
                    {"symbol_id": "modState.AppStatus@modState.bas",
                     "name": "AppStatus", "type_name": "Integer", "module_name": "modState"},
                ],
                ui_access=[
                    {"proc": "Reset", "receiver_raw": "AppStatus",
                     "member": "", "access": "write", "via_with": False, "line": 3},
                    {"proc": "Reset", "receiver_raw": "MAX_TRIES",
                     "member": "", "access": "read", "via_with": False, "line": 4},
                    {"proc": "Reset", "receiver_raw": "shadowed",
                     "member": "", "access": "write", "via_with": False, "line": 5},
                ],
            ),
            _anchor_payload(
                "modData.bas", "modData",
                functions=[_fn("modData", "Query", rel="modData.bas")],
                variables=[
                    {"symbol_id": "modData.Query.rs@modData.bas", "name": "rs",
                     "type_name": "ADODB.Recordset", "module_name": "modData",
                     "procedure_name": "Query"},
                    {"symbol_id": "modData.ord@modData.bas", "name": "ord",
                     "type_name": "clsOrder", "module_name": "modData"},
                ],
                ui_access=[
                    {"proc": "Query", "receiver_raw": "rs",
                     "member": "Open", "access": "read", "via_with": False, "line": 7},
                    {"proc": "Query", "receiver_raw": "ord",
                     "member": "Total", "access": "read", "via_with": False, "line": 8},
                ],
                instantiations=[
                    {"proc": "Query", "name": "ADODB.Recordset", "line": 6},
                ],
            ),
            _anchor_payload(
                "clsOrder.cls", "clsOrder",
                functions=[_fn("clsOrder", "Make", rel="clsOrder.cls")],
                instantiations=[{"proc": "Make", "name": "clsOrder", "line": 3}],
            ),
        ]

    def _resolve(self):
        payloads = self._registry_payloads()
        registry = VB6ModuleRegistry.from_payloads(payloads)
        resolve_anchor_planes(payloads, registry)
        return payloads

    def test_com_typed_receiver_annotated_not_blind(self) -> None:
        payloads = self._resolve()
        rows = {r["line"]: r for r in payloads[2]["ui_access"]}
        rs_open = rows[7]
        self.assertEqual(rs_open["target_kind"], "com_type")
        self.assertEqual(rs_open["target_id"], external_com_type_id("ADODB.Recordset"))
        self.assertEqual(rs_open.get("com_type"), "ADODB.Recordset")
        # project-typed receiver resolves to the types-lane node
        ord_total = rows[8]
        self.assertEqual(ord_total["target_kind"], "type")
        self.assertEqual(ord_total["target_id"], "clsOrder@clsOrder.cls")

    def test_control_member_and_state_rows(self) -> None:
        payloads = self._resolve()
        login = {r["line"]: r for r in payloads[0]["ui_access"]}
        self.assertEqual(login[11]["target_kind"], "control")
        self.assertEqual(login[11]["target_id"], "frmLogin.txtUsername@control")
        state = {r["line"]: r for r in payloads[1]["ui_access"]}
        self.assertEqual((state[3]["target_kind"], state[3]["target_id"]),
                         ("variable", "modState.AppStatus@modState.bas"))
        self.assertEqual((state[4]["target_kind"], state[4]["target_id"]),
                         ("constant", "MAX_TRIES@modState.bas"))

    def test_const_shadowed_by_local_wins(self) -> None:
        payloads = self._registry_payloads()
        payloads[1]["variables"].append({
            "symbol_id": "modState.Reset.shadowed@modState.bas", "name": "shadowed",
            "type_name": "Long", "module_name": "modState", "procedure_name": "Reset",
        })
        registry = VB6ModuleRegistry.from_payloads(payloads)
        resolve_anchor_planes(payloads, registry)
        row = [r for r in payloads[1]["ui_access"] if r["line"] == 5][0]
        self.assertEqual(row["target_kind"], "local_shadow")

    def test_with_targets_classified_and_members_attached(self) -> None:
        payloads = self._resolve()
        login_targets = {t["expr_raw"]: t for t in payloads[0]["with_targets"]}
        self.assertEqual(login_targets["txtUsername"]["target_kind"], "control")
        self.assertEqual(login_targets["txtUsername"]["target_id"],
                         "frmLogin.txtUsername@control")
        # form receiver resolves to the form's Type node (form-nav flavor)
        self.assertEqual(login_targets["frmMain"]["target_kind"], "unknown")
        # nested innermost-block attachment: member inside block 12..14
        payloads[0]["ui_access"].append({
            "proc": "cmdSubmit_Click", "receiver_raw": "", "member": "SetFocus",
            "access": "read", "via_with": True, "line": 13,
        })
        registry = VB6ModuleRegistry.from_payloads(payloads)
        resolve_anchor_planes(payloads, registry)
        attached = [r for r in payloads[0]["ui_access"] if r.get("line") == 13][0]
        self.assertEqual(attached.get("with_target_kind"), "control")
        self.assertEqual(attached.get("with_target_id"), "frmLogin.txtUsername@control")

    def test_with_form_nav_to_type_node(self) -> None:
        # corpus addbook.frm:529 flavor: `With <form> ... .Show` references
        # the form's Type node when the form module is in the project model
        payloads = self._registry_payloads()
        payloads.append(_anchor_payload(
            "frmMain.frm", "frmMain",
            with_targets=[{"proc": "Go", "expr_raw": "frmMain", "line": 5, "block_end_line": 7}],
        ))
        registry = VB6ModuleRegistry.from_payloads(payloads)
        resolve_anchor_planes(payloads, registry)
        target = payloads[-1]["with_targets"][0]
        self.assertEqual((target["target_kind"], target["target_id"]),
                         ("type", "frmMain@frmMain.frm"))

    def test_instantiation_project_vs_external(self) -> None:
        payloads = self._resolve()
        self.assertEqual(
            (payloads[3]["instantiations"][0]["target_kind"],
             payloads[3]["instantiations"][0]["target_id"]),
            ("project", "clsOrder@clsOrder.cls"),
        )
        self.assertEqual(
            (payloads[2]["instantiations"][0]["target_kind"],
             payloads[2]["instantiations"][0]["target_id"]),
            ("external", external_com_type_id("ADODB.Recordset")),
        )

    def test_is_com_type_name(self) -> None:
        for name in ("ADODB.Recordset", "dao.Database", "Scripting.FileSystemObject"):
            self.assertTrue(is_com_type_name(name), name)
        self.assertFalse(is_com_type_name("clsOrder"))
        self.assertFalse(is_com_type_name("Object"))

    def test_param_typed_receiver(self) -> None:
        payloads = self._registry_payloads()
        payloads[2]["functions"][0].param_names = ["raw"]
        payloads[2]["functions"][0].param_types = ["Scripting.FileSystemObject"]
        payloads[2]["ui_access"].append({
            "proc": "Query", "receiver_raw": "raw", "member": "FileExists",
            "access": "read", "via_with": False, "line": 9,
        })
        registry = VB6ModuleRegistry.from_payloads(payloads)
        resolve_anchor_planes(payloads, registry)
        row = [r for r in payloads[2]["ui_access"] if r.get("line") == 9][0]
        self.assertEqual(row["target_kind"], "com_type")
        self.assertEqual(row["target_id"], external_com_type_id("Scripting.FileSystemObject"))

    def test_with_typed_variable_resolves(self) -> None:
        # review M1: `With rs` (rs As ADODB.Recordset local) must classify the
        # With target through the typed variable — not downgrade to unknown
        payloads = self._registry_payloads()
        payloads[2]["with_targets"].append(
            {"proc": "Query", "expr_raw": "rs", "line": 10, "block_end_line": 12}
        )
        registry = VB6ModuleRegistry.from_payloads(payloads)
        resolve_anchor_planes(payloads, registry)
        target = payloads[2]["with_targets"][-1]
        self.assertEqual(target["target_kind"], "com_type")
        self.assertEqual(target["target_id"], external_com_type_id("ADODB.Recordset"))

    def test_type_node_id_excludes_bas_modules(self) -> None:
        # review C1 variant: .bas modules emit NO :Type node (classes[] is
        # class-module only) — matching them would produce an unguarded target
        payloads = self._registry_payloads()
        registry = VB6ModuleRegistry.from_payloads(payloads)
        self.assertIsNone(registry.type_node_id("modState"))
        self.assertIsNotNone(registry.type_node_id("clsOrder"))


if __name__ == "__main__":
    unittest.main()
