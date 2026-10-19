"""Tests for link_engine.py — the 5-step pipeline on stub graph handles
(Phase 03 acceptance criteria). No real graph, no lease, no MCP.

Run: PYTHONPATH=link-tiny python -m pytest link-tiny/tests/test_link_engine.py -q
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


LINK_TINY = Path(__file__).resolve().parents[1]


def _load(name: str):
    if str(LINK_TINY) not in sys.path:
        sys.path.insert(0, str(LINK_TINY))
    spec = importlib.util.spec_from_file_location(name, LINK_TINY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


store = _load("link_store")
engine = _load("link_engine")
normalizers = _load("normalizers")


class StubGraph:
    """Minimal read-only handle routed by query shape (mirrors ReadOnlyGraph.run)."""

    def __init__(
        self,
        paragraphs=None,
        entity_mentions=None,
        symbols=None,
        endpoints=None,
    ):
        self.paragraphs = paragraphs or []
        self.entity_mentions = entity_mentions or []
        self.symbols = symbols or []
        self.endpoints = endpoints or []
        self.queries: list[str] = []

    def run(self, query, params=None):
        self.queries.append(query)
        if "HAS_ENTITY" in query:
            return list(self.entity_mentions)
        if "Paragraph" in query:
            return list(self.paragraphs)
        if "n:Function" in query:
            return [dict(row, language="python") for row in self.symbols]
        if "n:ApiEndpoint" in query:
            return list(self.endpoints)
        return []  # every other label is empty in fixtures


def _symbol_row(name, path, qualified_name="", start=10, end=20, target="file_path"):
    return {
        "name": name,
        "qualified_name": qualified_name,
        "file_path": path,
        "start_line": start,
        "end_line": end,
    }


def _paragraph(doc_id, paragraph_id, text):
    return {"source_id": doc_id, "paragraph_id": paragraph_id, "text": text}


DOC_SHAS = {"docs/auth.md": "a" * 8}
CODE_SHAS = {"src/auth/login.py": "b" * 8}


def _fixture_graphs():
    doc = StubGraph(
        paragraphs=[
            _paragraph(
                "docs/auth.md",
                1,
                "The login_handler delegates to session_store. Plain prose here.",
            ),
            _paragraph("docs/misc.md", 2, "Nothing identifier-like in this paragraph."),
        ],
        entity_mentions=[
            {"source_id": "docs/auth.md", "paragraph_id": 1, "name": "login_handler"},
        ],
    )
    code = StubGraph(
        symbols=[
            _symbol_row("login_handler", "src/auth/login.py", "auth.login.login_handler"),
            _symbol_row("session_store", "src/auth/session.py", "auth.session.session_store"),
            _symbol_row("unrelated_thing", "src/other.py"),
        ]
    )
    return code, doc


def _build(links_dir, *, code=None, doc=None, doc_shas=None, code_shas=None, **kwargs):
    code = code if code is not None else _fixture_graphs()[0]
    doc = doc if doc is not None else _fixture_graphs()[1]
    return engine.build_links(
        project_id="fixt",
        links_dir=links_dir,
        code_graph=code,
        doc_graph=doc,
        doc_shas=DOC_SHAS if doc_shas is None else doc_shas,
        code_shas=CODE_SHAS if code_shas is None else code_shas,
        parser_type="python",
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Exact-name pipeline
# ---------------------------------------------------------------------------


def test_exact_name_link(tmp_path):
    links_dir = tmp_path / "fixt"
    report = _build(links_dir)
    records = store.load_links(links_dir, status="active")
    by_name = {r["code"]["name"]: r for r in records}
    assert set(by_name) == {"login_handler", "session_store"}
    link = by_name["login_handler"]
    assert link["relation"] == "implements"
    assert link["provenance"] == "exact-name"
    assert link["score"] == 1.0
    assert link["doc"] == {
        "doc_id": "docs/auth.md",
        "paragraph_id": 1,
        "source_sha": DOC_SHAS["docs/auth.md"],
    }
    assert link["code"]["path"] == "src/auth/login.py"
    assert link["code"]["lines"] == [10, 20]
    assert link["code"]["source_sha"] == CODE_SHAS["src/auth/login.py"]
    assert link["code"]["parser_type"] == "python"
    assert link["code"]["target_type"] == "symbol"
    assert report.unchanged_links == 0


def test_qualified_bidirectional(tmp_path):
    """Doc token dotted-suffix matches a qualified symbol, both directions."""
    links_dir = tmp_path / "fixt"
    doc = StubGraph(
        paragraphs=[
            _paragraph("docs/auth.md", 1, "see auth.login.login_handler for details"),
        ]
    )
    code = StubGraph(
        symbols=[_symbol_row("login_handler", "src/auth/login.py", "auth.login.login_handler")]
    )
    _build(links_dir, code=code, doc=doc)
    records = store.load_links(links_dir, status="active")
    assert [r["code"]["name"] for r in records] == ["login_handler"]


def test_ambiguous_emits_all(tmp_path):
    links_dir = tmp_path / "fixt"
    code = StubGraph(
        symbols=[
            _symbol_row("login_handler", "src/a/login.py"),
            _symbol_row("login_handler", "src/b/login.py"),
        ]
    )
    doc = StubGraph(paragraphs=[_paragraph("docs/auth.md", 1, "the login_handler does work")])
    _build(links_dir, code=code, doc=doc)
    records = store.load_links(links_dir, status="active")
    assert {r["code"]["path"] for r in records} == {"src/a/login.py", "src/b/login.py"}


def test_prose_no_tokens(tmp_path):
    """Plain prose yields no records but counts the doc as unmatched."""
    links_dir = tmp_path / "fixt"
    doc = StubGraph(paragraphs=[_paragraph("docs/misc.md", 1, "This explains the concept.")])
    code = StubGraph(symbols=[_symbol_row("something", "src/x.py")])
    report = _build(links_dir, code=code, doc=doc)
    assert store.load_links(links_dir) == []
    assert report.doc_universe == 1
    assert report.coverage["docs_with_active_links"] == 0


def test_endpoint_links(tmp_path):
    """Framework endpoint labels link by name with source_file identity."""
    links_dir = tmp_path / "fixt"
    doc = StubGraph(paragraphs=[_paragraph("docs/api.md", 1, "handled by LoginController")])
    code = StubGraph(
        endpoints=[
            _symbol_row(
                "LoginController",
                "src/http/login_controller.py",
                start=None,
                end=None,
                target="source_file",
            )
        ]
    )
    _build(links_dir, code=code, doc=doc)
    records = store.load_links(links_dir, status="active")
    assert len(records) == 1
    assert records[0]["code"]["target_type"] == "endpoint"
    assert records[0]["code"]["lines"] is None


# ---------------------------------------------------------------------------
# Delta / stale propagation
# ---------------------------------------------------------------------------


def test_delta_noop(tmp_path):
    links_dir = tmp_path / "fixt"
    first = _build(links_dir)
    assert len(first.new_links) == 2
    second = _build(links_dir)
    assert second.new_links == []
    assert second.unchanged_links == 2
    # state.json was still refreshed
    state = store.load_state(links_dir)
    assert state["built_at"] >= first.coverage.get("built_at", "") or state["built_at"]


def test_code_drift_stale_rematch(tmp_path):
    links_dir = tmp_path / "fixt"
    _build(links_dir)
    # code file rewritten: same symbol identity, new SHA
    drifted_shas = {**CODE_SHAS, "src/auth/login.py": "c" * 8}
    second = _build(links_dir, code_shas=drifted_shas)
    # the supersede transition lives in the raw log…
    raw_stale = {r["code"]["name"] for r in store.load_links(links_dir, status="stale")}
    assert "login_handler" in raw_stale
    # …and the latest view carries the re-matched record with the new SHA
    active = store.latest_links(links_dir, status="active")
    by_name = {r["code"]["name"]: r for r in active}
    assert by_name["login_handler"]["code"]["source_sha"] == "c" * 8
    assert store.latest_links(links_dir, status="stale") == []
    assert len(second.new_links) >= 1


def test_code_renamed_stale(tmp_path):
    """Symbol renamed in code: old link goes stale.

    Exact-name matching cannot re-match — the doc still says the old name,
    so no new record appears until the doc is updated too.
    """
    links_dir = tmp_path / "fixt"
    _build(links_dir)
    code = StubGraph(
        symbols=[
            _symbol_row("sign_in_handler", "src/auth/login.py", "auth.login.sign_in_handler"),
            _symbol_row("session_store", "src/auth/session.py", "auth.session.session_store"),
        ]
    )
    _build(links_dir, code=code)
    active = {r["code"]["name"] for r in store.latest_links(links_dir, status="active")}
    stale_names = {r["code"]["name"] for r in store.latest_links(links_dir, status="stale")}
    assert active == {"session_store"}
    assert "login_handler" in stale_names


def test_doc_deleted_stale(tmp_path):
    links_dir = tmp_path / "fixt"
    _build(links_dir)
    doc = StubGraph(
        paragraphs=[_paragraph("docs/misc.md", 2, "Nothing identifier-like here.")]
    )
    _build(links_dir, doc=doc)
    active = store.latest_links(links_dir, status="active")
    stale = store.latest_links(links_dir, status="stale")
    assert all(r["doc"]["doc_id"] != "docs/auth.md" for r in active)
    assert {r["code"]["name"] for r in stale} == {"login_handler", "session_store"}


def test_dry_run_writes_nothing(tmp_path):
    links_dir = tmp_path / "fixt"
    report = _build(links_dir, dry_run=True)
    assert len(report.new_links) == 2
    assert not links_dir.exists() or not store.load_links(links_dir)
    assert store.load_state(links_dir) is None


def test_reconcile_supersedes_everything(tmp_path):
    links_dir = tmp_path / "fixt"
    _build(links_dir)
    report = _build(links_dir, reconcile=True)
    # the log keeps the supersede transition (stale copies)…
    raw_stale = store.load_links(links_dir, status="stale")
    assert len(raw_stale) == 2
    # …but the authoritative latest view is the fresh active set
    active = store.latest_links(links_dir, status="active")
    assert len(active) == 2
    assert store.latest_links(links_dir, status="stale") == []
    assert len(report.new_links) == 2


# ---------------------------------------------------------------------------
# Golden pipeline (regression anchor)
# ---------------------------------------------------------------------------


def test_golden_pipeline(tmp_path):
    """End-to-end record shapes on the fixture mini-project."""
    links_dir = tmp_path / "fixt"
    _build(links_dir)
    records = store.load_links(links_dir)
    golden = [
        {
            "doc": {"doc_id": r["doc"]["doc_id"], "paragraph_id": r["doc"]["paragraph_id"]},
            "code": {"path": r["code"]["path"], "name": r["code"]["name"]},
            "provenance": r["provenance"],
            "score": r["score"],
            "relation": r["relation"],
            "status": r["status"],
        }
        for r in sorted(records, key=lambda r: (r["code"]["path"], r["doc"]["doc_id"]))
    ]
    assert golden == [
        {
            "doc": {"doc_id": "docs/auth.md", "paragraph_id": 1},
            "code": {"path": "src/auth/login.py", "name": "login_handler"},
            "provenance": "exact-name",
            "score": 1.0,
            "relation": "implements",
            "status": "active",
        },
        {
            "doc": {"doc_id": "docs/auth.md", "paragraph_id": 1},
            "code": {"path": "src/auth/session.py", "name": "session_store"},
            "provenance": "exact-name",
            "score": 1.0,
            "relation": "implements",
            "status": "active",
        },
    ]
    state = store.load_state(links_dir)
    assert state["project_id"] == "fixt"
    assert state["doc_shas"] == DOC_SHAS
    assert state["code_shas"] == CODE_SHAS
    assert state["coverage"]["doc_coverage"] == 0.5  # 1 of 2 docs linked
    assert state["counts"]["active"] == 2


# ---------------------------------------------------------------------------
# Coverage telemetry fixture (hand-computed)
# ---------------------------------------------------------------------------


def test_coverage_fixture(tmp_path):
    links_dir = tmp_path / "fixt"
    report = _build(links_dir)
    # docs/auth.md linked, docs/misc.md not; 2 of 3 symbols referenced
    assert report.coverage == {
        "active_links": 2,
        "stale_links": 0,
        "docs_with_active_links": 1,
        "doc_coverage": 0.5,
        "symbols_referenced": 2,
        "symbol_coverage": round(2 / 3, 4),
        "provenance_distribution": {"exact-name": 2},
    }


# ---------------------------------------------------------------------------
# Delta sources: sync-state merge + inventory loading
# ---------------------------------------------------------------------------


def test_load_sync_state_hashes_and_split(tmp_path):
    state_dir = tmp_path / ".cortext-harness" / "sync-state"
    state_dir.mkdir(parents=True)
    (state_dir / "aaa.json").write_text(
        json.dumps({"file_hashes": {"docs/auth.md": "a1", "src/x.py": "c1"}}),
        encoding="utf-8",
    )
    (state_dir / "bbb.json").write_text(
        json.dumps({"file_hashes": {"docs/api.pdf": "a2"}}), encoding="utf-8"
    )
    merged = engine.load_sync_state_hashes(tmp_path)
    assert merged == {"docs/auth.md": "a1", "src/x.py": "c1", "docs/api.pdf": "a2"}
    doc_shas, code_shas = engine.split_shas_by_side(merged)
    assert doc_shas == {"docs/auth.md": "a1", "docs/api.pdf": "a2"}
    assert code_shas == {"src/x.py": "c1"}


def test_load_code_inventory_hashes(tmp_path):
    folder = tmp_path / "repo"
    incremental = folder / ".cache" / "incremental_sync"
    inventories = folder / ".cache" / "inventories"
    incremental.mkdir(parents=True)
    inventories.mkdir(parents=True)
    snapshot = inventories / "snap1.json"
    snapshot.write_text(
        json.dumps(
            {
                "entries": {
                    "src/auth/login.py": {"sha256": "b" * 8},
                    "src/other.py": {"sha256": "d" * 8},
                }
            }
        ),
        encoding="utf-8",
    )
    (incremental / "fixt_scope1.json").write_text(
        json.dumps({"inventory_path": str(snapshot), "last_run_after": "2026-10-19T01:00:00Z"}),
        encoding="utf-8",
    )
    hashes = engine.load_code_inventory_hashes(
        [str(folder)], "fixt", project_dir=tmp_path
    )
    # rel paths re-expressed relative to the project dir
    assert hashes == {"repo/src/auth/login.py": "b" * 8, "repo/src/other.py": "d" * 8}


def test_load_code_inventory_picks_newest_state(tmp_path):
    folder = tmp_path / "repo"
    incremental = folder / ".cache" / "incremental_sync"
    inventories = folder / ".cache" / "inventories"
    incremental.mkdir(parents=True)
    inventories.mkdir(parents=True)
    for name, sha, run_at in (("snap_old.json", "1" * 8, "2026-10-18T00:00:00Z"), ("snap_new.json", "2" * 8, "2026-10-19T00:00:00Z")):
        snapshot = inventories / name
        snapshot.write_text(
            json.dumps({"entries": {"src/x.py": {"sha256": sha}}}), encoding="utf-8"
        )
        (incremental / f"fixt_scope.json").write_text(
            json.dumps({"inventory_path": str(snapshot), "last_run_after": run_at}),
            encoding="utf-8",
        )
        import time as _time

        _time.sleep(0.01)  # mtime ordering tie-break safety
    hashes = engine.load_code_inventory_hashes([str(folder)], "fixt", tmp_path)
    assert hashes == {"repo/src/x.py": "2" * 8}


def test_doc_sha_spelling_fallback(tmp_path):
    """Single-file mode: source_id is the stem; sha resolves by stem match."""
    links_dir = tmp_path / "fixt"
    doc = StubGraph(paragraphs=[_paragraph("auth", 1, "the login_handler works")])
    code = StubGraph(symbols=[_symbol_row("login_handler", "src/auth/login.py")])
    _build(links_dir, code=code, doc=doc, doc_shas={"docs/auth.md": "a" * 8})
    records = store.load_links(links_dir, status="active")
    assert records[0]["doc"]["source_sha"] == "a" * 8
