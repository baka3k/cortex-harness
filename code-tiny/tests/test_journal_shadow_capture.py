"""Shadow capture cho journal (Track B1, phase 1309-2104).

Kiểm chứng: ``CORTEX_JOURNAL_SHADOW=<dir>`` bật → mọi op qua
``GraphWriteJournalRuntime`` được append ra ``<dir>/<journal-stem>.jsonl``
(header đầu file + 1 dòng/op, shape khớp fixture loader của
``scripts/rust_parity/gen_journal_scenario.py``); OFF → không file, hành vi
executor không đổi.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.graph.journal.config import JournalConfig
from tools.graph.journal.models import JOURNAL_SCHEMA_VERSION, RunMetadata
from tools.graph.journal.runtime import GraphWriteJournalRuntime
from tools.graph.journal.shadow import SHADOW_ENV, SQLiteJournal, load_capture


def _metadata() -> RunMetadata:
    return RunMetadata(
        project_id="shadow-demo",
        scope_id="shadow-demo",
        source_revision="rev-1",
        source_snapshot="snap-1",
        physical_target="local",
        generation="gen-1",
        parser="python",
        parser_version="1",
        schema_fingerprint="sfp-1",
        query_shape_version="language-writer-v1",
    )


def _config(root: Path) -> JournalConfig:
    return JournalConfig(mode="required", path=root / "python.sqlite3", metadata=_metadata())


_ROWS = [
    {"id": "file-1", "file_path": "alpha.py", "project_id": "shadow-demo"},
    {"id": "file-2", "file_path": "beta.py", "project_id": "shadow-demo"},
]


def _run_write_batch(config: JournalConfig, sequence: int, rows: list[dict]) -> None:
    """Một write batch thật qua runtime: prepare → execute → acknowledge."""

    runtime = GraphWriteJournalRuntime(config)
    try:
        ticket = runtime.prepare(label="files", rows=list(rows), sequence=sequence)
        assert ticket.execute, "batch mới phải được claim để execute"
        runtime.acknowledge(ticket, count=len(rows), elapsed_ms=5)
    finally:
        runtime.close()


class JournalShadowCaptureTests(unittest.TestCase):
    def test_capture_enabled_writes_header_and_ops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()  # macOS /tmp là symlink → journal từ chối
            shadow_dir = root / "shadow"
            config = _config(root / "journal")
            with mock.patch.dict(os.environ, {SHADOW_ENV: str(shadow_dir)}):
                _run_write_batch(config, sequence=0, rows=list(_ROWS))
                # Runtime thứ hai dùng chung shadow dir → append, không nhân
                # header; batch khác sequence để không bị idempotent-dedupe.
                _run_write_batch(config, sequence=1, rows=list(reversed(_ROWS)))

            capture = shadow_dir / "python.jsonl"
            self.assertTrue(capture.is_file(), "phải sinh <dir>/<journal-stem>.jsonl")

            raw_lines = [
                line
                for line in capture.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            header = json.loads(raw_lines[0])
            self.assertTrue(header["_header"])
            self.assertEqual(header["schema_version"], JOURNAL_SCHEMA_VERSION)
            self.assertEqual(header["journal_config"]["metadata"]["project_id"], "shadow-demo")
            self.assertEqual(header["journal_config"]["path"], str(config.path))

            # Parse lại bằng cùng loader dùng cho fixture/replay.
            loaded_header, ops = load_capture(capture)
            self.assertEqual(loaded_header["schema_version"], JOURNAL_SCHEMA_VERSION)
            for op in ops:
                self.assertEqual(
                    set(op) - {"op"},
                    {"args"}
                    | ({"result"} if "result" in op else {"error"}),
                    "shape op phải khớp fixture loader: op/args/result|error",
                )

            names = [op["op"] for op in ops]
            # Hai write batch chạy nối tiếp → op stream lặp 2 lần.
            expected_cycle = [
                "open_run",
                "recover_run_leases_as_ambiguous",
                "create_artifact",
                "enqueue_batch",
                "close_barrier",
                "claim_job",
                "ack_batch",
                "close",
            ]
            self.assertEqual(names, expected_cycle + expected_cycle)
            # `_seq` đánh số liên tục xuyên các runtime (append semantics).
            self.assertEqual(
                [json.loads(line)["_seq"] for line in raw_lines[1:]],
                list(range(len(raw_lines) - 1)),
            )
            # `_captured_at` có mặt trên mỗi dòng op (key debug prefix `_`).
            for line in raw_lines[1:]:
                self.assertIn("_captured_at", json.loads(line))

            # create_artifact nhận đúng rows executor truyền vào.
            create_ops = [op for op in ops if op["op"] == "create_artifact"]
            self.assertEqual(create_ops[0]["args"]["rows"], _ROWS)
            enqueue_ops = [op for op in ops if op["op"] == "enqueue_batch"]
            self.assertEqual(
                enqueue_ops[0]["args"]["spec"]["operation_key"],
                "graph-write/v1/nodes/files",
            )
            # Kết quả serialize là record thật (fencing_token không rỗng khi lease).
            claim_ops = [op for op in ops if op["op"] == "claim_job"]
            self.assertTrue(claim_ops[0]["result"]["fencing_token"])

    def test_capture_off_writes_no_file_and_keeps_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            shadow_dir = root / "shadow"
            config = _config(root / "journal")
            with mock.patch.dict(os.environ):
                os.environ.pop(SHADOW_ENV, None)
                runtime = GraphWriteJournalRuntime(config)
                self.assertIsInstance(
                    runtime.journal,
                    SQLiteJournal,
                    "OFF → journal không được bọc wrapper",
                )
                try:
                    ticket = runtime.prepare(label="files", rows=list(_ROWS), sequence=0)
                    self.assertTrue(ticket.execute)
                    runtime.acknowledge(ticket, count=len(_ROWS), elapsed_ms=5)
                finally:
                    runtime.close()
            self.assertFalse(
                shadow_dir.exists() and any(shadow_dir.iterdir()),
                "OFF → không được sinh file capture",
            )

    def test_capture_empty_env_treated_as_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            shadow_dir = root / "shadow"
            config = _config(root / "journal")
            with mock.patch.dict(os.environ, {SHADOW_ENV: ""}):
                _run_write_batch(config, sequence=0, rows=list(_ROWS))
            self.assertFalse(shadow_dir.exists())


if __name__ == "__main__":
    unittest.main()
