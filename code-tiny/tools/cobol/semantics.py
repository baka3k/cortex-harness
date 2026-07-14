"""Normalize resolved COBOL facts into the graph contract."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from .cfg import build_cfg
from .models import (
    Diagnostic,
    SemanticEdge,
    SemanticNode,
    SourceEvidence,
    stable_id,
)
from .resolver import ResolvedProject


def _data_access(statement_text: str, name: str) -> str:
    upper = " ".join(statement_text.upper().split())
    escaped = re.escape(name)
    if re.search(rf"\b(?:ADD|SUBTRACT|MULTIPLY|DIVIDE)\b.*\b(?:TO|FROM|GIVING)\s+{escaped}\b", upper):
        return "read_write"
    if re.search(rf"\b(?:MOVE\b.*\bTO|COMPUTE|ACCEPT|INITIALIZE|SET|STRING\b.*\bINTO|UNSTRING\b.*\bINTO)\s+{escaped}\b", upper):
        return "write"
    return "read"


def build_semantic_facts(project: ResolvedProject, *, project_id: str) -> tuple[list[SemanticNode], list[SemanticEdge], list[Diagnostic]]:
    nodes: list[SemanticNode] = []
    edges: list[SemanticEdge] = []
    diagnostics: list[Diagnostic] = list(project.diagnostics)
    node_ids: set[str] = set()
    owner_ids: dict[str, str] = {}
    data_ids: dict[tuple[str, str], str] = {}
    file_binding_ids: dict[tuple[str, str], str] = {}

    # Reserve every cross-file owner identity before emitting relationships.
    # This makes call/include resolution independent of lexical file order.
    for source in project.files:
        if source.is_copybook:
            owner_ids[source.path] = stable_id(project_id, "CobolCopybook", source.path)
        else:
            owner_ids[source.path] = stable_id(
                project_id,
                "CobolProgram",
                source.path,
                source.program_name,
            )

    def add_node(label: str, name: str, file_path: str, evidence: SourceEvidence, identity: tuple[Any, ...], **properties: Any) -> str:
        node_id = stable_id(project_id, label, *identity)
        if node_id not in node_ids:
            base = {
                "project_id": project_id,
                "language": "cobol",
                "name": name,
                "qualified_name": properties.pop("qualified_name", name),
                "file_path": file_path,
                "path": file_path,
                "start_line": evidence.start_line,
                "end_line": evidence.end_line,
            }
            base.update(properties)
            nodes.append(SemanticNode(node_id, label, name, file_path, evidence, base))
            node_ids.add(node_id)
        return node_id

    def add_edge(source_id: str, target_id: str, relationship: str, evidence: SourceEvidence, *, confidence: float = 1.0, dynamic: bool = False, **properties: Any) -> None:
        edges.append(
            SemanticEdge(
                stable_id(project_id, "edge", relationship, source_id, target_id, evidence.file, evidence.start_line, len(edges)),
                source_id,
                target_id,
                relationship,
                evidence,
                {"project_id": project_id, **properties},
                confidence,
                dynamic,
            )
        )

    for source in project.files:
        first_evidence = (
            source.paragraphs[0].evidence if source.paragraphs else
            source.data_items[0].evidence if source.data_items else
            source.copies[0].evidence if source.copies else
            SourceEvidence(source.path, 1)
        )
        file_id = add_node("File", Path(source.path).name, source.path, first_evidence, (source.path,), source_format=source.source_format, dialect=source.dialect, encoding=source.encoding)
        if source.is_copybook:
            owner_name = Path(source.path).stem.upper()
            owner_id = add_node("CobolCopybook", owner_name, source.path, first_evidence, (source.path,), qualified_name=owner_name)
        else:
            owner_name = source.program_name
            owner_id = add_node("CobolProgram", owner_name, source.path, first_evidence, (source.path, owner_name), qualified_name=owner_name)
        if owner_ids[source.path] != owner_id:
            raise AssertionError("reserved COBOL owner identity drifted during normalization")
        add_edge(file_id, owner_id, "DEFINES", first_evidence)

        section_ids: dict[str, str] = {}
        for name in source.sections:
            evidence = next((p.evidence for p in source.paragraphs if p.section == name), first_evidence)
            section_id = add_node("CobolSection", name, source.path, evidence, (source.path, owner_name, name), qualified_name=f"{owner_name}.{name}")
            section_ids[name] = section_id
            add_edge(owner_id, section_id, "DEFINES", evidence)

        paragraph_ids: dict[str, str] = {}
        for paragraph in source.paragraphs:
            paragraph_id = add_node(
                "CobolParagraph",
                paragraph.name,
                source.path,
                paragraph.evidence,
                (source.path, owner_name, paragraph.name),
                qualified_name=f"{owner_name}.{paragraph.name}",
                ordinal=paragraph.ordinal,
                section=paragraph.section,
                code="\n".join(statement.text for statement in paragraph.statements),
            )
            paragraph_ids[paragraph.name] = paragraph_id
            add_edge(section_ids.get(paragraph.section, owner_id), paragraph_id, "DEFINES", paragraph.evidence)

        for item in source.data_items:
            item_id = add_node(
                "CobolDataItem",
                item.name,
                source.path,
                item.evidence,
                (source.path, item.storage, item.level, item.name, item.evidence.start_line),
                qualified_name=f"{owner_name}.{item.storage}.{item.name}",
                level=item.level,
                storage=item.storage,
                picture=item.picture,
                usage=item.usage,
                value=item.value,
                redefines=item.redefines,
                occurs=item.occurs,
            )
            data_ids[(source.path, item.name)] = item_id
            add_edge(owner_id, item_id, "DEFINES", item.evidence)

        for binding in source.file_bindings:
            binding_id = add_node(
                "CobolFile",
                binding.name,
                source.path,
                binding.evidence,
                (source.path, binding.name),
                qualified_name=f"{owner_name}.{binding.name}",
                assignment=binding.assignment,
                has_description=binding.has_description,
            )
            file_binding_ids[(source.path, binding.name)] = binding_id
            add_edge(owner_id, binding_id, "DEFINES", binding.evidence)

        cfg_edges, cfg_diagnostics = build_cfg(source, project_id=project_id, program_id=owner_id, paragraph_ids=paragraph_ids)
        edges.extend(cfg_edges)
        diagnostics.extend(cfg_diagnostics)

        symbol_candidates: dict[str, list[str]] = {}
        for item in source.data_items:
            symbol_candidates.setdefault(item.name, []).append(
                stable_id(
                    project_id,
                    "CobolDataItem",
                    source.path,
                    item.storage,
                    item.level,
                    item.name,
                    item.evidence.start_line,
                )
            )
        for imported_path in project.include_closure.get(source.path, ()):
            imported = next((item for item in project.files if item.path == imported_path), None)
            if not imported:
                continue
            for item in imported.data_items:
                target_id = stable_id(
                    project_id,
                    "CobolDataItem",
                    imported.path,
                    item.storage,
                    item.level,
                    item.name,
                    item.evidence.start_line,
                )
                symbol_candidates.setdefault(item.name, []).append(target_id)
                add_edge(owner_id, target_id, "REFERENCES", item.evidence, imported=True, copybook_path=imported.path)

        for paragraph in source.paragraphs:
            paragraph_id = paragraph_ids[paragraph.name]
            for statement in paragraph.statements:
                if statement.kind == "call":
                    target = str(statement.properties.get("target", "")).upper()
                    resolved = project.programs.get(target) if statement.properties.get("literal") else None
                    if resolved:
                        add_edge(paragraph_id, owner_ids[resolved.path], "CALLS", statement.evidence, confidence=statement.confidence, target_name=target)
                    else:
                        diagnostics.append(
                            Diagnostic(
                                "COBOL_DYNAMIC_CALL" if not statement.properties.get("literal") else "COBOL_CALL_TARGET_UNRESOLVED",
                                f"call target {target} is {'dynamic' if not statement.properties.get('literal') else 'not defined in this project'}",
                                evidence=statement.evidence,
                                details={"target": target},
                            )
                        )
                elif statement.kind == "io":
                    target = str(statement.properties.get("target", "")).upper()
                    target_id = file_binding_ids.get((source.path, target))
                    if target_id is None and statement.properties.get("operation") in {"WRITE", "REWRITE"}:
                        file_records = {item.name for item in source.data_items if item.storage == "FILE"}
                        bindings = [value for (path, _), value in file_binding_ids.items() if path == source.path]
                        if target in file_records and len(bindings) == 1:
                            target_id = bindings[0]
                    if target_id:
                        operation = str(statement.properties.get("operation", ""))
                        mode = str(statement.properties.get("mode", ""))
                        if operation == "OPEN" and mode in {"OUTPUT", "EXTEND"}:
                            relationships = ("WRITES",)
                        elif operation == "OPEN" and mode == "I-O":
                            relationships = ("READS", "WRITES")
                        else:
                            relationships = ("READS",) if operation in {"OPEN", "READ", "START", "CLOSE"} else ("WRITES",)
                        for relationship in relationships:
                            add_edge(paragraph_id, target_id, relationship, statement.evidence, operation=operation, mode=mode)
                    else:
                        diagnostics.append(Diagnostic("COBOL_FILE_TARGET_UNRESOLVED", f"file target {target} was not found", evidence=statement.evidence))
                elif statement.kind in {"sql", "cics"}:
                    label = "CobolSqlStatement" if statement.kind == "sql" else "CobolCicsCommand"
                    name = str(statement.properties.get("operation") or statement.kind.upper())
                    statement_id = add_node(
                        label,
                        name,
                        source.path,
                        statement.evidence,
                        (source.path, statement.kind, statement.evidence.start_line, statement.text),
                        operation=name,
                        raw_text=statement.text,
                        targets=list(statement.properties.get("targets", statement.properties.get("resources", ()))),
                        host_variables=list(statement.properties.get("host_variables", ())),
                    )
                    add_edge(paragraph_id, statement_id, "DEFINES", statement.evidence, confidence=statement.confidence)

                words = statement.properties.get("words")
                if not words:
                    words = re.findall(r"[A-Z0-9][A-Z0-9-]*", statement.text.upper())
                for name in dict.fromkeys(map(str, words)):
                    candidates = list(dict.fromkeys(symbol_candidates.get(name, ())))
                    if len(candidates) == 1:
                        add_edge(
                            paragraph_id,
                            candidates[0],
                            "REFERENCES",
                            statement.evidence,
                            confidence=statement.confidence,
                            statement_kind=statement.kind,
                            access=_data_access(statement.text, name),
                        )
                    elif len(candidates) > 1:
                        diagnostics.append(
                            Diagnostic(
                                "COBOL_SYMBOL_AMBIGUOUS",
                                f"unqualified data reference {name} matches {len(candidates)} definitions",
                                evidence=statement.evidence,
                                details={"name": name, "candidate_ids": candidates},
                            )
                        )

    by_path = {item.path: item for item in project.files}
    for owner_path, target_paths in project.include_graph.items():
        owner_id = owner_ids.get(owner_path)
        if not owner_id:
            continue
        include_by_target = {
            target.path: include
            for include in by_path[owner_path].copies
            for target in project.copybooks.values()
            if Path(target.path).stem.upper() == include.name.upper()
        }
        for target_path in target_paths:
            target_id = owner_ids.get(target_path)
            include = include_by_target.get(target_path)
            if target_id and include:
                add_edge(owner_id, target_id, "INCLUDES", include.evidence, replacing=include.replacing)
    return nodes, edges, diagnostics
