import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CODE_TINY = ROOT / "code-tiny"
if str(CODE_TINY) not in sys.path:
    sys.path.insert(0, str(CODE_TINY))

from tools.project_topology.contracts import (  # noqa: E402
    COMPATIBILITY_MAP,
    CONTEXT_TOOL_CONTRACTS,
)
from tools.project_topology.models import (  # noqa: E402
    ModuleFact,
    normalize_module_path,
    stable_module_id,
)


def test_module_identity_is_case_insensitive_and_checkout_independent():
    assert stable_module_id("MiXeD", r".\app\core") == (
        "project-module:mixed:app/core"
    )
    assert stable_module_id("mixed", "app/./core") == (
        "project-module:mixed:app/core"
    )
    assert normalize_module_path("") == "."


def test_module_path_escape_and_absolute_paths_are_rejected():
    with pytest.raises(ValueError):
        normalize_module_path("../outside")
    with pytest.raises(ValueError):
        normalize_module_path("/absolute")
    with pytest.raises(ValueError):
        normalize_module_path(r"C:\absolute")


def test_compatibility_contract_is_additive_and_tools_require_scope():
    assert COMPATIBILITY_MAP["GradleModule"]["canonical_label"] == "ProjectModule"
    assert not any(
        value["destructive_migration"] for value in COMPATIBILITY_MAP.values()
    )
    assert all(
        "project_id" in contract["scope"]
        for contract in CONTEXT_TOOL_CONTRACTS.values()
    )


def test_module_serialization_is_deterministic():
    first = ModuleFact.create(
        project_id="demo",
        module_path="app",
        name="app",
        languages=("kotlin", "java"),
    )
    second = ModuleFact.create(
        project_id="demo",
        module_path="app",
        name="app",
        languages=("kotlin", "java"),
    )
    assert first.to_dict() == second.to_dict()
