"""
living-doc-summarize-infra.py
─────────────────────────────
Tổng hợp mô tả (name + summary) cho các InfraNode (Louvain community) bằng LLM.

Cách hoạt động:
  1. Query tất cả InfraNode có status = 'pending_summary' (và tùy chọn filter theo project_id).
  2. Với mỗi InfraNode, lấy tất cả Function thành viên via BELONGS_TO relationship.
  3. Gom Business_Intent + Keywords từ property 'summary' (JSON string) của từng Function.
  4. Gọi LLM để tổng hợp tên module + mô tả cấp cao.
  5. SET infra.name, infra.summary, infra.status = 'summarized' trực tiếp vào Neo4j.

Yêu cầu: các Function đã được summarize trước (living-doc-summarize.py + living-doc-vectorize.py).
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from neo4j import GraphDatabase


# ─── Prompts ─────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are a Senior Software Architect.\n"
    "You will be given a list of function summaries that belong to the same software module cluster "
    "(detected by Louvain community detection).\n"
    "Your task is to synthesize a high-level description of this module cluster.\n\n"
    "RULES:\n"
    "1. Output: JSON only. No markdown, no extra text.\n"
    "2. Language: English, professional and concise.\n"
    "3. The 'name' must be a short, human-readable label (3-6 words, e.g. 'NFC Card Emulation Handler').\n"
    "4. The 'summary' must describe WHAT this cluster does and WHY it exists in 2-4 sentences.\n"
    "5. Extract the most representative keywords from all functions.\n\n"
    "JSON STRUCTURE:\n"
    "{\n"
    "  \"name\": \"Short module name (3-6 words)\",\n"
    "  \"summary\": \"High-level description of the cluster's purpose and responsibilities.\",\n"
    "  \"keywords\": [\"list\", \"of\", \"key\", \"concepts\"]\n"
    "}"
)


def get_env(name, default=None):
    return os.getenv(name, default)


def http_post_json(url, headers, payload, timeout=120):
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


def parse_args():
    parser = argparse.ArgumentParser(
        description="Summarize InfraNode (Louvain community) nodes using member function summaries + LLM.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--neo4j-uri",      default=get_env("NEO4J_URI",      "bolt://localhost:7687"))
    parser.add_argument("--neo4j-user",     default=get_env("NEO4J_USER",     "neo4j"))
    parser.add_argument("--neo4j-pass", default=get_env("NEO4J_PASSWORD"))
    parser.add_argument("--neo4j-db",       default=get_env("NEO4J_DB"))

    parser.add_argument("--project-id",   default=get_env("PROJECT_ID"))
    parser.add_argument("--infra-label",  default=get_env("INFRA_LABEL",  "InfraNode"))
    parser.add_argument("--belongs-rel",  default=get_env("BELONGS_REL",  "BELONGS_TO"))
    parser.add_argument("--node-label",   default=get_env("NODE_LABEL",   "Function"))
    parser.add_argument(
        "--pending-status",
        default=get_env("PENDING_STATUS", "pending_summary"),
        help="Only process InfraNodes whose 'status' property matches this value.",
    )
    parser.add_argument(
        "--done-status",
        default=get_env("DONE_STATUS", "summarized"),
        help="Value to SET on infra.status after successful summarization.",
    )
    parser.add_argument(
        "--min-members",
        type=int,
        default=int(get_env("MIN_MEMBERS", "2")),
        help="Skip InfraNodes with fewer than N member functions that have summaries.",
    )

    parser.add_argument("--llm-api-base", default=get_env("LLM_API_BASE", "http://localhost:11434/v1"))
    parser.add_argument("--llm-api-key",  default=get_env("LLM_API_KEY",  "local"))
    parser.add_argument("--llm-model",    default=get_env("LLM_MODEL",    "deepseek-coder-v2"))
    parser.add_argument("--llm-timeout",  type=int,   default=int(get_env("LLM_TIMEOUT", "120")))
    parser.add_argument("--llm-sleep",    type=float, default=float(get_env("LLM_SLEEP", "0")))
    parser.add_argument(
        "--max-functions",
        type=int,
        default=int(get_env("MAX_FUNCTIONS", "30")),
        help="Max member functions to include in the context prompt (largest summaries trimmed).",
    )
    parser.add_argument(
        "--skip-existing",
        default=get_env("SKIP_EXISTING", "1"),
        help="Set to 0 to re-summarize InfraNodes that already have a 'name' property.",
    )
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()
    missing = []
    if not args.neo4j_password:
        missing.append("NEO4J_PASSWORD/--neo4j-pass")
    if missing:
        print("Missing required options: " + ", ".join(missing), file=sys.stderr)
        sys.exit(2)
    return args


# ─── Neo4j helpers ────────────────────────────────────────────────────────────

def fetch_infra_nodes(session, infra_label, pending_status, project_id, skip_existing):
    """Return list of InfraNode dicts: {id, community_id, project_id}"""
    conditions = ["n.status = $pending_status"]
    params = {"pending_status": pending_status}
    if project_id:
        conditions.append("n.project_id CONTAINS $project_id")
        params["project_id"] = project_id
    if skip_existing:
        conditions.append("n.name IS NULL")

    where = "WHERE " + " AND ".join(conditions)
    query = f"""
    MATCH (n:{infra_label})
    {where}
    RETURN n.id AS infra_id,
           n.community_id AS community_id,
           n.project_id AS project_id
    ORDER BY n.community_id
    """
    return [dict(r) for r in session.run(query, params)]


def fetch_member_summaries(session, infra_id, infra_label, node_label, belongs_rel):
    """Return list of {node_id, qualified_name, file_path, summary_json} for member functions."""
    query = f"""
    MATCH (f:{node_label})-[:{belongs_rel}]->(infra:{infra_label} {{id: $infra_id}})
    WHERE f.summary IS NOT NULL
    RETURN
        f.id              AS node_id,
        f.qualified_name  AS qualified_name,
        f.file_path       AS file_path,
        f.summary         AS summary_json
    ORDER BY f.id
    """
    return [dict(r) for r in session.run(query, {"infra_id": infra_id})]


def count_total_members(session, infra_id, infra_label, node_label, belongs_rel):
    """Return total member count (including those without summaries)."""
    query = f"""
    MATCH (f:{node_label})-[:{belongs_rel}]->(infra:{infra_label} {{id: $infra_id}})
    RETURN count(f) AS cnt
    """
    rec = session.run(query, {"infra_id": infra_id}).single()
    return rec["cnt"] if rec else 0


def write_infra_summary(session, infra_label, infra_id, name, summary, keywords, done_status):
    """Store generated name/summary on the InfraNode."""
    query = f"""
    MATCH (infra:{infra_label} {{id: $infra_id}})
    SET infra.name     = $name,
        infra.summary  = $summary,
        infra.keywords = $keywords,
        infra.status   = $done_status
    """
    session.run(query, {
        "infra_id":    infra_id,
        "name":        name,
        "summary":     summary,
        "keywords":    json.dumps(keywords, ensure_ascii=False),
        "done_status": done_status,
    })


# ─── Prompt builder ───────────────────────────────────────────────────────────

def build_context_prompt(members, max_functions):
    """
    Build the user prompt from member function summaries.
    Trim to max_functions by favoring those with longer Business_Intent.
    """
    parsed = []
    for m in members:
        raw = m.get("summary_json") or ""
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            data = {}
        parsed.append({
            "node_id":        m.get("node_id") or "",
            "qualified_name": m.get("qualified_name") or m.get("node_id") or "",
            "file_path":      m.get("file_path") or "",
            "intent":         data.get("Business_Intent") or data.get("summary") or "",
            "logic":          data.get("Logic") or "",
            "keywords":       data.get("Keywords") or data.get("keywords") or [],
        })

    # Sort by intent length desc and take top N
    parsed.sort(key=lambda x: len(x["intent"]), reverse=True)
    selected = parsed[:max_functions]
    # Restore original order by qualified_name for readability
    selected.sort(key=lambda x: x["qualified_name"])

    lines = [f"This module cluster has {len(members)} member function(s). "
             f"Below are up to {len(selected)} representative summaries:\n"]
    for i, fn in enumerate(selected, 1):
        lines.append(f"### Function {i}: {fn['qualified_name']}")
        if fn["file_path"]:
            lines.append(f"  File: {fn['file_path']}")
        if fn["intent"]:
            lines.append(f"  Business_Intent: {fn['intent']}")
        if fn["logic"]:
            # Truncate long logic to keep context manageable
            logic = fn["logic"][:300] + "..." if len(fn["logic"]) > 300 else fn["logic"]
            lines.append(f"  Logic: {logic}")
        if fn["keywords"]:
            kws = fn["keywords"] if isinstance(fn["keywords"], list) else [fn["keywords"]]
            lines.append(f"  Keywords: {', '.join(str(k) for k in kws[:10])}")
        lines.append("")

    return "\n".join(lines).strip()


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    skip_existing = str(args.skip_existing) != "0"

    driver = GraphDatabase.driver(
        args.neo4j_uri, auth=(args.neo4j_user, args.neo4j_password)
    )
    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {args.llm_api_key}",
    }

    try:
        with driver.session(database=args.neo4j_db) as session:
            infra_nodes = fetch_infra_nodes(
                session,
                args.infra_label,
                args.pending_status,
                args.project_id,
                skip_existing,
            )
            total = len(infra_nodes)
            print(f"[summarize-infra] InfraNodes to process: {total}")
            if total == 0:
                print("[summarize-infra] Nothing to do.")
                return

            saved = skipped = failed = 0
            for idx, infra in enumerate(infra_nodes, 1):
                infra_id    = infra["infra_id"]
                community_id = infra.get("community_id")
                remaining   = total - idx

                members = fetch_member_summaries(
                    session,
                    infra_id,
                    args.infra_label,
                    args.node_label,
                    args.belongs_rel,
                )
                total_members = count_total_members(
                    session, infra_id, args.infra_label, args.node_label, args.belongs_rel
                )

                print(
                    f"[{idx}/{total}] infra_id={infra_id} | "
                    f"members={total_members} summarized={len(members)} | "
                    f"saved={saved} skipped={skipped} failed={failed} remaining={remaining}"
                )

                if len(members) < args.min_members:
                    print(
                        f"  [SKIP] Only {len(members)} summarized members (need {args.min_members})"
                    )
                    skipped += 1
                    continue

                context = build_context_prompt(members, args.max_functions)
                if args.verbose:
                    print(f"  [context] {len(context)} chars")

                payload = {
                    "model": args.llm_model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",   "content": context},
                    ],
                    "temperature": 0.3,
                }
                try:
                    response = http_post_json(
                        f"{args.llm_api_base.rstrip('/')}/chat/completions",
                        headers,
                        payload,
                        timeout=args.llm_timeout,
                    )
                    content  = response["choices"][0]["message"]["content"]
                    result   = extract_json(content)
                    if result is None:
                        print(f"  [WARN] Could not parse LLM response for {infra_id}")
                        failed += 1
                        continue

                    name     = str(result.get("name") or f"Community {community_id}").strip()
                    summary  = str(result.get("summary") or "").strip()
                    keywords = result.get("keywords") or []

                    write_infra_summary(
                        session,
                        args.infra_label,
                        infra_id,
                        name,
                        summary,
                        keywords,
                        args.done_status,
                    )
                    print(f"  [OK] name='{name}'")
                    saved += 1
                except (urllib.error.URLError, KeyError, ValueError) as exc:
                    print(f"  [ERROR] {infra_id}: {exc}")
                    failed += 1

                if args.llm_sleep > 0:
                    time.sleep(args.llm_sleep)

    finally:
        driver.close()

    print(
        f"\n[summarize-infra] Done. Total={total} Saved={saved} Skipped={skipped} Failed={failed}"
    )


if __name__ == "__main__":
    main()
