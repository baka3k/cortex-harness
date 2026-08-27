"""Bounded retry classification and deterministic exponential backoff."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import RetryClass


def classify_error(error: Exception) -> RetryClass:
    if isinstance(error, (ConnectionError, TimeoutError, OSError)):
        return RetryClass.TRANSIENT
    return RetryClass.TERMINAL


def retry_at(
    attempt: int,
    *,
    now: datetime | None = None,
    base_seconds: int = 1,
    max_seconds: int = 60,
) -> datetime:
    if base_seconds <= 0 or max_seconds <= 0 or base_seconds > max_seconds:
        raise ValueError("retry bounds must be positive and ordered")
    delay_seconds = min(max_seconds, base_seconds * (2 ** max(0, attempt - 1)))
    return (now or datetime.now(timezone.utc)) + timedelta(seconds=delay_seconds)
