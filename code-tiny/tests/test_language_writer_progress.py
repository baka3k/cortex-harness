from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from tools.graph.writer.language_writer import LanguageCodeWriter


class _RecordingDriver:
    provider = "falkordb"

    async def execute_query(self, query, parameters=None, database=None):
        rows = (parameters or {}).get("rows", [])
        return ([{"count": len(rows)}], [], None)


class LanguageWriterProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_verbose_batch_lifecycle_is_visible_flushed_and_not_duplicated(self) -> None:
        writer = LanguageCodeWriter(
            _RecordingDriver(), database="code", batch_size=2, verbose=True
        )
        writer._progress_heartbeat_seconds = 0.001

        async def slow_write(batch):
            await asyncio.sleep(0.01)
            return len(batch)

        with patch("builtins.print") as output:
            written = await writer.write_batches("files", [{"id": 1}, {"id": 2}], slow_write)

        self.assertEqual(written, 2)
        messages = [str(call.args[0]) for call in output.call_args_list]
        self.assertEqual(sum("batch_started" in message for message in messages), 1)
        self.assertGreaterEqual(sum("query_running" in message for message in messages), 1)
        self.assertEqual(sum("batch_finished" in message for message in messages), 1)
        self.assertEqual(sum("batch_failed" in message for message in messages), 0)
        self.assertTrue(all(call.kwargs.get("flush") is True for call in output.call_args_list))
        self.assertIn("completed=2 total=2 matched=2", messages[-1])

    async def test_failed_batch_emits_one_terminal_failure_and_propagates(self) -> None:
        writer = LanguageCodeWriter(_RecordingDriver(), batch_size=1, verbose=True)

        async def fail_write(batch):
            raise RuntimeError("write failed")

        with patch("builtins.print") as output:
            with self.assertRaisesRegex(RuntimeError, "write failed"):
                await writer.write_batches("relations", [{"id": 1}], fail_write)

        messages = [str(call.args[0]) for call in output.call_args_list]
        self.assertEqual(sum("batch_started" in message for message in messages), 1)
        self.assertEqual(sum("batch_failed" in message for message in messages), 1)
        self.assertEqual(sum("batch_finished" in message for message in messages), 0)
        self.assertIn("error=RuntimeError", messages[-1])

    async def test_non_verbose_writer_has_no_progress_output(self) -> None:
        writer = LanguageCodeWriter(_RecordingDriver(), batch_size=1, verbose=False)

        async def write(batch):
            return len(batch)

        with patch("builtins.print") as output:
            await writer.write_batches("files", [{"id": 1}], write)

        output.assert_not_called()

    async def test_cancellation_waits_for_inflight_write_reconciliation(self) -> None:
        writer = LanguageCodeWriter(_RecordingDriver(), batch_size=1)
        started = asyncio.Event()
        release = asyncio.Event()
        completed = False

        async def write(batch):
            nonlocal completed
            started.set()
            await release.wait()
            completed = True
            return len(batch)

        task = asyncio.create_task(writer.write_batches("files", [{"id": 1}], write))
        await started.wait()
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(completed)

    async def test_cancellation_reconciliation_has_a_deadline(self) -> None:
        writer = LanguageCodeWriter(_RecordingDriver(), batch_size=1, verbose=True)
        writer._reconciliation_timeout_seconds = 0.01
        writer._progress_heartbeat_seconds = 0.002
        started = asyncio.Event()
        release = asyncio.Event()

        async def write(batch):
            started.set()
            await release.wait()
            return len(batch)

        task = asyncio.create_task(writer.write_batches("files", [{"id": 1}], write))
        await started.wait()
        task.cancel()
        with patch("builtins.print") as output:
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=0.1)
        messages = [str(call.args[0]) for call in output.call_args_list]
        self.assertTrue(any("batch_reconcile_ambiguous" in message for message in messages))
        release.set()
        await asyncio.sleep(0)


if __name__ == "__main__":
    unittest.main()
