#!/usr/bin/env python3
"""Phase 10 (Scope A) parity gate: cortex-dev (Rust) vs cortex_harness/dev.py.

Gates
  1. Option-surface parity: for EVERY command path, parse `<cmd> --help` on
     both binaries and compare option names (aliases included) + subcommands.
  2. Output parity for `status`, `doctor`, `storage-layout` on a fixture
     project (line structures compared after normalizing volatile values).
  3. `init` on fresh dirs: identical scaffold file list, identical config JSON,
     identical active-project flip (sibling config deactivated).
  4. `ignore` add/list/remove semantics: identical stdout + config result.
  5. `sync code/doc --help` surface (subset of gate 1); an end-to-end sync run
     is covered by the orchestrator parity harness (phase 09) and is
     intentionally skipped here — see the report for the note.

Usage:
  .venv/bin/python scripts/rust_parity/dev_cli_parity.py [--rust-bin PATH] [--json]

Exit code 0 iff every gate passes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = REPO / ".venv" / "bin" / "python"
DEV_PY = REPO / "cortex_harness" / "dev.py"
DEFAULT_RUST_BIN = REPO / "rust" / "target" / "debug" / "cortex-dev"
RUST_BIN = Path(os.environ.get("CORTEX_DEV_BIN", str(DEFAULT_RUST_BIN)))

# Every command path in the tree (depth-first), mirroring dev.py's groups.
COMMAND_PATHS = [
    [],
    ["build"],
    ["install"],
    ["uninstall"],
    ["infra-up"],
    ["infra-down"],
    ["storage-init"],
    ["storage-layout"],
    ["storage-migrate-layout"],
    ["storage-backup"],
    ["storage-stop"],
    ["start"],
    ["stop"],
    ["doctor"],
    ["mcp-gates"],
    ["init"],
    ["status"],
    ["help"],
    ["export-db"],
    ["import-db"],
    ["export"],
    ["import"],
    ["sync"],
    ["sync", "code"],
    ["sync", "code", "add"],
    ["sync", "code", "all"],
    ["sync", "code", "stop"],
    ["sync", "doc"],
    ["sync", "doc", "add"],
    ["sync", "doc", "all"],
    ["sync", "doc", "stop"],
    ["journal"],
    ["journal", "purge"],
    ["journal", "status"],
    ["ignore"],
    ["ignore", "add"],
    ["ignore", "list"],
    ["ignore", "remove"],
    ["mcp"],
    ["mcp", "add"],
    ["mcp", "start"],
    ["harness"],
    ["harness", "context"],
    ["harness", "init"],
    ["harness", "run"],
    ["harness", "status"],
    ["harness", "task"],
    ["harness", "task", "add"],
    ["harness", "task", "list"],
    ["harness", "task", "show"],
    ["harness", "verify"],
    ["installer"],
    ["installer", "build"],
    ["installer", "install"],
    ["installer", "uninstall"],
]


def run(cmd: list[str], cwd: Path | None = None, stdin: str | None = None) -> tuple[int, str, str]:
    env = dict(os.environ)
    if cmd and cmd[0] == str(RUST_BIN):
        # A globally-installed `dev` resolves the harness repo explicitly;
        # mirror that here so the gate can run from any cwd.
        env["CORTEX_HARNESS_REPO_ROOT"] = str(REPO)
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def parse_help(text: str) -> tuple[set[str], set[str]]:
    """Extract option tokens and subcommand names from a Click-style help."""
    opts: set[str] = set()
    subs: set[str] = set()
    section = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == "Options:":
            section = "opts"
            continue
        if stripped == "Commands:":
            section = "cmds"
            continue
        if stripped.startswith("Usage:"):
            section = None
            continue
        if section == "opts" and line.startswith("  ") and stripped:
            body = line[2:]
            left = re.split(r"\s{2,}", body)[0]
            opts.update(re.findall(r"--[A-Za-z0-9-]+", left))
        elif section == "cmds":
            m = re.match(r"^  (\S+)\s+", line)
            if m:
                subs.add(m.group(1))
    opts.discard("--help")  # both sides always have it; checked separately
    return opts, subs


def normalize(text: str, fixture: Path | None = None) -> list[str]:
    """Normalize volatile values so only the line *structure* is compared."""
    out = text
    if fixture is not None:
        for variant in {str(fixture), os.path.realpath(str(fixture))}:
            out = out.replace(variant, "<FIXTURE>")
    out = re.sub(r"(?m)(?<![\w-])/(?:[\w.@+-]+/)*[\w.@+-]+", "<ABS>", out)
    out = re.sub(r"\b\d+\.\d+s\b", "<DUR>", out)
    out = re.sub(r"\d{4}-\d{2}-\d{2}T[\d:.+]+\b", "<TS>", out)
    out = re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", "<TS>", out)
    out = re.sub(r"\b\d{2,}(\.\d+)?(ms|s)\b", "<DUR>", out)
    # Machine-global process listings (doctor probes) are volatile.
    out = re.sub(r"running pid\(s\): [\d, ]+", "running pid(s): <PIDS>", out)
    return [line.rstrip() for line in out.splitlines()]


FIXTURE_DEV_CONFIG = {
    "active": True,
    "project": {"code": "parity_project", "name": "Parity Project"},
    "storage_backend": "local",
    "code": {
        "env": {
            "CORTEX_STORAGE_INSTANCE": "parity",
            "GRAPH_PROVIDER": "falkordb",
            "CODE_GRAPH_PROVIDER": "falkordb",
            "FALKORDB_GRAPH": "parity_project",
            "QDRANT_COLLECTION": "parity_project",
            "EMBEDDING_MODEL": "jinaai/jina-embeddings-v3",
            "BATCH_SIZE": "8",
            "MAX_EMBED_CHARS": "500",
            "device": "cpu",
        },
        "source": {"projects": [{"git": "", "folder": ["src"]}]},
    },
    "doc": {
        "env": {
            "CORTEX_STORAGE_INSTANCE": "parity",
            "GRAPH_PROVIDER": "falkordb",
            "DOC_GRAPH_PROVIDER": "falkordb",
            "FALKORDB_GRAPH": "parity_project_doc",
            "EMBEDDING_MODEL": "BAAI/bge-m3",
            "BATCH_SIZE": "8",
            "MAX_EMBED_CHARS": "500",
            "device": "cpu",
        },
        "source": {"projects": [{"git": "", "folder": ["docs"]}]},
    },
    "ignore": {"folders": ["legacy"]},
}

# Variants for the env-payload gate (phase-01 D2): stock FalkorDB, embedded
# Ladybug, and a remote-backed project (the three shapes the plan requires).
ENV_FIXTURE_VARIANTS = {
    "stock_falkordb": None,
    "ladybug": lambda cfg: (
        cfg["code"]["env"].update(
            {
                "GRAPH_PROVIDER": "ladybug",
                "CODE_GRAPH_PROVIDER": "ladybug",
                "LADYBUG_GRAPH": "parity_project",
            }
        ),
        cfg["doc"]["env"].update(
            {
                "GRAPH_PROVIDER": "ladybug",
                "DOC_GRAPH_PROVIDER": "ladybug",
                "LADYBUG_GRAPH": "parity_project_doc",
            }
        ),
    ),
    "remote_backend": lambda cfg: (
        cfg.update(
            {
                "storage_backend": "remote",
                "remote": {
                    "qdrant_url": "https://qdrant.example.cloud:6333",
                    "qdrant_api_key": "parity-key",
                    "falkordb_uri": "redis://falkordb.example.cloud:6379",
                    "falkordb_password": "parity-pass",
                    "falkordb_ssl": True,
                },
            }
        ),
        cfg["code"]["env"].pop("FALKORDB_GRAPH", None),
        cfg["doc"]["env"].pop("FALKORDB_GRAPH", None),
    ),
}


def make_env_fixture(base: Path, variant: str | None) -> Path:
    cfg_dir = base / ".cortext-harness" / "config"
    cfg_dir.mkdir(parents=True)
    dev = json.loads(json.dumps(FIXTURE_DEV_CONFIG))
    mutate = ENV_FIXTURE_VARIANTS.get(variant or "stock_falkordb")
    if mutate is not None:
        mutate(dev)
    (cfg_dir / "dev.json").write_text(json.dumps(dev, indent=2, ensure_ascii=False))
    return base


_PY_ENV_PAYLOAD_SCRIPT = r"""
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from cortex_harness.dev import (
    _code_env_for_process,
    _doc_env_for_process,
    _load_active_config,
)
project = Path(sys.argv[2])
role = sys.argv[3]
cfg, path = _load_active_config(project)
if role == "code":
    payload = _code_env_for_process(cfg, project)
else:
    payload = _doc_env_for_process(cfg, project)
payload["CORTEX_HARNESS_CONFIG_PATH"] = str(Path(path).resolve())
print(json.dumps(payload, sort_keys=True, default=str))
"""


def env_payload_py(project: Path, role: str) -> tuple[int, str, str]:
    script = f"import sys; sys.argv = ['x', {str(REPO)!r}, {str(project)!r}, {role!r}]\n" + _PY_ENV_PAYLOAD_SCRIPT
    env = dict(os.environ)
    return run([str(PY), "-c", script])


def env_payload_rust(rust_bin: Path, project: Path, role: str) -> tuple[int, str, str]:
    env = dict(os.environ)
    env["CORTEX_DEV_PARITY_ENV"] = role
    env["CORTEX_DEV_PARITY_PROJECT"] = str(project)
    env["CORTEX_HARNESS_REPO_ROOT"] = str(REPO)
    proc = subprocess.run([str(rust_bin)], capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout, proc.stderr


def make_fixture(base: Path) -> Path:
    cfg_dir = base / ".cortext-harness" / "config"
    cfg_dir.mkdir(parents=True)
    dev = json.loads(json.dumps(FIXTURE_DEV_CONFIG))
    (cfg_dir / "dev.json").write_text(json.dumps(dev, indent=2, ensure_ascii=False))
    prod = json.loads(json.dumps(FIXTURE_DEV_CONFIG))
    prod["active"] = False
    prod["project"] = {"code": "parity_prod", "name": "Parity Prod"}
    (cfg_dir / "prod.json").write_text(json.dumps(prod, indent=2, ensure_ascii=False))
    (base / "src").mkdir()
    (base / "src" / "main.py").write_text("print('hi')\n")
    (base / "docs").mkdir()
    (base / "docs" / "guide.md").write_text("# Guide\n")
    return base


class Gate:
    def __init__(self, name: str) -> None:
        self.name = name
        self.cases: list[tuple[str, bool, str]] = []

    def add(self, case: str, ok: bool, detail: str = "") -> None:
        self.cases.append((case, ok, detail))

    @property
    def passed(self) -> bool:
        return all(ok for _, ok, _ in self.cases)

    @property
    def passed_count(self) -> int:
        return sum(1 for _, ok, _ in self.cases if ok)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rust-bin", default=str(DEFAULT_RUST_BIN))
    ap.add_argument("--json", action="store_true", help="machine-readable summary")
    args = ap.parse_args()

    rust_bin = Path(args.rust_bin)
    global RUST_BIN
    RUST_BIN = rust_bin
    if not rust_bin.exists():
        print(f"[fatal] rust binary not found: {rust_bin} (build with: cargo build -p cortex-dev)")
        return 2
    if not PY.exists():
        print(f"[fatal] harness venv python not found: {PY}")
        return 2

    gates: list[Gate] = []

    # ── Gate 1: option surface for every command ─────────────────────────
    g1 = Gate("GATE 1 — help option surface (every command)")
    for path in COMMAND_PATHS:
        label = " ".join(path) if path else "<root>"
        _, py_out, py_err = run([str(PY), str(DEV_PY), *path, "--help"])
        _, rs_out, rs_err = run([str(rust_bin), *path, "--help"])
        py_opts, py_subs = parse_help(py_out)
        rs_opts, rs_subs = parse_help(rs_out)
        ok = py_opts == rs_opts and py_subs == rs_subs
        detail = ""
        if not ok:
            detail = (
                f"py-only options: {sorted(py_opts - rs_opts)}; "
                f"rust-only options: {sorted(rs_opts - py_opts)}; "
                f"py subs: {sorted(py_subs)}; rust subs: {sorted(rs_subs)}"
            )
            if py_err.strip():
                detail += f" | py stderr: {py_err.strip()[:200]}"
            if rs_err.strip():
                detail += f" | rust stderr: {rs_err.strip()[:200]}"
        g1.add(label, ok, detail)
    gates.append(g1)

    # ── Gate 2: status / doctor / storage-layout output ──────────────────
    g2 = Gate("GATE 2 — status/doctor/storage-layout output structure")
    tmp = Path(tempfile.mkdtemp(prefix="cortex-dev-parity-fixture-"))
    fixture = make_fixture(tmp)
    try:
        _, py_status, py_status_err = run(
            [str(PY), str(DEV_PY), "status", "--project-dir", str(fixture)]
        )
        _, rs_status, rs_status_err = run(
            [str(rust_bin), "status", "--project-dir", str(fixture)]
        )
        py_lines = normalize(py_status, fixture)
        rs_lines = normalize(rs_status, fixture)
        ok = py_lines == rs_lines
        detail = "" if ok else _line_diff(py_lines, rs_lines)
        g2.add("status", ok, detail)

        _, py_doc, _ = run([str(PY), str(DEV_PY), "doctor"], cwd=fixture)
        _, rs_doc, _ = run([str(rust_bin), "doctor"], cwd=fixture)
        py_doc_l = normalize(py_doc, fixture)
        rs_doc_l = normalize(rs_doc, fixture)
        ok = py_doc_l == rs_doc_l
        g2.add("doctor", ok, "" if ok else _line_diff(py_doc_l, rs_doc_l))

        _, py_layout, _ = run([str(PY), str(DEV_PY), "storage-layout"], cwd=fixture)
        _, rs_layout, _ = run([str(rust_bin), "storage-layout"], cwd=fixture)
        py_layout_l = normalize(py_layout, fixture)
        rs_layout_l = normalize(rs_layout, fixture)
        ok = py_layout_l == rs_layout_l
        g2.add(
            "storage-layout",
            ok,
            "" if ok else _line_diff(py_layout_l, rs_layout_l),
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    gates.append(g2)

    # ── Gate 3: init scaffold + active flip ──────────────────────────────
    g3 = Gate("GATE 3 — init scaffold + active-project flip")
    py_dir = Path(tempfile.mkdtemp(prefix="cortex-dev-parity-init-py-"))
    rs_dir = Path(tempfile.mkdtemp(prefix="cortex-dev-parity-init-rs-"))
    try:
        # Pre-existing sibling config must be deactivated by init.
        for base in (py_dir, rs_dir):
            cfg_dir = base / ".cortext-harness" / "config"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "prod.json").write_text(
                json.dumps({"active": True, "project": {"code": "old"}}, indent=2)
            )

        stdin = "\n" * 23  # every prompt answered with its default
        rc_py, _, err_py = run(
            [str(PY), str(DEV_PY), "init", str(py_dir)], stdin=stdin
        )
        rc_rs, _, err_rs = run([str(rust_bin), "init", str(rs_dir)], stdin=stdin)
        g3.add("init exit code 0", rc_py == 0 and rc_rs == 0, f"py={rc_py} rust={rc_rs} err_py={err_py[:200]} err_rs={err_rs[:200]}")

        def tree_state(base: Path) -> tuple[list[str], dict[str, str], bool]:
            files: list[str] = []
            hashes: dict[str, str] = {}
            for p in sorted(base.rglob("*")):
                rel = str(p.relative_to(base))
                if p.is_file():
                    files.append(rel)
                    hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
            dev = json.loads((base / ".cortext-harness" / "config" / "dev.json").read_text())
            prod = json.loads((base / ".cortext-harness" / "config" / "prod.json").read_text())
            return files, hashes, (dev.get("active") is True and prod.get("active") is False)

        py_files, py_hashes, py_flip = tree_state(py_dir)
        rs_files, rs_hashes, rs_flip = tree_state(rs_dir)
        g3.add("scaffold file list identical", py_files == rs_files,
               "" if py_files == rs_files else f"py-only={set(py_files)-set(rs_files)} rust-only={set(rs_files)-set(py_files)}")
        g3.add("scaffold file contents identical", py_hashes == rs_hashes,
               "" if py_hashes == rs_hashes else str({k for k in py_hashes if py_hashes.get(k) != rs_hashes.get(k)}))
        g3.add("dev.json active + prod.json deactivated", py_flip and rs_flip)
        py_cfg = json.loads((py_dir / ".cortext-harness" / "config" / "dev.json").read_text())
        rs_cfg = json.loads((rs_dir / ".cortext-harness" / "config" / "dev.json").read_text())
        g3.add("dev.json content identical", py_cfg == rs_cfg,
               "" if py_cfg == rs_cfg else _json_diff(py_cfg, rs_cfg))
    finally:
        shutil.rmtree(py_dir, ignore_errors=True)
        shutil.rmtree(rs_dir, ignore_errors=True)
    gates.append(g3)

    # ── Gate 4: ignore add/list/remove semantics ─────────────────────────
    g4 = Gate("GATE 4 — ignore add/list semantics")
    tmp_py = Path(tempfile.mkdtemp(prefix="cortex-dev-parity-ign-py-"))
    tmp_rs = Path(tempfile.mkdtemp(prefix="cortex-dev-parity-ign-rs-"))
    try:
        make_fixture(tmp_py)
        make_fixture(tmp_rs)
        sequences = [
            ["ignore", "add", "legacy-x", "generated-*"],
            ["ignore", "add", "legacy-x"],
            ["ignore", "list"],
            ["ignore", "remove", "legacy-x"],
            ["ignore", "remove", "not-there"],
            ["ignore", "list"],
        ]
        all_ok = True
        details = []
        for cmd in sequences:
            _, out_py, _ = run([str(PY), str(DEV_PY), *cmd, "--project-dir", str(tmp_py)])
            _, out_rs, _ = run([str(rust_bin), *cmd, "--project-dir", str(tmp_rs)])
            cfg_py = (tmp_py / ".cortext-harness" / "config" / "dev.json").read_text()
            cfg_rs = (tmp_rs / ".cortext-harness" / "config" / "dev.json").read_text()
            out_py_n = out_py.replace(str(tmp_py), "<D>")
            out_rs_n = out_rs.replace(str(tmp_rs), "<D>")
            cfg_py_n = cfg_py.replace(str(tmp_py), "<D>")
            cfg_rs_n = cfg_rs.replace(str(tmp_rs), "<D>")
            ok = out_py_n == out_rs_n and cfg_py_n == cfg_rs_n
            all_ok = all_ok and ok
            if not ok:
                details.append(f"cmd={' '.join(cmd)}\n  py :{out_py_n!r}\n  rs :{out_rs_n!r}\n  cfg equal: {cfg_py_n == cfg_rs_n}")
        g4.add("add/dup/list/remove sequences", all_ok, "\n".join(details))
    finally:
        shutil.rmtree(tmp_py, ignore_errors=True)
        shutil.rmtree(tmp_rs, ignore_errors=True)
    gates.append(g4)

    # ── Gate 5: sync help surface + end-to-end note ──────────────────────
    g5 = Gate("GATE 5 — sync code/doc --help surface")
    for path in (["sync", "code"], ["sync", "doc"], ["sync", "code", "all"]):
        _, py_out, _ = run([str(PY), str(DEV_PY), *path, "--help"])
        _, rs_out, _ = run([str(rust_bin), *path, "--help"])
        py_opts, py_subs = parse_help(py_out)
        rs_opts, rs_subs = parse_help(rs_out)
        g5.add(" ".join(path), py_opts == rs_opts and py_subs == rs_subs,
               f"py-only={sorted(py_opts - rs_opts)} rs-only={sorted(rs_opts - py_opts)}")
    gates.append(g5)
    print(
        "NOTE: an end-to-end `dev sync code` run is covered by the phase-09 "
        "orchestrator parity harness (incremental_sync.py is invoked "
        "unchanged); skipped here by design."
    )

    # ── Gate 6: per-process env payload parity (phase-01, plan D2) ───────
    g6 = Gate("GATE 6 — code/doc env payload per key (3 config shapes)")
    for variant in ("stock_falkordb", "ladybug", "remote_backend"):
        tmp_py6 = Path(tempfile.mkdtemp(prefix=f"cortex-dev-parity-env-{variant}-py-"))
        tmp_rs6 = Path(tempfile.mkdtemp(prefix=f"cortex-dev-parity-env-{variant}-rs-"))
        make_env_fixture(tmp_py6, variant)
        make_env_fixture(tmp_rs6, variant)
        try:
            for role in ("code", "doc"):
                rc_py, out_py, err_py = env_payload_py(tmp_py6, role)
                rc_rs, out_rs, err_rs = env_payload_rust(rust_bin, tmp_rs6, role)
                if rc_py != 0 or rc_rs != 0:
                    g6.add(
                        f"{variant}/{role} exit",
                        False,
                        f"py={rc_py} err={err_py.strip()[:200]} | rust={rc_rs} err={err_rs.strip()[:200]}",
                    )
                    continue
                try:
                    py_env = json.loads(out_py)
                    rs_env = json.loads(out_rs)
                except json.JSONDecodeError as exc:
                    g6.add(f"{variant}/{role} json", False, f"decode: {exc}; py={out_py[:120]!r} rs={out_rs[:120]!r}")
                    continue
                # The two fixtures live in different temp dirs; only the
                # config-path prefix differs by construction.
                for env_obj, base_dir in ((py_env, tmp_py6), (rs_env, tmp_rs6)):
                    cfg_path = env_obj.get("CORTEX_HARNESS_CONFIG_PATH", "")
                    env_obj["CORTEX_HARNESS_CONFIG_PATH"] = cfg_path.replace(str(base_dir), "<FIXTURE>")
                ok = py_env == rs_env
                detail = ""
                if not ok:
                    py_keys, rs_keys = set(py_env), set(rs_env)
                    diffs = [
                        f"    {k}: py={py_env[k]!r} rs={rs_env.get(k, '<missing>')!r}"
                        for k in sorted(py_keys & rs_keys)
                        if py_env[k] != rs_env.get(k)
                    ]
                    if py_keys - rs_keys:
                        diffs.append(f"    py-only keys: {sorted(py_keys - rs_keys)}")
                    if rs_keys - py_keys:
                        diffs.append(f"    rs-only keys: {sorted(rs_keys - py_keys)}")
                    detail = "\n".join(diffs[:25])
                g6.add(f"{variant}/{role}", ok, detail)
        finally:
            shutil.rmtree(tmp_py6, ignore_errors=True)
            shutil.rmtree(tmp_rs6, ignore_errors=True)
    gates.append(g6)

    # ── report ───────────────────────────────────────────────────────────
    if args.json:
        print(json.dumps({
            g.name: {
                "passed": g.passed,
                "cases": [{"case": c, "ok": ok, "detail": d} for c, ok, d in g.cases],
            }
            for g in gates
        }, indent=2))
    else:
        print("=" * 70)
        for g in gates:
            status = "PASS" if g.passed else "FAIL"
            print(f"{status}  {g.name}  ({g.passed_count}/{len(g.cases)} cases)")
            for case, ok, detail in g.cases:
                if not ok:
                    print(f"     FAIL: {case}")
                    if detail:
                        for dl in detail.splitlines():
                            print(f"       {dl}")
        print("=" * 70)
        print(f"TOTAL: {sum(g.passed_count for g in gates)}/{sum(len(g.cases) for g in gates)} cases passed")

    return 0 if all(g.passed for g in gates) else 1


def _line_diff(py_lines: list[str], rs_lines: list[str]) -> str:
    lines = [f"line counts: py={len(py_lines)} rust={len(rs_lines)}"]
    for i in range(max(len(py_lines), len(rs_lines))):
        a = py_lines[i] if i < len(py_lines) else "<missing>"
        b = rs_lines[i] if i < len(rs_lines) else "<missing>"
        if a != b:
            lines.append(f"  [{i}] py : {a!r}")
            lines.append(f"  [{i}] rs : {b!r}")
    return "\n".join(lines[:40])


def _json_diff(a: dict, b: dict, prefix: str = "") -> str:
    diffs = []
    keys = set(a) | set(b)
    for k in sorted(keys):
        va, vb = a.get(k, "<missing>"), b.get(k, "<missing>")
        if isinstance(va, dict) and isinstance(vb, dict):
            diffs.append(_json_diff(va, vb, prefix + k + "."))
        elif va != vb:
            diffs.append(f"{prefix}{k}: py={va!r} rs={vb!r}")
    return "\n".join(d for d in diffs if d)


if __name__ == "__main__":
    sys.exit(main())
