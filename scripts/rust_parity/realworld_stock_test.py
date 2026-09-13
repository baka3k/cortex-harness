#!/usr/bin/env python3
"""Real-world test trên repo thật: /Users/hieplq1.aip/baka3k/stock

Chạy toàn bộ stack vừa port trên dữ liệu thật (bounded sample):
  1. Inventory: walk source .py thật (bỏ venv/pyc/build), trích symbols.
  2. Journal: Python `SQLiteJournal` ghi run thật (artifacts từ file thật,
     2 batches + barrier) → Rust `inspect_journal` đọc và so với Python.
  3. Retrieval brain: BM25/intent/query-understanding trên text thật —
     parity Python ↔ PyO3 (`cortex_retrieval_py.so`) + sanity relevance.
  4. Ladybug: ghi File nodes thật bằng Python → Rust đọc/ghi marker →
     Python đọc lại.

Chạy từ repo root:
    .venv/bin/python scripts/rust_parity/realworld_stock_test.py
"""

from __future__ import annotations

import gc
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code-tiny"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
RUST_DIR = REPO / "rust"

STOCK = Path("/Users/hieplq1.aip/baka3k/stock")
MAX_FILES = 150
MAX_SYMBOLS_PER_FILE = 5

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def run_cargo(args: list[str]) -> str:
    result = subprocess.run(
        ["cargo", "run", "--quiet", *args],
        cwd=RUST_DIR,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cargo {' '.join(args)} failed:\n{result.stderr[-800:]}")
    return result.stdout.strip()


# ── 1. Inventory ─────────────────────────────────────────────

CLASS_RE = re.compile(r"^class\s+(\w+)", re.M)
DEF_RE = re.compile(r"^(?:async\s+)?def\s+(\w+)", re.M)
EXCLUDE = ("venv", "site-packages", "__pycache__", "egg-info", "build", "dist", "node_modules")


def collect_inventory() -> tuple[list[dict], list[dict]]:
    files: list[dict] = []
    symbols: list[dict] = []
    py_files = sorted(
        p
        for p in STOCK.rglob("*.py")
        if not any(part in EXCLUDE for part in p.parts)
    )[:MAX_FILES]
    for path in py_files:
        rel = str(path.relative_to(STOCK))
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        classes = CLASS_RE.findall(text)[:MAX_SYMBOLS_PER_FILE]
        functions = DEF_RE.findall(text)[:MAX_SYMBOLS_PER_FILE]
        files.append(
            {
                "file_id": rel,
                "path": rel,
                "byte_count": len(text.encode("utf-8")),
                "classes": classes,
                "functions": functions,
            }
        )
        module = rel.removesuffix(".py").replace("/", ".")
        for name in classes:
            symbols.append(
                {
                    "symbol_id": f"class:{module}:{name}",
                    "text": f"class {name} trong module {module} — {name} definition",
                }
            )
        for name in functions:
            symbols.append(
                {
                    "symbol_id": f"fn:{module}:{name}",
                    "text": f"function {name} trong module {module} — {name} implementation",
                }
            )
    return files, symbols


# ── 2. Journal trên dữ liệu thật ─────────────────────────────


def journal_test(files: list[dict]) -> None:
    import datetime as dt

    from tools.graph.journal import sqlite_store
    from tools.graph.journal.models import BatchSpec, OperationPhase, RunMetadata

    with tempfile.TemporaryDirectory() as tmp:
        db_path = (Path(tmp) / "stock_journal.sqlite").resolve()
        artifact_root = (Path(tmp) / "artifacts").resolve()
        journal = sqlite_store.SQLiteJournal(db_path, artifact_root=artifact_root)

        metadata = RunMetadata(
            project_id="stock", scope_id="stock", source_revision="realworld-test",
            source_snapshot="snapshot-1", physical_target="stock:local",
            generation="gen-1", parser="python", parser_version="1.0",
            schema_fingerprint="code_graph@2", query_shape_version="qv-1",
        )
        run = journal.open_run(metadata, expected_items=2)
        artifact = journal.create_artifact(
            run.run_id, [{"file_id": f["file_id"], "path": f["path"]} for f in files]
        )
        journal.open_barrier(run.run_id, "files-ingested")
        spec_nodes = BatchSpec(
            phase=OperationPhase.NODES, operation_key="node.upsert", sequence=0,
            artifact=artifact, expected_count=len(files),
            produced_barriers=["files-ingested"], operation={},
        )
        journal.enqueue_batch(run.run_id, spec_nodes)
        spec_calls = BatchSpec(
            phase=OperationPhase.CALLS, operation_key="call.upsert", sequence=1,
            artifact=artifact, expected_count=len(files),
            required_barriers=["files-ingested"], operation={},
        )
        journal.enqueue_batch(run.run_id, spec_calls)
        journal.close_barrier(run.run_id, "files-ingested")

        claimed = journal.claim_batch(run_id_value=run.run_id, lease_seconds=300)
        assert claimed is not None
        journal.ack_batch(claimed.job_id, claimed.fencing_token, elapsed_ms=42)
        claimed2 = journal.claim_batch(run_id_value=run.run_id, lease_seconds=300)
        assert claimed2 is not None
        journal.ack_batch(claimed2.job_id, claimed2.fencing_token)
        journal.complete_producers(run.run_id)
        journal._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        journal._connection.close()

        # Python inspect (source of truth)
        python_summary = sqlite_store.inspect_journal(db_path)

        # Rust inspect: copy sang path phẳng trong temp (WAL readonly cần shm)
        inspect_path = db_path.parent / "inspect_source.sqlite"
        inspect_path.write_bytes(db_path.read_bytes())
        rust_out = run_cargo(
            ["-p", "cortex-graph-core", "--example", "inspect_journal", "--", str(inspect_path)]
        )
        try:
            rust_summary = json.loads(rust_out)
        except json.JSONDecodeError:
            raise RuntimeError(f"rust stdout không phải JSON: {rust_out[:200]!r}")

    check(
        "journal: 1 run ghi từ dữ liệu stock thật",
        len(python_summary) == 1 and python_summary[0]["parser"] == "python",
        f"python_summary={python_summary}",
    )
    check(
        "journal: Rust inspect khớp Python trên DB thật",
        len(rust_summary) == len(python_summary),
        f"rust={rust_summary}",
    )
    if rust_summary and python_summary:
        masked_keys = {"journal_bytes", "oldest_unfinished_age_seconds"}
        mismatches = [
            key
            for key in python_summary[0]
            if key not in masked_keys and rust_summary[0].get(key) != python_summary[0][key]
        ]
        check("journal: field-by-field khớp (mask age/bytes)", not mismatches,
              f"mismatch keys={mismatches}")
        check(
            "journal: toàn bộ batches DONE (ack 2/2)",
            python_summary[0]["produced"] == 2 and python_summary[0]["acked"] == 2,
            f"summary={python_summary[0]}",
        )


# ── 3. Retrieval brain trên text thật ────────────────────────

RETRIEVAL_QUERIES = [
    "who calls place_order",
    "đặt lệnh mua cổ phiếu bị lỗi",
    "explain the portfolio analysis flow",
    "recently changed files",
    "matching engine VN30",
    "random gibberish zzqq",
]


def retrieval_test(symbols: list[dict]) -> None:
    import cortex_retrieval_py as rust

    from tools.common.bm25_ranker import BM25Ranker
    from tools.common.query_intent_classifier import classify_query as py_classify
    from tools.common.query_understanding import QueryUnderstanding

    ranker = BM25Ranker()
    ranker.build_index(symbols, text_field="text", id_field="symbol_id")
    docs_json = json.dumps(symbols, ensure_ascii=False)

    all_parity = True
    relevance_hits = 0
    relevance_queries = 0
    for query in RETRIEVAL_QUERIES:
        expected = ranker.score(query)
        actual = json.loads(rust.bm25_score(docs_json, query, text_field="text", id_field="symbol_id"))
        same = set(actual) == set(expected) and all(
            abs(actual[k] - v) < 1e-9 for k, v in expected.items()
        )
        all_parity = all_parity and same
        if not same:
            print(f"    mismatch {query!r}: rust={actual} python={expected}")

        intent_ok = rust.classify_query(query) == py_classify(query)
        all_parity = all_parity and intent_ok

        qu_py = QueryUnderstanding.from_text(query).to_dict()
        qu_rust = json.loads(rust.query_understanding(query))
        all_parity = all_parity and qu_rust == qu_py



    check(
        f"retrieval: parity Python↔PyO3 trên {len(RETRIEVAL_QUERIES)} query thật "
        f"(BM25 + intent + query-understanding)",
        all_parity,
    )
    # sanity relevance: top hit phải liên quan domain (dựa trên inventory thật)
    # Tokenizer [a-z0-9_]+ gộp CamelCase thành 1 token → match đến từ module
    # path (dot-split) — dùng path tokens làm sanity keywords.
    sanity_cases = [
        ("portfolio", "portfolio"),
        ("snapshots", "snapshots"),
        ("analyze_stock", "analyze_stock"),
    ]
    sanity_pass = 0
    sanity_total = 0
    for query, keyword in sanity_cases:
        scores = ranker.score(query)
        if not scores:
            continue
        sanity_total += 1
        top_id = max(scores, key=scores.get)
        if keyword in top_id.lower():
            sanity_pass += 1
    check(
        f"retrieval: sanity relevance trên text thật ({sanity_pass}/{sanity_total} hit đúng domain)",
        sanity_total > 0 and sanity_pass == sanity_total,
        f"pass={sanity_pass}/{sanity_total}",
    )


# ── 4. Ladybug trên dữ liệu thật ─────────────────────────────


def ladybug_test(files: list[dict]) -> None:
    import ladybug

    with tempfile.TemporaryDirectory() as tmp:
        store = str(Path(tmp) / "stock_files.lbug")
        db = ladybug.Database(store)
        conn = ladybug.Connection(db)
        conn.execute("CREATE NODE TABLE File (id STRING, name STRING, PRIMARY KEY(id))")
        for f in files[:100]:
            conn.execute(
                f"CREATE (f:File {{id: {json.dumps(f['file_id'])}, "
                f"name: {json.dumps(Path(f['path']).name)}}})"
            )
        count_before = conn.execute("MATCH (f:File) RETURN count(f)").get_next()[0]
        # Ladybug single-writer: db.close() KHÔNG nhả OS flock — phải drop
        # object + gc.collect() (finding thực nghiệm, xem phase-06).
        conn = None
        db = None
        gc.collect()

        # Rust: đọc + ghi marker vào store do Python tạo
        rust_ids = json.loads(
            run_cargo(
                ["-p", "cortex-graph-driver", "--example", "lbug_probe", "--",
                 "write", store, "rust-marker::stock", "written-by-rust"]
            )
        )

        # Mở lại bằng Python để xác nhận node Rust ghi
        db = ladybug.Database(store)
        conn = ladybug.Connection(db)
        count_after = conn.execute("MATCH (f:File) RETURN count(f)").get_next()[0]
        marker = conn.execute(
            "MATCH (f:File {id: 'rust-marker::stock'}) RETURN count(f)"
        ).get_next()[0]
        db.close()

    check(
        f"ladybug: Rust đọc được {len(rust_ids)} File node thật từ stock",
        len(rust_ids) == count_before + 1 and "rust-marker::stock" in rust_ids,
        f"ids_sample={rust_ids[:3]}",
    )
    check(
        "ladybug: node Rust ghi đọc lại được từ Python",
        count_after == count_before + 1 and marker == 1,
        f"before={count_before} after={count_after} marker={marker}",
    )


def main() -> None:
    print(f"=== Real-world test trên {STOCK} ===")
    files, symbols = collect_inventory()
    print(f"inventory: {len(files)} files, {len(symbols)} symbols (bounded sample)")
    check("inventory: sample đủ lớn để có ý nghĩa", len(files) >= 50 and len(symbols) >= 100,
          f"files={len(files)} symbols={len(symbols)}")

    journal_test(files)
    retrieval_test(symbols)
    ladybug_test(files)

    print()
    if FAILURES:
        print(f"KẾT QUẢ: {len(FAILURES)} FAILURE — {FAILURES}")
        sys.exit(1)
    print("KẾT QUẢ: tất cả PASS — stack Rust hoạt động trên dữ liệu thật stock")


if __name__ == "__main__":
    main()
