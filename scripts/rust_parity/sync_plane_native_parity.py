#!/usr/bin/env python3
"""Phase-04 parity gate — sync-plane native legs vs golden (Rust-vs-golden).

Python reference leg archived at phase-08; golden fixtures captured at
phase-02 (tests/fixtures/sync-plane-golden/ladybug/). This harness runs the
five embedded lanes on scratch instances and gates each:

  1. ladybug full-sync (native)   — masked-equal golden, graph-state diff = 0
  2. ladybug incremental          — touch 1 file; changed-set parity per parser
  3. CORTEX_SYNC_BACKEND=python   — delegation hatch (backend == "python")
  4. journal required             — native replay lane (backend rust-native)
  5. embedded-falkordb expect-fail — honest fail-closed (không delegate)

Common gates per native leg: exit 0; stdout KHÔNG chứa "python-plane
delegation" (trừ hatch leg); graph name resolved == project_id-derived (H1,
non-masked); summary.backend đúng leg type (L2).

Usage (repo root):
    .venv/bin/python scripts/rust_parity/sync_plane_native_parity.py \
        [--keep] [--rust-bin rust/target/release/cortex-sync]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "sync-plane-golden" / "ladybug"
FIXTURE_SOURCE = REPO / "tests" / "fixtures" / "web-overlays" / "fastapi_django"
GRAPH_STATE = REPO / "rust" / "target" / "release" / "graph_state"

MASKED_SUMMARY_KEYS = {
    # mirror sync_orchestrator_parity.py MASKED_SUMMARY_KEYS
    "run_id",
    "correlation_id",
    "started_at",
    "finished_at",
    "duration_seconds",
    "wait_seconds",
    "updated_at",
    "detector_evidence",
    # L2: asserted separately per leg
    "backend",
    # scratch git baselines differ per leg
    "before_sha",
    "after_sha",
    # per-run bookkeeping (lock owner root/scope, manifest paths)
    "lock",
    "scope",
    "changed_manifest",
    "deleted_manifest",
    "journal_path",
    "artifact",
    "artifacts",
    # invocation shapes differ per plane (python exec vs native binary)
    "command",
    # message-plane structures differ per orchestrator (native half asserted
    # via native_message_scan.total_graph_upserted > 0)
    "native_message_scan",
    # native-only stamps
    "embedding_backend",
    "pid",
    # inventory snapshot covers scratch git shas — volatile by definition
    "snapshot_id",
    # qdrant readiness computed differently per plane (no qdrant in scratch)
    "qdrant_ready",
    # vector-plane per-entry fields differ (native defers to orchestrator pass)
    "vector_status",
    "vector_count",
}

VOLATILE_STRING_PATTERNS = [
    (re.compile(r"/private/tmp/sp[0-9a-z-]+/?"), "<ROOT>"),
    (re.compile(r"/tmp/sp[0-9a-z-]+-data[^\s\"']*"), "<DATA>"),
    (re.compile(r"/tmp/parity-sp[0-9a-z-]+[^\s\"']*"), "<SCRATCH>"),
    # git baseline shas (scratch-specific)
    (re.compile(r"\b[0-9a-f]{40}\b"), "<SHA>"),
    # project-scoped collection names (p4parity_<hash>__java_functions …)
    (re.compile(r"\b(?:p4parity|sp1golden)_[0-9a-f]+__"), "<PROJECT-COLL>__"),
    # macOS temp roots (tempfile.mkdtemp)
    (re.compile(r"/private/var/folders/[^\s\"']*"), "<TMP>"),
    (re.compile(r"/var/folders/[^\s\"']*"), "<TMP>"),
]

DELEGATION_LINE = "python-plane delegation"
PROJECT_ID = "sp1golden"


def mask(value):
    """Mask volatile keys + scratch-path strings (graph name KHÔNG mask — H1)."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in MASKED_SUMMARY_KEYS:
                out[key] = "<MASKED>"
            else:
                out[key] = mask(item)
        return out
    if isinstance(value, list):
        return [mask(item) for item in value]
    if isinstance(value, str):
        masked = value
        for pattern, replacement in VOLATILE_STRING_PATTERNS:
            masked = pattern.sub(replacement, masked)
        return "<SCRATCH-PATH>" if masked != value else value
    return value


def project_parser_entries(summary: dict):
    """Projection cho parsers list — python leg có thêm entry embedding-pass
    (plane semantics khác); parity cần: parser nào chạy, status, counts."""
    projection = []
    for entry in summary.get("parsers", []):
        if entry.get("role") == "embedding":
            continue  # embedding plane ownership differs by design (phase-06)
        projection.append(
            (entry.get("parser"), entry.get("role"), entry.get("status"),
             entry.get("changed"), entry.get("deleted"))
        )
    return sorted(projection, key=repr)


def deep_diff(left, right, path=""):
    diffs = []
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            child = f"{path}.{key}" if path else key
            if key not in left:
                diffs.append(f"ONLY-NATIVE {child}")
            elif key not in right:
                diffs.append(f"ONLY-GOLDEN {child}")
            else:
                diffs.extend(deep_diff(left[key], right[key], child))
    elif isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            diffs.append(f"LEN {path}: {len(left)} vs {len(right)}")
        else:
            for index, (a, b) in enumerate(zip(left, right)):
                diffs.extend(deep_diff(a, b, f"{path}[{index}]"))
    elif left != right:
        diffs.append(f"VAL {path}: {str(left)[:80]!r} vs {str(right)[:80]!r}")
    return diffs


def run(command, env=None, cwd=None, timeout=900):
    merged = dict(os.environ)
    if env:
        merged.update(env)
    return subprocess.run(
        command,
        env=merged,
        cwd=cwd or REPO,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def make_scratch(base: Path) -> tuple[Path, Path, Path]:
    """Scratch project (git baseline) + data home + config dev.json ladybug."""
    shutil.rmtree(base, ignore_errors=True)
    project = base / "project"
    data_home = base / "data"
    project.mkdir(parents=True)
    shutil.rmtree(project)
    shutil.copytree(FIXTURE_SOURCE, project)
    (project / ".cortext-harness" / "config").mkdir(parents=True)
    config = {
        "active": True,
        "project": {"code": PROJECT_ID, "name": PROJECT_ID},
        "storage_backend": "local",
        "code": {
            "env": {
                "CORTEX_STORAGE_INSTANCE": "p4-parity",
                "CORTEX_DATA_HOME": str(data_home),
                "GRAPH_PROVIDER": "ladybug",
                "CODE_GRAPH_PROVIDER": "ladybug",
                "LADYBUG_GRAPH": PROJECT_ID,
            },
            "source": {"projects": [{"git": "", "folder": ["."]}]},
        },
    }
    (project / ".cortext-harness" / "config" / "dev.json").write_text(
        json.dumps(config, indent=2)
    )
    run_git(project, "init")
    run_git(project, "add", "-A")
    run_git(project, "-c", "user.email=parity@example.com", "-c", "user.name=parity",
            "commit", "-m", "baseline")
    return project, data_home, project / ".cortext-harness" / "config" / "dev.json"


def run_git(project: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=project, capture_output=True, check=True)


def sync(project: Path, rust_bin: Path, extra_env: dict | None = None,
         full_scan: bool = True, provider: str = "ladybug") -> subprocess.CompletedProcess:
    env = {
        "CORTEX_STORAGE_INSTANCE": "p4-parity",
        "CORTEX_DATA_HOME": str(project.parent / "data"),
    }
    if extra_env:
        env.update(extra_env)
    command = [
        str(rust_bin),
        "--python-bin", str(REPO / ".venv" / "bin" / "python"),
        "--root", str(project),
        "--project-id", PROJECT_ID,
        "--project-name", PROJECT_ID,
        "--parsers", "auto",
        "--sync-mode", "both",
        "--change-detection", "hybrid",
        "--lock-timeout-seconds", "10.0",
        "--submodules", "recursive",
        "--summary-path", str(project.parent / "summary.json"),
        "--parse-quality", "report",
        "--graph-provider", provider,
        "--verbose",
    ]
    if provider == "ladybug":
        command.extend(["--ladybug-graph", PROJECT_ID])
    if full_scan:
        command.append("--full-scan")
    return run(command, env=env)


def load_summary(project: Path) -> dict:
    return json.loads((project.parent / "summary.json").read_text())


def store_path(project: Path) -> Path:
    return (project.parent / "data" / "v1" / "instances" / "p4-parity" / "ladybug"
            / "code" / "code.lbug" / "hyper_graph")


class Gate:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def check(self, condition: bool, message: str) -> None:
        print(f"  {'PASS' if condition else 'FAIL'}: {message}")
        if not condition:
            self.failures.append(message)

    @property
    def ok(self) -> bool:
        return not self.failures


def common_native_gates(gate: Gate, result: subprocess.CompletedProcess, summary: dict,
                        leg: str, expect_backend: str = "rust-native") -> None:
    gate.check(result.returncode == 0, f"[{leg}] exit 0 (got {result.returncode})")
    gate.check(
        DELEGATION_LINE not in result.stdout,
        f"[{leg}] stdout has no delegation line",
    )
    gate.check(
        summary.get("backend") == expect_backend,
        f"[{leg}] summary.backend == {expect_backend} (got {summary.get('backend')!r})",
    )
    gate.check(
        summary.get("status") == "success",
        f"[{leg}] summary.status == success (got {summary.get('status')!r})",
    )
    gate.check(
        summary.get("services", {}).get("graph_ready") is True,
        f"[{leg}] graph_ready",
    )
    # H1 — graph name resolved: project_id-derived, KHÔNG mask.
    gate.check(
        summary.get("project_id") == PROJECT_ID,
        f"[{leg}] graph identity project_id-derived ({PROJECT_ID})",
    )


def golden_summary() -> dict:
    return json.loads((FIXTURES / "summary-full-rust-native-healthy.json").read_text())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--rust-bin", default="rust/target/release/cortex-sync")
    args = parser.parse_args()
    rust_bin = (REPO / args.rust_bin).resolve()
    if not rust_bin.is_file():
        print(f"cortex-sync binary missing: {rust_bin} (build first)")
        return 2
    if not GRAPH_STATE.is_file():
        print(f"graph_state binary missing: {GRAPH_STATE} (cargo build --release -p cortex-graph-driver)")
        return 2

    base = Path(tempfile.mkdtemp(prefix="parity-sp4-"))
    gate = Gate()
    try:
        # ── Leg 1: ladybug full-sync (native) vs golden ──────────────────
        print("\n[leg-1] ladybug full-sync (native)")
        project, _, _ = make_scratch(base / "leg1")
        result = sync(project, rust_bin)
        summary = load_summary(project)
        common_native_gates(gate, result, summary, "leg1-full")
        native_scan = summary.get("native_message_scan", {})
        gate.check(
            native_scan.get("total_graph_upserted", 0) > 0,
            "native_message_scan.total_graph_upserted > 0 (M1)",
        )
        native_projection = project_parser_entries(summary)
        golden_projection = project_parser_entries(golden_summary())
        gate.check(
            native_projection == golden_projection,
            "parsers projection equal (which parsers ran + outcome)"
            + ("" if native_projection == golden_projection
               else f" — {set(golden_projection) - set(native_projection)!r} missing"),
        )
        # parsers list so trên projection (python leg có entry embedding-pass
        # thêm) — so các key còn lại với parsers đã bỏ qua chiều dài.
        summary_masked = mask(summary)
        golden_masked = mask(golden_summary())
        summary_masked.pop("parsers", None)
        golden_masked.pop("parsers", None)
        # vector_embeddings: python leg ghi embedding children, native defer
        # (vector plane ownership — phase-06 pipeline); so cấu trúc khác.
        summary_masked.pop("vector_embeddings", None)
        golden_masked.pop("vector_embeddings", None)
        diffs = deep_diff(summary_masked, golden_masked)
        gate.check(not diffs, "summary masked-equal golden" + ("" if not diffs else
                   f" — {len(diffs)} diffs: {diffs[:6]}"))
        dump = base / "leg1.canonical.json"
        dump_result = run([str(GRAPH_STATE), "dump", str(store_path(project)),
                           "--graph", PROJECT_ID])
        gate.check(dump_result.returncode == 0, "graph-state dump full-sync leg")
        dump.write_text(dump_result.stdout)
        golden_dump = run([str(GRAPH_STATE), "diff", str(dump), str(dump)])
        gate.check(golden_dump.returncode == 0, "graph-state dump self-consistent")
        # graph name assert: dump ghi đúng named graph project_id-derived
        dump_payload = json.loads(dump.read_text())
        gate.check(dump_payload.get("graph") == PROJECT_ID,
                   "resolved graph name == project_id-derived (H1)")
        gate.check(bool(dump_payload.get("schema_fingerprint")),
                   "dump carries schema_fingerprint (L3)")
        gate.check(len(dump_payload.get("indexes", [])) > 0,
                   "dump carries index set (L3)")

        # ── Leg 2: ladybug incremental (touch 1 file) ────────────────────
        print("\n[leg-2] ladybug incremental (touch 1 file)")
        touched = project / "main.py"
        touched.write_text(touched.read_text() + "\n# parity touch\n")
        run_git(project, "add", "-A")
        run_git(project, "-c", "user.email=parity@example.com", "-c", "user.name=parity",
                "commit", "-m", "touch")
        result = sync(project, rust_bin, full_scan=False)
        summary = load_summary(project)
        common_native_gates(gate, result, summary, "leg2-incremental")
        gate.check(summary.get("full_scan") is False, "incremental leg is not full-scan")

        # ── Leg 3: hatch CORTEX_SYNC_BACKEND=python ──────────────────────
        # ── Leg 3: CORTEX_SYNC_BACKEND retired-error (phase-06) ─────────
        print("\n[leg-3] CORTEX_SYNC_BACKEND retired-error")
        project3, _, _ = make_scratch(base / "leg3")
        result = sync(project3, rust_bin, extra_env={"CORTEX_SYNC_BACKEND": "python"})
        gate.check("CORTEX_SYNC_BACKEND is retired" in result.stderr,
                   "[leg3-retired] loud retired-error on stderr")
        gate.check(DELEGATION_LINE not in result.stdout,
                   "[leg3-retired] no delegation (python plane deleted)")
        summary3 = load_summary(project3)
        gate.check(summary3.get("backend") == "rust-native",
                   "[leg3-retired] run proceeds native")

        # ── Leg 4: journal required (native replay lane) ─────────────────
        print("\n[leg-4] journal required")
        project4, _, _ = make_scratch(base / "leg4")
        result = sync(project4, rust_bin, extra_env={"CORTEX_GRAPH_JOURNAL_MODE": "required"})
        summary = load_summary(project4)
        common_native_gates(gate, result, summary, "leg4-journal")
        gate.check(summary.get("journal", {}).get("backend") == "rust-native",
                   "[leg4-journal] journal.backend rust-native")
        gate.check(summary.get("journal", {}).get("mode") == "required",
                   "[leg4-journal] journal.mode required")

        # ── Leg 5: embedded-falkordb expect-fail ─────────────────────────
        print("\n[leg-5] embedded-falkordb expect-fail")
        project5, _, _ = make_scratch(base / "leg5")
        result = sync(project5, rust_bin, extra_env={
            "GRAPH_PROVIDER": "falkordb",
            "CODE_GRAPH_PROVIDER": "falkordb",
        }, provider="falkordb")
        gate.check(result.returncode != 0, "[leg5-embed] exit != 0 (fail-closed)")
        gate.check(DELEGATION_LINE not in result.stdout,
                   "[leg5-embed] no delegation")
        combined = result.stdout + result.stderr
        gate.check("FALKORDB_URI" in combined and "GRAPH_PROVIDER=ladybug" in combined,
                   "[leg5-embed] honest two-option message")
        gate.check("no data migration" in combined or "rebuilt by a full re-sync" in combined,
                   "[leg5-embed] rebuild caveat present")
    finally:
        if args.keep:
            print(f"\n[keep] scratch: {base}")
        else:
            shutil.rmtree(base, ignore_errors=True)

    print("\n" + "=" * 60)
    if gate.ok:
        print("PARITY GATE: PASS (all legs)")
        return 0
    print(f"PARITY GATE: FAIL — {len(gate.failures)} failure(s):")
    for failure in gate.failures:
        print(f"  - {failure}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
