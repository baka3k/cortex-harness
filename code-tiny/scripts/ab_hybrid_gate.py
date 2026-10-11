#!/usr/bin/env python3
"""A/B gate for hybrid retrieval on a REAL corpus: dense-only vs fused RRF.

Companion quality gate to ``backfill_hybrid_sparse.py``: runbook
(docs/UNIFIED_INGEST_QUERY_CONTRACT.md → "Hybrid retrieval enablement",
step 3) requires dense-only (flag off) vs fused (flag on) on the SAME
collection against a real project corpus before enabling ``EMBED_HYBRID``
per project. Gate: no ground-truth regression, top-k overlap stable.

Non-destructive: each target collection is CLONED under a ``_hybridab``
suffix, the clone is recreated with the named sparse vector and re-encoded
from its own payloads, and the original is never written. The clone is
dropped when the run ends (success or failure). Both arms query the SAME
clone, so encode drift cannot bias the comparison. Everything runs through
the production chokepoints (``get_code_qdrant_store`` / doc twin,
``embedding_marker.stamp``, ``embed_runtime.query_vector_pair``,
``qdrant_query_support.search_collection`` — sentinel exclusion and
``EMBED_FUSE_K`` included).

Target resolution:

* ``--collection C`` gates exactly that collection;
* ``--project-id X`` (code mode) discovers the project's physical
  per-analyzer collections — ``X_<hash>__<parser>_functions`` as named by
  incremental sync — and gates each in turn (overall verdict = worst);
* ``--project-id X --doc-mode`` gates ``X_doc`` (plain name, as created).

Corpus-derived identifier queries (most-duplicated names as duplicate
stress + longest distinctive names; compiler-mangled ``$`` names skipped)
work on any corpus without configuration. Paraphrase queries need
corpus-specific ground truth: pass ``--queries`` a JSON file of
``{"name", "class", "text", "truth_name"}`` objects.

Large corpora: ``--max-points N`` subsamples evenly (deterministic stride)
before encoding — a smoke gate in minutes instead of a full sweep in hours;
``"sampled": true`` is stamped on the summary.

Beyond the two production arms, measurement-only arms characterize a
regression when one appears: weighted RRF (0.7/0.3 and 0.85/0.15, computed
client-side — the client wrapper rejects a raw ``Rrf`` query on local mode)
and sparse-only.

Usage:
  python code-tiny/scripts/ab_hybrid_gate.py --project-id X
  python code-tiny/scripts/ab_hybrid_gate.py --collection C
  python code-tiny/scripts/ab_hybrid_gate.py --project-id X --doc-mode
  python code-tiny/scripts/ab_hybrid_gate.py --project-id X --max-points 300
  python code-tiny/scripts/ab_hybrid_gate.py --collection C \
      --queries my_queries.json --top-k 10 --out-dir /tmp
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

CODE_TINY = Path(__file__).resolve().parents[1]
REPO_ROOT = CODE_TINY.parent
DOC_TINY = REPO_ROOT / "doc-tiny"
for entry in (CODE_TINY, DOC_TINY, REPO_ROOT):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from qdrant_client.http import models as qmodels  # noqa: E402

from tools.common import embed_runtime, embedding_marker  # noqa: E402
from tools.common.local_qdrant import (  # noqa: E402
    discover_code_collections,
    hybrid_document_text,
)
from tools.common.qdrant_query_support import (  # noqa: E402
    HYBRID_SPARSE_VECTOR_NAME,
    _with_sentinel_exclusion,
    fuse_k_from_env,
    search_collection,
)

CLONE_SUFFIX = "_hybridab"

# Encode-time memory guard: attention memory scales with batch × seq², and
# analyzer payloads (the ``code`` field) can be whole files that reach the
# model's 8192-token truncation — a fixed batch of those blew 8 GB on MPS.
# Pack batches by estimated token cost instead of a fixed row count.
_EST_CHAR_PER_TOKEN = 3
_TOKEN_BUDGET = 16000


def _pack_batches(texts: List[str], max_size: int) -> List[List[int]]:
    large_cost = _TOKEN_BUDGET // 4
    batches: List[List[int]] = []
    current: List[int] = []
    estimated = 0
    current_has_large = False
    for index, text in enumerate(texts):
        cost = max(1, len(text) // _EST_CHAR_PER_TOKEN)
        is_large = cost >= large_cost
        if current and (is_large or current_has_large
                        or estimated + cost > _TOKEN_BUDGET or len(current) >= max_size):
            batches.append(current)
            current, estimated, current_has_large = [], 0, False
        current.append(index)
        estimated += cost
        current_has_large = current_has_large or is_large
    if current:
        batches.append(current)
    return batches


def _resolve_store(args):
    if args.doc_mode:
        from doc_local_qdrant import get_document_qdrant_store

        return get_document_qdrant_store(None)
    from tools.common.local_qdrant import get_code_qdrant_store

    return get_code_qdrant_store(None)


def _resolve_model(args) -> str:
    if args.doc_mode:
        return (
            os.environ.get("DOC_EMBEDDING_MODEL")
            or os.environ.get("EMBEDDING_MODEL")
            or __import__("embedding_utils").DEFAULT_DOC_EMBEDDING_MODEL
        )
    from tools.common.model_defaults import DEFAULT_CODE_EMBEDDING_MODEL

    return os.environ.get("CODE_EMBEDDING_MODEL") or DEFAULT_CODE_EMBEDDING_MODEL


def resolve_targets(args, store) -> List[str]:
    if args.collection:
        return [args.collection]
    project_id = (args.project_id or "").strip()
    if not project_id:
        raise SystemExit("Either --collection or --project-id is required.")
    if args.doc_mode:
        return [f"{project_id}_doc"]
    found = discover_code_collections(store, project_id)
    if not found:
        raise SystemExit(
            f"No code collections found for project {project_id!r} "
            f"(looked for {project_id}_*__*_functions in the resolved store). "
            f"Sync first with 'dev sync code', or pass --collection explicitly."
        )
    return found


def derive_identifier_queries(rows, limit: int) -> List[Tuple[str, str, str]]:
    """Corpus-derived identifier set: duplicate stress + distinctive names.

    Half the budget goes to the most-duplicated names (routing
    disambiguation stress), half to the longest unique names (exact-token
    lookup). Compiler-mangled names (``$`` markers) are skipped unless the
    clean pool cannot fill the budget — nobody searches
    ``foo$lambda@102:31`` on purpose.
    """
    names = Counter(
        str(payload.get("name") or "").strip()
        for _, payload in rows
        if str(payload.get("name") or "").strip()
    )

    def prefer_clean(candidates: List[str], needed: int) -> List[str]:
        no_mangled = [name for name in candidates if "$" not in name]
        return no_mangled if len(no_mangled) >= needed else candidates

    duplicated = prefer_clean(
        [name for name, count in names.most_common() if count >= 2],
        max(1, limit // 2),
    )
    picked = duplicated[: max(1, limit // 2)]
    remaining = limit - len(picked)
    if remaining > 0:
        singles = prefer_clean(
            sorted(
                (name for name, count in names.items() if count == 1),
                key=len,
                reverse=True,
            ),
            remaining,
        )
        picked += singles[:remaining]
    return [("id-" + name[:22], "identifier", name) for name in picked]


def rank_of_best_truth(hit_ids: List[str], truth: Set[str]) -> int:
    """1-based rank of the highest-ranked truth point, 0 when absent."""
    for i, hid in enumerate(hit_ids, start=1):
        if str(hid) in truth:
            return i
    return 0


def scroll_rows(store, collection: str) -> List[Tuple[str, Dict[str, Any]]]:
    rows: List[Tuple[str, Dict[str, Any]]] = []
    offset = None
    while True:
        points, offset = store.scroll(
            collection, scroll_filter=None, limit=256, offset=offset,
            with_payload=True, with_vectors=False,
        )
        for point in points or []:
            payload = point.get("payload") if isinstance(point, dict) else getattr(point, "payload", None)
            pid = point.get("id") if isinstance(point, dict) else getattr(point, "id", None)
            if embedding_marker.is_meta_payload(payload):
                continue
            rows.append((str(pid), dict(payload or {})))
        if offset is None:
            break
    return rows


def gate_one(store, collection: str, model_identity: str, args) -> Tuple[dict, List[dict]]:
    """Gate one collection; returns ``(summary, failures)``."""
    top_k = max(1, args.top_k)
    rows = scroll_rows(store, collection)
    if len(rows) < 2:
        return ({"collection": collection, "error": f"only {len(rows)} points"}, [])
    sampled = False
    if args.max_points and len(rows) > args.max_points:
        stride = len(rows) / float(args.max_points)
        rows = [rows[int(i * stride)] for i in range(args.max_points)]
        sampled = True
        print(f"[ab] {collection}: subsampled {len(rows)}/{args.max_points} "
              f"evenly from the full scroll (sampled gate)")

    # ---- clone with sparse schema + sentinel (same path as backfill)
    clone = collection + CLONE_SUFFIX
    store.recreate_collection(
        clone,
        vectors_config=qmodels.VectorParams(size=1024, distance=qmodels.Distance.COSINE),
        sparse_vectors_config={"sparse": qmodels.SparseVectorParams()},
    )
    embedding_marker.stamp(store, clone, embedding_model=model_identity,
                           vector_size=1024, hybrid=True)
    print(f"[ab] clone {clone} recreated (dense 1024 + named sparse)")

    try:
        # ---- re-encode dense+sparse from the clone's own payloads
        model = embed_runtime.get_sentence_transformer(
            model_identity,
            trust_remote_code=embed_runtime.model_policy(model_identity)["trust_remote_code"],
        )
        texts = [hybrid_document_text(payload) or " " for _, payload in rows]
        t_all = time.perf_counter()
        t0 = time.perf_counter()
        batch = 32
        upserted = 0
        from tools.common.embed_runtime import _is_accelerator_runtime_error

        def encode_batch(batch_texts):
            return model.encode(batch_texts, batch_size=len(batch_texts),
                                convert_to_numpy=True,
                                normalize_embeddings=True, show_progress_bar=False)

        for index_batch in _pack_batches(texts, batch):
            chunk_texts = [texts[i] for i in index_batch]
            try:
                dense = encode_batch(chunk_texts)
            except RuntimeError as exc:
                if not _is_accelerator_runtime_error(exc):
                    raise
                print(f"[ab] accelerator error on a batch ({str(exc)[:80]}…); "
                      f"retrying rows one at a time")
                dense_rows = []
                for single in chunk_texts:
                    dense_rows.append(encode_batch([single])[0])
                dense = dense_rows
            sparse = embed_runtime.encode_sparse(model_identity, chunk_texts, model=model)
            points = []
            for pos, row_index in enumerate(index_batch):
                pid, payload = rows[row_index]
                indices, values = sparse[pos]
                vec = dense[pos]
                points.append({
                    "id": pid,
                    "vector": {
                        "": vec.tolist(),
                        "sparse": qmodels.SparseVector(indices=list(indices), values=list(values)),
                    },
                    "payload": payload,
                })
            store.upsert(clone, points, wait=True)
            upserted += len(points)
            print(f"[ab] encoded {upserted}/{len(rows)}")
        print(f"[ab] encode+upsert done in {time.perf_counter() - t0:.1f}s")
        clone_count = int(store.count(clone, count_filter=None, exact=True).count)
        assert clone_count == len(rows) + 1, "clone population mismatch"

        # ---- ground truth resolved against this collection's payloads
        by_name: Dict[str, Set[str]] = {}
        for pid, payload in rows:
            by_name.setdefault(str(payload.get("name") or ""), set()).add(pid)
        id_to_name = {pid: str(payload.get("name") or "") for pid, payload in rows}

        queries: List[Tuple[str, str, str, Set[str]]] = [
            (name, qclass, identifier, by_name.get(identifier, set()))
            for name, qclass, identifier in derive_identifier_queries(rows, args.identifiers)
        ]
        skipped: List[str] = []
        for entry in args.parsed_queries:
            truth = by_name.get(str(entry.get("truth_name")), set())
            if truth:
                queries.append((str(entry["name"]), str(entry.get("class") or "custom"),
                                str(entry["text"]), truth))
            else:
                skipped.append(str(entry["name"]))
        if skipped:
            print(f"[ab] {collection}: {len(skipped)} paraphrase queries skipped "
                  f"(truth_name not present in this collection): {skipped}")
        queries = [(n, c, t, truth) for n, c, t, truth in queries if truth]
        if not queries:
            return ({"collection": collection, "error": "no resolvable queries"}, [])

        # ---- both production arms + measurement arms
        results = []
        print(f"\n{'query':<26} {'cls':<10} {'dR':<4} {'fR':<4} {'ovl10':<6} {'d ms':<7} {'f ms':<7}")
        for name, qclass, text, truth in queries:
            dense_vec, sparse_pair = embed_runtime.query_vector_pair(text, model_identity)

            t0 = time.perf_counter()
            dense_hits = search_collection(store, clone, dense_vec, None, top_k,
                                           sparse_vector=None)
            dense_ms = (time.perf_counter() - t0) * 1000
            t0 = time.perf_counter()
            fused_hits = search_collection(store, clone, dense_vec, None, top_k,
                                           sparse_vector=sparse_pair, fusion=True)
            fused_ms = (time.perf_counter() - t0) * 1000

            d_ids = [str(h.get("id")) for h in dense_hits]
            f_ids = [str(h.get("id")) for h in fused_hits]
            d_rank, f_rank = rank_of_best_truth(d_ids, truth), rank_of_best_truth(f_ids, truth)
            row = {
                "query": name, "class": qclass, "text": text,
                "truth_ids": sorted(truth), "truth_names": sorted({id_to_name[t] for t in truth}),
                "dense_rank": d_rank, "fused_rank": f_rank,
                "dense_hit@3": 1 <= d_rank <= 3, "fused_hit@3": 1 <= f_rank <= 3,
                "dense_hit@10": d_rank > 0, "fused_hit@10": f_rank > 0,
                "dense_top10": d_ids, "fused_top10": f_ids,
                "overlap@10": len(set(d_ids) & set(f_ids)),
                "dense_ms": round(dense_ms, 2), "fused_ms": round(fused_ms, 2),
            }
            # Measurement-only arms (NOT the production path): weighted RRF
            # client-side (the client wrapper rejects a raw Rrf object on
            # local mode) + sparse-only, to characterize any regression.
            flt = _with_sentinel_exclusion(None)
            fuse_k = fuse_k_from_env(top_k)
            dense_ch = store.query_points(
                clone, query=list(dense_vec), using="", limit=fuse_k, query_filter=flt,
                with_payload=False, with_vectors=False,
            ).points
            sparse_ch = store.query_points(
                clone,
                query=qmodels.SparseVector(indices=list(sparse_pair[0]), values=list(sparse_pair[1])),
                using=HYBRID_SPARSE_VECTOR_NAME, limit=fuse_k, query_filter=flt,
                with_payload=False, with_vectors=False,
            ).points
            dense_ranked = {str(p.id): i for i, p in enumerate(dense_ch)}
            sparse_ranked = {str(p.id): i for i, p in enumerate(sparse_ch)}
            for arm_name, w_dense in (("rrf_w70_30", 0.7), ("rrf_w85_15", 0.85)):
                scores: Dict[str, float] = {}
                for pid, r in dense_ranked.items():
                    scores[pid] = scores.get(pid, 0.0) + w_dense / (2.0 + r)
                for pid, r in sparse_ranked.items():
                    scores[pid] = scores.get(pid, 0.0) + (1.0 - w_dense) / (2.0 + r)
                a_ids = [pid for pid, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]]
                row[f"{arm_name}_rank"] = rank_of_best_truth(a_ids, truth)
                row[f"{arm_name}_top10"] = a_ids
            resp = store.query_points(
                clone,
                query=qmodels.SparseVector(indices=list(sparse_pair[0]), values=list(sparse_pair[1])),
                using=HYBRID_SPARSE_VECTOR_NAME, limit=top_k, with_vectors=False,
            )
            s_ids = [str(p.id) for p in resp.points]
            row["sparse_only_rank"] = rank_of_best_truth(s_ids, truth)
            results.append(row)
            print(f"{name:<26} {qclass:<10} {d_rank:<4} {f_rank:<4} "
                  f"{row['overlap@10']:<6} {dense_ms:<7.1f} {fused_ms:<7.1f}")

        # ---- aggregate + gate readout
        def agg(pred, subset=None):
            vals = [r for r in results if subset is None or r["class"] == subset]
            return round(100 * sum(1 for r in vals if pred(r)) / max(1, len(vals)), 1)

        def arm_summary(prefix, rank_key):
            return {
                f"hit3_{prefix}": agg(lambda r: 1 <= r[rank_key] <= 3),
                f"hit10_{prefix}": agg(lambda r: r[rank_key] > 0),
                f"regressions_{prefix}": [
                    r["query"] for r in results
                    if r[rank_key] == 0 or (r["dense_rank"] and r[rank_key] > r["dense_rank"])
                ],
                f"gains_{prefix}": [
                    r["query"] for r in results
                    if (r["dense_rank"] == 0 and r[rank_key] > 0)
                    or (0 < r[rank_key] < r["dense_rank"])
                ],
            }

        classes = sorted({r["class"] for r in results})
        def median(rank_key, cls):
            vals = sorted(r[rank_key] for r in results if r["class"] == cls)
            return vals[len(vals) // 2]
        summary = {
            "collection": collection, "points": len(rows),
            "sampled": sampled, "max_points": args.max_points or 0,
            "model": model_identity,
            "device": os.environ.get("EMBED_DEVICE", "auto"), "top_k": top_k,
            "queries": len(results),
            "hit3_dense": agg(lambda r: r["dense_hit@3"]),
            "hit3_fused": agg(lambda r: r["fused_hit@3"]),
            "hit10_dense": agg(lambda r: r["dense_hit@10"]),
            "hit10_fused": agg(lambda r: r["fused_hit@10"]),
            **arm_summary("rrf_w70_30", "rrf_w70_30_rank"),
            **arm_summary("rrf_w85_15", "rrf_w85_15_rank"),
            "hit3_sparse_only": agg(lambda r: 1 <= r["sparse_only_rank"] <= 3),
            "hit10_sparse_only": agg(lambda r: r["sparse_only_rank"] > 0),
            "by_class": {
                cls: {
                    "n": sum(1 for r in results if r["class"] == cls),
                    "hit3_dense": agg(lambda r: r["dense_hit@3"], cls),
                    "hit3_fused": agg(lambda r: r["fused_hit@3"], cls),
                    "hit10_dense": agg(lambda r: r["dense_hit@10"], cls),
                    "hit10_fused": agg(lambda r: r["fused_hit@10"], cls),
                    "median_dense_rank": median("dense_rank", cls),
                    "median_fused_rank": median("fused_rank", cls),
                }
                for cls in classes
            },
            "regressions": [
                r["query"] for r in results
                if r["fused_rank"] == 0 or (r["dense_rank"] and r["fused_rank"] > r["dense_rank"])
            ],
            "gains": [
                r["query"] for r in results
                if (r["dense_rank"] == 0 and r["fused_rank"] > 0)
                or (0 < r["fused_rank"] < r["dense_rank"])
            ],
            "avg_overlap@10": round(sum(r["overlap@10"] for r in results) / len(results), 2),
            "dense_ms_avg": round(sum(r["dense_ms"] for r in results) / len(results), 1),
            "fused_ms_avg": round(sum(r["fused_ms"] for r in results) / len(results), 1),
            "wall_s": round(time.perf_counter() - t_all, 1),
        }
        failures = [
            r for r in results
            if not r["fused_hit@10"]
            or (r["dense_rank"] and (r["fused_rank"] == 0 or r["fused_rank"] > r["dense_rank"]))
        ]
        summary["verdict"] = "PASS" if (
            summary["hit10_fused"] >= summary["hit10_dense"]
            and summary["hit3_fused"] >= summary["hit3_dense"]
            and not summary["regressions"]
        ) else "NOT PASSED"
        results_path = Path(args.out_dir) / f"ab_gate_{collection}_results.json"
        results_path.write_text(
            json.dumps({"summary": summary, "results": results}, indent=1, ensure_ascii=False),
            encoding="utf-8")
        return (summary, failures)
    finally:
        try:
            store.delete_collection(clone)
            print(f"[ab] clone {clone} dropped (original untouched)")
        except Exception as exc:  # noqa: BLE001 - cleanup best-effort
            print(f"[ab] WARNING: clone cleanup failed ({exc}); "
                  f"drop {clone!r} manually")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--collection", default=None)
    parser.add_argument("--project-id", default=None)
    parser.add_argument("--doc-mode", action="store_true",
                        help="Target the doc collection ({project}_doc).")
    parser.add_argument("--queries", default=None,
                        help="JSON file of paraphrase queries "
                             "[{name, class, text, truth_name}].")
    parser.add_argument("--identifiers", type=int, default=10,
                        help="Corpus-derived identifier queries per collection.")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--device", default=None,
                        help="EMBED_DEVICE override (default: auto).")
    parser.add_argument("--max-points", type=int, default=0,
                        help="Subsample the corpus to N points (even stride) "
                             "before encoding — smoke gate for large corpora. "
                             "0 = full corpus (default).")
    parser.add_argument("--out-dir", default=".",
                        help="Where results/failures JSON files are written.")
    args = parser.parse_args()

    os.environ.setdefault("MCP_PRELOAD_EMBEDDER", "0")
    os.environ["HF_HUB_OFFLINE"] = "1"          # snapshot + head must be cached
    os.environ["EMBED_HYBRID"] = "1"            # gate under test: the flag-on arm
    if args.device:
        os.environ["EMBED_DEVICE"] = args.device
    if args.max_points < 0:
        raise SystemExit("--max-points must be >= 0 (0 = full corpus).")
    args.parsed_queries = (
        json.loads(Path(args.queries).read_text(encoding="utf-8"))
        if args.queries else []
    )

    model_identity = embed_runtime.effective_model_identity(_resolve_model(args))
    store = _resolve_store(args)
    targets = resolve_targets(args, store)
    if len(targets) > 1:
        print(f"[ab] project resolves to {len(targets)} code collections — "
              f"gating each: {targets}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    all_summaries = []
    all_failures = []
    for collection in targets:
        print(f"\n===== GATE {collection} =====")
        summary, failures = gate_one(store, collection, model_identity, args)
        all_summaries.append(summary)
        all_failures.extend({"_collection": collection, **row} for row in failures)
        if "error" in summary:
            print(f"[ab] {collection}: SKIPPED — {summary['error']}")
            continue
        print(json.dumps(summary, indent=1, ensure_ascii=False))
        print(f"[ab] gate verdict [{collection}]: {summary['verdict']} "
              f"(runbook: no ground-truth regression, top-k overlap stable)")

    verdicts = [s.get("verdict") for s in all_summaries if "verdict" in s]
    overall = "PASS" if verdicts and all(v == "PASS" for v in verdicts) else "NOT PASSED"
    print(f"\n===== OVERALL: {overall} ({len(verdicts)} collection(s) gated) =====")
    if args.collection is None and len(targets) > 1:
        per = {s.get("collection", "?"): s.get("verdict", "SKIPPED") for s in all_summaries}
        print(json.dumps(per, indent=1, ensure_ascii=False))
    failures_path = out_dir / "ab_gate_failures.json"
    failures_path.write_text(json.dumps(all_failures, indent=1, ensure_ascii=False),
                             encoding="utf-8")
    print(f"[ab] failures → {failures_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
