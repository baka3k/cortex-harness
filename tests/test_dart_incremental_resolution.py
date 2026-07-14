import argparse
import asyncio
import tempfile
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.flutter.cache import DependencyIndex, select_incremental_facts  # noqa: E402
from tools.flutter.dart_parser import analyze_project  # noqa: E402
from tools.flutter.normalizer import normalize_facts  # noqa: E402
from tools.flutter.flutter_analyzer import write_graph  # noqa: E402


class _FailingWriter:
    def __init__(self, **_kwargs):
        pass

    async def write_all(self, **_kwargs):
        raise RuntimeError("injected writer failure")


class _RecordingDriver:
    def __init__(self):
        self.queries = []

    async def execute_query(self, *args):
        self.queries.append(args)
        return [], None, None

    async def close(self):
        return None


class DartIncrementalResolutionTest(unittest.TestCase):
    def test_reverse_dependents_expand_transitively_without_unrelated_files(self):
        index = DependencyIndex(
            {
                "lib/main.dart": {"lib/app.dart"},
                "lib/app.dart": {"lib/widgets/home.dart"},
                "lib/unrelated.dart": {"lib/other.dart"},
            }
        )
        impacted = index.impacted_files({"lib/widgets/home.dart"})
        self.assertEqual(
            impacted,
            {"lib/widgets/home.dart", "lib/app.dart", "lib/main.dart"},
        )

    def test_cache_is_versioned_and_deterministic(self):
        index = DependencyIndex({"lib/b.dart": {"lib/c.dart"}, "lib/a.dart": {"lib/b.dart"}})
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "dart-dependencies.json")
            index.save(path)
            first = path.read_text(encoding="utf-8")
            loaded = DependencyIndex.load(path)
            loaded.save(path)
            second = path.read_text(encoding="utf-8")
        self.assertEqual(first, second)
        self.assertEqual(loaded.dependencies, index.dependencies)

    def test_incremental_selection_keeps_targets_without_rewriting_them(self):
        facts = analyze_project(
            ROOT / "tests" / "fixtures" / "flutter-app",
            project_id="fixture",
            package_name="cortex_flutter_fixture",
            mode="flutter",
        )
        selected = select_incremental_facts(facts, {"lib/main.dart"})
        batch = normalize_facts(selected)

        self.assertEqual({row["file_path"] for row in batch.files}, {"lib/main.dart"})
        self.assertTrue(any(edge["rel_type"] == "CALLS" for edge in batch.relations))
        self.assertNotIn("lib/widgets/home_page.dart", {row["file_path"] for row in batch.classes})

    def test_empty_incremental_selection_writes_nothing(self):
        facts = analyze_project(
            ROOT / "tests" / "fixtures" / "flutter-app",
            project_id="fixture",
            package_name="cortex_flutter_fixture",
        )
        selected = select_incremental_facts(facts, set())
        batch = normalize_facts(selected)
        self.assertEqual(selected.summary.processed_files, 0)
        self.assertFalse(batch.files)
        self.assertFalse(batch.relations)

    def test_failed_replacement_does_not_delete_existing_graph_facts(self):
        facts = analyze_project(
            ROOT / "tests" / "fixtures" / "flutter-app",
            project_id="fixture",
            package_name="cortex_flutter_fixture",
        )
        driver = _RecordingDriver()
        args = argparse.Namespace(
            neo4j_db="fixture",
            neo4j_batch_size=100,
            verbose=False,
            project_name="Fixture",
            repo="fixture",
            root=str(ROOT),
            build_system="flutter",
        )
        with (
            patch(
                "tools.flutter.flutter_analyzer.create_graph_driver_from_args",
                new=AsyncMock(return_value=driver),
            ),
            patch("tools.flutter.flutter_analyzer.LanguageCodeWriter", _FailingWriter),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected writer failure"):
                asyncio.run(write_graph(args, facts, ["lib/main.dart"]))
        self.assertFalse(driver.queries)


if __name__ == "__main__":
    unittest.main()
