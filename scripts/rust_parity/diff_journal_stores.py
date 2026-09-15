#!/usr/bin/env python3
"""Diff 2 journal SQLite store (Python-written vs Rust-replayed) — Track B2.

So theo tầng DB-state:
1. ``PRAGMA user_version`` (schema_version);
2. danh sách table (schema objects) + cột (tên/type/thứ tự);
3. row count từng table;
4. checksum từng row (chuẩn hoá cột biến thiên, so cell-by-cell theo PK).

Cột chuẩn hoá khi checksum:
- ``fencing_token``: random mỗi lần claim ở CẢ HAI store (NULL vẫn là NULL,
  giá trị set chuẩn hoá về ``<token>``) — semantics set/không-set vẫn so được;
- các cột ``*_at``/``*_until``: timestamp wall-clock của process ghi — khác
  giữa 2 lần chạy độc lập. ``--strict-time`` tắt chuẩn hoá này (dùng cho
  fixture regression clock cố định, chứng minh parity timestamp tuyệt đối).

Exit 0 khi khớp 100%; exit 1 khi lệch (in diff đầu tiên); exit 2 lỗi usage.

Chạy từ repo root:
    .venv/bin/python scripts/rust_parity/diff_journal_stores.py \
        python.sqlite3 rust.sqlite3 [--strict-time] [--max-diff 5]
"""

# === Phase-08 archive notice (2026-09-15) ===
# PY side archived at phase-08 cutover, fixtures = golden.
# Python analyzer entry points were retired at the phase-08 cutover;
# fixtures under tests/fixtures/ are now the golden reference.
# Do not attempt to re-run the Python side — tools/<lang>/<lang>_analyzer.py
# no longer exists. See plans/260915-analyzer-layer-rust-cutover/reports/phase08-cutover.md.
# === end archive notice ===


from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

VOLATILE_ALWAYS = frozenset({"fencing_token"})


def _connect(path: str | Path) -> sqlite3.Connection:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise SystemExit(f"store không tồn tại: {resolved}")
    connection = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _tables(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return sorted(str(row["name"]) for row in rows)


def _columns(connection: sqlite3.Connection, table: str) -> list[tuple[str, str, int]]:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return [(str(row["name"]), str(row["type"]), int(row["pk"])) for row in rows]


def _order_by(columns: list[tuple[str, str, int]]) -> str:
    primary_keys = sorted(
        (item for item in columns if item[2] > 0), key=lambda item: item[2]
    )
    if not primary_keys:
        return "rowid"
    return ", ".join(f'"{name}"' for name, _type, _pk in primary_keys)


def _normalize(column: str, value: object, strict_time: bool) -> object:
    if value is None:
        return None
    if column in VOLATILE_ALWAYS:
        return f"<{column}>"
    if not strict_time and (column.endswith("_at") or column.endswith("_until")):
        return f"<{column}>"
    return value


def diff_stores(
    python_path: str | Path, rust_path: str | Path, *, strict_time: bool, max_diff: int
) -> int:
    python_conn = _connect(python_path)
    rust_conn = _connect(rust_path)
    diffs: list[str] = []

    def record(message: str) -> None:
        if len(diffs) < max_diff:
            diffs.append(message)

    version_python = int(python_conn.execute("PRAGMA user_version").fetchone()[0])
    version_rust = int(rust_conn.execute("PRAGMA user_version").fetchone()[0])
    if version_python != version_rust:
        record(f"schema user_version: python={version_python} rust={version_rust}")

    tables_python = _tables(python_conn)
    tables_rust = _tables(rust_conn)
    if tables_python != tables_rust:
        record(f"schema tables: python={tables_python} rust={tables_rust}")

    total_rows = 0
    for table in sorted(set(tables_python) & set(tables_rust)):
        columns_python = _columns(python_conn, table)
        columns_rust = _columns(rust_conn, table)
        if [(name, typ) for name, typ, _ in columns_python] != [
            (name, typ) for name, typ, _ in columns_rust
        ]:
            record(f"schema columns[{table}]: python={columns_python} rust={columns_rust}")
            continue
        count_python = int(python_conn.execute(f"SELECT COUNT(*) FROM \"{table}\"").fetchone()[0])
        count_rust = int(rust_conn.execute(f"SELECT COUNT(*) FROM \"{table}\"").fetchone()[0])
        if count_python != count_rust:
            record(f"row count[{table}]: python={count_python} rust={count_rust}")
        order = _order_by(columns_python)
        names = [name for name, _type, _pk in columns_python]
        rows_python = python_conn.execute(f"SELECT * FROM \"{table}\" ORDER BY {order}").fetchall()
        rows_rust = rust_conn.execute(f"SELECT * FROM \"{table}\" ORDER BY {order}").fetchall()
        for index in range(max(count_python, count_rust)):
            if index >= count_python:
                record(f"row[{table}:{index}] chỉ có ở rust: {dict(rows_rust[index])}")
                continue
            if index >= count_rust:
                record(f"row[{table}:{index}] chỉ có ở python: {dict(rows_python[index])}")
                continue
            normalized_python = [
                _normalize(name, rows_python[index][name], strict_time) for name in names
            ]
            normalized_rust = [
                _normalize(name, rows_rust[index][name], strict_time) for name in names
            ]
            if normalized_python != normalized_rust:
                for position, name in enumerate(names):
                    if normalized_python[position] != normalized_rust[position]:
                        record(
                            f"row[{table}:{index}] column={name}: "
                            f"python={normalized_python[position]!r} "
                            f"rust={normalized_rust[position]!r}"
                        )
                        break
                continue
            total_rows += 1

    python_conn.close()
    rust_conn.close()

    if diffs:
        print(f"DIFF python={python_path} rust={rust_path}")
        for message in diffs:
            print(f"  {message}")
        if len(diffs) >= max_diff:
            print(f"  ... (dừng ở {max_diff} diff đầu tiên)")
        return 1
    print(
        f"OK python={python_path} rust={rust_path}: "
        f"schema v{version_python}, {len(tables_python)} tables, "
        f"{total_rows} rows khớp 100%"
        + (" (strict-time)" if strict_time else "")
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Diff 2 journal SQLite store")
    parser.add_argument("python_store")
    parser.add_argument("rust_store")
    parser.add_argument(
        "--strict-time",
        action="store_true",
        help="so timestamp tuyệt đối (dành cho fixture clock cố định)",
    )
    parser.add_argument("--max-diff", type=int, default=5)
    arguments = parser.parse_args()
    return diff_stores(
        arguments.python_store,
        arguments.rust_store,
        strict_time=arguments.strict_time,
        max_diff=arguments.max_diff,
    )


if __name__ == "__main__":
    sys.exit(main())
