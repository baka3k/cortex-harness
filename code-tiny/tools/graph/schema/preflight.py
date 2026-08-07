"""Automatic, fail-closed graph schema preflight."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

from .manifest import CODE_GRAPH_SCHEMA, GraphSchemaManifest, SchemaIndex


logger = logging.getLogger(__name__)

_READY_STATES = {"ONLINE", "OPERATIONAL", "READY"}
_FAILED_STATES = {"FAILED", "FAILURE", "ERROR"}


class SchemaPreflightError(RuntimeError):
    """Required graph schema could not be made operational."""


@dataclass(frozen=True)
class SchemaEnsureResult:
    manifest: str
    fingerprint: str
    database: Optional[str]
    required_count: int
    verified_count: int
    elapsed_seconds: float


def _provider_name(driver: Any) -> str:
    provider = getattr(driver, "provider", "unknown")
    return str(getattr(provider, "value", provider)).lower()


def _normalize_properties(value: Any) -> Tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value)
    return ()


def _normalize_index_type(value: Any) -> str:
    index_type = str(value or "").casefold()
    if index_type == "btree":
        return "range"
    return index_type


def _normalize_entity_type(value: Any) -> str:
    return str(value or "node").casefold()


def _index_record(
    record: Mapping[str, Any],
) -> Tuple[str, Tuple[str, ...], str, str, str]:
    label_value = record.get("label", record.get("labelsOrTypes", record.get("labels_or_types", "")))
    if isinstance(label_value, Sequence) and not isinstance(label_value, str):
        label = str(label_value[0]) if label_value else ""
    else:
        label = str(label_value or "")
    properties = _normalize_properties(record.get("properties", record.get("property", ())))
    index_type = _normalize_index_type(record.get("index_type", record.get("type", "")))
    entity_type = _normalize_entity_type(
        record.get("entity_type", record.get("entityType", record.get("entitytype", "node")))
    )
    status = str(record.get("status", record.get("state", "")) or "").upper()
    return label, properties, index_type, entity_type, status


def _required_status(
    indexes: Iterable[SchemaIndex], records: Iterable[Mapping[str, Any]]
) -> Tuple[list[SchemaIndex], list[Tuple[SchemaIndex, str]]]:
    available: Dict[Tuple[str, Tuple[str, ...], str, str], str] = {}
    for record in records:
        label, properties, index_type, entity_type, status = _index_record(record)
        available[(label, properties, index_type, entity_type)] = status
    pending: list[SchemaIndex] = []
    failed: list[Tuple[SchemaIndex, str]] = []
    for index in indexes:
        if not index.required:
            continue
        status = available.get(index.key, "")
        if status in _FAILED_STATES:
            failed.append((index, status))
        elif status not in _READY_STATES:
            pending.append(index)
    return pending, failed


def _available_status(
    records: Iterable[Mapping[str, Any]],
) -> Dict[Tuple[str, Tuple[str, ...], str, str], str]:
    available: Dict[Tuple[str, Tuple[str, ...], str, str], str] = {}
    for record in records:
        label, properties, index_type, entity_type, status = _index_record(record)
        available[(label, properties, index_type, entity_type)] = status
    return available


async def ensure_schema(
    driver: Any,
    manifest: GraphSchemaManifest = CODE_GRAPH_SCHEMA,
    *,
    database: Optional[str] = None,
    timeout_seconds: float = 60.0,
    poll_interval_seconds: float = 0.1,
) -> SchemaEnsureResult:
    """Create and verify required indexes before the first graph mutation."""

    started = time.monotonic()
    cache_key = (_provider_name(driver), database, manifest.fingerprint)
    cache = getattr(driver, "_schema_preflight_cache", None)
    if cache is None:
        cache = {}
        setattr(driver, "_schema_preflight_cache", cache)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    lock = getattr(driver, "_schema_preflight_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        setattr(driver, "_schema_preflight_lock", lock)

    async with lock:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        inspect_indexes = getattr(driver, "inspect_indexes", None)
        if not callable(inspect_indexes):
            raise SchemaPreflightError(
                f"schema {manifest.name}@{manifest.fingerprint} cannot be verified: "
                "driver does not implement inspect_indexes"
            )

        if _provider_name(driver) == "falkordb":
            unsupported = [index for index in manifest.indexes if len(index.properties) != 1]
            if unsupported:
                detail = ", ".join(
                    f"{index.label}({','.join(index.properties)})" for index in unsupported
                )
                raise SchemaPreflightError(
                    "FalkorDB schema manifests require single-property indexes: " + detail
                )

        deadline = started + timeout_seconds
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            initial_records = await asyncio.wait_for(
                inspect_indexes(database=database), timeout=remaining
            )
        except TimeoutError as exc:
            raise SchemaPreflightError(
                f"schema {manifest.name}@{manifest.fingerprint} inspection exceeded "
                f"{timeout_seconds:.1f}s deadline"
            ) from exc
        except Exception as exc:
            raise SchemaPreflightError(
                f"schema {manifest.name}@{manifest.fingerprint} inspection failed: {exc}"
            ) from exc
        available = _available_status(initial_records)
        failed_initial = [
            (index, available[index.key])
            for index in manifest.indexes
            if index.required and available.get(index.key) in _FAILED_STATES
        ]
        if failed_initial:
            detail = ", ".join(
                f"{index.label}({','.join(index.properties)})/{index.index_type}={status}"
                for index, status in failed_initial
            )
            raise SchemaPreflightError(
                f"schema {manifest.name}@{manifest.fingerprint} contains failed required indexes: "
                f"{detail}"
            )

        missing_required = [
            index for index in manifest.indexes if index.required and index.key not in available
        ]
        missing_optional = [
            index for index in manifest.indexes if not index.required and index.key not in available
        ]
        for index in missing_required:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SchemaPreflightError(
                    f"schema {manifest.name}@{manifest.fingerprint} creation exceeded "
                    f"{timeout_seconds:.1f}s deadline before {index.label}"
                )
            logger.info(
                "Graph schema index create_started database=%s label=%s properties=%s type=%s",
                database,
                index.label,
                ",".join(index.properties),
                index.index_type,
            )
            try:
                await asyncio.wait_for(
                    driver.create_indexes(manifest.driver_indexes((index,)), database=database),
                    timeout=remaining,
                )
            except TimeoutError as exc:
                raise SchemaPreflightError(
                    f"schema {manifest.name}@{manifest.fingerprint} creation timed out for "
                    f"{index.label}({','.join(index.properties)}); completion is ambiguous and "
                    "must be re-inspected before retry"
                ) from exc
            except Exception as exc:
                raise SchemaPreflightError(
                    f"schema {manifest.name}@{manifest.fingerprint} creation failed for "
                    f"{index.label}({','.join(index.properties)}): {exc}"
                ) from exc
            logger.info(
                "Graph schema index create_finished database=%s label=%s properties=%s type=%s",
                database,
                index.label,
                ",".join(index.properties),
                index.index_type,
            )

        for index in missing_optional:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning("Optional graph index skipped after schema deadline: %s", index)
                continue
            try:
                await asyncio.wait_for(
                    driver.create_indexes(manifest.driver_indexes((index,)), database=database),
                    timeout=remaining,
                )
            except Exception as exc:
                logger.warning("Optional graph index creation failed for %s: %s", index, exc)

        pending = [index for index in manifest.indexes if index.required]
        while pending:
            try:
                records = (
                    initial_records
                    if not missing_required and pending == [index for index in manifest.indexes if index.required]
                    else await asyncio.wait_for(
                        inspect_indexes(database=database),
                        timeout=max(0.001, deadline - time.monotonic()),
                    )
                )
                initial_records = []
            except TimeoutError as exc:
                raise SchemaPreflightError(
                    f"schema {manifest.name}@{manifest.fingerprint} inspection exceeded "
                    f"{timeout_seconds:.1f}s deadline"
                ) from exc
            except Exception as exc:
                raise SchemaPreflightError(
                    f"schema {manifest.name}@{manifest.fingerprint} inspection failed: {exc}"
                ) from exc
            pending, failed = _required_status(manifest.indexes, records)
            if failed:
                detail = ", ".join(
                    f"{index.label}({','.join(index.properties)})/{index.index_type}={status}"
                    for index, status in failed
                )
                raise SchemaPreflightError(
                    f"schema {manifest.name}@{manifest.fingerprint} contains failed required indexes: "
                    f"{detail}"
                )
            if not pending:
                break
            if time.monotonic() >= deadline:
                detail = ", ".join(
                    f"{index.label}({','.join(index.properties)})" for index in pending
                )
                raise SchemaPreflightError(
                    f"schema {manifest.name}@{manifest.fingerprint} not operational before "
                    f"{timeout_seconds:.1f}s deadline: {detail}"
                )
            await asyncio.sleep(poll_interval_seconds)

        result = SchemaEnsureResult(
            manifest=manifest.name,
            fingerprint=manifest.fingerprint,
            database=database,
            required_count=sum(index.required for index in manifest.indexes),
            verified_count=sum(index.required for index in manifest.indexes),
            elapsed_seconds=time.monotonic() - started,
        )
        cache[cache_key] = result
        logger.info(
            "Graph schema ready provider=%s database=%s manifest=%s fingerprint=%s indexes=%d elapsed=%.3fs",
            _provider_name(driver),
            database,
            manifest.name,
            manifest.fingerprint,
            result.verified_count,
            result.elapsed_seconds,
        )
        return result


__all__ = ["SchemaEnsureResult", "SchemaPreflightError", "ensure_schema"]
