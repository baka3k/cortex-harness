"""FrontendPipeline — orchestrates the TypeScript/React frontend analysis pipeline.

High-level flow:
  1. ParserAgent   — scan files, parse ASTs, extract low-level structural data
  2. DependencyAgent — build import graph, compute incremental impact set
  3. TraversalAgent  — walk each AST, collect functions/calls/renders/navigates
  4. SymbolAgent     — classify symbols (react_role, middleware, API calls, navigation)
  5. GraphAgent      — resolve NAVIGATE targets, emit confirmed NAVIGATE edges

This class is a thin orchestration shell.  The heavy lifting is delegated to
each agent.  ``build_call_graph`` in ``ts_analyzer.py`` remains the production
entry-point; this pipeline is a re-usable alternative interface.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from tools.ts.agents.parser_agent import ParserAgent
from tools.ts.agents.dependency_agent import DependencyAgent
from tools.ts.agents.symbol_agent import SymbolAgent
from tools.ts.agents.traversal_agent import TraversalAgent
from tools.ts.agents.graph_agent import GraphAgent


class FrontendPipeline:
    """Wire ParserAgent → DependencyAgent → TraversalAgent → SymbolAgent → GraphAgent."""

    def __init__(self) -> None:
        self.parser = ParserAgent()
        self.dependency = DependencyAgent()
        self.symbol = SymbolAgent()
        self.traversal = TraversalAgent()
        self.graph = GraphAgent()

    # ── Incremental helpers ───────────────────────────────────────────────────

    def compute_selected_files(
        self,
        all_scanned_files: List[str],
        root: str,
        incremental: bool,
        changed_files: Optional[Iterable[str]] = None,
        deleted_files: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        """Return a dict with selected_files, selected_rel_paths, impacted counts."""
        import os

        all_rel_paths = [
            os.path.relpath(p, root).replace("\\", "/") for p in all_scanned_files
        ]
        rel_to_abs = {
            os.path.relpath(p, root).replace("\\", "/"): p for p in all_scanned_files
        }
        changed_set = {
            item.replace("\\", "/") for item in (changed_files or []) if item
        }
        deleted_set = {
            item.replace("\\", "/") for item in (deleted_files or []) if item
        }

        impacted_by_imports_count = 0
        if incremental:
            changed_existing = {p for p in changed_set if p in rel_to_abs}
            deps_by_file = self.dependency.collect_ts_import_graph(
                all_scanned_files, root
            )
            impacted = self.dependency.expand_impacted_files_by_imports(
                changed_existing, deps_by_file
            )
            selected_rel_paths: set[str] = changed_existing | impacted
            impacted_by_imports_count = len(impacted)
            selected_files = [
                rel_to_abs[p] for p in all_rel_paths if p in selected_rel_paths
            ]
        else:
            selected_rel_paths = set(all_rel_paths)
            selected_files = list(all_scanned_files)
            deps_by_file: Dict[str, List[str]] = {}

        return {
            "all_rel_paths": all_rel_paths,
            "rel_to_abs": rel_to_abs,
            "changed_set": changed_set,
            "deleted_set": deleted_set,
            "selected_rel_paths": selected_rel_paths,
            "selected_files": selected_files,
            "deps_by_file": deps_by_file,
            "impacted_by_imports_count": impacted_by_imports_count,
        }

    # ── Navigate resolution ───────────────────────────────────────────────────

    def resolve_navigate_edges(
        self,
        all_raw_navigates: List[Dict[str, Any]],
        all_calls: List[Dict[str, Any]],
        all_relations: List[Dict[str, Any]],
        nav_screen_index: Dict[str, List[str]],
        nav_route_index: Dict[str, List[str]],
        route_config_map: Dict[str, str],
        func_role_map: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        """Build reverse graphs then delegate to GraphAgent for NAVIGATE resolution."""
        reverse_call_graph: Dict[str, List[str]] = {}
        for c in all_calls:
            cee = c.get("callee_id")
            cer = c.get("caller_id")
            if cee and cer:
                reverse_call_graph.setdefault(cee, []).append(cer)

        reverse_renders_graph: Dict[str, List[str]] = {}
        for rel in all_relations:
            if rel.get("rel_type") == "RENDERS":
                src = rel.get("source_id")
                tgt = rel.get("target_id")
                if src and tgt:
                    reverse_renders_graph.setdefault(tgt, []).append(src)

        return self.graph.resolve_navigate_edges(
            all_raw_navigates,
            nav_screen_index,
            nav_route_index,
            route_config_map,
            func_role_map,
            reverse_call_graph,
            reverse_renders_graph,
        )
