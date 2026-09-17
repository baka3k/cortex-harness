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


if __name__ == "__main__":
    unittest.main()
