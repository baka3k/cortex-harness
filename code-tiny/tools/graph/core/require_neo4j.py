"""require_neo4j.py — Story #106 shared analyzer hard-fail helper.

Every per-language analyzer subprocess (android_kotlin_analyzer.py,
ts_analyzer.py, python_analyzer.py, ...) used to silently skip Neo4j
writes and exit 0 when any of ``--neo4j-uri / --neo4j-user / --neo4j-password``
was missing. ``incremental_sync.py`` then advanced ``last_good_sha`` on
that false-success — the C.2 failure mode in Story #106.

This module provides the shared resolution + hard-fail helpers so every
analyzer enforces the contract identically:

- ``add_require_neo4j_argument(parser)`` — registers ``--require-neo4j``.
- ``resolve_require_neo4j(args)`` — tri-state → bool.
- ``ensure_neo4j_or_exit(args, *, require, create_driver, verbose=False)``
  — the single hard-fail entry point. Returns the driver (or None when
  require=False and creds incomplete); raises SystemExit on any failure
  that would otherwise become a silent-skip.

Exit codes
----------
- 2 — operator misconfiguration: ``--require-neo4j`` on but creds incomplete.
- 3 — driver creation / handshake failed despite all creds present.

These codes are stable across analyzers; ``incremental_sync.py``'s
post-flight check uses them for diagnostics.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Callable, Optional


_EXIT_INCOMPLETE_CREDS = 2
_EXIT_DRIVER_FAILED = 3


def add_require_neo4j_argument(parser: argparse.ArgumentParser) -> None:
    """Register ``--require-neo4j`` on an analyzer's argparse Parser.

    Default is the env var ``REQUIRE_NEO4J`` or ``"auto"`` — matching
    operator intent (URI set ⇒ require). ``incremental_sync`` always
    passes the explicit value to remove ambiguity.
    """
    parser.add_argument(
        "--require-neo4j",
        default=os.environ.get("REQUIRE_NEO4J", "auto"),
        choices=("auto", "0", "1"),
        help=(
            "When 'auto' (default), require Neo4j writes only if NEO4J_URI is "
            "set; '1' forces requirement; '0' disables. Hard-fails (exit 2/3) "
            "if creds are missing or the driver cannot connect."
        ),
    )


def resolve_require_neo4j(args: argparse.Namespace) -> bool:
    """Resolve the ``--require-neo4j`` tri-state into a boolean.

    Returns True when the analyzer MUST produce Neo4j writes or exit
    non-zero. ``auto`` reads from ``args.neo4j_uri`` (set ⇒ require).
    """
    raw = str(getattr(args, "require_neo4j", "auto")).lower()
    if raw == "1":
        return True
    if raw == "0":
        return False
    return bool(getattr(args, "neo4j_uri", "") or "")


def _print_err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def check_creds_or_exit(args: argparse.Namespace, *, require: bool) -> bool:
    """Sync pre-driver-check: returns True if creds are complete, exits
    on (require=True AND creds incomplete). Returns False when require
    is False and any cred is missing (caller should proceed Qdrant-only).
    """
    complete = bool(
        getattr(args, "neo4j_uri", "") and
        getattr(args, "neo4j_user", "") and
        getattr(args, "neo4j_password", "")
    )
    if complete:
        return True
    if require:
        _print_err(
            "[analyzer] FATAL: --require-neo4j is on but Neo4j credentials "
            "are incomplete. Pass --neo4j-uri --neo4j-user --neo4j-password "
            "or unset NEO4J_URI to run in Qdrant-only mode."
        )
        sys.exit(_EXIT_INCOMPLETE_CREDS)
    return False


async def ensure_neo4j_driver_or_exit(
    args: argparse.Namespace,
    *,
    require: bool,
    create_driver: Callable[..., Any],
) -> Optional[Any]:
    """Wrap the analyzer's driver-creation call and hard-fail when
    require=True. Returns the driver or None.

    ``create_driver`` is an async callable that takes the same kwargs the
    analyzer passes to ``GraphDriverFactory.create_driver``: ``provider,
    uri, user, password``. Different analyzers may pass extra kwargs;
    callers should ``functools.partial`` those in.
    """
    if not check_creds_or_exit(args, require=require):
        return None
    try:
        return await create_driver(
            uri=args.neo4j_uri,
            user=args.neo4j_user,
            password=args.neo4j_password,
        )
    except Exception as exc:  # noqa: BLE001 — hard-fail with detail
        if require:
            _print_err(
                "[analyzer] FATAL: Neo4j driver creation failed: "
                f"{exc!r}. URI={args.neo4j_uri} — verify NEO4J_USER / "
                "NEO4J_PASS, or unset NEO4J_URI to run Qdrant-only."
            )
            sys.exit(_EXIT_DRIVER_FAILED)
        _print_err(
            "[analyzer] WARNING: Neo4j driver creation failed "
            f"({exc!r}); continuing in Qdrant-only mode because "
            "--require-neo4j is off."
        )
        return None


__all__ = [
    "add_require_neo4j_argument",
    "resolve_require_neo4j",
    "check_creds_or_exit",
    "ensure_neo4j_driver_or_exit",
]

