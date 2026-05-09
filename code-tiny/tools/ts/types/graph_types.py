"""Graph-level data types written to the Neo4j database.

These dataclasses represent nodes and edges at the graph layer:
files, namespaces, types, functions-as-graph-nodes, and relations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class FileDef:
    file_path: str
    start_line: int
    end_line: int
    code: str
    comment: str = ""
    summary: str = ""
    note: str = ""
    imports: List[str] = field(default_factory=list)
    exports: List[str] = field(default_factory=list)
    jsx_tags: List[str] = field(default_factory=list)
    jsx_components: List[str] = field(default_factory=list)


@dataclass
class NamespaceDef:
    symbol_id: str
    qualified_name: str
    name: str
    file_path: str
    start_line: int
    end_line: int
    code: str
    comment: str = ""
    summary: str = ""
    note: str = ""


@dataclass
class TypeDef:
    symbol_id: str
    qualified_name: str
    name: str
    kind: str
    file_path: str
    start_line: int
    end_line: int
    code: str
    comment: str = ""
    summary: str = ""
    note: str = ""
    exported: bool = False


@dataclass
class RelationEdge:
    source_id: str
    source_label: str
    target_id: str
    target_label: str
    rel_type: str
    properties: Dict[str, str]


@dataclass
class CallEdge:
    caller_id: str
    caller_scope: Optional[str]
    callee_name: str
    callee_id: Optional[str]
    callee_arity: Optional[int]
