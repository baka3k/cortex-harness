import sys
import tempfile
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cobol.parser_runtime import CobolRuntimeError, preflight  # noqa: E402
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
