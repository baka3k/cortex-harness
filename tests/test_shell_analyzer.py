from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.shell.shell_analyzer import analyze_root
from tools.shell.shell_parser import build_relations, parse_shell_file


SHELL_FIXTURE = """#!/bin/sh
# BBSEAB01 batch driver
# Loads config, connects, runs the Pro*C batch program.

FC_GET_INI() {
    KEY_NAME="$1"
    VALUE=`grep 'ORA_SID' "${CONFIG_DIR}/batch.ini" | awk -F: '{ print $2 }'`
    echo "$VALUE"
}

CONFIG_DIR="/app/config"
ORA_SID=$(FC_GET_INI ORA_SID)

sh ./BBSEAB02.sh
${CONFIG_DIR}/BZZAAB02
"""


class ShellParserTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.script_path = self.root / "BBSEAB01.sh"
        self.script_path.write_text(SHELL_FIXTURE, encoding="utf-8")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_function_definitions_extracted(self):
        script = parse_shell_file(str(self.script_path), str(self.root))
        names = {f.name for f in script.functions}
        self.assertIn("FC_GET_INI", names)
        func = next(f for f in script.functions if f.name == "FC_GET_INI")
        self.assertEqual(func.start_line, 5)
        self.assertIn("VALUE=", func.code)

    def test_file_comment_decoded_without_shebang(self):
        script = parse_shell_file(str(self.script_path), str(self.root))
        self.assertIn("BBSEAB01 batch driver", script.comment)
        self.assertNotIn("#!/bin/sh", script.comment)

    def test_variable_assignments_extracted(self):
        script = parse_shell_file(str(self.script_path), str(self.root))
        var_names = {v.name for v in script.variables}
        self.assertIn("CONFIG_DIR", var_names)
        self.assertIn("ORA_SID", var_names)

    def test_config_read_extracted(self):
        script = parse_shell_file(str(self.script_path), str(self.root))
        self.assertEqual(len(script.config_reads), 1)
        read = script.config_reads[0]
        self.assertEqual(read.config_key, "ORA_SID")
        self.assertIn("batch.ini", read.ini_path_expr)
        self.assertEqual(read.enclosing_function, "FC_GET_INI")

    def test_call_edges_extracted_for_sh_invocation(self):
        script = parse_shell_file(str(self.script_path), str(self.root))
        callees = {edge.callee_ref for edge in script.call_edges}
        self.assertIn("./BBSEAB02.sh", callees)

    def test_build_relations_emits_reads_config_and_calls(self):
        script = parse_shell_file(str(self.script_path), str(self.root))
        relations = build_relations(script)
        rel_types = {r.rel_type for r in relations}
        self.assertIn("READS_CONFIG", rel_types)
        self.assertIn("CALLS", rel_types)
        config_rel = next(r for r in relations if r.rel_type == "READS_CONFIG")
        self.assertEqual(config_rel.source_label, "Function")
        self.assertEqual(config_rel.target_label, "ConfigEntry")

    def test_analyze_root_discovers_and_counts(self):
        rows = analyze_root(str(self.root))
        self.assertEqual(len(rows["files"]), 1)
        self.assertGreaterEqual(len(rows["functions"]), 1)
        self.assertGreaterEqual(len(rows["relations"]), 1)


if __name__ == "__main__":
    unittest.main()
