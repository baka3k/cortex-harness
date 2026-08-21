from __future__ import annotations

import contextlib
import asyncio
import io
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from cortex_harness.dev import _run_with_retry  # noqa: E402
from tools.common.reliability import (  # noqa: E402
    FailureClass,
    FailureRecord,
    RunOutcome,
    RunPhase,
    RunResult,
)
from tools.sync.incremental_sync import (  # noqa: E402
    _failure_record_for_exception,
    _redact_debug_text,
    _run,
    _write_summary,
    main,
)


def _result(outcome: RunOutcome, *, retryable: bool) -> RunResult:
    failure = FailureRecord(
        code="test_failure",
        failure_class=(
            FailureClass.STORAGE_UNAVAILABLE if retryable else FailureClass.INTEGRITY
        ),
        phase=RunPhase.WRITING_RELATIONS,
        component="test-child",
        retryable=retryable,
        run_id="run-1",
        correlation_id="corr-1",
        summary="controlled failure",
        safe_action="follow the controlled recovery action",
    )
    return RunResult(
        run_id="run-1",
        correlation_id="corr-1",
        outcome=outcome,
        phase=RunPhase.WRITING_RELATIONS,
        component="test-child",
        failure=failure,
    )


def test_terminal_child_result_is_not_retried(tmp_path: Path):
    result_path = tmp_path / "result.json"
    terminal = _result(RunOutcome.FAILED_TERMINAL, retryable=False)

    def execute(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        result_path.write_text(
            json.dumps({"run_result": terminal.to_dict()}), encoding="utf-8"
        )
        return subprocess.CompletedProcess(["worker"], 1)

    with patch("cortex_harness.dev.subprocess.run", side_effect=execute) as run:
        assert _run_with_retry(
            ["worker"],
            result_path=result_path,
            env={"CORTEX_RUN_ID": "run-1", "CORTEX_CORRELATION_ID": "corr-1"},
        ) == 1
    run.assert_called_once()


def test_retryable_child_result_uses_bounded_retry(tmp_path: Path):
    result_path = tmp_path / "result.json"
    retryable = _result(RunOutcome.FAILED_RETRYABLE, retryable=True)
    attempts = 0

    def execute(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            result_path.write_text(
                json.dumps({"run_result": retryable.to_dict()}), encoding="utf-8"
            )
            return subprocess.CompletedProcess(["worker"], 1)
        return subprocess.CompletedProcess(["worker"], 0)

    with patch("cortex_harness.dev.subprocess.run", side_effect=execute), patch(
        "cortex_harness.dev.time.sleep"
    ):
        assert _run_with_retry(
            ["worker"],
            result_path=result_path,
            env={"CORTEX_RUN_ID": "run-1", "CORTEX_CORRELATION_ID": "corr-1"},
        ) == 0
    assert attempts == 2


def test_typed_retryable_result_overrides_legacy_non_retryable_exit_code(tmp_path: Path):
    result_path = tmp_path / "result.json"
    retryable = _result(RunOutcome.FAILED_RETRYABLE, retryable=True)
    attempts = 0

    def execute(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            result_path.write_text(json.dumps({"run_result": retryable.to_dict()}), encoding="utf-8")
            return subprocess.CompletedProcess(["worker"], 1)
        return subprocess.CompletedProcess(["worker"], 0)

    with patch("cortex_harness.dev.subprocess.run", side_effect=execute), patch(
        "cortex_harness.dev.time.sleep"
    ):
        assert _run_with_retry(
            ["worker"],
            result_path=result_path,
            env={"CORTEX_RUN_ID": "run-1", "CORTEX_CORRELATION_ID": "corr-1"},
            non_retryable_exit_codes={1},
        ) == 0
    assert attempts == 2


def test_failed_child_stderr_is_captured_not_replayed_as_traceback(tmp_path: Path):
    command = [
        sys.executable,
        "-c",
        "import sys; print('progress'); print('Traceback: controlled', file=sys.stderr); raise SystemExit(3)",
    ]
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        with pytest.raises(subprocess.CalledProcessError) as captured:
            _run(command, cwd=str(tmp_path), verbose=False)

    assert "progress" in stdout.getvalue()
    assert "Traceback" not in stderr.getvalue()
    assert "Traceback: controlled" in captured.value.stderr


def test_required_mode_uses_typed_exit_mapping(tmp_path: Path):
    result_path = tmp_path / "result.json"
    retryable = _result(RunOutcome.FAILED_RETRYABLE, retryable=True)

    async def execute(args: object) -> int:
        Path(args.summary_path).write_text(
            json.dumps({"run_result": retryable.to_dict()}), encoding="utf-8"
        )
        return 1

    with patch("tools.sync.incremental_sync._run_incremental", side_effect=execute):
        exit_code = asyncio.run(
            main(
                [
                    "--root",
                    str(tmp_path),
                    "--summary-path",
                    str(result_path),
                    "--reliability-mode",
                    "required",
                    "--no-graph",
                ]
            )
        )

    assert exit_code == 11


def test_child_traceback_is_classified_as_internal_defect_with_issue_fingerprint():
    failure = _failure_record_for_exception(
        subprocess.CalledProcessError(
            1,
            ["worker"],
            stderr="Traceback (most recent call last):\nRuntimeError: boom",
        ),
        run_id="run-1",
        correlation_id="corr-1",
        phase=RunPhase.PARSING,
    )

    assert failure.failure_class is FailureClass.INTERNAL_DEFECT
    assert failure.code == "analyzer_internal_defect"
    assert len(failure.details["issue_fingerprint"]) == 16


def test_known_relationship_mismatch_is_terminal_integrity_not_internal_defect():
    failure = _failure_record_for_exception(
        subprocess.CalledProcessError(
            1,
            ["worker"],
            stderr=(
                "Traceback (most recent call last):\n"
                "RuntimeError: relationship batch integrity failure: "
                "expected=1000 matched=985 unresolved_or_ambiguous=15"
            ),
        ),
        run_id="run-1",
        correlation_id="corr-1",
        phase=RunPhase.WRITING_RELATIONS,
    )

    assert failure.code == "relationship_cardinality_mismatch"
    assert failure.failure_class is FailureClass.INTEGRITY
    assert failure.retryable is False


def test_timeout_is_ambiguous_unless_submission_is_explicitly_known_safe():
    ambiguous = _failure_record_for_exception(
        TimeoutError("write timed out"),
        run_id="run-1",
        correlation_id="corr-1",
        phase=RunPhase.WRITING_NODES,
    )
    safe = TimeoutError("connect timed out")
    safe.submission_state = "before_submission"  # type: ignore[attr-defined]
    retryable = _failure_record_for_exception(
        safe,
        run_id="run-1",
        correlation_id="corr-1",
        phase=RunPhase.WRITING_NODES,
    )

    assert ambiguous.failure_class is FailureClass.AMBIGUOUS_MUTATION
    assert ambiguous.retryable is False
    assert retryable.failure_class is FailureClass.TIMEOUT
    assert retryable.retryable is True


def test_connection_error_is_ambiguous_unless_submission_is_explicitly_known_safe():
    ambiguous = _failure_record_for_exception(
        ConnectionResetError("connection reset"),
        run_id="run-1",
        correlation_id="corr-1",
        phase=RunPhase.WRITING_RELATIONS,
    )
    safe = ConnectionError("connect failed")
    safe.submission_state = "before_submission"  # type: ignore[attr-defined]
    retryable = _failure_record_for_exception(
        safe,
        run_id="run-1",
        correlation_id="corr-1",
        phase=RunPhase.WRITING_RELATIONS,
    )

    assert ambiguous.failure_class is FailureClass.AMBIGUOUS_MUTATION
    assert ambiguous.retryable is False
    assert retryable.failure_class is FailureClass.STORAGE_UNAVAILABLE
    assert retryable.retryable is True


def test_debug_artifacts_redact_workspace_paths_and_common_secrets(tmp_path: Path):
    raw = (
        f"command: worker --token abc123 {tmp_path}/source.cpp\n"
        "password=hunter2\nAuthorization: Bearer signed-value\n"
    )

    redacted = _redact_debug_text(raw, roots=(str(tmp_path),))

    assert str(tmp_path) not in redacted
    assert "abc123" not in redacted
    assert "hunter2" not in redacted
    assert "signed-value" not in redacted
    assert redacted.count("<redacted>") == 3


def test_debug_redaction_covers_provider_flags_headers_cookies_and_urls():
    raw = (
        "NEO4J_PASS=graph-secret\n"
        "command: worker --neo4j-password cli-secret\n"
        "Authorization: Basic dXNlcjpwYXNz\n"
        "Cookie: session=signed-cookie\n"
        "endpoint=https://admin:url-secret@example.test/db\n"
    )

    redacted = _redact_debug_text(raw)

    for secret in (
        "graph-secret",
        "cli-secret",
        "dXNlcjpwYXNz",
        "signed-cookie",
        "admin",
        "url-secret",
    ):
        assert secret not in redacted


def test_summary_artifact_is_atomic_and_owner_only(tmp_path: Path):
    path = tmp_path / "summary.json"
    _write_summary(str(path), {"status": "success"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "success"}
    if os.name != "nt":
        # Windows has no owner/group/other permission bits; os.chmod there
        # only toggles the read-only flag.
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not [item for item in tmp_path.iterdir() if item.name.endswith(".tmp")]
