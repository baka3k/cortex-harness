"""Bounded, provider-neutral aggregate project-context queries."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from tools.common.project_scope import project_id_lookup_key


QueryRunner = Callable[[str, Dict[str, Any]], Awaitable[List[Dict[str, Any]]]]
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
DEFAULT_SAMPLE_LIMIT = 10
MAX_SAMPLE_LIMIT = 50


def _positive_int(value: Any, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if parsed < 0:
        raise ValueError("pagination values cannot be negative")
    return min(parsed, maximum)


def _list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text[:1] in {"[", "{"}:
            try:
                decoded = json.loads(text)
                return decoded if isinstance(decoded, list) else [decoded]
            except ValueError:
                pass
        return [text]
    return [value]


def _mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            decoded = json.loads(value)
            return dict(decoded) if isinstance(decoded, Mapping) else {}
        except ValueError:
            return {}
    return {}


def _total(rows: Sequence[Mapping[str, Any]], fallback: int = 0) -> int:
    if rows and rows[0].get("total") is not None:
        return int(rows[0]["total"])
    return fallback


class ProjectContextService:
    """Execute fixed-count graph queries and normalize provider result shapes."""

    def __init__(self, runner: QueryRunner) -> None:
        self._runner = runner

    @staticmethod
    def _scope(project_id: str) -> Tuple[str, str]:
        raw = str(project_id or "").strip()
        normalized = project_id_lookup_key(raw)
        if normalized is None:
            raise ValueError("project_id is required")
        return raw, normalized

    async def _count(
        self,
        *,
        marker: str,
        label: str,
        project_id_normalized: str,
        module_id: str = "",
        extra_where: str = "",
        params: Optional[Dict[str, Any]] = None,
    ) -> int:
        query = f"""
        /* project_context:{marker}:count */
        MATCH (node:{label})
        WHERE node.project_id_normalized = $project_id_normalized
          AND ($module_id = '' OR node.module_id = $module_id OR node.id = $module_id)
          {extra_where}
        RETURN count(node) AS total
        """
        rows = await self._runner(
            query,
            {
                "project_id_normalized": project_id_normalized,
                "module_id": module_id,
                **(params or {}),
            },
        )
        return _total(rows)

    async def get_project_modules(
        self,
        *,
        project_id: str,
        module_id: str = "",
        module_path: str = "",
        include_dependencies: bool = True,
        offset: Any = 0,
        limit: Any = DEFAULT_LIMIT,
    ) -> Dict[str, Any]:
        raw, normalized = self._scope(project_id)
        page_offset = _positive_int(offset, 0, 1_000_000)
        page_limit = _positive_int(limit, DEFAULT_LIMIT, MAX_LIMIT)
        params = {
            "project_id_normalized": normalized,
            "module_id": str(module_id or ""),
            "module_path": str(module_path or "").replace("\\", "/"),
            "offset": page_offset,
            "limit": page_limit,
        }
        count_rows = await self._runner(
            """
            /* project_context:modules:count */
            MATCH (m:ProjectModule)
            WHERE m.project_id_normalized = $project_id_normalized
              AND ($module_id = '' OR m.id = $module_id)
              AND ($module_path = '' OR m.module_path = $module_path)
            RETURN count(m) AS total
            """,
            params,
        )
        rows = await self._runner(
            """
            /* project_context:modules:page */
            MATCH (m:ProjectModule)
            WHERE m.project_id_normalized = $project_id_normalized
              AND ($module_id = '' OR m.id = $module_id)
              AND ($module_path = '' OR m.module_path = $module_path)
            OPTIONAL MATCH (m)-[:HAS_DESCRIPTOR]->(d:BuildDescriptor)
            OPTIONAL MATCH (m)-[dep:DEPENDS_ON]->(target)
            RETURN m.id AS module_id,
                   m.name AS name,
                   m.module_path AS module_path,
                   m.kind AS kind,
                   m.languages AS languages,
                   m.frameworks AS frameworks,
                   m.build_systems AS build_systems,
                   m.source_roots AS source_roots,
                   m.confidence AS confidence,
                   m.diagnostics AS diagnostics,
                   collect(DISTINCT d.id) AS descriptor_ids,
                   collect(DISTINCT {
                     id: d.id,
                     path: d.file_path,
                     descriptor_type: d.descriptor_type,
                     role: d.role,
                     parse_depth: d.parse_depth
                   }) AS descriptors,
                   collect(DISTINCT {
                     id: dep.id,
                     target_id: target.id,
                     target_name: target.name,
                     target_kind: labels(target)[0],
                     scope: dep.scope,
                     target_labels: labels(target)
                   }) AS dependencies
            ORDER BY m.module_path, m.id
            SKIP $offset LIMIT $limit
            """,
            params,
        )
        modules = []
        for row in rows:
            dependencies = []
            for raw_dependency in _list(row.get("dependencies")):
                if not isinstance(raw_dependency, Mapping) or not raw_dependency.get("id"):
                    continue
                dependency = dict(raw_dependency)
                target_labels = [
                    str(item)
                    for item in _list(dependency.pop("target_labels", ()))
                    if item
                ]
                dependency["internal"] = "ProjectModule" in target_labels
                dependency["target_kind"] = (
                    "ProjectModule"
                    if dependency["internal"]
                    else "Dependency"
                    if "Dependency" in target_labels
                    else dependency.get("target_kind") or "unknown"
                )
                dependencies.append(dependency)
            modules.append(
                {
                    "module_id": row.get("module_id") or row.get("id"),
                    "name": row.get("name") or "",
                    "module_path": row.get("module_path") or ".",
                    "kind": row.get("kind") or "unknown",
                    "languages": sorted(str(item) for item in _list(row.get("languages")) if item),
                    "frameworks": sorted(str(item) for item in _list(row.get("frameworks")) if item),
                    "build_systems": sorted(str(item) for item in _list(row.get("build_systems")) if item),
                    "source_roots": sorted(str(item) for item in _list(row.get("source_roots")) if item),
                    "descriptor_ids": sorted(str(item) for item in _list(row.get("descriptor_ids")) if item),
                    "descriptors": sorted(
                        (
                            dict(item)
                            for item in _list(row.get("descriptors"))
                            if isinstance(item, Mapping) and item.get("id")
                        ),
                        key=lambda item: (
                            str(item.get("path") or ""),
                            str(item.get("id") or ""),
                        ),
                    ),
                    "dependencies": dependencies if include_dependencies else [],
                    "internal_dependencies": (
                        [
                            item
                            for item in dependencies
                            if item.get("internal")
                        ]
                        if include_dependencies
                        else []
                    ),
                    "external_dependencies": (
                        [
                            item
                            for item in dependencies
                            if not item.get("internal")
                        ]
                        if include_dependencies
                        else []
                    ),
                    "confidence": row.get("confidence") or "unknown",
                    "diagnostics": _list(row.get("diagnostics")),
                }
            )
        return {
            "ok": True,
            "project_id": raw,
            "modules": modules,
            "total": _total(count_rows, len(modules)),
            "offset": page_offset,
            "limit": page_limit,
            "has_more": page_offset + len(modules) < _total(count_rows, len(modules)),
        }

    async def get_public_apis(
        self,
        *,
        project_id: str,
        module_id: str = "",
        symbol_kinds: Optional[Iterable[str]] = None,
        language: str = "",
        include_inferred: bool = False,
        offset: Any = 0,
        limit: Any = DEFAULT_LIMIT,
    ) -> Dict[str, Any]:
        raw, normalized = self._scope(project_id)
        page_offset = _positive_int(offset, 0, 1_000_000)
        page_limit = _positive_int(limit, DEFAULT_LIMIT, MAX_LIMIT)
        kinds = sorted({str(item) for item in (symbol_kinds or ()) if str(item)})
        params = {
            "project_id_normalized": normalized,
            "module_id": str(module_id or ""),
            "symbol_kinds": kinds,
            "language": str(language or "").lower(),
            "include_inferred": bool(include_inferred),
            "offset": page_offset,
            "limit": page_limit,
        }
        predicate = """
          AND (coalesce(symbol.is_public_api, false) = true
               OR ($include_inferred = true AND symbol.visibility = 'inferred'))
          AND (size($symbol_kinds) = 0 OR labels(symbol)[0] IN $symbol_kinds OR symbol.kind IN $symbol_kinds)
          AND ($language = '' OR toLower(symbol.language) = $language)
        """
        count_rows = await self._runner(
            f"""
            /* project_context:public_apis:count */
            MATCH (m:ProjectModule)-[:EXPOSES_API]->(symbol)
            WHERE m.project_id_normalized = $project_id_normalized
              AND ($module_id = '' OR m.id = $module_id)
              {predicate}
            RETURN count(DISTINCT symbol) AS total
            """,
            params,
        )
        rows = await self._runner(
            f"""
            /* project_context:public_apis:page */
            MATCH (m:ProjectModule)-[:EXPOSES_API]->(symbol)
            WHERE m.project_id_normalized = $project_id_normalized
              AND ($module_id = '' OR m.id = $module_id)
              {predicate}
            RETURN DISTINCT symbol.id AS symbol_id,
                   symbol.name AS name,
                   labels(symbol)[0] AS kind,
                   symbol.kind AS declaration_kind,
                   symbol.signature AS signature,
                   symbol.visibility AS visibility,
                   symbol.visibility_source AS visibility_source,
                   symbol.export_evidence AS evidence,
                   symbol.language AS language,
                   symbol.file_path AS file_path,
                   symbol.start_line AS start_line,
                   symbol.module_id AS module_id,
                   symbol.public_api_confidence AS confidence,
                   coalesce(symbol.visibility = 'inferred', false) AS inferred
            ORDER BY symbol.file_path, symbol.start_line, symbol.id
            SKIP $offset LIMIT $limit
            """,
            params,
        )
        items = [
            {
                "symbol_id": row.get("symbol_id") or row.get("id"),
                "name": row.get("name") or "",
                "kind": row.get("kind") or row.get("declaration_kind") or "Symbol",
                "declaration_kind": row.get("declaration_kind") or "",
                "signature": row.get("signature") or "",
                "visibility": row.get("visibility") or "unknown",
                "visibility_source": row.get("visibility_source") or "",
                "evidence": row.get("evidence") or "",
                "language": row.get("language") or "",
                "file_path": row.get("file_path") or "",
                "start_line": row.get("start_line"),
                "module_id": row.get("module_id") or module_id,
                "inferred": bool(row.get("inferred")),
                "confidence": (
                    row.get("confidence")
                    or ("low" if row.get("inferred") else "high")
                ),
            }
            for row in rows
        ]
        total = _total(count_rows, len(items))
        return {
            "ok": True,
            "project_id": raw,
            "public_apis": items,
            "total": total,
            "offset": page_offset,
            "limit": page_limit,
            "has_more": page_offset + len(items) < total,
            "include_inferred": bool(include_inferred),
        }

    async def get_endpoints(
        self,
        *,
        project_id: str,
        module_id: str = "",
        protocol: str = "",
        framework: str = "",
        http_method: str = "",
        query: str = "",
        offset: Any = 0,
        limit: Any = DEFAULT_LIMIT,
    ) -> Dict[str, Any]:
        raw, normalized = self._scope(project_id)
        page_offset = _positive_int(offset, 0, 1_000_000)
        page_limit = _positive_int(limit, DEFAULT_LIMIT, MAX_LIMIT)
        params = {
            "project_id_normalized": normalized,
            "module_id": str(module_id or ""),
            "protocol": str(protocol or "").lower(),
            "framework": str(framework or "").lower(),
            "http_method": str(http_method or "").upper(),
            "query": str(query or "").lower(),
            "offset": page_offset,
            "limit": page_limit,
        }
        predicate = """
          AND ($protocol = '' OR toLower(coalesce(endpoint.protocol, 'http')) = $protocol)
          AND ($framework = '' OR toLower(coalesce(endpoint.framework, '')) = $framework)
          AND ($http_method = '' OR toUpper(coalesce(endpoint.http_method, endpoint.method, '')) = $http_method)
          AND ($query = '' OR toLower(coalesce(endpoint.path, endpoint.route, endpoint.name, '')) CONTAINS $query)
        """
        count_rows = await self._runner(
            f"""
            /* project_context:endpoints:count */
            MATCH (m:ProjectModule)-[:EXPOSES_ENDPOINT]->(endpoint)
            WHERE m.project_id_normalized = $project_id_normalized
              AND ($module_id = '' OR m.id = $module_id)
              {predicate}
            RETURN count(DISTINCT endpoint) AS total
            """,
            params,
        )
        rows = await self._runner(
            f"""
            /* project_context:endpoints:page */
            MATCH (m:ProjectModule)-[:EXPOSES_ENDPOINT]->(endpoint)
            WHERE m.project_id_normalized = $project_id_normalized
              AND ($module_id = '' OR m.id = $module_id)
              {predicate}
            OPTIONAL MATCH (endpoint)-[:HANDLED_BY|SEMANTIC_OF]->(handler)
            RETURN DISTINCT endpoint.id AS endpoint_id,
                   labels(endpoint) AS original_labels,
                   coalesce(endpoint.protocol, 'http') AS protocol,
                   coalesce(endpoint.http_method, endpoint.method, '') AS method,
                   coalesce(endpoint.path, endpoint.route, '') AS path,
                   endpoint.name AS name,
                   endpoint.service AS service,
                   endpoint.framework AS framework,
                   coalesce(handler.id, endpoint.handler_id) AS handler_id,
                   endpoint.security AS security,
                   endpoint.file_path AS file_path,
                   endpoint.start_line AS start_line,
                   m.id AS module_id,
                   endpoint.request_type AS request_type,
                   endpoint.response_type AS response_type,
                   endpoint.client_streaming AS client_streaming,
                   endpoint.server_streaming AS server_streaming
                   , endpoint.confidence AS confidence
                   , endpoint.evidence AS evidence
            ORDER BY protocol, path, name, endpoint_id
            SKIP $offset LIMIT $limit
            """,
            params,
        )
        dedup: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}
        for row in rows:
            labels = [str(item) for item in _list(row.get("original_labels")) if item]
            protocol_value = str(row.get("protocol") or "").lower()
            if not protocol_value:
                protocol_value = "grpc" if "GrpcEndpoint" in labels else "http"
            item = {
                "endpoint_id": row.get("endpoint_id") or row.get("id"),
                "protocol": protocol_value,
                "method": str(row.get("method") or "").upper(),
                "path": row.get("path") or "",
                "name": row.get("name") or "",
                "service": row.get("service") or "",
                "framework": row.get("framework") or "",
                "handler_id": row.get("handler_id") or "",
                "security": _mapping(row.get("security")),
                "file_path": row.get("file_path") or "",
                "start_line": row.get("start_line"),
                "module_id": row.get("module_id") or module_id,
                "request_type": row.get("request_type") or "",
                "response_type": row.get("response_type") or "",
                "client_streaming": bool(row.get("client_streaming")),
                "server_streaming": bool(row.get("server_streaming")),
                "original_labels": sorted(labels),
                "confidence": row.get("confidence") or "unknown",
                "evidence": _list(row.get("evidence")),
            }
            key = (
                str(item["endpoint_id"]),
                protocol_value,
                str(item["method"]),
                str(item["path"] or item["name"]),
            )
            dedup[key] = item
        items = sorted(
            dedup.values(),
            key=lambda item: (
                item["protocol"],
                item["path"],
                item["name"],
                item["endpoint_id"],
            ),
        )
        total = _total(count_rows, len(items))
        return {
            "ok": True,
            "project_id": raw,
            "endpoints": items,
            "total": total,
            "offset": page_offset,
            "limit": page_limit,
            "has_more": page_offset + len(items) < total,
        }

    async def get_project_special_files(
        self,
        *,
        project_id: str,
        module_id: str = "",
        role: str = "",
        parser: str = "",
        framework: str = "",
        parse_depth: str = "",
        status: str = "",
        include_generated: bool = True,
        offset: Any = 0,
        limit: Any = DEFAULT_LIMIT,
    ) -> Dict[str, Any]:
        raw, normalized = self._scope(project_id)
        page_offset = _positive_int(offset, 0, 1_000_000)
        page_limit = _positive_int(limit, DEFAULT_LIMIT, MAX_LIMIT)
        params = {
            "project_id_normalized": normalized,
            "module_id": str(module_id or ""),
            "role": str(role or ""),
            "parser": str(parser or ""),
            "framework": str(framework or ""),
            "parse_depth": str(parse_depth or ""),
            "status": str(status or ""),
            "include_generated": bool(include_generated),
            "offset": page_offset,
            "limit": page_limit,
        }
        predicate = """
          AND ($module_id = '' OR module.id = $module_id)
          AND ($role = '' OR descriptor.role = $role)
          AND ($parser = '' OR descriptor.parser = $parser)
          AND (
            $framework = ''
            OR descriptor.framework = $framework
            OR $framework IN coalesce(descriptor.frameworks, [])
          )
          AND ($parse_depth = '' OR descriptor.parse_depth = $parse_depth)
          AND ($status = '' OR coalesce(descriptor.status, 'present') = $status)
          AND ($include_generated = true OR coalesce(descriptor.generated, false) = false)
        """
        count_rows = await self._runner(
            f"""
            /* project_context:special_files:count */
            MATCH (module:ProjectModule)-[:HAS_DESCRIPTOR]->(descriptor:BuildDescriptor)
            WHERE module.project_id_normalized = $project_id_normalized
              {predicate}
            RETURN count(DISTINCT descriptor) AS total
            """,
            params,
        )
        rows = await self._runner(
            f"""
            /* project_context:special_files:page */
            MATCH (module:ProjectModule)-[:HAS_DESCRIPTOR]->(descriptor:BuildDescriptor)
            WHERE module.project_id_normalized = $project_id_normalized
              {predicate}
            RETURN descriptor.id AS descriptor_id,
                   descriptor.file_path AS path,
                   descriptor.role AS role,
                   descriptor.parser AS parser,
                   descriptor.parse_depth AS parse_depth,
                   coalesce(descriptor.status, 'present') AS status,
                   descriptor.framework AS framework,
                   descriptor.frameworks AS frameworks,
                   coalesce(descriptor.canonical, true) AS canonical,
                   coalesce(descriptor.generated, false) AS generated,
                   coalesce(descriptor.secret_bearing, false) AS secret_bearing,
                   coalesce(descriptor.redacted, false) AS redacted,
                   descriptor.summary AS safe_summary,
                   coalesce(descriptor.freshness, 'current') AS freshness,
                   descriptor.diagnostics AS diagnostics,
                   module.id AS module_id
            ORDER BY path, descriptor_id
            SKIP $offset LIMIT $limit
            """,
            params,
        )
        items = [
            {
                "descriptor_id": row.get("descriptor_id") or row.get("id"),
                "path": row.get("path") or "",
                "role": row.get("role") or "configuration",
                "parser": row.get("parser") or "",
                "parse_depth": row.get("parse_depth") or "unknown",
                "status": row.get("status") or "present",
                "framework": row.get("framework") or "",
                "frameworks": sorted(
                    str(item)
                    for item in _list(row.get("frameworks"))
                    if item
                ),
                "canonical": bool(row.get("canonical", True)),
                "generated": bool(row.get("generated", False)),
                "secret_bearing": bool(row.get("secret_bearing", False)),
                "redacted": bool(row.get("redacted", False)),
                "safe_summary": (
                    "[redacted]"
                    if row.get("secret_bearing")
                    else row.get("safe_summary") or ""
                ),
                "freshness": row.get("freshness") or "unknown",
                "diagnostics": _list(row.get("diagnostics")),
                "module_id": row.get("module_id") or module_id,
                "coverage_status": (
                    "unknown"
                    if (row.get("parse_depth") or "unknown") == "unknown"
                    else "supported"
                ),
                "source_provenance": "indexed_graph",
            }
            for row in rows
        ]
        total = _total(count_rows, len(items))
        return {
            "ok": True,
            "project_id": raw,
            "special_files": items,
            "total": total,
            "offset": page_offset,
            "limit": page_limit,
            "has_more": page_offset + len(items) < total,
        }

    async def get_framework_context(
        self,
        *,
        project_id: str,
        module_id: str = "",
        framework: str = "",
        dimensions: Optional[Iterable[str]] = None,
        offset: Any = 0,
        limit: Any = DEFAULT_LIMIT,
    ) -> Dict[str, Any]:
        raw, normalized = self._scope(project_id)
        page_offset = _positive_int(offset, 0, 1_000_000)
        page_limit = _positive_int(limit, DEFAULT_LIMIT, MAX_LIMIT)
        selected_dimensions = sorted(
            {str(item) for item in (dimensions or ()) if str(item)}
        )
        params = {
            "project_id_normalized": normalized,
            "module_id": str(module_id or ""),
            "framework": str(framework or "").lower(),
            "offset": page_offset,
            "limit": page_limit,
        }
        count_rows = await self._runner(
            """
            /* project_context:frameworks:count */
            MATCH (module:ProjectModule)-[:USES_FRAMEWORK]->(instance:FrameworkInstance)
            WHERE module.project_id_normalized = $project_id_normalized
              AND ($module_id = '' OR module.id = $module_id)
              AND ($framework = '' OR toLower(instance.framework) = $framework)
            RETURN count(DISTINCT instance) AS total
            """,
            params,
        )
        rows = await self._runner(
            """
            /* project_context:frameworks:page */
            MATCH (module:ProjectModule)-[:USES_FRAMEWORK]->(instance:FrameworkInstance)
            WHERE module.project_id_normalized = $project_id_normalized
              AND ($module_id = '' OR module.id = $module_id)
              AND ($framework = '' OR toLower(instance.framework) = $framework)
            RETURN instance.id AS instance_id,
                   instance.framework AS framework,
                   instance.version AS version,
                   instance.confidence AS confidence,
                   instance.dimensions AS dimensions,
                   instance.facts AS facts,
                   instance.evidence AS evidence,
                   instance.diagnostics AS diagnostics,
                   module.id AS module_id
            ORDER BY framework, module_id, instance_id
            SKIP $offset LIMIT $limit
            """,
            params,
        )
        items = []
        for row in rows:
            dimension_map = _mapping(row.get("dimensions"))
            if selected_dimensions:
                dimension_map = {
                    key: dimension_map.get(key, "unavailable")
                    for key in selected_dimensions
                }
            items.append(
                {
                    "instance_id": row.get("instance_id") or row.get("id"),
                    "framework": row.get("framework") or "",
                    "version": row.get("version") or "",
                    "confidence": row.get("confidence") or "unknown",
                    "module_id": row.get("module_id") or module_id,
                    "dimensions": dimension_map,
                    "facts": _mapping(row.get("facts")),
                    "evidence": _list(row.get("evidence")),
                    "diagnostics": _list(row.get("diagnostics")),
                    "coverage_status": (
                        "partial"
                        if any(
                            value in {"partial", "unknown", "unavailable"}
                            for value in dimension_map.values()
                        )
                        else "supported"
                    ),
                    "source_provenance": "indexed_graph",
                }
            )
        total = _total(count_rows, len(items))
        return {
            "ok": True,
            "project_id": raw,
            "frameworks": items,
            "total": total,
            "offset": page_offset,
            "limit": page_limit,
            "has_more": page_offset + len(items) < total,
        }

    async def get_module_architecture_summary(
        self,
        *,
        project_id: str,
        module_id: str = "",
        all_modules: bool = False,
        detail_level: str = "standard",
        item_limit: Any = DEFAULT_SAMPLE_LIMIT,
    ) -> Dict[str, Any]:
        if not module_id and not all_modules:
            raise ValueError("provide module_id or set all_modules=true")
        sample_limit = _positive_int(
            item_limit, DEFAULT_SAMPLE_LIMIT, MAX_SAMPLE_LIMIT
        )
        modules = await self.get_project_modules(
            project_id=project_id,
            module_id=module_id,
            include_dependencies=True,
            limit=MAX_LIMIT,
        )
        public_apis = await self.get_public_apis(
            project_id=project_id,
            module_id=module_id,
            limit=sample_limit,
        )
        endpoints = await self.get_endpoints(
            project_id=project_id,
            module_id=module_id,
            limit=sample_limit,
        )
        special_files = await self.get_project_special_files(
            project_id=project_id,
            module_id=module_id,
            limit=sample_limit,
        )
        frameworks = await self.get_framework_context(
            project_id=project_id,
            module_id=module_id,
            limit=sample_limit,
        )
        return {
            "ok": True,
            "project_id": project_id,
            "module_id": module_id,
            "all_modules": bool(all_modules),
            "detail_level": detail_level,
            "item_limit": sample_limit,
            "summary": {
                "modules": modules["modules"],
                "module_total": modules["total"],
                "public_api_count": public_apis["total"],
                "public_api_sample": public_apis["public_apis"],
                "endpoint_count": endpoints["total"],
                "endpoint_sample": endpoints["endpoints"],
                "special_file_count": special_files["total"],
                "special_file_sample": special_files["special_files"],
                "framework_count": frameworks["total"],
                "framework_sample": frameworks["frameworks"],
            },
            "ingestion_provenance": {
                "source": "indexed_graph",
                "filesystem_rescan": False,
            },
        }


__all__ = ["ProjectContextService"]
