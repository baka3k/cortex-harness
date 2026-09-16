#!/usr/bin/env python3
"""Rollback drill for the native local vector lane (phase-05 gate of
plans/260916-1154-native-vector-ingest-local). Three legs, mirroring the
rev2 plan:

* LEG A — pre-quarantine rollback (`CORTEX_VECTOR_BACKEND=python`): the
  sidecar still reads the legacy pickle store (STALE data) while the native
  writer stays FROZEN (zero writes). Both facts asserted, no fabrication of
  a "python children delegate" path (red-team C1).
* LEG B — post-quarantine rollback: with the JSON store owning the root and
  the pickle `collection/` subtree quarantined away, the python leg refuses
  LOUDLY (guard in vector_sidecar) — it never serves an empty store.
* LEG C — native convergence: two consecutive `--full-scan`-shaped
  full_replace passes through the real sync contract produce identical ids
  and counts on the JSON store.

Exit 0 = drill green; each leg prints one line for reports/phase05-drill.md.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
WORKER = HERE / "vector_worker.py"
WRITER_PROBE = REPO_ROOT / "rust" / "target" / "debug" / "examples" / "vector_writer_probe"
SIDECAR_PROBE = REPO_ROOT / "rust" / "target" / "debug" / "examples" / "sidecar_guard_probe"


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def write_pickle_store(store_dir: Path) -> None:
    """Legacy-format store written by qdrant-client (the pre-flip writer)."""
    import qdrant_client
    from qdrant_client import models as qmodels

    client = qdrant_client.QdrantClient(path=str(store_dir))
    client.create_collection(
        collection_name="code",
        vectors_config=qmodels.VectorParams(size=8, distance=qmodels.Distance.COSINE),
    )
    client.upsert(
        collection_name="code",
        points=[
            qmodels.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, "legacy|1")),
                vector=[0.5] * 8,
                payload={"project_id_normalized": "proj", "symbol": "LEGACY_STALE_SYMBOL"},
            )
        ],
    )
    client.close()


def worker_list(store_dir: Path) -> dict:
    proc = run(
        [str(VENV_PYTHON), str(WORKER), "--store", str(store_dir)],
        input=json.dumps({"op": "search", "collection": "code", "vector": [0.5] * 8, "limit": 5}) + "\n",
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return {"ok": False, "error": (proc.stderr or "worker died").strip().splitlines()[-1]}
    lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    return {"ok": True, "hits": lines[0].get("hits", [])}


def writer_frozen_check(store_dir: Path) -> dict:
    env = dict(os.environ)
    env["CORTEX_VECTOR_BACKEND"] = "python"
    env.pop("CORTEX_STORAGE_PROJECT_ID", None)
    env["QDRANT_CODE_PATH"] = str(store_dir)
    proc = run([str(WRITER_PROBE), "resolve", str(store_dir)], env=env)
    return json.loads(proc.stdout)


def native_resolve_check(store_dir: Path) -> dict:
    env = dict(os.environ)
    env["CORTEX_VECTOR_BACKEND"] = "rust"
    env.pop("CORTEX_STORAGE_PROJECT_ID", None)
    env["QDRANT_CODE_PATH"] = str(store_dir)
    proc = run([str(WRITER_PROBE), "resolve", str(store_dir)], env=env)
    return json.loads(proc.stdout)


def leg_a(root: Path) -> None:
    store = root / "leg-a"
    store.mkdir()
    write_pickle_store(store / "code")

    sidecar = worker_list(store / "code")
    symbols = [
        hit.get("payload", {}).get("symbol")
        for hit in sidecar.get("hits", [])
    ]
    stale_readable = sidecar.get("ok") and "LEGACY_STALE_SYMBOL" in symbols

    frozen = writer_frozen_check(store)
    frozen_ok = frozen.get("kind") == "unsupported" and "FROZEN at zero writes" in frozen.get("reason", "")

    if not (stale_readable and frozen_ok):
        print(f"[FAIL] leg-a/pre-quarantine: stale_readable={stale_readable} frozen={frozen}")
        raise SystemExit(1)
    print(
        "[PASS] leg-a/pre-quarantine: python leg reads legacy pickle (stale "
        "LEGACY_STALE_SYMBOL visible); native writer FROZEN (zero writes)"
    )


def leg_b(root: Path) -> None:
    instance = root / "leg-b"
    store = instance / "code"
    store.mkdir(parents=True)
    write_pickle_store(store)

    # Step 1 — native sync attempt on the legacy-only root: the open-time
    # guard refuses LOUDLY with the quarantine recipe (never touches pickle).
    pre = native_resolve_check(store)
    pre_refused = pre.get("kind") == "unsupported" and "legacy qdrant-client pickle store" in pre.get("reason", "")

    # Step 2 — quarantine the pickle subtree, then re-index natively.
    (store / "collection").rename(store / "collection.legacy-pickle.bak")
    write_json_store(store)

    # Step 3 — post-quarantine rollback: the python leg refuses loudly (the
    # JSON store owns the root; serving it as empty pickle would be a lie).
    python_leg = run([str(SIDECAR_PROBE), str(store)])
    body = json.loads(python_leg.stdout)
    refused_loudly = (
        not body.get("ok", True) and "refuses" in body.get("error", "") and "JSON vector store" in body.get("error", "")
    )

    if not (pre_refused and refused_loudly):
        print(f"[FAIL] leg-b/post-quarantine: pre_refused={pre} refusal={body}")
        raise SystemExit(1)
    print(
        "[PASS] leg-b/post-quarantine: guard blocks the legacy-only root loudly → quarantine → "
        "native re-index → python rollback leg refuses LOUDLY (no silent empty store)"
    )


def write_json_store(store_dir: Path) -> None:
    docs = [
        {
            "id": "id-1",
            "text": "native data",
            "payload": {
                "project_id": "proj",
                "project_id_normalized": "proj",
                "parser": "go",
                "root_scope": "/r",
                "file_path": "/r/a.go",
            },
        }
    ]
    proc = run(
        [str(WRITER_PROBE), "run", str(store_dir), "/dev/stdin"],
        input=json.dumps({"collection": "code", "dim": 8, "documents": docs, "full_replace": True}),
    )
    body = json.loads(proc.stdout)
    if not body.get("ok"):
        print(f"[FAIL] leg-b setup: {body}")
        raise SystemExit(1)


def leg_c(root: Path) -> None:
    store = root / "leg-c"
    docs = [
        {
            "id": f"id-{index}",
            "text": f"convergence text {index}",
            "payload": {
                "project_id": "proj",
                "project_id_normalized": "proj",
                "parser": "go",
                "root_scope": "/r",
                "file_path": f"/r/file{index}.go",
            },
        }
        for index in range(1, 4)
    ]
    scenario = json.dumps({"collection": "code", "dim": 8, "documents": docs, "full_replace": True})
    first = json.loads(run([str(WRITER_PROBE), "run", str(store), "/dev/stdin"], input=scenario).stdout)
    second = json.loads(run([str(WRITER_PROBE), "run", str(store), "/dev/stdin"], input=scenario).stdout)
    converged = (
        first.get("ok")
        and second.get("ok")
        and first.get("ids") == second.get("ids") == ["id-1", "id-2", "id-3"]
        and first.get("count") == second.get("count") == 3
    )
    if not converged:
        print(f"[FAIL] leg-c/native-convergence: first={first} second={second}")
        raise SystemExit(1)
    print(f"[PASS] leg-c/native-convergence: ids={first['ids']} count={first['count']} stable across re-runs")


def main() -> int:
    if not WRITER_PROBE.exists() or not SIDECAR_PROBE.exists():
        print("probes missing — build with: cargo build -p cortex-sync --example vector_writer_probe -p cortex-mcp --example sidecar_guard_probe")
        return 2
    with tempfile.TemporaryDirectory(prefix="vector-drill-") as tmp:
        root = Path(tmp)
        leg_a(root)
        leg_b(root)
        leg_c(root)
    print("[DRILL] 3/3 legs green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())