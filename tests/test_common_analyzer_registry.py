import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from cortex_harness.dev import FRAMEWORK_ANALYZERS as DEV_FRAMEWORK_ANALYZERS  # noqa: E402
from cortex_harness.dev import LANG_ANALYZERS as DEV_LANG_ANALYZERS  # noqa: E402
from tools.sync.incremental_sync import ANALYZERS, FRAMEWORK_ANALYZERS, _group_paths_by_parser  # noqa: E402
from tools.sync.owner_manifest import SUPPORTED_PARSERS  # noqa: E402

MCP_DIR = CODE_TINY / "mcp"
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from framework_registry import capability_for_parser  # noqa: E402


EXPECTED_PRIMARY = {
    "android",
    "cobol",
    "cplus",
    "csharp",
    "dart",
    "delphi",
    "go",
    "java",
    "js",
    "kotlin",
    "php",
    "perl",
    "shell",
    "jp1",
    "plsql",
    "python",
    "rust",
    "sql",
    "swift",
    "ts",
    "vb6",
    "vba",
    "vbnet",
    "vbscript",
}

EXPECTED_OVERLAYS = {
    "aspnet_core",
    "aspnet_framework",
    "flutter",
    "mybatis",
    "servlet_jsp",
    "spring",
    "struts",
    "fastapi_django",
    "express_js",
    "laravel",
    "database_sql",
    "database_plsql",
}


class CommonAnalyzerRegistryTest(unittest.TestCase):
    def test_incremental_sync_registers_every_supported_primary_and_overlay(self):
        self.assertEqual(set(ANALYZERS), EXPECTED_PRIMARY)
        self.assertEqual(SUPPORTED_PARSERS, EXPECTED_PRIMARY)
        self.assertEqual(set(FRAMEWORK_ANALYZERS), EXPECTED_OVERLAYS)
        self.assertTrue(all(Path(config.script_path).is_file() for config in ANALYZERS.values()))
        self.assertTrue(all(Path(config.script_path).is_file() for config in FRAMEWORK_ANALYZERS.values()))

    def test_root_cli_exposes_all_standalone_and_overlay_entrypoints(self):
        expected_dev_languages = (EXPECTED_PRIMARY - {"android"}) | {
            "android_java",
            "android_kotlin",
            "android_mixed",
        }
        self.assertEqual(set(DEV_LANG_ANALYZERS), expected_dev_languages)
        self.assertEqual(set(DEV_FRAMEWORK_ANALYZERS), EXPECTED_OVERLAYS)

    def test_every_analyzer_has_an_explicit_vector_strategy(self):
        self.assertTrue(all(config.writes_vectors for config in ANALYZERS.values()))
        self.assertTrue(all(not config.writes_vectors for config in FRAMEWORK_ANALYZERS.values()))
        self.assertTrue(all(config.prerequisite_parsers for config in FRAMEWORK_ANALYZERS.values()))

    def test_primary_file_ownership_includes_previously_unrouted_analyzers(self):
        with tempfile.TemporaryDirectory() as tmp:
            grouped = _group_paths_by_parser(
                ["src/main.go", "src/lib.pm", "src/test.t", "src/run.sh", "src/lib.rs", "src/app.swift", "lib/main.dart"],
                root=tmp,
            )

        self.assertEqual(grouped["go"], {"src/main.go"})
        self.assertEqual(grouped["perl"], {"src/lib.pm", "src/test.t"})
        self.assertEqual(grouped["shell"], {"src/run.sh"})
        self.assertEqual(grouped["rust"], {"src/lib.rs"})
        self.assertEqual(grouped["swift"], {"src/app.swift"})
        self.assertEqual(grouped["dart"], {"lib/main.dart"})

    def test_every_primary_analyzer_resolves_to_an_mcp_capability(self):
        missing = sorted(parser for parser in EXPECTED_PRIMARY if capability_for_parser(parser) is None)
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
