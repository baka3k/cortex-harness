"""SHA-join contract tests (plan 261019-1700 Phase 01, TDD red).

These encode the LINK_CONTRACT.md "SHA join" section as executable
fixtures copied from live deployment shapes (not invented):

* doc side — graph ``source_id`` is the ``__``-flattened rel path
  (dev.py ``_safe_source_id``), the sync-state baseline holds POSIX rel
  paths; single-file mode uses the stem;
* code side — graph symbols carry ``repo`` (the absolute scan-folder
  path, null on framework endpoint labels) and folder-relative
  ``file_path``; inventories hold folder-relative keys per scan folder.

Run: PYTHONPATH=link-tiny python -m pytest link-tiny/tests/test_sha_join.py -q
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


def norm_repo(value: str) -> str:
    """The engine's repo-key rule (resolved absolute POSIX, casefolded)."""
    return engine.normalize_repo_key(value)


def folder_key(value: str) -> str:
    """Inventory folder keys keep original case (casefold is lookup-only)."""
    return str(Path(value).expanduser().resolve())


# ---------------------------------------------------------------------------
# Doc side: dev.py's flatten rule + the lookup order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel, expected",
    [
        ("specs/module_a/batch_job_01.xlsx",
         "specs__module_a__batch_job_01.xlsx"),  # folder mode, forward slash
        ("docs\\auth.md", "docs__auth.md"),  # Windows separator
        ("mixed/dir\\file.md", "mixed__dir__file.md"),
        ("auth.md", "auth.md"),  # no separator → unchanged
    ],
)
def test_flatten_doc_rel_matches_dev_rule(rel, expected):
    assert engine.flatten_doc_rel(rel) == expected


class TestDocShaLookup:
    """`_doc_sha_for` order: direct rel → flattened → stem."""

    DOC_KEY = "specs/module_a(v1)/batch_job_01_data_flow.xlsx"
    FLAT_ID = ("specs__module_a(v1)__"
               "batch_job_01_data_flow.xlsx")

    def _index(self, doc_shas):
        return engine.build_doc_sha_index(doc_shas)

    def test_flattened_folder_mode(self):
        # live deployment shape: graph source_id is the flattened rel path
        index = self._index({self.DOC_KEY: "d" * 8})
        assert engine._doc_sha_for(self.FLAT_ID, index) == "d" * 8

    def test_direct_posix_rel(self):
        # folder-mode docs whose source_id is the plain rel path
        index = self._index({"docs/auth.md": "a" * 8})
        assert engine._doc_sha_for("docs/auth.md", index) == "a" * 8

    def test_stem_single_file_mode(self):
        index = self._index({"docs/auth.md": "a" * 8})
        assert engine._doc_sha_for("auth", index) == "a" * 8

    def test_literal_double_underscore_never_unflattened(self):
        # adversarial: the file name itself contains '__' — the direct
        # baseline entry must win and the id must never be un-flattened
        index = self._index({"docs/a__b.md": "x" * 8, "docs/a/b.md": "y" * 8})
        assert engine._doc_sha_for("docs/a__b.md", index) == "x" * 8
        # both spellings collapse to the same flattened id with different
        # SHAs — genuine ambiguity resolves to nothing, never a guess
        assert engine._doc_sha_for("docs__a__b.md", index) is None
        # same flat id with the SAME sha is unambiguous
        same = self._index({"docs/a__b.md": "x" * 8, "docs/a/b.md": "x" * 8})
        assert engine._doc_sha_for("docs__a__b.md", same) == "x" * 8

    def test_ambiguous_stem_returns_none(self):
        # two files sharing a stem with DIFFERENT shas: no guessing
        index = self._index({"docs/auth.md": "a" * 8, "sub/auth.md": "b" * 8})
        assert engine._doc_sha_for("auth", index) is None
        # non-stem lookups stay exact
        assert engine._doc_sha_for("docs/auth.md", index) == "a" * 8

    def test_agreeing_stem_set_resolves(self):
        # same content under one stem is unambiguous (review finding 6)
        index = self._index({"docs/auth.md": "a" * 8, "sub/auth.md": "a" * 8})
        assert engine._doc_sha_for("auth", index) == "a" * 8

    def test_flattened_ambiguity_stays_after_later_same_sha_key(self):
        # A:sha1 → B:sha2 marks ambiguity; a later C:sha1 must NOT revive it
        index = self._index(
            {"docs/a__b.md": "x" * 8, "docs/a/b.md": "y" * 8, "docs/c/d.md": "x" * 8}
        )
        assert engine._doc_sha_for("docs__a__b.md", index) is None

    def test_unknown_id_is_none(self):
        index = self._index({"docs/auth.md": "a" * 8})
        assert engine._doc_sha_for("docs/missing.md", index) is None


# ---------------------------------------------------------------------------
# Code side: repo-aware folder inventories + the (repo, path) join
# ---------------------------------------------------------------------------


S1 = "/proj/scan/s1"
S3 = "/proj/scan/s3"

FOLDER_SHAS = {
    norm_repo(S1): {"src/COMMON/x/ADODB.cs": "1" * 8, "src/only_s1.py": "2" * 8},
    # s3 suffix-shadows s1's ADODB path (project-relative spelling would
    # be ambiguous) but never contains the exact same folder-relative key
    norm_repo(S3): {"src/deep/src/COMMON/x/ADODB.cs": "3" * 8, "src/only_s3.py": "4" * 8},
}


class TestCodeShaJoin:
    def test_repo_exact_beats_suffix_shadow(self):
        # the s3 shadow must never leak into s1's resolution
        sha, outcome = engine.CodeShaIndex(FOLDER_SHAS, {}).resolve(
            S1, "src/COMMON/x/ADODB.cs"
        )
        assert (sha, outcome) == ("1" * 8, "matched")

    @pytest.mark.parametrize(
        "repo", [None, "", "/other/machine/project-root"], ids=["null", "empty", "foreign"]
    )
    def test_unique_fallback_without_repo(self, repo):
        # endpoint labels carry repo=null; a foreign repo (android parser
        # stamps the project root) must degrade the same way
        sha, outcome = engine.CodeShaIndex(FOLDER_SHAS, {}).resolve(
            repo, "src/only_s1.py"
        )
        assert (sha, outcome) == ("2" * 8, "matched")

    def test_ambiguous_never_guesses(self):
        both = {
            norm_repo(S1): {"src/dup.py": "1" * 8},
            norm_repo(S3): {"src/dup.py": "4" * 8},
        }
        sha, outcome = engine.CodeShaIndex(both, {}).resolve(None, "src/dup.py")
        assert sha is None
        assert outcome == "ambiguous"
        # but the repo pin still resolves it exactly
        sha, outcome = engine.CodeShaIndex(both, {}).resolve(S3, "src/dup.py")
        assert (sha, outcome) == ("4" * 8, "matched")

    def test_flat_only_map_unique_suffix_fallback(self):
        # legacy callers pass a project-relative flat map; a unique
        # component-boundary suffix match is the sanctioned fallback
        flat = {
            "src/auth/login.py": "0" * 8,  # direct-equal key (folder == project root)
            "repo/s1/src/only_s1.py": "2" * 8,
        }
        index = engine.CodeShaIndex({}, flat)
        assert index.resolve(None, "src/only_s1.py") == ("2" * 8, "matched")
        assert index.resolve(None, "src/auth/login.py") == ("0" * 8, "matched")

    def test_flat_ambiguous_suffix_is_ambiguous(self):
        flat = {"repo/s1/src/dup.py": "1" * 8, "repo/s3/src/dup.py": "4" * 8}
        sha, outcome = engine.CodeShaIndex({}, flat).resolve(None, "src/dup.py")
        assert sha is None and outcome == "ambiguous"

    def test_missing_everywhere(self):
        sha, outcome = engine.CodeShaIndex(FOLDER_SHAS, {}).resolve(
            S1, "src/ghost.cs"
        )
        assert sha is None and outcome == "missing"

    def test_known_repo_miss_never_borrows(self):
        # s1's pin + a path only s3 has → missing, NOT s3's SHA
        # (review finding 2: the fallback is for null/foreign repos only)
        sha, outcome = engine.CodeShaIndex(FOLDER_SHAS, {}).resolve(
            S1, "src/only_s3.py"
        )
        assert sha is None and outcome == "missing"


class TestEngineSymbolRepo:
    def test_repo_is_join_input_not_identity(self):
        symbol = engine.EngineSymbol(
            name="ADODB", path="src/COMMON/x/ADODB.cs", repo=S1
        )
        assert symbol.repo == S1
        # record identity is unchanged by the repo field
        assert symbol.identity == (
            store.posix_path("src/COMMON/x/ADODB.cs"),
            "ADODB",
            (None, None),
            "symbol",
        )


def test_build_record_schema_unchanged():
    """repo is a join input only — the record body stays byte-identical."""
    paragraph = engine.ParagraphRef("docs/auth.md", 1)
    symbol = engine.EngineSymbol(
        name="login_handler", path="src/auth/login.py", start_line=10,
        end_line=20, parser_type="python", repo="/proj/repo",
    )
    record = engine.build_record(
        project_id="fixt", paragraph=paragraph, symbol=symbol,
        doc_sha="a" * 8, code_sha="b" * 8,
    )
    assert set(record) == {
        "schema_version", "project_id", "relation", "doc", "code",
        "score", "provenance",
    }
    assert "repo" not in record["code"]
    assert record["code"]["source_sha"] == "b" * 8


def test_load_code_folder_inventories(tmp_path):
    """Two scan folders → {resolved folder → {folder_rel → sha}}."""
    for name, entries in (
        ("s1", {"src/COMMON/x/ADODB.cs": "1" * 8}),
        ("s3", {"src/deep/src/COMMON/x/ADODB.cs": "3" * 8}),
    ):
        folder = tmp_path / name
        incremental = folder / ".cache" / "incremental_sync"
        inventories = folder / ".cache" / "inventories"
        incremental.mkdir(parents=True)
        inventories.mkdir(parents=True)
        snapshot = inventories / "snap.json"
        snapshot.write_text(
            json.dumps({"entries": {k: {"sha256": v} for k, v in entries.items()}}),
            encoding="utf-8",
        )
        (incremental / "fixt_scope.json").write_text(
            json.dumps({"inventory_path": str(snapshot), "last_run_after": "t"}),
            encoding="utf-8",
        )
    result = engine.load_code_folder_inventories(
        [str(tmp_path / "s1"), str(tmp_path / "s3")], "fixt"
    )
    assert result == {
        folder_key(str(tmp_path / "s1")): {"src/COMMON/x/ADODB.cs": "1" * 8},
        folder_key(str(tmp_path / "s3")): {"src/deep/src/COMMON/x/ADODB.cs": "3" * 8},
    }


# ---------------------------------------------------------------------------
# Integration: build_links joins real SHAs (red against today's engine)
# ---------------------------------------------------------------------------


class StubGraph:
    """Minimal read-only handle routed by query shape."""

    def __init__(self, paragraphs=None, entity_mentions=None, symbols=None):
        self.paragraphs = paragraphs or []
        self.entity_mentions = entity_mentions or []
        self.symbols = symbols or []

    def run(self, query, params=None):
        if "HAS_ENTITY" in query:
            return list(self.entity_mentions)
        if "Paragraph" in query:
            return list(self.paragraphs)
        if "n:Function" in query:
            return [dict(row, language="python") for row in self.symbols]
        return []


def _symbol_row(name, path, repo, start=10, end=20):
    return {
        "name": name,
        "qualified_name": "",
        "file_path": path,
        "start_line": start,
        "end_line": end,
        "repo": repo,
    }


DOC_KEY = "docs/auth.md"
FLAT_DOC_ID = "docs__auth.md"
S1_FOLDER_SHAS = {
    norm_repo("/proj/repo"): {"src/auth/login.py": "b" * 8},
}


def _sha_join_graphs():
    doc = StubGraph(
        paragraphs=[{"source_id": FLAT_DOC_ID, "paragraph_id": 1,
                     "text": "the login_handler delegates to session_store."}],
        entity_mentions=[
            {"source_id": FLAT_DOC_ID, "paragraph_id": 1, "name": "login_handler"}
        ],
    )
    code = StubGraph(
        symbols=[_symbol_row("login_handler", "src/auth/login.py", "/proj/repo")]
    )
    return code, doc


def test_build_links_joins_shas_both_sides(tmp_path):
    """Flattened doc ids + repo-aware code inventory → non-null SHAs."""
    links_dir = tmp_path / "fixt"
    code, doc = _sha_join_graphs()
    report = engine.build_links(
        project_id="fixt",
        links_dir=links_dir,
        code_graph=code,
        doc_graph=doc,
        doc_shas={DOC_KEY: "a" * 8},
        code_shas={},  # folder inventories are the authoritative source
        code_folder_shas=S1_FOLDER_SHAS,
        parser_type="python",
    )
    records = store.load_links(links_dir, status="active")
    assert len(records) == 1
    assert records[0]["doc"]["source_sha"] == "a" * 8
    assert records[0]["code"]["source_sha"] == "b" * 8
    assert report.sha_join == {
        "doc_matched": 1,
        "doc_missing": 0,
        "code_matched": 1,
        "code_missing": 0,
        "code_ambiguous": 0,
    }


def test_build_links_edit_drift_fires(tmp_path):
    """Editing a baseline SHA stales the file's links and re-appends fresh.

    Previously impossible: with both sides null, `_sha_changed` could
    never fire (plan acceptance row: the unit drift proof).
    """
    links_dir = tmp_path / "fixt"
    code, doc = _sha_join_graphs()
    engine.build_links(
        project_id="fixt", links_dir=links_dir, code_graph=code, doc_graph=doc,
        doc_shas={DOC_KEY: "a" * 8}, code_shas={},
        code_folder_shas=S1_FOLDER_SHAS, parser_type="python",
    )
    drifted = {norm_repo("/proj/repo"): {"src/auth/login.py": "c" * 8}}
    second = engine.build_links(
        project_id="fixt", links_dir=links_dir, code_graph=code, doc_graph=doc,
        doc_shas={DOC_KEY: "a" * 8}, code_shas={},
        code_folder_shas=drifted, parser_type="python",
    )
    raw_stale = store.load_links(links_dir, status="stale")
    assert len(raw_stale) == 1  # the supersede transition was appended
    active = store.latest_links(links_dir, status="active")
    assert active[0]["code"]["source_sha"] == "c" * 8
    assert len(second.new_links) == 1


def test_build_links_noop_when_shas_stable(tmp_path):
    """Unchanged SHAs keep the delta no-op skip (drift triad, case b)."""
    links_dir = tmp_path / "fixt"
    code, doc = _sha_join_graphs()
    engine.build_links(
        project_id="fixt", links_dir=links_dir, code_graph=code, doc_graph=doc,
        doc_shas={DOC_KEY: "a" * 8}, code_shas={},
        code_folder_shas=S1_FOLDER_SHAS, parser_type="python",
    )
    second = engine.build_links(
        project_id="fixt", links_dir=links_dir, code_graph=code, doc_graph=doc,
        doc_shas={DOC_KEY: "a" * 8}, code_shas={},
        code_folder_shas=S1_FOLDER_SHAS, parser_type="python",
    )
    assert second.new_links == []
    assert second.unchanged_links == 1
    assert store.latest_links(links_dir, status="stale") == []


def test_build_links_doc_deleted_still_stales(tmp_path):
    """Deletion detection keeps working alongside sha drift (triad, case c)."""
    links_dir = tmp_path / "fixt"
    code, doc = _sha_join_graphs()
    engine.build_links(
        project_id="fixt", links_dir=links_dir, code_graph=code, doc_graph=doc,
        doc_shas={DOC_KEY: "a" * 8}, code_shas={},
        code_folder_shas=S1_FOLDER_SHAS, parser_type="python",
    )
    empty_doc = StubGraph(paragraphs=[])
    engine.build_links(
        project_id="fixt", links_dir=links_dir, code_graph=code,
        doc_graph=empty_doc,
        doc_shas={DOC_KEY: "a" * 8}, code_shas={},
        code_folder_shas=S1_FOLDER_SHAS, parser_type="python",
    )
    stale = store.latest_links(links_dir, status="stale")
    assert len(stale) == 1
