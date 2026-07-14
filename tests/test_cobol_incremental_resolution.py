import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
FIXTURE = ROOT / "tests" / "fixtures" / "cobol-application"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.cobol.pipeline import analyze_project, select_incremental_result  # noqa: E402
from tools.cobol.resolver import DependencyIndex  # noqa: E402


class CobolIncrementalResolutionTest(unittest.TestCase):
    def test_nested_copybook_change_invalidates_consumers_transitively(self):
        facts, index = analyze_project(FIXTURE, project_id="fixture")
        impacted = index.impacted_files({"nested.copy"})
        self.assertEqual(impacted, {"nested.copy", "common.cpy", "main.cbl"})
        selected = select_incremental_result(facts, impacted)
        self.assertEqual(selected.summary.invalidated_files, 3)
        self.assertNotIn("subprog.cob", {node.file_path for node in selected.nodes})

    def test_dependency_cache_round_trips_deterministically(self):
        _, index = analyze_project(FIXTURE, project_id="fixture")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "deps.json")
            index.save(path)
            first = path.read_text(encoding="utf-8")
            loaded = DependencyIndex.load(path)
            loaded.save(path)
            second = path.read_text(encoding="utf-8")
        self.assertEqual(first, second)
        self.assertEqual(loaded.dependencies, index.dependencies)

    def test_custom_copybook_extension_is_honored(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Path(root, "main.cbl").write_text(
                "       IDENTIFICATION DIVISION.\n"
                "       PROGRAM-ID. CUSTOMEXT.\n"
                "       DATA DIVISION.\n"
                "       WORKING-STORAGE SECTION.\n"
                "       COPY SHARED.\n"
                "       PROCEDURE DIVISION.\n"
                "       START-PARA.\n"
                "           STOP RUN.\n",
                encoding="utf-8",
            )
            Path(root, "shared.inc").write_text("       01 SHARED-VALUE PIC X.\n", encoding="utf-8")
            facts, index = analyze_project(
                root,
                project_id="custom-extension",
                copybook_extensions=(".inc", ".cpy"),
            )
        self.assertIn("shared.inc", index.dependencies["main.cbl"])
        self.assertTrue(any(node.name == "SHARED-VALUE" for node in facts.nodes))


if __name__ == "__main__":
    unittest.main()
