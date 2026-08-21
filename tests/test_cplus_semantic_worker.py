"""Phase 02 semantic worker contract tests (real worker, no mocks)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cplus.parse_recovery import run_semantic_worker  # noqa: E402
from tools.cplus.semantic_worker import (  # noqa: E402
    PINNED_LIBCLANG_VERSION,
    SEMANTIC_REQUEST_SCHEMA,
    SEMANTIC_WORKER_PROTOCOL_VERSION,
    probe_clang_runtime,
    validate_semantic_request,
)
from tools.cplus.semantic_shadow import run_shadow_comparison  # noqa: E402

WORKER = CODE_TINY / "tools" / "cplus" / "clang_worker.py"
FIXTURES = ROOT / "tests" / "fixtures" / "cplus_semantic_calls"


@unittest.skipUnless(
    probe_clang_runtime()["ready"],
    "pinned libclang backend not installed; this is a typed readiness failure, not a silent skip",
)
class SemanticWorkerContractTests(unittest.TestCase):
    maxDiff = None

    def _request(self, root, path, args=None, **overrides):
        request = {
            "protocol_version": SEMANTIC_WORKER_PROTOCOL_VERSION,
            "request_schema": SEMANTIC_REQUEST_SCHEMA,
            "root": str(root),
            "path": str(path),
            "compile_arguments": list(args or []),
            "compile_context_fingerprint": "test-config",
            "memory_mb": 1024,
            "cpu_seconds": 10,
            "max_output_bytes": 8 * 1024 * 1024,
            "max_source_bytes": 1024 * 1024,
        }
        request.update(overrides)
        return request

    def _run(self, request, timeout=30):
        result = run_semantic_worker(
            worker_path=str(WORKER), request=request, timeout_seconds=timeout
        )
        self.assertEqual(result.get("status"), "ok", result.get("error"))
        return result

    def _site(self, result, callee_name):
        sites = [s for s in result["callsites"] if s["callee_name"] == callee_name]
        self.assertTrue(sites, f"no callsite for {callee_name}")
        return sites

    # -- readiness -------------------------------------------------------

    def test_readiness_probe_reports_typed_outcome(self):
        probe = probe_clang_runtime()
        self.assertTrue(probe["ready"])
        self.assertEqual(probe["libclang_version"], PINNED_LIBCLANG_VERSION)
        self.assertEqual(probe["backend"], "cindex-libclang")
        mismatch = probe_clang_runtime(expected_version="0.0.0")
        self.assertFalse(mismatch["ready"])
        self.assertIn("libclang_version_mismatch", mismatch["reason"])

    # -- direct resolution with semantic identity -------------------------

    def test_direct_call_emits_usr_identity_and_config_provenance(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "direct.c")
            path.write_text(
                "static int target(int v) { return v; }\n"
                "int entry(int v) { return target(v); }\n",
                encoding="utf-8",
            )
            result = self._run(self._request(root, path, ["-std=c11"]))
            site = self._site(result, "target")[0]
            self.assertEqual(site["resolution_class"], "direct_resolved")
            self.assertTrue(site["callee_usr"].startswith("c:direct.c@F@target"))
            self.assertTrue(site["caller_usr"])
            self.assertEqual(site["config_fingerprint"], "test-config")
            self.assertEqual(site["tu_key"], "direct.c")
            self.assertEqual(site["callee_linkage"], "INTERNAL")

    def test_internal_linkage_usr_carries_file_identity(self):
        # USR alone can merge file-local entities; the evidence must retain
        # the file/TU disambiguation.
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "linkage.c")
            path.write_text(
                "static int internal(int v) { return v; }\n"
                "int entry(int v) { return internal(v); }\n",
                encoding="utf-8",
            )
            result = self._run(self._request(root, path, ["-std=c11"]))
            site = self._site(result, "internal")[0]
            self.assertEqual(site["resolution_class"], "direct_resolved")
            self.assertEqual(site["callee_linkage"], "INTERNAL")
            self.assertIn("linkage.c@F@internal", site["callee_usr"])

    def test_unresolved_implicit_declaration_keeps_weak_class(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "unresolved.c")
            path.write_text(
                "int entry(void) { return missing(1); }\n", encoding="utf-8"
            )
            result = self._run(self._request(root, path, ["-std=c11"]))
            site = self._site(result, "missing")[0]
            self.assertEqual(site["resolution_class"], "unresolved")
            self.assertEqual(site["resolution_reason"], "implicit_declaration")
            self.assertEqual(result["coverage"]["status"], "partial")

    # -- non-direct taxonomy ------------------------------------------------

    def test_virtual_call_is_declared_virtual_target(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "virtual.cpp")
            path.write_text(
                "struct Base { virtual int step() const { return 1; } };\n"
                "int run(Base& b) { return b.step(); }\n",
                encoding="utf-8",
            )
            result = self._run(self._request(root, path, ["-std=c++17"]))
            site = self._site(result, "step")[0]
            self.assertEqual(site["resolution_class"], "declared_virtual_target")
            self.assertEqual(site["resolution_reason"], "virtual_dispatch")

    def test_function_pointer_call_is_indirect_callsite(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "fp.c")
            path.write_text(
                "static int apply(int (*op)(int), int v) { return op(v); }\n"
                "static int dbl(int v) { return v * 2; }\n"
                "int entry(int v) { return apply(dbl, v); }\n",
                encoding="utf-8",
            )
            result = self._run(self._request(root, path, ["-std=c11"]))
            self.assertEqual(self._site(result, "op")[0]["resolution_class"], "indirect_callsite")
            self.assertEqual(self._site(result, "apply")[0]["resolution_class"], "direct_resolved")

    def test_dependent_template_call_is_not_direct(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "template.cpp")
            path.write_text(
                "template <typename T>\n"
                "struct Box {\n"
                "  T value;\n"
                "  T combine(const T& other) const { return value + other; }\n"
                "};\n"
                "int run(int a, int b) { Box<int> box{a}; return box.combine(b); }\n",
                encoding="utf-8",
            )
            result = self._run(self._request(root, path, ["-std=c++17"]))
            sites = [
                s for s in result["callsites"] if s["callee_name"] == "combine"
            ]
            dependent = [s for s in sites if s["resolution_class"] == "dependent_template_call"]
            self.assertTrue(dependent, [s["resolution_class"] for s in sites])

    def test_overloads_resolve_to_distinct_usrs(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "overload.cpp")
            path.write_text(
                "static int pick(int v) { return v; }\n"
                "static double pick(double v) { return v; }\n"
                "int run(int i, double d) { return pick(i) + static_cast<int>(pick(d)); }\n",
                encoding="utf-8",
            )
            result = self._run(self._request(root, path, ["-std=c++17"]))
            sites = [s for s in result["callsites"] if s["callee_name"] == "pick"]
            self.assertEqual(len(sites), 2)
            self.assertEqual(len({s["callee_usr"] for s in sites}), 2)
            for site in sites:
                self.assertEqual(site["resolution_class"], "direct_resolved")

    def test_macro_origin_call_records_expansion_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "macro_static.c")
            path.write_text(
                "#define WRAP(x) helper(x)\n"
                "static int helper(int v) { return v; }\n"
                "static int internal(int v) { return WRAP(v); }\n"
                "int entry(int v) { return internal(v); }\n",
                encoding="utf-8",
            )
            result = self._run(self._request(root, path, ["-std=c11"]))
            site = self._site(result, "helper")[0]
            self.assertEqual(site["resolution_class"], "direct_resolved")
            self.assertEqual(site["resolution_reason"], "macro_expansion")
            self.assertEqual(site["macro_origin"], "WRAP")

    def test_constructor_call_is_classified_not_forced_direct(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "ctor.cpp")
            path.write_text(
                "struct Item {\n"
                "  explicit Item(int v) : value(v) {}\n"
                "  int value;\n"
                "};\n"
                "int run(int v) { Item item(v); return item.value; }\n",
                encoding="utf-8",
            )
            result = self._run(self._request(root, path, ["-std=c++17"]))
            site = self._site(result, "Item")[0]
            self.assertEqual(site["resolution_class"], "constructor_call")
            self.assertEqual(site["resolution_reason"], "cxx_construct_expr")
            self.assertTrue(site["callee_usr"])
            self.assertTrue(site["caller_usr"])

    def test_overloaded_operator_via_operator_syntax_is_captured(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "operator.cpp")
            path.write_text(
                "struct Num {\n"
                "  int v;\n"
                "  Num(int v) : v(v) {}\n"
                "};\n"
                "static int operator+(const Num& a, const Num& b) { return a.v + b.v; }\n"
                "int run(int x, int y) { return Num(x) + Num(y); }\n",
                encoding="utf-8",
            )
            result = self._run(self._request(root, path, ["-std=c++17"]))
            sites = [s for s in result["callsites"] if "operator+" in s["callee_name"]]
            self.assertTrue(sites, [s["callee_name"] for s in result["callsites"]])
            self.assertEqual(sites[0]["resolution_class"], "direct_resolved")
            self.assertTrue(sites[0]["callee_usr"])

    def test_redact_relative_collapses_external_paths(self):
        from tools.cplus.semantic_worker import redact_relative

        with tempfile.TemporaryDirectory() as root:
            inside = str(Path(root, "src", "a.c"))
            self.assertEqual(redact_relative(root, inside), "src/a.c")
            external = "/usr/include/stdio.h"
            self.assertEqual(redact_relative(root, external), "<external>/stdio.h")
            self.assertNotIn("..", redact_relative(root, external))

    def test_error_response_never_leaks_absolute_paths(self):
        with tempfile.TemporaryDirectory() as root:
            missing_root = Path(root, "nope")
            request = self._request(missing_root, missing_root / "a.c", ["-std=c11"])
            result = run_semantic_worker(
                worker_path=str(WORKER), request=request, timeout_seconds=20
            )
            self.assertNotEqual(result["status"], "ok")
            encoded = json.dumps(result)
            self.assertNotIn(str(missing_root), encoded)
            self.assertNotIn("/Users/", encoded)
            self.assertNotIn("/tmp/", encoded)


    # -- safety and containment ----------------------------------------------

    def test_raw_proc_source_rejected_without_bundle(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "sample.pc")
            path.write_text("int main(void) { EXEC SQL COMMIT; return 0; }\n", encoding="utf-8")
            result = run_semantic_worker(
                worker_path=str(WORKER),
                request=self._request(root, path),
                timeout_seconds=20,
            )
            self.assertNotEqual(result["status"], "ok")
            self.assertIn("proc", result.get("error", ""))

    def test_credential_bearing_argument_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "a.c")
            path.write_text("int f(void) { return 0; }\n", encoding="utf-8")
            request = self._request(root, path, ["-std=c11", "-DUSER=scott/tiger"])
            with self.assertRaises(ValueError):
                validate_semantic_request(request)
            result = run_semantic_worker(
                worker_path=str(WORKER), request=request, timeout_seconds=20
            )
            self.assertEqual(result["status"], "invalid")

    def test_no_absolute_path_leakage_in_response(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "leak.c")
            path.write_text(
                "static int t(int v) { return v; }\nint e(int v){return t(v);}\n",
                encoding="utf-8",
            )
            result = self._run(self._request(root, path, ["-std=c11"]))
            encoded = json.dumps(result)
            self.assertNotIn(root, encoded)
            self.assertNotIn("/Users/", encoded)
            self.assertNotIn("/tmp/", encoded)

    def test_external_include_dependency_is_rejected_by_containment(self):
        # Include search paths outside the repository root are rejected by the
        # argument allowlist before any parsing happens: fail closed.
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as other:
            header = Path(other, "ext.h")
            header.write_text("int ext(void);\n", encoding="utf-8")
            path = Path(root, "uses_ext.c")
            path.write_text(
                '#include "ext.h"\nint e(void) { return ext(); }\n', encoding="utf-8"
            )
            result = run_semantic_worker(
                worker_path=str(WORKER),
                request=self._request(root, path, [f"-I{other}", "-std=c11"]),
                timeout_seconds=20,
            )
            self.assertNotEqual(result["status"], "ok")
            self.assertIn("escapes repository root", result.get("error", ""))

    # -- Pro*C source bundles -------------------------------------------------

    def _proc_generated(self, root: Path):
        gen_dir = root / "generated"
        gen_dir.mkdir()
        artifact = gen_dir / "proc_sample.c"
        artifact.write_text(
            "int commit_work(void);\n"
            "extern void sqlcxt(void *, unsigned int *, void *, void *);\n"
            "int main(void) {\n"
            "  sqlcxt((void *)0, (unsigned int *)0, (void *)0, (void *)0);\n"
            "  return commit_work();\n"
            "}\n",
            encoding="utf-8",
        )
        source_map = gen_dir / "proc_sample.map.json"
        source_map.write_text(
            json.dumps(
                {
                    "source_map_id": "map-1",
                    "original_path": "src/proc_sample.pc",
                    "entries": [
                        {"generated_line": 5, "generated_col": 3, "original_line": 3, "quality": "exact"}
                    ],
                }
            ),
            encoding="utf-8",
        )
        return artifact, source_map

    def test_proc_bundle_request_returns_generated_classes_and_map_reference(self):
        import hashlib

        with tempfile.TemporaryDirectory() as root_str:
            root = Path(root_str)
            artifact, source_map = self._proc_generated(root)
            artifact_sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
            request = self._request(
                root,
                root / "generated" / "proc_sample.c",
                ["-std=c11"],
                proc_bundle={
                    "bundle_id": "bundle-1",
                    "artifact_path": "generated/proc_sample.c",
                    "artifact_sha256": artifact_sha,
                    "source_map_id": "map-1",
                    "source_map_sha256": hashlib.sha256(source_map.read_bytes()).hexdigest(),
                    "original_path": "src/proc_sample.pc",
                    "language_mode": "c",
                    "mapping_policy": "exact",
                },
            )
            # Worker resolves the artifact through the bundle, not the .pc path.
            result = self._run(request)
            bundle = result["proc_bundle"]
            self.assertEqual(bundle["bundle_id"], "bundle-1")
            self.assertEqual(bundle["original_path"], "src/proc_sample.pc")
            self.assertEqual(bundle["source_map_id"], "map-1")
            self.assertTrue(bundle["precompiler_fingerprint"])
            wrapper = self._site(result, "sqlcxt")[0]
            self.assertEqual(wrapper["generated_code_class"], "precompiler_runtime")
            self.assertEqual(wrapper["proc_bundle_id"], "bundle-1")
            application = self._site(result, "commit_work")[0]
            self.assertEqual(application["generated_code_class"], "original_application")
            self.assertEqual(application["resolution_class"], "direct_resolved")

    def test_proc_bundle_rejects_stale_artifact_hash(self):
        import hashlib

        with tempfile.TemporaryDirectory() as root_str:
            root = Path(root_str)
            artifact, _ = self._proc_generated(root)
            request = self._request(
                root,
                artifact,
                ["-std=c11"],
                proc_bundle={
                    "bundle_id": "bundle-1",
                    "artifact_path": "generated/proc_sample.c",
                    "artifact_sha256": "0" * 64,
                    "source_map_id": "map-1",
                    "source_map_sha256": "0" * 64,
                    "original_path": "src/proc_sample.pc",
                    "language_mode": "c",
                    "mapping_policy": "exact",
                },
            )
            result = run_semantic_worker(
                worker_path=str(WORKER), request=request, timeout_seconds=20
            )
            self.assertNotEqual(result["status"], "ok")
            self.assertIn("hash", result.get("error", ""))

    def test_proc_bundle_rejects_raw_pc_artifact(self):
        with tempfile.TemporaryDirectory() as root_str:
            root = Path(root_str)
            (root / "generated").mkdir()
            raw = root / "generated" / "raw.pc"
            raw.write_text("int main(void) { return 0; }\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_semantic_request(
                    {
                        "protocol_version": SEMANTIC_WORKER_PROTOCOL_VERSION,
                        "request_schema": SEMANTIC_REQUEST_SCHEMA,
                        "root": str(root),
                        "path": str(raw),
                        "proc_bundle": {
                            "bundle_id": "b",
                            "artifact_path": "generated/raw.pc",
                            "artifact_sha256": "0" * 64,
                            "source_map_id": "m",
                            "source_map_sha256": "0" * 64,
                            "original_path": "src/x.pc",
                            "language_mode": "c",
                            "mapping_policy": "exact",
                        },
                    }
                )

    # -- shadow mode -----------------------------------------------------------

    def test_shadow_comparison_report_matches_reviewed_expectations(self):
        with tempfile.TemporaryDirectory() as out_dir:
            report_path = Path(out_dir, "shadow-report.json")
            report = run_shadow_comparison(
                root=str(FIXTURES),
                files=["direct.c", "fp.c", "virtual.cpp", "macro_static.c", "template.cpp", "overload.cpp"],
                output_path=str(report_path),
                worker_path=str(WORKER),
                expected=json.loads((FIXTURES / "expected.json").read_text(encoding="utf-8")),
            )
            summary = report["summary"]
            self.assertEqual(summary["mismatched"], 0, report["files"])
            self.assertEqual(summary["missing"], 0, report["files"])
            self.assertGreater(summary["matched"], 0)
            self.assertEqual(report["published_calls"], 0)
            saved = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["mode"], "shadow")


if __name__ == "__main__":
    unittest.main()
