import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
FIXTURE = ROOT / "tests" / "fixtures" / "project-topology"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.graph.writer.project_topology_writer import ProjectTopologyWriter  # noqa: E402
from tools.project_topology.pipeline import analyze_project  # noqa: E402


def run_async_test(async_test):
    """Run an async test without requiring an external pytest plugin."""

    def sync_test():
        asyncio.run(async_test())

    sync_test.__name__ = async_test.__name__
    return sync_test


class RecordingDriver:
    def __init__(self):
        self.calls = []

    async def execute_query(self, query, parameters=None, database=None):
        rows = list((parameters or {}).get("rows", ()))
        self.calls.append((query, dict(parameters or {}), database))
        return ([{"count": len(rows)}], [], None)


@run_async_test
async def test_writer_is_additive_idempotent_and_cleanup_is_topology_owned():
    result = analyze_project(FIXTURE, "Fixture")
    driver = RecordingDriver()
    writer = ProjectTopologyWriter(driver, database="fixture")

    first = await writer.write(result)
    second = await writer.write(result)
    await writer.cleanup_paths("fixture", ["app/build.gradle.kts"])

    assert first == second
    assert first["modules"] == len(result.modules)
    combined = "\n".join(query for query, _, _ in driver.calls)
    assert "MERGE (module:ProjectModule" in combined
    assert "MERGE (descriptor:BuildDescriptor" in combined
    assert "MERGE (service:GrpcService" in combined
    assert "SAME_MODULE" in combined
    assert "endpoint:ApiEndpoint" in combined
    assert "EXPOSES_ENDPOINT" in combined
    assert "fact:AndroidManifest" in combined
    assert "topology_owned = true" in combined
    assert (
        "substring(symbol.file_path, 0, size(row.module_path) + 1)"
        in combined
    )
    assert (
        "substring(endpoint.file_path, 0, size(row.module_path) + 1)"
        in combined
    )
    assert (
        "substring(fact.file_path, 0, size(row.module_path) + 1)"
        in combined
    )
    assert "substring(node.module_path, 0, size(path) + 1)" in combined
    assert "STARTS WITH" not in combined
    cleanup_query = driver.calls[-1][0]
    assert "node.topology_owned = true" in cleanup_query
    assert "AndroidComponent" not in cleanup_query
