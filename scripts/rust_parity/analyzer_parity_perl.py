#!/usr/bin/env python3
"""Phase 05 parity gate — Perl analyzer Python vs Rust.

Dual-run `tools/perl/perl_analyzer.py` và `analyzer-perl` trên 2 graph
FalkorDB riêng (FULL + incremental), dump và so exact ngoài mask chuẩn.

Lưu ý invocation phía Python: orchestrator luôn configure journal env
(`incremental_sync.configure_journal_env`) trước khi spawn analyzer, và
writer `_require_call_project_scope` lấy `project_id` cho call rows từ
`journal_config.metadata.project_id`. Harness tái tạo đúng env đó bằng
chính `configure_journal_env` (mode `shared-shadow`).

Usage (từ repo root):
    .venv/bin/python scripts/rust_parity/analyzer_parity_perl.py
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
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code-tiny"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools.graph.driver.falkordb_driver import FalkorDBDriver  # noqa: E402
from dual_write_diff import MASKED_PROPS, diff_dump, dump_graph  # noqa: E402

# `_start_id`/`_end_id` là internal node-id do engine cấp trên TỪNG graph —
# phụ thuộc lịch sử create/delete của graph đó (high-water mark không reset
# sau DETACH DELETE). Cùng nhóm volatile với `_graph_id`/`_edge_id`/`_src`/
# `_dst` của mask chuẩn nên mask thêm ở đây để so sánh không nhiễu.
MASKED_PROPS.update({"_start_id", "_end_id"})

try:
    from tools.graph.journal.config import configure_journal_env  # noqa: E402
except ImportError:  # pragma: no cover — code-tiny luôn có module này
    configure_journal_env = None  # type: ignore[assignment]

STOCK = Path("/Users/user/baka3k/stock")
TESTDATA = REPO / "tests" / "fixtures" / "perl-application"
PY_BIN = REPO / ".venv" / "bin" / "python"
PY_ANALYZER = REPO / "code-tiny" / "tools" / "perl" / "perl_analyzer.py"
RUST_BIN = REPO / "rust" / "target" / "release" / "analyzer-perl"
REPORT_PATH = (
    REPO / "plans" / "260913-2130-rust-full-migration" / "reports"
    / "phase05-perl-parity.md"
)

SCAN_RE = re.compile(
    r"\[SCAN_RESULT\] parser=perl files=(\d+) functions=(\d+) classes=(\d+) "
    r"vectors=(\d+) vector_status=(\w+)"
)
CLEANUP_RE = re.compile(
    r"\[cleanup\]\[graph\] deleted_nodes=(\d+) deleted_unknown_functions=(\d+)"
)

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def analyzer_env(falkordb_uri: str, project_id: str, root: Path) -> dict:
    """Env sạch cho analyzer Python + journal env như orchestrator."""
    import os

    env = dict(os.environ)
    for key in [
        "QDRANT_CODE_PATH",
        "QDRANT_COLLECTION",
        "FALKORDB_URI",
        "FALKORDB_GRAPH",
        "FALKORDB_PATH",
        "PROJECT_ID",
        "PROJECT_NAME",
        "PROJECT_REPO",
        "CORTEX_EXTRA_IGNORE_DIRS",
        "PERL_ANALYZER_CACHE_DIR",
        "CORTEX_DISABLE_GRAPH",
        "CORTEX_GRAPH_JOURNAL_MODE",
        "CORTEX_GRAPH_JOURNAL_PATH",
        "CORTEX_GRAPH_JOURNAL_METADATA",
    ]:
        env.pop(key, None)
    env["FALKORDB_URI"] = falkordb_uri
    if configure_journal_env is not None:
        # Orchestrator chạy analyzer con với journal metadata; writer dùng
        # metadata.project_id làm fallback cho call rows.
        configure_journal_env(
            env,
            root=str(root),
            project_id=project_id,
            parser="perl",
            source_revision="parity-perl",
            source_snapshot="parity-perl",
            physical_target=f"redis://{falkordb_uri}",
            cache_dir=tempfile.mkdtemp(prefix="p05_perl_journal_"),
            mode="shared-shadow",
            generation=f"p05-perl-{time.strftime('%H%M%S')}",
        )
    return env


def run_py(root: Path, project_id: str, graph: str, host: str, port: int,
           incremental: tuple[Path, Path] | None = None,
           extra_env: dict | None = None) -> str:
    cmd = [
        str(PY_BIN), str(PY_ANALYZER),
        "--root", str(root),
        "--project-id", project_id,
        "--graph-provider", "falkordb",
        "--falkordb-uri", f"{host}:{port}",
        "--falkordb-graph", graph,
        "--disable-message-scan",
        "--verbose",
    ]
    if incremental:
        cmd.append("--incremental")
        changed, deleted = incremental
        if changed:
            cmd.extend(["--changed-files-manifest", str(changed)])
        if deleted:
            cmd.extend(["--deleted-files-manifest", str(deleted)])
    env = analyzer_env(f"{host}:{port}", project_id, root)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900,
                          env=env)
    if proc.returncode != 0:
        raise RuntimeError(
            f"py analyzer failed:\n{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}"
        )
    return proc.stdout


def run_rust(root: Path, project_id: str, graph: str, host: str, port: int,
             incremental: tuple[Path, Path] | None = None) -> str:
    cmd = [
        str(RUST_BIN),
        "--root", str(root),
        "--project-id", project_id,
        "--graph-provider", "falkordb",
        "--falkordb-uri", f"{host}:{port}",
        "--falkordb-graph", graph,
        "--disable-message-scan",
        "--verbose",
    ]
    if incremental:
        cmd.append("--incremental")
        changed, deleted = incremental
        if changed:
            cmd.extend(["--changed-files-manifest", str(changed)])
        if deleted:
            cmd.extend(["--deleted-files-manifest", str(deleted)])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        raise RuntimeError(
            f"rust analyzer failed:\n{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}"
        )
    return proc.stdout


def scan_result_of(log: str) -> str:
    matches = list(SCAN_RE.finditer(log))
    if not matches:
        raise RuntimeError(f"[SCAN_RESULT] not found:\n{log[-1200:]}")
    return matches[-1].group(0)


def cleanup_counts_of(log: str) -> tuple[int, int]:
    matches = CLEANUP_RE.findall(log)
    return tuple(int(v) for v in matches[-1]) if matches else (0, 0)  # type: ignore[return-value]


def clean_graph(driver: FalkorDBDriver, graph: str) -> None:
    import asyncio

    asyncio.run(driver.execute_query("MATCH (n) DETACH DELETE n", {}, graph))


def compare_graphs(driver: FalkorDBDriver, graph_py: str, graph_rust: str,
                   label: str, report: list[str]) -> None:
    py_dump = dump_graph(driver, graph_py)
    rust_dump = dump_graph(driver, graph_rust)
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
        report.append(json.dumps(diff, indent=2, ensure_ascii=True, default=str)[:12000])
        report.append("\n```\n")


def dual(driver: FalkorDBDriver, root: Path, tag: str, host: str, port: int,
         report: list[str], incremental: tuple[Path, Path] | None = None,
         clean: bool = True) -> None:
    graph_py = f"p05_perl_{tag}_py"
    graph_rust = f"p05_perl_{tag}_rs"
    if clean:
        clean_graph(driver, graph_py)
        clean_graph(driver, graph_rust)
    py_log = run_py(root, "parity_perl", graph_py, host, port, incremental)
    rust_log = run_rust(root, "parity_perl", graph_rust, host, port, incremental)
    py_scan, rust_scan = scan_result_of(py_log), scan_result_of(rust_log)
    check(f"{tag}: [SCAN_RESULT] byte-identical", py_scan == rust_scan,
          f"py={py_scan!r} rust={rust_scan!r}")
    report.append(f"\n### scan_result {tag}\n\n- py: `{py_scan}`\n- rust: `{rust_scan}`\n")
    if incremental:
        py_cleanup, rust_cleanup = cleanup_counts_of(py_log), cleanup_counts_of(rust_log)
        check(f"{tag}: cleanup counts khớp", py_cleanup == rust_cleanup,
              f"py={py_cleanup} rust={rust_cleanup}")
        report.append(f"- cleanup: py={py_cleanup} rust={rust_cleanup}\n")
    compare_graphs(driver, graph_py, graph_rust, tag.upper(), report)


def scenario_incremental(driver: FalkorDBDriver, host: str, port: int,
                         report: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="p05_perl_inc_") as tmp:
        workdir = Path(tmp) / "corpus"
        workdir.mkdir()
        for relative in ("bin/app.pl", "lib/App/Model.pm", "lib/App/Util.pm",
                         "lib/App/Broken.pm", "t/model.t"):
            target = workdir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(TESTDATA / relative, target)

        # Bước 1: FULL seed vào graph `inc_seed` (chưa incremental).
        dual(driver, workdir, "inc_seed", host, port, report)

        # Mutate: sửa lib/App/Util.pm (thêm sub), thêm file, xoá file.
        with (workdir / "lib" / "App" / "Util.pm").open("a", encoding="utf-8") as handle:
            handle.write(
                "\nsub parity_helper2 {\n    my ($input) = @_;\n"
                "    return helper() . $input;\n}\n\n1;\n"
            )
        added = workdir / "parity_added.pl"
        added.write_text(
            "package App::Added;\n\nuse strict;\nuse App::Util;\n\n"
            "sub run_added {\n    return App::Util::helper();\n}\n\n1;\n",
            encoding="utf-8",
        )
        (workdir / "t" / "model.t").unlink()

        changed_manifest = Path(tmp) / "changed.json"
        deleted_manifest = Path(tmp) / "deleted.json"
        changed_manifest.write_text(
            json.dumps({"files": ["lib/App/Util.pm", "parity_added.pl"]}) + "\n",
            encoding="utf-8",
        )
        deleted_manifest.write_text(
            json.dumps({"files": ["t/model.t"]}) + "\n", encoding="utf-8"
        )
        # Bước 2: incremental TRÊN CÙNG graph (không clean) — cleanup phải
        # DELETE dữ liệu cũ của file changed/deleted ⇒ counts ≠ 0 trivial.
        dual(driver, workdir, "inc_seed", host, port, report,
             incremental=(changed_manifest, deleted_manifest), clean=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--rust-bin", default=str(RUST_BIN))
    args = parser.parse_args()

    rust_bin = Path(args.rust_bin)
    report = [
        "# Phase 05 — perl analyzer parity (python vs rust)",
        "",
        f"- chạy: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- testdata: `{TESTDATA.relative_to(REPO)}` (dedicated fixture corpus: "
        "packages, use/require, our/my/local vars, method/qualified/direct/coderef/eval refs, "
        "1 file broken để exercise error-node diagnostics)",
        f"- mask: `{sorted(MASKED_PROPS)}`",
        "- python invocation: journal env `shared-shadow` như orchestrator "
        "(`configure_journal_env`) — writer `_require_call_project_scope` lấy "
        "project_id của call rows từ journal metadata.",
        "- stock scenario: bỏ qua — stock corpus không có file .pl/.pm/.t.",
    ]
    driver = FalkorDBDriver(host=args.host, port=args.port)

    dual(driver, TESTDATA, "testdata_full", args.host, args.port, report)
    scenario_incremental(driver, args.host, args.port, report)

    report.append(
        f"\n## Kết luận\n\n- FAILURES: {FAILURES if FAILURES else 'không có — PASS toàn bộ'}\n"
    )
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"\nreport → {REPORT_PATH.relative_to(REPO)}")
    if FAILURES:
        print(f"FAILED: {FAILURES}")
        return 1
    print("ALL GATES PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
