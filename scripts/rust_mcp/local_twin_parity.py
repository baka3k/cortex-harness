#!/usr/bin/env python3
"""Twin-capture mini-harness — native JSON store vs legacy pickle store
(phase-03 gate of plans/260916-1154-native-vector-ingest-local).

The SAME logical points are materialised twice on a small fixture:

* python leg  — qdrant-client embedded pickle store + the REAL MCP sidecar
  worker (`vector_worker.py`) answering list/meta/search, i.e. exactly the
  pre-flip code-lane reader path;
* rust leg    — `LocalQdrantStore` JSON engine (writer probe) + the lock-free
  `LocalQdrantReader` (reader probe), i.e. the post-flip reader path.

Comparison contract (vector_contract.py): structural equality except scores,
which must agree within TOLERANCE = 1e-6 (f32-normalized QdrantLocal vs f64
ratio LocalClient — research B6). Accepted divergences (phase01-engine.md
shape audit): native hits carry no `version`, and the rust probe returns the
raw engine payload (the MCP lane strips `text` itself) — both normalised here
before comparing.

Exit 0 = parity; exit 1 = divergence (prints the max score diff measured).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from vector_contract import REPO_ROOT as _ROOT, TOLERANCE, VENV_PYTHON  # noqa: E402

WORKER = Path(__file__).resolve().parent / "vector_worker.py"
STORAGE_PROBE = _ROOT / "rust" / "target" / "debug" / "examples" / "local_vector_probe"
DIM = 8
COLLECTION = "code"
PROJECT = "twinproj"


def build_fixture() -> list[dict]:
    """Deterministic points: stable uuid5-style string ids + fixed vectors."""
    points = []
    for index in range(8):
        vector = [((index * 31 + d * 17) % 97) / 97.0 + 0.001 for d in range(DIM)]
        point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"twin|{index}"))
        points.append(
            {
                "id": point_id,
                "vector": vector,
                "payload": {
                    "project_id_normalized": PROJECT if index % 4 else "other",
                    "parser": "go",
                    "root_scope": "/r",
                    "file_path": f"/r/file{index}.go",
                    "symbol": f"sym_{index}",
                    "text": f"raw body {index} — excluded lane-side",
                },
            }
        )
    return points


def python_leg(store_dir: Path, fixture: list[dict], query: list[float]) -> dict:
    """Write the pickle store with qdrant-client, then answer through the
    actual MCP sidecar worker protocol."""
    import qdrant_client
    from qdrant_client import models as qmodels

    client = qdrant_client.QdrantClient(path=str(store_dir))
    client.recreate_collection(
        collection_name=COLLECTION,
        vectors_config=qmodels.VectorParams(size=DIM, distance=qmodels.Distance.COSINE),
    )
    client.upsert(
        collection_name=COLLECTION,
        points=[
            qmodels.PointStruct(id=p["id"], vector=p["vector"], payload=dict(p["payload"]))
            for p in fixture
        ],
    )
    client.close()

    response: dict = {}
    proc = subprocess.run(
        [str(VENV_PYTHON), str(WORKER), "--store", str(store_dir)],
        input="\n".join(
            [
                json.dumps({"op": "list"}),
                json.dumps({"op": "meta", "collection": COLLECTION}),
                json.dumps(
                    {
                        "op": "search",
                        "collection": COLLECTION,
                        "vector": query,
                        "limit": 5,
                        "filter": {
                            "must": [
                                {
                                    "key": "project_id_normalized",
                                    "match": {"any": [PROJECT, f"{PROJECT}-ext"]},
                                }
                            ]
                        },
                        "using": None,
                    }
                ),
            ]
        )
        + "\n",
        capture_output=True,
        text=True,
        check=True,
    )
    lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    response["list"] = lines[0].get("collections", [])
    response["sizes"] = lines[1].get("sizes", {})
    response["hits"] = lines[2].get("hits", [])
    return response


def rust_leg(store_dir: Path, fixture: list[dict], query: list[float]) -> dict:
    """Write + read the JSON twin through the engine probes."""
    store_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(STORAGE_PROBE),
            "init",
            str(store_dir),
            "/dev/stdin",
            COLLECTION,
            str(DIM),
        ],
        input=json.dumps(fixture),
        capture_output=True,
        text=True,
        check=True,
    )
    proc = subprocess.run(
        [
            str(STORAGE_PROBE),
            "search",
            str(store_dir),
            "/dev/stdin",
        ],
        input=json.dumps(
            {
                "collection": COLLECTION,
                "vector": query,
                "limit": 5,
                "filter": {
                    "must": [
                        {
                            "key": "project_id_normalized",
                            "match": {"any": [PROJECT, f"{PROJECT}-ext"]},
                        }
                    ]
                },
            }
        ),
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(proc.stdout)
    return {
        "list": payload.get("collections", []),
        "sizes": {"default": DIM},
        "hits": payload.get("hits", []),
    }


def normalise_rust_hit(hit: dict) -> dict:
    """Lane-shape the raw engine hit: strip `text` (the MCP lane does it
    post-search) and drop the inert null `vector` key."""
    hit = json.loads(json.dumps(hit))
    hit.pop("vector", None)
    hit.get("payload", {}).pop("text", None)
    return hit


def compare(name: str, py: dict, rs: dict, fixture: list[dict]) -> float:
    problems: list[str] = []
    if py["list"] != rs["list"]:
        problems.append(f"list mismatch: python={py['list']} rust={rs['list']}")
    if py["sizes"] != rs["sizes"]:
        problems.append(f"sizes mismatch: python={py['sizes']} rust={rs['sizes']}")

    py_hits = py["hits"]
    rs_hits = [normalise_rust_hit(hit) for hit in rs["hits"]]
    if len(py_hits) != len(rs_hits):
        problems.append(f"hit count: python={len(py_hits)} rust={len(rs_hits)}")
    max_score_diff = 0.0
    for index, (a, b) in enumerate(zip(py_hits, rs_hits)):
        if str(a["id"]) != str(b["id"]):
            problems.append(
                f"hit[{index}] id order: python={a['id']} rust={b['id']}"
            )
            continue
        diff = abs(float(a["score"]) - float(b["score"]))
        max_score_diff = max(max_score_diff, diff)
        if diff > TOLERANCE:
            problems.append(
                f"hit[{index}] score diff {diff:.3e} > {TOLERANCE} ({a['id']})"
            )
        if a.get("payload") != b.get("payload"):
            problems.append(
                f"hit[{index}] payload: python={a.get('payload')} rust={b.get('payload')}"
            )
    scope_ids = {p["id"] for p in fixture if p["payload"]["project_id_normalized"] == PROJECT}
    if py_hits and {str(h["id"]) for h in py_hits} - scope_ids:
        problems.append("scope filter leaked foreign-project points (python leg)")
    if problems:
        for problem in problems:
            print(f"  [TWIN-FAIL] {name}: {problem}")
        raise SystemExit(1)
    return max_score_diff


def main() -> int:
    if not STORAGE_PROBE.exists():
        print(
            f"probe binary missing: {STORAGE_PROBE}\n"
            "build with: cargo build -p cortex-storage --example local_vector_probe"
        )
        return 2
    fixture = build_fixture()
    query = [0.5] * DIM
    with tempfile.TemporaryDirectory(prefix="vector-twin-") as tmp:
        root = Path(tmp)
        py = python_leg(root / "pickle", fixture, query)
        rs = rust_leg(root / "json", fixture, query)
        max_diff = compare("twin", py, rs, fixture)
    print(
        f"[TWIN-PASS] collections={rs['list']} sizes={rs['sizes']} "
        f"hits={len(rs['hits'])} max_score_diff={max_diff:.3e} (tolerance {TOLERANCE})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())