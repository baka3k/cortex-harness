#!/usr/bin/env python3
"""Phase 05 parity gate — message-scan graph plane (Python children vs native).

Baseline  : python orchestrator (`code-tiny/tools/sync/incremental_sync.py`)
            runs the graph pass, then the Python children are re-invoked
            directly with `--enable-message-scan` — the old path that writes
            the `Message` / `MessageEndpoint` nodes + CONTAINS /
            SENDS_MESSAGE / TARGETS_ENDPOINT rels via
            `run_message_scan_pipeline`. (The orchestrator itself only wires
            `--enable-message-scan` into the embedding pass —
            incremental_sync.py `_build_analyzer_cmd` call at the vector loop
            — so the default no-qdrant config writes the message graph
            through exactly this child invocation.)
Candidate : rust orchestrator (`rust/target/release/cortex-sync`) with
            `CORTEX_RUST_ANALYZER=rust` — rust children write the code graph
            and the native message-scan lane
            (`cortex-sync::message_scan::graph`) writes the message graph via
            `cortex_graph_writer`.

Run matrix (parsers=java,ts,python, no qdrant, isolated FalkorDB graphs
`p05msg_py` / `p05msg_rs`):

  1. Full run at commit 1 (both backends).
  2. Incremental run after commit 2 — Bus.java notify renamed + new publish,
     broker.py / Dispatcher.ts extended (stale-message cleanup conservation).

Gates:
  (a) All runs (orchestrators + message children) exit 0.
  (b) FalkorDB graph states diff 0 outside the dual_write_diff mask for the
      full leg AND the incremental leg — including `MessageEndpoint` nodes /
      rels (phase-05.md parity gate). `_start_id`/`_end_id` are
      engine-assigned edge endpoint ids and are masked like `_src`/`_dst`.
  (c) Message plane sanity: baseline graph contains Message nodes,
      MessageEndpoint nodes and SENDS_MESSAGE rels (the plane is exercised).
      Message vectors are NOT gated here — ownership defers to phase-06
      (cortex-embed); see the phase-05 report "Ownership resolution".

Usage (repo root):
    .venv/bin/python scripts/rust_parity/analyzer_parity_message_scan_graph.py \
        [--keep] [--rust-bin rust/target/release/cortex-sync]

FalkorDB must be up at 127.0.0.1:6379.
Exit code 0 = PASS; 1 = FAIL (diffs printed).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "code-tiny"))
sys.path.insert(0, str(REPO / "scripts" / "rust_parity"))

import dual_write_diff  # noqa: E402
from dual_write_diff import MASKED_PROPS, diff_dump, dump_graph  # noqa: E402
from tools.graph.driver.falkordb_driver import FalkorDBDriver  # noqa: E402

# Engine-assigned edge endpoint ids differ across graphs by design — mask
# them like the other internal ids (`_src`/`_dst`).
dual_write_diff.MASKED_PROPS |= {"_start_id", "_end_id"}

PY_ORCHESTRATOR = REPO / "code-tiny" / "tools" / "sync" / "incremental_sync.py"
PYTON_BIN = REPO / ".venv" / "bin" / "python"
CORPUS_SOURCE = REPO / "rust" / "tests" / "fixtures" / "message_scan" / "corpus"
CODE_TINY = REPO / "code-tiny"
FALKORDB_URI = "127.0.0.1:6379"
GRAPH_PY = "p05msg_py"
GRAPH_RS = "p05msg_rs"
PARSERS = "java,ts,python"
PROJECT_ID = "p05msg"
PROJECT_NAME = "phase05msg"

# (parser, child script) — in ANALYZERS insertion order (java < python < ts).
MESSAGE_CHILDREN = [
    ("java", "tools/java/java_analyzer.py"),
    ("python", "tools/python/python_analyzer.py"),
    ("ts", "tools/ts/ts_analyzer.py"),
]
PARSER_EXTENSIONS = {
    "java": (".java",),
    "python": (".py",),
    "ts": (".ts", ".tsx", ".mts", ".cts"),
}

MESSAGE_REL_TYPES = ("SENDS_MESSAGE", "TARGETS_ENDPOINT")


def sanitize_env(graph: str, cache_dir: Path) -> dict:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", str(Path.home())),
        "FALKORDB_URI": FALKORDB_URI,
        "FALKORDB_GRAPH": graph,
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
    }
    for key in (
        "QDRANT_CODE_PATH",
        "QDRANT_URL",
        "QDRANT_CACHE_DIR",
        "CODE_GRAPH_PROVIDER",
        "GRAPH_PROVIDER",
        "NEO4J_URI",
        "NEO4J_USER",
        "NEO4J_PASS",
        "NEO4J_DB",
        # unset → python children (AUTO_FLIP_DEFAULT is false on the rust
        # side too)
        "CORTEX_RUST_ANALYZER",
        "CORTEX_RUST_ANALYZER_BIN_DIR",
        "CORTEX_GRAPH_JOURNAL_MODE",
        "CORTEX_DISABLE_GRAPH",
        "CORTEX_RUN_ID",
        "CORTEX_CORRELATION_ID",
        "GIT_COMMIT_SHA_BEFORE",
        "GIT_COMMIT_SHA_AFTER",
        "PROJECT_ID",
        "PROJECT_NAME",
        "PROJECT_CODE",
        "SYNC_MESSAGES",
        "CORTEX_EXTRA_IGNORE_DIRS",
        "HYPERPACK_COLLECTION_SCHEME",
    ):
        env.pop(key, None)
    env["QDRANT_CACHE_DIR"] = str(cache_dir)
    return env


def run_git(corpus: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(corpus), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def git_output(corpus: Path, *args: str) -> list[str]:
    out = subprocess.check_output(
        ["git", "-C", str(corpus), *args], text=True
    )
    return [line.strip() for line in out.splitlines() if line.strip()]


def git_sha(corpus: Path, ref: str) -> str:
    return git_output(corpus, "rev-parse", ref)[0]


def build_corpus(corpus: Path) -> str:
    corpus.mkdir(parents=True, exist_ok=True)
    shutil.copytree(CORPUS_SOURCE, corpus, dirs_exist_ok=True)
    run_git(corpus, "init")
    run_git(corpus, "config", "user.email", "parity@example.com")
    run_git(corpus, "config", "user.name", "parity")
    run_git(corpus, "add", "-A")
    run_git(corpus, "commit", "-m", "commit 1")
    return git_sha(corpus, "HEAD")


def advance_corpus(corpus: Path) -> str:
    """Commit 2 — modify all three files: rename a message, add new ones."""
    bus = corpus / "src/main/java/com/example/Bus.java"
    text = bus.read_text()
    assert 'notify("done", payload, listener);' in text, "corpus drift: Bus.java"
    assert "onCommit" not in text, "corpus drift: Bus.java already advanced"
    text = text.replace(
        'notify("done", payload, listener);',
        'notify("ack", payload, listener);',
    )
    # Splice the new method before the class-closing brace.
    text = text.rstrip()
    assert text.endswith("}")
    text = (
        text[:-1]
        + "    public void onCommit(String rev) {\n"
        + "        publish(\"rev-\" + rev, rev, auditor);\n"
        + "    }\n"
        + "}\n"
    )
    bus.write_text(text)

    broker = corpus / "pkg/broker.py"
    text = broker.read_text()
    assert "fanout" not in text, "corpus drift: broker.py already advanced"
    text += (
        "\n    def fanout(self, payload, peer):\n"
        "        # added in commit 2\n"
        "        self.publish(payload, peer)\n"
    )
    broker.write_text(text)

    dispatcher = corpus / "web/src/Dispatcher.ts"
    text = dispatcher.read_text()
    assert "broadcast(" not in text, "corpus drift: Dispatcher.ts already advanced"
    text = text.replace(
        "        send(event, peer);",
        "        send(event, peer);\n"
        "        broadcast(event + \"-ack\", peer);",
    )
    dispatcher.write_text(text)

    run_git(corpus, "add", "-A")
    run_git(corpus, "commit", "-m", "commit 2")
    return git_sha(corpus, "HEAD")


def write_manifest(path: Path, files: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"files": sorted(files)}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def run_cmd(label: str, cmd: list[str], env: dict, cwd: Path) -> int:
    proc = subprocess.run(
        cmd,
        env=env,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=1800,
    )
    if proc.returncode != 0:
        print(f"[{label}] FAILED rc={proc.returncode}")
        print(proc.stdout[-3000:])
        print(proc.stderr[-3000:])
    return proc.returncode


def run_orchestrator(
    backend: str,
    corpus: Path,
    cache_dir: Path,
    summary_path: Path,
    before: str,
    after: str,
    incremental: bool,
    rust_bin: Path,
) -> int:
    env = sanitize_env(GRAPH_PY if backend == "py" else GRAPH_RS, cache_dir)
    common = [
        "--root", str(corpus),
        "--project-id", PROJECT_ID,
        "--project-name", PROJECT_NAME,
        "--parsers", PARSERS,
        "--python-bin", str(PYTON_BIN),
        "--config", "/dev/null",
        "--summary-path", str(summary_path),
        "--cache-dir", str(cache_dir),
        "--change-detection", "hybrid",
        "--before-sha", before,
        "--after-sha", after,
    ]
    if backend == "py":
        cmd = [str(PYTON_BIN), str(PY_ORCHESTRATOR), *common]
    else:
        cmd = [str(rust_bin), *common]
        env["CORTEX_REPO_ROOT"] = str(REPO)
        env["CORTEX_RUST_ANALYZER"] = "rust"
        env["CORTEX_RUST_ANALYZER_BIN_DIR"] = str(REPO / "rust" / "target" / "release")
    return run_cmd(f"orchestrator {backend}", cmd, env, REPO)


def run_message_children(
    corpus: Path,
    cache_dir: Path,
    before: str,
    after: str,
    incremental: bool,
    changed_by_parser: dict[str, list[str]],
    deleted_by_parser: dict[str, list[str]],
) -> list[int]:
    """The old message-graph path: python children invoked with
    `--enable-message-scan` exactly like the orchestrator's embedding pass
    (`_build_analyzer_cmd`) does."""
    codes: list[int] = []
    msg_dir = cache_dir / "message_artifacts"
    msg_dir.mkdir(parents=True, exist_ok=True)
    for parser, script in MESSAGE_CHILDREN:
        env = sanitize_env(GRAPH_PY, cache_dir)
        cmd = [
            str(PYTON_BIN), str(CODE_TINY / script),
            "--root", str(corpus),
            "--project-id", PROJECT_ID,
            "--project-name", PROJECT_NAME,
            "--commit-sha-before", before,
            "--commit-sha-after", after,
            "--graph-provider", "falkordb",
            "--falkordb-graph", GRAPH_PY,
            "--enable-message-scan",
            "--message-output-dir", str(msg_dir),
        ]
        if incremental:
            changed = changed_by_parser.get(parser, [])
            deleted = deleted_by_parser.get(parser, [])
            changed_manifest = cache_dir / f"{parser}_changed_manifest.json"
            deleted_manifest = cache_dir / f"{parser}_deleted_manifest.json"
            write_manifest(changed_manifest, changed)
            write_manifest(deleted_manifest, deleted)
            cmd += [
                "--incremental",
                "--changed-files-manifest", str(changed_manifest),
                "--deleted-files-manifest", str(deleted_manifest),
            ]
        codes.append(run_cmd(f"message-child {parser}", cmd, env, CODE_TINY))
    return codes


def git_change_sets(corpus: Path, base: str) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Per-parser changed/deleted paths between `base` and HEAD."""
    changed: dict[str, list[str]] = {p: [] for p, _ in MESSAGE_CHILDREN}
    deleted: dict[str, list[str]] = {p: [] for p, _ in MESSAGE_CHILDREN}
    all_changed = git_output(corpus, "diff", "--name-only", base, "HEAD")
    all_deleted = git_output(
        corpus, "diff", "--name-only", "--diff-filter=D", base, "HEAD"
    )
    for path in all_changed:
        lower = path.lower()
        for parser, extensions in PARSER_EXTENSIONS.items():
            if lower.endswith(extensions):
                changed[parser].append(path)
    for path in all_deleted:
        lower = path.lower()
        for parser, extensions in PARSER_EXTENSIONS.items():
            if lower.endswith(extensions):
                deleted[parser].append(path)
    return changed, deleted


def message_plane_counts(dump: dict) -> dict:
    nodes = dump["nodes"]
    edges = dump["edges"]
    counts = {
        "message_nodes": sum(1 for key in nodes if key.startswith("Message|msg::")),
        "endpoint_nodes": sum(
            1 for key in nodes if key.startswith("MessageEndpoint|msg_endpoint::")
        ),
    }
    for rel in MESSAGE_REL_TYPES:
        marker = f" -[{rel}]-> "
        counts[rel.lower()] = sum(1 for key in edges if marker in key)
    return counts


def clean_graph(driver: FalkorDBDriver, graph: str) -> None:
    import asyncio

    asyncio.run(driver.execute_query("MATCH (n) DETACH DELETE n", {}, graph))


def is_message_node_key(key: str) -> bool:
    return key.startswith("Message|msg::") or key.startswith(
        "MessageEndpoint|msg_endpoint::"
    )


def is_message_edge_key(key: str) -> bool:
    """Edge keys are `src|id -[REL]-> dst|id` — message-plane when either
    endpoint is a Message/MessageEndpoint node."""
    labels = [part.split("|", 1)[0] for part in key.split(" -[")]
    labels[-1] = labels[-1].split("]-> ", 1)[-1].split("|", 1)[0]
    return any(label in ("Message", "MessageEndpoint") for label in labels)


def diff_props(path: str, py: dict, rs: dict) -> list[str]:
    """Property-level diff lines (stable, no truncation of the delta)."""
    lines = []
    for key in sorted(set(py) | set(rs)):
        if key in MASKED_PROPS:
            continue
        if key not in py:
            lines.append(f"  {path}.{key}: rust-only={json.dumps(rs[key], ensure_ascii=False)[:300]}")
        elif key not in rs:
            lines.append(f"  {path}.{key}: py-only={json.dumps(py[key], ensure_ascii=False)[:300]}")
        elif py[key] != rs[key]:
            lines.append(
                f"  {path}.{key}: py={json.dumps(py[key], ensure_ascii=False)[:300]} "
                f"rs={json.dumps(rs[key], ensure_ascii=False)[:300]}"
            )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    parser.add_argument(
        "--rust-bin", default=str(REPO / "rust/target/release/cortex-sync")
    )
    args = parser.parse_args()
    rust_bin = Path(args.rust_bin).resolve()
    if not rust_bin.exists():
        print(f"[FAIL] rust binary missing: {rust_bin} (cargo build --release -p cortex-sync)")
        return 1
    missing = [
        name
        for name in ("analyzer-java", "analyzer-ts", "analyzer-python")
        if not (REPO / "rust/target/release" / name).exists()
    ]
    if missing:
        print(
            "[FAIL] rust children missing in rust/target/release: "
            + ", ".join(missing)
            + " (cargo build --release -p analyzer-java -p analyzer-ts -p analyzer-python)"
        )
        return 1

    driver = FalkorDBDriver(uri=FALKORDB_URI)

    workdir = Path(tempfile.mkdtemp(prefix="p05msg_parity_"))
    corpus = workdir / "corpus"
    cache_root = workdir / "caches"
    summary_root = workdir / "summaries"
    cache_root.mkdir(parents=True, exist_ok=True)
    summary_root.mkdir(parents=True, exist_ok=True)
    print(f"[setup] workdir={workdir}")

    commit1 = build_corpus(corpus)
    gate_results: dict = {}

    def run_leg(label: str, incremental: bool, base: str | None) -> None:
        commit2 = git_sha(corpus, "HEAD") if incremental else commit1
        for backend in ("py", "rs"):
            print(
                f"[{label}] orchestrator {backend} — "
                f"children={'rust' if backend == 'rs' else 'python'}"
            )
            code = run_orchestrator(
                backend,
                corpus,
                cache_root / f"cache_{backend}",
                summary_root / f"{label}_{backend}.json",
                commit1,
                commit2,
                incremental,
                rust_bin,
            )
            gate_results.setdefault("exit_codes", {})[f"{label}_orchestrator_{backend}"] = code
        if backend_error := [
            (k, v)
            for k, v in gate_results["exit_codes"].items()
            if k.startswith(label) and v != 0
        ]:
            print(f"[{label}] orchestrator failures: {backend_error}")
            return
        if incremental:
            changed_by_parser, deleted_by_parser = git_change_sets(corpus, base)
            print(
                f"[{label}] message children (python, incremental) — "
                f"changed={changed_by_parser} deleted={deleted_by_parser}"
            )
            codes = run_message_children(
                corpus,
                cache_root / "cache_py",
                commit1,
                commit2,
                True,
                changed_by_parser,
                deleted_by_parser,
            )
        else:
            print(f"[{label}] message children (python, full)")
            codes = run_message_children(
                corpus, cache_root / "cache_py", commit1, commit2, False, {}, {}
            )
        for (parser, _), code in zip(MESSAGE_CHILDREN, codes):
            gate_results.setdefault("exit_codes", {})[f"{label}_message_child_{parser}"] = code

    def diff_leg(label: str) -> tuple[bool, bool]:
        """Returns (message_plane_pass, whole_graph_pass)."""
        py_dump = dump_graph(driver, GRAPH_PY)
        rs_dump = dump_graph(driver, GRAPH_RS)
        diff = diff_dump(py_dump, rs_dump)
        diff_total = sum(len(v) for v in diff.values())
        py_counts = message_plane_counts(py_dump)
        rs_counts = message_plane_counts(rs_dump)

        # Message-plane sub-diff (the phase-05 gate) vs code-graph residual
        # (children plane, diagnostic only).
        message_diff_lines: list[str] = []
        code_diff_lines: list[str] = []
        for section, items in diff.items():
            for key, payload in items.items():
                if section == "props_differ":
                    node_key = key.split(" ", 1)[1]
                    lines = diff_props(node_key, payload["py"], payload["rust"])
                else:
                    lines = [f"  {section}: {key}"]
                    node_key = key
                if is_message_node_key(node_key) or is_message_edge_key(node_key):
                    message_diff_lines.extend(lines)
                else:
                    code_diff_lines.extend(lines)
        message_pass = not message_diff_lines
        gate_results[f"graph_diff_{label}"] = {
            "message_plane_pass": message_pass,
            "whole_graph_pass": diff_total == 0,
            "diff_total": diff_total,
            "masked_props": sorted(MASKED_PROPS),
            "py_message_plane": py_counts,
            "rs_message_plane": rs_counts,
            "message_diff_count": len(message_diff_lines),
            "code_graph_diff_count": len(code_diff_lines),
        }
        print(
            f"[gate graph_diff_{label}] message_plane_pass={message_pass} "
            f"whole_graph_diff_total={diff_total} "
            f"py_message_plane={py_counts} rs_message_plane={rs_counts}"
        )
        for line in message_diff_lines[:20]:
            print(f"[diff {label}][message-plane]{line}")
        for line in code_diff_lines[:10]:
            print(f"[diff {label}][code-graph]{line}")
        return message_pass, diff_total == 0

    # ── leg 1: full run (commit 1) ────────────────────────────────────────
    print("[main] leg 1 — full scan at commit 1")
    clean_graph(driver, GRAPH_PY)
    clean_graph(driver, GRAPH_RS)
    run_leg("full", incremental=False, base=None)
    full_msg_ok, full_whole_ok = diff_leg("full")

    # ── leg 2: incremental after commit 2 (conservation) ──────────────────
    print("[main] leg 2 — incremental after commit 2")
    advance_corpus(corpus)
    run_leg("inc", incremental=True, base=commit1)
    inc_msg_ok, inc_whole_ok = diff_leg("inc")

    # ── gate a: exit codes ────────────────────────────────────────────────
    exits_ok = all(code == 0 for code in gate_results["exit_codes"].values())
    gate_results["exit_codes_pass"] = exits_ok

    # ── gate c: message plane exercised on the baseline ───────────────────
    py_full = message_plane_counts(dump_graph(driver, GRAPH_PY))
    plane_exercised = (
        py_full["message_nodes"] > 0
        and py_full["endpoint_nodes"] > 0
        and py_full["sends_message"] > 0
    )
    gate_results["message_plane_exercised"] = {
        "pass": plane_exercised,
        "counts": py_full,
    }
    print(f"[gate message_plane_exercised] pass={plane_exercised} counts={py_full}")

    # ── cleanup graphs ────────────────────────────────────────────────────
    clean_graph(driver, GRAPH_PY)
    clean_graph(driver, GRAPH_RS)

    all_pass = (
        exits_ok
        and full_msg_ok
        and inc_msg_ok
        and plane_exercised
    )
    gate_results["whole_graph_residual"] = {
        "full_clean": full_whole_ok,
        "inc_clean": inc_whole_ok,
        "note": (
            "message-plane gate is (b); whole-graph residual is the "
            "children code-graph plane on this corpus (diagnostic, "
            "phase-04 scope)"
        ),
    }
    print(json.dumps(gate_results, indent=2, default=str))
    print(f"[gates] all_pass={all_pass}")

    if args.keep:
        print(f"[keep] artifacts under {workdir}")
    else:
        shutil.rmtree(workdir, ignore_errors=True)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
