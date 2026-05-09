import argparse
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request

from neo4j import GraphDatabase
from sentence_transformers import SentenceTransformer


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


def qdrant_search(qdrant_url, headers, collection, vector, limit, filter_payload, timeout=30):
    url = f"{qdrant_url.rstrip('/')}/collections/{collection}/points/search"
    payload = {
        "vector": vector,
        "limit": limit,
        "filter": filter_payload,
        "with_payload": True,
        "with_vectors": False,
    }
    try:
        status, data = http_json("POST", url, headers=headers, payload=payload, timeout=timeout)
        if status != 200 or not data:
            return []
        return data.get("result", [])
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Qdrant search failed: {exc.code} {body}") from exc


def parse_args():
    parser = argparse.ArgumentParser(
        description="Link code nodes to document nodes via semantic search in Qdrant."
    )
    parser.add_argument("--cache-dir", default=get_env("CACHE_DIR", "cache"))
    parser.add_argument("--node-id-field", default=get_env("NODE_ID_FIELD", "id"))
    parser.add_argument("--doc-id-field", default=get_env("DOC_ID_FIELD", "id"))
    parser.add_argument("--doc-label", default=get_env("DOC_LABEL", "Document"))
    parser.add_argument("--code-label", default=get_env("CODE_LABEL"))
    parser.add_argument("--relationship", default=get_env("REL_TYPE", "IMPLEMENTS_LOGIC"))

    parser.add_argument("--neo4j-uri", default=get_env("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=get_env("NEO4J_USER"))
    parser.add_argument("--neo4j-pass", default=get_env("NEO4J_PASSWORD"))
    parser.add_argument("--project-id", default=get_env("PROJECT_ID"))

    parser.add_argument("--embed-model", default=get_env("EMBED_MODEL"))
    parser.add_argument("--embed-device", "--device", dest="embed_device", default=get_env("EMBED_DEVICE"))
    parser.add_argument(
        "--embed-trust-remote-code",
        action="store_true",
        help="Allow loading custom model code from the embedding model repo.",
    )

    parser.add_argument("--qdrant-url", default=get_env("QDRANT_URL", "http://localhost:6333"))
    parser.add_argument("--qdrant-api-key", default=get_env("QDRANT_API_KEY"))
    parser.add_argument("--collection", default=get_env("QDRANT_COLLECTION"))
    parser.add_argument("--qdrant-collection", dest="collection", help="Alias for --collection")
    parser.add_argument("--doc-id-key", default=get_env("DOC_ID_KEY", "doc_id"))
    parser.add_argument(
        "--doc-id-fallback-keys",
        default=get_env("DOC_ID_FALLBACK_KEYS", "paragraph_id,source_id"),
        help="Comma-separated fallback payload keys to use when doc-id-key is missing.",
    )
    parser.add_argument(
        "--link-both",
        default=get_env("LINK_BOTH", "0"),
        help="Set to 1 to link both Paragraph and Document nodes when payload keys exist.",
    )
    parser.add_argument("--paragraph-label", default=get_env("PARAGRAPH_LABEL", "Paragraph"))
    parser.add_argument("--paragraph-id-field", default=get_env("PARAGRAPH_ID_FIELD", "paragraph_id"))
    parser.add_argument("--paragraph-id-key", default=get_env("PARAGRAPH_ID_KEY", "paragraph_id"))
    parser.add_argument(
        "--paragraph-relationship",
        default=get_env("PARAGRAPH_REL", "IMPLEMENTS_PARAGRAPH"),
    )
    parser.add_argument("--document-label", default=get_env("DOCUMENT_LABEL", "Document"))
    parser.add_argument("--document-id-field", default=get_env("DOCUMENT_ID_FIELD", "id"))
    parser.add_argument("--document-id-key", default=get_env("DOCUMENT_ID_KEY", "source_id"))
    parser.add_argument(
        "--document-relationship",
        default=get_env("DOCUMENT_REL", "IMPLEMENTS_DOCUMENT"),
    )
    parser.add_argument(
        "--require-doc-key",
        default=get_env("REQUIRE_DOC_KEY", "1"),
        help="Set to 0 to disable payload key filtering in Qdrant search.",
    )
    parser.add_argument("--top-k", type=int, default=int(get_env("TOP_K", "5")))
    parser.add_argument("--score-threshold", type=float, default=float(get_env("SCORE_THRESHOLD", "0.0")))
    parser.add_argument("--sleep", type=float, default=float(get_env("SLEEP", "0")))
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
        missing.append("NEO4J_PASSWORD/--neo4j-pass")
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

def build_match_query(code_label, doc_label, node_id_field, doc_id_field, rel_type):
    code_label_clause = f":{code_label}" if code_label else ""
    doc_label_clause = f":{doc_label}" if doc_label else ""
    query = (
        f"MATCH (c{code_label_clause} {{{node_id_field}: $code_id}})\n"
        f"MATCH (d{doc_label_clause} {{{doc_id_field}: $doc_id}})\n"
        f"MERGE (c)-[r:{rel_type}]->(d)\n"
        "SET r.score = $score, r.rank = $rank\n"
    )
    return query


def build_exists_query(code_label, doc_label, node_id_field, doc_id_field):
    code_label_clause = f":{code_label}" if code_label else ""
    doc_label_clause = f":{doc_label}" if doc_label else ""
    query = (
        f"OPTIONAL MATCH (c{code_label_clause} {{{node_id_field}: $code_id}})\n"
        f"OPTIONAL MATCH (d{doc_label_clause} {{{doc_id_field}: $doc_id}})\n"
        "RETURN (c IS NOT NULL) AS code_exists, (d IS NOT NULL) AS doc_exists\n"
    )
    return query


def code_in_project(session, node_id_field, node_id, project_id, code_label):
    if not project_id:
        return True
    code_label_clause = f":{code_label}" if code_label else ""
    query = f"""
    MATCH (c{code_label_clause})
    WHERE c.{node_id_field} = $node_id
    RETURN c.project_id AS project_id
    """
    record = session.run(query, {"node_id": node_id}).single()
    if not record:
        return False
    return record.get("project_id") == project_id


def main():
    args = parse_args()
    files = read_cache_files(args.cache_dir)
    if not files:
        print(f"No cache files found in {args.cache_dir}", file=sys.stderr)
        sys.exit(1)
    index_map = load_index(args.cache_dir)
    require_index = str(args.require_index) != "0"
    if require_index and not index_map:
        print(
            f"Missing or empty index file in {args.cache_dir}/_index.jsonl",
            file=sys.stderr,
        )
        sys.exit(2)

    link_both = str(args.link_both) != "0"
    doc_id_keys = [args.doc_id_key]
    if args.doc_id_fallback_keys:
        extra = [k.strip() for k in args.doc_id_fallback_keys.split(",") if k.strip()]
        doc_id_keys.extend(extra)
    if link_both:
        doc_id_keys = [args.paragraph_id_key, args.document_id_key]

    model_name = args.embed_model or "BAAI/bge-m3"
    if args.verbose:
        print(f"Loading embedding model: {model_name} device={args.embed_device}")
        print(f"Qdrant target: url={args.qdrant_url} collection={args.collection}")
        print(f"Doc id keys: {doc_id_keys} doc label: {args.doc_label}")
    embedder = SentenceTransformer(
        model_name,
        device=args.embed_device,
        trust_remote_code=args.embed_trust_remote_code,
    )

    qdrant_headers = {"Content-Type": "application/json"}
    if args.qdrant_api_key:
        qdrant_headers["api-key"] = args.qdrant_api_key

    query = build_match_query(
        args.code_label,
        args.doc_label,
        args.node_id_field,
        args.doc_id_field,
        args.relationship,
    )
    exists_query = build_exists_query(
        args.code_label,
        args.doc_label,
        args.node_id_field,
        args.doc_id_field,
    )
    paragraph_query = build_match_query(
        args.code_label,
        args.paragraph_label,
        args.node_id_field,
        args.paragraph_id_field,
        args.paragraph_relationship,
    )
    document_query = build_match_query(
        args.code_label,
        args.document_label,
        args.node_id_field,
        args.document_id_field,
        args.document_relationship,
    )
    paragraph_exists_query = build_exists_query(
        args.code_label,
        args.paragraph_label,
        args.node_id_field,
        args.paragraph_id_field,
    )
    document_exists_query = build_exists_query(
        args.code_label,
        args.document_label,
        args.node_id_field,
        args.document_id_field,
    )

    driver = GraphDatabase.driver(args.neo4j_uri, auth=(args.neo4j_user, args.neo4j_password))
    try:
        with driver.session() as session:
            total = 0
            linked = 0
            skipped = 0
            failed = 0
            missing_index = 0

            for path in files:
                total += 1
                filename = os.path.basename(path)
                node_key = os.path.splitext(filename)[0]
                node_id = index_map.get(node_key)
                if not node_id:
                    missing_index += 1
                    msg = f"Missing index mapping for cache file: {filename}"
                    print(msg, file=sys.stderr)
                    if require_index:
                        skipped += 1
                        continue
                    node_id = node_key
                if not code_in_project(
                    session,
                    args.node_id_field,
                    node_id,
                    args.project_id,
                    args.code_label,
                ):
                    if args.verbose:
                        print(f"Skip node_id={node_id} project_id mismatch")
                    skipped += 1
                    continue

                try:
                    with open(path, "r", encoding="utf-8") as f:
                        summary = json.load(f)
                except (OSError, json.JSONDecodeError) as exc:
                    print(f"Failed to read {path}: {exc}", file=sys.stderr)
                    failed += 1
                    continue

                text = json.dumps(summary, ensure_ascii=True, indent=2)
                if args.verbose:
                    print(f"Searching for code node_id={node_id}")
                vector = embedder.encode([text])[0].tolist()
                if str(args.require_doc_key) != "0":
                    if len(doc_id_keys) == 1:
                        filter_payload = {"must": [{"key": doc_id_keys[0], "is_empty": False}]}
                    else:
                        filter_payload = {
                            "should": [{"key": key, "is_empty": False} for key in doc_id_keys]
                        }
                else:
                    filter_payload = None
                try:
                    results = qdrant_search(
                        args.qdrant_url,
                        qdrant_headers,
                        args.collection,
                        vector,
                        args.top_k,
                        filter_payload,
                    )
                except Exception as exc:
                    print(f"Qdrant search failed for {node_id}: {exc}", file=sys.stderr)
                    failed += 1
                    continue

                if args.verbose:
                    print(f"Qdrant results: {len(results)}")
                    for idx, item in enumerate(results[:3], start=1):
                        payload = item.get("payload", {}) or {}
                        score = item.get("score", 0.0)
                        keys = ",".join(sorted(payload.keys()))
                        print(f"Top{idx} score={score} payload_keys={keys}")

                matched = 0
                linked_paragraph_ids = set()
                linked_document_ids = set()
                for rank, item in enumerate(results, start=1):
                    score = item.get("score", 0.0)
                    if score < args.score_threshold:
                        continue
                    payload = item.get("payload", {}) or {}
                    if link_both:
                        paragraph_id = payload.get(args.paragraph_id_key)
                        if paragraph_id not in (None, "") and paragraph_id not in linked_paragraph_ids:
                            if args.verbose:
                                print(
                                    f"Match: node_id={node_id} paragraph_id={paragraph_id} "
                                    f"key={args.paragraph_id_key} score={score}"
                                )
                                record = session.run(
                                    paragraph_exists_query,
                                    {"code_id": node_id, "doc_id": paragraph_id},
                                ).single()
                                if record:
                                    if not record.get("code_exists"):
                                        print(f"Neo4j missing code node_id={node_id}")
                                    if not record.get("doc_exists"):
                                        print(f"Neo4j missing paragraph paragraph_id={paragraph_id}")
                            session.run(
                                paragraph_query,
                                {
                                    "code_id": node_id,
                                    "doc_id": paragraph_id,
                                    "score": score,
                                    "rank": rank,
                                },
                            )
                            linked += 1
                            matched += 1
                            linked_paragraph_ids.add(paragraph_id)

                        document_id = payload.get(args.document_id_key)
                        if document_id not in (None, "") and document_id not in linked_document_ids:
                            if args.verbose:
                                print(
                                    f"Match: node_id={node_id} document_id={document_id} "
                                    f"key={args.document_id_key} score={score}"
                                )
                                record = session.run(
                                    document_exists_query,
                                    {"code_id": node_id, "doc_id": document_id},
                                ).single()
                                if record:
                                    if not record.get("code_exists"):
                                        print(f"Neo4j missing code node_id={node_id}")
                                    if not record.get("doc_exists"):
                                        print(f"Neo4j missing document document_id={document_id}")
                            session.run(
                                document_query,
                                {
                                    "code_id": node_id,
                                    "doc_id": document_id,
                                    "score": score,
                                    "rank": rank,
                                },
                            )
                            linked += 1
                            matched += 1
                            linked_document_ids.add(document_id)
                        if paragraph_id in (None, "") and document_id in (None, ""):
                            skipped += 1
                            continue
                    else:
                        doc_id = None
                        doc_id_key = None
                        for key in doc_id_keys:
                            if key in payload and payload.get(key) not in (None, ""):
                                doc_id = payload.get(key)
                                doc_id_key = key
                                break
                        if not doc_id:
                            skipped += 1
                            continue
                        if args.verbose:
                            print(
                                f"Match: node_id={node_id} doc_id={doc_id} "
                                f"doc_id_key={doc_id_key} score={score}"
                            )
                        if args.verbose:
                            record = session.run(
                                exists_query,
                                {"code_id": node_id, "doc_id": doc_id},
                            ).single()
                            if record:
                                if not record.get("code_exists"):
                                    print(f"Neo4j missing code node_id={node_id}")
                                if not record.get("doc_exists"):
                                    print(f"Neo4j missing doc doc_id={doc_id}")
                        session.run(
                            query,
                            {
                                "code_id": node_id,
                                "doc_id": doc_id,
                                "score": score,
                                "rank": rank,
                            },
                        )
                        linked += 1
                        matched += 1
                if args.verbose:
                    print(f"Linked {matched} docs for node_id={node_id}")

                if args.sleep > 0:
                    time.sleep(args.sleep)

            print(f"Total={total} Linked={linked} Skipped={skipped} Failed={failed} MissingIndex={missing_index}")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
