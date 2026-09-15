#!/usr/bin/env python3
"""Snapshot một Ladybug store của harness về storage-version 43 (engine 0.19.0) để xem bằng gdotv.

Store thật của harness được pin Ladybug 0.20.4 (storage version 47), trong khi
gdotv nhúng engine 0.19.0 (storage version 43) nên không mở được file gốc.
Script này KHÔNG đụng vào store thật — nó:

  1. mở store thật READ-ONLY bằng ladybug >= 0.20 (venv của harness),
  2. EXPORT DATABASE ra parquet trong thư mục tạm,
  3. bootstrap một venv riêng có ladybug==0.19.0 (nếu chưa có),
  4. IMPORT DATABASE vào một file .lbug MỚI (bản copy để gdotv mở),
  5. đối chiếu tổng node/rel giữa hai bên, fail nếu lệch.

Không chạy ngược lại: store thật 0.20.4 đọc được cả v43, nên lần sync sau
engine 0.20.4 sẽ tự nâng bản copy lên 47 nếu bạn trỏ nhầm — copy chỉ để xem.

Usage:
    .venv/bin/python scripts/export_lbug_view.py [source.lbug] [-o dest.lbug]
        [--overwrite] [--keep-export]

Ví dụ:
    .venv/bin/python scripts/export_lbug_view.py            # code lane mặc định
    .venv/bin/python scripts/export_lbug_view.py .../doc.lbug/hyper_graph
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

DEFAULT_SOURCE = (
    Path.home()
    / ".cortext-harness/v1/instances/cortex/ladybug/code/code.lbug/hyper_graph"
)
WORK_ROOT = Path.home() / ".cortext-harness/graph-view"
VIEW_ENGINE_VERSION = "0.19.0"  # khớp engine Ladybug nhúng trong gdotv (storage v43)
REQUIRED_EXPORT_MIN = (0, 20)  # engine đọc v47 phải >= 0.20


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def ladybug_version() -> tuple[int, int, int]:
    try:
        import ladybug
    except ImportError:
        fail(
            "interpreter hiện tại chưa có package ladybug — chạy bằng venv của harness: "
            ".venv/bin/python scripts/export_lbug_view.py"
        )
    return tuple(int(part) for part in ladybug.__version__.split(".")[:3])


def run_cypher(python: str, store: Path, statements: list[str]) -> list[list[str]]:
    code = (
        "import json,sys\n"
        "from ladybug import Database, Connection\n"
        "db = Database(sys.argv[1], read_only=True)\n"
        "conn = Connection(db)\n"
        "out = []\n"
        "for stmt in json.loads(sys.argv[2]):\n"
        "    result = conn.execute(stmt)\n"
        "    out.append([str(row[0]) for row in result])\n"
        "print(json.dumps(out))\n"
    )
    proc = subprocess.run(
        [python, "-c", code, str(store), json.dumps(statements)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        fail(f"query {store} thất bại:\n{proc.stderr.strip()}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def count_totals(python: str, store: Path) -> tuple[str, str]:
    rows = run_cypher(
        python,
        store,
        ["MATCH (n) RETURN count(n)", "MATCH ()-[e]->() RETURN count(e)"],
    )
    return rows[0][0], rows[1][0]


def ensure_view_venv(venv_dir: Path) -> Path:
    python = venv_dir / "bin/python"
    if python.exists():
        proc = subprocess.run(
            [str(python), "-c", "import ladybug; print(ladybug.__version__)"],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0 and proc.stdout.strip().startswith(VIEW_ENGINE_VERSION):
            return python
        print(f"venv {venv_dir} sai version ({proc.stdout.strip()}), cài lại...")
    print(f"bootstrap venv ladybug=={VIEW_ENGINE_VERSION} tại {venv_dir} ...")
    venv.create(str(venv_dir), with_pip=True)
    proc = subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet", f"ladybug=={VIEW_ENGINE_VERSION}"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        fail(f"pip install ladybug=={VIEW_ENGINE_VERSION} thất bại:\n{proc.stderr.strip()}")
    return python


def import_into_view(view_python: str, dest: Path, export_dir: Path) -> None:
    code = (
        "import sys\n"
        "from ladybug import Database, Connection\n"
        "db = Database(sys.argv[1])\n"
        "conn = Connection(db)\n"
        "conn.execute(\"IMPORT DATABASE '\" + sys.argv[2] + \"'\")\n"
        "print('import ok')\n"
    )
    proc = subprocess.run(
        [str(view_python), "-c", code, str(dest), str(export_dir)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        fail(f"IMPORT DATABASE vào {dest} thất bại:\n{proc.stderr.strip()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", nargs="?", default=str(DEFAULT_SOURCE))
    parser.add_argument("-o", "--dest", default=None)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--keep-export", action="store_true")
    args = parser.parse_args()

    export_version = ladybug_version()
    if export_version < REQUIRED_EXPORT_MIN:
        fail(f"ladybug {export_version} không đọc được store v47 — cần >= 0.20")

    source = Path(args.source).expanduser().resolve()
    if not source.is_file():
        fail(f"store không tồn tại (hoặc là thư mục — engine cấm): {source}")
    if source.name.startswith("."):
        fail(f"{source} trông không phải store Ladybug")

    dest = (
        Path(args.dest).expanduser().resolve()
        if args.dest
        else WORK_ROOT / f"{source.name}.v{VIEW_ENGINE_VERSION.replace('.', '')}.lbug"
    )
    if dest == source or dest.parent == source.parent and dest.name == source.name:
        fail("dest phải khác store thật")
    if dest.exists() and not args.overwrite:
        fail(f"đã có bản copy tại {dest} — dùng --overwrite để ghi đè")

    export_engine = ".".join(map(str, export_version))
    print(f"store thật : {source}  (engine export: ladybug {export_engine}, READ-ONLY)")
    print(f"bản copy   : {dest}  (engine view: ladybug {VIEW_ENGINE_VERSION}, storage v43)")

    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    # engine tự tạo thư mục export và từ chối nếu nó đã tồn tại
    export_dir = WORK_ROOT / f"lbug_export_{os.urandom(4).hex()}"
    try:
        print("1/4 export parquet từ store thật ...")
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys\n"
                "from ladybug import Database, Connection\n"
                "db = Database(sys.argv[1], read_only=True)\n"
                "conn = Connection(db)\n"
                "conn.execute(\"EXPORT DATABASE '\" + sys.argv[2] + \"'\")\n"
                "print('export ok')\n",
                str(source),
                str(export_dir),
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            fail(f"EXPORT DATABASE thất bại:\n{proc.stderr.strip()}")

        print(f"2/4 bootstrap engine view {VIEW_ENGINE_VERSION} ...")
        view_python = ensure_view_venv(WORK_ROOT / f".venv-lbug-{VIEW_ENGINE_VERSION}")

        print("3/4 import vào bản copy ...")
        if dest.exists():
            dest.unlink()
        dest.parent.mkdir(parents=True, exist_ok=True)
        import_into_view(str(view_python), dest, export_dir)

        print("4/4 đối chiếu node/rel ...")
        src_nodes, src_rels = count_totals(sys.executable, source)
        dst_nodes, dst_rels = count_totals(str(view_python), dest)
        if (src_nodes, src_rels) != (dst_nodes, dst_rels):
            fail(
                f"lệch dữ liệu: store thật {src_nodes}/{src_rels} vs copy {dst_nodes}/{dst_rels} "
                f"— xoá {dest} rồi chạy lại"
            )
    except BaseException:
        if dest.exists():
            dest.unlink(missing_ok=True)
        raise
    finally:
        if args.keep_export:
            print(f"giữ thư mục export: {export_dir}")
        else:
            shutil.rmtree(export_dir, ignore_errors=True)

    wal = Path(str(dest) + ".wal")
    print()
    print("DONE — mở file này trong gdotv (chọn Ladybug, bật Read Only):")
    print(f"  {dest}")
    print(f"  nodes={src_nodes} rels={src_rels} (khớp store thật)")
    if wal.exists():
        print(f"  (kèm WAL: {wal} — copy cả hai nếu di chuyển)")


if __name__ == "__main__":
    main()
