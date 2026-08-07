import json
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


if __name__ == "__main__":
    unittest.main()
