"""Discovery and scoped termination for Cortex code/document sync workers."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

import psutil


SyncOwner = Literal["code", "doc"]


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    ppid: int
    argv: tuple[str, ...]

    @property
    def command(self) -> str:
        return " ".join(self.argv)


@dataclass(frozen=True)
class StopReport:
    owner: SyncOwner
    matched: tuple[int, ...]
    terminated: tuple[int, ...]
    forced: tuple[int, ...]
    remaining: tuple[int, ...]


def process_table() -> dict[int, ProcessRecord]:
    """Return a cross-platform snapshot without shell process-name matching."""

    records: dict[int, ProcessRecord] = {}
    for process in psutil.process_iter(("pid", "ppid", "cmdline")):
        try:
            argv = tuple(str(value) for value in (process.info.get("cmdline") or ()))
            pid = int(process.info["pid"])
            ppid = int(process.info.get("ppid") or 0)
        except (psutil.Error, TypeError, ValueError):
            continue
        if argv:
            records[pid] = ProcessRecord(pid=pid, ppid=ppid, argv=argv)
    return records


def _resolved_argument(argument: str) -> Path | None:
    if not argument or argument.startswith("-"):
        return None
    try:
        return Path(argument).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None


def _is_dev_sync(record: ProcessRecord, owner: SyncOwner, root: Path) -> bool:
    dev_script = (root / "cortex_harness" / "dev.py").resolve(strict=False)
    for index, argument in enumerate(record.argv):
        if _resolved_argument(argument) != dev_script:
            continue
        tail = record.argv[index + 1 :]
        return len(tail) >= 2 and tail[:2] == ("sync", owner) and "stop" not in tail[2:]
    return False


def _is_code_worker(record: ProcessRecord, root: Path) -> bool:
    code_root = (root / "code-tiny").resolve(strict=False)
    explicit_names = {
        "build_owner_manifests.py",
        "clang_worker.py",
        "incremental_sync.py",
    }
    for argument in record.argv:
        path = _resolved_argument(argument)
        if path is None or code_root not in path.parents:
            continue
        if path.name in explicit_names or path.name.endswith("_analyzer.py"):
            return True
    return False


def _is_doc_worker(record: ProcessRecord, root: Path) -> bool:
    ingestor = (root / "doc-tiny" / "graphrag_ingest_langextract.py").resolve(
        strict=False
    )
    return any(_resolved_argument(argument) == ingestor for argument in record.argv)


def sync_processes(
    owner: SyncOwner,
    *,
    root: Path,
    processes: dict[int, ProcessRecord] | None = None,
    exclude_pids: Iterable[int] = (),
    include_launchers: bool = True,
) -> list[ProcessRecord]:
    """Find only harness sync launchers/workers for ``owner``.

    ``include_launchers=False`` restricts the match to worker processes
    (incremental sync + analyzers). A lifecycle sweep must never terminate
    another session's interactive ``dev sync <owner>`` launcher — with
    concurrent terminals that made runs kill each other right after the
    folder prompt. Explicit ``stop`` commands still match launchers.
    """

    table = process_table() if processes is None else processes
    excluded = {int(pid) for pid in exclude_pids}
    matches: list[ProcessRecord] = []
    for record in table.values():
        if record.pid in excluded:
            continue
        if (include_launchers and _is_dev_sync(record, owner, root)) or (
            _is_code_worker(record, root)
            if owner == "code"
            else _is_doc_worker(record, root)
        ):
            matches.append(record)
    return sorted(matches, key=lambda item: item.pid)


def _descendants(
    roots: set[int], processes: dict[int, ProcessRecord]
) -> set[int]:
    selected = set(roots)
    changed = True
    while changed:
        changed = False
        for record in processes.values():
            if record.ppid in selected and record.pid not in selected:
                selected.add(record.pid)
                changed = True
    return selected


def _depth(pid: int, processes: dict[int, ProcessRecord], selected: set[int]) -> int:
    depth = 0
    current = pid
    while current in processes and processes[current].ppid in selected:
        depth += 1
        current = processes[current].ppid
    return depth


def stop_sync_processes(
    owner: SyncOwner,
    *,
    root: Path,
    exclude_pids: Iterable[int] = (),
    timeout: float = 5.0,
    include_launchers: bool = True,
) -> StopReport:
    """Terminate matching workers and descendants, escalating only after timeout."""

    excluded = {os.getpid(), *(int(pid) for pid in exclude_pids)}
    table = process_table()
    matched = {
        record.pid
        for record in sync_processes(
            owner,
            root=root,
            processes=table,
            exclude_pids=excluded,
            include_launchers=include_launchers,
        )
    }
    targets = _descendants(matched, table) - excluded
    ordered = sorted(
        targets,
        key=lambda pid: (_depth(pid, table, targets), pid),
        reverse=True,
    )
    live: list[psutil.Process] = []
    for pid in ordered:
        try:
            process = psutil.Process(pid)
            process.terminate()
            live.append(process)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except psutil.AccessDenied:
            live.append(psutil.Process(pid))

    _, remaining = psutil.wait_procs(live, timeout=max(0.0, timeout)) if live else ([], [])
    forced: list[int] = []
    for process in remaining:
        try:
            process.kill()
            forced.append(process.pid)
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    if remaining:
        psutil.wait_procs(remaining, timeout=min(2.0, max(0.0, timeout)))

    still_running_values: list[int] = []
    for pid in targets:
        try:
            if psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE:
                still_running_values.append(pid)
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    still_running = tuple(sorted(still_running_values))
    terminated = tuple(sorted(targets - set(still_running)))
    return StopReport(
        owner=owner,
        matched=tuple(sorted(matched)),
        terminated=terminated,
        forced=tuple(sorted(forced)),
        remaining=still_running,
    )


def embedded_falkordb_pids(
    db_path: Path,
    *,
    processes: dict[int, ProcessRecord] | None = None,
) -> list[int]:
    """Find redislite servers whose config points at one exact RDB file."""

    target = db_path.expanduser().resolve(strict=False)
    table = process_table() if processes is None else processes
    matches: list[int] = []
    for record in table.values():
        if not record.argv or "redis-server" not in Path(record.argv[0]).name:
            continue
        config_paths: list[Path] = []
        for argument in record.argv[1:]:
            if argument.startswith("unixsocket:"):
                config_paths.append(
                    Path(argument.removeprefix("unixsocket:")).parent / "redis.config"
                )
            else:
                candidate = Path(argument)
                if candidate.is_file():
                    config_paths.append(candidate)
        for config_path in config_paths:
            try:
                values: dict[str, str] = {}
                for config_line in config_path.read_text(encoding="utf-8").splitlines():
                    tokens = shlex.split(config_line, comments=True)
                    if len(tokens) >= 2 and tokens[0] in {"dir", "dbfilename"}:
                        values[tokens[0]] = tokens[1]
                configured = (
                    Path(values["dir"]) / values["dbfilename"]
                ).expanduser().resolve(strict=False)
            except (KeyError, OSError, ValueError):
                continue
            if configured == target:
                matches.append(record.pid)
                break
    return sorted(matches)


def stop_embedded_falkordb(db_path: Path, *, timeout: float = 5.0) -> tuple[int, ...]:
    """Stop only embedded Redis processes owning ``db_path``."""

    pids = embedded_falkordb_pids(db_path)
    processes: list[psutil.Process] = []
    for pid in pids:
        try:
            process = psutil.Process(pid)
            process.terminate()
            processes.append(process)
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    _, remaining = (
        psutil.wait_procs(processes, timeout=max(0.0, timeout))
        if processes
        else ([], [])
    )
    for process in remaining:
        try:
            process.kill()
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    if remaining:
        psutil.wait_procs(remaining, timeout=min(2.0, max(0.0, timeout)))
    return tuple(pids)


__all__ = [
    "ProcessRecord",
    "StopReport",
    "SyncOwner",
    "embedded_falkordb_pids",
    "process_table",
    "stop_embedded_falkordb",
    "stop_sync_processes",
    "sync_processes",
]
