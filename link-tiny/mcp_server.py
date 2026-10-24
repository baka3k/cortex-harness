#!/usr/bin/env python3
"""link_mcp — read-only doc↔code traceability facade (port 8790).

Third MCP server, parallel to graph_mcp (:8788) and mind_mcp (:8789):

* queries ``links.db`` (SQLite WAL with B-Tree indexes) and ``state.json``
  from the instance links directory (sub-millisecond, local, no graph access,
  no leases);
* serves pre-redacted snippets stored in ``links.db`` at link-build time
  (0 HTTP calls in the normal path) and falls back to mind_mcp over HTTP
  when snippets are absent, degrading gracefully when mind_mcp is down;
* emits the shared ``cortex.mcp.tool-result`` v2 envelope on every tool
  (single producer: ``cortex_harness.mcp_contract`` — no copied envelope
  code);
* never writes link data and never imports the engine (isolation contract
  in ``CLAUDE.md``).
"""

from __future__ import annotations

import argparse
import fnmatch
import functools
import inspect
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple


_WORKER_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _WORKER_DIR.parent
for _entry in (
    str(_REPO_ROOT),
    str(_REPO_ROOT / "code-tiny"),
    str(_WORKER_DIR),
):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from fastmcp import FastMCP  # noqa: E402
from mcp.types import CallToolResult, TextContent  # noqa: E402
from cortex_harness.mcp_contract import (  # noqa: E402
    normalize_error,
    normalize_success,
    result_envelope_json,
    result_meta,
    result_summary,
)

import link_store  # noqa: E402
import registry_facade  # noqa: E402
from normalizers import token_match_key  # noqa: E402


MCP_NAME = os.getenv("MCP_SERVER_NAME", "link_mcp")
DEFAULT_GRAPH_MCP_URL = "http://127.0.0.1:8788/mcp"
DEFAULT_MIND_MCP_URL = "http://127.0.0.1:8789/mcp"
SNIPPET_MAX_CHARS = 240
MAX_TOP_K = 100
SNIPPET_FETCH_BUDGET = 20
STALE_HINT = "trace data stale — run 'dev sync link'"
NO_LINKS_HINT = "no links built yet — run 'dev sync link'"


# ---------------------------------------------------------------------------
# Sensitive-terms snippet filter (R2) — first Python consumer of the file
# ---------------------------------------------------------------------------


def sensitive_terms_path() -> Path:
    configured = os.environ.get("CORTEX_SENSITIVE_TERMS")
    return Path(configured).expanduser() if configured else _REPO_ROOT / ".sensitive-terms"


def load_sensitive_terms(path: Optional[Path] = None) -> List[Tuple[str, bool]]:
    """Parse the sensitive-terms file into ``(term, case_sensitive)`` pairs."""
    target = Path(path) if path else sensitive_terms_path()
    if not target.is_file():
        return []
    terms: List[Tuple[str, bool]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("case:"):
            terms.append((stripped[len("case:"):], True))
        else:
            terms.append((stripped, False))
    return terms


def filter_sensitive_text(
    text: str,
    terms: Optional[List[Tuple[str, bool]]] = None,
) -> str:
    """Replace sensitive-term occurrences in ``text`` with ``▮``."""
    if not text:
        return text
    if terms is None:
        terms = load_sensitive_terms()
    filtered = text
    for term, case_sensitive in terms:
        if not term:
            continue
        if "*" in term:
            pattern = re.compile(r"[A-Za-z0-9_]+")
            filtered = pattern.sub(
                lambda match: "▮"
                if fnmatch.fnmatch(match.group(0).casefold(), term.casefold())
                else match.group(0),
                filtered,
            )
            continue
        flags = 0 if case_sensitive else re.IGNORECASE
        filtered = re.sub(
            rf"\b{re.escape(term)}\b", "▮", filtered, flags=flags
        )
    return filtered


# ---------------------------------------------------------------------------
# Upstream content delegation (thin HTTP MCP client, degrade-friendly)
# ---------------------------------------------------------------------------


class UpstreamUnavailable(RuntimeError):
    """Raised when graph_mcp/mind_mcp cannot be reached."""


def upstream_urls() -> Tuple[str, str]:
    graph_url = os.environ.get("GRAPH_MCP_URL") or DEFAULT_GRAPH_MCP_URL
    mind_url = os.environ.get("MIND_MCP_URL") or DEFAULT_MIND_MCP_URL
    return graph_url, mind_url


def call_upstream_tool(endpoint: str, tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Call one tool on an upstream MCP server; return the parsed envelope."""
    try:
        import httpx

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        request_id = 0
        with httpx.Client(timeout=5.0) as client:
            def post(body: Dict[str, Any]) -> Any:
                nonlocal request_id
                request_id += 1
                body = {"jsonrpc": "2.0", "id": request_id, **body}
                response = client.post(endpoint, headers=headers, json=body)
                if response.status_code >= 400:
                    raise UpstreamUnavailable(
                        f"upstream HTTP {response.status_code} from {endpoint}"
                    )
                session = response.headers.get("mcp-session-id")
                if session:
                    headers["mcp-session-id"] = session
                content_type = response.headers.get("content-type", "")
                text = response.text
                if "text/event-stream" in content_type:
                    for line in text.splitlines():
                        line = line.strip()
                        if line.startswith("data:"):
                            payload = line[5:].strip()
                            if payload and payload != "[DONE]":
                                try:
                                    return json.loads(payload)
                                except json.JSONDecodeError:
                                    continue
                    raise UpstreamUnavailable(f"empty SSE reply from {endpoint}")
                return json.loads(text)

            post({"method": "initialize", "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "link_mcp", "version": "1.0.0"},
            }})
            post({"method": "notifications/initialized"})
            reply = post({"method": "tools/call", "params": {
                "name": tool, "arguments": arguments,
            }})
        result = reply.get("result") or {}
        contents = result.get("content") or []
        if not contents:
            raise UpstreamUnavailable(f"upstream {tool} returned no content")
        return json.loads(contents[0].get("text") or "{}")
    except UpstreamUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - any failure degrades
        raise UpstreamUnavailable(f"upstream {tool} unavailable: {exc}") from exc


def paragraph_snippet(
    project_id: str, doc_id: str, paragraph_id: Any
) -> Tuple[Optional[str], bool]:
    """Fetch a filtered short snippet for one paragraph via mind_mcp."""
    _graph_url, mind_url = upstream_urls()
    try:
        envelope = call_upstream_tool(
            mind_url,
            "get_paragraph_text",
            {
                "source_id": doc_id,
                "paragraph_id": paragraph_id,
                "project_id": project_id,
            },
        )
    except UpstreamUnavailable:
        return None, False
    if not envelope.get("ok"):
        return None, False
    data = envelope.get("data")
    text = ""
    if isinstance(data, Mapping):
        text = str(data.get("text") or data.get("paragraph") or "")
    elif isinstance(data, str):
        text = data
    if not text:
        return None, True
    filtered = filter_sensitive_text(text)
    snippet = filtered[:SNIPPET_MAX_CHARS]
    if len(filtered) > SNIPPET_MAX_CHARS:
        snippet += "…"
    return snippet, True


# ---------------------------------------------------------------------------
# Fast Read Model (Indexed SQLite + state.json)
# ---------------------------------------------------------------------------


def _project_metadata(project_id: Any) -> Dict[str, Any]:
    """Resolve targets, links_dir, state.json, and staleness in <1 ms (never scans JSONL)."""
    targets = registry_facade.resolve_project_targets(project_id)
    links_dir = registry_facade.links_dir(targets.project_id)
    state = link_store.load_state(links_dir)
    state_counts = state.get("counts") if isinstance(state, Mapping) else None
    state_path = links_dir / link_store.STATE_FILENAME
    jsonl_path = links_dir / link_store.LINKS_FILENAME
    if (
        isinstance(state_counts, Mapping)
        and state_path.is_file()
        and (not jsonl_path.is_file() or state_path.stat().st_mtime >= jsonl_path.stat().st_mtime)
    ):
        counts: Optional[Dict[str, int]] = {
            "active": int(state_counts.get("active") or 0),
            "stale": int(state_counts.get("stale") or 0),
            "total": int(state_counts.get("total") or 0),
        }
    else:
        counts = link_store.query_status_counts(links_dir)

    stale_reason: Optional[str] = None
    if state is None:
        stale_reason = NO_LINKS_HINT
    else:
        built_at = str(state.get("built_at") or "")
        newest_baseline = _newest_sync_state_mtime()
        if newest_baseline is not None and built_at:
            try:
                from datetime import datetime, timezone

                built = datetime.fromisoformat(built_at.replace("Z", "+00:00"))
                if newest_baseline > built:
                    stale_reason = STALE_HINT
            except ValueError:
                stale_reason = STALE_HINT
    has_any_links = bool(
        (counts and counts.get("total", 0) > 0)
        or (state and ((state.get("counts") or {}).get("total") or 0) > 0)
    )
    return {
        "targets": targets,
        "links_dir": links_dir,
        "state": state,
        "counts": counts,
        "has_any_links": has_any_links,
        "stale_reason": stale_reason,
    }


def _newest_sync_state_mtime() -> Any:
    """Newest mtime among the ACTIVE project root's sync-state files."""
    project_root = registry_facade.project_root_from_config()
    state_dir = project_root / ".cortext-harness" / "sync-state"
    if not state_dir.is_dir():
        return None
    newest = None
    for path in state_dir.glob("*.json"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        newest = mtime if newest is None else max(newest, mtime)
    if newest is None:
        return None
    from datetime import datetime, timezone

    return datetime.fromtimestamp(newest, tz=timezone.utc)


def _match_query(record: Mapping[str, Any], query: Optional[str]) -> bool:
    if not query:
        return True
    needle = token_match_key(query)
    if not needle:
        return True
    code = record.get("code") or {}
    haystacks = (
        token_match_key(code.get("name")),
        token_match_key(code.get("path")),
        token_match_key(record.get("doc", {}).get("doc_id")),
    )
    return any(needle == key or (key and needle in _subsequences(key)) for key in haystacks if key)


def _subsequences(key: Tuple[str, ...]) -> List[Tuple[str, ...]]:
    return [
        tuple(key[start:end])
        for start in range(len(key))
        for end in range(start + 1, len(key) + 1)
    ]


def _result_view(record: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "relation": record.get("relation"),
        "score": record.get("score"),
        "provenance": record.get("provenance"),
        "status": record.get("status"),
        "doc": record.get("doc"),
        "code": record.get("code"),
    }


# ---------------------------------------------------------------------------
# Tool business logic (pure functions — envelope wrapping is separate)
# ---------------------------------------------------------------------------


def find_code_for_doc_impl(
    project_id: Optional[str] = None,
    doc_id: Optional[str] = None,
    query: Optional[str] = None,
    top_k: int = 20,
    min_score: float = 0.0,
) -> Dict[str, Any]:
    """Ranked active code refs implementing a document (refs + snippets)."""
    meta = _project_metadata(project_id)
    top_k = max(1, min(int(top_k), MAX_TOP_K))
    queried = link_store.query_code_for_doc(
        meta["links_dir"],
        doc_id=doc_id,
        query=query,
        top_k=top_k,
        min_score=min_score,
    )
    if queried is not None:
        matched_items, total_matched = queried
        truncated = total_matched > top_k
    else:
        matched_items = []
        truncated = False

    results: List[Dict[str, Any]] = []
    content_available = True
    for index, (record, local_snippet) in enumerate(matched_items):
        view = _result_view(record)
        if local_snippet:
            view["snippet"] = filter_sensitive_text(local_snippet)[:SNIPPET_MAX_CHARS]
        elif index < SNIPPET_FETCH_BUDGET:
            doc = record.get("doc") or {}
            snippet, available = paragraph_snippet(
                meta["targets"].project_id,
                str(doc.get("doc_id") or ""),
                doc.get("paragraph_id"),
            )
            content_available = content_available and available
            if snippet is not None:
                view["snippet"] = snippet
        results.append(view)

    data: Dict[str, Any] = {
        "project_id": meta["targets"].project_id,
        "results": results,
    }
    notes: List[str] = []
    if meta["stale_reason"]:
        notes.append(meta["stale_reason"])
    if not content_available:
        notes.append("content unavailable")
    if truncated:
        notes.append(
            f"results truncated to top_k={top_k}; narrow the query and retry"
        )
        data["truncation"] = {"truncated": True, "top_k": top_k}
    if not meta["has_any_links"] and NO_LINKS_HINT not in notes:
        notes.append(NO_LINKS_HINT)
    if notes:
        data["note"] = "; ".join(notes)
    return data


def find_doc_for_code_impl(
    project_id: Optional[str] = None,
    path: Optional[str] = None,
    name: Optional[str] = None,
    top_k: int = 20,
    min_score: float = 0.0,
) -> Dict[str, Any]:
    """Reverse lookup: documents linked to a code symbol identity."""
    meta = _project_metadata(project_id)
    top_k = max(1, min(int(top_k), MAX_TOP_K))
    if not path and not name:
        queried = ([], 0)
    else:
        queried = link_store.query_doc_for_code(
            meta["links_dir"],
            path=path,
            name=name,
            top_k=top_k,
            min_score=min_score,
        )
    if queried is not None:
        matched_items, total_matched = queried
        truncated = total_matched > top_k
    else:
        matched_items = []
        truncated = False

    results: List[Dict[str, Any]] = []
    for record, local_snippet in matched_items:
        view = _result_view(record)
        if local_snippet:
            view["snippet"] = filter_sensitive_text(local_snippet)[:SNIPPET_MAX_CHARS]
        results.append(view)

    data: Dict[str, Any] = {
        "project_id": meta["targets"].project_id,
        "results": results,
    }
    notes: List[str] = []
    if meta["stale_reason"]:
        notes.append(meta["stale_reason"])
    if truncated:
        notes.append(
            f"results truncated to top_k={top_k}; narrow the query and retry"
        )
        data["truncation"] = {"truncated": True, "top_k": top_k}
    if not meta["has_any_links"] and NO_LINKS_HINT not in notes:
        notes.append(NO_LINKS_HINT)
    if notes:
        data["note"] = "; ".join(notes)
    return data


def trace_status_impl(project_id: Optional[str] = None) -> Dict[str, Any]:
    """State snapshot: built_at, counts, coverage, stale verdict."""
    meta = _project_metadata(project_id)
    state = meta["state"] or {}
    db_counts = meta["counts"] or {}
    state_counts = state.get("counts")
    stale_links = int(
        db_counts.get("stale")
        if db_counts
        else ((state_counts or {}).get("stale") or 0)
    )
    data: Dict[str, Any] = {
        "project_id": meta["targets"].project_id,
        "built_at": state.get("built_at"),
        "counts": state_counts or (db_counts if db_counts else None),
        "coverage": state.get("coverage"),
        "sha_join": state.get("sha_join"),
        "symbol_universe_by_label": state.get("symbol_universe_by_label"),
        "symbol_universe_expected": state.get("symbol_universe_expected"),
        "stale_links": stale_links,
        "stale": meta["stale_reason"] is not None,
    }
    notes: List[str] = []
    if meta["stale_reason"]:
        notes.append(meta["stale_reason"])
    if notes:
        data["note"] = "; ".join(notes)
    return data


# ---------------------------------------------------------------------------
# Envelope plumbing (copy of the doc server's _standard_tool pattern)
# ---------------------------------------------------------------------------


def _standard_tool(mcp: FastMCP):
    """Register a link tool with the shared Cortex MCP v2 result contract."""

    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            try:
                data = function(*args, **kwargs)
            except Exception as exc:
                envelope = normalize_error(exc)
                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=result_envelope_json(
                                function.__name__,
                                ok=False,
                                data=None,
                                error=envelope["error"],
                                message=envelope["error"]["message"],
                            ),
                        )
                    ],
                    isError=True,
                    _meta=result_meta(function.__name__),
                )

            envelope = normalize_success(data)
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=result_envelope_json(
                            function.__name__,
                            ok=True,
                            data=envelope["data"],
                            message=result_summary(data, ok=True),
                        ),
                    )
                ],
                isError=False,
                _meta=result_meta(function.__name__),
            )

        wrapped.__annotations__ = {
            key: value
            for key, value in function.__annotations__.items()
            if key != "return"
        }
        wrapped.__signature__ = inspect.signature(function).replace(
            return_annotation=inspect.Signature.empty
        )
        return mcp.tool()(wrapped)

    return decorate


def register_tools(mcp: FastMCP) -> None:
    @_standard_tool(mcp)
    def find_code_for_doc(
        project_id: Optional[str] = None,
        doc_id: Optional[str] = None,
        query: Optional[str] = None,
        top_k: int = 20,
        min_score: float = 0.0,
    ) -> Dict[str, Any]:
        """Find code symbols implementing a document (exact-name & IDF-ranked links)."""
        return find_code_for_doc_impl(project_id, doc_id, query, top_k, min_score)

    @_standard_tool(mcp)
    def find_doc_for_code(
        project_id: Optional[str] = None,
        path: Optional[str] = None,
        name: Optional[str] = None,
        top_k: int = 20,
        min_score: float = 0.0,
    ) -> Dict[str, Any]:
        """Reverse lookup: documents linked to a code symbol identity."""
        return find_doc_for_code_impl(project_id, path, name, top_k, min_score)

    @_standard_tool(mcp)
    def trace_status(project_id: Optional[str] = None) -> Dict[str, Any]:
        """Link-build state snapshot with staleness verdict for a project."""
        return trace_status_impl(project_id)


def build_mcp() -> FastMCP:
    mcp = FastMCP(
        name=MCP_NAME,
        version="1.0.0",
        instructions=(
            "Read-only doc↔code traceability. Links are IDF-ranked exact-name "
            "and context-scoped matches. Full document/paragraph content lives "
            "in mind_mcp; full symbol content in graph_mcp."
        ),
    )
    register_tools(mcp)
    return mcp


mcp_server = build_mcp()


# ---------------------------------------------------------------------------
# Launch (same argparse contract as the other servers)
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="mcp_server.py")
    parser.add_argument(
        "--transport",
        default=os.getenv("FASTMCP_TRANSPORT", "streamable-http"),
    )
    parser.add_argument("--host", default=os.getenv("FASTMCP_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("FASTMCP_PORT", "8790"))
    )
    parser.add_argument(
        "--path",
        default=os.getenv("FASTMCP_STREAMABLE_HTTP_PATH", "/mcp"),
    )
    args = parser.parse_args(argv)
    if not args.path.startswith("/"):
        args.path = "/" + args.path
    return args


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    run_kwargs: Dict[str, Any] = {}
    if args.transport == "streamable-http":
        run_kwargs = {"stateless_http": True, "json_response": True}
    mcp_server.run(
        transport=args.transport,
        host=args.host,
        port=args.port,
        path=args.path,
        **run_kwargs,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
