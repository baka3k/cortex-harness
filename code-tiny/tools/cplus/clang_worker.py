"""Disposable JSON-in/JSON-out libclang candidate worker."""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path


_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.cplus import clang_parser  # noqa: E402
from tools.cplus.parse_recovery import (  # noqa: E402
    MAX_SOURCE_BYTES,
    WORKER_PROTOCOL_VERSION,
    _contained_path,
    sanitize_compile_arguments,
)


def _apply_limits(memory_mb: int, cpu_seconds: int) -> None:
    try:
        import resource

        memory_bytes = int(memory_mb) * 1024 * 1024
        if hasattr(resource, "RLIMIT_AS"):
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        if hasattr(resource, "RLIMIT_CPU"):
            resource.setrlimit(resource.RLIMIT_CPU, (int(cpu_seconds), int(cpu_seconds) + 1))
        if hasattr(resource, "RLIMIT_CORE"):
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, OSError, ValueError):
        pass


def _disable_network() -> None:
    def denied(*_args, **_kwargs):
        raise RuntimeError("network access is disabled in parser workers")

    socket.socket = denied  # type: ignore[assignment]
    socket.create_connection = denied  # type: ignore[assignment]


def main() -> int:
    try:
        request = json.load(sys.stdin)
        if request.get("protocol_version") != WORKER_PROTOCOL_VERSION:
            raise ValueError("worker protocol mismatch")
        root = str(request["root"])
        path = _contained_path(root, str(request["path"]))
        max_source_bytes = min(int(request.get("max_source_bytes") or MAX_SOURCE_BYTES), MAX_SOURCE_BYTES)
        if os.path.getsize(path) > max_source_bytes:
            raise ValueError("source file exceeds worker size cap")
        arguments = sanitize_compile_arguments(
            [str(value) for value in request.get("compile_arguments") or ()],
            root=root,
            directory=os.path.dirname(path),
            source_path=path,
        )
        _apply_limits(int(request.get("memory_mb") or 1024), int(request.get("cpu_seconds") or 30))
        _disable_network()
        payload = clang_parser.parse_and_extract(
            path,
            root,
            "",
            validated_args=list(arguments),
        )
        response = {
            "protocol_version": WORKER_PROTOCOL_VERSION,
            "status": "ok" if payload is not None else "failed",
            "payload": payload,
            "error": "" if payload is not None else "libclang candidate unavailable",
        }
    except BaseException as exc:
        response = {
            "protocol_version": WORKER_PROTOCOL_VERSION,
            "status": "invalid",
            "payload": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
    sys.stdout.write(json.dumps(response, ensure_ascii=True))
    return 0 if response["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
