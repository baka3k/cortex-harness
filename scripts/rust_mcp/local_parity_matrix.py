#!/usr/bin/env python3
"""Full parity matrix for the native local vector lane (phase-04 gate of
plans/260916-1154-native-vector-ingest-local).

Legs (vector-lane phase-01 matrix, local-lane revision):

1. twin           — JSON engine vs legacy pickle store via the real MCP
                    sidecar worker (delegates to local_twin_parity.py).
2. stale-rename   — full-replace upsert, then an incremental pass whose
                    stale_filter (must + must_not has_id) deletes exactly the
                    renamed file's points — never the kept ones.
3. drift          — size-drift guard message on the local store equals the
                    cortex-sync `size_drift_message` contract.
4. scope          — prefix-expanded / no-filter / unregistered project filters
                    over the reader probe (project_scope_filter semantics).
5. explore-seeds  — per-collection searches + merge_hits semantics (dedupe by
                    str(id) keep-higher-score, stable sort desc, cut top_k).
6. tune-env       — QDRANT_HNSW_M/EF_CONSTRUCT/SCALAR_QUANT set: inert on the
                    local engine (create succeeds, sizes unchanged).
7. guard          — reader probe refuses a missing store dir and a
                    legacy-pickle-only root loudly (never serves empty).

Gate: every leg green, TWICE in a row (run the script two times). `--rss`
additionally runs the reader RSS gate (peak RSS < 300MB over a ~132MB store).

Exit 0 = all legs green; prints a one-line summary per leg for
reports/phase04-parity.md.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from vector_contract import REPO_ROOT as RUST_ROOT_alias, TOLERANCE  # noqa: E402,F401
from local_twin_parity import build_fixture, COLLECTION, DIM, PROJECT  # noqa: E402

RUST_TARGET = REPO_ROOT / "rust" / "target"
STORAGE_PROBE = RUST_TARGET / "debug" / "examples" / "local_vector_probe"
WRITER_PROBE = RUST_TARGET / "debug" / "examples" / "vector_writer_probe"
TWIN_SCRIPT = HERE / "local_twin_parity.py"

RESULTS: list[tuple[str, str]] = []


def leg(name: str, ok: bool, detail: str) -> None:
    RESULTS.append((name, "PASS" if ok else "FAIL"))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    if not ok:
        raise SystemExit(1)


def run_binary(binary: Path, args: list[str], stdin: str | None = None, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [str(binary), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
    )


def payload_for(index: int) -> dict:
    return {
        "project_id": PROJECT,
        "project_id_normalized": PROJECT,
        "parser": "go",
        "root_scope": "/r",
        "file_path": f"/r/file{index}.go",
    }


def doc(index: int) -> dict:
    return {
        "id": f"id-{index}",
        "text": f"deterministic text {index}",
        "payload": payload_for(index),
    }


# ── leg 2: stale-rename ──────────────────────────────────────────────────────
def leg_stale_rename(root: Path) -> None:
    store = root / "stale-rename"
    full = run_binary(
        WRITER_PROBE,
        ["run", str(store), "/dev/stdin"],
        stdin=json.dumps(
            {
                "collection": "code",
                "dim": DIM,
                "project_id": PROJECT,
                "documents": [doc(1), doc(2), doc(3)],
                "full_replace": True,
            }
        ),
    )
    body = json.loads(full.stdout)
    leg(
        "stale-rename/full-replace",
        body.get("ok") and body.get("ids") == ["id-1", "id-2", "id-3"],
        f"ids={body.get('ids')} count={body.get('count')}",
    )

    # file1.go renamed away: cleanup carries it, keep_ids are the survivors —
    # the phase-01 must_not/has_id fix is load-bearing here.
    rename = run_binary(
        WRITER_PROBE,
        ["run", str(store), "/dev/stdin"],
        stdin=json.dumps(
            {
                "collection": "code",
                "dim": DIM,
                "project_id": PROJECT,
                "documents": [doc(2), doc(3)],
                "cleanup_paths": ["/r/file1.go"],
                "full_replace": False,
            }
        ),
    )
    body = json.loads(rename.stdout)
    leg(
        "stale-rename/incremental",
        body.get("ok") and body.get("ids") == ["id-2", "id-3"],
        f"ids={body.get('ids')} (only the stale file1.go point died)",
    )


# ── leg 3: drift ─────────────────────────────────────────────────────────────
def leg_drift(root: Path) -> None:
    result = run_binary(
        WRITER_PROBE,
        ["drift", str(root / "drift"), str(DIM), str(DIM + 4)],
    )
    body = json.loads(result.stdout)
    expected = (
        f"Qdrant collection 'code' has vector size default={DIM}, "
        f"but the configured embedder produces {DIM + 4}"
    )
    leg(
        "drift/message-contract",
        body.get("error") == expected,
        f"error={body.get('error')!r}",
    )


# ── leg 4: scope filters ─────────────────────────────────────────────────────
def scope_search(store: Path, keys: list[str] | None) -> list[str]:
    query: dict = {"collection": COLLECTION, "vector": [0.5] * DIM, "limit": 50}
    if keys is not None:
        query["filter"] = {
            "must": [{"key": "project_id_normalized", "match": {"any": keys}}]
        }
    result = run_binary(STORAGE_PROBE, ["search", str(store), "/dev/stdin"], stdin=json.dumps(query))
    body = json.loads(result.stdout)
    return [str(hit["id"]) for hit in body.get("hits", [])]


def leg_scope(root: Path) -> None:
    store = root / "scope"
    fixture = [p for p in build_fixture()]
    run_binary(STORAGE_PROBE, ["init", str(store), "/dev/stdin", COLLECTION, str(DIM)], stdin=json.dumps(fixture))
    all_ids = sorted(p["id"] for p in fixture)
    project_ids = sorted(p["id"] for p in fixture if p["payload"]["project_id_normalized"] == PROJECT)

    # prefix-expanded keys (project_scope_filter emits prefix matches too)
    prefix = scope_search(store, [PROJECT, f"{PROJECT}-a", f"{PROJECT}-ab"])
    # no filter (unscoped query)
    unscoped = scope_search(store, None)
    # unregistered-only key
    ghost = scope_search(store, ["ghost-project"])

    ok = (
        sorted(prefix) == project_ids
        and sorted(unscoped) == all_ids
        and ghost == []
    )
    leg(
        "scope/prefix-empty-unregistered",
        ok,
        f"prefix={len(prefix)} unscoped={len(unscoped)} ghost={len(ghost)}",
    )


# ── leg 5: explore seeds + merge ─────────────────────────────────────────────
def leg_explore_seeds(root: Path) -> None:
    store = root / "seeds"
    shared = [
        {
            "id": f"seed-{index}",
            "vector": [(index * 13 + d * 7) % 89 / 89.0 + 0.01 for d in range(DIM)],
            "payload": {"project_id_normalized": PROJECT, "rank": index},
        }
        for index in range(6)
    ]
    run_binary(STORAGE_PROBE, ["init", str(store), "/dev/stdin", "seed_a", str(DIM)], stdin=json.dumps(shared))
    run_binary(STORAGE_PROBE, ["init", str(store), "/dev/stdin", "seed_b", str(DIM)], stdin=json.dumps(shared))

    per_collection = []
    for collection in ("seed_a", "seed_b"):
        result = run_binary(
            STORAGE_PROBE,
            ["search", str(store), "/dev/stdin"],
            stdin=json.dumps({"collection": collection, "vector": [0.4] * DIM, "limit": 5}),
        )
        body = json.loads(result.stdout)
        for hit in body["hits"]:
            hit["_collection"] = collection
        per_collection.append(body["hits"])

    # merge_hits semantics: dedupe by str(id) keep-higher-score, stable sort
    # desc, cut to top_k.
    order: list[str] = []
    combined: dict[str, dict] = {}
    for hits in per_collection:
        for hit in hits:
            key = str(hit["id"])
            if key not in combined:
                order.append(key)
                combined[key] = hit
            elif hit["score"] > combined[key]["score"]:
                combined[key] = hit
    merged = sorted(order, key=lambda k: -combined[k]["score"])[:5]
    ok = len(merged) == 5 and len(set(merged)) == 5
    leg(
        "explore-seeds/merge-dedupe",
        ok,
        f"collections=2 hits={sum(len(h) for h in per_collection)} merged={len(merged)} unique={len(set(merged))}",
    )


# ── leg 6: tune-env inert ────────────────────────────────────────────────────
def leg_tune_env(root: Path) -> None:
    store = root / "tune"
    result = run_binary(
        WRITER_PROBE,
        ["run", str(store), "/dev/stdin"],
        stdin=json.dumps(
            {
                "collection": "code",
                "dim": DIM,
                "project_id": PROJECT,
                "documents": [doc(1), doc(2)],
                "full_replace": True,
            },
        ),
        env_extra={
            "QDRANT_HNSW_M": "24",
            "QDRANT_HNSW_EF_CONSTRUCT": "64",
            "QDRANT_HNSW_EF": "128",
            "QDRANT_SCALAR_QUANT": "on",
        },
    )
    body = json.loads(result.stdout)
    info = run_binary(STORAGE_PROBE, ["info", str(store), "code"])
    info_body = json.loads(info.stdout)
    ok = (
        body.get("ok")
        and body.get("ids") == ["id-1", "id-2"]
        and info_body["sizes"] == {"default": DIM}
        and isinstance(info_body.get("payload_indexes"), list)
    )
    leg(
        "tune-env/inert",
        ok,
        f"sync ok={body.get('ok')} sizes={info_body['sizes']} indexes={len(info_body.get('payload_indexes') or [])}",
    )


# ── leg 7: reader guards ─────────────────────────────────────────────────────
def leg_guard(root: Path) -> None:
    missing = run_binary(
        STORAGE_PROBE,
        ["search", str(root / "does-not-exist"), "/dev/stdin"],
        stdin=json.dumps({"collection": "code", "vector": [0.5] * DIM, "limit": 5}),
    )
    missing_loud = missing.returncode != 0 and "not found" in (missing.stderr + missing.stdout)

    legacy = root / "legacy"
    (legacy / "collection" / "some").mkdir(parents=True)
    (legacy / "collection" / "some" / "storage.sqlite").write_bytes(b"x")
    legacy_result = run_binary(
        STORAGE_PROBE,
        ["search", str(legacy), "/dev/stdin"],
        stdin=json.dumps({"collection": "code", "vector": [0.5] * DIM, "limit": 5}),
    )
    legacy_loud = legacy_result.returncode != 0 and "legacy qdrant-client pickle store" in (
        legacy_result.stderr + legacy_result.stdout
    )
    leg(
        "guard/missing-and-legacy",
        missing_loud and legacy_loud,
        f"missing loud={missing_loud} legacy loud={legacy_loud} (reader is write-op-free at type level)",
    )


# ── optional: reader RSS gate ────────────────────────────────────────────────
def leg_rss(root: Path) -> None:
    release = RUST_TARGET / "release" / "examples" / "local_vector_probe"
    if not release.exists():
        print("  [RSS] building release probe…")
        subprocess.run(
            ["cargo", "build", "--release", "-p", "cortex-storage", "--example", "local_vector_probe"],
            cwd=REPO_ROOT / "rust",
            check=True,
            capture_output=True,
        )
    store = root / "rss-store"
    store.mkdir(parents=True)
    subprocess.run(
        [str(release), "gen-big", str(store), "12000", "512"],
        check=True,
        capture_output=True,
        text=True,
    )
    store_bytes = (store / "cortex-local-store.json").stat().st_size
    measured = subprocess.run(
        ["/usr/bin/time", "-l", str(release), "read-big", str(store), "20"],
        capture_output=True,
        text=True,
    )
    peak_bytes = 0
    for line in measured.stderr.splitlines():
        if "maximum resident set size" in line.lower():
            tokens = line.strip().split()
            # macOS /usr/bin/time -l: "<bytes>  maximum resident set size";
            # GNU time -v: "Maximum resident set size (kbytes): <kb>".
            if tokens[-1].lower() == "size":
                peak_bytes = int(tokens[0])
            else:
                peak_bytes = int(tokens[-1]) * 1024
            break
    gate = 300 * 1024 * 1024
    read_ok = measured.returncode == 0 and "read-big ok" in measured.stdout
    leg(
        "rss/reader-peak",
        read_ok and 0 < peak_bytes < gate and store_bytes > 100 * 1024 * 1024,
        f"store={store_bytes / 1e6:.0f}MB peak_rss={peak_bytes / 1e6:.0f}MB gate=300MB completed={read_ok}",
    )


def main() -> int:
    if not STORAGE_PROBE.exists() or not WRITER_PROBE.exists():
        print(
            "probe binaries missing — build with:\n"
            "  cargo build -p cortex-storage --example local_vector_probe\n"
            "  cargo build -p cortex-sync --example vector_writer_probe"
        )
        return 2
    with tempfile.TemporaryDirectory(prefix="vector-matrix-") as tmp:
        root = Path(tmp)
        twin = subprocess.run([sys.executable, str(TWIN_SCRIPT)], capture_output=True, text=True)
        leg("twin/native-vs-pickle", twin.returncode == 0, twin.stdout.strip().splitlines()[-1] if twin.stdout.strip() else twin.stderr.strip()[-200:])
        leg_stale_rename(root)
        leg_drift(root)
        leg_scope(root)
        leg_explore_seeds(root)
        leg_tune_env(root)
        leg_guard(root)
        if "--rss" in sys.argv:
            leg_rss(root)
    print(f"[MATRIX] {len(RESULTS)}/{len(RESULTS)} legs green (tolerance {TOLERANCE})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())