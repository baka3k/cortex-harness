"""link_mcp tool tests (Phase 05) — direct envelope calls, repo style.

No in-memory FastMCP client exists in this repo (convention, digest §8):
tools are exercised through their registered wrapper functions (which
produce the v2 ``CallToolResult``), and the boot smoke test drives a real
server process over HTTP.

Run: PYTHONPATH=link-tiny python -m pytest link-tiny/tests/test_mcp_tools.py -q
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
LINK_TINY = Path(__file__).resolve().parents[1]


def _load(name: str):
    if str(LINK_TINY) not in sys.path:
        sys.path.insert(0, str(LINK_TINY))
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    spec = importlib.util.spec_from_file_location(name, LINK_TINY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


store = _load("link_store")
mcp_server = _load("mcp_server")

V2_KEYS = {
    "contract",
    "contractVersion",
    "tool",
    "message",
    "ok",
    "data",
    "error",
    "truncation",
}


class StubFastMCP:
    """Captures registered tools so wrappers can be invoked directly."""

    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def decorate(function):
            self.tools[function.__name__] = function
            return function

        return decorate


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Registry config + instance links dir with fixture records."""
    config_dir = tmp_path / ".cortext-harness" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "dev.json").write_text(
        json.dumps({"active": True, "project": {"code": "fixt", "name": "fixt"}}),
        encoding="utf-8",
    )
    # a second registered project with no links built yet
    (config_dir / "other.json").write_text(
        json.dumps({"active": False, "project": {"code": "fixt_empty"}}),
        encoding="utf-8",
    )
    instance = tmp_path / "instance"
    instance.mkdir()
    monkeypatch.setenv("CORTEX_HARNESS_CONFIG_PATH", str(config_dir / "dev.json"))
    monkeypatch.setenv("CORTEX_LINKS_ROOT", str(instance))
    monkeypatch.delenv("CORTEX_SENSITIVE_TERMS", raising=False)
    links_dir = instance / "links" / "fixt"

    def record(name, path, status=None, score=1.0, doc_id="docs/auth.md"):
        payload = {
            "project_id": "fixt",
            "relation": "implements",
            "doc": {"doc_id": doc_id, "paragraph_id": 1, "source_sha": "a" * 8},
            "code": {
                "target_type": "symbol",
                "parser_type": "python",
                "path": path,
                "name": name,
                "lines": [10, 20],
                "source_sha": "b" * 8,
            },
            "score": score,
            "provenance": "exact-name",
        }
        if status:
            payload["status"] = status
        return payload

    store.append_links(
        links_dir,
        [
            record("login_handler", "src/auth/login.py"),
            record("session_store", "src/auth/session.py"),
            record("old_thing", "src/auth/gone.py", status="stale"),
        ],
    )
    store.save_state(
        links_dir,
        {
            "built_at": "2026-10-19T00:00:00Z",
            "project_id": "fixt",
            "doc_shas": {"docs/auth.md": "a" * 8},
            "code_shas": {},
            "counts": {"active": 2, "stale": 1, "total": 3},
            "coverage": {"doc_coverage": 1.0},
            "sha_join": {
                "doc_matched": 2,
                "doc_missing": 0,
                "code_matched": 2,
                "code_missing": 0,
                "code_ambiguous": 0,
            },
        },
    )
    return {"links_dir": links_dir, "instance": instance, "tmp": tmp_path}


@pytest.fixture
def tools():
    stub = StubFastMCP()
    mcp_server.register_tools(stub)
    return stub.tools


def _envelope(result) -> dict:
    assert isinstance(result, object)
    text = result.content[0].text
    return json.loads(text)


# ---------------------------------------------------------------------------
# Contract + lookups
# ---------------------------------------------------------------------------


def test_envelope_v2(tools, project):
    result = tools["trace_status"](project_id="fixt")
    envelope = _envelope(result)
    assert set(envelope) == V2_KEYS
    assert envelope["contract"] == "cortex.mcp.tool-result"
    assert envelope["contractVersion"] == "2.0"
    assert envelope["tool"] == "trace_status"
    assert envelope["ok"] is True
    assert result.isError is False


def test_trace_status_reports_sha_join(tools, project):
    """state.json['sha_join'] passes through to trace_status (plan telemetry)."""
    status = _envelope(tools["trace_status"](project_id="fixt"))["data"]
    assert status["sha_join"]["code_matched"] == 2
    assert status["sha_join"]["code_ambiguous"] == 0


def test_find_code_for_doc(tools, project, monkeypatch):
    captured = {}

    def fake_snippet(project_id, doc_id, paragraph_id):
        captured.update(project_id=project_id, doc_id=doc_id, paragraph_id=paragraph_id)
        return ("The login_handler …", True)

    monkeypatch.setattr(mcp_server, "paragraph_snippet", fake_snippet)
    result = tools["find_code_for_doc"](project_id="fixt", doc_id="docs/auth.md")
    envelope = _envelope(result)
    assert envelope["ok"] is True
    results = envelope["data"]["results"]
    assert [r["code"]["name"] for r in results] == ["login_handler", "session_store"]
    assert results[0]["provenance"] == "exact-name"
    assert results[0]["score"] == 1.0
    assert results[0]["snippet"] == "The login_handler …"
    # snippet fetch is project-scoped (R2: no cross-project fan-out)
    assert captured["project_id"] == "fixt"
    assert captured["doc_id"] == "docs/auth.md"
    # stale links never surface in results
    assert "old_thing" not in {r["code"]["name"] for r in results}


def test_top_k_clamped(tools, project, monkeypatch):
    """top_k is server-capped; enrichment stops at the snippet budget."""
    calls = []

    def fake_snippet(project_id, doc_id, paragraph_id):
        calls.append(doc_id)
        return ("s", True)

    monkeypatch.setattr(mcp_server, "paragraph_snippet", fake_snippet)
    envelope = _envelope(
        tools["find_code_for_doc"](project_id="fixt", top_k=10_000)
    )
    assert envelope["ok"] is True
    # only 2 records exist; the clamp must not error and truncation is
    # computed against the clamped cap
    assert "truncation" not in envelope["data"]
    # enrichment budget: with 2 results both get snippets (budget 20)
    assert len(calls) == 2


def test_redact_before_truncate(monkeypatch):
    """A sensitive term straddling the snippet cut must not leak a prefix."""
    monkeypatch.setattr(
        mcp_server, "load_sensitive_terms", lambda: [("supersecrettoken", False)]
    )
    # the term starts inside the snippet window but crosses the 240-char cut
    long_text = ("lorem " * 39) + "supersecrettoken with trailing prose past the cap"
    assert long_text.index("supersecrettoken") < mcp_server.SNIPPET_MAX_CHARS
    assert long_text.index("supersecrettoken") + len("supersecrettoken") > mcp_server.SNIPPET_MAX_CHARS
    filtered = mcp_server.filter_sensitive_text(long_text)
    snippet = filtered[: mcp_server.SNIPPET_MAX_CHARS]
    assert "supersecrettoken" not in snippet
    assert "supersecrettoken" not in filtered
    assert "▮" in filtered


def test_find_doc_for_code(tools, project):
    result = tools["find_doc_for_code"](
        project_id="fixt", path="src/auth/login.py", name="login_handler"
    )
    envelope = _envelope(result)
    results = envelope["data"]["results"]
    assert len(results) == 1
    assert results[0]["doc"]["doc_id"] == "docs/auth.md"
    assert results[0]["code"]["path"] == "src/auth/login.py"


def test_find_doc_for_code_requires_identity(tools, project):
    envelope = _envelope(tools["find_doc_for_code"](project_id="fixt"))
    assert envelope["data"]["results"] == []  # no unscoped dumps


def test_stale_excluded_but_visible_in_status(tools, project):
    envelope = _envelope(tools["find_code_for_doc"](project_id="fixt"))
    assert "old_thing" not in {
        r["code"]["name"] for r in envelope["data"]["results"]
    }
    status = _envelope(tools["trace_status"](project_id="fixt"))["data"]
    assert status["stale_links"] == 1
    assert status["counts"] == {"active": 2, "stale": 1, "total": 3}


def test_truncation_hint(tools, project):
    envelope = _envelope(tools["find_code_for_doc"](project_id="fixt", top_k=1))
    data = envelope["data"]
    assert len(data["results"]) == 1
    assert data["truncation"] == {"truncated": True, "top_k": 1}
    assert "truncated" in data["note"]


def test_upstream_down_degrade(tools, project, monkeypatch):
    """Upstream unreachable → refs + 'content unavailable', still ok=true."""

    def unavailable(endpoint, tool, arguments):
        raise mcp_server.UpstreamUnavailable("mind_mcp down")

    monkeypatch.setattr(mcp_server, "call_upstream_tool", unavailable)
    envelope = _envelope(tools["find_code_for_doc"](project_id="fixt"))
    assert envelope["ok"] is True
    assert envelope["data"]["results"]
    assert "content unavailable" in envelope["data"]["note"]
    assert all("snippet" not in r for r in envelope["data"]["results"])


def test_no_links_hint(tools, project):
    envelope = _envelope(tools["find_code_for_doc"](project_id="fixt_empty"))
    assert envelope["ok"] is True
    assert envelope["data"]["results"] == []
    assert "run 'dev sync link'" in envelope["data"]["note"]


def test_stale_annotation(tools, project):
    # sync-state baseline newer than state.json → stale hint
    sync_state = project["tmp"] / ".cortext-harness" / "sync-state"
    sync_state.mkdir(parents=True, exist_ok=True)
    (sync_state / "docstate.json").write_text("{}", encoding="utf-8")
    time.sleep(1.1)  # mtime resolution
    (sync_state / "docstate.json").write_text("{}", encoding="utf-8")
    envelope = _envelope(tools["find_code_for_doc"](project_id="fixt"))
    assert "trace data stale" in envelope["data"]["note"]
    status = _envelope(tools["trace_status"](project_id="fixt"))["data"]
    assert status["stale"] is True


def test_unregistered_project_is_envelope_error(tools, project):
    result = tools["trace_status"](project_id="ghost")
    envelope = _envelope(result)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "project_not_registered"
    assert result.isError is True


# ---------------------------------------------------------------------------
# Sensitive-terms filter (R2 boundary)
# ---------------------------------------------------------------------------


def test_sensitive_terms_filter(tmp_path):
    terms_file = tmp_path / ".sensitive-terms"
    terms_file.write_text(
        "# comment\nbaka3k\ncase:sample\n*sample-text*\n",  # sensitive-guard:allow (fixture mirrors .sensitive-terms)
        encoding="utf-8",
    )
    terms = mcp_server.load_sensitive_terms(terms_file)
    text = "owner baka3k wrote sample docs about sample yesterday"  # sensitive-guard:allow
    filtered = mcp_server.filter_sensitive_text(text, terms)
    assert "▮" in filtered
    assert "baka3k" not in filtered
    assert "yesterday" in filtered  # untouched
    # missing file → no filtering, no crash
    assert mcp_server.load_sensitive_terms(tmp_path / "missing") == []
    assert mcp_server.filter_sensitive_text("hello", []) == "hello"


# ---------------------------------------------------------------------------
# Boot smoke test — real server process over HTTP
# ---------------------------------------------------------------------------


def test_server_boots(project, tmp_path):
    """`python mcp_server.py` boots and answers trace_status over HTTP."""
    import socket

    import httpx

    port = 8791  # avoid clashing with a live :8790
    endpoint = f"http://127.0.0.1:{port}/mcp"
    env = {
        **os.environ,
        "MCP_SERVER_NAME": "link_mcp_test",
        "CORTEX_HARNESS_CONFIG_PATH": os.environ["CORTEX_HARNESS_CONFIG_PATH"],
        "CORTEX_LINKS_ROOT": str(project["instance"]),
    }
    proc = subprocess.Popen(
        [
            sys.executable,
            str(LINK_TINY / "mcp_server.py"),
            "--transport", "streamable-http",
            "--host", "127.0.0.1",
            "--port", str(port),
            "--path", "/mcp",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            with socket.socket() as probe:
                probe.settimeout(0.5)
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    break
            if proc.poll() is not None:
                pytest.fail("server process exited before accepting connections")
            time.sleep(0.3)
        else:
            pytest.fail("server did not open its port in time")

        status = mcp_server.call_upstream_tool(
            endpoint, "trace_status", {"project_id": "fixt"}
        )
        assert status["ok"] is True
        assert status["data"]["project_id"] == "fixt"
        assert status["contract"] == "cortex.mcp.tool-result"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
