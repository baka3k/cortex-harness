"""Phase 02 contract test: vb6-antlr-worker protocol over the fixture corpus.

Builds the worker if needed, runs it through the adapter (which materializes
.frm into temp .cls — keep-designer by default), and asserts the JSON
contract: schema shape, parse success, .frm payloads, malformed isolation,
the cross-module CalcTotal edge, the hydrated planes (plan 260917-1628 M1),
DICTIONARY_CALL rows (M2), the designer control tree (M3) and comment
attachment (M5).
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
from tools.vb.vb_common import PARSE_CACHE_VERSION  # noqa: E402

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "vb6-application"

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
                # keep-designer: line numbers are the ORIGINAL file positions
                self.assertEqual(form_load[0]["start_line"], 102)

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

    # ------------------------------------------------------------------
    # plan 260917-1628: hydrated planes (M1), dictionary rows (M2),
    # designer controls (M3), comments (M5)
    # ------------------------------------------------------------------

    def test_m1_enum_constant_event_declare_hydrated(self) -> None:
        mod_api = self.payloads["modApi.bas"]

        enums = [e for e in mod_api["enums"] if e["name"] == "AppColor"]
        self.assertTrue(enums, "planted enum missing from payload (M1)")
        enum = enums[0]
        self.assertEqual(enum["symbol_id"], "AppColor@modApi.bas")
        self.assertEqual(enum["start_line"], 7)
        self.assertEqual(enum["end_line"], 10)
        self.assertEqual(
            [list(m) for m in enum["members"]],
            [["acBackground", "1"], ["acHighlight", "2"]],
        )

        constants = {c["name"]: c for c in mod_api["constants"]}
        self.assertIn("MAX_RETRY", constants)
        self.assertEqual(constants["MAX_RETRY"]["value"], "3")
        self.assertEqual(constants["MAX_RETRY"]["line_number"], 13)
        self.assertEqual(constants["APP_TITLE"]["value"], '"Fixture"')
        self.assertEqual(constants["APP_TITLE"]["type_name"], "String")

        events = self.payloads["clsEvents.cls"]["events"]
        self.assertTrue(
            any(e["name"] == "BeforeSave" for e in events),
            "planted event missing from payload (M1)",
        )
        before_save = [e for e in events if e["name"] == "BeforeSave"][0]
        self.assertEqual(before_save["parameters"], "Cancel As Boolean")
        self.assertEqual(before_save["start_line"], before_save["end_line"])

        declares = [d for d in mod_api["declares"] if d["name"] == "GetTickCount"]
        self.assertTrue(declares, "planted Declare missing from payload (M1)")
        declare = declares[0]
        self.assertEqual(declare["proc_kind"], "function")
        self.assertEqual(declare["lib"], "kernel32")
        self.assertEqual(declare["alias"], "GetTickCount")
        self.assertEqual(declare["return_type"], "Long")
        self.assertTrue(declare["is_private"])

        attributes = self.payloads["frmMain.frm"]["parse_meta"]["module_attributes"]
        self.assertEqual(attributes["vb_name"], "frmMain")
        self.assertEqual(attributes["vb_predeclaredid"], "True")
        self.assertEqual(attributes["vb_exposed"], "False")

    def test_m1_arity_enrichment(self) -> None:
        functions = {f["name"]: f for f in self.payloads["modApi.bas"]["functions"]}
        flexible = functions["Flexible"]
        self.assertEqual(flexible["arity"], 3)
        self.assertEqual(flexible["min_arity"], 1)  # ParamArray arg excluded (F6)
        self.assertTrue(flexible["has_optional_args"])
        self.assertTrue(flexible["has_paramarray"])
        plain = functions["CallFlexible"]
        self.assertEqual(plain["min_arity"], 0)
        self.assertFalse(plain["has_optional_args"])
        self.assertFalse(plain["has_paramarray"])

    def test_m2_dictionary_call_row_present(self) -> None:
        calls = self.payloads["modApi.bas"]["calls"]
        dict_rows = [c for c in calls if c.get("call_type") == "dictionary_call"]
        self.assertTrue(dict_rows, "dictionary call dropped (M2: zero drop)")
        row = dict_rows[0]
        self.assertEqual(row["callee_name"], "rs!FieldName")
        self.assertEqual(row["call_line"], 41)
        self.assertTrue(row.get("default_member"))
        self.assertEqual(row["callee_id"], None)

    def test_m3_designer_control_tree(self) -> None:
        controls = self.payloads["frmMain.frm"]["controls"]
        self.assertTrue(controls, "frmMain designer block must yield controls[] (M3)")
        by_name = {c["name"]: c for c in controls}
        # >=3 controls incl. nested + BEGINPROPERTY in the designer source
        for name in ("frmMain", "fraData", "txtName", "txtEmail", "cmdGo", "lstItems"):
            self.assertIn(name, by_name, f"control {name} missing from tree")
        # parent/child wiring
        self.assertEqual(by_name["fraData"]["parent"], "frmMain")
        self.assertEqual(by_name["txtName"]["parent"], "fraData")
        self.assertEqual(by_name["txtEmail"]["parent"], "fraData")
        self.assertEqual(by_name["cmdGo"]["parent"], "frmMain")
        self.assertEqual(by_name["frmMain"]["parent"], "")
        self.assertEqual(by_name["fraData"]["type"], "VB.Frame")
        self.assertEqual(by_name["txtName"]["type"], "VB.TextBox")
        # useful properties captured; Tab(n).Control(m) stays raw (skipped)
        self.assertEqual(by_name["cmdGo"]["properties"]["Caption"], "Go")
        self.assertEqual(by_name["txtName"]["properties"]["Text"], "nested")

    def test_m5_comments_attached(self) -> None:
        mod_api = self.payloads["modApi.bas"]
        elapsed = [f for f in mod_api["functions"] if f["name"] == "ElapsedMs"][0]
        self.assertIn("Reads the process uptime", elapsed["comment"])
        self.assertIn("\n", elapsed["comment"], "block comments join with newline")
        self.assertNotIn("'", elapsed["comment"], "comment markers must be stripped")
        self.assertEqual(elapsed["summary"], elapsed["comment"])
        self.assertIn("Comment:", elapsed["note"])

        enum = [e for e in mod_api["enums"] if e["name"] == "AppColor"][0]
        self.assertEqual(enum["comment"], "Color codes for the fixture UI")

        declare = [d for d in mod_api["declares"] if d["name"] == "GetTickCount"][0]
        self.assertEqual(declare["comment"], "kernel32 uptime probe used by ElapsedMs")

    def test_designer_stripped_flag_off_by_default(self) -> None:
        for rel, payload in self.payloads.items():
            meta = payload["parse_meta"]
            self.assertFalse(
                meta.get("designer_stripped", False),
                f"{rel}: strip fallback must be OFF by default",
            )

    # ------------------------------------------------------------------
    # plan 260924: anchor planes (instantiations / with_targets /
    # ui_access / redim) + variable flags + declared signatures
    # ------------------------------------------------------------------

    def test_anchor_planes_present_and_shape(self) -> None:
        for rel, payload in self.payloads.items():
            for plane in ("instantiations", "with_targets", "ui_access", "redim"):
                self.assertIn(plane, payload, f"{rel} missing anchor plane {plane}")
            for row in payload["instantiations"]:
                for key in ("proc", "name", "line", "call_type"):
                    self.assertIn(key, row)
                self.assertEqual(row["call_type"], "NEW")
            for row in payload["with_targets"]:
                for key in ("proc", "expr_raw", "line", "block_end_line"):
                    self.assertIn(key, row)
            for row in payload["ui_access"]:
                for key in ("proc", "receiver_raw", "member", "access", "via_with", "line"):
                    self.assertIn(key, row)
                self.assertIn(row["access"], ("read", "write"))
            for row in payload["redim"]:
                for key in ("proc", "name", "preserve", "line"):
                    self.assertIn(key, row)

    def test_new_instantiations_captured(self) -> None:
        # `Set ord = New clsOrder` was previously filtered out of calls[] by
        # the moduleNames guard (Vb6Worker); it must live in its own plane
        frm_main = self.payloads["frmMain.frm"]
        names = {(row["proc"], row["name"]) for row in frm_main["instantiations"]}
        self.assertIn(("Form_Load", "clsOrder"), names)
        self.assertIn(("Form_Load", "clsShip"), names)
        # As New in a class module
        sink = {(row["proc"], row["name"]) for row in self.payloads["clsSink.cls"]["instantiations"]}
        self.assertIn(("Class_Initialize", "clsSource"), sink)
        self.assertIn(("QueryRows", "ADODB.Recordset"), sink)

    def test_with_targets_carry_block_range(self) -> None:
        frm_main = self.payloads["frmMain.frm"]
        targets = [row for row in frm_main["with_targets"] if row["proc"] == "Form_Load"]
        self.assertTrue(targets)
        target = targets[0]
        self.assertEqual(target["expr_raw"], "ord")
        self.assertGreater(target["block_end_line"], target["line"])
        # nested-With-capable: frmAnchor has two sibling With blocks
        anchor = {(row["expr_raw"], row["block_end_line"])
                  for row in self.payloads["frmAnchor.frm"]["with_targets"]}
        self.assertTrue(any(expr == "frmMain" for expr, _end in anchor))
        self.assertTrue(any(expr == "cboType" for expr, _end in anchor))

    def test_ui_access_member_rows(self) -> None:
        rows = self.payloads["frmAnchor.frm"]["ui_access"]
        by_line = {row["line"]: row for row in rows}
        # control state read with member + access
        read = by_line[60]
        self.assertEqual((read["receiver_raw"], read["member"], read["access"]),
                         ("txtUsername", "Text", "read"))
        # with-target member: via_with + raw receiver empty
        show = [row for row in rows if row["member"] == "Show" and row["via_with"]]
        self.assertTrue(show, "With <form> ... .Show member row missing")
        # write access detected on assignment target
        writes = [row for row in rows if row["access"] == "write"]
        self.assertTrue(any(row["member"] == "ListIndex" for row in writes))

    def test_ui_access_state_rows_and_redim(self) -> None:
        # bare module-state references (member=="") only for program-wide
        # module-level names — the state anchor
        mod_state = self.payloads["modState.bas"]["ui_access"]
        app_rows = [row for row in mod_state if row["receiver_raw"] == "AppStatus"]
        self.assertTrue(app_rows)
        self.assertTrue(any(row["access"] == "write" for row in app_rows))
        const_rows = [row for row in mod_state if row["receiver_raw"] == "MAX_LOGIN_TRIES"]
        self.assertTrue(const_rows and all(row["member"] == "" for row in const_rows))
        # ReDim Preserve plane
        redims = self.payloads["clsSink.cls"]["redim"]
        self.assertEqual(redims, [
            {"proc": "ResizeBuffer", "name": "buffer", "preserve": True, "line": redims[0]["line"]}
        ] if redims else ["missing"])

    def test_variable_flags_static_with_events_global(self) -> None:
        sink_vars = {v["name"]: v for v in self.payloads["clsSink.cls"]["variables"]}
        self.assertTrue(sink_vars["mSource"]["with_events"], "WithEvents flag lost")
        self.assertTrue(sink_vars["buffer"]["is_static"], "Static local flag lost (S2)")
        self.assertFalse(sink_vars["userStatus"]["with_events"])
        # S1: `Global AppStatus` must keep is_global (distinct VisibilityEnum.GLOBAL)
        state_vars = {v["name"]: v for v in self.payloads["modState.bas"]["variables"]}
        self.assertTrue(state_vars["AppStatus"]["is_global"])

    def test_function_signature_types(self) -> None:
        calc = [f for f in self.payloads["modUtil.bas"]["functions"] if f["name"] == "CalcTotal"][0]
        self.assertEqual(calc["param_types"], ["Double", "Double"])
        self.assertEqual(calc["return_type"], "Double")
        work = [f for f in self.payloads["modMain.bas"]["functions"] if f["name"] == "DoWork"][0]
        self.assertEqual(work["param_types"], [])
        self.assertEqual(work["return_type"], "")


if __name__ == "__main__":
    unittest.main()
