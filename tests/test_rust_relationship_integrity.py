import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.rust import rust_analyzer  # noqa: E402


def _parse_source(source: str):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source_path = root / "src" / "lib.rs"
        source_path.parent.mkdir(parents=True)
        source_path.write_text(source, encoding="utf-8")
        return rust_analyzer.parse_rust_file(str(source_path), str(root))


def _endpoint_ids(payload):
    return {
        "Alias": {item["symbol_id"] for item in payload["aliases"]},
        "Field": {item["symbol_id"] for item in payload["fields"]},
        "Function": {item["symbol_id"] for item in payload["functions"]},
        "Namespace": {item["symbol_id"] for item in payload["namespaces"]},
        "Template": {item["symbol_id"] for item in payload["templates"]},
        "Type": {item["symbol_id"] for item in payload["types"]},
    }


class RustRelationshipIntegrityTests(unittest.TestCase):
    def test_impl_methods_are_declared_by_the_implemented_type(self):
        payload = _parse_source(
            """
            mod matchers {
                struct AccessMatcher;

                impl Matcher for AccessMatcher {
                    fn matches(&self, value: &str) -> bool { !value.is_empty() }
                }
            }

            struct StyleManager<'a>(&'a str);

            impl<'a> StyleManager<'a> {
                fn style(&self) -> &str { self.0 }
            }
            """
        )

        declarations = {
            (edge["source_id"], edge["source_label"], edge["target_id"])
            for edge in payload["relations"]
            if edge["rel_type"] == "DECLARES" and edge["target_label"] == "Function"
        }

        self.assertIn(
            (
                "matchers::AccessMatcher",
                "Type",
                "matchers::AccessMatcher::matches/2@src/lib.rs",
            ),
            declarations,
        )
        self.assertIn(
            ("StyleManager", "Type", "StyleManager::style/1@src/lib.rs"),
            declarations,
        )
        self.assertFalse(
            any(source_label == "Namespace" for _, source_label, _ in declarations),
            declarations,
        )

    def test_every_explicit_relationship_has_materialized_endpoints(self):
        payload = _parse_source(
            """
            struct Command;

            impl external::Command for Command {
                fn execute(&self) {
                    enum LocalResult { Success, Failure }
                    let _ = LocalResult::Success;
                }
            }

            type Error = brush_core::Error;
            type Outcome<T> = Result<T, Error>;
            """
        )
        endpoints = _endpoint_ids(payload)

        unresolved = [
            edge
            for edge in payload["relations"]
            if edge["source_id"] not in endpoints[edge["source_label"]]
            or edge["target_id"] not in endpoints[edge["target_label"]]
        ]

        self.assertEqual(unresolved, [])


if __name__ == "__main__":
    unittest.main()
