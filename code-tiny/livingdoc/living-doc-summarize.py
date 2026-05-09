import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import asyncio
from typing import Optional

# Ensure the repo root (parent of this script's directory) is on sys.path
# so that `tools.graph.*` imports work when running the script directly.
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from neo4j import GraphDatabase


SYSTEM_PROMPT = (
    "You are a Senior Technical Architect and Business Analyst.\n"
    "Your task is to analyze source code functions and generate a structured JSON summary "
    "optimized for a Semantic Search Engine (RAG).\n\n"
    "Your goal is to bridge the gap between \"Technical Implementation\" (Code) and "
    "\"Business Logic\" (Documentation).\n\n"
    "RULES:\n"
    "1. Output Format: JSON only. No markdown, no conversational text.\n"
    "2. Language: The values in JSON should be in English (or Vietnamese if requested), "
    "but keep it professional and concise.\n"
    "3. Analyze Deeply: Look for specific standard references (e.g., ISO, CCC, RFC), "
    "error codes, and business rules within the code.\n\n"
    "JSON STRUCTURE:\n"
    "{\n"
    "  \"Business_Intent\": \"A single, clear sentence explaining WHY this function exists "
    "from a business perspective.\",\n"
    "  \"Input\": {\n"
    "    \"param_name\": \"Description of what this parameter represents and its data type.\"\n"
    "  },\n"
    "  \"Logic\": \"A step-by-step description of the flow.\",\n"
    "  \"Output\": \"Description of the return value and what it signifies.\",\n"
    "  \"Keywords\": [\"List\", \"of\", \"important\", \"technical\", \"terms\"]\n"
    "}\n"
)


def get_env(name, default=None, required=False):
    value = os.getenv(name, default)
    if required and not value:
        print(f"Missing required env var: {name}", file=sys.stderr)
        sys.exit(2)
    return value


def sanitize_filename(value):
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value))
    return safe.strip("_") or "node"


def http_post_json(url, headers, payload, timeout=60):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_json(content):
    if not content:
        return None
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    start = content.find("{")
    end = content.rfind("}")
    if start >= 0 and end > start:
        snippet = content[start : end + 1]
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            return None
    return None


def build_predicates(project_id, node_labels, label_match):
    predicates = []
    params = {}
    if project_id:
        predicates.append("n.project_id CONTAINS $project_id")
        params["project_id"] = project_id

    if node_labels:
        label_list = [lbl.strip() for lbl in node_labels.split(",") if lbl.strip()]
        if label_list:
            # Case-insensitive: compare toLower on both sides
            if label_match == "all":
                label_clause = "ALL(lbl IN $label_list WHERE toLower(lbl) IN [l IN labels(n) | toLower(l)])"
            else:
                label_clause = "ANY(lbl IN $label_list WHERE toLower(lbl) IN [l IN labels(n) | toLower(l)])"
            predicates.append(label_clause)
            params["label_list"] = label_list

    return predicates, params


def build_query(project_id, node_labels, label_match):
    predicates, params = build_predicates(project_id, node_labels, label_match)
    predicates.append("n.code IS NOT NULL")
    # Only exclude File/Class by default when no specific label filter is provided
    if not node_labels:
        predicates.extend(["NOT n:File", "NOT n:Class"])
    where_clause = ""
    if predicates:
        where_clause = "WHERE " + " AND ".join(predicates)
    query = f"""
    MATCH (n)
    {where_clause}
    RETURN labels(n) AS labels, n AS node
    """
    return query, params, predicates


def run_id_check(session, predicates, params, node_id_field):
    where_clause = ""
    if predicates:
        where_clause = "WHERE " + " AND ".join(predicates)
    query = f"""
    MATCH (n)
    {where_clause}
    RETURN
        count(n) AS total,
        sum(CASE WHEN n.{node_id_field} IS NULL OR n.{node_id_field} = '' THEN 1 ELSE 0 END) AS missing_id
    """
    record = session.run(query, params).single()
    if not record:
        return 0, 0
    total = record.get("total") or 0
    missing = record.get("missing_id") or 0
    return total, missing


def parse_args():
    parser = argparse.ArgumentParser(
        description="Summarize Neo4j code nodes with an OpenAI-compatible LLM."
    )
    parser.add_argument("--neo4j-uri", default=get_env("NEO4J_URI"))
    parser.add_argument("--neo4j-user", default=get_env("NEO4J_USER"))
    parser.add_argument("--neo4j-pass", default=get_env("NEO4J_PASS"))
    parser.add_argument("--neo4j-db", default=get_env("NEO4J_DB"))
    parser.add_argument("--project-id", default=get_env("PROJECT_ID"))
    parser.add_argument("--node-labels", default=get_env("NODE_LABELS"))
    parser.add_argument("--node-id-field", default=get_env("NODE_ID_FIELD", "id"))
    parser.add_argument(
        "--label-match",
        choices=["any", "all"],
        default=get_env("LABEL_MATCH", "any"),
        help="Match any or all labels in --node-labels (default: any).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging.",
    )
    parser.add_argument("--limit", type=int, default=get_env("LIMIT"))
    parser.add_argument("--cache-dir", default=get_env("CACHE_DIR", "cache"))
    parser.add_argument(
        "--skip-existing",
        default=get_env("SKIP_EXISTING", "1"),
        help="Set to 0 to overwrite existing cache files.",
    )
    parser.add_argument(
        "--require-node-id",
        default=get_env("REQUIRE_NODE_ID", "1"),
        help="Set to 0 to allow nodes missing the node id field.",
    )

    parser.add_argument(
        "--only",
        choices=["list", "summarize", "both"],
        default=get_env("ONLY", "both"),
        help="Run only the listing step, only summarization, or both (default: both)",
    )

    parser.add_argument(
        "--nodes-list-path",
        default=None,
        help="Path to write/read the nodes list JSONL (default: cache/_nodes.jsonl)",
    )

    parser.add_argument("--llm-api-base", default=get_env("LLM_API_BASE", "https://api.openai.com/v1"))
    parser.add_argument("--llm-api-key", default=get_env("LLM_API_KEY"))
    parser.add_argument("--llm-model", default=get_env("LLM_MODEL", "gpt-4o-mini"))
    parser.add_argument("--llm-timeout", type=int, default=get_env("LLM_TIMEOUT", "60"))
    parser.add_argument("--llm-sleep", type=float, default=get_env("LLM_SLEEP", "0"))

    # Qdrant / optional settings (exposed so all config can be passed as params)
    parser.add_argument("--qdrant-url", default=get_env("QDRANT_URL"))
    parser.add_argument("--qdrant-collection", default=get_env("QDRANT_COLLECTION_CODE", "livingdoc"))
    parser.add_argument("--qdrant-api-key", default=get_env("QDRANT_API_KEY"))

    args = parser.parse_args()
    missing = []
    if not args.neo4j_uri:
        missing.append("NEO4J_URI/--neo4j-uri")
    if not args.neo4j_user:
        missing.append("NEO4J_USER/--neo4j-user")
    if not args.NEO4J_PASS:
        missing.append("NEO4J_PASS/--neo4j-pass")
    if not args.llm_api_key:
        missing.append("LLM_API_KEY/--llm-api-key")
    if missing:
        print("Missing required options: " + ", ".join(missing), file=sys.stderr)
        sys.exit(2)
    return args


def write_nodes_list(
    driver,
    query: str,
    params: dict,
    node_id_field: str,
    require_node_id: bool,
    limit: Optional[int],
    nodes_list_path: str,
    database: Optional[str] = None,
) -> int:
    """Query Neo4j and write a JSONL list of nodes to `nodes_list_path`.

    Returns number of nodes written.
    """
    # use driver's sync execute if available
    try:
        records, _, _ = driver.execute_query_sync(query + (f"\nLIMIT {int(limit)}" if limit else ""), params, database=database)
    except Exception:
        # fallback to session run
        with driver.session(database=database) as session:
            result = session.run(query + (f"\nLIMIT {int(limit)}" if limit else ""), params)
            records = [r.data() for r in result]

    written = 0
    os.makedirs(os.path.dirname(nodes_list_path) or ".", exist_ok=True)
    with open(nodes_list_path, "w", encoding="utf-8") as handle:
        for rec in records:
            node = rec.get("node") or rec.get("n")
            labels = rec.get("labels")
            node_id = None
            if node and node_id_field in node:
                node_id = node.get(node_id_field)
            if not node_id:
                if require_node_id:
                    continue
                fallback = getattr(node, "element_id", None) if node is not None else None
                node_id = fallback or (str(node.id) if node is not None else None)
            if not node:
                continue
            entry = {
                "node_id": node_id,
                "labels": labels,
                "qualified_name": node.get("qualified_name") if node else None,
                "file_path": node.get("file_path") if node else None,
                "code": node.get("code") if node else None,
            }
            handle.write(json.dumps(entry, ensure_ascii=True) + "\n")
            written += 1
    return written


def run_summarization(
    nodes_list_path: str,
    cache_dir: str,
    skip_existing: bool,
    api_base: str,
    api_key: str,
    model: str,
    timeout: int,
    sleep_s: float,
):
    """Read nodes list JSONL and run LLM summarization, writing cache files and _index.jsonl.

    Resume support: nodes whose output cache file already exists are skipped
    automatically (controlled by skip_existing).  Progress is printed for every
    node that is actually sent to the LLM.
    """
    os.makedirs(cache_dir, exist_ok=True)
    index_path = os.path.join(cache_dir, "_index.jsonl")

    # --- pre-count total nodes so we can show progress ---
    with open(nodes_list_path, "r", encoding="utf-8") as _f:
        total_nodes = sum(1 for _ in _f)

    # --- count already-done nodes for resume reporting ---
    already_done = 0
    if skip_existing:
        with open(nodes_list_path, "r", encoding="utf-8") as _f:
            for _line in _f:
                try:
                    _entry = json.loads(_line)
                    _nid = _entry.get("node_id")
                    if _nid:
                        _fname = sanitize_filename(_nid) + ".json"
                        if os.path.exists(os.path.join(cache_dir, _fname)):
                            already_done += 1
                except Exception:
                    pass
    print(f"[summarize] Total nodes: {total_nodes} | Already cached: {already_done} | To process: {total_nodes - already_done}")

    idx = saved = skipped = failed = 0
    with open(nodes_list_path, "r", encoding="utf-8") as handle:
        for line in handle:
            idx += 1
            try:
                entry = json.loads(line)
            except Exception:
                failed += 1
                continue
            node_id = entry.get("node_id")
            labels = entry.get("labels")
            code = entry.get("code")
            file_path = entry.get("file_path") or ""
            if not code:
                skipped += 1
                continue
            filename = sanitize_filename(node_id) + ".json"
            out_path = os.path.join(cache_dir, filename)
            if skip_existing and os.path.exists(out_path):
                skipped += 1
                continue

            remaining = total_nodes - idx
            print(
                f"[{idx}/{total_nodes}] Processing {node_id} | {file_path} "
                f"| saved={saved} skipped={skipped} failed={failed} remaining={remaining}"
            )

            user_prompt = (
                "Analyze the following code snippet and generate the JSON summary.\n\n"
                "### CODE:\n"
                f"{code}"
            )
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.2,
            }
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
            try:
                response = http_post_json(f"{api_base.rstrip('/')}/chat/completions", headers, payload, timeout=timeout)
                content = response["choices"][0]["message"]["content"]
                summary = extract_json(content)
                if summary is None:
                    print(f"  [WARN] Could not parse LLM response for {node_id}")
                    failed += 1
                    continue
                with open(out_path, "w", encoding="ascii") as f:
                    json.dump(summary, f, ensure_ascii=True, indent=2)
                meta = {
                    "file": filename,
                    "node_id": node_id,
                    "qualified_name": entry.get("qualified_name"),
                    "file_path": file_path,
                }
                with open(index_path, "a", encoding="ascii") as f:
                    f.write(json.dumps(meta, ensure_ascii=True) + "\n")
                saved += 1
            except (urllib.error.URLError, KeyError, ValueError) as exc:
                print(f"  [ERROR] {node_id}: {exc}")
                failed += 1
            if sleep_s > 0:
                time.sleep(sleep_s)

    print(f"[summarize] Done. Total={total_nodes} Saved={saved} Skipped={skipped} Failed={failed}")


def main():
    args = parse_args()
    verbose = args.verbose
    uri = args.neo4j_uri
    user = args.neo4j_user
    password = args.NEO4J_PASS
    project_id = args.project_id
    node_labels = args.node_labels
    node_id_field = args.node_id_field
    label_match = args.label_match
    limit = args.limit
    cache_dir = args.cache_dir
    skip_existing = str(args.skip_existing) != "0"
    require_node_id = str(args.require_node_id) != "0"

    api_base = args.llm_api_base
    api_key = args.llm_api_key
    model = args.llm_model
    timeout = int(args.llm_timeout)
    sleep_s = float(args.llm_sleep)

    os.makedirs(cache_dir, exist_ok=True)

    query, params, predicates = build_query(project_id, node_labels, label_match)

    # Use the project's Neo4j wrapper so higher-level helpers are available
    from tools.graph.driver.neo4j_driver import Neo4jDriver
    driver = Neo4jDriver(uri, user, password, database=args.neo4j_db)
    nodes_list_path = args.nodes_list_path or os.path.join(cache_dir, "_nodes.jsonl")

    try:
        # Optional pre-check for node id presence
        with driver.session() as session:
            if require_node_id:
                total_nodes, missing_ids = run_id_check(
                    session,
                    predicates,
                    params,
                    node_id_field,
                )
                if missing_ids:
                    print(
                        f"Missing node id field '{node_id_field}' for {missing_ids}/{total_nodes} nodes. "
                        "Set REQUIRE_NODE_ID=0 to allow fallback.",
                        file=sys.stderr,
                    )
                    sys.exit(2)

        # Step 1: list nodes
        if args.only in ("list", "both"):
            written = write_nodes_list(
                driver,
                query,
                params,
                node_id_field,
                require_node_id,
                limit,
                nodes_list_path,
                database=args.neo4j_db,
            )
            print(f"Nodes listed: {written} -> {nodes_list_path}")
            if args.only == "list":
                return

        # Step 2: summarization
        if args.only in ("summarize", "both"):
            if not os.path.exists(nodes_list_path):
                print(f"Nodes list not found: {nodes_list_path}", file=sys.stderr)
                sys.exit(2)
            run_summarization(
                nodes_list_path,
                cache_dir,
                skip_existing,
                api_base,
                api_key,
                model,
                timeout,
                sleep_s,
            )
    finally:
        # Neo4jDriver.close() is async; call it synchronously here
        try:
            asyncio.run(driver.close())
        except RuntimeError:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                fut = asyncio.ensure_future(driver.close())
                time.sleep(0.1)
            else:
                loop.run_until_complete(driver.close())


if __name__ == "__main__":
    main()
