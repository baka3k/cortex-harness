"""Autonomous durable journal consumer, including a subprocess entry point."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

from tools.graph.core.base import GraphDriver, GraphProvider
from tools.graph.core.factory import GraphDriverFactory

from .config import JournalConfig, journal_config_from_env
from .executor import compile_persisted_mutation, result_count
from .guard import install_required_write_guard, journaled_mutation
from .identity import run_id
from .models import JournalError, RetryClass, TerminalErrorCode
from .operation import GraphWriteOperation
from .reconcile import compile_reconciliation_readback, readback_count
from .retry import classify_error, retry_at
from .sqlite_store import SQLiteJournal


class GraphWriteJournalConsumer:
    """Drain one run using only its persisted descriptor and immutable artifact."""

    def __init__(self, config: JournalConfig, driver: GraphDriver) -> None:
        if not config.required:
            raise ValueError("the autonomous consumer is only valid in required mode")
        self.config = config
        self.driver = driver
        self.database = getattr(driver, "database", None)
        self.journal = SQLiteJournal(config.path)
        self.run_id = run_id(config.metadata)
        if self.journal.get_run(self.run_id) is None:
            self.journal.close()
            raise JournalError(
                TerminalErrorCode.INVALID_TRANSITION,
                "journal run does not exist for autonomous recovery",
            )
        install_required_write_guard(driver)
        self.journal.recover_run_leases_as_ambiguous(self.run_id)

    def _load(self, batch: Any) -> tuple[GraphWriteOperation, list[dict[str, Any]]]:
        try:
            operation = GraphWriteOperation.from_dict(batch.operation)
            if operation.operation_key != batch.operation_key:
                raise ValueError("batch and descriptor operation keys do not match")
            raw_rows = self.journal.artifacts.read_jsonl(batch.artifact)
            if not all(isinstance(row, dict) for row in raw_rows):
                raise ValueError("artifact rows must be JSON objects")
            rows = [dict(row) for row in raw_rows]
        except (KeyError, TypeError, ValueError) as exc:
            raise JournalError(
                TerminalErrorCode.INVALID_CONTRACT,
                f"invalid persisted operation for {batch.job_id}: {exc}",
            ) from exc
        if len(rows) != batch.expected_count:
            raise JournalError(
                TerminalErrorCode.ARTIFACT_HASH_MISMATCH,
                f"artifact count does not match batch {batch.job_id}",
            )
        return operation, rows

    async def _reconcile_one(self, batch: Any) -> None:
        assert batch.fencing_token
        operation, rows = self._load(batch)
        readback = compile_reconciliation_readback(
            operation, rows, job_id=batch.job_id
        )
        assert readback is not None
        try:
            records, _, _ = await self.driver.execute_query(
                readback[0], readback[1], self.database
            )
        except Exception as exc:
            retry_class = classify_error(exc)
            if retry_class is RetryClass.TRANSIENT:
                self.journal.schedule_retry(
                    batch.job_id,
                    batch.fencing_token,
                    retry_at=retry_at(batch.attempt),
                    retry_class=retry_class,
                    error_code=TerminalErrorCode.INVALID_TRANSITION,
                )
                return
            self.journal.block_batch(
                batch.job_id,
                batch.fencing_token,
                retry_class=retry_class,
                error_code=TerminalErrorCode.INVALID_CONTRACT,
            )
            raise
        if readback_count(records) == 1:
            self.journal.ack_batch(batch.job_id, batch.fencing_token)
            return
        self.journal.schedule_retry(
            batch.job_id,
            batch.fencing_token,
            retry_at=datetime.now(timezone.utc),
            retry_class=RetryClass.AMBIGUOUS,
            error_code=TerminalErrorCode.INVALID_TRANSITION,
        )

    async def _execute_one(self, batch: Any) -> None:
        assert batch.fencing_token
        try:
            operation, rows = self._load(batch)
            query, parameters = compile_persisted_mutation(operation, rows)
        except JournalError as exc:
            self.journal.block_batch(
                batch.job_id,
                batch.fencing_token,
                retry_class=RetryClass.INCOMPATIBLE,
                error_code=exc.code,
            )
            raise
        parameters["__journal_operation_key"] = operation.operation_key
        started = time.monotonic()
        try:
            with journaled_mutation(batch.job_id, operation.operation_key):
                records, _, _ = await self.driver.execute_query(
                    query, parameters, self.database
                )
            count = result_count(records)
            if count != batch.expected_count:
                self.journal.block_batch(
                    batch.job_id,
                    batch.fencing_token,
                    retry_class=RetryClass.INTEGRITY,
                    error_code=TerminalErrorCode.INVALID_CONTRACT,
                )
                raise JournalError(
                    TerminalErrorCode.INVALID_CONTRACT,
                    f"replayed count mismatch for {batch.job_id}: "
                    f"expected {batch.expected_count}, received {count}",
                )
            self.journal.ack_batch(
                batch.job_id,
                batch.fencing_token,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
        except JournalError:
            raise
        except BaseException:
            self.journal.mark_reconciling(
                batch.job_id,
                batch.fencing_token,
                error_code=TerminalErrorCode.INVALID_TRANSITION,
            )
            raise

    async def drain(self) -> int:
        drained = 0
        while True:
            ambiguous = self.journal.claim_reconciling(run_id_value=self.run_id)
            if ambiguous is not None:
                await self._reconcile_one(ambiguous)
                drained += 1
                continue
            pending = self.journal.claim_batch(run_id_value=self.run_id)
            if pending is None:
                return drained
            await self._execute_one(pending)
            drained += 1

    def close(self) -> None:
        self.journal.close()


async def resume_journal(config: JournalConfig, driver: GraphDriver) -> int:
    if not config.required or not config.path.is_file():
        return 0
    with SQLiteJournal(config.path) as journal:
        if journal.get_run(run_id(config.metadata)) is None:
            return 0
    consumer = GraphWriteJournalConsumer(config, driver)
    try:
        return await consumer.drain()
    finally:
        consumer.close()


async def _main() -> int:
    config = journal_config_from_env()
    if config is None or not config.required or not config.path.is_file():
        return 0
    provider_name = (
        os.getenv("CODE_GRAPH_PROVIDER") or os.getenv("GRAPH_PROVIDER") or "falkordb"
    ).casefold()
    provider = GraphProvider.NEO4J if provider_name in {"neo", "neo4j"} else GraphProvider.FALKORDB
    driver = await GraphDriverFactory.create_from_env(provider)
    try:
        count = await resume_journal(config, driver)
        if count:
            print(f"[journal] autonomously recovered {count} batch(es)", flush=True)
        return 0
    finally:
        driver.close()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_main()))
    except Exception as exc:
        print(f"[journal] recovery failed: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(70) from exc
