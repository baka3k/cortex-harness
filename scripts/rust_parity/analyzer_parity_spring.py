#!/usr/bin/env python3
"""Phase 08 parity gate — Spring overlay analyzer Python vs Rust.

Dual-run: seed 2 graph FalkorDB bằng base java analyzer Python (journal
shared-shadow), rồi `tools/spring/spring_analyzer.py` vs `analyzer-spring`.
Gate: artifact JSON byte-identical + `[spring]`/`spring_facts` lines + graph
dump diff rỗng ngoài mask + incremental round.

Usage (từ repo root):
    .venv/bin/python scripts/rust_parity/analyzer_parity_spring.py
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyzer_parity_p08_common import (  # noqa: E402
    PY_BIN, REPO, REPORTS, FAILURES, analyzer_env, check, clean_graph, compare_files, scratch_dir,
    compare_graphs, dump_graph_masked, header, run_overlay, seed_base_graph,
)
from tools.graph.driver.falkordb_driver import FalkorDBDriver  # noqa: E402

TESTDATA = REPO / "tests" / "fixtures" / "java-spring-overlays"
RUST_BIN = REPO / "rust" / "target" / "release" / "analyzer-spring"
PY_ANALYZER = REPO / "code-tiny" / "tools" / "spring" / "spring_analyzer.py"
REPORT_PATH = REPORTS / "phase08-spring-parity.md"
PROJECT_ID = "parity_spring"

SPRING_RE = re.compile(
    r"\[spring\] modules=(\d+) configs=(\d+) language_facts=(\d+) "
    r"semantic_facts=(\d+) relationships=(\d+) diagnostics=(\d+)"
)
FACTS_RE = re.compile(r"\[falkordb\] spring_facts (\d+)/(\d+)")
RELS_RE = re.compile(r"\[falkordb\] spring_relationships (\d+)/(\d+)")


def common_flags(graph: str, host: str, port: int) -> list[str]:
    return [
        "--project-id", PROJECT_ID,
        "--graph-provider", "falkordb",
        "--falkordb-uri", f"{host}:{port}",
        "--falkordb-graph", graph,
        "--disable-message-scan",
        "--verbose",
    ]


def run_py(root: Path, graph: str, host: str, port: int, artifact: Path,
           incremental: tuple[Path, Path] | None) -> str:
    cmd = [
        str(PY_BIN), str(PY_ANALYZER),
        "--root", str(root),
        *common_flags(graph, host, port),
        "--spring-facts-output", str(artifact),
    ]
    if incremental:
        changed, deleted = incremental
        cmd.append("--incremental")
        if changed:
            cmd.extend(["--changed-files-manifest", str(changed)])
        if deleted:
            cmd.extend(["--deleted-files-manifest", str(deleted)])
    return run_overlay(cmd)


def run_rust(root: Path, graph: str, host: str, port: int, artifact: Path,
             incremental: tuple[Path, Path] | None, rust_bin: Path) -> str:
    cmd = [
        str(rust_bin),
        "--root", str(root),
        *common_flags(graph, host, port),
        "--spring-facts-output", str(artifact),
    ]
    if incremental:
        changed, deleted = incremental
        cmd.append("--incremental")
        if changed:
            cmd.extend(["--changed-files-manifest", str(changed)])
        if deleted:
            cmd.extend(["--deleted-files-manifest", str(deleted)])
    return run_overlay(cmd)


def key_lines(log: str) -> list[str]:
    lines: list[str] = []
    for pattern in (SPRING_RE, FACTS_RE, RELS_RE):
        matches = list(pattern.finditer(log))
        if not matches:
            lines.append(f"{pattern.pattern[:24]}: <missing>")
            continue
        lines.append(matches[-1].group(0))
    return lines


def dual(driver: FalkorDBDriver, root: Path, tag: str, host: str, port: int,
         scratch: Path, report: list[str], rust_bin: Path,
         incremental: tuple[Path, Path] | None = None) -> None:
    graph_py = f"p08_spring_{tag}_py"
    graph_rust = f"p08_spring_{tag}_rs"
    artifact_py = scratch / f"{tag}_py" / "spring_facts.json"
    artifact_rs = scratch / f"{tag}_rs" / "spring_facts.json"
    artifact_py.parent.mkdir(parents=True, exist_ok=True)
    artifact_rs.parent.mkdir(parents=True, exist_ok=True)

    # Seed CẢ HAI graph bằng base java analyzer Python.
    seed_base_graph(driver, root, PROJECT_ID, graph_py, host, port)
    seed_base_graph(driver, root, PROJECT_ID, graph_rust, host, port)

    py_log = run_py(root, graph_py, host, port, artifact_py, incremental)
    rs_log = run_rust(root, graph_rust, host, port, artifact_rs, incremental, rust_bin)

    py_lines, rs_lines = key_lines(py_log), key_lines(rs_log)
    check(f"{tag}: [spring]/facts lines byte-identical", py_lines == rs_lines,
          f"py={py_lines} rs={rs_lines}")
    report.append(f"\n### lines {tag}\n\n- py: `{py_lines}`\n- rs: `{rs_lines}`\n")

    compare_files(artifact_py, artifact_rs, f"{tag} spring_facts.json", report)
    compare_graphs(driver, graph_py, graph_rust, tag.upper(), report)


def scenario_incremental(driver: FalkorDBDriver, host: str, port: int,
                         report: list[str], rust_bin: Path, scratch: Path) -> None:
    tmp = scratch_dir("p08_spring_inc")
    try:
        workdir = tmp / "corpus"
        shutil.copytree(TESTDATA, workdir)

        # Seed FULL trên corpus copy (2 graph + artifacts).
        dual(driver, workdir, "inc_seed", host, port, scratch, report, rust_bin)

        # Mutate: sửa PricingService (thêm method), thêm properties key,
        # xoá CacheConfig.java.
        pricing = workdir / "src" / "main" / "java" / "com" / "example" / "shop" / "PricingService.java"
        pricing.write_text(
            pricing.read_text(encoding="utf-8").replace(
                "    @Scheduled(cron = \"0 0 * * * *\")",
                "    public long discount(long id) {\n        return price(id) - 1;\n    }\n\n"
                "    @Scheduled(cron = \"0 0 * * * *\")",
            ),
            encoding="utf-8",
        )
        props = workdir / "src" / "main" / "resources" / "application.properties"
        props.write_text(
            props.read_text(encoding="utf-8") + "app.discount.rate=0.1\n",
            encoding="utf-8",
        )
        (workdir / "src" / "main" / "java" / "com" / "example" / "shop" / "CacheConfig.java").unlink()

        changed_manifest = Path(tmp) / "changed.json"
        deleted_manifest = Path(tmp) / "deleted.json"
        changed_manifest.write_text(json.dumps({"files": [
            "src/main/java/com/example/shop/PricingService.java",
            "src/main/resources/application.properties",
        ]}) + "\n", encoding="utf-8")
        deleted_manifest.write_text(json.dumps({"files": [
            "src/main/java/com/example/shop/CacheConfig.java",
        ]}) + "\n", encoding="utf-8")
        dual(driver, workdir, "inc_run", host, port, scratch, report, rust_bin,
             incremental=(changed_manifest, deleted_manifest))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--rust-bin", default=str(RUST_BIN))
    args = parser.parse_args()

    rust_bin = Path(args.rust_bin)
    report = header(
        "Spring overlay", TESTDATA, rust_bin,
        ["- base seed: java analyzer Python (journal shared-shadow) trên CẢ HAI graph"],
    )
    driver = FalkorDBDriver(host=args.host, port=args.port)

    tmp = scratch_dir("p08_spring")
    try:
        scratch = tmp
        dual(driver, TESTDATA, "testdata_full", args.host, args.port, scratch, report, rust_bin)
        scenario_incremental(driver, args.host, args.port, report, rust_bin, scratch)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

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
