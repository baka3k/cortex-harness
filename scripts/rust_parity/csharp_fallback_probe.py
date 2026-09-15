#!/usr/bin/env python3
"""Phase 03 fallback gate — đo tree-sitter fallback có được trigger thật không.

Câu hỏi policy (phase-03.md, red-team A6): entry Python có tree-sitter
fallback per-file khi Roslyn worker unavailable/fail; Rust entry KHÔNG port
fallback. Gate này ĐO trên corpus thật: fallback có chạy không, trên file nào.

Phương pháp (honest instrumentation — sitecustomize-style patch, chạy trong
subprocess để không đụng process parity):
  * re-exec chính script này với `--child`: import module
    `tools.csharp.csharp_analyzer` nguyên vẹn, monkeypatch
    `parse_csharp_file` (fallback-of-record, được `_load_or_parse_payload`
    gọi cho MỌI file không có payload Roslyn) để đếm trigger per-file, và
    wrap `RoslynFirstRunner.try_load` + `_build_roslyn_runner` để ghi nhận
    backend Roslyn, resolved files, per-file errors, last_error.
  * CLI contract GIỐNG HỆT leg Python của parity run (journal env + cùng
    flags) trừ parse cache (`--ignore-cache`) để cache không che fallback.
  * `[FALLBACK_PROBE]` JSON in ra stdout cuối + ghi file `--probe-out`.

Control run: `--roslyn-worker-project /nonexistent/...` ép worker fail để
chứng minh instrumentation bắt được fallback (worker down ⇒ mọi file rơi
về tree-sitter).

Usage (từ repo root):
    .venv/bin/python scripts/rust_parity/csharp_fallback_probe.py \
        [--root tests/fixtures/csharp-analyzer] [--host 127.0.0.1] [--port 6379]
        [--probe-out /tmp/probe.json] [--break-worker]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CORPUS_DEFAULT = REPO / "tests" / "fixtures" / "csharp-analyzer"
SCRATCH = REPO / ".cache" / "p03_csharp"
JOURNAL_DIR = SCRATCH / "journal"
PY_BIN = REPO / ".venv" / "bin" / "python"


def sanitized_env() -> dict:
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
    env["DOTNET_ROLL_FORWARD"] = "LatestMajor"
    return env


def journal_metadata(project_id: str, graph: str, port: int) -> str:
    return json.dumps(
        {
            "project_id": project_id,
            "scope_id": f"{project_id}-scope",
            "source_revision": "HEAD",
            "source_snapshot": "parity",
            "physical_target": f"falkordb://127.0.0.1:{port}/{graph}",
            "generation": "g1",
            "parser": "csharp",
            "parser_version": "py-ref",
            "schema_fingerprint": "parity-fp",
            "query_shape_version": "v1",
        },
        ensure_ascii=True,
    )


def child_cli_args(args: argparse.Namespace) -> list[str]:
    argv = [
        "--config", "/dev/null",
        "--root", str(args.root),
        "--project-id", args.project_id,
        "--project-name", args.project_id,
        "--language", "csharp",
        "--commit-sha-before", "",
        "--commit-sha-after", "",
        "--graph-provider", "falkordb",
        "--falkordb-uri", f"{args.host}:{args.port}",
        "--falkordb-graph", args.graph,
        "--disable-message-scan",
        "--ignore-cache",
        "--verbose",
    ]
    if args.break_worker:
        argv.extend(["--roslyn-worker-project", "/nonexistent/p03/CSharpRoslynWorker.csproj"])
    return argv


def run_child(args: argparse.Namespace) -> tuple[dict, str]:
    """Re-exec bản thân với --child; trả (probe dict, child log)."""
    env = sanitized_env()
    env["CORTEX_GRAPH_JOURNAL_MODE"] = "shadow"
    env["CORTEX_GRAPH_JOURNAL_PATH"] = str(JOURNAL_DIR)
    env["CORTEX_GRAPH_JOURNAL_METADATA"] = journal_metadata(
        args.project_id, args.graph, args.port
    )
    probe_out = SCRATCH / f"probe_{args.graph}_{int(time.time())}.json"
    cmd = [
        str(PY_BIN), str(Path(__file__).resolve()),
        "--child",
        "--probe-out", str(probe_out),
        # Truyền nguyên argv analyzer qua JSON — child không rebuild từ args
        # (các flag analyzer là unknown với argparse của probe nên sẽ mất).
        "--analyzer-argv-json", json.dumps(child_cli_args(args)),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
    log = (proc.stdout or "") + "\n---stderr---\n" + (proc.stderr or "")
    (SCRATCH / "logs").mkdir(parents=True, exist_ok=True)
    (SCRATCH / "logs" / f"probe_{args.graph}.log").write_text(log, encoding="utf-8")

    probe: dict = {}
    marker = "[FALLBACK_PROBE] "
    for line in reversed((proc.stdout or "").splitlines()):
        if line.startswith(marker):
            probe = json.loads(line[len(marker):])
            break
    if not probe and probe_out.exists():
        probe = json.loads(probe_out.read_text(encoding="utf-8"))
    if not probe:
        raise RuntimeError(
            f"[FALLBACK_PROBE] line not found (child exit={proc.returncode}):\n"
            f"{(proc.stderr or '')[-2000:]}"
        )
    return probe, log


def run_child_mode(args: argparse.Namespace) -> int:
    """Chạy trong subprocess: patch instrumentation rồi gọi ca.main(argv)."""
    sys.path.insert(0, str(REPO / "code-tiny"))

    from tools.common.harness_config import load_harness_config

    load_harness_config("/dev/null")

    from tools.csharp import csharp_analyzer as ca

    fallback_calls: dict[str, int] = {}

    original_parse = ca.parse_csharp_file

    def parse_probe(file_path, root):
        rel = os.path.relpath(file_path, root).replace("\\", "/")
        fallback_calls[rel] = fallback_calls.get(rel, 0) + 1
        return original_parse(file_path, root)

    ca.parse_csharp_file = parse_probe

    runner_state: dict = {"runner_present": False, "last_error": None,
                          "backend": None, "resolved": None, "errors": {},
                          "coverage": None, "workspace_kind": None}

    original_build = ca._build_roslyn_runner

    def build_probe(root, parsed_args, **kwargs):
        runner = original_build(root, parsed_args, **kwargs)
        if runner is None:
            return None
        runner_state["runner_present"] = True
        original_try_load = runner.try_load

        def try_load_probe(files, **kwargs2):
            cache = original_try_load(files, **kwargs2)
            runner_state["last_error"] = runner.last_error
            if cache is None:
                runner_state["resolved"] = None
            else:
                runner_state["backend"] = cache.backend
                runner_state["resolved"] = sorted(cache.success_by_relpath)
                runner_state["errors"] = dict(cache.errors_by_relpath)
            return cache

        runner.try_load = try_load_probe
        return runner

    ca._build_roslyn_runner = build_probe

    original_scan = ca._scan_csharp_files

    def scan_probe(root):
        files = original_scan(root)
        runner_state["scanned"] = [
            os.path.relpath(path, root).replace("\\", "/") for path in files
        ]
        return files

    ca._scan_csharp_files = scan_probe

    exit_code = 0
    worker_log_markers: list[str] = []
    import asyncio
    import contextlib
    import io

    stdout_capture = io.StringIO()
    analyzer_argv = (
        json.loads(args.analyzer_argv_json)
        if getattr(args, "analyzer_argv_json", None)
        else child_cli_args(args)
    )
    with contextlib.redirect_stdout(stdout_capture):
        exit_code = asyncio.run(ca.main(analyzer_argv))  # type: ignore[arg-type]
    captured = stdout_capture.getvalue()
    worker_log_markers = [
        line for line in captured.splitlines()
        if "[parse] Roslyn" in line or "[csharp][roslyn]" in line
    ]

    scanned = runner_state.get("scanned") or []
    resolved = runner_state.get("resolved") or []
    probe = {
        "method": (
            "monkeypatch tools.csharp.csharp_analyzer.parse_csharp_file "
            "(tree-sitter fallback-of-record) + RoslynFirstRunner.try_load, "
            "chạy subprocess re-exec với CLI contract giống parity py leg"
        ),
        "worker_requested": runner_state["runner_present"],
        "worker_last_error": runner_state["last_error"],
        "roslyn_backend": runner_state["backend"],
        "roslyn_resolved_count": len(resolved),
        "roslyn_resolved": resolved,
        "roslyn_per_file_errors": runner_state.get("errors") or {},
        "scanned_count": len(scanned),
        "fallback_trigger_count": len(fallback_calls),
        "fallback_calls": dict(sorted(fallback_calls.items())),
        "files_without_roslyn_payload": sorted(set(scanned) - set(resolved)),
        "exit_code": exit_code,
        "worker_log_markers": worker_log_markers[:10],
    }
    if args.probe_out:
        Path(args.probe_out).write_text(
            json.dumps(probe, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
        )
    print("[FALLBACK_PROBE] " + json.dumps(probe, ensure_ascii=True))
    return exit_code


def summarize(tag: str, probe: dict) -> list[str]:
    lines = [
        f"[probe:{tag}] worker_requested={probe['worker_requested']} "
        f"backend={probe.get('roslyn_backend')} "
        f"resolved={probe.get('roslyn_resolved_count')}/{probe.get('scanned_count')}",
        f"[probe:{tag}] fallback triggers: {probe['fallback_trigger_count']} file(s)",
    ]
    for rel in (probe.get("fallback_calls") or {}):
        lines.append(f"[probe:{tag}]   fallback → {rel}")
    if probe.get("worker_last_error"):
        lines.append(f"[probe:{tag}] worker last_error: {probe['worker_last_error'][:200]}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(CORPUS_DEFAULT))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--project-id", default="parity_probe")
    parser.add_argument("--graph", default="p03cs_probe_py")
    parser.add_argument("--probe-out", default=None)
    parser.add_argument("--break-worker", action="store_true",
                        help="control run: ép worker project sai để chứng minh "
                             "instrumentation bắt được fallback")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--analyzer-argv-json", default=None, help=argparse.SUPPRESS)
    # Analyzer CLI đi thẳng qua child_cli_args (rebuild từ args có cấu trúc)
    # nên parent dùng parse_known_args để nuốt các flag analyzer thừa.
    args, _extra = parser.parse_known_args()

    SCRATCH.mkdir(parents=True, exist_ok=True)
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)

    if args.child:
        return run_child_mode(args)

    args.root = str(Path(args.root).resolve())

    tag = "broken-worker" if args.break_worker else "healthy-worker"
    probe, _log = run_child(args)
    for line in summarize(tag, probe):
        print(line)

    verdict = (
        "FALLBACK EXERCISED" if probe["fallback_trigger_count"] else
        "FALLBACK NOT TRIGGERED (worker covered all files)"
    )
    print(f"[probe:{tag}] verdict: {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
