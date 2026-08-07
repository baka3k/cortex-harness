import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.common.analyzer_cache import load_parse_cache, write_parse_cache  # noqa: E402
from tools.cplus import cplus_analyzer  # noqa: E402
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

    def test_recovery_selects_only_strict_improvement_and_caches_terminal_result(self):
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
            self.assertEqual(metrics["improved"], 1)
            self.assertEqual(selected[str(path)]["quality_provenance"]["backend"], "libclang")
            self.assertEqual(selected_again, {})
            self.assertEqual(metrics_again["attempted"], 0)


if __name__ == "__main__":
    unittest.main()
