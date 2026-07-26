"""Provider-neutral writer for topology-owned graph facts."""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional, Sequence

from tools.common.project_scope import project_id_lookup_key
from tools.graph.core.base import GraphDriver
from tools.project_topology.models import TopologyAnalysisResult, stable_fact_id


_MODULE_QUERY = """
UNWIND $rows AS row
MERGE (project:Project {project_id: row.project_id})
SET project.project_id_normalized = row.project_id_normalized
MERGE (module:ProjectModule {id: row.id})
SET module += row, module.topology_owned = true
MERGE (project)-[rel:CONTAINS {topology_owner: 'project_topology'}]->(module)
SET rel.project_id = row.project_id
RETURN count(module) AS count
"""

_GRADLE_COMPATIBILITY_QUERY = """
UNWIND $rows AS row
MATCH (module:ProjectModule {id: row.id})
SET module:GradleModule
RETURN count(module) AS count
"""

_GRADLE_LEGACY_LINK_QUERY = """
UNWIND $rows AS row
MATCH (module:ProjectModule {id: row.id})
MATCH (legacy:GradleModule)
WHERE legacy.id <> module.id
  AND legacy.project_id = row.project_id
  AND legacy.module_path = CASE
    WHEN row.module_path = '.' THEN ':'
    ELSE ':' + replace(row.module_path, '/', ':')
  END
MERGE (legacy)-[rel:SAME_MODULE {
  id: row.id + ':legacy-gradle'
}]->(module)
SET rel.project_id = row.project_id,
    rel.topology_owner = 'project_topology'
RETURN count(legacy) AS count
"""

_DESCRIPTOR_QUERY = """
UNWIND $rows AS row
MATCH (module:ProjectModule {id: row.module_id})
MERGE (descriptor:BuildDescriptor {id: row.id})
SET descriptor += row, descriptor.topology_owned = true
MERGE (module)-[rel:HAS_DESCRIPTOR {id: row.edge_id}]->(descriptor)
SET rel.project_id = row.project_id,
    rel.topology_owner = 'project_topology',
    rel.file_path = row.file_path
RETURN count(descriptor) AS count
"""

_DEPENDENCY_NODE_QUERY = """
UNWIND $rows AS row
MERGE (dependency:Dependency {id: row.id})
SET dependency += row, dependency.topology_owned = true
RETURN count(dependency) AS count
"""

_DEPENDENCY_EDGE_QUERY = """
UNWIND $rows AS row
MATCH (source:ProjectModule {id: row.source_id})
MATCH (target {id: row.target_id})
WHERE target:ProjectModule OR target:Dependency
MERGE (source)-[rel:DEPENDS_ON {id: row.id}]->(target)
SET rel += row, rel.topology_owner = 'project_topology'
RETURN count(rel) AS count
"""

_ENDPOINT_QUERY = """
UNWIND $rows AS row
MATCH (module:ProjectModule {id: row.module_id})
MERGE (endpoint:GrpcEndpoint {id: row.id})
SET endpoint += row, endpoint.topology_owned = true
MERGE (module)-[rel:EXPOSES_ENDPOINT {id: row.edge_id}]->(endpoint)
SET rel.project_id = row.project_id, rel.topology_owner = 'project_topology'
RETURN count(endpoint) AS count
"""

_GRPC_SERVICE_QUERY = """
UNWIND $rows AS row
MATCH (module:ProjectModule {id: row.module_id})
MATCH (endpoint:GrpcEndpoint {id: row.endpoint_id})
MERGE (service:GrpcService {id: row.id})
SET service += row, service.topology_owned = true
MERGE (module)-[declares:DECLARES_SERVICE {id: row.module_edge_id}]->(service)
SET declares.project_id = row.project_id,
    declares.topology_owner = 'project_topology'
MERGE (service)-[rpc:HAS_RPC {id: row.rpc_edge_id}]->(endpoint)
SET rpc.project_id = row.project_id,
    rpc.topology_owner = 'project_topology'
RETURN count(service) AS count
"""

_FRAMEWORK_QUERY = """
UNWIND $rows AS row
MATCH (module:ProjectModule {id: row.module_id})
MERGE (framework:FrameworkInstance {id: row.id})
SET framework += row, framework.topology_owned = true
MERGE (module)-[rel:USES_FRAMEWORK {id: row.edge_id}]->(framework)
SET rel.project_id = row.project_id, rel.topology_owner = 'project_topology'
RETURN count(framework) AS count
"""

_PUBLIC_API_LINK_QUERY = """
UNWIND $rows AS row
MATCH (module:ProjectModule {id: row.module_id})
MATCH (symbol)
WHERE (symbol:Class OR symbol:Function OR symbol:Type OR symbol:Interface)
  AND symbol.project_id_normalized = row.project_id_normalized
  AND coalesce(symbol.is_public_api, false) = true
  AND (
    row.module_path = '.'
    OR symbol.file_path = row.module_path
    OR substring(symbol.file_path, 0, size(row.module_path) + 1) = row.module_path + '/'
  )
OPTIONAL MATCH (more_specific:ProjectModule)
WHERE more_specific.project_id_normalized = row.project_id_normalized
  AND more_specific.id <> module.id
  AND more_specific.module_path <> '.'
  AND size(more_specific.module_path) > size(row.module_path)
  AND substring(symbol.file_path, 0, size(more_specific.module_path) + 1)
      = more_specific.module_path + '/'
WITH row, module, symbol, count(more_specific) AS more_specific_count
WHERE more_specific_count = 0
SET symbol.module_id = row.module_id
MERGE (module)-[rel:EXPOSES_API {
  id: row.module_id + ':EXPOSES_API:' + symbol.id
}]->(symbol)
SET rel.project_id = row.project_id,
    rel.topology_owner = 'project_topology'
RETURN count(symbol) AS count
"""

_EXISTING_ENDPOINT_LINK_QUERY = """
UNWIND $rows AS row
MATCH (module:ProjectModule {id: row.module_id})
MATCH (endpoint)
WHERE (
    endpoint:ApiEndpoint
    OR endpoint:HttpEndpoint
    OR endpoint:Route
    OR endpoint:ControllerAction
    OR endpoint:ServletEndpoint
  )
  AND coalesce(endpoint.topology_owned, false) = false
  AND endpoint.project_id_normalized = row.project_id_normalized
  AND (
    row.module_path = '.'
    OR endpoint.file_path = row.module_path
    OR substring(endpoint.file_path, 0, size(row.module_path) + 1) = row.module_path + '/'
  )
OPTIONAL MATCH (more_specific:ProjectModule)
WHERE more_specific.project_id_normalized = row.project_id_normalized
  AND more_specific.id <> module.id
  AND more_specific.module_path <> '.'
  AND size(more_specific.module_path) > size(row.module_path)
  AND substring(endpoint.file_path, 0, size(more_specific.module_path) + 1)
      = more_specific.module_path + '/'
WITH row, module, endpoint, count(more_specific) AS more_specific_count
WHERE more_specific_count = 0
SET endpoint.module_id = row.module_id
MERGE (module)-[rel:EXPOSES_ENDPOINT {
  id: row.module_id + ':EXPOSES_ENDPOINT:' + endpoint.id
}]->(endpoint)
SET rel.project_id = row.project_id,
    rel.topology_owner = 'project_topology'
RETURN count(endpoint) AS count
"""

_ANDROID_FACT_LINK_QUERY = """
UNWIND $rows AS row
MATCH (module:ProjectModule {id: row.module_id})
MATCH (fact)
WHERE (
    fact:AndroidManifest
    OR fact:AndroidComponent
    OR fact:AndroidResource
  )
  AND (
    fact.project_id_normalized = row.project_id_normalized
    OR toLower(coalesce(fact.project_id, '')) = row.project_id_normalized
  )
  AND (
    row.module_path = '.'
    OR fact.file_path = row.module_path
    OR substring(fact.file_path, 0, size(row.module_path) + 1) = row.module_path + '/'
  )
OPTIONAL MATCH (more_specific:ProjectModule)
WHERE more_specific.project_id_normalized = row.project_id_normalized
  AND more_specific.id <> module.id
  AND more_specific.module_path <> '.'
  AND size(more_specific.module_path) > size(row.module_path)
  AND substring(fact.file_path, 0, size(more_specific.module_path) + 1)
      = more_specific.module_path + '/'
WITH row, module, fact, count(more_specific) AS more_specific_count
WHERE more_specific_count = 0
SET fact.module_id = row.module_id
MERGE (module)-[rel:CONTAINS {
  id: row.module_id + ':CONTAINS:' + fact.id
}]->(fact)
SET rel.project_id = row.project_id,
    rel.topology_owner = 'project_topology'
RETURN count(fact) AS count
"""

_CLEANUP_PATHS_QUERY = """
MATCH (node)
WHERE node.project_id_normalized = $project_id_normalized
  AND node.topology_owned = true
  AND (
    node.file_path IN $paths
    OR node.path IN $paths
    OR any(path IN $paths WHERE
      node.module_path = path OR
      substring(node.module_path, 0, size(path) + 1) = path + '/'
    )
  )
WITH collect(node) AS nodes
FOREACH (node IN nodes | DETACH DELETE node)
RETURN size(nodes) AS count
"""

_CLEANUP_PROJECT_QUERY = """
MATCH (node)
WHERE node.project_id_normalized = $project_id_normalized
  AND node.topology_owned = true
WITH collect(node) AS nodes
FOREACH (node IN nodes | DETACH DELETE node)
RETURN size(nodes) AS count
"""


def _count(records: Sequence[Dict[str, Any]], fallback: int) -> int:
    if records and records[0].get("count") is not None:
        return int(records[0]["count"])
    return fallback


def _graph_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    if isinstance(value, (tuple, list)):
        if all(
            item is None or isinstance(item, (str, int, float, bool))
            for item in value
        ):
            return list(value)
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return value


def _graph_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {str(key): _graph_value(value) for key, value in row.items()}


class ProjectTopologyWriter:
    """Write additive topology facts and delete only topology-owned state."""

    def __init__(
        self,
        driver: GraphDriver,
        database: Optional[str] = None,
        batch_size: int = 500,
    ) -> None:
        self.driver = driver
        self.database = database
        self.batch_size = max(1, int(batch_size))

    async def _write_batches(
        self, query: str, rows: Sequence[Dict[str, Any]]
    ) -> int:
        total = 0
        for offset in range(0, len(rows), self.batch_size):
            batch = [_graph_row(item) for item in rows[offset : offset + self.batch_size]]
            records, _, _ = await self.driver.execute_query(
                query, {"rows": batch}, self.database
            )
            total += _count(records, len(batch))
        return total

    async def write(self, result: TopologyAnalysisResult) -> Dict[str, int]:
        project_key = project_id_lookup_key(result.project_id)
        if project_key is None:
            raise ValueError("project_id is required")
        module_rows = []
        for module in result.modules:
            row = module.to_dict()
            row["project_id_normalized"] = project_key
            module_rows.append(row)
        module_ids = {item.module_path: item.id for item in result.modules}
        module_frameworks = {
            item.module_path: list(item.frameworks) for item in result.modules
        }
        descriptor_rows = []
        for descriptor in result.descriptors:
            row = descriptor.to_dict()
            row.update(
                {
                    "module_id": module_ids[descriptor.module_path],
                    "file_path": descriptor.path,
                    "name": descriptor.path.rsplit("/", 1)[-1],
                    "project_id_normalized": project_key,
                    "frameworks": module_frameworks.get(
                        descriptor.module_path, []
                    ),
                    "edge_id": stable_fact_id(
                        result.project_id,
                        "topology-edge",
                        module_ids[descriptor.module_path],
                        "HAS_DESCRIPTOR",
                        descriptor.id,
                    ),
                }
            )
            descriptor_rows.append(row)

        external_rows = []
        dependency_rows = []
        for dependency in result.dependencies:
            source_id = module_ids.get(dependency.source_module_path)
            if source_id is None:
                continue
            if dependency.internal and dependency.target_module_path in module_ids:
                target_id = module_ids[dependency.target_module_path]
            else:
                target_id = stable_fact_id(
                    result.project_id, "external-dependency", dependency.target
                )
                external_rows.append(
                    {
                        "id": target_id,
                        "project_id": result.project_id,
                        "project_id_normalized": project_key,
                        "name": dependency.target,
                        "coordinate": dependency.target,
                        "kind": "external",
                    }
                )
            row = dependency.to_dict()
            row.update({"source_id": source_id, "target_id": target_id})
            dependency_rows.append(row)

        endpoint_rows = []
        grpc_service_rows = []
        for endpoint in result.endpoints:
            row = endpoint.to_dict()
            row.update(
                {
                    "project_id_normalized": project_key,
                    "edge_id": stable_fact_id(
                        result.project_id,
                        "topology-edge",
                        endpoint.module_id,
                        "EXPOSES_ENDPOINT",
                        endpoint.id,
                    ),
                }
            )
            endpoint_rows.append(row)
            if endpoint.service:
                service_id = stable_fact_id(
                    result.project_id,
                    "grpc-service",
                    endpoint.module_id,
                    endpoint.service,
                )
                grpc_service_rows.append(
                    {
                        "id": service_id,
                        "project_id": result.project_id,
                        "project_id_normalized": project_key,
                        "module_id": endpoint.module_id,
                        "name": endpoint.service.rsplit(".", 1)[-1],
                        "qualified_name": endpoint.service,
                        "framework": endpoint.framework,
                        "file_path": endpoint.file_path,
                        "endpoint_id": endpoint.id,
                        "module_edge_id": stable_fact_id(
                            result.project_id,
                            "topology-edge",
                            endpoint.module_id,
                            "DECLARES_SERVICE",
                            service_id,
                        ),
                        "rpc_edge_id": stable_fact_id(
                            result.project_id,
                            "topology-edge",
                            service_id,
                            "HAS_RPC",
                            endpoint.id,
                        ),
                    }
                )
        framework_rows = []
        for framework in result.frameworks:
            row = framework.to_dict()
            row.update(
                {
                    "project_id_normalized": project_key,
                    "edge_id": stable_fact_id(
                        result.project_id,
                        "topology-edge",
                        framework.module_id,
                        "USES_FRAMEWORK",
                        framework.id,
                    ),
                }
            )
            framework_rows.append(row)
        public_api_link_rows = [
            {
                "module_id": module.id,
                "module_path": module.module_path,
                "project_id": result.project_id,
                "project_id_normalized": project_key,
            }
            for module in result.modules
            if module.module_path != "." or len(result.modules) == 1
        ]
        gradle_compatibility_rows = [
            row for row in module_rows if "gradle" in row.get("build_systems", ())
        ]

        return {
            "modules": await self._write_batches(_MODULE_QUERY, module_rows),
            "gradle_compatibility_labels": await self._write_batches(
                _GRADLE_COMPATIBILITY_QUERY, gradle_compatibility_rows
            ),
            "gradle_legacy_links": await self._write_batches(
                _GRADLE_LEGACY_LINK_QUERY, gradle_compatibility_rows
            ),
            "descriptors": await self._write_batches(
                _DESCRIPTOR_QUERY, descriptor_rows
            ),
            "external_dependencies": await self._write_batches(
                _DEPENDENCY_NODE_QUERY,
                sorted(
                    {item["id"]: item for item in external_rows}.values(),
                    key=lambda item: item["id"],
                ),
            ),
            "dependencies": await self._write_batches(
                _DEPENDENCY_EDGE_QUERY, dependency_rows
            ),
            "endpoints": await self._write_batches(_ENDPOINT_QUERY, endpoint_rows),
            "grpc_services": await self._write_batches(
                _GRPC_SERVICE_QUERY, grpc_service_rows
            ),
            "frameworks": await self._write_batches(
                _FRAMEWORK_QUERY, framework_rows
            ),
            "public_api_links": await self._write_batches(
                _PUBLIC_API_LINK_QUERY, public_api_link_rows
            ),
            "existing_endpoint_links": await self._write_batches(
                _EXISTING_ENDPOINT_LINK_QUERY, public_api_link_rows
            ),
            "android_fact_links": await self._write_batches(
                _ANDROID_FACT_LINK_QUERY, public_api_link_rows
            ),
        }

    async def cleanup_paths(
        self, project_id: str, paths: Sequence[str]
    ) -> int:
        normalized = sorted(
            {str(path).replace("\\", "/").strip("/") for path in paths if str(path)}
        )
        if not normalized:
            return 0
        records, _, _ = await self.driver.execute_query(
            _CLEANUP_PATHS_QUERY,
            {
                "project_id_normalized": project_id_lookup_key(project_id),
                "paths": normalized,
            },
            self.database,
        )
        return _count(records, 0)

    async def cleanup_project(self, project_id: str) -> int:
        records, _, _ = await self.driver.execute_query(
            _CLEANUP_PROJECT_QUERY,
            {"project_id_normalized": project_id_lookup_key(project_id)},
            self.database,
        )
        return _count(records, 0)


__all__ = ["ProjectTopologyWriter"]
