#!/usr/bin/env python
"""GLiNER sidecar — phase-13 contract: `{text, labels, threshold} →
[{entity, type, score, span}]` over doc-tiny's GLiNER pipeline.

GLiNER has no official ONNX export, so entity extraction stays a Python
sidecar (phase-13 decision record). Byte-identity is by construction: this
script imports doc-tiny's `entity_extractors` (same model loader, same
prediction + normalization code path) and only re-shapes the output into the
sidecar contract.

Modes:
  1. stdio (default)   — one JSON request on argv[1] or stdin:
                         {"text": "...", "labels": [...], "threshold": 0.3}
                         → [{"entity", "type", "score", "span"}]
  2. --verify [JSON]   — compare the sidecar contract output against
                         doc-tiny's in-process `extract_entities_gliner`
                         output for the same inputs; exits 0 on byte match.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOC_TINY = REPO_ROOT / "doc-tiny"
sys.path.insert(0, str(DOC_TINY))
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "code-tiny"))

DEFAULT_LABELS = [
    "PERSON", "ORG", "PRODUCT", "GPE", "DATE", "TECH", "CRYPTO", "STANDARD",
]
DEFAULT_THRESHOLD = 0.3


def extract_contract(text: str, labels: list[str], threshold: float) -> list[dict]:
    """Run doc-tiny's GLiNER path and reshape into the sidecar contract."""
    from entity_extractors import extract_entities_gliner

    entities, _relations = extract_entities_gliner(
        text, labels=labels, threshold=threshold
    )
    return [
        {
            "entity": item.get("name"),
            "type": item.get("type"),
            "score": item.get("confidence"),
            "span": [item.get("start_char"), item.get("end_char")],
        }
        for item in entities
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", nargs="?", help="inline JSON request")
    parser.add_argument("--verify", action="store_true", help=__doc__)
    parser.add_argument("--text", action="append", default=None)
    args = parser.parse_args()

    if args.verify:
        from entity_extractors import extract_entities_gliner

        texts = args.text or [
            "Acme Systems hợp tác với CCC tại Berlin theo chuẩn ISO 15118 "
            "về Plug&Charge và Digital Key 3.0.",
            "FalkorDB is a graph database on Redis; Qdrant stores the vectors "
            "for the DeerFlow pipeline deployed on AWS.",
        ]
        threshold = float(os.getenv("GLINER_THRESHOLD", str(DEFAULT_THRESHOLD)))
        ok = True
        for text in texts:
            contract = extract_contract(text, DEFAULT_LABELS, threshold)
            entities, _ = extract_entities_gliner(
                text, labels=DEFAULT_LABELS, threshold=threshold
            )
            expected = [
                {
                    "entity": item.get("name"),
                    "type": item.get("type"),
                    "score": item.get("confidence"),
                    "span": [item.get("start_char"), item.get("end_char")],
                }
                for item in entities
            ]
            contract_bytes = json.dumps(contract, ensure_ascii=False, sort_keys=True)
            expected_bytes = json.dumps(expected, ensure_ascii=False, sort_keys=True)
            if contract_bytes != expected_bytes:
                ok = False
                print(f"MISMATCH for text: {text[:60]}…")
                print(f"  sidecar = {contract_bytes[:400]}")
                print(f"  direct  = {expected_bytes[:400]}")
            else:
                print(f"OK ({len(contract)} entities): {text[:60]}…")
        print("GLINER SIDECAR VERIFY:", "PASS" if ok else "FAIL")
        return 0 if ok else 1

    raw = args.request or sys.stdin.read()
    request = json.loads(raw)
    text = request.get("text") or ""
    labels = request.get("labels") or DEFAULT_LABELS
    threshold = float(request.get("threshold", DEFAULT_THRESHOLD))
    print(json.dumps(extract_contract(text, labels, threshold), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
