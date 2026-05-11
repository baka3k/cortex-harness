#!/usr/bin/env python3
"""Graph-first context selector with MCP integration for graph_mcp and mind_mcp."""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_K_HOP = 2
DEFAULT_TIMEOUT = 8
DEFAULT_MIND_FALLBACK_TOP_K = 8
DEFAULT_BUDGET_MAX_TOOL_CALLS = 50
GRAPH_AUTO_TOOL_PRIORITY = [
    "query_subgraph",
    "explore_graph",
    "semantic_search",
    "search_functions",
    "search_by_code",
]
MIND_AUTO_TOOL_PRIORITY = [
    "hybrid_search",
    "query_graph_rag_relation",
    "sequential_search",
    "query_worksheet",
    "semantic_search",
    "search",
    "retrieve",
]
TASK_KEYWORDS: dict[str, list[str]] = {
    "bugfix": ["bug", "fix", "error", "exception", "failure", "timeout", "crash", "incident"],
    "feature": ["feature", "workflow", "design", "spec", "use case", "implementation", "api"],
    "refactor": ["refactor", "architecture", "cleanup", "module", "dependency", "maintainability", "performance"],
}


class MCPHttpClient:
    def __init__(self, url: str, timeout: int) -> None:
        self.url = url
        self.timeout = timeout
        self.session_id: str = ""

    def call(self, method: str, params: dict[str, Any] | None, req_id: str) -> dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")

        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            sid = resp.headers.get("mcp-session-id", "")
            if sid:
                self.session_id = sid
            raw = resp.read().decode("utf-8", errors="replace")
            return parse_mcp_response(raw)


def load_feature_state(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def select_task(features: list[dict[str, Any]], task_id: str) -> dict[str, Any]:
    for item in features:
        if item.get("id") == task_id:
            return item
    raise ValueError(f"Task not found: {task_id}")


def parse_mcp_response(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if not text:
        return {}

    if text.startswith("{") or text.startswith("["):
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        return {"result": parsed}

    data_lines = [line[len("data:") :].strip() for line in text.splitlines() if line.startswith("data:")]
    for chunk in reversed(data_lines):
        if not chunk:
            continue
        try:
            parsed = json.loads(chunk)
            if isinstance(parsed, dict):
                return parsed
            return {"result": parsed}
        except json.JSONDecodeError:
            continue

    raise ValueError("Unsupported MCP response payload format")


def parse_bool_env(value: str, default: bool) -> bool:
    v = value.strip().lower()
    if not v:
        return default
    if v in ("1", "true", "yes", "y", "on"):
        return True
    if v in ("0", "false", "no", "n", "off"):
        return False
    return default


def parse_query_list(raw: str) -> list[str]:
    if not raw.strip():
        return []
    parts = [p.strip() for p in raw.replace("\n", ";").split(";")]
    return [item for item in parts if item]


def dedupe_keep_order(items: list[str]) -> list[str]:
    seen = set()
    out: list[str] = []
    for item in items:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item.strip())
    return out


def safe_mcp_discover(url: str, timeout: int, client_name: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "url": url,
        "reachable": False,
        "initialize_ok": False,
        "tool_names": [],
        "tool_schemas": {},
        "error": "",
        "http_status": None,
        "session_id": "",
    }
    if not url:
        out["error"] = "not configured"
        return out

    client = MCPHttpClient(url, timeout)

    try:
        init_res = client.call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": client_name, "version": "0.1"},
            },
            req_id=f"{client_name}-initialize",
        )
        out["reachable"] = True
        out["session_id"] = client.session_id
        if "error" not in init_res:
            out["initialize_ok"] = True

        try:
            client.call("notifications/initialized", {}, req_id=f"{client_name}-initialized")
        except Exception:
            pass

        tools_res = client.call("tools/list", {}, req_id=f"{client_name}-tools-list")
        tools = tools_res.get("result", {}).get("tools", [])
        out["tool_names"] = [t.get("name", "") for t in tools if isinstance(t, dict)]
        for t in tools:
            if not isinstance(t, dict):
                continue
            name = str(t.get("name", ""))
            schema = t.get("inputSchema", {})
            if name:
                out["tool_schemas"][name] = schema
    except urllib.error.HTTPError as exc:
        out["http_status"] = exc.code
        if exc.code in (400, 406, 415):
            out["reachable"] = True
            out["error"] = f"HTTP {exc.code} (reachable, but method/transport/content mismatch)"
        else:
            out["error"] = f"HTTP {exc.code}: {exc.reason}"
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        out["reachable"] = True
        out["error"] = str(exc)

    return out


def parse_json_obj(raw: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Expected JSON object")
    return parsed


def choose_tool(configured_tool: str, available: list[str], priorities: list[str]) -> str:
    if configured_tool:
        return configured_tool
    for name in priorities:
        if name in available:
            return name
    return ""


def schema_keys(schema: dict[str, Any]) -> set[str]:
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    if not isinstance(properties, dict):
        return set()
    return set(properties.keys())


def infer_graph_args(tool_name: str, task: dict[str, Any], k_hop: int, schema: dict[str, Any]) -> dict[str, Any]:
    entry = task.get("graph_entry_node")
    title = task.get("title", "")
    keys = schema_keys(schema)

    args: dict[str, Any] = {}
    if "function_id" in keys:
        args["function_id"] = entry
    if "query" in keys:
        args["query"] = title or str(entry)
    if "task_id" in keys:
        args["task_id"] = task.get("id")
    if "k_hop" in keys:
        args["k_hop"] = k_hop
    if "max_hops" in keys:
        args["max_hops"] = k_hop
    if "hops" in keys:
        args["hops"] = k_hop
    if "depth" in keys:
        args["depth"] = k_hop
    if "limit" in keys:
        args["limit"] = str(max(20, k_hop * 20))
    if "expand_search" in keys:
        args["expand_search"] = True

    if not args and tool_name == "query_subgraph":
        args = {"function_id": entry, "limit": "40", "expand_search": True}
    elif not args and tool_name == "explore_graph":
        args = {"query": title or str(entry)}
    elif not args and tool_name in ("semantic_search", "search_functions", "search_by_code"):
        args = {"query": title or str(entry)}
    elif not args:
        args = {"entry_node": entry, "k_hop": k_hop, "task_id": task.get("id")}

    return args


def infer_mind_args(task: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    keys = schema_keys(schema)
    title = task.get("title", "")

    args: dict[str, Any] = {}
    if "query" in keys:
        args["query"] = title
    if "task_id" in keys:
        args["task_id"] = task.get("id")
    if "modules" in keys:
        args["modules"] = task.get("related_modules", [])
    if "collection" in keys:
        args["collection"] = ""
    if "collection_name" in keys:
        args["collection_name"] = ""
    if "top_k" in keys:
        args["top_k"] = 5

    if not args:
        args = {
            "query": title,
            "task_id": task.get("id"),
            "modules": task.get("related_modules", []),
        }
    return args


def merge_args(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    merged.update(override)
    return merged


def try_tool_call(
    url: str,
    tool_name: str,
    arguments: dict[str, Any],
    timeout: int,
    req_id: str,
    client_name: str,
) -> dict[str, Any]:
    if not tool_name:
        return {"called": False, "reason": "tool not configured or not auto-detected", "result": None}

    client = MCPHttpClient(url, timeout)

    try:
        client.call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": client_name, "version": "0.1"},
            },
            req_id=f"{req_id}-initialize",
        )
        try:
            client.call("notifications/initialized", {}, req_id=f"{req_id}-initialized")
        except Exception:
            pass

        res = client.call("tools/call", {"name": tool_name, "arguments": arguments}, req_id=req_id)
        return {"called": True, "result": res, "reason": "", "session_id": client.session_id}
    except urllib.error.HTTPError as exc:
        return {
            "called": True,
            "result": None,
            "reason": f"HTTP {exc.code}: {exc.reason}",
            "session_id": client.session_id,
        }
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return {"called": True, "result": None, "reason": str(exc), "session_id": client.session_id}


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _pick(d: dict[str, Any], keys: list[str], default: Any = None) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def _try_parse_json_text(text: str) -> Any:
    t = text.strip()
    if not t:
        return None
    if t.startswith("```"):
        t = t.strip("`")
        if t.startswith("json"):
            t = t[4:].strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        return None


def _extract_mind_result_payload(mind_call: dict[str, Any]) -> Any:
    if not mind_call.get("called"):
        return None
    result_wrapper = mind_call.get("result")
    if not isinstance(result_wrapper, dict):
        return None

    inner = result_wrapper.get("result")
    if isinstance(inner, dict):
        content = inner.get("content", [])
        if isinstance(content, list) and content:
            for item in content:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str):
                    parsed = _try_parse_json_text(text)
                    if parsed is not None:
                        return parsed
            return inner
        return inner
    return result_wrapper


def normalize_vector_refs_from_mind_call(mind_call: dict[str, Any], max_refs: int = 8, snippet_chars: int = 320) -> list[dict[str, Any]]:
    payload = _extract_mind_result_payload(mind_call)
    if payload is None:
        return []

    if isinstance(payload, dict):
        for key in ["results", "passages", "chunks", "items", "matches"]:
            if isinstance(payload.get(key), list):
                raw_list = payload.get(key)
                break
        else:
            raw_list = []
    elif isinstance(payload, list):
        raw_list = payload
    else:
        raw_list = []

    refs: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_list[:max_refs], start=1):
        if not isinstance(item, dict):
            continue

        payload_obj = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        metadata_obj = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}

        source_id = _pick(item, ["source_id", "source", "doc_id", "document_id"], None)
        if source_id is None:
            source_id = _pick(payload_obj, ["source_id", "doc_id", "document_id"], None)
        if source_id is None:
            source_id = _pick(metadata_obj, ["source_id", "doc_id", "document_id"], None)

        score = _safe_float(_pick(item, ["score", "similarity", "rerank_score"], None))
        if score is None:
            score = _safe_float(_pick(payload_obj, ["score", "similarity"], None))

        snippet = _pick(item, ["text", "content", "chunk", "passage"], "")
        if not snippet:
            snippet = _pick(payload_obj, ["text", "content", "chunk", "passage"], "")
        if not snippet:
            snippet = json.dumps(item, ensure_ascii=True)

        collection = _pick(item, ["collection", "collection_name"], None)
        if collection is None:
            collection = _pick(payload_obj, ["collection", "collection_name"], None)

        title = _pick(item, ["title", "name", "heading"], None)
        if title is None:
            title = _pick(payload_obj, ["title", "name", "heading"], None)

        ref = {
            "rank": idx,
            "score": score,
            "source_id": source_id,
            "collection": collection,
            "title": title,
            "snippet": str(snippet)[:snippet_chars],
            "metadata": metadata_obj if metadata_obj else payload_obj,
        }
        refs.append(ref)

    return refs


def normalize_task_type(task: dict[str, Any]) -> str:
    t = str(task.get("type", "")).strip().lower()
    if t in ("bug", "bugfix", "fix"):
        return "bugfix"
    if t in ("feature", "feat"):
        return "feature"
    if t in ("refactor", "cleanup"):
        return "refactor"
    return "feature"


def tokenize(text: str) -> list[str]:
    return [tok for tok in re.split(r"[^a-zA-Z0-9_]+", text.lower()) if tok]


def score_query_for_task(query: str, task: dict[str, Any], task_type: str) -> float:
    q = query.strip().lower()
    score = 0.0

    keywords = TASK_KEYWORDS.get(task_type, [])
    for kw in keywords:
        if kw in q:
            score += 2.0

    title_tokens = set(tokenize(str(task.get("title", ""))))
    entry_tokens = set(tokenize(str(task.get("graph_entry_node", ""))))
    module_tokens: set[str] = set()
    for module in task.get("related_modules", []):
        module_tokens.update(tokenize(str(module)))

    q_tokens = set(tokenize(q))
    score += 0.6 * len(q_tokens.intersection(title_tokens))
    score += 0.8 * len(q_tokens.intersection(entry_tokens))
    score += 1.0 * len(q_tokens.intersection(module_tokens))

    if 1 <= len(q_tokens) <= 4:
        score += 0.5

    return score


def rank_fallback_queries(queries: list[str], task: dict[str, Any], task_type: str) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for q in queries:
        scored.append({
            "query": q,
            "score": score_query_for_task(q, task, task_type),
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def build_mind_fallback_queries(task: dict[str, Any], initial_query: str, configured: list[str]) -> list[str]:
    candidates: list[str] = []

    if configured:
        candidates.extend(configured)

    title = str(task.get("title", "")).strip()
    entry_node = str(task.get("graph_entry_node", "")).strip()
    modules = task.get("related_modules", [])
    task_type = normalize_task_type(task)

    if title:
        candidates.append(title)
    if entry_node:
        candidates.append(entry_node)

    for module in modules:
        m = str(module).strip()
        if m:
            candidates.append(m)
            candidates.append(f"{m} architecture")
            candidates.append(f"{m} workflow")

    candidates.extend(TASK_KEYWORDS.get(task_type, []))
    candidates.extend([
        "architecture",
        "workflow",
        "design",
        "overview",
        "authentication",
    ])

    deduped = dedupe_keep_order(candidates)
    initial_key = initial_query.strip().lower()
    filtered = [q for q in deduped if q.strip().lower() != initial_key]

    ranked = rank_fallback_queries(filtered, task, task_type)
    return [x["query"] for x in ranked]


def resolve_mind_fallback_max_attempts(configured: int, budget_max_tool_calls: int, candidate_count: int) -> int:
    if candidate_count <= 0:
        return 0

    if configured > 0:
        return min(configured, candidate_count)

    # Auto mode: allocate ~20% tool budget for fallback, clamped to [3, 12]
    derived = max(3, min(12, budget_max_tool_calls // 5))
    return min(derived, candidate_count)


def build_context(
    task: dict[str, Any],
    k_hop: int,
    graph_mcp_url: str,
    mind_mcp_url: str,
    graph_tool: str,
    mind_tool: str,
    graph_override_args: dict[str, Any],
    mind_override_args: dict[str, Any],
    mind_fallback_enabled: bool,
    mind_fallback_queries: list[str],
    mind_fallback_top_k: int,
    mind_fallback_max_attempts: int,
    budget_max_tool_calls: int,
    timeout: int,
) -> dict[str, Any]:
    graph_discovery = safe_mcp_discover(graph_mcp_url, timeout, "harness-graph")
    mind_discovery = safe_mcp_discover(mind_mcp_url, timeout, "harness-mind")

    selected_graph_tool = choose_tool(graph_tool, graph_discovery.get("tool_names", []), GRAPH_AUTO_TOOL_PRIORITY)
    selected_mind_tool = choose_tool(mind_tool, mind_discovery.get("tool_names", []), MIND_AUTO_TOOL_PRIORITY)

    graph_schema = graph_discovery.get("tool_schemas", {}).get(selected_graph_tool, {})
    mind_schema = mind_discovery.get("tool_schemas", {}).get(selected_mind_tool, {})

    graph_args = merge_args(infer_graph_args(selected_graph_tool, task, k_hop, graph_schema), graph_override_args)
    mind_args = merge_args(infer_mind_args(task, mind_schema), mind_override_args)

    task_type = normalize_task_type(task)

    context = {
        "task_id": task.get("id"),
        "task_type": task_type,
        "entry_node": task.get("graph_entry_node"),
        "k_hop": k_hop,
        "related_modules": task.get("related_modules", []),
        "whitelist_files": task.get("related_files", []),
        "suggested_tests": [],
        "vector_refs": [],
        "mcp": {
            "graph": graph_discovery,
            "mind": mind_discovery,
        },
        "selected_tools": {
            "graph": selected_graph_tool,
            "mind": selected_mind_tool,
        },
        "tool_args": {
            "graph": graph_args,
            "mind": mind_args,
        },
        "mcp_calls": {},
        "mind_fallback": {
            "enabled": mind_fallback_enabled,
            "attempted": False,
            "succeeded": False,
            "attempts": [],
            "query_used": str(mind_args.get("query", "")),
            "max_attempts": mind_fallback_max_attempts,
            "budget_max_tool_calls": budget_max_tool_calls,
            "ranked_queries": [],
        },
    }

    context["mcp_calls"]["graph"] = try_tool_call(
        graph_mcp_url,
        selected_graph_tool,
        graph_args,
        timeout,
        req_id="harness-graph-tool-call",
        client_name="harness-graph-call",
    )
    context["mcp_calls"]["mind"] = try_tool_call(
        mind_mcp_url,
        selected_mind_tool,
        mind_args,
        timeout,
        req_id="harness-mind-tool-call",
        client_name="harness-mind-call",
    )

    refs = normalize_vector_refs_from_mind_call(context["mcp_calls"]["mind"])

    if not refs and mind_fallback_enabled and selected_mind_tool:
        context["mind_fallback"]["attempted"] = True
        initial_query = str(mind_args.get("query", ""))
        fallback_queries = build_mind_fallback_queries(task, initial_query, mind_fallback_queries)

        ranked = rank_fallback_queries(fallback_queries, task, task_type)
        context["mind_fallback"]["ranked_queries"] = ranked

        attempt_budget = resolve_mind_fallback_max_attempts(
            mind_fallback_max_attempts,
            budget_max_tool_calls,
            len(fallback_queries),
        )
        context["mind_fallback"]["max_attempts"] = attempt_budget

        for idx, query in enumerate(fallback_queries[:attempt_budget], start=1):
            fb_args = dict(mind_args)
            fb_args["query"] = query
            if "top_k" in fb_args:
                fb_args["top_k"] = max(int(fb_args.get("top_k", 0) or 0), mind_fallback_top_k)
            else:
                fb_args["top_k"] = mind_fallback_top_k

            fb_call = try_tool_call(
                mind_mcp_url,
                selected_mind_tool,
                fb_args,
                timeout,
                req_id=f"harness-mind-tool-call-fallback-{idx}",
                client_name="harness-mind-call-fallback",
            )
            fb_refs = normalize_vector_refs_from_mind_call(fb_call)

            attempt = {
                "query": query,
                "args": fb_args,
                "called": fb_call.get("called", False),
                "reason": fb_call.get("reason", ""),
                "vector_ref_count": len(fb_refs),
            }
            context["mind_fallback"]["attempts"].append(attempt)

            if fb_refs:
                refs = fb_refs
                context["mind_fallback"]["succeeded"] = True
                context["mind_fallback"]["query_used"] = query
                context["mcp_calls"]["mind_fallback_selected"] = fb_call
                break

    context["vector_refs"] = refs

    return context


def main() -> int:
    parser = argparse.ArgumentParser(description="Select bounded context for a task")
    parser.add_argument("--state", default=".harness/state/feature_list.json")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--k-hop", type=int, default=DEFAULT_K_HOP)
    parser.add_argument("--output", default="-")
    parser.add_argument("--graph-mcp-url", default=os.getenv("GRAPH_MCP_URL", "http://127.0.0.1:8788/mcp"))
    parser.add_argument("--mind-mcp-url", default=os.getenv("MIND_MCP_URL", os.getenv("VECTOR_MCP_URL", "http://127.0.0.1:8789/mcp")))
    parser.add_argument("--graph-mcp-tool", default=os.getenv("GRAPH_MCP_TOOL", ""))
    parser.add_argument("--mind-mcp-tool", default=os.getenv("MIND_MCP_TOOL", ""))
    parser.add_argument("--graph-mcp-tool-args-json", default=os.getenv("GRAPH_MCP_TOOL_ARGS_JSON", ""))
    parser.add_argument("--mind-mcp-tool-args-json", default=os.getenv("MIND_MCP_TOOL_ARGS_JSON", ""))
    parser.add_argument("--mind-fallback-enabled", default=os.getenv("MIND_FALLBACK_ENABLED", "1"))
    parser.add_argument("--mind-fallback-queries", default=os.getenv("MIND_FALLBACK_QUERIES", ""))
    parser.add_argument("--mind-fallback-top-k", type=int, default=int(os.getenv("MIND_FALLBACK_TOP_K", str(DEFAULT_MIND_FALLBACK_TOP_K))))
    parser.add_argument("--mind-fallback-max-attempts", type=int, default=int(os.getenv("MIND_FALLBACK_MAX_ATTEMPTS", "0")))
    parser.add_argument("--budget-max-tool-calls", type=int, default=int(os.getenv("BUDGET_MAX_TOOL_CALLS", str(DEFAULT_BUDGET_MAX_TOOL_CALLS))))
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    args = parser.parse_args()

    state_path = Path(args.state)
    payload = load_feature_state(state_path)
    features = payload.get("features", [])
    task = select_task(features, args.task_id)

    graph_override_args = parse_json_obj(args.graph_mcp_tool_args_json)
    mind_override_args = parse_json_obj(args.mind_mcp_tool_args_json)

    context = build_context(
        task,
        args.k_hop,
        args.graph_mcp_url,
        args.mind_mcp_url,
        args.graph_mcp_tool,
        args.mind_mcp_tool,
        graph_override_args,
        mind_override_args,
        mind_fallback_enabled=parse_bool_env(args.mind_fallback_enabled, True),
        mind_fallback_queries=parse_query_list(args.mind_fallback_queries),
        mind_fallback_top_k=args.mind_fallback_top_k,
        mind_fallback_max_attempts=args.mind_fallback_max_attempts,
        budget_max_tool_calls=args.budget_max_tool_calls,
        timeout=args.timeout,
    )

    serialized = json.dumps(context, ensure_ascii=True, indent=2)
    if args.output == "-":
        print(serialized)
    else:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(serialized + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
