from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cplus import cplus_analyzer
from tools.cplus.proc_preprocessor import preprocess_proc_directives


PROC_FIXTURE = """/* Kora batch connect/commit helpers */
int Kora_logon(char *username, char *password) {
    EXEC SQL CONNECT :username IDENTIFIED BY :password;
    return 0;
}

int Kcommit(void) {
    EXEC SQL WHENEVER SQLERROR GOTO error_exit;
    EXEC SQL COMMIT WORK RELEASE;
    return 1;
error_exit:
    return -1;
}
"""

C_FIXTURE = """/* plain C file, unaffected by Pro*C support */
int add(int a, int b) {
    return a + b;
}
"""


class ProcPreprocessorTest(unittest.TestCase):
    def test_preserves_byte_length_and_newline_count(self):
        data = PROC_FIXTURE.encode("utf-8")
        patched, statements = preprocess_proc_directives(data)
        self.assertEqual(len(patched), len(data))
        self.assertEqual(patched.count(b"\n"), data.count(b"\n"))
        kinds = [s.kind for s in statements]
        self.assertEqual(kinds, ["CONNECT", "WHENEVER", "COMMIT"])
        connect = statements[0]
        self.assertIn("username", connect.host_vars)
        self.assertIn("password", connect.host_vars)


class ProcAnalyzerFixtureTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        (self.root / "BZZAAB02.pc").write_text(PROC_FIXTURE, encoding="utf-8")
        (self.root / "BZZAAB01.c").write_text(C_FIXTURE, encoding="utf-8")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_pc_extension_is_discovered_and_classified_as_c(self):
        files = cplus_analyzer._scan_c_family_files(str(self.root))
        names = {Path(f).name for f in files}
        self.assertIn("BZZAAB02.pc", names)
        pc_path = str(self.root / "BZZAAB02.pc")
        self.assertFalse(cplus_analyzer._is_cpp_file(pc_path, str(self.root)))

    def test_pc_functions_extracted_with_correct_line_numbers_and_exec_sql_relations(self):
        pc_path = str(self.root / "BZZAAB02.pc")
        result = cplus_analyzer.parse_c_family_file(pc_path, str(self.root), False)
        functions, calls, types, namespaces, relations = result[0], result[1], result[2], result[3], result[4]
        parse_meta = result[-1]

        func_names = {f.name: f for f in functions}
        self.assertIn("Kora_logon", func_names)
        self.assertIn("Kcommit", func_names)
        # Line numbers after the first EXEC SQL block must be unshifted:
        # byte-length-preserving preprocessing is what keeps this correct.
        self.assertEqual(func_names["Kcommit"].start_line, 7)

        exec_sql_relations = [r for r in relations if r.rel_type == "EXEC_SQL"]
        self.assertEqual(len(exec_sql_relations), 3)
        kinds = sorted(r.properties["kind"] for r in exec_sql_relations)
        self.assertEqual(kinds, ["COMMIT", "CONNECT", "WHENEVER"])
        # Each EXEC_SQL edge should be attached to its enclosing function.
        connect_rel = next(r for r in exec_sql_relations if r.properties["kind"] == "CONNECT")
        self.assertEqual(connect_rel.source_id, func_names["Kora_logon"].symbol_id)

        self.assertEqual(parse_meta["embedded_sql_statement_count"], 3)

    def test_c_file_still_parses_unaffected(self):
        c_path = str(self.root / "BZZAAB01.c")
        result = cplus_analyzer.parse_c_family_file(c_path, str(self.root), False)
        functions, _, _, _, relations = result[0], result[1], result[2], result[3], result[4]
        self.assertEqual({f.name for f in functions}, {"add"})
        self.assertFalse([r for r in relations if r.rel_type == "EXEC_SQL"])


if __name__ == "__main__":
    unittest.main()
