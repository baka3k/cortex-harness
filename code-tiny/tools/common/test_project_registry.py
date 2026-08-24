"""Unit tests for project_registry.

Run from the repo root with::

    PYTHONPATH=code-tiny python -m unittest \
        code-tiny.tools.common.test_project_registry
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest import mock


_REPO_ROOT = Path(__file__).resolve().parents[3]
_CODE_TINY = _REPO_ROOT / "code-tiny"
_REGISTRY_PATH = _CODE_TINY / "tools" / "common" / "project_registry.py"
_PROJECT_SCOPE_PATH = _CODE_TINY / "tools" / "common" / "project_scope.py"


def _load_module(name: str, file_path: Path) -> ModuleType:
    """Load a module from ``file_path`` under ``name`` and register it."""
    spec = importlib.util.spec_from_file_location(name, file_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclass introspection requires sys.modules entry.
    spec.loader.exec_module(module)
    return module


def _load_registry() -> ModuleType:
    """Load project_registry as a standalone module so sys.path is irrelevant.

    project_scope is loaded first under its own synthetic name so the
    ``from tools.common.project_scope import ...`` line inside project_registry
    can resolve without changing the process sys.path.
    """
    _load_module("tools.common.project_scope", _PROJECT_SCOPE_PATH)
    return _load_module("project_registry_under_test", _REGISTRY_PATH)


project_registry = _load_registry()


# Env vars that the resolver reads from the environment. The host shell may
# have any of these set (NEO4J_DB, QDRANT_*, FALKORDB_GRAPH) which would leak
# into tests. We snapshot + restore around every test that doesn't explicitly
# want a polluted env.
_LEAKED_ENV_KEYS = (
    "FALKORDB_GRAPH",
    "FALKORDB_GRAPH_DOC",
    "NEO4J_DB",
    "QDRANT_COLLECTION",
    "QDRANT_COLLECTION_DOC",
    "GRAPH_PROVIDER",
    "CODE_GRAPH_PROVIDER",
    "DOC_GRAPH_PROVIDER",
    "MCP_GRAPH_PROVIDER",
)


def _write_config(directory: Path, project_id: str, **overrides: dict) -> Path:
    """Write one dev.json-shaped file in ``directory`` for ``project_id``.

    Tests scrub the harness env via ``_BaseTest`` before calling this so the
    registry only sees config-file values, not stray host env vars.
    """
    payload = {
        "active": True,
        "project": {"code": project_id, "name": project_id},
        "code": {"env": overrides.pop("code_env", {})},
        "doc": {"env": overrides.pop("doc_env", {})},
    }
    file_path = directory / f"{project_id}.json"
    file_path.write_text(json.dumps(payload), encoding="utf-8")
    return file_path


class _EnvScrubber:
    """Context manager that scrubs harness env vars on enter and restores on exit."""

    def __enter__(self) -> "_EnvScrubber":
        self._originals = {key: os.environ.get(key) for key in _LEAKED_ENV_KEYS}
        for key in _LEAKED_ENV_KEYS:
            os.environ[key] = ""
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        for key, value in self._originals.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class _BaseTest(unittest.TestCase):
    """Base test case that scrubs host env before each test."""

    def setUp(self) -> None:
        self._scrubber = _EnvScrubber()
        self._scrubber.__enter__()
        self.addCleanup(self._scrubber.__exit__, None, None, None)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_dir = Path(self._tmp.name)

    def write_config(self, project_id: str, **overrides: dict) -> Path:
        return _write_config(self.config_dir, project_id, **overrides)


class CaseInsensitiveLookupTests(_BaseTest):
    """``resolve_project_targets`` is case-insensitive per the naming contract."""

    def test_uppercase_resolves_same_as_lowercase(self) -> None:
        self.write_config("cortext")
        lower = project_registry.resolve_project_targets(
            "cortext", config_dir=self.config_dir
        )
        upper = project_registry.resolve_project_targets(
            "CORTEXT", config_dir=self.config_dir
        )
        # Use a case variant whose Unicode casefold is byte-identical to
        # the lowercase form. ``casefold()`` is stricter than ``lower()``
        # for some characters; pick variants that survive that round-trip.
        mixed = project_registry.resolve_project_targets(
            "CORText", config_dir=self.config_dir
        )
        # The full ProjectTargets must be identical for the same project —
        # that's the success criterion. The raw project_id is canonicalized
        # to the registered form so case variants collapse to one shape.
        self.assertEqual(lower, upper)
        self.assertEqual(lower, mixed)
        self.assertEqual(upper.project_id, "cortext")
        self.assertEqual(mixed.project_id, "cortext")
        self.assertEqual(upper.project_id_normalized, "cortext")

    def test_whitespace_is_trimmed(self) -> None:
        self.write_config("cortext")
        targets = project_registry.resolve_project_targets(
            "  cortext  ", config_dir=self.config_dir
        )
        self.assertEqual(targets.project_id, "cortext")

    def test_none_or_empty_raises(self) -> None:
        self.write_config("cortext")
        with self.assertRaises(project_registry.ProjectNotRegisteredError):
            project_registry.resolve_project_targets(None, config_dir=self.config_dir)
        with self.assertRaises(project_registry.ProjectNotRegisteredError):
            project_registry.resolve_project_targets("", config_dir=self.config_dir)
        with self.assertRaises(project_registry.ProjectNotRegisteredError):
            project_registry.resolve_project_targets("   ", config_dir=self.config_dir)


class DefaultNamingRuleTests(_BaseTest):
    """When config omits a field, the naming contract fills in the default."""

    def test_defaults_when_config_is_empty(self) -> None:
        self.write_config("alpha")
        targets = project_registry.resolve_project_targets(
            "alpha", config_dir=self.config_dir
        )
        self.assertEqual(targets.code_graph, "alpha")
        self.assertEqual(targets.code_graph, "alpha")
        self.assertEqual(targets.code_qdrant_collection, "alpha")
        self.assertEqual(targets.doc_graph, "alpha_doc")
        self.assertEqual(targets.doc_qdrant_collection, "alpha_doc")
        self.assertEqual(targets.provider, "falkordb")

    def test_defaults_when_no_config_file_exists_with_env(self) -> None:
        empty_dir = self.config_dir / "empty"
        empty_dir.mkdir()
        # Without a config file but with env vars set, the resolver seeds
        # an ad-hoc project from the env and returns the naming-rule
        # defaults for the unseeded doc side.
        with mock.patch.dict(
            os.environ,
            {"FALKORDB_GRAPH": "env_only_code", "QDRANT_COLLECTION": "env_only_q"},
            clear=False,
        ):
            targets = project_registry.resolve_project_targets(
                "alpha", config_dir=empty_dir
            )
        self.assertEqual(targets.code_graph, "env_only_code")
        self.assertEqual(targets.code_qdrant_collection, "env_only_q")
        self.assertEqual(targets.doc_graph, "alpha_doc")
        self.assertEqual(targets.doc_qdrant_collection, "alpha_doc")
        self.assertEqual(targets.source, "env+defaults")

    def test_raises_when_no_config_and_no_env(self) -> None:
        empty_dir = self.config_dir / "empty"
        empty_dir.mkdir()
        with self.assertRaises(project_registry.ProjectNotRegisteredError):
            project_registry.resolve_project_targets(
                "alpha", config_dir=empty_dir
            )


class ExplicitConfigOverrideTests(_BaseTest):
    """Explicit values in dev.json win over the naming rule defaults."""

    def test_code_and_doc_targets_come_from_config(self) -> None:
        self.write_config(
            "beta",
            code_env={
                "FALKORDB_GRAPH": "beta_code_graph",
                "QDRANT_COLLECTION": "beta_code_q",
                "GRAPH_PROVIDER": "falkordb",
            },
            doc_env={
                "FALKORDB_GRAPH": "beta_doc_graph",
                "QDRANT_COLLECTION": "beta_doc_q",
            },
        )
        targets = project_registry.resolve_project_targets(
            "beta", config_dir=self.config_dir
        )
        self.assertEqual(targets.code_graph, "beta_code_graph")
        self.assertEqual(targets.code_qdrant_collection, "beta_code_q")
        self.assertEqual(targets.doc_graph, "beta_doc_graph")
        self.assertEqual(targets.doc_qdrant_collection, "beta_doc_q")
        self.assertEqual(targets.provider, "falkordb")
        self.assertEqual(targets.source, "registry")

    def test_partial_config_falls_back_to_naming_rule(self) -> None:
        self.write_config("gamma", code_env={"FALKORDB_GRAPH": "gamma_g"})
        targets = project_registry.resolve_project_targets(
            "gamma", config_dir=self.config_dir
        )
        self.assertEqual(targets.code_graph, "gamma_g")
        self.assertEqual(targets.code_qdrant_collection, "gamma")
        self.assertEqual(targets.doc_graph, "gamma_doc")
        self.assertEqual(targets.doc_qdrant_collection, "gamma_doc")

    def test_falkor_targets_ignore_stale_neo4j_database_values(self) -> None:
        self.write_config(
            "gamma",
            code_env={
                "GRAPH_PROVIDER": "falkordb",
                "NEO4J_DB": "stale_code_graph",
            },
            doc_env={
                "GRAPH_PROVIDER": "falkordb",
                "NEO4J_DB": "stale_doc_graph",
            },
        )

        targets = project_registry.resolve_project_targets(
            "gamma", config_dir=self.config_dir
        )

        self.assertEqual(targets.provider, "falkordb")
        self.assertEqual(targets.code_graph, "gamma")
        self.assertEqual(targets.doc_graph, "gamma_doc")

    def test_explicit_neo4j_targets_use_only_neo4j_database_values(self) -> None:
        self.write_config(
            "gamma",
            code_env={
                "GRAPH_PROVIDER": "neo4j",
                "FALKORDB_GRAPH": "stale_falkor_code",
                "NEO4J_DB": "neo4j_code",
            },
            doc_env={
                "GRAPH_PROVIDER": "neo4j",
                "FALKORDB_GRAPH": "stale_falkor_doc",
                "NEO4J_DB": "neo4j_doc",
            },
        )

        targets = project_registry.resolve_project_targets(
            "gamma", config_dir=self.config_dir
        )

        self.assertEqual(targets.provider, "neo4j")
        self.assertEqual(targets.code_graph, "neo4j_code")
        self.assertEqual(targets.doc_graph, "neo4j_doc")

    def test_parser_type_is_read_from_project_descriptor(self) -> None:
        payload = {
            "project": {"code": "gamma", "name": "gamma", "parser_type": "python"},
            "code": {"env": {}},
            "doc": {"env": {}},
        }
        (self.config_dir / "gamma.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        targets = project_registry.resolve_project_targets(
            "gamma", config_dir=self.config_dir
        )
        self.assertEqual(targets.parser_type, "python")

    def test_parser_type_override_wins(self) -> None:
        payload = {
            "project": {"code": "gamma", "name": "gamma", "parser": "python"},
            "code": {"env": {}},
            "doc": {"env": {}},
        }
        (self.config_dir / "gamma.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        targets = project_registry.resolve_project_targets(
            "gamma", config_dir=self.config_dir, parser_type="typescript"
        )
        self.assertEqual(targets.parser_type, "typescript")


class DistinctProjectsTests(_BaseTest):
    """Two registered projects must yield distinct graph + collection names."""

    def test_two_projects_have_disjoint_targets(self) -> None:
        self.write_config("alpha")
        self.write_config("beta")
        a = project_registry.resolve_project_targets(
            "alpha", config_dir=self.config_dir
        )
        b = project_registry.resolve_project_targets(
            "beta", config_dir=self.config_dir
        )
        self.assertNotEqual(a.code_graph, b.code_graph)
        self.assertNotEqual(a.code_qdrant_collection, b.code_qdrant_collection)
        self.assertNotEqual(a.doc_graph, b.doc_graph)
        self.assertNotEqual(a.doc_qdrant_collection, b.doc_qdrant_collection)

    def test_list_registered_projects_returns_both(self) -> None:
        self.write_config("alpha")
        self.write_config("beta")
        self.assertEqual(
            sorted(project_registry.list_registered_projects(config_dir=self.config_dir)),
            ["alpha", "beta"],
        )


class UnknownProjectTests(_BaseTest):
    """Unknown projects raise with a helpful message and known list."""

    def test_unknown_project_lists_known(self) -> None:
        self.write_config("alpha")
        self.write_config("beta")
        with self.assertRaises(project_registry.ProjectNotRegisteredError) as ctx:
            project_registry.resolve_project_targets("gamma", config_dir=self.config_dir)
        self.assertIn("gamma", str(ctx.exception))
        self.assertIn("alpha", ctx.exception.known)
        self.assertIn("beta", ctx.exception.known)

    def test_unknown_project_message_is_actionable(self) -> None:
        self.write_config("alpha")
        with self.assertRaises(project_registry.ProjectNotRegisteredError) as ctx:
            project_registry.resolve_project_targets("gamma", config_dir=self.config_dir)
        self.assertIn(".cortext-harness/config", str(ctx.exception))

    def test_unknown_project_with_no_registry_raises(self) -> None:
        empty_dir = self.config_dir / "empty"
        empty_dir.mkdir()
        with self.assertRaises(project_registry.ProjectNotRegisteredError):
            project_registry.resolve_project_targets("gamma", config_dir=empty_dir)


class PerCallOverrideTests(_BaseTest):
    """Per-call overrides win over both config and the naming contract."""

    def test_overrides_replace_individual_fields(self) -> None:
        self.write_config("alpha")
        targets = project_registry.resolve_project_targets(
            "alpha",
            config_dir=self.config_dir,
            code_graph="custom_code",
            doc_qdrant_collection="custom_doc_q",
            provider="neo4j",
        )
        self.assertEqual(targets.code_graph, "custom_code")
        self.assertEqual(targets.code_qdrant_collection, "alpha")
        self.assertEqual(targets.doc_graph, "alpha_doc")
        self.assertEqual(targets.doc_qdrant_collection, "custom_doc_q")
        self.assertEqual(targets.provider, "neo4j")

    def test_invalid_override_field_raises_value_error(self) -> None:
        self.write_config("alpha")
        with self.assertRaises(ValueError):
            project_registry.with_overrides(
                project_registry.resolve_project_targets(
                    "alpha", config_dir=self.config_dir
                ),
                not_a_field="x",
            )


class EnvFallbackTests(_BaseTest):
    """When no project is registered, env vars seed defaults."""

    def test_env_seeds_code_graph_when_no_registry(self) -> None:
        # _BaseTest scrubs the env. Inject our own values on top.
        empty_dir = self.config_dir / "empty"
        empty_dir.mkdir()
        env = {
            "FALKORDB_GRAPH": "env_code_graph",
            "QDRANT_COLLECTION": "env_code_q",
            "QDRANT_COLLECTION_DOC": "env_doc_q",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            targets = project_registry.resolve_project_targets(
                "alpha", config_dir=empty_dir
            )
        self.assertEqual(targets.code_graph, "env_code_graph")
        self.assertEqual(targets.code_qdrant_collection, "env_code_q")
        self.assertEqual(targets.doc_qdrant_collection, "env_doc_q")
        # No dedicated doc graph env var was set, so naming rule kicks in.
        self.assertEqual(targets.doc_graph, "alpha_doc")


class ConfigReadingTests(_BaseTest):
    """The loader must skip malformed / unparseable config files silently."""

    def test_only_well_formed_project_entries_are_listed(self) -> None:
        self.write_config("alpha")
        (self.config_dir / "broken.json").write_text("{ not json", encoding="utf-8")
        (self.config_dir / "no_project.json").write_text(
            json.dumps({"code": {"env": {}}}), encoding="utf-8"
        )
        self.assertEqual(
            project_registry.list_registered_projects(config_dir=self.config_dir),
            ["alpha"],
        )

    def test_resolve_ignores_broken_files(self) -> None:
        self.write_config("alpha")
        (self.config_dir / "broken.json").write_text("{ not json", encoding="utf-8")
        targets = project_registry.resolve_project_targets(
            "alpha", config_dir=self.config_dir
        )

    def test_casefold_duplicate_registrations_are_rejected(self) -> None:
        self.write_config("Alpha")
        payload = {
            "project": {"code": "alpha", "name": "alpha"},
            "code": {"env": {}},
            "doc": {"env": {}},
        }
        (self.config_dir / "duplicate.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        with self.assertRaises(
            project_registry.DuplicateProjectRegistrationError
        ) as ctx:
            project_registry.list_registered_projects(config_dir=self.config_dir)
        self.assertIn("alpha", ctx.exception.collisions)


class RegistryContractTests(_BaseTest):
    """Properties the rest of the harness will lean on."""

    def test_targets_are_frozen_and_hashable(self) -> None:
        self.write_config("alpha")
        targets = project_registry.resolve_project_targets(
            "alpha", config_dir=self.config_dir
        )
        # Frozen dataclasses are hashable.
        self.assertEqual(
            len({targets, targets, targets}), 1
        )

    def test_with_overrides_returns_new_instance(self) -> None:
        self.write_config("alpha")
        targets = project_registry.resolve_project_targets(
            "alpha", config_dir=self.config_dir
        )
        patched = project_registry.with_overrides(targets, code_graph="zzz")
        self.assertEqual(patched.code_graph, "zzz")
        # Original instance untouched.
        self.assertEqual(targets.code_graph, "alpha")


if __name__ == "__main__":
    unittest.main()
