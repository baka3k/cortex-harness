"""Bounded retry classification and injectable exponential backoff."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import random
from typing import Callable

from .models import JournalError, RetryClass, TerminalErrorCode


def classify_error(error: Exception) -> RetryClass:
    if isinstance(error, JournalError):
        if error.code in {
            TerminalErrorCode.ARTIFACT_HASH_MISMATCH,
            TerminalErrorCode.JOURNAL_CORRUPT,
        }:
            return RetryClass.INTEGRITY
        if error.code in {
            TerminalErrorCode.INCOMPATIBLE_SCHEMA,
            TerminalErrorCode.INVALID_CONTRACT,
        }:
            return RetryClass.INCOMPATIBLE
        return RetryClass.TERMINAL
    if isinstance(error, (ConnectionError, TimeoutError, OSError)):
        return RetryClass.TRANSIENT
    return RetryClass.TERMINAL


def retry_at(
    attempt: int,
    *,
    now: datetime | None = None,
    base_seconds: int = 1,
    max_seconds: int = 60,
    jitter: Callable[[float, float], float] | None = None,
) -> datetime:
    if base_seconds <= 0 or max_seconds <= 0 or base_seconds > max_seconds:
        raise ValueError("retry bounds must be positive and ordered")
    base_delay = min(max_seconds, base_seconds * (2 ** max(0, attempt - 1)))
    jitter_fn = jitter or random.uniform
    jitter_seconds = float(jitter_fn(0.0, base_delay * 0.25))
    delay_seconds = min(max_seconds, max(0.0, base_delay + jitter_seconds))
    return (now or datetime.now(timezone.utc)) + timedelta(seconds=delay_seconds)
