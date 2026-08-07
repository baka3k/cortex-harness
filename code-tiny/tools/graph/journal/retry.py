"""Bounded retry classification and deterministic exponential backoff."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import RetryClass


def classify_error(error: Exception) -> RetryClass:
    if isinstance(error, (ConnectionError, TimeoutError, OSError)):
        return RetryClass.TRANSIENT
    return RetryClass.TERMINAL


def retry_at(attempt: int, *, now: datetime | None = None) -> datetime:
    delay_seconds = min(60, 2 ** max(0, attempt - 1))
    return (now or datetime.now(timezone.utc)) + timedelta(seconds=delay_seconds)
