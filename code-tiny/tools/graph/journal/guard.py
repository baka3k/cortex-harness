"""Fail-closed guard against graph mutations outside a journaled operation."""

from __future__ import annotations

import contextvars
import functools
import re
from contextlib import contextmanager
from typing import Any, Iterator

from .models import JournalError, TerminalErrorCode


_JOURNALED_MUTATION: contextvars.ContextVar[
    tuple[str, str, str, str, str] | None
] = contextvars.ContextVar(
    "graph_journaled_mutation", default=None
)
_MUTATION_PATTERN = re.compile(
    r"\b(?:CREATE|DELETE|DETACH|DROP|MERGE|REMOVE|SET)\b", re.IGNORECASE
)


@contextmanager
def journaled_mutation(
    job_id: str | None = None,
    operation_key: str = "schema",
    *,
    artifact_sha256: str = "",
    run_id: str = "",
    generation: str = "",
) -> Iterator[None]:
    token = _JOURNALED_MUTATION.set(
        (
            job_id or "schema",
            operation_key,
            artifact_sha256,
            run_id,
            generation,
        )
    )
    try:
        yield
    finally:
        _JOURNALED_MUTATION.reset(token)


def _receipt_query(query: str, *, returns_count: bool) -> str:
    # Receipts are the durable proof used after an ambiguous remote submit.
    # Never delete them by age on the mutation path: the current journal purge
    # API owns only SQLite/artifacts and cannot atomically prove that an exact
    # graph job ID is inactive. Safe bounding therefore remains the journal's
    # retention-expired, exact-scope lifecycle; graph cleanup must wait for a
    # graph-aware purge that deletes those exact IDs rather than an age range.
    source = query.strip().rstrip(";")
    if returns_count:
        source, replacements = re.subn(
            r"\bRETURN\s+count\((?P<value>\*|[A-Za-z_]\w*)\)\s+AS\s+count\s*$",
            r"WITH count(\g<value>) AS __journal_count",
            source,
            count=1,
            flags=re.IGNORECASE,
        )
        if replacements != 1:
            raise JournalError(
                TerminalErrorCode.INVALID_CONTRACT,
                "journaled count mutation must end with RETURN count(value) AS count",
            )
        return (
            source + " "
            "MERGE (receipt:GraphWriteReceipt {id: $__journal_job_id}) "
            "SET receipt.operation_key = $__journal_operation_key, "
            "receipt.row_count = __journal_count, "
            "receipt.artifact_sha256 = $__journal_artifact_sha256, "
            "receipt.run_id = $__journal_run_id, "
            "receipt.generation = $__journal_generation, "
            "receipt.applied_at = datetime() "
            "RETURN __journal_count AS count"
        )
    if re.search(r"\bRETURN\b", source, re.IGNORECASE):
        raise JournalError(
            TerminalErrorCode.INVALID_CONTRACT,
            "journaled mutation has an unsupported RETURN shape",
        )
    return _receipt_query(source + " RETURN count(*) AS count", returns_count=True)


def install_required_write_guard(driver: object) -> None:
    """Wrap a driver instance once, allowing reads and fenced mutations only."""

    if getattr(driver, "_graph_journal_guard_installed", False):
        return
    original = getattr(driver, "execute_query")

    @functools.wraps(original)
    async def guarded(query: str, *args: Any, **kwargs: Any) -> Any:
        is_mutation = bool(_MUTATION_PATTERN.search(str(query)))
        mutation = _JOURNALED_MUTATION.get()
        if is_mutation and mutation is None:
            raise JournalError(
                TerminalErrorCode.INVALID_CONTRACT,
                "direct graph mutation bypassed the required write journal",
            )
        if is_mutation and mutation is not None and mutation[0] != "schema":
            job_id, operation_key, artifact_sha256, run_id, generation = mutation
            positional = list(args)
            if positional:
                parameters = dict(positional[0] or {})
                positional[0] = parameters
            else:
                parameters = dict(kwargs.get("parameters") or {})
                kwargs["parameters"] = parameters
            expected_count = len(parameters.get("rows", [])) or 1
            parameters["__journal_job_id"] = job_id
            supplied_operation_key = parameters.pop("__journal_operation_key", None)
            if supplied_operation_key not in {None, operation_key}:
                raise JournalError(
                    TerminalErrorCode.INVALID_CONTRACT,
                    "journal mutation operation key does not match its fence",
                )
            parameters["__journal_operation_key"] = operation_key
            parameters["__journal_expected_count"] = expected_count
            parameters["__journal_artifact_sha256"] = artifact_sha256
            parameters["__journal_run_id"] = run_id
            parameters["__journal_generation"] = generation
            returns_count = bool(
                re.search(
                    r"\bRETURN\b[\s\S]*\bAS\s+count\b",
                    str(query),
                    re.IGNORECASE,
                )
            )
            query = _receipt_query(str(query), returns_count=returns_count)
            args = tuple(positional)
        return await original(query, *args, **kwargs)

    setattr(driver, "execute_query", guarded)
    setattr(driver, "_graph_journal_guard_installed", True)
