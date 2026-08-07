import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.common.parse_quality import (  # noqa: E402
    CandidateSummary,
    DamageSummary,
    ParseContext,
    ParserBackend,
    QualityTier,
    SemanticYield,
    aggregate_quality_records,
    atomic_write_json,
    build_quality_record,
    candidate_is_strictly_better,
    candidate_score,
    collect_tree_sitter_damage,
    normalized_repository_path,
)


class _FakeNode:
    def __init__(
        self,
        node_type,
        *,
        start=0,
        end=0,
        point=(0, 0),
        is_error=False,
        is_missing=False,
        children=(),
    ):
        self.type = node_type
        self.start_byte = start
        self.end_byte = end
        self.start_point = point
        self.is_error = is_error
        self.is_missing = is_missing
        self.children = list(children)


class ParseQualityContractTests(unittest.TestCase):
    def test_tree_damage_separates_error_and_missing_and_merges_spans(self):
        root = _FakeNode(
            "translation_unit",
            children=[
                _FakeNode(
                    "function_definition",
                    children=[
                        _FakeNode("ERROR", start=10, end=20, point=(2, 3), is_error=True),
                        _FakeNode(";", start=15, end=15, point=(2, 8), is_missing=True),
                    ],
                )
            ],
        )
        damage = collect_tree_sitter_damage(root, 100)
        self.assertEqual(damage.error_count, 1)
        self.assertEqual(damage.missing_count, 1)
        self.assertEqual(damage.damaged_bytes, 10)
        self.assertEqual(damage.damaged_span_ratio, 0.1)
        self.assertTrue(damage.critical_structural_damage)
        self.assertTrue(all("source" not in value for value in damage.signatures))

    def test_candidate_order_is_frozen_and_requires_strict_improvement(self):
        baseline = CandidateSummary(
            damage=DamageSummary(error_count=2, damaged_span_ratio=0.1),
            semantic_yield=SemanticYield(function_count=1, call_count=1),
        )
        improved = CandidateSummary(
            damage=DamageSummary(error_count=5, damaged_span_ratio=0.05),
            semantic_yield=SemanticYield(function_count=1),
            backend=ParserBackend.LIBCLANG,
        )
        self.assertLess(candidate_score(improved), candidate_score(baseline))
        self.assertTrue(candidate_is_strictly_better(improved, baseline))
        self.assertFalse(candidate_is_strictly_better(baseline, baseline))

    def test_record_is_relative_deterministic_and_private(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "src", "demo.c")
            path.parent.mkdir()
            path.write_text("int demo(void) { return 1; }\n", encoding="utf-8")
            source = path.read_bytes()
            record = build_quality_record(
                root=root,
                path=str(path),
                source=source,
                damage=DamageSummary(source_bytes=len(source)),
                semantic_yield=SemanticYield(function_count=1, stable_scope_count=1),
                context=ParseContext(parser_language="c", source_encoding="utf-8"),
            )
            serialized = record.to_dict()
            self.assertEqual(record.tier, QualityTier.CLEAN)
            self.assertEqual(serialized["file_path"], "src/demo.c")
            self.assertNotIn(root, json.dumps(serialized))
            duplicate = build_quality_record(
                root=root,
                path=str(path),
                source=source,
                damage=DamageSummary(source_bytes=len(source)),
                semantic_yield=SemanticYield(function_count=1, stable_scope_count=1),
                context=ParseContext(parser_language="c", source_encoding="utf-8"),
            )
            self.assertEqual(record.context_fingerprint, duplicate.context_fingerprint)

    def test_paths_outside_root_are_rejected(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            with self.assertRaises(ValueError):
                normalized_repository_path(root, os.path.join(outside, "escape.c"))

    def test_atomic_artifact_is_owner_only_and_bounded(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "artifacts", "quality.json")
            atomic_write_json(path, {"ok": True}, allowed_root=root, max_bytes=1024)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            with self.assertRaises(ValueError):
                atomic_write_json(path, {"value": "x" * 2048}, allowed_root=root, max_bytes=64)

    def test_aggregate_reconciles_file_and_node_semantics(self):
        records = [
            {
                "tier": "recovered",
                "damage": {"error_count": 1, "missing_count": 0},
                "context": {"lossy_decode": False},
                "retry_stages": [],
            },
            {
                "tier": "retry_required",
                "damage": {"error_count": 0, "missing_count": 2},
                "context": {"lossy_decode": True},
                "retry_stages": ["alternate_grammar"],
            },
        ]
        aggregate = aggregate_quality_records(records)
        self.assertEqual(aggregate["file_count"], 2)
        self.assertEqual(aggregate["files_with_error"], 1)
        self.assertEqual(aggregate["files_with_missing"], 1)
        self.assertEqual(aggregate["error_node_total"], 1)
        self.assertEqual(aggregate["missing_node_total"], 2)

    def test_reviewed_manifest_materializes_exactly_100_files(self):
        manifest_path = ROOT / "tests" / "fixtures" / "cplus_parse_quality" / "corpus.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(sum(int(item["count"]) for item in manifest["cohorts"]), 100)
        self.assertEqual(len({item["id"] for item in manifest["cohorts"]}), 10)
        self.assertEqual(
            {item["encoding"] for item in manifest["cohorts"]},
            {"utf-8", "cp932"},
        )
        self.assertEqual(
            {item["extension"] for item in manifest["cohorts"]},
            {".c", ".cpp", ".h", ".pc", ".rc"},
        )


if __name__ == "__main__":
    unittest.main()
