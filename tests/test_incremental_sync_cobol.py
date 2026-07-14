import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.sync import incremental_sync  # noqa: E402
from tools.sync.owner_manifest import build_owner_maps  # noqa: E402


class IncrementalSyncCobolTest(unittest.TestCase):
    def test_all_four_extensions_route_exclusively_to_cobol(self):
        paths = {"src/a.cbl", "src/b.cob", "copy/c.cpy", "copy/d.copy"}
        grouped = incremental_sync._group_paths_by_parser(paths, root=str(ROOT))
        self.assertEqual(grouped["cobol"], paths)
        self.assertFalse(any(paths & values for parser, values in grouped.items() if parser != "cobol"))

    def test_owner_manifest_assigns_programs_and_copybooks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("a.cbl", "b.cob", "c.cpy", "d.copy"):
                Path(root, name).write_text("       COPY X.\n", encoding="utf-8")
            result = build_owner_maps(root=str(root), parsers=["cobol"])
        self.assertEqual(result.owned_by_parser["cobol"], {"a.cbl", "b.cob", "c.cpy", "d.copy"})

    def test_analyzer_command_uses_standard_incremental_contract(self):
        config = incremental_sync.ANALYZERS["cobol"]
        command = incremental_sync._build_analyzer_cmd(
            python_bin="python",
            analyzer=config,
            root="repo",
            project_id="p",
            project_name="P",
            before_sha="a",
            after_sha="b",
            changed_manifest="changed.json",
            deleted_manifest="deleted.json",
            qdrant_collection="p-code-cobol",
            message_scan_enabled=False,
            message_output_dir=None,
            message_qdrant_collection=None,
            incremental=True,
            verbose=True,
        )
        self.assertIn("tools\\cobol\\cobol_analyzer.py", config.script_path)
        self.assertIn("--incremental", command)
        self.assertIn("changed.json", command)
        self.assertIn("deleted.json", command)


if __name__ == "__main__":
    unittest.main()
