"""``dev sync link`` CLI + worker tests (plan phase 04).

Covers:

- the link worker e2e on a real FalkorDBLite fixture project (writes links)
- ``--dry-run`` / ``--project`` / ``--reconcile`` semantics
- unknown project → rc 2, no partial writes
- ``_is_link_worker`` classification + ``SyncOwner`` widening
- lifecycle ``--server all`` still selects only code + doc (default opt-in)
- ``dev sync code`` / ``dev sync doc`` groups behave exactly as before
  (the plan's core isolation promise)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
LINK_TINY = REPO_ROOT / "link-tiny"
for entry in (str(REPO_ROOT), str(REPO_ROOT / "code-tiny"), str(LINK_TINY)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import link_store  # noqa: E402
import link_worker  # noqa: E402
from cortex_harness import dev as dev_module  # noqa: E402
from cortex_harness import sync_processes as process_module  # noqa: E402


pytest.importorskip("redislite.falkordb_client", reason="falkordblite not installed")


# ---------------------------------------------------------------------------
# Fixture: a tiny registered project with real FalkorDBLite graphs
# ---------------------------------------------------------------------------


def _make_fixture(tmp_path: Path, *, project_id: str = "fixt") -> dict:
    """config + code graph + doc graph + project docs; returns env dict."""
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

    # layout per resolve_storage: {instance}/falkordb/{owner}/data.rdb
    code_rdb = instance / "falkordb" / "code" / "data.rdb"
    code_rdb.parent.mkdir(parents=True, exist_ok=True)
    code_client = FalkorDB(str(code_rdb))
    graph = code_client.select_graph(project_id)
    graph.query(
        "CREATE (f:Function {id: 's1'}) "
        "SET f.name='login_handler', f.qualified_name='auth.login.login_handler', "
        "f.file_path='src/auth/login.py', f.start_line=10, f.end_line=20, "
        "f.language='python', f.project_id=$pid, f.project_id_normalized=$pid",
        {"pid": project_id},
    )
    graph.query(
        "CREATE (f:Function {id: 's2'}) "
        "SET f.name='session_store', f.qualified_name='auth.session.session_store', "
        "f.file_path='src/auth/session.py', f.start_line=5, f.end_line=9, "
        "f.language='python', f.project_id=$pid, f.project_id_normalized=$pid",
        {"pid": project_id},
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
        "CREATE (p:Paragraph {source_id: 'docs/auth.md', paragraph_id: 1}) "
        "SET p.text='The login_handler delegates to session_store for state.', "
        "p.project_id=$pid, p.project_id_normalized=$pid",
        {"pid": project_id},
    )
    doc_graph.query(
        "MATCH (d:Document {id: 'docs/auth.md'}), (p:Paragraph) "
        "WHERE (p.source_id = 'docs/auth.md') "
        "CREATE (d)-[:HAS_PARAGRAPH]->(p)"
    )
    doc_client.close()

    # doc sync baseline (dev.py writes file_hashes under sync-state)
    sync_state = tmp_path / ".cortext-harness" / "sync-state"
    sync_state.mkdir(parents=True, exist_ok=True)
    (sync_state / "docstate.json").write_text(
        json.dumps({"file_hashes": {"docs/auth.md": "a" * 8}}), encoding="utf-8"
    )

    env = {
        "CORTEX_DATA_HOME": str(data_home),
        "CORTEX_STORAGE_INSTANCE": "default",
        "CORTEX_HARNESS_CONFIG_PATH": str(config_dir / "dev.json"),
        "CORTEX_LINKS_ROOT": str(instance),
        "PATH": os.environ.get("PATH", ""),
    }
    return {"env": env, "instance": instance, "tmp": tmp_path}


def _worker_env(fixture: dict, monkeypatch) -> None:
    for key, value in fixture["env"].items():
        monkeypatch.setenv(key, value)


# ---------------------------------------------------------------------------
# Worker e2e
# ---------------------------------------------------------------------------


def test_sync_link_default(tmp_path, monkeypatch):
    fixture = _make_fixture(tmp_path)
    _worker_env(fixture, monkeypatch)
    summary = link_worker.build_project("fixt", project_dir=fixture["tmp"])

    links_dir = fixture["instance"] / "links" / "fixt"
    records = link_store.latest_links(links_dir, status="active")
    names = {record["code"]["name"] for record in records}
    assert names == {"login_handler", "session_store"}
    for record in records:
        assert record["provenance"] == "exact-name"
        assert record["score"] == 1.0
        assert record["doc"]["doc_id"] == "docs/auth.md"
    assert summary["new_links"] == 2
    assert summary["coverage"]["docs_with_active_links"] == 1
    # second run is a delta no-op
    again = link_worker.build_project("fixt", project_dir=fixture["tmp"])
    assert again["new_links"] == 0
    assert again["unchanged_links"] == 2


def test_dry_run_no_write(tmp_path, monkeypatch, capsys):
    fixture = _make_fixture(tmp_path)
    _worker_env(fixture, monkeypatch)
    rc = link_worker.main(["--project-id", "fixt", "--project-dir", str(fixture["tmp"]), "--dry-run"])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["dry_run"] is True
    assert len(summary["would_link"]) == 2
    links_dir = fixture["instance"] / "links" / "fixt"
    assert not (links_dir / "links.jsonl").exists()
    assert not (links_dir / "state.json").exists()


def test_project_scope(tmp_path, monkeypatch):
    fixture = _make_fixture(tmp_path)
    _worker_env(fixture, monkeypatch)
    # an unknown id never writes anything
    rc = link_worker.main(["--project-id", "other", "--project-dir", str(fixture["tmp"])])
    assert rc == 2
    links_root = fixture["instance"] / "links"
    assert not (links_root / "other").exists()
    # and the known project still links only its own directory
    link_worker.build_project("fixt", project_dir=fixture["tmp"])
    assert (links_root / "fixt" / "links.jsonl").exists()
    assert [p.name for p in links_root.iterdir()] == ["fixt"]


def test_unknown_project_error(tmp_path, monkeypatch, capsys):
    fixture = _make_fixture(tmp_path)
    _worker_env(fixture, monkeypatch)
    rc = link_worker.main(["--project-id", "ghost", "--project-dir", str(fixture["tmp"])])
    assert rc == 2
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["error"] == "project_not_registered"
    assert not (fixture["instance"] / "links" / "ghost").exists()


def test_reconcile_full(tmp_path, monkeypatch):
    fixture = _make_fixture(tmp_path)
    _worker_env(fixture, monkeypatch)
    links_dir = fixture["instance"] / "links" / "fixt"
    link_worker.build_project("fixt", project_dir=fixture["tmp"])
    summary = link_worker.build_project(
        "fixt", project_dir=fixture["tmp"], reconcile=True
    )
    assert summary["reconcile"] is True
    raw_stale = link_store.load_links(links_dir, status="stale")
    assert len(raw_stale) == 2  # old set superseded in the log
    assert len(link_store.latest_links(links_dir, status="active")) == 2


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def test_cli_invokes_worker(tmp_path, monkeypatch):
    from click.testing import CliRunner

    config_dir = tmp_path / ".cortext-harness" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "dev.json").write_text(
        json.dumps({"active": True, "project": {"code": "proj", "name": "proj"}}),
        encoding="utf-8",
    )
    captured = {}

    def fake_run(cmd, env=None, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = env
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(dev_module.subprocess, "run", fake_run)
    result = CliRunner().invoke(
        dev_module.cli,
        ["sync", "link", "--project-dir", str(tmp_path), "--reconcile"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    cmd = captured["cmd"]
    assert cmd[1].endswith("link_worker.py")
    assert "--project-id" in cmd and "proj" in cmd
    assert "--reconcile" in cmd
    assert "CORTEX_HARNESS_CONFIG_PATH" in captured["env"]


def test_cli_dry_run_flag_passthrough(tmp_path, monkeypatch):
    from click.testing import CliRunner

    config_dir = tmp_path / ".cortext-harness" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "dev.json").write_text(
        json.dumps({"active": True, "project": {"code": "proj"}}), encoding="utf-8"
    )
    captured = {}
    monkeypatch.setattr(
        dev_module.subprocess,
        "run",
        lambda cmd, env=None, **kw: captured.update(cmd=cmd)
        or type("R", (), {"returncode": 0})(),
    )
    result = CliRunner().invoke(
        dev_module.cli,
        ["sync", "link", "--project-dir", str(tmp_path), "--dry-run"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert "--dry-run" in captured["cmd"]


def test_cli_unknown_project_maps_rc2(tmp_path, monkeypatch):
    from click.testing import CliRunner

    config_dir = tmp_path / ".cortext-harness" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "dev.json").write_text(
        json.dumps({"active": True, "project": {"code": "proj"}}), encoding="utf-8"
    )
    monkeypatch.setattr(
        dev_module.subprocess,
        "run",
        lambda cmd, env=None, **kw: type("R", (), {"returncode": 2})(),
    )
    result = CliRunner().invoke(
        dev_module.cli,
        ["sync", "link", "--project-dir", str(tmp_path)],
        catch_exceptions=False,
    )
    assert result.exit_code != 0
    assert "invalid input" in result.output


def test_stop_link_worker_classification():
    """_is_link_worker matches the worker argv; code/doc owners do not."""
    record = process_module.ProcessRecord(
        pid=424242,
        ppid=1,
        argv=(
            str(REPO_ROOT / ".venv" / "bin" / "python"),
            str(REPO_ROOT / "link-tiny" / "link_worker.py"),
            "--project-id",
            "fixt",
        ),
    )
    assert process_module.sync_processes("link", root=REPO_ROOT, processes={424242: record}) == [record]
    assert process_module.sync_processes("code", root=REPO_ROOT, processes={424242: record}) == []
    assert process_module.sync_processes("doc", root=REPO_ROOT, processes={424242: record}) == []
    # dev sync link launchers classify too
    launcher = process_module.ProcessRecord(
        pid=424243,
        ppid=1,
        argv=(str(REPO_ROOT / "cortex_harness" / "dev.py"), "sync", "link"),
    )
    assert process_module.sync_processes(
        "link", root=REPO_ROOT, processes={424243: launcher}, include_launchers=True
    ) == [launcher]
    # explicit stop sweeps still match launchers only via include_launchers
    assert process_module.sync_processes(
        "link", root=REPO_ROOT, processes={424243: launcher}, include_launchers=False
    ) == []


def test_old_sync_untouched(tmp_path):
    """`dev sync code` / `dev sync doc` keep their grammar and behavior."""
    from click.testing import CliRunner

    runner = CliRunner()
    empty = tmp_path / "empty-project"
    config_dir = empty / ".cortext-harness" / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "dev.json").write_text(
        json.dumps({"active": True, "project": {"code": "proj", "name": "proj"}}),
        encoding="utf-8",
    )

    code_result = runner.invoke(
        dev_module.cli, ["sync", "code", "--project-dir", str(empty), "--dry-run"],
        catch_exceptions=False,
    )
    assert code_result.exit_code == 0
    assert "No source folders configured" in code_result.output

    doc_result = runner.invoke(
        dev_module.cli, ["sync", "doc", "--project-dir", str(empty), "--dry-run"],
        catch_exceptions=False,
    )
    assert doc_result.exit_code == 0
    assert "No doc folders configured" in doc_result.output

    assert set(dev_module.sync.commands) == {"code", "doc", "link"}


def test_lifecycle_all_still_code_and_doc():
    """`--server all` keeps selecting exactly code + doc in Phase 1."""
    import importlib.util

    lifecycle_path = REPO_ROOT / "scripts" / "mcp-lifecycle.py"
    spec = importlib.util.spec_from_file_location(
        "mcp_lifecycle_under_test", lifecycle_path
    )
    lifecycle = importlib.util.module_from_spec(spec)
    sys.modules["mcp_lifecycle_under_test"] = lifecycle
    spec.loader.exec_module(lifecycle)

    options = lifecycle.start_options([])
    names = [server["name"] for server in lifecycle.selected_servers(options)]
    assert names == ["code-tiny", "doc-tiny"]

    link_options = lifecycle.start_options(["--server", "link"])
    link_names = [
        server["name"] for server in lifecycle.selected_servers(link_options)
    ]
    assert link_names == ["link-tiny"]
    assert link_names[0].startswith("link")  # startswith contract (RT-4)
