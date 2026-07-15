from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
MCP_DIR = ROOT / "code-tiny" / "mcp"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))
if str(MCP_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_DIR))

from framework_registry import default_relationships, framework_for_parser, parser_aliases  # noqa: E402
from tools.aspnet_core.pipeline import run_aspnet_core_analysis  # noqa: E402
from tools.sync.incremental_sync import FRAMEWORK_ANALYZERS, _group_paths_by_framework  # noqa: E402
CORE_FIXTURE = ROOT / "tests" / "fixtures" / "aspnet-core-application"
FRAMEWORK_FIXTURE = ROOT / "tests" / "fixtures" / "aspnet-framework-application"


class AspNetIntegrationTest(unittest.TestCase):
    def test_incremental_registry_declares_csharp_prerequisite(self) -> None:
        self.assertEqual(FRAMEWORK_ANALYZERS["aspnet_core"].prerequisite_parsers, ("csharp",))
        self.assertEqual(FRAMEWORK_ANALYZERS["aspnet_framework"].prerequisite_parsers, ("csharp",))
        self.assertFalse(FRAMEWORK_ANALYZERS["aspnet_core"].writes_vectors)
        self.assertFalse(FRAMEWORK_ANALYZERS["aspnet_framework"].writes_vectors)

    def test_incremental_routing_is_detector_gated(self) -> None:
        core_paths = {
            "CoreWeb.csproj", "Program.cs", "Pages/Index.cshtml", "appsettings.json",
        }
        grouped, evidence = _group_paths_by_framework(core_paths, root=str(CORE_FIXTURE))
        self.assertEqual(grouped["aspnet_core"], core_paths)
        self.assertFalse(grouped["aspnet_framework"])
        self.assertTrue(evidence["aspnet_core"])

        framework_paths = {
            "LegacyWeb.csproj", "Global.asax", "web.config", "Default.aspx", "HomeController.cs",
        }
        grouped, evidence = _group_paths_by_framework(framework_paths, root=str(FRAMEWORK_FIXTURE))
        self.assertEqual(grouped["aspnet_framework"], framework_paths)
        self.assertFalse(grouped["aspnet_core"])
        self.assertTrue(evidence["aspnet_framework"])

    def test_framework_registry_exposes_distinct_aliases_and_shared_contract(self) -> None:
        core = framework_for_parser("aspnet-core")
        legacy = framework_for_parser("asp.net-framework")
        self.assertIsNotNone(core)
        self.assertIsNotNone(legacy)
        self.assertEqual(core.name, "aspnet_core")
        self.assertEqual(legacy.name, "aspnet_framework")
        self.assertIn("HttpEndpoint", core.labels & legacy.labels)
        self.assertIn("PASSES_THROUGH", default_relationships("aspnetcore"))
        self.assertTrue({"aspnet_core", "aspnet_framework"} <= parser_aliases())

    def test_root_cli_exposes_overlays_without_changing_csharp_ownership(self) -> None:
        path = ROOT / "cortex_harness" / "dev.py"
        spec = importlib.util.spec_from_file_location("cortex_dev_aspnet_test", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        assert spec.loader is not None
        spec.loader.exec_module(module)
        self.assertIn("aspnet_core", module.FRAMEWORK_ANALYZERS)
        self.assertIn("aspnet_framework", module.FRAMEWORK_ANALYZERS)
        self.assertEqual(module.LANG_EXTENSIONS["csharp"], {".cs"})

    def test_each_detected_module_uses_its_own_project_for_roslyn(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("WebA", "WebB"):
                module = root / name
                module.mkdir()
                (module / f"{name}.csproj").write_text(
                    '<Project Sdk="Microsoft.NET.Sdk.Web"><PropertyGroup>'
                    '<TargetFramework>net9.0</TargetFramework></PropertyGroup></Project>',
                    encoding="utf-8",
                )
                (module / "Program.cs").write_text(
                    "var builder = Microsoft.AspNetCore.Builder.WebApplication.CreateBuilder(args);",
                    encoding="utf-8",
                )
            project_paths: list[str] = []

            def fake_roslyn(**kwargs):
                project_paths.append(kwargs["project_path"])
                return {
                    "coverage_status": "complete", "workspace_kind": "project",
                    "semantic_enabled": True, "results": [], "diagnostics": [],
                }

            with patch("tools.aspnet_core.pipeline.analyze_csharp_files", side_effect=fake_roslyn):
                result = run_aspnet_core_analysis(root=temporary, project_id="multi")
        self.assertEqual({item.module_path for item in result.modules}, {"WebA", "WebB"})
        self.assertEqual(set(project_paths), {"WebA/WebA.csproj", "WebB/WebB.csproj"})

    def test_deleted_only_module_emits_empty_generation_for_stale_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Plain.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                '<TargetFramework>net9.0</TargetFramework></PropertyGroup></Project>',
                encoding="utf-8",
            )
            (root / "appsettings.json").write_text("{}", encoding="utf-8")
            result = run_aspnet_core_analysis(
                root=temporary, project_id="deleted", selected_paths=("Program.cs",),
                deleted_paths=("Program.cs",), semantic_mode="off",
            )
        self.assertEqual(len(result.modules), 1)
        self.assertEqual(result.facts, ())
        self.assertIn(
            "aspnet_core.deleted_module_cleanup",
            {item.code for item in result.diagnostics},
        )

    def test_completely_removed_project_recovers_prior_module_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            module = root / "Bar"
            module.mkdir()
            (module / "Bar.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk.Web"><PropertyGroup>'
                '<TargetFramework>net9.0</TargetFramework></PropertyGroup></Project>',
                encoding="utf-8",
            )
            (module / "Program.cs").write_text(
                "var app = Microsoft.AspNetCore.Builder.WebApplication.Create();",
                encoding="utf-8",
            )
            result = run_aspnet_core_analysis(
                root=temporary, project_id="deleted-project",
                selected_paths=("Foo/Foo.csproj",),
                deleted_paths=("Foo/Foo.csproj", "Foo/Program.cs"), semantic_mode="off",
            )
        self.assertEqual([item.module_path for item in result.modules], ["Foo"])
        self.assertEqual(result.facts, ())


if __name__ == "__main__":
    unittest.main()
