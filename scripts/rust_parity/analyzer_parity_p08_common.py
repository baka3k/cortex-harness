#!/usr/bin/env python3
"""Shared helpers cho phase-08 overlay parity harnesses (spring/struts/servlet_jsp).

Mỗi overlay cần một base graph giống hệt trước khi chạy overlay: 2 graph
FalkorDB `p08_<overlay>_<tag>_py`/`_rs` được seed bằng BASE JAVA ANALYZER
Python (journal-shadow env như orchestrator), sau đó overlay Python chạy trên
graph _py và overlay Rust trên graph _rs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code-tiny"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools.graph.driver.falkordb_driver import FalkorDBDriver  # noqa: E402
from tools.graph.journal.config import configure_journal_env  # noqa: E402
import dual_write_diff  # noqa: E402
from dual_write_diff import MASKED_PROPS, diff_dump, dump_graph  # noqa: E402

ENGINE_INTERNAL_PROPS = MASKED_PROPS | {"_start_id", "_end_id"}

PY_BIN = REPO / ".venv" / "bin" / "python"

# macOS: /tmp và /var là symlink → python `_secure_makedirs` (servlet_jsp
# cache/preview) từ chối path có symlink component. Scratch parity đặt dưới
# REPO/.cache (đường dẫn thật).
SCRATCH_ROOT = REPO / ".cache" / "p08_overlays"


def scratch_dir(name: str) -> Path:
    path = SCRATCH_ROOT / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}"
    path.mkdir(parents=True, exist_ok=True)
    return path
RUST_TARGET = REPO / "rust" / "target" / "release"
REPORTS = REPO / "plans" / "260913-2130-rust-full-migration" / "reports"

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def analyzer_env() -> dict:
    env = dict(os.environ)
    for key in [
        "QDRANT_CODE_PATH",
        "QDRANT_COLLECTION",
        "FALKORDB_URI",
        "FALKORDB_GRAPH",
        "FALKORDB_PATH",
        "PROJECT_ID",
        "PROJECT_NAME",
        "PROJECT_LANGUAGE",
        "PROJECT_REPO",
        "PROJECT_BUILD_SYSTEM",
        "CODE_EMBEDDING_MODEL",
        "CODE_EMBEDDING_MODEL_PATH",
        "JINA_MODEL_PATH",
        "EMBED_DEVICE",
        "EMBED_BATCH_SIZE",
        "MAX_EMBED_CHARS",
        "NEO4J_URI",
        "NEO4J_USER",
        "NEO4J_PASS",
        "NEO4J_DB",
        "NEO4J_STATE_PATH",
        "LADYBUG_PATH",
        "LADYBUG_GRAPH",
        "CORTEX_DISABLE_GRAPH",
        "CORTEX_EXTRA_IGNORE_DIRS",
        "QDRANT_CACHE_DIR",
        "REQUIRE_NEO4J",
    ]:
        env.pop(key, None)
    env["PYTHONPATH"] = str(REPO / "code-tiny")
    return env


def clean_graph(driver: FalkorDBDriver, graph: str) -> None:
    import asyncio

    asyncio.run(driver.execute_query("MATCH (n) DETACH DELETE n", {}, graph))


def seed_base_graph(driver: FalkorDBDriver, root: Path, project_id: str, graph: str,
                    host: str, port: int) -> str:
    """Chạy base java analyzer Python lên graph (journal shared-shadow như
    orchestrator) — cả hai graph đều seed bằng backend Python để start giống hệt."""
    import asyncio

    clean_graph(driver, graph)
    cmd = [
        str(PY_BIN), str(REPO / "code-tiny" / "tools" / "java" / "java_analyzer.py"),
        "--root", str(root),
        "--config", "/dev/null",
        "--project-id", project_id,
        "--graph-provider", "falkordb",
        "--falkordb-uri", f"{host}:{port}",
        "--falkordb-graph", graph,
        "--disable-message-scan",
        "--verbose",
    ]
    env = analyzer_env()
    scratch = REPO / ".cache" / "p08_overlays_journal"
    scratch.mkdir(parents=True, exist_ok=True)
    configure_journal_env(
        env,
        root=str(root),
        project_id=project_id,
        parser="java",
        source_revision=f"parity-{graph}",
        source_snapshot=f"parity-{graph}",
        physical_target=f"falkordb:{host}:{port}/{graph}",
        cache_dir=str(scratch),
        mode=env.get("CORTEX_GRAPH_JOURNAL_MODE", "shared-shadow"),
        generation=f"parity-{graph}",
    )
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
    if proc.returncode != 0:
        raise RuntimeError(
            f"java seed failed for {graph}:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
        )
    # Dọn journal env — overlay không chạy với journal của java lane.
    import asyncio as _asyncio

    async def drain() -> None:
        # shared-shadow: finalize không cần — shadow không block graph writes.
        return None

    _asyncio.run(drain())
    return proc.stdout


def run_overlay(cmd: list[str], env: dict | None = None, timeout: int = 900) -> str:
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout,
        env=env if env is not None else analyzer_env(),
        cwd=str(REPO),
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"overlay failed ({cmd[0]}):\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}"
        )
    return proc.stdout


def dump_graph_masked(driver: FalkorDBDriver, graph: str) -> dict:
    dump = dump_graph(driver, graph)

    def strip(props: dict) -> dict:
        return {k: v for k, v in props.items() if k not in ENGINE_INTERNAL_PROPS}

    return {
        "nodes": {k: strip(v) for k, v in dump["nodes"].items()},
        "edges": {k: strip(v) for k, v in dump["edges"].items()},
    }


def compare_graphs(driver: FalkorDBDriver, graph_py: str, graph_rust: str,
                   label: str, report: list[str]) -> None:
    py_dump = dump_graph_masked(driver, graph_py)
    rust_dump = dump_graph_masked(driver, graph_rust)
    diff = diff_dump(py_dump, rust_dump)
    diff_total = sum(len(v) for k, v in diff.items() if not k.startswith("_"))
    check(f"{label}: graph diff rỗng ngoài mask", diff_total == 0,
          f"nodes py={len(py_dump['nodes'])} rust={len(rust_dump['nodes'])} diff={diff_total}")
    report.append(
        f"\n### {label}\n\n- nodes: py={len(py_dump['nodes'])} rust={len(rust_dump['nodes'])}\n"
        f"- edges: py={len(py_dump['edges'])} rust={len(rust_dump['edges'])}\n"
        f"- diff_total: **{diff_total}**\n"
    )
    if diff_total:
        report.append("```json\n")
        report.append(json.dumps(diff, indent=2, ensure_ascii=True, default=str)[:16000])
        report.append("\n```\n")


def compare_files(path_py: Path, path_rs: Path, label: str, report: list[str]) -> None:
    py_bytes = path_py.read_bytes() if path_py.exists() else b""
    rs_bytes = path_rs.read_bytes() if path_rs.exists() else b""
    check(f"{label}: artifact byte-identical", py_bytes == rs_bytes,
          f"py={path_py} ({len(py_bytes)}B) rs={path_rs} ({len(rs_bytes)}B)")
    report.append(f"- {label} artifact: py={len(py_bytes)}B rs={len(rs_bytes)}B "
                  f"{'=PASS=' if py_bytes == rs_bytes else '=FAIL='}\n")


def header(title: str, testdata: Path, rust_bin: Path, extra: list[str]) -> list[str]:
    return [
        f"# Phase 08 — {title} parity (python vs rust)",
        "",
        f"- chạy: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- testdata: `{testdata.relative_to(REPO)}`",
        f"- rust bin: `{rust_bin}`",
        *extra,
        f"- mask: `{sorted(MASKED_PROPS)}` + `_start_id`/`_end_id`",
    ]
