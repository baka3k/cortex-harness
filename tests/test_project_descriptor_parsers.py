import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
FIXTURE = ROOT / "tests" / "fixtures" / "project-topology"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.project_topology.models import (  # noqa: E402
    DiagnosticCode,
    EndpointProtocol,
    ModuleKind,
)
from tools.project_topology.pipeline import analyze_project  # noqa: E402


def test_mixed_descriptor_fixture_extracts_modules_dependencies_and_grpc():
    result = analyze_project(FIXTURE, "fixture")

    by_path = {item.module_path: item for item in result.modules}
    assert by_path["app"].kind == ModuleKind.ANDROID_APPLICATION
    assert by_path["library"].kind == ModuleKind.ANDROID_LIBRARY
    assert by_path["feature"].kind == ModuleKind.ANDROID_DYNAMIC_FEATURE
    assert by_path["jvm/child"].name == "child"
    assert any(
        item.internal
        and item.source_module_path == "app"
        and item.target_module_path == "library"
        for item in result.dependencies
    )
    assert any(
        item.protocol == EndpointProtocol.GRPC
        and item.service == "example.users.UserService"
        and item.path == "/v1/users/{id}"
        for item in result.endpoints
    )
    assert any(item.client_streaming and item.server_streaming for item in result.endpoints)


def test_malformed_and_dynamic_descriptors_are_bounded_diagnostics():
    result = analyze_project(FIXTURE, "fixture")
    codes = {item.code for item in result.diagnostics}
    assert DiagnosticCode.MALFORMED_DESCRIPTOR in codes
    assert DiagnosticCode.DYNAMIC_EXPRESSION in codes


def test_secret_bearing_framework_config_is_redacted():
    result = analyze_project(FIXTURE, "fixture")
    mybatis = next(
        item for item in result.descriptors if item.path.endswith("mybatis-config.xml")
    )
    assert mybatis.redacted
    assert "UserMapper" not in mybatis.summary
