import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.perl.models import stable_id  # noqa: E402
from tools.perl.parser_runtime import capabilities, new_parser  # noqa: E402
from tools.perl.pipeline import run_perl_analysis, scan_perl_files  # noqa: E402


FIXTURE = ROOT / "tests" / "fixtures" / "perl-application"


class PerlParserTest(unittest.TestCase):
    def test_runtime_loads_pinned_grammar_and_reports_capabilities(self):
        info = capabilities()
        self.assertEqual(info.language_name, "perl")
        self.assertEqual(info.grammar_version, "1.2.1")
        self.assertGreaterEqual(info.grammar_abi, 14)
        tree = new_parser().parse(b"package Example; sub hello { 1 }")
        self.assertFalse(tree.root_node.has_error)

    def test_supported_scanner_owns_only_pl_pm_and_t(self):
        paths, diagnostics = scan_perl_files(str(FIXTURE))
        self.assertFalse(diagnostics)
        self.assertEqual(
            set(paths),
            {"bin/app.pl", "lib/App/Broken.pm", "lib/App/Model.pm", "lib/App/Util.pm", "t/model.t"},
        )

    def test_full_analysis_extracts_structural_facts_and_uncertainty(self):
        with tempfile.TemporaryDirectory() as cache:
            result = run_perl_analysis(
                str(FIXTURE),
                project_id="perl-fixture",
                cache_dir=cache,
                include_docs=True,
            )
        package_names = {item.fq_name for item in result.symbols if item.kind == "package"}
        sub_names = {item.fq_name for item in result.symbols if item.kind == "subroutine"}
        declaration_kinds = {item.declaration_kind for item in result.symbols if item.kind == "variable"}
        import_kinds = {item.kind for item in result.imports}
        reference_kinds = {item.kind for item in result.references}

        self.assertIn("App::Model", package_names)
        self.assertIn("App::Secondary", package_names)
        self.assertIn("App::Util::helper", sub_names)
        mutable = next(item for item in result.symbols if item.fq_name == "App::Model::mutable")
        self.assertIn("lvalue", mutable.attributes)
        self.assertTrue(any(value.startswith("my:lexical") for value in declaration_kinds))
        self.assertTrue(any(value.startswith("our:package") for value in declaration_kinds))
        self.assertTrue(any(value.startswith("local:dynamic-local") for value in declaration_kinds))
        self.assertTrue({"use", "no", "require"}.issubset(import_kinds))
        self.assertTrue({"direct", "qualified", "method", "coderef", "eval"}.issubset(reference_kinds))
        self.assertTrue(any(item.resolution_status == "resolved" for item in result.references))
        self.assertTrue(all(item.resolution_status != "resolved" for item in result.references if item.kind in {"method", "coderef", "eval"}))
        self.assertTrue(result.documentation)
        self.assertEqual(result.coverage, "partial")

    def test_json_and_ids_are_checkout_independent_and_byte_stable(self):
        first = stable_id("project", "lib/App.pm", "App", "App", "subroutine", "App::run")
        second = stable_id("project", "lib/App.pm", "App", "App", "subroutine", "App::run")
        self.assertEqual(first, second)
        with tempfile.TemporaryDirectory() as cache:
            one = run_perl_analysis(str(FIXTURE), project_id="p", cache_dir=cache).to_json()
            two = run_perl_analysis(str(FIXTURE), project_id="p", cache_dir=cache).to_json()
        self.assertEqual(one, two)
        payload = json.loads(one)
        self.assertEqual(payload["normalized_root"], ".")
        self.assertNotIn(str(FIXTURE), one)
        with tempfile.TemporaryDirectory() as left, tempfile.TemporaryDirectory() as right:
            left_root = Path(left) / "checkout"
            right_root = Path(right) / "checkout"
            shutil.copytree(FIXTURE, left_root)
            shutil.copytree(FIXTURE, right_root)
            left_json = run_perl_analysis(str(left_root), project_id="p", cache_dir=left).to_json()
            right_json = run_perl_analysis(str(right_root), project_id="p", cache_dir=right).to_json()
        self.assertEqual(left_json, right_json)

    def test_budgets_and_secret_redaction_are_deterministic(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as cache:
            root = Path(directory)
            source = "# password=supersecret\npackage Secret;\nsub reveal { my $password = 'supersecret'; }\n"
            (root / "Secret.pm").write_text(source, encoding="utf-8")
            result = run_perl_analysis(
                str(root),
                project_id="secret",
                cache_dir=cache,
                include_docs=True,
                max_file_bytes=64,
            )
        payload = result.to_json()
        self.assertNotIn("supersecret", payload)
        self.assertEqual(result.coverage, "partial")
        self.assertTrue(any(item.code == "perl.scan.file_byte_budget" for item in result.diagnostics))


if __name__ == "__main__":
    unittest.main()
