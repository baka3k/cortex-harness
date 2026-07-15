import asyncio
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from cortex_harness.dev import LANG_ANALYZERS, LANG_EXTENSIONS, _detect_langs  # noqa: E402
from tools.perl.perl_analyzer import build_graph_rows, parse_args  # noqa: E402
from tools.perl.pipeline import run_perl_analysis  # noqa: E402
from tools.sync.incremental_sync import ANALYZERS, _group_paths_by_parser  # noqa: E402
from tools.sync.owner_manifest import SUPPORTED_PARSERS, build_owner_maps  # noqa: E402


FIXTURE = ROOT / "tests" / "fixtures" / "perl-application"


class PerlIntegrationTest(unittest.TestCase):
    def test_registries_and_root_discovery_agree(self):
        self.assertIn("perl", ANALYZERS)
        self.assertIn("perl", SUPPORTED_PARSERS)
        self.assertIn("perl", LANG_ANALYZERS)
        self.assertEqual(LANG_EXTENSIONS["perl"], {".pl", ".pm", ".t"})
        grouped = _group_paths_by_parser(
            ["bin/app.pl", "lib/App.pm", "t/model.t", "docs/model.pod"],
            root=str(FIXTURE),
        )
        self.assertEqual(grouped["perl"], {"bin/app.pl", "lib/App.pm", "t/model.t"})
        self.assertIn("perl", _detect_langs(FIXTURE))
        owners = build_owner_maps(root=str(FIXTURE), parsers=["perl"])
        self.assertEqual(
            owners.owned_by_parser["perl"],
            {"bin/app.pl", "lib/App/Broken.pm", "lib/App/Model.pm", "lib/App/Util.pm", "t/model.t"},
        )

    def test_cli_accepts_shared_incremental_contract(self):
        args = parse_args(
            [
                "--root", str(FIXTURE),
                "--project-id", "p",
                "--project-name", "P",
                "--commit-sha-before", "a",
                "--commit-sha-after", "b",
                "--incremental",
                "--changed-files-manifest", "changed.txt",
                "--deleted-files-manifest", "deleted.txt",
                "--disable-message-scan",
                "--dry-run",
            ]
        )
        self.assertTrue(args.incremental)
        self.assertFalse(args.enable_message_scan)

    def test_graph_rows_use_only_canonical_labels_and_resolved_calls(self):
        with tempfile.TemporaryDirectory() as cache:
            result = run_perl_analysis(str(FIXTURE), project_id="p", cache_dir=cache)
        rows = build_graph_rows(result, project_name="P", repo="P/perl")
        labels = {
            relation[key]
            for relation in rows["relations"]
            for key in ("source_label", "target_label")
        }
        self.assertTrue(labels.issubset({"File", "Namespace", "Function", "Field"}))
        resolved_pairs = {
            (item.source_symbol_id, item.target_symbol_id)
            for item in result.references
            if item.resolution_status == "resolved" and item.source_symbol_id
        }
        self.assertEqual(
            {(item["caller_id"], item["callee_id"]) for item in rows["calls"]},
            resolved_pairs,
        )

    def test_unified_mcp_routes_and_lists_perl(self):
        import mcp.unified_mcp as unified

        self.assertEqual(unified._resolve_backend_name("perl"), "cplus")
        tool = unified.tool_list_parsers
        function = getattr(tool, "fn", tool)
        payload = asyncio.run(function())
        self.assertIn("perl", payload["parsers"])


if __name__ == "__main__":
    unittest.main()
