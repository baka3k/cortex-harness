from __future__ import annotations

import re
from typing import List, Sequence, Tuple

from tools.spring.annotation_catalog import (
    AOP_CLASS_ANNOTATIONS,
    AOP_METHOD_ANNOTATIONS,
    CACHE_ANNOTATIONS,
    VALIDATION_ANNOTATIONS,
    short_annotation_name,
)
from tools.spring.extractors.common import class_owner_id, fact, first_annotation, rel, stable_hash
from tools.spring.models import SpringFact, SpringRelationship
from tools.spring.source_scanner import SourceUnit
from tools.spring.value_resolver import list_arg, parse_annotation_args


def extract_crosscutting_facts(
    *,
    units: Sequence[SourceUnit],
    project_id: str,
    project_name: str,
) -> Tuple[List[SpringFact], List[SpringRelationship]]:
    facts: List[SpringFact] = []
    relationships: List[SpringRelationship] = []
    for unit in units:
        for cls in unit.classes:
            aspect_ann = first_annotation(cls.annotations, AOP_CLASS_ANNOTATIONS)
            aspect_id = ""
            if aspect_ann:
                aspect_id = f"spring_aspect::{project_id}::{stable_hash(class_owner_id(cls))}"
                facts.append(
                    fact(
                        kind="Aspect",
                        stable_id=aspect_id,
                        name=cls.name,
                        source=cls.source,
                        project_id=project_id,
                        project_name=project_name,
                        language=cls.language,
                        source_symbol_id=class_owner_id(cls),
                    )
                )
                relationships.append(rel("SEMANTIC_OF", aspect_id, class_owner_id(cls), project_id, cls.source, "Aspect class anchor"))

            for method in cls.methods:
                advice_ann = first_annotation(method.annotations, AOP_METHOD_ANNOTATIONS)
                if advice_ann:
                    if advice_ann.short_name == "Pointcut":
                        pointcut_id = f"spring_pointcut::{project_id}::{stable_hash(method.symbol_id + advice_ann.raw)}"
                        facts.append(
                            fact(
                                kind="Pointcut",
                                stable_id=pointcut_id,
                                name=f"{cls.name}.{method.name}",
                                source=method.source,
                                project_id=project_id,
                                project_name=project_name,
                                language=method.language,
                                source_symbol_id=method.symbol_id,
                                expression=advice_ann.args.get("value") or "",
                                parsed_clauses=_pointcut_clauses(str(advice_ann.args.get("value") or "")),
                            )
                        )
                        if aspect_id:
                            relationships.append(rel("DECLARES_POINTCUT", aspect_id, pointcut_id, project_id, method.source, "@Pointcut declaration"))
                    else:
                        advice_id = f"spring_advice::{project_id}::{stable_hash(method.symbol_id + advice_ann.raw)}"
                        facts.append(
                            fact(
                                kind="Advice",
                                stable_id=advice_id,
                                name=f"{cls.name}.{method.name}",
                                source=method.source,
                                project_id=project_id,
                                project_name=project_name,
                                language=method.language,
                                source_symbol_id=method.symbol_id,
                                advice_kind=advice_ann.short_name,
                                expression=advice_ann.args.get("value") or "",
                            )
                        )
                        if aspect_id:
                            relationships.append(rel("APPLIES_ADVICE", aspect_id, advice_id, project_id, method.source, "AOP advice declaration"))

                for ann in method.annotations:
                    if ann.short_name in VALIDATION_ANNOTATIONS:
                        constraint_id = f"spring_validation::{project_id}::{stable_hash(method.symbol_id + ann.raw)}"
                        facts.append(
                            fact(
                                kind="ValidationConstraint",
                                stable_id=constraint_id,
                                name=ann.short_name,
                                source=method.source,
                                project_id=project_id,
                                project_name=project_name,
                                language=method.language,
                                source_symbol_id=method.symbol_id,
                                annotation=ann.short_name,
                                message=ann.args.get("message") or "",
                                groups=list_arg(ann.args, "groups"),
                                target_position="method",
                            )
                        )
                        relationships.append(rel("CONSTRAINED_BY", method.symbol_id, constraint_id, project_id, method.source, "Bean Validation annotation"))
                        if ann.short_name in {"Valid", "Validated"}:
                            relationships.append(rel("VALIDATES_CASCADE", method.symbol_id, constraint_id, project_id, method.source, "Validation cascade"))

                for raw, short_name, args in _parameter_validation_annotations(method.params):
                    constraint_id = f"spring_validation::{project_id}::{stable_hash(method.symbol_id + ':param:' + raw)}"
                    facts.append(
                        fact(
                            kind="ValidationConstraint",
                            stable_id=constraint_id,
                            name=short_name,
                            source=method.source,
                            project_id=project_id,
                            project_name=project_name,
                            language=method.language,
                            source_symbol_id=method.symbol_id,
                            annotation=short_name,
                            message=args.get("message") or "",
                            groups=list_arg(args, "groups"),
                            target_position="parameter",
                        )
                    )
                    relationships.append(rel("CONSTRAINED_BY", method.symbol_id, constraint_id, project_id, method.source, "Bean Validation parameter annotation"))
                    if short_name in {"Valid", "Validated"}:
                        relationships.append(rel("VALIDATES_CASCADE", method.symbol_id, constraint_id, project_id, method.source, "Validation cascade"))

                for cache_ann in [ann for ann in method.annotations if ann.short_name in CACHE_ANNOTATIONS]:
                    cache_names = list_arg(cache_ann.args, "cacheNames", "value") or ["<unresolved>"]
                    operation_id = f"spring_cache_op::{project_id}::{stable_hash(method.symbol_id + cache_ann.raw)}"
                    facts.append(
                        fact(
                            kind="CacheOperation",
                            stable_id=operation_id,
                            name=f"{cls.name}.{method.name}",
                            source=method.source,
                            project_id=project_id,
                            project_name=project_name,
                            language=method.language,
                            confidence=1.0 if cache_names != ["<unresolved>"] else 0.6,
                            resolution_status="resolved" if cache_names != ["<unresolved>"] else "unresolved",
                            source_symbol_id=method.symbol_id,
                            operation_kind=cache_ann.short_name,
                            cache_names=cache_names,
                            key=cache_ann.args.get("key") or "",
                            condition=cache_ann.args.get("condition") or "",
                            unless=cache_ann.args.get("unless") or "",
                            all_entries=cache_ann.args.get("allEntries") or False,
                            before_invocation=cache_ann.args.get("beforeInvocation") or False,
                        )
                    )
                    relationships.append(rel("APPLIES_TO", operation_id, method.symbol_id, project_id, method.source, "Cache operation applies to method"))
                    edge_type = "READS_CACHE" if cache_ann.short_name == "Cacheable" else ("WRITES_CACHE" if cache_ann.short_name == "CachePut" else "EVICTS_CACHE")
                    for cache_name in cache_names:
                        region_id = f"spring_cache_region::{project_id}::default::{stable_hash(cache_name)}"
                        facts.append(
                            fact(
                                kind="CacheRegion",
                                stable_id=region_id,
                                name=cache_name,
                                source=method.source,
                                project_id=project_id,
                                project_name=project_name,
                                language=method.language,
                                confidence=1.0 if cache_name != "<unresolved>" else 0.6,
                                resolution_status="resolved" if cache_name != "<unresolved>" else "unresolved",
                                cache_name=cache_name,
                                manager="default",
                            )
                        )
                        relationships.append(rel(edge_type, operation_id, region_id, project_id, method.source, f"{cache_ann.short_name} cache effect"))
    return facts, relationships


def _pointcut_clauses(expression: str) -> List[str]:
    return [token for token in ("execution", "within", "@annotation", "@within", "bean") if token in (expression or "")]


def _parameter_validation_annotations(params: str) -> List[Tuple[str, str, dict]]:
    annotations: List[Tuple[str, str, dict]] = []
    for match in re.finditer(r"@([A-Za-z_][\w.]*)(\([^)]*\))?", params or ""):
        short = short_annotation_name(match.group(1))
        if short not in VALIDATION_ANNOTATIONS:
            continue
        raw_args = match.group(2) or ""
        annotations.append((match.group(0), short, parse_annotation_args(raw_args)))
    return annotations
