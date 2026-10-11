"""Real-machine A/B verification harness: dense-only vs fused RRF (bge-m3).

Populates two temp local Qdrant collections (dense-only schema vs hybrid
schema) with real bge-m3 embeddings of the golden-fixture texts, then runs
EN/VI/JA/identifier queries through both and records top-k overlap, latency,
and storage. Numbers land in this plan's verification-report.md.

Run: .venv/bin/python docs/plans/261011-hybrid-bge-m3/ab_harness.py
"""

import json
import os
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(REPO, "code-tiny"))
os.environ.setdefault("MCP_PRELOAD_EMBEDDER", "0")
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["EMBED_DEVICE"] = os.environ.get("EMBED_DEVICE", "cpu")

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from tools.common import embed_runtime

BGE = "BAAI/bge-m3"
fixture = json.load(open(os.path.join(REPO, "tests/fixtures/bge_m3_sparse_golden.json")))
docs = fixture["texts"]

# Paraphrase-style + identifier queries; ground truth = the doc sharing language/topic.
QUERIES = [
    ("EN-paraphrase", "how does the sync worker re-embed changed files and clean stale points", 0),
    ("EN-paraphrase2", "upsert deterministic point ids into the project collection", 0),
    ("identifier", "_normalize_rel_types parseAstWorker resolve_project_targets", 1),
    ("identifier2", "EMBED_BATCH_SIZE", 1),
    ("VI", "tìm kiếm mã nguồn bằng vector từ vựng thưa", 2),
    ("JA", "埋め込みベクトルで類似検索を実行する", 3),
]


def dense(texts):
    return model.encode(texts, batch_size=8, convert_to_numpy=True,
                        normalize_embeddings=True, show_progress_bar=False)


def sparse(texts):
    return embed_runtime.encode_sparse(BGE, texts, device="cpu", model=model)


model = embed_runtime.get_sentence_transformer(BGE, device="cpu")

with tempfile.TemporaryDirectory() as tmp:
    client = QdrantClient(path=tmp)
    client.create_collection("dense_only", vectors_config=qmodels.VectorParams(
        size=1024, distance=qmodels.Distance.COSINE))
    client.create_collection("hybrid", vectors_config=qmodels.VectorParams(
        size=1024, distance=qmodels.Distance.COSINE),
        sparse_vectors_config={"sparse": qmodels.SparseVectorParams()})

    dvecs = dense([d["text"] for d in docs])
    svecs = sparse([d["text"] for d in docs])
    client.upsert("dense_only", points=[
        qmodels.PointStruct(id=i, vector=d.tolist(), payload={"text": doc["text"]})
        for i, (d, doc) in enumerate(zip(dvecs, docs))
    ])
    client.upsert("hybrid", points=[
        qmodels.PointStruct(
            id=i,
            vector={
                "": d.tolist(),
                "sparse": qmodels.SparseVector(indices=list(idx), values=list(vals)),
            },
            payload={"text": doc["text"]},
        )
        for i, (d, doc, (idx, vals)) in enumerate(zip(dvecs, docs, svecs))
    ])

    def run_dense(q):
        v = dense([q])[0]
        t0 = time.perf_counter()
        hits = client.query_points("dense_only", query=v.tolist(), limit=3,
                                   with_payload=False).points
        return [h.id for h in hits], (time.perf_counter() - t0) * 1000

    def run_fused(q):
        v = dense([q])[0]
        idx, vals = sparse([q])[0]
        t0 = time.perf_counter()
        hits = client.query_points(
            "hybrid", limit=3, with_payload=False,
            prefetch=[
                qmodels.Prefetch(query=v.tolist(), using="", limit=64),
                qmodels.Prefetch(
                    query=qmodels.SparseVector(indices=list(idx), values=list(vals)),
                    using="sparse", limit=64),
            ],
            query=qmodels.FusionQuery(fusion=qmodels.Fusion.RRF)).points
        return [h.id for h in hits], (time.perf_counter() - t0) * 1000

    print(f"{'query':<16} {'dense top3':<14} {'fused top3':<14} {'overlap':<8} "
          f"{'dense ms':<9} {'fused ms':<9}")
    results = []
    for name, q, truth in QUERIES:
        d_ids, d_ms = run_dense(q)
        f_ids, f_ms = run_fused(q)
        overlap = len(set(d_ids) & set(f_ids))
        hit_truth = f"D={'Y' if truth in d_ids else 'n'} F={'Y' if truth in f_ids else 'n'}"
        print(f"{name:<16} {str(d_ids):<14} {str(f_ids):<14} {overlap:<8} "
              f"{d_ms:<9.2f} {f_ms:<9.2f} truth:{hit_truth}")
        results.append({"query": name, "truth_doc": truth, "dense_top3": d_ids,
                        "fused_top3": f_ids, "overlap": overlap,
                        "truth_in_dense": truth in d_ids, "truth_in_fused": truth in f_ids,
                        "dense_ms": round(d_ms, 2), "fused_ms": round(f_ms, 2)})

    size_kb = subprocess.run(["du", "-sk", tmp], capture_output=True,
                             text=True).stdout.split()[0]
    print(f"\nstorage, 4 docs x2 collections (dense-only + hybrid): {size_kb} KB")

    idx, vals = sparse(["incremental sync worker"])[0]
    print(f"query sparse entries: {len(idx)}, top weight: {max(vals):.3f}")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ab_results.json")
    json.dump(results, open(out, "w"), indent=1, ensure_ascii=False)
    print(f"saved {out}")
