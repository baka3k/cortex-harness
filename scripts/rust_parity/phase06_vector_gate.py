#!/usr/bin/env python3
"""Phase-06 live vector gates (G4 snapshot parity, G5 quality, G6 wall-time).

Subcommands (all against a plain Qdrant HTTP server — measurement only, no
parity logic lives here):

  export   --collection NAME --out FILE [--url URL]
      Scroll every point WITH vectors → {id, vector, text, symbol_id,
      file_path, parser, root_scope} JSONL + collection info snapshot.

  compare  --before FILE --after FILE
      G5 evidence: count per collection, point-id set diff, per-point cosine
      joined BY POINT ID (deterministic uuid5 ⇒ the same document has the same
      id across runs), min/mean/worst cosine + below-gate counts.

  stale    --expected-deleted FILE --before FILE --after FILE
      G4 evidence: which ids each side has; which file_paths disappeared.

  info     --collection NAME [--url URL]
      Print collection config (for the no-recreate check).

Run from the repo root with .venv/bin/python.
"""

# === Phase-08 archive notice (2026-09-15) ===
# PY side archived at phase-08 cutover, fixtures = golden.
# Python analyzer entry points were retired at the phase-08 cutover;
# fixtures under tests/fixtures/ are now the golden reference.
# Do not attempt to re-run the Python side — tools/<lang>/<lang>_analyzer.py
# no longer exists. See plans/260915-analyzer-layer-rust-cutover/reports/phase08-cutover.md.
# === end archive notice ===


from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from urllib import request as urlrequest

DEFAULT_URL = "http://127.0.0.1:6333"


def rest(url: str, method: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urlrequest.Request(url + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    with urlrequest.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


def cmd_export(args: argparse.Namespace) -> int:
    url = args.url.rstrip("/")
    info = rest(url, "GET", f"/collections/{args.collection}")
    out = Path(args.out)
    count = 0
    offset = None
    with out.open("w", encoding="utf-8") as handle:
        while True:
            body: dict = {"limit": 64, "with_payload": True, "with_vectors": True}
            if offset is not None:
                body["offset"] = offset
            page = rest(url, "POST", f"/collections/{args.collection}/points/scroll", body)
            points = page["result"]["points"]
            for point in points:
                payload = point.get("payload") or {}
                handle.write(
                    json.dumps(
                        {
                            "id": point["id"],
                            "vector": point.get("vector"),
                            "text": payload.get("text"),
                            "symbol_id": payload.get("symbol_id"),
                            "file_path": payload.get("file_path"),
                            "parser": payload.get("parser"),
                            "root_scope": payload.get("root_scope"),
                            "node_type": payload.get("node_type"),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                count += 1
            offset = page["result"].get("next_page_offset")
            if offset is None:
                break
    meta = out.with_suffix(".meta.json")
    meta.write_text(json.dumps(info["result"].get("config", {}), indent=1) + "\n", encoding="utf-8")
    print(f"[export] {args.collection}: {count} points → {out} (+config {meta})")
    return 0


def _load(path: str) -> dict[str, dict]:
    points: dict[str, dict] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        points[str(row["id"])] = row
    return points


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def cmd_compare(args: argparse.Namespace) -> int:
    before, after = _load(args.before), _load(args.after)
    shared = sorted(set(before) & set(after))
    only_before = sorted(set(before) - set(after))
    only_after = sorted(set(after) - set(before))
    cosines: list[float] = []
    below = 0
    worst_value, worst_id = 2.0, ""
    text_mismatch = 0
    for pid in shared:
        b, a = before[pid], after[pid]
        if (b.get("text") or "") != (a.get("text") or ""):
            text_mismatch += 1
        if not b.get("vector") or not a.get("vector"):
            continue
        cosine = _cosine(b["vector"], a["vector"])
        cosines.append(cosine)
        if cosine < args.gate:
            below += 1
        if cosine < worst_value:
            worst_value, worst_id = cosine, pid
    print(json.dumps({
        "gate": args.gate,
        "count_before": len(before),
        "count_after": len(after),
        "shared_ids": len(shared),
        "only_before": len(only_before),
        "only_after": len(only_after),
        "text_field_mismatches": text_mismatch,
        "compared": len(cosines),
        "mean_cosine": (sum(cosines) / len(cosines)) if cosines else None,
        "worst_cosine": min(cosines) if cosines else None,
        "worst_point_id": worst_id,
        "points_below_gate": below,
        "verdict": "PASS" if cosines and below == 0 and text_mismatch == 0 else "FAIL",
    }, indent=1))
    return 0


def cmd_stale(args: argparse.Namespace) -> int:
    before, after = _load(args.before), _load(args.after)
    expected_deleted = {
        json.loads(line)["id"]
        for line in Path(args.expected_deleted).read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    gone = set(before) - set(after)
    remaining_expected = sorted(expected_deleted & set(after))
    survived = sorted(set(before) - expected_deleted - set(after))
    print(json.dumps({
        "expected_deleted": len(expected_deleted),
        "actually_deleted": len(gone),
        "expected_but_still_present": remaining_expected,
        "deleted_though_not_expected": survived[:20],
        "deleted_count_though_not_expected": len(survived),
        "verdict": "PASS" if not remaining_expected and not survived else "FAIL",
    }, indent=1))
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    url = args.url.rstrip("/")
    info = rest(url, "GET", f"/collections/{args.collection}")
    print(json.dumps(info["result"].get("config", {}), indent=1))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--collection", required=True)
    export.add_argument("--out", required=True)
    export.add_argument("--url", default=DEFAULT_URL)
    export.set_defaults(func=cmd_export)
    compare = commands.add_parser("compare")
    compare.add_argument("--before", required=True)
    compare.add_argument("--after", required=True)
    compare.add_argument("--gate", type=float, default=0.999)
    compare.set_defaults(func=cmd_compare)
    stale = commands.add_parser("stale")
    stale.add_argument("--expected-deleted", required=True)
    stale.add_argument("--before", required=True)
    stale.add_argument("--after", required=True)
    stale.set_defaults(func=cmd_stale)
    info = commands.add_parser("info")
    info.add_argument("--collection", required=True)
    info.add_argument("--url", default=DEFAULT_URL)
    info.set_defaults(func=cmd_info)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
