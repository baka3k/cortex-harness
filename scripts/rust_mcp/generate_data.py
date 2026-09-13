#!/usr/bin/env python3
"""Generate byte-exact Rust fixture/data files for the cortex-mcp crate.

Phase 11 of plans/260913-2130-rust-full-migration.

This script imports the *pure* Python contract modules (no torch / no graph):

* ``code-tiny/mcp/tool_metadata.py``        — the hand-written tool catalog
* ``code-tiny/mcp/framework_registry.py``   — canonical parser capabilities
* ``cortex_harness/mcp_contract.py``        — the wire envelope contract
* ``code-tiny/tools/common/project_registry.py`` — project_id registry

and dumps golden fixtures that the Rust port is tested against:

Crate data (shipped inside the binary):
* ``rust/crates/cortex-mcp/data/catalog.json``
* ``rust/crates/cortex-mcp/data/unified.json``

Crate test fixtures (committed, compared by ``cargo test -p cortex-mcp``):
* ``rust/crates/cortex-mcp/tests/fixtures/catalog_served.json``
* ``rust/crates/cortex-mcp/tests/fixtures/capability_catalog.json``
* ``rust/crates/cortex-mcp/tests/fixtures/list_parsers_summary.json``
* ``rust/crates/cortex-mcp/tests/fixtures/list_parsers_full.json``
* ``rust/crates/cortex-mcp/tests/fixtures/parser_aliases.json``
* ``rust/crates/cortex-mcp/tests/fixtures/framework_relationships.json``

The unified-server metadata (tool name set, parameter guidelines, server
instructions, per-tool wire descriptions) is extracted from
``code-tiny/mcp/unified_mcp.py`` via AST literal evaluation so this script
never imports torch / fastmcp.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CODE_TINY = ROOT / "code-tiny"
MCP_DIR = CODE_TINY / "mcp"

sys.path.insert(0, str(MCP_DIR))
sys.path.insert(0, str(CODE_TINY))

import tool_metadata  # noqa: E402
import framework_registry  # noqa: E402

DATA_DIR = ROOT / "rust" / "crates" / "cortex-mcp" / "data"
TEST_FIXTURES = ROOT / "rust" / "crates" / "cortex-mcp" / "tests" / "fixtures"


def dump_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=1) + "\n"
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path} ({len(text)} bytes)")


def _literal_or_container(node: ast.AST):
    """literal_eval that also unwraps ``frozenset({...})`` constructor calls."""

    try:
        return ast.literal_eval(node)
    except ValueError:
        pass
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id == "frozenset" and node.args:
            inner = ast.literal_eval(node.args[0])
            return frozenset(inner)
    raise ValueError(f"cannot literal-eval {ast.dump(node)[:120]}")


def extract_unified_literals() -> dict:
    """AST-extract module-level literals from unified_mcp.py (no import)."""

    source = (MCP_DIR / "unified_mcp.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    out: dict = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if not isinstance(target, ast.Name):
                continue
            name = target.id
            if name in {"_UNIFIED_TOOL_NAMES", "_PARAMETER_GUIDELINES"}:
                out[name] = _literal_or_container(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            if name == "MCP_NAME" and node.value is not None:
                try:
                    out[name] = ast.literal_eval(node.value)
                except ValueError:
                    pass
            if name in {"_UNIFIED_TOOL_NAMES", "_PARAMETER_GUIDELINES"} and node.value is not None:
                out[name] = _literal_or_container(node.value)
        # MCP_NAME = os.getenv(...) — fall back to the runtime default below.
    # INSTRUCTIONS is a plain string assignment.
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == "INSTRUCTIONS":
                out["INSTRUCTIONS"] = _literal_or_container(node.value)

    # Wire descriptions: every @mcp_server.tool(name=..., description=...)
    # registered directly on the unified server (proxied tools use the
    # catalog description instead).
    wire_descriptions: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            called_on_server = (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "mcp_server"
                and func.attr == "tool"
            )
            if not called_on_server:
                continue
            tool_name = None
            description = None
            for keyword in decorator.keywords:
                if keyword.arg == "name":
                    try:
                        tool_name = ast.literal_eval(keyword.value)
                    except ValueError:
                        tool_name = None
                elif keyword.arg == "description":
                    try:
                        description = ast.literal_eval(keyword.value)
                    except ValueError:
                        description = None
            if tool_name and description:
                wire_descriptions[str(tool_name)] = str(description)
    out["wire_descriptions"] = wire_descriptions
    return out


def main() -> int:
    unified = extract_unified_literals()
    tool_names = sorted(unified["_UNIFIED_TOOL_NAMES"])

    # ------------------------------------------------------------------
    # Crate data: full catalog (post module-import, i.e. with the injected
    # parser_type fanout input) + unified metadata.
    # ------------------------------------------------------------------
    dump_json(DATA_DIR / "catalog.json", tool_metadata._FULL_CATALOG)
    dump_json(
        DATA_DIR / "unified.json",
        {
            "server_name": unified.get("MCP_NAME") or "graph_mcp",
            "server_version": "1.2.0",
            "instructions": unified["INSTRUCTIONS"],
            "tool_names": tool_names,
            "parameter_guidelines": unified["_PARAMETER_GUIDELINES"],
            "wire_descriptions": unified["wire_descriptions"],
            "fanout_search_tool_names": sorted(tool_metadata.FANOUT_SEARCH_TOOL_NAMES),
            "fanout_parser_type_input": tool_metadata._FANOUT_PARSER_TYPE_INPUT,
            "android_overrides": tool_metadata.ANDROID_OVERRIDES,
        },
    )

    # ------------------------------------------------------------------
    # Crate test fixtures: what the Python modules actually serve.
    # ------------------------------------------------------------------
    served = tool_metadata.build_catalog(set(tool_names))
    dump_json(TEST_FIXTURES / "catalog_served.json", served)
    dump_json(TEST_FIXTURES / "capability_catalog.json", framework_registry.capability_catalog())
    dump_json(TEST_FIXTURES / "parser_aliases.json", sorted(framework_registry.parser_aliases()))

    relationships = {
        name: config.relationships_for()
        for name, config in sorted(framework_registry.CAPABILITIES.items())
    }
    dump_json(TEST_FIXTURES / "framework_relationships.json", relationships)

    # tool_list_parsers payloads (pure functions of framework_registry).
    full_capabilities = list(framework_registry.capability_catalog())
    summary_fields = (
        "canonical_parser",
        "aliases",
        "query_engine",
        "support_level",
        "support",
        "generation_scoped",
    )
    summary_capabilities = [
        {key: capability[key] for key in summary_fields if key in capability}
        for capability in full_capabilities
    ]
    base = {
        "parsers": sorted(framework_registry.parser_aliases()),
        "detail_level": "summary",
        "capability_contract_version": framework_registry.CAPABILITY_CONTRACT_VERSION,
        "default_query_engine": framework_registry.query_engine_for_backend("cplus"),
        "active_parser_type": None,
        "active_capability": {
            "requested_parser": None,
            "canonical_parser": None,
            "query_engine": framework_registry.query_engine_for_backend("cplus"),
            "support_level": "generic",
            "support": {
                "symbols": "generic",
                "calls": "generic",
                "endpoints": "none",
                "database": "none",
            },
        },
    }
    dump_json(
        TEST_FIXTURES / "list_parsers_summary.json",
        {**base, "capabilities": summary_capabilities},
    )
    dump_json(
        TEST_FIXTURES / "list_parsers_full.json",
        {**base, "detail_level": "full", "capabilities": full_capabilities},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
