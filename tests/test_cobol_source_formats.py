import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
FIXTURE = ROOT / "tests" / "fixtures" / "cobol-dialects"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cobol.pipeline import analyze_project  # noqa: E402


class CobolSourceFormatsTest(unittest.TestCase):
    def test_fixed_and_free_formats_report_dialect_metadata(self):
        facts, _ = analyze_project(FIXTURE, project_id="dialects")
        files = {node.file_path: node for node in facts.nodes if node.label == "File"}
        self.assertEqual(files["ibm-fixed.cbl"].properties["source_format"], "fixed")
        self.assertEqual(files["ibm-fixed.cbl"].properties["dialect"], "ibm-enterprise")
        self.assertEqual(files["gnucobol-free.cob"].properties["source_format"], "free")
        self.assertEqual(files["gnucobol-free.cob"].properties["dialect"], "gnucobol")
        programs = {node.name for node in facts.nodes if node.label == "CobolProgram"}
        paragraphs = {node.name for node in facts.nodes if node.label == "CobolParagraph"}
        self.assertEqual(programs, {"ANSIFORMAT", "GNUFORMAT", "IBMFORMAT", "MFFORMAT"})
        self.assertIn("START-PARA", paragraphs)

        self.assertEqual(files["ansi-free.cbl"].properties["dialect"], "ansi")
        self.assertEqual(files["micro-focus.cob"].properties["dialect"], "micro-focus")

    def test_ebcdic_is_normalized_for_parsing_but_retains_original_byte_ranges(self):
        source = (
            "       IDENTIFICATION DIVISION.\n"
            "       PROGRAM-ID. EBCDICPGM.\n"
            "       DATA DIVISION.\n"
            "       WORKING-STORAGE SECTION.\n"
            "       01 EBCDIC-FIELD PIC X.\n"
            "       PROCEDURE DIVISION.\n"
            "       START-PARA.\n"
            "           STOP RUN.\n"
        )
        data = source.encode("cp037")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Path(root, "ebcdic.cbl").write_bytes(data)
            facts, _ = analyze_project(root, project_id="ebcdic")
        file_node = next(node for node in facts.nodes if node.label == "File")
        field = next(node for node in facts.nodes if node.label == "CobolDataItem")
        self.assertEqual(file_node.properties["encoding"], "cp037")
        self.assertEqual(field.name, "EBCDIC-FIELD")
        self.assertEqual(field.evidence.start_byte, data.find("       01 EBCDIC-FIELD".encode("cp037")))


if __name__ == "__main__":
    unittest.main()
