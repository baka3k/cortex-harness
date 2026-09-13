"""Shared graph-driver error types.

Drivers raise these so caller reconciliation logic does not depend on any
one backend's exception classes.
"""

from __future__ import annotations


class AmbiguousWriteTimeoutError(TimeoutError):
    """A timed-out mutation may have committed and must be reconciled."""


class NativeOperationInFlightError(RuntimeError):
    """A canceled native call is still running and owns the embedded client."""


__all__ = [
    "AmbiguousWriteTimeoutError",
    "NativeOperationInFlightError",
]
