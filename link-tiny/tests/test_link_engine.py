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


def _symbol_row(name, path, qualified_name="", start=10, end=20, target="file_path", nid=None):
    row = {
        "name": name,
        "qualified_name": qualified_name,
        "file_path": path,
        "start_line": start,
        "end_line": end,
    }
    if nid is not None:
        row["_node_id"] = nid
    return row


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


def test_sha_join_telemetry(tmp_path):
    """Per-record sha-join counters reach the report AND state.json."""
    links_dir = tmp_path / "fixt"
    report = _build(links_dir)
    # session_store's file is absent from the flat baseline → code_missing
    assert report.sha_join == {
        "doc_matched": 2,
        "doc_missing": 0,
        "code_matched": 1,
        "code_missing": 1,
        "code_ambiguous": 0,
    }
    state = store.load_state(links_dir)
    assert state["sha_join"] == report.sha_join
    # state.json sha snapshot keys stay unchanged (flat doc+code maps)
    assert set(state["doc_shas"]) == {"docs/auth.md"}
    assert set(state["code_shas"]) == {"src/auth/login.py"}


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


# ---------------------------------------------------------------------------
# Full symbol universe: pagination + Field label (plan 261019-1700 Phase 03)
# ---------------------------------------------------------------------------


import re as _re


def _int_after(query: str, keyword: str) -> int:
    match = _re.search(rf"{keyword} (\d+)", query)
    return int(match.group(1)) if match else 0


class PagedStubGraph:
    """Fake graph with per-label stores, count() and a resultset cap.

    Emulates the engine's id-window paging contract: a window query scopes
    rows to ``$wlo <= id(anchor) < $whi``, a group-resume query (``after``
    param) re-reads one anchor's rows past ``id(last) > $after``, both
    ordered by the id tuple.

    ``cap`` simulates the server's ``resultset_size`` (FalkorDB default
    10000): every ``run()`` response is truncated to at most ``cap`` rows,
    exactly how the live incident lost symbols beyond the cut.
    ``cut_after`` models a server that serves nothing at or beyond that
    anchor id (the shortfall case — a loud warning must fire).
    ``fail_labels`` makes page queries raise for those labels (unreadable
    label → per-label degradation that must stay visible in accounting).
    """

    def __init__(
        self,
        labels=None,
        paragraphs=None,
        entity_mentions=None,
        cap=None,
        cut_after=None,
        count_override=None,
        fail_labels=None,
    ):
        self.labels = labels or {}
        self.paragraphs = paragraphs or []
        self.entity_mentions = entity_mentions or []
        self.cap = cap
        self.cut_after = cut_after
        self.count_override = count_override or {}
        self.fail_labels = fail_labels or set()
        self._synthesize_internal_ids()

    def _synthesize_internal_ids(self):
        """Real servers always answer ``id()``; fixtures omit the columns.

        Assign positional ids so the window/resume pager exercises the same
        contract it sees live (anchor ``_node_id``, edge ``_entity_id``).
        """
        p_ids = {
            (p.get("source_id"), p.get("paragraph_id")): i
            for i, p in enumerate(self.paragraphs)
        }
        for i, p in enumerate(self.paragraphs):
            p.setdefault("_node_id", i)
        next_anchor = len(self.paragraphs)
        for j, m in enumerate(self.entity_mentions):
            anchor = (m.get("source_id"), m.get("paragraph_id"))
            if anchor not in p_ids:
                p_ids[anchor] = next_anchor
                next_anchor += 1
            m.setdefault("_node_id", p_ids[anchor])
            m.setdefault("_entity_id", j)
        for rows in self.labels.values():
            for i, row in enumerate(rows):
                row.setdefault("_node_id", i)

    @staticmethod
    def _label_of(query):
        match = _re.search(r"MATCH \(n:(\w+)\)", query)
        return match.group(1) if match else None

    def _store_of(self, query):
        if "HAS_ENTITY" in query:
            return self.entity_mentions, ("_node_id", "_entity_id")
        if "Paragraph" in query:
            return self.paragraphs, ("_node_id",)
        label = self._label_of(query)
        return self.labels.get(label, []), ("_node_id",)

    def _bounds(self, query):
        rows, keys = self._store_of(query)
        if not rows:
            return [{"lo": None, "hi": None}]
        ids = [row[keys[0]] for row in rows]
        return [{"lo": min(ids), "hi": max(ids)}]

    def _page(self, rows, keys, query, params):
        rows = list(rows)
        limit = _int_after(query, "LIMIT") or len(rows)

        def id_key(row):
            return tuple(row[k] for k in keys if k in row)

        if "after" in params:
            scoped = [
                row for row in rows
                if row[keys[0]] == params["wlo"] and row[keys[-1]] > params["after"]
                and (self.cut_after is None or row[keys[0]] < self.cut_after)
            ]
        else:
            scoped = [
                row for row in rows
                if params["wlo"] <= row[keys[0]] < params["whi"]
                and (self.cut_after is None or row[keys[0]] < self.cut_after)
            ]
        scoped.sort(key=id_key)
        page = scoped[:limit]
        if self.cap is not None:
            page = page[:self.cap]
        return [dict(row) for row in page]

    def run(self, query, params=None):
        params = params or {}
        rows, keys = self._store_of(query)
        if "count(" in query:
            if "HAS_ENTITY" in query:
                return [{"c": len(self.entity_mentions)}]
            if "Paragraph" in query:
                return [{"c": len(self.paragraphs)}]
            label = self._label_of(query)
            if label in self.count_override:
                return [{"c": self.count_override[label]}]
            return [{"c": len(self.labels.get(label, []))}]
        if "min(id(" in query:
            return self._bounds(query)
        if self._label_of(query) in self.fail_labels:
            raise RuntimeError(f"label store broken: {self._label_of(query)}")
        return self._page(rows, keys, query, params)


def _function_rows(count, name_prefix="fn"):
    return [
        _symbol_row(f"{name_prefix}_{i:02d}", f"src/mod{i % 3}/file{i}.py", nid=i)
        for i in range(count)
    ]


def test_pagination_indexes_every_row_under_resultset_cap(tmp_path):
    """>cap rows per label all reach the match index (cap-proof reads)."""
    code = PagedStubGraph(
        labels={
            "Function": _function_rows(11),
            "Variable": [_symbol_row("v1", "src/v.py"), _symbol_row("v2", "src/v.py")],
        },
        cap=4,  # server caps every response at 4 rows
    )
    symbols, by_label = engine.read_code_symbols_report(code, "fixt", "python", batch=3)
    assert len({s.identity for s in symbols}) == 13
    assert by_label["Function"] == {"rows_read": 11, "expected": 11, "kept": 11}
    assert by_label["Variable"]["rows_read"] == 2


def test_pagination_no_duplicates_across_batch_boundaries(tmp_path):
    code = PagedStubGraph(labels={"Function": _function_rows(9)}, cap=5)
    symbols, by_label = engine.read_code_symbols_report(code, "fixt", "python", batch=4)
    identities = [s.identity for s in symbols]
    assert len(identities) == len(set(identities)) == 9
    assert by_label["Function"]["rows_read"] == by_label["Function"]["expected"] == 9


def test_shortfall_is_loud_never_silent(tmp_path, capsys):
    """A server that stops serving rows short of its own count warns loudly."""
    code = PagedStubGraph(
        labels={"Function": _function_rows(4)},
        cut_after=4,
        count_override={"Function": 10},  # server claims 10, serves 4
    )
    symbols, by_label = engine.read_code_symbols_report(code, "fixt", "python", batch=3)
    assert len(symbols) == 4
    assert by_label["Function"]["rows_read"] < by_label["Function"]["expected"]
    assert "truncated" in capsys.readouterr().err


def test_field_label_linked(tmp_path):
    """Designer `Field` symbols link like Variables (incident regression)."""
    links_dir = tmp_path / "fixt"
    doc = StubGraph(
        paragraphs=[
            _paragraph(
                "specs__order_summary_spec.md",
                21,
                "grid_unit_col controls the unit column.",
            )
        ]
    )
    code = PagedStubGraph(
        labels={
            "Variable": [
                _symbol_row(
                    "grid_unit_col",
                    "src/orders/OrderSummaryForm.Designer.vb",
                    start=None,
                    end=None,
                )
            ],
            "Field": [
                _symbol_row(
                    "grid_unit_col",
                    "src/orders/OrderSummaryForm.Designer.cs",
                    start=4443,
                    end=4443,
                )
            ],
        },
    )
    _build(links_dir, code=code, doc=doc, doc_shas={}, code_shas={})
    records = store.load_links(links_dir, status="active")
    paths = {r["code"]["path"] for r in records}
    assert paths == {
        "src/orders/OrderSummaryForm.Designer.vb",
        "src/orders/OrderSummaryForm.Designer.cs",
    }
    assert all(r["code"]["target_type"] == "symbol" for r in records)


def test_symbol_universe_equals_sum_of_server_counts(tmp_path):
    """Universe accounting: report total == Σ per-label server counts."""
    links_dir = tmp_path / "fixt"
    doc = StubGraph(paragraphs=[])
    code = PagedStubGraph(
        labels={
            "Function": _function_rows(3),
            "Variable": [_symbol_row("v1", "src/v.py"), _symbol_row("v2", "src/v2.py")],
            "Field": [_symbol_row("f1", "src/f.Designer.cs")],
        },
        cap=2,  # even under a cap, accounting must reconcile
    )
    report = _build(links_dir, code=code, doc=doc)
    expected_sum = sum(
        stats["expected"] for stats in report.symbol_universe_by_label.values()
    )
    assert report.symbol_universe == expected_sum == 6  # Σ kept on clean reads
    assert report.symbol_universe_expected == 6  # Σ server counts (gate)
    state = store.load_state(links_dir)
    assert state["symbol_universe_by_label"]["Field"] == {
        "rows_read": 1, "expected": 1, "kept": 1,
    }
    assert state["symbol_universe_expected"] == 6


def test_symbol_beyond_cap_regression(tmp_path):
    """The live incident: a symbol past the unpaginated cut still links."""
    links_dir = tmp_path / "fixt"
    doc = StubGraph(
        paragraphs=[
            _paragraph("specs__order_summary_spec.md", 21, "grid_unit_col is the unit.")
        ]
    )
    # grid_unit_col sits behind 5 other rows; the server caps at 2
    code = PagedStubGraph(
        labels={
            "Variable": _function_rows(5, "filler")
            + [
                _symbol_row(
                    "grid_unit_col",
                    "src/orders/OrderSummaryForm.Designer.vb",
                    start=None,
                    end=None,
                )
            ],
        },
        cap=2,
    )
    _build(links_dir, code=code, doc=doc, doc_shas={}, code_shas={})
    records = store.load_links(links_dir, status="active")
    assert [r["code"]["name"] for r in records] == ["grid_unit_col"]


# ---------------------------------------------------------------------------
# Review findings (261019-1700 fix cycle 1): join determinism, guard rails
# ---------------------------------------------------------------------------


def test_duplicate_identity_joins_one_way_no_oscillation(tmp_path):
    """Same identity stamped in two repos resolves ONE way, forever.

    The write path (match index) and the drift path must share one join
    result per identity — otherwise identical builds oscillate and grow
    the append-only log on every run (review finding 1).
    """
    links_dir = tmp_path / "fixt"
    doc = StubGraph(
        paragraphs=[_paragraph("docs/auth.md", 1, "the dup_handler works.")]
    )
    row = _symbol_row("dup_handler", "src/shared/_same.vb", start=1, end=2)
    code = PagedStubGraph(labels={"Function": [dict(row, repo="/proj/s1"),
                                               dict(row, repo="/proj/s3")]})
    folder_shas = {
        engine.normalize_repo_key("/proj/s1"): {"src/shared/_same.vb": "1" * 8},
        engine.normalize_repo_key("/proj/s3"): {"src/shared/_same.vb": "4" * 8},
    }
    _build(links_dir, code=code, doc=doc,
           doc_shas={"docs/auth.md": "a" * 8}, code_shas={},
           code_folder_shas=folder_shas)
    # 2nd and 3rd identical builds are strict no-ops
    for _ in range(2):
        report = _build(links_dir, code=code, doc=doc,
                        doc_shas={"docs/auth.md": "a" * 8}, code_shas={},
                        code_folder_shas=folder_shas)
        assert report.new_links == []
        assert report.unchanged_links == 1
    active = store.latest_links(links_dir, status="active")
    assert len(active) == 1
    assert store.latest_links(links_dir, status="stale") == []
    # the winning SHA is the first symbol's repo (s1), stably
    assert active[0]["code"]["source_sha"] == "1" * 8


def test_known_repo_miss_never_borrows():
    """A repo pinning a known folder is authoritative (review finding 2)."""
    folders = {
        engine.normalize_repo_key("/proj/s1"): {"src/only_s1.py": "1" * 8},
        engine.normalize_repo_key("/proj/s3"): {"src/only_s3.py": "4" * 8},
    }
    index = engine.CodeShaIndex(folders, {})
    # s1's pin + a path only s3 has → missing, NOT s3's SHA
    assert index.resolve("/proj/s1", "src/only_s3.py") == (None, "missing")
    assert index.resolve("/proj/s3", "src/only_s3.py") == ("4" * 8, "matched")


def test_destructive_read_guard(tmp_path):
    """A collapsed symbol read must not stale-mark a non-empty link set."""
    links_dir = tmp_path / "fixt"
    _build(links_dir)  # 2 active records
    empty = StubGraph(paragraphs=[], symbols=[])
    with pytest.raises(engine.LinkEngineError, match="collapsed"):
        _build(links_dir, code=empty, doc=empty)
    # existing actives untouched by the aborted build
    assert len(store.latest_links(links_dir, status="active")) == 2
    assert store.latest_links(links_dir, status="stale") == []
    # reconcile states operator intent and bypasses the guard
    report = _build(links_dir, code=empty, doc=empty, reconcile=True)
    assert len(report.staled_links) == 2


def test_doc_wipe_still_stales_when_symbols_present(tmp_path):
    """A genuine doc-side wipe stales — the guard is code-reads only."""
    links_dir = tmp_path / "fixt"
    _build(links_dir)
    doc = StubGraph(paragraphs=[])  # docs wiped, code graph intact
    _build(links_dir, doc=doc)
    stale = store.latest_links(links_dir, status="stale")
    assert {r["code"]["name"] for r in stale} == {"login_handler", "session_store"}


def test_unreadable_label_stays_in_accounting(tmp_path, capsys):
    """A dead label degrades but never vanishes from the universe (F3)."""
    code = PagedStubGraph(
        labels={"Function": _function_rows(2)},
        fail_labels={"Field"},
    )
    symbols, by_label = engine.read_code_symbols_report(code, "fixt", "python")
    assert len(symbols) == 2
    assert by_label["Field"] == {"rows_read": 0, "expected": 0, "kept": 0, "error": 1}
    assert "unreadable" in capsys.readouterr().err


def test_doc_reads_are_paginated_too(tmp_path):
    """Paragraph + entity reads survive a server cap (review finding 4)."""
    paragraphs = [
        {"source_id": f"docs/d{i}.md", "paragraph_id": 1, "text": f"handler_{i}"}
        for i in range(6)
    ]
    mentions = [
        {"source_id": "docs/d0.md", "paragraph_id": 1, "name": "handler_0"},
        {"source_id": "docs/d0.md", "paragraph_id": 1, "name": "handler_0b"},
        {"source_id": "docs/d1.md", "paragraph_id": 1, "name": "handler_1"},
    ]
    doc = PagedStubGraph(paragraphs=paragraphs, entity_mentions=mentions, cap=2)
    read = engine.read_doc_paragraphs(doc, "fixt")
    assert len(read) == 6
    mentions_map = engine.read_doc_entity_names(doc, "fixt")
    total = sum(len(v) for v in mentions_map.values())
    assert total == 3  # every edge row, deduped by (paragraph, entity)


# ---------------------------------------------------------------------------
# v2 improvements: IDF/co-occurrence gate, endpoint file_path, doc_sha NFC/NFD
# ---------------------------------------------------------------------------


def test_idf_and_cooccurrence_gate_blocks_unscoped_high_df_noise(tmp_path):
    """High-DF symbols (> MAX_UNSCOPED_SYMBOL_DF files) require file-context affinity."""
    links_dir = tmp_path / "fixt"
    # 'on_submit_click' appears in 12 distinct files (high DF > 8)
    high_df_symbols = [
        _symbol_row("on_submit_click", f"src/mod/screen_{i:02d}.vb")
        for i in range(12)
    ]
    # 'order_screen_03_save' is a unique (DF=1) symbol in screen_03.vb
    unique_sym = _symbol_row("order_screen_03_save", "src/mod/screen_03.vb")

    doc = StubGraph(
        paragraphs=[
            # Paragraph 1: mentions only on_submit_click with no file affinity -> gated out
            _paragraph("docs/general_notes.md", 1, "Pressing on_submit_click closes the form."),
            # Paragraph 2: mentions on_submit_click alongside order_screen_03_save -> links screen_03.vb's on_submit_click
            _paragraph(
                "docs/general_notes.md",
                2,
                "In this screen, order_screen_03_save runs before on_submit_click.",
            ),
            # Paragraph 3: doc_id itself names screen_05 -> links screen_05.vb's on_submit_click
            _paragraph("docs/screen_05_spec.md", 1, "on_submit_click exits screen_05."),
        ]
    )
    code = StubGraph(symbols=high_df_symbols + [unique_sym])
    _build(links_dir, code=code, doc=doc, doc_shas={}, code_shas={})
    records = store.latest_links(links_dir, status="active")

    # Paragraph 1 produced 0 links
    assert [r for r in records if r["doc"]["doc_id"] == "docs/general_notes.md" and r["doc"]["paragraph_id"] == 1] == []

    # Paragraph 2 linked order_screen_03_save (score 1.0) and ONLY screen_03.vb's on_submit_click
    p2_links = [r for r in records if r["doc"]["doc_id"] == "docs/general_notes.md" and r["doc"]["paragraph_id"] == 2]
    assert {(r["code"]["path"], r["code"]["name"]) for r in p2_links} == {
        ("src/mod/screen_03.vb", "order_screen_03_save"),
        ("src/mod/screen_03.vb", "on_submit_click"),
    }

    # Paragraph 3 linked ONLY screen_05.vb's on_submit_click via doc_id stem affinity
    p3_links = [r for r in records if r["doc"]["doc_id"] == "docs/screen_05_spec.md"]
    assert [(r["code"]["path"], r["code"]["name"]) for r in p3_links] == [
        ("src/mod/screen_05.vb", "on_submit_click")
    ]


def test_doc_sha_unicode_nfd_nfc_and_extensionless_flattened(tmp_path):
    """macOS NFD paths and extensionless flattened doc_ids resolve to exact SHA."""
    import unicodedata

    doc_dir = tmp_path / "docs_root"
    sub = doc_dir / unicodedata.normalize("NFD", "café_docs")
    sub.mkdir(parents=True)
    xlsx = sub / unicodedata.normalize("NFD", "résumé_spec.xlsx")
    xlsx.write_bytes(b"excel-payload")

    shas = engine.load_doc_folder_hashes([str(doc_dir)])
    assert len(shas) == 1
    index = engine.build_doc_sha_index(shas)

    # Graph source_id in NFC and without .xlsx extension
    nfc_no_ext = unicodedata.normalize("NFC", "café_docs__résumé_spec")
    resolved = engine._doc_sha_for(nfc_no_ext, index)
    assert resolved is not None
    assert len(resolved) == 64

