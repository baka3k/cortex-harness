"""YAKE pre-pass wiring in the ingestor (plan 260924-1642-yake-dynamic-rules-doc-sync phase 02).

Covers: rules generated from already-loaded text (D1), the empty-text guard
(C2), folder-mode prune scoping (D2), graceful degradation (D4) and the
single-path gliner × ruler merge (D3/m4).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_DOC_TINY = Path(__file__).resolve().parents[1]


def _load_ingestor():
    spec = importlib.util.spec_from_file_location(
        "graphrag_ingest_langextract_yake_test",
        _DOC_TINY / "graphrag_ingest_langextract.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ingestor = _load_ingestor()

_SAMPLE_TEXT = (
    "The Digital Key Framework uses NFC and Bluetooth transports. "
    "The Key Technical Specification mandates AES-256 with ECDSA. "
    "ISO 18013-5 defines the mDL security requirements."
)


def _args(rules_dir: Path, yake: bool = True) -> argparse.Namespace:
    return argparse.Namespace(
        yake_rules=yake,
        yake_rules_dir=str(rules_dir),
        yake_language="en",
        yake_top=30,
        yake_max_ngram=3,
        source_id=None,
    )


class EnsureYakeRulesTests(unittest.TestCase):
    def test_ensure_yake_rules_uses_loaded_text(self):
        with tempfile.TemporaryDirectory() as temp:
            rules_dir = Path(temp) / "from-yake" / "proj"
            args = _args(rules_dir)

            change = ingestor.ensure_yake_rules(_SAMPLE_TEXT, "docs__spec.pdf", args)

            self.assertEqual(change["status"], "written")
            self.assertTrue(change["patterns"])
            rule_file = rules_dir / "ruler.from-yake.docs__spec.pdf.json"
            self.assertTrue(rule_file.exists())
            patterns = json.loads(rule_file.read_text(encoding="utf-8"))["patterns"]
            self.assertTrue(patterns)

    def test_ensure_yake_rules_empty_text_no_file(self):
        with tempfile.TemporaryDirectory() as temp:
            rules_dir = Path(temp)
            args = _args(rules_dir)

            change = ingestor.ensure_yake_rules("", "empty.pdf", args)

            # stale-file removal is reported so the sidecar can rebuild
            self.assertEqual(change, {"status": "removed"})
            self.assertEqual(list(rules_dir.glob("*.json")), [])

    def test_ensure_yake_rules_disabled_is_noop(self):
        with tempfile.TemporaryDirectory() as temp:
            rules_dir = Path(temp)
            args = _args(rules_dir, yake=False)

            change = ingestor.ensure_yake_rules(_SAMPLE_TEXT, "doc.md", args)

            self.assertIsNone(change)
            self.assertEqual(list(rules_dir.glob("*.json")), [])

    def test_prepass_degrades_gracefully(self):
        with tempfile.TemporaryDirectory() as temp:
            rules_dir = Path(temp)
            args = _args(rules_dir)

            with patch.object(
                ingestor.yake_rules,
                "ensure_rule_file",
                side_effect=RuntimeError("boom"),
            ):
                change = ingestor.ensure_yake_rules(_SAMPLE_TEXT, "doc.md", args)

            self.assertIsNone(change)


class EnvIntTests(unittest.TestCase):
    def test_env_int_defaults_and_coercion(self):
        with patch.dict("os.environ", {"YAKE_TOP": "abc"}):
            self.assertEqual(ingestor._env_int("YAKE_TOP", 150), 150)
        with patch.dict("os.environ", {"YAKE_TOP": "80"}):
            self.assertEqual(ingestor._env_int("YAKE_TOP", 150), 80)
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("YAKE_TOP", None)
            self.assertEqual(ingestor._env_int("YAKE_TOP", 150), 150)


class RulerRefreshTests(unittest.TestCase):
    def _gliner_args(self, rules_dir: Path) -> argparse.Namespace:
        return argparse.Namespace(
            entity_provider="gliner",
            ruler_json=None,
            yake_rules=True,
            yake_rules_dir=str(rules_dir),
        )

    def test_ruler_sidecar_refresh_adds_new_patterns_incrementally(self):
        with tempfile.TemporaryDirectory() as temp:
            rules_dir = Path(temp)
            (rules_dir / "ruler.from-yake.one.md.json").write_text(
                json.dumps({"patterns": [{"label": "TRANSPORT", "pattern": "NFC"}]}),
                encoding="utf-8",
            )
            args = self._gliner_args(rules_dir)

            ruler_nlp = ingestor._get_ruler_nlp(args)
            self.assertIsNotNone(ruler_nlp)
            self.assertEqual([e.text for e in ruler_nlp("UWB link").ents], [])

            # second source's rules must become visible WITHOUT a full rebuild
            change = {
                "status": "written",
                "path": rules_dir / "ruler.from-yake.two.md.json",
                "patterns": [{"label": "TRANSPORT", "pattern": "UWB"}],
            }
            ingestor._refresh_ruler_for_yake_change(args, change)

            self.assertTrue(args._ruler_nlp_ready)
            self.assertEqual(
                [(e.text, e.label_) for e in ruler_nlp("NFC and UWB links").ents],
                [("NFC", "TRANSPORT"), ("UWB", "TRANSPORT")],
            )

    def test_ruler_refresh_removed_forces_rebuild(self):
        with tempfile.TemporaryDirectory() as temp:
            rules_dir = Path(temp)
            args = self._gliner_args(rules_dir)
            ingestor._get_ruler_nlp(args)  # no sources -> ready=True, nlp=None

            ingestor._refresh_ruler_for_yake_change(args, {"status": "removed"})

            self.assertFalse(args._ruler_nlp_ready)

    def test_ruler_refresh_before_build_defers_to_first_build(self):
        with tempfile.TemporaryDirectory() as temp:
            args = self._gliner_args(Path(temp))

            change = {
                "status": "written",
                "path": Path(temp) / "ruler.from-yake.a.md.json",
                "patterns": [{"label": "TRANSPORT", "pattern": "NFC"}],
            }
            ingestor._refresh_ruler_for_yake_change(args, change)

            # not built yet -> stays stale so the first _get_ruler_nlp picks
            # the just-written file up from disk
            self.assertFalse(getattr(args, "_ruler_nlp_ready", False))


class FolderPruneTests(unittest.TestCase):
    def test_folder_prepass_prunes_before_processing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "docs"
            folder.mkdir()
            doc = folder / "keep.md"
            doc.write_text(_SAMPLE_TEXT, encoding="utf-8")

            rules_dir = root / "rules"
            rules_dir.mkdir()
            for source_id in ("keep.md", "gone.md"):
                target = rules_dir / f"ruler.from-yake.{source_id}.json"
                target.write_text(
                    json.dumps({"patterns": [{"label": "KEYWORD", "pattern": "x"}]}),
                    encoding="utf-8",
                )

            args = _args(rules_dir)
            file_paths = ingestor._iter_input_files(folder)

            ingestor._prune_yake_rules_for_folder(folder, file_paths, args)

            self.assertEqual(
                [p.name for p in sorted(rules_dir.glob("*.json"))],
                ["ruler.from-yake.keep.md.json"],
            )


class _FakeGlinerModel:
    def predict_entities(self, text, labels, threshold=0.3):
        return [
            {"text": "Alice", "label": "PERSON", "score": 0.9, "start": 4, "end": 9},
            # duplicate of a ruler match — ruler must win with confidence 1.0
            {"text": "nfc", "label": "TRANSPORT", "score": 0.4, "start": 30, "end": 33},
        ]


class SinglePathMergeTests(unittest.TestCase):
    def test_single_path_gliner_merges_ruler(self):
        import entity_extractors as ee

        with tempfile.TemporaryDirectory() as temp:
            rules = Path(temp) / "ruler.json"
            rules.write_text(
                json.dumps(
                    {
                        "patterns": [
                            {"label": "TRANSPORT", "pattern": "NFC"},
                            {"label": "CRYPTO", "pattern": "AES-256"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            ruler_nlp = ee.build_ruler_pipeline([str(rules)])
            self.assertIsNotNone(ruler_nlp)

            text = "Alice taps NFC protected by AES-256 keys."
            nodes, _relations = ingestor.build_graph_components(
                text,
                "gliner",
                gliner_model=_FakeGlinerModel(),
                gliner_labels=["PERSON", "TRANSPORT", "CRYPTO"],
                ruler_nlp=ruler_nlp,
            )

            found = {(node["name"], node["type"]) for node in nodes.values()}
            self.assertIn(("NFC", "TRANSPORT"), found)
            self.assertIn(("AES-256", "CRYPTO"), found)
            self.assertIn(("Alice", "PERSON"), found)
            confidences = {
                (node["name"], node["type"]): node["confidence"]
                for node in nodes.values()
            }
            self.assertEqual(confidences[("NFC", "TRANSPORT")], 1.0)

    def test_ruler_sources_union_user_and_yake_dir(self):
        with tempfile.TemporaryDirectory() as temp:
            rules_dir = Path(temp) / "from-yake" / "proj"
            rules_dir.mkdir(parents=True)
            args = argparse.Namespace(
                ruler_json=["/custom/ruler.json"],
                yake_rules=True,
                yake_rules_dir=str(rules_dir),
                entity_provider="gliner",
            )

            sources = ingestor._ruler_sources_for(args)

            self.assertEqual(sources, ["/custom/ruler.json", str(rules_dir)])

    def test_ruler_sources_drop_yake_dir_when_disabled(self):
        with tempfile.TemporaryDirectory() as temp:
            rules_dir = Path(temp) / "from-yake" / "proj"
            rules_dir.mkdir(parents=True)
            args = argparse.Namespace(
                ruler_json=None,
                yake_rules=False,
                yake_rules_dir=str(rules_dir),
                entity_provider="gliner",
            )

            self.assertEqual(ingestor._ruler_sources_for(args), [])


if __name__ == "__main__":
    unittest.main()
