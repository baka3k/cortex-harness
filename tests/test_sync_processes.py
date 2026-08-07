from __future__ import annotations

from pathlib import Path
from unittest import mock

from cortex_harness import sync_processes as process_module
from cortex_harness.sync_processes import ProcessRecord, stop_sync_processes, sync_processes


ROOT = Path(__file__).resolve().parents[1]


def _record(pid: int, ppid: int, *argv: str) -> ProcessRecord:
    return ProcessRecord(pid=pid, ppid=ppid, argv=tuple(argv))


def test_sync_discovery_separates_code_doc_and_stop_commands():
    dev = str(ROOT / "cortex_harness" / "dev.py")
    incremental = str(ROOT / "code-tiny" / "tools" / "sync" / "incremental_sync.py")
    analyzer = str(ROOT / "code-tiny" / "tools" / "cplus" / "cplus_analyzer.py")
    ingestor = str(ROOT / "doc-tiny" / "graphrag_ingest_langextract.py")
    processes = {
        101: _record(101, 1, "python", dev, "sync", "code", "all"),
        102: _record(102, 101, "python", incremental, "--root", "/source"),
        103: _record(103, 102, "python", analyzer, "--root", "/source"),
        201: _record(201, 1, "python", dev, "sync", "doc", "all"),
        202: _record(202, 201, "python", ingestor, "--folder", "/docs"),
        301: _record(301, 1, "python", dev, "sync", "code", "stop"),
        302: _record(302, 1, "python", "/other/project/cplus_analyzer.py"),
    }

    assert [item.pid for item in sync_processes("code", root=ROOT, processes=processes)] == [
        101,
        102,
        103,
    ]
    assert [item.pid for item in sync_processes("doc", root=ROOT, processes=processes)] == [
        201,
        202,
    ]


def test_sync_discovery_honors_explicit_exclusions():
    dev = str(ROOT / "cortex_harness" / "dev.py")
    processes = {101: _record(101, 1, "python", dev, "sync", "code", "all")}

    assert sync_processes(
        "code", root=ROOT, processes=processes, exclude_pids={101}
    ) == []


def test_stop_sync_processes_terminates_descendants_before_parent():
    dev = str(ROOT / "cortex_harness" / "dev.py")
    table = {
        101: _record(101, 1, "python", dev, "sync", "code", "all"),
        102: _record(102, 101, "python", "child-worker.py"),
    }
    calls: list[tuple[str, int]] = []

    class FakeProcess:
        def __init__(self, pid: int):
            self.pid = pid

        def terminate(self) -> None:
            calls.append(("terminate", self.pid))

        def kill(self) -> None:
            calls.append(("kill", self.pid))

    with mock.patch.object(process_module, "process_table", return_value=table), mock.patch.object(
        process_module.psutil, "Process", side_effect=FakeProcess
    ), mock.patch.object(
        process_module.psutil, "wait_procs", side_effect=lambda values, timeout: (values, [])
    ), mock.patch.object(process_module.psutil, "pid_exists", return_value=False):
        report = stop_sync_processes("code", root=ROOT)

    assert calls == [("terminate", 102), ("terminate", 101)]
    assert report.matched == (101,)
    assert report.terminated == (101, 102)
    assert report.remaining == ()
