from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.common.reliability import (  # noqa: E402
    FailureClass,
    FailureRecord,
    PhaseResult,
    ReliabilityExitCode,
    RunOutcome,
    RunPhase,
    RunResult,
    atomic_write_run_result,
    exit_code_for,
    load_run_result,
    validate_transition,
)


def _failure(*, retryable: bool = False, failure_class: FailureClass = FailureClass.INTEGRITY) -> FailureRecord:
    return FailureRecord(
        code="relation_endpoint_missing",
        failure_class=failure_class,
        phase=RunPhase.VERIFYING_GENERATION,
        component="test",
        retryable=retryable,
        run_id="run-1",
        correlation_id="corr-1",
        summary="required endpoint was not accepted",
        safe_action="inspect quarantine.json",
        details={"token": "secret", "rows": list(range(150))},
    )


def test_result_round_trip_is_bounded_redacted_and_owner_only(tmp_path: Path):
    result = RunResult(
        run_id="run-1",
        correlation_id="corr-1",
        outcome=RunOutcome.FAILED_TERMINAL,
        phase=RunPhase.VERIFYING_GENERATION,
        component="test",
        failure=_failure(),
        phase_results=(
            PhaseResult(
                phase=RunPhase.VALIDATING,
                expected=4,
                accepted=3,
                quarantined=1,
            ),
        ),
    )
    path = tmp_path / "result.json"
    ref = atomic_write_run_result(path, result)

    loaded = load_run_result(path)
    assert loaded == result
    assert loaded.failure is not None
    assert loaded.failure.details["token"] == "<redacted>"
    assert len(loaded.failure.details["rows"]) == 101
    assert ref.sha256
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == "1.0"


def test_phase_accounting_rejects_unexplained_deltas():
    with pytest.raises(ValueError, match="discovery accounting"):
        PhaseResult(
            phase=RunPhase.VALIDATING,
            expected=10,
            accepted=8,
            quarantined=1,
        )


def test_retry_and_exit_policy_distinguishes_terminal_retryable_and_ambiguous():
    retryable = RunResult(
        run_id="run-1",
        correlation_id="corr-1",
        outcome=RunOutcome.FAILED_RETRYABLE,
        phase=RunPhase.WRITING_NODES,
        component="test",
        failure=_failure(retryable=True, failure_class=FailureClass.STORAGE_UNAVAILABLE),
    )
    ambiguous_failure = FailureRecord(
        code="mutation_ack_unknown",
        failure_class=FailureClass.AMBIGUOUS_MUTATION,
        phase=RunPhase.WRITING_NODES,
        component="test",
        retryable=False,
        run_id="run-1",
        correlation_id="corr-1",
        summary="submission completed but acknowledgment timed out",
        safe_action="reconcile operation identity before retry",
    )
    ambiguous = RunResult(
        run_id="run-1",
        correlation_id="corr-1",
        outcome=RunOutcome.AMBIGUOUS,
        phase=RunPhase.WRITING_NODES,
        component="test",
        failure=ambiguous_failure,
    )

    assert retryable.should_retry is True
    assert ambiguous.should_retry is False
    assert exit_code_for(retryable, observe_only=False) == ReliabilityExitCode.RETRYABLE_FAILURE
    assert exit_code_for(ambiguous, observe_only=False) == ReliabilityExitCode.AMBIGUOUS
    assert exit_code_for(ambiguous, observe_only=True) == ReliabilityExitCode.LEGACY_FAILURE


def test_state_machine_rejects_publication_before_verification():
    validate_transition(RunPhase.VERIFYING_GENERATION, RunPhase.PUBLISHING)
    with pytest.raises(ValueError, match="invalid reliability phase transition"):
        validate_transition(RunPhase.PARSING, RunPhase.PUBLISHING)

