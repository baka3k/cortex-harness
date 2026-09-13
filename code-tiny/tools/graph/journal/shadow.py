"""Shadow capture: ghi op-stream của journal ra JSONL cho replay Rust (Track B).

Env ``CORTEX_JOURNAL_SHADOW=<dir>``: khi set, ``GraphWriteJournalRuntime``
bọc ``SQLiteJournal`` bằng :class:`ShadowCaptureJournal` — mỗi op gọi xuống
store được append 1 dòng JSONL vào ``<dir>/<journal-stem>.jsonl``. Dòng đầu
file là header (``_header``) chứa ``schema_version`` + ``journal_config``.

Shape của 1 dòng op khớp fixture loader của
``scripts/rust_parity/gen_journal_scenario.py`` (``op``/``args``/``result``/
``error``); các key phụ debug mang prefix ``_`` (``_seq``, ``_captured_at``,
``_captured_at_epoch``) — side Rust bỏ qua key có prefix ``_``.

Mặc định OFF (env unset): hành vi executor không đổi, chi phí là 1 lần
``os.environ.get`` khi khởi tạo runtime (không phải mỗi write).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import threading
from pathlib import Path
from typing import Any, Mapping

from .config import JournalConfig
from .models import (
    JOURNAL_SCHEMA_VERSION,
    ArtifactRef,
    BarrierRecord,
    BatchRecord,
    BatchSpec,
    RunMetadata,
    RunRecord,
)
from .sqlite_store import SQLiteJournal

SHADOW_ENV = "CORTEX_JOURNAL_SHADOW"

# Thứ tự positional args của từng op — khớp chữ ký ``SQLiteJournal``.
_PARAM_NAMES: dict[str, tuple[str, ...]] = {
    "open_run": ("metadata", "expected_items", "expected_bytes"),
    "recover_run_leases_as_ambiguous": ("run_id",),
    "recover_expired_leases": (),
    "create_artifact": ("run_id", "rows"),
    "enqueue_batch": ("run_id", "spec"),
    "claim_job": ("job_id", "lease_seconds"),
    "claim_reconciling_job": ("job_id", "lease_seconds"),
    "get_batch": ("job_id",),
    "ack_batch": ("job_id", "fencing_token", "elapsed_ms"),
    "renew_lease": ("job_id", "fencing_token", "lease_seconds"),
    "mark_reconciling": ("job_id", "fencing_token", "error_code"),
    "schedule_retry": (
        "job_id",
        "fencing_token",
        "retry_at",
        "retry_class",
        "error_code",
    ),
    "schedule_reconciliation_retry": (
        "job_id",
        "fencing_token",
        "retry_at",
        "error_code",
    ),
    "block_batch": ("job_id", "fencing_token", "retry_class", "error_code"),
    "open_barrier": ("run_id", "name"),
    "close_barrier": ("run_id", "name"),
    "complete_producers": ("run_id",),
    "close": (),
}

# Default của các tham số tuỳ chọn (khớp SQLiteJournal) khi caller bỏ qua.
_PARAM_DEFAULTS: dict[str, Any] = {
    "expected_items": 0,
    "expected_bytes": 0,
    "lease_seconds": 300,
    "elapsed_ms": None,
    "error_code": None,
}


def _iso(value: dt.datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).isoformat(timespec="microseconds")


def artifact_to_dict(ref: ArtifactRef) -> dict[str, Any]:
    return dataclasses.asdict(ref)


def run_to_dict(record: RunRecord) -> dict[str, Any]:
    return {
        "run_id": record.run_id,
        "fingerprint": record.fingerprint,
        "metadata": record.metadata.to_dict(),
        "status": record.status.value,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "retention_until": record.retention_until,
        "error_code": record.error_code.value if record.error_code else None,
    }


def batch_to_dict(record: BatchRecord) -> dict[str, Any]:
    return {
        "job_id": record.job_id,
        "run_id": record.run_id,
        "phase": record.phase.value,
        "operation_key": record.operation_key,
        "sequence": record.sequence,
        "artifact": artifact_to_dict(record.artifact),
        "expected_count": record.expected_count,
        "status": record.status.value,
        "attempt": record.attempt,
        "max_attempts": record.max_attempts,
        "fencing_token": record.fencing_token,
        "lease_until": record.lease_until,
        "next_attempt_at": record.next_attempt_at,
        "required_barriers": list(record.required_barriers),
        "produced_barriers": list(record.produced_barriers),
        "retry_class": record.retry_class.value if record.retry_class else None,
        "error_code": record.error_code.value if record.error_code else None,
        "operation": dict(record.operation),
    }


def barrier_to_dict(record: BarrierRecord) -> dict[str, Any]:
    data = dataclasses.asdict(record)
    data["status"] = record.status.value
    return data


def spec_to_dict(spec: BatchSpec) -> dict[str, Any]:
    """Serialize ``BatchSpec`` khớp serde struct ``BatchSpec`` phía Rust."""

    return {
        "phase": spec.phase.value,
        "operation_key": spec.operation_key,
        "sequence": spec.sequence,
        "artifact": artifact_to_dict(spec.artifact),
        "expected_count": spec.expected_count,
        "required_barriers": list(spec.required_barriers),
        "produced_barriers": list(spec.produced_barriers),
        "max_attempts": spec.max_attempts,
        "operation": dict(spec.operation),
    }


def _serialize_value(name: str, value: Any) -> Any:
    if name == "metadata" and isinstance(value, RunMetadata):
        return value.to_dict()
    if name == "spec" and isinstance(value, BatchSpec):
        return spec_to_dict(value)
    if name == "retry_at" and isinstance(value, dt.datetime):
        return _iso(value)
    if hasattr(value, "value") and not isinstance(value, (str, int, float, bool)):
        return value.value  # enum → giá trị string ổn định
    if isinstance(value, Mapping):
        return dict(value)
    return value


def serialize_args(
    op_name: str, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> dict[str, Any]:
    """Gói positional/kwargs thành dict arg tên hoá, ổn định giữa các lần chạy."""

    names = _PARAM_NAMES.get(op_name)
    if names is None:
        # Op ngoài vocabulary (SQLiteJournal thêm mới) → serialize thân hữu hạn.
        return {"_positional": [repr(argument) for argument in args]}
    serialized: dict[str, Any] = {}
    for index, name in enumerate(names):
        if index < len(args):
            serialized[name] = _serialize_value(name, args[index])
        elif name in kwargs:
            serialized[name] = _serialize_value(name, kwargs[name])
        elif name in _PARAM_DEFAULTS:
            serialized[name] = _serialize_value(name, _PARAM_DEFAULTS[name])
        # required arg thiếu → để trống; call thật sẽ tự raise trước đó.
    if op_name in {"schedule_retry", "schedule_reconciliation_retry"}:
        retry_at = kwargs.get("retry_at") or (
            args[2] if len(args) > 2 else None
        )
        if isinstance(retry_at, dt.datetime):
            serialized["retry_at_epoch"] = retry_at.timestamp()
    return serialized


def _serialize_result(result: Any) -> Any:
    if isinstance(result, RunRecord):
        return run_to_dict(result)
    if isinstance(result, BatchRecord):
        return batch_to_dict(result)
    if isinstance(result, BarrierRecord):
        return barrier_to_dict(result)
    if isinstance(result, ArtifactRef):
        return artifact_to_dict(result)
    if isinstance(result, (list, tuple)):
        return [_serialize_result(item) for item in result]
    if result is None or isinstance(result, (str, int, float, bool)):
        return result
    return repr(result)


def _error_to_dict(exc: BaseException) -> dict[str, Any]:
    code = getattr(exc, "code", None)
    code_value = getattr(code, "value", code)
    return {"code": code_value if isinstance(code_value, str) else type(exc).__name__}


def journal_config_to_dict(config: JournalConfig) -> dict[str, Any]:
    return {
        "mode": config.mode,
        "path": str(config.path),
        "metadata": config.metadata.to_dict(),
        "limits": dataclasses.asdict(config.limits),
        "auto_resume": config.auto_resume,
        "lease_seconds": config.lease_seconds,
        "max_attempts": config.max_attempts,
        "retry_base_seconds": config.retry_base_seconds,
        "retry_max_seconds": config.retry_max_seconds,
    }


class ShadowCaptureJournal:
    """Proxy quanh ``SQLiteJournal``: ghi mỗi op ra JSONL rồi trả kết quả thật.

    Chỉ dùng cho producer path — mọi write của ingest đi qua
    ``GraphWriteJournalRuntime.journal`` (điểm capture duy nhất, xem
    phase-B1). Kết quả trả về/exception luôn là của store thật.
    """

    def __init__(self, inner: SQLiteJournal, capture_path: Path, header: dict[str, Any]) -> None:
        self._inner = inner
        self._capture_path = Path(capture_path)
        self._header = header
        self._lock = threading.Lock()
        self._capture_path.parent.mkdir(parents=True, exist_ok=True)
        fresh = (
            not self._capture_path.exists()
            or self._capture_path.stat().st_size == 0
        )
        if fresh:
            self._seq = 0
            self._write_line(dict(self._header), None)
        else:
            # Append vào capture có sẵn: tiếp tục đánh `_seq` liên tục
            # (file có 1 dòng header + N dòng op → seq bắt đầu từ N).
            with self._capture_path.open("r", encoding="utf-8") as handle:
                existing = sum(1 for line in handle if line.strip())
            self._seq = max(existing - 1, 0)

    # -- proxy ------------------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._inner, name)
        if not callable(attribute) or name not in _PARAM_NAMES:
            return attribute
        inner_callable = attribute

        def captured(*args: Any, **kwargs: Any) -> Any:
            return self._capture_and_call(name, inner_callable, args, kwargs)

        return captured

    def _capture_and_call(
        self, name: str, func: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> Any:
        now = dt.datetime.now(dt.timezone.utc)
        args_dict = serialize_args(name, args, kwargs)
        try:
            result = func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — lỗi thật vẫn raise sau khi ghi
            self._append({"op": name, "args": args_dict, "error": _error_to_dict(exc)}, now)
            raise
        self._append({"op": name, "args": args_dict, "result": _serialize_result(result)}, now)
        return result

    # -- JSONL append -----------------------------------------------------
    def _append(self, payload: dict[str, Any], now: dt.datetime) -> None:
        with self._lock:
            payload["_seq"] = self._seq
            self._seq += 1
        self._write_line(payload, now)

    def _write_line(self, payload: dict[str, Any], now: dt.datetime | None) -> None:
        if now is not None:
            payload["_captured_at"] = _iso(now)
            payload["_captured_at_epoch"] = now.timestamp()
        line = json.dumps(
            payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
        with self._capture_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def attach_shadow_capture(
    journal: SQLiteJournal, config: JournalConfig
) -> SQLiteJournal | ShadowCaptureJournal:
    """Bọc ``journal`` bằng shadow capture khi ``CORTEX_JOURNAL_SHADOW`` set.

    OFF (unset/empty) → trả nguyên bản journal, chi phí 1 ``os.environ.get``.
    """

    directory = str(os.environ.get(SHADOW_ENV) or "").strip()
    if not directory:
        return journal
    capture_path = Path(directory).expanduser() / f"{Path(config.path).stem}.jsonl"
    header = {
        "_header": True,
        "schema_version": JOURNAL_SCHEMA_VERSION,
        "journal_config": journal_config_to_dict(config),
    }
    return ShadowCaptureJournal(journal, capture_path, header)


def load_capture(path: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Đọc JSONL capture → ``(header, ops)``; op bỏ key top-level prefix ``_``.

    Đây là loader dùng chung cho unit test và các script replay/diff trong
    ``scripts/rust_parity/``.
    """

    header: dict[str, Any] | None = None
    ops: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if value.get("_header"):
            header = value
            continue
        ops.append({key: item for key, item in value.items() if not key.startswith("_")})
    if header is None:
        raise ValueError(f"capture {path} thiếu header line")
    return header, ops
