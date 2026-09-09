"""
workflow_service.py — MCP service wrapper for find_screen_workflows.

Provides the tool-level entrypoint that unified_mcp registers. Reuses the
shared graph driver from whichever backend the caller is using (cplus/android)
by delegating driver acquisition to a small callable passed at init time.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Awaitable, Callable, Dict, List, Optional

from tools.common.project_registry import (
    ProjectNotRegisteredError,
    list_registered_projects,
    resolve_project_scope_candidates,
    resolve_project_targets,
)
from tools.graph.core.provider_contract import normalize_graph_provider_name

logger = logging.getLogger(__name__)


DriverProvider = Callable[[], Awaitable[Any]]


def _env_default_database() -> str:
    """Provider-aware default database from the environment."""
    provider = normalize_graph_provider_name(
        os.environ.get("CODE_GRAPH_PROVIDER")
        or os.environ.get("GRAPH_PROVIDER")
        or os.environ.get("MCP_GRAPH_PROVIDER")
    )
    if provider == "neo4j":
        return os.environ.get("NEO4J_DB") or "hyper_graph"
    return (
        os.environ.get("FALKORDB_GRAPH")
        or os.environ.get("FALKORDB_DATABASE")
        or "hyper_graph"
    )


def _project_graph(project_id: str) -> str:
    """Resolve the FalkorDB graph that stores ``project_id``'s topology.

    Follows the project registry when the project is registered and falls
    back to the per-project naming convention (code_graph == project_id), so
    an unregistered project_id still names the right graph. Neo4j keeps one
    shared database for every project (queries filter by project_id), so
    callers must route through :func:`_resolve_database_for_project`.
    """
    try:
        return resolve_project_targets(project_id).code_graph
    except ProjectNotRegisteredError:
        return project_id


def _resolve_database_for_project(project_id: str, explicit: str = "") -> str:
    """Pick the database for a project-scoped workflow query.

    An explicit caller-supplied db wins. Neo4j stores every project in one
    shared database (env-selected); FalkorDB shards per project, so the
    registry/naming-convention graph is used instead of the server default.
    """
    if explicit:
        return explicit
    provider = normalize_graph_provider_name(
        os.environ.get("CODE_GRAPH_PROVIDER")
        or os.environ.get("GRAPH_PROVIDER")
        or os.environ.get("MCP_GRAPH_PROVIDER")
    )
    if provider == "neo4j":
        return _env_default_database()
    return _project_graph(project_id) or _env_default_database()


def _merge_screen_workflow_results(
    results: Dict[str, Dict[str, Any]],
    node_a: str,
    node_b: Optional[str],
) -> Dict[str, Any]:
    """Merge per-project find_screen_workflows results into one payload.

    ``workflows`` and ``uncertainties`` are concatenated (each workflow
    tagged with its source ``project_id``); ``truncated`` is the logical OR;
    ``resolved`` candidates are concatenated per node. If every project
    failed, the first error is re-raised so callers see the standard error
    contract.
    """
    ok_projects = [
        project for project, result in sorted(results.items())
        if isinstance(result, dict) and result.get("ok") is not False
    ]
    failed = {
        project: result
        for project, result in sorted(results.items())
        if isinstance(result, dict) and result.get("ok") is False
    }
    if not ok_projects:
        first_failure = next(iter(failed.values()), None)
        first_error = ""
        if isinstance(first_failure, dict):
            first_error = str(first_failure.get("error") or "")
        raise ValueError(
            "project_id is omitted and no registered project produced a "
            "result."
            + (f" First error: {first_error}" if first_error else "")
            + " Register a project or pass project_id explicitly."
        )

    merged: Dict[str, Any] = {
        "ok": True,
        "project_id": "",
        "projects_searched": ok_projects,
    }
    if failed:
        merged["projects_failed"] = sorted(failed.keys())
    first = results[ok_projects[0]]
    merged["mode"] = first.get("mode", "single")
    merged["direction"] = first.get("direction", "bidirectional")

    workflows: List[Dict[str, Any]] = []
    uncertainties: List[str] = []
    resolved_candidates: Dict[str, List[Dict[str, Any]]] = {
        "node_a": [],
    }
    if node_b:
        resolved_candidates["node_b"] = []
    for project in ok_projects:
        result = results[project]
        for workflow in result.get("workflows") or []:
            if isinstance(workflow, dict):
                tagged = dict(workflow)
                tagged.setdefault("project_id", project)
                workflows.append(tagged)
            else:
                workflows.append(workflow)
        uncertainties.extend(result.get("uncertainties") or [])
        resolved = result.get("resolved") or {}
        node_a_entry = resolved.get("node_a") or {}
        if isinstance(node_a_entry, dict):
            for candidate in node_a_entry.get("candidates") or []:
                if isinstance(candidate, dict):
                    tagged = dict(candidate)
                    tagged.setdefault("project_id", project)
                    resolved_candidates["node_a"].append(tagged)
        if node_b:
            node_b_entry = resolved.get("node_b") or {}
            if isinstance(node_b_entry, dict):
                for candidate in node_b_entry.get("candidates") or []:
                    if isinstance(candidate, dict):
                        tagged = dict(candidate)
                        tagged.setdefault("project_id", project)
                        resolved_candidates["node_b"].append(tagged)

    merged["resolved"] = {
        "node_a": {"input": node_a, "candidates": resolved_candidates["node_a"]},
        "node_b": (
            {"input": node_b, "candidates": resolved_candidates["node_b"]}
            if node_b
            else None
        ),
    }
    merged["workflows"] = workflows
    merged["uncertainties"] = uncertainties
    merged["truncated"] = any(
        bool(results[project].get("truncated")) for project in ok_projects
    )
    return merged


async def run_find_screen_workflows(
    driver_provider: DriverProvider,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Dispatch entrypoint invoked by unified_mcp.

    Imports the core tool lazily to keep module import cheap (the tool pulls
    in logging/typing only, but we preserve the pattern used elsewhere).

    ``project_id`` is optional per the unified search contract: omit it to
    search every registered project. Each project is queried against its own
    graph (registry-resolved; naming-convention fallback) and the per-project
    results are merged.
    """

    from tools.ts.workflow_finder import find_screen_workflows  # lazy import

    project_id = (payload.get("project_id") or "").strip()
    node_a = (payload.get("node_a") or payload.get("source") or "").strip()
    node_b_raw = payload.get("node_b") or payload.get("target")
    node_b = node_b_raw.strip() if isinstance(node_b_raw, str) and node_b_raw.strip() else None
    direction = (payload.get("direction") or "bidirectional").strip().lower()
    explicit_database = str(payload.get("db") or payload.get("database") or "").strip()

    def _int(key: str, default: int) -> int:
        val = payload.get(key)
        if val in (None, ""):
            return default
        try:
            return int(val)
        except (TypeError, ValueError):
            return default

    def _bool(key: str) -> bool:
        val = payload.get(key)
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.strip().lower() in {"1", "true", "yes", "y"}
        return False

    max_hops = _int("max_hops", 8)
    max_paths = _int("max_paths", 100)
    include_entry_function = _bool("include_entry_function")
    include_api_calls = _bool("include_api_calls")

    if not node_a:
        raise ValueError("node_a is required (bare name or symbol_id)")
    if not node_b and direction not in {"inbound", "outbound", "bidirectional"}:
        raise ValueError("direction must be one of: inbound, outbound, bidirectional")

    async def _run_one(project: str, database: str) -> Dict[str, Any]:
        driver = await driver_provider()
        return await find_screen_workflows(
            driver,
            database,
            project_id=project,
            node_a=node_a,
            node_b=node_b,
            direction=direction,
            max_hops=max_hops,
            max_paths=max_paths,
            include_entry_function=include_entry_function,
            include_api_calls=include_api_calls,
        )

    if project_id:
        # project_id query rules: exact case-insensitive match wins, else
        # every registered project whose id casefold-starts-with the query
        # (bank -> bank_android, bank_Cplus) is searched and merged. No
        # registry match -> the raw id names an out-of-band shard.
        matched = resolve_project_scope_candidates(project_id)
        if len(matched) <= 1:
            return await _run_one(
                matched[0].project_id if matched else project_id,
                _resolve_database_for_project(project_id, explicit_database),
            )
        projects = [targets.project_id for targets in matched]
    else:
        # Omitted project_id — search every registered project and merge.
        # Each project is queried against its own graph; a caller-supplied
        # db override is honored as the shared database for every project
        # (queries still filter by project_id, so only matching projects
        # return rows).
        projects = list_registered_projects()
    if not projects:
        raise ValueError(
            "No registered project matches the query, so there is nothing "
            "to search. Register a project or pass project_id explicitly."
        )

    async def _run_project(project: str) -> Dict[str, Any]:
        try:
            return await _run_one(
                project, _resolve_database_for_project(project, explicit_database)
            )
        except Exception as exc:  # noqa: BLE001 — per-project isolation
            return {"ok": False, "error": str(exc)}

    runs = await asyncio.gather(*(_run_project(project) for project in projects))
    results = dict(zip(projects, runs))
    return _merge_screen_workflow_results(results, node_a, node_b)


__all__ = ["run_find_screen_workflows"]
