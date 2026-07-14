import unittest
from pathlib import Path

from cortex_harness.dev import LANG_ANALYZERS, LANG_EXTENSIONS, _detect_langs


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "cobol-application"


class DevCobolParserDiscoveryTest(unittest.TestCase):
    def test_cobol_analyzer_and_all_extensions_are_registered(self):
        self.assertTrue(LANG_ANALYZERS["cobol"].is_file())
        self.assertEqual(LANG_EXTENSIONS["cobol"], {".cbl", ".cob", ".cpy", ".copy"})
        self.assertIn("cobol", _detect_langs(FIXTURE))


if __name__ == "__main__":
    unittest.main()
