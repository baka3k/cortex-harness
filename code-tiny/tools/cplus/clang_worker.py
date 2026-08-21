"""Disposable JSON-in/JSON-out libclang candidate worker."""

from __future__ import annotations

import json
import os
import re
import socket
import sys


_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from tools.cplus import clang_parser, semantic_worker  # noqa: E402
from tools.cplus.parse_recovery import (  # noqa: E402
    MAX_SOURCE_BYTES,
    MAX_WORKER_REQUEST_BYTES,
    WORKER_PROTOCOL_VERSION,
    _contained_path,
    sanitize_compile_arguments,
)


def _apply_limits(memory_mb: int, cpu_seconds: int, output_bytes: int) -> None:
    try:
        import resource
    except ImportError as exc:
        raise RuntimeError("worker resource limits are unavailable") from exc

    memory_bytes = int(memory_mb) * 1024 * 1024
    memory_limit = getattr(resource, "RLIMIT_AS", None)
    if memory_limit is None:
        memory_limit = getattr(resource, "RLIMIT_DATA", None)
    required = {
        "cpu": getattr(resource, "RLIMIT_CPU", None),
        "output": getattr(resource, "RLIMIT_FSIZE", None),
    }
    if any(limit is None for limit in required.values()):
        raise RuntimeError("required worker resource limit is unavailable")
    try:
        resource.setrlimit(required["cpu"], (int(cpu_seconds), int(cpu_seconds) + 1))
        resource.setrlimit(required["output"], (int(output_bytes), int(output_bytes)))
        if hasattr(resource, "RLIMIT_CORE"):
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (OSError, ValueError) as exc:
        raise RuntimeError("failed to apply worker resource limits") from exc
    if memory_limit is not None:
        try:
            resource.setrlimit(memory_limit, (memory_bytes, memory_bytes))
        except (OSError, ValueError) as exc:
            # Darwin exposes RLIMIT_AS/RLIMIT_DATA but rejects finite values.
            # CPU, output, wall-time, and source-size limits remain mandatory;
            # other platforms fail closed when their advertised memory limit fails.
            if sys.platform != "darwin":
                raise RuntimeError("failed to apply worker memory limit") from exc


def _disable_network() -> None:
    def denied(*_args, **_kwargs):
        raise RuntimeError("network access is disabled in parser workers")

    socket.socket = denied  # type: ignore[assignment]
    socket.create_connection = denied  # type: ignore[assignment]


# Exception text (e.g. OSError file paths) must never leak absolute local
# paths into a worker response.
_ABSOLUTE_PATH_PATTERN = re.compile(r"(?:[A-Za-z]:)?(?:/[\w.\-+@ ]+)+")


def _redact_error_text(text: str) -> str:
    return _ABSOLUTE_PATH_PATTERN.sub("<path>", str(text))[:500]


def _error_response(exc: BaseException, semantic: bool) -> Dict[str, Any]:
    message = _redact_error_text(f"{type(exc).__name__}: {exc}")
    if semantic:
        return {
            "protocol_version": semantic_worker.SEMANTIC_WORKER_PROTOCOL_VERSION,
            "request_schema": semantic_worker.SEMANTIC_REQUEST_SCHEMA,
            "status": "invalid",
            "error": message,
            "callsites": [],
            "coverage": {"status": "failed", "detail": _redact_error_text(str(exc))},
        }
    return {
        "protocol_version": WORKER_PROTOCOL_VERSION,
        "status": "invalid",
        "payload": None,
        "error": message,
    }


def _run_semantic_request(request: Dict[str, Any]) -> Dict[str, Any]:
    """Protocol "2" call-evidence path (see semantic_worker module docs)."""

    validated = semantic_worker.validate_semantic_request(request)
    root = validated["root"]
    proc_bundle = validated["proc_bundle"]
    path = validated["path"]
    if proc_bundle is not None:
        # Pro*C semantic input is only the allowlisted generated artifact.
        path = os.path.join(root, *proc_bundle.artifact_path.replace("\\", "/").split("/"))
    path = _contained_path(root, path)
    max_source_bytes = min(
        int(validated["max_source_bytes"] or MAX_SOURCE_BYTES), MAX_SOURCE_BYTES
    )
    if os.path.getsize(path) > max_source_bytes:
        raise ValueError("source file exceeds worker size cap")
    if proc_bundle is not None:
        import hashlib  # noqa: PLC0415

        with open(path, "rb") as handle:
            artifact_sha = hashlib.sha256(handle.read()).hexdigest()
        if artifact_sha != proc_bundle.artifact_sha256:
            raise ValueError("generated artifact hash does not match the source bundle")

    arguments = sanitize_compile_arguments(
        validated["compile_arguments"],
        root=root,
        directory=os.path.dirname(path),
        source_path=path,
    )
    memory_mb = int(validated["memory_mb"] or 1024)
    cpu_seconds = int(validated["cpu_seconds"] or 30)
    output_bytes = int(validated["max_output_bytes"] or MAX_SOURCE_BYTES * 2)
    if not 1 <= memory_mb <= 4096:
        raise ValueError("worker memory limit is invalid")
    if not 1 <= cpu_seconds <= 300:
        raise ValueError("worker CPU limit is invalid")
    if not 1 <= output_bytes <= MAX_SOURCE_BYTES * 2:
        raise ValueError("worker output limit is invalid")
    _apply_limits(memory_mb, cpu_seconds, output_bytes)
    _disable_network()

    readiness = semantic_worker.probe_clang_runtime()
    if not readiness["ready"]:
        raise RuntimeError(f"clang_runtime_not_ready:{readiness['reason']}")
    extraction = semantic_worker.extract_semantic_callsite_evidence(
        path,
        root,
        list(arguments),
        config_fingerprint=validated["compile_context_fingerprint"],
        proc_bundle=proc_bundle,
    )
    return semantic_worker.build_semantic_response(
        validated,
        extraction,
        proc_bundle=proc_bundle,
        libclang_version=readiness["libclang_version"] or "",
    )


def main() -> int:
    try:
        if len(sys.argv) > 1:
            with open(sys.argv[1], "rb") as handle:
                request_bytes = handle.read(MAX_WORKER_REQUEST_BYTES + 1)
            if len(request_bytes) > MAX_WORKER_REQUEST_BYTES:
                raise ValueError("worker request exceeds cap")
            request = json.loads(request_bytes.decode("utf-8"))
        else:
            request = json.load(sys.stdin)
        if request.get("request_schema") == semantic_worker.SEMANTIC_REQUEST_SCHEMA:
            try:
                response = _run_semantic_request(request)
            except (ValueError, RuntimeError, OSError) as exc:
                # Typed request/runtime failure: still a well-formed response
                # so the gateway can classify it precisely.
                response = _error_response(exc, semantic=True)
            sys.stdout.write(json.dumps(response, ensure_ascii=True))
            return 0 if response["status"] == "ok" else 1
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
        memory_mb = int(request.get("memory_mb") or 1024)
        cpu_seconds = int(request.get("cpu_seconds") or 30)
        output_bytes = int(request.get("max_output_bytes") or MAX_SOURCE_BYTES * 2)
        if not 1 <= memory_mb <= 4096:
            raise ValueError("worker memory limit is invalid")
        if not 1 <= cpu_seconds <= 300:
            raise ValueError("worker CPU limit is invalid")
        if not 1 <= output_bytes <= MAX_SOURCE_BYTES * 2:
            raise ValueError("worker output limit is invalid")
        _apply_limits(memory_mb, cpu_seconds, output_bytes)
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
        response = _error_response(
            exc,
            semantic=isinstance(request, dict)
            and request.get("request_schema") == semantic_worker.SEMANTIC_REQUEST_SCHEMA,
        )
    sys.stdout.write(json.dumps(response, ensure_ascii=True))
    return 0 if response["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
