"""Paragraph-level COBOL control-flow graph construction."""

from __future__ import annotations

from typing import Mapping

from .models import Diagnostic, ParsedFile, SemanticEdge, stable_id


def build_cfg(
    source: ParsedFile,
    *,
    project_id: str,
    program_id: str,
    paragraph_ids: Mapping[str, str],
) -> tuple[list[SemanticEdge], list[Diagnostic]]:
    edges: list[SemanticEdge] = []
    diagnostics: list[Diagnostic] = []
    paragraphs = list(source.paragraphs)

    def add(source_id: str, target_id: str, relationship: str, statement, *, dynamic: bool = False, **properties) -> None:
        confidence = min(statement.confidence, 0.7 if dynamic else 1.0)
        edges.append(
            SemanticEdge(
                stable_id(project_id, "edge", relationship, source_id, target_id, statement.evidence.start_line, len(edges)),
                source_id,
                target_id,
                relationship,
                statement.evidence,
                {"project_id": project_id, **properties},
                confidence,
                dynamic,
            )
        )

    for index, paragraph in enumerate(paragraphs):
        source_id = paragraph_ids[paragraph.name]
        terminated = False
        for statement in paragraph.statements:
            if statement.kind == "perform":
                target = str(statement.properties.get("target", ""))
                through = str(statement.properties.get("through", ""))
                target_id = paragraph_ids.get(target)
                if target_id:
                    add(source_id, target_id, "PERFORMS_THRU" if through else "PERFORMS", statement, through=through, loop=statement.properties.get("loop", ""))
                    if through:
                        end_id = paragraph_ids.get(through)
                        if not end_id:
                            diagnostics.append(Diagnostic("COBOL_PERFORM_THRU_UNRESOLVED", f"PERFORM THRU end {through} was not found", evidence=statement.evidence))
                        else:
                            add(end_id, source_id, "RETURNS", statement, caller=paragraph.name, continuation_line=statement.evidence.end_line)
                    else:
                        add(target_id, source_id, "RETURNS", statement, caller=paragraph.name, continuation_line=statement.evidence.end_line)
                else:
                    diagnostics.append(Diagnostic("COBOL_PERFORM_TARGET_UNRESOLVED", f"PERFORM target {target} was not found", evidence=statement.evidence))
            elif statement.kind == "goto":
                dynamic = bool(statement.properties.get("dynamic"))
                for target in statement.properties.get("targets", ()):  # type: ignore[assignment]
                    target_id = paragraph_ids.get(str(target))
                    if target_id:
                        add(source_id, target_id, "GOES_TO_DYNAMIC" if dynamic else "GOES_TO", statement, dynamic=dynamic, selector=statement.properties.get("selector", ""))
                    else:
                        diagnostics.append(Diagnostic("COBOL_GOTO_TARGET_UNRESOLVED", f"GO TO target {target} was not found", evidence=statement.evidence))
                terminated = not dynamic
            elif statement.kind == "alter":
                target = str(statement.properties.get("target", ""))
                target_id = paragraph_ids.get(target)
                if target_id:
                    add(source_id, target_id, "ALTERS", statement, dynamic=True, altered_source=statement.properties.get("source", ""))
                else:
                    diagnostics.append(Diagnostic("COBOL_ALTER_TARGET_UNRESOLVED", f"ALTER target {target} was not found", evidence=statement.evidence))
            elif statement.kind == "conditional" and index + 1 < len(paragraphs):
                add(source_id, paragraph_ids[paragraphs[index + 1].name], "CONDITIONAL", statement, branch=statement.text.split()[0].upper())
            elif statement.kind == "exit":
                add(source_id, program_id, "EXITS", statement, terminal=bool(statement.properties.get("terminal")))
                terminated = bool(statement.properties.get("terminal"))
        if index + 1 < len(paragraphs) and not terminated:
            evidence = paragraph.statements[-1].evidence if paragraph.statements else paragraph.evidence
            pseudo = type("Statement", (), {"evidence": evidence, "confidence": 1.0})()
            add(source_id, paragraph_ids[paragraphs[index + 1].name], "FALLS_THROUGH", pseudo)
    return edges, diagnostics
