import sys
import unittest
from pathlib import Path

_MCP_DIR = Path(__file__).resolve().parents[2] / "mcp"
if str(_MCP_DIR) not in sys.path:
    sys.path.insert(0, str(_MCP_DIR))

from framework_registry import (  # noqa: E402
    CAPABILITIES,
    NON_TEXT_SEARCH_PROPERTIES,
    backend_property_union,
    backend_text_property_union,
    searchable_properties,
    text_search_properties,
)


class TextSearchPropertiesTest(unittest.TestCase):
    def test_non_text_properties_excluded_for_every_profile(self):
        for parser_type in CAPABILITIES:
            text_props = text_search_properties(parser_type)
            self.assertNotIn("is_public_api", text_props, parser_type)
            self.assertNotIn("parse_depth", text_props, parser_type)
            self.assertTrue(
                set(text_props).isdisjoint(NON_TEXT_SEARCH_PROPERTIES),
                parser_type,
            )
            self.assertTrue(text_props, parser_type)

    def test_global_union_excludes_non_text_properties(self):
        all_text = text_search_properties()
        self.assertTrue(set(all_text).isdisjoint(NON_TEXT_SEARCH_PROPERTIES))
        self.assertIn("name", all_text)
        raw_union = searchable_properties()
        self.assertIn("is_public_api", raw_union)
        self.assertLess(set(all_text), set(raw_union))

    def test_backend_text_property_union_filters_non_text(self):
        for backend in ("cplus", "android"):
            text_union = backend_text_property_union(backend)
            self.assertTrue(
                set(text_union).isdisjoint(NON_TEXT_SEARCH_PROPERTIES), backend
            )
            raw_union = backend_property_union(backend)
            self.assertIn("is_public_api", raw_union)
            self.assertLessEqual(set(text_union), set(raw_union))

    def test_unknown_parser_returns_empty_like_searchable_properties(self):
        self.assertEqual(searchable_properties("no-such-parser"), ())
        self.assertEqual(text_search_properties("no-such-parser"), ())


if __name__ == "__main__":
    unittest.main()
