from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.aspnet_core.pipeline import run_aspnet_core_analysis
from tools.aspnet_framework.pipeline import run_aspnet_framework_analysis


CORE_FIXTURE = ROOT / "tests" / "fixtures" / "aspnet-core-application"
FRAMEWORK_FIXTURE = ROOT / "tests" / "fixtures" / "aspnet-framework-application"


class AspNetFixtureAnalysisTest(unittest.TestCase):
    def test_core_fixture_has_routes_pipeline_di_views_and_redacted_config(self) -> None:
        result = run_aspnet_core_analysis(
            root=str(CORE_FIXTURE), project_id="core-fixture", semantic_mode="off",
        )
        kinds = {item.kind for item in result.facts}
        relationships = {item.relationship_type for item in result.relationships}
        self.assertEqual(len(result.modules), 1)
        self.assertTrue({"HttpEndpoint", "Route", "Middleware", "Service", "RazorPage", "ConfigurationKey"} <= kinds)
        self.assertTrue({"MAPPED_TO", "PASSES_THROUGH", "HANDLED_BY", "RENDERS"} <= relationships)
        self.assertNotIn("must-not-leak", result.to_json())
        middleware = sorted(
            (item for item in result.facts if item.kind == "Middleware"),
            key=lambda item: int(item.properties["position"]),
        )
        self.assertEqual([item.properties["position"] for item in middleware], list(range(len(middleware))))

    def test_framework_fixture_has_legacy_pipeline_webforms_and_redacted_config(self) -> None:
        result = run_aspnet_framework_analysis(
            root=str(FRAMEWORK_FIXTURE), project_id="framework-fixture", semantic_mode="off",
        )
        kinds = {item.kind for item in result.facts}
        relationships = {item.relationship_type for item in result.relationships}
        self.assertEqual(len(result.modules), 1)
        self.assertTrue({"ApplicationEvent", "HttpModule", "HttpHandler", "WebFormPage", "SessionState", "ConfigurationKey"} <= kinds)
        self.assertTrue({"POSTS_BACK_TO", "WRITES_SESSION", "LOADS_FROM"} <= relationships)
        self.assertNotIn("must-not-leak", result.to_json())

    def test_stable_ids_and_serialization_do_not_depend_on_checkout_root(self) -> None:
        first = run_aspnet_core_analysis(
            root=str(CORE_FIXTURE), project_id="stable", semantic_mode="off",
        )
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "copy"
            shutil.copytree(CORE_FIXTURE, copied)
            second = run_aspnet_core_analysis(
                root=str(copied), project_id="stable", semantic_mode="off",
            )
        self.assertEqual(first.to_json(), second.to_json())

    def test_unrelated_csharp_project_does_not_activate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, "Plain.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net9.0</TargetFramework></PropertyGroup></Project>',
                encoding="utf-8",
            )
            Path(temporary, "Program.cs").write_text("System.Console.WriteLine(1);", encoding="utf-8")
            core = run_aspnet_core_analysis(root=temporary, project_id="plain", semantic_mode="off")
            framework = run_aspnet_framework_analysis(root=temporary, project_id="plain", semantic_mode="off")
        self.assertEqual(core.modules, ())
        self.assertEqual(framework.modules, ())

    def test_roslyn_worker_in_analyzer_tooling_does_not_activate_aspnet_core(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            worker = Path(temporary, "tools", "common", "aspnet", "roslyn_worker")
            worker.mkdir(parents=True)
            (worker / "AspNetRoslynWorker.csproj").write_text(
                """<Project Sdk=\"Microsoft.NET.Sdk\">
  <PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net8.0</TargetFramework></PropertyGroup>
  <ItemGroup>
    <PackageReference Include=\"Microsoft.CodeAnalysis.CSharp\" Version=\"4.11.0\" />
    <FrameworkReference Include=\"Microsoft.AspNetCore.App\" />
  </ItemGroup>
</Project>""",
                encoding="utf-8",
            )
            (worker / "Program.cs").write_text(
                "using Microsoft.AspNetCore.Builder; System.Console.WriteLine(1);",
                encoding="utf-8",
            )

            result = run_aspnet_core_analysis(
                root=temporary,
                project_id="python-typescript-app",
                semantic_mode="off",
            )

        self.assertEqual(result.modules, ())

    def test_module_incremental_result_matches_full_module_semantics(self) -> None:
        full = run_aspnet_core_analysis(
            root=str(CORE_FIXTURE), project_id="incremental", semantic_mode="off",
        )
        changed = run_aspnet_core_analysis(
            root=str(CORE_FIXTURE), project_id="incremental", semantic_mode="off",
            selected_paths=("Program.cs",),
        )
        self.assertEqual(
            [item.stable_id for item in full.facts],
            [item.stable_id for item in changed.facts],
        )
        self.assertEqual(
            [item.stable_id for item in full.relationships],
            [item.stable_id for item in changed.relationships],
        )

    def test_malformed_optional_config_is_partial_without_losing_code_facts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "copy"
            shutil.copytree(CORE_FIXTURE, copied)
            (copied / "appsettings.json").write_text('{"broken":', encoding="utf-8")
            result = run_aspnet_core_analysis(
                root=str(copied), project_id="malformed", semantic_mode="off",
            )
        self.assertEqual(result.coverage_status, "partial")
        self.assertIn("HttpEndpoint", {item.kind for item in result.facts})
        self.assertIn("aspnet_core.config.parse_error", {item.code for item in result.diagnostics})

    def test_frameworks_share_the_public_migration_vocabulary(self) -> None:
        core = run_aspnet_core_analysis(
            root=str(CORE_FIXTURE), project_id="core-contract", semantic_mode="off",
        )
        legacy = run_aspnet_framework_analysis(
            root=str(FRAMEWORK_FIXTURE), project_id="legacy-contract", semantic_mode="off",
        )
        shared_labels = {item.kind for item in core.facts} & {item.kind for item in legacy.facts}
        shared_relationships = (
            {item.relationship_type for item in core.relationships}
            & {item.relationship_type for item in legacy.relationships}
        )
        self.assertTrue({"HttpEndpoint", "Route", "Controller", "ConfigurationKey"} <= shared_labels)
        self.assertTrue({"MAPPED_TO", "HANDLED_BY"} <= shared_relationships)

    def test_semantic_links_target_canonical_csharp_ids_only_when_compilation_succeeds(self) -> None:
        core = run_aspnet_core_analysis(
            root=str(CORE_FIXTURE), project_id="core-semantic", semantic_mode="on",
        )
        semantic_links = [
            item for item in core.relationships if item.relationship_type == "SEMANTIC_OF"
        ]
        self.assertTrue(semantic_links)
        self.assertIn(
            "CoreWeb.Controllers::HomeController::Status/0@Controllers/HomeController.cs",
            {item.to_id for item in semantic_links},
        )
        self.assertTrue(all(not item.to_generated for item in semantic_links))

    def test_controller_properties_constructors_and_private_helpers_are_not_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "copy"
            shutil.copytree(CORE_FIXTURE, copied)
            controller = copied / "Controllers" / "HomeController.cs"
            source = controller.read_text(encoding="utf-8")
            source = source.replace(
                "{\n    [HttpGet",
                "{\n    public HomeController() {}\n"
                "    public object Injected { get; set; } = new();\n"
                "    private void Helper() {}\n\n"
                "    [NonAction] public void Utility() {}\n\n"
                "    [HttpGet",
            )
            controller.write_text(source, encoding="utf-8")
            result = run_aspnet_core_analysis(
                root=str(copied), project_id="member-kinds", semantic_mode="on",
            )
        actions = {item.name for item in result.facts if item.kind == "Action"}
        self.assertIn("Status", actions)
        self.assertTrue({"HomeController", "Injected", "Helper", "Utility"}.isdisjoint(actions))


if __name__ == "__main__":
    unittest.main()
