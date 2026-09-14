#!/usr/bin/env python3
"""G3 after-sync check: marker-node round-trip qua Rust MCP server.

Kiểm chứng sau-sync (tương đương Python-side đã establish ở phase-12):
dữ liệu graph MỚI (ghi sau khi sync index) phải phản ánh trong các tool
của Rust server.

Trình tự:
1. Discover primary `data.rdb` theo đúng discovery của `graph/runtime.rs`
   (`CORTEX_DATA_HOME|~/.cortext-harness`, `v1/instances`, primary-first)
   và backup file.
2. Boot embedded `redis-server` + `falkordb.so` (binary redislite của venv,
   cùng cách `runtime.rs` dùng) trên port free trỏ vào `data.rdb`, ghi
   ĐÚNG 1 marker node `(:Function)` vào default graph, rồi `SHUTDOWN SAVE`
   để persist vào `data.rdb` (mô phỏng sync writer; runtime Rust là
   read-only nên cần persist trước khi server boot).
3. Launch Rust `cortex-mcp` (debug binary như harness), đợi ready.
4. Gọi `get_symbol` + `search_functions` (fanout unscoped — quét cả default
   graph) và xác nhận marker xuất hiện.
5. Dừng server (embedded runtime tắt bằng `SHUTDOWN NOSAVE`) và restore
   `data.rdb` từ backup — môi trường về đúng trạng thái ban đầu.

Usage:
    python scripts/rust_mcp/after_sync_check.py [--port 8799]
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from compare_contract import (  # noqa: E402
    launch_rust_server,
    stop_rust_server,
    wait_for_server,
)
from compare_graph import _call_with_timeout  # noqa: E402


def data_root() -> Path:
    home = os.environ.get("CORTEX_DATA_HOME", "").strip()
    if home:
        home = Path(home).expanduser()
    else:
        home = Path.home() / ".cortext-harness"
    return home


def default_graph() -> str:
    return (
        os.environ.get("FALKORDB_GRAPH")
        or os.environ.get("FALKORDB_DATABASE")
        or "hyper_graph"
    )


def discover_primary_data_file() -> Path:
    """`discover_falkordb_data_files()` — phần primary instance."""
    instance = os.environ.get("CORTEX_STORAGE_INSTANCE", "").strip() or "default"
    candidate = (
        data_root()
        / "v1"
        / "instances"
        / instance
        / "falkordb"
        / "code"
        / "data.rdb"
    )
    if not candidate.is_file():
        raise SystemExit(f"primary falkordb data file not found: {candidate}")
    return candidate


def redislite_bin(name: str) -> Path:
    venv_bin = REPO_ROOT / ".venv" / "bin" / name
    if venv_bin.is_file():
        return venv_bin
    venv_lib = REPO_ROOT / ".venv" / "lib"
    for python in sorted(venv_lib.iterdir()):
        candidate = python / "site-packages" / "redislite" / "bin" / name
        if candidate.is_file():
            return candidate
    raise SystemExit(f"redislite binary not found: {name}")


def bind_free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class EmbeddedFalkor:
    """redis-server + falkordb.so cho phase ghi marker (same as runtime.rs)."""

    def __init__(self, data_rdb: Path) -> None:
        self.temp_dir = Path(
            tempfile.mkdtemp(prefix=f"after-sync-check-{os.getpid()}-")
        )
        self.port = bind_free_port()
        config = self.temp_dir / "redis.config"
        config.write_text(
            f"port {self.port}\nbind 127.0.0.1\n"
            f"dir {data_rdb.parent}\ndbfilename {data_rdb.name}\n"
            f'save ""\nappendonly no\n'
            f"logfile {self.temp_dir / 'redis.log'}\ndaemonize no\n",
            encoding="utf-8",
        )
        self.process = subprocess.Popen(
            [
                str(redislite_bin("redis-server")),
                str(config),
                "--loadmodule",
                str(redislite_bin("falkordb.so")),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._wait_ready()

    def _wait_ready(self, timeout: float = 60.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.cli("PING") == "PONG" and "GRAPH.LIST" in (
                self.cli("COMMAND", "COUNT", "GRAPH.LIST") or ""
            ) or self.cli("GRAPH.LIST") is not None:
                try:
                    if self.graph_query("RETURN 1") is not None:
                        return
                except RuntimeError:
                    pass
            time.sleep(0.25)
        raise SystemExit("embedded falkordb failed to boot (timeout)")

    def cli(self, *args: str) -> str | None:
        result = subprocess.run(
            [str(redislite_bin("redis-cli")), "-h", "127.0.0.1",
             "-p", str(self.port), *args],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        return result.stdout

    def graph_query(self, cypher: str) -> str | None:
        out = self.cli("GRAPH.RO_QUERY", default_graph(), cypher)
        if out is None:
            out = self.cli("GRAPH.QUERY", default_graph(), cypher)
        return out

    def graph_write(self, cypher: str) -> str | None:
        return self.cli("GRAPH.QUERY", default_graph(), cypher)

    def shutdown_save(self) -> None:
        # Persist the marker into data.rdb (sync-writer semantics).
        self.cli("SHUTDOWN", "SAVE")
        for _ in range(50):
            if self.process.poll() is not None:
                return
            time.sleep(0.1)
        self.process.kill()
        self.process.wait()

    def cleanup(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        shutil.rmtree(self.temp_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8799)
    args = parser.parse_args()

    data_rdb = discover_primary_data_file()
    backup = data_rdb.parent / "data.rdb.after-sync-backup"
    shutil.copy2(data_rdb, backup)
    print(f"[after-sync] backed up {data_rdb}")

    token = f"after_sync_marker_{os.getpid()}_{int(time.time())}"
    node_id = f"after_sync/{token}"
    embedded: EmbeddedFalkor | None = None
    process = None
    try:
        embedded = EmbeddedFalkor(data_rdb)
        written = embedded.graph_write(
            "CREATE (f:Function {"
            f"id: '{node_id}', name: '{token}', "
            f"file_path: 'after_sync/marker.py', "
            f"code: 'def {token}(): pass', summary: 'after-sync marker'"
            "}) RETURN f.id"
        )
        if written is None or token not in written:
            raise SystemExit(f"marker write failed: {written!r}")
        print(f"[after-sync] marker written: {node_id}")
        embedded.shutdown_save()
        embedded.cleanup()
        embedded = None

        process = launch_rust_server(args.port)
        if not wait_for_server(args.port, 60):
            raise SystemExit("rust server failed to start")

        get_symbol = _call_with_timeout(
            args.port, "get_symbol", {"node_id": node_id}
        )
        symbol_data = (get_symbol["structured_content"] or {}).get("data") or {}
        symbol_found = (
            not get_symbol["is_error"]
            and symbol_data.get("found") is True
            and token in str(symbol_data.get("parser_results") or "")
        )
        if not symbol_found:
            print(f"[after-sync] FAIL get_symbol: {str(symbol_data)[:400]}")
            return 1
        print("[after-sync] get_symbol reflects the marker node")

        search = _call_with_timeout(
            args.port, "search_functions", {"query": token}
        )
        search_data = (search["structured_content"] or {}).get("data") or {}
        search_hit = (
            not search["is_error"]
            and any(token in str(item) for item in search_data.get("ids") or [])
        )
        if not search_hit:
            print(f"[after-sync] FAIL search_functions: {str(search_data)[:400]}")
            return 1
        print("[after-sync] search_functions reflects the marker node")

        print("[after-sync] PASS — post-sync marker data is visible via Rust MCP")
        return 0
    finally:
        if process is not None:
            stop_rust_server(process)
        if embedded is not None:
            embedded.cleanup()
        shutil.copy2(backup, data_rdb)
        backup.unlink(missing_ok=True)
        print(f"[after-sync] restored {data_rdb}")


if __name__ == "__main__":
    raise SystemExit(main())
