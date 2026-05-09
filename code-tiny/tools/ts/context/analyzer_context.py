"""AnalyzerContext — shared state carried through the analysis pipeline.

Each pipeline run creates one AnalyzerContext.  Agents read from and write
to it rather than passing long argument lists.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass
class AnalyzerContext:
    """Shared mutable state for a single TypeScript analysis pipeline run."""

    # ── Run configuration ─────────────────────────────────────────────────────
    root: str
    project_id: str
    project_name: str
    language: str
    repo: str
    build_system: str = ""
    verbose: bool = False
    incremental: bool = False
    commit_sha: str = ""
    commit_sha_before: str = ""

    # ── Input file sets (populated by DependencyAgent) ────────────────────────
    all_scanned_files: List[str] = field(default_factory=list)
    selected_files: List[str] = field(default_factory=list)
    changed_set: Set[str] = field(default_factory=set)
    deleted_set: Set[str] = field(default_factory=set)
    selected_rel_paths: Set[str] = field(default_factory=set)

    # ── Per-file payloads (populated by ParserAgent / cache load) ─────────────
    # Each payload is the dict returned by _load_or_parse_payload
    selected_payloads: List[Dict[str, Any]] = field(default_factory=list)
    index_payloads: List[Dict[str, Any]] = field(default_factory=list)
    selected_payload_by_rel: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # ── Cross-file indexes (populated by SymbolAgent / GraphAgent) ────────────
    function_index_by_name: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    function_index_by_name_arity: Dict[Tuple[str, int], List[Dict[str, Any]]] = field(default_factory=dict)
    render_target_index: Dict[str, List[str]] = field(default_factory=dict)
    nav_screen_index: Dict[str, List[str]] = field(default_factory=dict)
    nav_route_index: Dict[str, List[str]] = field(default_factory=dict)
    func_role_map: Dict[str, str] = field(default_factory=dict)
    route_config_map: Dict[str, str] = field(default_factory=dict)

    # ── Graph edge collections (populated by GraphAgent) ──────────────────────
    all_projects: List[Dict[str, Any]] = field(default_factory=list)
    all_namespaces: List[Dict[str, Any]] = field(default_factory=list)
    all_files: List[Dict[str, Any]] = field(default_factory=list)
    all_types: List[Dict[str, Any]] = field(default_factory=list)
    all_functions: List[Dict[str, Any]] = field(default_factory=list)
    all_relations: List[Dict[str, Any]] = field(default_factory=list)
    all_calls: List[Dict[str, Any]] = field(default_factory=list)
    all_raw_navigates: List[Dict[str, Any]] = field(default_factory=list)
    all_navigators: List[Dict[str, Any]] = field(default_factory=list)
    all_param_lists: List[Dict[str, Any]] = field(default_factory=list)
    all_api_calls: List[Dict[str, Any]] = field(default_factory=list)

    # ── Reverse graphs (built by GraphAgent for NAVIGATE attribution) ─────────
    reverse_call_graph: Dict[str, List[str]] = field(default_factory=dict)
    reverse_renders_graph: Dict[str, List[str]] = field(default_factory=dict)

    # ── Parse statistics ─────────────────────────────────────────────────────
    parse_error_file_count: int = 0
    parse_error_node_total: int = 0
    parse_error_examples: List[str] = field(default_factory=list)

    # ── Import graph (for incremental expansion) ──────────────────────────────
    deps_by_file: Dict[str, List[str]] = field(default_factory=dict)
    impacted_by_imports_count: int = 0
