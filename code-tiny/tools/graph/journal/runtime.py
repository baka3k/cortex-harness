"""Inline durable producer/consumer used by ``LanguageCodeWriter``."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from .config import JournalConfig
from .identity import canonical_json
from .models import (
    BatchRecord,
    BatchSpec,
    BatchStatus,
    JournalError,
    RetryClass,
    TerminalErrorCode,
)
from .operation import GraphWriteOperation
from .sqlite_store import SQLiteJournal


@dataclass(frozen=True)
class JournalTicket:
    batch: BatchRecord
    execute: bool
    operation: GraphWriteOperation
    rows: tuple[dict[str, Any], ...]
    reconcile: bool = False


class GraphWriteJournalRuntime:
    """Persist, fence, and acknowledge one synchronous stream of async writes."""

    def __init__(self, config: JournalConfig) -> None:
        self.config = config
        self.journal = SQLiteJournal(config.path)
        self.run = self.journal.open_run(config.metadata)
        self.journal.recover_run_leases_as_ambiguous(self.run.run_id)
        self._node_barriers: dict[tuple[str, str], str] = {}

    def _barrier_contract(
        self,
        operation: GraphWriteOperation,
        rows: list[dict[str, Any]],
        sequence: int,
        artifact_sha256: str,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        produced: tuple[str, ...] = ()
        required: set[str] = set()
        if (
            operation.reconciliation == "node_identity"
            and operation.node_label
            and operation.identity_property
        ):
            barrier = (
                f"node:{operation.node_label}:{sequence}:{artifact_sha256[:16]}"
            )
            produced = (barrier,)
            for row in rows:
                identity = row.get(operation.row_identity_property)
                if identity is not None:
                    self._node_barriers[(operation.node_label, str(identity))] = barrier
        elif operation.reconciliation == "typed_relationship":
            for row in rows:
                for label_key, identity_key in (
                    ("source_label", "source_id"),
                    ("target_label", "target_id"),
                ):
                    dependency = self._node_barriers.get(
                        (str(row.get(label_key)), str(row.get(identity_key)))
                    )
                    if dependency:
                        required.add(dependency)
        elif operation.reconciliation == "repository_file":
            for row in rows:
                dependency = self._node_barriers.get(("File", str(row.get("id"))))
                if dependency:
                    required.add(dependency)
        elif operation.reconciliation == "evidence_edge":
            # Evidence-edge rows are self-describing: their endpoint barriers
            # are whatever node planes produced those identities, regardless
            # of the physical identity property (``id`` or ``site_id``).
            for row in rows:
                for label_key, value_key in (
                    ("source_label", "source_id"),
                    ("target_label", "target_id"),
                ):
                    dependency = self._node_barriers.get(
                        (str(row.get(label_key)), str(row.get(value_key)))
                    )
                    if dependency:
                        required.add(dependency)
        elif operation.reconciliation in {"call_edge", "call_site"}:
            for row in rows:
                for identity_key in ("caller_id", "callee_id"):
                    dependency = self._node_barriers.get(
                        ("Function", str(row.get(identity_key)))
                    )
                    if dependency:
                        required.add(dependency)
        return tuple(sorted(required)), produced

    def prepare(
        self,
        *,
        label: str,
        rows: Iterable[dict[str, Any]],
        sequence: int,
        operation: GraphWriteOperation | None = None,
        additional_required_barriers: tuple[str, ...] = (),
        defer: bool = False,
    ) -> JournalTicket:
        materialized = list(rows)
        operation = operation or GraphWriteOperation.for_label(label)
        if self.config.shadow:
            canonical_json(operation.to_dict())
            for row in materialized:
                canonical_json(row)
            raise RuntimeError("shadow operations do not create execution tickets")
        if operation.reconciliation == "unsupported":
            raise JournalError(
                TerminalErrorCode.INVALID_CONTRACT,
                f"operation {operation.operation_key} has no trusted recovery compiler",
            )
        artifact = self.journal.create_artifact(self.run.run_id, materialized)
        required_barriers, produced_barriers = self._barrier_contract(
            operation, materialized, sequence, artifact.sha256
        )
        required_barriers = tuple(
            sorted(set(required_barriers).union(additional_required_barriers))
        )
        batch = self.journal.enqueue_batch(
            self.run.run_id,
            BatchSpec(
                phase=operation.phase,
                operation_key=operation.operation_key,
                sequence=sequence,
                artifact=artifact,
                expected_count=len(materialized),
                required_barriers=required_barriers,
                produced_barriers=produced_barriers,
                operation=operation.to_dict(),
            ),
        )
        for barrier in produced_barriers:
            self.journal.close_barrier(self.run.run_id, barrier)
        if defer and batch.status is not BatchStatus.DONE:
            return JournalTicket(
                batch=batch,
                execute=False,
                operation=operation,
                rows=tuple(materialized),
            )
        if batch.status is BatchStatus.DONE:
            return JournalTicket(
                batch=batch,
                execute=False,
                operation=operation,
                rows=tuple(materialized),
            )
        if batch.status in {BatchStatus.BLOCKED, BatchStatus.DEAD_LETTER}:
            raise JournalError(
                batch.error_code or TerminalErrorCode.INVALID_TRANSITION,
                f"journal batch {batch.job_id} is terminal ({batch.status.value})",
            )
        if batch.status is BatchStatus.RECONCILING:
            reconciliation = self.journal.claim_reconciling_job(batch.job_id)
            if reconciliation is None or not reconciliation.fencing_token:
                raise JournalError(
                    TerminalErrorCode.INVALID_TRANSITION,
                    f"cannot fence reconciliation for {batch.job_id}",
                )
            return JournalTicket(
                batch=reconciliation,
                execute=False,
                operation=operation,
                rows=tuple(materialized),
                reconcile=True,
            )
        claimed = self.journal.claim_job(batch.job_id)
        if claimed is None or not claimed.fencing_token:
            current = self.journal.get_batch(batch.job_id)
            if current is not None and current.status is BatchStatus.DONE:
                return JournalTicket(
                    batch=current,
                    execute=False,
                    operation=operation,
                    rows=tuple(materialized),
                )
            raise JournalError(
                TerminalErrorCode.INVALID_TRANSITION,
                f"journal batch {batch.job_id} cannot be claimed",
            )
        return JournalTicket(
            batch=claimed,
            execute=True,
            operation=operation,
            rows=tuple(materialized),
        )

    def open_barrier(self, name: str) -> None:
        self.journal.open_barrier(self.run.run_id, name)

    def close_barrier(self, name: str) -> None:
        self.journal.close_barrier(self.run.run_id, name)

    def claim_deferred(self, ticket: JournalTicket) -> JournalTicket:
        current = self.journal.get_batch(ticket.batch.job_id)
        if current is not None and current.status is BatchStatus.DONE:
            return JournalTicket(
                batch=current,
                execute=False,
                operation=ticket.operation,
                rows=ticket.rows,
            )
        claimed = self.journal.claim_job(ticket.batch.job_id)
        if claimed is None:
            raise JournalError(
                TerminalErrorCode.INVALID_TRANSITION,
                f"deferred batch {ticket.batch.job_id} is not eligible",
            )
        return JournalTicket(
            batch=claimed,
            execute=True,
            operation=ticket.operation,
            rows=ticket.rows,
        )

    def resolve_reconciliation(
        self, ticket: JournalTicket, *, applied: bool | None
    ) -> JournalTicket:
        """ACK confirmed effects, replay confirmed misses, block unknown shapes."""

        if not ticket.reconcile or not ticket.batch.fencing_token:
            raise ValueError("ticket is not fenced for reconciliation")
        if applied is True:
            completed = self.journal.ack_batch(
                ticket.batch.job_id, ticket.batch.fencing_token
            )
            return JournalTicket(
                batch=completed,
                execute=False,
                operation=ticket.operation,
                rows=ticket.rows,
            )
        if applied is None:
            self.journal.block_batch(
                ticket.batch.job_id,
                ticket.batch.fencing_token,
                retry_class=RetryClass.INTEGRITY,
                error_code=TerminalErrorCode.INVALID_CONTRACT,
            )
            raise JournalError(
                TerminalErrorCode.INVALID_CONTRACT,
                f"operation {ticket.operation.operation_key} has no safe readback",
            )
        self.journal.schedule_retry(
            ticket.batch.job_id,
            ticket.batch.fencing_token,
            retry_at=datetime.now(timezone.utc),
            retry_class=RetryClass.AMBIGUOUS,
            error_code=TerminalErrorCode.INVALID_TRANSITION,
        )
        claimed = self.journal.claim_job(ticket.batch.job_id)
        if claimed is None:
            raise JournalError(
                TerminalErrorCode.INVALID_TRANSITION,
                f"reconciled batch {ticket.batch.job_id} cannot be reclaimed",
            )
        return JournalTicket(
            batch=claimed,
            execute=True,
            operation=ticket.operation,
            rows=ticket.rows,
        )

    def acknowledge(self, ticket: JournalTicket, count: int, elapsed_ms: int) -> None:
        if count != ticket.batch.expected_count:
            self.block_integrity(ticket)
            raise JournalError(
                TerminalErrorCode.INVALID_CONTRACT,
                f"graph write count mismatch for {ticket.batch.operation_key}: "
                f"expected {ticket.batch.expected_count}, received {count}",
            )
        assert ticket.batch.fencing_token is not None
        self.journal.ack_batch(
            ticket.batch.job_id,
            ticket.batch.fencing_token,
            elapsed_ms=elapsed_ms,
        )

    def renew(self, ticket: JournalTicket) -> None:
        if ticket.execute and ticket.batch.fencing_token:
            self.journal.renew_lease(
                ticket.batch.job_id,
                ticket.batch.fencing_token,
                lease_seconds=300,
            )

    def mark_ambiguous(self, ticket: JournalTicket) -> None:
        if not ticket.execute or not ticket.batch.fencing_token:
            return
        current = self.journal.get_batch(ticket.batch.job_id)
        if current is not None and current.status is BatchStatus.LEASED:
            self.journal.mark_reconciling(
                ticket.batch.job_id,
                ticket.batch.fencing_token,
                error_code=TerminalErrorCode.INVALID_TRANSITION,
            )

    def block_integrity(self, ticket: JournalTicket) -> None:
        if not ticket.execute or not ticket.batch.fencing_token:
            return
        self.journal.block_batch(
            ticket.batch.job_id,
            ticket.batch.fencing_token,
            retry_class=RetryClass.INTEGRITY,
            error_code=TerminalErrorCode.INVALID_CONTRACT,
        )

    def close(self) -> None:
        self.journal.close()
