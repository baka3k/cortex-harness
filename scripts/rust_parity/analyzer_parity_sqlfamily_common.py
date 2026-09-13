#!/usr/bin/env python3
"""Shared helpers cho phase-08 SQL-family parity harnesses (mybatis đã có
riêng; database_schema / sql / plsql dùng module này).

Ghi chú quan trọng: `analyzer_parity_p08_common.py` là của nhóm overlay khác
(spring/struts/servlet_jsp) — module này tách riêng để không đụng file chung.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys_path_hint = str(REPO / "code-tiny")

from tools.graph.driver.falkordb_driver import FalkorDBDriver  # noqa: E402
from tools.graph.journal.config import configure_journal_env  # noqa: E402
from dual_write_diff import MASKED_PROPS, diff_dump, dump_graph  # noqa: E402

ENGINE_INTERNAL_PROPS = MASKED_PROPS | {"_start_id", "_end_id"}

PY_BIN = REPO / ".venv" / "bin" / "python"
RUST_DIR = REPO / "rust" / "target" / "release"

SCAN_RE = re.compile(
    r"\[SCAN_RESULT\] parser=(\S+) files=(\d+) functions=(\d+) classes=(\d+)"
)
CLEANUP_RE = re.compile(
    r"\[cleanup\]\[graph\] deleted_nodes=(\d+) deleted_unknown_functions=(\d+)"
)
# Full line gồm dict `graph={'nodes': N, 'relationships': N, 'deleted': N}`
# — deleted count là cleanup gate của overlay này.
OVERLAY_RE = re.compile(
    r"\[overlay\] database=(\w+) objects=(\d+) relationships=(\d+) "
    r"graph=\{'nodes': (\d+), 'relationships': (\d+), 'deleted': (\d+)\}"
)


def check(name: str, ok: bool, detail: str = "", failures: list | None = None) -> bool:
    ok = bool(ok)
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok and failures is not None:
        failures.append(name)
    return ok


def analyzer_env() -> dict:
    env = dict(os.environ)
    for key in [
        "QDRANT_CODE_PATH", "QDRANT_COLLECTION", "FALKORDB_URI", "FALKORDB_PATH",
        "FALKORDB_GRAPH", "PROJECT_ID", "PROJECT_NAME", "PROJECT_LANGUAGE",
        "PROJECT_REPO", "PROJECT_BUILD_SYSTEM", "GIT_COMMIT_SHA_BEFORE",
        "GIT_COMMIT_SHA_AFTER", "NEO4J_URI", "NEO4J_USER", "NEO4J_PASS",
        "NEO4J_DB", "NEO4J_STATE_PATH", "LADYBUG_PATH", "LADYBUG_GRAPH",
        "CORTEX_DISABLE_GRAPH", "CORTEX_EXTRA_IGNORE_DIRS", "QDRANT_CACHE_DIR",
        "MESSAGE_OUTPUT_DIR", "MESSAGE_QDRANT_COLLECTION", "CALL_SCOPE",
        "CODE_EMBEDDING_MODEL", "EMBED_DEVICE",
    ]:
        env.pop(key, None)
    return env


def journal_env(env: dict, root: Path, project_id: str, graph: str, host: str,
                port: int, parser: str, journal_dir: Path | None) -> dict:
    """CALLS rows không project_id — writer đọc journal metadata fallback
    (`configure_journal_env` mode=shadow như orchestrator production)."""
    scratch = Path(journal_dir) if journal_dir else (
        REPO / ".cache" / "p08_sqlfamily_journal")
    scratch = scratch / f"j_{graph}"
    scratch.mkdir(parents=True, exist_ok=True)
    configure_journal_env(
        env,
        root=str(root),
        project_id=project_id,
        parser=parser,
        source_revision=f"parity-{graph}",
        source_snapshot=f"parity-{graph}",
        physical_target=f"falkordb:{host}:{port}/{graph}",
        cache_dir=str(scratch),
        mode=env.get("CORTEX_GRAPH_JOURNAL_MODE", "shared-shadow"),
        generation=f"parity-{graph}",
    )
    return env


def run_py(analyzer: Path, root: Path, project_id: str, graph: str, host: str,
           port: int, incremental: tuple[Path, Path] | None = None,
           journal_dir: Path | None = None, parser: str = "sql",
           extra: tuple[str, ...] = (), config: bool = True) -> str:
    cmd = [
        str(PY_BIN), str(analyzer),
        "--root", str(root),
    ]
    if config:
        cmd += ["--config", "/dev/null"]
    cmd += [
        "--project-id", project_id,
        "--graph-provider", "falkordb",
        "--falkordb-uri", f"{host}:{port}",
        "--falkordb-graph", graph,
        "--disable-message-scan",
        "--verbose",
    ]
    cmd.extend(extra)
    if incremental:
        cmd.append("--incremental")
        changed, deleted = incremental
        if changed:
            cmd.extend(["--changed-files-manifest", str(changed)])
        if deleted:
            cmd.extend(["--deleted-files-manifest", str(deleted)])
    env = journal_env(analyzer_env(), root, project_id, graph, host, port,
                      parser, journal_dir)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
    if proc.returncode != 0:
        raise RuntimeError(
            f"py analyzer failed (rc={proc.returncode}):\n"
            f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
        )
    return proc.stdout


def run_rust(binary: Path, root: Path, project_id: str, graph: str, host: str,
             port: int, incremental: tuple[Path, Path] | None = None,
             extra: tuple[str, ...] = ()) -> str:
    cmd = [
        str(binary),
        "--root", str(root),
        "--config", "/dev/null",
        "--project-id", project_id,
        "--graph-provider", "falkordb",
        "--falkordb-uri", f"{host}:{port}",
        "--falkordb-graph", graph,
        "--disable-message-scan",
        "--verbose",
    ]
    cmd.extend(extra)
    if incremental:
        cmd.append("--incremental")
        changed, deleted = incremental
        if changed:
            cmd.extend(["--changed-files-manifest", str(changed)])
        if deleted:
            cmd.extend(["--deleted-files-manifest", str(deleted)])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=analyzer_env())
    if proc.returncode != 0:
        raise RuntimeError(
            f"rust analyzer failed (rc={proc.returncode}):\n"
            f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
        )
    return proc.stdout


def scan_result_of(log: str) -> str:
    matches = list(SCAN_RE.finditer(log))
    if not matches:
        raise RuntimeError(f"[SCAN_RESULT] not found:\n{log[-1200:]}")
    return matches[-1].group(0)


def cleanup_counts_of(log: str) -> tuple[int, int] | None:
    matches = CLEANUP_RE.findall(log)
    return tuple(int(v) for v in matches[-1]) if matches else None  # type: ignore[return-value]


def overlay_line_of(log: str) -> str:
    matches = list(OVERLAY_RE.finditer(log))
    if not matches:
        raise RuntimeError(f"[overlay] line not found:\n{log[-1200:]}")
    return matches[-1].group(0)


def clean_graph(driver: FalkorDBDriver, graph: str) -> None:
    import asyncio

    asyncio.run(driver.execute_query("MATCH (n) DETACH DELETE n", {}, graph))


def dump_graph_masked(driver: FalkorDBDriver, graph: str) -> dict:
    dump = dump_graph(driver, graph)

    def strip(props: dict) -> dict:
        return {k: v for k, v in props.items() if k not in ENGINE_INTERNAL_PROPS}

    return {
        "nodes": {k: strip(v) for k, v in dump["nodes"].items()},
        "edges": {k: strip(v) for k, v in dump["edges"].items()},
    }


def compare_graphs(driver: FalkorDBDriver, graph_py: str, graph_rust: str,
                   label: str, report: list[str], failures: list[str]) -> None:
    py_dump = dump_graph_masked(driver, graph_py)
    rust_dump = dump_graph_masked(driver, graph_rust)
    diff = diff_dump(py_dump, rust_dump)
    diff_total = sum(len(v) for k, v in diff.items() if not k.startswith("_"))
    check(f"{label}: graph diff rỗng ngoài mask", diff_total == 0,
          f"nodes py={len(py_dump['nodes'])} rust={len(rust_dump['nodes'])} diff={diff_total}",
          failures)
    report.append(
        f"\n### {label}\n\n- nodes: py={len(py_dump['nodes'])} rust={len(rust_dump['nodes'])}\n"
        f"- edges: py={len(py_dump['edges'])} rust={len(rust_dump['edges'])}\n"
        f"- diff_total: **{diff_total}**\n"
    )
    if diff_total:
        report.append("```json\n")
        report.append(json.dumps(diff, indent=2, ensure_ascii=True, default=str)[:16000])
        report.append("\n```\n")


def write_report(report: list[str], failures: list[str], report_path: Path, notes: str) -> int:
    report.append(
        f"\n## Kết luận\n\n- FAILURES: {failures if failures else 'không có — PASS toàn bộ'}\n"
    )
    report.append(notes)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"\nreport → {report_path.relative_to(REPO)}")
    if failures:
        print(f"FAILED: {failures}")
        return 1
    print("ALL GATES PASS")
    return 0
