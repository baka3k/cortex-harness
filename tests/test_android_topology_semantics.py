import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
FIXTURE = ROOT / "tests" / "fixtures" / "project-topology"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.project_topology.models import DiagnosticCode  # noqa: E402
from tools.project_topology.pipeline import analyze_project  # noqa: E402


def test_manifest_semantics_include_permissions_components_and_deep_links():
    result = analyze_project(FIXTURE, "android-fixture")
    manifest = next(
        item for item in result.descriptors if item.path.endswith("AndroidManifest.xml")
    )

    assert manifest.module_path == "app"
    assert manifest.properties["permissions"][0]["name"] == (
        "android.permission.INTERNET"
    )
    activity = manifest.properties["components"][0]
    assert activity["exported"] == "true"
    assert activity["intent_filters"][0]["data"][0]["host"] == "example.test"


def test_resource_semantics_keep_qualifiers_hierarchy_and_redact_secrets():
    result = analyze_project(FIXTURE, "android-fixture")
    strings = next(
        item for item in result.descriptors if item.path.endswith("values/strings.xml")
    )
    layout = next(
        item for item in result.descriptors if item.path.endswith("layout/activity_main.xml")
    )
    navigation = next(
        item for item in result.descriptors if item.path.endswith("navigation/main_nav.xml")
    )

    assert strings.redacted
    api_key = next(
        item for item in strings.properties["values"] if item["name"] == "api_key"
    )
    assert api_key["value"] == "[redacted]"
    assert "must-not-leak" not in strings.summary
    assert layout.properties["qualifier"] == "layout"
    assert any(item["id"] == "@+id/title" for item in layout.properties["views"])
    assert "@string/app_name" in layout.properties["references"]
    assert any(
        item["uri"] == "https://example.test/details/{id}"
        for item in navigation.properties["views"]
    )
    assert any(
        item.code == DiagnosticCode.SECRET_REDACTED
        for item in strings.diagnostics
    )
