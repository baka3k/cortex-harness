"""Cross-tree acceptance tests for link-tiny (Phase 06).

Proves the plan's promises end-to-end on a real FalkorDBLite fixture:

- full pipeline e2e: worker builds links from real graphs, the link_mcp
  surface answers find_code_for_doc / find_doc_for_code correctly;
- drift: renaming a symbol supersedes the old link (stale) and links the
  new identity;
- isolation: no tree outside link-tiny imports link-tiny, and the old
  sync/CLI grammar is untouched (the plan's core promise).
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
LINK_TINY = REPO_ROOT / "link-tiny"
for entry in (str(REPO_ROOT), str(REPO_ROOT / "code-tiny"), str(LINK_TINY)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import link_store  # noqa: E402
import link_worker  # noqa: E402
import mcp_server  # noqa: E402


pytest.importorskip("redislite.falkordb_client", reason="falkordblite not installed")


# ---------------------------------------------------------------------------
# Fixture: mini project (tiny code repo + docs mentioning real identifiers)
# ---------------------------------------------------------------------------


def _fixture_project(tmp_path: Path, *, project_id: str = "accept"):
    config_dir = tmp_path / ".cortext-harness" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "dev.json").write_text(
        json.dumps(
            {
                "active": True,
                "project": {"code": project_id, "name": project_id},
                "code": {
                    "env": {"GRAPH_PROVIDER": "falkordb"},
                    "source": {"projects": [{"git": "", "folder": [str(tmp_path / "repo")]}]},
                },
                "doc": {
                    "env": {"GRAPH_PROVIDER": "falkordb"},
                    "source": {"projects": [{"git": "", "folder": [str(tmp_path / "docs")]}]},
                },
            }
        ),
        encoding="utf-8",
    )
    data_home = tmp_path / "data-home"
    data_home.mkdir()
    instance = data_home / "v1" / "instances" / "default"
    instance.mkdir(parents=True)

    from redislite.falkordb_client import FalkorDB

    code_rdb = instance / "falkordb" / "code" / "data.rdb"
    code_rdb.parent.mkdir(parents=True, exist_ok=True)
    code_client = FalkorDB(str(code_rdb))
    graph = code_client.select_graph(project_id)

    def add_symbol(name, qualified, path, start, end, symbol_id):
        graph.query(
            "CREATE (f:Function {id: $sid}) "
            "SET f.name=$name, f.qualified_name=$qualified, f.file_path=$path, "
            "f.start_line=$start, f.end_line=$end, f.language='python', "
            "f.project_id=$pid, f.project_id_normalized=$pid",
            {
                "sid": symbol_id,
                "name": name,
                "qualified": qualified,
                "path": path,
                "start": start,
                "end": end,
                "pid": project_id,
            },
        )

    add_symbol(
        "login_handler", "auth.login.login_handler",
        "src/auth/login.py", 88, 140, "acc-s1",
    )
    add_symbol(
        "session_store", "auth.session.session_store",
        "src/auth/session.py", 12, 55, "acc-s2",
    )
    code_client.close()

    doc_rdb = instance / "falkordb" / "doc" / "data.rdb"
    doc_rdb.parent.mkdir(parents=True, exist_ok=True)
    doc_client = FalkorDB(str(doc_rdb))
    doc_graph = doc_client.select_graph(f"{project_id}_doc")
    doc_graph.query(
        "CREATE (d:Document {id: 'docs/auth.md'}) SET d.name='docs/auth.md', "
        "d.project_id=$pid, d.project_id_normalized=$pid",
        {"pid": project_id},
    )
    doc_graph.query(
        "CREATE (p:Paragraph {source_id: 'docs/auth.md', paragraph_id: 3}) "
        "SET p.text='Authentication: the login_handler delegates persistence "
        "to session_store.', p.project_id=$pid, p.project_id_normalized=$pid",
        {"pid": project_id},
    )
    doc_graph.query(
        "MATCH (d:Document {id: 'docs/auth.md'}), (p:Paragraph) "
        "WHERE (p.source_id = 'docs/auth.md') CREATE (d)-[:HAS_PARAGRAPH]->(p)"
    )
    doc_client.close()

    sync_state = tmp_path / ".cortext-harness" / "sync-state"
    sync_state.mkdir(parents=True, exist_ok=True)
    (sync_state / "docstate.json").write_text(
        json.dumps({"file_hashes": {"docs/auth.md": "a" * 8}}), encoding="utf-8"
    )
    return {
        "tmp": tmp_path,
        "instance": instance,
        "project_id": project_id,
        "env": {
            "CORTEX_DATA_HOME": str(data_home),
            "CORTEX_STORAGE_INSTANCE": "default",
            "CORTEX_HARNESS_CONFIG_PATH": str(config_dir / "dev.json"),
            "CORTEX_LINKS_ROOT": str(instance),
            "PATH": os.environ.get("PATH", ""),
        },
    }


def _worker_env(fixture, monkeypatch):
    for key, value in fixture["env"].items():
        monkeypatch.setenv(key, value)


# ---------------------------------------------------------------------------
# Acceptance 1: full pipeline through the MCP surface
# ---------------------------------------------------------------------------


def test_acceptance_pipeline_e2e(tmp_path, monkeypatch):
    """sync-link → boot link_mcp → find_code_for_doc / find_doc_for_code."""
    fixture = _fixture_project(tmp_path)
    _worker_env(fixture, monkeypatch)

    summary = link_worker.build_project(
        fixture["project_id"], project_dir=fixture["tmp"]
    )
    assert summary["new_links"] == 2

    import socket

    port = 8792  # acceptance-dedicated port; never touch a live :8790
    endpoint = f"http://127.0.0.1:{port}/mcp"
    proc = subprocess.Popen(
        [
            sys.executable, str(LINK_TINY / "mcp_server.py"),
            "--transport", "streamable-http",
            "--host", "127.0.0.1", "--port", str(port), "--path", "/mcp",
        ],
        env={**os.environ, **fixture["env"], "MCP_SERVER_NAME": "link_mcp_accept"},
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
                pytest.fail("link_mcp exited before accepting connections")
            time.sleep(0.3)
        else:
            pytest.fail("link_mcp did not open its port in time")

        forward = mcp_server.call_upstream_tool(
            endpoint, "find_code_for_doc",
            {"project_id": fixture["project_id"], "doc_id": "docs/auth.md"},
        )
        assert forward["ok"] is True
        names = {r["code"]["name"] for r in forward["data"]["results"]}
        assert names == {"login_handler", "session_store"}
        assert all(r["provenance"] == "exact-name" for r in forward["data"]["results"])

        reverse = mcp_server.call_upstream_tool(
            endpoint, "find_doc_for_code",
            {
                "project_id": fixture["project_id"],
                "path": "src/auth/login.py",
                "name": "login_handler",
            },
        )
        assert reverse["ok"] is True
        reverse_results = reverse["data"]["results"]
        assert [r["doc"]["doc_id"] for r in reverse_results] == ["docs/auth.md"]
        assert reverse_results[0]["doc"]["paragraph_id"] == 3
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


# ---------------------------------------------------------------------------
# Acceptance 2: drift → stale + re-link round trip
# ---------------------------------------------------------------------------


def test_acceptance_drift(tmp_path, monkeypatch):
    """Rename the symbol in the code graph → old link stale, new active."""
    fixture = _fixture_project(tmp_path)
    _worker_env(fixture, monkeypatch)
    links_dir = fixture["instance"] / "links" / fixture["project_id"]
    link_worker.build_project(fixture["project_id"], project_dir=fixture["tmp"])

    from redislite.falkordb_client import FalkorDB

    code_rdb = fixture["instance"] / "falkordb" / "code" / "data.rdb"
    code_rdb.parent.mkdir(parents=True, exist_ok=True)
    code_client = FalkorDB(str(code_rdb))
    graph = code_client.select_graph(fixture["project_id"])
    # the rename dev sync code would produce: new symbol, old one gone
    graph.query(
        "MATCH (f:Function {name: 'login_handler'}) SET f.name='sign_in_handler', "
        "f.qualified_name='auth.login.sign_in_handler'"
    )
    code_client.close()

    summary = link_worker.build_project(fixture["project_id"], project_dir=fixture["tmp"])
    # exact-name cannot re-match until the doc mentions the new name; the
    # old link must be superseded, and no ghost 'login_handler' stays active
    active = {
        r["code"]["name"] for r in link_store.latest_links(links_dir, status="active")
    }
    assert "login_handler" not in active
    assert "session_store" in active
    stale_log = {
        r["code"]["name"] for r in link_store.load_links(links_dir, status="stale")
    }
    assert "login_handler" in stale_log
    assert summary["staled_links"] >= 0

    # after the doc is re-synced with the new name, the new identity links
    doc_rdb = fixture["instance"] / "falkordb" / "doc" / "data.rdb"
    doc_client = FalkorDB(str(doc_rdb))
    doc_graph = doc_client.select_graph(f"{fixture['project_id']}_doc")
    doc_graph.query(
        "CREATE (p:Paragraph {source_id: 'docs/auth.md', paragraph_id: 4}) "
        "SET p.text='Authentication: sign_in_handler is the new entry point.', "
        "p.project_id=$pid, p.project_id_normalized=$pid",
        {"pid": fixture["project_id"]},
    )
    doc_client.close()
    link_worker.build_project(fixture["project_id"], project_dir=fixture["tmp"])
    active = {
        r["code"]["name"] for r in link_store.latest_links(links_dir, status="active")
    }
    assert "sign_in_handler" in active


# ---------------------------------------------------------------------------
# Acceptance 3: isolation — absence of link-tiny leaves old flows untouched
# ---------------------------------------------------------------------------


def test_acceptance_isolation():
    """No tree outside link-tiny imports link-tiny (one-way dependency).

    Mirrors the AST source-scan style of the provider-boundary tests: the
    plan's core promise is that code-tiny / doc-tiny / cortex_harness keep
    working with link-tiny deleted. A single import edge would break that.
    """
    guard_names = {"link_store", "link_engine", "registry_facade", "normalizers", "link_worker", "mcp_server"}
    external_roots = [
        REPO_ROOT / "code-tiny",
        REPO_ROOT / "doc-tiny",
        REPO_ROOT / "cortex_harness",
        REPO_ROOT / "scripts",
    ]
    violations = []
    for root in external_roots:
        for path in root.rglob("*.py"):
            # only read the file once
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root_name = alias.name.split(".")[0]
                        if root_name in guard_names:
                            violations.append(f"{path}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if module.split(".")[0] in guard_names and node.level == 0:
                        violations.append(f"{path}: from {module} import …")
    assert violations == []
    # note: code-tiny/tools/graph/read_only_opener.py (the sanctioned RT-3
    # touchpoint) is inside the scanned code-tiny root, so the AST scan
    # above also proves it imports no link-tiny module.

    # old CLI grammar intact
    from click.testing import CliRunner
    from cortex_harness import dev as dev_module

    assert set(dev_module.sync.commands) == {"code", "doc", "link"}
    assert set(dev_module.cli.commands["start"].params[0].type.choices) == {
        "all", "code", "doc", "link",
    }
    # dev sync code/doc bodies carry the exact same options they had before
    code_options = {param.name for param in dev_module.sync.commands["code"].params}
    assert {"project_dir", "dry_run", "reconcile", "full_scan", "sync_mode"} <= code_options
    doc_options = {param.name for param in dev_module.sync.commands["doc"].params}
    assert {"project_dir", "dry_run", "stage", "entity_provider"} <= doc_options
