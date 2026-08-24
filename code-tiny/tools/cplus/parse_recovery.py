"""Bounded, parser-local recovery queue and isolated libclang execution."""

from __future__ import annotations

import concurrent.futures
import hashlib
import importlib.metadata
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from tools.common.parse_quality import (
    RECOVERY_POLICY_VERSION,
    CandidateOutcome,
    ParserBackend,
    RetryStage,
    atomic_write_json,
)


QUEUE_SCHEMA_VERSION = "2"
WORKER_PROTOCOL_VERSION = "1"
MAX_COMPILE_DATABASE_BYTES = 32 * 1024 * 1024
MAX_COMPILE_DATABASE_ENTRIES = 100_000
MAX_COMPILE_TOKENS_PER_ENTRY = 2_048
MAX_WORKER_OUTPUT_BYTES = 32 * 1024 * 1024
MAX_WORKER_REQUEST_BYTES = 4 * 1024 * 1024
MAX_SOURCE_BYTES = 16 * 1024 * 1024

try:
    import psutil
except ImportError:  # Recovery fails closed when process-tree RSS cannot be observed.
    psutil = None  # type: ignore[assignment]


_REJECTED_PREFIXES = (
    "@",
    "-fplugin",
    "-fmodule-file",
    "-fmodules-cache-path",
    "-include-pch",
    "-load",
    "-serialize-diagnostics",
    "-save-temps",
)
_REJECTED_WITH_VALUE = {
    "-o",
    "-MF",
    "-MT",
    "-MQ",
    "-MJ",
    "-Xclang",
    "-include-pch",
    "-fmodule-file",
    "-fmodules-cache-path",
}
_PATH_FLAGS = {"-I", "-isystem", "-iquote", "-include"}
_SAFE_EXACT_FLAGS = {
    "-nostdinc",
    "-nostdinc++",
    "-fms-extensions",
    "-fms-compatibility",
    "-fshort-wchar",
}


@dataclass(frozen=True)
class RecoveryBudgets:
    max_files: int = 500
    wall_seconds: int = 900
    workers: int = 1
    per_file_timeout_seconds: int = 30
    memory_mb: int = 1024

    def validate(self) -> None:
        if min(
            self.max_files,
            self.wall_seconds,
            self.workers,
            self.per_file_timeout_seconds,
            self.memory_mb,
        ) <= 0:
            raise ValueError("recovery budgets must be positive")
        if self.workers > 32:
            raise ValueError("recovery workers exceed hard cap")


@dataclass(frozen=True)
class CompileContext:
    file_path: str
    arguments: Tuple[str, ...]
    fingerprint: str
    free_mode: bool = False


def _contained_path(root: str, path: str) -> str:
    root_real = os.path.realpath(os.path.abspath(root))
    path_real = os.path.realpath(os.path.abspath(path))
    try:
        if os.path.commonpath((root_real, path_real)) != root_real:
            raise ValueError("path escapes repository root")
    except ValueError as exc:
        raise ValueError("path escapes repository root") from exc
    return path_real


def _resolve_root_path(root: str, base_dir: str, value: str) -> str:
    candidate = value if os.path.isabs(value) else os.path.join(base_dir, value)
    return _contained_path(root, candidate)


def sanitize_compile_arguments(
    arguments: Sequence[str],
    *,
    root: str,
    directory: str,
    source_path: str,
) -> Tuple[str, ...]:
    """Return a strict libclang-only allowlist; never execute the command."""

    root_real = os.path.realpath(os.path.abspath(root))
    source_real = _contained_path(root_real, source_path)
    safe: List[str] = []
    index = 0
    if arguments and not str(arguments[0]).startswith("-"):
        index = 1  # compiler executable is data, never an executable action

    while index < len(arguments):
        token = str(arguments[index])
        if any(token.startswith(prefix) for prefix in _REJECTED_PREFIXES):
            raise ValueError(f"unsafe compile flag rejected: {token}")
        if token in _REJECTED_WITH_VALUE:
            raise ValueError(f"unsafe compile flag rejected: {token}")
        if token in {"-c", "--compile"}:
            index += 1
            continue
        if token == "-x":
            if index + 1 >= len(arguments):
                raise ValueError("missing -x language value")
            language = str(arguments[index + 1])
            if language not in {"c", "c++", "c-header", "c++-header"}:
                raise ValueError(f"unsupported language mode: {language}")
            safe.extend((token, language))
            index += 2
            continue
        if token in {"-D", "-U", "-std"}:
            if index + 1 >= len(arguments):
                raise ValueError(f"missing value for {token}")
            value = str(arguments[index + 1])
            if not value or value.startswith("-"):
                raise ValueError(f"invalid value for {token}")
            safe.extend((token, value))
            index += 2
            continue
        if token in _PATH_FLAGS:
            if index + 1 >= len(arguments):
                raise ValueError(f"missing path for {token}")
            resolved = _resolve_root_path(root_real, directory, str(arguments[index + 1]))
            safe.extend((token, resolved))
            index += 2
            continue
        attached_path_flag = next(
            (
                flag
                for flag in ("-isystem", "-iquote", "-I")
                if token.startswith(flag) and token != flag
            ),
            None,
        )
        if attached_path_flag:
            resolved = _resolve_root_path(root_real, directory, token[len(attached_path_flag) :])
            safe.append(f"{attached_path_flag}{resolved}")
            index += 1
            continue
        if token.startswith(("-D", "-U", "-std=")) or token in _SAFE_EXACT_FLAGS:
            safe.append(token)
            index += 1
            continue
        token_candidate = token if os.path.isabs(token) else os.path.join(directory, token)
        if os.path.realpath(os.path.abspath(token_candidate)) == source_real:
            index += 1
            continue
        if token.startswith("-"):
            raise ValueError(f"compile flag is not allowlisted: {token}")
        raise ValueError(f"unexpected compile command token: {token}")

    return tuple(safe)


def load_compile_database(
    path: str,
    *,
    root: str,
    max_bytes: int = MAX_COMPILE_DATABASE_BYTES,
    max_entries: int = MAX_COMPILE_DATABASE_ENTRIES,
    max_tokens_per_entry: int = MAX_COMPILE_TOKENS_PER_ENTRY,
) -> Dict[str, CompileContext]:
    if not path or not os.path.isfile(path):
        return {}
    path_real = _contained_path(root, path)
    if os.path.getsize(path_real) > max_bytes:
        raise ValueError("compile database exceeds size cap")
    with open(path_real, "rb") as handle:
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError("compile database exceeds size cap")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("compile database must be a JSON array")
    if len(payload) > max_entries:
        raise ValueError("compile database exceeds entry cap")

    contexts: Dict[str, CompileContext] = {}
    default_dir = os.path.dirname(path_real)
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("compile database entry must be an object")
        file_value = item.get("file")
        if not isinstance(file_value, str) or not file_value:
            raise ValueError("compile database entry has no file")
        directory_value = item.get("directory")
        directory = (
            str(directory_value) if isinstance(directory_value, str) and directory_value else default_dir
        )
        directory = _contained_path(root, directory)
        source_path = _resolve_root_path(root, directory, file_value)
        if isinstance(item.get("arguments"), list):
            arguments = [str(value) for value in item["arguments"]]
        elif isinstance(item.get("command"), str):
            arguments = shlex.split(item["command"], posix=os.name != "nt")
        else:
            raise ValueError("compile database entry has no arguments or command")
        if len(arguments) > max_tokens_per_entry:
            raise ValueError("compile database entry exceeds token cap")
        safe_arguments = sanitize_compile_arguments(
            arguments,
            root=root,
            directory=directory,
            source_path=source_path,
        )
        rel_path = os.path.relpath(source_path, os.path.realpath(root)).replace("\\", "/")
        encoded = json.dumps(safe_arguments, ensure_ascii=True, separators=(",", ":"))
        contexts.setdefault(
            rel_path,
            CompileContext(
                file_path=rel_path,
                arguments=safe_arguments,
                fingerprint=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            ),
        )
    return contexts


class PersistentRecoveryQueue:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        self.state: Dict[str, Any] = {"schema_version": QUEUE_SCHEMA_VERSION, "items": {}}
        if os.path.isfile(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if loaded.get("schema_version") == QUEUE_SCHEMA_VERSION:
                    self.state = loaded
            except (OSError, ValueError, TypeError):
                pass

    @staticmethod
    def identity(
        file_path: str,
        context_fingerprint: str,
        candidate_identity: str = "",
    ) -> str:
        return hashlib.sha256(
            f"{file_path}\0{context_fingerprint}\0{candidate_identity}".encode("utf-8")
        ).hexdigest()

    def enqueue(
        self,
        file_path: str,
        quality: Mapping[str, Any],
        priority: Tuple[Any, ...],
        candidate_identity: str = "",
    ) -> bool:
        identity = self.identity(
            file_path,
            str(quality.get("context_fingerprint") or ""),
            candidate_identity,
        )
        items = self.state.setdefault("items", {})
        existing = items.get(identity)
        if existing and existing.get("status") in {
            "selected",
            "not_improved",
            "diagnostic_only",
            "invalid",
            "failed",
            "timed_out",
        }:
            return False
        items[identity] = {
            "id": identity,
            "file_path": file_path,
            "context_fingerprint": quality.get("context_fingerprint") or "",
            "candidate_identity": candidate_identity,
            "priority": list(priority),
            "status": "pending",
            "attempts": int((existing or {}).get("attempts") or 0),
            "updated_at": time.time(),
        }
        self.flush()
        return True

    def pending(self) -> List[Dict[str, Any]]:
        items = self.state.get("items") or {}
        return sorted(
            (dict(item) for item in items.values() if item.get("status") == "pending"),
            key=lambda item: (tuple(item.get("priority") or ()), item.get("file_path") or ""),
        )

    def finish(self, identity: str, status: str, reason: str) -> None:
        item = (self.state.get("items") or {}).get(identity)
        if not item:
            return
        item["status"] = status
        item["reason"] = reason
        item["attempts"] = int(item.get("attempts") or 0) + 1
        item["updated_at"] = time.time()
        self.flush()

    def flush(self) -> None:
        atomic_write_json(
            self.path,
            self.state,
            allowed_root=os.path.dirname(self.path),
            max_bytes=16 * 1024 * 1024,
        )


def _worker_environment(temp_dir: str) -> Dict[str, str]:
    allowed = {"PATH", "PYTHONPATH", "SYSTEMROOT", "WINDIR", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"}
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env.update({"TMPDIR": temp_dir, "TEMP": temp_dir, "TMP": temp_dir, "NO_PROXY": "*"})
    return env


def run_clang_worker(
    *,
    worker_path: str,
    request: Mapping[str, Any],
    timeout_seconds: float,
    expected_protocol: str = WORKER_PROTOCOL_VERSION,
) -> Dict[str, Any]:
    request_bytes = json.dumps(dict(request), ensure_ascii=True).encode("utf-8")
    if len(request_bytes) > MAX_WORKER_REQUEST_BYTES:
        return {"status": "invalid", "error": "worker request exceeds cap"}
    if psutil is None:
        return {"status": "invalid", "error": "worker memory monitor unavailable"}
    try:
        memory_limit_bytes = int(request.get("memory_mb") or 1024) * 1024 * 1024
    except (TypeError, ValueError):
        return {"status": "invalid", "error": "worker memory limit is invalid"}
    if memory_limit_bytes <= 0:
        return {"status": "invalid", "error": "worker memory limit is invalid"}

    deadline = time.monotonic() + max(0.001, float(timeout_seconds))
    with tempfile.TemporaryDirectory(prefix="cplus-clang-worker-") as temp_dir:
        request_path = os.path.join(temp_dir, "request.json")
        stdout_path = os.path.join(temp_dir, "stdout.json")
        stderr_path = os.path.join(temp_dir, "stderr.log")
        with open(request_path, "wb") as handle:
            handle.write(request_bytes)
        if time.monotonic() >= deadline:
            return {"status": "timed_out", "error": "worker timeout"}
        with open(stdout_path, "wb") as stdout_handle, open(stderr_path, "wb") as stderr_handle:
            process = subprocess.Popen(
                [sys.executable, worker_path, request_path],
                stdin=subprocess.DEVNULL,
                stdout=stdout_handle,
                stderr=stderr_handle,
                cwd=temp_dir,
                env=_worker_environment(temp_dir),
                start_new_session=True,
            )
            stop_status = ""
            while process.poll() is None:
                if (
                    os.path.getsize(stdout_path) > MAX_WORKER_OUTPUT_BYTES
                    or os.path.getsize(stderr_path) > MAX_WORKER_OUTPUT_BYTES
                ):
                    stop_status = "output_limit"
                    break
                try:
                    worker_process = psutil.Process(process.pid)
                    rss_bytes = worker_process.memory_info().rss + sum(
                        child.memory_info().rss
                        for child in worker_process.children(recursive=True)
                    )
                except psutil.NoSuchProcess:
                    rss_bytes = 0
                except psutil.AccessDenied:
                    stop_status = "memory_monitor"
                    break
                if rss_bytes > memory_limit_bytes:
                    stop_status = "memory_limit"
                    break
                if time.monotonic() >= deadline:
                    stop_status = "timeout"
                    break
                time.sleep(0.01)

            if stop_status:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except (AttributeError, OSError, ProcessLookupError):
                    if process.poll() is None:
                        process.kill()
                process.wait()

        stdout_size = os.path.getsize(stdout_path)
        stderr_size = os.path.getsize(stderr_path)
        if stop_status == "timeout":
            return {"status": "timed_out", "error": "worker timeout"}
        if stop_status == "memory_limit":
            return {"status": "failed", "error": "worker memory limit exceeded"}
        if stop_status == "memory_monitor":
            return {"status": "invalid", "error": "worker memory monitor denied"}
        if (
            stop_status == "output_limit"
            or stdout_size > MAX_WORKER_OUTPUT_BYTES
            or stderr_size > MAX_WORKER_OUTPUT_BYTES
        ):
            return {"status": "invalid", "error": "worker output exceeds cap"}
        with open(stdout_path, "rb") as handle:
            stdout = handle.read(MAX_WORKER_OUTPUT_BYTES + 1).decode("utf-8", errors="replace")
        with open(stderr_path, "rb") as handle:
            stderr = handle.read(MAX_WORKER_OUTPUT_BYTES + 1).decode("utf-8", errors="replace")
        if process.returncode != 0:
            # A non-zero exit with a well-formed protocol response means the
            # worker classified its own failure (e.g. invalid request); prefer
            # that typed outcome over a raw stderr tail.
            try:
                typed = json.loads(stdout)
                if isinstance(typed, dict) and typed.get("protocol_version") == expected_protocol:
                    return typed
            except ValueError:
                pass
            return {
                "status": "failed",
                "error": (stderr or stdout or f"worker exit {process.returncode}")[-4000:],
            }
        try:
            result = json.loads(stdout)
        except ValueError:
            return {"status": "invalid", "error": "worker returned invalid JSON"}
        if not isinstance(result, dict) or result.get("protocol_version") != expected_protocol:
            return {"status": "invalid", "error": "worker protocol mismatch"}
        return result


def run_semantic_worker(
    *,
    worker_path: str,
    request: Mapping[str, Any],
    timeout_seconds: float,
) -> Dict[str, Any]:
    """Run one protocol-"2" semantic call-evidence worker request.

    The child worker validates the request itself (schema, containment,
    credential redaction, Pro*C bundle allowlisting); this wrapper keeps the
    same disposable-process, output-cap, RSS, and process-tree termination
    guarantees as the recovery worker and accepts the semantic protocol
    version on the response.
    """

    from tools.cplus.semantic_worker import SEMANTIC_WORKER_PROTOCOL_VERSION

    return run_clang_worker(
        worker_path=worker_path,
        request=request,
        timeout_seconds=timeout_seconds,
        expected_protocol=SEMANTIC_WORKER_PROTOCOL_VERSION,
    )


def _attach_quality(payload: Dict[str, Any], record: Dict[str, Any]) -> Dict[str, Any]:
    payload["parse_meta"] = {
        **dict(payload.get("parse_meta") or {}),
        "quality": record,
        "quality_tier": record["tier"],
        "parser_backend": record["context"]["backend"],
        "context_fingerprint": record["context_fingerprint"],
        "candidate_outcome": record["candidate_outcome"],
        "selected_candidate": record["selected_candidate"],
        "selection_reason": record["selection_reason"],
    }
    compact = {
        "schema_version": record["schema_version"],
        "tier": record["tier"],
        "backend": record["context"]["backend"],
        "parser_language": record["context"]["parser_language"],
        "context_fingerprint": record["context_fingerprint"],
        "recovery_policy_version": record["context"]["recovery_policy_version"],
        "selected_candidate": record["selected_candidate"],
        "selection_reason": record["selection_reason"],
    }
    payload["quality_provenance"] = compact
    if isinstance(payload.get("file_def"), dict):
        payload["file_def"]["parse_quality"] = compact
    payload["evidence_policy"] = {
        "strong_relation_types": ["CALLS", "INHERITS", "CONTAINS"],
        "strong_relations_allowed": record["tier"] != "quarantined",
        "weak_evidence_allowed": True,
    }
    return payload


def _priority(record: Mapping[str, Any], compile_available: bool) -> Tuple[int, int, int, int, int]:
    tier = str(record.get("tier") or "retry_required")
    damage = record.get("damage") or {}
    semantic = record.get("semantic_yield") or {}
    context = record.get("context") or {}
    return (
        0 if tier == "quarantined" else 1,
        0 if damage.get("critical_structural_damage") else 1,
        0 if context.get("lossy_decode") else 1,
        0 if compile_available else 1,
        int(semantic.get("function_count") or 0) + int(semantic.get("type_count") or 0),
    )


def recover_payload_candidates(
    *,
    root: str,
    candidates: Mapping[str, Dict[str, Any]],
    queue_path: str,
    compile_commands_path: str,
    budgets: RecoveryBudgets,
    worker_path: str,
    compile_contexts: Optional[Mapping[str, CompileContext]] = None,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    budgets.validate()
    resolved_compile_contexts = (
        dict(compile_contexts)
        if compile_contexts is not None
        else load_compile_database(compile_commands_path, root=root)
    )
    queue = PersistentRecoveryQueue(queue_path)
    try:
        libclang_version = importlib.metadata.version("libclang")
    except importlib.metadata.PackageNotFoundError:
        libclang_version = "unknown"
    candidate_identity = ":".join(
        (
            ParserBackend.LIBCLANG.value,
            libclang_version,
            WORKER_PROTOCOL_VERSION,
            RECOVERY_POLICY_VERSION,
        )
    )
    by_rel: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    for path, payload in candidates.items():
        rel_path = os.path.relpath(path, root).replace("\\", "/")
        quality = (payload.get("parse_meta") or {}).get("quality") or {}
        if quality.get("tier") not in {"retry_required", "quarantined"}:
            continue
        context = resolved_compile_contexts.get(
            rel_path,
            CompileContext(rel_path, (), "free-mode", free_mode=True),
        )
        queue.enqueue(
            rel_path,
            quality,
            _priority(quality, not context.free_mode),
            candidate_identity,
        )
        by_rel[rel_path] = (path, payload)

    started = time.monotonic()
    deadline = started + budgets.wall_seconds
    attempted = improved = non_improved = failed = 0
    consecutive_non_improvements = 0
    trailing: List[bool] = []
    stop_reason = "queue_empty"
    selected_payloads: Dict[str, Dict[str, Any]] = {}
    pending = [
        item
        for item in queue.pending()
        if str(item.get("file_path") or "") in by_rel
        and str(item.get("candidate_identity") or "") == candidate_identity
    ][: budgets.max_files]

    def execute(item: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        rel_path = str(item["file_path"])
        path, _ = by_rel[rel_path]
        remaining_seconds = deadline - time.monotonic()
        if remaining_seconds <= 0:
            return item, {"status": "timed_out", "error": "run wall-time budget"}
        context = resolved_compile_contexts.get(
            rel_path,
            CompileContext(rel_path, (), "free-mode", free_mode=True),
        )
        request = {
            "protocol_version": WORKER_PROTOCOL_VERSION,
            "root": root,
            "path": path,
            "compile_arguments": list(context.arguments),
            "compile_context_fingerprint": context.fingerprint,
            "memory_mb": budgets.memory_mb,
            "cpu_seconds": max(
                1,
                int(min(budgets.per_file_timeout_seconds, remaining_seconds) + 0.999),
            ),
            "max_output_bytes": MAX_WORKER_OUTPUT_BYTES,
            "max_source_bytes": MAX_SOURCE_BYTES,
        }
        return item, run_clang_worker(
            worker_path=worker_path,
            request=request,
            timeout_seconds=min(budgets.per_file_timeout_seconds, remaining_seconds),
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=budgets.workers) as executor:
        cursor = 0
        while cursor < len(pending):
            elapsed = time.monotonic() - started
            if elapsed >= budgets.wall_seconds:
                stop_reason = "wall_time_budget"
                break
            if consecutive_non_improvements >= 20:
                stop_reason = "consecutive_non_improvement_circuit_breaker"
                break
            if len(trailing) >= 100 and sum(trailing[-100:]) / 100 < 0.10:
                stop_reason = "trailing_improvement_rate_circuit_breaker"
                break
            wave = pending[cursor : cursor + budgets.workers]
            cursor += len(wave)
            futures = [executor.submit(execute, item) for item in wave]
            for future in concurrent.futures.as_completed(futures):
                item, result = future.result()
                attempted += 1
                rel_path = str(item["file_path"])
                path, baseline_payload = by_rel[rel_path]
                baseline_record = (baseline_payload.get("parse_meta") or {}).get("quality") or {}
                if result.get("status") != "ok" or not isinstance(result.get("payload"), dict):
                    status = str(result.get("status") or "failed")
                    queue.finish(str(item["id"]), "invalid" if status == "invalid" else status, str(result.get("error") or status))
                    failed += 1
                    consecutive_non_improvements += 1
                    trailing.append(False)
                    continue
                # Protocol 1 is retained only as a bounded diagnostic fixture.
                # Its whole-file payload can never replace Tree-sitter
                # structure, regardless of diagnostics or semantic yield.
                updated = dict(baseline_record)
                updated["retry_stages"] = sorted(
                    set(updated.get("retry_stages") or ()) | {RetryStage.LIBCLANG.value}
                )
                updated["candidate_outcome"] = (
                    CandidateOutcome.CROSS_BACKEND_STRUCTURE_FORBIDDEN.value
                )
                updated["selection_reason"] = "cross_backend_structure_forbidden"
                selected_payloads[path] = _attach_quality(baseline_payload, updated)
                queue.finish(
                    str(item["id"]),
                    "diagnostic_only",
                    "cross_backend_structure_forbidden",
                )
                non_improved += 1
                consecutive_non_improvements += 1
                trailing.append(False)
            if time.monotonic() >= deadline:
                stop_reason = "wall_time_budget"
                break
        else:
            stop_reason = "queue_empty"

    metrics = {
        "queued": len(pending),
        "attempted": attempted,
        "improved": improved,
        "non_improved": non_improved,
        "failed": failed,
        "stop_reason": stop_reason,
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "workers": budgets.workers,
        "max_files": budgets.max_files,
        "wall_seconds": budgets.wall_seconds,
    }
    return selected_payloads, metrics
