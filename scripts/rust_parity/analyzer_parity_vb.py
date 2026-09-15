#!/usr/bin/env python3
"""Phase 07 parity gate — VB analyzer family (vbnet/vb6/vba/vbscript) py vs rust.

Dual-run `code-tiny/tools/vb/<variant>_analyzer.py` và 4 binary
`analyzer-{vbnet,vb6,vba,vbscript}` (crate analyzer-vb) trên 2 graph FalkorDB
riêng (FULL + incremental với changed/deleted manifests), dump và so exact
ngoài mask chuẩn.

Parser note: `vb_common.parse_vb_file` là parser LINE/REGEX thuần dùng chung 4
dialect; vbnet engine `auto` còn thử Roslyn worker subprocess (key decision #8
— plane C# giữ nguyên là process ngoài, Rust port invoke cùng worker). Trên
máy gate này chỉ có .NET 10 (worker target net9) nên CẢ HAI phía rơi vào
regex fallback — parity vẫn đúng vì Rust mirror `_worker_dll_path` +
fallback semantics của Python.

Usage (từ repo root):
    .venv/bin/python scripts/rust_parity/analyzer_parity_vb.py [--skip-stock]
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
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code-tiny"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools.graph.driver.falkordb_driver import FalkorDBDriver  # noqa: E402
from tools.graph.journal.config import configure_journal_env  # noqa: E402
from dual_write_diff import MASKED_PROPS, diff_dump, dump_graph  # noqa: E402

STOCK = Path("/Users/hieplq1.aip/baka3k/stock")
TESTDATA = REPO / "tests" / "fixtures" / "vb-analyzer"
PY_BIN = REPO / ".venv" / "bin" / "python"
RUST_BIN = REPO / "rust" / "target" / "release"
REPORT_PATH = (
    REPO / "plans" / "260913-2130-rust-full-migration" / "reports"
    / "phase07-vb-parity.md"
)

# variant → (dialect, rust bin, python entry script, added-file content class)
VARIANTS = {
    "net": {
        "dialect": "vbnet",
        "rust_bin": "analyzer-vbnet",
        "py_script": "vbnet_analyzer.py",
        "fixture_dir": "vbnet",
        "main_file": "App.vb",
        "delete_file": "Util.vb",
        "added_file": ("Extra.vb", "vbnet"),
        "main_append": (
            "\n' appended for parity incremental test\n"
            "Public Function ParityAdd(a As Integer, b As Integer) As Integer\n"
            "    Dim total As Integer = a + b\n"
            "    Return total\n"
            "End Function\n"
        ),
    },
    "6": {
        "dialect": "vb6",
        "rust_bin": "analyzer-vb6",
        "py_script": "vb6_analyzer.py",
        "fixture_dir": "vb6",
        "main_file": "Module1.bas",
        "delete_file": "Form1.frm",
        "added_file": ("Extra.bas", "vb6"),
        "main_append": (
            "\n' appended for parity incremental test\n"
            "Public Sub ParityLog(message As String)\n"
            "    Debug.Print message\n"
            "End Sub\n"
        ),
    },
    "vba": {
        "dialect": "vba",
        "rust_bin": "analyzer-vba",
        "py_script": "vba_analyzer.py",
        "fixture_dir": "vba",
        "main_file": "PayrollMacros.bas",
        "delete_file": "Invoicing.bas",
        "added_file": ("Extra.bas", "vba"),
        "main_append": (
            "\n' appended for parity incremental test\n"
            "Public Sub ParitySave()\n"
            "    ThisWorkbook.Worksheets(\"Parity\").Range(\"A1\").Value = Now\n"
            "End Sub\n"
        ),
    },
    "vbscript": {
        "dialect": "vbscript",
        "rust_bin": "analyzer-vbscript",
        "py_script": "vbscript_analyzer.py",
        "fixture_dir": "vbscript",
        "main_file": "classic.vbs",
        "delete_file": "page.asp",
        "added_file": ("Extra.vbs", "vbscript"),
        "main_append": (
            "\n' appended for parity incremental test\n"
            "Sub ParityMain()\n"
            "    WScript.Echo AddNumbers(1, 2)\n"
            "End Sub\n"
        ),
    },
}

ADDED_FILE_CONTENT = {
    "vbnet": (
        "' added for parity incremental test\n"
        "Namespace Acme.Extra\n"
        "    Public Class Extra\n"
        "        Public Sub Go()\n"
        "            Dim value As Integer = 1\n"
        "            Console.WriteLine(value)\n"
        "        End Sub\n"
        "    End Class\n"
        "End Namespace\n"
    ),
    "vb6": (
        "Attribute VB_Name = \"Extra\"\n"
        "' added for parity incremental test\n"
        "Public Sub ExtraGo()\n"
        "    Debug.Print \"extra\"\n"
        "End Sub\n"
    ),
    "vba": (
        "Attribute VB_Name = \"Extra\"\n"
        "' added for parity incremental test (Office patterns keep vba)\n"
        "Public Sub ExtraGo()\n"
        "    ThisWorkbook.Worksheets(\"Extra\").Range(\"A1\") = 1\n"
        "End Sub\n"
    ),
    "vbscript": (
        "' added for parity incremental test\n"
        "Sub ExtraMain()\n"
        "    WScript.Echo \"extra\"\n"
        "End Sub\n"
    ),
}

SCAN_RE_TEMPLATE = (
    r"\[SCAN_RESULT\] parser={dialect} files=(\d+) functions=(\d+) classes=(\d+)"
)
CLEANUP_RE = re.compile(
    r"\[cleanup\]\[graph\] deleted_nodes=(\d+) deleted_unknown_functions=(\d+)"
)

FAILURES: list[str] = []
RESULTS: dict[str, dict[str, str]] = {}



def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)
    if "/" in name and ":" in name:
        gate_key, gate_name = name.split(":", 1)
        variant_key, tag = gate_key.split("/", 1)
        RESULTS.setdefault(f"{variant_key.lower()}/{tag.lower()}", {})[gate_name.strip()] = status


def analyzer_env() -> dict:
    env = dict(os.environ)
    for key in [
        "QDRANT_CODE_PATH",
        "QDRANT_COLLECTION",
        "NEO4J_URI",
        "NEO4J_USER",
        "NEO4J_PASS",
        "NEO4J_DB",
        "FALKORDB_URI",
        "FALKORDB_PATH",
        "FALKORDB_GRAPH",
        "PROJECT_ID",
        "PROJECT_NAME",
        "PROJECT_LANGUAGE",
        "PROJECT_REPO",
        "PROJECT_BUILD_SYSTEM",
        "GIT_COMMIT_SHA_BEFORE",
        "GIT_COMMIT_SHA_AFTER",
        "CORTEX_EXTRA_IGNORE_DIRS",
        "MESSAGE_OUTPUT_DIR",
        "MESSAGE_QDRANT_COLLECTION",
        "CODE_EMBEDDING_MODEL",
        "EMBED_DEVICE",
        "EMBED_BATCH_SIZE",
        "MAX_EMBED_CHARS",
        "CORTEX_DISABLE_GRAPH",
        # Roslyn worker env defaults — gate phải deterministic
        "VBNET_ROSLYN_WORKER_PROJECT",
        "VBNET_ROSLYN_TIMEOUT_SEC",
        "VBNET_ROSLYN_WORKSPACE_TIMEOUT_MS",
        "VBNET_ROSLYN_FILE_TIMEOUT_MS",
        "LADYBUG_PATH",
        "LADYBUG_GRAPH",
    ]:
        env.pop(key, None)
    # Máy gate chỉ có .NET 10 runtime nhưng RoslynVbWorker target net9 ⇒
    # roll-forward để worker CHẠY được — vbnet gate exercise đúng plane
    # Roslyn (subprocess) thay vì cả 2 phía cùng rơi regex fallback. Nếu
    # worker vẫn không chạy được, CẢ HAI phía fallback regex (parity vẫn đúng).
    env["DOTNET_ROLL_FORWARD"] = "LatestMajor"
    return env


def journal_env(env: dict, root: Path, project_id: str, graph: str, host: str, port: int) -> dict:
    """CALLS project-scope contract — như analyzer_parity_go.py: Python
    `build_call_graph` dựng call rows KHÔNG project_id nên writer đọc journal
    metadata làm fallback (`configure_journal_env(mode="shadow")`); Rust supply
    `project_id` tường minh trên rows — cùng giá trị, cùng graph."""
    journal_cache = Path(tempfile.mkdtemp(prefix="p07_vb_journal_"))
    configure_journal_env(
        env,
        root=str(root),
        project_id=project_id,
        parser="vb",
        source_revision="parity",
        source_snapshot="parity",
        physical_target=f"falkordb://{host}:{port}/{graph}",
        cache_dir=str(journal_cache),
        mode="shadow",
    )
    return env


def _common_flags(root: Path, project_id: str, graph: str, host: str, port: int) -> list[str]:
    return [
        "--config", "/dev/null",
        "--root", str(root),
        "--project-id", project_id,
        "--project-name", project_id,
        "--graph-provider", "falkordb",
        "--falkordb-uri", f"{host}:{port}",
        "--falkordb-graph", graph,
        "--disable-message-scan",
        # Parse cache Python-side là plane riêng nhưng làm engine vbnet "dính"
        # theo lần parse đầu (regex vs roslyn) giữa các run — --ignore-cache
        # (orchestrator contract) để py luôn parse mới; Rust nhận và bỏ qua.
        "--ignore-cache",
        # Python default parallel-workers=4 thu payloads theo thread-completion
        # order (nondeterministic) ⇒ write order đổi giữa các run ⇒ internal
        # FalkorDB node ids (_start_id/_end_id) khác dù graph content giống
        # hệt. Chạy sequential (workers=1) cho CẢ HAI phía để so sánh strict.
        "--parallel-workers", "1",
        "--verbose",
    ]


def run_py(variant: dict, root: Path, project_id: str, graph: str, host: str, port: int,
           incremental: tuple[Path, Path] | None = None) -> str:
    cmd = [str(PY_BIN), str(REPO / "code-tiny" / "tools" / "vb" / variant["py_script"])]
    cmd.extend(_common_flags(root, project_id, graph, host, port))
    if incremental:
        cmd.append("--incremental")
        changed, deleted = incremental
        if changed:
            cmd.extend(["--changed-files-manifest", str(changed)])
        if deleted:
            cmd.extend(["--deleted-files-manifest", str(deleted)])
    env = journal_env(analyzer_env(), root, project_id, graph, host, port)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
    if proc.returncode != 0:
        raise RuntimeError(
            f"py analyzer failed (rc={proc.returncode}):\n"
            f"{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}"
        )
    return proc.stdout


def run_rust(variant: dict, root: Path, project_id: str, graph: str, host: str, port: int,
             incremental: tuple[Path, Path] | None = None) -> str:
    rust_bin = RUST_BIN / variant["rust_bin"]
    cmd = [str(rust_bin)]
    cmd.extend(_common_flags(root, project_id, graph, host, port))
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
            f"{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}"
        )
    return proc.stdout


def scan_result_of(log: str, dialect: str) -> str:
    scan_re = re.compile(SCAN_RE_TEMPLATE.format(dialect=re.escape(dialect)))
    matches = list(scan_re.finditer(log))
    if not matches:
        raise RuntimeError(f"[SCAN_RESULT] not found:\n{log[-1200:]}")
    return matches[-1].group(0)


def cleanup_counts_of(log: str) -> tuple[int, int] | None:
    matches = CLEANUP_RE.findall(log)
    return tuple(int(v) for v in matches[-1]) if matches else None  # type: ignore[return-value]


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


def dual(driver: FalkorDBDriver, variant: dict, variant_key: str, root: Path, tag: str,
         host: str, port: int, report: list[str],
         incremental: tuple[Path, Path] | None = None,
         clean: bool = True, project_id: str | None = None,
         graph_tag: str | None = None) -> None:
    """graph_tag: tên graph dùng chung khi incremental phải chạy TRÊN graph
    đã seed (clean=False) để cleanup xoá node thật thay vì graph rỗng."""
    dialect = variant["dialect"]
    project_id = project_id or f"parity_vb_{variant_key}"
    graph_tag = graph_tag or tag
    graph_py = f"p07_vb_{variant_key}_{graph_tag}_py"
    graph_rust = f"p07_vb_{variant_key}_{graph_tag}_rs"
    if clean:
        clean_graph(driver, graph_py)
        clean_graph(driver, graph_rust)
    py_log = run_py(variant, root, project_id, graph_py, host, port, incremental)
    rust_log = run_rust(variant, root, project_id, graph_rust, host, port, incremental)
    py_scan, rust_scan = scan_result_of(py_log, dialect), scan_result_of(rust_log, dialect)
    check(f"{variant_key}/{tag}: [SCAN_RESULT] byte-identical", py_scan == rust_scan,
          f"py={py_scan!r} rust={rust_scan!r}")
    report.append(f"\n### scan_result {variant_key}/{tag}\n\n- py: `{py_scan}`\n- rust: `{rust_scan}`\n")
    if incremental:
        py_cleanup, rust_cleanup = cleanup_counts_of(py_log), cleanup_counts_of(rust_log)
        check(f"{variant_key}/{tag}: cleanup line xuất hiện ở cả 2 log",
              py_cleanup is not None and rust_cleanup is not None,
              f"py={py_cleanup} rust={rust_cleanup}")
        check(f"{variant_key}/{tag}: cleanup counts khớp", py_cleanup == rust_cleanup,
              f"py={py_cleanup} rust={rust_cleanup}")
        report.append(f"- cleanup: py={py_cleanup} rust={rust_cleanup}\n")
    compare_graphs(driver, graph_py, graph_rust, f"{variant_key.upper()}/{tag.upper()}", report)


def scenario_incremental(driver: FalkorDBDriver, variant: dict, variant_key: str,
                         host: str, port: int, report: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix=f"p07_vb_{variant_key}_inc_") as tmp:
        workdir = Path(tmp) / "corpus"
        shutil.copytree(TESTDATA / variant["fixture_dir"], workdir)

        # FULL seed (dual tự clean 2 graph trước khi chạy)
        dual(driver, variant, variant_key, workdir, "inc_seed", host, port, report)

        # Mutate: sửa main (thêm function), thêm file, xoá file.
        main_path = workdir / variant["main_file"]
        main_path.write_text(main_path.read_text(encoding="utf-8") + variant["main_append"],
                             encoding="utf-8")
        added_name, added_kind = variant["added_file"]
        (workdir / added_name).write_text(ADDED_FILE_CONTENT[added_kind], encoding="utf-8")
        (workdir / variant["delete_file"]).unlink()

        changed_manifest = Path(tmp) / "changed.json"
        deleted_manifest = Path(tmp) / "deleted.json"
        changed_manifest.write_text(
            json.dumps({"files": [variant["main_file"], added_name]}) + "\n",
            encoding="utf-8",
        )
        deleted_manifest.write_text(
            json.dumps({"files": [variant["delete_file"]]}) + "\n", encoding="utf-8"
        )
        # KHÔNG clean giữa seed và incremental — incremental chạy TRÊN graph
        # seed (graph_tag="inc_seed") nên cleanup xoá node thật.
        dual(driver, variant, variant_key, workdir, "inc_run", host, port, report,
             incremental=(changed_manifest, deleted_manifest), clean=False,
             graph_tag="inc_seed")


def scenario_ambiguous_const(driver: FalkorDBDriver, host: str, port: int,
                             report: list[str]) -> None:
    """Upstream quirk: bare `Const X = ...` sinh Constant + Variable trùng id
    ⇒ label inference của write_all (CẢ Py lẫn Rust) raise
    'cannot infer target_label'. Gate: CẢ HAI backend fail với cùng error."""
    with tempfile.TemporaryDirectory(prefix="p07_vb_amb_") as tmp:
        workdir = Path(tmp) / "corpus"
        workdir.mkdir()
        (workdir / "Amb.vb").write_text(
            "Public Class Amb\n"
            "    Const Limit = 10\n"
            "End Class\n",
            encoding="utf-8",
        )
        variant = dict(VARIANTS["net"])
        variant["rust_bin"] = "analyzer-vbnet"
        cmd_py = [
            str(PY_BIN), str(REPO / "code-tiny" / "tools" / "vb" / "vbnet_analyzer.py"),
        ]
        cmd_py.extend(_common_flags(workdir, "parity_vb_amb", "p07_vb_amb_py", host, port))
        # Quirk chỉ tồn tại trên regex path (Roslyn không emit Variable row
        # "Const X") — pin engine=regex để scenario deterministic bất kể
        # dotnet worker có chạy được hay không.
        cmd_py.extend(["--vbnet-parser-engine", "regex"])
        env = journal_env(analyzer_env(), workdir, "parity_vb_amb", "p07_vb_amb_py", host, port)
        py_proc = subprocess.run(cmd_py, capture_output=True, text=True, timeout=900, env=env)

        cmd_rs = [str(RUST_BIN / "analyzer-vbnet")]
        cmd_rs.extend(_common_flags(workdir, "parity_vb_amb", "p07_vb_amb_rs", host, port))
        cmd_rs.extend(["--vbnet-parser-engine", "regex"])
        rs_proc = subprocess.run(cmd_rs, capture_output=True, text=True, timeout=900,
                                 env=analyzer_env())

        both_fail = py_proc.returncode != 0 and rs_proc.returncode != 0
        check("ambiguous_const: CẢ 2 backend fail", both_fail,
              f"py_rc={py_proc.returncode} rust_rc={rs_proc.returncode}")
        same_reason = (
            "cannot infer target_label" in (py_proc.stderr + py_proc.stdout)
            and "cannot infer target_label" in (rs_proc.stderr + rs_proc.stdout)
        )
        check("ambiguous_const: cùng error 'cannot infer target_label'", same_reason,
              f"py_tail={ (py_proc.stderr or py_proc.stdout)[-300:]!r} "
              f"rust_tail={(rs_proc.stderr or rs_proc.stdout)[-300:]!r}")
        report.append(
            "\n### ambiguous_const (upstream quirk, crash-parity)\n\n"
            f"- py rc={py_proc.returncode}, rust rc={rs_proc.returncode} "
            "(cùng `cannot infer target_label` từ write_all preprocessing)\n"
        )


def count_stock_vb(stock: Path) -> int:
    exts = {".vb", ".bas", ".cls", ".frm", ".vbs", ".asp", ".wsf", ".hta", ".ctl", ".pag"}
    count = 0
    for dirpath, dirnames, filenames in os.walk(stock):
        dirnames[:] = [name for name in dirnames if name not in {
            ".git", "node_modules", "vendor", "dist", "build", "out", "target",
            "bin", "obj", ".venv", "venv",
        }]
        for name in filenames:
            if os.path.splitext(name.lower())[1] in exts:
                count += 1
    return count


def main() -> int:
    global RUST_BIN
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--rust-bin-dir", default=str(RUST_BIN))
    parser.add_argument("--skip-stock", action="store_true")
    args = parser.parse_args()

    rust_bin_dir = Path(args.rust_bin_dir)
    missing = [v["rust_bin"] for v in VARIANTS.values() if not (rust_bin_dir / v["rust_bin"]).exists()]
    if missing:
        print(f"Rust analyzer binaries not found: {missing}")
        print("Build first: cargo build --release -p analyzer-vb (run inside rust/)")
        return 1

    report = [
        "# Phase 07 — VB analyzer family parity (python vs rust)",
        "",
        f"- chạy: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "- variants: vbnet, vb6, vba, vbscript (crate `analyzer-vb`, 4 binary)",
        f"- testdata: `{TESTDATA.relative_to(REPO)}` (1-2 file/variant, classifier + regex quirks)",
        "- mask: `" + str(sorted(MASKED_PROPS)) + "`",
        "- parser: `vb_common.parse_vb_file` là line/regex thuần (tree-sitter chỉ "
        "feed `has_error` vào parse_meta — không vào graph). Rust port full "
        "heuristic kể cả quirk line-number (`source.find(line, start-len(line))`) "
        "và dead regex `_ARRAY_DECL_RE`.",
        "- vbnet Roslyn (key decision #8): plane C# giữ nguyên subprocess — Rust "
        "invoke cùng `RoslynVbWorker` (build + manifest + JSON payloads, fallback "
        "regex từng file). Gate set DOTNET_ROLL_FORWARD=LatestMajor để worker "
        "net9 chạy trên runtime .NET 10 — vbnet gate exercise đúng plane Roslyn "
        "(scan_result functions=7 = roslyn counts, khác regex counts=11); nếu "
        "worker vẫn fail, CẢ HAI phía fallback regex (parity đúng cả hai world).",
        "- accommodation (không sửa rows): Python reference chạy với "
        "`configure_journal_env(mode=\"shadow\")` cho CALLS project_id fallback; "
        "Rust supply `project_id` tường minh trên call rows.",
    ]
    RUST_BIN = rust_bin_dir
    driver = FalkorDBDriver(host=args.host, port=args.port)

    for variant_key, variant in VARIANTS.items():
        print(f"\n===== variant {variant_key} ({variant['dialect']}) =====")
        dual(driver, variant, variant_key, TESTDATA / variant["fixture_dir"],
             "testdata_full", args.host, args.port, report)
        scenario_incremental(driver, variant, variant_key, args.host, args.port, report)

    scenario_ambiguous_const(driver, args.host, args.port, report)

    if not args.skip_stock and STOCK.exists():
        stock_vb = count_stock_vb(STOCK)
        if stock_vb >= 3:
            print(f"[stock] {stock_vb} vb-family files scanned")
            for variant_key, variant in VARIANTS.items():
                dual(driver, variant, variant_key, STOCK, "stock_full",
                     args.host, args.port, report)
        else:
            note = (
                f"[warn] skip stock: chỉ {stock_vb} file VB-family dưới stock "
                "< 3 — stock scenario bỏ qua (stock không chứa VB sources)."
            )
            print(note)
            report.append(f"\n> {note}\n")
    else:
        print("[warn] skip stock")
        report.append("\n> stock scenario bỏ qua (--skip-stock / stock không tồn tại).\n")

    report.append(
        f"\n## Kết luận\n\n- FAILURES: {FAILURES if FAILURES else 'không có — PASS toàn bộ'}\n"
    )
    gate_rows = [
        "testdata_full: [SCAN_RESULT] byte-identical",
        "testdata_full: graph diff rỗng ngoài mask",
        "inc_seed: [SCAN_RESULT] byte-identical",
        "inc_seed: graph diff rỗng ngoài mask",
        "inc_run: [SCAN_RESULT] byte-identical",
        "inc_run: cleanup line xuất hiện ở cả 2 log",
        "inc_run: cleanup counts khớp",
        "inc_run: graph diff rỗng ngoài mask",
    ]
    table = ["## Gates", "", "| Gate | net (vbnet) | 6 (vb6) | vba | vbscript |", "|---|---|---|---|---|"]
    for gate_name in gate_rows:
        row = [gate_name]
        for variant_key in ("net", "6", "vba", "vbscript"):
            statuses = []
            for tag in ("testdata_full", "inc_seed", "inc_run"):
                value = RESULTS.get(f"{variant_key}/{tag}", {}).get(
                    gate_name.split(": ", 1)[-1].strip())
                if value:
                    statuses.append(f"{tag}={value}")
            row.append(", ".join(statuses) if statuses else "-")
        table.append("| " + " | ".join(row) + " |")
    report.append("\n".join(table) + "\n")
    report.append(
        """
## Accepted divergences (không tác động graph-plane)

1. Parse cache (`--cache-dir`, `--disable-parse-cache`, `--ignore-cache`) là
   plane Python — Rust nhận và bỏ qua (verbose `cache=off` vs `cache=on`).
2. Qdrant/embedding KHÔNG port (key decision #3); `--qdrant-*`, `--embed-*`,
   `--device`, `--batch-size` nhận và bỏ qua.
3. Message scan là plane Python; default BẬT như Python nhưng gate chạy
   `--disable-message-scan` cho cả 2 phía.
4. `--config` Rust nhận và bỏ qua; Python pre-parse harness config
   (`load_harness_config`) với `/dev/null` = no-op.
5. `parse_meta` (has_error/error_nodes từ tree-sitter, worker_elapsed_ms,
   fallback_reason) chỉ sống trong parse cache Python — không vào graph; Rust
   set has_error=false.
6. Verbose log lines (ngoài `[SCAN_RESULT]`/`[cleanup][graph]`) không bắt buộc
   byte-identical.
7. Rust default Roslyn worker project resolve theo CWD layout repo
   (`code-tiny/tools/vb/roslyn_worker/...`) thay vì `__file__` Python;
   flag/env override giống nhau.
8. Harness accommodations (không đụng rows, áp cho CẢ HAI phía):
   `--parallel-workers 1` (Python default 4 thu payloads theo thread-completion
   order — nondeterministic ⇒ write order đổi ⇒ internal FalkorDB ids
   `_start_id`/`_end_id` khác dù graph content giống hệt), `--ignore-cache`
   (parse cache Python làm engine vbnet "dính" theo lần parse đầu giữa các
   run), `DOTNET_ROLL_FORWARD=LatestMajor` (chạy worker net9 trên runtime
   .NET 10 để exercise plane Roslyn), `--disable-message-scan` (message scan
   là plane Python-side).

## Suspected upstream bugs (không sửa — ghi nhận)

1. **Bare `Const X = ...` crash cả 2 backend**: `_VAR_DECL_RE` match scope
   `Const` ⇒ sinh thêm Variable row tên `X` trùng id với Constant row ⇒
   `write_all` preprocessing (Py lẫn Rust writer) raise
   `cannot infer target_label ... candidates=["Constant","Variable"]` ⇒
   analyzer crash trên mọi source có bare Const (rc=1). Đã gate crash-parity
   (`scenario_ambiguous_const`): CẢ HAI fail cùng error.
2. **Enum members mất phần tử đầu**: vòng extract member dùng `i > line_num`
   với `line_num` 1-based làm start index 0-based ⇒ member dòng đầu sau dòng
   Enum bị skip (port giữ nguyên, test `interface_enum_dual_rows`).
3. **Heuristic line-number** của Property/Event/Interface/Enum/Constant/
   Variable trả dòng trống ĐẦU TIÊN của file (hoặc 1) vì điều kiện
   `source.find(line, start - len(line)) == start - len(line)` chỉ thoả với
   `line=""` — các row này mang start_line/end_line sai từ upstream; port
   replica đầy đủ (py_find semantics).
4. `.ctl`/`.pag` nằm trong `_SOURCE_EXTS` vb6 nhưng classifier trả None ⇒
   dead extension (port giữ nguyên hành vi).
""")
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
