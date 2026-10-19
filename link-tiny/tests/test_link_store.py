"""Tests for link_store.py — append-only semantics, stale propagation,
state snapshot, coverage telemetry (Phase 02 acceptance criteria).

Run: PYTHONPATH=link-tiny python -m pytest link-tiny/tests/test_link_store.py -q
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from pathlib import Path

import pytest


LINK_TINY = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, LINK_TINY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


store = _load("link_store")


def _record(**overrides):
    payload = {
        "project_id": "fixt",
        "relation": "implements",
        "doc": {"doc_id": "SPEC-auth.md", "paragraph_id": 12, "source_sha": "a" * 8},
        "code": {
            "target_type": "symbol",
            "parser_type": "python",
            "path": "src/auth/login.py",
            "name": "login_handler",
            "lines": [88, 140],
            "source_sha": "b" * 8,
        },
        "score": 1.0,
        "provenance": "exact-name",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def project_dir(tmp_path):
    return tmp_path / "links" / "fixt"


# ---------------------------------------------------------------------------
# Round-trip + validation
# ---------------------------------------------------------------------------


def test_append_load_roundtrip(project_dir):
    written = store.append_links(project_dir, [_record(), _record(doc={"doc_id": "D2", "paragraph_id": 1})])
    assert written == 2
    records = store.load_links(project_dir)
    assert len(records) == 2
    first = records[0]
    assert first["project_id"] == "fixt"
    assert first["relation"] == "implements"
    assert first["doc"]["doc_id"] == "SPEC-auth.md"
    assert first["code"]["path"] == "src/auth/login.py"
    assert first["status"] == "active"
    assert first["score"] == 1.0
    assert first["provenance"] == "exact-name"
    assert first["created_at"].endswith("Z")
    assert first["schema_version"] == 1


def test_reject_missing_provenance(project_dir):
    bad = _record()
    del bad["provenance"]
    with pytest.raises(ValueError, match="provenance"):
        store.append_links(project_dir, [bad])
    # nothing was written
    assert store.load_links(project_dir) == []


def test_reject_missing_score(project_dir):
    bad = _record(score=None)
    with pytest.raises(ValueError, match="score"):
        store.append_links(project_dir, [bad])


def test_reject_non_numeric_score(project_dir):
    with pytest.raises(ValueError, match="numeric"):
        store.append_links(project_dir, [_record(score="high")])


def test_posix_path_normalization_at_store_boundary(project_dir):
    store.append_links(project_dir, [_record(code={"path": "src\\auth\\login.py", "name": "x"})])
    record = store.load_links(project_dir)[0]
    assert record["code"]["path"] == "src/auth/login.py"


def test_load_links_status_filter(project_dir):
    store.append_links(project_dir, [_record()])
    store.mark_stale(project_dir, lambda record: True)
    # append-only: the original active line remains, plus a stale copy
    assert len(store.load_links(project_dir)) == 2
    active = store.load_links(project_dir, status="active")
    stale = store.load_links(project_dir, status="stale")
    assert len(active) == 1
    assert len(stale) == 1
    assert stale[0]["code"]["path"] == "src/auth/login.py"
    assert stale[0]["staled_at"].endswith("Z")


# ---------------------------------------------------------------------------
# Append-only invariant: stale appends, never rewrites
# ---------------------------------------------------------------------------


def test_stale_appends_not_rewrites(project_dir):
    store.append_links(project_dir, [_record(), _record(doc={"doc_id": "D2", "paragraph_id": 1})])
    before = (project_dir / "links.jsonl").read_text(encoding="utf-8")

    copies = store.mark_stale(
        project_dir,
        lambda record: record["doc"]["doc_id"] == "SPEC-auth.md",
    )
    assert len(copies) == 1
    after = (project_dir / "links.jsonl").read_text(encoding="utf-8")
    # original lines untouched; a stale copy was appended
    assert after.startswith(before)
    lines = after.strip().splitlines()
    assert len(lines) == 3
    assert json.loads(lines[-1])["status"] == "stale"
    assert json.loads(lines[0])["status"] == "active"


def test_mark_stale_idempotent(project_dir):
    store.append_links(project_dir, [_record()])
    first = store.mark_stale(project_dir, lambda record: True)
    second = store.mark_stale(project_dir, lambda record: True)
    assert len(first) == 1
    assert second == []  # already stale — no duplicate append
    assert len(store.load_links(project_dir)) == 2


# ---------------------------------------------------------------------------
# Crash tolerance: truncated tail
# ---------------------------------------------------------------------------


def test_tolerate_truncated_tail(project_dir, capsys):
    store.append_links(project_dir, [_record(), _record(doc={"doc_id": "D2", "paragraph_id": 1})])
    path = project_dir / "links.jsonl"
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    # simulate a crash mid-write: second line cut in half
    path.write_text(lines[0] + "\n" + lines[1][:40], encoding="utf-8")

    records = store.load_links(project_dir)
    assert len(records) == 1  # valid prefix readable
    assert "skipping undecodable line" in capsys.readouterr().err
    # and the store still appends fine after the torn tail
    store.append_links(project_dir, [_record(doc={"doc_id": "D3", "paragraph_id": 2})])
    assert len(store.load_links(project_dir)) == 2


def test_load_links_missing_dir(project_dir):
    assert store.load_links(project_dir) == []
    assert store.load_state(project_dir) is None


# ---------------------------------------------------------------------------
# state.json round-trip
# ---------------------------------------------------------------------------


def test_state_roundtrip(project_dir):
    state = {
        "built_at": "2026-10-19T00:00:00Z",
        "project_id": "fixt",
        "doc_shas": {"SPEC-auth.md": "a" * 8},
        "code_shas": {"src/auth/login.py": "b" * 8},
        "counts": {"active": 1, "stale": 0, "total": 1},
        "coverage": {"doc_coverage": 0.5},
    }
    store.save_state(project_dir, state)
    loaded = store.load_state(project_dir)
    assert loaded["schema_version"] == 1
    assert loaded["doc_shas"] == {"SPEC-auth.md": "a" * 8}
    assert loaded["code_shas"] == {"src/auth/login.py": "b" * 8}
    assert loaded["built_at"] == "2026-10-19T00:00:00Z"
    # atomic write leaves no tmp file behind
    assert not (project_dir / "state.json.tmp").exists()


# ---------------------------------------------------------------------------
# Coverage telemetry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "records,total_docs,total_symbols,expect",
    [
        # empty
        ([], 0, 0, {"active": 0, "docs": 0, "doc_cov": None, "symbols": 0}),
        # partial: two docs reference the same symbol → 1 symbol
        (
            [_record(), _record(doc={"doc_id": "D2", "paragraph_id": 3})],
            4,
            10,
            {"active": 2, "docs": 2, "doc_cov": 0.5, "symbols": 1},
        ),
        # full: same symbol referenced from two docs collapses to 1 symbol
        (
            [
                _record(),
                _record(doc={"doc_id": "D2", "paragraph_id": 3}),
            ],
            2,
            1,
            {"active": 2, "docs": 2, "doc_cov": 1.0, "symbols": 1},
        ),
    ],
)
def test_coverage_stats(records, total_docs, total_symbols, expect):
    stats = store.coverage_stats(
        records, total_docs=total_docs, total_symbols=total_symbols
    )
    assert stats["active_links"] == expect["active"]
    assert stats["docs_with_active_links"] == expect["docs"]
    assert stats["doc_coverage"] == expect["doc_cov"]
    assert stats["symbols_referenced"] == expect["symbols"]
    expected_distribution = (
        {"exact-name": expect["active"]} if expect["active"] else {}
    )
    assert stats["provenance_distribution"] == expected_distribution


def test_coverage_stats_counts_stale_separately(project_dir):
    store.append_links(project_dir, [_record()])
    store.mark_stale(project_dir, lambda record: True)
    records = store.load_links(project_dir)
    # the original line stays active; the stale copy was appended
    stats = store.coverage_stats(records, total_docs=1, total_symbols=1)
    assert stats["active_links"] == 1
    assert stats["stale_links"] == 1
    assert stats["docs_with_active_links"] == 1
    assert stats["doc_coverage"] == 1.0


# ---------------------------------------------------------------------------
# Concurrent appends serialize
# ---------------------------------------------------------------------------


def test_append_lock(project_dir):
    errors: list[Exception] = []

    def _append(tag: str, count: int) -> None:
        try:
            store.append_links(
                project_dir,
                [_record(doc={"doc_id": f"{tag}-{i}", "paragraph_id": i}) for i in range(count)],
            )
        except Exception as exc:  # pragma: no cover - surfaced via errors
            errors.append(exc)

    threads = [
        threading.Thread(target=_append, args=(f"t{n}", 25)) for n in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []

    records = store.load_links(project_dir)
    assert len(records) == 100  # every line intact, none interleaved
    doc_ids = {r["doc"]["doc_id"] for r in records}
    assert len(doc_ids) == 100
