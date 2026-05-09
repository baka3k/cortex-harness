"""
flow_reconstructor.py — Unified Flow Reconstructor V1.1

Reconstructs POSSIBLE flows from candidate graph paths.

Contract (strict — matches the system prompt spec):

Input:
    entry_context: {
        "type": "backend" | "frontend" | "hybrid",
        "entry_point": str,
        "entry_node_id": str,
        "screen": str | None,
        "trigger": str | None,
    }
    paths: [
        {
            "path_id": str,
            "nodes": [{"node_id": str, "name": str, "mapped_type": str,
                        "location": {"file": str, "line": int}, ...}],
            "edges": [{"from": str, "to": str, "type": str}],
        },
        ...
    ]

Output (strict JSON):
    {
        "flows": [ ... FlowResult ... ],
        "uncertainties": [ str, ... ],
    }
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ── Enums / constants (kept as str for JSON-serializability) ─────────────────

CONFIDENCE_HIGH   = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW    = "low"

RELATION_DIRECT_EDGE       = "direct_edge"
RELATION_SAME_PATH_SEQ     = "same_path_sequence"
RELATION_INFERRED_BRIDGE   = "inferred_bridge"
RELATION_SHARED_STATE      = "shared_state"
RELATION_UNKNOWN           = "unknown"

UNCERTAINTY_LOW    = "low"
UNCERTAINTY_MEDIUM = "medium"
UNCERTAINTY_HIGH   = "high"

_FAILURE = {"flows": [], "uncertainties": ["Insufficient data to reconstruct flow"]}

# Node mapped_types that carry no semantic meaning in a flow sequence.
# These are structural/container nodes, not executable steps.
_NON_FLOW_TYPES: frozenset[str] = frozenset({
    "file", "File",
    "package", "Package",
    "chunk", "Chunk",
    "module", "Module",
    "directory", "Directory",
})


# ── Internal helpers ──────────────────────────────────────────────────────────

def _build_node_index(paths: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Global node index: node_id → node dict (first occurrence wins).
    Non-flow node types (File, Package, Chunk …) are excluded.
    """
    index: Dict[str, Dict[str, Any]] = {}
    for path in paths:
        for node in path.get("nodes", []):
            nid = node.get("node_id")
            mtype = str(node.get("mapped_type", "") or node.get("type", ""))
            if nid and nid not in index and mtype not in _NON_FLOW_TYPES:
                index[nid] = node
    return index


def _build_edge_index(
    edges: List[Dict[str, Any]],
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, List[Dict[str, Any]]]]:
    """Returns (forward adjacency, reverse adjacency) for a list of edges."""
    fwd: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    rev: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        src = edge.get("from")
        dst = edge.get("to")
        if src and dst:
            fwd[src].append(edge)
            rev[dst].append(edge)
    return dict(fwd), dict(rev)


def _has_direct_edge(from_id: str, to_id: str, fwd: Dict[str, List[Dict[str, Any]]]) -> bool:
    return any(e.get("to") == to_id for e in fwd.get(from_id, []))


def _node_line(node: Dict[str, Any]) -> int:
    loc = node.get("location") or {}
    return int(loc.get("line", 0) or 0)


def _node_file(node: Dict[str, Any]) -> str:
    loc = node.get("location") or {}
    return str(loc.get("file", "") or "")


def _topo_order(
    entry_node_id: str,
    nodes_in_path: Dict[str, Dict[str, Any]],
    fwd: Dict[str, List[Dict[str, Any]]],
) -> List[str]:
    """
    Return node_ids in traversal order starting from entry_node_id.

    Rules (per spec §STEP ORDERING):
      1. Prefer ascending location.line within the same file.
      2. Across files: follow edge direction.
      3. Nodes not reachable via edges from entry: appended at end.
    """
    ordered: List[str] = []
    visited: Set[str] = set()

    def _dfs(nid: str) -> None:
        if nid in visited or nid not in nodes_in_path:
            return
        visited.add(nid)
        ordered.append(nid)
        # Collect successors reachable via forward edges, sort by line
        successors = [
            e.get("to")
            for e in fwd.get(nid, [])
            if e.get("to") in nodes_in_path
        ]
        # Remove already-visited
        successors = [s for s in successors if s not in visited]
        # Sort: same file → ascending line; different file → keep edge order
        entry_file = _node_file(nodes_in_path[nid])
        same_file  = [s for s in successors if _node_file(nodes_in_path[s]) == entry_file]
        diff_file  = [s for s in successors if _node_file(nodes_in_path[s]) != entry_file]
        same_file.sort(key=lambda s: _node_line(nodes_in_path[s]))
        for s in same_file + diff_file:
            _dfs(s)

    _dfs(entry_node_id)

    # Append unreachable nodes (sorted by file+line for determinism)
    unreachable = [
        nid for nid in nodes_in_path
        if nid not in visited
    ]
    unreachable.sort(
        key=lambda nid: (_node_file(nodes_in_path[nid]), _node_line(nodes_in_path[nid]))
    )
    ordered.extend(unreachable)
    return ordered


def _determine_relation(
    prev_id: Optional[str],
    curr_id: str,
    fwd: Dict[str, List[Dict[str, Any]]],
    is_first: bool,
    path_node_ids: List[str],
) -> Tuple[str, str, str]:
    """
    Returns (relation, uncertainty, reason_text) for a step.
    """
    if is_first:
        return RELATION_SAME_PATH_SEQ, UNCERTAINTY_LOW, "entry node for this flow"

    if prev_id and _has_direct_edge(prev_id, curr_id, fwd):
        edge_list = [e for e in fwd.get(prev_id, []) if e.get("to") == curr_id]
        edge_type = edge_list[0].get("type", "call") if edge_list else "call"
        return RELATION_DIRECT_EDGE, UNCERTAINTY_LOW, f"{edge_type} edge from previous node"

    if prev_id and curr_id in path_node_ids and prev_id in path_node_ids:
        prev_idx = path_node_ids.index(prev_id)
        curr_idx = path_node_ids.index(curr_id)
        if curr_idx == prev_idx + 1:
            return RELATION_SAME_PATH_SEQ, UNCERTAINTY_MEDIUM, "sequential in path without direct edge"

    return RELATION_UNKNOWN, UNCERTAINTY_HIGH, "no direct edge or sequence found"


def _compute_confidence(
    steps: List[Dict[str, Any]],
    has_ordering_conflicts: bool,
) -> str:
    """
    high   → all direct_edge or same_path_seq, no ordering conflicts
    medium → some unknown/inferred, no severe conflicts
    low    → majority unknown or ordering conflicts
    """
    if not steps:
        return CONFIDENCE_LOW
    unknown_count = sum(
        1 for s in steps
        if s.get("uncertainty", UNCERTAINTY_LOW) == UNCERTAINTY_HIGH
    )
    ratio = unknown_count / len(steps)
    if ratio == 0 and not has_ordering_conflicts:
        return CONFIDENCE_HIGH
    if ratio < 0.5:
        return CONFIDENCE_MEDIUM
    return CONFIDENCE_LOW


def _generate_title(entry_context: Dict[str, Any], steps: List[Dict[str, Any]]) -> str:
    trigger = entry_context.get("trigger") or ""
    entry  = entry_context.get("entry_point") or ""
    if len(steps) <= 1:
        return f"{entry} invocation"
    last = steps[-1].get("name") or steps[-1].get("node_id") or ""
    if trigger:
        return f"{trigger} → {last}"
    return f"{entry} → {last}"


# ── Core reconstructor ────────────────────────────────────────────────────────

class FlowReconstructor:
    """
    Stateless flow reconstructor.  Call reconstruct() for each request.
    """

    def reconstruct(
        self,
        entry_context: Dict[str, Any],
        paths: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Main entry point.  Returns strict JSON matching the spec output contract.
        """
        if not paths or not entry_context:
            return _FAILURE

        entry_node_id: str = entry_context.get("entry_node_id", "")
        flow_type: str     = entry_context.get("type", "backend")

        # 1. Build global node index
        node_index = _build_node_index(paths)

        # 2. Validate entry_node_id exists in at least one path (ENTRY ANCHOR RULE)
        if not entry_node_id or entry_node_id not in node_index:
            return _FAILURE

        # 3. Partition paths
        qualifying  = [p for p in paths if any(n.get("node_id") == entry_node_id for n in p.get("nodes", []))]
        discarded   = [p for p in paths if p not in qualifying]

        if not qualifying:
            return _FAILURE

        flows: List[Dict[str, Any]] = []
        global_uncertainties: List[str] = []

        for i, path in enumerate(qualifying):
            flow_id = f"flow_{i + 1}"

            nodes_in_path: Dict[str, Dict[str, Any]] = {
                n["node_id"]: n
                for n in path.get("nodes", [])
                if n.get("node_id")
                and str(n.get("mapped_type", "") or n.get("type", "")) not in _NON_FLOW_TYPES
            }
            path_node_sequence: List[str] = [
                n["node_id"]
                for n in path.get("nodes", [])
                if n.get("node_id")
                and str(n.get("mapped_type", "") or n.get("type", "")) not in _NON_FLOW_TYPES
            ]
            raw_edges = path.get("edges", [])
            fwd, _ = _build_edge_index(raw_edges)

            # 4. Determine traversal order from entry
            ordered_ids = _topo_order(entry_node_id, nodes_in_path, fwd)

            # 5. Build steps
            steps: List[Dict[str, Any]] = []
            seen: Set[str] = set()
            prev_id: Optional[str] = None

            for j, nid in enumerate(ordered_ids):
                if nid in seen:
                    continue
                seen.add(nid)

                node = node_index[nid]
                relation, uncertainty, reason_text = _determine_relation(
                    prev_id=prev_id,
                    curr_id=nid,
                    fwd=fwd,
                    is_first=(j == 0),
                    path_node_ids=path_node_sequence,
                )

                step: Dict[str, Any] = {
                    "step_id":     f"{flow_id}_step_{len(steps) + 1}",
                    "node_id":     nid,
                    "name":        node.get("name", nid),
                    "mapped_type": node.get("mapped_type", "function"),
                    "path_ids":    [path["path_id"]],
                    "relation":    relation,
                    "reason_text": reason_text,
                    "uncertainty": uncertainty,
                }

                # Preserve optional fields from input node
                if node.get("location"):
                    step["location"] = node["location"]
                if node.get("summary"):
                    step["summary"] = node["summary"]

                steps.append(step)
                prev_id = nid

            # 6. Detect ordering conflicts (nodes not reachable from entry via edges)
            unreachable_in_steps = [
                s for s in steps
                if s["uncertainty"] == UNCERTAINTY_HIGH
            ]
            has_conflicts = len(unreachable_in_steps) > 0

            if has_conflicts:
                global_uncertainties.append(
                    f"Path {path['path_id']}: {len(unreachable_in_steps)} node(s) not"
                    " reachable from entry via declared edges"
                )

            confidence = _compute_confidence(steps, has_conflicts)

            flow: Dict[str, Any] = {
                "flow_id":          flow_id,
                "title":            _generate_title(entry_context, steps),
                "type":             flow_type,
                "confidence":       confidence,
                "entry_node_id":    entry_node_id,
                "paths_used":       [path["path_id"]],
                "discarded_paths":  [p["path_id"] for p in discarded],
                "discard_reason":   "does not contain entry_node_id" if discarded else "",
                "steps":            steps,
            }
            flows.append(flow)

        # 7. Merge flows that are identical except path_id (STEP UNIQUENESS RULE)
        flows = _merge_duplicate_flows(flows)

        # 8. Check for cross-path ordering ambiguities
        if len(flows) > 1:
            global_uncertainties.append(
                "Multiple flows reconstructed; execution path is non-deterministic"
            )

        return {
            "flows":         flows,
            "uncertainties": global_uncertainties,
        }


def _merge_duplicate_flows(flows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Merge flows whose step sequences (by node_id) are identical.
    Combines path_ids and keeps the strongest confidence.
    """
    if len(flows) <= 1:
        return flows

    _conf_rank = {CONFIDENCE_HIGH: 2, CONFIDENCE_MEDIUM: 1, CONFIDENCE_LOW: 0}

    buckets: List[Dict[str, Any]] = []
    for flow in flows:
        sig = tuple(s["node_id"] for s in flow["steps"])
        matched = next((b for b in buckets if b["_sig"] == sig), None)
        if matched is None:
            flow["_sig"] = sig
            buckets.append(flow)
        else:
            # Merge path_ids
            matched["paths_used"]    = list(set(matched["paths_used"]    + flow["paths_used"]))
            matched["discarded_paths"] = list(set(matched["discarded_paths"] + flow["discarded_paths"]))
            # Upgrade confidence
            if _conf_rank.get(flow["confidence"], 0) > _conf_rank.get(matched["confidence"], 0):
                matched["confidence"] = flow["confidence"]
            # Merge path_ids in steps
            for step_a, step_b in zip(matched["steps"], flow["steps"]):
                step_a["path_ids"] = list(set(step_a["path_ids"] + step_b["path_ids"]))

    # Strip internal sentinel key
    for b in buckets:
        b.pop("_sig", None)

    return buckets


# ── Module-level singleton ────────────────────────────────────────────────────

_reconstructor = FlowReconstructor()


def reconstruct_flows(
    entry_context: Dict[str, Any],
    paths: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Public API: reconstruct flows from entry_context + candidate paths."""
    return _reconstructor.reconstruct(entry_context, paths)
