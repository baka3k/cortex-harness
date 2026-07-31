from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.batchconfig.ini_analyzer import analyze_root
from tools.batchconfig.ini_parser import build_relations, parse_ini_file


INI_FIXTURE = """# BBSEAB01_06_01 batch config
# key/value pairs, not standard [section] INI
WK_MK_MNG_KEY:BBSEAB06
WK_MK_MNG_PTN_KEY:01
"""


class BatchConfigParserTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.ini_path = self.root / "BBSEAB01_06_01.ini"
        self.ini_path.write_text(INI_FIXTURE, encoding="utf-8")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_entries_extracted_with_key_value_split_on_first_colon(self):
        config = parse_ini_file(str(self.ini_path), str(self.root))
        self.assertEqual(len(config.entries), 2)
        by_key = {e.key: e.value for e in config.entries}
        self.assertEqual(by_key["WK_MK_MNG_KEY"], "BBSEAB06")
        self.assertEqual(by_key["WK_MK_MNG_PTN_KEY"], "01")

    def test_comment_header_decoded(self):
        config = parse_ini_file(str(self.ini_path), str(self.root))
        self.assertIn("BBSEAB01_06_01 batch config", config.comment)

    def test_build_relations_emits_defines_config(self):
        config = parse_ini_file(str(self.ini_path), str(self.root))
        relations = build_relations(config)
        self.assertEqual(len(relations), 2)
        self.assertTrue(all(r.rel_type == "DEFINES_CONFIG" for r in relations))
        self.assertTrue(all(r.source_label == "File" for r in relations))
        self.assertTrue(all(r.target_label == "ConfigEntry" for r in relations))

    def test_analyze_root_discovers_and_counts(self):
        rows = analyze_root(str(self.root))
        self.assertEqual(len(rows["files"]), 1)
        self.assertEqual(len(rows["functions"]), 2)
        self.assertEqual(len(rows["relations"]), 2)


if __name__ == "__main__":
    unittest.main()
