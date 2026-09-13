#!/usr/bin/env python3
"""Sinh fixture rows cho dual-write parity gate của phase 03.

Fixture deterministic, dẫn xuất từ đường dẫn/file thật của repo (stock subset
theo tinh thần gate): file paths, function names, relations lấy từ cây
`rust/crates` + `code-tiny/tools`. Cả writer Python và writer Rust đọc CÙNG
fixture này → ghi vào 2 graph riêng → diff.

Output: rust/crates/cortex-graph-writer/tests/fixtures/writer_rows.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "rust" / "crates" / "cortex-graph-writer" / "tests" / "fixtures"

PROJECT_ID = "stock"
PROJECT_KEY = "stock"
REPO_NAME = "cortex-harness"

# ── File subset thật từ repo ────────────────────────────────────────────────
_FILE_SOURCES_TESTDATA = [
    ("testdata/README.md", "markdown"),
    ("scripts/rust_parity/dual_write_diff.py", "python"),
    ("scripts/rust_parity/gen_writer_rows_fixture.py", "python"),
    ("docs/architecture.md", "markdown"),
    ("cortex_harness/storage/lease.py", "python"),
    ("cortex_harness/storage/layout.py", "python"),
]

_FILE_SOURCES = [
    ("rust/crates/cortex-graph-core/src/lib.rs", "rust"),
    ("rust/crates/cortex-graph-core/src/journal.rs", "rust"),
    ("rust/crates/cortex-graph-core/src/journal_manifest.rs", "rust"),
    ("rust/crates/cortex-falkordb/src/client.rs", "rust"),
    ("rust/crates/cortex-graph-writer/src/lib.rs", "rust"),
    ("rust/crates/cortex-graph-writer/src/query_contract.rs", "rust"),
    ("rust/crates/cortex-graph-writer/src/language_writer.rs", "rust"),
    ("rust/crates/cortex-graph-writer/src/store/ladybug_store.rs", "rust"),
    ("code-tiny/tools/graph/writer/language_writer.py", "python"),
    ("code-tiny/tools/graph/writer/query_contract.py", "python"),
    ("code-tiny/tools/graph/driver/ladybug_driver.py", "python"),
    ("code-tiny/tools/graph/operations/class_ops.py", "python"),
]

_FILES = []
for index, (path, language) in enumerate(_FILE_SOURCES):
    _FILES.append(
        {
            "id": path,
            "path": path,
            "start_line": 1,
            "end_line": 120 + index * 7,
            "code": f"// source: {path}",
            "comment": "",
            "summary": f"Module {path.rsplit('/', 1)[-1]}",
            "note": "",
            "project_id": PROJECT_ID,
            "project_id_normalized": PROJECT_KEY,
            "project_name": "cortex-harness",
            "language": language,
            "repo": REPO_NAME if index % 2 == 0 else "",
            "build_system": "cargo" if language == "rust" else "pip",
        }
    )

# ── Packages / namespaces ───────────────────────────────────────────────────
_PACKAGES = [
    {
        "id": f"pkg::{name}",
        "name": name,
        "start_line": 1,
        "end_line": 40,
        "code": f"mod {name};",
        "comment": "",
        "summary": "",
        "note": "",
        "project_id": PROJECT_ID,
        "project_id_normalized": PROJECT_KEY,
        "project_name": "cortex-harness",
        "language": "rust",
        "repo": REPO_NAME,
        "build_system": "cargo",
    }
    for name in ("cortex-graph-core", "cortex-falkordb", "cortex-graph-writer")
]
_NAMESPACES = [
    {
        "id": f"ns::tools.graph.{name}",
        "name": name,
        "qualified_name": f"tools.graph.{name}",
        "file_path": f"code-tiny/tools/graph/{name}/__init__.py",
        "start_line": 1,
        "end_line": 10,
        "code": "",
        "comment": "",
        "summary": "",
        "note": "",
        "project_id": PROJECT_ID,
        "project_id_normalized": PROJECT_KEY,
        "project_name": "cortex-harness",
        "language": "python",
        "repo": REPO_NAME,
        "build_system": "pip",
    }
    for name in ("writer", "operations", "driver")
]

# ── Classes ─────────────────────────────────────────────────────────────────
_CLASSES = [
    {
        "id": f"class:{path}::{name}",
        "name": name,
        "node_type": "code",
        "qualified_name": f"{path}::{name}",
        "kind": kind,
        "package_name": "cortex",
        "file_path": path,
        "start_line": start,
        "end_line": start + 30,
        "code": "",
        "comment": "",
        "summary": "",
        "note": "",
        "visibility": visibility,
        "is_public_api": is_public,
        "visibility_source": "explicit",
        "export_evidence": "",
        "signature": "",
        "project_id": PROJECT_ID,
        "project_id_normalized": PROJECT_KEY,
        "project_name": "cortex-harness",
        "language": language,
        "repo": REPO_NAME,
        "build_system": "cargo" if language == "rust" else "pip",
    }
    for path, name, kind, start, language, visibility, is_public in [
        ("rust/crates/cortex-graph-writer/src/query_contract.rs", "RelationshipGroup", "struct", 40, "rust", "public", True),
        ("rust/crates/cortex-graph-writer/src/query_contract.rs", "EvidenceEdgeGroup", "struct", 90, "rust", "public", True),
        ("rust/crates/cortex-graph-writer/src/language_writer.rs", "LanguageCodeWriter", "struct", 20, "rust", "pub(crate)", False),
        ("code-tiny/tools/graph/writer/language_writer.py", "LanguageCodeWriter", "class", 79, "python", "public", True),
        ("code-tiny/tools/graph/operations/class_ops.py", "ClassNodeOperations", "class", 18, "python", "public", True),
        # visibility vắng mặt để exercise coalesce(row.visibility, 'unknown')
        ("code-tiny/tools/graph/driver/ladybug_driver.py", "LadybugDriver", "class", 279, "python", "", False),
    ]
]

# ── Types (2 passes với file_path khác nhau cho CASE min-lexicographic) ────
_TYPES_BASE = [
    ("type:signal_kind", "SignalKind", "enum", "rust/crates/cortex-retrieval/src/signal_normalize.rs"),
    ("type:writer_error", "WriterError", "enum", "rust/crates/cortex-graph-writer/src/language_writer.rs"),
    ("type:store_error", "StoreError", "enum", "rust/crates/cortex-graph-writer/src/store/mod.rs"),
    ("type:param", "Param", "enum", "rust/crates/cortex-falkordb/src/client.rs"),
]


def _type_row(type_id: str, name: str, kind: str, file_path: str, exported: bool) -> dict:
    return {
        "id": type_id,
        "name": name,
        "qualified_name": f"{file_path}::{name}",
        "kind": kind,
        "file_path": file_path,
        "start_line": 10,
        "end_line": 50,
        "code": "",
        "comment": "",
        "summary": "",
        "note": "",
        "exported": exported,
        "project_id": PROJECT_ID,
        "project_id_normalized": PROJECT_KEY,
        "project_name": "cortex-harness",
        "language": "rust",
        "repo": REPO_NAME,
        "build_system": "cargo",
    }


_TYPES_PASS1 = [_type_row(tid, name, kind, fp, True) for tid, name, kind, fp in _TYPES_BASE]
# Pass 2: writer_error xuất hiện lại với file_path LEXICOGRAPHIC LỚN HƠN —
# CASE phải giữ file_path cũ; param với file_path NHỎ HƠN — CASE phải đổi.
_TYPES_PASS2 = [
    _type_row("type:writer_error", "WriterError", "enum", "rust/crates/cortex-graph-writer/src/zzz_alt.rs", False),
    _type_row("type:param", "Param", "enum", "rust/crates/cortex-falkordb/src/aaa_alt.rs", True),
]

# ── Functions ───────────────────────────────────────────────────────────────
_FUNCTIONS = []
_fn_specs = [
    ("rust/crates/cortex-graph-writer/src/query_contract.rs", "group_typed_relations", "group_typed_relations", "public", True),
    ("rust/crates/cortex-graph-writer/src/query_contract.rs", "compile_relationship_upsert", "compile_relationship_upsert", "public", True),
    ("rust/crates/cortex-graph-writer/src/query_contract.rs", "group_evidence_edges", "group_evidence_edges", "public", True),
    ("rust/crates/cortex-graph-writer/src/language_writer.rs", "write_batches", "LanguageCodeWriter::write_batches", "method", False),
    ("rust/crates/cortex-graph-writer/src/language_writer.rs", "write_relations_typed", "LanguageCodeWriter::write_relations_typed", "method", False),
    ("rust/crates/cortex-graph-writer/src/language_writer.rs", "write_evidence_edges", "LanguageCodeWriter::write_evidence_edges", "method", False),
    ("rust/crates/cortex-graph-writer/src/language_writer.rs", "write_calls", "LanguageCodeWriter::write_calls", "method", False),
    ("rust/crates/cortex-graph-writer/src/store/ladybug_store.rs", "auto_ddl_statement", "LadybugStore::auto_ddl_statement", "method", False),
    ("rust/crates/cortex-falkordb/src/client.rs", "stringify_param_value", "stringify_param_value", "public", True),
    ("code-tiny/tools/graph/writer/language_writer.py", "write_all", "LanguageCodeWriter.write_all", "method", False),
    ("code-tiny/tools/graph/writer/query_contract.py", "group_typed_relations", "group_typed_relations", "function", True),
    ("code-tiny/tools/graph/driver/ladybug_driver.py", "_auto_ddl_statement", "LadybugDriver._auto_ddl_statement", "method", False),
    ("code-tiny/tools/graph/operations/class_ops.py", "batch_create_classes", "ClassNodeOperations.batch_create_classes", "method", False),
]
for index, (path, name, qname, visibility, exported) in enumerate(_fn_specs):
    _FUNCTIONS.append(
        {
            "id": f"fn:{path}::{name}",
            "name": name,
            "node_type": "code",
            "qualified_name": qname,
            "kind": "function",
            "class_name": "",
            "package_name": "cortex",
            "scope_name": "",
            "file_path": path,
            "start_byte": index * 100,
            "end_byte": index * 100 + 90,
            "start_line": 10 + index,
            "end_line": 20 + index,
            "arity": 2,
            "code": "",
            "comment": "",
            "summary": f"fn {name}",
            "note": "",
            "exported": exported,
            "visibility": visibility,
            "is_public_api": exported,
            "visibility_source": "tree_sitter",
            "export_evidence": "",
            "signature": "",
            "external": False,
            "builtin": False,
            "react_role": "",
            "middleware_kind": "",
            "project_id": PROJECT_ID,
            "project_id_normalized": PROJECT_KEY,
            "project_name": "cortex-harness",
            "language": "rust" if path.endswith(".rs") else "python",
            "repo": REPO_NAME,
            "build_system": "cargo" if path.endswith(".rs") else "pip",
        }
    )

# ── Calls (có duplicates để exercise dedupe) ───────────────────────────────
_CALLS = [
    {"caller_id": "fn:rust/crates/cortex-graph-writer/src/language_writer.rs::write_relations_typed",
     "callee_id": "fn:rust/crates/cortex-graph-writer/src/query_contract.rs::group_typed_relations",
     "count": 1, "call_type": "direct", "project_id": PROJECT_ID},
    # duplicate — write_calls phải collapse count = 2
    {"caller_id": "fn:rust/crates/cortex-graph-writer/src/language_writer.rs::write_relations_typed",
     "callee_id": "fn:rust/crates/cortex-graph-writer/src/query_contract.rs::group_typed_relations",
     "count": 1, "call_type": "direct", "project_id": PROJECT_ID},
    {"caller_id": "fn:rust/crates/cortex-graph-writer/src/language_writer.rs::write_relations_typed",
     "callee_id": "fn:rust/crates/cortex-graph-writer/src/query_contract.rs::compile_relationship_upsert",
     "count": 3, "call_type": "direct", "project_id": PROJECT_ID},
    {"caller_id": "fn:code-tiny/tools/graph/writer/language_writer.py::write_all",
     "callee_id": "fn:code-tiny/tools/graph/writer/query_contract.py::group_typed_relations",
     "count": 1, "call_type": "direct", "project_id": PROJECT_ID},
]

# ── Typed relations (label-qualified) ───────────────────────────────────────
_RELATIONS = [
    {
        "source_label": "Class", "target_label": "Class", "rel_type": "IMPLEMENTS",
        "source_id": "class:rust/crates/cortex-graph-writer/src/query_contract.rs::RelationshipGroup",
        "target_id": "class:rust/crates/cortex-graph-writer/src/query_contract.rs::EvidenceEdgeGroup",
        "properties": {"kind": "structural"},
    },
    {
        "source_label": "Function", "target_label": "Type", "rel_type": "USES_TYPE",
        "source_id": "fn:rust/crates/cortex-graph-writer/src/language_writer.rs::write_batches",
        "target_id": "type:writer_error",
        "properties": {"resolved": True},
    },
    # (Project)-[:CONTAINS]-> phải bị strip ở write_relations_typed/write_all
    {
        "source_label": "Project", "target_label": "File", "rel_type": "CONTAINS",
        "source_id": PROJECT_ID,
        "target_id": _FILES[0]["id"],
        "properties": {},
    },
]

# ── Evidence plane ──────────────────────────────────────────────────────────
_CALL_SITES = [
    {
        "site_id": "site:lang_writer.rs:120:5",
        "caller_id": "fn:rust/crates/cortex-graph-writer/src/language_writer.rs::write_batches",
        "callee_id": "fn:rust/crates/cortex-graph-writer/src/query_contract.rs::group_typed_relations",
        "file_path": "rust/crates/cortex-graph-writer/src/language_writer.rs",
        "start_line": 120,
        "resolution_class": "lexical_candidate",
        "observation_count": 2,
        "project_id": PROJECT_ID,
        "props": {
            "file_path": "rust/crates/cortex-graph-writer/src/language_writer.rs",
            "start_line": 120,
            "resolution_class": "lexical_candidate",
            "observation_count": 2,
            "project_id": PROJECT_ID,
            "project_id_normalized": PROJECT_KEY,
        },
    },
    {
        "site_id": "site:lang_writer.rs:305:9",
        "caller_id": "fn:rust/crates/cortex-graph-writer/src/language_writer.rs::write_evidence_edges",
        "callee_id": "fn:rust/crates/cortex-graph-writer/src/query_contract.rs::compile_relationship_upsert",
        "file_path": "rust/crates/cortex-graph-writer/src/language_writer.rs",
        "start_line": 305,
        "resolution_class": "lexical_candidate",
        "observation_count": 1,
        "project_id": PROJECT_ID,
        "props": {
            "file_path": "rust/crates/cortex-graph-writer/src/language_writer.rs",
            "start_line": 305,
            "resolution_class": "lexical_candidate",
            "observation_count": 1,
            "project_id": PROJECT_ID,
            "project_id_normalized": PROJECT_KEY,
        },
    },
]
_CALL_OBSERVATIONS = [
    {
        "site_id": "site:lang_writer.rs:120:5",
        "callee_id": "fn:rust/crates/cortex-graph-writer/src/query_contract.rs::group_typed_relations",
        "evidence_id": "evidence:120:5:1",
        "provider": "tree_sitter",
        "resolution_class": "lexical_candidate",
        "confidence": 0.8,
        "dangling": False,
        "project_id": PROJECT_ID,
    },
    {
        "site_id": "site:lang_writer.rs:120:5",
        "callee_id": "",
        "evidence_id": "evidence:120:5:2",
        "dangling": True,
        "project_id": PROJECT_ID,
    },
    {
        "site_id": "site:lang_writer.rs:305:9",
        "callee_id": "fn:rust/crates/cortex-graph-writer/src/query_contract.rs::compile_relationship_upsert",
        "evidence_id": "evidence:305:9:1",
        "provider": "tree_sitter",
        "resolution_class": "lexical_candidate",
        "confidence": 0.6,
        "dangling": False,
        "project_id": PROJECT_ID,
    },
]
_BUILD_CONFIGURATIONS = [
    {
        "site_id": "site:lang_writer.rs:120:5",
        "config_fingerprint": "cfg:debug-aarch64",
        "project_id": PROJECT_ID,
        "props": {"compiler": "rustc", "opt": "debug", "project_id": PROJECT_ID,
                  "project_id_normalized": PROJECT_KEY},
    },
    {
        "site_id": "site:lang_writer.rs:120:5",
        "config_fingerprint": "cfg:release-aarch64",
        "project_id": PROJECT_ID,
        "props": {"compiler": "rustc", "opt": "release", "project_id": PROJECT_ID,
                  "project_id_normalized": PROJECT_KEY},
    },
]
_SEMANTIC_COVERAGE = [
    {
        "fingerprint": "cov:writer-plane:partial",
        "status": "partial",
        "tu_count": 13,
        "project_id": PROJECT_ID,
        "props": {"status": "partial", "tu_count": 13,
                  "project_id": PROJECT_ID, "project_id_normalized": PROJECT_KEY},
    },
]
_PROC_FUNCTION_JOINS = [
    {
        "function_id": "fn:rust/crates/cortex-graph-writer/src/language_writer.rs::write_calls",
        "statement_id": "sql:select-candidates",
        "join_quality": "exact",
        "project_id": PROJECT_ID,
    },
]
_PROC_HOST_DECLARATIONS = [
    {
        "host_variable_id": "host:paths",
        "declaration_id": "fn:rust/crates/cortex-graph-writer/src/language_writer.rs::write_batches",
        "declaration_kind": "function",
        "project_id": PROJECT_ID,
    },
    {
        "host_variable_id": "host:unresolved",
        "declaration_id": "",
        "declaration_kind": "variable",
        "project_id": PROJECT_ID,
    },
]

# ── Navigators / workflows ──────────────────────────────────────────────────
_NAVIGATORS = [
    {
        "id": "nav:root-stack",
        "var_name": "RootStack",
        "nav_type": "stack",
        "factory": "createStackNavigator",
        "param_list_ref": "param:root-stack-params",
        "file_path": "rust/crates/cortex-graph-writer/src/language_writer.rs",
        "start_line": 200,
        "project_id": PROJECT_ID,
        "project_name": "cortex-harness",
    },
]
_HAS_ROUTES = [
    {
        "navigator_id": "nav:root-stack",
        "screen_id": "fn:rust/crates/cortex-graph-writer/src/language_writer.rs::write_calls",
        "route_name": "Calls",
        "param_schema": "{}",
    },
]
_PARAM_LISTS = [
    {
        "symbol_id": "nav:root-stack",
        "name": "RootStackParams",
        "route_name": "Calls",
        "type_str": "{ count: number }",
        "file_path": "rust/crates/cortex-graph-writer/src/language_writer.rs",
        "project_id": PROJECT_ID,
    },
]
_WORKFLOWS = [
    {
        "workflow_id": "wf:write-entities",
        "workflow_name": "write entities",
        "domain": "ingest",
        "description": "Writer pipeline main flow",
        "confidence": 0.9,
        "entrypoint_id": "fn:rust/crates/cortex-graph-writer/src/language_writer.rs::write_all",
        "language": "rust",
        "project": PROJECT_ID,
        "kind": "pipeline",
    },
]
_WORKFLOW_STEPS = [
    {"workflow_id": "wf:write-entities",
     "function_id": "fn:rust/crates/cortex-graph-writer/src/language_writer.rs::write_batches",
     "step_order": 1},
    {"workflow_id": "wf:write-entities",
     "function_id": "fn:rust/crates/cortex-graph-writer/src/language_writer.rs::write_relations_typed",
     "step_order": 2},
]

# ── Topology result (TopologyAnalysisResult.to_dict() shape) ───────────────
def _module(mod_id: str, module_path: str, name: str, kind: str, build_systems: list) -> dict:
    # Full ModuleFact.to_dict() shape.
    return {
        "id": mod_id, "project_id": PROJECT_ID, "module_path": module_path,
        "name": name, "kind": kind, "languages": ["rust"], "frameworks": [],
        "build_systems": build_systems, "source_roots": [], "descriptor_ids": [],
        "confidence": "high", "diagnostics": [], "properties": {},
    }


_TOPOLOGY = {
    "project_id": PROJECT_ID,
    "root": ".",
    "modules": [
        _module("mod:rust", "rust", "rust", "cargo_workspace", ["cargo"]),
        _module("mod:code-tiny", "code-tiny", "code-tiny", "python_package", ["pip"]),
    ],
    "descriptors": [
        {   # DescriptorFact.to_dict() shape
            "id": "desc:cargo-writer", "project_id": PROJECT_ID,
            "module_path": "rust", "path": "rust/crates/cortex-graph-writer/Cargo.toml",
            "descriptor_type": "cargo", "role": "primary", "parser": "cargo",
            "parse_depth": "full", "parser_version": "1", "canonical": True,
            "generated": False, "secret_bearing": False, "redacted": False,
            "summary": "", "properties": {}, "confidence": "high",
            "evidence": [], "diagnostics": [],
        },
        {
            "id": "desc:pyproject", "project_id": PROJECT_ID,
            "module_path": "code-tiny", "path": "code-tiny/pyproject.toml",
            "descriptor_type": "pyproject", "role": "primary", "parser": "pip",
            "parse_depth": "full", "parser_version": "1", "canonical": True,
            "generated": False, "secret_bearing": False, "redacted": False,
            "summary": "", "properties": {}, "confidence": "high",
            "evidence": [], "diagnostics": [],
        },
    ],
    "dependencies": [
        {   # DependencyFact.to_dict() shape
            "id": "dep:cortex-falkordb", "project_id": PROJECT_ID,
            "source_module_path": "rust", "target": "cortex-falkordb",
            "scope": "normal", "internal": False, "target_module_path": None,
            "source": "", "confidence": "high", "evidence": [], "properties": {},
        },
        {
            "id": "dep:cortex-graph-writer", "project_id": PROJECT_ID,
            "source_module_path": "rust", "target": "cortex-graph-writer",
            "scope": "normal", "internal": True, "target_module_path": "rust",
            "source": "", "confidence": "high", "evidence": [], "properties": {},
        },
    ],
    "public_apis": [],
    "endpoints": [],
    "special_files": [],
    "frameworks": [
        {   # FrameworkInstanceFact.to_dict() shape
            "id": "fw:tokio", "project_id": PROJECT_ID, "module_id": "mod:rust",
            "framework": "tokio", "version": "1", "confidence": "medium",
            "evidence": [], "dimensions": {}, "facts": {}, "diagnostics": [],
        },
    ],
    "diagnostics": [],
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=["stock", "testdata"], default="stock")
    args = parser.parse_args()
    if args.variant == "testdata":
        apply_variant_testdata()
    fixture = {
        "project": {
            "id": PROJECT_ID,
            "name": "cortex-harness",
            "language": "rust",
            "repo": REPO_NAME,
            "root": ".",
            "build_system": "cargo",
        },
        "repository_setup": {
            "project_id": PROJECT_ID,
            "project_name": "cortex-harness",
            "project_slug": "cortex-harness",
            "repository_name": REPO_NAME,
        },
        "packages": _PACKAGES,
        "namespaces": _NAMESPACES,
        "files": _FILES,
        "classes": _CLASSES,
        "types_pass1": _TYPES_PASS1,
        "types_pass2": _TYPES_PASS2,
        "functions": _FUNCTIONS,
        "relations": _RELATIONS,
        "calls": _CALLS,
        "calls_with_site": [],
        "call_evidence_sites": _CALL_SITES,
        "call_evidence_observations": _CALL_OBSERVATIONS,
        "build_configurations": _BUILD_CONFIGURATIONS,
        "semantic_coverage": _SEMANTIC_COVERAGE,
        "proc_function_joins": _PROC_FUNCTION_JOINS,
        "proc_host_declarations": _PROC_HOST_DECLARATIONS,
        "navigators": _NAVIGATORS,
        "has_routes": _HAS_ROUTES,
        "param_lists": _PARAM_LISTS,
        "workflows": _WORKFLOWS,
        "workflow_steps": _WORKFLOW_STEPS,
        "topology": _TOPOLOGY,
    }
    FIXTURES.mkdir(parents=True, exist_ok=True)
    suffix = "" if args.variant == "stock" else f"_{args.variant}"
    out = FIXTURES / f"writer_rows{suffix}.json"
    out.write_text(json.dumps(fixture, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out.relative_to(REPO)}")


def apply_variant_testdata() -> None:
    """Bộ input thứ 2 cho gate (≥2 bộ): testdata/docs/scripts subset."""
    global PROJECT_ID, PROJECT_KEY, _FILE_SOURCES, _FILES
    PROJECT_ID = "testdata"
    PROJECT_KEY = "testdata"
    _FILE_SOURCES[:] = _FILE_SOURCES_TESTDATA
    _FILES.clear()
    for index, (path, language) in enumerate(_FILE_SOURCES):
        _FILES.append(
            {
                "id": path,
                "path": path,
                "start_line": 1,
                "end_line": 30 + index * 5,
                "code": f"# source: {path}",
                "comment": "",
                "summary": f"Doc {path.rsplit('/', 1)[-1]}",
                "note": "",
                "project_id": PROJECT_ID,
                "project_id_normalized": PROJECT_KEY,
                "project_name": "cortex-harness-testdata",
                "language": language,
                "repo": REPO_NAME,
                "build_system": "none",
            }
        )


if __name__ == "__main__":
    sys.exit(main())
