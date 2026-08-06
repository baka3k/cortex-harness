"""
living-doc-vectorize-infra.py
──────────────────────────────
Embed InfraNode (name + summary) và upsert vào Qdrant.

Sau khi living-doc-summarize-infra.py ghi name/summary vào Neo4j:
  → Script này embed chúng và đưa vào cùng Qdrant collection với Function vectors.

Metadata Qdrant payload:
  {
    "node_id":      "digital_key_main:4441",
    "project_id":   "digital_key_main",
    "node_type":    "InfraNode",
    "name":         "NFC Card Emulation Handler",
    "summary":      "Handles the full lifecycle ...",
    "community_id": 4441,
    "keywords":     "[\"NFC\", \"emulation\"]"
  }
"""
import argparse
import json
import os
import sys
import uuid

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)


from sentence_transformers import SentenceTransformer
from tools.common.local_qdrant import (
    default_local_qdrant_path,
    ensure_collection as _ensure_local_collection,
    get_code_qdrant_store,
    scroll_points,
)
from graph_runtime import add_graph_arguments, open_graph_session, prepare_graph_arguments


def get_env(name, default=None):
    return os.getenv(name, default)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Vectorize InfraNode summaries and store embeddings in Qdrant.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_graph_arguments(parser)

    parser.add_argument("--project-id",  default=get_env("PROJECT_ID"))
    parser.add_argument("--infra-label", default=get_env("INFRA_LABEL", "InfraNode"))
    parser.add_argument(
        "--done-status",
        default=get_env("DONE_STATUS", "summarized"),
        help="Only process InfraNodes with this status value.",
    )

    parser.add_argument("--embed-model",  default=get_env("CODE_EMBEDDING_MODEL",  "BAAI/bge-m3"))
    parser.add_argument("--embed-device", default=get_env("EMBEDDING_DEVICE", "mps"))
    parser.add_argument("--embed-trust-remote-code", action="store_true")

    parser.add_argument("--qdrant-url",        default=default_local_qdrant_path())
    parser.add_argument("--collection",        default=get_env("QDRANT_COLLECTION_CODE"))
    parser.add_argument("--qdrant-collection", dest="collection", help="Alias for --collection")
    parser.add_argument("--qdrant-api-key",    default=get_env("QDRANT_API_KEY"))
    parser.add_argument(
        "--qdrant-create",
        default=get_env("QDRANT_CREATE", "1"),
        help="Set to 0 to skip auto-create collection.",
    )
    parser.add_argument(
        "--skip-existing",
        default=get_env("SKIP_EXISTING", "1"),
        help="Set to 0 to always re-upsert even if already in Qdrant.",
    )
    parser.add_argument("--cache-dir", default=get_env("CACHE_DIR", "cache"),
                        help="Used to store .vectorized_infra_ids resume cache.")
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()
    missing = []
    if not args.collection:
        missing.append("QDRANT_COLLECTION_CODE/--collection")
    if missing:
        print("Missing required options: " + ", ".join(missing), file=sys.stderr)
        sys.exit(2)
    try:
        return prepare_graph_arguments(args)
    except ValueError as exc:
        parser.error(str(exc))


# ─── Resume cache ─────────────────────────────────────────────────────────────

def load_vectorized_ids(path: str) -> set:
    ids: set = set()
    if not os.path.exists(path):
        return ids
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            nid = line.strip()
            if nid:
                ids.add(nid)
    return ids


def save_vectorized_id(path: str, node_id: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(node_id + "\n")


# ─── Neo4j ────────────────────────────────────────────────────────────────────

def fetch_infra_nodes(session, infra_label, done_status, project_id):
    """Return InfraNode rows: {id, name, summary, community_id, keywords, project_id}"""
    conditions = ["n.name IS NOT NULL", "n.summary IS NOT NULL"]
    params = {}
    if done_status:
        conditions.append("n.status = $done_status")
        params["done_status"] = done_status
    if project_id:
        conditions.append("n.project_id CONTAINS $project_id")
        params["project_id"] = project_id
    where = "WHERE " + " AND ".join(conditions)
    query = f"""
    MATCH (n:{infra_label})
    {where}
    RETURN n.id           AS node_id,
           n.name         AS name,
           n.summary      AS summary,
           n.community_id AS community_id,
           n.keywords     AS keywords,
           n.project_id   AS project_id
    ORDER BY n.id
    """
    return [dict(r) for r in session.run(query, params)]


# ─── Qdrant ───────────────────────────────────────────────────────────────────

def ensure_collection(qdrant_url, headers, collection, vector_size, create_enabled, timeout=30):
    del headers, timeout
    _ensure_local_collection(
        get_code_qdrant_store(qdrant_url), collection, vector_size,
        create=str(create_enabled) != "0",
    )
    print(f"[vectorize-infra] Created Qdrant collection: {collection} dim={vector_size}")


def qdrant_has_node(qdrant_url, headers, collection, node_id, timeout=30):
    del headers, timeout
    points, _ = scroll_points(
        get_code_qdrant_store(qdrant_url), collection, limit=1,
        query_filter={"must": [{"key": "node_id", "match": {"value": node_id}}]},
        with_payload=False,
    )
    return bool(points)


def upsert_point(qdrant_url, headers, collection, vector, payload_data, timeout=30):
    point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, payload_data["node_id"]))
    del headers, timeout
    get_code_qdrant_store(qdrant_url).upsert(
        collection, [{"id": point_id, "vector": vector, "payload": payload_data}], wait=True,
    )


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    os.makedirs(args.cache_dir, exist_ok=True)

    qdrant_headers = {"Content-Type": "application/json"}
    if args.qdrant_api_key:
        qdrant_headers["api-key"] = args.qdrant_api_key

    # Load embedding model
    model_name = args.embed_model
    if args.verbose:
        print(f"[vectorize-infra] Loading model: {model_name} device={args.embed_device}")
    embedder = SentenceTransformer(
        model_name,
        device=args.embed_device,
        trust_remote_code=args.embed_trust_remote_code,
    )
    vector_size = embedder.get_sentence_embedding_dimension()
    if args.verbose:
        print(f"[vectorize-infra] Embedding dim={vector_size}")

    ensure_collection(
        args.qdrant_url, qdrant_headers, args.collection,
        vector_size, args.qdrant_create,
    )

    # Resume cache
    resume_path = os.path.join(args.cache_dir, ".vectorized_infra_ids")
    vectorized_ids = load_vectorized_ids(resume_path)
    if vectorized_ids and args.verbose:
        print(f"[vectorize-infra] Already vectorized (local cache): {len(vectorized_ids)}")

    with open_graph_session(args) as session:
            infra_nodes = fetch_infra_nodes(
                session, args.infra_label, args.done_status, args.project_id
            )
            total = len(infra_nodes)
            print(f"[vectorize-infra] InfraNodes to process: {total}")
            if total == 0:
                print("[vectorize-infra] Nothing to do.")
                return

            upserted = skipped = failed = 0
            for idx, node in enumerate(infra_nodes, 1):
                node_id  = node["node_id"]
                name     = node.get("name") or ""
                summary  = node.get("summary") or ""
                remaining = total - idx

                # Resume check
                if str(args.skip_existing) != "0":
                    if node_id in vectorized_ids:
                        skipped += 1
                        if args.verbose:
                            print(f"[{idx}/{total}] skip (cache): {node_id}")
                        continue
                    try:
                        if qdrant_has_node(args.qdrant_url, qdrant_headers, args.collection, node_id):
                            save_vectorized_id(resume_path, node_id)
                            vectorized_ids.add(node_id)
                            skipped += 1
                            continue
                    except Exception as exc:
                        print(f"[{idx}/{total}] WARN qdrant check failed for {node_id}: {exc}")

                # Text to embed: name + summary (richer context)
                embed_text = f"{name}\n{summary}".strip()
                if not embed_text:
                    print(f"[{idx}/{total}] SKIP empty text: {node_id}")
                    skipped += 1
                    continue

                print(
                    f"[{idx}/{total}] Embedding {node_id} | "
                    f"upserted={upserted} skipped={skipped} failed={failed} remaining={remaining}"
                )

                try:
                    vector = embedder.encode(embed_text, show_progress_bar=False).tolist()
                except Exception as exc:
                    print(f"  [ERROR] encode {node_id}: {exc}")
                    failed += 1
                    continue

                payload_data = {
                    "node_id":      node_id,
                    "project_id":   node.get("project_id"),
                    "node_type":    "InfraNode",
                    "name":         name,
                    "summary":      summary,
                    "community_id": node.get("community_id"),
                    "keywords":     node.get("keywords"),
                }

                try:
                    upsert_point(
                        args.qdrant_url, qdrant_headers, args.collection,
                        vector, payload_data,
                    )
                    save_vectorized_id(resume_path, node_id)
                    vectorized_ids.add(node_id)
                    upserted += 1
                    if args.verbose:
                        print(f"  [OK] '{name}'")
                except Exception as exc:
                    print(f"  [ERROR] upsert {node_id}: {exc}")
                    failed += 1
    print(
        f"\n[vectorize-infra] Done. "
        f"Total={total} Upserted={upserted} Skipped={skipped} Failed={failed}"
    )


if __name__ == "__main__":
    main()
