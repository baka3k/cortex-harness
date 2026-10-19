"""Tests for link-tiny normalizers (pure matching logic) and the registry
facade delegation contract (Phase 01 acceptance criteria).

Run: PYTHONPATH=link-tiny python -m pytest link-tiny/tests/test_normalizers.py -q
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


LINK_TINY = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, LINK_TINY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


normalizers = _load("normalizers")


# ---------------------------------------------------------------------------
# qualified_segments — typical / boundary / invalid
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("a.b.C", ["a.b.C", "b.C", "C"]),
        ("C", ["C"]),
        ("", []),
        (None, []),
        ("a..b", ["a.b", "b"]),  # empty segment collapsed
        (".a.b.", ["a.b", "b"]),  # leading/trailing dots stripped
    ],
)
def test_qualified_segments(value, expected):
    assert normalizers.qualified_segments(value) == expected


# ---------------------------------------------------------------------------
# extract_identifier_tokens — snake / camel / dotted / route from prose
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        # typical: mixed identifier spellings in one sentence; a dotted path
        # and a route count as one token each (their segments are matched
        # through qualified-segment keys, not re-extracted as words)
        (
            "The login_handler calls UserAuthService.verify_token, see /api/v1/users.",
            ["login_handler", "UserAuthService.verify_token", "/api/v1/users"],
        ),
        # boundary: plain prose only → no tokens
        ("This document describes how the system works.", []),
        # boundary: empty / None
        ("", []),
        (None, []),
        # dotted path with class
        ("implemented in auth.login.handler", ["auth.login.handler"]),
        # camelCase in prose
        ("the traceStatus endpoint", ["traceStatus"]),
    ],
)
def test_identifier_tokens(text, expected):
    assert normalizers.extract_identifier_tokens(text) == expected


def test_identifier_tokens_dedup_preserves_order():
    text = "login_handler and again login_handler, then UserAuthService"
    assert normalizers.extract_identifier_tokens(text) == [
        "login_handler",
        "UserAuthService",
    ]


# ---------------------------------------------------------------------------
# token keys + stoplist (RT-5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("login_handler", ("login", "handler")),
        ("loginHandler", ("login", "handler")),
        ("LoginHandler", ("login", "handler")),
        ("login.handler", ("login", "handler")),
        ("HTTPServer", ("http", "server")),
        ("", ()),
    ],
)
def test_token_match_key(value, expected):
    assert normalizers.token_match_key(value) == expected


@pytest.mark.parametrize(
    "token,linkable",
    [
        ("init", False),  # stoplist verb
        ("setup", False),  # stoplist verb
        ("run", False),  # stoplist + short
        ("abc", False),  # below min length
        ("1234", False),  # pure digits
        ("login_handler", True),
        ("UserAuthService", True),
        ("parse", False),  # stoplist even though length >= 4
        # designer Field names (Phase 03 noise guard): the label list grew,
        # but doc tokens still pass the same stoplist/min-length rules —
        # a doc must literally name the control for a link to appear
        ("grid_unit_col", True),
        ("textBox2", True),  # identifier-shaped; only links when named in a doc
    ],
)
def test_is_linkable_token(token, linkable):
    assert normalizers.is_linkable_token(token) is linkable


# ---------------------------------------------------------------------------
# match_candidates — exact-name rule, ambiguity emits all (R5)
# ---------------------------------------------------------------------------


def _sym(name, qualified_name="", path="src/x.py", target_type="symbol"):
    return normalizers.CodeSymbolRef(
        name=name,
        qualified_name=qualified_name,
        path=path,
        target_type=target_type,
        identity=(path, name),
    )


def test_match_candidates_exact_short_name():
    symbols = [_sym("login_handler", path="src/auth/login.py")]
    pairs = normalizers.match_candidates("login_handler", symbols)
    assert [(p[0].name, p[1], p[2]) for p in pairs] == [
        ("login_handler", 1.0, "exact-name")
    ]


@pytest.mark.parametrize(
    "token",
    ["login_handler", "loginHandler", "LoginHandler", "login.handler"],
)
def test_match_candidates_convention_equal(token):
    symbols = [_sym("login_handler")]
    assert len(normalizers.match_candidates(token, symbols)) == 1


def test_match_candidates_qualified_bidirectional():
    symbols = [_sym("login_handler", qualified_name="auth.login.login_handler")]
    # doc references the full qualified path
    assert len(normalizers.match_candidates("auth.login.login_handler", symbols)) == 1
    # doc references a dotted suffix segment
    assert len(normalizers.match_candidates("login.login_handler", symbols)) == 1


def test_match_candidates_ambiguous_emits_all():
    symbols = [
        _sym("login_handler", path="src/a/login.py"),
        _sym("login_handler", path="src/b/login.py"),
    ]
    pairs = normalizers.match_candidates("login_handler", symbols)
    assert len(pairs) == 2  # never auto-select
    assert {p[0].path for p in pairs} == {"src/a/login.py", "src/b/login.py"}


def test_match_candidates_stoplisted_token_produces_nothing():
    symbols = [_sym("init"), _sym("parse_config")]
    assert normalizers.match_candidates("init", symbols) == []
    assert normalizers.match_candidates("setup", symbols) == []


def test_match_candidates_no_match():
    symbols = [_sym("unrelated_thing")]
    assert normalizers.match_candidates("login_handler", symbols) == []
    assert normalizers.match_candidates("", symbols) == []


# ---------------------------------------------------------------------------
# registry facade — delegation contract
# ---------------------------------------------------------------------------


@pytest.fixture
def facade(tmp_path, monkeypatch):
    """Load registry_facade fresh with an isolated config dir."""
    config_dir = tmp_path / ".cortext-harness" / "config"
    config_dir.mkdir(parents=True)
    monkeypatch.setenv("CORTEX_HARNESS_CONFIG_PATH", str(config_dir / "dev.json"))
    monkeypatch.delenv("CORTEX_LINKS_ROOT", raising=False)
    for name in ("registry_facade",):
        sys.modules.pop(name, None)
    module = _load("registry_facade")
    return module, config_dir


def _write_config(config_dir: Path, payload: dict) -> None:
    import json

    (config_dir / "dev.json").write_text(json.dumps(payload), encoding="utf-8")


def test_facade_matches_code_registry(facade, tmp_path):
    """Registered project resolves to code-side targets (delegation on)."""
    module, config_dir = facade
    _write_config(
        config_dir,
        {
            "active": True,
            "project": {"code": "fixt", "name": "fixt"},
            "code": {"env": {"FALKORDB_GRAPH": "fixt", "QDRANT_COLLECTION": "fixt"}},
            "doc": {"env": {"FALKORDB_GRAPH": "fixt_doc"}},
        },
    )
    targets = module.resolve_project_targets("FIXT")  # case-insensitive lookup
    assert targets.code_graph == "fixt"
    assert targets.doc_graph == "fixt_doc"
    assert targets.code_qdrant_collection == "fixt"
    assert targets.doc_qdrant_collection == "fixt_doc"
    assert targets.project_id_normalized == "fixt"
    # code-side registry is importable in this repo, so delegation happened
    from tools.common.project_registry import resolve_project_targets as code_resolve

    code_targets = code_resolve("fixt", config_dir=config_dir)
    assert targets.code_graph == code_targets.code_graph
    assert targets.doc_graph == code_targets.doc_graph


def test_facade_env_fallback(facade, tmp_path, monkeypatch):
    """Boundary: registry file missing → env+defaults resolution works.

    With the code registry importable, env+defaults is the registry's own
    ad-hoc path (resolution precedence: naming contract < env < config).
    """
    module, _config_dir = facade
    monkeypatch.setenv("FALKORDB_GRAPH", "adhoc_from_env")
    monkeypatch.setenv("QDRANT_COLLECTION", "adhoc_from_env")
    targets = module.resolve_project_targets("ad_hoc_proj")
    assert targets.code_graph == "adhoc_from_env"
    assert targets.project_id_normalized == "ad_hoc_proj"


def test_facade_standalone_naming_contract_fallback(facade, monkeypatch):
    """When the code registry is not importable, the naming contract rules."""
    module, _config_dir = facade
    monkeypatch.setattr(module, "_registry_resolve_targets", None)
    monkeypatch.delenv("FALKORDB_GRAPH", raising=False)
    monkeypatch.delenv("DOC_FALKORDB_GRAPH", raising=False)
    targets = module.resolve_project_targets("ad_hoc_proj")
    assert targets.code_graph == "ad_hoc_proj"
    assert targets.doc_graph == "ad_hoc_proj_doc"
    assert targets.project_id_normalized == "ad_hoc_proj"


def test_facade_unregistered_raises(facade):
    module, config_dir = facade
    _write_config(config_dir, {"project": {"code": "other"}})
    with pytest.raises(module.ProjectNotRegisteredError) as excinfo:
        module.resolve_project_targets("missing_proj")
    assert "other" in str(excinfo.value)


def test_facade_lists_registered_projects(facade):
    module, config_dir = facade
    _write_config(config_dir, {"project": {"code": "proj_a"}, "active": True})
    assert module.list_registered_projects() == ["proj_a"]


def test_links_dir_under_instance_root(facade, tmp_path, monkeypatch):
    module, _config_dir = facade
    instance = tmp_path / "instance"
    instance.mkdir()
    monkeypatch.setenv("CORTEX_LINKS_ROOT", str(instance))
    path = module.links_dir("proj_x")
    assert path == instance / "links" / "proj_x"


def test_links_dir_requires_project_id(facade):
    module, _config_dir = facade
    with pytest.raises(module.ProjectNotRegisteredError):
        module.links_dir("")


@pytest.mark.parametrize(
    "bad", ["../etc", "a/b", "a\\b", ".hidden", "-lead", "", "x" * 65]
)
def test_links_dir_rejects_unsafe_project_ids(facade, tmp_path, monkeypatch, bad):
    """The id becomes a path segment — traversal/separators are rejected."""
    module, _config_dir = facade
    instance = tmp_path / "instance"
    instance.mkdir()
    monkeypatch.setenv("CORTEX_LINKS_ROOT", str(instance))
    with pytest.raises(module.ProjectNotRegisteredError):
        module.links_dir(bad)
    assert not (instance / "links").exists() or list(
        (instance / "links").iterdir()
    ) == []
