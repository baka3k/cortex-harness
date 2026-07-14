import sys
import tempfile
import json
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cobol import parser_runtime  # noqa: E402
from tools.cobol.parser_runtime import CobolRuntimeError, load_parser, preflight  # noqa: E402
from tools.cobol.cobol_analyzer import _manifest_paths  # noqa: E402


class CobolParserRuntimeTest(unittest.TestCase):
    def test_portable_runtime_preflight(self):
        info = preflight()
        self.assertEqual(info.provider, "tree-sitter-language-pack")
        self.assertGreaterEqual(info.grammar_abi, 13)

    def test_missing_override_has_actionable_code(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory, "missing-cobol.dll")
            with self.assertRaisesRegex(CobolRuntimeError, "COBOL_RUNTIME_LIBRARY_NOT_FOUND"):
                preflight(str(missing))

    def test_bundled_runtime_failure_falls_back_to_language_pack(self):
        bundled = ROOT / "code-tiny" / "tools" / "cobol" / "lib" / "broken.dylib"
        failure = CobolRuntimeError("COBOL_RUNTIME_LOAD_FAILED", "test failure")
        with (
            patch.object(parser_runtime, "resolve_language_library", return_value=bundled),
            patch.object(parser_runtime, "_native_parser", side_effect=failure),
        ):
            _, info = load_parser()
        self.assertEqual(info.provider, "tree-sitter-language-pack")

    def test_explicit_runtime_failure_does_not_silently_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            library = Path(directory, "custom-cobol.dylib")
            library.touch()
            failure = CobolRuntimeError("COBOL_RUNTIME_LOAD_FAILED", "test failure")
            with patch.object(parser_runtime, "_native_parser", side_effect=failure):
                with self.assertRaisesRegex(CobolRuntimeError, "COBOL_RUNTIME_LOAD_FAILED"):
                    load_parser(str(library))

    def test_bundled_macos_library_is_universal(self):
        library = ROOT / "code-tiny" / "tools" / "cobol" / "lib" / "cobol.cpython-310-darwin.so"
        data = library.read_bytes()[:48]
        self.assertEqual(data[:4], bytes.fromhex("cafebabe"))
        self.assertEqual(int.from_bytes(data[4:8], "big"), 2)
        cpu_types = {
            int.from_bytes(data[offset : offset + 4], "big")
            for offset in (8, 28)
        }
        self.assertEqual(cpu_types, {0x01000007, 0x0100000C})

    def test_manifest_rejects_relative_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory, "root")
            root.mkdir()
            manifest = Path(directory, "manifest.json")
            manifest.write_text(json.dumps(["../outside.cbl"]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "outside the project root"):
                _manifest_paths(str(manifest), root)


if __name__ == "__main__":
    unittest.main()
