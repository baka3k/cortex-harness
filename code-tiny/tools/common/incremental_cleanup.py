from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

import requests


class _QdrantCollectionMissing(Exception):
    """Raised when the target Qdrant collection does not exist."""


def _normalize_files(paths: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    ordered: List[str] = []
    for item in paths:
        if not item:
            continue
        normalized = item.replace("\\", "/")
        if normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _chunks(items: Sequence[str], size: int) -> Iterable[List[str]]:
    batch_size = max(1, size)
    for idx in range(0, len(items), batch_size):
        yield list(items[idx : idx + batch_size])


def _post_qdrant_delete(
    *,
    endpoint: str,
    payload: Dict[str, Any],
    timeout: float,
    retries: int,
    retry_sleep: float,
) -> None:
    for attempt in range(retries + 1):
        try:
            response = requests.post(endpoint, json=payload, timeout=timeout)
            response.raise_for_status()
            return
        except requests.RequestException as exc:
            if isinstance(exc, requests.HTTPError):
                response = getattr(exc, "response", None)
                if response is not None and getattr(response, "status_code", None) == 404:
                    raise _QdrantCollectionMissing() from exc
            if attempt >= retries:
                raise
            time.sleep(retry_sleep)


async def cleanup_neo4j_for_files(
    *,
    driver: Any,
    database: Optional[str],
    project_id: str,
    file_paths: Sequence[str],
    verbose: bool = False,
) -> Dict[str, int]:
    paths = _normalize_files(file_paths)
    if not paths:
        return {"deleted_nodes": 0, "deleted_unknown_functions": 0}

    if verbose:
        print(f"[cleanup][neo4j] deleting graph data for {len(paths)} files")

    delete_query = """
    WITH $paths AS paths, $project_id AS project_id
    MATCH (n)
    WHERE n.project_id = project_id
      AND (
        coalesce(n.file_path, '') IN paths
        OR coalesce(n.path, '') IN paths
        OR (n:File AND n.id IN paths)
      )
    WITH collect(DISTINCT n) AS nodes
    UNWIND nodes AS n
    WITH DISTINCT n
    DETACH DELETE n
    RETURN count(n) AS deleted_nodes
    """
    records, _, _ = await driver.execute_query(
        delete_query,
        {"project_id": project_id, "paths": paths},
        database=database,
    )
    deleted_nodes = int((records or [{}])[0].get("deleted_nodes", 0))

    prune_unknown_query = """
    MATCH (u:UnknownFunction)
    WHERE NOT ()-[:UNKNOWN_CALL]->(u)
    WITH collect(u) AS nodes
    UNWIND nodes AS u
    DETACH DELETE u
    RETURN count(u) AS deleted_unknown_functions
    """
    records, _, _ = await driver.execute_query(prune_unknown_query, database=database)
    deleted_unknown = int((records or [{}])[0].get("deleted_unknown_functions", 0))

    if verbose:
        print(
            "[cleanup][neo4j] deleted_nodes=%d deleted_unknown_functions=%d"
            % (deleted_nodes, deleted_unknown)
        )
    return {
        "deleted_nodes": deleted_nodes,
        "deleted_unknown_functions": deleted_unknown,
    }


def cleanup_qdrant_for_files(
    *,
    qdrant_url: str,
    collection: str,
    project_id: str,
    file_paths: Sequence[str],
    timeout: float = 300.0,
    retries: int = 3,
    retry_sleep: float = 2.0,
    batch_size: int = 256,
    verbose: bool = False,
) -> Dict[str, int]:
    paths = _normalize_files(file_paths)
    if not paths:
        return {"requested_files": 0, "deleted_filters": 0, "deleted_batches": 0}

    endpoint = qdrant_url.rstrip("/") + f"/collections/{collection}/points/delete?wait=true"
    deleted_batches = 0
    deleted_filters = 0
    batch_supported = True

    for group in _chunks(paths, batch_size):
        if batch_supported and len(group) > 1:
            batch_payload = {
                "filter": {
                    "must": [
                        {"key": "project_id", "match": {"value": project_id}},
                        {"key": "file_path", "match": {"any": group}},
                    ]
                }
            }
            try:
                _post_qdrant_delete(
                    endpoint=endpoint,
                    payload=batch_payload,
                    timeout=timeout,
                    retries=retries,
                    retry_sleep=retry_sleep,
                )
                deleted_batches += 1
                continue
            except _QdrantCollectionMissing:
                if verbose:
                    print(f"[cleanup][qdrant] collection '{collection}' not found; skip cleanup")
                return {
                    "requested_files": len(paths),
                    "deleted_filters": deleted_filters,
                    "deleted_batches": deleted_batches,
                }
            except requests.RequestException:
                batch_supported = False
                if verbose:
                    print("[cleanup][qdrant] batch filter unsupported/failure, fallback to per-file delete")

        for file_path in group:
            payload = {
                "filter": {
                    "must": [
                        {"key": "project_id", "match": {"value": project_id}},
                        {"key": "file_path", "match": {"value": file_path}},
                    ]
                }
            }
            try:
                _post_qdrant_delete(
                    endpoint=endpoint,
                    payload=payload,
                    timeout=timeout,
                    retries=retries,
                    retry_sleep=retry_sleep,
                )
            except _QdrantCollectionMissing:
                if verbose:
                    print(f"[cleanup][qdrant] collection '{collection}' not found; skip cleanup")
                return {
                    "requested_files": len(paths),
                    "deleted_filters": deleted_filters,
                    "deleted_batches": deleted_batches,
                }
            deleted_filters += 1
    if verbose:
        print(
            "[cleanup][qdrant] files=%d deleted_batches=%d deleted_filters=%d"
            % (len(paths), deleted_batches, deleted_filters)
        )
    return {
        "requested_files": len(paths),
        "deleted_filters": deleted_filters,
        "deleted_batches": deleted_batches,
    }


def cleanup_qdrant_with_writer(
    *,
    writer: Any,
    project_id: str,
    file_paths: Sequence[str],
    verbose: bool = False,
) -> Dict[str, int]:
    return cleanup_qdrant_for_files(
        qdrant_url=str(getattr(writer, "url")),
        collection=str(getattr(writer, "collection")),
        project_id=project_id,
        file_paths=file_paths,
        timeout=float(getattr(writer, "timeout", 300.0)),
        retries=int(getattr(writer, "retries", 3)),
        retry_sleep=float(getattr(writer, "retry_sleep", 2.0)),
        verbose=verbose,
    )
