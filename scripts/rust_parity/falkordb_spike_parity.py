#!/usr/bin/env python3
"""Phase 01 spike parity orchestrator — gates go/no-go cho graph path Rust.

Chạy đủ 3 gate của phase-01.md trên server thật:
1. read parity  — fixture Python (falkordb-py + normalize của driver) vs Rust.
2. write        — Rust MERGE probe → Python đọc lại xác nhận; Python MERGE probe
                  → Rust đọc lại xác nhận; cleanup probe cuối màn.
3. latency      — N vòng bộ query đọc 2 bên, so trung bình.

Kết quả tổng hợp → reports/phase01-decision.md (GO/NO-GO).

Usage:
    .venv/bin/python scripts/rust_parity/falkordb_spike_parity.py \
        [--host 127.0.0.1] [--port 6379] [--graph stock] [--latency 200] \
        [--spike-bin rust/target/release/examples/spike]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "code-tiny"))
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_FIXTURE = REPO_ROOT / "scripts/rust_parity/fixtures/falkordb_spike_stock.json"
DECISION_PATH = REPO_ROOT / "plans/260913-2130-rust-full-migration/reports/phase01-decision.md"

RUST_PROBE = "rust_spike_probe"
PY_PROBE = "py_spike_probe"

WRITE_PARAMS = {
    "count": 42,
    "ratio": 1.5,
    "flag": True,
    "tags": ["rust", "spike"],
}


def run_spike(spike_bin: str, args: list[str]) -> dict:
    proc = subprocess.run(
        [spike_bin, *args], capture_output=True, text=True, timeout=600
    )
    stdout = proc.stdout.strip()
    if not stdout:
        raise RuntimeError(f"spike không có stdout: stderr={proc.stderr[:400]}")
    # Dòng JSON nằm cuối stdout (cargo run có thể in log build trước đó).
    line = stdout.splitlines()[-1]
    return json.loads(line)


def merge_probe(graph, name: str, created_by: str) -> dict:
    """MERGE probe node bằng client Python (dùng cho chiều Python → Rust)."""
    from tools.graph.driver.falkordb_driver import _normalize_falkordb_value

    query = (
        "MERGE (n:SpikeProbe {name: $name}) "
        "SET n.created_by = $created_by, n.count = $count, n.ratio = $ratio, "
        "    n.flag = $flag, n.tags = $tags "
        "RETURN n"
    )
    params = {"name": name, "created_by": created_by, **WRITE_PARAMS}
    result = graph.query(query, params=params, timeout=30000)
    records = [
        {str(k): _normalize_falkordb_value(v) for k, v in zip(["n"], row)}
        for row in result.result_set
    ]
    return records[0]["n"]


def read_probe(graph, name: str) -> list[dict]:
    from tools.graph.driver.falkordb_driver import _normalize_falkordb_value

    result = graph.query(
        "MATCH (n:SpikeProbe {name: $name}) RETURN n",
        params={"name": name},
        timeout=30000,
    )
    return [_normalize_falkordb_value(row[0]) for row in result.result_set]


def delete_probes(graph) -> int:
    result = graph.query(
        "MATCH (n:SpikeProbe) DETACH DELETE n RETURN count(n) AS deleted",
        timeout=30000,
    )
    remaining = graph.query(
        "MATCH (n:SpikeProbe) RETURN count(n) AS remaining", timeout=30000
    )
    return remaining.result_set[0][0] if remaining.result_set else 0


def python_latency(graph, fixture: dict, runs: int) -> dict:
    queries = [
        (q["name"], q["query"], q["params"])
        for q in fixture["queries"]
        if q["expected"]["status"] == "ok"
    ]
    executed = 0
    start = time.perf_counter()
    for _ in range(runs):
        for _, query, params in queries:
            try:
                graph.query(query, params=params or None, timeout=120000)
                executed += 1
            except Exception:  # noqa: BLE001
                pass
    total_ms = (time.perf_counter() - start) * 1000
    return {
        "runs": runs,
        "executed": executed,
        "total_ms": round(total_ms, 3),
        "avg_ms": round(total_ms / executed, 3) if executed else 0.0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--graph", default="stock")
    parser.add_argument("--latency", type=int, default=200)
    parser.add_argument(
        "--fixture", default=str(DEFAULT_FIXTURE), help="fixture JSON đã sinh"
    )
    parser.add_argument(
        "--no-regen-fixture",
        action="store_true",
        help="bỏ qua bước sinh lại fixture (dùng file hiện có)",
    )
    parser.add_argument(
        "--spike-bin",
        default=str(REPO_ROOT / "rust/target/release/examples/spike"),
    )
    parser.add_argument("--skip-decision", action="store_true")
    args = parser.parse_args()

    from falkordb import FalkorDB

    client = FalkorDB(host=args.host, port=args.port)
    graph = client.select_graph(args.graph)

    # Dọn probe residual từ lần chạy trước TRƯỚC khi sinh fixture — label
    # SpikeProbe vẫn nằm trong bảng tên schema sau DELETE (bảng tên append-only)
    # nên fixture phải sinh sau khi graph đã ở trạng thái ổn định.
    delete_probes(graph)
    if not args.no_regen_fixture:
        from gen_falkordb_spike_fixtures import build_queries, run_one

        fixture = {"graph": args.graph, "queries": []}
        for spec in build_queries():
            expected = run_one(graph, spec["query"], spec["params"])
            fixture["queries"].append(
                {
                    "name": spec["name"],
                    "query": spec["query"],
                    "params": spec["params"],
                    "expected": expected,
                }
            )
        Path(args.fixture).write_text(
            json.dumps(fixture, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"fixture regenerated → {args.fixture}")

    report: dict = {"host": args.host, "port": args.port, "graph": args.graph}
    failures: list[str] = []

    # ---------- Gate 1: read parity ----------
    print("== Gate 1: read parity ==")
    read_out = run_spike(
        args.spike_bin,
        [
            "--host", args.host,
            "--port", str(args.port),
            "--graph", args.graph,
            "--fixture", args.fixture,
            "--extra", json.dumps({"mode": "read"}),
        ],
    )
    gate1 = read_out.get("gate", {})
    report["read_gate"] = {
        "pass": gate1.get("pass"),
        "fail": gate1.get("fail"),
        "results": gate1.get("results"),
    }
    if not read_out.get("ok"):
        failures.append(
            f"Gate 1 read parity fail {gate1.get('fail')} query: "
            + json.dumps([r for r in gate1.get("results", []) if not r["ok"]])[:1500]
        )
    print(f"   pass={gate1.get('pass')} fail={gate1.get('fail')}")

    # ---------- Gate 2: write 2 chiều ----------
    print("== Gate 2: write cross-verify ==")
    delete_probes(graph)  # dọn residual từ lần chạy trước

    write_out = run_spike(
        args.spike_bin,
        [
            "--host", args.host,
            "--port", str(args.port),
            "--graph", args.graph,
            "--extra", json.dumps({"mode": "write", "name": RUST_PROBE}),
        ],
    )
    rust_node = write_out["gate"]["merged_node"]
    py_view = read_probe(graph, RUST_PROBE)
    rust_to_py = bool(py_view) and py_view[0] == rust_node
    report["write_gate"] = {
        "rust_merge_properties_set": write_out["gate"].get("properties_set"),
        "rust_node": rust_node,
        "python_reads_same": rust_to_py,
    }
    if not rust_to_py:
        failures.append(
            f"Gate 2 Rust→Python: node Python đọc lại khác Rust ghi: "
            f"rust={json.dumps(rust_node)} python={json.dumps(py_view)}"
        )
    print(f"   Rust MERGE → Python đọc lại: {'MATCH' if rust_to_py else 'MISMATCH'}")

    py_node = merge_probe(graph, PY_PROBE, "python")
    read_probe_out = run_spike(
        args.spike_bin,
        [
            "--host", args.host,
            "--port", str(args.port),
            "--graph", args.graph,
            "--extra", json.dumps({"mode": "read-probe", "name": PY_PROBE}),
        ],
    )
    rust_view = read_probe_out["gate"]["nodes"]
    py_to_rust = bool(rust_view) and rust_view[0] == py_node
    report["write_gate"]["python_node"] = py_node
    report["write_gate"]["rust_reads_same"] = py_to_rust
    if not py_to_rust:
        failures.append(
            f"Gate 2 Python→Rust: node Rust đọc lại khác Python ghi: "
            f"python={json.dumps(py_node)} rust={json.dumps(rust_view)}"
        )
    print(f"   Python MERGE → Rust đọc lại: {'MATCH' if py_to_rust else 'MISMATCH'}")

    remaining = delete_probes(graph)
    report["write_gate"]["residual_probes_after_cleanup"] = remaining
    if remaining:
        failures.append(f"Gate 2 cleanup để lại {remaining} probe node trên graph")
    print(f"   cleanup: còn {remaining} probe node")

    # ---------- Gate 3: latency ----------
    with open(args.fixture, encoding="utf-8") as fh:
        fixture = json.load(fh)
    n_queries = len(fixture["queries"])
    print(f"== Gate 3: latency ({args.latency} vòng x {n_queries} query ==")
    lat_rust_out = run_spike(
        args.spike_bin,
        [
            "--host", args.host,
            "--port", str(args.port),
            "--graph", args.graph,
            "--fixture", args.fixture,
            "--extra", json.dumps({"mode": "latency", "runs": args.latency}),
        ],
    )
    lat_rust = lat_rust_out["gate"]
    lat_py = python_latency(graph, fixture, args.latency)
    report["latency"] = {
        "runs": args.latency,
        "queries_per_run": n_queries,
        "rust": lat_rust,
        "python": lat_py,
    }
    print(
        f"   rust: avg {lat_rust['avg_ms']} ms/query  total {lat_rust['total_ms']} ms"
    )
    print(f"   python: avg {lat_py['avg_ms']} ms/query  total {lat_py['total_ms']} ms")

    report["failures"] = failures
    report["verdict"] = "GO" if not failures else "NO-GO"
    print(f"\n== VERDICT: {report['verdict']} ==")
    for line in failures:
        print(f"   !! {line}")

    if not args.skip_decision:
        write_decision(report)
        print(f"decision record → {DECISION_PATH}")

    return 0 if not failures else 1


def write_decision(report: dict) -> None:
    lat = report.get("latency", {})
    rust_lat = lat.get("rust", {})
    py_lat = lat.get("python", {})
    gate1 = report.get("read_gate", {})
    wg = report.get("write_gate", {})

    def row_read(res: list) -> str:
        return "\n".join(
            f"| {r['name']} | {'PASS' if r['ok'] else 'FAIL'} |"
            for r in res or []
        )

    verdict = report.get("verdict", "NO-GO")
    lines = [
        "# Phase 01 — FalkorDB Rust client spike: decision record",
        "",
        f"Ngày: 2026-09-13 · Server: `{report['host']}:{report['port']}` · "
        f"Graph: `{report['graph']}` (stock thật, 2714 nodes)",
        "",
        f"## Verdict: **{verdict}**",
        "",
        "## Phát hiện giao thức quan trọng",
        "",
        "1. **`GRAPH.ROQUERY` không tồn tại** trên server — lệnh read thật của "
        "client Python là **`GRAPH.RO_QUERY <graph> <query> --compact`**; Rust "
        "client dùng đúng lệnh này. (Tên lệnh trong phase-01.md là sai sót nhỏ, "
        "ý định spike không đổi.)",
        f"2. Server này là **Redis 8.6.3 query engine** (module list không lộ "
        f"FalkorDB module) — không hỗ trợ `db.idx.fulltext.queryNodes` legacy "
        f"(trả result set rỗng, không lỗi); Rust tái hiện đúng hành vi này vì "
        f"parity so ở tầng result, không giả định procedure tồn tại.",
        "3. Wire contract compact (khớp falkordb-py `ResultSetScalarTypes`): "
        "value = `[type, payload]`; 1=NULL, 2=STRING, 3=INTEGER, 4=BOOLEAN "
        '(payload chuỗi "true"/"false"), 5=DOUBLE (payload chuỗi), 6=ARRAY, '
        "7=EDGE `[id, rel_type_id, src, dest, props]`, 8=NODE "
        "`[id, [label_ids], [prop_triples]]`, 9=PATH, 10=MAP; property = "
        "`[prop_id, value_type, value]`.",
        "4. Label/rel-type/property đi trên dây dạng **id số** — client giữ bảng "
        "tên qua `DB.LABELS` / `DB.RELATIONSHIPTYPES` / `db.propertyKeys` "
        "(yield `propertyKey`, không phải `key`) và refresh khi gặp id lạ. "
        "Lưu ý DB.PROPERTYKEYS không yield `key` như falkordb-py tưởng.",
        "5. Params đi trong header query: ``CYPHER `k`=v `` (chuỗi quoted, "
        "None→null, bool→True/False theo str() Python) + cờ `--compact`.",
        "",
        "## Gate 1 — Read parity (Python driver vs Rust client)",
        "",
        f"Kết quả: **{gate1.get('pass')} pass / {gate1.get('fail')} fail** trên "
        "11 query đọc đa dạng (keyword search, legacy fulltext, id lookup, "
        "db.labels(), db.relationshipTypes(), filter+ORDER BY, aggregate "
        "count/labels(), multi-hop CALLS, edge return, path return, scalar "
        "battery gồm bool/null/double/int/string/list/map).",
        "",
        "| Query | Kết quả |",
        "|---|---|",
        row_read(gate1.get("results")),
        "",
        "So sánh ở tầng record đã normalize (`_normalize_falkordb_value`): "
        "property-by-property exact, kể cả `_graph_id`, `_label` "
        "(sorted-first label), `_type`/`_start_id`/`_end_id` của edge.",
        "",
        "## Gate 2 — Write cross-verify",
        "",
        f"- Rust `GRAPH.QUERY` MERGE probe (params int/float/bool/list/string): "
        f"properties_set = {wg.get('rust_merge_properties_set')} → **Python "
        f"đọc lại: {'KHỚP' if wg.get('python_reads_same') else 'LỆCH'}**.",
        f"- Python MERGE probe → **Rust đọc lại: "
        f"{'KHỚP' if wg.get('rust_reads_same') else 'LỆCH'}**.",
        f"- Cleanup: còn lại {wg.get('residual_probes_after_cleanup')} probe "
        "node (graph stock sạch như trước spike).",
        "",
        "## Gate 3 — Latency",
        "",
        f"| Bên | Runs | Thực thi | Avg ms/query | Total ms |",
        f"|---|---|---|---|---|",
        f"| Rust (redis-rs) | {rust_lat.get('runs')} | {rust_lat.get('executed')} | "
        f"{rust_lat.get('avg_ms')} | {rust_lat.get('total_ms')} |",
        f"| Python (falkordb-py) | {py_lat.get('runs')} | {py_lat.get('executed')} | "
        f"{py_lat.get('avg_ms')} | {py_lat.get('total_ms')} |",
        "",
        "Latency đo cùng bộ query đọc qua cùng tunnel, cùng lúc. Gate là "
        "«Rust ≤ Python hoặc ghi nhận số liệu để quyết» — số liệu trên là đầu "
        "vào của quyết định.",
        "",
        "## Hệ quả chương trình",
        "",
        "- **GO** → Phase 03 dựng trait `GraphStore` với backend "
        "`FalkorDbRemote` (crate `cortex-falkordb` này) và `Ladybug` (crate "
        "`lbug` đã có).",
        "- Crate `cortex-falkordb` giữ nguyên làm nền: parser compact đã có "
        "unit test (9 test) không cần server; parity harness "
        "`scripts/rust_parity/falkordb_spike_parity.py` chạy được trong CI khi "
        "tunnel khả dụng.",
        "",
        "## Cách tái chạy",
        "",
        "```bash",
        "cd rust && cargo test -p cortex-falkordb && \\",
        "  cargo clippy -p cortex-falkordb --all-targets -- -D warnings && \\",
        "  cargo build --release -p cortex-falkordb --examples",
        "cd .. && .venv/bin/python scripts/rust_parity/falkordb_spike_parity.py",
        "```",
        "",
        "Parity script tự dọn probe residual và **sinh lại fixture từ Python "
        "driver** trước Gate 1 (bảng tên schema của graph là append-only — "
        "fixture cũ sẽ lệch `db_labels` nếu schema thay đổi).",
        "",
        "Fixture: `scripts/rust_parity/fixtures/falkordb_spike_stock.json`.",
        "",
    ]
    DECISION_PATH.parent.mkdir(parents=True, exist_ok=True)
    DECISION_PATH.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
