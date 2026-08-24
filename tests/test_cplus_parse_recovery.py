import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.common.analyzer_cache import load_parse_cache, write_parse_cache  # noqa: E402
from tools.cplus import cplus_analyzer  # noqa: E402
from tools.cplus import parse_recovery  # noqa: E402
from tools.cplus.parse_recovery import (  # noqa: E402
    PersistentRecoveryQueue,
    RecoveryBudgets,
    load_compile_database,
    recover_payload_candidates,
    run_clang_worker,
    sanitize_compile_arguments,
)


class CPlusQualityCacheTests(unittest.TestCase):
    def _compile_index(self, rel_path, fingerprint, other=""):
        contexts = {rel_path: fingerprint}
        if other:
            contexts["other.c"] = other
        return {
            "path": "",
            "entries": len(contexts),
            "cpp_files": set(),
            "c_files": set(contexts),
            "context_by_file": contexts,
            "fingerprint": "global-is-not-used-for-file-identity",
        }

    def test_cache_invalidates_only_when_target_parse_context_changes(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "sample.c")
            path.write_text("int sample(void) { return 0; }\n", encoding="utf-8")
            cache_root = str(Path(root, ".cache"))
            Path(cache_root).mkdir()
            original = cplus_analyzer.parse_c_family_file
            with mock.patch.object(
                cplus_analyzer,
                "parse_c_family_file",
                wraps=original,
            ) as parse_mock:
                first = cplus_analyzer._load_or_parse_payload(
                    str(path), root, cache_root, True, self._compile_index("sample.c", "A", "X"), "demo"
                )
                same_target = cplus_analyzer._load_or_parse_payload(
                    str(path), root, cache_root, True, self._compile_index("sample.c", "A", "Y"), "demo"
                )
                changed_target = cplus_analyzer._load_or_parse_payload(
                    str(path), root, cache_root, True, self._compile_index("sample.c", "B", "Y"), "demo"
                )
            self.assertEqual(parse_mock.call_count, 2)
            self.assertEqual(
                first["quality_provenance"]["context_fingerprint"],
                same_target["quality_provenance"]["context_fingerprint"],
            )
            self.assertNotEqual(
                first["quality_provenance"]["context_fingerprint"],
                changed_target["quality_provenance"]["context_fingerprint"],
            )

    def test_payload_carries_compact_provenance_and_evidence_policy(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "broken.cpp")
            path.write_text("class Broken { public: void run( {\n", encoding="utf-8")
            payload = cplus_analyzer._load_or_parse_payload(
                str(path), root, str(Path(root, ".cache")), False, None, "demo"
            )
            provenance = payload["quality_provenance"]
            self.assertIn(provenance["tier"], {"retry_required", "quarantined"})
            self.assertEqual(payload["file_def"]["parse_quality"], provenance)
            self.assertEqual(
                payload["evidence_policy"]["strong_relations_allowed"],
                provenance["tier"] != "quarantined",
            )

    def test_shared_cache_accepts_legacy_dict_signatures(self):
        with tempfile.TemporaryDirectory() as root:
            signature = {"mtime_ns": 1, "size": 2}
            write_parse_cache(root, "legacy.py", signature, {"ok": True})
            self.assertEqual(load_parse_cache(root, "legacy.py", signature), {"ok": True})

    def test_legacy_libclang_structure_cache_is_ignored(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "broken.cpp")
            path.write_text("class Broken { public: void run( {\n", encoding="utf-8")
            cache_root = str(Path(root, ".cache"))
            Path(cache_root).mkdir()
            candidate = {
                "functions": [],
                "calls": [],
                "types": [],
                "namespaces": [],
                "relations": [],
                "function_types": [],
                "fields": [],
                "aliases": [],
                "templates": [],
                "file_def": {"file_path": "broken.cpp"},
                "using_namespaces": [],
                "using_imports": {},
                "includes": [],
                "macros": {},
                "parse_meta": {
                    "error_nodes": 0,
                    "recovery_policy_version": cplus_analyzer.RECOVERY_POLICY_VERSION,
                },
            }
            libclang_signature = cplus_analyzer._parse_cache_context_signature(
                file_path=str(path),
                rel_path="broken.cpp",
                is_cpp=True,
                is_resource=False,
                compile_db_index=None,
                project_id="demo",
                selected_backend=cplus_analyzer.ParserBackend.LIBCLANG,
                selected_parser_version="18.1-test",
                recovery_policy_version=cplus_analyzer.RECOVERY_POLICY_VERSION,
            )
            write_parse_cache(cache_root, "broken.cpp", libclang_signature, candidate)
            with mock.patch.object(
                cplus_analyzer,
                "parse_c_family_file",
                wraps=cplus_analyzer.parse_c_family_file,
            ) as parse_mock:
                result = cplus_analyzer._load_or_parse_payload(
                    str(path),
                    root,
                    cache_root,
                    True,
                    None,
                    "demo",
                )
                cached_result = cplus_analyzer._load_or_parse_payload(
                    str(path),
                    root,
                    cache_root,
                    True,
                    None,
                    "demo",
                )
                tree_sitter_signature = cplus_analyzer._parse_cache_context_signature(
                    file_path=str(path),
                    rel_path="broken.cpp",
                    is_cpp=True,
                    is_resource=False,
                    compile_db_index=None,
                    project_id="demo",
                )
                other_libclang_version_signature = (
                    cplus_analyzer._parse_cache_context_signature(
                        file_path=str(path),
                        rel_path="broken.cpp",
                        is_cpp=True,
                        is_resource=False,
                        compile_db_index=None,
                        project_id="demo",
                        selected_backend=cplus_analyzer.ParserBackend.LIBCLANG,
                        selected_parser_version="19.0-test",
                        recovery_policy_version=cplus_analyzer.RECOVERY_POLICY_VERSION,
                    )
                )
                other_recovery_policy_signature = (
                    cplus_analyzer._parse_cache_context_signature(
                        file_path=str(path),
                        rel_path="broken.cpp",
                        is_cpp=True,
                        is_resource=False,
                        compile_db_index=None,
                        project_id="demo",
                        selected_backend=cplus_analyzer.ParserBackend.LIBCLANG,
                        selected_parser_version="18.1-test",
                        recovery_policy_version="test-policy-v3",
                    )
                )

            self.assertEqual(parse_mock.call_count, 1)
            self.assertEqual(
                result["quality_provenance"]["backend"],
                cplus_analyzer.ParserBackend.TREE_SITTER.value,
            )
            self.assertEqual(cached_result, result)
            self.assertNotEqual(tree_sitter_signature, libclang_signature)
            self.assertNotEqual(
                libclang_signature, other_libclang_version_signature
            )
            self.assertNotEqual(
                libclang_signature, other_recovery_policy_signature
            )
            self.assertEqual(
                load_parse_cache(cache_root, "broken.cpp", tree_sitter_signature),
                result,
            )
            self.assertIsNone(
                load_parse_cache(cache_root, "broken.cpp", libclang_signature)
            )


class CPlusBoundedRecoveryTests(unittest.TestCase):
    def test_compile_arguments_are_allowlisted_and_external_paths_rejected(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            source = Path(root, "src", "sample.c")
            include = Path(root, "include")
            source.parent.mkdir()
            include.mkdir()
            source.write_text("int sample(void);\n", encoding="utf-8")
            safe = sanitize_compile_arguments(
                ["clang", "-std=c11", "-DVALUE=1", "-I", str(include), "-c", str(source)],
                root=root,
                directory=root,
                source_path=str(source),
            )
            self.assertEqual(
                safe,
                ("-std=c11", "-DVALUE=1", "-I", str(include.resolve())),
            )
            for unsafe in (
                ["clang", "@flags.rsp", str(source)],
                ["clang", "-fplugin=evil.so", str(source)],
                ["clang", "-o", "output.o", str(source)],
                ["clang", "-I", outside, str(source)],
            ):
                with self.assertRaises(ValueError):
                    sanitize_compile_arguments(
                        unsafe,
                        root=root,
                        directory=root,
                        source_path=str(source),
                    )

    def test_compile_database_is_parsed_as_data_and_never_executed(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root, "sample.c")
            source.write_text("int sample(void);\n", encoding="utf-8")
            marker = Path(root, "executed")
            database = Path(root, "compile_commands.json")
            database.write_text(
                json.dumps(
                    [
                        {
                            "directory": root,
                            "file": str(source),
                            "command": f"touch {marker}",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_compile_database(str(database), root=root)
            self.assertFalse(marker.exists())

    def test_queue_terminal_outcome_is_not_enqueued_again(self):
        with tempfile.TemporaryDirectory() as root:
            queue = PersistentRecoveryQueue(str(Path(root, "queue.json")))
            quality = {"context_fingerprint": "ctx"}
            self.assertTrue(queue.enqueue("sample.c", quality, (0,)))
            item = queue.pending()[0]
            queue.finish(item["id"], "not_improved", "no regression")
            self.assertFalse(queue.enqueue("sample.c", quality, (0,)))
            self.assertEqual(queue.pending(), [])

    def test_unchanged_failed_and_timed_out_items_are_terminal(self):
        for status in ("failed", "timed_out", "invalid"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as root:
                queue = PersistentRecoveryQueue(str(Path(root, "queue.json")))
                quality = {"context_fingerprint": "ctx"}
                self.assertTrue(queue.enqueue("sample.c", quality, (0,)))
                item = queue.pending()[0]
                queue.finish(item["id"], status, status)
                self.assertFalse(queue.enqueue("sample.c", quality, (0,)))
                self.assertEqual(queue.pending(), [])

    def test_candidate_version_change_reopens_terminal_queue_item(self):
        with tempfile.TemporaryDirectory() as root:
            queue = PersistentRecoveryQueue(str(Path(root, "queue.json")))
            quality = {"context_fingerprint": "ctx"}
            self.assertTrue(queue.enqueue("sample.c", quality, (0,), "libclang:18:1"))
            item = queue.pending()[0]
            queue.finish(item["id"], "failed", "worker crash")
            self.assertFalse(queue.enqueue("sample.c", quality, (0,), "libclang:18:1"))
            self.assertTrue(queue.enqueue("sample.c", quality, (0,), "libclang:19:1"))

    def test_obsolete_pending_candidate_identity_is_not_executed(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "broken.cpp")
            path.write_text("class Broken { public: void run( {\n", encoding="utf-8")
            baseline = cplus_analyzer._load_or_parse_payload(
                str(path), root, str(Path(root, ".cache")), False, None, "demo"
            )
            quality = (baseline.get("parse_meta") or {}).get("quality") or {}
            queue_path = str(Path(root, "queue.json"))
            queue = PersistentRecoveryQueue(queue_path)
            self.assertTrue(
                queue.enqueue("broken.cpp", quality, (0,), "libclang:obsolete:1:1")
            )
            with mock.patch(
                "tools.cplus.parse_recovery.run_clang_worker",
                return_value={"status": "failed", "error": "expected"},
            ) as worker_mock:
                _selected, metrics = recover_payload_candidates(
                    root=root,
                    candidates={str(path): baseline},
                    queue_path=queue_path,
                    compile_commands_path="",
                    budgets=RecoveryBudgets(max_files=10, wall_seconds=10, workers=1),
                    worker_path="unused",
                )
            self.assertEqual(worker_mock.call_count, 1)
            self.assertEqual(metrics["queued"], 1)

    def test_worker_timeout_isolated_to_one_process(self):
        with tempfile.TemporaryDirectory() as root:
            worker = Path(root, "hang.py")
            worker.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
            result = run_clang_worker(
                worker_path=str(worker),
                request={"protocol_version": "1"},
                timeout_seconds=1,
            )
            self.assertEqual(result["status"], "timed_out")

    def test_worker_crash_is_reported_without_raising_in_parent(self):
        with tempfile.TemporaryDirectory() as root:
            worker = Path(root, "crash.py")
            worker.write_text("raise RuntimeError('boom')\n", encoding="utf-8")
            result = run_clang_worker(
                worker_path=str(worker),
                request={"protocol_version": "1"},
                timeout_seconds=5,
            )
            self.assertEqual(result["status"], "failed")
            self.assertIn("boom", result["error"])

    def test_worker_output_is_capped_before_parent_reads_it(self):
        with tempfile.TemporaryDirectory() as root:
            worker = Path(root, "noisy.py")
            worker.write_text("import sys\nsys.stdout.write('x' * 2048)\n", encoding="utf-8")
            with mock.patch("tools.cplus.parse_recovery.MAX_WORKER_OUTPUT_BYTES", 1024):
                result = run_clang_worker(
                    worker_path=str(worker),
                    request={"protocol_version": "1"},
                    timeout_seconds=5,
                )
            self.assertEqual(result["status"], "invalid")
            self.assertIn("output", result["error"])

    def test_worker_process_tree_memory_is_capped_by_parent(self):
        with tempfile.TemporaryDirectory() as root:
            worker = Path(root, "memory.py")
            worker.write_text(
                "import time\npayload = bytearray(64 * 1024 * 1024)\ntime.sleep(30)\n",
                encoding="utf-8",
            )
            result = run_clang_worker(
                worker_path=str(worker),
                request={"protocol_version": "1", "memory_mb": 1},
                timeout_seconds=5,
            )
            self.assertEqual(result["status"], "failed")
            self.assertIn("memory", result["error"])

    def test_worker_memory_monitor_permission_failure_is_terminal(self):
        with tempfile.TemporaryDirectory() as root:
            worker = Path(root, "hang.py")
            worker.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
            with mock.patch(
                "tools.cplus.parse_recovery.psutil.Process",
                side_effect=parse_recovery.psutil.AccessDenied(),
            ):
                result = run_clang_worker(
                    worker_path=str(worker),
                    request={"protocol_version": "1", "memory_mb": 1024},
                    timeout_seconds=5,
                )
            self.assertEqual(result["status"], "invalid")
            self.assertIn("monitor", result["error"])

    def test_run_wall_budget_caps_each_worker_deadline(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "broken.cpp")
            path.write_text("class Broken { public: void run( {\n", encoding="utf-8")
            baseline = cplus_analyzer._load_or_parse_payload(
                str(path), root, str(Path(root, ".cache")), False, None, "demo"
            )
            observed_timeouts = []

            def wait_for_deadline(*, timeout_seconds, **_kwargs):
                observed_timeouts.append(timeout_seconds)
                time.sleep(timeout_seconds + 0.05)
                return {"status": "timed_out", "error": "worker timeout"}

            started = time.monotonic()
            with mock.patch(
                "tools.cplus.parse_recovery.run_clang_worker",
                side_effect=wait_for_deadline,
            ):
                _selected, metrics = recover_payload_candidates(
                    root=root,
                    candidates={str(path): baseline},
                    queue_path=str(Path(root, "queue.json")),
                    compile_commands_path="",
                    budgets=RecoveryBudgets(
                        max_files=1,
                        wall_seconds=1,
                        workers=1,
                        per_file_timeout_seconds=30,
                    ),
                    worker_path="unused",
                )
            self.assertLess(time.monotonic() - started, 2)
            self.assertLessEqual(observed_timeouts[0], 1)
            self.assertEqual(metrics["stop_reason"], "wall_time_budget")

    def test_recovery_keeps_libclang_payload_diagnostic_only(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "broken.cpp")
            path.write_text("class Broken { public: void run( {\n", encoding="utf-8")
            baseline = cplus_analyzer._load_or_parse_payload(
                str(path), root, str(Path(root, ".cache")), False, None, "demo"
            )
            candidate = {
                "functions": [
                    {
                        "symbol_id": "Broken::run/0@broken.cpp",
                        "qualified_name": "Broken::run",
                        "name": "run",
                        "file_path": "broken.cpp",
                    }
                ],
                "calls": [],
                "types": [{"qualified_name": "Broken", "file_path": "broken.cpp"}],
                "namespaces": [],
                "relations": [],
                "function_types": [],
                "fields": [],
                "aliases": [],
                "templates": [],
                "file_def": {"file_path": "broken.cpp"},
                "using_namespaces": [],
                "using_imports": {},
                "includes": [],
                "macros": {},
                "parse_meta": {"error_nodes": 0, "damaged_bytes": 0},
            }
            worker_result = {
                "protocol_version": "1",
                "status": "ok",
                "payload": candidate,
                "error": "",
            }
            queue_path = str(Path(root, "queue.json"))
            with mock.patch(
                "tools.cplus.parse_recovery.run_clang_worker",
                return_value=worker_result,
            ) as worker_mock:
                selected, metrics = recover_payload_candidates(
                    root=root,
                    candidates={str(path): baseline},
                    queue_path=queue_path,
                    compile_commands_path="",
                    budgets=RecoveryBudgets(max_files=1, wall_seconds=10, workers=1),
                    worker_path="unused",
                )
                selected_again, metrics_again = recover_payload_candidates(
                    root=root,
                    candidates={str(path): baseline},
                    queue_path=queue_path,
                    compile_commands_path="",
                    budgets=RecoveryBudgets(max_files=1, wall_seconds=10, workers=1),
                    worker_path="unused",
                )
            self.assertEqual(worker_mock.call_count, 1)
            self.assertEqual(metrics["improved"], 0)
            self.assertEqual(metrics["non_improved"], 1)
            self.assertEqual(selected[str(path)]["quality_provenance"]["backend"], "tree_sitter")
            self.assertEqual(
                selected[str(path)]["quality_provenance"]["selection_reason"],
                "cross_backend_structure_forbidden",
            )
            self.assertEqual(selected_again, {})
            self.assertEqual(metrics_again["attempted"], 0)


if __name__ == "__main__":
    unittest.main()
