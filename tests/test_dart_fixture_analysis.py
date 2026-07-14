import asyncio
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
GOLDEN = ROOT / "tests" / "fixtures" / "flutter-protocol-v1.jsonl"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.flutter.normalizer import normalize_facts, qdrant_payloads, stable_symbol_id  # noqa: E402
from tools.flutter.pipeline import write_canonical_batch  # noqa: E402
from tools.flutter.dart_parser import analyze_project  # noqa: E402
from tools.flutter.detector import project_package_name  # noqa: E402
from tools.flutter.protocol import parse_jsonl  # noqa: E402


class CapturingWriter:
    def __init__(self):
        self.kwargs = None

    async def write_all(self, **kwargs):
        self.kwargs = kwargs
        return {"files": len(kwargs["files"]), "functions": len(kwargs["functions"])}


class DartFixtureAnalysisTest(unittest.TestCase):
    def setUp(self):
        self.facts = parse_jsonl(GOLDEN.read_text(encoding="utf-8").splitlines())

    def test_python_parser_extracts_cross_file_dart_facts(self):
        fixture = ROOT / "tests" / "fixtures" / "flutter-app"
        facts = analyze_project(
            fixture,
            project_id="fixture",
            package_name="cortex_flutter_fixture",
            mode="flutter",
        )
        nodes = {
            str(node.properties.get("name")): node
            for node in facts.nodes
            if node.kind in {"class", "function"}
        }
        relationships = {(edge.relationship, edge.source, edge.target) for edge in facts.edges}

        self.assertEqual(facts.header.sdk_version, "not-required-python-runtime")
        self.assertTrue(facts.header.analyzer_version.startswith("tree-sitter-dart/"))
        self.assertIn("FixtureApp", nodes)
        self.assertIn("HomePage", nodes)
        self.assertIn("main", nodes)
        self.assertTrue(any(kind == "IMPORTS" for kind, _, _ in relationships))
        self.assertTrue(
            any(
                kind == "CALLS" and target == nodes["HomePage"].identity
                for kind, _, target in relationships
            )
        )
        self.assertEqual(facts.summary.processed_files, 6)
        self.assertGreaterEqual(facts.summary.error_count, 1)

    def test_stable_ids_are_project_scoped_and_checkout_independent(self):
        identity = "package:cortex_fixture/main.dart|function|main|27"
        first = stable_symbol_id("fixture", identity)
        second = stable_symbol_id("fixture", identity)
        self.assertEqual(first, second)
        self.assertNotEqual(first, stable_symbol_id("other-project", identity))
        self.assertNotIn("/workspace", first)

    def test_normalizes_canonical_rows_and_qdrant_payloads(self):
        batch = normalize_facts(self.facts, project_name="Fixture", repo="repo")
        self.assertEqual(len(batch.files), 1)
        self.assertEqual(len(batch.functions), 1)
        self.assertEqual(batch.functions[0]["language"], "dart")
        self.assertEqual(batch.relations[0]["rel_type"], "CONTAINS")
        payloads = list(qdrant_payloads(batch))
        self.assertEqual(payloads[0]["symbol_id"], batch.functions[0]["id"])
        self.assertEqual(payloads[0]["project_id"], "fixture")

    def test_writer_receives_only_a_complete_normalized_batch(self):
        batch = normalize_facts(self.facts)
        writer = CapturingWriter()
        counts = asyncio.run(write_canonical_batch(writer, batch))
        self.assertEqual(counts, {"files": 1, "functions": 1})
        self.assertTrue(writer.kwargs["use_full_writers"])
        self.assertEqual(writer.kwargs["files_variant"], "with_imports")

    def test_pure_dart_package_uri_uses_pubspec_name(self):
        with tempfile.TemporaryDirectory(prefix="checkout-name-") as directory:
            root = Path(directory)
            (root / "lib").mkdir()
            (root / "pubspec.yaml").write_text("name: actual_pkg\n", encoding="utf-8")
            (root / "lib" / "target.dart").write_text("void target() {}\n", encoding="utf-8")
            (root / "lib" / "main.dart").write_text(
                "import 'package:actual_pkg/target.dart';\nvoid main() { target(); }\n",
                encoding="utf-8",
            )
            facts = analyze_project(
                root,
                project_id="pure-dart",
                package_name=project_package_name(root),
            )
        self.assertTrue(any(edge.relationship == "IMPORTS" for edge in facts.edges))
        self.assertTrue(any(edge.relationship == "CALLS" for edge in facts.edges))

    def test_unqualified_call_does_not_resolve_to_an_instance_method(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "lib").mkdir()
            (root / "lib" / "a.dart").write_text(
                "class A { void helper() {} }\n",
                encoding="utf-8",
            )
            (root / "lib" / "main.dart").write_text(
                "import 'a.dart';\nvoid main() { helper(); }\n",
                encoding="utf-8",
            )
            facts = analyze_project(root, project_id="scope-test", package_name="scope_test")
        self.assertFalse(
            any(
                edge.relationship == "CALLS"
                and edge.properties.get("resolved_name") == "helper"
                for edge in facts.edges
            )
        )


if __name__ == "__main__":
    unittest.main()
