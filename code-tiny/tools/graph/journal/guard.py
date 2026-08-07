"""Fail-closed guard against graph mutations outside a journaled operation."""

from __future__ import annotations

import contextvars
import functools
import re
from contextlib import contextmanager
from typing import Any, Iterator

from .models import JournalError, TerminalErrorCode


_JOURNALED_JOB_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "graph_journaled_job_id", default=None
)
_MUTATION_PATTERN = re.compile(
    r"\b(?:CREATE|DELETE|DETACH|DROP|MERGE|REMOVE|SET)\b", re.IGNORECASE
)


@contextmanager
def journaled_mutation(job_id: str | None = None) -> Iterator[None]:
    token = _JOURNALED_JOB_ID.set(job_id or "schema")
    try:
        yield
    finally:
        _JOURNALED_JOB_ID.reset(token)


def _receipt_query(query: str, *, returns_count: bool) -> str:
    source = query.strip().rstrip(";")
    if returns_count:
        source, replacements = re.subn(
            r"\bRETURN\s+count\((?P<value>[A-Za-z_]\w*)\)\s+AS\s+count\s*$",
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
            "receipt.applied_at = datetime() "
            "RETURN __journal_count AS count"
        )
    if re.search(r"\bRETURN\b", source, re.IGNORECASE):
        source = "CALL { " + source + " }"
    return (
        source
        + " WITH $__journal_expected_count AS __journal_count "
        "MERGE (receipt:GraphWriteReceipt {id: $__journal_job_id}) "
        "SET receipt.operation_key = $__journal_operation_key, "
        "receipt.row_count = __journal_count, "
        "receipt.applied_at = datetime() "
        "RETURN __journal_count AS count"
    )


def install_required_write_guard(driver: object) -> None:
    """Wrap a driver instance once, allowing reads and fenced mutations only."""

    if getattr(driver, "_graph_journal_guard_installed", False):
        return
    original = getattr(driver, "execute_query")

    @functools.wraps(original)
    async def guarded(query: str, *args: Any, **kwargs: Any) -> Any:
        is_mutation = bool(_MUTATION_PATTERN.search(str(query)))
        job_id = _JOURNALED_JOB_ID.get()
        if is_mutation and job_id is None:
            raise JournalError(
                TerminalErrorCode.INVALID_CONTRACT,
                "direct graph mutation bypassed the required write journal",
            )
        if is_mutation and job_id != "schema":
            positional = list(args)
            if positional:
                parameters = dict(positional[0] or {})
                positional[0] = parameters
            else:
                parameters = dict(kwargs.get("parameters") or {})
                kwargs["parameters"] = parameters
            expected_count = len(parameters.get("rows", [])) or 1
            parameters["__journal_job_id"] = job_id
            parameters["__journal_operation_key"] = str(
                parameters.pop("__journal_operation_key", "graph-write")
            )
            parameters["__journal_expected_count"] = expected_count
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
