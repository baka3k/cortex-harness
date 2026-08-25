from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

CODE_TINY = Path(__file__).resolve().parents[1] / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.common.aspnet.roslyn_adapter import analyze_csharp_files
from tools.common.aspnet.cli_runtime import write_outputs
from tools.common.aspnet.models import AnalysisResult, Diagnostic, SourceSpan
from tools.common.aspnet.safe_formats import parse_xml_file


DOTNET_AVAILABLE = shutil.which("dotnet") is not None


class AspNetProtocolAndSecurityTest(unittest.TestCase):
    @unittest.skipUnless(DOTNET_AVAILABLE, "requires the .NET SDK for the Roslyn worker")
    def test_worker_emits_versioned_syntax_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary, "Program.cs")
            source.write_text(
                'class Program { static void Main() { WebApplication.CreateBuilder(); } }',
                encoding="utf-8",
            )
            result = analyze_csharp_files(
                root=temporary, files=["Program.cs"], semantic_mode="off", timeout_sec=120,
            )
        self.assertEqual(result["protocol_version"], "aspnet-roslyn-v1")
        self.assertEqual(result["results"][0]["file_path"], "Program.cs")
        self.assertTrue(result["results"][0]["evidence"]["types"])

    def test_outside_root_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            source = Path(outside, "Outside.cs")
            source.write_text("class Outside {}", encoding="utf-8")
            with self.assertRaises(ValueError):
                analyze_csharp_files(root=root, files=[str(source)], semantic_mode="off")

    @unittest.skipUnless(DOTNET_AVAILABLE, "requires the .NET SDK for the Roslyn worker")
    def test_semantic_mode_never_evaluates_project_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "must-not-exist.txt"
            (root / "Hostile.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk.Web"><PropertyGroup>'
                '<TargetFramework>net9.0</TargetFramework></PropertyGroup>'
                '<Target Name="Exploit" BeforeTargets="Compile">'
                f'<WriteLinesToFile File="{marker}" Lines="executed" />'
                '</Target></Project>',
                encoding="utf-8",
            )
            (root / "Program.cs").write_text(
                "var app = Microsoft.AspNetCore.Builder.WebApplication.Create();",
                encoding="utf-8",
            )
            payload = analyze_csharp_files(
                root=temporary, files=("Program.cs",), semantic_mode="on",
                project_path="Hostile.csproj",
            )
            self.assertEqual(payload["workspace_kind"], "safe_compilation")
            self.assertFalse(marker.exists())

    @unittest.skipUnless(DOTNET_AVAILABLE, "requires the .NET SDK for the Roslyn worker")
    def test_project_semantics_are_explicitly_partial_when_safe_compilation_cannot_honor_properties(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Feature.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                '<TargetFramework>net9.0</TargetFramework><DefineConstants>FEATURE</DefineConstants>'
                '</PropertyGroup></Project>',
                encoding="utf-8",
            )
            (root / "Feature.cs").write_text(
                "#if FEATURE\npublic class FeatureController {}\n#endif\n",
                encoding="utf-8",
            )
            payload = analyze_csharp_files(
                root=temporary, files=("Feature.cs",), semantic_mode="on",
                project_path="Feature.csproj",
            )
        self.assertEqual(payload["coverage_status"], "partial")
        self.assertIn(
            "aspnet.roslyn.project_semantics_unavailable",
            {item["code"] for item in payload["diagnostics"]},
        )

    def test_xml_dtd_and_entity_declarations_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, "web.config").write_text(
                '<!DOCTYPE x [<!ENTITY leak SYSTEM "file:///etc/passwd">]><configuration>&leak;</configuration>',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                parse_xml_file(temporary, "web.config")

    def test_standalone_diagnostics_are_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary, "diagnostics.json")
            result = AnalysisResult(
                project_id="p", project_name="P", framework="aspnet_core",
                diagnostics=(Diagnostic(
                    "unsafe", "Password=must-not-leak;User ID=private-user",
                    source=SourceSpan("appsettings.json"),
                    details={"api_key": "also-secret"},
                ),),
            )
            write_outputs(
                SimpleNamespace(preview_output="", diagnostics_output=str(output)), result,
            )
            payload = output.read_text(encoding="utf-8")
        self.assertNotIn("must-not-leak", payload)
        self.assertNotIn("private-user", payload)
        self.assertNotIn("also-secret", payload)


if __name__ == "__main__":
    unittest.main()
