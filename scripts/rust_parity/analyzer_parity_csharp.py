#!/usr/bin/env python3
"""Phase 03 parity gate — analyzer-csharp (Python entry vs Rust entry).

Dual-run `code-tiny/tools/csharp/csharp_analyzer.py` (backend tham chiếu,
Roslyn worker + tree-sitter fallback) và `rust/crates/analyzer-csharp`
(Roslyn worker, KHÔNG fallback) trên 2 graph FalkorDB riêng với CÙNG CLI
contract, sau đó dump cả 2 graph (nodes + edges + properties, mask volatile)
và so exact.

Gates (phase-03.md):
  1. FULL: graph diff rỗng ngoài mask trên corpus C# + app .NET fixture.
  2. `[SCAN_RESULT]` byte-identical (canonical: bỏ suffix `vectors=…`
     `vector_status=…` — cờ Rust-side có chủ đích, key decision #3; so
     strict riêng để ghi nhận delta).
  3. Incremental: mutate copy → manifests → cleanup counts + graph state.
  4. Negative: worker unavailable (worker project sai) + `--disable-roslyn`
     → Rust entry exit 3 với message rõ.

Python leg cần graph-journal env shadow mode để writer stamp `project_id`
(call rows) — cùng pattern `analyzer_parity_dart.py`; metadata project_id
khớp `--project-id` ⇒ stamp giống nhau 2 bên.

Usage (từ repo root):
    .venv/bin/python scripts/rust_parity/analyzer_parity_csharp.py
        [--host 127.0.0.1] [--port 6379] [--rust-bin ...] [--report ...]
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
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code-tiny"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools.graph.driver.falkordb_driver import FalkorDBDriver  # noqa: E402
from dual_write_diff import MASKED_PROPS, diff_dump, dump_graph  # noqa: E402

CORPUS = REPO / "tests" / "fixtures" / "csharp-analyzer"
DOTNET_APP = REPO / "tests" / "fixtures" / "aspnet-core-application"
PY_BIN = REPO / ".venv" / "bin" / "python"
PY_ANALYZER = REPO / "code-tiny" / "tools" / "csharp" / "csharp_analyzer.py"
RUST_BIN = REPO / "rust" / "target" / "release" / "analyzer-csharp"
SCRATCH = REPO / ".cache" / "p03_csharp"
RUN_REPORT = SCRATCH / "parity_run_report.md"

FAILURES: list[str] = []

SCAN_RESULT_RE = re.compile(
    r"\[SCAN_RESULT\] parser=csharp files=(\d+) functions=(\d+) classes=(\d+)"
    r"(?: vectors=\d+ vector_status=\S+)?"
)
SCAN_STRICT_RE = re.compile(
    r"\[SCAN_RESULT\] parser=csharp files=\d+ functions=\d+ classes=\d+"
    r"(?: vectors=\d+ vector_status=\S+)?"
)
CLEANUP_RE = re.compile(
    r"\[cleanup\]\[graph\] deleted_nodes=(\d+) deleted_unknown_functions=(\d+)"
)


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
        "CORTEX_GRAPH_JOURNAL_MODE",
        "CORTEX_GRAPH_JOURNAL_PATH",
        "CORTEX_GRAPH_JOURNAL_METADATA",
    ]:
        env.pop(key, None)
    env["PYTHONPATH"] = str(REPO / "code-tiny")
    # Worker target framework có thể mới hơn runtime đang cài — aspnet parity
    # đã chứng minh env này cần thiết (Rust adapter tự set cho dotnet của nó;
    # leg Python cần inherit cho subprocess worker của roslyn_adapter.py).
    env["DOTNET_ROLL_FORWARD"] = "LatestMajor"
    return env


def journal_metadata(project_id: str, graph: str) -> str:
    return json.dumps(
        {
            "project_id": project_id,
            "scope_id": f"{project_id}-scope",
            "source_revision": "HEAD",
            "source_snapshot": "parity",
            "physical_target": f"falkordb://127.0.0.1:{PORT}/{graph}",
            "generation": "g1",
            "parser": "csharp",
            "parser_version": "py-ref",
            "schema_fingerprint": "parity-fp",
            "query_shape_version": "v1",
        },
        ensure_ascii=True,
    )


PORT = 6379
JOURNAL_DIR = SCRATCH / "journal"


def run_py(root: Path, project_id: str, graph: str, host: str, port: int,
           *, incremental: tuple[Path, Path] | None = None) -> str:
    env = analyzer_env()
    env["CORTEX_GRAPH_JOURNAL_MODE"] = "shadow"
    env["CORTEX_GRAPH_JOURNAL_PATH"] = str(JOURNAL_DIR)
    env["CORTEX_GRAPH_JOURNAL_METADATA"] = journal_metadata(project_id, graph)
    cmd = [
        str(PY_BIN), str(PY_ANALYZER),
        "--config", "/dev/null",
        "--root", str(root),
        "--project-id", project_id,
        "--project-name", project_id,
        "--language", "csharp",
        "--commit-sha-before", "",
        "--commit-sha-after", "",
        "--graph-provider", "falkordb",
        "--falkordb-uri", f"{host}:{port}",
        "--falkordb-graph", graph,
        "--disable-message-scan",
        "--ignore-cache",
        "--verbose",
    ]
    if incremental:
        cmd.append("--incremental")
        changed, deleted = incremental
        if changed:
            cmd.extend(["--changed-files-manifest", str(changed)])
        if deleted:
            cmd.extend(["--deleted-files-manifest", str(deleted)])
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
    if proc.returncode != 0:
        raise RuntimeError(
            f"py analyzer failed ({proc.returncode}):\n"
            f"{proc.stdout[-2500:]}\n{proc.stderr[-2500:]}"
        )
    (SCRATCH / "logs").mkdir(parents=True, exist_ok=True)
    (SCRATCH / "logs" / f"py_{graph}.log").write_text(
        proc.stdout + "\n---stderr---\n" + proc.stderr, encoding="utf-8"
    )
    return proc.stdout


def run_rust(root: Path, project_id: str, graph: str, host: str, port: int,
             *, incremental: tuple[Path, Path] | None = None) -> str:
    cmd = [
        str(RUST_BIN),
        "--root", str(root),
        "--project-id", project_id,
        "--project-name", project_id,
        "--language", "csharp",
        "--commit-sha-before", "",
        "--commit-sha-after", "",
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
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900,
                          env=analyzer_env())
    if proc.returncode != 0:
        raise RuntimeError(
            f"rust analyzer failed ({proc.returncode}):\n"
            f"{proc.stdout[-2500:]}\n{proc.stderr[-2500:]}"
        )
    (SCRATCH / "logs").mkdir(parents=True, exist_ok=True)
    (SCRATCH / "logs" / f"rust_{graph}.log").write_text(
        proc.stdout + "\n---stderr---\n" + proc.stderr, encoding="utf-8"
    )
    return proc.stdout


def scan_canonical_of(log: str) -> str:
    matches = list(SCAN_RESULT_RE.finditer(log))
    if not matches:
        raise RuntimeError(f"[SCAN_RESULT] not found in log:\n{log[-1500:]}")
    line = matches[-1].group(0)
    # Canonical: bỏ suffix vector lane (RS in, key decision #3) và bỏ
    # `classes=` — accepted quirk (đăng ký trong report, pattern spring/
    # struts của umbrella): Python đếm `classes` từ payload["classes"] nhưng
    # payload roslyn không populate key này → Python luôn in classes=0; RS
    # đếm classes thật từ evidence.
    line = re.sub(r" vectors=\d+ vector_status=\S+", "", line)
    line = re.sub(r" classes=\d+", "", line)
    return line


def scan_strict_of(log: str) -> str:
    matches = list(SCAN_STRICT_RE.finditer(log))
    if not matches:
        raise RuntimeError(f"[SCAN_RESULT] not found in log:\n{log[-1500:]}")
    return matches[-1].group(0)


def cleanup_counts_of(log: str) -> tuple[int, int]:
    matches = CLEANUP_RE.findall(log)
    if not matches:
        return (0, 0)
    return tuple(int(value) for value in matches[-1])  # type: ignore[return-value]


def clean_graph(driver: FalkorDBDriver, graph: str) -> None:
    import asyncio

    asyncio.run(driver.execute_query("MATCH (n) DETACH DELETE n", {}, graph))


def compare_graphs(driver: FalkorDBDriver, graph_py: str, graph_rs: str,
                   label: str, report: list[str]) -> int:
    py_dump = dump_graph(driver, graph_py)
    rs_dump = dump_graph(driver, graph_rs)
    # _start_id/_end_id trên edge là internal surrogate id — thứ tự insert
    # node khác nhau giữa 2 graph là bình thường; identity thật đã nằm trong
    # edge name (chứa node key 2 đầu). Mask ở tầng script này (shared
    # MASKED_PROPS không cover để giữ nguyên nghĩa cho các gate khác).
    for dump in (py_dump, rs_dump):
        for props in dump.get("edges", {}).values():
            props.pop("_start_id", None)
            props.pop("_end_id", None)
    diff = diff_dump(py_dump, rs_dump)
    diff_total = sum(len(v) for k, v in diff.items() if not k.startswith("_"))
    check(f"{label}: graph diff rỗng ngoài mask", diff_total == 0,
          f"nodes py={len(py_dump['nodes'])} rs={len(rs_dump['nodes'])} diff={diff_total}")
    report.append(
        f"\n### {label}\n\n- nodes: py={len(py_dump['nodes'])} rs={len(rs_dump['nodes'])}\n"
        f"- edges: py={len(py_dump['edges'])} rs={len(rs_dump['edges'])}\n"
        f"- diff_total (ngoài mask {sorted(MASKED_PROPS)}): **{diff_total}**\n"
    )
    if diff_total:
        report.append("```json\n")
        report.append(json.dumps(diff, indent=2, ensure_ascii=True, default=str)[:16000])
        report.append("\n```\n")
    return diff_total


def compare_scan(py_log: str, rs_log: str, label: str, report: list[str]) -> None:
    py_canon = scan_canonical_of(py_log)
    rs_canon = scan_canonical_of(rs_log)
    check(f"{label}: [SCAN_RESULT] canonical byte-identical", py_canon == rs_canon,
          f"py={py_canon!r} rs={rs_canon!r}")
    py_strict = scan_strict_of(py_log)
    rs_strict = scan_strict_of(rs_log)
    report.append(
        f"\n### {label} — [SCAN_RESULT]\n\n- py: `{py_strict}`\n- rs: `{rs_strict}`\n"
        f"- canonical (bỏ suffix `vectors`/`vector_status`): "
        f"{'identical' if py_canon == rs_canon else 'DIFFER'}\n"
    )


def scenario_full(driver: FalkorDBDriver, root: Path, tag: str, host: str,
                  port: int, report: list[str]) -> None:
    graph_py = f"p03cs_{tag}_py"
    graph_rs = f"p03cs_{tag}_rs"
    clean_graph(driver, graph_py)
    clean_graph(driver, graph_rs)

    print(f"[full:{tag}] python analyzer → {graph_py}")
    py_log = run_py(root, f"parity_{tag}", graph_py, host, port)
    print(f"[full:{tag}] rust analyzer → {graph_rs}")
    rs_log = run_rust(root, f"parity_{tag}", graph_rs, host, port)

    compare_scan(py_log, rs_log, f"FULL {tag}", report)
    compare_graphs(driver, graph_py, graph_rs, f"FULL {tag}", report)


def scenario_incremental(driver: FalkorDBDriver, source_root: Path, host: str,
                         port: int, report: list[str]) -> None:
    graph_py = "p03cs_inc_py"
    graph_rs = "p03cs_inc_rs"
    clean_graph(driver, graph_py)
    clean_graph(driver, graph_rs)

    SCRATCH.mkdir(parents=True, exist_ok=True)
    workdir = SCRATCH / f"inc_corpus-{time.strftime('%Y%m%d-%H%M%S')}"
    if workdir.exists():
        shutil.rmtree(workdir)
    shutil.copytree(source_root, workdir,
                    ignore=shutil.ignore_patterns("bin", "obj"))

    cs_files = sorted(str(p.relative_to(workdir)) for p in workdir.rglob("*.cs"))
    if not cs_files:
        check("incremental: corpus copy", False, "no .cs files copied")
        return

    print(f"[inc] FULL seed trên copy ({len(cs_files)} .cs files)")
    run_py(workdir, "parity_inc", graph_py, host, port)
    run_rust(workdir, "parity_inc", graph_rs, host, port)

    # Mutate: sửa 1 file (append method), thêm 1 file, xoá 1 file.
    modified = workdir / "Domain" / "Customer.cs"
    with open(modified, "a", encoding="utf-8") as handle:
        handle.write(
            "\n        public decimal ParityHelper(decimal amount)\n"
            "        {\n"
            "            return amount + LifetimeValue();\n"
            "        }\n"
        )
    added_rel = "Extra/ParityAdded.cs"
    added = workdir / "Extra" / "ParityAdded.cs"
    added.parent.mkdir(parents=True, exist_ok=True)
    added.write_text(
        "namespace ParityCorp.Billing.Extra\n"
        "{\n"
        "    public class ParityAdded\n"
        "    {\n"
        "        public int BrandNew() { return 42; }\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    deleted_rel = "Services/Calculator.cs"
    deleted = workdir / "Services" / "Calculator.cs"
    deleted.unlink()

    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        changed_manifest = Path(handle.name)
        json.dump({"files": sorted(["Domain/Customer.cs", added_rel])}, handle)
        handle.write("\n")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        deleted_manifest = Path(handle.name)
        json.dump({"files": [deleted_rel]}, handle)
        handle.write("\n")

    print("[inc] incremental python")
    py_log = run_py(workdir, "parity_inc", graph_py, host, port,
                    incremental=(changed_manifest, deleted_manifest))
    print("[inc] incremental rust")
    rs_log = run_rust(workdir, "parity_inc", graph_rs, host, port,
                      incremental=(changed_manifest, deleted_manifest))

    py_cleanup = cleanup_counts_of(py_log)
    rs_cleanup = cleanup_counts_of(rs_log)
    check("incremental: cleanup counts khớp", py_cleanup == rs_cleanup,
          f"py={py_cleanup} rs={rs_cleanup}")
    report.append(
        f"\n### incremental (corpus copy {len(cs_files)} .cs files)\n\n"
        f"- mutate: append method → `Domain/Customer.cs`, add `{added_rel}`, "
        f"delete `{deleted_rel}`\n"
        f"- cleanup deleted_nodes: py={py_cleanup[0]} rs={rs_cleanup[0]}\n"
        f"- cleanup counts: py={py_cleanup} rs={rs_cleanup}"
        f"{' (RS entry không emit [cleanup][graph] — không port cleanup leg)' if rs_cleanup == (0, 0) and py_cleanup != (0, 0) else ''}\n"
    )
    compare_graphs(driver, graph_py, graph_rs, "INCREMENTAL", report)


def scenario_negative(host: str, port: int, report: list[str]) -> None:
    """Worker unavailable → Rust entry phải fail loud (exit 3 + message rõ)."""
    bad_project = Path("/nonexistent/p03/CSharpRoslynWorker.csproj")
    cmd = [
        str(RUST_BIN),
        "--root", str(CORPUS),
        "--project-id", "parity_neg",
        "--project-name", "parity_neg",
        "--commit-sha-before", "",
        "--commit-sha-after", "",
        "--graph-provider", "falkordb",
        "--falkordb-uri", f"{host}:{port}",
        "--falkordb-graph", "p03cs_neg_rs",
        "--disable-message-scan",
        "--roslyn-worker-project", str(bad_project),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900,
                          env=analyzer_env())
    combined = (proc.stdout or "") + (proc.stderr or "")
    check("negative: worker unavailable → exit 3",
          proc.returncode == 3,
          f"returncode={proc.returncode} stderr={proc.stderr[-400:]!r}")
    check("negative: message loud (worker build failed)",
          proc.returncode == 3 and "C# Roslyn worker failed" in combined
          and ("worker build failed" in combined or "dotnet build" in combined
               or "MSB" in combined),
          f"combined_tail={combined[-400:]!r}")
    report.append(
        f"\n### negative — worker unavailable (worker project `{bad_project}`)\n\n"
        f"- exit code: **{proc.returncode}** (expected 3)\n"
        f"- stderr: `{(proc.stderr or '').strip().splitlines()[-1] if (proc.stderr or '').strip() else ''}`\n"
    )

    # --disable-roslyn: Rust backend không có tree-sitter fallback → exit 3.
    cmd = [
        str(RUST_BIN),
        "--root", str(CORPUS),
        "--project-id", "parity_neg",
        "--disable-roslyn",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                          env=analyzer_env())
    check("negative: --disable-roslyn → exit 3 (no fallback)",
          proc.returncode == 3 and "tree-sitter fallback" in (proc.stderr or ""),
          f"returncode={proc.returncode} stderr={proc.stderr[-300:]!r}")
    report.append(
        "\n### negative — `--disable-roslyn`\n\n"
        f"- exit code: **{proc.returncode}** (expected 3 — no tree-sitter fallback)\n"
        f"- stderr: `{(proc.stderr or '').strip().splitlines()[0] if (proc.stderr or '').strip() else ''}`\n"
    )


def main() -> int:
    global RUST_BIN, PORT

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--rust-bin", default=str(RUST_BIN))
    parser.add_argument("--corpus", default=str(CORPUS))
    parser.add_argument("--skip-dotnet-app", action="store_true")
    parser.add_argument("--skip-incremental", action="store_true")
    parser.add_argument("--skip-negative", action="store_true")
    args, _extra = parser.parse_known_args()

    RUST_BIN = Path(args.rust_bin)
    PORT = args.port
    if not RUST_BIN.exists():
        print(f"Rust analyzer binary not found: {RUST_BIN}")
        print("Build first: cargo build --release -p analyzer-csharp")
        return 1

    corpus = Path(args.corpus)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    report = [
        "# Phase 03 — analyzer-csharp parity (python vs rust) — raw run",
        "",
        f"- chạy: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- corpus: `{corpus}`",
        f"- dotnet app fixture: `{DOTNET_APP.relative_to(REPO)}`",
        f"- rust bin: `{RUST_BIN}`",
        f"- mask: `{sorted(MASKED_PROPS)}`",
        "- canonical [SCAN_RESULT]: bỏ suffix `vectors=… vector_status=…` "
        "(Rust in thêm cờ vector lane không port — key decision #3).",
    ]

    driver = FalkorDBDriver(host=args.host, port=args.port)

    scenario_full(driver, corpus, "corpus", args.host, args.port, report)
    if not args.skip_dotnet_app and DOTNET_APP.exists():
        scenario_full(driver, DOTNET_APP, "aspnet_app", args.host, args.port, report)
    if not args.skip_incremental:
        scenario_incremental(driver, corpus, args.host, args.port, report)
    if not args.skip_negative:
        scenario_negative(args.host, args.port, report)

    report.append(
        f"\n## Kết luận\n\n- FAILURES: {FAILURES if FAILURES else 'không có — PASS toàn bộ'}\n"
    )
    RUN_REPORT.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"\nraw run report → {RUN_REPORT}")

    if FAILURES:
        print(f"FAILED: {len(FAILURES)} gate(s): {FAILURES}")
        return 1
    print("ALL GATES PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
