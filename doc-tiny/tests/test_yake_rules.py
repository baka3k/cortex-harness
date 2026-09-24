"""yake_rules module (plan 260924-1642-yake-dynamic-rules-doc-sync phase 01).

Covers: keyword extraction ordering, label heuristics, pattern building,
per-source rule file naming/pruning, the empty-patterns guard (C2), the
relpath-safe file naming (C3) and the CLI ruler output.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

_DOC_TINY = Path(__file__).resolve().parents[1]


def _load_yake_rules():
    spec = importlib.util.spec_from_file_location(
        "yake_rules_under_test",
        _DOC_TINY / "yake_rules.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


yake_rules = _load_yake_rules()

_SAMPLE_TEXT = (
    "The Car Connectivity Consortium Digital Key Framework lets a mobile device act as a "
    "vehicle key. Digital Key uses NFC and Bluetooth and UWB transports. "
    "ISO 18013-5 and RFC 5280 define the certificate profile. "
    "The Key Technical Specification mandates AES-256 encryption with ECDSA "
    "and HMAC-SHA-256 for device pairing."
)


class ExtractorTests(unittest.TestCase):
    def test_extract_returns_score_sorted_keywords(self):
        extractor = yake_rules.YakeRuleExtractor(language="en", top=30)
        keywords = extractor.extract(_SAMPLE_TEXT)

        self.assertTrue(keywords)
        scores = [score for _phrase, score in keywords]
        self.assertEqual(scores, sorted(scores))

    def test_extract_empty_text_returns_no_keywords(self):
        extractor = yake_rules.YakeRuleExtractor(language="en", top=10)
        self.assertEqual(extractor.extract(""), [])
        self.assertEqual(extractor.extract("   \n\t "), [])


class LabelHeuristicsTests(unittest.TestCase):
    def test_infer_entity_label_heuristics(self):
        cases = {
            "RFC 5280": "STANDARD",
            "IEEE 802.11": "STANDARD",
            "ISO 18013-5": "STANDARD",
            "NFC": "TRANSPORT",
            "Bluetooth": "TRANSPORT",
            "AES-256": "CRYPTO",
            "ECDSA": "CRYPTO",
            "HMAC-SHA-256": "KDF_HASH",
            "Key Technical Specification": "DOCUMENT",
            "Spake2+": "ALGORITHM",
            "random thing": "KEYWORD",
        }
        for phrase, expected in cases.items():
            self.assertEqual(
                yake_rules.infer_entity_label(phrase),
                expected,
                msg=f"label for {phrase!r}",
            )


class PatternBuildingTests(unittest.TestCase):
    def test_keywords_to_ruler_patterns_dedupe_and_min_length(self):
        patterns = yake_rules.keywords_to_ruler_patterns(
            [("NFC", 0.1), ("nfc", 0.2), ("X", 0.3), ("AES-256", 0.4)]
        )

        self.assertEqual([p["pattern"] for p in patterns], ["NFC", "AES-256"])
        self.assertEqual([p["label"] for p in patterns], ["TRANSPORT", "CRYPTO"])

    def test_keywords_to_ruler_patterns_without_heuristics(self):
        patterns = yake_rules.keywords_to_ruler_patterns(
            [("NFC", 0.1)], use_heuristics=False, default_label="TERM"
        )
        self.assertEqual(patterns, [{"label": "TERM", "pattern": "NFC"}])


class RuleFileTests(unittest.TestCase):
    def test_ensure_rule_file_writes_patterns(self):
        with tempfile.TemporaryDirectory() as temp:
            rules_dir = Path(temp) / "from-yake" / "proj"
            path = yake_rules.ensure_rule_file(
                rules_dir, "docs__spec.pdf", _SAMPLE_TEXT,
                language="en", top=30,
            )

            self.assertIsNotNone(path)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("patterns", data)
            self.assertTrue(data["patterns"])
            self.assertEqual(
                path.name, "ruler.from-yake.docs__spec.pdf.json"
            )

    def test_ensure_rule_file_skips_empty_and_deletes_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            rules_dir = Path(temp)
            stale = yake_rules.rule_file_for(rules_dir, "empty.pdf")
            stale.parent.mkdir(parents=True, exist_ok=True)
            stale.write_text('{"patterns": [{"label": "KEYWORD", "pattern": "old"}]}')

            result = yake_rules.ensure_rule_file(
                rules_dir, "empty.pdf", "", language="en", top=10,
            )

            self.assertIsNone(result)
            self.assertFalse(stale.exists())

    def test_ensure_rule_file_naming_relpath_source(self):
        # relpath-based ids must not collide (plan C3: stem collision)
        with tempfile.TemporaryDirectory() as temp:
            rules_dir = Path(temp)
            first = yake_rules.rule_file_for(rules_dir, "docs/a spec.pdf")
            second = yake_rules.rule_file_for(rules_dir, "docs/b spec.pdf")
            stem_only = yake_rules.rule_file_for(rules_dir, "spec.pdf")

            self.assertNotEqual(first, second)
            self.assertNotEqual(first, stem_only)
            self.assertEqual(first.name, "ruler.from-yake.docs__a spec.pdf.json")

    def test_prune_rule_files_and_prune_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            rules_dir = Path(temp)
            for source_id in ("docs__a.md", "docs__b.md", "docs__c.md"):
                yake_rules.ensure_rule_file(
                    rules_dir, source_id, _SAMPLE_TEXT, language="en", top=5,
                )
            self.assertEqual(len(list(rules_dir.glob("*.json"))), 3)

            removed = yake_rules.prune_rule_files(rules_dir, ["docs__a.md", "docs__c.md"])
            self.assertEqual(
                sorted(p.name for p in rules_dir.glob("*.json")),
                ["ruler.from-yake.docs__a.md.json", "ruler.from-yake.docs__c.md.json"],
            )
            self.assertEqual(len(removed), 1)

            removed = yake_rules.prune_sources(rules_dir, ["docs__a.md"])
            self.assertEqual(
                [p.name for p in rules_dir.glob("*.json")],
                ["ruler.from-yake.docs__c.md.json"],
            )
            self.assertEqual(removed, ["ruler.from-yake.docs__a.md.json"])

    def test_merge_rule_files_dedupes(self):
        with tempfile.TemporaryDirectory() as temp:
            rules_dir = Path(temp)
            yake_rules.ensure_rule_file(
                rules_dir, "one.md", _SAMPLE_TEXT, language="en", top=5,
            )
            yake_rules.ensure_rule_file(
                rules_dir, "two.md", _SAMPLE_TEXT, language="en", top=5,
            )

            merged = yake_rules.merge_rule_files(list(rules_dir.glob("*.json")))

            keys = [
                (p["label"], p["pattern"].casefold()) for p in merged["patterns"]
            ]
            self.assertEqual(len(keys), len(set(keys)))


class LoadTextTests(unittest.TestCase):
    def test_load_text_skips_xlsx(self):
        with tempfile.TemporaryDirectory() as temp:
            xlsx = Path(temp) / "sheet.xlsx"
            xlsx.write_bytes(b"not really an xlsx")
            self.assertEqual(yake_rules.load_text(xlsx), "")

    def test_load_text_plain_file(self):
        with tempfile.TemporaryDirectory() as temp:
            txt = Path(temp) / "note.txt"
            txt.write_text(_SAMPLE_TEXT, encoding="utf-8")
            self.assertIn("Digital Key", yake_rules.load_text(txt))


class CliTests(unittest.TestCase):
    def test_cli_ruler_output(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp) / "doc.txt"
            src.write_text(_SAMPLE_TEXT, encoding="utf-8")
            out = Path(temp) / "out.json"

            rc = yake_rules.main([
                "--file", str(src), "-l", "en", "--top", "20",
                "--format", "ruler", "-o", str(out),
            ])

            self.assertEqual(rc, 0)
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertIsInstance(data["patterns"], list)
            self.assertTrue(data["patterns"])

    def test_cli_prune_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            rules_dir = Path(temp) / "rules"
            yake_rules.ensure_rule_file(
                rules_dir, "docs__gone.md", _SAMPLE_TEXT, language="en", top=5,
            )
            yake_rules.ensure_rule_file(
                rules_dir, "docs__keep.md", _SAMPLE_TEXT, language="en", top=5,
            )

            rc = yake_rules.main([
                "--rules-dir", str(rules_dir),
                "--prune-sources", "docs__gone.md",
            ])

            self.assertEqual(rc, 0)
            self.assertEqual(
                [p.name for p in rules_dir.glob("*.json")],
                ["ruler.from-yake.docs__keep.md.json"],
            )


if __name__ == "__main__":
    unittest.main()
