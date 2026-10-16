"""Unit tests for project_scope helpers.

Run from the repo root with::

    PYTHONPATH=code-tiny python -m unittest \
        code-tiny.tools.common.test_project_scope
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


_PROJECT_SCOPE_PATH = (
    Path(__file__).resolve().parents[0] / "project_scope.py"
)


def _load_project_scope():
    spec = importlib.util.spec_from_file_location(
        "project_scope_under_test", _PROJECT_SCOPE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["project_scope_under_test"] = module
    spec.loader.exec_module(module)
    return module


project_scope = _load_project_scope()


class QdrantFilterTests(unittest.TestCase):
    """``qdrant_project_filter`` scopes by project_id when set, returns
    ``None`` (full search) when the id is empty, and expands the scope to
    the LIKE/prefix key set of the known projects."""

    def test_filters_by_project(self) -> None:
        filt = project_scope.qdrant_project_filter("cortext", known_ids=[])
        self.assertEqual(
            filt,
            {
                "must": [
                    {
                        "key": "project_id_normalized",
                        "match": {"any": ["cortext"]},
                    }
                ]
            },
        )

    def test_prefix_expands_known_ids(self) -> None:
        # The LIKE rule: query "Bank" matches the query key itself plus
        # every known id that casefold-starts-with it. Unrelated ids are
        # not pulled in.
        filt = project_scope.qdrant_project_filter(
            "Bank",
            known_ids=["bank_android", "bank_Cplus", "bangkok", "other"],
        )
        self.assertEqual(
            filt,
            {
                "must": [
                    {
                        "key": "project_id_normalized",
                        "match": {
                            "any": ["bank", "bank_android", "bank_cplus"]
                        },
                    }
                ]
            },
        )

    def test_no_project_id_returns_none(self) -> None:
        # Missing project_id is the implicit full-search path — no filter.
        self.assertIsNone(project_scope.qdrant_project_filter(None))
        self.assertIsNone(project_scope.qdrant_project_filter(""))
        self.assertIsNone(project_scope.qdrant_project_filter("   "))


class ScopeKeysTests(unittest.TestCase):
    """``project_id_scope_keys`` implements the case-insensitive LIKE key
    set used by every read-path filter."""

    def test_unscoped_is_none(self) -> None:
        self.assertIsNone(project_scope.project_id_scope_keys(None))
        self.assertIsNone(project_scope.project_id_scope_keys("  "))

    def test_exact_key_only_without_known_ids(self) -> None:
        self.assertEqual(
            project_scope.project_id_scope_keys("Bank", known_ids=[]),
            ["bank"],
        )

    def test_prefix_keys_include_query_key(self) -> None:
        self.assertEqual(
            project_scope.project_id_scope_keys(
                "bank", known_ids=["Bank_android", "BANK_CPLUS", "bank"]
            ),
            ["bank", "bank_android", "bank_cplus"],
        )


class MatchesProjectScopeTests(unittest.TestCase):
    """``matches_project_scope`` returns True for every candidate when
    ``project_id`` is missing, and applies the case-insensitive LIKE rule
    otherwise."""

    def test_matching_project(self) -> None:
        candidate = {"project_id_normalized": "projA", "name": "x"}
        self.assertTrue(project_scope.matches_project_scope(candidate, "ProjA"))

    def test_prefix_candidate_matches(self) -> None:
        # "proj" matches projA / projAndroid — the scanner names per-target
        # projects {stem}_{platform} off a shared stem.
        self.assertTrue(
            project_scope.matches_project_scope(
                {"project_id_normalized": "proja"}, "proj"
            )
        )
        self.assertTrue(
            project_scope.matches_project_scope(
                {"project_id": "ProjAndroid"}, "proj"
            )
        )

    def test_non_matching_project(self) -> None:
        candidate = {"project_id_normalized": "projB", "name": "x"}
        self.assertFalse(project_scope.matches_project_scope(candidate, "ProjA"))

    def test_no_project_id_always_matches(self) -> None:
        # Missing project_id is the implicit full-search path — every
        # candidate passes.
        candidate = {"project_id_normalized": "projB", "name": "x"}
        self.assertTrue(project_scope.matches_project_scope(candidate, None))
        self.assertTrue(project_scope.matches_project_scope(candidate, ""))


class PrepareParametersTests(unittest.TestCase):
    """``prepare_project_scope_parameters`` adds ``*_normalized`` keys but
    no longer sets a ``search_full`` flag."""

    def test_adds_normalized_key(self) -> None:
        params = project_scope.prepare_project_scope_parameters(
            "MATCH (n)", {"project_id": "cortext"}
        )
        self.assertEqual(params["project_id_normalized"], "cortext")

    def test_does_not_set_search_full(self) -> None:
        params = project_scope.prepare_project_scope_parameters(
            "MATCH (n)", {"project_id": "cortext"}
        )
        self.assertNotIn("search_full", params)

    def test_empty_params(self) -> None:
        params = project_scope.prepare_project_scope_parameters("MATCH (n)", None)
        self.assertNotIn("search_full", params)
        self.assertNotIn("project_id_normalized", params)


class PrepareRepoScopeParametersTests(unittest.TestCase):
    """``prepare_repo_scope_parameters`` injects the ``repo_suffix`` sibling
    for the ENDS WITH branch and normalizes blank values to ``None`` (RR3/RR4)."""

    def test_scoped_repo_gets_suffix(self) -> None:
        # typical: repo="s3" → suffix "/s3" so stored flavors
        # "/Users/x/s3" and "proj/s3" both match, "xs3" does not.
        params = project_scope.prepare_repo_scope_parameters({"repo": "s3"})
        self.assertEqual(params["repo"], "s3")
        self.assertEqual(params["repo_suffix"], "/s3")

    def test_scoped_repo_value_is_stripped(self) -> None:
        params = project_scope.prepare_repo_scope_parameters({"repo": "  s3  "})
        self.assertEqual(params["repo"], "s3")
        self.assertEqual(params["repo_suffix"], "/s3")

    def test_blank_repo_binds_empty_string(self) -> None:
        # boundary: blank/whitespace → unscoped. The Cypher-facing binding
        # is the EMPTY STRING, never None: LadybugDB types a None parameter
        # as BOOL, which poisons the string comparisons (see the predicate
        # docstring). The predicate's "$repo = ''" branch makes it a no-op.
        for blank in ("", "   "):
            params = project_scope.prepare_repo_scope_parameters({"repo": blank})
            self.assertEqual(params["repo"], "")
            self.assertEqual(params["repo_suffix"], "")

    def test_none_repo_is_unscoped(self) -> None:
        # invalid: explicit None behaves exactly like the unscoped default.
        params = project_scope.prepare_repo_scope_parameters({"repo": None})
        self.assertEqual(params["repo"], "")
        self.assertEqual(params["repo_suffix"], "")

    def test_absent_repo_key_injects_nothing(self) -> None:
        params = project_scope.prepare_repo_scope_parameters({"limit": 5})
        self.assertEqual(params, {"limit": 5})

    def test_none_parameters(self) -> None:
        self.assertEqual(project_scope.prepare_repo_scope_parameters(None), {})

    def test_coexists_with_project_scope_preparation(self) -> None:
        # integration-light: both helpers chain over the same params dict
        # without overwriting each other (disjoint keys).
        params = {"project_id": "cortext", "repo": "s3"}
        params = project_scope.prepare_project_scope_parameters("MATCH (n)", params)
        params = project_scope.prepare_repo_scope_parameters(params)
        self.assertEqual(params["project_id"], "cortext")
        self.assertEqual(params["project_id_normalized"], "cortext")
        self.assertEqual(params["repo"], "s3")
        self.assertEqual(params["repo_suffix"], "/s3")


class RepoScopePredicateTests(unittest.TestCase):
    """``repo_scope_predicate`` renders the no-op-safe fragment for any
    static alias and never interpolates user values into the query text."""

    def test_renders_for_each_backend_alias(self) -> None:
        for alias in ("n", "node", "s", "t", "w"):
            fragment = project_scope.repo_scope_predicate(alias)
            self.assertEqual(
                fragment,
                "($repo = '' OR "
                f"{alias}.repo = $repo OR {alias}.repo ENDS WITH $repo_suffix)",
            )

    def test_fragment_never_uses_is_null_on_repo(self) -> None:
        # LadybugDB binds None parameters as BOOL; "$repo IS NULL" would
        # poison "$repo" for the string comparisons. The empty-string test
        # keeps every backend STRING-typed.
        self.assertNotIn("IS NULL", project_scope.repo_scope_predicate("n"))

    def test_user_values_never_enter_the_fragment(self) -> None:
        fragment = project_scope.repo_scope_predicate("n")
        self.assertNotIn("s3", fragment)
        self.assertNotIn("/s3", fragment)


class MatchesRepoScopeTests(unittest.TestCase):
    """``matches_repo_scope`` is the Python twin of the Cypher predicate:
    exact or '/'-suffix match, unscoped when repo is blank, and empty node
    values never match a scoped query."""

    def test_unscoped_matches_everything(self) -> None:
        for blank in (None, "", "   "):
            self.assertTrue(project_scope.matches_repo_scope("/Users/x/s3", blank))
            self.assertTrue(project_scope.matches_repo_scope("", blank))

    def test_exact_match(self) -> None:
        self.assertTrue(project_scope.matches_repo_scope("/Users/x/s3", "/Users/x/s3"))
        self.assertTrue(project_scope.matches_repo_scope("projA/s3", "projA/s3"))

    def test_suffix_match_covers_both_stored_flavors(self) -> None:
        self.assertTrue(project_scope.matches_repo_scope("/Users/x/s3", "s3"))
        self.assertTrue(project_scope.matches_repo_scope("projA/s3", "s3"))

    def test_basename_prefix_is_not_a_match(self) -> None:
        # "s3" must not match "xs3" — the suffix is anchored on "/".
        self.assertFalse(project_scope.matches_repo_scope("/Users/x/xs3", "s3"))
        self.assertFalse(project_scope.matches_repo_scope("projA/xs3", "s3"))

    def test_empty_node_repo_never_matches_scoped(self) -> None:
        self.assertFalse(project_scope.matches_repo_scope("", "s3"))
        self.assertFalse(project_scope.matches_repo_scope(None, "s3"))

    def test_unrelated_repo_does_not_match(self) -> None:
        self.assertFalse(project_scope.matches_repo_scope("/Users/x/s1", "s3"))


if __name__ == "__main__":
    unittest.main()
