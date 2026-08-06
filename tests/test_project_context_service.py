import asyncio
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
MCP_DIR = CODE_TINY / "mcp"
for path in (str(CODE_TINY), str(MCP_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from services.project_context_service import ProjectContextService  # noqa: E402


def run_async_test(async_test):
    """Run an async test without requiring an external pytest plugin."""

    def sync_test():
        asyncio.run(async_test())

    sync_test.__name__ = async_test.__name__
    return sync_test


class RecordingRunner:
    def __init__(self):
        self.calls = []

    async def __call__(self, query, params):
        self.calls.append((query, dict(params)))
        if ":count */" in query:
            return [{"total": 1}]
        if "modules:page" in query:
            return [
                {
                    "module_id": "project-module:shop:app",
                    "name": "app",
                    "module_path": "app",
                    "kind": "android_application",
                    "languages": ["kotlin", "java"],
                    "frameworks": ["spring"],
                    "build_systems": ["gradle"],
                    "source_roots": ["app/src/main"],
                    "descriptor_ids": ["descriptor:1"],
                    "confidence": "high",
                    "dependencies": [
                        {
                            "id": "dependency:1",
                            "target_id": "project-module:shop:lib",
                            "target_name": "lib",
                            "target_labels": ["ProjectModule", "GradleModule"],
                            "scope": "compile",
                        }
                    ],
                }
            ]
        if "public_apis:page" in query:
            return [
                {
                    "symbol_id": "function:public",
                    "name": "publicApi",
                    "kind": "Function",
                    "signature": "publicApi(): Unit",
                    "visibility": "public",
                    "visibility_source": "explicit",
                    "evidence": "public_modifier",
                    "language": "kotlin",
                    "file_path": "app/src/Public.kt",
                    "start_line": 3,
                    "module_id": "project-module:shop:app",
                }
            ]
        if "endpoints:page" in query:
            row = {
                "endpoint_id": "grpc:hello",
                "original_labels": ["GrpcEndpoint"],
                "protocol": "grpc",
                "method": "",
                "path": "",
                "name": "SayHello",
                "service": "Greeter",
                "framework": "protobuf",
                "module_id": "project-module:shop:app",
            }
            return [row, dict(row)]
        if "special_files:page" in query:
            return [
                {
                    "descriptor_id": "descriptor:secret",
                    "path": "app/.env",
                    "role": "secret-bearing",
                    "parser": "runtime_secret_keys",
                    "parse_depth": "identity",
                    "status": "present",
                    "secret_bearing": True,
                    "redacted": True,
                    "safe_summary": "PASSWORD=must-not-leak",
                    "module_id": "project-module:shop:app",
                }
            ]
        if "frameworks:page" in query:
            return [
                {
                    "instance_id": "framework:spring",
                    "framework": "spring",
                    "confidence": "high",
                    "dimensions": '{"configuration":"supported","security":"partial"}',
                    "facts": '{"profiles":["prod"]}',
                    "evidence": ["app/pom.xml"],
                    "module_id": "project-module:shop:app",
                }
            ]
        return []


@run_async_test
async def test_context_methods_normalize_bounded_provider_neutral_results():
    runner = RecordingRunner()
    service = ProjectContextService(runner)

    modules = await service.get_project_modules(project_id="Shop", limit=999)
    apis = await service.get_public_apis(project_id="Shop")
    endpoints = await service.get_endpoints(project_id="Shop")
    special = await service.get_project_special_files(project_id="Shop")
    frameworks = await service.get_framework_context(
        project_id="Shop", dimensions=["security", "deployment"]
    )

    assert modules["limit"] == 200
    assert modules["modules"][0]["dependencies"][0]["internal"] is True
    assert apis["public_apis"][0]["visibility"] == "public"
    assert len(endpoints["endpoints"]) == 1
    assert special["special_files"][0]["safe_summary"] == "[redacted]"
    assert frameworks["frameworks"][0]["dimensions"] == {
        "deployment": "unavailable",
        "security": "partial",
    }
    assert all(
        call[1]["project_id_normalized"] == "shop" for call in runner.calls
    )


@run_async_test
async def test_architecture_summary_has_fixed_query_count_and_requires_scope():
    runner = RecordingRunner()
    service = ProjectContextService(runner)

    with pytest.raises(ValueError, match="module_id"):
        await service.get_module_architecture_summary(project_id="shop")

    result = await service.get_module_architecture_summary(
        project_id="shop", module_id="project-module:shop:app", item_limit=500
    )
    assert len(runner.calls) == 10
    assert result["item_limit"] == 50
    assert result["ingestion_provenance"]["filesystem_rescan"] is False


@run_async_test
async def test_context_rejects_missing_scope_and_negative_pagination():
    service = ProjectContextService(RecordingRunner())
    with pytest.raises(ValueError, match="project_id"):
        await service.get_project_modules(project_id="")
    with pytest.raises(ValueError, match="negative"):
        await service.get_project_modules(project_id="shop", offset=-1)
