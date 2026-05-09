from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException, Request
import httpx

from ...db_flavors import UnsupportedDatabaseError, detect_flavor_name
from ..cache import graph_cache, paths_cache
from ..http_client import call_internal

logger = logging.getLogger("project_call_graph.mcp.graph")


class GraphQueryService:
    def _normalize_neo4j_db(self, value: str) -> str:
        name = value.strip()
        if not name:
            return name
        candidate = Path(name).expanduser()
        if candidate.is_absolute() or "/" in name or "\\" in name:
            return candidate.name
        return name

    def _normalise_db(self, db: str) -> Tuple[str, str]:
        try:
            db_name = self._normalize_neo4j_db(db)
            if not db_name:
                raise HTTPException(status_code=400, detail="Database name cannot be empty.")
            flavor = detect_flavor_name(db_name)
        except UnsupportedDatabaseError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return db_name, flavor.value

    def _extract_error_detail(self, response: httpx.Response) -> str:
        detail: Optional[str] = None
        try:
            data = response.json()
            if isinstance(data, dict):
                detail = data.get("detail") or data.get("message")
        except ValueError:
            detail = None
        if detail:
            return detail
        text = response.text.strip()
        if text:
            return text
        return response.reason_phrase

    def _translate_http_error(self, exc: httpx.HTTPStatusError) -> None:
        detail = self._extract_error_detail(exc.response)
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc

    async def query_subgraph(self, request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
        db_name, flavor = self._normalise_db(payload["db"])
        normalized = dict(payload)
        normalized["db"] = db_name
        cache_key = (
            db_name,
            normalized.get("function_id"),
            normalized.get("direction"),
            normalized.get("max_depth"),
        )
        cached = graph_cache.get(cache_key)
        if cached:
            result = dict(cached)
            result["cache_hit"] = True
            return result
        try:
            response = await call_internal(request, "POST", "/api/project-call-graph/graph", normalized)
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Graph query failed with %s | db=%s | function_id=%s",
                exc.response.status_code,
                db_name,
                normalized.get("function_id"),
            )
            self._translate_http_error(exc)
        except Exception as exc:
            logger.exception(
                "Graph query failed | db=%s | function_id=%s | direction=%s",
                db_name,
                normalized.get("function_id"),
                normalized.get("direction"),
            )
            raise HTTPException(status_code=502, detail="Unable to load call graph.") from exc
        result = dict(response)
        result["flavor"] = flavor
        result["cache_hit"] = False
        graph_cache.set(cache_key, result)
        return dict(result)

    async def find_paths(self, request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
        db_name, _ = self._normalise_db(payload["db"])
        normalized = dict(payload)
        normalized["db"] = db_name
        cache_key = (
            db_name,
            normalized.get("start_function_id"),
            normalized.get("end_function_id"),
            normalized.get("max_depth"),
        )
        cached = paths_cache.get(cache_key)
        if cached:
            result = dict(cached)
            result["cache_hit"] = True
            return result
        try:
            response = await call_internal(request, "POST", "/api/project-call-graph/detail-design", normalized)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status < 500:
                logger.warning(
                    "Path query returned %s | db=%s | start=%s | end=%s",
                    status,
                    db_name,
                    normalized.get("start_function_id"),
                    normalized.get("end_function_id"),
                )
                self._translate_http_error(exc)
            logger.exception(
                "Path query failed with %s | db=%s | start=%s | end=%s",
                status,
                db_name,
                normalized.get("start_function_id"),
                normalized.get("end_function_id"),
            )
            return await self._fallback_response(request, normalized, cache_key)
        except Exception as exc:
            logger.exception(
                "Path query failed | db=%s | start=%s | end=%s",
                db_name,
                normalized.get("start_function_id"),
                normalized.get("end_function_id"),
            )
            return await self._fallback_response(request, normalized, cache_key, exc)
        summary = response.get("summary", {})
        path = summary.get("path_function_ids") or summary.get("ordered_function_ids") or []
        call_records = response.get("call_records", [])
        diagram = response.get("diagram", {})
        result = {
            "path": path,
            "summary": summary,
            "call_records": call_records,
            "details": response.get("details", []),
            "diagram": {
                "sequence": diagram.get("sequence"),
                "class": diagram.get("class"),
            },
            "cache_hit": False,
            "fallback": False,
        }
        paths_cache.set(cache_key, result)
        return dict(result)

    async def _fallback_response(
        self,
        request: Request,
        normalized: Dict[str, Any],
        cache_key: Tuple[Any, ...],
        original_exc: Optional[Exception] = None,
    ) -> Dict[str, Any]:
        try:
            fallback = await self._build_fallback_path(request, normalized)
        except httpx.HTTPStatusError as exc:
            self._translate_http_error(exc)
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception(
                "Fallback path failed | start=%s | end=%s",
                normalized.get("start_function_id"),
                normalized.get("end_function_id"),
            )
            raise HTTPException(status_code=502, detail="Unable to compute fallback path.") from exc
        paths_cache.set(cache_key, fallback)
        return dict(fallback)

    async def _build_fallback_path(self, request: Request, normalized: Dict[str, Any]) -> Dict[str, Any]:
        start_id = normalized.get("start_function_id")
        end_id = normalized.get("end_function_id")
        depth = normalized.get("max_depth") or 2
        graph_payload = {
            "db": normalized["db"],
            "function_id": start_id,
            "direction": "out",
            "max_depth": depth,
        }
        graph = await self.query_subgraph(request, graph_payload)
        adjacency: Dict[int, list[int]] = {}
        for edge in graph.get("edges", []):
            if edge.get("direction") != "outgoing":
                continue
            src = edge.get("from_id")
            dst = edge.get("to_id")
            if not isinstance(src, int) or not isinstance(dst, int):
                continue
            adjacency.setdefault(src, []).append(dst)
        path = self._bfs_path(adjacency, start_id, end_id, depth)
        summary = {"ordered_function_ids": path}
        return {
            "path": path,
            "summary": summary,
            "call_records": [],
            "details": [],
            "diagram": {"sequence": None, "class": None},
            "cache_hit": False,
            "fallback": True,
        }

    @staticmethod
    def _bfs_path(adjacency: Dict[int, list[int]], start: Optional[int], end: Optional[int], max_depth: int) -> list[int]:
        if not isinstance(start, int):
            return []
        queue: list[tuple[int, list[int]]] = [(start, [start])]
        seen = {start}
        while queue:
            current, chain = queue.pop(0)
            if end is not None and current == end:
                return chain
            if len(chain) > max_depth:
                continue
            for neighbor in adjacency.get(current, []):
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                queue.append((neighbor, chain + [neighbor]))
        if end is None:
            return list(seen)
        return []

    async def explain_path(self, request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
        explanation = await self.find_paths(request, payload)
        snippets = []
        for detail in explanation.get("details", []):
            snippets.append(
                {
                    "id": detail.get("id"),
                    "qual_name": detail.get("qual_name"),
                    "file": detail.get("file"),
                    "start_line": detail.get("start_line"),
                    "snippet": detail.get("snippet") or detail.get("code_snippet"),
                }
            )
        return {
            "summary": explanation.get("summary"),
            "sequence_diagram": explanation.get("diagram", {}).get("sequence"),
            "class_diagram": explanation.get("diagram", {}).get("class"),
            "call_records": explanation.get("call_records"),
            "snippets": snippets,
        }

    async def get_symbol_detail(self, request: Request, node_id: int, db: str) -> Dict[str, Any]:
        db_path, _ = self._normalise_db(db)
        body = {"function_id": node_id, "db": db_path}
        return await call_internal(request, "POST", "/api/project-call-graph/node/detail", body)

    async def annotate_node(
        self,
        request: Request,
        node_id: int,
        note: Optional[str],
        db: str,
    ) -> Dict[str, Any]:
        db_path, _ = self._normalise_db(db)
        body = {"function_id": node_id, "note": note, "db": db_path}
        return await call_internal(request, "POST", "/api/project-call-graph/node/note", body)


graph_query_service = GraphQueryService()


async def query_subgraph(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    return await graph_query_service.query_subgraph(request, payload)


async def find_paths(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    return await graph_query_service.find_paths(request, payload)


async def explain_path(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    return await graph_query_service.explain_path(request, payload)


async def get_symbol_detail(request: Request, node_id: int, db: str) -> Dict[str, Any]:
    return await graph_query_service.get_symbol_detail(request, node_id, db)


async def annotate_node(
    request: Request,
    node_id: int,
    note: Optional[str],
    db: str,
) -> Dict[str, Any]:
    return await graph_query_service.annotate_node(request, node_id, note, db)
