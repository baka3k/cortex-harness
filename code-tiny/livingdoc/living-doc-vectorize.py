import argparse
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

# Ensure the repo root is on sys.path so that tools.graph.* imports work.
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from sentence_transformers import SentenceTransformer
from tools.graph.driver.neo4j_driver import Neo4jDriver


def get_env(name, default=None):
    return os.getenv(name, default)


def http_json(method, url, headers=None, payload=None, timeout=60):
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return resp.status, json.loads(body.decode("utf-8")) if body else None


def parse_args():
    parser = argparse.ArgumentParser(
        description="Vectorize cached summaries, update Neo4j, and store embeddings in Qdrant."
    )
    parser.add_argument("--cache-dir", default=get_env("CACHE_DIR", "cache"))
    parser.add_argument("--node-id-field", default=get_env("NODE_ID_FIELD", "id"))
    parser.add_argument("--summary-property", default=get_env("SUMMARY_PROPERTY", "summary"))
    parser.add_argument(
        "--summary-store",
        choices=["string", "map"],
        default=get_env("SUMMARY_STORE", "string"),
        help="Store summary in Neo4j as JSON string or map (default: string).",
    )

    parser.add_argument("--neo4j-uri", default=get_env("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=get_env("NEO4J_USER"))
    parser.add_argument("--neo4j-password", default=get_env("NEO4J_PASSWORD"))
    parser.add_argument("--project-id", default=get_env("PROJECT_ID"))
    parser.add_argument("--file-path-field", default=get_env("FILE_PATH_FIELD", "file_path"))

    parser.add_argument("--embed-model", default=get_env("EMBED_MODEL"))
    parser.add_argument("--embed-device", "--device", dest="embed_device", default=get_env("EMBED_DEVICE"))
    parser.add_argument(
        "--embed-trust-remote-code",
        action="store_true",
        help="Allow loading custom model code from the embedding model repo.",
    )
    parser.add_argument(
        "--hf-progress",
        action="store_true",
        help="Enable Hugging Face download progress output.",
    )
    parser.add_argument("--embed-sleep", type=float, default=float(get_env("EMBED_SLEEP", "0")))

    parser.add_argument("--qdrant-url", default=get_env("QDRANT_URL", "http://localhost:6333"))
    parser.add_argument("--qdrant-api-key", default=get_env("QDRANT_API_KEY"))
    parser.add_argument("--collection", default=get_env("QDRANT_COLLECTION"))
    parser.add_argument("--qdrant-collection", dest="collection", help="Alias for --collection")
    parser.add_argument(
        "--qdrant-store-summary",
        default=get_env("QDRANT_STORE_SUMMARY", "1"),
        help="Set to 0 to avoid storing the full summary in Qdrant payload.",
    )
    parser.add_argument(
        "--qdrant-summary-key",
        default=get_env("QDRANT_SUMMARY_KEY", "summary"),
        help="Payload key for storing summary when enabled.",
    )
    parser.add_argument(
        "--qdrant-create",
        default=get_env("QDRANT_CREATE", "1"),
        help="Set to 0 to skip auto-create collection.",
    )
    parser.add_argument(
        "--skip-existing",
        default=get_env("SKIP_EXISTING", "1"),
        help="Set to 0 to always insert into Qdrant.",
    )
    parser.add_argument(
        "--require-index",
        default=get_env("REQUIRE_INDEX", "1"),
        help="Set to 0 to allow cache files without _index.jsonl mapping.",
    )
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()
    missing = []
    if not args.neo4j_uri:
        missing.append("NEO4J_URI/--neo4j-uri")
    if not args.neo4j_user:
        missing.append("NEO4J_USER/--neo4j-user")
    if not args.neo4j_password:
        missing.append("NEO4J_PASSWORD/--neo4j-password")
    if not args.collection:
        missing.append("QDRANT_COLLECTION/--collection")
    if missing:
        print("Missing required options: " + ", ".join(missing), file=sys.stderr)
        sys.exit(2)
    return args


def read_cache_files(cache_dir):
    pattern = os.path.join(cache_dir, "*.json")
    return sorted(glob.glob(pattern))


def load_index(cache_dir):
    index_path = os.path.join(cache_dir, "_index.jsonl")
    if not os.path.exists(index_path):
        return {}
    mapping = {}
    with open(index_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                name = item.get("file")
                node_id = item.get("node_id")
                if name and node_id:
                    mapping[os.path.splitext(name)[0]] = node_id
            except json.JSONDecodeError:
                continue
    return mapping


# ---------------------------------------------------------------------------
# Local resume cache — tracks node_ids already upserted into Qdrant so we
# can skip the per-node Qdrant scroll request on subsequent runs (O(1) set
# lookup instead of 1 HTTP call per node).
# ---------------------------------------------------------------------------

def load_vectorized_ids(path: str) -> set:
    """Load set of node_ids already vectorized from a local plain-text file."""
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
    """Append a node_id to the local resume cache file."""
    with open(path, "a", encoding="utf-8") as f:
        f.write(node_id + "\n")


def batch_build_metadata(
    session,
    node_id_field: str,
    node_ids: list,
    file_path_field: str,
) -> dict:
    """Fetch metadata for a batch of node_ids in a single Neo4j query.

    Returns a dict mapping node_id -> metadata dict.
    """
    if not node_ids:
        return {}
    query = f"""
    UNWIND $node_ids AS nid
    MATCH (n)
    WHERE n.{node_id_field} = nid
    RETURN n.{node_id_field} AS node_id,
           labels(n) AS labels,
           n.project_id AS project_id,
           n.{file_path_field} AS file_path
    """
    result = session.run(query, {"node_ids": node_ids})
    meta_map: dict = {}
    for record in result:
        nid = record["node_id"]
        labels = record["labels"] or []
        meta_map[nid] = {
            "node_id": nid,
            "project_id": record.get("project_id"),
            "node_type": labels[0] if labels else None,
            "file_path": record.get("file_path"),
        }
    return meta_map


def update_summary(session, node_id_field, node_id, summary_property, summary, store_mode):
    if store_mode == "map":
        query = f"""
        MATCH (n)
        WHERE n.{node_id_field} = $node_id
        SET n.{summary_property} = $summary
        """
        session.run(query, {"node_id": node_id, "summary": summary})
    else:
        summary_str = json.dumps(summary, ensure_ascii=True, indent=2)
        query = f"""
        MATCH (n)
        WHERE n.{node_id_field} = $node_id
        SET n.{summary_property} = $summary
        """
        session.run(query, {"node_id": node_id, "summary": summary_str})


def ensure_collection(qdrant_url, headers, collection, vector_size, create_enabled, timeout=30):
    url = f"{qdrant_url.rstrip('/')}/collections/{collection}"
    try:
        status, _ = http_json("GET", url, headers=headers, timeout=timeout)
        if status == 200:
            return
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise

    if str(create_enabled) == "0":
        print(f"Collection not found: {collection}", file=sys.stderr)
        sys.exit(2)

    payload = {
        "vectors": {
            "size": vector_size,
            "distance": "Cosine",
        }
    }
    http_json("PUT", url, headers=headers, payload=payload, timeout=timeout)


def qdrant_has_node(qdrant_url, headers, collection, node_id, timeout=30):
    url = f"{qdrant_url.rstrip('/')}/collections/{collection}/points/scroll"
    payload = {
        "limit": 1,
        "filter": {
            "must": [
                {"key": "node_id", "match": {"value": node_id}},
            ]
        },
        "with_payload": False,
        "with_vectors": False,
    }
    try:
        status, data = http_json("POST", url, headers=headers, payload=payload, timeout=timeout)
        if status != 200 or not data:
            return False
        points = data.get("result", {}).get("points", [])
        return len(points) > 0
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Qdrant scroll failed: {exc.code} {body}") from exc


def upsert_point(qdrant_url, headers, collection, point_id, vector, payload, timeout=30):
    url = f"{qdrant_url.rstrip('/')}/collections/{collection}/points?wait=true"
    body = {"points": [{"id": point_id, "vector": vector, "payload": payload}]}
    try:
        http_json("PUT", url, headers=headers, payload=body, timeout=timeout)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Qdrant upsert failed: {exc.code} {body}") from exc


def main():
    args = parse_args()
    if args.hf_progress:
        os.environ["HF_HUB_ENABLE_PROGRESS_BARS"] = "1"
        os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "0"
        os.environ["TRANSFORMERS_VERBOSITY"] = "info"
    cache_dir = args.cache_dir
    files = read_cache_files(cache_dir)
    if not files:
        print(f"No cache files found in {cache_dir}", file=sys.stderr)
        sys.exit(1)
    index_map = load_index(cache_dir)
    require_index = str(args.require_index) != "0"
    if require_index and not index_map:
        print(
            f"Missing or empty index file in {cache_dir}/_index.jsonl",
            file=sys.stderr,
        )
        sys.exit(2)

    qdrant_headers = {"Content-Type": "application/json"}
    if args.qdrant_api_key:
        qdrant_headers["api-key"] = args.qdrant_api_key

    model_name = args.embed_model or "BAAI/bge-m3"
    if args.verbose:
        print(f"Loading embedding model: {model_name} device={args.embed_device}")
    try:
        embedder = SentenceTransformer(
            model_name,
            device=args.embed_device,
            trust_remote_code=args.embed_trust_remote_code,
        )
    except ModuleNotFoundError as exc:
        print(
            "Embedding model load failed. If the model uses custom code, "
            "re-run with --embed-trust-remote-code.",
            file=sys.stderr,
        )
        raise
    vector_size = embedder.get_sentence_embedding_dimension()
    if args.verbose:
        print(f"Embedding dimension: {vector_size}")
        print(f"Qdrant target: url={args.qdrant_url} collection={args.collection}")
    ensure_collection(
        args.qdrant_url,
        qdrant_headers,
        args.collection,
        vector_size,
        args.qdrant_create,
    )

    total_files = len(files)
    print(f"[vectorize] Cache files to process: {total_files}")

    # Local resume cache — O(1) skip check without Qdrant scroll
    vectorized_ids_path = os.path.join(cache_dir, ".vectorized_ids")
    vectorized_ids = load_vectorized_ids(vectorized_ids_path)
    if vectorized_ids:
        print(f"[vectorize] Already vectorized (local cache): {len(vectorized_ids)}")

    driver = Neo4jDriver(args.neo4j_uri, args.neo4j_user, args.neo4j_password)
    try:
        with driver.session() as session:
            # Batch-fetch metadata for all indexed node_ids upfront (single query)
            file_node_ids = [
                index_map[os.path.splitext(os.path.basename(p))[0]]
                for p in files
                if os.path.splitext(os.path.basename(p))[0] in index_map
            ]
            meta_map = batch_build_metadata(session, args.node_id_field, file_node_ids, args.file_path_field)
            if args.verbose:
                print(f"[vectorize] Metadata fetched for {len(meta_map)} nodes")

            updated = 0
            embedded = 0
            skipped = 0
            failed = 0
            missing_index = 0

            for idx, path in enumerate(files, 1):
                filename = os.path.basename(path)
                node_key = os.path.splitext(filename)[0]
                node_id = index_map.get(node_key)
                if not node_id:
                    missing_index += 1
                    print(f"[{idx}/{total_files}] WARN missing index: {filename}", file=sys.stderr)
                    if require_index:
                        skipped += 1
                        continue
                    node_id = node_key

                try:
                    with open(path, "r", encoding="utf-8") as f:
                        summary = json.load(f)
                except (OSError, json.JSONDecodeError) as exc:
                    print(f"[{idx}/{total_files}] ERROR read {filename}: {exc}", file=sys.stderr)
                    failed += 1
                    continue

                try:
                    update_summary(
                        session,
                        args.node_id_field,
                        node_id,
                        args.summary_property,
                        summary,
                        args.summary_store,
                    )
                    updated += 1
                except Exception as exc:
                    print(f"[{idx}/{total_files}] ERROR neo4j update {node_id}: {exc}", file=sys.stderr)
                    failed += 1
                    continue

                # Resume: check local set first (O(1)), fall back to Qdrant scroll only for unknowns
                if str(args.skip_existing) != "0":
                    if node_id in vectorized_ids:
                        skipped += 1
                        if args.verbose:
                            print(f"[{idx}/{total_files}] skip (local cache): {node_id}")
                        continue
                    try:
                        if qdrant_has_node(args.qdrant_url, qdrant_headers, args.collection, node_id):
                            save_vectorized_id(vectorized_ids_path, node_id)
                            vectorized_ids.add(node_id)
                            skipped += 1
                            if args.verbose:
                                print(f"[{idx}/{total_files}] skip (qdrant): {node_id}")
                            continue
                    except Exception as exc:
                        print(f"[{idx}/{total_files}] ERROR qdrant check {node_id}: {exc}", file=sys.stderr)
                        failed += 1
                        continue

                remaining = total_files - idx
                print(
                    f"[{idx}/{total_files}] Embedding {node_id} "
                    f"| updated={updated} skipped={skipped} failed={failed} remaining={remaining}"
                )

                text = json.dumps(summary, ensure_ascii=True, indent=2)
                try:
                    if args.verbose:
                        print(text)
                    vector = embedder.encode([text])[0].tolist()
                    if args.verbose:
                        print(f"Vector length for node_id={node_id}: {len(vector)}")
                    metadata = meta_map.get(node_id, {"node_id": node_id})
                    # make a copy so we don't mutate the prefetched map
                    metadata = dict(metadata)
                    if args.project_id:
                        metadata["project_id"] = args.project_id
                    if str(args.qdrant_store_summary) != "0":
                        metadata[args.qdrant_summary_key] = summary
                    metadata = {k: v for k, v in metadata.items() if v is not None}
                    if args.verbose:
                        print(f"Qdrant payload for node_id={node_id}: {metadata}")
                    point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(node_id)))
                    upsert_point(
                        args.qdrant_url,
                        qdrant_headers,
                        args.collection,
                        point_id,
                        vector,
                        metadata,
                    )
                    save_vectorized_id(vectorized_ids_path, node_id)
                    vectorized_ids.add(node_id)
                    embedded += 1
                except Exception as exc:
                    print(f"[{idx}/{total_files}] ERROR embed/qdrant {node_id}: {exc}", file=sys.stderr)
                    failed += 1

                if args.embed_sleep > 0:
                    time.sleep(args.embed_sleep)

            print(
                f"[vectorize] Done. Total={total_files} Updated={updated} Embedded={embedded} "
                f"Skipped={skipped} Failed={failed} MissingIndex={missing_index}"
            )
    finally:
        driver.close()


if __name__ == "__main__":
    main()
