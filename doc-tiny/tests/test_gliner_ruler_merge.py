"""GLiNER × EntityRuler merge helpers (plan 260924-1642-yake-dynamic-rules-doc-sync phase 02).

Regression guards: the ruler sidecar must work on ``spacy.blank("en")``
without ``en_core_web_sm`` (C1/M2), empty rule files are skipped not fatal
(C2), and ruler matches win collisions at the merge layer (D3).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import entity_extractors as ee


def _write_rules(path: Path, patterns) -> None:
    path.write_text(json.dumps({"patterns": patterns}), encoding="utf-8")


class BuildRulerPipelineTests(unittest.TestCase):
    def test_build_ruler_pipeline_blank_no_model(self):
        with tempfile.TemporaryDirectory() as temp:
            rules_dir = Path(temp)
            _write_rules(
                rules_dir / "ruler.a.json",
                [{"label": "TRANSPORT", "pattern": "NFC"}],
            )
            # C2: empty rule file must be skipped, not raise
            _write_rules(rules_dir / "ruler.empty.json", [])

            nlp = ee.build_ruler_pipeline([str(rules_dir)])

            self.assertIsNotNone(nlp)
            self.assertEqual(nlp.pipe_names, ["entity_ruler"])
            doc = nlp("Payment over NFC is fast.")
            self.assertEqual([(e.text, e.label_) for e in doc.ents], [("NFC", "TRANSPORT")])

    def test_build_ruler_pipeline_returns_none_without_patterns(self):
        with tempfile.TemporaryDirectory() as temp:
            empty_dir = Path(temp) / "rules"
            empty_dir.mkdir()
            self.assertIsNone(ee.build_ruler_pipeline([str(empty_dir)]))
            self.assertIsNone(ee.build_ruler_pipeline([]))
            self.assertIsNone(ee.build_ruler_pipeline([str(empty_dir / "missing.json")]))

    def test_build_ruler_pipeline_mixes_file_and_dir(self):
        with tempfile.TemporaryDirectory() as temp:
            rules_dir = Path(temp) / "dir"
            rules_dir.mkdir()
            _write_rules(
                rules_dir / "one.json",
                [{"label": "CRYPTO", "pattern": "AES-256"}],
            )
            single = Path(temp) / "single.json"
            _write_rules(single, [{"label": "TRANSPORT", "pattern": "UWB"}])

            nlp = ee.build_ruler_pipeline([str(single), str(rules_dir)])

            doc = nlp("AES-256 over UWB")
            self.assertEqual(
                sorted((e.text, e.label_) for e in doc.ents),
                [("AES-256", "CRYPTO"), ("UWB", "TRANSPORT")],
            )


class RulerMatchTests(unittest.TestCase):
    def test_ruler_match_spans(self):
        with tempfile.TemporaryDirectory() as temp:
            rules = Path(temp) / "r.json"
            _write_rules(
                rules,
                [
                    {"label": "TRANSPORT", "pattern": "NFC"},
                    {"label": "CRYPTO", "pattern": "AES-256"},
                ],
            )
            nlp = ee.build_ruler_pipeline([str(rules)])
            text = "The AES-256 key is sent over NFC."

            entities = ee.ruler_match(nlp, text)

            by_name = {e["name"]: e for e in entities}
            self.assertEqual(set(by_name), {"AES-256", "NFC"})
            self.assertEqual(by_name["AES-256"]["type"], "CRYPTO")
            self.assertEqual(by_name["AES-256"]["confidence"], 1.0)
            self.assertEqual(
                text[by_name["NFC"]["start_char"] : by_name["NFC"]["end_char"]], "NFC"
            )

    def test_ruler_match_none_pipeline(self):
        self.assertEqual(ee.ruler_match(None, "some text"), [])
        self.assertEqual(ee.ruler_match(None, ""), [])


class MergeRulerGlinerTests(unittest.TestCase):
    def test_merge_ruler_gliner_prefers_ruler(self):
        ruler = [{"name": "NFC", "type": "TRANSPORT", "confidence": 1.0}]
        gliner = [
            {"name": "nfc", "type": "TRANSPORT", "confidence": 0.4},
            {"name": "Alice", "type": "PERSON", "confidence": 0.9},
        ]

        merged = ee.merge_ruler_gliner(ruler, gliner)

        self.assertEqual(len(merged), 2)
        by_name = {e["name"].casefold(): e for e in merged}
        self.assertEqual(by_name["nfc"]["confidence"], 1.0)
        self.assertEqual(by_name["alice"]["type"], "PERSON")

    def test_merge_ruler_gliner_diff_type_kept(self):
        ruler = [{"name": "aes", "type": "CRYPTO", "confidence": 1.0}]
        gliner = [{"name": "AES", "type": "TECH", "confidence": 0.8}]

        merged = ee.merge_ruler_gliner(ruler, gliner)

        self.assertEqual(len(merged), 2)
        self.assertEqual(
            sorted((e["name"], e["type"]) for e in merged),
            [("AES", "TECH"), ("aes", "CRYPTO")],
        )

    def test_merge_ruler_gliner_empty_inputs(self):
        self.assertEqual(ee.merge_ruler_gliner(None, None), [])
        self.assertEqual(ee.merge_ruler_gliner([], [{"name": "A", "type": "B"}]),
                         [{"name": "A", "type": "B"}])


if __name__ == "__main__":
    unittest.main()
