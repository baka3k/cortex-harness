"""Map parser identities and facts into CortexHarness canonical graph rows."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any, Dict, Iterable, List, Mapping

from .models import AnalysisFacts, NodeRecord


CANONICAL_CLASS_KINDS = frozenset({"class", "mixin"})
CANONICAL_TYPE_KINDS = frozenset({"enum", "extension", "extension_type", "type_alias", "parameter"})
CANONICAL_FUNCTION_KINDS = frozenset(
    {"function", "method", "constructor", "getter", "setter", "operator"}
)


def stable_symbol_id(project_id: str, identity: str) -> str:
    """Return a project-scoped ID independent of the checkout location."""
    scope = project_id.strip()
    if not scope:
        raise ValueError("project_id must not be empty")
    digest = hashlib.sha256(f"dart\0{scope}\0{identity}".encode("utf-8")).hexdigest()[:24]
    return f"dart::{scope}::{digest}"


@dataclass
class CanonicalBatch:
    files: List[Dict[str, Any]] = field(default_factory=list)
    classes: List[Dict[str, Any]] = field(default_factory=list)
    types: List[Dict[str, Any]] = field(default_factory=list)
    functions: List[Dict[str, Any]] = field(default_factory=list)
    fields: List[Dict[str, Any]] = field(default_factory=list)
    relations: List[Dict[str, Any]] = field(default_factory=list)
    identity_to_id: Dict[str, str] = field(default_factory=dict)


def _base_row(
    node: NodeRecord,
    *,
    node_id: str,
    project_id: str,
    project_name: str,
    repo: str,
    build_system: str,
) -> Dict[str, Any]:
    properties = dict(node.properties)
    name = str(properties.get("name") or properties.get("path") or node.identity)
    qualified_name = str(properties.get("qualified_name") or name)
    code = str(properties.get("code") or "")
    comment = str(properties.get("comment") or "")
    return {
        "id": node_id,
        "name": name,
        "qualified_name": qualified_name,
        "kind": node.kind,
        "package_name": str(properties.get("package_uri") or properties.get("package_name") or ""),
        "class_name": str(properties.get("class_name") or ""),
        "scope_name": str(properties.get("scope_name") or properties.get("class_name") or ""),
        "file_path": node.evidence.file,
        "path": node.evidence.file,
        "start_byte": node.evidence.offset,
        "end_byte": node.evidence.offset + node.evidence.length,
        "start_line": node.evidence.start_line,
        "end_line": node.evidence.end_line,
        "arity": int(properties.get("arity", 0)),
        "code": code,
        "comment": comment,
        "summary": "",
        "note": str(properties.get("note") or comment or code[:4000]),
        "imports": list(properties.get("imports", [])),
        "exports": list(properties.get("exports", [])),
        "exported": bool(properties.get("exported", not name.startswith("_"))),
        "external": bool(properties.get("external", False)),
        "builtin": bool(properties.get("builtin", False)),
        "react_role": "",
        "middleware_kind": "",
        "type_signature": str(properties.get("type_signature") or ""),
        "project_id": project_id,
        "project_name": project_name,
        "language": "dart",
        "repo": repo,
        "build_system": build_system,
        "source_identity": node.identity,
        "generated": bool(properties.get("generated", False)),
    }


def normalize_facts(
    facts: AnalysisFacts,
    *,
    project_name: str | None = None,
    repo: str = "",
    build_system: str = "flutter",
    include_generated: bool = False,
) -> CanonicalBatch:
    project_id = facts.header.project_id
    batch = CanonicalBatch()
    included: List[NodeRecord] = []
    for node in facts.nodes:
        if bool(node.properties.get("generated", False)) and not include_generated:
            continue
        batch.identity_to_id[node.identity] = stable_symbol_id(project_id, node.identity)
        if bool(node.properties.get("_reference_only", False)):
            continue
        included.append(node)
    for node in included:
        row = _base_row(
            node,
            node_id=batch.identity_to_id[node.identity],
            project_id=project_id,
            project_name=project_name or project_id,
            repo=repo,
            build_system=build_system,
        )
        if node.kind == "file":
            batch.files.append(row)
        elif node.kind in CANONICAL_CLASS_KINDS:
            batch.classes.append(row)
        elif node.kind in CANONICAL_TYPE_KINDS:
            batch.types.append(row)
        elif node.kind in CANONICAL_FUNCTION_KINDS:
            batch.functions.append(row)
        elif node.kind == "field":
            batch.fields.append(row)
    for edge in facts.edges:
        source_id = batch.identity_to_id.get(edge.source)
        target_id = batch.identity_to_id.get(edge.target)
        if source_id is None or target_id is None:
            continue
        properties = dict(edge.properties)
        properties.update(
            {
                "confidence": edge.confidence,
                "source_file": edge.evidence.file,
                "source_line": edge.evidence.start_line,
                "project_id": project_id,
                "analyzer_version": facts.header.analyzer_version,
                "protocol_version": facts.header.schema_version,
            }
        )
        batch.relations.append(
            {
                "source_id": source_id,
                "target_id": target_id,
                "rel_type": edge.relationship,
                "properties": properties,
            }
        )
    for values in (batch.files, batch.classes, batch.types, batch.functions, batch.fields):
        values.sort(key=lambda row: row["id"])
    batch.relations.sort(key=lambda row: (row["rel_type"], row["source_id"], row["target_id"]))
    return batch


def qdrant_payloads(batch: CanonicalBatch) -> Iterable[Dict[str, Any]]:
    """Yield canonical symbol payloads for the existing project collection."""
    for row in (*batch.classes, *batch.types, *batch.functions, *batch.fields):
        yield {
            "symbol_id": row["id"],
            "qualified_name": row["qualified_name"],
            "name": row["name"],
            "kind": row["kind"],
            "file_path": row["file_path"],
            "start_line": row["start_line"],
            "end_line": row["end_line"],
            "code": row["code"],
            "comment": row["comment"],
            "note": row["note"],
            "project_id": row["project_id"],
            "project_name": row["project_name"],
            "language": "dart",
            "repo": row["repo"],
            "build_system": row["build_system"],
        }
