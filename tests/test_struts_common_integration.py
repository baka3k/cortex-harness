import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.struts.struts_analyzer import parse_args  # noqa: E402


class StrutsCommonIntegrationTest(unittest.TestCase):
    def test_accepts_shared_incremental_and_graph_provider_arguments(self):
        args = parse_args(
            [
                "--root",
                ".",
                "--project-id",
                "sample",
                "--commit-sha-before",
                "before",
                "--commit-sha-after",
                "after",
                "--incremental",
                "--changed-files-manifest",
                "changed.json",
                "--deleted-files-manifest",
                "deleted.json",
                "--ignore-cache",
                "--disable-message-scan",
                "--graph-provider",
                "falkordb",
                "--falkordb-graph",
                "sample",
                "--verbose",
            ]
        )

        self.assertTrue(args.incremental)
        self.assertEqual(args.changed_files_manifest, "changed.json")
        self.assertEqual(args.deleted_files_manifest, "deleted.json")
        self.assertEqual(args.graph_provider, "falkordb")
        self.assertEqual(args.falkordb_graph, "sample")
        self.assertTrue(args.ignore_cache)
        self.assertTrue(args.disable_message_scan)


if __name__ == "__main__":
    unittest.main()
