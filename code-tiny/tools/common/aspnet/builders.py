from __future__ import annotations

from typing import Any, Dict

from .identity import relationship_id, semantic_id
from .models import SemanticFact, SemanticRelationship, SourceSpan


def fact(
    *,
    kind: str,
    name: str,
    framework: str,
    project_id: str,
    project_name: str,
    module_id: str,
    source: SourceSpan,
    coordinates: tuple[object, ...] = (),
    confidence: float = 1.0,
    resolution_status: str = "resolved",
    extraction_method: str = "source",
    source_symbol_id: str = "",
    properties: Dict[str, Any] | None = None,
) -> SemanticFact:
    stable_id = semantic_id(
        framework, project_id, module_id, kind,
        source.file_path, source.start_line, name, *coordinates,
    )
    return SemanticFact(
        kind=kind,
        stable_id=stable_id,
        name=name,
        framework=framework,
        project_id=project_id,
        project_name=project_name,
        module_id=module_id,
        source=source,
        confidence=confidence,
        resolution_status=resolution_status,
        extraction_method=extraction_method,
        source_symbol_id=source_symbol_id,
        properties=properties or {},
    )


def relationship(
    *,
    relationship_type: str,
    source_fact: SemanticFact,
    target_fact: SemanticFact,
    source: SourceSpan | None = None,
    confidence: float = 1.0,
    resolution_status: str = "resolved",
    reason: str = "",
    properties: Dict[str, Any] | None = None,
) -> SemanticRelationship:
    evidence = source or source_fact.source
    return SemanticRelationship(
        stable_id=relationship_id(
            source_fact.framework,
            source_fact.project_id,
            source_fact.module_id,
            relationship_type,
            source_fact.stable_id,
            target_fact.stable_id,
            evidence.file_path,
            evidence.start_line,
        ),
        relationship_type=relationship_type,
        from_id=source_fact.stable_id,
        to_id=target_fact.stable_id,
        from_label=source_fact.kind,
        to_label=target_fact.kind,
        framework=source_fact.framework,
        project_id=source_fact.project_id,
        module_id=source_fact.module_id,
        source=evidence,
        confidence=confidence,
        resolution_status=resolution_status,
        reason=reason,
        properties=properties or {},
    )
