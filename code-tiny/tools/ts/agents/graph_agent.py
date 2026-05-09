"""GraphAgent — NAVIGATE resolution and screen ownership attribution.

Responsibilities:
- Build reverse call and renders graphs from collected edges.
- BFS walk up RENDERS → CALLS to find screen owners of a component/hook.
- Resolve NAVIGATE intents via 4-tier target lookup.
- Emit confirmed NAVIGATE edges with confidence scores.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple


# ─── Screen owner BFS ─────────────────────────────────────────────────────────

def _find_screen_owners(
    sid: str,
    func_role_map: Dict[str, str],
    reverse_renders_graph: Dict[str, List[str]],
    reverse_call_graph: Dict[str, List[str]],
    max_depth: int = 6,
) -> List[Tuple[str, int]]:
    """BFS up RENDERS then CALLS to find every screen on the ancestry chain.

    Ownership model:  a screen S "owns" a navigate call at node N iff there is
    a path N → … → S in the (RENDERS ∪ CALLS) reverse graph, where each edge
    means "is contained in" / "is invoked by".  Every such S is a legitimate
    workflow: while the user is on S they can trigger the UI at N which
    triggers the navigate.

    The BFS does NOT stop at the first screen ancestor — nested navigators
    (Tab / Stack / Drawer) legitimately place one screen inside another, so
    both inner and outer screens own the navigate.  `call_depth` is the BFS
    distance from N to each owner; consumers use it to distinguish direct
    owners (small depth) from outer-layer workflows (large depth).

    The function also does NOT short-circuit when `sid` itself has
    react_role=="screen".  Heuristic classification (e.g. useNavigation hook +
    screen-dir path) can misclassify a reusable component as a screen; such a
    node still has a true outer screen owner reachable via BFS.  A genuinely
    top-level screen has no screen ancestors, so BFS returns [] and the caller
    falls back to direct attribution.
    """
    visited: Set[str] = {sid}
    queue: List[Tuple[str, int]] = [(sid, 0)]
    found: List[Tuple[str, int]] = []
    while queue:
        curr, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        parents: List[str] = list(reverse_renders_graph.get(curr, []))
        for _p in reverse_call_graph.get(curr, []):
            if _p not in parents:
                parents.append(_p)
        for parent in parents:
            if parent in visited:
                continue
            visited.add(parent)
            if func_role_map.get(parent) == "screen":
                found.append((parent, depth + 1))
            queue.append((parent, depth + 1))
    return found


# ─── NAVIGATE edge resolution ─────────────────────────────────────────────────

def resolve_navigate_edges(
    all_raw_navigates: List[Dict[str, Any]],
    nav_screen_index: Dict[str, List[str]],
    nav_route_index: Dict[str, List[str]],
    route_config_map: Dict[str, str],
    func_role_map: Dict[str, str],
    reverse_call_graph: Dict[str, List[str]],
    reverse_renders_graph: Dict[str, List[str]],
) -> List[Dict[str, Any]]:
    """Resolve raw NAVIGATE intents into confirmed NAVIGATE graph edges.

    Uses a 4-tier target resolution strategy:
      Tier 1 – exact screen name match
      Tier 2 – route config map (Stack.Screen name → component name → screen)
      Tier 3 – Expo Router / Next.js file-based route path match
      Tier 4 – last-resort screen-name match with lower confidence

    Then attributes the source to a screen using BFS (Phase 4).
    Returns a list of relation dicts ready for Neo4j upsert.
    """
    _ROLE_TO_VIA: Dict[str, str] = {
        "component": "component",
        "hook":      "hook",
        "":          "wrapped",
    }
    _emitted_nav: Set[Tuple[str, str]] = set()
    edges: List[Dict[str, Any]] = []

    for nav in all_raw_navigates:
        source_id = nav.get("source_id", "")
        target_name = nav.get("target_name", "")
        method = nav.get("nav_method", "navigate")
        trigger_type = nav.get("trigger_type", "user") or "user"
        guard = nav.get("guard") or ""

        # ── Phase 3: Target resolution (4-tier) ──────────────────────────────
        target_id: Optional[str] = None
        target_confidence: float = 0.0

        t1 = nav_screen_index.get(target_name) or []
        if t1:
            target_id = t1[0]
            target_confidence = 1.0 if len(t1) == 1 else 0.7

        if not target_id:
            comp_name = route_config_map.get(target_name)
            if comp_name:
                t2 = nav_screen_index.get(comp_name) or []
                if t2:
                    target_id = t2[0]
                    target_confidence = 0.9 if len(t2) == 1 else 0.65

        if not target_id and "/" in target_name:
            normalized = target_name.rstrip("/")
            t3 = (
                nav_route_index.get(normalized)
                or nav_route_index.get(normalized.lstrip("/"))
                or []
            )
            if t3:
                target_id = t3[0]
                target_confidence = 0.85 if len(t3) == 1 else 0.6

        if not target_id:
            t4 = nav_screen_index.get(target_name) or []
            if t4:
                target_id = t4[0]
                target_confidence = 0.5 if len(t4) == 1 else 0.3

        if not target_id:
            continue

        # ── Phase 4: Source attribution → emit one edge per screen owner ─────
        # Always BFS first regardless of source_role: heuristic classification
        # can mis-promote a reusable component to "screen".  If BFS finds screen
        # ancestors we use them; only when there are none AND the source itself
        # is a screen do we fall back to direct self-attribution.
        source_role = func_role_map.get(source_id, "")
        screen_owners = _find_screen_owners(
            source_id, func_role_map, reverse_renders_graph, reverse_call_graph
        )
        if not screen_owners:
            if source_role == "screen":
                screen_owners = [(source_id, 0)]
            else:
                continue

        # No owner-count penalty and no cap: multiple owners is the expected
        # shape of nested navigators, not an ambiguity signal.  Confidence
        # decays with call_depth only.  Consumers filter by confidence / depth.
        for screen_id, call_depth in screen_owners:
            pair = (screen_id, target_id)
            if pair in _emitted_nav:
                continue
            _emitted_nav.add(pair)
            edge_via = (
                "direct" if call_depth == 0
                else _ROLE_TO_VIA.get(source_role, "wrapped")
            )
            call_path_score = max(0.5, 1.0 - 0.15 * call_depth)
            confidence = round(target_confidence * call_path_score, 3)
            edges.append({
                "source_id": screen_id,
                "target_id": target_id,
                "rel_type": "NAVIGATE",
                "properties": {
                    "method": method,
                    "target": target_name,
                    "via": edge_via,
                    "trigger_type": trigger_type,
                    "guard": guard,
                    "call_depth": call_depth,
                    "confidence": confidence,
                    },
                })

    return edges


# ─── GraphAgent class facade ──────────────────────────────────────────────────

class GraphAgent:
    """Object-oriented facade over the module-level graph resolution functions."""

    def resolve_navigate_edges(
        self,
        all_raw_navigates: List[Dict[str, Any]],
        nav_screen_index: Dict[str, List[str]],
        nav_route_index: Dict[str, List[str]],
        route_config_map: Dict[str, str],
        func_role_map: Dict[str, str],
        reverse_call_graph: Dict[str, List[str]],
        reverse_renders_graph: Dict[str, List[str]],
    ) -> List[Dict[str, Any]]:
        return resolve_navigate_edges(
            all_raw_navigates,
            nav_screen_index,
            nav_route_index,
            route_config_map,
            func_role_map,
            reverse_call_graph,
            reverse_renders_graph,
        )

    def find_screen_owners(
        self,
        sid: str,
        func_role_map: Dict[str, str],
        reverse_renders_graph: Dict[str, List[str]],
        reverse_call_graph: Dict[str, List[str]],
        max_depth: int = 4,
    ) -> List[Tuple[str, int]]:
        return _find_screen_owners(
            sid, func_role_map, reverse_renders_graph, reverse_call_graph, max_depth
        )
