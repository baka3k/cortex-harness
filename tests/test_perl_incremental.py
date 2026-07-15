import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.perl.pipeline import normalize_manifest_paths, run_perl_analysis  # noqa: E402


FIXTURE = ROOT / "tests" / "fixtures" / "perl-application"


class PerlIncrementalTest(unittest.TestCase):
    def test_changed_module_expands_reverse_dependency_closure_and_hits_cache(self):
        with tempfile.TemporaryDirectory() as cache:
            full = run_perl_analysis(str(FIXTURE), project_id="p", cache_dir=cache)
            cached_files = tuple(sorted(Path(cache).rglob("*.json")))
            incremental = run_perl_analysis(
                str(FIXTURE),
                project_id="p",
                changed_paths=["lib/App/Util.pm"],
                cache_dir=cache,
            )
            cached_files_after = tuple(sorted(Path(cache).rglob("*.json")))
        returned = {item.file_path for item in incremental.files}
        self.assertIn("lib/App/Util.pm", returned)
        self.assertIn("lib/App/Model.pm", returned)
        full_by_id = {item.symbol_id: item for item in full.symbols}
        for symbol in incremental.symbols:
            self.assertEqual(symbol, full_by_id[symbol.symbol_id])
        self.assertTrue(cached_files)
        self.assertEqual(cached_files, cached_files_after)

    def test_manifest_paths_reject_escape_and_non_owned_extensions(self):
        normalized = normalize_manifest_paths(
            str(FIXTURE),
            ["../../outside.pm", "docs/standalone.pod", "lib/App/Model.pm"],
        )
        self.assertEqual(normalized, ("lib/App/Model.pm",))

    def test_deleted_paths_are_staged_without_source_records(self):
        with tempfile.TemporaryDirectory() as cache:
            result = run_perl_analysis(
                str(FIXTURE),
                project_id="p",
                changed_paths=[],
                deleted_paths=["lib/App/Old.pm"],
                cache_dir=cache,
            )
        self.assertEqual(result.files, ())
        self.assertEqual(result.deleted_paths, ("lib/App/Old.pm",))


if __name__ == "__main__":
    unittest.main()
